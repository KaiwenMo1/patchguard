"""LLM-based generated pytest tests for changed Python functions."""

from __future__ import annotations

import ast
import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import requests

from patchguard.models import (
    BehavioralContract,
    ChangedFile,
    ChangedFunction,
    GeneratedTest,
    GeneratedTestMetadata,
    RunStatus,
    ToolRun,
)
from patchguard.utils.command_runner import CommandRunner
from patchguard.utils.file_utils import ensure_dir

DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
GENERATED_TEST_DIR = Path(".patchguard") / "generated_tests"
DISALLOWED_IMPORT_ROOTS = {"httpx", "requests", "socket", "subprocess", "urllib"}


class TestGenerationError(RuntimeError):
    """Raised when generated pytest code is missing or unsafe."""


class LLMTestProvider(Protocol):
    provider_name: str
    model: str

    def generate_pytest(self, prompt: str) -> str:
        """Return raw pytest code from an LLM provider."""


@dataclass(frozen=True)
class TestGenerationResult:
    generated_tests: list[GeneratedTest]
    metadata: list[GeneratedTestMetadata]
    tool_run: ToolRun


class OpenAIResponsesProvider:
    """Minimal OpenAI Responses API client using requests."""

    provider_name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_OPENAI_MODEL,
        base_url: str = DEFAULT_OPENAI_BASE_URL,
        timeout_seconds: int = 60,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def generate_pytest(self, prompt: str) -> str:
        response = requests.post(
            f"{self.base_url}/responses",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "instructions": (
                    "You generate concise, deterministic Python pytest code. "
                    "Return only valid Python code. Do not use markdown."
                ),
                "input": prompt,
                "temperature": 0,
                "max_output_tokens": 2500,
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return self._extract_text(response.json())

    @staticmethod
    def _extract_text(payload: dict[str, Any]) -> str:
        output_text = payload.get("output_text")
        if isinstance(output_text, str):
            return output_text

        parts: list[str] = []
        for item in payload.get("output", []):
            if not isinstance(item, dict):
                continue
            for content in item.get("content", []):
                if not isinstance(content, dict):
                    continue
                text = content.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)


class TestGenerationService:
    """Generate targeted pytest files with an LLM when OPENAI_API_KEY is available."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        provider: LLMTestProvider | None = None,
        enabled: bool = True,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.getenv("OPENAI_API_KEY")
        self.model = model or os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL)
        self.provider = provider
        self.enabled = enabled

    def generate(
        self,
        repo_dir: str | Path,
        changed_files: list[ChangedFile],
        changed_functions: list[ChangedFunction],
        behavioral_contract: BehavioralContract | None = None,
    ) -> TestGenerationResult:
        repo_path = Path(repo_dir)
        targets = self._targets(changed_files, changed_functions)
        if not targets:
            return self._skipped("No changed Python functions available for LLM test generation")
        if not self.enabled:
            return self._skipped("LLM test generation disabled by --skip-llm")
        if not self.api_key:
            return self._skipped("OPENAI_API_KEY is not set; LLM test generation skipped")

        provider = self.provider or OpenAIResponsesProvider(
            api_key=self.api_key,
            model=self.model,
            base_url=self.base_url,
        )
        generated_tests: list[GeneratedTest] = []
        metadata: list[GeneratedTestMetadata] = []
        errors: list[str] = []
        for file_path, functions in targets.items():
            changed_file = next(file for file in changed_files if file.filename == file_path)
            prompt = self._prompt_for_file(
                repo_path,
                changed_file,
                functions,
                behavioral_contract=behavioral_contract,
            )
            try:
                raw_code = provider.generate_pytest(prompt)
                code = self._post_process(raw_code)
            except Exception as exc:  # noqa: BLE001 - report failures instead of crashing.
                errors.append(f"{file_path}: {exc}")
                continue

            relative_test_path = GENERATED_TEST_DIR / self._generated_test_filename(file_path)
            output_path = repo_path / relative_test_path
            ensure_dir(output_path.parent)
            output_path.write_text(code.rstrip() + "\n", encoding="utf-8")
            test_metadata = self._metadata_for_generated_code(
                code,
                file_path=file_path,
                functions=functions,
                contract=behavioral_contract,
            )
            metadata.extend(test_metadata)
            generated_tests.append(
                GeneratedTest(
                    path=str(relative_test_path),
                    target_files=[file_path],
                    target_functions=[function.qualified_name for function in functions],
                    rationale="LLM-generated regression tests for changed Python functions.",
                    code=code.rstrip() + "\n",
                    provider=provider.provider_name,
                    model=provider.model,
                    metadata=test_metadata,
                )
            )

        if metadata:
            self._write_metadata(repo_path, metadata)

        if generated_tests and not errors:
            return TestGenerationResult(
                generated_tests=generated_tests,
                metadata=metadata,
                tool_run=ToolRun(
                    name="generate LLM pytest tests",
                    kind="test_generation",
                    status=RunStatus.PASSED,
                    summary=f"Generated {len(generated_tests)} pytest file(s)",
                    findings_count=len(generated_tests),
                ),
            )
        if generated_tests:
            return TestGenerationResult(
                generated_tests=generated_tests,
                metadata=metadata,
                tool_run=ToolRun(
                    name="generate LLM pytest tests",
                    kind="test_generation",
                    status=RunStatus.ERROR,
                    summary=(
                        f"Generated {len(generated_tests)} pytest file(s); "
                        f"{len(errors)} target(s) failed: {'; '.join(errors[:3])}"
                    ),
                    findings_count=len(generated_tests),
                ),
            )
        return TestGenerationResult(
            generated_tests=[],
            metadata=[],
            tool_run=ToolRun(
                name="generate LLM pytest tests",
                kind="test_generation",
                status=RunStatus.ERROR,
                summary=f"LLM test generation failed: {'; '.join(errors[:3])}",
            ),
        )

    @staticmethod
    def _targets(
        changed_files: list[ChangedFile],
        changed_functions: list[ChangedFunction],
    ) -> dict[str, list[ChangedFunction]]:
        python_files = {
            file.filename
            for file in changed_files
            if file.is_python and not file.is_test and file.status != "removed"
        }
        targets: dict[str, list[ChangedFunction]] = defaultdict(list)
        for function in changed_functions:
            if function.file_path in python_files and function.symbol_type != "file":
                targets[function.file_path].append(function)
        return dict(targets)

    @staticmethod
    def _post_process(raw_code: str) -> str:
        code = TestGenerationService._strip_markdown_fences(raw_code).strip()
        if not code:
            raise TestGenerationError("LLM returned empty pytest code")
        if "```" in code:
            raise TestGenerationError("LLM returned markdown instead of plain Python code")
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            raise TestGenerationError(f"Generated pytest code is not valid Python: {exc}") from exc
        TestGenerationService._reject_unsafe_constructs(tree)
        return code

    @staticmethod
    def _strip_markdown_fences(raw_code: str) -> str:
        text = raw_code.strip()
        match = re.fullmatch(r"```(?:python|py)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return text

    @staticmethod
    def _metadata_for_generated_code(
        code: str,
        *,
        file_path: str,
        functions: list[ChangedFunction],
        contract: BehavioralContract | None = None,
    ) -> list[GeneratedTestMetadata]:
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return []
        behavior_targets = TestGenerationService._behavior_targets(contract)
        test_nodes = sorted(
            (
                node
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name.startswith("test_")
            ),
            key=lambda node: node.lineno,
        )
        metadata: list[GeneratedTestMetadata] = []
        for index, node in enumerate(test_nodes):
            if functions:
                target_function = functions[index % len(functions)].qualified_name
            else:
                target_function = file_path
            if behavior_targets:
                behavior_checked, test_type = behavior_targets[index % len(behavior_targets)]
            else:
                behavior_checked = f"Generated regression behavior for {target_function}"
                test_type = "regression"
            metadata.append(
                GeneratedTestMetadata(
                    test_name=node.name,
                    target_file=file_path,
                    target_function=target_function,
                    behavior_checked=behavior_checked,
                    test_type=test_type,
                )
            )
        return metadata

    @staticmethod
    def _write_metadata(repo_path: Path, metadata: list[GeneratedTestMetadata]) -> None:
        metadata_path = repo_path / GENERATED_TEST_DIR / "metadata.json"
        ensure_dir(metadata_path.parent)
        metadata_path.write_text(
            json.dumps(
                {"tests": [item.model_dump(mode="json") for item in metadata]},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _reject_unsafe_constructs(tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [alias.name for alias in node.names]
                if isinstance(node, ast.ImportFrom) and node.module:
                    names.append(node.module)
                for name in names:
                    if name.split(".", 1)[0] in DISALLOWED_IMPORT_ROOTS:
                        raise TestGenerationError(f"Generated tests import disallowed module: {name}")
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute) and node.func.attr == "sleep":
                    raise TestGenerationError("Generated tests call sleep")
                if isinstance(node.func, ast.Name) and node.func.id == "sleep":
                    raise TestGenerationError("Generated tests call sleep")

    def _prompt_for_file(
        self,
        repo_path: Path,
        changed_file: ChangedFile,
        functions: list[ChangedFunction],
        *,
        behavioral_contract: BehavioralContract | None = None,
    ) -> str:
        changed_function_text = "\n\n".join(
            (
                f"Function: {function.qualified_name}\n"
                f"Lines: {function.start_line}-{function.end_line}\n"
                f"Changed lines: {function.changed_lines}\n"
                f"Source:\n{function.source_code}"
            )
            for function in functions
        )
        nearby_context = self._nearby_context(repo_path, functions)
        return f"""Generate pytest regression tests for this changed Python file.

Rules:
- Output only valid Python pytest code.
- Do not use markdown fences or explanatory text.
- No network calls.
- Do not use sleeps or time-based waits.
- Keep tests deterministic.
- Focus on edge cases and regression behavior for the changed functions.
- Prefer the behavioral contract below when choosing assertions.
- Cover intended new behavior, preserved existing behavior, edge cases, and invalid inputs when possible.
- Prefer importing from the changed module when possible.
- If direct imports are risky because dependencies may be absent, use ast/importlib guards or pytest.skip with a clear reason.

File path:
{changed_file.filename}

GitHub patch:
{changed_file.patch or "No patch text available."}

Behavioral contract:
{self._contract_text(behavioral_contract)}

Changed functions:
{changed_function_text}

Nearby context:
{nearby_context}
"""

    @staticmethod
    def _nearby_context(repo_path: Path, functions: list[ChangedFunction], radius: int = 25) -> str:
        if not functions:
            return ""
        file_path = repo_path / functions[0].file_path
        if not file_path.exists():
            return ""
        lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(1, min(function.start_line for function in functions) - radius)
        end = min(len(lines), max(function.end_line for function in functions) + radius)
        numbered = [f"{line_number}: {lines[line_number - 1]}" for line_number in range(start, end + 1)]
        return "\n".join(numbered)

    @staticmethod
    def _generated_test_filename(file_path: str) -> str:
        safe_name = re.sub(r"[^A-Za-z0-9]+", "_", file_path).strip("_").lower()
        if not safe_name:
            safe_name = "python_file"
        return f"test_patchguard_generated_{safe_name}.py"

    @staticmethod
    def _contract_text(contract: BehavioralContract | None) -> str:
        if contract is None:
            return "No behavioral contract was extracted."
        sections = [
            ("Intended new behaviors", contract.intended_new_behaviors),
            ("Existing behaviors to preserve", contract.existing_behaviors_to_preserve),
            ("Edge cases to test", contract.edge_cases_to_test),
            ("Invalid inputs to test", contract.invalid_inputs_to_test),
            ("Uncertainties", contract.contract_uncertainties),
        ]
        lines = [f"Confidence: {contract.confidence:.2f}"]
        for title, values in sections:
            lines.append(f"{title}:")
            if values:
                lines.extend(f"- {value}" for value in values)
            else:
                lines.append("- none")
        return "\n".join(lines)

    @staticmethod
    def _behavior_targets(
        contract: BehavioralContract | None,
    ) -> list[tuple[str, str]]:
        if contract is None:
            return []
        targets: list[tuple[str, str]] = []
        targets.extend((value, "new_behavior") for value in contract.intended_new_behaviors)
        targets.extend((value, "regression") for value in contract.existing_behaviors_to_preserve)
        targets.extend((value, "edge_case") for value in contract.edge_cases_to_test)
        targets.extend((value, "edge_case") for value in contract.invalid_inputs_to_test)
        return targets

    @staticmethod
    def _skipped(reason: str) -> TestGenerationResult:
        runner = CommandRunner()
        return TestGenerationResult(
            generated_tests=[],
            metadata=[],
            tool_run=ToolRun(
                name="generate LLM pytest tests",
                kind="test_generation",
                status=RunStatus.SKIPPED,
                summary=reason,
                command=runner.skipped(["openai", "responses", "create"], reason),
            ),
        )
