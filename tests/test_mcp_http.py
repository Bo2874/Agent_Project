import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from agent_core.mcp.outbound import OutboundDenied, OutboundPolicy, PinnedBackend, canonical_url
from backend.core.credential_cipher import CredentialCipher
from backend.core.mcp_http_config import HTTPProfile, OAuthProfile
from backend.core.redaction import redact_data, redact_text
from backend.schemas.plugin import StoreCredentialRequest
from backend.services.plugins import PluginServiceError


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/mcp",
        "file:///etc/passwd",
        "ftp://example.com/mcp",
        "https://user:pass@example.com/mcp",
        "https://example.com/mcp?token=canary",
        "https://example.com/mcp#fragment",
        "https://example.com./mcp",
        "https://example.com\\@127.0.0.1/mcp",
        "https://example.com:0/mcp",
        "https://example.com:99999/mcp",
        "https://%31%32%37.0.0.1/mcp",
        "https://example.com/\nmalicious",
        "https://[fe80::1%25eth0]/mcp",
    ],
)
def test_outbound_url_rejects_ambiguous_or_unapproved_shapes(url):
    with pytest.raises(OutboundDenied):
        canonical_url(url)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "192.168.1.1",
        "172.16.0.1",
        "169.254.169.254",
        "0.0.0.0",
        "100.64.0.1",
        "224.0.0.1",
        "::1",
        "::",
        "fc00::1",
        "fe80::1",
        "::ffff:127.0.0.1",
        "64:ff9b::a9fe:a9fe",
        "2002:7f00:1::",
    ],
)
async def test_dns_rejects_nonpublic_destinations(address):
    async def resolver(host, port):
        return (address,)

    policy = OutboundPolicy(("https://approved.example/mcp",), resolver=resolver)
    with pytest.raises(OutboundDenied):
        await policy.addresses("approved.example", 443)


@pytest.mark.asyncio
async def test_dns_mixed_answers_fail_closed_before_any_dial():
    async def resolver(host, port):
        return ("8.8.8.8", "127.0.0.1")

    policy = OutboundPolicy(("https://approved.example/mcp",), resolver=resolver)
    backend = PinnedBackend(policy)

    async def forbidden(*args, **kwargs):
        raise AssertionError("Must not dial mixed DNS answers")

    backend.backend = SimpleNamespace(connect_tcp=forbidden)
    with pytest.raises(OutboundDenied):
        await backend.connect_tcp("approved.example", 443)


@pytest.mark.asyncio
async def test_dns_rebinding_connects_numeric_ip_and_revalidates_next_dial():
    answers = iter([("8.8.8.8",), ("127.0.0.1",)])
    dialed = []

    async def resolver(host, port):
        return next(answers)

    async def dial(host, port, **kwargs):
        dialed.append(host)
        return object()

    backend = PinnedBackend(OutboundPolicy(("https://approved.example/mcp",), resolver=resolver))
    backend.backend = SimpleNamespace(connect_tcp=dial)
    await backend.connect_tcp("approved.example", 443)
    with pytest.raises(OutboundDenied):
        await backend.connect_tcp("approved.example", 443)
    assert dialed == ["8.8.8.8"]


@pytest.mark.parametrize(
    "url",
    [
        "https://other.example/mcp",
        "https://approved.example:8443/mcp",
        "https://approved.example/token",
    ],
)
def test_every_request_is_bound_to_exact_reviewed_endpoint(url):
    policy = OutboundPolicy(("https://approved.example/mcp",))
    with pytest.raises(OutboundDenied):
        policy.validate_url(url)


def test_credential_cipher_binds_owner_connection_record_and_profile():
    cipher = CredentialCipher(Fernet.generate_key().decode())
    binding = {
        "owner": str(uuid4()),
        "connection": str(uuid4()),
        "record": str(uuid4()),
        "profile": "v1",
    }
    payload = {"access_token": "private-canary", "refresh_token": "refresh-canary"}
    encrypted = cipher.seal(binding, payload)
    assert b"private-canary" not in encrypted and b"refresh-canary" not in encrypted
    assert cipher.open(binding, encrypted) == payload
    for key in binding:
        with pytest.raises(PluginServiceError):
            cipher.open({**binding, key: "forged"}, encrypted)
    with pytest.raises(PluginServiceError):
        cipher.open(binding, encrypted[:-5] + b"wrong")


def test_encryption_key_rotation_reads_old_ciphertext_and_seals_with_new_key():
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    prior = CredentialCipher(old).seal({"owner": "a"}, {"token": "canary"})
    rotating = CredentialCipher(new + "," + old)
    fresh = rotating.seal({"owner": "a"}, rotating.open({"owner": "a"}, prior))
    assert CredentialCipher(new).open({"owner": "a"}, fresh) == {"token": "canary"}
    with pytest.raises(PluginServiceError):
        CredentialCipher(old).open({"owner": "a"}, fresh)


@pytest.mark.parametrize(
    "value", ["", "secret\nheader", "secret with space", "é-token", "x" * 16385]
)
def test_raw_credential_input_is_bounded_and_header_safe(value):
    with pytest.raises(ValidationError):
        StoreCredentialRequest(server_id="main", slot="token", value=value, expected_auth_version=0)


def test_secrets_do_not_appear_in_request_repr_or_redacted_logs():
    body = StoreCredentialRequest(
        server_id="main", slot="token", value="canary-secret", expected_auth_version=0
    )
    assert "canary-secret" not in repr(body)
    message = "GET /api/plugins/oauth/callback?%63ode=private-code&state=private-state&iss=https://issuer.example"
    sanitized = redact_text(message)
    assert "private-code" not in sanitized and "private-state" not in sanitized
    assert "error_code" in json.dumps(redact_data({"error_code": "tool_timeout", "code": "canary"}))
    assert "canary" not in json.dumps(redact_data({"code": "canary", "code_verifier": "canary"}))


@pytest.mark.parametrize(
    "uri",
    ["http://remote.example/callback", "https://user:pass@example.com/cb", "javascript:alert(1)"],
)
def test_oauth_callback_uri_requires_reviewed_https_or_loopback(uri):
    with pytest.raises(ValueError):
        OAuthProfile(
            issuer="https://issuer.example",
            authorization_endpoint="https://issuer.example/authorize",
            token_endpoint="https://issuer.example/token",
            client_id="registered",
            redirect_uri=uri,
        )


def test_http_profiles_do_not_accept_tokens_or_untrusted_extra_fields():
    with pytest.raises(ValidationError):
        HTTPProfile(
            id="fixture.http",
            release="0.1.0",
            endpoint="https://approved.example/mcp",
            token="canary",
        )


@pytest.mark.asyncio
async def test_http_protocol_json_sse_tls_and_redirect_matrix():
    from scripts.check_http_plugins import check

    result = await check()
    assert result["tls_sni_verified"] and result["redirect_blocked"]
