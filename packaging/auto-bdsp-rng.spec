# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_all,
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
    copy_metadata,
)


ROOT = Path(SPECPATH).parent
ENTRY = ROOT / "packaging" / "entry_gui.py"
ICON = ROOT / "docs" / "assets" / "app-icon.ico"


def tree_datas(source: str, target: str):
    base = ROOT / source
    if not base.exists():
        return []
    ignored = {
        ".git",
        ".venv",
        "build",
        "dist",
        "release",
        "__pycache__",
        ".pytest_cache",
        "bin",
        "obj",
    }
    items = []
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        if any(part in ignored for part in path.relative_to(base).parts):
            continue
        items.append((str(path), str(Path(target) / path.relative_to(base).parent)))
    return items


def keep_runtime_entry(entry):
    source = Path(str(entry[0]))
    parts = {part.lower() for part in source.parts}
    return not ({"tests", "__pycache__", ".pytest_cache"} & parts)


def keep_runtime_import(name: str) -> bool:
    parts = {part.lower() for part in name.split(".")}
    if "tests" in parts or "testing" in parts or "conftest" in parts:
        return False
    return True


datas = []
binaries = []
hiddenimports = [
    "auto_bdsp_rng.rng_core._native",
    "cv2",
    "PIL.Image",
    "pyautogui",
    "win32api",
    "win32con",
    "win32gui",
    "win32process",
    "win32ui",
]

for package in (
    "PySide6",
    "cv2",
    "numpy",
    "PIL",
    "pyautogui",
    "paddle",
    "paddleocr",
    "paddlex",
    "bs4",
    "einops",
    "ftfy",
    "imagesize",
    "jinja2",
    "latex2mathml",
    "lxml",
    "openpyxl",
    "premailer",
    "pyclipper",
    "pypdfium2",
    "bidi",
    "regex",
    "safetensors",
    "sklearn",
    "scipy",
    "sentencepiece",
    "shapely",
    "tiktoken",
    "tokenizers",
):
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += [entry for entry in package_datas if keep_runtime_entry(entry)]
    binaries += [entry for entry in package_binaries if keep_runtime_entry(entry)]
    hiddenimports += [name for name in package_hiddenimports if keep_runtime_import(name)]

for distribution in (
    "paddlepaddle",
    "paddleocr",
    "paddlex",
    "beautifulsoup4",
    "einops",
    "ftfy",
    "imagesize",
    "Jinja2",
    "latex2mathml",
    "lxml",
    "opencv-contrib-python",
    "openpyxl",
    "premailer",
    "pyclipper",
    "pypdfium2",
    "python-bidi",
    "regex",
    "safetensors",
    "scikit-learn",
    "scipy",
    "sentencepiece",
    "shapely",
    "tiktoken",
    "tokenizers",
):
    datas += copy_metadata(distribution)

hiddenimports += collect_submodules("auto_bdsp_rng")
datas += collect_data_files("auto_bdsp_rng", include_py_files=False)
datas += tree_datas("script", "script")
datas += tree_datas("docs/assets", "docs/assets")
datas += tree_datas("private_assets/sponsor", "private_assets/sponsor")
datas += tree_datas("third_party/Project_Xs_CHN", "third_party/Project_Xs_CHN")
datas += tree_datas("third_party/PokeFinder/Core/Resources", "third_party/PokeFinder/Core/Resources")

binaries += collect_dynamic_libs("auto_bdsp_rng")


a = Analysis(
    [str(ENTRY)],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "numpy.tests",
        "pandas.tests",
        "PIL.Tests",
        "paddleocr.tests",
        "paddlex.tests",
        "pytest",
        "scipy.tests",
        "scipy.special.tests",
        "scipy.stats.tests",
        "shapely.tests",
        "sklearn.tests",
        "tkinter",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="珍钻复刻定点自动乱数",
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
    icon=str(ICON),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="auto-bdsp-rng",
    contents_directory="_internal",
)
