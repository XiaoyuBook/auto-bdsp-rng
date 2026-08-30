from __future__ import annotations

from pathlib import Path
import threading

import pytest

from auto_bdsp_rng.automation.easycon.native import (
    EasyConScriptEngine,
    ScriptCancelled,
    ScriptCompileError,
    ScriptRuntimeError,
)


ROOT = Path(__file__).resolve().parents[2]


class RecordingWaiter:
    def __init__(self) -> None:
        self.milliseconds: list[int] = []

    def wait(self, milliseconds: int, cancel_event=None) -> None:
        self.milliseconds.append(milliseconds)


class RecordingGamepad:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def click_buttons(self, key: str, duration_ms: int, cancel_event=None) -> None:
        self.events.append(("click", key, duration_ms))

    def press_buttons(self, key: str) -> None:
        self.events.append(("down", key))

    def release_buttons(self, key: str) -> None:
        self.events.append(("up", key))

    def click_stick(self, key: str, x: int, y: int, duration_ms: int, cancel_event=None) -> None:
        self.events.append(("stick-click", key, x, y, duration_ms))

    def set_stick(self, key: str, x: int, y: int) -> None:
        self.events.append(("stick", key, x, y))

    def change_amiibo(self, index: int) -> bool:
        self.events.append(("amiibo", index))
        return True


class RecordingOutput:
    def __init__(self) -> None:
        self.printed: list[tuple[str, bool]] = []
        self.alerted: list[str] = []

    def print(self, message: str, newline: bool) -> None:
        self.printed.append((message, newline))

    def alert(self, message: str) -> None:
        self.alerted.append(message)


@pytest.fixture
def engine() -> EasyConScriptEngine:
    return EasyConScriptEngine()


def test_compiles_every_repository_script(engine: EasyConScriptEngine) -> None:
    paths = sorted((ROOT / "script").glob("*.txt"))

    assert paths
    for path in paths:
        program = engine.load_file(path)
        assert program.source == str(path.resolve())


@pytest.mark.parametrize(
    "relative_path",
    [
        Path("红圣菇.txt"),
        Path("美梦神.txt"),
    ],
)
def test_compiles_roaming_scripts(
    engine: EasyConScriptEngine, relative_path: Path
) -> None:
    path = ROOT / "script" / relative_path

    program = engine.load_file(path)

    assert program.source == str(path.resolve())
    assert program.requires_image_search


def test_compiles_original_easycon_examples(engine: EasyConScriptEngine) -> None:
    examples = ROOT / "third_party" / "EasyCon" / "examples"
    paths = [
        path
        for path in sorted(examples.rglob("*"))
        if path.is_file() and path.suffix.lower() in {".txt", ".ecs"} and path.parent.name != "lib"
    ]

    assert paths
    for path in paths:
        engine.load_file(path)


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("-7 / 3", -2),
        ("7 / -3", -2),
        ("-7 % 3", -1),
        ("5 \\ 2", 3),
        ("-5 \\ 2", -3),
        ("1 + 2 * 3", 7),
        ("8 >> 1 + 1", 5),
        ("1 or @never", True),
    ],
)
def test_expression_semantics(
    engine: EasyConScriptEngine, expression: str, expected: object
) -> None:
    program = engine.compile(f"$result = {expression}")

    assert program.run() == expected


def test_constants_variables_and_augmented_assignment(engine: EasyConScriptEngine) -> None:
    program = engine.compile("_BASE = 5\n$value = _BASE\n$value *= 3\n$value -= 2")

    assert program.run() == 13

    with pytest.raises(ScriptCompileError, match="已经定义"):
        engine.compile("_BASE = 5\n_BASE = 6")
    with pytest.raises(ScriptCompileError, match="找不到变量"):
        engine.compile("$value = $missing + 1")
    with pytest.raises(ScriptCompileError, match="编译期表达式"):
        engine.compile("$value = 1\n_BAD = $value")


def test_nested_control_flow_break_levels_and_continue(engine: EasyConScriptEngine) -> None:
    program = engine.compile(
        """
$sum = 0
FOR $i = 1 TO 4
    FOR $j = 1 TO 4
        IF $j == 2
            CONTINUE
        ENDIF
        IF $i == 3
            BREAK 2
        ENDIF
        $sum += $i * 10 + $j
    NEXT
NEXT
$result = $sum
"""
    )

    assert program.run() == 106


def test_while_elif_and_legacy_single_equal(engine: EasyConScriptEngine) -> None:
    program = engine.compile(
        """
$value = 0
WHILE $value < 3
    $value += 1
END
IF $value = 2
    $result = 1
ELIF $value == 3
    $result = 2
ELSE
    $result = 3
ENDIF
"""
    )

    assert program.run() == 2


def test_user_functions_parameters_returns_and_recursion(engine: EasyConScriptEngine) -> None:
    program = engine.compile(
        """
FUNC fib($n:INT):INT
    IF $n <= 1
        RETURN $n
    ENDIF
    RETURN fib($n - 1) + fib($n - 2)
ENDFUNC
$result = fib(10)
"""
    )

    assert program.run() == 55


def test_string_parameters_and_returns_use_original_implicit_conversion(
    engine: EasyConScriptEngine,
) -> None:
    program = engine.compile(
        """
FUNC echo($value:STRING):STRING
    RETURN $value
ENDFUNC
FUNC answer():STRING
    RETURN 42
ENDFUNC
$result = echo(false) & ":" & answer()
"""
    )

    assert program.run() == "false:42"


def test_unassigned_bound_global_defaults_to_zero_but_local_does_not(
    engine: EasyConScriptEngine,
) -> None:
    global_program = engine.compile(
        """
WHILE false
    $value = 7
END
$result = $value
"""
    )
    assert global_program.run() == 0

    local_program = engine.compile(
        """
FUNC value():INT
    WHILE false
        $local = 7
    END
    RETURN $local
ENDFUNC
$result = value()
"""
    )
    with pytest.raises(ScriptRuntimeError, match="尚未赋值"):
        local_program.run()


def test_arrays_index_slice_append_and_len(engine: EasyConScriptEngine) -> None:
    program = engine.compile(
        "$array = [1, 2, 3]\n$next = APPEND($array, 4)\n$slice = $next[1:3]\n$result = LEN($slice) + $next[3]"
    )

    assert program.run() == 6


def test_gamepad_and_wait_event_sequence(engine: EasyConScriptEngine) -> None:
    gamepad = RecordingGamepad()
    waiter = RecordingWaiter()
    program = engine.compile(
        """
A
B 125
X DOWN
X UP
LS UP
LS RESET
RS 45,25
WAIT 333
AMIIBO 2
"""
    )

    program.run(gamepad=gamepad, waiter=waiter)

    assert gamepad.events == [
        ("click", "A", 50),
        ("click", "B", 125),
        ("down", "X"),
        ("up", "X"),
        ("stick", "LS", 128, 0),
        ("stick", "LS", 128, 128),
        ("stick-click", "RS", 255, 0, 25),
        ("amiibo", 2),
    ]
    assert waiter.milliseconds == [333]


def test_recorded_direction_sequence_compiles_and_replays_releases(engine: EasyConScriptEngine) -> None:
    gamepad = RecordingGamepad()
    waiter = RecordingWaiter()
    program = engine.compile(
        """
RIGHT DOWN
WAIT 200
RIGHT UP
LS RIGHT
WAIT 300
LS RESET
RS RIGHT
WAIT 400
RS RESET
"""
    )

    program.run(gamepad=gamepad, waiter=waiter)

    assert gamepad.events == [
        ("down", "RIGHT"),
        ("up", "RIGHT"),
        ("stick", "LS", 255, 128),
        ("stick", "LS", 128, 128),
        ("stick", "RS", 255, 128),
        ("stick", "RS", 128, 128),
    ]
    assert waiter.milliseconds == [200, 300, 400]


def test_amiibo_only_script_is_gamepad_action_and_invokes_gamepad(engine: EasyConScriptEngine) -> None:
    gamepad = RecordingGamepad()
    program = engine.compile("AMIIBO 4")

    assert program.has_gamepad_actions
    program.run(gamepad=gamepad)
    assert gamepad.events == [("amiibo", 4)]


def test_external_getter_is_resolved_at_evaluation_time(engine: EasyConScriptEngine) -> None:
    calls = 0

    def getter() -> int:
        nonlocal calls
        calls += 1
        return calls * 10

    program = engine.compile("$first = @目标\n$second = @目标\n$result = $first + $second")

    assert program.external_labels == frozenset({"目标"})
    assert program.requires_image_search
    assert program.run(external_getters={"目标": getter}) == 30
    assert calls == 2


def test_print_unquoted_concatenation_and_line_continuation(engine: EasyConScriptEngine) -> None:
    output = RecordingOutput()
    program = engine.compile("$count = 2\nPRINT 已运行 & $count & 次\\\nPRINT 继续")

    program.run(output=output)

    assert output.printed == [("已运行2次", True), ("继续", False)]


def test_lib_auto_load_import_and_scope_isolation(engine: EasyConScriptEngine, tmp_path: Path) -> None:
    library = tmp_path / "lib"
    library.mkdir()
    (library / "math.ecs").write_text(
        "_OFFSET = 4\nFUNC addOffset($value:INT):INT\nRETURN $value + _OFFSET\nENDFUNC\n",
        encoding="utf-8",
    )
    main = tmp_path / "main.ecs"
    main.write_text('IMPORT "math.ecs"\n$result = addOffset(6)\n', encoding="utf-8")

    assert engine.load_file(main).run() == 10

    (library / "bad.ecs").write_text(
        "FUNC leak():INT\nRETURN $mainValue\nENDFUNC\n", encoding="utf-8"
    )
    main.write_text("$mainValue = 3\n$result = addOffset(6)\n", encoding="utf-8")
    with pytest.raises(ScriptCompileError, match="找不到变量"):
        engine.load_file(main)


def test_import_rejects_escape_and_missing_target(engine: EasyConScriptEngine, tmp_path: Path) -> None:
    (tmp_path / "lib").mkdir()
    outside = tmp_path / "outside.ecs"
    outside.write_text("_VALUE = 1", encoding="utf-8")

    with pytest.raises(ScriptCompileError, match="不能离开 lib"):
        engine.compile('IMPORT "../outside.ecs"', script_dir=tmp_path)
    with pytest.raises(ScriptCompileError, match="找不到导入库"):
        engine.compile('IMPORT "missing.ecs"', script_dir=tmp_path)


def test_cancellation_is_checked_in_loops_and_waits(engine: EasyConScriptEngine) -> None:
    already_cancelled = threading.Event()
    already_cancelled.set()
    program = engine.compile("FOR\nNEXT")
    with pytest.raises(ScriptCancelled):
        program.run(cancel_event=already_cancelled)

    cancel_during_wait = threading.Event()

    def cancelling_waiter(milliseconds: int, cancel_event) -> None:
        assert milliseconds == 250
        cancel_during_wait.set()

    wait_program = engine.compile("WAIT 250\nA")
    with pytest.raises(ScriptCancelled):
        wait_program.run(cancel_event=cancel_during_wait, waiter=cancelling_waiter)


def test_compile_and_runtime_errors_include_source_line(engine: EasyConScriptEngine) -> None:
    with pytest.raises(ScriptCompileError) as compile_error:
        engine.compile("$x = 1\n$x = $missing", source="bad.ecs")
    assert "bad.ecs:2:" in str(compile_error.value)

    program = engine.compile("$x = 1\n$result = $x / 0", source="runtime.ecs")
    with pytest.raises(Exception) as runtime_error:
        program.run()
    assert "runtime.ecs:2:" in str(runtime_error.value)
