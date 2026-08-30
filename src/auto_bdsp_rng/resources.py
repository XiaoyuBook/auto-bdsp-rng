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
    if parts and os.fspath(parts[0]).casefold() == "script":
        return app_candidate
    if app_candidate.exists():
        return app_candidate
    return package_base_dir().joinpath(*parts)


def app_icon_path() -> Path:
    return resource_path("docs", "assets", "app-icon.png")


def app_path(*parts: str | os.PathLike[str]) -> Path:
    return app_base_dir().joinpath(*parts)


def script_directory() -> Path:
    return app_path("script")


def remap_legacy_script_path(
    path: str | os.PathLike[str],
    *,
    script_dir: Path | None = None,
) -> Path:
    original = Path(path)
    parts = os.fspath(path).replace("\\", "/").split("/")
    folded_parts = [part.casefold() for part in parts]

    legacy_index: int | None = None
    for index in range(len(parts) - 1):
        if folded_parts[index : index + 2] == ["_internal", "script"]:
            legacy_index = index

    if legacy_index is None:
        return original

    relative_parts = parts[legacy_index + 2 :]
    if ".." in relative_parts or any(":" in part for part in relative_parts):
        return original

    safe_parts = [part for part in relative_parts if part not in {"", "."}]
    target_dir = script_directory() if script_dir is None else Path(script_dir)
    expected_legacy = target_dir.parent.joinpath("_internal", "script", *safe_parts)
    try:
        original.lstat()
    except FileNotFoundError:
        original_exists = False
    except OSError:
        return original
    else:
        original_exists = True
    if original_exists:
        try:
            is_current_legacy = (
                original.resolve(strict=False) == expected_legacy.resolve(strict=False)
            )
        except OSError:
            return original
        if not is_current_legacy:
            return original
    return target_dir.joinpath(*safe_parts)


def writable_app_data_dir(*parts: str | os.PathLike[str]) -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    path = root / "auto_bdsp_rng"
    if parts:
        path = path.joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


def bundled_easycon_bridge_path() -> Path:
    return app_path("bridge", "EasyConBridge", "EasyConBridge.exe")
