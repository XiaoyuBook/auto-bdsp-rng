from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from auto_bdsp_rng.resources import writable_app_data_dir


SETTINGS_PATH = writable_app_data_dir("settings") / "config.json"


def load_settings(path: Path | None = None) -> dict[str, Any]:
    path = path or SETTINGS_PATH
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(settings: dict[str, Any], path: Path | None = None) -> Path:
    path = path or SETTINGS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def should_show_startup_notice(path: Path | None = None) -> bool:
    return not bool(load_settings(path).get("startup_notice_acknowledged", False))


def set_startup_notice_acknowledged(acknowledged: bool, path: Path | None = None) -> Path:
    settings = load_settings(path)
    settings["startup_notice_acknowledged"] = bool(acknowledged)
    return save_settings(settings, path)
