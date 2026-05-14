from __future__ import annotations

from pathlib import Path

from patchguard.models import ChangedFile, CommandResult, MergeRecommendation, RunStatus
from patchguard.services.github_service import GitHubService, PullRequestData
from patchguard.services.report_service import SkeletonReportService


def test_pipeline_runs_generated_tests_on_controlled_demo_repo(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        "patchguard.services.test_generation_service.OpenAIResponsesProvider.generate_pytest",
        lambda self, prompt: "def test_patchguard_generated_demo_regression():\n    assert False\n",
    )
    output_path = tmp_path / "report.json"

    report = SkeletonReportService(
        github_service=ControlledGitHubService(),
        command_runner=ControlledCommandRunner(generated_pytest_exit_code=1),
    ).analyze(
        "https://github.com/owner/demo/pull/7",
        output_path,
        workspaces_dir=tmp_path / "workspaces",
    )

    assert output_path.exists()
    assert report.generated_tests
    assert report.test_generation is not None
    assert report.test_generation.status == RunStatus.PASSED
    assert [
        (run.name, run.status)
        for run in report.generated_test_results
    ] == [
        ("compile generated PatchGuard tests", RunStatus.PASSED),
        ("run generated PatchGuard tests", RunStatus.FAILED),
    ]
    generated_run = report.generated_test_results[-1]
    assert generated_run.command is not None
    assert "1 failed" in generated_run.command.stdout_tail
    assert report.risk_score == 70
    assert report.risk_level.value == "high"
    assert report.risk_breakdown is not None
    assert report.risk_breakdown.test_coverage_risk == 100
    assert any(
        reason.category == "generated_tests" and reason.score_impact == 100
        for reason in report.risk_reasons
    )
    assert report.recommendation == MergeRecommendation.REVIEW_GENERATED_FAILURES


class ControlledGitHubService:
    def fetch_pull_request(self, pr_url: str):
        metadata = GitHubService._metadata_from_api(
            owner="owner",
            repo="demo",
            pr_number=7,
            pull={
                "title": "Tighten positive check",
                "user": {"login": "octo-dev"},
                "state": "open",
                "draft": False,
                "html_url": pr_url,
                "base": {
                    "ref": "main",
                    "sha": "base-sha",
                    "repo": {
                        "full_name": "owner/demo",
                        "clone_url": "https://github.com/owner/demo.git",
                    },
                },
                "head": {
                    "ref": "feature",
                    "sha": "head-sha",
                    "repo": {
                        "full_name": "contributor/demo",
                        "clone_url": "https://github.com/contributor/demo.git",
                    },
                },
                "changed_files": 1,
                "additions": 1,
                "deletions": 1,
            },
        )
        return PullRequestData(
            metadata=metadata,
            changed_files=[
                ChangedFile(
                    filename="src/demo.py",
                    status="modified",
                    additions=1,
                    deletions=1,
                    changes=2,
                    patch="@@ -2,1 +2,1 @@\n-    return value >= 0\n+    return value > 0\n",
                )
            ],
        )

    def pull_request_info_from_metadata(self, metadata):
        return GitHubService.pull_request_info_from_metadata(metadata)


class ControlledCommandRunner:
    def __init__(self, *, generated_pytest_exit_code: int) -> None:
        self.generated_pytest_exit_code = generated_pytest_exit_code

    def run(self, command, *, cwd=None, timeout_seconds=300, env=None):  # noqa: ANN001, ARG002
        command_parts = [str(part) for part in command]
        command_text = " ".join(command_parts)
        if command_parts[:2] == ["git", "clone"]:
            repo_dir = Path(command_parts[-1])
            (repo_dir / "src").mkdir(parents=True)
            (repo_dir / "src" / "__init__.py").write_text("", encoding="utf-8")
            (repo_dir / "src" / "demo.py").write_text(
                "def is_positive(value):\n    return value > 0\n",
                encoding="utf-8",
            )
            return CommandResult(command=command_parts, exit_code=0, stdout_tail="cloned")
        if "python -m py_compile .patchguard/generated_tests" in command_text:
            return CommandResult(command=command_parts, exit_code=0, stdout_tail="compiled")
        if "python -m pytest -q .patchguard/generated_tests" in command_text:
            return CommandResult(
                command=command_parts,
                exit_code=self.generated_pytest_exit_code,
                stdout_tail="1 failed" if self.generated_pytest_exit_code else "1 passed",
            )
        if "python -m pytest -q" in command_text:
            return CommandResult(command=command_parts, exit_code=0, stdout_tail="1 passed")
        if "ruff check" in command_text:
            return CommandResult(command=command_parts, exit_code=0, stdout_tail="[]")
        if "bandit -r" in command_text:
            return CommandResult(command=command_parts, exit_code=0, stdout_tail='{"results": []}')
        return CommandResult(command=command_parts, exit_code=0, stdout_tail="ok")

    def skipped(self, command, reason: str):  # noqa: ANN001
        return CommandResult(
            command=[str(part) for part in command],
            skipped=True,
            skip_reason=reason,
        )
