from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from patchguard.app_models import (
    GitHubAppAnalysisJob,
    GitHubAppInstallation,
    GitHubAppJobStatus,
    GitHubAppRepository,
    GitHubInstallationToken,
)
from patchguard.models import (
    MergeDecision,
    PolicyDecision,
    PolicyGateDecision,
    PullRequestInfo,
    RiskLevel,
    RiskReport,
)
from patchguard.services.github_app_job_service import GitHubAppJobService
from patchguard.storage.sqlite_store import GitHubAppSQLiteStore


def test_process_next_job_marks_running_then_completed_and_saves_summary(tmp_path) -> None:
    store = initialized_store(tmp_path)
    job = sample_job(store)
    report_service = StatusAssertingReportService(store, job.id, status="complete")
    service = job_service(tmp_path, store, lambda: report_service)

    result = service.process_next_job()

    assert result is not None
    assert result.job.status == GitHubAppJobStatus.COMPLETED
    assert result.job.report_path is not None
    assert Path(result.job.report_path).exists()
    assert report_service.received_pr_url == "https://github.com/KaiwenMo1/patchguard/pull/42"
    assert report_service.received_options["skip_llm"] is True
    assert report_service.received_options["skip_docker"] is False
    assert report_service.received_options["compare_base"] is False
    assert report_service.received_options["use_memory"] is False
    assert result.report_summary is not None
    assert result.report_summary.risk_score == 44
    assert result.report_summary.risk_level == RiskLevel.MEDIUM
    assert result.report_summary.merge_decision == MergeDecision.MERGE_WITH_CAUTION
    assert result.report_summary.policy_decision == PolicyGateDecision.WARN
    stored_summary = store.get_report_summary_by_job_id(job.id)
    assert stored_summary.report_json_path == result.job.report_path


def test_process_next_job_returns_none_when_queue_is_empty(tmp_path) -> None:
    store = initialized_store(tmp_path)
    service = job_service(tmp_path, store, lambda: StatusAssertingReportService(store, 0))

    assert service.process_next_job() is None


def test_process_job_marks_partial_and_preserves_report_summary(tmp_path) -> None:
    store = initialized_store(tmp_path)
    job = sample_job(store)
    service = job_service(
        tmp_path,
        store,
        lambda: StatusAssertingReportService(
            store,
            job.id,
            status="partial",
            errors=["Docker unavailable"],
        ),
    )

    result = service.process_job(job.id)

    assert result.job.status == GitHubAppJobStatus.PARTIAL
    assert result.job.error == "Docker unavailable"
    assert result.job.report_path is not None
    assert Path(result.job.report_path).exists()
    assert result.report_summary is not None
    assert store.get_report_summary_by_job_id(job.id).risk_score == 44


def test_process_job_marks_failed_when_report_service_raises(tmp_path) -> None:
    store = initialized_store(tmp_path)
    job = sample_job(store)
    service = job_service(tmp_path, store, lambda: ExplodingReportService())

    result = service.process_job(job.id)

    assert result.job.status == GitHubAppJobStatus.FAILED
    assert "boom" in (result.job.error or "")
    assert result.job.report_path is None
    assert result.report_summary is None
    with pytest.raises(KeyError):
        store.get_report_summary_by_job_id(job.id)


def test_process_job_publishes_github_check_when_configured(tmp_path) -> None:
    store = initialized_store(tmp_path)
    job = sample_job(store)
    check_service = FakeCheckService()
    service = job_service(
        tmp_path,
        store,
        lambda: StatusAssertingReportService(store, job.id),
        check_service_factory=lambda: check_service,
    )

    result = service.process_job(job.id)

    assert result.job.status == GitHubAppJobStatus.COMPLETED
    assert result.job.check_run_id == 777
    assert result.job.check_run_url == "https://github.com/checks/777"
    assert check_service.calls == [
        ("create", "running", 98765),
        ("update", "completed", 98765, 44),
    ]


def test_default_report_service_uses_installation_token_for_api_and_git(
    tmp_path,
    monkeypatch,
) -> None:
    store = initialized_store(tmp_path)
    job = sample_job(store)
    constructed_services = []

    class RecordingReportService(StatusAssertingReportService):
        def __init__(self, *, github_service, git_token):  # noqa: ANN001
            super().__init__(store, job.id)
            constructed_services.append((github_service.token, git_token))

    monkeypatch.setattr(
        "patchguard.services.github_app_job_service.SkeletonReportService",
        RecordingReportService,
    )
    service = job_service(
        tmp_path,
        store,
        report_service_factory=None,
        auth_service=FakeAuthService("installation-token"),
    )

    result = service.process_job(job.id)

    assert result.job.status == GitHubAppJobStatus.COMPLETED
    assert constructed_services == [("installation-token", "installation-token")]


def initialized_store(tmp_path) -> GitHubAppSQLiteStore:  # noqa: ANN001
    store = GitHubAppSQLiteStore(tmp_path / "patchguard-app.db")
    store.initialize()
    return store


def job_service(
    tmp_path,
    store: GitHubAppSQLiteStore,
    report_service_factory,
    *,
    check_service_factory=None,  # noqa: ANN001
    auth_service=None,  # noqa: ANN001
) -> GitHubAppJobService:  # noqa: ANN001
    return GitHubAppJobService(
        store=store,
        report_service_factory=report_service_factory,
        check_service_factory=check_service_factory,
        auth_service=auth_service,
        reports_dir=tmp_path / ".patchguard" / "app_reports",
        workspaces_dir=tmp_path / ".patchguard" / "app_workspaces",
    )


def sample_job(store: GitHubAppSQLiteStore) -> GitHubAppAnalysisJob:
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
        )
    )
    job = store.create_analysis_job(
        GitHubAppAnalysisJob(
            installation_id=installation.id,
            repository_id=repository.id,
            repository_full_name=repository.full_name,
            event_type="pull_request.opened",
            pr_number=42,
            pr_url="https://github.com/KaiwenMo1/patchguard/pull/42",
            base_sha="base-sha",
            head_sha="head-sha",
        )
    )
    assert job.id is not None
    return job


class StatusAssertingReportService:
    def __init__(
        self,
        store: GitHubAppSQLiteStore,
        job_id: int,
        *,
        status: str = "complete",
        errors: list[str] | None = None,
    ) -> None:
        self.store = store
        self.job_id = job_id
        self.status = status
        self.errors = errors or []
        self.received_pr_url: str | None = None
        self.received_options = {}

    def analyze(
        self,
        pr_url: str,
        output_path: str | Path,
        *,
        workspaces_dir=None,  # noqa: ANN001
        cleanup_workspace: bool = False,
        skip_llm: bool = False,
        skip_docker: bool = False,
        compare_base: bool = False,
        use_memory: bool = False,
        memory_db_path: str | Path | None = None,
    ) -> RiskReport:
        running_job = self.store.get_analysis_job(self.job_id)
        assert running_job.status == GitHubAppJobStatus.RUNNING
        self.received_pr_url = pr_url
        self.received_options = {
            "workspaces_dir": workspaces_dir,
            "cleanup_workspace": cleanup_workspace,
            "skip_llm": skip_llm,
            "skip_docker": skip_docker,
            "compare_base": compare_base,
            "use_memory": use_memory,
            "memory_db_path": memory_db_path,
        }
        report = RiskReport(
            status=self.status,
            errors=self.errors,
            pr=PullRequestInfo(
                owner="KaiwenMo1",
                repo="patchguard",
                number=42,
                url=pr_url,
                title="Improve parser behavior",
            ),
            risk_score=44,
            risk_level=RiskLevel.MEDIUM,
            merge_decision=MergeDecision.MERGE_WITH_CAUTION,
            policy_decision=PolicyDecision(decision=PolicyGateDecision.WARN),
        )
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return report


class ExplodingReportService:
    def analyze(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("boom")


class FakeCheckRun:
    id = 777
    html_url = "https://github.com/checks/777"


class FakeCheckService:
    def __init__(self) -> None:
        self.calls = []

    def create_in_progress(self, job, *, github_installation_id):  # noqa: ANN001
        self.calls.append(("create", job.status.value, github_installation_id))
        return FakeCheckRun()

    def update_from_report(self, job, report, *, github_installation_id):  # noqa: ANN001
        self.calls.append(
            (
                "update",
                job.status.value,
                github_installation_id,
                report.risk_score,
            )
        )
        return FakeCheckRun()


class FakeAuthService:
    def __init__(self, token: str) -> None:
        self.token = token

    def fetch_installation_token(self, installation_id: int) -> GitHubInstallationToken:
        assert installation_id == 98765
        return GitHubInstallationToken(
            token=self.token,
            expires_at=datetime.now(UTC),
        )
