"""Structured report models for PatchGuard."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class RunStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class MergeDecision(StrEnum):
    MERGE = "merge"
    MERGE_WITH_CAUTION = "merge_with_caution"
    MANUAL_REVIEW = "manual_review"
    DO_NOT_MERGE = "do_not_merge"


class MergeRecommendation(StrEnum):
    DO_NOT_MERGE_EXISTING_TESTS = "Do not merge: existing tests failed."
    REVIEW_GENERATED_FAILURES = "Review generated failing cases before merge."
    DO_NOT_MERGE_SECURITY = "Do not merge until security issue is reviewed."
    HUMAN_REVIEW = "Merge only after human review."
    LIKELY_SAFE = "Likely safe to merge after normal review."


class CommandResult(BaseModel):
    command: list[str]
    exit_code: int | None = None
    stdout_tail: str = ""
    stderr_tail: str = ""
    duration_seconds: float = 0.0
    timed_out: bool = False
    skipped: bool = False
    skip_reason: str | None = None

    @property
    def succeeded(self) -> bool:
        return not self.skipped and not self.timed_out and self.exit_code == 0


class ToolRun(BaseModel):
    name: str
    kind: Literal[
        "clone",
        "docker_build",
        "dependency_install",
        "existing_tests",
        "test_generation",
        "generated_tests",
        "static_analysis",
        "security_scan",
    ]
    status: RunStatus
    summary: str
    command: CommandResult | None = None
    findings_count: int = 0


class ChangedFile(BaseModel):
    filename: str
    status: str
    additions: int = 0
    deletions: int = 0
    changes: int = 0
    patch: str | None = None
    classification: str | None = None
    previous_filename: str | None = None
    raw_url: str | None = None
    blob_url: str | None = None

    @property
    def is_python(self) -> bool:
        return self.filename.endswith(".py")

    @property
    def is_test(self) -> bool:
        path = self.filename.lower()
        return (
            path.startswith("tests/")
            or "/tests/" in path
            or path.endswith("_test.py")
            or path.endswith("test.py")
            or "/test_" in path
            or path.rsplit("/", 1)[-1].startswith("test_")
        )


class PullRequestInfo(BaseModel):
    owner: str
    repo: str
    number: int
    url: str
    title: str | None = None
    author: str | None = None
    state: str | None = None
    is_draft: bool = False
    base_ref: str | None = None
    base_sha: str | None = None
    base_repo_full_name: str | None = None
    head_ref: str | None = None
    head_sha: str | None = None
    head_repo_full_name: str | None = None
    additions: int = 0
    deletions: int = 0
    changed_files_count: int = 0


class TestResult(BaseModel):
    name: str
    status: RunStatus = RunStatus.SKIPPED
    command: str | None = None
    stdout: str = ""
    stderr: str = ""


class SecurityFinding(BaseModel):
    tool: str
    severity: str
    confidence: str | None = None
    filename: str | None = None
    line_number: int | None = None
    message: str = ""
    file: str | None = None
    line: int | None = None
    issue_text: str = ""
    issue_code: str | None = None
    more_info: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class StaticFinding(BaseModel):
    tool: str
    code: str | None = None
    message: str
    file: str | None = None
    line: int | None = None
    severity: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class RiskReason(BaseModel):
    category: str
    score_impact: int
    reason: str


class ChangedFunction(BaseModel):
    file_path: str
    qualified_name: str
    symbol_type: Literal["function", "async_function", "class", "method", "async_method", "file"]
    start_line: int
    end_line: int
    source_code: str
    changed_lines: list[int] = Field(default_factory=list)
    fallback: bool = False
    parse_error: str | None = None


class GeneratedTest(BaseModel):
    path: str
    target_files: list[str]
    rationale: str
    target_functions: list[str] = Field(default_factory=list)
    code: str = ""
    provider: str | None = None
    model: str | None = None


class RiskReport(BaseModel):
    version: str = "0.1.0"
    generated_at: datetime = Field(default_factory=utc_now)
    status: Literal["complete", "partial", "failed"] = "partial"
    errors: list[str] = Field(default_factory=list)
    pr: PullRequestInfo
    changed_files: list[ChangedFile] = Field(default_factory=list)
    changed_functions: list[ChangedFunction] = Field(default_factory=list)
    generated_tests: list[GeneratedTest] = Field(default_factory=list)
    test_generation: ToolRun | None = None
    generated_test_results: list[ToolRun] = Field(default_factory=list)
    test_results: list[TestResult] = Field(default_factory=list)
    security_findings: list[SecurityFinding] = Field(default_factory=list)
    static_analysis_results: list[ToolRun] = Field(default_factory=list)
    static_findings: list[StaticFinding] = Field(default_factory=list)
    clone_results: list[ToolRun] = Field(default_factory=list)
    sandbox_results: list[ToolRun] = Field(default_factory=list)
    dependency_install: ToolRun | None = None
    existing_tests: ToolRun | None = None
    workspace_path: str | None = None
    risk_score: int = 0
    risk_level: RiskLevel = RiskLevel.LOW
    risk_reasons: list[RiskReason] = Field(default_factory=list)
    merge_decision: MergeDecision = MergeDecision.MANUAL_REVIEW
    recommendation: MergeRecommendation = MergeRecommendation.HUMAN_REVIEW
    report_path: str | None = None


class PRMetadata(BaseModel):
    owner: str
    repo: str
    number: int
    title: str
    author: str
    state: str
    is_draft: bool
    html_url: str
    base_ref: str
    base_sha: str
    base_repo_full_name: str
    base_clone_url: str
    head_ref: str
    head_sha: str
    head_repo_full_name: str
    head_clone_url: str
    changed_files_count: int
    additions: int
    deletions: int


class FunctionSymbol(BaseModel):
    file_path: str
    symbol_type: Literal["function", "async_function", "class", "method", "async_method"]
    name: str
    line: int
    signature: str | None = None


class PatchGuardReport(BaseModel):
    version: str = "0.1.0"
    generated_at: datetime = Field(default_factory=utc_now)
    input_pr_url: str
    status: Literal["complete", "partial", "failed"] = "partial"
    errors: list[str] = Field(default_factory=list)
    pr: PRMetadata | None = None
    changed_files: list[ChangedFile] = Field(default_factory=list)
    changed_symbols: list[FunctionSymbol] = Field(default_factory=list)
    changed_functions: list[ChangedFunction] = Field(default_factory=list)
    generated_tests: list[GeneratedTest] = Field(default_factory=list)
    test_generation: ToolRun | None = None
    sandbox_results: list[ToolRun] = Field(default_factory=list)
    existing_test_results: list[ToolRun] = Field(default_factory=list)
    generated_test_results: list[ToolRun] = Field(default_factory=list)
    static_analysis_results: list[ToolRun] = Field(default_factory=list)
    static_findings: list[StaticFinding] = Field(default_factory=list)
    security_findings: list[SecurityFinding] = Field(default_factory=list)
    risk_score: int = 0
    risk_level: RiskLevel = RiskLevel.LOW
    risk_reasons: list[RiskReason] = Field(default_factory=list)
    merge_decision: MergeDecision = MergeDecision.MANUAL_REVIEW
    recommendation: MergeRecommendation = MergeRecommendation.HUMAN_REVIEW
    report_path: str | None = None
