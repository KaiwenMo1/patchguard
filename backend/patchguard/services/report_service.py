"""End-to-end PatchGuard report pipeline."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from patchguard.config import PatchGuardSettings
from patchguard.models import PatchGuardReport, RiskReport, RunStatus, TestResult, ToolRun
from patchguard.services.clone_service import CloneService
from patchguard.services.function_extractor import FunctionExtractor
from patchguard.services.github_service import GitHubService
from patchguard.services.risk_score_service import RiskScoreService
from patchguard.services.sandbox_service import SandboxService
from patchguard.services.security_scan_service import SecurityScanService
from patchguard.services.test_generation_service import TestGenerationService
from patchguard.utils.command_runner import CommandRunner
from patchguard.utils.file_utils import ensure_dir, write_json_report

DEPENDENCY_INSTALL_SCRIPT = """
set -o pipefail
if [ -f requirements.txt ]; then
  python -m pip install -r requirements.txt
elif [ -f pyproject.toml ]; then
  python -m pip install -e .
else
  echo "No requirements.txt or pyproject.toml found; skipping dependency install"
fi
""".strip()


EXISTING_TEST_SCRIPT = """
set -o pipefail
if [ -d tests ] || find . -maxdepth 3 -name 'test_*.py' -not -path './.patchguard/*' -print -quit | grep -q .; then
  python -m pytest -q
else
  echo "No pytest tests discovered; skipping existing test run"
fi
""".strip()


GENERATED_TEST_SCRIPT = "python -m pytest -q .patchguard/generated_tests"


class SkeletonReportService:
    """Prompt 2 report writer: fetch PR metadata and changed files."""

    def __init__(
        self,
        github_service: GitHubService | None = None,
        *,
        settings: PatchGuardSettings | None = None,
        command_runner: CommandRunner | None = None,
    ) -> None:
        self.settings = settings or PatchGuardSettings()
        self.command_runner = command_runner or CommandRunner()
        self.github_service = github_service or GitHubService()
        self.clone_service = CloneService(
            command_runner=self.command_runner,
            timeout_seconds=self.settings.command_timeout_seconds,
        )
        self.function_extractor = FunctionExtractor()
        self.test_generation_service = TestGenerationService()
        self.risk_score_service = RiskScoreService()

    def analyze(
        self,
        pr_url: str,
        output_path: str | Path,
        *,
        workspaces_dir: str | Path | None = None,
        cleanup_workspace: bool = False,
        skip_llm: bool = False,
        skip_docker: bool = False,
        status_callback: Callable[[str], None] | None = None,
    ) -> RiskReport:
        self._emit(status_callback, "fetching_pr")
        pr_data = self.github_service.fetch_pull_request(pr_url)
        pr_info = self.github_service.pull_request_info_from_metadata(pr_data.metadata)
        report = RiskReport(
            pr=pr_info,
            changed_files=pr_data.changed_files,
        )

        self._emit(status_callback, "cloning")
        workspace = self.clone_service.create_workspace(
            workspaces_dir or self.settings.workspaces_dir,
            pr_data.metadata,
        )
        report.workspace_path = str(workspace / "repo")
        checkout = self.clone_service.checkout_pull_request(pr_data.metadata, workspace)
        report.clone_results = checkout.tool_runs
        if checkout.repo_dir is None:
            report.status = "partial"
            report.workspace_path = None
            report.errors.append("Repository clone or PR checkout failed")
        else:
            report.workspace_path = str(checkout.repo_dir)
            self._emit(status_callback, "analyzing_diff")
            report.changed_functions = self.function_extractor.extract_changed_functions(
                checkout.repo_dir,
                report.changed_files,
            )
            self._emit(status_callback, "generating_tests")
            generation_service = (
                TestGenerationService(enabled=False)
                if skip_llm
                else self.test_generation_service
            )
            generation = generation_service.generate(
                checkout.repo_dir,
                report.changed_files,
                report.changed_functions,
            )
            report.generated_tests = generation.generated_tests
            report.test_generation = generation.tool_run
            report.status = "complete"
            if skip_docker:
                report.status = "partial"
                self._mark_docker_skipped(
                    report,
                    reason="Docker execution disabled by --skip-docker",
                )
            else:
                sandbox = SandboxService(
                    command_runner=self.command_runner,
                    docker_image=self.settings.docker_image,
                    limits=self.settings.sandbox_limits,
                )
                image_run = sandbox.ensure_image(self.settings.docker_build_timeout_seconds)
                report.sandbox_results.append(image_run)
                if image_run.status != RunStatus.PASSED:
                    report.status = "partial"
                    report.errors.append("Docker sandbox image is unavailable; existing tests were not run")
                else:
                    dependency_run = sandbox.run_dependency_install(
                        repo_dir=checkout.repo_dir,
                        timeout_seconds=self.settings.command_timeout_seconds,
                    )
                    report.dependency_install = dependency_run
                    report.sandbox_results.append(dependency_run)

                    self._emit(status_callback, "running_existing_tests")
                    if dependency_run.status in {RunStatus.FAILED, RunStatus.ERROR}:
                        existing_tests = sandbox.skipped(
                            name="run existing pytest suite",
                            kind="existing_tests",
                            reason="Dependency installation failed; existing tests were not run",
                            command=["docker", "run", "...", "python -m pytest -q"],
                        )
                    else:
                        existing_tests = sandbox.run_existing_tests(
                            repo_dir=checkout.repo_dir,
                            timeout_seconds=self.settings.command_timeout_seconds,
                        )
                    report.existing_tests = existing_tests
                    report.test_results.append(
                        self._test_result_from_tool_run(existing_tests)
                    )

                    self._emit(status_callback, "running_generated_tests")
                    if dependency_run.status in {RunStatus.FAILED, RunStatus.ERROR}:
                        report.generated_test_results.append(
                            sandbox.skipped(
                                name="run generated PatchGuard tests",
                                kind="generated_tests",
                                reason="Dependency installation failed; generated tests were not run",
                                command=["docker", "run", "...", GENERATED_TEST_SCRIPT],
                            )
                        )
                    else:
                        self._run_generated_tests(sandbox, checkout.repo_dir, report)

                    self._emit(status_callback, "scanning_security")
                    security_scanner = SecurityScanService(sandbox)
                    ruff_run, static_findings = security_scanner.run_ruff(
                        checkout.repo_dir,
                        report.changed_files,
                    )
                    report.static_analysis_results.append(ruff_run)
                    report.static_findings.extend(static_findings)

                    bandit_run, security_findings = security_scanner.run_bandit(
                        checkout.repo_dir,
                        report.changed_files,
                    )
                    report.static_analysis_results.append(bandit_run)
                    report.security_findings.extend(security_findings)

        if cleanup_workspace:
            self.clone_service.cleanup_workspace(workspace)

        self.risk_score_service.score_risk_report(report)
        path = Path(output_path)
        ensure_dir(path.parent)
        report.report_path = str(path)
        path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return report

    @staticmethod
    def _emit(callback: Callable[[str], None] | None, status: str) -> None:
        if callback:
            callback(status)

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
            report.generated_test_results.append(
                sandbox.skipped(
                    name="run generated PatchGuard tests",
                    kind="generated_tests",
                    reason=reason,
                    command=["docker", "run", "...", GENERATED_TEST_SCRIPT],
                )
            )
            return

        compile_run = sandbox.run_generated_test_compile(
            repo_dir=repo_dir,
            timeout_seconds=self.settings.command_timeout_seconds,
        )
        report.generated_test_results.append(compile_run)
        if compile_run.status != RunStatus.PASSED:
            report.generated_test_results.append(
                sandbox.skipped(
                    name="run generated PatchGuard tests",
                    kind="generated_tests",
                    reason="Generated test compilation failed; pytest was not run",
                    command=["docker", "run", "...", GENERATED_TEST_SCRIPT],
                )
            )
            return

        generated_tests = sandbox.run_generated_tests(
            repo_dir=repo_dir,
            timeout_seconds=self.settings.command_timeout_seconds,
        )
        report.generated_test_results.append(generated_tests)
        report.test_results.append(self._test_result_from_tool_run(generated_tests))

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

    def _mark_docker_skipped(self, report: RiskReport, *, reason: str) -> None:
        dependency_run = ToolRun(
            name="install repository dependencies",
            kind="dependency_install",
            status=RunStatus.SKIPPED,
            summary=reason,
            command=self.command_runner.skipped(["docker", "run", "..."], reason),
        )
        report.dependency_install = dependency_run
        report.sandbox_results.append(dependency_run)

        existing_tests = ToolRun(
            name="run existing pytest suite",
            kind="existing_tests",
            status=RunStatus.SKIPPED,
            summary=reason,
            command=self.command_runner.skipped(["docker", "run", "...", "python -m pytest -q"], reason),
        )
        report.existing_tests = existing_tests
        report.test_results.append(self._test_result_from_tool_run(existing_tests))

        report.generated_test_results.append(
            ToolRun(
                name="run generated PatchGuard tests",
                kind="generated_tests",
                status=RunStatus.SKIPPED,
                summary=reason,
                command=self.command_runner.skipped(["docker", "run", "...", GENERATED_TEST_SCRIPT], reason),
            )
        )
        report.static_analysis_results.append(
            ToolRun(
                name="ruff check",
                kind="static_analysis",
                status=RunStatus.SKIPPED,
                summary=reason,
                command=self.command_runner.skipped(["docker", "run", "...", "python -m ruff check ."], reason),
            )
        )
        report.static_analysis_results.append(
            ToolRun(
                name="bandit security scan",
                kind="security_scan",
                status=RunStatus.SKIPPED,
                summary=reason,
                command=self.command_runner.skipped(["docker", "run", "...", "python -m bandit -r ."], reason),
            )
        )


class PatchGuardRunner:
    """Orchestrates GitHub metadata, checkout, Docker execution, scans, and scoring."""

    def __init__(
        self,
        *,
        settings: PatchGuardSettings | None = None,
        github_service: GitHubService | None = None,
        command_runner: CommandRunner | None = None,
    ) -> None:
        self.settings = settings or PatchGuardSettings()
        self.command_runner = command_runner or CommandRunner()
        self.github_service = github_service or GitHubService()
        self.clone_service = CloneService(
            command_runner=self.command_runner,
            timeout_seconds=self.settings.command_timeout_seconds,
        )
        self.function_extractor = FunctionExtractor()
        self.test_generation_service = TestGenerationService()
        self.risk_score_service = RiskScoreService()

    def run(
        self,
        pr_url: str,
        *,
        output_path: str | Path | None = None,
        runs_dir: str | Path | None = None,
        skip_docker: bool = False,
        skip_llm: bool = False,
        docker_image: str | None = None,
    ) -> PatchGuardReport:
        report = PatchGuardReport(input_pr_url=pr_url)
        run_dir = self._new_run_dir(runs_dir or self.settings.runs_dir)

        try:
            pr_data = self.github_service.fetch_pull_request(pr_url)
            report.pr = pr_data.metadata
            report.changed_files = pr_data.changed_files
        except Exception as exc:  # noqa: BLE001 - top-level report must survive API failures.
            report.errors.append(f"GitHub metadata fetch failed: {exc}")
            return self._finalize(report, output_path)

        checkout = self.clone_service.checkout_pull_request(report.pr, run_dir)
        report.sandbox_results.extend(checkout.tool_runs)
        if checkout.repo_dir is None:
            report.errors.append("Repository checkout failed; Docker tests and scans were not run")
            return self._finalize(report, output_path)

        repo_dir = checkout.repo_dir
        report.changed_symbols = self.function_extractor.extract(repo_dir, report.changed_files)
        report.changed_functions = self.function_extractor.extract_changed_functions(
            repo_dir,
            report.changed_files,
        )
        generation_service = (
            TestGenerationService(enabled=False)
            if skip_llm
            else self.test_generation_service
        )
        generation = generation_service.generate(
            repo_dir,
            report.changed_files,
            report.changed_functions,
        )
        report.generated_tests = generation.generated_tests
        report.test_generation = generation.tool_run

        if skip_docker:
            self._mark_docker_skipped(report, reason="Docker execution disabled by --skip-docker")
            return self._finalize(report, output_path)

        sandbox = SandboxService(
            command_runner=self.command_runner,
            docker_image=docker_image or self.settings.docker_image,
            limits=self.settings.sandbox_limits,
        )
        security_scanner = SecurityScanService(sandbox)

        docker_build = sandbox.build_image(self.settings.docker_build_timeout_seconds)
        report.sandbox_results.append(docker_build)
        if docker_build.status != RunStatus.PASSED:
            self._mark_docker_skipped(report, reason="Docker image build failed")
            return self._finalize(report, output_path)

        dependency_run = sandbox.run_in_repo(
            repo_dir=repo_dir,
            name="install repository dependencies",
            kind="dependency_install",
            script=DEPENDENCY_INSTALL_SCRIPT,
            timeout_seconds=self.settings.command_timeout_seconds,
        )
        self._mark_skipped_when_stdout_contains(
            dependency_run,
            marker="No requirements.txt or pyproject.toml found",
            summary="No requirements.txt or pyproject.toml found; dependency install skipped",
        )
        report.sandbox_results.append(dependency_run)

        if dependency_run.status in {RunStatus.FAILED, RunStatus.ERROR}:
            existing_tests = sandbox.skipped(
                name="run existing pytest suite",
                kind="existing_tests",
                reason="Dependency installation failed; existing tests were not run",
                command=["docker", "run", "...", "python -m pytest -q"],
            )
        else:
            existing_tests = sandbox.run_in_repo(
                repo_dir=repo_dir,
                name="run existing pytest suite",
                kind="existing_tests",
                script=EXISTING_TEST_SCRIPT,
                timeout_seconds=self.settings.command_timeout_seconds,
            )
            self._mark_skipped_when_stdout_contains(
                existing_tests,
                marker="No pytest tests discovered",
                summary="No pytest tests discovered; existing test run skipped",
            )
        report.existing_test_results.append(existing_tests)

        if dependency_run.status in {RunStatus.FAILED, RunStatus.ERROR}:
            report.generated_test_results.append(
                sandbox.skipped(
                    name="run generated PatchGuard tests",
                    kind="generated_tests",
                    reason="Dependency installation failed; generated tests were not run",
                    command=["docker", "run", "...", GENERATED_TEST_SCRIPT],
                )
            )
        elif report.generated_tests:
            compile_run = sandbox.run_generated_test_compile(
                repo_dir=repo_dir,
                timeout_seconds=self.settings.command_timeout_seconds,
            )
            report.generated_test_results.append(compile_run)
            if compile_run.status == RunStatus.PASSED:
                generated_tests = sandbox.run_generated_tests(
                    repo_dir=repo_dir,
                    timeout_seconds=self.settings.command_timeout_seconds,
                )
                report.generated_test_results.append(generated_tests)
            else:
                report.generated_test_results.append(
                    sandbox.skipped(
                        name="run generated PatchGuard tests",
                        kind="generated_tests",
                        reason="Generated test compilation failed; pytest was not run",
                        command=["docker", "run", "...", GENERATED_TEST_SCRIPT],
                    )
                )
        else:
            report.generated_test_results.append(
                sandbox.skipped(
                    name="run generated PatchGuard tests",
                    kind="generated_tests",
                    reason=(
                        report.test_generation.summary
                        if report.test_generation
                        else "No generated tests available"
                    ),
                    command=["docker", "run", "...", GENERATED_TEST_SCRIPT],
                )
            )

        ruff_run, static_findings = security_scanner.run_ruff(repo_dir, report.changed_files)
        report.static_analysis_results.append(ruff_run)
        report.static_findings.extend(static_findings)

        bandit_run, security_findings = security_scanner.run_bandit(repo_dir, report.changed_files)
        report.static_analysis_results.append(bandit_run)
        report.security_findings.extend(security_findings)

        return self._finalize(report, output_path)

    def _mark_docker_skipped(self, report: PatchGuardReport, *, reason: str) -> None:
        command_runner = self.command_runner
        report.sandbox_results.append(
            ToolRun(
                name="install repository dependencies",
                kind="dependency_install",
                status=RunStatus.SKIPPED,
                summary=reason,
                command=command_runner.skipped(["docker", "run", "..."], reason),
            )
        )
        report.existing_test_results.append(
            ToolRun(
                name="run existing pytest suite",
                kind="existing_tests",
                status=RunStatus.SKIPPED,
                summary=reason,
                command=command_runner.skipped(["docker", "run", "...", "python -m pytest -q"], reason),
            )
        )
        report.generated_test_results.append(
            ToolRun(
                name="run generated PatchGuard tests",
                kind="generated_tests",
                status=RunStatus.SKIPPED,
                summary=reason,
                command=command_runner.skipped(["docker", "run", "...", GENERATED_TEST_SCRIPT], reason),
            )
        )
        report.static_analysis_results.append(
            ToolRun(
                name="ruff check",
                kind="static_analysis",
                status=RunStatus.SKIPPED,
                summary=reason,
                command=command_runner.skipped(["docker", "run", "...", "python -m ruff check ."], reason),
            )
        )
        report.static_analysis_results.append(
            ToolRun(
                name="bandit security scan",
                kind="security_scan",
                status=RunStatus.SKIPPED,
                summary=reason,
                command=command_runner.skipped(["docker", "run", "...", "python -m bandit -r ."], reason),
            )
        )

    def _finalize(
        self,
        report: PatchGuardReport,
        output_path: str | Path | None,
    ) -> PatchGuardReport:
        self.risk_score_service.score(report)
        if report.pr is None:
            report.status = "failed"
        elif report.errors:
            report.status = "partial"
        elif any(run.status == RunStatus.SKIPPED for run in self._all_runs(report)):
            report.status = "partial"
        else:
            report.status = "complete"
        path = output_path or self._default_report_path(report)
        write_json_report(report, path)
        return report

    def _default_report_path(self, report: PatchGuardReport) -> Path:
        ensure_dir(self.settings.report_dir)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        if report.pr:
            name = f"{report.pr.owner}_{report.pr.repo}_{report.pr.number}_{stamp}.json"
        else:
            name = f"patchguard_report_{stamp}.json"
        return self.settings.report_dir / name

    def _new_run_dir(self, runs_dir: str | Path) -> Path:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        safe_stamp = re.sub(r"[^A-Za-z0-9_.-]", "-", stamp)
        return ensure_dir(Path(runs_dir) / f"run-{safe_stamp}")

    @staticmethod
    def _all_runs(report: PatchGuardReport):
        yield from report.sandbox_results
        yield from report.existing_test_results
        yield from report.generated_test_results
        yield from report.static_analysis_results

    @staticmethod
    def _mark_skipped_when_stdout_contains(run: ToolRun, *, marker: str, summary: str) -> None:
        stdout = run.command.stdout_tail if run.command else ""
        if marker in stdout and run.status == RunStatus.PASSED:
            run.status = RunStatus.SKIPPED
            run.summary = summary
