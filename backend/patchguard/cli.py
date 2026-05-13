"""PatchGuard local CLI."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from patchguard.config import PatchGuardSettings
from patchguard.models import MergeDecision, PatchGuardReport, RiskReport
from patchguard.services.demo_report_service import DemoReportService
from patchguard.services.github_service import GitHubService, GitHubServiceError
from patchguard.services.markdown_report_service import write_markdown_report
from patchguard.services.pr_comment_service import GitHubPRCommentService, PRCommentResult
from patchguard.services.report_service import PatchGuardRunner, SkeletonReportService

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
        "--comment",
        action="store_true",
        help="Post or update a concise PatchGuard summary comment on the pull request.",
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
        "--cleanup-workspace",
        action="store_true",
        help="Delete the copied demo workspace after writing the report.",
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
            ).analyze(
                args.pr_url,
                Path(args.out),
                workspaces_dir=Path(args.workspaces_dir),
                cleanup_workspace=args.cleanup_workspace,
                skip_llm=args.skip_llm,
                skip_docker=args.skip_docker,
            )
        except ValueError as exc:
            parser.error(str(exc))
        except GitHubServiceError as exc:
            parser.exit(1, f"error: {exc}\n")
        if args.output_format == "markdown":
            write_markdown_report(report, Path(args.out))
        _print_skeleton_summary(report)
        _print_comment_result(_maybe_comment(args, report))
        return 0

    if args.command == "analyze-demo":
        try:
            report = DemoReportService().analyze(
                Path(args.demo_dir),
                Path(args.out),
                workspaces_dir=Path(args.workspaces_dir),
                skip_docker=args.skip_docker,
                cleanup_workspace=args.cleanup_workspace,
            )
        except ValueError as exc:
            parser.error(str(exc))
        _print_skeleton_summary(report)
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
    )
    report = runner.run(
        args.pr_url,
        output_path=Path(args.output) if args.output else None,
        runs_dir=Path(args.runs_dir),
        skip_docker=args.skip_docker,
        skip_llm=args.skip_llm,
        docker_image=args.docker_image,
    )
    _print_summary(report)
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
    print(
        "Changed files: "
        f"{len(report.changed_files)} "
        f"(+{report.pr.additions}/-{report.pr.deletions})"
    )
    print(f"Changed functions: {len(report.changed_functions)}")
    print(f"Risk: {report.risk_score}/100 ({report.risk_level.value})")
    print(f"Decision: {report.merge_decision.value}")
    print(f"Recommendation: {report.recommendation.value}")
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
    print(f"Decision: {report.merge_decision.value}")
    print(f"Recommendation: {report.recommendation.value}")
    if report.pr:
        print(f"PR: {report.pr.html_url}")
        print(
            "Changed files: "
            f"{len(report.changed_files)} "
            f"(+{report.pr.additions}/-{report.pr.deletions})"
        )
        print(f"Changed functions: {len(report.changed_functions)}")
    if report.test_generation:
        print(f"Test generation: {report.test_generation.status.value} ({report.test_generation.summary})")
    if report.risk_reasons:
        print("Top risk reasons:")
        for reason in report.risk_reasons[:5]:
            print(f"  - [{reason.category}] +{reason.score_impact}: {reason.reason}")
    if report.errors:
        print("Pipeline errors:")
        for error in report.errors:
            print(f"  - {error}")


def _maybe_comment(args: argparse.Namespace, report: RiskReport | PatchGuardReport) -> PRCommentResult | None:
    if not getattr(args, "comment", False):
        return None
    return GitHubPRCommentService(token=getattr(args, "github_token", None)).post_or_update(report)


def _print_comment_result(result: PRCommentResult | None) -> None:
    if result is None:
        return
    suffix = f" ({result.comment_url})" if result.comment_url else ""
    print(f"PR comment: {result.status} - {result.summary}{suffix}")


if __name__ == "__main__":
    sys.exit(main())
