"""Log presentation metadata carried through the existing string-based sinks.

The string value is always the original diagnostic message. This preserves
script callbacks, history consumers and disk logging while the UI can use a
short, explicitly authored summary. Unclassified messages are diagnostic only;
warnings and failures without user copy receive a fixed, safe notice.
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


def log_summary(message: object, level: str = "INFO") -> str:
    """Return only user-authored copy, never fall back to diagnostic text.

    Severity must not bypass this boundary: an exception is still diagnostic
    even when reported as ERROR. Diagnostic summaries also stay out of user UI.
    """

    effective_level = log_level(message, level)
    summary = getattr(message, "summary", "")
    if summary and effective_level != "DEBUG" and not getattr(message, "diagnostic", False):
        return summary
    if effective_level == "WARNING":
        return "操作遇到问题，请查看完整日志中的诊断详情"
    if effective_level in {"ERROR", "CRITICAL"}:
        return "操作未能完成，请查看完整日志中的诊断详情"
    return ""


def log_level(message: object, level: str = "INFO") -> str:
    requested = str(level).strip().upper() or "INFO"
    authored = str(getattr(message, "level", None) or requested).strip().upper()
    if requested in PROBLEM_LEVELS:
        severity = {"WARNING": 1, "ERROR": 2, "CRITICAL": 3}
        return max((requested, authored), key=lambda item: severity.get(item, 0))
    return authored


def is_user_message(message: object, level: str = "INFO") -> bool:
    return bool(log_summary(message, level))


def script_output_message(message: str, *, error: bool = False) -> LogMessage:
    lines = message.count("\n") + (0 if message.endswith("\n") else 1)
    return LogMessage(
        message,
        summary=f"脚本报告错误信息（{max(1, lines)} 行），请展开详情查看"
        if error else f"脚本输出已更新（{max(1, lines)} 行）",
        event_type="script.stderr" if error else "script.stdout",
    )
