from __future__ import annotations

from patchguard.models import ChangedFile, ChangedFunction, RunStatus
from patchguard.services.contract_extraction_service import ContractExtractionService


def test_contract_extraction_skips_without_api_key(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _write_source(tmp_path)

    result = ContractExtractionService().extract(
        tmp_path,
        pr_title="Tighten parser behavior",
        changed_files=[_changed_file()],
        changed_functions=[_changed_function()],
    )

    assert result.tool_run.status == RunStatus.SKIPPED
    assert "OPENAI_API_KEY is not set" in result.tool_run.summary
    assert result.contract.confidence == 0
    assert result.contract.contract_uncertainties


def test_contract_extraction_parses_provider_json(tmp_path) -> None:
    _write_source(tmp_path)
    provider = FakeContractProvider(
        """
        {
          "intended_new_behaviors": ["empty input returns an empty list"],
          "existing_behaviors_to_preserve": ["valid tokens still parse"],
          "edge_cases_to_test": ["whitespace-only input"],
          "invalid_inputs_to_test": ["missing separator"],
          "contract_uncertainties": ["caller behavior is not visible"],
          "confidence": 0.83
        }
        """
    )

    result = ContractExtractionService(api_key="test-key", provider=provider).extract(
        tmp_path,
        pr_title="Tighten parser behavior",
        changed_files=[_changed_file()],
        changed_functions=[_changed_function()],
    )

    assert result.tool_run.status == RunStatus.PASSED
    assert result.tool_run.findings_count == 5
    assert result.contract.intended_new_behaviors == ["empty input returns an empty list"]
    assert result.contract.existing_behaviors_to_preserve == ["valid tokens still parse"]
    assert result.contract.edge_cases_to_test == ["whitespace-only input"]
    assert result.contract.invalid_inputs_to_test == ["missing separator"]
    assert result.contract.contract_uncertainties == ["caller behavior is not visible"]
    assert result.contract.confidence == 0.83
    assert "Changed patches" in provider.prompt


def test_contract_extraction_records_invalid_json_as_error(tmp_path) -> None:
    _write_source(tmp_path)

    result = ContractExtractionService(
        api_key="test-key",
        provider=FakeContractProvider("not json"),
    ).extract(
        tmp_path,
        pr_title="Tighten parser behavior",
        changed_files=[_changed_file()],
        changed_functions=[_changed_function()],
    )

    assert result.tool_run.status == RunStatus.ERROR
    assert "failed" in result.tool_run.summary.lower()
    assert result.contract.contract_uncertainties


def _write_source(tmp_path) -> None:
    source = tmp_path / "src" / "parser.py"
    source.parent.mkdir()
    source.write_text("def parse(value):\n    return value.split(',')\n", encoding="utf-8")


def _changed_file() -> ChangedFile:
    return ChangedFile(
        filename="src/parser.py",
        status="modified",
        additions=1,
        deletions=1,
        changes=2,
        patch="@@ -2,1 +2,1 @@\n-    return value.split()\n+    return value.split(',')\n",
    )


def _changed_function() -> ChangedFunction:
    return ChangedFunction(
        file_path="src/parser.py",
        qualified_name="parse",
        symbol_type="function",
        start_line=1,
        end_line=2,
        source_code="def parse(value):\n    return value.split(',')",
        changed_lines=[2],
    )


class FakeContractProvider:
    provider_name = "fake-llm"
    model = "fake-model"

    def __init__(self, output: str) -> None:
        self.output = output
        self.prompt = ""

    def generate_contract(self, prompt: str) -> str:
        self.prompt = prompt
        return self.output
