from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from auto_bdsp_rng.automation.easycon.native.errors import SourceLocation


@dataclass(frozen=True, slots=True)
class Node:
    location: SourceLocation


@dataclass(frozen=True, slots=True)
class Literal(Node):
    value: object


@dataclass(frozen=True, slots=True)
class Name(Node):
    name: str
    kind: str  # variable, constant, external


@dataclass(frozen=True, slots=True)
class Unary(Node):
    operator: str
    operand: Expression


@dataclass(frozen=True, slots=True)
class Binary(Node):
    left: Expression
    operator: str
    right: Expression


@dataclass(frozen=True, slots=True)
class Call(Node):
    name: str
    arguments: tuple[Expression, ...]


@dataclass(frozen=True, slots=True)
class ArrayLiteral(Node):
    items: tuple[Expression, ...]


@dataclass(frozen=True, slots=True)
class Index(Node):
    target: Expression
    index: Expression


@dataclass(frozen=True, slots=True)
class Slice(Node):
    target: Expression
    start: Expression | None
    end: Expression | None


Expression: TypeAlias = Literal | Name | Unary | Binary | Call | ArrayLiteral | Index | Slice


@dataclass(frozen=True, slots=True)
class Assignment(Node):
    name: str
    operator: str
    expression: Expression
    constant: bool = False


@dataclass(frozen=True, slots=True)
class Wait(Node):
    duration: Expression
    omitted_keyword: bool = False


@dataclass(frozen=True, slots=True)
class ButtonAction(Node):
    button: str
    action: str  # click, down, up
    duration: Expression | None = None


@dataclass(frozen=True, slots=True)
class StickAction(Node):
    side: str
    direction: str | int
    duration: Expression | None = None


@dataclass(frozen=True, slots=True)
class CallStatement(Node):
    name: str
    arguments: tuple[Expression, ...]


@dataclass(frozen=True, slots=True)
class IfBranch:
    condition: Expression
    body: tuple[Statement, ...]


@dataclass(frozen=True, slots=True)
class IfStatement(Node):
    branches: tuple[IfBranch, ...]
    else_body: tuple[Statement, ...]


@dataclass(frozen=True, slots=True)
class ForStatement(Node):
    body: tuple[Statement, ...]
    count: Expression | None = None
    variable: str | None = None
    lower: Expression | None = None
    upper: Expression | None = None
    step: Expression | None = None


@dataclass(frozen=True, slots=True)
class WhileStatement(Node):
    condition: Expression
    body: tuple[Statement, ...]


@dataclass(frozen=True, slots=True)
class BreakStatement(Node):
    level: int = 1


@dataclass(frozen=True, slots=True)
class ContinueStatement(Node):
    pass


@dataclass(frozen=True, slots=True)
class ReturnStatement(Node):
    expression: Expression | None = None


@dataclass(frozen=True, slots=True)
class ImportStatement(Node):
    module: str


@dataclass(frozen=True, slots=True)
class Parameter:
    name: str
    type_name: str = "INT"


@dataclass(frozen=True, slots=True)
class FunctionDeclaration(Node):
    name: str
    parameters: tuple[Parameter, ...]
    return_type: str
    body: tuple[Statement, ...]
    library: bool = False


@dataclass(frozen=True, slots=True)
class ExternDeclaration(Node):
    name: str
    parameters: tuple[Parameter, ...]
    return_type: str
    library_name: str


Statement: TypeAlias = (
    Assignment
    | Wait
    | ButtonAction
    | StickAction
    | CallStatement
    | IfStatement
    | ForStatement
    | WhileStatement
    | BreakStatement
    | ContinueStatement
    | ReturnStatement
    | ImportStatement
    | FunctionDeclaration
    | ExternDeclaration
)


@dataclass(frozen=True, slots=True)
class ParsedUnit:
    source: str
    statements: tuple[Statement, ...]
    library: bool = False


@dataclass(frozen=True, slots=True)
class Program:
    main: ParsedUnit
    libraries: tuple[ParsedUnit, ...] = ()

    @property
    def statements(self) -> tuple[Statement, ...]:
        return self.main.statements
