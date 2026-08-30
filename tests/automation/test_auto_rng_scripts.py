from __future__ import annotations

from pathlib import Path

import pytest

from auto_bdsp_rng.automation.auto_rng.scripts import (
    AUTO_ADVANCE_PARAMETER,
    AUTO_HIT_PARAMETER,
    AUTO_TELEPORT_SLOT_PARAMETER,
    ROAMER_SPECIES,
    TELEPORT_SLOT_SPECIES,
    AutoScriptError,
    list_auto_scripts,
    prepare_advance_script_text,
    prepare_hit_script_text,
    prepare_teleport_slot_script_text,
    validate_auto_scripts,
)
from auto_bdsp_rng.automation.easycon.scripts import parse_script_parameters


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


def test_teleport_slot_species_are_mesprit_and_cresselia():
    assert ROAMER_SPECIES == TELEPORT_SLOT_SPECIES == {481, 488}


@pytest.mark.parametrize("target_species", [481, 488])
def test_prepare_teleport_slot_replaces_declared_parameter_and_preserves_format(target_species):
    text = f"  {AUTO_TELEPORT_SLOT_PARAMETER} = 1  # 瞬移精灵\r\nA 100\r\n"

    updated = prepare_teleport_slot_script_text(text, 2, target_species=target_species)

    assert updated == f"  {AUTO_TELEPORT_SLOT_PARAMETER} = 2  # 瞬移精灵\r\nA 100\r\n"


@pytest.mark.parametrize("target_species", [None, 492])
def test_prepare_teleport_slot_leaves_non_target_script_unchanged(target_species):
    text = f"{AUTO_TELEPORT_SLOT_PARAMETER} = 1\nA 100\n"

    updated = prepare_teleport_slot_script_text(text, 2, target_species=target_species)

    assert updated == text


def test_prepare_advance_script_fails_when_target_parameter_missing():
    with pytest.raises(AutoScriptError, match=AUTO_ADVANCE_PARAMETER):
        prepare_advance_script_text("_闪帧 = 100\n", 900)


def test_prepare_hit_script_fails_when_flash_parameter_missing():
    with pytest.raises(AutoScriptError, match=AUTO_HIT_PARAMETER):
        prepare_hit_script_text("_目标帧数 = 900\n", 100)


def test_validate_auto_scripts_accepts_hit_script_without_flash_parameter(tmp_path):
    advance_script = tmp_path / "advance.txt"
    hit_script = tmp_path / "hit.txt"
    advance_script.write_text(f"{AUTO_ADVANCE_PARAMETER} = 100\n", encoding="utf-8")
    hit_script.write_text("A 100\n", encoding="utf-8")

    validate_auto_scripts(None, advance_script, hit_script)


def test_validate_auto_scripts_rejects_non_integer_optional_flash_parameter(tmp_path):
    advance_script = tmp_path / "advance.txt"
    hit_script = tmp_path / "hit.txt"
    advance_script.write_text(f"{AUTO_ADVANCE_PARAMETER} = 100\n", encoding="utf-8")
    hit_script.write_text(f"{AUTO_HIT_PARAMETER} = 填入这里\n", encoding="utf-8")

    with pytest.raises(AutoScriptError, match="必须是固定数字"):
        validate_auto_scripts(None, advance_script, hit_script)


@pytest.mark.parametrize("target_species", [481, 488])
def test_validate_auto_scripts_requires_teleport_slot_for_roamers(tmp_path, target_species):
    advance_script = tmp_path / "advance.txt"
    hit_script = tmp_path / "hit.txt"
    advance_script.write_text(f"{AUTO_ADVANCE_PARAMETER} = 100\n", encoding="utf-8")
    hit_script.write_text(f"{AUTO_HIT_PARAMETER} = 60\n", encoding="utf-8")

    with pytest.raises(AutoScriptError, match=AUTO_TELEPORT_SLOT_PARAMETER):
        validate_auto_scripts(None, advance_script, hit_script, target_species=target_species)


@pytest.mark.parametrize("target_species", [481, 488])
def test_validate_auto_scripts_requires_integer_teleport_slot_for_roamers(tmp_path, target_species):
    advance_script = tmp_path / "advance.txt"
    hit_script = tmp_path / "hit.txt"
    advance_script.write_text(f"{AUTO_ADVANCE_PARAMETER} = 100\n", encoding="utf-8")
    hit_script.write_text(
        f"{AUTO_HIT_PARAMETER} = 60\n{AUTO_TELEPORT_SLOT_PARAMETER} = 填入这里\n",
        encoding="utf-8",
    )

    with pytest.raises(AutoScriptError, match="必须是固定数字"):
        validate_auto_scripts(None, advance_script, hit_script, target_species=target_species)


@pytest.mark.parametrize("target_species", [481, 488])
def test_validate_auto_scripts_accepts_integer_teleport_slot_for_roamers(tmp_path, target_species):
    advance_script = tmp_path / "advance.txt"
    hit_script = tmp_path / "hit.txt"
    advance_script.write_text(f"{AUTO_ADVANCE_PARAMETER} = 100\n", encoding="utf-8")
    hit_script.write_text(
        f"{AUTO_HIT_PARAMETER} = 60\n{AUTO_TELEPORT_SLOT_PARAMETER} = 1\n",
        encoding="utf-8",
    )

    validate_auto_scripts(None, advance_script, hit_script, target_species=target_species)


@pytest.mark.parametrize(
    ("script_name", "target_species"),
    [("红圣菇.txt", 481), ("美梦神.txt", 488)],
)
def test_bundled_roamer_scripts_use_runner_wait_mode(script_name, target_species):
    script_dir = Path(__file__).resolve().parents[2] / "script"
    hit_script = script_dir / script_name
    text = hit_script.read_text(encoding="utf-8")

    assert AUTO_HIT_PARAMETER not in {parameter.name for parameter in parse_script_parameters(text)}
    validate_auto_scripts(
        None,
        script_dir / "bdsp过帧.txt",
        hit_script,
        target_species=target_species,
    )


@pytest.mark.parametrize("target_species", [None, 492])
def test_validate_auto_scripts_does_not_require_teleport_slot_for_other_targets(tmp_path, target_species):
    advance_script = tmp_path / "advance.txt"
    hit_script = tmp_path / "hit.txt"
    advance_script.write_text(f"{AUTO_ADVANCE_PARAMETER} = 100\n", encoding="utf-8")
    hit_script.write_text(f"{AUTO_HIT_PARAMETER} = 60\n", encoding="utf-8")

    validate_auto_scripts(None, advance_script, hit_script, target_species=target_species)


def test_validate_auto_scripts_requires_escape_script_when_escape_continue_is_enabled(tmp_path):
    advance_script = tmp_path / "advance.txt"
    hit_script = tmp_path / "hit.txt"
    advance_script.write_text(f"{AUTO_ADVANCE_PARAMETER} = 100\n", encoding="utf-8")
    hit_script.write_text(f"{AUTO_HIT_PARAMETER} = 60\n", encoding="utf-8")

    with pytest.raises(AutoScriptError, match="逃跑脚本"):
        validate_auto_scripts(
            None,
            advance_script,
            hit_script,
            escape_continue=True,
            shiny_threshold_seconds=2.8,
        )


@pytest.mark.parametrize("threshold", [None, 0.0, -1.0, float("nan")])
def test_validate_auto_scripts_requires_positive_shiny_threshold_for_escape_continue(tmp_path, threshold):
    advance_script = tmp_path / "advance.txt"
    hit_script = tmp_path / "hit.txt"
    escape_script = tmp_path / "escape.txt"
    advance_script.write_text(f"{AUTO_ADVANCE_PARAMETER} = 100\n", encoding="utf-8")
    hit_script.write_text(f"{AUTO_HIT_PARAMETER} = 60\n", encoding="utf-8")
    escape_script.write_text("B 100\n", encoding="utf-8")

    with pytest.raises(AutoScriptError, match="闪光阈值"):
        validate_auto_scripts(
            None,
            advance_script,
            hit_script,
            escape_continue=True,
            escape_script_path=escape_script,
            shiny_threshold_seconds=threshold,
        )


def test_validate_auto_scripts_reads_enabled_escape_script_as_utf8(tmp_path):
    advance_script = tmp_path / "advance.txt"
    hit_script = tmp_path / "hit.txt"
    escape_script = tmp_path / "escape.txt"
    advance_script.write_text(f"{AUTO_ADVANCE_PARAMETER} = 100\n", encoding="utf-8")
    hit_script.write_text(f"{AUTO_HIT_PARAMETER} = 60\n", encoding="utf-8")
    escape_script.write_bytes(b"\xff")

    with pytest.raises(AutoScriptError, match="UTF-8"):
        validate_auto_scripts(
            None,
            advance_script,
            hit_script,
            escape_continue=True,
            escape_script_path=escape_script,
            shiny_threshold_seconds=2.8,
        )


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
