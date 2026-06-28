from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import httpx
import pytest
from patchguard.api_app import AnalysisStore, create_app
from patchguard.app_models import GitHubAppJobStatus
from patchguard.storage.sqlite_store import GitHubAppSQLiteStore

pytestmark = pytest.mark.anyio

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "github_app"
WEBHOOK_SECRET = "test-webhook-secret"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def api_client(app) -> httpx.AsyncClient:  # noqa: ANN001
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )


@pytest.mark.parametrize("action", ["opened", "synchronize", "reopened", "ready_for_review"])
async def test_supported_pull_request_actions_create_queued_job(tmp_path, action: str) -> None:
    store = initialized_store(tmp_path)
    app = create_webhook_app(tmp_path, store)
    payload = pull_request_payload(action=action)
    body = encode_payload(payload)

    async with api_client(app) as client:
        response = await client.post(
            "/github/webhook",
            content=body,
            headers=webhook_headers("pull_request", f"delivery-{action}", sign(body)),
        )

    assert response.status_code == 202
    assert response.json()["jobs_created"] == 1
    job = store.get_analysis_job(response.json()["job_id"])
    assert job.status == GitHubAppJobStatus.QUEUED
    assert job.event_type == f"pull_request.{action}"
    assert job.repository_full_name == "KaiwenMo1/patchguard"
    assert job.pr_number == 42
    assert job.pr_url == "https://github.com/KaiwenMo1/patchguard/pull/42"
    assert job.base_sha == "base-sha-123"
    assert job.head_sha == "head-sha-456"
    assert store.count_rows("analysis_jobs") == 1


async def test_draft_pull_request_is_ignored_by_default(tmp_path) -> None:
    store = initialized_store(tmp_path)
    app = create_webhook_app(tmp_path, store)
    body = encode_payload(pull_request_payload(draft=True))

    async with api_client(app) as client:
        response = await client.post(
            "/github/webhook",
            content=body,
            headers=webhook_headers("pull_request", "delivery-draft", sign(body)),
        )

    assert response.status_code == 202
    assert response.json()["jobs_created"] == 0
    assert "job_id" not in response.json()
    assert store.count_rows("webhook_deliveries") == 1
    assert store.count_rows("analysis_jobs") == 0


async def test_draft_pull_request_can_be_enabled(tmp_path) -> None:
    store = initialized_store(tmp_path)
    app = create_webhook_app(tmp_path, store, analyze_draft_prs=True)
    body = encode_payload(pull_request_payload(draft=True))

    async with api_client(app) as client:
        response = await client.post(
            "/github/webhook",
            content=body,
            headers=webhook_headers("pull_request", "delivery-draft-enabled", sign(body)),
        )

    assert response.status_code == 202
    assert response.json()["jobs_created"] == 1
    assert store.count_rows("analysis_jobs") == 1


async def test_unsupported_pull_request_action_does_not_create_job(tmp_path) -> None:
    store = initialized_store(tmp_path)
    app = create_webhook_app(tmp_path, store)
    body = encode_payload(pull_request_payload(action="closed"))

    async with api_client(app) as client:
        response = await client.post(
            "/github/webhook",
            content=body,
            headers=webhook_headers("pull_request", "delivery-closed", sign(body)),
        )

    assert response.status_code == 202
    assert response.json()["jobs_created"] == 0
    assert store.count_rows("analysis_jobs") == 0


async def test_duplicate_pull_request_delivery_does_not_duplicate_job(tmp_path) -> None:
    store = initialized_store(tmp_path)
    app = create_webhook_app(tmp_path, store)
    body = encode_payload(pull_request_payload())
    headers = webhook_headers("pull_request", "delivery-duplicate", sign(body))

    async with api_client(app) as client:
        first = await client.post("/github/webhook", content=body, headers=headers)
        duplicate = await client.post("/github/webhook", content=body, headers=headers)

    assert first.status_code == 202
    assert first.json()["jobs_created"] == 1
    assert duplicate.status_code == 202
    assert duplicate.json()["status"] == "duplicate"
    assert duplicate.json()["jobs_created"] == 0
    assert store.count_rows("webhook_deliveries") == 1
    assert store.count_rows("analysis_jobs") == 1


async def test_distinct_deliveries_for_same_pr_head_do_not_duplicate_job(tmp_path) -> None:
    store = initialized_store(tmp_path)
    app = create_webhook_app(tmp_path, store)
    body = encode_payload(pull_request_payload())

    async with api_client(app) as client:
        first = await client.post(
            "/github/webhook",
            content=body,
            headers=webhook_headers("pull_request", "delivery-head-1", sign(body)),
        )
        second = await client.post(
            "/github/webhook",
            content=body,
            headers=webhook_headers("pull_request", "delivery-head-2", sign(body)),
        )

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["jobs_created"] == 1
    assert second.json()["jobs_created"] == 0
    assert second.json()["job_id"] == first.json()["job_id"]
    assert store.count_rows("webhook_deliveries") == 2
    assert store.count_rows("analysis_jobs") == 1


async def test_pull_request_webhook_does_not_run_analysis_inside_request(tmp_path) -> None:
    store = initialized_store(tmp_path)

    def exploding_report_service():
        raise AssertionError("webhook should only enqueue jobs")

    app = create_webhook_app(
        tmp_path,
        store,
        report_service_factory=exploding_report_service,
    )
    body = encode_payload(pull_request_payload())

    async with api_client(app) as client:
        response = await client.post(
            "/github/webhook",
            content=body,
            headers=webhook_headers("pull_request", "delivery-fast", sign(body)),
        )

    assert response.status_code == 202
    assert response.json()["jobs_created"] == 1
    assert store.count_rows("analysis_jobs") == 1


def create_webhook_app(
    tmp_path,
    store: GitHubAppSQLiteStore,
    *,
    analyze_draft_prs: bool = False,
    report_service_factory=None,  # noqa: ANN001
):  # noqa: ANN001
    return create_app(
        store=AnalysisStore(tmp_path / "api-runs"),
        github_app_store=store,
        github_webhook_secret=WEBHOOK_SECRET,
        github_analyze_draft_prs=analyze_draft_prs,
        report_service_factory=report_service_factory,
    )


def initialized_store(tmp_path) -> GitHubAppSQLiteStore:  # noqa: ANN001
    store = GitHubAppSQLiteStore(tmp_path / "patchguard-app.db")
    store.initialize()
    return store


def pull_request_payload(*, action: str = "opened", draft: bool = False) -> dict:
    payload = load_fixture("pull_request_opened")
    payload["action"] = action
    payload["pull_request"]["draft"] = draft
    return payload


def load_fixture(name: str) -> dict:
    fixture_path = FIXTURE_DIR / f"{name}.json"
    return json.loads(fixture_path.read_text(encoding="utf-8"))


def encode_payload(payload: dict) -> bytes:  # noqa: ANN001
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


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
