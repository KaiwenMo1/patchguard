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
            "--compare-base",
            "--use-memory",
            "--memory-db",
            ".patchguard/memory/test.db",
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
    assert args.compare_base is True
    assert args.use_memory is True
    assert args.memory_db == ".patchguard/memory/test.db"


def test_app_worker_parser_accepts_operational_options() -> None:
    args = build_parser().parse_args(
        [
            "app-worker",
            "--db-path",
            ".patchguard/github_app/patchguard-app.db",
            "--poll",
            "--interval",
            "5",
            "--limit",
            "3",
            "--publish-checks",
            "--skip-docker",
            "--enable-llm",
            "--cleanup-workspace",
            "--compare-base",
            "--use-memory",
            "--memory-db",
            ".patchguard/memory/test.db",
            "--public-base-url",
            "https://patchguard.example.com",
        ]
    )

    assert args.command == "app-worker"
    assert args.db_path == ".patchguard/github_app/patchguard-app.db"
    assert args.poll is True
    assert args.interval == 5
    assert args.limit == 3
    assert args.publish_checks is True
    assert args.skip_docker is True
    assert args.enable_llm is True
    assert args.cleanup_workspace is True
    assert args.compare_base is True
    assert args.use_memory is True
    assert args.memory_db == ".patchguard/memory/test.db"
    assert args.public_base_url == "https://patchguard.example.com"


def test_memory_index_parser_accepts_report_path() -> None:
    args = build_parser().parse_args(
        [
            "memory-index",
            ".patchguard/app_reports",
            "--db-path",
            ".patchguard/memory/test.db",
        ]
    )

    assert args.command == "memory-index"
    assert args.path == ".patchguard/app_reports"
    assert args.db_path == ".patchguard/memory/test.db"
