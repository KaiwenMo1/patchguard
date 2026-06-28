"""Backfill recent pull requests for installed GitHub App repositories."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import requests

from patchguard.app_models import GitHubAppAnalysisJob, GitHubAppRepository
from patchguard.services.github_app_auth_service import GitHubAppAuthService
from patchguard.storage.sqlite_store import GitHubAppSQLiteStore

GITHUB_API_BASE_URL = "https://api.github.com"
DEFAULT_BACKFILL_LIMIT = 10
MAX_PER_PAGE = 100


class GitHubAppBackfillError(RuntimeError):
    """User-facing backfill error."""


@dataclass(frozen=True)
class BackfillPullRequest:
    number: int
    html_url: str
    head_sha: str
    base_sha: str | None = None
    title: str | None = None
    draft: bool = False


@dataclass(frozen=True)
class BackfilledJob:
    job_id: int
    repository_full_name: str
    pr_number: int
    head_sha: str
    created: bool


@dataclass(frozen=True)
class BackfillResult:
    github_installation_id: int
    repositories_scanned: int = 0
    pull_requests_seen: int = 0
    jobs_created: int = 0
    duplicates_skipped: int = 0
    draft_prs_skipped: int = 0
    jobs: list[BackfilledJob] = field(default_factory=list)


class GitHubAppBackfillService:
    """Create queued analysis jobs for recent PRs in selected installed repos."""

    def __init__(
        self,
        *,
        store: GitHubAppSQLiteStore,
        token: str | None = None,
        auth_service: GitHubAppAuthService | None = None,
        api_base_url: str = GITHUB_API_BASE_URL,
        timeout_seconds: int = 20,
        include_drafts: bool = False,
    ) -> None:
        self.store = store
        self.token = token
        self.auth_service = auth_service
        self.api_base_url = api_base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.include_drafts = include_drafts

    def backfill_installation(
        self,
        github_installation_id: int,
        *,
        limit: int = DEFAULT_BACKFILL_LIMIT,
    ) -> BackfillResult:
        validate_limit(limit)
        installation = self.store.get_installation_by_github_id(github_installation_id)
        if installation.id is None:
            raise GitHubAppBackfillError(
                f"Installation {github_installation_id} does not have a local id."
            )
        repositories = self.store.list_active_selected_repositories_for_installation(
            installation.id
        )
        token = self._token_for_installation(github_installation_id)
        jobs: list[BackfilledJob] = []
        pull_requests_seen = 0
        jobs_created = 0
        duplicates_skipped = 0
        draft_prs_skipped = 0
        for repository in repositories:
            pull_requests = self.list_recent_pull_requests(
                repository.full_name,
                token=token,
                limit=limit,
            )
            pull_requests_seen += len(pull_requests)
            for pull_request in pull_requests:
                if pull_request.draft and not self.include_drafts:
                    draft_prs_skipped += 1
                    continue
                job, created = self._create_job_for_pull_request(
                    installation_id=installation.id,
                    repository=repository,
                    pull_request=pull_request,
                )
                if created:
                    jobs_created += 1
                else:
                    duplicates_skipped += 1
                jobs.append(
                    BackfilledJob(
                        job_id=required_job_id(job.id),
                        repository_full_name=repository.full_name,
                        pr_number=pull_request.number,
                        head_sha=pull_request.head_sha,
                        created=created,
                    )
                )
        return BackfillResult(
            github_installation_id=github_installation_id,
            repositories_scanned=len(repositories),
            pull_requests_seen=pull_requests_seen,
            jobs_created=jobs_created,
            duplicates_skipped=duplicates_skipped,
            draft_prs_skipped=draft_prs_skipped,
            jobs=jobs,
        )

    def list_recent_pull_requests(
        self,
        repository_full_name: str,
        *,
        token: str,
        limit: int = DEFAULT_BACKFILL_LIMIT,
    ) -> list[BackfillPullRequest]:
        validate_limit(limit)
        owner, repo = split_repo_full_name(repository_full_name)
        collected: list[BackfillPullRequest] = []
        page = 1
        while len(collected) < limit:
            per_page = min(MAX_PER_PAGE, limit - len(collected))
            payload = self._get_json(
                f"{self.api_base_url}/repos/{owner}/{repo}/pulls",
                token=token,
                params={
                    "state": "all",
                    "sort": "updated",
                    "direction": "desc",
                    "per_page": per_page,
                    "page": page,
                },
            )
            if not isinstance(payload, list):
                raise GitHubAppBackfillError(
                    f"GitHub returned an unexpected PR list for {repository_full_name}."
                )
            if not payload:
                break
            collected.extend(parse_pull_request(item) for item in payload)
            if len(payload) < per_page:
                break
            page += 1
        return collected[:limit]

    def _create_job_for_pull_request(
        self,
        *,
        installation_id: int,
        repository: GitHubAppRepository,
        pull_request: BackfillPullRequest,
    ) -> tuple[GitHubAppAnalysisJob, bool]:
        if repository.id is None:
            raise GitHubAppBackfillError(
                f"Repository {repository.full_name} does not have a local id."
            )
        return self.store.create_analysis_job_if_absent(
            GitHubAppAnalysisJob(
                installation_id=installation_id,
                repository_id=repository.id,
                repository_full_name=repository.full_name,
                event_type="backfill.pull_request",
                pr_number=pull_request.number,
                pr_url=pull_request.html_url,
                head_sha=pull_request.head_sha,
                base_sha=pull_request.base_sha,
            )
        )

    def _get_json(
        self,
        url: str,
        *,
        token: str,
        params: dict[str, int | str],
    ) -> Any:
        try:
            response = requests.get(
                url,
                headers=headers_for_token(token),
                params=params,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise GitHubAppBackfillError(f"GitHub backfill request failed: {exc}") from exc
        if response.status_code >= 400:
            raise GitHubAppBackfillError(
                "GitHub backfill request failed with HTTP "
                f"{response.status_code}: {response_message(response)}"
            )
        return response.json()

    def _token_for_installation(self, github_installation_id: int) -> str:
        if self.token:
            return self.token
        auth_service = self.auth_service or GitHubAppAuthService()
        return auth_service.fetch_installation_token(github_installation_id).token


def validate_limit(limit: int) -> None:
    if limit <= 0:
        raise GitHubAppBackfillError("--limit must be greater than 0.")


def parse_pull_request(payload: dict[str, Any]) -> BackfillPullRequest:
    head = payload.get("head") if isinstance(payload.get("head"), dict) else {}
    base = payload.get("base") if isinstance(payload.get("base"), dict) else {}
    return BackfillPullRequest(
        number=int(required_value(payload, "number")),
        html_url=str(required_value(payload, "html_url")),
        head_sha=str(required_value(head, "sha")),
        base_sha=str(base.get("sha")) if base.get("sha") else None,
        title=str(payload.get("title")) if payload.get("title") else None,
        draft=bool(payload.get("draft", False)),
    )


def headers_for_token(token: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "PatchGuard-GitHub-App",
    }


def split_repo_full_name(full_name: str) -> tuple[str, str]:
    parts = full_name.split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise GitHubAppBackfillError(f"Invalid repository full name: {full_name}")
    return parts[0], parts[1]


def required_value(payload: dict[str, Any], key: str) -> Any:
    value = payload.get(key)
    if value is None:
        raise GitHubAppBackfillError(f"GitHub PR payload is missing field: {key}.")
    return value


def required_job_id(job_id: int | None) -> int:
    if job_id is None:
        raise GitHubAppBackfillError("Created analysis job did not include an id.")
    return job_id


def response_message(response: requests.Response) -> str:
    try:
        payload: Any = response.json()
    except ValueError:
        return response.text.strip() or response.reason
    if isinstance(payload, dict):
        return str(payload.get("message") or response.reason)
    return response.reason
