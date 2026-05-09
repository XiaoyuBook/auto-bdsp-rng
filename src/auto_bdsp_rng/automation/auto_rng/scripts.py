from __future__ import annotations

from pathlib import Path

from auto_bdsp_rng.automation.easycon.scripts import (
    apply_parameter_values,
    detect_newline_style,
    generate_script_file,
    parse_script_parameters,
    scan_builtin_scripts,
)


AUTO_ADVANCE_PARAMETER = "_目标帧数"
AUTO_HIT_PARAMETER = "_闪帧"
DEFAULT_SEED_SCRIPT_NAME = "BDSP测种.txt"
DEFAULT_ADVANCE_SCRIPT_NAME = "bdsp过帧.txt"


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


def prepare_advance_script(path: Path, frames: int, generated_dir: Path) -> tuple[str, Path]:
    text = path.read_text(encoding="utf-8")
    updated = prepare_advance_script_text(text, frames)
    output = generate_script_file(
        updated,
        path.name,
        generated_dir,
        task_type="auto_advance",
        newline=detect_newline_style(text),
    )
    return updated, output


def prepare_hit_script(path: Path, flash_frames: int, generated_dir: Path) -> tuple[str, Path]:
    text = path.read_text(encoding="utf-8")
    updated = prepare_hit_script_text(text, flash_frames)
    output = generate_script_file(
        updated,
        path.name,
        generated_dir,
        task_type="auto_hit",
        newline=detect_newline_style(text),
    )
    return updated, output


def validate_auto_scripts(
    seed_script_path: Path | None,
    advance_script_path: Path | None,
    hit_script_path: Path | None,
) -> None:
    if seed_script_path is not None:
        _read_utf8(seed_script_path)
    if advance_script_path is None:
        raise AutoScriptError("请选择过帧脚本")
    if hit_script_path is None:
        raise AutoScriptError("请选择撞闪脚本")
    require_parameter(advance_script_path, AUTO_ADVANCE_PARAMETER)
    require_integer_parameter(hit_script_path, AUTO_HIT_PARAMETER)


def require_parameter(path: Path, parameter_name: str) -> None:
    text = _read_utf8(path)
    if parameter_name not in {parameter.name for parameter in parse_script_parameters(text)}:
        raise AutoScriptError(f"{path.name} 缺少必需参数 {parameter_name}")


def require_integer_parameter(path: Path, parameter_name: str) -> None:
    read_integer_parameter(path, parameter_name)


def read_integer_parameter(path: Path, parameter_name: str) -> int:
    text = _read_utf8(path)
    for parameter in parse_script_parameters(text):
        if parameter.name != parameter_name:
            continue
        if not parameter.is_integer:
            raise AutoScriptError(f"{path.name} 必需参数 {parameter_name} 必须是固定数字")
        return int(parameter.value)
    raise AutoScriptError(f"{path.name} 缺少必需参数 {parameter_name}")


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
