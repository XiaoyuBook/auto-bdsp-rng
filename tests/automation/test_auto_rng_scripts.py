from __future__ import annotations

from pathlib import Path

import pytest

from auto_bdsp_rng.automation.auto_rng.scripts import (
    AUTO_ADVANCE_PARAMETER,
    AUTO_HIT_PARAMETER,
    AutoScriptError,
    list_auto_scripts,
    prepare_advance_script_text,
    prepare_hit_script_text,
)


def test_list_auto_scripts_reads_supported_files(tmp_path):
    (tmp_path / "BDSP测种.txt").write_text("", encoding="utf-8")
    (tmp_path / "bdsp过帧.txt").write_text("", encoding="utf-8")
    (tmp_path / "谢米.ecs").write_text("", encoding="utf-8")
    (tmp_path / "ignore.md").write_text("", encoding="utf-8")

    scripts = list_auto_scripts(tmp_path)

    assert [path.name for path in scripts] == ["BDSP测种.txt", "bdsp过帧.txt", "谢米.ecs"]


def test_prepare_advance_script_replaces_target_frames_and_preserves_format():
    text = "  _目标帧数 = 填写目标帧数  # 理论剩余帧\r\nA 100\r\n"

    updated = prepare_advance_script_text(text, 900)

    assert updated == "  _目标帧数 = 900  # 理论剩余帧\r\nA 100\r\n"


def test_prepare_hit_script_replaces_flash_frames_and_preserves_format():
    text = "_闪帧 = 填入这里 # 最终剩余帧\nA 100\n"

    updated = prepare_hit_script_text(text, 298)

    assert updated == "_闪帧 = 298 # 最终剩余帧\nA 100\n"


def test_prepare_advance_script_fails_when_target_parameter_missing():
    with pytest.raises(AutoScriptError, match=AUTO_ADVANCE_PARAMETER):
        prepare_advance_script_text("_闪帧 = 100\n", 900)


def test_prepare_hit_script_fails_when_flash_parameter_missing():
    with pytest.raises(AutoScriptError, match=AUTO_HIT_PARAMETER):
        prepare_hit_script_text("_目标帧数 = 900\n", 100)


def test_bundled_shaymin_script_does_not_repeat_flash_frame_wait():
    script_path = Path(__file__).resolve().parents[2] / "script" / "谢米.txt"
    text = script_path.read_text(encoding="utf-8").replace("\r\n", "\n")

    repeated_wait_loop = (
        "FOR\n"
        "        WAIT 1010\n"
        "        $2 -= 1\n"
        "        IF $2 == 0\n"
        "            BREAK\n"
        "        ENDIF\n"
        "    NEXT"
    )

    assert repeated_wait_loop not in text


def test_bundled_hit_scripts_do_not_apply_internal_flash_compensation():
    script_dir = Path(__file__).resolve().parents[2] / "script"

    for script_name in ("谢米.txt", "玫瑰公园.txt"):
        text = (script_dir / script_name).read_text(encoding="utf-8")

        assert "$2 -= 43" not in text
