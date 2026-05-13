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

from fastapi import BackgroundTasks, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from patchguard.services.report_service import SkeletonReportService
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
DEFAULT_CORS_ORIGINS = ["http://127.0.0.1:5173", "http://localhost:5173"]


class AnalyzePRRequest(BaseModel):
    pr_url: str = Field(..., min_length=1)
    cleanup_workspace: bool = False
    skip_llm: bool = True
    skip_docker: bool = False


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
        path.write_text(record.model_dump_json(indent=2) + "\n", encoding="utf-8")


def create_app(
    *,
    store: AnalysisStore | None = None,
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
    app.state.report_service_factory = report_service_factory or SkeletonReportService

    @app.post(
        "/api/analyze-pr",
        response_model=AnalysisSubmitted,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def analyze_pr(request: AnalyzePRRequest, background_tasks: BackgroundTasks) -> AnalysisSubmitted:
        analysis_store: AnalysisStore = app.state.analysis_store
        record = analysis_store.create(request.pr_url)
        background_tasks.add_task(_run_analysis, app, record.analysis_id, request)
        return AnalysisSubmitted(
            analysis_id=record.analysis_id,
            status=record.status,
            status_url=f"/api/analysis/{record.analysis_id}",
            report_url=f"/api/report/{record.analysis_id}",
        )

    @app.get("/api/analysis/{analysis_id}", response_model=AnalysisRecord)
    def get_analysis(analysis_id: str) -> AnalysisRecord:
        return _get_record_or_404(app, analysis_id)

    @app.get("/api/report/{analysis_id}")
    def get_report(analysis_id: str) -> JSONResponse:
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

    return app


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


def _cors_origins_from_env() -> list[str]:
    raw_origins = os.getenv("PATCHGUARD_CORS_ORIGINS")
    if not raw_origins:
        return DEFAULT_CORS_ORIGINS
    origins = [origin.strip() for origin in raw_origins.split(",") if origin.strip()]
    return origins or DEFAULT_CORS_ORIGINS


app = create_app()
