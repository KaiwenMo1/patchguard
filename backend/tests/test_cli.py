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
        ]
    )

    assert args.command == "analyze"
    assert args.output_format == "markdown"
    assert args.skip_llm is True
    assert args.timeout == 180
    assert args.keep_workspace is True
    assert args.comment is True
