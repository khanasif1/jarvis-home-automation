"""Google OAuth authorization-code flow.

Wraps ``google_auth_oauthlib.flow.Flow`` behind a small explicit interface.
The backend acts as a confidential OAuth client: it builds the consent URL,
exchanges the authorization code for tokens, and hands the resulting
credentials to :class:`~home_assistant_api.google.credentials.CredentialStore`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import requests
from google.auth.exceptions import GoogleAuthError
from google_auth_oauthlib.flow import Flow
from oauthlib.oauth2.rfc6749.errors import OAuth2Error

from home_assistant_api.config import GoogleOAuthConfig
from home_assistant_api.errors import ConfigurationError, UpstreamServiceError

# The token exchange can fail for any of these third-party exception types
# depending on where it breaks down (HTTP transport, OAuth2 protocol error,
# or Google credential handling). Each one maps to the same outcome here.
_TOKEN_EXCHANGE_FAILURES = (OAuth2Error, GoogleAuthError, requests.RequestException)


@dataclass(frozen=True)
class StoredCredentialData:
    token: str
    refresh_token: Optional[str]
    token_uri: str
    client_id: str
    client_secret: str
    scopes: tuple[str, ...]
    expiry_iso: Optional[str]


class GoogleOAuthClient:
    """Builds authorization URLs and exchanges codes for tokens."""

    def __init__(self, config: GoogleOAuthConfig) -> None:
        self._config = config

    def _build_flow(self, *, state: Optional[str] = None) -> Flow:
        client_config = {
            "web": {
                "client_id": self._config.client_id,
                "client_secret": self._config.client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [self._config.redirect_uri],
            }
        }
        return Flow.from_client_config(
            client_config,
            scopes=list(self._config.scopes),
            state=state,
            redirect_uri=self._config.redirect_uri,
        )

    def build_authorization_url(self, *, state: str) -> str:
        flow = self._build_flow(state=state)
        authorization_url, _state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
        )
        return authorization_url

    def exchange_code(self, *, code: str, state: Optional[str] = None) -> StoredCredentialData:
        flow = self._build_flow(state=state)
        try:
            flow.fetch_token(code=code)
        except _TOKEN_EXCHANGE_FAILURES as exc:
            raise UpstreamServiceError("Failed to exchange Google authorization code.") from exc

        credentials = flow.credentials
        if credentials is None:  # pragma: no cover - defensive, library guarantees this
            raise UpstreamServiceError("Google token exchange did not return credentials.")

        return StoredCredentialData(
            token=credentials.token,
            refresh_token=credentials.refresh_token,
            token_uri=credentials.token_uri,
            client_id=credentials.client_id,
            client_secret=credentials.client_secret,
            scopes=tuple(credentials.scopes or []),
            expiry_iso=credentials.expiry.isoformat() if credentials.expiry else None,
        )


def require_oauth_client(config: Optional[GoogleOAuthConfig]) -> GoogleOAuthClient:
    if config is None:
        raise ConfigurationError(
            "Google OAuth is not configured. Set GOOGLE_OAUTH_CLIENT_ID, "
            "GOOGLE_OAUTH_CLIENT_SECRET, and GOOGLE_OAUTH_REDIRECT_URI."
        )
    return GoogleOAuthClient(config)
