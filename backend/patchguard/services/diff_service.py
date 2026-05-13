"""Changed-file analysis helpers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath

from patchguard.models import ChangedFile


class FileClassification(StrEnum):
    SOURCE = "source"
    TEST = "test"
    CONFIG = "config"
    DOCS = "docs"
    SECURITY_SENSITIVE = "security_sensitive"
    DEPENDENCY = "dependency"
    OTHER = "other"


RISKY_KEYWORDS = (
    "auth",
    "login",
    "security",
    "token",
    "password",
    "secret",
    "crypto",
    "payment",
    "database",
    "migration",
    "parser",
    "eval",
)

DEPENDENCY_FILES = (
    "requirements.txt",
    "requirements-dev.txt",
    "pyproject.toml",
    "poetry.lock",
    "pipfile",
    "pipfile.lock",
    "setup.py",
    "setup.cfg",
    "tox.ini",
    "uv.lock",
)

CONFIG_SUFFIXES = (
    ".toml",
    ".yaml",
    ".yml",
    ".ini",
    ".cfg",
    ".json",
    ".env",
)

DOC_SUFFIXES = (
    ".md",
    ".rst",
    ".txt",
)


@dataclass(frozen=True)
class DiffSummary:
    source_files: list[ChangedFile]
    test_files: list[ChangedFile]
    config_files: list[ChangedFile]
    dependency_files: list[ChangedFile]
    security_sensitive_files: list[ChangedFile]
    total_changes: int

    @property
    def python_files(self) -> list[ChangedFile]:
        return self.source_files

    @property
    def risky_files(self) -> list[ChangedFile]:
        return self.security_sensitive_files

    @property
    def source_changed_without_tests(self) -> bool:
        return bool(self.source_files and not self.test_files)


class DiffService:
    def classify_file(self, filename: str) -> FileClassification:
        normalized = filename.replace("\\", "/").lower()
        path = PurePosixPath(normalized)
        name = path.name
        parts = set(path.parts)

        if self._is_test_path(normalized, name, parts):
            return FileClassification.TEST
        if self._is_dependency_file(name):
            return FileClassification.DEPENDENCY
        if self._is_docs_path(name, parts):
            return FileClassification.DOCS
        if self._has_risky_keyword(normalized):
            return FileClassification.SECURITY_SENSITIVE
        if self._is_config_path(normalized, name):
            return FileClassification.CONFIG
        if name.endswith(".py"):
            return FileClassification.SOURCE
        return FileClassification.OTHER

    def annotate_changed_files(self, changed_files: list[ChangedFile]) -> list[ChangedFile]:
        for changed_file in changed_files:
            changed_file.classification = self.classify_file(changed_file.filename).value
        return changed_files

    def summarize(self, changed_files: list[ChangedFile]) -> DiffSummary:
        self.annotate_changed_files(changed_files)
        source_files = [
            file
            for file in changed_files
            if file.classification
            in {FileClassification.SOURCE.value, FileClassification.SECURITY_SENSITIVE.value}
            and file.is_python
            and file.status != "removed"
        ]
        test_files = [
            file
            for file in changed_files
            if file.classification == FileClassification.TEST.value and file.status != "removed"
        ]
        config_files = [
            file
            for file in changed_files
            if file.classification == FileClassification.CONFIG.value and file.status != "removed"
        ]
        dependency_files = [
            file
            for file in changed_files
            if file.classification == FileClassification.DEPENDENCY.value and file.status != "removed"
        ]
        security_sensitive_files = [
            file
            for file in changed_files
            if file.classification == FileClassification.SECURITY_SENSITIVE.value
            and file.status != "removed"
        ]
        total_changes = sum(file.changes for file in changed_files)
        return DiffSummary(
            source_files=source_files,
            test_files=test_files,
            config_files=config_files,
            dependency_files=dependency_files,
            security_sensitive_files=security_sensitive_files,
            total_changes=total_changes,
        )

    @staticmethod
    def _has_risky_keyword(normalized: str) -> bool:
        return any(keyword in normalized for keyword in RISKY_KEYWORDS)

    @staticmethod
    def _is_test_path(normalized: str, name: str, parts: set[str]) -> bool:
        return (
            "tests" in parts
            or "test" in parts
            or name.startswith("test_")
            or name.endswith("_test.py")
            or normalized.endswith("/conftest.py")
        )

    @staticmethod
    def _is_dependency_file(name: str) -> bool:
        return name in DEPENDENCY_FILES or name.startswith("requirements-")

    @staticmethod
    def _is_docs_path(name: str, parts: set[str]) -> bool:
        return "docs" in parts or "doc" in parts or name.endswith(DOC_SUFFIXES)

    @staticmethod
    def _is_config_path(normalized: str, name: str) -> bool:
        return (
            name in {"dockerfile", "makefile", ".pre-commit-config.yaml"}
            or name.startswith(".github")
            or normalized.startswith(".github/")
            or name.endswith(CONFIG_SUFFIXES)
        )
