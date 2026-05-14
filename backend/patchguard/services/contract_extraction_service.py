"""LLM-assisted behavioral contract extraction for changed Python code."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import requests

from patchguard.models import (
    BehavioralContract,
    ChangedFile,
    ChangedFunction,
    RunStatus,
    ToolRun,
)
from patchguard.services.test_generation_service import (
    DEFAULT_OPENAI_BASE_URL,
    DEFAULT_OPENAI_MODEL,
)
from patchguard.utils.command_runner import CommandRunner


class BehavioralContractProvider(Protocol):
    provider_name: str
    model: str

    def generate_contract(self, prompt: str) -> str:
        """Return raw JSON text describing the intended behavior contract."""


@dataclass(frozen=True)
class ContractExtractionResult:
    contract: BehavioralContract
    tool_run: ToolRun


class OpenAIContractProvider:
    """Minimal OpenAI Responses API client for structured contract extraction."""

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

    def generate_contract(self, prompt: str) -> str:
        response = requests.post(
            f"{self.base_url}/responses",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "instructions": (
                    "You extract behavioral contracts from pull request diffs. "
                    "Return only compact JSON with the requested keys."
                ),
                "input": prompt,
                "temperature": 0,
                "max_output_tokens": 1600,
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


class ContractExtractionService:
    """Extract intended behavior changes to guide generated regression tests."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        provider: BehavioralContractProvider | None = None,
        enabled: bool = True,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.getenv("OPENAI_API_KEY")
        self.model = model or os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL)
        self.provider = provider
        self.enabled = enabled

    def extract(
        self,
        repo_dir: str | Path,
        *,
        pr_title: str | None,
        pr_body: str | None = None,
        changed_files: list[ChangedFile],
        changed_functions: list[ChangedFunction],
    ) -> ContractExtractionResult:
        targets = [
            file
            for file in changed_files
            if file.is_python and not file.is_test and file.status != "removed"
        ]
        if not targets:
            return self._skipped("No changed Python source files available for contract extraction")
        if not self.enabled:
            return self._skipped("Behavioral contract extraction disabled by --skip-llm")
        if not self.api_key:
            return self._skipped("OPENAI_API_KEY is not set; behavioral contract extraction skipped")

        provider = self.provider or OpenAIContractProvider(
            api_key=self.api_key,
            model=self.model,
            base_url=self.base_url,
        )
        prompt = self._prompt(
            Path(repo_dir),
            pr_title=pr_title,
            pr_body=pr_body,
            changed_files=targets,
            changed_functions=changed_functions,
        )
        try:
            raw = provider.generate_contract(prompt)
            contract = self._parse_contract(raw)
        except Exception as exc:  # noqa: BLE001 - failures are report evidence.
            return ContractExtractionResult(
                contract=BehavioralContract(
                    contract_uncertainties=[
                        "Behavioral contract extraction failed; generated tests should be reviewed manually.",
                    ]
                ),
                tool_run=ToolRun(
                    name="extract behavioral contract",
                    kind="contract_extraction",
                    status=RunStatus.ERROR,
                    summary=f"Behavioral contract extraction failed: {exc}",
                ),
            )

        total_items = self._contract_item_count(contract)
        summary = (
            f"Extracted {total_items} behavioral contract item(s) "
            f"with confidence {contract.confidence:.2f}"
        )
        return ContractExtractionResult(
            contract=contract,
            tool_run=ToolRun(
                name="extract behavioral contract",
                kind="contract_extraction",
                status=RunStatus.PASSED,
                summary=summary,
                findings_count=total_items,
            ),
        )

    @staticmethod
    def _prompt(
        repo_dir: Path,
        *,
        pr_title: str | None,
        pr_body: str | None,
        changed_files: list[ChangedFile],
        changed_functions: list[ChangedFunction],
    ) -> str:
        patch_text = "\n\n".join(
            f"File: {file.filename}\nPatch:\n{ContractExtractionService._limit(file.patch or 'No patch text available.', 5000)}"
            for file in changed_files[:8]
        )
        functions_text = "\n\n".join(
            (
                f"Function: {function.file_path}::{function.qualified_name}\n"
                f"Lines: {function.start_line}-{function.end_line}\n"
                f"Changed lines: {function.changed_lines}\n"
                f"Source:\n{ContractExtractionService._limit(function.source_code, 3000)}"
            )
            for function in changed_functions[:10]
            if function.symbol_type != "file"
        )
        if not functions_text:
            functions_text = ContractExtractionService._file_context(repo_dir, changed_files[:3])

        return f"""Extract a behavioral contract from this Python pull request.

Return only JSON with exactly these keys:
{{
  "intended_new_behaviors": ["..."],
  "existing_behaviors_to_preserve": ["..."],
  "edge_cases_to_test": ["..."],
  "invalid_inputs_to_test": ["..."],
  "contract_uncertainties": ["..."],
  "confidence": 0.0
}}

Rules:
- Do not invent test results.
- Keep each item short, concrete, and testable.
- Prefer observable behavior over implementation trivia.
- Use uncertainties when the diff does not prove intent.
- Set confidence from 0.0 to 1.0.

PR title:
{pr_title or "No title available."}

PR body:
{pr_body or "No PR body available."}

Changed patches:
{patch_text}

Changed function context:
{functions_text}
"""

    @staticmethod
    def _file_context(repo_dir: Path, files: list[ChangedFile]) -> str:
        chunks: list[str] = []
        for file in files:
            path = repo_dir / file.filename
            if not path.exists():
                continue
            chunks.append(
                f"File: {file.filename}\n"
                f"Source excerpt:\n{ContractExtractionService._limit(path.read_text(encoding='utf-8', errors='replace'), 3000)}"
            )
        return "\n\n".join(chunks) or "No source context available."

    @staticmethod
    def _parse_contract(raw_text: str) -> BehavioralContract:
        text = ContractExtractionService._strip_markdown_fences(raw_text).strip()
        if not text:
            raise ValueError("LLM returned empty contract JSON")
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("Contract response must be a JSON object")
        normalized = {
            "intended_new_behaviors": ContractExtractionService._string_list(
                data.get("intended_new_behaviors")
            ),
            "existing_behaviors_to_preserve": ContractExtractionService._string_list(
                data.get("existing_behaviors_to_preserve")
            ),
            "edge_cases_to_test": ContractExtractionService._string_list(
                data.get("edge_cases_to_test")
            ),
            "invalid_inputs_to_test": ContractExtractionService._string_list(
                data.get("invalid_inputs_to_test")
            ),
            "contract_uncertainties": ContractExtractionService._string_list(
                data.get("contract_uncertainties")
            ),
            "confidence": ContractExtractionService._confidence(data.get("confidence")),
        }
        return BehavioralContract.model_validate(normalized)

    @staticmethod
    def _strip_markdown_fences(raw_text: str) -> str:
        text = raw_text.strip()
        match = re.fullmatch(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return text

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        items = [str(item).strip() for item in value if str(item).strip()]
        return items[:8]

    @staticmethod
    def _confidence(value: Any) -> float:
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, confidence))

    @staticmethod
    def _contract_item_count(contract: BehavioralContract) -> int:
        return (
            len(contract.intended_new_behaviors)
            + len(contract.existing_behaviors_to_preserve)
            + len(contract.edge_cases_to_test)
            + len(contract.invalid_inputs_to_test)
            + len(contract.contract_uncertainties)
        )

    @staticmethod
    def _limit(value: str, max_chars: int) -> str:
        if len(value) <= max_chars:
            return value
        return value[:max_chars].rstrip() + "\n... [truncated]"

    @staticmethod
    def _skipped(reason: str) -> ContractExtractionResult:
        runner = CommandRunner()
        return ContractExtractionResult(
            contract=BehavioralContract(
                contract_uncertainties=[
                    reason,
                ]
            ),
            tool_run=ToolRun(
                name="extract behavioral contract",
                kind="contract_extraction",
                status=RunStatus.SKIPPED,
                summary=reason,
                command=runner.skipped(["openai", "responses", "create"], reason),
            ),
        )
