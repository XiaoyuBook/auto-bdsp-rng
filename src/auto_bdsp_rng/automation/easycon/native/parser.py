from __future__ import annotations

from dataclasses import dataclass
import re

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
    IfBranch,
    IfStatement,
    ImportStatement,
    Index,
    Literal,
    Name,
    Parameter,
    ParsedUnit,
    ReturnStatement,
    Slice,
    Statement,
    StickAction,
    Unary,
    Wait,
    WhileStatement,
)
from auto_bdsp_rng.automation.easycon.native.errors import ScriptCompileError, SourceLocation


_BUTTONS = frozenset(
    {
        "A",
        "B",
        "X",
        "Y",
        "L",
        "R",
        "ZL",
        "ZR",
        "MINUS",
        "PLUS",
        "HOME",
        "CAPTURE",
        "LCLICK",
        "RCLICK",
        "DOWNLEFT",
        "DOWNRIGHT",
        "UPLEFT",
        "UPRIGHT",
        "UP",
        "DOWN",
        "LEFT",
        "RIGHT",
    }
)
_DIRECTIONS = frozenset(
    {"UP", "DOWN", "LEFT", "RIGHT", "DOWNLEFT", "DOWNRIGHT", "UPLEFT", "UPRIGHT", "RESET"}
)
_ASSIGNMENT_OPERATORS = ("<<=", ">>=", "+=", "-=", "*=", "/=", "\\=", "%=", "&=", "|=", "^=", "=")
_BINARY_PRECEDENCE = {
    "or": 1,
    "and": 2,
    "==": 3,
    "!=": 3,
    "<": 3,
    "<=": 3,
    ">": 3,
    ">=": 3,
    "+": 4,
    "-": 4,
    "|": 4,
    "^": 4,
    "*": 5,
    "/": 5,
    "\\": 5,
    "%": 5,
    "&": 5,
    "<<": 5,
    ">>": 5,
}
_UNARY_OPERATORS = frozenset({"-", "~", "not"})
_VALID_TYPES = frozenset({"BOOL", "INT", "STRING", "PTR", "DOUBLE", "VOID"})
_IDENT_RE = re.compile(r"[^\W\d]\w*", re.UNICODE)
_VARIABLE_RE = re.compile(r"(?:\$\$?|_|@)\w+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class _Line:
    text: str
    number: int


@dataclass(frozen=True, slots=True)
class _Token:
    kind: str
    value: object
    column: int


def _location(source: str, line: int, column: int = 1) -> SourceLocation:
    return SourceLocation(source, line, column)


def _fail(message: str, source: str, line: int, column: int = 1) -> None:
    raise ScriptCompileError(message, _location(source, line, column))


def _strip_comment(text: str) -> str:
    quote: str | None = None
    escaped = False
    for index, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if quote is not None and char == "\\":
            escaped = True
            continue
        if char in {"'", '"'}:
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            continue
        if char == "#" and quote is None:
            return text[:index]
    return text


def _head(text: str) -> tuple[str, str]:
    parts = text.lstrip().split(None, 1)
    if not parts:
        return "", ""
    return parts[0].upper(), parts[1].strip() if len(parts) == 2 else ""


def _split_top_level(text: str, separator: str = ",") -> list[str]:
    result: list[str] = []
    start = 0
    quote: str | None = None
    escaped = False
    depth = 0
    for index, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if quote is not None:
            if char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
        elif char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        elif char == separator and depth == 0:
            result.append(text[start:index].strip())
            start = index + 1
    result.append(text[start:].strip())
    return result


def _decode_string(text: str, source: str, line: int, column: int) -> str:
    quote = text[0]
    value: list[str] = []
    index = 1
    escapes = {"n": "\n", "t": "\t", "r": "\r", "'": "'", '"': '"', "\\": "\\"}
    while index < len(text) - 1:
        char = text[index]
        if char == "\\":
            index += 1
            if index >= len(text) - 1 or text[index] not in escapes:
                _fail("无效的字符串转义", source, line, column + index)
            value.append(escapes[text[index]])
        else:
            value.append(char)
        index += 1
    if not text.endswith(quote):
        _fail("字符串没有正确结束", source, line, column)
    return "".join(value)


def _tokenize_expression(text: str, source: str, line: int, column_offset: int = 0) -> tuple[_Token, ...]:
    tokens: list[_Token] = []
    position = 0
    operators = ("<<", ">>", "<=", ">=", "==", "!=")
    while position < len(text):
        char = text[position]
        if char.isspace():
            position += 1
            continue
        column = column_offset + position + 1
        if char in {"'", '"'}:
            quote = char
            end = position + 1
            escaped = False
            while end < len(text):
                current = text[end]
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == quote:
                    end += 1
                    break
                end += 1
            else:
                _fail("字符串没有正确结束", source, line, column)
            raw = text[position:end]
            tokens.append(_Token("LITERAL", _decode_string(raw, source, line, column), column))
            position = end
            continue
        variable = _VARIABLE_RE.match(text, position)
        if variable is not None:
            raw = variable.group(0)
            kind = "EXTERNAL" if raw.startswith("@") else "CONSTANT" if raw.startswith("_") else "VARIABLE"
            tokens.append(_Token(kind, raw, column))
            position = variable.end()
            continue
        if char.isdigit():
            end = position + 1
            if text.startswith(("0x", "0X"), position):
                end = position + 2
                while end < len(text) and text[end] in "0123456789abcdefABCDEF":
                    end += 1
                if end == position + 2:
                    _fail("十六进制数字无效", source, line, column)
                tokens.append(_Token("LITERAL", int(text[position:end], 16), column))
            else:
                while end < len(text) and text[end].isdigit():
                    end += 1
                if end < len(text) and text[end] == "." and end + 1 < len(text) and text[end + 1].isdigit():
                    end += 1
                    while end < len(text) and text[end].isdigit():
                        end += 1
                    tokens.append(_Token("LITERAL", float(text[position:end]), column))
                else:
                    value = int(text[position:end])
                    if value > 2_147_483_648:
                        _fail("整数超出 32 位范围", source, line, column)
                    tokens.append(_Token("LITERAL", value, column))
            position = end
            continue
        identifier = _IDENT_RE.match(text, position)
        if identifier is not None:
            raw = identifier.group(0)
            lower = raw.lower()
            if lower == "true":
                tokens.append(_Token("LITERAL", True, column))
            elif lower == "false":
                tokens.append(_Token("LITERAL", False, column))
            elif lower in {"and", "or", "not"}:
                tokens.append(_Token("OP", lower, column))
            else:
                tokens.append(_Token("IDENT", raw, column))
            position = identifier.end()
            continue
        operator = next((candidate for candidate in operators if text.startswith(candidate, position)), None)
        if operator is not None:
            tokens.append(_Token("OP", operator, column))
            position += len(operator)
            continue
        if char in "+-*/\\%&|^~<>":
            tokens.append(_Token("OP", char, column))
            position += 1
            continue
        if char == "=":
            # EasyCon's legacy mode accepts a single '=' in IF/ELIF conditions.
            tokens.append(_Token("OP", "==", column))
            position += 1
            continue
        punctuation = {"(": "LPAREN", ")": "RPAREN", "[": "LBRACKET", "]": "RBRACKET", ",": "COMMA", ":": "COLON"}
        if char in punctuation:
            tokens.append(_Token(punctuation[char], char, column))
            position += 1
            continue
        _fail(f"无法识别的字符 {char!r}", source, line, column)
    tokens.append(_Token("EOF", "", column_offset + len(text) + 1))
    return tuple(tokens)


class _ExpressionParser:
    def __init__(self, text: str, source: str, line: int, column_offset: int = 0) -> None:
        self._source = source
        self._line = line
        self._tokens = _tokenize_expression(text, source, line, column_offset)
        self._position = 0

    @property
    def current(self) -> _Token:
        return self._tokens[self._position]

    def advance(self) -> _Token:
        token = self.current
        self._position += 1
        return token

    def match(self, kind: str, value: str | None = None) -> _Token:
        token = self.current
        if token.kind != kind or (value is not None and token.value != value):
            expected = value if value is not None else kind
            _fail(f"需要 {expected}，实际为 {token.value!r}", self._source, self._line, token.column)
        return self.advance()

    def parse(self) -> Expression:
        expression = self.parse_expression()
        if self.current.kind != "EOF":
            _fail(f"表达式末尾存在多余内容 {self.current.value!r}", self._source, self._line, self.current.column)
        return expression

    def parse_expression(self, parent_precedence: int = 0) -> Expression:
        token = self.current
        if token.kind == "OP" and token.value in _UNARY_OPERATORS:
            operator = str(self.advance().value)
            operand = self.parse_expression(6)
            left: Expression = Unary(_location(self._source, self._line, token.column), operator, operand)
        else:
            left = self.parse_primary()

        while self.current.kind == "OP":
            operator = str(self.current.value)
            precedence = _BINARY_PRECEDENCE.get(operator, 0)
            if precedence == 0 or precedence <= parent_precedence:
                break
            op_token = self.advance()
            right = self.parse_expression(precedence)
            left = Binary(_location(self._source, self._line, op_token.column), left, operator, right)
        return left

    def parse_primary(self) -> Expression:
        token = self.current
        location = _location(self._source, self._line, token.column)
        if token.kind == "LITERAL":
            self.advance()
            expression: Expression = Literal(location, token.value)
        elif token.kind in {"VARIABLE", "CONSTANT", "EXTERNAL"}:
            self.advance()
            kind = {"VARIABLE": "variable", "CONSTANT": "constant", "EXTERNAL": "external"}[token.kind]
            name = str(token.value)
            if kind == "external":
                name = name[1:]
            expression = Name(location, name, kind)
        elif token.kind == "IDENT":
            identifier = str(self.advance().value)
            self.match("LPAREN")
            arguments = self.parse_arguments("RPAREN")
            self.match("RPAREN")
            expression = Call(location, identifier, arguments)
        elif token.kind == "LPAREN":
            self.advance()
            expression = self.parse_expression()
            self.match("RPAREN")
        elif token.kind == "LBRACKET":
            self.advance()
            items = self.parse_arguments("RBRACKET")
            self.match("RBRACKET")
            expression = ArrayLiteral(location, items)
        else:
            _fail(f"无效表达式 {token.value!r}", self._source, self._line, token.column)

        while self.current.kind == "LBRACKET":
            bracket = self.advance()
            bracket_location = _location(self._source, self._line, bracket.column)
            if self.current.kind == "COLON":
                start = None
            else:
                start = self.parse_expression()
            if self.current.kind == "COLON":
                self.advance()
                end = None if self.current.kind == "RBRACKET" else self.parse_expression()
                self.match("RBRACKET")
                expression = Slice(bracket_location, expression, start, end)
            else:
                if start is None:
                    _fail("索引不能为空", self._source, self._line, bracket.column)
                self.match("RBRACKET")
                expression = Index(bracket_location, expression, start)
        return expression

    def parse_arguments(self, closing_kind: str) -> tuple[Expression, ...]:
        arguments: list[Expression] = []
        if self.current.kind == closing_kind:
            return ()
        while True:
            arguments.append(self.parse_expression())
            if self.current.kind != "COMMA":
                break
            self.advance()
            if self.current.kind == closing_kind:
                _fail("逗号后缺少表达式", self._source, self._line, self.current.column)
        return tuple(arguments)


def parse_expression(text: str, source: str = "<script>", line: int = 1, column_offset: int = 0) -> Expression:
    if not text.strip():
        _fail("表达式不能为空", source, line, column_offset + 1)
    return _ExpressionParser(text, source, line, column_offset).parse()


class _ProgramParser:
    _CLOSERS = frozenset({"ELIF", "ELSE", "ENDIF", "NEXT", "END", "ENDFUNC"})

    def __init__(self, text: str, source: str, library: bool) -> None:
        self.source = source
        self.library = library
        self.lines: list[_Line] = []
        for number, physical_line in enumerate(text.splitlines(), 1):
            code = _strip_comment(physical_line).strip()
            if code:
                self.lines.append(_Line(code, number))
        self.position = 0

    def parse(self) -> ParsedUnit:
        statements = self._parse_sequence(frozenset(), top_level=True, loop_depth=0, in_function=False)
        if self.position != len(self.lines):
            line = self.lines[self.position]
            _fail("存在未处理的脚本内容", self.source, line.number)
        self._validate_import_order(statements)
        if self.library:
            allowed = (Assignment, FunctionDeclaration, ExternDeclaration, ImportStatement)
            for statement in statements:
                if not isinstance(statement, allowed):
                    raise ScriptCompileError("库脚本只允许变量、常量、函数和 EXTERN 声明", statement.location)
        return ParsedUnit(self.source, statements, self.library)

    def _validate_import_order(self, statements: tuple[Statement, ...]) -> None:
        executable_seen = False
        for statement in statements:
            if isinstance(statement, ImportStatement):
                if executable_seen:
                    raise ScriptCompileError("IMPORT 只能出现在脚本开头", statement.location)
            else:
                executable_seen = True

    def _parse_sequence(
        self,
        stop_words: frozenset[str],
        *,
        top_level: bool,
        loop_depth: int,
        in_function: bool,
    ) -> tuple[Statement, ...]:
        statements: list[Statement] = []
        while self.position < len(self.lines):
            line = self.lines[self.position]
            keyword, remainder = _head(line.text)
            if keyword in stop_words:
                break
            if keyword in self._CLOSERS:
                _fail(f"多余的结束语句 {keyword}", self.source, line.number)
            if keyword == "IF":
                statements.append(self._parse_if(remainder, top_level, loop_depth, in_function))
            elif keyword == "FOR":
                statements.append(self._parse_for(remainder, top_level, loop_depth, in_function))
            elif keyword == "WHILE":
                statements.append(self._parse_while(remainder, top_level, loop_depth, in_function))
            elif keyword == "FUNC":
                if not top_level:
                    _fail("函数必须在顶层定义", self.source, line.number)
                statements.append(self._parse_function(remainder))
            else:
                statements.append(self._parse_simple(line, top_level, loop_depth, in_function))
                self.position += 1
        return tuple(statements)

    def _parse_if(self, remainder: str, top_level: bool, loop_depth: int, in_function: bool) -> IfStatement:
        line = self.lines[self.position]
        branches: list[IfBranch] = []
        condition = parse_expression(remainder, self.source, line.number, len("IF "))
        location = _location(self.source, line.number)
        self.position += 1
        body = self._parse_sequence(
            frozenset({"ELIF", "ELSE", "ENDIF"}),
            top_level=False,
            loop_depth=loop_depth,
            in_function=in_function,
        )
        branches.append(IfBranch(condition, body))
        while self.position < len(self.lines):
            marker = self.lines[self.position]
            keyword, branch_text = _head(marker.text)
            if keyword != "ELIF":
                break
            branch_condition = parse_expression(branch_text, self.source, marker.number, len("ELIF "))
            self.position += 1
            branch_body = self._parse_sequence(
                frozenset({"ELIF", "ELSE", "ENDIF"}),
                top_level=False,
                loop_depth=loop_depth,
                in_function=in_function,
            )
            branches.append(IfBranch(branch_condition, branch_body))
        else_body: tuple[Statement, ...] = ()
        if self.position < len(self.lines) and _head(self.lines[self.position].text)[0] == "ELSE":
            else_line = self.lines[self.position]
            if _head(else_line.text)[1]:
                _fail("ELSE 后不能有其他内容", self.source, else_line.number)
            self.position += 1
            else_body = self._parse_sequence(
                frozenset({"ENDIF"}), top_level=False, loop_depth=loop_depth, in_function=in_function
            )
        self._consume_closer("ENDIF", "IF 语句缺少 ENDIF")
        return IfStatement(location, tuple(branches), else_body)

    def _parse_for(self, remainder: str, top_level: bool, loop_depth: int, in_function: bool) -> ForStatement:
        line = self.lines[self.position]
        location = _location(self.source, line.number)
        count: Expression | None = None
        variable: str | None = None
        lower: Expression | None = None
        upper: Expression | None = None
        step: Expression | None = None
        if remainder:
            full = re.fullmatch(
                r"(\$\$?\w+)\s*=\s*(.+?)\s+TO\s+(.+?)(?:\s+STEP\s+(.+))?",
                remainder,
                flags=re.IGNORECASE,
            )
            if full is not None:
                variable = full.group(1)
                lower = parse_expression(full.group(2), self.source, line.number)
                upper = parse_expression(full.group(3), self.source, line.number)
                step = parse_expression(full.group(4), self.source, line.number) if full.group(4) else Literal(location, 1)
            else:
                count = parse_expression(remainder, self.source, line.number, len("FOR "))
        self.position += 1
        body = self._parse_sequence(
            frozenset({"NEXT"}), top_level=False, loop_depth=loop_depth + 1, in_function=in_function
        )
        self._consume_closer("NEXT", "FOR 语句缺少 NEXT")
        return ForStatement(location, body, count, variable, lower, upper, step)

    def _parse_while(self, remainder: str, top_level: bool, loop_depth: int, in_function: bool) -> WhileStatement:
        line = self.lines[self.position]
        condition = parse_expression(remainder, self.source, line.number, len("WHILE "))
        location = _location(self.source, line.number)
        self.position += 1
        body = self._parse_sequence(
            frozenset({"END"}), top_level=False, loop_depth=loop_depth + 1, in_function=in_function
        )
        self._consume_closer("END", "WHILE 语句缺少 END")
        return WhileStatement(location, condition, body)

    def _parse_function(self, remainder: str) -> FunctionDeclaration:
        line = self.lines[self.position]
        match = re.fullmatch(r"([^\s(:]+)\s*(?:\((.*?)\))?\s*(?::\s*([\w\[\]]+))?", remainder)
        if match is None:
            _fail("FUNC 声明格式无效", self.source, line.number)
        name = match.group(1)
        parameters = self._parse_parameters(match.group(2) or "", line.number)
        return_type = self._parse_type(match.group(3) or "VOID", line.number, allow_void=True)
        location = _location(self.source, line.number)
        self.position += 1
        body = self._parse_sequence(
            frozenset({"ENDFUNC"}), top_level=False, loop_depth=0, in_function=True
        )
        self._consume_closer("ENDFUNC", "FUNC 声明缺少 ENDFUNC")
        return FunctionDeclaration(location, name, parameters, return_type, body, self.library)

    def _parse_parameters(self, text: str, line: int) -> tuple[Parameter, ...]:
        if not text.strip():
            return ()
        parameters: list[Parameter] = []
        names: set[str] = set()
        for item in _split_top_level(text):
            match = re.fullmatch(r"(\$\$?\w+)\s*(?::\s*([\w\[\]]+))?", item)
            if match is None:
                _fail("函数参数格式无效", self.source, line)
            name = match.group(1)
            if name in names:
                _fail(f"函数参数重复定义: {name}", self.source, line)
            names.add(name)
            type_name = self._parse_type(match.group(2) or "INT", line, allow_void=False)
            parameters.append(Parameter(name, type_name))
        return tuple(parameters)

    def _parse_type(self, text: str, line: int, *, allow_void: bool) -> str:
        normalized = text.upper()
        base = normalized[:-2] if normalized.endswith("[]") else normalized
        if base not in _VALID_TYPES or (base == "VOID" and (normalized.endswith("[]") or not allow_void)):
            _fail(f"未知类型 {text}", self.source, line)
        return normalized

    def _parse_simple(
        self, line: _Line, top_level: bool, loop_depth: int, in_function: bool
    ) -> Statement:
        keyword, remainder = _head(line.text)
        location = _location(self.source, line.number)
        assignment = self._match_assignment(line.text)
        if assignment is not None:
            name, operator, expression_text = assignment
            expression = parse_expression(expression_text, self.source, line.number)
            return Assignment(location, name, operator, expression, name.startswith("_"))
        if line.text.isdigit():
            return Wait(location, Literal(location, int(line.text)), True)
        if keyword == "WAIT":
            duration = Literal(location, 50) if not remainder else parse_expression(remainder, self.source, line.number)
            return Wait(location, duration)
        if keyword in {"PRINT", "ALERT"}:
            return CallStatement(location, keyword, (self._parse_output_expression(remainder, line.number),))
        if keyword in _BUTTONS:
            if not remainder:
                return ButtonAction(location, keyword, "click", Literal(location, 50))
            state = remainder.upper()
            if state in {"DOWN", "UP"}:
                return ButtonAction(location, keyword, state.lower())
            return ButtonAction(location, keyword, "click", parse_expression(remainder, self.source, line.number))
        if keyword in {"LS", "RS"}:
            return self._parse_stick(keyword, remainder, line.number)
        if keyword == "BREAK":
            if loop_depth == 0:
                _fail("BREAK 只能在循环中使用", self.source, line.number)
            if not remainder:
                level = 1
            elif remainder.isdigit():
                level = int(remainder)
            else:
                _fail("BREAK 层级必须是整数", self.source, line.number)
            if level < 1 or level > 3 or level > loop_depth:
                _fail("BREAK 层级超出当前循环深度（最大 3）", self.source, line.number)
            return BreakStatement(location, level)
        if keyword == "CONTINUE":
            if loop_depth == 0:
                _fail("CONTINUE 只能在循环中使用", self.source, line.number)
            if remainder:
                _fail("CONTINUE 不接受参数", self.source, line.number)
            return ContinueStatement(location)
        if keyword == "RETURN":
            if not in_function:
                _fail("RETURN 只能在函数中使用", self.source, line.number)
            expression = parse_expression(remainder, self.source, line.number) if remainder else None
            return ReturnStatement(location, expression)
        if keyword == "IMPORT":
            if not top_level:
                _fail("IMPORT 必须在顶层", self.source, line.number)
            expression = parse_expression(remainder, self.source, line.number)
            if not isinstance(expression, Literal) or not isinstance(expression.value, str):
                _fail("IMPORT 后必须是库文件名字符串", self.source, line.number)
            return ImportStatement(location, expression.value)
        if keyword == "EXTERN":
            if not top_level:
                _fail("EXTERN 声明必须在顶层", self.source, line.number)
            return self._parse_extern(remainder, line.number)
        if keyword == "CALL":
            if not remainder:
                _fail("CALL 后缺少函数名", self.source, line.number)
            return self._parse_call_statement(remainder, line.number, explicit=True)
        return self._parse_call_statement(line.text, line.number, explicit=False)

    def _match_assignment(self, text: str) -> tuple[str, str, str] | None:
        name_match = re.match(r"(\$\$?\w+|_\w+)\s*", text)
        if name_match is None:
            return None
        offset = name_match.end()
        operator = next((item for item in _ASSIGNMENT_OPERATORS if text.startswith(item, offset)), None)
        if operator is None:
            return None
        expression = text[offset + len(operator) :].strip()
        if not expression:
            _fail("赋值语句缺少表达式", self.source, self.lines[self.position].number, offset + len(operator) + 1)
        return name_match.group(1), operator, expression

    def _parse_output_expression(self, text: str, line: int) -> Expression:
        location = _location(self.source, line)
        if not text:
            return Literal(location, "")
        parts = _split_top_level(text, "&")
        expressions: list[Expression] = []
        for part in parts:
            if not part:
                continue
            if _VARIABLE_RE.fullmatch(part) is not None:
                expressions.append(parse_expression(part, self.source, line))
            elif len(part) >= 2 and part[0] in {"'", '"'} and part[-1] == part[0]:
                expressions.append(parse_expression(part, self.source, line))
            else:
                expressions.append(Literal(location, part.strip()))
        if not expressions:
            return Literal(location, "")
        result = expressions[0]
        for expression in expressions[1:]:
            result = Binary(location, result, "&", expression)
        return result

    def _parse_stick(self, side: str, text: str, line: int) -> StickAction:
        location = _location(self.source, line)
        if not text:
            _fail(f"{side} 后缺少方向或 RESET", self.source, line)
        parts = _split_top_level(text)
        if len(parts) > 2:
            _fail("摇杆语句参数过多", self.source, line)
        raw_direction = parts[0].strip()
        upper_direction = raw_direction.upper()
        direction: str | int
        if upper_direction in _DIRECTIONS:
            direction = upper_direction
        else:
            try:
                direction = int(raw_direction)
            except ValueError:
                _fail(f"无效摇杆方向 {raw_direction!r}", self.source, line)
            if direction < 0:
                _fail("摇杆角度不能小于 0", self.source, line)
        duration = parse_expression(parts[1], self.source, line) if len(parts) == 2 else None
        if upper_direction == "RESET" and duration is not None:
            _fail("RESET 不接受持续时间", self.source, line)
        return StickAction(location, side, direction, duration)

    def _parse_extern(self, text: str, line: int) -> ExternDeclaration:
        match = re.fullmatch(
            r"FUNC\s+([^\s(]+)\s*\((.*?)\)\s*:\s*([\w\[\]]+)\s+FROM\s+(['\"])(.*?)\4",
            text,
            flags=re.IGNORECASE,
        )
        if match is None:
            _fail("EXTERN FUNC 声明格式无效", self.source, line)
        parameters = self._parse_parameters(match.group(2), line)
        return_type = self._parse_type(match.group(3), line, allow_void=True)
        return ExternDeclaration(_location(self.source, line), match.group(1), parameters, return_type, match.group(5))

    def _parse_call_statement(self, text: str, line: int, *, explicit: bool) -> CallStatement:
        location = _location(self.source, line)
        name_match = _IDENT_RE.match(text)
        if name_match is None:
            _fail("无法识别的语句", self.source, line)
        name = name_match.group(0)
        remainder = text[name_match.end() :].strip()
        if remainder.startswith("("):
            expression = parse_expression(f"{name}{remainder}", self.source, line)
            if not isinstance(expression, Call):
                _fail("无效函数调用", self.source, line)
            return CallStatement(location, expression.name, expression.arguments)
        if not remainder:
            return CallStatement(location, name, ())
        argument_parts = _split_top_level(remainder)
        if any(not part for part in argument_parts):
            _fail("函数参数不能为空", self.source, line)
        arguments = tuple(parse_expression(part, self.source, line) for part in argument_parts)
        return CallStatement(location, name, arguments)

    def _consume_closer(self, expected: str, message: str) -> None:
        if self.position >= len(self.lines):
            last_line = self.lines[-1].number if self.lines else 1
            _fail(message, self.source, last_line)
        line = self.lines[self.position]
        keyword, remainder = _head(line.text)
        if keyword != expected:
            _fail(message, self.source, line.number)
        if remainder:
            _fail(f"{expected} 后不能有其他内容", self.source, line.number)
        self.position += 1


def parse_text(text: str, source: str = "<script>", *, library: bool = False) -> ParsedUnit:
    return _ProgramParser(text, source, library).parse()


__all__ = ["parse_expression", "parse_text"]
