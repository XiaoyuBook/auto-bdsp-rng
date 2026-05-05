from __future__ import annotations

from auto_bdsp_rng.automation.easycon.scripts import apply_parameter_values, generate_script_file, parse_script_parameters, scan_builtin_scripts


def test_parse_script_parameters_reads_required_integer_and_comments():
    text = "_闪帧 = 填入这里  # 目标差值\n_等待时间 = 8\nA 100\n"

    parameters = parse_script_parameters(text)

    assert [parameter.name for parameter in parameters] == ["_闪帧", "_等待时间"]
    assert parameters[0].required is True
    assert parameters[0].comment == "目标差值"
    assert parameters[1].is_integer is True


def test_apply_parameter_values_preserves_indent_comment_and_newline():
    text = "  _闪帧 = 填入这里  # 目标差值\r\nA 100\r\n"

    updated = apply_parameter_values(text, {"_闪帧": 123})

    assert updated == "  _闪帧 = 123  # 目标差值\r\nA 100\r\n"


def test_generate_script_file_writes_ecs_without_touching_source(tmp_path):
    generated = generate_script_file("A 100\n", "玫瑰公园.txt", tmp_path / ".generated", task_type="test")

    assert generated.suffix == ".ecs"
    assert generated.parent.name == ".generated"
    assert "玫瑰公园" in generated.name
    assert generated.read_text(encoding="utf-8") == "A 100\n"


def test_scan_builtin_scripts_only_returns_supported_files(tmp_path):
    (tmp_path / "b.ecs").write_text("", encoding="utf-8")
    (tmp_path / "a.txt").write_text("", encoding="utf-8")
    (tmp_path / "ignore.md").write_text("", encoding="utf-8")

    assert [path.name for path in scan_builtin_scripts(tmp_path)] == ["a.txt", "b.ecs"]
