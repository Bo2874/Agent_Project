import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import uuid4

from sqlalchemy import select, update

from backend.models import GoogleConnection, GoogleFileGrant, GoogleOAuthState, User
from backend.schemas.google import DRIVE_SCOPE, ConnectionView, OAuthStart
from backend.services.google.client import AUTH_URL, DRIVE_URL, TOKEN_URL
from backend.services.google.credentials import GoogleCredentials
from backend.services.google.errors import GoogleServiceError


class GoogleOAuth(GoogleCredentials):
    def configured(self):
        return bool(
            self.settings.google_connector_enabled
            and self.settings.google_client_id
            and self.settings.google_client_secret
            and self.settings.google_credential_encryption_keys
        )

    def fingerprint(self):
        return hashlib.sha256(
            (
                self.settings.google_client_id
                + "|"
                + self.settings.google_redirect_uri
                + "|"
                + DRIVE_SCOPE
            ).encode()
        ).hexdigest()

    async def begin(self, session, owner):
        from google_auth_oauthlib.flow import Flow

        actor = await session.get(User, owner, populate_existing=True)
        if not self.configured() or actor is None or not actor.is_active:
            raise GoogleServiceError(503, "Google OAuth is not configured")
        redirect = urlsplit(self.settings.google_redirect_uri)
        if (
            redirect.username
            or redirect.password
            or redirect.query
            or redirect.fragment
            or redirect.path != "/connectors/google/callback"
            or not redirect.hostname
            or (
                redirect.scheme != "https"
                and not (
                    redirect.scheme == "http"
                    and redirect.hostname in {"localhost", "127.0.0.1", "::1"}
                )
            )
        ):
            raise GoogleServiceError(503, "Google redirect URI is not configured")
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        digest = hashlib.sha256(state.encode()).hexdigest()
        expires = datetime.now(UTC) + timedelta(minutes=10)
        binding = {"owner_id": str(owner), "state_hash": digest, "kind": "google_oauth"}
        row = GoogleOAuthState(
            state_hash=digest,
            owner_id=owner,
            expires_at=expires,
            ciphertext=self.cipher().seal(
                binding, {"verifier": verifier, "fingerprint": self.fingerprint()}
            ),
        )
        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": self.settings.google_client_id,
                    "client_secret": self.settings.google_client_secret.get_secret_value(),
                    "auth_uri": AUTH_URL,
                    "token_uri": TOKEN_URL,
                }
            },
            scopes=[DRIVE_SCOPE],
            redirect_uri=self.settings.google_redirect_uri,
            code_verifier=verifier,
            autogenerate_code_verifier=False,
        )
        url, _ = flow.authorization_url(
            state=state, access_type="offline", prompt="consent", include_granted_scopes="false"
        )
        session.add(row)
        await session.commit()
        return OAuthStart(authorization_url=url, expires_at=expires)

    async def complete(self, session, owner, *, state, code):
        if not self.configured() or not 1 <= len(state) <= 200 or not 1 <= len(code) <= 4096:
            raise GoogleServiceError(400, "Invalid Google OAuth callback")
        actor = await session.get(User, owner, populate_existing=True)
        if actor is None or not actor.is_active:
            raise GoogleServiceError(404, "Google owner unavailable")
        digest = hashlib.sha256(state.encode()).hexdigest()
        row = await session.scalar(
            select(GoogleOAuthState)
            .where(GoogleOAuthState.state_hash == digest, GoogleOAuthState.owner_id == owner)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None or row.consumed_at or row.expires_at <= datetime.now(UTC):
            await session.rollback()
            raise GoogleServiceError(400, "Google OAuth state expired or already used")
        secret = self.cipher().open(
            {"owner_id": str(owner), "state_hash": digest, "kind": "google_oauth"}, row.ciphertext
        )
        row.consumed_at = datetime.now(UTC)
        await session.commit()  # Fence replay before any token exchange, including failures.
        if secret["fingerprint"] != self.fingerprint():
            raise GoogleServiceError(409, "Google OAuth configuration changed; reconnect")
        response = await self.http.request(
            "POST",
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": self.settings.google_client_id,
                "client_secret": self.settings.google_client_secret.get_secret_value(),
                "redirect_uri": self.settings.google_redirect_uri,
                "code_verifier": secret["verifier"],
                "code": code,
            },
        )
        scopes = response.get("scope", "").split()
        if set(scopes) != {DRIVE_SCOPE}:
            raise GoogleServiceError(409, "Google permission scope was not granted")
        token = self.token(response.get("access_token"))
        about = await self.http.request(
            "GET",
            DRIVE_URL + "/about",
            token=token,
            params={"fields": "user(permissionId,emailAddress,displayName)"},
        )
        user = about.get("user", {})
        if not isinstance(user.get("permissionId"), str) or not user["permissionId"]:
            raise GoogleServiceError(502, "Google account identity unavailable")
        # Serialize reconnects for one owner even when no connection row exists yet.
        await session.scalar(select(User).where(User.id == owner).with_for_update())
        connection = await session.scalar(
            select(GoogleConnection)
            .where(GoogleConnection.owner_id == owner)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if connection is None:
            connection = GoogleConnection(id=uuid4(), owner_id=owner, generation=1)
            session.add(connection)
        else:
            connection.generation += 1
            await session.execute(
                update(GoogleFileGrant)
                .where(GoogleFileGrant.connection_id == connection.id)
                .values(revoked=True)
            )
        connection.account_id = user["permissionId"][:256]
        connection.account_email = user.get("emailAddress", "")[:320] or None
        connection.account_name = user.get("displayName", "")[:256] or None
        connection.granted_scopes = scopes
        connection.offline_ready = bool(response.get("refresh_token"))
        connection.status = "connected" if connection.offline_ready else "reconnect"
        connection.expires_at = datetime.now(UTC) + timedelta(
            seconds=max(1, min(int(response.get("expires_in", 3600)), 86400))
        )
        payload = {"client_id": self.settings.google_client_id, "access_token": token}
        if connection.offline_ready:
            payload["refresh_token"] = self.token(response["refresh_token"])
        self.encrypt(connection, payload)
        await session.commit()
        return ConnectionView.model_validate(connection)


async def begin_google_oauth(session, owner):
    from backend.core.config import get_settings

    return await GoogleOAuth(get_settings()).begin(session, owner)


async def complete_google_oauth(session, owner, *, state, code):
    from backend.core.config import get_settings

    return await GoogleOAuth(get_settings()).complete(session, owner, state=state, code=code)
