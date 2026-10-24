import json
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema.validators import validator_for
from pydantic import TypeAdapter, ValidationError

from agent_core.plugins import (
    AccessPolicy,
    PluginManifest,
    ToolPolicy,
    load_plugin_manifest,
    skill_capability_id,
    tool_alias,
    tool_capability_id,
)
from agent_core.plugins.manifest import MAX_MANIFEST_BYTES, PluginVersion
from scripts.validate_plugin_manifests import validate_directory

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def payload():
    return json.loads((ROOT / "plugins/manifests/demo.json").read_text())


def test_examples_roundtrip_and_exported_schema_match_the_models():
    schema = PluginManifest.model_json_schema()
    exported = json.loads((ROOT / "docs/plugins/schemas/plugin-manifest.schema.json").read_text())
    assert exported == schema
    validator_for(schema).check_schema(schema)
    for path in (ROOT / "plugins/manifests").glob("*.json"):
        manifest = load_plugin_manifest(path)
        validator_for(schema)(schema).validate(manifest.model_dump(mode="json"))
        assert PluginManifest.model_validate_json(manifest.model_dump_json()) == manifest
        assert all(cap.plugin_id == manifest.id for cap in manifest.capabilities())


@pytest.mark.parametrize("version", ["1.0.0", "0.1.0", "1.2.3-rc.1", "1.2.3+build.4"])
def test_exact_semver_releases_are_supported(version):
    assert TypeAdapter(PluginVersion).validate_python(version) == version


@pytest.mark.parametrize(
    "version", ["latest", "^1.0.0", ">=1.0.0", "1.0", "v1.0.0", "01.0.0", "1.0.0-01", " 1.0.0"]
)
def test_floating_or_ambiguous_versions_are_rejected(version):
    with pytest.raises(ValidationError):
        TypeAdapter(PluginVersion).validate_python(version)


@pytest.mark.parametrize("field", ["namespace", "version", "policy_version"])
def test_manifest_identity_and_versions_are_required(payload, field):
    payload.pop(field)
    with pytest.raises(ValidationError):
        PluginManifest.model_validate(payload)


def test_default_host_policy_is_admin_approval_unknown_and_no_retry():
    policy = ToolPolicy()
    assert policy.allowed_roles == ("admin",) and policy.requires_approval
    assert policy.side_effect == "unknown" and policy.max_retries == 0
    assert not policy.idempotent and policy.max_concurrency == 1


@pytest.mark.parametrize("effect", ["write", "destructive", "unknown"])
def test_non_read_only_actions_cannot_skip_approval(effect):
    with pytest.raises(ValidationError, match="require approval"):
        AccessPolicy(side_effect=effect, requires_approval=False)


@pytest.mark.parametrize(
    "change",
    [
        {"max_retries": 1},
        {"max_retries": 1, "idempotent": True},
        {"max_retries": 1, "idempotent": True, "side_effect": "write"},
        {"max_retries": True},
        {"max_concurrency": True},
        {"timeout_seconds": float("nan")},
        {"timeout_seconds": float("inf")},
        {"timeout_seconds": 0.0},
        {"max_concurrency": 17},
        {"allowed_roles": []},
        {"allowed_roles": ["superadmin"]},
        {"allowed_roles": ["user", "user"]},
        {"requires_approval": "false"},
    ],
)
def test_unsafe_retry_or_invalid_limits_and_roles_fail(change):
    with pytest.raises(ValidationError):
        ToolPolicy(**change)


def test_read_only_retry_requires_an_explicit_idempotence_declaration():
    policy = ToolPolicy(side_effect="read_only", idempotent=True, max_retries=1)
    assert policy.max_retries == 1


@pytest.mark.parametrize(
    "scope,field,value",
    [
        ("root", "api_key", "private-fixture-value"),
        ("root", "enabled", True),
        ("root", "schema_version", "2.0"),
        ("server", "command", "powershell"),
        ("server", "args", ["-Command", "do-something"]),
        ("server", "env", {"TOKEN": "private-fixture-value"}),
        ("server", "url", "https://user:password@example.invalid/mcp"),
        ("server", "headers", {"Authorization": "Bearer private-fixture-value"}),
        ("tool", "annotations", {"readOnlyHint": True}),
    ],
)
def test_manifest_has_no_execution_or_raw_credential_escape_hatches(payload, scope, field, value):
    target = payload if scope == "root" else payload["servers"][0]
    if scope == "tool":
        target = target["tools"][0]
    target[field] = value
    with pytest.raises(ValidationError):
        PluginManifest.model_validate(payload)


@pytest.mark.parametrize(
    "path",
    [
        "../secret",
        "/etc/passwd",
        "C:/secret",
        "skills\\report",
        "skills//report",
        "skills/./report",
        "https://example.invalid/skill",
        "skills/report/",
    ],
)
def test_skill_directory_rejects_traversal_and_nonportable_paths(payload, path):
    payload["skills"][0]["relative_directory"] = path
    with pytest.raises(ValidationError, match="portable relative path"):
        PluginManifest.model_validate(payload)


@pytest.mark.parametrize(
    "change",
    [
        "missing_tool",
        "duplicate_reference",
        "role_escalation",
        "approval_bypass",
        "understated_side_effect",
    ],
)
def test_skill_dependencies_cannot_weaken_tool_policy(payload, change):
    skill = payload["skills"][0]
    calculator = payload["servers"][0]["tools"][1]
    if change == "missing_tool":
        skill["required_tools"][0]["tool_name"] = "missing"
    elif change == "duplicate_reference":
        skill["required_tools"] *= 2
    elif change == "role_escalation":
        calculator["policy"]["allowed_roles"] = ["admin"]
    elif change == "approval_bypass":
        calculator["policy"]["requires_approval"] = True
    else:
        calculator["policy"].update(side_effect="destructive", requires_approval=True)
        skill["policy"].update(side_effect="write", requires_approval=True)
    with pytest.raises(ValidationError):
        PluginManifest.model_validate(payload)


@pytest.mark.parametrize(
    "change",
    [
        "server_id",
        "tool_name",
        "remote_name",
        "skill_name",
        "server_limit",
        "tool_limit",
        "timeout",
    ],
)
def test_duplicate_declarations_and_inconsistent_resource_limits_fail(payload, change):
    server = payload["servers"][0]
    if change == "server_id":
        payload["servers"] *= 2
    elif change == "tool_name":
        server["tools"][1]["name"] = server["tools"][0]["name"]
    elif change == "remote_name":
        server["tools"][1]["remote_name"] = server["tools"][0]["remote_name"]
    elif change == "skill_name":
        payload["skills"] *= 2
    elif change == "server_limit":
        server["max_concurrency"] = 2
    elif change == "tool_limit":
        server["tools"][0]["policy"]["max_concurrency"] = 2
    else:
        server["tools"][0]["policy"]["timeout_seconds"] = 31.0
    with pytest.raises(ValidationError):
        PluginManifest.model_validate(payload)


@pytest.mark.parametrize(
    "namespace", ["Demo", "../demo", "demo-name", " demo", "demo__main", "x" * 21]
)
def test_host_namespaces_reject_ambiguous_alias_components(namespace):
    with pytest.raises(ValidationError):
        tool_alias(namespace, "main", "search")


def test_remote_mcp_name_is_preserved_and_host_alias_is_portable(payload):
    payload["servers"][0]["tools"][0]["remote_name"] = "Remote.Search-API_v2"
    manifest = PluginManifest.model_validate(payload)
    assert manifest.servers[0].tools[0].remote_name == "Remote.Search-API_v2"
    assert len(tool_alias("a" * 20, "b" * 16, "c" * 24)) == 64
    assert tool_capability_id("demo", "skill", "report") != skill_capability_id("demo", "report")
    assert tool_alias("research", "github", "search") != tool_alias("research", "web", "search")


def test_bounded_reader_rejects_oversized_duplicate_and_nonfinite_json(tmp_path):
    path = tmp_path / "bad.json"
    for content in [
        b" " * (MAX_MANIFEST_BYTES + 1),
        b'{"id":"one","id":"two"}',
        b'{"max_concurrency":NaN}',
    ]:
        path.write_bytes(content)
        with pytest.raises(ValueError):
            load_plugin_manifest(path)


def test_directory_validator_rejects_duplicates_and_never_logs_raw_rejected_secret(
    tmp_path, payload
):
    first = tmp_path / "one.json"
    second = tmp_path / "two.json"
    first.write_text(json.dumps(payload))
    second.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="duplicate plugin ids"):
        validate_directory(tmp_path)
    second.unlink()
    payload["servers"][0]["api_key"] = "NEVER_LOG_THIS_REJECTED_SECRET"
    first.write_text(json.dumps(payload))
    result = subprocess.run(
        [sys.executable, "-m", "scripts.validate_plugin_manifests", str(tmp_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "NEVER_LOG_THIS_REJECTED_SECRET" not in result.stdout + result.stderr
    assert "extra_forbidden" in result.stdout


def test_validation_cli_is_offline_and_registers_everything_disabled():
    result = validate_directory(ROOT / "plugins/manifests")
    assert result["connections_started"] == 0 and result["enabled_capabilities"] == 0
    assert result["capabilities"] == 7 and len(result["plugins"]) == 1
    code = (
        "import sys; import agent_core.plugins; assert 'backend' not in sys.modules; "
        "assert 'agent_core.mcp.client' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True)
