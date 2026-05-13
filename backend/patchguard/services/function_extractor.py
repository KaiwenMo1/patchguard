"""AST-based changed-symbol and changed-function extraction for Python files."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from patchguard.models import ChangedFile, ChangedFunction, FunctionSymbol
from patchguard.utils.file_utils import safe_repo_path

HUNK_HEADER_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,\d+)? @@")
ChangedSymbolType = Literal["function", "async_function", "class", "method", "async_method", "file"]


@dataclass(frozen=True)
class _Definition:
    file_path: str
    qualified_name: str
    symbol_type: ChangedSymbolType
    start_line: int
    end_line: int
    source_code: str


class FunctionExtractor:
    def extract(self, repo_dir: str | Path, changed_files: list[ChangedFile]) -> list[FunctionSymbol]:
        symbols: list[FunctionSymbol] = []
        for changed_file in changed_files:
            if not changed_file.is_python or changed_file.status == "removed":
                continue
            file_path = safe_repo_path(repo_dir, changed_file.filename)
            if not file_path.exists():
                continue
            try:
                tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=changed_file.filename)
            except (SyntaxError, UnicodeDecodeError):
                continue
            symbols.extend(self._extract_from_tree(changed_file.filename, tree))
        return symbols

    def extract_changed_functions(
        self,
        repo_dir: str | Path,
        changed_files: list[ChangedFile],
    ) -> list[ChangedFunction]:
        changed_functions: list[ChangedFunction] = []
        for changed_file in changed_files:
            if not changed_file.is_python or changed_file.status == "removed":
                continue

            file_path = safe_repo_path(repo_dir, changed_file.filename)
            if not file_path.exists():
                continue

            changed_lines = self.parse_changed_lines(changed_file.patch)
            source = ""
            try:
                source = file_path.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=changed_file.filename)
            except (SyntaxError, UnicodeDecodeError) as exc:
                changed_functions.append(
                    self._file_context(changed_file.filename, source, changed_lines, exc)
                )
                continue

            definitions = self._definitions_from_tree(changed_file.filename, tree, source)
            if not definitions or not changed_lines:
                changed_functions.append(self._file_context(changed_file.filename, source, changed_lines))
                continue

            matches = self._changed_definition_matches(definitions, changed_lines)
            if matches:
                changed_functions.extend(matches)
            else:
                changed_functions.append(self._file_context(changed_file.filename, source, changed_lines))
        return changed_functions

    @staticmethod
    def parse_changed_lines(patch: str | None) -> list[int]:
        if not patch:
            return []

        changed_lines: list[int] = []
        current_new_line: int | None = None
        for line in patch.splitlines():
            header_match = HUNK_HEADER_RE.match(line)
            if header_match:
                current_new_line = int(header_match.group("start"))
                continue
            if current_new_line is None or line.startswith("\\"):
                continue
            if line.startswith("+") and not line.startswith("+++"):
                changed_lines.append(current_new_line)
                current_new_line += 1
            elif line.startswith("-") and not line.startswith("---"):
                continue
            else:
                current_new_line += 1
        return sorted(set(changed_lines))

    def _extract_from_tree(self, file_path: str, tree: ast.AST) -> list[FunctionSymbol]:
        symbols: list[FunctionSymbol] = []
        for node in getattr(tree, "body", []):
            if isinstance(node, ast.ClassDef):
                symbols.append(
                    FunctionSymbol(
                        file_path=file_path,
                        symbol_type="class",
                        name=node.name,
                        line=node.lineno,
                    )
                )
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        kind = "async_method" if isinstance(child, ast.AsyncFunctionDef) else "method"
                        symbols.append(
                            FunctionSymbol(
                                file_path=file_path,
                                symbol_type=kind,
                                name=f"{node.name}.{child.name}",
                                line=child.lineno,
                                signature=self._signature(child),
                            )
                        )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = "async_function" if isinstance(node, ast.AsyncFunctionDef) else "function"
                symbols.append(
                    FunctionSymbol(
                        file_path=file_path,
                        symbol_type=kind,
                        name=node.name,
                        line=node.lineno,
                        signature=self._signature(node),
                    )
                )
        return symbols

    def _definitions_from_tree(self, file_path: str, tree: ast.AST, source: str) -> list[_Definition]:
        lines = source.splitlines()
        definitions: list[_Definition] = []
        for node in getattr(tree, "body", []):
            if isinstance(node, ast.ClassDef):
                definitions.append(
                    self._definition(
                        file_path=file_path,
                        qualified_name=node.name,
                        symbol_type="class",
                        node=node,
                        lines=lines,
                    )
                )
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        definitions.append(
                            self._definition(
                                file_path=file_path,
                                qualified_name=f"{node.name}.{child.name}",
                                symbol_type="async_method" if isinstance(child, ast.AsyncFunctionDef) else "method",
                                node=child,
                                lines=lines,
                            )
                        )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                definitions.append(
                    self._definition(
                        file_path=file_path,
                        qualified_name=node.name,
                        symbol_type="async_function" if isinstance(node, ast.AsyncFunctionDef) else "function",
                        node=node,
                        lines=lines,
                    )
                )
        return definitions

    def _changed_definition_matches(
        self,
        definitions: list[_Definition],
        changed_lines: list[int],
    ) -> list[ChangedFunction]:
        changed_by_definition: dict[_Definition, list[int]] = {}
        for line in changed_lines:
            matches = [definition for definition in definitions if definition.start_line <= line <= definition.end_line]
            if not matches:
                continue
            most_specific = max(
                matches,
                key=lambda definition: (definition.start_line, -(definition.end_line - definition.start_line)),
            )
            changed_by_definition.setdefault(most_specific, []).append(line)

        return [
            self._changed_function(definition, sorted(set(lines)))
            for definition, lines in sorted(
                changed_by_definition.items(),
                key=lambda item: (item[0].file_path, item[0].start_line, item[0].qualified_name),
            )
        ]

    @staticmethod
    def _definition(
        *,
        file_path: str,
        qualified_name: str,
        symbol_type: ChangedSymbolType,
        node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
        lines: list[str],
    ) -> _Definition:
        start_line = node.lineno
        end_line = getattr(node, "end_lineno", node.lineno)
        return _Definition(
            file_path=file_path,
            qualified_name=qualified_name,
            symbol_type=symbol_type,
            start_line=start_line,
            end_line=end_line,
            source_code="\n".join(lines[start_line - 1 : end_line]),
        )

    @staticmethod
    def _changed_function(definition: _Definition, changed_lines: list[int]) -> ChangedFunction:
        return ChangedFunction(
            file_path=definition.file_path,
            qualified_name=definition.qualified_name,
            symbol_type=definition.symbol_type,
            start_line=definition.start_line,
            end_line=definition.end_line,
            source_code=definition.source_code,
            changed_lines=changed_lines,
        )

    @staticmethod
    def _file_context(
        file_path: str,
        source: str,
        changed_lines: list[int],
        error: Exception | None = None,
    ) -> ChangedFunction:
        line_count = max(1, len(source.splitlines()))
        return ChangedFunction(
            file_path=file_path,
            qualified_name="<file>",
            symbol_type="file",
            start_line=1,
            end_line=line_count,
            source_code=source,
            changed_lines=changed_lines,
            fallback=True,
            parse_error=str(error) if error else None,
        )

    @staticmethod
    def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        args = node.args
        parts: list[str] = []
        parts.extend(arg.arg for arg in args.posonlyargs)
        if args.posonlyargs:
            parts.append("/")
        parts.extend(arg.arg for arg in args.args)
        if args.vararg:
            parts.append(f"*{args.vararg.arg}")
        elif args.kwonlyargs:
            parts.append("*")
        parts.extend(arg.arg for arg in args.kwonlyargs)
        if args.kwarg:
            parts.append(f"**{args.kwarg.arg}")
        return f"{node.name}({', '.join(parts)})"
