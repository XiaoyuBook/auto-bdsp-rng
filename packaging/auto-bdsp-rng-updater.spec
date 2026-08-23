# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


ROOT = Path(SPECPATH).parent
ENTRY = ROOT / "packaging" / "updater_entry.py"


a = Analysis(
    [str(ENTRY)],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[],
    hiddenimports=["auto_bdsp_rng.update_core"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PIL",
        "PySide6",
        "cv2",
        "numpy",
        "paddle",
        "paddleocr",
        "paddlex",
        "pyautogui",
        "scipy",
        "sklearn",
        "win32api",
        "win32con",
        "win32gui",
    ],
    noarchive=False,
    optimize=2,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="auto-bdsp-rng-updater",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="x86_64",
    codesign_identity=None,
    entitlements_file=None,
)
