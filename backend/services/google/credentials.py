from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, update

from backend.core.credential_cipher import BoundCredentialCipher, CredentialCipherError
from backend.models import GoogleConnection, GoogleFileGrant, User
from backend.schemas.google import DRIVE_SCOPE
from backend.services.google.client import TOKEN_URL, GoogleHTTP
from backend.services.google.errors import GoogleServiceError


class GoogleCredentials:
    def __init__(self, settings, *, transport=None):
        self.settings = settings
        self.http = GoogleHTTP(transport)

    def cipher(self):
        keys = self.settings.google_credential_encryption_keys
        try:
            return BoundCredentialCipher(keys.get_secret_value() if keys else "")
        except CredentialCipherError as error:
            raise GoogleServiceError(error.status_code, error.detail) from None

    @staticmethod
    def binding(row):
        return {
            "provider": "google",
            "owner_id": str(row.owner_id),
            "record_id": str(row.id),
            "kind": "credentials",
        }

    def decrypt(self, row):
        try:
            return self.cipher().open(self.binding(row), row.ciphertext or b"")
        except CredentialCipherError as error:
            raise GoogleServiceError(error.status_code, error.detail) from None

    def encrypt(self, row, payload):
        row.ciphertext = self.cipher().seal(self.binding(row), payload)

    async def owned(self, session, owner, connection_id, *, lock=False):
        actor = await session.get(User, owner, populate_existing=True)
        if not self.settings.google_connector_enabled or actor is None or not actor.is_active:
            raise GoogleServiceError(404, "Google connection not found")
        query = select(GoogleConnection).where(
            GoogleConnection.id == connection_id, GoogleConnection.owner_id == owner
        )
        if lock:
            query = query.with_for_update()
        row = await session.scalar(query.execution_options(populate_existing=True))
        if row is None:
            raise GoogleServiceError(404, "Google connection not found")
        return row

    @staticmethod
    def token(value):
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= 16384
            or not value.isascii()
            or any(ch.isspace() or ord(ch) < 33 for ch in value)
        ):
            raise GoogleServiceError(409, "Google reconnect required")
        return value

    async def access_token(self, session, owner: UUID, connection_id: UUID):
        row = await self.owned(session, owner, connection_id, lock=True)
        if row.status != "connected" or not row.offline_ready:
            raise GoogleServiceError(409, "Google reconnect required")
        payload = self.decrypt(row)
        if payload.get("client_id") != self.settings.google_client_id:
            raise GoogleServiceError(409, "Google reconnect required")
        if row.expires_at and row.expires_at > datetime.now(UTC) + timedelta(seconds=60):
            await session.commit()
            return self.token(payload["access_token"])
        try:
            response = await self.http.request(
                "POST",
                TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "client_id": self.settings.google_client_id,
                    "client_secret": self.settings.google_client_secret.get_secret_value(),
                    "refresh_token": self.token(payload.get("refresh_token")),
                },
            )
        except GoogleServiceError as error:
            if error.code == "google_reconnect_required":
                row.status, row.offline_ready, row.ciphertext = "reconnect", False, None
                await session.commit()
            else:
                await session.rollback()
            raise
        scopes = response.get("scope", " ".join(row.granted_scopes)).split()
        if set(scopes) != {DRIVE_SCOPE}:
            row.status, row.offline_ready = "reconnect", False
            await session.commit()
            raise GoogleServiceError(409, "Google permission scope requires reconnect")
        payload["access_token"] = self.token(response.get("access_token"))
        if response.get("refresh_token"):
            payload["refresh_token"] = self.token(response["refresh_token"])
        row.expires_at = datetime.now(UTC) + timedelta(
            seconds=max(1, min(int(response.get("expires_in", 3600)), 86400))
        )
        self.encrypt(row, payload)
        await session.commit()
        return payload["access_token"]

    async def disconnect(self, session, owner, connection_id):
        row = await self.owned(session, owner, connection_id, lock=True)
        row.status, row.offline_ready, row.ciphertext = "disconnected", False, None
        row.generation += 1
        await session.execute(
            update(GoogleFileGrant)
            .where(GoogleFileGrant.connection_id == row.id)
            .values(revoked=True)
        )
        await session.commit()


async def get_google_access_token(session, owner, connection_id):
    from backend.core.config import get_settings

    return await GoogleCredentials(get_settings()).access_token(session, owner, connection_id)
