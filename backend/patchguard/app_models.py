"""Models for the PatchGuard GitHub App storage and job queue."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from patchguard.models import MergeDecision, PolicyGateDecision, RiskLevel


class GitHubAppJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"


class GitHubAppInstallation(BaseModel):
    id: int | None = None
    github_installation_id: int
    account_login: str
    account_type: str
    active: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None


class GitHubAppRepository(BaseModel):
    id: int | None = None
    installation_id: int
    github_repo_id: int
    full_name: str
    private: bool = False
    default_branch: str = "main"
    selected: bool = True
    active: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None


class GitHubWebhookDelivery(BaseModel):
    id: int | None = None
    delivery_id: str
    event_name: str
    action: str | None = None
    github_installation_id: int | None = None
    repository_full_name: str | None = None
    payload_sha256: str | None = None
    received_at: datetime | None = None


class WebhookDeliveryResult(BaseModel):
    delivery: GitHubWebhookDelivery
    created: bool


class GitHubAppAnalysisJob(BaseModel):
    id: int | None = None
    installation_id: int
    repository_id: int
    repository_full_name: str
    event_type: str
    status: GitHubAppJobStatus = GitHubAppJobStatus.QUEUED
    pr_number: int | None = None
    pr_url: str | None = None
    head_sha: str | None = None
    base_sha: str | None = None
    check_run_id: int | None = None
    check_run_url: str | None = None
    report_path: str | None = None
    error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class GitHubAppAnalysisReport(BaseModel):
    id: int | None = None
    job_id: int
    risk_score: int
    risk_level: RiskLevel
    merge_decision: MergeDecision
    policy_decision: PolicyGateDecision
    report_json_path: str
    created_at: datetime | None = None


class GitHubInstallationToken(BaseModel):
    token: str
    expires_at: datetime
    permissions: dict[str, str] = Field(default_factory=dict)
    repository_selection: str | None = None
