"""FastAPI wrapper for PatchGuard analysis runs."""

from __future__ import annotations

import json
import os
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from patchguard.app_models import (
    GitHubAppAnalysisJob,
    GitHubAppAnalysisReport,
    GitHubAppInstallation,
    GitHubAppRepository,
)
from patchguard.services.github_app_auth_service import GITHUB_WEBHOOK_SECRET_ENV
from patchguard.services.github_app_webhook_service import (
    GitHubAppWebhookRouter,
    WebhookSignatureError,
)
from patchguard.services.report_service import SkeletonReportService
from patchguard.storage.sqlite_store import GitHubAppSQLiteStore
from patchguard.utils.file_utils import ensure_dir

AnalysisStatus = Literal[
    "pending",
    "fetching_pr",
    "cloning",
    "analyzing_diff",
    "running_existing_tests",
    "scanning_security",
    "generating_tests",
    "running_generated_tests",
    "completed",
    "failed",
    "partial",
]

TERMINAL_STATUSES = {"completed", "failed", "partial"}
DEFAULT_API_RUNS_DIR = Path(".patchguard") / "api_runs"
DEFAULT_GITHUB_APP_DB = Path(".patchguard") / "github_app" / "patchguard-app.db"
GITHUB_APP_DB_ENV = "PATCHGUARD_APP_DB_PATH"
DEFAULT_CORS_ORIGINS = ["http://127.0.0.1:5173", "http://localhost:5173"]
ANALYZE_DRAFT_PRS_ENV = "PATCHGUARD_ANALYZE_DRAFT_PRS"


class AnalyzePRRequest(BaseModel):
    pr_url: str = Field(..., min_length=1)
    cleanup_workspace: bool = False
    skip_llm: bool = True
    skip_docker: bool = False
    compare_base: bool = False
    use_memory: bool = False
    memory_db_path: str | None = None


class AnalysisRecord(BaseModel):
    analysis_id: str
    pr_url: str
    status: AnalysisStatus
    created_at: datetime
    updated_at: datetime
    report_path: str | None = None
    error: str | None = None


class AnalysisSubmitted(BaseModel):
    analysis_id: str
    status: AnalysisStatus
    status_url: str
    report_url: str


class AppInstallationListResponse(BaseModel):
    count: int
    installations: list[GitHubAppInstallation]


class AppRepositoryListResponse(BaseModel):
    count: int
    repositories: list[GitHubAppRepository]


class AppJobDetail(BaseModel):
    job: GitHubAppAnalysisJob
    report_summary: GitHubAppAnalysisReport | None = None


class AppRepositoryJobsResponse(BaseModel):
    repository: GitHubAppRepository
    count: int
    jobs: list[AppJobDetail]


class AnalysisStore:
    """Persist analysis status and report paths as local JSON files."""

    def __init__(self, root_dir: str | Path = DEFAULT_API_RUNS_DIR) -> None:
        self.root_dir = Path(root_dir)
        self._lock = threading.Lock()
        ensure_dir(self.root_dir)

    def create(self, pr_url: str) -> AnalysisRecord:
        now = datetime.now(UTC)
        record = AnalysisRecord(
            analysis_id=uuid.uuid4().hex,
            pr_url=pr_url,
            status="pending",
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            ensure_dir(self.analysis_dir(record.analysis_id))
            self._write_record(record)
        return record

    def update(
        self,
        analysis_id: str,
        *,
        status_value: AnalysisStatus | None = None,
        report_path: str | None = None,
        error: str | None = None,
    ) -> AnalysisRecord:
        with self._lock:
            record = self.get(analysis_id)
            if status_value is not None:
                record.status = status_value
            if report_path is not None:
                record.report_path = report_path
            if error is not None:
                record.error = error
            record.updated_at = datetime.now(UTC)
            self._write_record(record)
            return record

    def get(self, analysis_id: str) -> AnalysisRecord:
        path = self.status_path(analysis_id)
        if not path.exists():
            raise KeyError(analysis_id)
        return AnalysisRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def analysis_dir(self, analysis_id: str) -> Path:
        return self.root_dir / analysis_id

    def report_path(self, analysis_id: str) -> Path:
        return self.analysis_dir(analysis_id) / "report.json"

    def status_path(self, analysis_id: str) -> Path:
        return self.analysis_dir(analysis_id) / "status.json"

    def _write_record(self, record: AnalysisRecord) -> None:
        path = self.status_path(record.analysis_id)
        ensure_dir(path.parent)
        temporary_path = path.with_suffix(".json.tmp")
        temporary_path.write_text(record.model_dump_json(indent=2) + "\n", encoding="utf-8")
        temporary_path.replace(path)


def create_app(
    *,
    store: AnalysisStore | None = None,
    github_app_store: GitHubAppSQLiteStore | None = None,
    github_webhook_secret: str | None = None,
    github_analyze_draft_prs: bool | None = None,
    report_service_factory: Callable[[], SkeletonReportService] | None = None,
) -> FastAPI:
    app = FastAPI(
        title="PatchGuard API",
        version="0.1.0",
        description="Evidence-backed merge-risk analysis for GitHub pull requests.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins_from_env(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.analysis_store = store or AnalysisStore()
    app.state.github_app_store = github_app_store
    app.state.github_webhook_secret = github_webhook_secret
    app.state.github_analyze_draft_prs = github_analyze_draft_prs
    app.state.report_service_factory = report_service_factory or SkeletonReportService

    @app.post(
        "/api/analyze-pr",
        response_model=AnalysisSubmitted,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def analyze_pr(request: AnalyzePRRequest) -> AnalysisSubmitted:
        analysis_store: AnalysisStore = app.state.analysis_store
        record = analysis_store.create(request.pr_url)
        _start_analysis(app, record.analysis_id, request)
        return AnalysisSubmitted(
            analysis_id=record.analysis_id,
            status=record.status,
            status_url=f"/api/analysis/{record.analysis_id}",
            report_url=f"/api/report/{record.analysis_id}",
        )

    @app.get("/api/analysis/{analysis_id}", response_model=AnalysisRecord)
    async def get_analysis(analysis_id: str) -> AnalysisRecord:
        return _get_record_or_404(app, analysis_id)

    @app.get("/api/report/{analysis_id}")
    async def get_report(analysis_id: str) -> JSONResponse:
        record = _get_record_or_404(app, analysis_id)
        if record.status not in TERMINAL_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Analysis is not finished yet: {record.status}",
            )
        if record.report_path is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Analysis report is unavailable",
            )
        report_path = Path(record.report_path)
        if not report_path.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Analysis report file was not found",
            )
        return JSONResponse(json.loads(report_path.read_text(encoding="utf-8")))

    @app.get("/api/app/installations", response_model=AppInstallationListResponse)
    async def list_app_installations() -> AppInstallationListResponse:
        store = _github_app_store(app)
        installations = store.list_installations()
        return AppInstallationListResponse(
            count=len(installations),
            installations=installations,
        )

    @app.get("/api/app/repositories", response_model=AppRepositoryListResponse)
    async def list_app_repositories() -> AppRepositoryListResponse:
        store = _github_app_store(app)
        repositories = store.list_repositories()
        return AppRepositoryListResponse(
            count=len(repositories),
            repositories=repositories,
        )

    @app.get(
        "/api/app/repositories/{owner}/{repo}/jobs",
        response_model=AppRepositoryJobsResponse,
    )
    async def list_app_repository_jobs(owner: str, repo: str) -> AppRepositoryJobsResponse:
        store = _github_app_store(app)
        repository = _get_repository_or_404(store, f"{owner}/{repo}")
        jobs = store.list_analysis_jobs_for_repository(_required_id(repository.id, "repository"))
        return AppRepositoryJobsResponse(
            repository=repository,
            count=len(jobs),
            jobs=[_app_job_detail(store, job) for job in jobs],
        )

    @app.get("/api/app/jobs/{job_id}", response_model=AppJobDetail)
    async def get_app_job(job_id: int) -> AppJobDetail:
        store = _github_app_store(app)
        return _app_job_detail(store, _get_app_job_or_404(store, job_id))

    @app.get("/api/app/jobs/{job_id}/report")
    async def get_app_job_report(job_id: int) -> JSONResponse:
        store = _github_app_store(app)
        job = _get_app_job_or_404(store, job_id)
        report_path = _app_job_report_path_or_404(store, job)
        return JSONResponse(json.loads(report_path.read_text(encoding="utf-8")))

    @app.post("/github/webhook", status_code=status.HTTP_202_ACCEPTED)
    async def github_webhook(request: Request) -> dict[str, object]:
        body = await request.body()
        event_name = request.headers.get("X-GitHub-Event")
        delivery_id = request.headers.get("X-GitHub-Delivery")
        signature = request.headers.get("X-Hub-Signature-256")
        if not event_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing X-GitHub-Event header.",
            )
        if not delivery_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing X-GitHub-Delivery header.",
            )
        secret = _github_webhook_secret(app)
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Webhook payload must be valid JSON.",
            ) from exc
        router = GitHubAppWebhookRouter(
            store=_github_app_store(app),
            webhook_secret=secret,
            analyze_draft_prs=_github_analyze_draft_prs(app),
        )
        try:
            result = router.handle(
                body=body,
                signature_header=signature,
                event_name=event_name,
                delivery_id=delivery_id,
                payload=payload,
            )
        except WebhookSignatureError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        return result

    return app


def _start_analysis(app: FastAPI, analysis_id: str, request: AnalyzePRRequest) -> None:
    """Start one local analysis worker without blocking API status polling."""

    threading.Thread(
        target=_run_analysis,
        args=(app, analysis_id, request),
        name=f"patchguard-analysis-{analysis_id[:8]}",
        daemon=True,
    ).start()


def _run_analysis(app: FastAPI, analysis_id: str, request: AnalyzePRRequest) -> None:
    analysis_store: AnalysisStore = app.state.analysis_store
    report_service_factory: Callable[[], SkeletonReportService] = app.state.report_service_factory
    report_path = analysis_store.report_path(analysis_id)

    def update_status(status_value: str) -> None:
        if status_value in TERMINAL_STATUSES:
            return
        analysis_store.update(analysis_id, status_value=status_value)  # type: ignore[arg-type]

    try:
        service = report_service_factory()
        report = service.analyze(
            request.pr_url,
            report_path,
            workspaces_dir=analysis_store.analysis_dir(analysis_id) / "workspace",
            cleanup_workspace=request.cleanup_workspace,
            skip_llm=request.skip_llm,
            skip_docker=request.skip_docker,
            compare_base=request.compare_base,
            use_memory=request.use_memory,
            memory_db_path=request.memory_db_path or ".patchguard/memory/patchguard-memory.db",
            status_callback=update_status,
        )
        final_status: AnalysisStatus
        if report.status == "complete":
            final_status = "completed"
        elif report.status == "partial":
            final_status = "partial"
        else:
            final_status = "failed"
        analysis_store.update(
            analysis_id,
            status_value=final_status,
            report_path=str(report_path),
            error="; ".join(report.errors) if report.errors else None,
        )
    except Exception as exc:  # noqa: BLE001 - API status must persist failures.
        analysis_store.update(analysis_id, status_value="failed", error=str(exc))


def _get_record_or_404(app: FastAPI, analysis_id: str) -> AnalysisRecord:
    analysis_store: AnalysisStore = app.state.analysis_store
    try:
        return analysis_store.get(analysis_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Analysis not found: {analysis_id}",
        ) from exc


def _get_repository_or_404(
    store: GitHubAppSQLiteStore,
    full_name: str,
) -> GitHubAppRepository:
    try:
        return store.get_repository_by_full_name(full_name)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"GitHub App repository not found: {full_name}",
        ) from exc


def _get_app_job_or_404(
    store: GitHubAppSQLiteStore,
    job_id: int,
) -> GitHubAppAnalysisJob:
    try:
        return store.get_analysis_job(job_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"GitHub App analysis job not found: {job_id}",
        ) from exc


def _app_job_detail(
    store: GitHubAppSQLiteStore,
    job: GitHubAppAnalysisJob,
) -> AppJobDetail:
    return AppJobDetail(
        job=job,
        report_summary=_app_report_summary_or_none(store, job),
    )


def _app_report_summary_or_none(
    store: GitHubAppSQLiteStore,
    job: GitHubAppAnalysisJob,
) -> GitHubAppAnalysisReport | None:
    try:
        return store.get_report_summary_by_job_id(_required_id(job.id, "analysis job"))
    except KeyError:
        return None


def _app_job_report_path_or_404(
    store: GitHubAppSQLiteStore,
    job: GitHubAppAnalysisJob,
) -> Path:
    report_path: Path | None = None
    try:
        summary = store.get_report_summary_by_job_id(_required_id(job.id, "analysis job"))
        report_path = Path(summary.report_json_path)
    except KeyError:
        if job.report_path:
            report_path = Path(job.report_path)
    if report_path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"GitHub App report is unavailable for job: {job.id}",
        )
    if not report_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"GitHub App report file was not found for job: {job.id}",
        )
    return report_path


def _required_id(value: int | None, model_name: str) -> int:
    if value is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Stored {model_name} row is missing its database id.",
        )
    return value


def _cors_origins_from_env() -> list[str]:
    raw_origins = os.getenv("PATCHGUARD_CORS_ORIGINS")
    if not raw_origins:
        return DEFAULT_CORS_ORIGINS
    origins = [origin.strip() for origin in raw_origins.split(",") if origin.strip()]
    return origins or DEFAULT_CORS_ORIGINS


def _github_app_store(app: FastAPI) -> GitHubAppSQLiteStore:
    store: GitHubAppSQLiteStore | None = app.state.github_app_store
    if store is None:
        store = GitHubAppSQLiteStore(os.getenv(GITHUB_APP_DB_ENV, str(DEFAULT_GITHUB_APP_DB)))
        store.initialize()
        app.state.github_app_store = store
    return store


def _github_webhook_secret(app: FastAPI) -> str:
    secret = app.state.github_webhook_secret or os.getenv(GITHUB_WEBHOOK_SECRET_ENV)
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{GITHUB_WEBHOOK_SECRET_ENV} is not configured.",
        )
    return secret


def _github_analyze_draft_prs(app: FastAPI) -> bool:
    configured: bool | None = app.state.github_analyze_draft_prs
    if configured is not None:
        return configured
    raw_value = os.getenv(ANALYZE_DRAFT_PRS_ENV, "")
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


app = create_app()
