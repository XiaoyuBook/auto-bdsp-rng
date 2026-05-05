from __future__ import annotations

import os

from auto_bdsp_rng.automation.easycon.scripts import (
    apply_parameter_values,
    detect_newline_style,
    generate_script_file,
    parse_script_parameters,
    prune_generated_scripts,
    scan_builtin_scripts,
)


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


def test_generate_script_file_preserves_requested_newline_style(tmp_path):
    generated = generate_script_file("A 100\nB 100\n", "sample.txt", tmp_path / ".generated", newline="\r\n")

    assert generated.read_bytes() == b"A 100\r\nB 100\r\n"


def test_detect_newline_style_prefers_existing_majority():
    assert detect_newline_style("A\r\nB\r\n") == "\r\n"
    assert detect_newline_style("A\nB\n") == "\n"


def test_prune_generated_scripts_keeps_newest_files(tmp_path):
    generated_dir = tmp_path / ".generated"
    generated_dir.mkdir()
    old = generated_dir / "old.ecs"
    middle = generated_dir / "middle.ecs"
    new = generated_dir / "new.ecs"
    for index, path in enumerate((old, middle, new), start=1):
        path.write_text(str(index), encoding="utf-8")
        os.utime(path, (index, index))

    removed = prune_generated_scripts(generated_dir, keep=2)

    assert [path.name for path in removed] == ["old.ecs"]
    assert old.exists() is False
    assert middle.exists() is True
    assert new.exists() is True


def test_scan_builtin_scripts_only_returns_supported_files(tmp_path):
    (tmp_path / "b.ecs").write_text("", encoding="utf-8")
    (tmp_path / "a.txt").write_text("", encoding="utf-8")
    (tmp_path / "ignore.md").write_text("", encoding="utf-8")

    assert [path.name for path in scan_builtin_scripts(tmp_path)] == ["a.txt", "b.ecs"]
