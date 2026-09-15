from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
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
            if level == "beginner" and not _unfinished_guide(settings.get("guide_progress")):
                settings["guide_progress"] = _new_guide_progress()
        save_settings(settings, path)
    return level


def _new_guide_progress() -> dict[str, Any]:
    return {"version": 1, "session_id": uuid.uuid4().hex, "step": "target_selection", "status": "in_progress"}


GUIDE_STEPS = (
    "target_selection", "search_range", "delay_strategy", "max_wait",
    "shiny_threshold", "sync", "auto_reverse", "correction_strategy",
    "save_config", "task_configured", "connect_devices", "devices_connected",
)


def _unfinished_guide(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and type(value.get("version")) is int and value["version"] == 1
        and isinstance(value.get("session_id"), str) and bool(value["session_id"])
        and value.get("step") in GUIDE_STEPS
        and value.get("status") == "in_progress"
    )


def get_guide_progress(path: Path | None = None) -> dict[str, Any] | None:
    value = load_settings(path).get("guide_progress")
    return dict(value) if _unfinished_guide(value) else None


def start_guide_progress(path: Path | None = None) -> dict[str, Any]:
    with _SETTINGS_LOCK:
        settings = load_settings(path)
        progress = _new_guide_progress()
        settings["guide_progress"] = progress
        save_settings(settings, path)
    return dict(progress)


def advance_guide_progress(step: str, detail: str = "", path: Path | None = None) -> dict[str, Any]:
    """Save a guide position without replacing its session or other settings."""
    if step not in GUIDE_STEPS:
        raise ValueError("unknown guide step")
    with _SETTINGS_LOCK:
        settings = load_settings(path)
        previous = settings.get("guide_progress")
        if not _unfinished_guide(previous):
            raise ValueError("no unfinished guide")
        progress = dict(previous, step=step, detail=detail)
        settings["guide_progress"] = progress
        save_settings(settings, path)
    return dict(progress)


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
