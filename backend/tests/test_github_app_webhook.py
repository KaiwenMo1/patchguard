from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest
from patchguard.api_app import AnalysisStore, create_app
from patchguard.storage.sqlite_store import GitHubAppSQLiteStore

pytestmark = pytest.mark.anyio

WEBHOOK_SECRET = "test-webhook-secret"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def api_client(app) -> httpx.AsyncClient:  # noqa: ANN001
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )


async def test_webhook_rejects_invalid_signature(tmp_path) -> None:
    store = initialized_store(tmp_path)
    app = create_webhook_app(tmp_path, store)
    body = encode_payload(installation_payload())

    async with api_client(app) as client:
        response = await client.post(
            "/github/webhook",
            content=body,
            headers=webhook_headers("installation", "delivery-1", "sha256=bad"),
        )

    assert response.status_code == 401
    assert store.count_rows("webhook_deliveries") == 0
    assert store.count_rows("analysis_jobs") == 0


async def test_webhook_records_valid_installation_delivery(tmp_path) -> None:
    store = initialized_store(tmp_path)
    app = create_webhook_app(tmp_path, store)
    body = encode_payload(installation_payload())

    async with api_client(app) as client:
        response = await client.post(
            "/github/webhook",
            content=body,
            headers=webhook_headers("installation", "delivery-1", sign(body)),
        )

    assert response.status_code == 202
    assert response.json()["status"] == "accepted"
    assert store.count_rows("webhook_deliveries") == 1
    assert store.count_rows("installations") == 1
    assert store.count_rows("analysis_jobs") == 0


async def test_duplicate_pull_request_delivery_does_not_create_duplicate_job(tmp_path) -> None:
    store = initialized_store(tmp_path)
    app = create_webhook_app(tmp_path, store)
    body = encode_payload(pull_request_payload())
    headers = webhook_headers("pull_request", "delivery-pr-1", sign(body))

    async with api_client(app) as client:
        first = await client.post("/github/webhook", content=body, headers=headers)
        duplicate = await client.post("/github/webhook", content=body, headers=headers)

    assert first.status_code == 202
    assert first.json()["jobs_created"] == 1
    assert duplicate.status_code == 202
    assert duplicate.json()["status"] == "duplicate"
    assert duplicate.json()["jobs_created"] == 0
    assert store.count_rows("webhook_deliveries") == 1
    assert store.count_rows("installations") == 1
    assert store.count_rows("repositories") == 1
    assert store.count_rows("analysis_jobs") == 1


async def test_webhook_ignores_unsupported_event_after_recording_delivery(tmp_path) -> None:
    store = initialized_store(tmp_path)
    app = create_webhook_app(tmp_path, store)
    body = encode_payload({"zen": "Approachable is better than simple."})

    async with api_client(app) as client:
        response = await client.post(
            "/github/webhook",
            content=body,
            headers=webhook_headers("ping", "delivery-ping-1", sign(body)),
        )

    assert response.status_code == 202
    assert response.json()["status"] == "ignored"
    assert response.json()["reason"] == "unsupported_event"
    assert store.count_rows("webhook_deliveries") == 1
    assert store.count_rows("analysis_jobs") == 0


async def test_installation_repositories_event_routes_added_repositories(tmp_path) -> None:
    store = initialized_store(tmp_path)
    app = create_webhook_app(tmp_path, store)
    body = encode_payload(installation_repositories_payload())

    async with api_client(app) as client:
        response = await client.post(
            "/github/webhook",
            content=body,
            headers=webhook_headers(
                "installation_repositories",
                "delivery-repos-1",
                sign(body),
            ),
        )

    assert response.status_code == 202
    assert response.json()["status"] == "accepted"
    assert store.count_rows("webhook_deliveries") == 1
    assert store.count_rows("installations") == 1
    assert store.count_rows("repositories") == 2
    assert store.count_rows("analysis_jobs") == 0


def create_webhook_app(tmp_path, store: GitHubAppSQLiteStore):  # noqa: ANN001
    return create_app(
        store=AnalysisStore(tmp_path / "api-runs"),
        github_app_store=store,
        github_webhook_secret=WEBHOOK_SECRET,
    )


def initialized_store(tmp_path) -> GitHubAppSQLiteStore:  # noqa: ANN001
    store = GitHubAppSQLiteStore(tmp_path / "patchguard-app.db")
    store.initialize()
    return store


def webhook_headers(event: str, delivery_id: str, signature: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery_id,
        "X-Hub-Signature-256": signature,
    }


def sign(body: bytes) -> str:
    digest = hmac.new(WEBHOOK_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def encode_payload(payload: dict) -> bytes:  # noqa: ANN001
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def installation_payload() -> dict:
    return {
        "action": "created",
        "installation": {
            "id": 111,
            "account": {"login": "KaiwenMo1", "type": "User"},
        },
        "repositories": [
            {
                "id": 222,
                "full_name": "KaiwenMo1/patchguard",
                "private": False,
                "default_branch": "main",
            }
        ],
    }


def installation_repositories_payload() -> dict:
    payload = installation_payload()
    payload["action"] = "added"
    payload["repositories_added"] = [
        {
            "id": 222,
            "full_name": "KaiwenMo1/patchguard",
            "private": False,
            "default_branch": "main",
        },
        {
            "id": 333,
            "full_name": "KaiwenMo1/demo",
            "private": False,
            "default_branch": "trunk",
        },
    ]
    payload["repositories_removed"] = []
    payload.pop("repositories")
    return payload


def pull_request_payload() -> dict:
    return {
        "action": "opened",
        "installation": {
            "id": 111,
            "account": {"login": "KaiwenMo1", "type": "User"},
        },
        "repository": {
            "id": 222,
            "full_name": "KaiwenMo1/patchguard",
            "private": False,
            "default_branch": "main",
            "owner": {"login": "KaiwenMo1", "type": "User"},
        },
        "number": 42,
        "pull_request": {
            "number": 42,
            "html_url": "https://github.com/KaiwenMo1/patchguard/pull/42",
            "draft": False,
            "head": {"sha": "head-sha"},
            "base": {"sha": "base-sha"},
        },
    }
