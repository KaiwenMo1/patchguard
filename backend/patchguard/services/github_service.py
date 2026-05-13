"""GitHub API access for public pull requests."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import requests

from patchguard.models import ChangedFile, PRMetadata, PullRequestInfo

PR_URL_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)"
    r"(?:[/?#].*)?$"
)


@dataclass(frozen=True)
class ParsedPullRequestURL:
    owner: str
    repo: str
    number: int


@dataclass(frozen=True)
class PullRequestData:
    metadata: PRMetadata
    changed_files: list[ChangedFile]


class GitHubServiceError(RuntimeError):
    """User-facing GitHub API error."""


class GitHubUnauthorizedError(GitHubServiceError):
    """Raised when GitHub denies repository or pull request access."""


class GitHubRateLimitError(GitHubServiceError):
    """Raised when GitHub API rate limits prevent the request."""


class GitHubNotFoundError(GitHubServiceError):
    """Raised when a repository or pull request cannot be found."""


class GitHubService:
    """Small wrapper around the GitHub REST API."""

    def __init__(self, token: str | None = None, timeout_seconds: int = 20) -> None:
        self.token = token or os.getenv("GITHUB_TOKEN")
        self.timeout_seconds = timeout_seconds

    def parse_pr_url(self, pr_url: str) -> ParsedPullRequestURL:
        match = PR_URL_RE.match(pr_url.strip())
        if not match:
            raise ValueError(
                "Expected a GitHub pull request URL like "
                "https://github.com/owner/repo/pull/123"
            )
        return ParsedPullRequestURL(
            owner=match.group("owner"),
            repo=match.group("repo"),
            number=int(match.group("number")),
        )

    def fetch_pull_request(self, pr_url: str) -> PullRequestData:
        parsed = self.parse_pr_url(pr_url)
        metadata = self.fetch_pr_info(parsed.owner, parsed.repo, parsed.number)
        files = self.fetch_changed_files(parsed.owner, parsed.repo, parsed.number)
        return PullRequestData(metadata=metadata, changed_files=files)

    def fetch_pr_info(self, owner: str, repo: str, pr_number: int) -> PRMetadata:
        pull = self._get_json(f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}")
        return self._metadata_from_api(owner=owner, repo=repo, pr_number=pr_number, pull=pull)

    def fetch_changed_files(self, owner: str, repo: str, pr_number: int) -> list[ChangedFile]:
        files: list[ChangedFile] = []
        page = 1
        while True:
            payload = self._get_json(
                f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/files",
                params={"per_page": 100, "page": page},
            )
            if not payload:
                break
            files.extend(self._changed_file_from_api(item) for item in payload)
            if len(payload) < 100:
                break
            page += 1
        return files

    def fetch_report_inputs(self, pr_url: str) -> tuple[PullRequestInfo, list[ChangedFile]]:
        parsed = self.parse_pr_url(pr_url)
        metadata = self.fetch_pr_info(parsed.owner, parsed.repo, parsed.number)
        changed_files = self.fetch_changed_files(parsed.owner, parsed.repo, parsed.number)
        return self.pull_request_info_from_metadata(metadata), changed_files

    def _get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        try:
            response = requests.get(
                url,
                headers=self._headers(),
                params=params,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise GitHubServiceError(f"GitHub request failed: {exc}") from exc
        self._raise_for_status(response)
        return response.json()

    def _raise_for_status(self, response: requests.Response) -> None:
        if response.status_code < 400:
            return
        message = self._response_message(response)
        rate_remaining = response.headers.get("X-RateLimit-Remaining")
        if response.status_code in {403, 429} and rate_remaining == "0":
            reset = self._format_rate_limit_reset(response.headers.get("X-RateLimit-Reset"))
            suffix = f" Resets at {reset}." if reset else ""
            raise GitHubRateLimitError(
                "GitHub API rate limit exceeded. Set GITHUB_TOKEN for a higher limit." + suffix
            )
        if response.status_code in {401, 403}:
            raise GitHubUnauthorizedError(
                f"GitHub denied access to this repository or pull request: {message}"
            )
        if response.status_code == 404:
            raise GitHubNotFoundError(
                "GitHub repository or pull request was not found. "
                "Check the URL and whether the repo is public."
            )
        raise GitHubServiceError(f"GitHub API returned HTTP {response.status_code}: {message}")

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "PatchGuard-MVP",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    @staticmethod
    def _metadata_from_api(
        *,
        owner: str,
        repo: str,
        pr_number: int,
        pull: dict[str, Any],
    ) -> PRMetadata:
        return PRMetadata(
            owner=owner,
            repo=repo,
            number=pr_number,
            title=pull.get("title") or "",
            author=(pull.get("user") or {}).get("login") or "",
            state=pull.get("state") or "",
            is_draft=bool(pull.get("draft", False)),
            html_url=pull.get("html_url") or f"https://github.com/{owner}/{repo}/pull/{pr_number}",
            base_ref=(pull.get("base") or {}).get("ref") or "",
            base_sha=(pull.get("base") or {}).get("sha") or "",
            base_repo_full_name=((pull.get("base") or {}).get("repo") or {}).get("full_name")
            or f"{owner}/{repo}",
            base_clone_url=((pull.get("base") or {}).get("repo") or {}).get("clone_url") or "",
            head_ref=(pull.get("head") or {}).get("ref") or "",
            head_sha=(pull.get("head") or {}).get("sha") or "",
            head_repo_full_name=((pull.get("head") or {}).get("repo") or {}).get("full_name")
            or "",
            head_clone_url=((pull.get("head") or {}).get("repo") or {}).get("clone_url") or "",
            changed_files_count=int(pull.get("changed_files") or 0),
            additions=int(pull.get("additions") or 0),
            deletions=int(pull.get("deletions") or 0),
        )

    @staticmethod
    def pull_request_info_from_metadata(metadata: PRMetadata) -> PullRequestInfo:
        return PullRequestInfo(
            owner=metadata.owner,
            repo=metadata.repo,
            number=metadata.number,
            url=metadata.html_url,
            title=metadata.title,
            author=metadata.author,
            state=metadata.state,
            is_draft=metadata.is_draft,
            base_ref=metadata.base_ref,
            base_sha=metadata.base_sha,
            base_repo_full_name=metadata.base_repo_full_name,
            head_ref=metadata.head_ref,
            head_sha=metadata.head_sha,
            head_repo_full_name=metadata.head_repo_full_name,
            additions=metadata.additions,
            deletions=metadata.deletions,
            changed_files_count=metadata.changed_files_count,
        )

    @staticmethod
    def _changed_file_from_api(item: dict[str, Any]) -> ChangedFile:
        return ChangedFile(
            filename=item.get("filename") or "",
            status=item.get("status") or "",
            additions=int(item.get("additions") or 0),
            deletions=int(item.get("deletions") or 0),
            changes=int(item.get("changes") or 0),
            patch=item.get("patch"),
            previous_filename=item.get("previous_filename"),
            raw_url=item.get("raw_url"),
            blob_url=item.get("blob_url"),
        )

    @staticmethod
    def _response_message(response: requests.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return response.text.strip() or response.reason
        if isinstance(payload, dict):
            return str(payload.get("message") or response.reason)
        return response.reason

    @staticmethod
    def _format_rate_limit_reset(reset_value: str | None) -> str | None:
        if not reset_value:
            return None
        try:
            timestamp = int(reset_value)
        except ValueError:
            try:
                return parsedate_to_datetime(reset_value).isoformat()
            except (TypeError, ValueError):
                return None
        return datetime.fromtimestamp(timestamp, UTC).isoformat()
