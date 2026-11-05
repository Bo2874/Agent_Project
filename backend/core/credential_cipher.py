import json

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from backend.core.config import get_settings
from backend.services.plugins import PluginServiceError


class CredentialCipherError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


class BoundCredentialCipher:
    error_type = CredentialCipherError

    def __init__(self, keys: str):
        try:
            values = keys.split(",")
            if not 1 <= len(values) <= 3:
                raise ValueError("Invalid key ring")
            self.cipher = MultiFernet([Fernet(value.strip().encode("ascii")) for value in values])
        except (ValueError, UnicodeError):
            raise self.error_type(503, "Credential encryption is not configured") from None

    def seal(self, binding: dict, payload: dict) -> bytes:
        data = json.dumps({"binding": binding, "payload": payload}, separators=(",", ":")).encode()
        if len(data) > 64 * 1024:
            raise self.error_type(422, "Credential payload is too large")
        return self.cipher.encrypt(data)

    def open(self, binding: dict, ciphertext: bytes) -> dict:
        try:
            value = json.loads(self.cipher.decrypt(ciphertext))
            if value["binding"] != binding or not isinstance(value["payload"], dict):
                raise ValueError("Binding mismatch")
            return value["payload"]
        except (InvalidToken, ValueError, KeyError, TypeError):
            raise self.error_type(
                409, "Credential is unavailable; reconnect is required"
            ) from None


class CredentialCipher(BoundCredentialCipher):
    # Retain the existing plugin exception contract and byte-for-byte envelope.
    error_type = PluginServiceError


def credential_cipher() -> CredentialCipher:
    keys = get_settings().mcp_credential_encryption_keys
    if keys is None:
        raise PluginServiceError(503, "Credential encryption is not configured")
    return CredentialCipher(keys.get_secret_value())
