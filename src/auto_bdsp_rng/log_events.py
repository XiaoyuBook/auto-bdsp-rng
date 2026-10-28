"""Log presentation metadata carried through the existing string-based sinks.

The string value is always the original diagnostic message. This preserves
script callbacks, history consumers and disk logging while the UI can use a
short, explicitly authored summary. Unclassified messages are diagnostic only;
warnings and failures without user copy receive a fixed, safe notice.
"""

from __future__ import annotations

import re

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
        context: str = "",
        details: str = "",
        event_type: str = "message",
        phase: str = "",
        diagnostic: bool = False,
        level: str | None = None,
        coalesce_key: str | None = None,
    ) -> LogMessage:
        value = super().__new__(cls, str(message))
        value.summary = " ".join(summary.split())
        value.context = " ".join(context.split())
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


def log_context(message: object, level: str = "INFO") -> str:
    if not log_summary(message, level):
        return ""
    if getattr(message, "summary", "") and not getattr(message, "diagnostic", False):
        context = getattr(message, "context", "")
        if context:
            return context
        if log_level(message, level) not in PROBLEM_LEVELS:
            return ""
    operation = getattr(message, "phase", "") or getattr(message, "source", "")
    return problem_context(message, operation=operation) if log_level(message, level) in PROBLEM_LEVELS else ""


def log_user_text(message: object, level: str = "INFO") -> str:
    return "\n".join(part for part in (log_summary(message, level), log_context(message, level)) if part)


def exception_details(error: BaseException | None) -> str:
    """Keep causal messages for explicit diagnostics, including wrapped errors."""

    chain: list[str] = []
    seen: set[int] = set()
    current = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        text = f"{type(current).__name__}: {current}"
        diagnostic_details = getattr(current, "diagnostic_details", "")
        if isinstance(diagnostic_details, str) and diagnostic_details:
            text += "\n" + diagnostic_details
        chain.append(text)
        current = current.__cause__ or (None if current.__suppress_context__ else current.__context__)
    return "异常原因链：\n" + "\n上层异常：\n".join(reversed(chain)) if chain else ""


def problem_context(message: object, *, operation: str = "") -> str:
    """Describe confirmed conditions without guessing a failure's location.

    An unsuccessful measurement alone is not evidence of a capture, template
    or timeout problem. Ordinary backend text is diagnostic, not a diagnosis.
    """

    text = (exception_details(message) if isinstance(message, BaseException)
            else str(message) + "\n" + getattr(message, "details", "")).casefold()
    kinds: set[str] = set()
    if isinstance(message, BaseException):
        current: BaseException | None = message
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            kinds.update(base.__name__ for base in type(current).__mro__)
            current = current.__cause__ or (None if current.__suppress_context__ else current.__context__)
    else:
        details = getattr(message, "details", "")
        if details.startswith("异常原因链："):
            kinds.update(re.findall(r"^(\w+):", details, flags=re.MULTILINE))
    if "相同 seed" in text and "无候选" in text:
        return "连续测得相同 Seed 且未找到目标。请确认测种流程已正确重置。"
    if "连续 5 次 seed 捕获失败" in text:
        return "已连续 5 次测种失败，触发保护停止。"
    if kinds & {"SerialException", "PortNotOpenError"} or "could not open port" in text or "单片机连接失败" in text:
        return "请检查单片机连接和串口选择，关闭占用串口的其他程序，然后重新连接伊机控。"
    if "FileNotFoundError" in kinds:
        return "未找到所需文件，请检查对应设置。"
    if "PermissionError" in kinds:
        return "访问被拒绝，请检查对应文件或设备的权限。"
    if "TimeoutError" in kinds:
        return "等待操作结果超时。"
    return ""


def task_failure(message: object, *, task: str, phase: str = "") -> LogMessage:
    operation = {
        "捕获Seed": "测种", "TID/SID测种": "TID 测种",
        "搜索目标": "目标搜索", "搜索目标Display TID": "目标 TID 搜索",
        "运行测种脚本": "测种脚本执行", "运行过帧脚本": "过帧脚本执行",
        "运行撞闪脚本": "撞闪脚本执行", "运行取名脚本": "取名脚本执行",
        "运行反查ID脚本": "反查脚本执行", "运行逃跑脚本": "逃跑脚本执行",
        "校正位置": "位置校正", "最终校准": "最终位置校正",
        "运行过场脚本": "过场校正", "反查个体": "个体反查",
        "等待触发": "撞闪等待", "等待取名帧": "取名等待",
        "等待反查帧": "反查等待", "OCR识别TID": "TID 识别", "校准delay": "延迟校准",
    }.get(phase, task)
    return LogMessage(
        message, summary=f"{operation}失败，流程已停止",
        context=problem_context(message, operation=operation),
        details=exception_details(message) if isinstance(message, BaseException) else getattr(message, "details", ""),
        event_type="task.failed", level="ERROR",
    )


def script_output_message(message: str, *, error: bool = False) -> LogMessage:
    lines = message.count("\n") + (0 if message.endswith("\n") else 1)
    return LogMessage(
        message,
        summary="脚本返回错误输出"
        if error else f"脚本输出已更新（{max(1, lines)} 行）",
        context=problem_context(message, operation="脚本执行") if error else "",
        diagnostic=not error,
        event_type="script.stderr" if error else "script.stdout",
    )
