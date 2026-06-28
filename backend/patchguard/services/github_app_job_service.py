"""Local worker for queued GitHub App analysis jobs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from patchguard.app_models import (
    GitHubAppAnalysisJob,
    GitHubAppAnalysisReport,
    GitHubAppJobStatus,
)
from patchguard.models import PatchGuardReport, RiskReport
from patchguard.services.github_app_auth_service import GitHubAppAuthService
from patchguard.services.github_app_check_service import (
    GitHubAppCheckService,
    GitHubCheckRunPublishError,
)
from patchguard.services.github_service import GitHubService
from patchguard.services.memory_service import DEFAULT_MEMORY_DB
from patchguard.services.report_service import SkeletonReportService
from patchguard.storage.sqlite_store import GitHubAppSQLiteStore
from patchguard.utils.file_utils import ensure_dir

DEFAULT_GITHUB_APP_DB = Path(".patchguard") / "github_app" / "patchguard-app.db"
DEFAULT_APP_REPORTS_DIR = Path(".patchguard") / "app_reports"
DEFAULT_APP_WORKSPACES_DIR = Path(".patchguard") / "app_workspaces"


@dataclass(frozen=True)
class GitHubAppJobProcessingResult:
    job: GitHubAppAnalysisJob
    report_summary: GitHubAppAnalysisReport | None = None


class GitHubAppJobService:
    """Process queued GitHub App jobs with the existing PatchGuard pipeline."""

    def __init__(
        self,
        *,
        store: GitHubAppSQLiteStore,
        report_service_factory: Callable[[], SkeletonReportService] | None = None,
        check_service_factory: Callable[[], GitHubAppCheckService] | None = None,
        auth_service: GitHubAppAuthService | None = None,
        reports_dir: str | Path = DEFAULT_APP_REPORTS_DIR,
        workspaces_dir: str | Path = DEFAULT_APP_WORKSPACES_DIR,
        skip_llm: bool = True,
        skip_docker: bool = False,
        cleanup_workspace: bool = False,
        compare_base: bool = False,
        use_memory: bool = False,
        memory_db_path: str | Path = DEFAULT_MEMORY_DB,
    ) -> None:
        self.store = store
        self.report_service_factory = report_service_factory
        self.check_service_factory = check_service_factory
        self.auth_service = auth_service
        self.reports_dir = Path(reports_dir)
        self.workspaces_dir = Path(workspaces_dir)
        self.skip_llm = skip_llm
        self.skip_docker = skip_docker
        self.cleanup_workspace = cleanup_workspace
        self.compare_base = compare_base
        self.use_memory = use_memory
        self.memory_db_path = memory_db_path

    def process_next_job(self) -> GitHubAppJobProcessingResult | None:
        job = self.store.claim_next_queued_job()
        if job is None:
            return None
        if job.id is None:
            raise ValueError("Queued analysis job does not include an id.")
        return self._process_running_job(job)

    def process_job(self, job_id: int) -> GitHubAppJobProcessingResult:
        job = self.store.update_job_status(job_id, GitHubAppJobStatus.RUNNING)
        return self._process_running_job(job)

    def _process_running_job(self, job: GitHubAppAnalysisJob) -> GitHubAppJobProcessingResult:
        if job.id is None:
            raise ValueError("Analysis job does not include an id.")
        job_id = job.id
        report_path = self._report_path(job_id)
        check_service = self.check_service_factory() if self.check_service_factory else None
        github_installation_id = self._github_installation_id(job)
        check_error: str | None = None
        if check_service is not None:
            try:
                check_run = check_service.create_in_progress(
                    job,
                    github_installation_id=github_installation_id,
                )
                job = self.store.attach_check_run_to_job(
                    job_id,
                    check_run_id=check_run.id,
                    check_run_url=check_run.html_url,
                )
            except GitHubCheckRunPublishError as exc:
                check_error = f"GitHub Check Run create failed: {exc}"
        try:
            report = self._run_report(job, report_path)
            final_status = status_from_report(report)
            errors = [*report.errors]
            if check_error:
                errors.append(check_error)
            error = "; ".join(errors) if errors else None
            updated_job = self.store.update_job_status(
                job_id,
                final_status,
                report_path=str(report_path),
                error=error,
            )
            summary = self.store.attach_report_summary(
                summary_from_report(job_id, report, report_path)
            )
            if check_service is not None and updated_job.check_run_id is not None:
                try:
                    check_service.update_from_report(
                        updated_job,
                        report,
                        github_installation_id=github_installation_id,
                    )
                except GitHubCheckRunPublishError as exc:
                    updated_job = self.store.update_job_status(
                        job_id,
                        final_status,
                        error=append_error(updated_job.error, f"GitHub Check Run update failed: {exc}"),
                    )
            return GitHubAppJobProcessingResult(
                job=updated_job,
                report_summary=summary,
            )
        except Exception as exc:  # noqa: BLE001 - worker must persist job failures.
            if check_service is not None and job.check_run_id is not None:
                try:
                    check_service.update_for_failure(
                        job,
                        github_installation_id=github_installation_id,
                        error=str(exc),
                    )
                except GitHubCheckRunPublishError as check_exc:
                    check_error = append_error(
                        check_error,
                        f"GitHub Check Run failure update failed: {check_exc}",
                    )
            failed_job = self.store.update_job_status(
                job_id,
                GitHubAppJobStatus.FAILED,
                error=append_error(str(exc), check_error),
            )
            return GitHubAppJobProcessingResult(job=failed_job)

    def _run_report(
        self,
        job: GitHubAppAnalysisJob,
        report_path: Path,
    ) -> RiskReport | PatchGuardReport:
        ensure_dir(report_path.parent)
        pr_url = pr_url_for_job(job)
        service = self._report_service_for_job(job)
        report = service.analyze(
            pr_url,
            report_path,
            workspaces_dir=self.workspaces_dir / f"job-{job.id}",
            cleanup_workspace=self.cleanup_workspace,
            skip_llm=self.skip_llm,
            skip_docker=self.skip_docker,
            compare_base=self.compare_base,
            use_memory=self.use_memory,
            memory_db_path=self.memory_db_path,
        )
        if not report_path.exists():
            report_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return report

    def _report_service_for_job(self, job: GitHubAppAnalysisJob) -> SkeletonReportService:
        if self.report_service_factory is not None:
            return self.report_service_factory()
        token = self._installation_token(job)
        return SkeletonReportService(
            github_service=GitHubService(token=token),
            git_token=token,
        )

    def _report_path(self, job_id: int) -> Path:
        return self.reports_dir / f"job-{job_id}.json"

    def _github_installation_id(self, job: GitHubAppAnalysisJob) -> int:
        installation = self.store.get_installation(job.installation_id)
        return installation.github_installation_id

    def _installation_token(self, job: GitHubAppAnalysisJob) -> str:
        github_installation_id = self._github_installation_id(job)
        auth_service = self.auth_service or GitHubAppAuthService()
        return auth_service.fetch_installation_token(github_installation_id).token


def process_next_job(
    *,
    store: GitHubAppSQLiteStore | None = None,
    db_path: str | Path = DEFAULT_GITHUB_APP_DB,
    report_service_factory: Callable[[], SkeletonReportService] | None = None,
    check_service_factory: Callable[[], GitHubAppCheckService] | None = None,
    auth_service: GitHubAppAuthService | None = None,
    reports_dir: str | Path = DEFAULT_APP_REPORTS_DIR,
    workspaces_dir: str | Path = DEFAULT_APP_WORKSPACES_DIR,
    skip_llm: bool = True,
    skip_docker: bool = False,
    cleanup_workspace: bool = False,
    compare_base: bool = False,
    use_memory: bool = False,
    memory_db_path: str | Path = DEFAULT_MEMORY_DB,
) -> GitHubAppJobProcessingResult | None:
    service = build_job_service(
        store=store,
        db_path=db_path,
        report_service_factory=report_service_factory,
        check_service_factory=check_service_factory,
        auth_service=auth_service,
        reports_dir=reports_dir,
        workspaces_dir=workspaces_dir,
        skip_llm=skip_llm,
        skip_docker=skip_docker,
        cleanup_workspace=cleanup_workspace,
        compare_base=compare_base,
        use_memory=use_memory,
        memory_db_path=memory_db_path,
    )
    return service.process_next_job()


def process_job(
    job_id: int,
    *,
    store: GitHubAppSQLiteStore | None = None,
    db_path: str | Path = DEFAULT_GITHUB_APP_DB,
    report_service_factory: Callable[[], SkeletonReportService] | None = None,
    check_service_factory: Callable[[], GitHubAppCheckService] | None = None,
    auth_service: GitHubAppAuthService | None = None,
    reports_dir: str | Path = DEFAULT_APP_REPORTS_DIR,
    workspaces_dir: str | Path = DEFAULT_APP_WORKSPACES_DIR,
    skip_llm: bool = True,
    skip_docker: bool = False,
    cleanup_workspace: bool = False,
    compare_base: bool = False,
    use_memory: bool = False,
    memory_db_path: str | Path = DEFAULT_MEMORY_DB,
) -> GitHubAppJobProcessingResult:
    service = build_job_service(
        store=store,
        db_path=db_path,
        report_service_factory=report_service_factory,
        check_service_factory=check_service_factory,
        auth_service=auth_service,
        reports_dir=reports_dir,
        workspaces_dir=workspaces_dir,
        skip_llm=skip_llm,
        skip_docker=skip_docker,
        cleanup_workspace=cleanup_workspace,
        compare_base=compare_base,
        use_memory=use_memory,
        memory_db_path=memory_db_path,
    )
    return service.process_job(job_id)


def build_job_service(
    *,
    store: GitHubAppSQLiteStore | None,
    db_path: str | Path,
    report_service_factory: Callable[[], SkeletonReportService] | None,
    check_service_factory: Callable[[], GitHubAppCheckService] | None,
    auth_service: GitHubAppAuthService | None,
    reports_dir: str | Path,
    workspaces_dir: str | Path,
    skip_llm: bool,
    skip_docker: bool,
    cleanup_workspace: bool,
    compare_base: bool,
    use_memory: bool,
    memory_db_path: str | Path,
) -> GitHubAppJobService:
    if store is None:
        store = GitHubAppSQLiteStore(db_path)
        store.initialize()
    return GitHubAppJobService(
        store=store,
        report_service_factory=report_service_factory,
        check_service_factory=check_service_factory,
        auth_service=auth_service,
        reports_dir=reports_dir,
        workspaces_dir=workspaces_dir,
        skip_llm=skip_llm,
        skip_docker=skip_docker,
        cleanup_workspace=cleanup_workspace,
        compare_base=compare_base,
        use_memory=use_memory,
        memory_db_path=memory_db_path,
    )


def append_error(existing: str | None, new: str | None) -> str | None:
    if existing and new:
        return f"{existing}; {new}"
    return existing or new


def pr_url_for_job(job: GitHubAppAnalysisJob) -> str:
    if job.pr_url:
        return job.pr_url
    if job.repository_full_name and job.pr_number is not None:
        return f"https://github.com/{job.repository_full_name}/pull/{job.pr_number}"
    raise ValueError(f"Analysis job {job.id} is missing a pull request URL.")


def status_from_report(report: RiskReport | PatchGuardReport) -> GitHubAppJobStatus:
    if report.status == "complete":
        return GitHubAppJobStatus.COMPLETED
    if report.status == "partial":
        return GitHubAppJobStatus.PARTIAL
    return GitHubAppJobStatus.FAILED


def summary_from_report(
    job_id: int,
    report: RiskReport | PatchGuardReport,
    report_path: Path,
) -> GitHubAppAnalysisReport:
    return GitHubAppAnalysisReport(
        job_id=job_id,
        risk_score=report.risk_score,
        risk_level=report.risk_level,
        merge_decision=report.merge_decision,
        policy_decision=report.policy_decision.decision,
        report_json_path=str(report_path),
    )
