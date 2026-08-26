from __future__ import annotations

from dataclasses import dataclass, field

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
from auto_bdsp_rng.automation.easycon.native.errors import ScriptCompileError, SourceLocation


_BUILTIN_SIGNATURES: dict[str, tuple[tuple[str, ...], str, int]] = {
    "WAIT": (("INT",), "VOID", 0),
    "PRINT": (("ANY",), "VOID", 0),
    "ALERT": (("ANY",), "VOID", 1),
    "RAND": (("INT",), "INT", 0),
    "TIME": ((), "INT", 0),
    "AMIIBO": (("INT",), "VOID", 1),
    "BEEP": (("INT", "INT"), "VOID", 2),
    "APPEND": (("ARRAY", "ANY"), "ARRAY", 2),
    "LEN": (("CONTAINER",), "INT", 1),
}


@dataclass(frozen=True, slots=True)
class _Symbol:
    type_name: str
    readonly: bool = False


@dataclass(slots=True)
class _Scope:
    parent: _Scope | None = None
    symbols: dict[str, _Symbol] = field(default_factory=dict)

    def lookup(self, name: str) -> _Symbol | None:
        symbol = self.symbols.get(name)
        if symbol is not None:
            return symbol
        return self.parent.lookup(name) if self.parent is not None else None

    def declare(self, name: str, symbol: _Symbol) -> None:
        self.symbols[name] = symbol


@dataclass(frozen=True, slots=True)
class _FunctionSignature:
    parameters: tuple[str, ...]
    return_type: str
    declaration: FunctionDeclaration | ExternDeclaration


def _error(message: str, location: SourceLocation) -> None:
    raise ScriptCompileError(message, location)


def _literal_type(value: object) -> str:
    if isinstance(value, bool):
        return "BOOL"
    if isinstance(value, int):
        return "INT"
    if isinstance(value, float):
        return "DOUBLE"
    if isinstance(value, str):
        return "STRING"
    return "UNKNOWN"


def _array_element(type_name: str) -> str | None:
    return type_name[:-2] if type_name.endswith("[]") else None


def _assignable(destination: str, source: str) -> bool:
    if destination == source or destination == "ANY" or source == "UNKNOWN":
        return True
    return destination == "STRING"


class _Validator:
    def __init__(self, program: Program) -> None:
        self.program = program
        self.functions: dict[str, _FunctionSignature] = {}
        self.library_scope = _Scope()
        self.main_scope = _Scope()

    def validate(self) -> None:
        self._collect_functions()
        for unit in self.program.libraries:
            self._validate_statements(unit.statements, self.library_scope, None)
        self._validate_statements(self.program.main.statements, self.main_scope, None)
        for unit in self.program.libraries:
            for statement in unit.statements:
                if isinstance(statement, FunctionDeclaration):
                    self._validate_function(statement, self.library_scope)
        for statement in self.program.main.statements:
            if isinstance(statement, FunctionDeclaration):
                self._validate_function(statement, self.main_scope)

    def _collect_functions(self) -> None:
        for unit in (*self.program.libraries, self.program.main):
            for statement in unit.statements:
                if not isinstance(statement, (FunctionDeclaration, ExternDeclaration)):
                    continue
                if statement.name in self.functions:
                    _error(f"函数 {statement.name} 已经定义", statement.location)
                parameters = tuple(parameter.type_name for parameter in statement.parameters)
                self.functions[statement.name] = _FunctionSignature(parameters, statement.return_type, statement)

    def _validate_function(self, declaration: FunctionDeclaration, globals_scope: _Scope) -> None:
        scope = _Scope(globals_scope)
        for parameter in declaration.parameters:
            scope.declare(parameter.name, _Symbol(parameter.type_name, True))
        self._validate_statements(declaration.body, scope, declaration)
        if declaration.return_type != "VOID" and not self._all_paths_return(declaration.body):
            _error(f"函数 {declaration.name} 并非所有路径都返回值", declaration.location)

    def _validate_statements(
        self,
        statements: tuple[Statement, ...],
        scope: _Scope,
        function: FunctionDeclaration | None,
    ) -> None:
        for statement in statements:
            if isinstance(statement, Assignment):
                expression_type = self._expression_type(statement.expression, scope)
                if expression_type == "VOID":
                    _error("VOID 表达式不能赋值", statement.location)
                existing = scope.lookup(statement.name)
                if statement.constant:
                    if existing is not None:
                        _error(f"常量 {statement.name} 已经定义", statement.location)
                    if statement.operator != "=":
                        _error("常量不能使用复合赋值", statement.location)
                    if not self._is_constant_expression(statement.expression, scope):
                        _error("常量只能使用编译期表达式，不能引用变量、getter 或函数", statement.location)
                    scope.declare(statement.name, _Symbol(expression_type, True))
                    continue
                if statement.operator != "=":
                    if existing is None:
                        _error(f"找不到变量 {statement.name}", statement.location)
                    if existing.readonly:
                        _error(f"变量 {statement.name} 是只读的", statement.location)
                    expression_type = self._binary_type(
                        statement.operator[:-1], existing.type_name, expression_type, statement.location
                    )
                if existing is not None:
                    if existing.readonly:
                        _error(f"变量 {statement.name} 是只读的", statement.location)
                    if not _assignable(existing.type_name, expression_type):
                        _error(
                            f"不能把 {expression_type} 赋给 {existing.type_name} 变量 {statement.name}",
                            statement.location,
                        )
                else:
                    scope.declare(statement.name, _Symbol(expression_type))
            elif isinstance(statement, Wait):
                self._expect_type(self._expression_type(statement.duration, scope), "INT", statement.location)
            elif isinstance(statement, ButtonAction):
                if statement.duration is not None:
                    self._expect_type(self._expression_type(statement.duration, scope), "INT", statement.location)
            elif isinstance(statement, StickAction):
                if statement.duration is not None:
                    self._expect_type(self._expression_type(statement.duration, scope), "INT", statement.location)
            elif isinstance(statement, CallStatement):
                argument_types = tuple(self._expression_type(argument, scope) for argument in statement.arguments)
                self._call_type(statement.name, argument_types, statement.location)
            elif isinstance(statement, IfStatement):
                for branch in statement.branches:
                    self._expression_type(branch.condition, scope)
                    self._validate_statements(branch.body, _Scope(scope), function)
                self._validate_statements(statement.else_body, _Scope(scope), function)
            elif isinstance(statement, ForStatement):
                loop_scope = _Scope(scope)
                if statement.variable is not None:
                    assert statement.lower is not None and statement.upper is not None and statement.step is not None
                    self._expect_type(self._expression_type(statement.lower, scope), "INT", statement.location)
                    self._expect_type(self._expression_type(statement.upper, scope), "INT", statement.location)
                    self._expect_type(self._expression_type(statement.step, scope), "INT", statement.location)
                    loop_scope.declare(statement.variable, _Symbol("INT", True))
                elif statement.count is not None:
                    self._expect_type(self._expression_type(statement.count, scope), "INT", statement.location)
                self._validate_statements(statement.body, loop_scope, function)
            elif isinstance(statement, WhileStatement):
                self._expression_type(statement.condition, scope)
                self._validate_statements(statement.body, scope, function)
            elif isinstance(statement, ReturnStatement):
                if function is None:
                    _error("RETURN 只能在函数中使用", statement.location)
                if function.return_type == "VOID":
                    if statement.expression is not None:
                        _error(f"VOID 函数 {function.name} 不能返回值", statement.location)
                elif statement.expression is None:
                    _error(f"函数 {function.name} 必须返回 {function.return_type}", statement.location)
                else:
                    actual = self._expression_type(statement.expression, scope)
                    if not _assignable(function.return_type, actual):
                        _error(
                            f"函数 {function.name} 返回值需要 {function.return_type}，实际为 {actual}",
                            statement.location,
                        )
            elif isinstance(
                statement,
                (
                    BreakStatement,
                    ContinueStatement,
                    ImportStatement,
                    FunctionDeclaration,
                    ExternDeclaration,
                ),
            ):
                continue

    def _expression_type(self, expression: Expression, scope: _Scope) -> str:
        if isinstance(expression, Literal):
            return _literal_type(expression.value)
        if isinstance(expression, Name):
            if expression.kind == "external":
                return "INT"
            symbol = scope.lookup(expression.name)
            if symbol is None:
                _error(f"找不到变量 {expression.name}", expression.location)
            return symbol.type_name
        if isinstance(expression, Unary):
            operand_type = self._expression_type(expression.operand, scope)
            if expression.operator == "not":
                return "BOOL"
            self._expect_type(operand_type, "INT", expression.location)
            return "INT"
        if isinstance(expression, Binary):
            left = self._expression_type(expression.left, scope)
            if expression.operator in {"and", "or"}:
                self._expression_type(expression.right, scope)
                return "BOOL"
            right = self._expression_type(expression.right, scope)
            return self._binary_type(expression.operator, left, right, expression.location)
        if isinstance(expression, Call):
            arguments = tuple(self._expression_type(argument, scope) for argument in expression.arguments)
            return self._call_type(expression.name, arguments, expression.location)
        if isinstance(expression, ArrayLiteral):
            if not expression.items:
                return "INT[]"
            types = [self._expression_type(item, scope) for item in expression.items]
            if any(item != types[0] for item in types[1:]):
                _error("数组元素类型必须一致", expression.location)
            return f"{types[0]}[]"
        if isinstance(expression, Index):
            target_type = self._expression_type(expression.target, scope)
            self._expect_type(self._expression_type(expression.index, scope), "INT", expression.location)
            if target_type == "STRING":
                return "STRING"
            element_type = _array_element(target_type)
            if element_type is None:
                _error(f"{target_type} 不支持索引", expression.location)
            return element_type
        if isinstance(expression, Slice):
            target_type = self._expression_type(expression.target, scope)
            if target_type != "STRING" and _array_element(target_type) is None:
                _error(f"{target_type} 不支持切片", expression.location)
            if expression.start is not None:
                self._expect_type(self._expression_type(expression.start, scope), "INT", expression.location)
            if expression.end is not None:
                self._expect_type(self._expression_type(expression.end, scope), "INT", expression.location)
            return target_type
        _error(f"无法绑定表达式 {type(expression).__name__}", expression.location)

    def _binary_type(self, operator: str, left: str, right: str, location: SourceLocation) -> str:
        if operator in {"==", "!="}:
            if left != right:
                _error(f"比较两侧类型不一致: {left} 和 {right}", location)
            return "BOOL"
        if operator in {"<", "<=", ">", ">="}:
            if left != right or left not in {"INT", "DOUBLE", "STRING"}:
                _error(f"{operator} 不支持 {left} 和 {right}", location)
            return "BOOL"
        if operator == "&" and (left == "STRING" or right == "STRING"):
            return "STRING"
        if operator == "+" and left == right and (left in {"STRING", "INT", "DOUBLE"} or left.endswith("[]")):
            return left
        if operator in {"+", "-", "*", "/"} and {left, right} <= {"INT", "DOUBLE"}:
            return "DOUBLE" if "DOUBLE" in {left, right} else "INT"
        if operator in {"\\", "%", "&", "|", "^", "<<", ">>"} and left == right == "INT":
            return "INT"
        _error(f"运算符 {operator} 不支持 {left} 和 {right}", location)

    def _call_type(self, name: str, arguments: tuple[str, ...], location: SourceLocation) -> str:
        signature = self.functions.get(name)
        if signature is not None:
            if len(arguments) != len(signature.parameters):
                _error(f"函数 {name} 需要 {len(signature.parameters)} 个参数，实际为 {len(arguments)}", location)
            for expected, actual in zip(signature.parameters, arguments, strict=True):
                if not _assignable(expected, actual):
                    _error(f"函数 {name} 参数需要 {expected}，实际为 {actual}", location)
            return signature.return_type

        builtin_name = name.upper()
        builtin = _BUILTIN_SIGNATURES.get(builtin_name)
        if builtin is None:
            _error(f"找不到函数 {name}", location)
        parameters, return_type, minimum = builtin
        if not minimum <= len(arguments) <= len(parameters):
            expected = str(minimum) if minimum == len(parameters) else f"{minimum}..{len(parameters)}"
            _error(f"函数 {builtin_name} 需要 {expected} 个参数，实际为 {len(arguments)}", location)
        if builtin_name == "APPEND" and len(arguments) == 2:
            element = _array_element(arguments[0])
            if element is None:
                _error("APPEND 的第一个参数必须是数组", location)
            if element != arguments[1]:
                _error(f"APPEND 需要 {element} 元素，实际为 {arguments[1]}", location)
            return arguments[0]
        if builtin_name == "LEN" and arguments and arguments[0] != "STRING" and not arguments[0].endswith("[]"):
            _error(f"LEN 不支持 {arguments[0]}", location)
        for expected, actual in zip(parameters, arguments):
            if expected in {"ANY", "ARRAY", "CONTAINER"}:
                continue
            if not _assignable(expected, actual):
                _error(f"函数 {builtin_name} 参数需要 {expected}，实际为 {actual}", location)
        return return_type

    def _expect_type(self, actual: str, expected: str, location: SourceLocation) -> None:
        if not _assignable(expected, actual):
            _error(f"表达式需要 {expected}，实际为 {actual}", location)

    def _is_constant_expression(self, expression: Expression, scope: _Scope) -> bool:
        if isinstance(expression, Literal):
            return True
        if isinstance(expression, Name):
            if expression.kind == "external":
                return False
            symbol = scope.lookup(expression.name)
            return symbol is not None and symbol.readonly and expression.kind == "constant"
        if isinstance(expression, Unary):
            return self._is_constant_expression(expression.operand, scope)
        if isinstance(expression, Binary):
            return self._is_constant_expression(expression.left, scope) and self._is_constant_expression(
                expression.right, scope
            )
        if isinstance(expression, ArrayLiteral):
            return all(self._is_constant_expression(item, scope) for item in expression.items)
        if isinstance(expression, Index):
            return self._is_constant_expression(expression.target, scope) and self._is_constant_expression(
                expression.index, scope
            )
        if isinstance(expression, Slice):
            return self._is_constant_expression(expression.target, scope) and (
                expression.start is None or self._is_constant_expression(expression.start, scope)
            ) and (expression.end is None or self._is_constant_expression(expression.end, scope))
        return False

    def _all_paths_return(self, statements: tuple[Statement, ...]) -> bool:
        for statement in statements:
            if isinstance(statement, ReturnStatement):
                return True
            if isinstance(statement, IfStatement):
                if statement.else_body and all(
                    self._all_paths_return(branch.body) for branch in statement.branches
                ) and self._all_paths_return(statement.else_body):
                    return True
        return False


def validate_program(program: Program) -> None:
    _Validator(program).validate()


__all__ = ["validate_program"]
