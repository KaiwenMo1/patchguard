"""Local reproducible demo report pipeline."""

from __future__ import annotations

import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from patchguard.config import PatchGuardSettings
from patchguard.models import (
    ChangedFile,
    PullRequestInfo,
    RiskReport,
    RunStatus,
    TestResult,
    ToolRun,
)
from patchguard.services.function_extractor import FunctionExtractor
from patchguard.services.risk_score_service import RiskScoreService
from patchguard.services.sandbox_service import SandboxService
from patchguard.services.security_scan_service import SecurityScanService
from patchguard.services.test_generation_service import TestGenerationService
from patchguard.utils.command_runner import CommandRunner
from patchguard.utils.file_utils import ensure_dir

GENERATED_TEST_SCRIPT = "python -m pytest -q .patchguard/generated_tests"


class DemoReportService:
    """Run PatchGuard against local example PR fixtures."""

    def __init__(
        self,
        *,
        settings: PatchGuardSettings | None = None,
        command_runner: CommandRunner | None = None,
    ) -> None:
        self.settings = settings or PatchGuardSettings()
        self.command_runner = command_runner or CommandRunner()
        self.function_extractor = FunctionExtractor()
        self.test_generation_service = TestGenerationService()
        self.risk_score_service = RiskScoreService()

    def analyze(
        self,
        demo_dir: str | Path,
        output_path: str | Path,
        *,
        workspaces_dir: str | Path | None = None,
        skip_docker: bool = False,
        cleanup_workspace: bool = False,
    ) -> RiskReport:
        demo_path = Path(demo_dir)
        metadata = self._read_metadata(demo_path)
        repo_source = demo_path / "repo"
        patch_path = demo_path / "patch.diff"
        if not repo_source.is_dir():
            raise ValueError(f"Demo repo directory not found: {repo_source}")
        if not patch_path.exists():
            raise ValueError(f"Demo patch not found: {patch_path}")

        workspace = self._create_workspace(workspaces_dir or self.settings.workspaces_dir, demo_path)
        repo_dir = workspace / "repo"
        shutil.copytree(repo_source, repo_dir, ignore=shutil.ignore_patterns(".patchguard", "__pycache__"))

        changed_files = self._changed_files_from_patch(patch_path.read_text(encoding="utf-8"))
        pr_info = self._pull_request_info(metadata, demo_path)
        pr_info.additions = sum(file.additions for file in changed_files)
        pr_info.deletions = sum(file.deletions for file in changed_files)
        pr_info.changed_files_count = len(changed_files)
        report = RiskReport(
            pr=pr_info,
            changed_files=changed_files,
            workspace_path=str(repo_dir),
        )
        report.changed_functions = self.function_extractor.extract_changed_functions(
            repo_dir,
            report.changed_files,
        )
        generation = self.test_generation_service.generate(
            repo_dir,
            report.changed_files,
            report.changed_functions,
        )
        report.generated_tests = generation.generated_tests
        report.test_generation = generation.tool_run

        if skip_docker:
            report.status = "partial"
            report.errors.append("Docker execution skipped for local demo")
            self._mark_generated_tests_skipped(report, "Docker execution skipped for local demo")
        else:
            self._run_docker_evidence(repo_dir, report)

        if cleanup_workspace:
            shutil.rmtree(workspace, ignore_errors=True)
            report.workspace_path = None

        self.risk_score_service.score_risk_report(report)
        path = Path(output_path)
        ensure_dir(path.parent)
        report.report_path = str(path)
        path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return report

    def _run_docker_evidence(self, repo_dir: Path, report: RiskReport) -> None:
        report.status = "complete"
        sandbox = SandboxService(
            command_runner=self.command_runner,
            docker_image=self.settings.docker_image,
            limits=self.settings.sandbox_limits,
        )
        image_run = sandbox.ensure_image(self.settings.docker_build_timeout_seconds)
        report.sandbox_results.append(image_run)
        if image_run.status != RunStatus.PASSED:
            report.status = "partial"
            report.errors.append("Docker sandbox image is unavailable; demo evidence was partial")
            self._mark_generated_tests_skipped(report, "Docker sandbox image is unavailable")
            return

        dependency_run = sandbox.run_dependency_install(
            repo_dir=repo_dir,
            timeout_seconds=self.settings.command_timeout_seconds,
        )
        report.dependency_install = dependency_run
        report.sandbox_results.append(dependency_run)

        existing_tests = sandbox.run_existing_tests(repo_dir=repo_dir, timeout_seconds=180)
        report.existing_tests = existing_tests
        report.test_results.append(self._test_result_from_tool_run(existing_tests))

        self._run_generated_tests(sandbox, repo_dir, report)

        security_scanner = SecurityScanService(sandbox)
        ruff_run, static_findings = security_scanner.run_ruff(repo_dir)
        report.static_analysis_results.append(ruff_run)
        report.static_findings.extend(static_findings)

        bandit_run, security_findings = security_scanner.run_bandit(repo_dir)
        report.static_analysis_results.append(bandit_run)
        report.security_findings.extend(security_findings)

    def _run_generated_tests(
        self,
        sandbox: SandboxService,
        repo_dir: Path,
        report: RiskReport,
    ) -> None:
        if not report.generated_tests:
            reason = (
                report.test_generation.summary
                if report.test_generation
                else "No generated tests available"
            )
            self._mark_generated_tests_skipped(report, reason)
            return

        compile_run = sandbox.run_generated_test_compile(
            repo_dir=repo_dir,
            timeout_seconds=self.settings.command_timeout_seconds,
        )
        report.generated_test_results.append(compile_run)
        if compile_run.status != RunStatus.PASSED:
            self._mark_generated_tests_skipped(
                report,
                "Generated test compilation failed; pytest was not run",
            )
            return

        generated_tests = sandbox.run_generated_tests(
            repo_dir=repo_dir,
            timeout_seconds=self.settings.command_timeout_seconds,
        )
        report.generated_test_results.append(generated_tests)
        report.test_results.append(self._test_result_from_tool_run(generated_tests))

    def _mark_generated_tests_skipped(self, report: RiskReport, reason: str) -> None:
        report.generated_test_results.append(
            ToolRun(
                name="run generated PatchGuard tests",
                kind="generated_tests",
                status=RunStatus.SKIPPED,
                summary=reason,
                command=self.command_runner.skipped(["docker", "run", "...", GENERATED_TEST_SCRIPT], reason),
            )
        )

    @staticmethod
    def _read_metadata(demo_path: Path) -> dict[str, Any]:
        metadata_path = demo_path / "demo.json"
        if not metadata_path.exists():
            raise ValueError(f"Demo metadata not found: {metadata_path}")
        return json.loads(metadata_path.read_text(encoding="utf-8"))

    @staticmethod
    def _pull_request_info(metadata: dict[str, Any], demo_path: Path) -> PullRequestInfo:
        return PullRequestInfo(
            owner=metadata.get("owner", "patchguard"),
            repo=metadata.get("repo", demo_path.name),
            number=int(metadata.get("number", 1)),
            url=metadata.get("url", f"local://{demo_path.name}"),
            title=metadata.get("title"),
            author=metadata.get("author", "patchguard-demo"),
            state=metadata.get("state", "open"),
            is_draft=bool(metadata.get("is_draft", False)),
            base_ref=metadata.get("base_ref", "main"),
            base_sha=metadata.get("base_sha", "demo-base"),
            base_repo_full_name=metadata.get("base_repo_full_name", f"patchguard/{demo_path.name}"),
            head_ref=metadata.get("head_ref", "demo-pr"),
            head_sha=metadata.get("head_sha", "demo-head"),
            head_repo_full_name=metadata.get("head_repo_full_name", f"patchguard/{demo_path.name}"),
        )

    @staticmethod
    def _create_workspace(workspaces_dir: str | Path, demo_path: Path) -> Path:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "-", demo_path.name)
        return ensure_dir(Path(workspaces_dir) / f"{safe_name}-{stamp}")

    @staticmethod
    def _changed_files_from_patch(patch_text: str) -> list[ChangedFile]:
        files: list[ChangedFile] = []
        current_filename: str | None = None
        current_hunks: list[str] = []
        additions = 0
        deletions = 0
        status = "modified"

        def flush() -> None:
            nonlocal additions, deletions, current_filename, current_hunks, status
            if current_filename:
                files.append(
                    ChangedFile(
                        filename=current_filename,
                        status=status,
                        additions=additions,
                        deletions=deletions,
                        changes=additions + deletions,
                        patch="\n".join(current_hunks).rstrip() or None,
                    )
                )
            current_filename = None
            current_hunks = []
            additions = 0
            deletions = 0
            status = "modified"

        for line in patch_text.splitlines():
            if line.startswith("diff --git "):
                flush()
                parts = line.split()
                if len(parts) >= 4:
                    current_filename = parts[3].removeprefix("b/")
                continue
            if line.startswith("new file mode"):
                status = "added"
                continue
            if line.startswith("deleted file mode"):
                status = "removed"
                continue
            if line.startswith("+++ b/"):
                current_filename = line.removeprefix("+++ b/")
                continue
            if line.startswith("@@"):
                current_hunks.append(line)
                continue
            if current_hunks:
                current_hunks.append(line)
                if line.startswith("+") and not line.startswith("+++"):
                    additions += 1
                elif line.startswith("-") and not line.startswith("---"):
                    deletions += 1
        flush()
        return files

    @staticmethod
    def _test_result_from_tool_run(run: ToolRun) -> TestResult:
        command = " ".join(run.command.command) if run.command else None
        return TestResult(
            name=run.name,
            status=run.status,
            command=command,
            stdout=run.command.stdout_tail if run.command else "",
            stderr=run.command.stderr_tail if run.command else "",
        )
