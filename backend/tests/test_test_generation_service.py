from __future__ import annotations

from patchguard.models import ChangedFile, ChangedFunction, RunStatus
from patchguard.services.test_generation_service import TestGenerationService as GenerationService


def test_skips_llm_generation_without_api_key(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    source = tmp_path / "src" / "app.py"
    source.parent.mkdir()
    source.write_text("def greet(name):\n    return f'hi {name}'\n", encoding="utf-8")

    result = GenerationService().generate(
        tmp_path,
        [ChangedFile(filename="src/app.py", status="modified", additions=2, deletions=0, changes=2)],
        [_changed_function()],
    )

    assert result.generated_tests == []
    assert result.tool_run.status == RunStatus.SKIPPED
    assert "OPENAI_API_KEY is not set" in result.tool_run.summary


def test_skip_llm_overrides_available_api_key(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    source = tmp_path / "src" / "app.py"
    source.parent.mkdir()
    source.write_text("def greet(name):\n    return f'hi {name}'\n", encoding="utf-8")

    result = GenerationService(enabled=False).generate(
        tmp_path,
        [ChangedFile(filename="src/app.py", status="modified", additions=2, deletions=0, changes=2)],
        [_changed_function()],
    )

    assert result.generated_tests == []
    assert result.tool_run.status == RunStatus.SKIPPED
    assert "--skip-llm" in result.tool_run.summary


def test_generates_llm_pytest_file_when_api_key_exists(tmp_path) -> None:
    source = tmp_path / "src" / "app.py"
    source.parent.mkdir()
    source.write_text("def greet(name):\n    return f'hi {name}'\n", encoding="utf-8")
    provider = FakeProvider(
        """```python
from src.app import greet


def test_greet_includes_name():
    assert greet("Ada") == "hi Ada"
```"""
    )

    result = GenerationService(api_key="test-key", provider=provider).generate(
        tmp_path,
        [
            ChangedFile(
                filename="src/app.py",
                status="modified",
                additions=2,
                deletions=0,
                changes=2,
                patch="@@ -2,1 +2,1 @@\n-    return name\n+    return f'hi {name}'\n",
            )
        ],
        [_changed_function()],
    )

    assert result.tool_run.status == RunStatus.PASSED
    assert len(result.generated_tests) == 1
    assert result.generated_tests[0].path == (
        ".patchguard/generated_tests/test_patchguard_generated_src_app_py.py"
    )
    assert result.generated_tests[0].target_functions == ["greet"]
    assert result.generated_tests[0].code.startswith("from src.app import greet")
    generated_path = tmp_path / result.generated_tests[0].path
    assert generated_path.exists()
    assert "```" not in generated_path.read_text(encoding="utf-8")
    assert "no network calls" in provider.prompt.lower()


def test_rejects_empty_llm_output(tmp_path) -> None:
    source = tmp_path / "src" / "app.py"
    source.parent.mkdir()
    source.write_text("def greet(name):\n    return f'hi {name}'\n", encoding="utf-8")

    result = GenerationService(api_key="test-key", provider=FakeProvider("```python\n\n```")).generate(
        tmp_path,
        [
            ChangedFile(
                filename="src/app.py",
                status="modified",
                additions=2,
                deletions=0,
                changes=2,
            )
        ],
        [_changed_function()],
    )

    assert result.generated_tests == []
    assert result.tool_run.status == RunStatus.ERROR
    assert "empty pytest code" in result.tool_run.summary


def test_rejects_generated_sleep_calls(tmp_path) -> None:
    source = tmp_path / "src" / "app.py"
    source.parent.mkdir()
    source.write_text("def greet(name):\n    return f'hi {name}'\n", encoding="utf-8")
    provider = FakeProvider("import time\n\ndef test_slow():\n    time.sleep(1)\n")

    result = GenerationService(api_key="test-key", provider=provider).generate(
        tmp_path,
        [ChangedFile(filename="src/app.py", status="modified")],
        [_changed_function()],
    )

    assert result.generated_tests == []
    assert result.tool_run.status == RunStatus.ERROR
    assert "sleep" in result.tool_run.summary


def _changed_function() -> ChangedFunction:
    return ChangedFunction(
        file_path="src/app.py",
        qualified_name="greet",
        symbol_type="function",
        start_line=1,
        end_line=2,
        source_code="def greet(name):\n    return f'hi {name}'",
        changed_lines=[2],
    )


class FakeProvider:
    provider_name = "fake-llm"
    model = "fake-model"

    def __init__(self, output: str) -> None:
        self.output = output
        self.prompt = ""

    def generate_pytest(self, prompt: str) -> str:
        self.prompt = prompt
        return self.output
