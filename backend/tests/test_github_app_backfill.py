from __future__ import annotations

from patchguard.app_models import (
    GitHubAppInstallation,
    GitHubAppRepository,
)
from patchguard.cli import main
from patchguard.services.github_app_backfill_service import (
    GitHubAppBackfillService,
)
from patchguard.storage.sqlite_store import GitHubAppSQLiteStore


def test_list_recent_pull_requests_is_bounded_and_uses_github_params(monkeypatch, tmp_path) -> None:
    calls = []

    def fake_get(url, headers, params, timeout):  # noqa: ANN001
        calls.append(
            {
                "url": url,
                "headers": headers,
                "params": params,
                "timeout": timeout,
            }
        )
        return FakeResponse([pr_payload(1, "head-1"), pr_payload(2, "head-2")])

    monkeypatch.setattr("patchguard.services.github_app_backfill_service.requests.get", fake_get)
    service = GitHubAppBackfillService(
        store=initialized_store(tmp_path),
        token="installation-token",
        timeout_seconds=7,
    )

    pull_requests = service.list_recent_pull_requests(
        "KaiwenMo1/patchguard",
        token="installation-token",
        limit=2,
    )

    assert [pr.number for pr in pull_requests] == [1, 2]
    assert calls == [
        {
            "url": "https://api.github.com/repos/KaiwenMo1/patchguard/pulls",
            "headers": {
                "Accept": "application/vnd.github+json",
                "Authorization": "Bearer installation-token",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "PatchGuard-GitHub-App",
            },
            "params": {
                "state": "all",
                "sort": "updated",
                "direction": "desc",
                "per_page": 2,
                "page": 1,
            },
            "timeout": 7,
        }
    ]


def test_backfill_creates_jobs_for_active_selected_repositories(monkeypatch, tmp_path) -> None:
    store = store_with_installation_and_repositories(tmp_path)

    def fake_get(url, headers, params, timeout):  # noqa: ANN001, ARG001
        assert "inactive-repo" not in url
        return FakeResponse(
            [
                pr_payload(42, "head-42", base_sha="base-42"),
                pr_payload(43, "head-43", draft=True),
            ]
        )

    monkeypatch.setattr("patchguard.services.github_app_backfill_service.requests.get", fake_get)

    result = GitHubAppBackfillService(
        store=store,
        token="installation-token",
    ).backfill_installation(98765, limit=10)

    assert result.repositories_scanned == 1
    assert result.pull_requests_seen == 2
    assert result.jobs_created == 1
    assert result.duplicates_skipped == 0
    assert result.draft_prs_skipped == 1
    job = store.get_analysis_job(result.jobs[0].job_id)
    assert job.event_type == "backfill.pull_request"
    assert job.repository_full_name == "KaiwenMo1/patchguard"
    assert job.pr_number == 42
    assert job.pr_url == "https://github.com/KaiwenMo1/patchguard/pull/42"
    assert job.head_sha == "head-42"
    assert job.base_sha == "base-42"


def test_backfill_avoids_duplicate_jobs_for_same_repo_pr_head(monkeypatch, tmp_path) -> None:
    store = store_with_installation_and_repositories(tmp_path)

    def fake_get(url, headers, params, timeout):  # noqa: ANN001, ARG001
        return FakeResponse([pr_payload(42, "same-head")])

    monkeypatch.setattr("patchguard.services.github_app_backfill_service.requests.get", fake_get)
    service = GitHubAppBackfillService(store=store, token="installation-token")

    first = service.backfill_installation(98765, limit=10)
    second = service.backfill_installation(98765, limit=10)

    assert first.jobs_created == 1
    assert second.jobs_created == 0
    assert second.duplicates_skipped == 1
    assert store.count_rows("analysis_jobs") == 1


def test_backfill_allows_same_pr_with_new_head_sha(monkeypatch, tmp_path) -> None:
    store = store_with_installation_and_repositories(tmp_path)
    responses = [
        [pr_payload(42, "old-head")],
        [pr_payload(42, "new-head")],
    ]

    def fake_get(url, headers, params, timeout):  # noqa: ANN001, ARG001
        return FakeResponse(responses.pop(0))

    monkeypatch.setattr("patchguard.services.github_app_backfill_service.requests.get", fake_get)
    service = GitHubAppBackfillService(store=store, token="installation-token")

    first = service.backfill_installation(98765, limit=10)
    second = service.backfill_installation(98765, limit=10)

    assert first.jobs_created == 1
    assert second.jobs_created == 1
    assert store.count_rows("analysis_jobs") == 2
    assert second.jobs[0].head_sha == "new-head"


def test_cli_app_backfill_uses_local_store_and_prints_summary(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    db_path = tmp_path / "patchguard-app.db"
    store = store_with_installation_and_repositories(tmp_path, db_path=db_path)
    assert store.count_rows("repositories") == 2

    def fake_get(url, headers, params, timeout):  # noqa: ANN001, ARG001
        return FakeResponse([pr_payload(42, "head-42")])

    monkeypatch.setattr("patchguard.services.github_app_backfill_service.requests.get", fake_get)

    exit_code = main(
        [
            "app-backfill",
            "--installation-id",
            "98765",
            "--limit",
            "1",
            "--db-path",
            str(db_path),
            "--github-token",
            "installation-token",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "GitHub App installation: 98765" in output
    assert "Repositories scanned: 1" in output
    assert "Jobs created: 1" in output
    assert "KaiwenMo1/patchguard#42 @head-42" in output


def initialized_store(tmp_path, *, db_path=None) -> GitHubAppSQLiteStore:  # noqa: ANN001
    store = GitHubAppSQLiteStore(db_path or tmp_path / "patchguard-app.db")
    store.initialize()
    return store


def store_with_installation_and_repositories(
    tmp_path,
    *,
    db_path=None,  # noqa: ANN001
) -> GitHubAppSQLiteStore:  # noqa: ANN001
    store = initialized_store(tmp_path, db_path=db_path)
    installation = store.upsert_installation(
        GitHubAppInstallation(
            github_installation_id=98765,
            account_login="KaiwenMo1",
            account_type="User",
        )
    )
    store.upsert_repository(
        GitHubAppRepository(
            installation_id=installation.id,
            github_repo_id=1001,
            full_name="KaiwenMo1/patchguard",
            private=False,
            default_branch="main",
            selected=True,
            active=True,
        )
    )
    store.upsert_repository(
        GitHubAppRepository(
            installation_id=installation.id,
            github_repo_id=1002,
            full_name="KaiwenMo1/inactive-repo",
            private=False,
            default_branch="main",
            selected=False,
            active=False,
        )
    )
    return store


def pr_payload(
    number: int,
    head_sha: str,
    *,
    base_sha: str = "base-sha",
    draft: bool = False,
) -> dict:
    return {
        "number": number,
        "title": f"PR {number}",
        "html_url": f"https://github.com/KaiwenMo1/patchguard/pull/{number}",
        "draft": draft,
        "head": {"sha": head_sha},
        "base": {"sha": base_sha},
    }


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
