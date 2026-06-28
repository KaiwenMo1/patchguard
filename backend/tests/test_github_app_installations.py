from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import httpx
import pytest
from patchguard.api_app import AnalysisStore, create_app
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


async def test_installation_created_stores_selected_repositories(tmp_path) -> None:
    store = initialized_store(tmp_path)
    app = create_webhook_app(tmp_path, store)

    response = await post_fixture(app, "installation", "delivery-created", "installation_created")

    assert response.status_code == 202
    installation = store.get_installation_by_github_id(98765)
    repositories = store.list_repositories_for_installation(installation.id)
    assert installation.active is True
    assert installation.account_login == "KaiwenMo1"
    assert [repository.full_name for repository in repositories] == [
        "KaiwenMo1/demo-parser",
        "KaiwenMo1/patchguard",
    ]
    assert all(repository.selected for repository in repositories)
    assert all(repository.active for repository in repositories)


async def test_installation_deleted_marks_installation_and_repositories_inactive(tmp_path) -> None:
    store = initialized_store(tmp_path)
    app = create_webhook_app(tmp_path, store)
    await post_fixture(app, "installation", "delivery-created", "installation_created")

    response = await post_fixture(app, "installation", "delivery-deleted", "installation_deleted")

    assert response.status_code == 202
    installation = store.get_installation_by_github_id(98765)
    repositories = store.list_repositories_for_installation(installation.id)
    assert installation.active is False
    assert len(repositories) == 2
    assert all(not repository.selected for repository in repositories)
    assert all(not repository.active for repository in repositories)
    assert store.count_rows("repositories") == 2


async def test_installation_repositories_added_stores_new_selected_repo(tmp_path) -> None:
    store = initialized_store(tmp_path)
    app = create_webhook_app(tmp_path, store)
    await post_fixture(app, "installation", "delivery-created", "installation_created")

    response = await post_fixture(
        app,
        "installation_repositories",
        "delivery-repos-added",
        "installation_repositories_added",
    )

    assert response.status_code == 202
    repository = store.get_repository_by_github_id(1003)
    assert repository.full_name == "KaiwenMo1/security-demo"
    assert repository.private is True
    assert repository.selected is True
    assert repository.active is True
    assert store.count_rows("repositories") == 3


async def test_installation_repositories_removed_marks_repo_inactive(tmp_path) -> None:
    store = initialized_store(tmp_path)
    app = create_webhook_app(tmp_path, store)
    await post_fixture(app, "installation", "delivery-created", "installation_created")

    response = await post_fixture(
        app,
        "installation_repositories",
        "delivery-repos-removed",
        "installation_repositories_removed",
    )

    assert response.status_code == 202
    removed = store.get_repository_by_github_id(1002)
    still_selected = store.get_repository_by_github_id(1001)
    assert removed.full_name == "KaiwenMo1/demo-parser"
    assert removed.selected is False
    assert removed.active is False
    assert still_selected.selected is True
    assert still_selected.active is True
    assert store.count_rows("repositories") == 2


async def post_fixture(
    app,
    event: str,
    delivery_id: str,
    fixture_name: str,
) -> httpx.Response:  # noqa: ANN001
    body = encode_payload(load_fixture(fixture_name))
    async with api_client(app) as client:
        return await client.post(
            "/github/webhook",
            content=body,
            headers=webhook_headers(event, delivery_id, sign(body)),
        )


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
