from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SourceLocation:
    source: str
    line: int
    column: int = 1

    def __str__(self) -> str:
        return f"{self.source}:{self.line}:{self.column}"


class EasyConScriptError(Exception):
    """Base class for native EasyCon script failures."""

    def __init__(self, message: str, location: SourceLocation | None = None) -> None:
        self.message = message
        self.location = location
        prefix = f"{location}: " if location is not None else ""
        super().__init__(f"{prefix}{message}")


class ScriptCompileError(EasyConScriptError):
    pass


class ScriptRuntimeError(EasyConScriptError):
    pass


class ScriptCancelled(EasyConScriptError):
    pass
