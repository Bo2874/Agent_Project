import hashlib
import hmac
import time
from uuid import uuid4


def sign(key: str, method: str, path: str, manifest: bytes, *, expires: int, nonce: str) -> str:
    message = f"{method}\n{path}\n{expires}\n{nonce}\n{hashlib.sha256(manifest).hexdigest()}"
    return hmac.new(key.encode(), message.encode(), hashlib.sha256).hexdigest()


def signed_headers(key: str, method: str, path: str, manifest: bytes) -> dict[str, str]:
    expires, nonce = int(time.time()) + 120, uuid4().hex
    return {
        "X-Artifact-Expires": str(expires),
        "X-Artifact-Nonce": nonce,
        "X-Artifact-Signature": sign(key, method, path, manifest, expires=expires, nonce=nonce),
        "Content-Type": "application/vnd.personal-agent.artifact-bundle",
    }
