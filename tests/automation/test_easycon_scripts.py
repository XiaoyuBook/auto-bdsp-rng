from __future__ import annotations

import pytest

from auto_bdsp_rng.automation.easycon.scripts import (
    apply_parameter_values,
    create_temporary_cli_script,
    detect_newline_style,
    discard_legacy_generated_snapshots,
    parse_script_parameters,
    remove_temporary_cli_script,
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


def test_temporary_cli_script_uses_txt_and_can_be_removed(tmp_path):
    temporary = create_temporary_cli_script("A 100\n", temp_dir=tmp_path)

    assert temporary.parent == tmp_path
    assert temporary.name.startswith("auto-bdsp-rng-easycon-")
    assert temporary.suffix == ".txt"
    assert temporary.read_text(encoding="utf-8") == "A 100\n"

    remove_temporary_cli_script(temporary)

    assert temporary.exists() is False


def test_temporary_cli_script_preserves_requested_newline_style(tmp_path):
    temporary = create_temporary_cli_script(
        "A 100\nB 100\n",
        newline="\r\n",
        temp_dir=tmp_path,
    )
    try:
        assert temporary.read_bytes() == b"A 100\r\nB 100\r\n"
    finally:
        remove_temporary_cli_script(temporary)


def test_detect_newline_style_prefers_existing_majority():
    assert detect_newline_style("A\r\nB\r\n") == "\r\n"
    assert detect_newline_style("A\nB\n") == "\n"


def test_discard_legacy_generated_snapshots_removes_only_known_snapshot_layout(tmp_path):
    generated_dir = tmp_path / ".generated"
    generated_dir.mkdir()
    first = generated_dir / "玫瑰公园_20260525_103655.ecs"
    second = generated_dir / "bdsp过帧_20260522_224318_auto_advance.ecs"
    first.write_text("A 100\n", encoding="utf-8")
    second.write_text("B 100\n", encoding="utf-8")

    removed = discard_legacy_generated_snapshots(tmp_path)

    assert removed == 2
    assert generated_dir.exists() is False


def test_discard_legacy_generated_snapshots_preserves_directory_with_unknown_content(tmp_path):
    generated_dir = tmp_path / ".generated"
    generated_dir.mkdir()
    snapshot = generated_dir / "玫瑰公园_20260525_103655.ecs"
    unknown = generated_dir / "手动保存.ecs"
    snapshot.write_text("A 100\n", encoding="utf-8")
    unknown.write_text("B 100\n", encoding="utf-8")

    with pytest.raises(OSError, match="未知内容"):
        discard_legacy_generated_snapshots(tmp_path)

    assert snapshot.exists() is True
    assert unknown.exists() is True


def test_scan_builtin_scripts_only_returns_supported_files(tmp_path):
    (tmp_path / "b.ecs").write_text("", encoding="utf-8")
    (tmp_path / "a.txt").write_text("", encoding="utf-8")
    (tmp_path / "ignore.md").write_text("", encoding="utf-8")

    assert [path.name for path in scan_builtin_scripts(tmp_path)] == ["a.txt", "b.ecs"]
