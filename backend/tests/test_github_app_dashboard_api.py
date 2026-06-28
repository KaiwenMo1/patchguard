from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from patchguard.api_app import AnalysisStore, create_app
from patchguard.app_models import (
    GitHubAppAnalysisJob,
    GitHubAppAnalysisReport,
    GitHubAppInstallation,
    GitHubAppJobStatus,
    GitHubAppRepository,
)
from patchguard.models import (
    MergeDecision,
    PolicyGateDecision,
    PullRequestInfo,
    RiskLevel,
    RiskReport,
)
from patchguard.storage.sqlite_store import GitHubAppSQLiteStore

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def api_client(app) -> httpx.AsyncClient:  # noqa: ANN001
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )


async def test_dashboard_lists_installations_and_repositories(tmp_path) -> None:
    store = initialized_store(tmp_path)
    seed_dashboard_store(tmp_path, store)
    app = dashboard_app(tmp_path, store)

    async with api_client(app) as client:
        installations = await client.get("/api/app/installations")
        repositories = await client.get("/api/app/repositories")

    assert installations.status_code == 200
    installation_payload = installations.json()
    assert installation_payload["count"] == 1
    assert installation_payload["installations"][0]["github_installation_id"] == 98765
    assert installation_payload["installations"][0]["account_login"] == "KaiwenMo1"

    assert repositories.status_code == 200
    repository_payload = repositories.json()
    assert repository_payload["count"] == 2
    assert [repo["full_name"] for repo in repository_payload["repositories"]] == [
        "KaiwenMo1/patchguard",
        "KaiwenMo1/archived-demo",
    ]
    assert repository_payload["repositories"][1]["active"] is False


async def test_dashboard_lists_repository_jobs_with_report_summaries(tmp_path) -> None:
    store = initialized_store(tmp_path)
    seeded = seed_dashboard_store(tmp_path, store)
    app = dashboard_app(tmp_path, store)

    async with api_client(app) as client:
        response = await client.get("/api/app/repositories/KaiwenMo1/patchguard/jobs")

    assert response.status_code == 200
    payload = response.json()
    assert payload["repository"]["full_name"] == "KaiwenMo1/patchguard"
    assert payload["count"] == 2
    assert [item["job"]["id"] for item in payload["jobs"]] == [
        seeded.queued_job.id,
        seeded.completed_job.id,
    ]
    assert payload["jobs"][0]["job"]["status"] == "queued"
    assert payload["jobs"][0]["report_summary"] is None
    assert payload["jobs"][1]["job"]["status"] == "completed"
    assert payload["jobs"][1]["report_summary"]["risk_score"] == 67
    assert payload["jobs"][1]["report_summary"]["policy_decision"] == "warn"


async def test_dashboard_gets_single_job_and_full_report_json(tmp_path) -> None:
    store = initialized_store(tmp_path)
    seeded = seed_dashboard_store(tmp_path, store)
    app = dashboard_app(tmp_path, store)

    async with api_client(app) as client:
        job_response = await client.get(f"/api/app/jobs/{seeded.completed_job.id}")
        report_response = await client.get(f"/api/app/jobs/{seeded.completed_job.id}/report")

    assert job_response.status_code == 200
    job_payload = job_response.json()
    assert job_payload["job"]["pr_number"] == 42
    assert job_payload["job"]["check_run_url"] == "https://github.com/checks/777"
    assert job_payload["report_summary"]["risk_level"] == "high"

    assert report_response.status_code == 200
    report_payload = report_response.json()
    assert report_payload["pr"]["url"] == "https://github.com/KaiwenMo1/patchguard/pull/42"
    assert report_payload["risk_score"] == 67
    assert report_payload["status"] == "complete"


async def test_dashboard_returns_404_for_missing_repository_job_and_report(tmp_path) -> None:
    store = initialized_store(tmp_path)
    seeded = seed_dashboard_store(tmp_path, store)
    app = dashboard_app(tmp_path, store)

    async with api_client(app) as client:
        missing_repo = await client.get("/api/app/repositories/KaiwenMo1/missing/jobs")
        missing_job = await client.get("/api/app/jobs/999999")
        missing_report = await client.get(f"/api/app/jobs/{seeded.queued_job.id}/report")

    assert missing_repo.status_code == 404
    assert "repository not found" in missing_repo.json()["detail"].lower()
    assert missing_job.status_code == 404
    assert "analysis job not found" in missing_job.json()["detail"].lower()
    assert missing_report.status_code == 404
    assert "report is unavailable" in missing_report.json()["detail"].lower()


async def test_dashboard_returns_404_when_report_file_is_missing(tmp_path) -> None:
    store = initialized_store(tmp_path)
    seeded = seed_dashboard_store(tmp_path, store)
    Path(seeded.report_path).unlink()
    app = dashboard_app(tmp_path, store)

    async with api_client(app) as client:
        response = await client.get(f"/api/app/jobs/{seeded.completed_job.id}/report")

    assert response.status_code == 404
    assert "report file was not found" in response.json()["detail"].lower()


class SeededDashboardStore:
    def __init__(
        self,
        *,
        completed_job: GitHubAppAnalysisJob,
        queued_job: GitHubAppAnalysisJob,
        report_path: Path,
    ) -> None:
        self.completed_job = completed_job
        self.queued_job = queued_job
        self.report_path = report_path


def initialized_store(tmp_path) -> GitHubAppSQLiteStore:  # noqa: ANN001
    store = GitHubAppSQLiteStore(tmp_path / "patchguard-app.db")
    store.initialize()
    return store


def dashboard_app(tmp_path, store: GitHubAppSQLiteStore):  # noqa: ANN001
    return create_app(
        store=AnalysisStore(tmp_path / "api-runs"),
        github_app_store=store,
        github_webhook_secret="test-secret",
    )


def seed_dashboard_store(
    tmp_path,
    store: GitHubAppSQLiteStore,
) -> SeededDashboardStore:  # noqa: ANN001
    installation = store.upsert_installation(
        GitHubAppInstallation(
            github_installation_id=98765,
            account_login="KaiwenMo1",
            account_type="User",
        )
    )
    repository = store.upsert_repository(
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
            full_name="KaiwenMo1/archived-demo",
            private=False,
            default_branch="main",
            selected=False,
            active=False,
        )
    )
    completed_job = store.create_analysis_job(
        GitHubAppAnalysisJob(
            installation_id=installation.id,
            repository_id=repository.id,
            repository_full_name=repository.full_name,
            event_type="pull_request.opened",
            pr_number=42,
            pr_url="https://github.com/KaiwenMo1/patchguard/pull/42",
            base_sha="base-sha",
            head_sha="head-sha",
            check_run_id=777,
            check_run_url="https://github.com/checks/777",
        )
    )
    queued_job = store.create_analysis_job(
        GitHubAppAnalysisJob(
            installation_id=installation.id,
            repository_id=repository.id,
            repository_full_name=repository.full_name,
            event_type="pull_request.synchronize",
            pr_number=43,
            pr_url="https://github.com/KaiwenMo1/patchguard/pull/43",
            base_sha="base-sha-2",
            head_sha="head-sha-2",
        )
    )
    report_path = tmp_path / ".patchguard" / "app_reports" / "job-42.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = RiskReport(
        status="complete",
        pr=PullRequestInfo(
            owner="KaiwenMo1",
            repo="patchguard",
            number=42,
            url="https://github.com/KaiwenMo1/patchguard/pull/42",
            title="Improve parser behavior",
        ),
        risk_score=67,
        risk_level=RiskLevel.HIGH,
        merge_decision=MergeDecision.MANUAL_REVIEW,
    )
    report_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    completed_job = store.update_job_status(
        completed_job.id,
        GitHubAppJobStatus.COMPLETED,
        report_path=str(report_path),
    )
    store.attach_report_summary(
        GitHubAppAnalysisReport(
            job_id=completed_job.id,
            risk_score=67,
            risk_level=RiskLevel.HIGH,
            merge_decision=MergeDecision.MANUAL_REVIEW,
            policy_decision=PolicyGateDecision.WARN,
            report_json_path=str(report_path),
        )
    )
    return SeededDashboardStore(
        completed_job=completed_job,
        queued_job=queued_job,
        report_path=report_path,
    )
