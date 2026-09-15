"""Replace unexecuted statements while retaining the interpreter's stack."""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass, replace

from .ast import ForStatement, FunctionDeclaration, IfBranch, IfStatement, Program, Statement, WhileStatement
from .errors import ScriptCompileError, SourceLocation
from .trace import ExecutionPoint


@dataclass
class StatementSequence:
    statements: tuple[Statement, ...]
    consumed: int = 0


class LiveProgram:
    def __init__(self, program: Program) -> None:
        self.program = program
        # Active Python frames can still reference an older AST. Retain these
        # identities until this run ends; never replay them to rebuild state.
        self._versions = [program]
        self._sequences: dict[int, StatementSequence] = {}
        self._nodes: dict[int, Statement] = {}
        self._locations: dict[int, SourceLocation] = {}
        self.called_functions: set[str] = set()

    def sequence(self, statements: tuple[Statement, ...]) -> StatementSequence:
        if not statements:
            return StatementSequence(statements)
        return self._sequences.setdefault(id(statements), StatementSequence(statements))

    def node(self, statement: Statement) -> Statement:
        while id(statement) in self._nodes:
            statement = self._nodes[id(statement)]
        return statement

    def relocate(self, point: ExecutionPoint) -> ExecutionPoint:
        if not self._locations:
            return point

        def location(value):
            while id(value) in self._locations:
                value = self._locations[id(value)]
            return value

        return replace(point, location=location(point.location), caller=location(point.caller),
                       loops=tuple(replace(loop, location=location(loop.location)) for loop in point.loops))

    @staticmethod
    def _signature(value):
        if is_dataclass(value):
            return type(value), tuple(LiveProgram._signature(getattr(value, field.name)) for field in fields(value) if field.name != "location")
        if isinstance(value, tuple):
            return tuple(LiveProgram._signature(item) for item in value)
        return type(value), value

    @staticmethod
    def _header(statement: Statement) -> Statement:
        if isinstance(statement, (ForStatement, WhileStatement, FunctionDeclaration)):
            return replace(statement, body=())
        if isinstance(statement, IfStatement):
            return replace(statement, branches=tuple(IfBranch(branch.condition, ()) for branch in statement.branches), else_body=())
        return statement

    def apply(self, program: Program) -> None:
        old_units = (self.program.main, *self.program.libraries)
        new_units = (program.main, *program.libraries)
        if [unit.source for unit in old_units] != [unit.source for unit in new_units]:
            raise ScriptCompileError("暂停后不能增删已加载的脚本库，请停止后重新运行")
        changes: list[tuple[StatementSequence, tuple[Statement, ...]]] = []
        nodes: dict[int, Statement] = {}
        locations: dict[int, SourceLocation] = {}

        def map_locations(old, new) -> None:
            if isinstance(old, SourceLocation):
                if old is not new:
                    locations[id(old)] = new
            elif is_dataclass(old):
                for field in fields(old):
                    map_locations(getattr(old, field.name), getattr(new, field.name))
            elif isinstance(old, tuple):
                for before, after in zip(old, new):
                    map_locations(before, after)

        def merge_node(old: Statement, new: Statement) -> None:
            old_header, new_header = self._header(old), self._header(new)
            if self._signature(old_header) != self._signature(new_header):
                raise ScriptCompileError("这条语句已经执行或正在执行，不能修改；请恢复该处，或停止后重新运行", old.location)
            map_locations(old_header, new_header)
            if isinstance(old, (ForStatement, WhileStatement, FunctionDeclaration)):
                merge_sequence(old.body, new.body)
            elif isinstance(old, IfStatement):
                for before, after in zip(old.branches, new.branches):
                    merge_sequence(before.body, after.body)
                merge_sequence(old.else_body, new.else_body)
            if old is not new:
                nodes[id(old)] = new

        def merge_sequence(old: tuple[Statement, ...], new: tuple[Statement, ...]) -> None:
            state = self._sequences.get(id(old)) if old else None
            consumed = state.consumed if state is not None else 0
            if len(new) < consumed:
                raise ScriptCompileError("不能删除已经执行的语句，请停止后重新运行", old[consumed - 1].location)
            for index in range(consumed):
                merge_node(old[index], new[index])
            # Functions can be called before their declaration is encountered.
            functions = {item.name: item for item in new if isinstance(item, FunctionDeclaration)}
            for item in old[consumed:]:
                if isinstance(item, FunctionDeclaration) and item.name in self.called_functions:
                    replacement = functions.get(item.name)
                    if replacement is None:
                        raise ScriptCompileError("不能删除已经调用的函数", item.location)
                    merge_node(item, replacement)
            if state is not None:
                changes.append((state, new))

        for old, new in zip(old_units, new_units):
            merge_sequence(old.statements, new.statements)
        # All validation finishes before any suspended frame sees a change.
        for state, statements in changes:
            state.statements = statements
            if statements:
                self._sequences[id(statements)] = state
        self._nodes.update(nodes)
        self._locations.update(locations)
        self.program = program
        self._versions.append(program)
