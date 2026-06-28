"""GitHub App authentication helpers."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import jwt
import requests

from patchguard.app_models import GitHubInstallationToken

GITHUB_API_BASE_URL = "https://api.github.com"
GITHUB_APP_ID_ENV = "PATCHGUARD_GITHUB_APP_ID"
GITHUB_APP_PRIVATE_KEY_PATH_ENV = "PATCHGUARD_GITHUB_APP_PRIVATE_KEY_PATH"
GITHUB_APP_PRIVATE_KEY_ENV = "PATCHGUARD_GITHUB_APP_PRIVATE_KEY"
GITHUB_WEBHOOK_SECRET_ENV = "PATCHGUARD_GITHUB_WEBHOOK_SECRET"


class GitHubAppAuthError(RuntimeError):
    """User-facing GitHub App authentication error."""


class MissingGitHubAppConfigError(GitHubAppAuthError):
    """Raised when required GitHub App environment variables are missing."""


@dataclass(frozen=True)
class GitHubAppAuthConfig:
    app_id: str
    webhook_secret: str
    private_key_path: Path | None = None
    private_key: str | None = None

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> GitHubAppAuthConfig:
        env = environ if environ is not None else os.environ
        missing = [
            name
            for name in (
                GITHUB_APP_ID_ENV,
                GITHUB_WEBHOOK_SECRET_ENV,
            )
            if not env.get(name)
        ]
        if not env.get(GITHUB_APP_PRIVATE_KEY_PATH_ENV) and not env.get(GITHUB_APP_PRIVATE_KEY_ENV):
            missing.append(f"{GITHUB_APP_PRIVATE_KEY_PATH_ENV} or {GITHUB_APP_PRIVATE_KEY_ENV}")
        if missing:
            joined = ", ".join(missing)
            raise MissingGitHubAppConfigError(
                "Missing GitHub App configuration environment variable(s): "
                f"{joined}."
            )
        return cls(
            app_id=env[GITHUB_APP_ID_ENV],
            webhook_secret=env[GITHUB_WEBHOOK_SECRET_ENV],
            private_key_path=(
                Path(env[GITHUB_APP_PRIVATE_KEY_PATH_ENV])
                if env.get(GITHUB_APP_PRIVATE_KEY_PATH_ENV)
                else None
            ),
            private_key=env.get(GITHUB_APP_PRIVATE_KEY_ENV),
        )


class GitHubAppAuthService:
    """Generate app JWTs and installation access tokens for a GitHub App."""

    def __init__(
        self,
        *,
        config: GitHubAppAuthConfig | None = None,
        timeout_seconds: int = 20,
        api_base_url: str = GITHUB_API_BASE_URL,
    ) -> None:
        self.config = config or GitHubAppAuthConfig.from_env()
        self.timeout_seconds = timeout_seconds
        self.api_base_url = api_base_url.rstrip("/")

    def create_app_jwt(self, *, now: datetime | None = None) -> str:
        issued_at = now or datetime.now(UTC)
        if issued_at.tzinfo is None:
            issued_at = issued_at.replace(tzinfo=UTC)
        private_key = self._read_private_key()
        payload = {
            "iat": int((issued_at - timedelta(seconds=60)).timestamp()),
            "exp": int((issued_at + timedelta(minutes=9)).timestamp()),
            "iss": self.config.app_id,
        }
        try:
            encoded = jwt.encode(payload, private_key, algorithm="RS256")
        except Exception as exc:  # pragma: no cover - library-specific details vary
            raise GitHubAppAuthError(f"Could not sign GitHub App JWT: {exc}") from exc
        if not isinstance(encoded, str):
            return encoded.decode("utf-8")
        return encoded

    def fetch_installation_token(self, installation_id: int) -> GitHubInstallationToken:
        app_jwt = self.create_app_jwt()
        url = f"{self.api_base_url}/app/installations/{installation_id}/access_tokens"
        try:
            response = requests.post(
                url,
                headers=self._headers(app_jwt),
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise GitHubAppAuthError(f"GitHub installation token request failed: {exc}") from exc
        if response.status_code >= 400:
            raise GitHubAppAuthError(
                "GitHub installation token request failed with HTTP "
                f"{response.status_code}: {_response_message(response)}"
            )
        payload = response.json()
        try:
            return GitHubInstallationToken(
                token=payload["token"],
                expires_at=payload["expires_at"],
                permissions=payload.get("permissions") or {},
                repository_selection=payload.get("repository_selection"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise GitHubAppAuthError(
                "GitHub installation token response was missing required fields."
            ) from exc

    def _read_private_key(self) -> str:
        if self.config.private_key:
            key = self.config.private_key
            if "\\n" in key and "\n" not in key:
                return key.replace("\\n", "\n")
            return key
        if self.config.private_key_path is None:
            raise GitHubAppAuthError(
                "GitHub App private key is not configured. Set "
                f"{GITHUB_APP_PRIVATE_KEY_PATH_ENV} or {GITHUB_APP_PRIVATE_KEY_ENV}."
            )
        try:
            return self.config.private_key_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise GitHubAppAuthError(
                f"Could not read GitHub App private key at {self.config.private_key_path}: {exc}"
            ) from exc

    @staticmethod
    def _headers(app_jwt: str) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {app_jwt}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "PatchGuard-GitHub-App",
        }


def _response_message(response: requests.Response) -> str:
    try:
        payload: Any = response.json()
    except ValueError:
        return response.text.strip() or response.reason
    if isinstance(payload, dict):
        return str(payload.get("message") or response.reason)
    return response.reason
