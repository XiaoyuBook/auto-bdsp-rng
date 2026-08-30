from __future__ import annotations

import re
from pathlib import Path

from auto_bdsp_rng.automation.easycon.scripts import (
    apply_parameter_values,
    parse_script_parameters,
    scan_builtin_scripts,
)


AUTO_ADVANCE_PARAMETER = "_目标帧数"
AUTO_HIT_PARAMETER = "_闪帧"
AUTO_TELEPORT_SLOT_PARAMETER = "_瞬移精灵槽位"
ROAMER_SPECIES = frozenset({481, 488})
TELEPORT_SLOT_SPECIES = ROAMER_SPECIES
DEFAULT_SEED_SCRIPT_NAME = "BDSP测种.txt"
DEFAULT_ADVANCE_SCRIPT_NAME = "bdsp过帧.txt"
DEFAULT_RECORD_SCRIPT_NAME = "录屏.txt"
_ADVANCE_SCRIPT_OFFSET_RE = re.compile(r"_目标帧数\s*-\s*(\d+)")


class AutoScriptError(ValueError):
    pass


def list_auto_scripts(script_dir: Path) -> list[Path]:
    return scan_builtin_scripts(script_dir)


def choose_default_script(scripts: list[Path], preferred_name: str) -> Path | None:
    preferred = preferred_name.casefold()
    for script in scripts:
        if script.name.casefold() == preferred:
            return script
    return scripts[0] if scripts else None


def prepare_advance_script_text(text: str, frames: int) -> str:
    return replace_required_parameter(text, AUTO_ADVANCE_PARAMETER, frames)


def prepare_hit_script_text(text: str, flash_frames: int) -> str:
    return replace_required_parameter(text, AUTO_HIT_PARAMETER, flash_frames)


def prepare_teleport_slot_script_text(text: str, slot: int, *, target_species: int | None) -> str:
    if target_species not in TELEPORT_SLOT_SPECIES:
        return text
    return replace_required_parameter(text, AUTO_TELEPORT_SLOT_PARAMETER, slot)


def validate_auto_scripts(
    seed_script_path: Path | None,
    advance_script_path: Path | None,
    hit_script_path: Path | None,
    *,
    escape_continue: bool = False,
    escape_script_path: Path | None = None,
    shiny_threshold_seconds: float | None = None,
    target_species: int | None = None,
) -> None:
    if seed_script_path is not None:
        _read_utf8(seed_script_path)
    if advance_script_path is None:
        raise AutoScriptError("请选择过帧脚本")
    if hit_script_path is None:
        raise AutoScriptError("请选择撞闪脚本")
    require_parameter(advance_script_path, AUTO_ADVANCE_PARAMETER)
    read_optional_integer_parameter(hit_script_path, AUTO_HIT_PARAMETER)
    if target_species in TELEPORT_SLOT_SPECIES:
        require_integer_parameter(hit_script_path, AUTO_TELEPORT_SLOT_PARAMETER)
    if escape_continue:
        if shiny_threshold_seconds is None or not (shiny_threshold_seconds > 0):
            raise AutoScriptError("启用逃跑续搜后必须将闪光阈值设置为大于 0")
        if escape_script_path is None:
            raise AutoScriptError("启用逃跑续搜后必须选择逃跑脚本")
        _read_utf8(escape_script_path)


def read_advance_script_offset(path: Path) -> int:
    """从过帧脚本中读取 _目标帧数 的内部偏移量（例如 `_目标帧数 - 300` 返回 300）。

    如果找不到偏移量，返回 0。
    """
    text = path.read_text(encoding="utf-8")
    match = _ADVANCE_SCRIPT_OFFSET_RE.search(text)
    if match is None:
        return 0
    return int(match.group(1))


def require_parameter(path: Path, parameter_name: str) -> None:
    text = _read_utf8(path)
    if parameter_name not in {parameter.name for parameter in parse_script_parameters(text)}:
        raise AutoScriptError(f"{path.name} 缺少必需参数 {parameter_name}")


def require_integer_parameter(path: Path, parameter_name: str) -> None:
    read_integer_parameter(path, parameter_name)


def read_integer_parameter(path: Path, parameter_name: str) -> int:
    value = read_optional_integer_parameter(path, parameter_name)
    if value is None:
        raise AutoScriptError(f"{path.name} 缺少必需参数 {parameter_name}")
    return value


def read_optional_integer_parameter(path: Path, parameter_name: str) -> int | None:
    text = _read_utf8(path)
    for parameter in parse_script_parameters(text):
        if parameter.name != parameter_name:
            continue
        if not parameter.is_integer:
            raise AutoScriptError(f"{path.name} 必需参数 {parameter_name} 必须是固定数字")
        return int(parameter.value)
    return None


def replace_required_parameter(text: str, parameter_name: str, value: int) -> str:
    if parameter_name not in {parameter.name for parameter in parse_script_parameters(text)}:
        raise AutoScriptError(f"脚本缺少必需参数 {parameter_name}")
    return apply_parameter_values(text, {parameter_name: int(value)})


def _read_utf8(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise AutoScriptError(f"{path.name} 不是有效的 UTF-8 脚本") from exc
    except OSError as exc:
        raise AutoScriptError(f"无法读取脚本 {path}") from exc
