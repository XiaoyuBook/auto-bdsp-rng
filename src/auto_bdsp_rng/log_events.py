"""Keep original diagnostics and explicitly selected user explanations together.

Unknown exception text never becomes UI copy. Known failures carry a readable
cause and next step, while raw exception chains remain available on request.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import re

from auto_bdsp_rng.log_catalog import ERROR_TITLES, SOURCES, application_copy

PROBLEM_LEVELS = frozenset({"WARNING", "ERROR", "CRITICAL"})
LEVEL_LABELS = {"DEBUG": "调试", "INFO": "信息", "WARNING": "警告", "ERROR": "错误", "CRITICAL": "严重"}


class LogMessage(str):
    def __new__(
        cls,
        message: object,
        *,
        summary: str = "",
        context: str = "",
        details: str = "",
        user_details: str = "",
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
        value.user_details = user_details
        value.event_type = event_type
        value.phase = phase
        value.diagnostic = diagnostic
        value.level = level
        value.coalesce_key = coalesce_key
        return value


def log_summary(message: object, level: str = "INFO") -> str:
    effective_level = log_level(message, level)
    summary = getattr(message, "summary", "")
    if summary and effective_level != "DEBUG" and not getattr(message, "diagnostic", False):
        return summary
    if effective_level == "WARNING":
        return "操作未完成"
    if effective_level in {"ERROR", "CRITICAL"}:
        return "操作失败"
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
    return problem_context(message) if log_level(message, level) in PROBLEM_LEVELS else ""


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


@dataclass(frozen=True)
class _Problem:
    causes: tuple[str, ...] = ()
    action: str = ""
    script: str = ""
    line: int | None = None
    column: int | None = None
    compile_error: bool = False

    def user_details(self) -> str:
        lines = ["具体原因：" + (self.causes[0] if self.causes else "当前记录未提供可确认的具体原因。")]
        if len(self.causes) > 1:
            lines.extend(("", "相关原因：", *[f"{i}. {cause}" for i, cause in enumerate(self.causes[1:], 1)]))
        if self.script:
            lines.append(f"脚本：{self.script}")
        if self.line:
            position = f"第 {self.line} 行" + (f"，第 {self.column} 列" if self.column else "")
            lines.append("错误位置：" + position)
        if self.action:
            lines.append("处理建议：" + self.action)
        if not self.causes:
            lines.extend(("", "如需进一步排查，可复制本条记录的原始诊断；其中保留了程序返回的原文。"))
        return "\n".join(lines)


_SCRIPT_REASONS = frozenset((
    "赋值语句缺少表达式", "表达式不能为空", "索引不能为空", "逗号后缺少表达式",
    "IF 语句缺少 ENDIF", "FOR 语句缺少 NEXT", "WHILE 语句缺少 END", "FUNC 声明缺少 ENDFUNC",
    "函数必须在顶层定义", "FUNC 声明格式无效", "函数参数格式无效", "CALL 后缺少函数名",
    "BREAK 只能在循环中使用", "BREAK 层级必须是整数", "BREAK 层级超出当前循环深度（最大 3）",
    "CONTINUE 只能在循环中使用", "CONTINUE 不接受参数", "RETURN 只能在函数中使用",
    "IMPORT 必须在顶层", "IMPORT 后必须是库文件名字符串", "IMPORT 只能出现在脚本开头",
    "EXTERN 声明必须在顶层", "EXTERN FUNC 声明格式无效", "无法识别的语句", "无效函数调用",
    "函数参数不能为空", "摇杆语句参数过多", "摇杆角度不能小于 0", "RESET 不接受持续时间",
    "ELSE 后不能有其他内容", "等待时间不能小于 0", "按键持续时间不能小于 0",
    "摇杆持续时间不能小于 0", "FOR 步长不能为 0", "除数不能为 0",
    "算术运算只支持数值", "数组元素类型不一致", "比较两侧类型必须相同且支持排序",
    "APPEND 的第一个参数必须是数组", "APPEND 的元素类型与数组不一致",
    "常量只能使用编译期表达式，不能引用变量、getter 或函数",
    "字符串没有正确结束", "无效的字符串转义", "十六进制数字无效", "整数超出 32 位范围",
    "数组元素类型必须一致", "VOID 表达式不能赋值", "常量不能使用复合赋值", "存在未处理的脚本内容",
))
_SCRIPT_LOCATION = re.compile(r"^(?P<source>.+):(?P<line>\d+):(?P<column>\d+):\s*(?P<reason>[^\r\n]*)$")


def _script_reason(text: str) -> str:
    if text in _SCRIPT_REASONS:
        return "计算时除数为 0" if text == "除数不能为 0" else text
    match = re.fullmatch(r"(找不到函数|找不到变量|函数参数重复定义:)\s*([$\w]+)", text)
    if match and "project_xs" not in match[2].casefold():
        return f"{match[1]} {match[2]}"
    for prefix, reason in (
        ("需要 ", "表达式缺少预期的符号"), ("未知类型 ", "变量类型无效"),
        ("多余的结束语句 ", "存在多余的结束语句"), ("表达式需要 ", "表达式类型不匹配"),
        ("比较两侧类型不一致", "比较两侧的值类型不一致"),
        ("无法绑定表达式 ", "表达式无法解析"),
        ("APPEND 需要 ", "加入数组的元素类型不匹配"),
        ("无法识别的字符", "语句包含无法识别的字符"), ("表达式末尾存在多余内容", "表达式末尾存在多余内容"),
        ("无效表达式", "表达式格式无效"), ("无效摇杆方向", "摇杆方向无效"),
        ("找不到导入库", "找不到脚本引用的库文件"), ("找不到搜图标签", "找不到脚本引用的搜图标签"),
        ("IMPORT 路径不能离开", "导入库的位置超出允许的脚本库目录"),
        ("不能把 ", "赋值内容与变量类型不一致"), ("无法读取脚本", "脚本文件无法读取"),
    ):
        if text.startswith(prefix):
            return reason
    if re.fullmatch(r"(LS|RS) 后缺少方向或 RESET", text):
        return text
    if re.fullmatch(r"[A-Z]+ 后不能有其他内容", text):
        return text
    for pattern, reason in (
        (r"函数 [$\w]+ 并非所有路径都返回值", "函数的部分执行路径缺少返回值"),
        (r"(?:函数|常量) [$\w]+ 已经定义", "函数或常量重复定义"),
        (r"VOID 函数 [$\w]+ 不能返回值", "无返回值的函数不能返回内容"),
        (r"函数 [$\w]+ (?:参数需要|返回值需要|必须返回) .+", "函数的参数或返回值类型不匹配"),
        (r"(?:INT|FLOAT|BOOL|STRING|VOID|ARRAY|INT\[\]|FLOAT\[\]) 不支持 .+", "表达式类型不支持此操作"),
        (r"LEN 不支持 .+", "LEN 的参数类型不受支持"),
        (r"运算符 .+ 不支持 .+ 和 .+", "运算符两侧的值类型不匹配"),
    ):
        if re.fullmatch(pattern, text):
            return reason
    if re.fullmatch(r"变量 [$\w]+ (是只读的|尚未赋值)", text):
        return text
    if re.fullmatch(r"函数 [$\w]+ 需要 \d+ 个参数，实际为 \d+", text):
        return text
    return ""


def _reason(kind: str, text: str) -> tuple[str, str]:
    """Translate confirmed error kinds; do not diagnose arbitrary strings."""
    if kind in {"ScriptCompileError", "ScriptRuntimeError"}:
        match = _SCRIPT_LOCATION.fullmatch(text)
        return _script_reason(match["reason"] if match else text), "请在脚本编辑区定位错误行，修正后重新运行。"
    if kind == "ZeroDivisionError":
        return "计算时除数为 0", "请检查参与计算的参数。"
    if kind in {"SerialException", "PortNotOpenError", "DeviceNotConnectedError", "DeviceConnectionError"}:
        if "access is denied" in text.casefold() or "拒绝访问" in text:
            return "串口访问被拒绝，设备可能正被其他程序占用", "请关闭占用串口的程序，再重新连接伊机控。"
        return "伊机控串口不可用或连接已断开", "请检查设备和串口选择后重新连接。"
    if kind in {"PySerialUnavailableError", "BrokerNotFoundError", "BrokerManifestError"}:
        return "设备连接所需组件缺失或无效", "请修复软件安装后重试连接。"
    if kind == "CaptureOpenError":
        return "无法打开所选视频设备", "请检查设备连接、设备占用和视频源设置。"
    if kind == "FrameFormatError":
        return "视频画面格式不受支持", "请调整视频源的格式或分辨率后重新连接。"
    if kind == "BrokerAlreadyRunningError":
        return "视频连接服务已经在运行", "请先断开现有视频源后重试。"
    if kind == "DialogFrameCaptureError":
        return "对话识别时无法读取游戏画面", "请确认视频预览持续更新后重试。"
    if kind == "DialogOcrError":
        return "对话文字识别失败", "请确认游戏对话画面清晰后重试。"
    if kind == "NativeEasyConBusyError":
        return "伊机控正在执行其他任务或切换连接", "请等待当前操作结束后重试。"
    if kind in {"FileNotFoundError", "ModuleNotFoundError"}:
        return ("未找到所需文件" if kind == "FileNotFoundError" else "所需功能组件缺失"), "请检查对应文件或修复软件安装。"
    if kind == "PermissionError":
        return "访问文件或设备时权限不足", "请检查访问权限，或选择其他保存位置。"
    if kind == "UnicodeDecodeError":
        return "文件不是有效的 UTF-8 文本", "请将文件保存为 UTF-8 编码后重试。"
    if kind == "JSONDecodeError":
        return "配置文件格式无效", "请重新选择有效配置，或恢复备份配置。"
    if kind in {"TimeoutError", "DialogTimingTimeoutError", "DialogScriptTimeoutError", "DialogKeywordTimeoutError"}:
        if "伊机控" in text or "串口" in text:
            return "等待伊机控响应超时", "请检查设备连接和串口占用后重试。"
        if kind == "DialogScriptTimeoutError":
            return "对话脚本未在限定时间内完成", "请确认游戏是否停留在预期对话阶段后重试。"
        if kind == "DialogKeywordTimeoutError":
            return "未在限定时间内识别到目标对话", "请确认游戏是否停留在预期对话阶段后重试。"
        if text.startswith(("等待共享视频源新帧超时", "获取游戏画面超时", "读取游戏画面超时")):
            return "读取游戏画面超时", "请确认视频预览持续更新后重试。"
        return "等待操作结果超时", "请确认当前操作是否完成后重试。"
    if kind == "ProjectXsNoFrameError":
        return "采集期间未收到有效游戏画面", "请确认视频预览持续更新后重新测种。"
    if kind == "ProjectXsSeedRecoveryError":
        if "validate" in text:
            return "已采集到眨眼数据，但求解出的 Seed 未通过验证", "请重新测种；若持续失败，请保留本轮原始诊断。"
        if "invalid seed state" in text:
            return "测种返回的 Seed 状态无效", "请重新测种；若持续失败，请保留本轮原始诊断。"
        return "已采集到眨眼数据，但 Seed 求解失败", "请重新测种；若持续失败，请保留本轮原始诊断。"
    if kind == "ProjectXsCaptureConfigError":
        if text.startswith(("眼睛 ROI", "捕捉画面没有有效的宽高", "ROI is smaller than the configured eye template")):
            return "眼睛识别范围与当前画面不匹配", "请重新框选眼睛并保存测种配置。"
        return "眼睛模板或测种配置无效", "请重新保存眼睛模板和测种配置。"
    if kind == "ProjectXsIntegrationError":
        for needle, reason in (
            ("校正未找到匹配状态", "本次校正未找到匹配的推进位置"),
            ("抗干扰校正未找到匹配状态", "本次校正未找到匹配的推进位置"),
            ("校正返回了无效", "校正返回的 Seed 状态无效"),
            ("frame capture returned an empty frame", "采集返回了空画面"),
            ("Invalid Project_Xs JSON config", "测种配置文件格式无效"),
            ("config is missing required field", "测种配置缺少必需项目"),
            ("source directory not found", "测种所需组件缺失"),
            ("Cannot read Project_Xs config", "测种配置文件无法读取"),
            ("共享视频源客户端已经关闭", "视频连接已关闭"),
            ("共享视频源读取失败", "游戏画面读取失败"),
            ("无法连接共享视频源", "视频连接不可用"),
        ):
            if needle in text:
                return reason, "请检查对应设置后重试。"
    if kind in {"BrokerUnavailableError", "BrokerConnectionError"}:
        return "当前视频连接不可用", "请重新连接视频源，确认预览持续更新。"
    if kind == "TesseractRuntimeError":
        return "文字识别组件运行失败", "请检查文字识别设置后重试。"
    if kind in {"AutoScriptError", "ValueError"}:
        for prefix, reason in (
            ("请选择过帧脚本", "尚未选择过帧脚本"), ("请选择撞闪脚本", "尚未选择撞闪脚本"),
            ("请选择测种脚本", "尚未选择测种脚本"), ("过帧数必须", "过帧数设置无效"),
            ("目标 Display TID 必须", "目标 TID 格式无效"),
            ("启用逃跑续搜后必须", "逃跑续搜所需设置尚未完成"),
        ):
            if text.startswith(prefix):
                return reason, "请检查自动流程设置后重新开始。"
        if re.fullmatch(r"[\w .()-]+\.(?:txt|ecs) 缺少必需参数 [$\w]+", text):
            return "所选脚本缺少自动流程必需参数", "请重新选择适配的脚本或补全必需参数。"
    if kind == "OSError" and ("No space left" in text or "磁盘空间不足" in text):
        return "保存位置的可用空间不足", "请释放磁盘空间或选择其他保存位置。"
    return "", ""


def _describe_problem(error: object, *, compile_error: bool = False, script_output: bool = False) -> _Problem:
    records: list[tuple[str, str, object | None]] = []
    if isinstance(error, BaseException):
        current: BaseException | None = error
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            records.append((type(current).__name__, str(current), getattr(current, "location", None)))
            current = current.__cause__ or (None if current.__suppress_context__ else current.__context__)
        records.reverse()
    else:
        details = getattr(error, "details", "")
        if details.startswith("异常原因链："):
            records = [(m[1], m[2], None) for m in re.finditer(r"^(\w+): ?([^\r\n]*)", details, re.MULTILINE)]
        if script_output:
            # Only the actual script result channel may interpret a location.
            # A random warning containing 'TimeoutError' is never a diagnosis.
            for text in str(error).splitlines():
                if _SCRIPT_LOCATION.fullmatch(text):
                    records.append(("ScriptCompileError" if compile_error else "ScriptRuntimeError", text, None))
                    break
    causes: list[str] = []
    action, script, line, column = "", "", None, None
    for kind, text, location in records:
        reason, suggestion = _reason(kind, text)
        if reason and reason not in causes:
            causes.append(reason)
        action = action or suggestion
        compile_error = compile_error or kind == "ScriptCompileError"
        match = _SCRIPT_LOCATION.fullmatch(text) if kind in {"ScriptCompileError", "ScriptRuntimeError"} else None
        if location is not None or match:
            source = str(getattr(location, "source", "") or match["source"])
            name = re.split(r"[\\/]", source)[-1]
            script = name if "project_xs" not in name.casefold() and re.fullmatch(r"[\w .()（）-]{1,120}\.(?:txt|ecs)", name, re.IGNORECASE) else "所选脚本"
            line = int(getattr(location, "line", 0) or match["line"])
            column = int(getattr(location, "column", 0) or match["column"])
    return _Problem(tuple(causes), action, script, line, column, compile_error)


def problem_context(message: object, *, operation: str = "") -> str:
    problem = _describe_problem(message)
    if problem.causes:
        return "；".join((problem.causes[0], problem.action)).rstrip("；")
    text = str(message).casefold()
    if "相同 seed" in text and "无候选" in text:
        return "连续测得相同 Seed 且未找到目标。请确认测种流程已正确重置。"
    if "连续 5 次 seed 捕获失败" in text:
        return "已连续 5 次测种失败，触发保护停止。"
    return ""


def operation_failure(
    error: object,
    *,
    summary: str,
    context: str = "",
    raw_message: object | None = None,
    event_type: str = "operation.failed",
) -> LogMessage:
    problem = _describe_problem(error)
    if context:
        problem = replace(problem, action=context)
    explanation = "；".join(part for part in (problem.causes[0] if problem.causes else "", context or problem.action) if part)
    return LogMessage(
        error if raw_message is None else raw_message, summary=ERROR_TITLES.get(summary, summary), context=explanation,
        details=exception_details(error) if isinstance(error, BaseException) else getattr(error, "details", ""),
        user_details=problem.user_details(), event_type=event_type,
    )


def task_failure(message: object, *, task: str, phase: str = "") -> LogMessage:
    operation = {
        "捕获Seed": "测种", "TID/SID测种": "TID 测种", "搜索目标": "目标搜索", "搜索目标Display TID": "目标 TID 搜索",
        "运行测种脚本": "测种脚本执行", "运行过帧脚本": "过帧脚本执行", "运行撞闪脚本": "撞闪脚本执行",
        "运行取名脚本": "取名脚本执行", "运行反查ID脚本": "反查脚本执行", "运行逃跑脚本": "逃跑脚本执行",
        "校正位置": "位置校正", "最终校准": "最终位置校正", "运行过场脚本": "过场校正", "反查个体": "个体反查",
        "等待触发": "撞闪等待", "等待取名帧": "取名等待", "等待反查帧": "反查等待", "OCR识别TID": "TID 识别", "校准delay": "延迟校准",
    }.get(phase, task)
    problem = _describe_problem(message)
    summary = f"{operation}失败，流程已停止"
    if problem.line:
        operation = operation.removesuffix("执行")
        kind = "语法错误" if problem.compile_error else "运行错误"
        reason = "：" + problem.causes[0] if problem.causes else ""
        summary = f"{operation}第 {problem.line} 行{kind}{reason}，流程已停止"
    guard = "已连续 5 次测种失败，触发保护停止。" if "连续 5 次 seed 捕获失败" in str(message) else ""
    event = operation_failure(message, summary=summary, context=guard + problem.action if guard else "", event_type="task.failed")
    event.user_details = problem.user_details()
    event.level = "ERROR"
    return event


def script_failure(error: object, *, compile_error: bool = False, line: int | None = None) -> LogMessage:
    problem = _describe_problem(error, compile_error=compile_error, script_output=True)
    if line is not None and problem.line is None:
        problem = replace(problem, line=line)
    kind = "语法错误" if problem.compile_error else "运行失败"
    position = f"第 {problem.line} 行" if problem.line else ""
    reason = "：" + problem.causes[0] if problem.causes else ""
    return LogMessage(
        error, summary=f"脚本{position}{kind}{reason}", context=problem.action,
        details=exception_details(error) if isinstance(error, BaseException) else getattr(error, "details", ""),
        user_details=problem.user_details(), event_type="script.failed",
    )


def script_output_message(message: str, *, error: bool = False) -> LogMessage:
    if error:
        problem = _describe_problem(message, script_output=True)
        return LogMessage(message, summary="脚本返回错误信息", context=problem.causes[0] if problem.causes else "",
                          user_details=problem.user_details(), event_type="script.stderr")
    lines = message.count("\n") + (0 if message.endswith("\n") else 1)
    return LogMessage(message, summary=f"脚本输出已更新（{max(1, lines)} 行）", diagnostic=True, event_type="script.stdout")


def present_log_message(source: str, message: object, level: str = "INFO") -> LogMessage:
    """Normalize all UI sinks while keeping every raw string byte-for-byte."""
    metadata = message if isinstance(message, LogMessage) else LogMessage(message)
    effective_level = log_level(metadata, level)
    if effective_level == "DEBUG" or metadata.diagnostic:
        return metadata
    summary, context = metadata.summary, metadata.context
    if not summary:
        summary, selected_context = application_copy(source, str(message))
        context = context or selected_context
    if not summary and effective_level in PROBLEM_LEVELS:
        operation = SOURCES.get(source, "操作")
        summary = operation + ("未完成" if effective_level == "WARNING" else "失败")
    authored_level = metadata.level
    if source == "QQ 通知" and not metadata.summary and summary and str(message).startswith(("QQ 通知未发送", "绑定结果未能保存")):
        authored_level = "WARNING"
        effective_level = "WARNING"
    user_details = metadata.user_details
    if effective_level in PROBLEM_LEVELS:
        problem = _describe_problem(message)
        reason = problem.causes[0] if problem.causes else ""
        if reason and reason not in context and reason not in summary:
            context = "；".join(part for part in (reason, context or problem.action) if part)
        user_details = user_details or problem.user_details()
    return LogMessage(
        message, summary=summary, context=context,
        details=exception_details(message) if isinstance(message, BaseException) else metadata.details, user_details=user_details,
        event_type=metadata.event_type, phase=metadata.phase, diagnostic=metadata.diagnostic,
        level=authored_level, coalesce_key=metadata.coalesce_key,
    )
