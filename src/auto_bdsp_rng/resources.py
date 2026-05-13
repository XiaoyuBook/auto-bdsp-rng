from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_base_dir() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def package_base_dir() -> Path:
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass).resolve()
    return app_base_dir()


def resource_path(*parts: str | os.PathLike[str]) -> Path:
    app_candidate = app_base_dir().joinpath(*parts)
    if app_candidate.exists():
        return app_candidate
    return package_base_dir().joinpath(*parts)


def app_path(*parts: str | os.PathLike[str]) -> Path:
    return app_base_dir().joinpath(*parts)


def writable_app_data_dir(*parts: str | os.PathLike[str]) -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    path = root / "auto_bdsp_rng"
    if parts:
        path = path.joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


def bundled_easycon_bridge_path() -> Path:
    return app_path("bridge", "EasyConBridge", "EasyConBridge.exe")
