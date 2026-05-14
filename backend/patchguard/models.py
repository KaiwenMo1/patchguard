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


class PolicyGateDecision(StrEnum):
    PASS = "pass"
    WARN = "warn"
    BLOCK = "block"


class PolicyConfig(BaseModel):
    risk_threshold: int = 70
    block_on: list[str] = Field(
        default_factory=lambda: [
            "generated_test_failure",
            "existing_test_failure",
            "high_security_finding",
            "secret_detected",
            "auth_code_without_tests",
        ]
    )
    sensitive_paths: list[str] = Field(
        default_factory=lambda: [
            "auth/",
            "security/",
            "payments/",
            "api/routes/",
        ]
    )
    allow_merge_with_caution_below: int = 60


class PolicyDecision(BaseModel):
    decision: PolicyGateDecision = PolicyGateDecision.PASS
    reasons: list[str] = Field(default_factory=list)
    triggered_rules: list[str] = Field(default_factory=list)
    config_path: str | None = None


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
        "contract_extraction",
        "test_generation",
        "generated_tests",
        "static_analysis",
        "security_scan",
        "ai_review",
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
    severity: Literal["info", "low", "medium", "high", "critical"] = "medium"
    evidence: list[str] = Field(default_factory=list)


class SecurityFindingCounts(BaseModel):
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0


class RiskInput(BaseModel):
    changed_files_count: int = 0
    total_lines_changed: int = 0
    changed_functions_count: int = 0
    source_changed: bool = False
    tests_changed: bool = False
    dependency_files_changed: bool = False
    config_files_changed: bool = False
    security_sensitive_files_changed: bool = False
    existing_tests_status: Literal["passed", "failed", "skipped", "error", "not_run"] = "not_run"
    generated_tests_status: Literal["passed", "failed", "skipped", "error", "not_run"] = "not_run"
    generated_tests_failed_count: int = 0
    existing_tests_failed_count: int = 0
    security_findings_by_severity: SecurityFindingCounts = Field(
        default_factory=SecurityFindingCounts
    )
    secrets_detected: bool = False
    dependency_install_failed: bool = False
    no_existing_tests_found: bool = False
    pr_description_missing: bool = False
    diff_too_large_for_full_analysis: bool = False
    behavior_changed: bool = False
    behavior_change_type: str | None = None
    behavior_risky_categories: list[str] = Field(default_factory=list)
    behavior_confidence: float | None = None


class RiskBreakdown(BaseModel):
    overall_score: int = 0
    risk_level: RiskLevel = RiskLevel.LOW
    change_size_risk: int = 0
    test_coverage_risk: int = 0
    behavioral_risk: int = 0
    security_risk: int = 0
    uncertainty_risk: int = 0
    reasons: list[RiskReason] = Field(default_factory=list)


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


class BehavioralContract(BaseModel):
    intended_new_behaviors: list[str] = Field(default_factory=list)
    existing_behaviors_to_preserve: list[str] = Field(default_factory=list)
    edge_cases_to_test: list[str] = Field(default_factory=list)
    invalid_inputs_to_test: list[str] = Field(default_factory=list)
    contract_uncertainties: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class GeneratedTest(BaseModel):
    path: str
    target_files: list[str]
    rationale: str
    target_functions: list[str] = Field(default_factory=list)
    code: str = ""
    provider: str | None = None
    model: str | None = None
    metadata: list[GeneratedTestMetadata] = Field(default_factory=list)


class GeneratedTestMetadata(BaseModel):
    test_name: str
    target_file: str
    target_function: str
    behavior_checked: str
    test_type: Literal["new_behavior", "regression", "edge_case", "security", "unknown"] = "regression"


class FailureMapping(BaseModel):
    failed_test: str
    target_file: str | None = None
    target_function: str | None = None
    behavior_checked: str | None = None
    failure_summary: str
    risk_message: str
    suggested_next_step: str = "Review the generated test failure before merging."


class EvidenceRisk(BaseModel):
    title: str
    severity: Literal["low", "medium", "high", "critical"] = "medium"
    evidence: list[str] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)
    suggested_fix: str = ""


class EvidenceBasedReview(BaseModel):
    merge_recommendation: Literal[
        "merge",
        "merge_with_caution",
        "do_not_merge",
        "needs_human_review",
    ] = "needs_human_review"
    executive_summary: str = ""
    pr_change_summary: list[str] = Field(default_factory=list)
    correctness_notes: list[str] = Field(default_factory=list)
    efficiency_notes: list[str] = Field(default_factory=list)
    top_risks: list[EvidenceRisk] = Field(default_factory=list)
    files_to_review_first: list[str] = Field(default_factory=list)
    suggested_followup_tests: list[str] = Field(default_factory=list)
    suggested_fixes: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class RiskReport(BaseModel):
    version: str = "0.1.0"
    generated_at: datetime = Field(default_factory=utc_now)
    status: Literal["complete", "partial", "failed"] = "partial"
    errors: list[str] = Field(default_factory=list)
    pr: PullRequestInfo
    changed_files: list[ChangedFile] = Field(default_factory=list)
    changed_functions: list[ChangedFunction] = Field(default_factory=list)
    behavioral_contract: BehavioralContract = Field(default_factory=BehavioralContract)
    contract_extraction: ToolRun | None = None
    generated_tests: list[GeneratedTest] = Field(default_factory=list)
    generated_test_metadata: list[GeneratedTestMetadata] = Field(default_factory=list)
    failure_mappings: list[FailureMapping] = Field(default_factory=list)
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
    risk_breakdown: RiskBreakdown | None = None
    risk_reasons: list[RiskReason] = Field(default_factory=list)
    policy_decision: PolicyDecision = Field(default_factory=PolicyDecision)
    ai_review: EvidenceBasedReview | None = None
    ai_review_run: ToolRun | None = None
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
    behavioral_contract: BehavioralContract = Field(default_factory=BehavioralContract)
    contract_extraction: ToolRun | None = None
    generated_tests: list[GeneratedTest] = Field(default_factory=list)
    generated_test_metadata: list[GeneratedTestMetadata] = Field(default_factory=list)
    failure_mappings: list[FailureMapping] = Field(default_factory=list)
    test_generation: ToolRun | None = None
    sandbox_results: list[ToolRun] = Field(default_factory=list)
    existing_test_results: list[ToolRun] = Field(default_factory=list)
    generated_test_results: list[ToolRun] = Field(default_factory=list)
    static_analysis_results: list[ToolRun] = Field(default_factory=list)
    static_findings: list[StaticFinding] = Field(default_factory=list)
    security_findings: list[SecurityFinding] = Field(default_factory=list)
    risk_score: int = 0
    risk_level: RiskLevel = RiskLevel.LOW
    risk_breakdown: RiskBreakdown | None = None
    risk_reasons: list[RiskReason] = Field(default_factory=list)
    policy_decision: PolicyDecision = Field(default_factory=PolicyDecision)
    ai_review: EvidenceBasedReview | None = None
    ai_review_run: ToolRun | None = None
    merge_decision: MergeDecision = MergeDecision.MANUAL_REVIEW
    recommendation: MergeRecommendation = MergeRecommendation.HUMAN_REVIEW
    report_path: str | None = None
