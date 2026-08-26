from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
import random
from typing import Any

from auto_bdsp_rng.automation.easycon.native.ast import (
    ArrayLiteral,
    Binary,
    ButtonAction,
    Call,
    Expression,
    ForStatement,
    FunctionDeclaration,
    IfStatement,
    ImportStatement,
    Index,
    Name,
    Program,
    Slice,
    Statement,
    StickAction,
    Unary,
    WhileStatement,
)
from auto_bdsp_rng.automation.easycon.native.errors import ScriptCompileError, SourceLocation
from auto_bdsp_rng.automation.easycon.native.parser import parse_text
from auto_bdsp_rng.automation.easycon.native.runtime import (
    CancelEvent,
    ExternalGetter,
    GamepadProtocol,
    OutputCallback,
    OutputProtocol,
    WaiterProtocol,
    evaluate_program,
)
from auto_bdsp_rng.automation.easycon.native.validation import validate_program


def _read_utf8(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise ScriptCompileError(f"无法读取脚本: {exc}", SourceLocation(str(path), 1, 1)) from exc


def _iter_expressions(expression: Expression):
    yield expression
    if isinstance(expression, Unary):
        yield from _iter_expressions(expression.operand)
    elif isinstance(expression, Binary):
        yield from _iter_expressions(expression.left)
        yield from _iter_expressions(expression.right)
    elif isinstance(expression, Call):
        for argument in expression.arguments:
            yield from _iter_expressions(argument)
    elif isinstance(expression, ArrayLiteral):
        for item in expression.items:
            yield from _iter_expressions(item)
    elif isinstance(expression, Index):
        yield from _iter_expressions(expression.target)
        yield from _iter_expressions(expression.index)
    elif isinstance(expression, Slice):
        yield from _iter_expressions(expression.target)
        if expression.start is not None:
            yield from _iter_expressions(expression.start)
        if expression.end is not None:
            yield from _iter_expressions(expression.end)


def _statement_children(statement: Statement) -> tuple[Statement, ...]:
    if isinstance(statement, IfStatement):
        return tuple(item for branch in statement.branches for item in branch.body) + statement.else_body
    if isinstance(statement, (ForStatement, WhileStatement, FunctionDeclaration)):
        return statement.body
    return ()


def _statement_expressions(statement: Statement) -> tuple[Expression, ...]:
    from auto_bdsp_rng.automation.easycon.native.ast import (
        Assignment,
        CallStatement,
        ReturnStatement,
        Wait,
    )

    if isinstance(statement, Assignment):
        return (statement.expression,)
    if isinstance(statement, Wait):
        return (statement.duration,)
    if isinstance(statement, ButtonAction):
        return (statement.duration,) if statement.duration is not None else ()
    if isinstance(statement, StickAction):
        return (statement.duration,) if statement.duration is not None else ()
    if isinstance(statement, CallStatement):
        return statement.arguments
    if isinstance(statement, IfStatement):
        return tuple(branch.condition for branch in statement.branches)
    if isinstance(statement, ForStatement):
        return tuple(item for item in (statement.count, statement.lower, statement.upper, statement.step) if item is not None)
    if isinstance(statement, WhileStatement):
        return (statement.condition,)
    if isinstance(statement, ReturnStatement):
        return (statement.expression,) if statement.expression is not None else ()
    return ()


def _program_metadata(program: Program) -> tuple[frozenset[str], bool]:
    from auto_bdsp_rng.automation.easycon.native.ast import CallStatement

    labels: set[str] = set()
    has_gamepad_actions = False
    pending = [statement for unit in (*program.libraries, program.main) for statement in unit.statements]
    while pending:
        statement = pending.pop()
        has_gamepad_actions = has_gamepad_actions or isinstance(statement, (ButtonAction, StickAction)) or (
            isinstance(statement, CallStatement) and statement.name.upper() == "AMIIBO"
        )
        for expression in _statement_expressions(statement):
            for node in _iter_expressions(expression):
                if isinstance(node, Name) and node.kind == "external":
                    labels.add(node.name)
                elif isinstance(node, Call) and node.name.upper() == "AMIIBO":
                    has_gamepad_actions = True
        pending.extend(_statement_children(statement))
    return frozenset(labels), has_gamepad_actions


@dataclass(frozen=True, slots=True)
class ScriptProgram:
    ast: Program
    source: str
    external_labels: frozenset[str]
    has_gamepad_actions: bool

    @property
    def requires_image_search(self) -> bool:
        return bool(self.external_labels)

    def run(
        self,
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
        return evaluate_program(
            self.ast,
            gamepad=gamepad,
            external_getters=external_getters,
            extern_functions=extern_functions,
            output=output,
            cancel_event=cancel_event,
            waiter=waiter,
            random_source=random_source,
            beep=beep,
        )


class EasyConScriptEngine:
    """Compile and execute EasyCon ECS scripts without starting ezcon.exe."""

    def compile(
        self,
        text: str,
        *,
        source: str = "<script>",
        script_dir: str | Path | None = None,
    ) -> ScriptProgram:
        main = parse_text(text, source, library=False)
        root = Path(script_dir).resolve() if script_dir is not None else None
        libraries = self._load_libraries(root) if root is not None else ()
        self._validate_imports((main, *libraries), root)
        ast = Program(main, libraries)
        validate_program(ast)
        external_labels, has_gamepad_actions = _program_metadata(ast)
        return ScriptProgram(ast, source, external_labels, has_gamepad_actions)

    def compile_text(
        self,
        text: str,
        *,
        source: str = "<script>",
        script_dir: str | Path | None = None,
    ) -> ScriptProgram:
        return self.compile(text, source=source, script_dir=script_dir)

    def load_file(self, path: str | Path) -> ScriptProgram:
        script_path = Path(path).resolve()
        return self.compile(_read_utf8(script_path), source=str(script_path), script_dir=script_path.parent)

    def compile_file(self, path: str | Path) -> ScriptProgram:
        return self.load_file(path)

    def run(self, program: ScriptProgram, **kwargs: Any) -> object:
        if not isinstance(program, ScriptProgram):
            raise TypeError("program 必须是 ScriptProgram；请先调用 compile() 或 load_file()")
        return program.run(**kwargs)

    def _load_libraries(self, script_root: Path) -> tuple:
        library_root = script_root / "lib"
        if not library_root.is_dir():
            return ()
        units = []
        for path in sorted(library_root.glob("*.ecs"), key=lambda item: item.name.casefold()):
            resolved = path.resolve()
            units.append(parse_text(_read_utf8(resolved), str(resolved), library=True))
        return tuple(units)

    def _validate_imports(self, units: tuple, script_root: Path | None) -> None:
        imports = [statement for unit in units for statement in unit.statements if isinstance(statement, ImportStatement)]
        if not imports:
            return
        if script_root is None:
            raise ScriptCompileError("包含 IMPORT 的脚本必须提供 script_dir", imports[0].location)
        library_root = (script_root / "lib").resolve()
        for statement in imports:
            candidate = (library_root / statement.module).resolve()
            try:
                candidate.relative_to(library_root)
            except ValueError as exc:
                raise ScriptCompileError("IMPORT 路径不能离开 lib 目录", statement.location) from exc
            if not candidate.is_file():
                raise ScriptCompileError(f"找不到导入库 {statement.module}", statement.location)


NativeEasyConEngine = EasyConScriptEngine


__all__ = ["EasyConScriptEngine", "NativeEasyConEngine", "ScriptProgram"]
