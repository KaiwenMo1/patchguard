from __future__ import annotations

from patchguard.cli import build_parser


def test_analyze_parser_accepts_package_friendly_options() -> None:
    args = build_parser().parse_args(
        [
            "analyze",
            "https://github.com/owner/repo/pull/123",
            "--out",
            "report.md",
            "--format",
            "markdown",
            "--skip-llm",
            "--timeout",
            "180",
            "--keep-workspace",
            "--comment",
            "--github-annotations",
            "--github-step-summary",
            "--fail-on-do-not-merge",
        ]
    )

    assert args.command == "analyze"
    assert args.output_format == "markdown"
    assert args.skip_llm is True
    assert args.timeout == 180
    assert args.keep_workspace is True
    assert args.comment is True
    assert args.github_annotations is True
    assert args.github_step_summary is True
    assert args.fail_on_do_not_merge is True
