from __future__ import annotations

from patchguard.models import ChangedFile, CommandResult, RunStatus, ToolRun
from patchguard.services.security_scan_service import SecurityScanService


def test_parse_bandit_json_findings() -> None:
    run = ToolRun(
        name="bandit security scan",
        kind="security_scan",
        status=RunStatus.FAILED,
        summary="bandit reported findings",
        command=CommandResult(
            command=["python", "-m", "bandit", "-r", ".", "-f", "json"],
            exit_code=1,
            stdout_tail="""{
              "results": [
                {
                  "filename": "./src/app.py",
                  "line_number": 42,
                  "issue_severity": "HIGH",
                  "issue_confidence": "MEDIUM",
                  "issue_text": "Use of assert detected",
                  "test_id": "B101",
                  "more_info": "https://bandit.readthedocs.io/"
                }
              ]
            }""",
        ),
    )

    findings = SecurityScanService._parse_bandit_findings(run)

    assert len(findings) == 1
    assert findings[0].tool == "bandit"
    assert findings[0].severity == "HIGH"
    assert findings[0].confidence == "MEDIUM"
    assert findings[0].filename == "src/app.py"
    assert findings[0].line_number == 42
    assert findings[0].message == "Use of assert detected"


def test_filters_bandit_findings_to_changed_python_files() -> None:
    run = ToolRun(
        name="bandit security scan",
        kind="security_scan",
        status=RunStatus.FAILED,
        summary="bandit reported findings",
        command=CommandResult(
            command=["python", "-m", "bandit", "-r", ".", "-f", "json"],
            exit_code=1,
            stdout_tail="""{
              "results": [
                {
                  "filename": "./src/changed.py",
                  "line_number": 10,
                  "issue_severity": "HIGH",
                  "issue_confidence": "HIGH",
                  "issue_text": "Use of eval detected",
                  "test_id": "B307"
                },
                {
                  "filename": "./src/baseline_noise.py",
                  "line_number": 20,
                  "issue_severity": "HIGH",
                  "issue_confidence": "HIGH",
                  "issue_text": "Existing baseline issue",
                  "test_id": "B307"
                },
                {
                  "filename": "./src/changed.py",
                  "line_number": 99,
                  "issue_severity": "HIGH",
                  "issue_confidence": "HIGH",
                  "issue_text": "Existing issue elsewhere in changed file",
                  "test_id": "B307"
                }
              ]
            }""",
        ),
    )

    raw_findings = SecurityScanService._parse_bandit_findings(run)
    findings = SecurityScanService._filter_security_findings(
        raw_findings,
        [
            ChangedFile(
                filename="src/changed.py",
                status="modified",
                patch="@@ -9,2 +9,3 @@\n context\n+eval(user_input)\n context\n",
            )
        ],
    )

    assert [finding.filename for finding in findings] == ["src/changed.py"]
    assert [finding.line_number for finding in findings] == [10]
