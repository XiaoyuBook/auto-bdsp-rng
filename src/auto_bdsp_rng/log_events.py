"""Log presentation metadata carried through the existing string-based sinks.

The string value is always the original diagnostic message. This preserves
script callbacks, history consumers and disk logging while the UI can use a
short, explicitly authored summary. Unknown/plain messages remain visible.
"""

from __future__ import annotations


PROBLEM_LEVELS = frozenset({"WARNING", "ERROR", "CRITICAL"})
LEVEL_LABELS = {
    "DEBUG": "调试",
    "INFO": "信息",
    "WARNING": "警告",
    "ERROR": "错误",
    "CRITICAL": "严重",
}


class LogMessage(str):
    def __new__(
        cls,
        message: object,
        *,
        summary: str = "",
        details: str = "",
        event_type: str = "message",
        phase: str = "",
        diagnostic: bool = False,
        level: str | None = None,
        coalesce_key: str | None = None,
    ) -> LogMessage:
        value = super().__new__(cls, str(message))
        value.summary = " ".join(summary.split())
        value.details = details
        value.event_type = event_type
        value.phase = phase
        value.diagnostic = diagnostic
        value.level = level
        value.coalesce_key = coalesce_key
        return value


def log_summary(message: object) -> str:
    """Use authored summaries, keeping ordinary messages backward compatible."""

    return getattr(message, "summary", "") or str(message)


def log_level(message: object, level: str = "INFO") -> str:
    requested = str(level).strip().upper() or "INFO"
    authored = str(getattr(message, "level", None) or requested).strip().upper()
    if requested in PROBLEM_LEVELS:
        severity = {"WARNING": 1, "ERROR": 2, "CRITICAL": 3}
        return max((requested, authored), key=lambda item: severity.get(item, 0))
    return authored


def is_user_message(message: object, level: str = "INFO") -> bool:
    effective_level = log_level(message, level)
    return effective_level in PROBLEM_LEVELS or (
        effective_level != "DEBUG" and not getattr(message, "diagnostic", False)
    )


def script_output_message(message: str, *, error: bool = False) -> LogMessage:
    lines = message.count("\n") + (0 if message.endswith("\n") else 1)
    return LogMessage(
        message,
        summary=f"脚本报告错误信息（{max(1, lines)} 行），请展开详情查看"
        if error else f"脚本输出已更新（{max(1, lines)} 行）",
        event_type="script.stderr" if error else "script.stdout",
    )
