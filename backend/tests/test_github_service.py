from __future__ import annotations

import pytest
from patchguard.models import ChangedFile, CommandResult
from patchguard.services.github_service import (
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubService,
    GitHubUnauthorizedError,
    PullRequestData,
)
from patchguard.services.report_service import SkeletonReportService


def test_parse_github_pull_request_url() -> None:
    parsed = GitHubService().parse_pr_url("https://github.com/owner/repo/pull/123")

    assert parsed.owner == "owner"
    assert parsed.repo == "repo"
    assert parsed.number == 123


def test_parse_github_pull_request_url_with_trailing_segments() -> None:
    parsed = GitHubService().parse_pr_url("https://github.com/owner/repo/pull/123/files")

    assert parsed.owner == "owner"
    assert parsed.repo == "repo"
    assert parsed.number == 123


def test_rejects_non_pull_request_url() -> None:
    with pytest.raises(ValueError):
        GitHubService().parse_pr_url("https://github.com/owner/repo/issues/123")


def test_fetch_pr_info_and_changed_files_from_mocked_github(monkeypatch) -> None:
    calls: list[tuple[str, dict | None]] = []

    def fake_get(url, headers, params, timeout):  # noqa: ANN001
        calls.append((url, params))
        if url.endswith("/pulls/123"):
            return FakeResponse(
                {
                    "title": "Improve parser",
                    "user": {"login": "octo-dev"},
                    "state": "open",
                    "draft": False,
                    "html_url": "https://github.com/owner/repo/pull/123",
                    "base": {
                        "ref": "main",
                        "sha": "base-sha",
                        "repo": {
                            "full_name": "owner/repo",
                            "clone_url": "https://github.com/owner/repo.git",
                        },
                    },
                    "head": {
                        "ref": "feature",
                        "sha": "head-sha",
                        "repo": {
                            "full_name": "contributor/repo",
                            "clone_url": "https://github.com/contributor/repo.git",
                        },
                    },
                    "changed_files": 1,
                    "additions": 10,
                    "deletions": 2,
                }
            )
        if url.endswith("/pulls/123/files"):
            return FakeResponse(
                [
                    {
                        "filename": "src/parser.py",
                        "status": "modified",
                        "additions": 10,
                        "deletions": 2,
                        "changes": 12,
                        "patch": "@@ -1 +1 @@",
                    }
                ]
            )
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr("patchguard.services.github_service.requests.get", fake_get)

    service = GitHubService(token="test-token")
    pr_info = service.fetch_pr_info("owner", "repo", 123)
    changed_files = service.fetch_changed_files("owner", "repo", 123)

    assert pr_info.title == "Improve parser"
    assert pr_info.author == "octo-dev"
    assert pr_info.base_ref == "main"
    assert pr_info.head_ref == "feature"
    assert pr_info.additions == 10
    assert changed_files == [
        ChangedFile(
            filename="src/parser.py",
            status="modified",
            additions=10,
            deletions=2,
            changes=12,
            patch="@@ -1 +1 @@",
        )
    ]
    assert calls[-1][1] == {"per_page": 100, "page": 1}


def test_fetch_pr_info_maps_404_to_clean_error(monkeypatch) -> None:
    def fake_get(url, headers, params, timeout):  # noqa: ANN001, ARG001
        return FakeResponse({"message": "Not Found"}, status_code=404)

    monkeypatch.setattr("patchguard.services.github_service.requests.get", fake_get)

    with pytest.raises(GitHubNotFoundError):
        GitHubService().fetch_pr_info("owner", "repo", 404)


def test_fetch_pr_info_maps_unauthorized_to_clean_error(monkeypatch) -> None:
    def fake_get(url, headers, params, timeout):  # noqa: ANN001, ARG001
        return FakeResponse({"message": "Bad credentials"}, status_code=401)

    monkeypatch.setattr("patchguard.services.github_service.requests.get", fake_get)

    with pytest.raises(GitHubUnauthorizedError):
        GitHubService().fetch_pr_info("owner", "repo", 123)


def test_fetch_pr_info_maps_rate_limit_to_clean_error(monkeypatch) -> None:
    def fake_get(url, headers, params, timeout):  # noqa: ANN001, ARG001
        return FakeResponse(
            {"message": "API rate limit exceeded"},
            status_code=403,
            headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1770000000"},
        )

    monkeypatch.setattr("patchguard.services.github_service.requests.get", fake_get)

    with pytest.raises(GitHubRateLimitError):
        GitHubService().fetch_pr_info("owner", "repo", 123)


def test_skeleton_report_service_writes_json_report(tmp_path) -> None:
    output_path = tmp_path / "report.json"

    report = SkeletonReportService(
        github_service=FakeGitHubService(),
        command_runner=FakeCommandRunner(),
    ).analyze(
        "https://github.com/owner/repo/pull/123",
        output_path,
        workspaces_dir=tmp_path / "workspaces",
    )

    assert output_path.exists()
    assert report.status == "complete"
    assert report.pr.owner == "owner"
    assert report.workspace_path
    assert len(report.clone_results) == 4
    assert report.dependency_install is not None
    assert report.existing_tests is not None
    assert report.existing_tests.status.value == "passed"
    assert [run.name for run in report.static_analysis_results] == [
        "ruff check",
        "bandit security scan",
    ]
    assert '"title": "Improve parser"' in output_path.read_text(encoding="utf-8")


def test_skeleton_report_is_partial_when_clone_fails(tmp_path) -> None:
    output_path = tmp_path / "report.json"

    report = SkeletonReportService(
        github_service=FakeGitHubService(),
        command_runner=FakeCommandRunner(fail_first=True),
    ).analyze(
        "https://github.com/owner/repo/pull/123",
        output_path,
        workspaces_dir=tmp_path / "workspaces",
    )

    assert output_path.exists()
    assert report.status == "partial"
    assert report.workspace_path is None
    assert report.errors == ["Repository clone or PR checkout failed"]
    assert report.clone_results[0].status.value == "failed"


def test_skeleton_report_records_existing_test_failure(tmp_path) -> None:
    output_path = tmp_path / "report.json"

    report = SkeletonReportService(
        github_service=FakeGitHubService(),
        command_runner=FakeCommandRunner(pytest_exit_code=1),
    ).analyze(
        "https://github.com/owner/repo/pull/123",
        output_path,
        workspaces_dir=tmp_path / "workspaces",
    )

    assert output_path.exists()
    assert report.status == "complete"
    assert report.existing_tests is not None
    assert report.existing_tests.status.value == "failed"
    assert any(reason.category == "existing_tests" for reason in report.risk_reasons)


class FakeResponse:
    def __init__(
        self,
        payload,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        reason: str = "OK",
    ) -> None:
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self.reason = reason
        self.text = str(payload)

    def json(self):
        return self._payload


class FakeGitHubService:
    def fetch_pull_request(self, pr_url: str):
        metadata = GitHubService._metadata_from_api(
            owner="owner",
            repo="repo",
            pr_number=123,
            pull={
                "title": "Improve parser",
                "user": {"login": "octo-dev"},
                "state": "open",
                "draft": False,
                "html_url": pr_url,
                "base": {
                    "ref": "main",
                    "sha": "base-sha",
                    "repo": {
                        "full_name": "owner/repo",
                        "clone_url": "https://github.com/owner/repo.git",
                    },
                },
                "head": {
                    "ref": "feature",
                    "sha": "head-sha",
                    "repo": {
                        "full_name": "contributor/repo",
                        "clone_url": "https://github.com/contributor/repo.git",
                    },
                },
                "changed_files": 1,
                "additions": 10,
                "deletions": 2,
            },
        )
        return PullRequestData(
            metadata=metadata,
            changed_files=[
                ChangedFile(
                    filename="src/parser.py",
                    status="modified",
                    additions=10,
                    deletions=2,
                    changes=12,
                    patch="@@ -1 +1 @@",
                )
            ],
        )

    def pull_request_info_from_metadata(self, metadata):
        return GitHubService.pull_request_info_from_metadata(metadata)


def test_skeleton_report_skips_tests_when_dependency_install_fails(tmp_path) -> None:
    output_path = tmp_path / "report.json"

    report = SkeletonReportService(
        github_service=FakeGitHubService(),
        command_runner=FakeCommandRunner(dependency_exit_code=1),
    ).analyze(
        "https://github.com/owner/repo/pull/123",
        output_path,
        workspaces_dir=tmp_path / "workspaces",
    )

    assert output_path.exists()
    assert report.dependency_install is not None
    assert report.dependency_install.status == "failed"
    assert report.existing_tests is not None
    assert report.existing_tests.status == "skipped"
    assert "Dependency installation failed" in report.existing_tests.summary
    assert any(reason.category == "dependencies" for reason in report.risk_reasons)
    assert any(reason.category == "existing_tests" for reason in report.risk_reasons)


class FakeCommandRunner:
    def __init__(
        self,
        *,
        fail_first: bool = False,
        pytest_exit_code: int = 0,
        dependency_exit_code: int = 0,
    ) -> None:
        self.fail_first = fail_first
        self.pytest_exit_code = pytest_exit_code
        self.dependency_exit_code = dependency_exit_code
        self.calls = 0

    def run(self, command, *, cwd=None, timeout_seconds=300, env=None):  # noqa: ANN001, ARG002
        self.calls += 1
        if self.fail_first and self.calls == 1:
            return CommandResult(
                command=[str(part) for part in command],
                exit_code=128,
                stderr_tail="fatal: repository not found",
            )
        command_text = " ".join(str(part) for part in command)
        if "pip install" in command_text:
            return CommandResult(
                command=[str(part) for part in command],
                exit_code=self.dependency_exit_code,
                stdout_tail="installed" if self.dependency_exit_code == 0 else "",
                stderr_tail="dependency install failed" if self.dependency_exit_code else "",
            )
        if "pytest -q" in command_text:
            return CommandResult(
                command=[str(part) for part in command],
                exit_code=self.pytest_exit_code,
                stdout_tail="1 failed" if self.pytest_exit_code else "1 passed",
            )
        return CommandResult(
            command=[str(part) for part in command],
            exit_code=0,
            stdout_tail="ok",
        )

    def skipped(self, command, reason: str):  # noqa: ANN001
        return CommandResult(
            command=[str(part) for part in command],
            skipped=True,
            skip_reason=reason,
        )
