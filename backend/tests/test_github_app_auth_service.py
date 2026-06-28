from __future__ import annotations

from datetime import UTC, datetime

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from patchguard.services.github_app_auth_service import (
    GITHUB_APP_ID_ENV,
    GITHUB_APP_PRIVATE_KEY_ENV,
    GITHUB_APP_PRIVATE_KEY_PATH_ENV,
    GITHUB_WEBHOOK_SECRET_ENV,
    GitHubAppAuthConfig,
    GitHubAppAuthError,
    GitHubAppAuthService,
    MissingGitHubAppConfigError,
)


def test_config_from_env_reports_missing_values(monkeypatch) -> None:
    monkeypatch.delenv(GITHUB_APP_ID_ENV, raising=False)
    monkeypatch.delenv(GITHUB_APP_PRIVATE_KEY_ENV, raising=False)
    monkeypatch.delenv(GITHUB_APP_PRIVATE_KEY_PATH_ENV, raising=False)
    monkeypatch.delenv(GITHUB_WEBHOOK_SECRET_ENV, raising=False)

    with pytest.raises(MissingGitHubAppConfigError) as exc_info:
        GitHubAppAuthConfig.from_env()

    message = str(exc_info.value)
    assert GITHUB_APP_ID_ENV in message
    assert GITHUB_APP_PRIVATE_KEY_PATH_ENV in message
    assert GITHUB_APP_PRIVATE_KEY_ENV in message
    assert GITHUB_WEBHOOK_SECRET_ENV in message


def test_config_from_env_loads_required_values(monkeypatch, tmp_path) -> None:
    key_path = write_private_key(tmp_path)
    monkeypatch.setenv(GITHUB_APP_ID_ENV, "12345")
    monkeypatch.setenv(GITHUB_APP_PRIVATE_KEY_PATH_ENV, str(key_path))
    monkeypatch.setenv(GITHUB_WEBHOOK_SECRET_ENV, "webhook-secret")

    config = GitHubAppAuthConfig.from_env()

    assert config.app_id == "12345"
    assert config.private_key_path == key_path
    assert config.webhook_secret == "webhook-secret"


def test_config_from_env_can_load_private_key_content(monkeypatch) -> None:
    private_key = generate_private_key_pem().decode("utf-8")
    monkeypatch.setenv(GITHUB_APP_ID_ENV, "12345")
    monkeypatch.delenv(GITHUB_APP_PRIVATE_KEY_PATH_ENV, raising=False)
    monkeypatch.setenv(GITHUB_APP_PRIVATE_KEY_ENV, private_key)
    monkeypatch.setenv(GITHUB_WEBHOOK_SECRET_ENV, "webhook-secret")

    config = GitHubAppAuthConfig.from_env()

    assert config.app_id == "12345"
    assert config.private_key_path is None
    assert config.private_key == private_key


def test_create_app_jwt_signs_expected_claims(tmp_path) -> None:
    service = GitHubAppAuthService(
        config=GitHubAppAuthConfig(
            app_id="12345",
            private_key_path=write_private_key(tmp_path),
            webhook_secret="webhook-secret",
        )
    )
    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=UTC)

    token = service.create_app_jwt(now=now)

    header = jwt.get_unverified_header(token)
    claims = jwt.decode(token, options={"verify_signature": False})
    assert header["alg"] == "RS256"
    assert claims["iss"] == "12345"
    assert claims["iat"] == int(now.timestamp()) - 60
    assert claims["exp"] == int(now.timestamp()) + 540


def test_fetch_installation_token_uses_mocked_github(monkeypatch, tmp_path) -> None:
    calls: list[tuple[str, dict[str, str], int]] = []

    def fake_post(url, headers, timeout):  # noqa: ANN001
        calls.append((url, headers, timeout))
        assert url == "https://api.github.com/app/installations/999/access_tokens"
        assert headers["Authorization"].startswith("Bearer ")
        assert headers["Accept"] == "application/vnd.github+json"
        return FakeResponse(
            {
                "token": "ghs_installation_token",
                "expires_at": "2026-06-25T13:00:00Z",
                "permissions": {"contents": "read", "pull_requests": "write"},
                "repository_selection": "selected",
            }
        )

    monkeypatch.setattr("patchguard.services.github_app_auth_service.requests.post", fake_post)
    service = GitHubAppAuthService(
        config=GitHubAppAuthConfig(
            app_id="12345",
            private_key_path=write_private_key(tmp_path),
            webhook_secret="webhook-secret",
        ),
        timeout_seconds=7,
    )

    token = service.fetch_installation_token(999)

    assert token.token == "ghs_installation_token"
    assert token.expires_at == datetime(2026, 6, 25, 13, 0, 0, tzinfo=UTC)
    assert token.permissions == {"contents": "read", "pull_requests": "write"}
    assert token.repository_selection == "selected"
    assert calls[0][2] == 7


def test_fetch_installation_token_maps_http_failure(monkeypatch, tmp_path) -> None:
    def fake_post(url, headers, timeout):  # noqa: ANN001, ARG001
        return FakeResponse({"message": "Bad credentials"}, status_code=401)

    monkeypatch.setattr("patchguard.services.github_app_auth_service.requests.post", fake_post)
    service = GitHubAppAuthService(
        config=GitHubAppAuthConfig(
            app_id="12345",
            private_key_path=write_private_key(tmp_path),
            webhook_secret="webhook-secret",
        )
    )

    with pytest.raises(GitHubAppAuthError, match="HTTP 401: Bad credentials"):
        service.fetch_installation_token(999)


def write_private_key(tmp_path):  # noqa: ANN001
    key_path = tmp_path / "patchguard-test-private-key.pem"
    key_path.write_bytes(generate_private_key_pem())
    return key_path


def generate_private_key_pem() -> bytes:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )


class FakeResponse:
    def __init__(
        self,
        payload,
        *,
        status_code: int = 200,
        text: str = "",
        reason: str = "OK",
    ) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = text
        self.reason = reason

    def json(self):
        return self._payload
