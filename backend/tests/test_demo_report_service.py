from __future__ import annotations

from pathlib import Path

from patchguard.services.demo_report_service import DemoReportService


def test_demo_patch_parser_extracts_changed_file_hunks() -> None:
    patch_text = Path("examples/demo_parser_bug/patch.diff").read_text(encoding="utf-8")

    changed_files = DemoReportService._changed_files_from_patch(patch_text)

    assert len(changed_files) == 1
    assert changed_files[0].filename == "parser_demo/parser.py"
    assert changed_files[0].status == "modified"
    assert changed_files[0].additions == 1
    assert changed_files[0].deletions == 1
    assert changed_files[0].patch is not None
    assert "@@ -1,7 +1,6 @@" in changed_files[0].patch
