"""PatchGuard local CLI."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from patchguard.config import PatchGuardSettings
from patchguard.models import MergeDecision, PatchGuardReport, RiskReport
from patchguard.services.demo_report_service import DemoReportService
from patchguard.services.github_actions_service import emit_github_actions_output
from patchguard.services.github_app_backfill_service import (
    DEFAULT_BACKFILL_LIMIT,
    BackfillResult,
    GitHubAppBackfillError,
    GitHubAppBackfillService,
)
from patchguard.services.github_app_check_service import GitHubAppCheckService
from patchguard.services.github_app_job_service import DEFAULT_GITHUB_APP_DB
from patchguard.services.github_app_job_service import process_next_job as process_next_app_job
from patchguard.services.github_service import GitHubService, GitHubServiceError
from patchguard.services.markdown_report_service import write_markdown_report
from patchguard.services.memory_service import DEFAULT_MEMORY_DB, MemoryService
from patchguard.services.pr_comment_service import GitHubPRCommentService, PRCommentResult
from patchguard.services.report_service import PatchGuardRunner, SkeletonReportService
from patchguard.storage.sqlite_store import GitHubAppSQLiteStore

DEFAULT_TIMEOUT_SECONDS = PatchGuardSettings().command_timeout_seconds


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="patchguard",
        description="Generate an evidence-backed merge-risk report for a public GitHub Python PR.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze_parser = subparsers.add_parser(
        "analyze",
        help="Analyze a public GitHub PR and write a PatchGuard report.",
    )
    analyze_parser.add_argument("pr_url", help="Public GitHub pull request URL")
    analyze_parser.add_argument(
        "--out",
        required=True,
        help="Path to write the report",
    )
    analyze_parser.add_argument(
        "--format",
        dest="output_format",
        choices=["json", "markdown"],
        default="json",
        help="Report output format. Defaults to json.",
    )
    analyze_parser.add_argument(
        "--workspaces-dir",
        default=str(PatchGuardSettings().workspaces_dir),
        help="Directory where cloned PR workspaces are kept.",
    )
    workspace_group = analyze_parser.add_mutually_exclusive_group()
    workspace_group.add_argument(
        "--keep-workspace",
        action="store_true",
        help="Keep the cloned workspace after the run. This is the default for debugging.",
    )
    workspace_group.add_argument(
        "--cleanup-workspace",
        action="store_true",
        help="Delete the cloned workspace after writing the report.",
    )
    analyze_parser.add_argument(
        "--skip-llm",
        action="store_true",
        help="Disable LLM test generation even if OPENAI_API_KEY is set.",
    )
    analyze_parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Timeout in seconds for sandbox commands. Defaults to {DEFAULT_TIMEOUT_SECONDS}.",
    )
    analyze_parser.add_argument(
        "--docker-image",
        default=PatchGuardSettings().docker_image,
        help="Docker image tag for the Python sandbox.",
    )
    analyze_parser.add_argument(
        "--github-token",
        default=None,
        help="Optional GitHub token for higher API rate limits.",
    )
    analyze_parser.add_argument(
        "--skip-docker",
        action="store_true",
        help="Fetch, clone, and analyze the PR but skip Docker tests and static scans.",
    )
    analyze_parser.add_argument(
        "--compare-base",
        action="store_true",
        help="Run pytest at the base SHA and PR head SHA to detect regressions.",
    )
    analyze_parser.add_argument(
        "--use-memory",
        action="store_true",
        help="Retrieve similar prior PatchGuard evidence from the local memory index.",
    )
    analyze_parser.add_argument(
        "--memory-db",
        default=str(DEFAULT_MEMORY_DB),
        help="Path to the local PatchGuard memory SQLite database.",
    )
    analyze_parser.add_argument(
        "--comment",
        action="store_true",
        help="Post or update a concise PatchGuard summary comment on the pull request.",
    )
    analyze_parser.add_argument(
        "--github-annotations",
        action="store_true",
        help="Emit GitHub Actions workflow annotations for policy, test, and security evidence.",
    )
    analyze_parser.add_argument(
        "--github-step-summary",
        action="store_true",
        help="Append a concise PatchGuard summary to GITHUB_STEP_SUMMARY when running in GitHub Actions.",
    )
    analyze_parser.add_argument(
        "--fail-on-do-not-merge",
        action="store_true",
        help="Exit with code 2 when the recommendation is do_not_merge.",
    )

    demo_parser = subparsers.add_parser(
        "analyze-demo",
        help="Run PatchGuard against a local controlled demo under examples/.",
    )
    demo_parser.add_argument("demo_dir", help="Path to a local demo directory")
    demo_parser.add_argument(
        "--out",
        required=True,
        help="Path to write the JSON report",
    )
    demo_parser.add_argument(
        "--workspaces-dir",
        default=str(PatchGuardSettings().workspaces_dir),
        help="Directory where local demo workspaces are kept.",
    )
    demo_parser.add_argument(
        "--skip-docker",
        action="store_true",
        help="Generate local demo metadata but skip Docker evidence collection.",
    )
    demo_parser.add_argument(
        "--skip-llm",
        action="store_true",
        help="Disable behavioral contract extraction and LLM test generation for the local demo.",
    )
    demo_parser.add_argument(
        "--cleanup-workspace",
        action="store_true",
        help="Delete the copied demo workspace after writing the report.",
    )
    demo_parser.add_argument(
        "--github-annotations",
        action="store_true",
        help="Emit GitHub Actions workflow annotations for policy, test, and security evidence.",
    )
    demo_parser.add_argument(
        "--github-step-summary",
        action="store_true",
        help="Append a concise PatchGuard summary to GITHUB_STEP_SUMMARY when running in GitHub Actions.",
    )

    full_parser = subparsers.add_parser(
        "full",
        help="Run the richer local pipeline. Docker is required unless --skip-docker is used.",
    )
    full_parser.add_argument("pr_url", help="Public GitHub pull request URL")
    full_parser.add_argument("-o", "--output", help="Path to write the JSON report")
    full_parser.add_argument(
        "--runs-dir",
        default=str(PatchGuardSettings().runs_dir),
        help="Directory for temporary cloned repositories",
    )
    full_parser.add_argument(
        "--docker-image",
        default=PatchGuardSettings().docker_image,
        help="Docker image tag for the Python sandbox",
    )
    full_parser.add_argument(
        "--github-token",
        default=None,
        help="Optional GitHub token for higher API rate limits",
    )
    full_parser.add_argument(
        "--skip-docker",
        action="store_true",
        help="Fetch and analyze the PR but skip Docker execution and static scans.",
    )
    full_parser.add_argument(
        "--skip-llm",
        action="store_true",
        help="Disable LLM test generation even if OPENAI_API_KEY is set.",
    )
    full_parser.add_argument(
        "--compare-base",
        action="store_true",
        help="Run pytest at the base SHA and PR head SHA to detect regressions.",
    )
    full_parser.add_argument(
        "--use-memory",
        action="store_true",
        help="Retrieve similar prior PatchGuard evidence from the local memory index.",
    )
    full_parser.add_argument(
        "--memory-db",
        default=str(DEFAULT_MEMORY_DB),
        help="Path to the local PatchGuard memory SQLite database.",
    )
    full_parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Timeout in seconds for sandbox commands. Defaults to {DEFAULT_TIMEOUT_SECONDS}.",
    )
    full_parser.add_argument(
        "--fail-on-do-not-merge",
        action="store_true",
        help="Exit with code 2 when the recommendation is do_not_merge.",
    )
    full_parser.add_argument(
        "--comment",
        action="store_true",
        help="Post or update a concise PatchGuard summary comment on the pull request.",
    )
    full_parser.add_argument(
        "--github-annotations",
        action="store_true",
        help="Emit GitHub Actions workflow annotations for policy, test, and security evidence.",
    )
    full_parser.add_argument(
        "--github-step-summary",
        action="store_true",
        help="Append a concise PatchGuard summary to GITHUB_STEP_SUMMARY when running in GitHub Actions.",
    )

    backfill_parser = subparsers.add_parser(
        "app-backfill",
        help="Enqueue recent PRs for repositories selected in a GitHub App installation.",
    )
    backfill_parser.add_argument(
        "--installation-id",
        type=int,
        required=True,
        help="GitHub App installation ID to backfill.",
    )
    backfill_parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_BACKFILL_LIMIT,
        help=f"Maximum recent PRs to inspect per repository. Defaults to {DEFAULT_BACKFILL_LIMIT}.",
    )
    backfill_parser.add_argument(
        "--db-path",
        default=os.getenv("PATCHGUARD_APP_DB_PATH", str(DEFAULT_GITHUB_APP_DB)),
        help="Path to the local GitHub App SQLite database.",
    )
    backfill_parser.add_argument(
        "--github-token",
        default=None,
        help=(
            "Optional dev override token for listing PRs. "
            "Without this, PatchGuard uses GitHub App installation auth."
        ),
    )
    backfill_parser.add_argument(
        "--include-drafts",
        action="store_true",
        help="Include draft PRs in backfill jobs.",
    )

    worker_parser = subparsers.add_parser(
        "app-worker",
        help="Process queued GitHub App analysis jobs.",
    )
    worker_parser.add_argument(
        "--db-path",
        default=os.getenv("PATCHGUARD_APP_DB_PATH", str(DEFAULT_GITHUB_APP_DB)),
        help="Path to the local GitHub App SQLite database.",
    )
    worker_parser.add_argument(
        "--poll",
        action="store_true",
        help="Keep polling for queued jobs until interrupted.",
    )
    worker_parser.add_argument(
        "--interval",
        type=int,
        default=10,
        help="Polling interval in seconds when --poll is set. Defaults to 10.",
    )
    worker_parser.add_argument(
        "--limit",
        type=int,
        default=1,
        help="Maximum jobs to process when not polling. Defaults to 1.",
    )
    worker_parser.add_argument(
        "--publish-checks",
        action="store_true",
        help="Create and update GitHub Check Runs for processed jobs.",
    )
    worker_parser.add_argument(
        "--skip-docker",
        action="store_true",
        help="Skip Docker tests and scans. Reports will be partial.",
    )
    worker_parser.add_argument(
        "--enable-llm",
        action="store_true",
        help="Enable OpenAI-powered contract extraction, generated tests, and AI review.",
    )
    worker_parser.add_argument(
        "--cleanup-workspace",
        action="store_true",
        help="Delete cloned workspaces after each job.",
    )
    worker_parser.add_argument(
        "--compare-base",
        action="store_true",
        help="Run base-vs-head pytest comparison for each job.",
    )
    worker_parser.add_argument(
        "--use-memory",
        action="store_true",
        help="Retrieve similar prior PatchGuard evidence for each job.",
    )
    worker_parser.add_argument(
        "--memory-db",
        default=os.getenv("PATCHGUARD_MEMORY_DB", str(DEFAULT_MEMORY_DB)),
        help="Path to the local PatchGuard memory SQLite database.",
    )
    worker_parser.add_argument(
        "--public-base-url",
        default=None,
        help=(
            "Public FastAPI base URL used for GitHub Check Run details links, "
            "for example https://patchguard.example.com."
        ),
    )

    memory_parser = subparsers.add_parser(
        "memory-index",
        help="Index one report file or a directory of reports into local PatchGuard memory.",
    )
    memory_parser.add_argument("path", help="Report JSON file or directory containing report JSON files.")
    memory_parser.add_argument(
        "--db-path",
        default=str(DEFAULT_MEMORY_DB),
        help="Path to the local PatchGuard memory SQLite database.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "analyze":
        if args.timeout <= 0:
            parser.error("--timeout must be greater than 0")
        settings = PatchGuardSettings(
            command_timeout_seconds=args.timeout,
            docker_image=args.docker_image,
        )
        try:
            report = SkeletonReportService(
                settings=settings,
                github_service=GitHubService(token=args.github_token),
                git_token=args.github_token,
            ).analyze(
                args.pr_url,
                Path(args.out),
                workspaces_dir=Path(args.workspaces_dir),
                cleanup_workspace=args.cleanup_workspace,
                skip_llm=args.skip_llm,
                skip_docker=args.skip_docker,
                compare_base=args.compare_base,
                use_memory=args.use_memory,
                memory_db_path=Path(args.memory_db),
            )
        except ValueError as exc:
            parser.error(str(exc))
        except GitHubServiceError as exc:
            parser.exit(1, f"error: {exc}\n")
        if args.output_format == "markdown":
            write_markdown_report(report, Path(args.out))
        _maybe_emit_github_actions_output(args, report)
        _print_skeleton_summary(report)
        _print_comment_result(_maybe_comment(args, report))
        if args.fail_on_do_not_merge and report.merge_decision == MergeDecision.DO_NOT_MERGE:
            return 2
        return 0

    if args.command == "analyze-demo":
        try:
            report = DemoReportService().analyze(
                Path(args.demo_dir),
                Path(args.out),
                workspaces_dir=Path(args.workspaces_dir),
                skip_docker=args.skip_docker,
                skip_llm=args.skip_llm,
                cleanup_workspace=args.cleanup_workspace,
            )
        except ValueError as exc:
            parser.error(str(exc))
        _maybe_emit_github_actions_output(args, report)
        _print_skeleton_summary(report)
        return 0

    if args.command == "app-backfill":
        if args.limit <= 0:
            parser.error("--limit must be greater than 0")
        store = GitHubAppSQLiteStore(Path(args.db_path))
        store.initialize()
        service = GitHubAppBackfillService(
            store=store,
            token=args.github_token,
            include_drafts=args.include_drafts,
        )
        try:
            result = service.backfill_installation(
                args.installation_id,
                limit=args.limit,
            )
        except (GitHubAppBackfillError, KeyError) as exc:
            parser.exit(1, f"error: {exc}\n")
        _print_backfill_summary(result)
        return 0

    if args.command == "app-worker":
        return _run_app_worker(args, parser)

    if args.command == "memory-index":
        result = MemoryService(args.db_path).index_path(args.path)
        print(f"Memory DB: {result.db_path}")
        print(f"Reports seen: {result.reports_seen}")
        print(f"Documents indexed: {result.documents_indexed}")
        return 0

    if args.timeout <= 0:
        parser.error("--timeout must be greater than 0")
    settings = PatchGuardSettings(
        command_timeout_seconds=args.timeout,
        docker_image=args.docker_image,
    )
    runner = PatchGuardRunner(
        settings=settings,
        github_service=GitHubService(token=args.github_token),
        git_token=args.github_token,
    )
    report = runner.run(
        args.pr_url,
        output_path=Path(args.output) if args.output else None,
        runs_dir=Path(args.runs_dir),
        skip_docker=args.skip_docker,
        skip_llm=args.skip_llm,
        docker_image=args.docker_image,
        compare_base=args.compare_base,
        use_memory=args.use_memory,
        memory_db_path=Path(args.memory_db),
    )
    _print_summary(report)
    _maybe_emit_github_actions_output(args, report)
    _print_comment_result(_maybe_comment(args, report))

    if args.fail_on_do_not_merge and report.merge_decision == MergeDecision.DO_NOT_MERGE:
        return 2
    return 0


def _print_skeleton_summary(report: RiskReport) -> None:
    print(f"PatchGuard report: {report.report_path}")
    print(f"Status: {report.status}")
    print(f"PR: {report.pr.url}")
    print(f"Title: {report.pr.title}")
    if report.workspace_path:
        print(f"Workspace: {report.workspace_path}")
    if report.existing_tests:
        print(f"Existing tests: {report.existing_tests.status.value} ({report.existing_tests.summary})")
    if report.static_analysis_results:
        print(
            "Static scans: "
            + ", ".join(
                f"{run.name}={run.status.value}"
                for run in report.static_analysis_results
            )
        )
    if report.security_findings:
        print(f"Security findings: {len(report.security_findings)}")
    if report.contract_extraction:
        print(
            "Behavioral contract: "
            f"{report.contract_extraction.status.value} "
            f"({report.contract_extraction.summary})"
        )
    if report.test_generation:
        print(f"Test generation: {report.test_generation.status.value} ({report.test_generation.summary})")
    if report.generated_test_results:
        print(
            "Generated tests: "
            + ", ".join(
                f"{run.name}={run.status.value}"
                for run in report.generated_test_results
            )
        )
    if report.ai_review_run:
        print(f"AI review: {report.ai_review_run.status.value} ({report.ai_review_run.summary})")
    if report.ai_review and report.ai_review.executive_summary:
        print(f"AI summary: {report.ai_review.executive_summary}")
    print(
        "Changed files: "
        f"{len(report.changed_files)} "
        f"(+{report.pr.additions}/-{report.pr.deletions})"
    )
    print(f"Changed functions: {len(report.changed_functions)}")
    print(f"Risk: {report.risk_score}/100 ({report.risk_level.value})")
    _print_risk_breakdown(report)
    _print_policy_decision(report)
    print(f"Decision: {report.merge_decision.value}")
    print(f"Recommendation: {report.recommendation.value}")
    _print_failure_mappings(report)
    if report.risk_reasons:
        print("Top risk reasons:")
        for reason in report.risk_reasons[:5]:
            print(f"  - [{reason.category}] +{reason.score_impact}: {reason.reason}")
    if report.errors:
        print("Pipeline errors:")
        for error in report.errors:
            print(f"  - {error}")


def _print_summary(report: PatchGuardReport) -> None:
    print(f"PatchGuard report: {report.report_path}")
    print(f"Status: {report.status}")
    print(f"Risk: {report.risk_score}/100 ({report.risk_level.value})")
    _print_risk_breakdown(report)
    _print_policy_decision(report)
    print(f"Decision: {report.merge_decision.value}")
    print(f"Recommendation: {report.recommendation.value}")
    _print_failure_mappings(report)
    if report.pr:
        print(f"PR: {report.pr.html_url}")
        print(
            "Changed files: "
            f"{len(report.changed_files)} "
            f"(+{report.pr.additions}/-{report.pr.deletions})"
        )
        print(f"Changed functions: {len(report.changed_functions)}")
    if report.contract_extraction:
        print(
            "Behavioral contract: "
            f"{report.contract_extraction.status.value} "
            f"({report.contract_extraction.summary})"
        )
    if report.test_generation:
        print(f"Test generation: {report.test_generation.status.value} ({report.test_generation.summary})")
    if report.ai_review_run:
        print(f"AI review: {report.ai_review_run.status.value} ({report.ai_review_run.summary})")
    if report.ai_review and report.ai_review.executive_summary:
        print(f"AI summary: {report.ai_review.executive_summary}")
    if report.risk_reasons:
        print("Top risk reasons:")
        for reason in report.risk_reasons[:5]:
            print(f"  - [{reason.category}] +{reason.score_impact}: {reason.reason}")
    if report.errors:
        print("Pipeline errors:")
        for error in report.errors:
            print(f"  - {error}")


def _print_risk_breakdown(report: RiskReport | PatchGuardReport) -> None:
    if not report.risk_breakdown:
        return
    breakdown = report.risk_breakdown
    print(
        "Risk breakdown: "
        f"change={breakdown.change_size_risk}, "
        f"tests={breakdown.test_coverage_risk}, "
        f"behavior={breakdown.behavioral_risk}, "
        f"security={breakdown.security_risk}, "
        f"uncertainty={breakdown.uncertainty_risk}"
    )


def _print_policy_decision(report: RiskReport | PatchGuardReport) -> None:
    decision = report.policy_decision
    rules = ", ".join(decision.triggered_rules) if decision.triggered_rules else "none"
    print(f"Policy: {decision.decision.value} (rules: {rules})")


def _print_failure_mappings(report: RiskReport | PatchGuardReport) -> None:
    if not report.failure_mappings:
        return
    print("Failed generated tests:")
    for mapping in report.failure_mappings[:5]:
        target = (
            f"{mapping.target_file}::{mapping.target_function}"
            if mapping.target_file and mapping.target_function
            else "unknown target"
        )
        print(f"  - {mapping.failed_test} -> {target}: {mapping.failure_summary}")
        print(f"    Risk: {mapping.risk_message}")
        print(f"    Next: {mapping.suggested_next_step}")


def _print_backfill_summary(result: BackfillResult) -> None:
    print(f"GitHub App installation: {result.github_installation_id}")
    print(f"Repositories scanned: {result.repositories_scanned}")
    print(f"Pull requests seen: {result.pull_requests_seen}")
    print(f"Jobs created: {result.jobs_created}")
    print(f"Duplicate jobs skipped: {result.duplicates_skipped}")
    print(f"Draft PRs skipped: {result.draft_prs_skipped}")
    if result.jobs:
        print("Backfill jobs:")
        for job in result.jobs[:20]:
            status = "created" if job.created else "duplicate"
            print(
                f"  - {status}: job={job.job_id} "
                f"{job.repository_full_name}#{job.pr_number} @{job.head_sha}"
            )


def _run_app_worker(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if args.interval <= 0:
        parser.error("--interval must be greater than 0")
    if args.limit <= 0:
        parser.error("--limit must be greater than 0")

    check_service_factory = None
    if args.publish_checks:
        details_url = details_url_template(
            args.public_base_url or os.getenv("PATCHGUARD_PUBLIC_BASE_URL")
        )
        check_service_factory = (
            (lambda: GitHubAppCheckService(details_url=details_url))
            if details_url
            else GitHubAppCheckService
        )
    processed = 0

    while True:
        result = process_next_app_job(
            db_path=Path(args.db_path),
            check_service_factory=check_service_factory,
            skip_llm=not args.enable_llm,
            skip_docker=args.skip_docker,
            cleanup_workspace=args.cleanup_workspace,
            compare_base=args.compare_base,
            use_memory=args.use_memory,
            memory_db_path=Path(args.memory_db),
        )
        if result is None:
            if args.poll:
                print(f"No queued PatchGuard jobs. Sleeping {args.interval}s...")
                time.sleep(args.interval)
                continue
            print("No queued PatchGuard jobs.")
            return 0

        processed += 1
        job = result.job
        message = f"Processed job {job.id}: {job.repository_full_name}"
        if job.pr_number is not None:
            message += f" PR #{job.pr_number}"
        message += f" -> {job.status.value}"
        if job.report_path:
            message += f" ({job.report_path})"
        print(message)
        if job.error:
            print(f"Job evidence/error: {job.error}")

        if not args.poll and processed >= args.limit:
            return 0


def details_url_template(public_base_url: str | None) -> str | None:
    if not public_base_url:
        return None
    return public_base_url.rstrip("/") + "/api/app/jobs/{job_id}/report"


def _maybe_comment(args: argparse.Namespace, report: RiskReport | PatchGuardReport) -> PRCommentResult | None:
    if not getattr(args, "comment", False):
        return None
    return GitHubPRCommentService(token=getattr(args, "github_token", None)).post_or_update(report)


def _maybe_emit_github_actions_output(args: argparse.Namespace, report: RiskReport | PatchGuardReport) -> None:
    if not (getattr(args, "github_annotations", False) or getattr(args, "github_step_summary", False)):
        return
    result = emit_github_actions_output(
        report,
        annotations=getattr(args, "github_annotations", False),
        step_summary=getattr(args, "github_step_summary", False),
    )
    if result.step_summary_written:
        print(f"GitHub step summary: {result.step_summary_path}")
    if result.annotations_emitted:
        print(f"GitHub annotations: {result.annotations_emitted}")


def _print_comment_result(result: PRCommentResult | None) -> None:
    if result is None:
        return
    suffix = f" ({result.comment_url})" if result.comment_url else ""
    print(f"PR comment: {result.status} - {result.summary}{suffix}")


if __name__ == "__main__":
    sys.exit(main())
