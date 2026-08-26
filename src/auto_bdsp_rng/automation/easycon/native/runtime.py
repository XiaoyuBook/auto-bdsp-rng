from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import math
import random
import sys
import threading
import time
from typing import Any, Protocol, runtime_checkable

from auto_bdsp_rng.automation.easycon.native.ast import (
    ArrayLiteral,
    Assignment,
    Binary,
    BreakStatement,
    ButtonAction,
    Call,
    CallStatement,
    ContinueStatement,
    ExternDeclaration,
    Expression,
    ForStatement,
    FunctionDeclaration,
    IfStatement,
    ImportStatement,
    Index,
    Literal,
    Name,
    Program,
    ReturnStatement,
    Slice,
    Statement,
    StickAction,
    Unary,
    Wait,
    WhileStatement,
)
from auto_bdsp_rng.automation.easycon.native.errors import ScriptCancelled, ScriptRuntimeError, SourceLocation


CancelEvent = threading.Event | Callable[[], bool] | None
ExternalGetter = Callable[[], int]
OutputCallback = Callable[[str], None]


@runtime_checkable
class GamepadProtocol(Protocol):
    def click_buttons(self, key: str, duration_ms: int, cancel_event: CancelEvent = None) -> None: ...

    def press_buttons(self, key: str) -> None: ...

    def release_buttons(self, key: str) -> None: ...

    def click_stick(
        self, key: str, x: int, y: int, duration_ms: int, cancel_event: CancelEvent = None
    ) -> None: ...

    def set_stick(self, key: str, x: int, y: int) -> None: ...

    def change_amiibo(self, index: int) -> object: ...


@runtime_checkable
class OutputProtocol(Protocol):
    def print(self, message: str, newline: bool) -> None: ...

    def alert(self, message: str) -> None: ...


@runtime_checkable
class WaiterProtocol(Protocol):
    def wait(self, milliseconds: int, cancel_event: CancelEvent = None) -> None: ...


class HighPrecisionWaiter:
    """Cancellable EasyCon-compatible wait with a short yield phase."""

    _COARSE_RESERVE_SECONDS = 0.020

    def wait(self, milliseconds: int, cancel_event: CancelEvent = None) -> None:
        if milliseconds < 0:
            raise ValueError("等待时间不能小于 0")
        if _is_cancelled(cancel_event):
            raise ScriptCancelled("脚本已取消")
        if milliseconds == 0:
            return

        deadline = time.perf_counter() + milliseconds / 1000.0
        coarse = milliseconds / 1000.0 - self._COARSE_RESERVE_SECONDS
        if coarse > 0:
            _cancellable_sleep(coarse, cancel_event)

        while True:
            if _is_cancelled(cancel_event):
                raise ScriptCancelled("脚本已取消")
            if time.perf_counter() >= deadline:
                return
            time.sleep(0)


def _is_cancelled(cancel_event: CancelEvent) -> bool:
    if cancel_event is None:
        return False
    if callable(cancel_event) and not hasattr(cancel_event, "is_set"):
        return bool(cancel_event())
    is_set = getattr(cancel_event, "is_set", None)
    return bool(is_set()) if callable(is_set) else False


def _cancellable_sleep(seconds: float, cancel_event: CancelEvent) -> None:
    if seconds <= 0:
        return
    wait = getattr(cancel_event, "wait", None)
    if callable(wait):
        if wait(seconds):
            raise ScriptCancelled("脚本已取消")
        return
    deadline = time.perf_counter() + seconds
    while True:
        if _is_cancelled(cancel_event):
            raise ScriptCancelled("脚本已取消")
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 0.020))


def _int32(value: int) -> int:
    value &= 0xFFFF_FFFF
    return value - 0x1_0000_0000 if value >= 0x8000_0000 else value


def _require_int(value: object, location: SourceLocation, purpose: str = "整数") -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ScriptRuntimeError(f"{purpose}需要 INT，实际为 {_type_name(value)}", location)
    return value


def _type_name(value: object) -> str:
    if value is None:
        return "VOID"
    if isinstance(value, bool):
        return "BOOL"
    if isinstance(value, int):
        return "INT"
    if isinstance(value, float):
        return "DOUBLE"
    if isinstance(value, str):
        return "STRING"
    if isinstance(value, tuple):
        inner = _type_name(value[0]) if value else "INT"
        return f"{inner}[]"
    return type(value).__name__


def _truth(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, (bool, int, float, str, tuple)):
        return bool(value)
    return True


def _format_value(value: object) -> str:
    if value is None:
        return "void"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, tuple):
        return f"[{', '.join(_format_value(item) for item in value)}]"
    if isinstance(value, float):
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value)


def _coerce_declared_type(value: object, type_name: str) -> object:
    return _format_value(value) if type_name == "STRING" else value


def _truncating_division(left: int, right: int) -> int:
    if right == 0:
        raise ZeroDivisionError("除数不能为 0")
    quotient = abs(left) // abs(right)
    if (left < 0) != (right < 0):
        quotient = -quotient
    return _int32(quotient)


def _rounded_division(left: int, right: int) -> int:
    if right == 0:
        raise ZeroDivisionError("除数不能为 0")
    quotient, remainder = divmod(abs(left), abs(right))
    if remainder * 2 >= abs(right):
        quotient += 1
    if (left < 0) != (right < 0):
        quotient = -quotient
    return _int32(quotient)


def _same_type(left: object, right: object) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right)
    return type(left) is type(right)


def _binary_operation(operator: str, left: object, right: object, location: SourceLocation) -> object:
    try:
        if operator == "==":
            return _same_type(left, right) and left == right
        if operator == "!=":
            return not (_same_type(left, right) and left == right)
        if operator in {"<", "<=", ">", ">="}:
            if not _same_type(left, right) or not isinstance(left, (int, float, str)) or isinstance(left, bool):
                raise TypeError("比较两侧类型必须相同且支持排序")
            return {"<": left < right, "<=": left <= right, ">": left > right, ">=": left >= right}[operator]
        if operator == "&" and (isinstance(left, str) or isinstance(right, str)):
            return _format_value(left) + _format_value(right)
        if operator == "+" and isinstance(left, str) and isinstance(right, str):
            return left + right
        if operator == "+" and isinstance(left, tuple) and isinstance(right, tuple):
            if left and right and _type_name(left[0]) != _type_name(right[0]):
                raise TypeError("数组元素类型不一致")
            return left + right
        if operator in {"+", "-", "*", "/"} and (
            isinstance(left, float) or isinstance(right, float)
        ):
            if isinstance(left, bool) or isinstance(right, bool) or not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
                raise TypeError("算术运算只支持数值")
            if operator == "+":
                return float(left) + float(right)
            if operator == "-":
                return float(left) - float(right)
            if operator == "*":
                return float(left) * float(right)
            if right == 0:
                raise ZeroDivisionError("除数不能为 0")
            return float(left) / float(right)

        left_int = _require_int(left, location, "运算")
        right_int = _require_int(right, location, "运算")
        if operator == "+":
            return _int32(left_int + right_int)
        if operator == "-":
            return _int32(left_int - right_int)
        if operator == "*":
            return _int32(left_int * right_int)
        if operator == "/":
            return _truncating_division(left_int, right_int)
        if operator == "\\":
            return _rounded_division(left_int, right_int)
        if operator == "%":
            quotient = _truncating_division(left_int, right_int)
            return _int32(left_int - quotient * right_int)
        if operator == "&":
            return _int32(left_int & right_int)
        if operator == "|":
            return _int32(left_int | right_int)
        if operator == "^":
            return _int32(left_int ^ right_int)
        if operator == "<<":
            return _int32(left_int << (right_int & 0x1F))
        if operator == ">>":
            return _int32(left_int >> (right_int & 0x1F))
    except (TypeError, ValueError, ZeroDivisionError, OverflowError) as exc:
        raise ScriptRuntimeError(str(exc), location) from exc
    raise ScriptRuntimeError(f"不支持运算符 {operator}", location)


def _stick_xy(direction: str | int) -> tuple[int, int]:
    if isinstance(direction, str):
        degrees = {
            "RIGHT": 0,
            "UPRIGHT": 45,
            "UP": 90,
            "UPLEFT": 135,
            "LEFT": 180,
            "DOWNLEFT": 225,
            "DOWN": 270,
            "DOWNRIGHT": 315,
            "RESET": -1,
        }
        degree = degrees[direction.upper()]
    else:
        degree = direction
    if degree == -1:
        return (128, 128)

    radians = degree * math.pi / 180.0
    tangent = math.tan(radians)
    sign_cos = (math.cos(radians) > 0) - (math.cos(radians) < 0)
    sign_sin = (math.sin(radians) > 0) - (math.sin(radians) < 0)
    dy = round(tangent * sign_cos, 4)
    if radians == 0:
        dx = 1.0
    elif tangent == 0:
        dx = -1.0 if math.cos(radians) < 0 else 1.0
    else:
        dx = round((1.0 / tangent) * sign_sin, 4)
    dx = min(1.0, max(-1.0, dx))
    dy = min(1.0, max(-1.0, dy))
    x = int(min(255.0, max(0.0, (dx + 1.0) * 128.0)))
    y = int(min(255.0, max(0.0, (-dy + 1.0) * 128.0)))
    return x, y


@dataclass(slots=True)
class _Environment:
    parent: _Environment | None = None
    values: dict[str, object] = field(default_factory=dict)
    readonly: set[str] = field(default_factory=set)
    declared: set[str] = field(default_factory=set)
    default_unassigned: bool = False

    def find(self, name: str) -> _Environment | None:
        if name in self.values or name in self.declared:
            return self
        return self.parent.find(name) if self.parent is not None else None

    def get(self, name: str, location: SourceLocation) -> object:
        owner = self.find(name)
        if owner is None:
            raise ScriptRuntimeError(f"找不到变量 {name}", location)
        if name not in owner.values:
            if owner.default_unassigned:
                return 0
            raise ScriptRuntimeError(f"变量 {name} 尚未赋值", location)
        return owner.values[name]

    def declare(self, names: set[str]) -> None:
        for name in names:
            if name in self.values or name in self.declared:
                continue
            if self.parent is None or self.parent.find(name) is None:
                self.declared.add(name)

    def assign(self, name: str, value: object, location: SourceLocation, *, constant: bool = False) -> None:
        owner = self.find(name)
        if owner is not None:
            if name in owner.readonly:
                raise ScriptRuntimeError(f"变量 {name} 是只读的", location)
            if name in owner.values and not _same_type(owner.values[name], value):
                raise ScriptRuntimeError(
                    f"不能把 {_type_name(value)} 赋给 {_type_name(owner.values[name])} 变量 {name}", location
                )
            owner.values[name] = value
            if constant:
                owner.readonly.add(name)
            return
        self.values[name] = value
        if constant:
            self.readonly.add(name)


def _scope_declarations(statements: tuple[Statement, ...]) -> set[str]:
    declarations: set[str] = set()
    for statement in statements:
        if isinstance(statement, Assignment):
            declarations.add(statement.name)
        elif isinstance(statement, WhileStatement):
            declarations.update(_scope_declarations(statement.body))
    return declarations


class _BreakSignal(Exception):
    def __init__(self, level: int) -> None:
        self.level = level


class _ContinueSignal(Exception):
    pass


class _ReturnSignal(Exception):
    def __init__(self, value: object) -> None:
        self.value = value


class _OutputSink:
    def __init__(self, output: OutputProtocol | OutputCallback | Any | None) -> None:
        self.output = output

    def print(self, message: str, newline: bool) -> None:
        if self.output is None:
            return
        method = getattr(self.output, "print", None)
        if callable(method):
            method(message, newline)
            return
        rendered = message + ("\n" if newline else "")
        if callable(self.output):
            self.output(rendered)
            return
        write = getattr(self.output, "write", None)
        if callable(write):
            write(rendered)
            return
        raise TypeError("output 必须是 callback、文本流或 OutputProtocol")

    def alert(self, message: str) -> None:
        if self.output is None:
            return
        method = getattr(self.output, "alert", None)
        if callable(method):
            method(message)
            return
        if callable(self.output):
            self.output(message)
            return
        self.print(message, True)


class _Evaluator:
    def __init__(
        self,
        program: Program,
        *,
        gamepad: GamepadProtocol | None,
        external_getters: Mapping[str, ExternalGetter],
        extern_functions: Mapping[str, Callable[..., object]],
        output: OutputProtocol | OutputCallback | Any | None,
        cancel_event: CancelEvent,
        waiter: WaiterProtocol | Callable[[int, CancelEvent], None],
        random_source: random.Random,
        beep: Callable[[int, int], None] | None,
    ) -> None:
        self.program = program
        self.gamepad = gamepad
        self.external_getters = external_getters
        self.extern_functions = extern_functions
        self.output = _OutputSink(output)
        self.cancel_event = cancel_event
        self.waiter = waiter
        self.random = random_source
        self.beep = beep
        self.started_ns = time.monotonic_ns()
        self.cancel_line_break = False
        self.last_value: object = None
        library_declarations = {
            name
            for unit in self.program.libraries
            for name in _scope_declarations(unit.statements)
        }
        self.library_globals = _Environment(
            declared=library_declarations,
            default_unassigned=True,
        )
        self.main_globals = _Environment(
            declared=_scope_declarations(self.program.main.statements),
            default_unassigned=True,
        )
        self.functions: dict[str, tuple[FunctionDeclaration, _Environment]] = {}
        self.extern_declarations: dict[str, ExternDeclaration] = {}
        self._register_declarations()

    def _register_declarations(self) -> None:
        for unit in self.program.libraries:
            for statement in unit.statements:
                if isinstance(statement, FunctionDeclaration):
                    self.functions[statement.name] = (statement, self.library_globals)
                elif isinstance(statement, ExternDeclaration):
                    self.extern_declarations[statement.name] = statement
        for statement in self.program.main.statements:
            if isinstance(statement, FunctionDeclaration):
                self.functions[statement.name] = (statement, self.main_globals)
            elif isinstance(statement, ExternDeclaration):
                self.extern_declarations[statement.name] = statement

    def run(self) -> object:
        try:
            for unit in self.program.libraries:
                self._execute_statements(unit.statements, self.library_globals, skip_declarations=True)
            self._execute_statements(self.program.main.statements, self.main_globals, skip_declarations=True)
            return self.last_value
        except ScriptCancelled:
            raise
        except ScriptRuntimeError:
            raise
        except Exception as exc:
            if _is_cancelled(self.cancel_event):
                raise ScriptCancelled("脚本已取消") from exc
            raise

    def _check_cancelled(self, location: SourceLocation | None = None) -> None:
        if _is_cancelled(self.cancel_event):
            raise ScriptCancelled("脚本已取消", location)

    def _execute_statements(
        self, statements: tuple[Statement, ...], environment: _Environment, *, skip_declarations: bool = False
    ) -> None:
        environment.declare(_scope_declarations(statements))
        for statement in statements:
            self._check_cancelled(statement.location)
            if skip_declarations and isinstance(
                statement, (FunctionDeclaration, ExternDeclaration, ImportStatement)
            ):
                continue
            self._execute_statement(statement, environment)

    def _execute_statement(self, statement: Statement, environment: _Environment) -> None:
        try:
            if isinstance(statement, Assignment):
                value = self._evaluate(statement.expression, environment)
                if statement.operator != "=":
                    current = environment.get(statement.name, statement.location)
                    value = _binary_operation(statement.operator[:-1], current, value, statement.location)
                environment.assign(statement.name, value, statement.location, constant=statement.constant)
                self.last_value = value
                return
            if isinstance(statement, Wait):
                duration = _require_int(self._evaluate(statement.duration, environment), statement.location, "等待时间")
                self._wait(duration, statement.location)
                self.last_value = None
                return
            if isinstance(statement, ButtonAction):
                self._button(statement, environment)
                return
            if isinstance(statement, StickAction):
                self._stick(statement, environment)
                return
            if isinstance(statement, CallStatement):
                arguments = tuple(self._evaluate(item, environment) for item in statement.arguments)
                self.last_value = self._call(statement.name, arguments, statement.location)
                return
            if isinstance(statement, IfStatement):
                for branch in statement.branches:
                    if _truth(self._evaluate(branch.condition, environment)):
                        self._execute_statements(
                            branch.body,
                            _Environment(environment, default_unassigned=environment.default_unassigned),
                        )
                        return
                self._execute_statements(
                    statement.else_body,
                    _Environment(environment, default_unassigned=environment.default_unassigned),
                )
                return
            if isinstance(statement, ForStatement):
                self._for(statement, environment)
                return
            if isinstance(statement, WhileStatement):
                self._while(statement, environment)
                return
            if isinstance(statement, BreakStatement):
                raise _BreakSignal(statement.level)
            if isinstance(statement, ContinueStatement):
                raise _ContinueSignal()
            if isinstance(statement, ReturnStatement):
                value = self._evaluate(statement.expression, environment) if statement.expression is not None else None
                raise _ReturnSignal(value)
            if isinstance(statement, (FunctionDeclaration, ExternDeclaration, ImportStatement)):
                return
            raise ScriptRuntimeError(f"无法执行语句 {type(statement).__name__}", statement.location)
        except (ScriptRuntimeError, ScriptCancelled, _BreakSignal, _ContinueSignal, _ReturnSignal):
            raise
        except Exception as exc:
            if _is_cancelled(self.cancel_event):
                raise ScriptCancelled("脚本已取消", statement.location) from exc
            raise ScriptRuntimeError(str(exc) or type(exc).__name__, statement.location) from exc

    def _evaluate(self, expression: Expression | None, environment: _Environment) -> object:
        if expression is None:
            return None
        try:
            if isinstance(expression, Literal):
                if isinstance(expression.value, int) and not isinstance(expression.value, bool):
                    return _int32(expression.value)
                return expression.value
            if isinstance(expression, Name):
                if expression.kind == "external":
                    getter = self.external_getters.get(expression.name) or self.external_getters.get(f"@{expression.name}")
                    if getter is None:
                        raise ScriptRuntimeError(f"找不到外部变量 {expression.name} 的 getter", expression.location)
                    value = getter()
                    return _int32(_require_int(value, expression.location, "外部变量 getter 返回值"))
                return environment.get(expression.name, expression.location)
            if isinstance(expression, Unary):
                operand = self._evaluate(expression.operand, environment)
                if expression.operator == "not":
                    return not _truth(operand)
                value = _require_int(operand, expression.location, "一元运算")
                return _int32(-value) if expression.operator == "-" else _int32(~value)
            if isinstance(expression, Binary):
                left = self._evaluate(expression.left, environment)
                if expression.operator == "and":
                    return False if not _truth(left) else bool(_truth(self._evaluate(expression.right, environment)))
                if expression.operator == "or":
                    return True if _truth(left) else bool(_truth(self._evaluate(expression.right, environment)))
                right = self._evaluate(expression.right, environment)
                return _binary_operation(expression.operator, left, right, expression.location)
            if isinstance(expression, Call):
                arguments = tuple(self._evaluate(item, environment) for item in expression.arguments)
                return self._call(expression.name, arguments, expression.location)
            if isinstance(expression, ArrayLiteral):
                items = tuple(self._evaluate(item, environment) for item in expression.items)
                if items and any(_type_name(item) != _type_name(items[0]) for item in items[1:]):
                    raise ScriptRuntimeError("数组元素类型必须一致", expression.location)
                return items
            if isinstance(expression, Index):
                target = self._evaluate(expression.target, environment)
                index = _require_int(self._evaluate(expression.index, environment), expression.location, "数组下标")
                if not isinstance(target, (str, tuple)):
                    raise ScriptRuntimeError(f"{_type_name(target)} 不支持索引", expression.location)
                if index < 0 or index >= len(target):
                    raise ScriptRuntimeError("数组下标越界", expression.location)
                return target[index]
            if isinstance(expression, Slice):
                target = self._evaluate(expression.target, environment)
                if not isinstance(target, (str, tuple)):
                    raise ScriptRuntimeError(f"{_type_name(target)} 不支持切片", expression.location)
                start = 0 if expression.start is None else _require_int(
                    self._evaluate(expression.start, environment), expression.location, "切片下标"
                )
                end = len(target) if expression.end is None else _require_int(
                    self._evaluate(expression.end, environment), expression.location, "切片下标"
                )
                if start < 0 or end < 0 or start > end or end > len(target):
                    raise ScriptRuntimeError("数组下标越界", expression.location)
                return target[start:end]
            raise ScriptRuntimeError(f"无法执行表达式 {type(expression).__name__}", expression.location)
        except (ScriptRuntimeError, ScriptCancelled):
            raise
        except Exception as exc:
            if _is_cancelled(self.cancel_event):
                raise ScriptCancelled("脚本已取消", expression.location) from exc
            raise ScriptRuntimeError(str(exc) or type(exc).__name__, expression.location) from exc

    def _call(self, name: str, arguments: tuple[object, ...], location: SourceLocation) -> object:
        self._check_cancelled(location)
        function_info = self.functions.get(name)
        if function_info is not None:
            declaration, global_environment = function_info
            if len(arguments) != len(declaration.parameters):
                raise ScriptRuntimeError(
                    f"函数 {name} 需要 {len(declaration.parameters)} 个参数，实际为 {len(arguments)}", location
                )
            locals_environment = _Environment(global_environment)
            for parameter, argument in zip(declaration.parameters, arguments, strict=True):
                locals_environment.values[parameter.name] = _coerce_declared_type(argument, parameter.type_name)
                locals_environment.readonly.add(parameter.name)
            try:
                self._execute_statements(declaration.body, locals_environment)
            except _ReturnSignal as signal:
                return _coerce_declared_type(signal.value, declaration.return_type)
            return None
        builtin_name = name.upper()
        if builtin_name in {"WAIT", "PRINT", "ALERT", "RAND", "TIME", "AMIIBO", "BEEP", "APPEND", "LEN"}:
            return self._call_builtin(builtin_name, arguments, location)
        if name in self.extern_declarations:
            declaration = self.extern_declarations[name]
            callback = self.extern_functions.get(name)
            if callback is None:
                raise ScriptRuntimeError(f"EXTERN 函数 {name} 没有运行时实现", location)
            try:
                converted_arguments = tuple(
                    _coerce_declared_type(argument, parameter.type_name)
                    for parameter, argument in zip(declaration.parameters, arguments, strict=True)
                )
                result = callback(*converted_arguments)
                return _coerce_declared_type(result, declaration.return_type)
            except Exception as exc:
                raise ScriptRuntimeError(f"EXTERN 函数 {name} 调用失败: {exc}", location) from exc
        raise ScriptRuntimeError(f"找不到函数 {name}", location)

    def _call_builtin(self, name: str, arguments: tuple[object, ...], location: SourceLocation) -> object:
        defaults: dict[str, tuple[object, ...]] = {"WAIT": (50,), "PRINT": ("",), "RAND": (100,)}
        required = {"WAIT": (0, 1), "PRINT": (0, 1), "ALERT": (1, 1), "RAND": (0, 1), "TIME": (0, 0),
                    "AMIIBO": (1, 1), "BEEP": (2, 2), "APPEND": (2, 2), "LEN": (1, 1)}
        minimum, maximum = required[name]
        if not minimum <= len(arguments) <= maximum:
            expected = str(minimum) if minimum == maximum else f"{minimum}..{maximum}"
            raise ScriptRuntimeError(f"函数 {name} 需要 {expected} 个参数，实际为 {len(arguments)}", location)
        if not arguments and name in defaults:
            arguments = defaults[name]
        if name == "WAIT":
            self._wait(_require_int(arguments[0], location, "等待时间"), location)
            return None
        if name == "PRINT":
            value = _format_value(arguments[0])
            output = value[:-1] if value.endswith("\\") else value
            self.output.print(output, not self.cancel_line_break)
            self.cancel_line_break = value.endswith("\\")
            return None
        if name == "ALERT":
            value = _format_value(arguments[0])
            self.output.alert(value[:-1] if value.endswith("\\") else value)
            return None
        if name == "RAND":
            maximum_value = max(0, _require_int(arguments[0], location, "RAND 参数"))
            return 0 if maximum_value == 0 else self.random.randrange(maximum_value)
        if name == "TIME":
            return _int32((time.monotonic_ns() - self.started_ns) // 1_000_000)
        if name == "AMIIBO":
            index = _require_int(arguments[0], location, "AMIIBO 参数")
            if index <= 9 and self.gamepad is not None:
                self.gamepad.change_amiibo(index & 0xFFFF_FFFF)
            return None
        if name == "BEEP":
            frequency = _require_int(arguments[0], location, "BEEP 频率")
            duration = _require_int(arguments[1], location, "BEEP 时长")
            if not 37 <= frequency <= 32767:
                raise ScriptRuntimeError("BEEP 参数 freq 范围不正确 (37..32767)", location)
            beep = self.beep or _platform_beep
            beep(frequency, duration)
            return None
        if name == "LEN":
            value = arguments[0]
            if not isinstance(value, (str, tuple)):
                raise ScriptRuntimeError(f"LEN 不支持 {_type_name(value)}", location)
            return len(value)
        if name == "APPEND":
            array, value = arguments
            if not isinstance(array, tuple):
                raise ScriptRuntimeError("APPEND 的第一个参数必须是数组", location)
            if array and _type_name(array[0]) != _type_name(value):
                raise ScriptRuntimeError("APPEND 的元素类型与数组不一致", location)
            return (*array, value)
        raise ScriptRuntimeError(f"未知内置函数 {name}", location)

    def _wait(self, duration: int, location: SourceLocation) -> None:
        if duration < 0:
            raise ScriptRuntimeError("等待时间不能小于 0", location)
        self._check_cancelled(location)
        try:
            method = getattr(self.waiter, "wait", None)
            if callable(method):
                method(duration, self.cancel_event)
            else:
                self.waiter(duration, self.cancel_event)  # type: ignore[operator]
        except ScriptCancelled as exc:
            if exc.location is None:
                raise ScriptCancelled(exc.message, location) from exc
            raise
        self._check_cancelled(location)

    def _button(self, statement: ButtonAction, environment: _Environment) -> None:
        if self.gamepad is None:
            return
        if statement.action == "down":
            self.gamepad.press_buttons(statement.button)
        elif statement.action == "up":
            self.gamepad.release_buttons(statement.button)
        else:
            duration = _require_int(
                self._evaluate(statement.duration, environment), statement.location, "按键持续时间"
            )
            if duration < 0:
                raise ScriptRuntimeError("按键持续时间不能小于 0", statement.location)
            self.gamepad.click_buttons(statement.button, duration, self.cancel_event)
        self._check_cancelled(statement.location)

    def _stick(self, statement: StickAction, environment: _Environment) -> None:
        if self.gamepad is None:
            return
        x, y = _stick_xy(statement.direction)
        if statement.duration is None:
            self.gamepad.set_stick(statement.side, x, y)
        else:
            duration = _require_int(
                self._evaluate(statement.duration, environment), statement.location, "摇杆持续时间"
            )
            if duration < 0:
                raise ScriptRuntimeError("摇杆持续时间不能小于 0", statement.location)
            self.gamepad.click_stick(statement.side, x, y, duration, self.cancel_event)
        self._check_cancelled(statement.location)

    def _for(self, statement: ForStatement, environment: _Environment) -> None:
        if statement.variable is not None:
            assert statement.lower is not None and statement.upper is not None and statement.step is not None
            current = _require_int(self._evaluate(statement.lower, environment), statement.location, "FOR 下界")
            upper = _require_int(self._evaluate(statement.upper, environment), statement.location, "FOR 上界")
            step = _require_int(self._evaluate(statement.step, environment), statement.location, "FOR 步长")
            if step == 0:
                raise ScriptRuntimeError("FOR 步长不能为 0", statement.location)
            loop_environment = _Environment(
                environment,
                {statement.variable: current},
                {statement.variable},
                default_unassigned=environment.default_unassigned,
            )
            while (step > 0 and current <= upper) or (step < 0 and current >= upper):
                self._check_cancelled(statement.location)
                loop_environment.values[statement.variable] = current
                if self._execute_loop_body(statement.body, loop_environment):
                    return
                current = _int32(current + step)
            return
        if statement.count is not None:
            count = _require_int(self._evaluate(statement.count, environment), statement.location, "FOR 次数")
            loop_environment = _Environment(environment, default_unassigned=environment.default_unassigned)
            for _ in range(max(0, count)):
                self._check_cancelled(statement.location)
                if self._execute_loop_body(statement.body, loop_environment):
                    return
            return
        loop_environment = _Environment(environment, default_unassigned=environment.default_unassigned)
        while True:
            self._check_cancelled(statement.location)
            if self._execute_loop_body(statement.body, loop_environment):
                return

    def _while(self, statement: WhileStatement, environment: _Environment) -> None:
        while _truth(self._evaluate(statement.condition, environment)):
            self._check_cancelled(statement.location)
            if self._execute_loop_body(statement.body, environment):
                return

    def _execute_loop_body(self, body: tuple[Statement, ...], environment: _Environment) -> bool:
        try:
            self._execute_statements(body, environment)
        except _ContinueSignal:
            return False
        except _BreakSignal as signal:
            if signal.level == 1:
                return True
            raise _BreakSignal(signal.level - 1) from signal
        return False


def _platform_beep(frequency: int, duration: int) -> None:
    if sys.platform != "win32":
        raise RuntimeError("BEEP 只在 Windows 上可用；测试可注入 beep callback")
    import winsound

    winsound.Beep(frequency, duration)


def evaluate_program(
    program: Program,
    *,
    gamepad: GamepadProtocol | None = None,
    external_getters: Mapping[str, ExternalGetter] | None = None,
    extern_functions: Mapping[str, Callable[..., object]] | None = None,
    output: OutputProtocol | OutputCallback | Any | None = None,
    cancel_event: CancelEvent = None,
    waiter: WaiterProtocol | Callable[[int, CancelEvent], None] | None = None,
    random_source: random.Random | None = None,
    beep: Callable[[int, int], None] | None = None,
) -> object:
    evaluator = _Evaluator(
        program,
        gamepad=gamepad,
        external_getters=external_getters or {},
        extern_functions=extern_functions or {},
        output=output,
        cancel_event=cancel_event,
        waiter=waiter or HighPrecisionWaiter(),
        random_source=random_source or random.Random(),
        beep=beep,
    )
    return evaluator.run()


__all__ = [
    "CancelEvent",
    "ExternalGetter",
    "GamepadProtocol",
    "HighPrecisionWaiter",
    "OutputCallback",
    "OutputProtocol",
    "WaiterProtocol",
    "evaluate_program",
]
