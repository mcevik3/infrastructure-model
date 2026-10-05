"""Stable, source-aware diagnostics for loading and validation."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    source: str = ""
    path: str = ""

    def __str__(self) -> str:
        location = f"{self.source}{self.path}"
        return f"{location + ': ' if location else ''}[{self.code}] {self.message}"


class InfrastructureError(Exception):
    """Base class for expected model errors."""


class LoadError(InfrastructureError):
    """The input could not be read as unambiguous, JSON-compatible YAML."""


class ValidationError(InfrastructureError):
    """One or more schema or semantic checks failed."""

    def __init__(self, issues: list[Issue]):
        self.issues = tuple(issues)
        super().__init__("\n".join(str(issue) for issue in issues))


@dataclass(frozen=True)
class ValidationReport:
    """A successful validation; warnings describe checks lacking evidence."""

    document_count: int
    entity_count: int
    warnings: tuple[Issue, ...] = ()
