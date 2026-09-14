from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Literal, TypeAlias

from auto_bdsp_rng.resources import writable_app_data_dir


SETTINGS_PATH = writable_app_data_dir("settings") / "config.json"
_SETTINGS_LOCK = threading.RLock()

UI_SCALE_AUTO = "auto"
UI_SCALE_MIN = 50
UI_SCALE_MAX = 125
UI_SCALE_STEP = 5
UI_SCALE_VALUES = tuple(range(UI_SCALE_MIN, UI_SCALE_MAX + 1, UI_SCALE_STEP))
UiScale: TypeAlias = Literal["auto"] | int
ExperienceLevel: TypeAlias = Literal["beginner", "expert"]
RngMode: TypeAlias = Literal["standard", "guided"]


def load_settings(path: Path | None = None) -> dict[str, Any]:
    with _SETTINGS_LOCK:
        path = path or SETTINGS_PATH
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}


def save_settings(settings: dict[str, Any], path: Path | None = None) -> Path:
    with _SETTINGS_LOCK:
        path = path or SETTINGS_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(settings, ensure_ascii=False, indent=2)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                prefix=f".{path.name}.",
                suffix=".tmp",
                dir=path.parent,
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
            temporary_path = None
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except OSError:
                    pass
        return path


def should_show_startup_notice(path: Path | None = None) -> bool:
    return not bool(load_settings(path).get("startup_notice_acknowledged", False))


def set_startup_notice_acknowledged(acknowledged: bool, path: Path | None = None) -> Path:
    with _SETTINGS_LOCK:
        settings = load_settings(path)
        settings["startup_notice_acknowledged"] = bool(acknowledged)
        return save_settings(settings, path)


def get_experience_level(path: Path | None = None) -> ExperienceLevel | None:
    """Return the saved first-launch RNG experience selection, if valid."""

    value = load_settings(path).get("experience_level")
    if value in ("beginner", "expert"):
        return value
    return None


def should_show_experience_level(path: Path | None = None) -> bool:
    return get_experience_level(path) is None


def set_experience_level(
    level: ExperienceLevel,
    path: Path | None = None,
    *,
    acknowledge_startup: bool = False,
) -> ExperienceLevel:
    if level not in ("beginner", "expert"):
        raise ValueError("experience level must be 'beginner' or 'expert'")
    with _SETTINGS_LOCK:
        settings = load_settings(path)
        settings["experience_level"] = level
        if acknowledge_startup:
            settings["startup_notice_acknowledged"] = True
            settings["rng_mode"] = "guided" if level == "beginner" else "standard"
        save_settings(settings, path)
    return level


def get_rng_mode(path: Path | None = None) -> RngMode:
    settings = load_settings(path)
    mode = settings.get("rng_mode")
    if mode in ("standard", "guided"):
        return mode
    # Existing users inherit their first-launch choice until they switch modes.
    return "guided" if settings.get("experience_level") == "beginner" else "standard"


def set_rng_mode(mode: RngMode, path: Path | None = None) -> RngMode:
    if mode not in ("standard", "guided"):
        raise ValueError("RNG mode must be 'standard' or 'guided'")
    with _SETTINGS_LOCK:
        settings = load_settings(path)
        settings["rng_mode"] = mode
        save_settings(settings, path)
    return mode


def is_run_log_enabled(path: Path | None = None) -> bool:
    return bool(load_settings(path).get("run_log_enabled", True))


def set_run_log_enabled(enabled: bool, path: Path | None = None) -> bool:
    with _SETTINGS_LOCK:
        settings = load_settings(path)
        actual = bool(enabled)
        settings["run_log_enabled"] = actual
        save_settings(settings, path)
        return actual


def is_auto_update_check_enabled(path: Path | None = None) -> bool:
    value = load_settings(path).get("auto_update_check_enabled", True)
    return value if isinstance(value, bool) else True


def set_auto_update_check_enabled(enabled: bool, path: Path | None = None) -> bool:
    with _SETTINGS_LOCK:
        settings = load_settings(path)
        actual = bool(enabled)
        settings["auto_update_check_enabled"] = actual
        save_settings(settings, path)
        return actual


def normalize_ui_scale(value: object) -> UiScale:
    if value == UI_SCALE_AUTO:
        return UI_SCALE_AUTO
    if isinstance(value, bool) or not isinstance(value, int) or value not in UI_SCALE_VALUES:
        raise ValueError(
            f"ui_scale must be '{UI_SCALE_AUTO}' or a {UI_SCALE_STEP}% step "
            f"from {UI_SCALE_MIN} to {UI_SCALE_MAX}"
        )
    return value


def get_ui_scale(path: Path | None = None) -> UiScale:
    try:
        return normalize_ui_scale(load_settings(path).get("ui_scale", UI_SCALE_AUTO))
    except ValueError:
        return UI_SCALE_AUTO


def set_ui_scale(value: object, path: Path | None = None) -> UiScale:
    actual = normalize_ui_scale(value)
    with _SETTINGS_LOCK:
        settings = load_settings(path)
        settings["ui_scale"] = actual
        save_settings(settings, path)
    return actual
