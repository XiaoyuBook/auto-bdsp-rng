from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path


APP_NAME = "auto-bdsp-rng"
ROOT = Path(__file__).resolve().parents[1]
PROJECT_VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
VERSION = f"v{PROJECT_VERSION}"
ZIP_NAME = f"{APP_NAME}-{VERSION}-windows-x64.zip"
VENV = ROOT / ".venv"
DIST_DIR = ROOT / "dist" / APP_NAME
RELEASE_DIR = ROOT / "release"
ZIP_PATH = RELEASE_DIR / ZIP_NAME
SPEC_PATH = ROOT / "packaging" / f"{APP_NAME}.spec"
ICON_PNG = ROOT / "docs" / "assets" / "app-icon.png"
ICON_ICO = ROOT / "docs" / "assets" / "app-icon.ico"
BRIDGE_PROJECT = ROOT / "bridge" / "EasyConBridge" / "EasyConBridge.csproj"
BRIDGE_DIST = DIST_DIR / "bridge" / "EasyConBridge"
PROJECT_XS_ROOT = ROOT / "third_party" / "Project_Xs_CHN"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.clean:
        clean_outputs()
        return 0

    require_windows()
    host_python = Path(sys.executable)
    check_python_version(host_python)
    run(["git", "submodule", "update", "--init", "--recursive"])
    python = ensure_venv(host_python)
    remove_stale_native_extensions()
    install_dependencies(python)
    run([str(python), "-m", "auto_bdsp_rng", "--version"])
    build_icon(python)
    build_pyinstaller(python)
    copy_release_files()
    verify_project_xs_assets()
    build_easycon_bridge()
    create_release_zip()
    print()
    print(f"Build complete: {DIST_DIR}")
    print(f"Release zip: {ZIP_PATH}")
    return 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the Windows x64 onedir release package.")
    parser.add_argument("command", nargs="?", choices=["clean"], help="Use 'clean' to delete build outputs.")
    parser.add_argument("--clean", action="store_true", help="Delete build, dist, release, and PyInstaller cache outputs.")
    args = parser.parse_args(argv)
    args.clean = args.clean or args.command == "clean"
    return args


def require_windows() -> None:
    if platform.system() != "Windows":
        raise SystemExit("This build script must run on Windows x64.")
    if platform.machine().lower() not in {"amd64", "x86_64"}:
        raise SystemExit(f"Windows x64 is required, got {platform.machine()}.")


def check_python_version(python: Path) -> None:
    if sys.version_info[:2] != (3, 12):
        raise SystemExit(f"Python 3.12 is required. Current interpreter is {python} ({platform.python_version()}).")


def ensure_venv(host_python: Path) -> Path:
    if not VENV.exists():
        run([str(host_python), "-m", "venv", str(VENV)])
    python = VENV / "Scripts" / "python.exe"
    if not python.exists():
        raise SystemExit(f"Virtual environment python not found: {python}")
    return python


def install_dependencies(python: Path) -> None:
    run([str(python), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"])
    run([str(python), "-m", "pip", "install", "-e", ".[dev]"], cwd=ROOT)
    run([str(python), "-m", "pip", "install", "pyinstaller>=6,<7", "pillow>=10,<12"])


def remove_stale_native_extensions() -> None:
    native_dir = ROOT / "src" / "auto_bdsp_rng" / "rng_core"
    for path in native_dir.glob("_native*.pyd"):
        try:
            path.unlink()
        except PermissionError as exc:
            raise SystemExit(
                f"Cannot replace {path}. Close any running auto-bdsp-rng GUI or Python process and retry."
            ) from exc


def build_icon(python: Path) -> None:
    if not ICON_PNG.exists():
        raise SystemExit(f"Icon source not found: {ICON_PNG}")
    if ICON_ICO.exists() and ICON_ICO.stat().st_mtime >= ICON_PNG.stat().st_mtime:
        return
    code = (
        "from pathlib import Path\n"
        "from PIL import Image\n"
        f"png = Path(r'{ICON_PNG}')\n"
        f"ico = Path(r'{ICON_ICO}')\n"
        "sizes = [(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)]\n"
        "image = Image.open(png).convert('RGBA')\n"
        "image.save(ico, format='ICO', sizes=sizes)\n"
    )
    run([str(python), "-c", code])


def build_pyinstaller(python: Path) -> None:
    run([str(python), "-m", "PyInstaller", "--noconfirm", "--clean", str(SPEC_PATH)], cwd=ROOT)
    exe = DIST_DIR / f"{APP_NAME}.exe"
    if not exe.exists():
        raise SystemExit(f"PyInstaller did not create {exe}")


def build_easycon_bridge() -> None:
    source_root = Path(os.environ.get("EASYCON_SOURCE_ROOT", ROOT / "third_party" / "EasyCon"))
    required = source_root / "src" / "EasyCon.Core" / "EasyCon.Core.csproj"
    if not BRIDGE_PROJECT.exists():
        print(f"EasyConBridge project not found, skipping: {BRIDGE_PROJECT}")
        return
    if not required.exists():
        print(f"EasyCon source not found, skipping bridge publish: {required}")
        print("Set EASYCON_SOURCE_ROOT to publish EasyConBridge.")
        return
    dotnet = shutil.which("dotnet")
    if not dotnet:
        print("dotnet was not found, skipping EasyConBridge publish.")
        return
    publish_dir = ROOT / "build" / "EasyConBridge" / "publish"
    if publish_dir.exists():
        shutil.rmtree(publish_dir)
    run([
        dotnet,
        "publish",
        str(BRIDGE_PROJECT),
        "-c",
        "Release",
        "-r",
        "win-x64",
        "--self-contained",
        "true",
        "-p:PublishSingleFile=true",
        f"-p:EasyConSourceRoot={source_root}",
        "-o",
        str(publish_dir),
    ])
    copy_tree(publish_dir, BRIDGE_DIST)
    if not (BRIDGE_DIST / "EasyConBridge.exe").exists():
        raise SystemExit("EasyConBridge publish finished but EasyConBridge.exe was not copied.")


def copy_release_files() -> None:
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    copy_optional_tree(ROOT / "script", DIST_DIR / "script")
    copy_optional_tree(ROOT / "docs" / "assets", DIST_DIR / "docs" / "assets")
    copy_optional_tree(PROJECT_XS_ROOT / "configs", DIST_DIR / "third_party" / "Project_Xs_CHN" / "configs")
    copy_optional_tree(PROJECT_XS_ROOT / "images", DIST_DIR / "third_party" / "Project_Xs_CHN" / "images")
    write_user_readme(DIST_DIR / "README.txt")
    license_source = ROOT / "LICENSE"
    license_txt_source = ROOT / "LICENSE.txt"
    if license_source.exists():
        shutil.copy2(license_source, DIST_DIR / "LICENSE")
    elif license_txt_source.exists():
        shutil.copy2(license_txt_source, DIST_DIR / "LICENSE.txt")
    else:
        (DIST_DIR / "LICENSE.txt").write_text("auto-bdsp-rng is licensed as GPL-3.0-or-later.\n", encoding="utf-8")


def write_user_readme(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                f"auto-bdsp-rng {VERSION}",
                "",
                "启动方式：双击 auto-bdsp-rng.exe。",
                "",
                "请不要只复制 exe。必须保留 auto-bdsp-rng.exe 旁边的 _internal、script、bridge、docs 等目录。",
                f"普通用户不要下载 GitHub 绿色 Code 按钮里的 Source code zip，请下载 Release 里的 {ZIP_NAME}。",
                "首次启动可能较慢，这是正常现象。",
                "",
                "Windows SmartScreen 如果提示未知发布者，可以点击“更多信息”，确认来源是本项目 Release 后再选择“仍要运行”。",
                "杀毒软件误报时，请先确认 zip 来自 XiaoyuBook/auto-bdsp-rng 的 GitHub Release，再将解压目录加入信任列表或提交误报反馈。",
                "",
                "运行条件：目标电脑不需要安装 Python。实际乱数流程仍需要 BDSP 游戏窗口或采集画面、对应脚本、串口/单片机/驱动，以及 EasyConBridge 或 ezcon 后端。",
                "基础版不包含 paddlepaddle/paddleocr；OCR 相关功能不可用时会在界面或日志中提示，请不要把 OCR 不可用当作程序启动失败。",
                "",
                "如果软件打不开，请到 GitHub Issues 反馈，并附上 Windows 版本、解压路径、是否有杀毒拦截、以及截图或日志。",
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )


def create_release_zip() -> None:
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in DIST_DIR.rglob("*"):
            if path.is_file():
                archive.write(path, Path(APP_NAME) / path.relative_to(DIST_DIR))
    if not ZIP_PATH.exists():
        raise SystemExit(f"Release zip was not created: {ZIP_PATH}")


def verify_project_xs_assets() -> None:
    config_root = DIST_DIR / "third_party" / "Project_Xs_CHN" / "configs"
    asset_root = DIST_DIR / "third_party" / "Project_Xs_CHN"
    if not config_root.exists():
        raise SystemExit(f"Project_Xs configs were not copied: {config_root}")
    missing: list[Path] = []
    for config_path in config_root.glob("*.json"):
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Cannot parse Project_Xs config: {config_path}") from exc
        image = data.get("image")
        if not isinstance(image, str) or not image.strip():
            continue
        image_path = Path(image)
        if not image_path.is_absolute():
            image_path = asset_root / image.replace("\\", "/").lstrip("./")
        if not image_path.exists():
            missing.append(image_path)
    if missing:
        formatted = "\n".join(f"- {path}" for path in missing)
        raise SystemExit(f"Project_Xs config image assets are missing:\n{formatted}")


def clean_outputs() -> None:
    for path in (ROOT / "build", ROOT / "dist", ROOT / "release"):
        if path.exists():
            shutil.rmtree(path)
            print(f"removed {path}")
    for spec_cache in ROOT.glob("*.spec.tmp"):
        spec_cache.unlink()


def copy_tree(source: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)


def copy_optional_tree(source: Path, target: Path) -> None:
    if source.exists():
        copy_tree(source, target)


def run(command: list[str], cwd: Path = ROOT) -> None:
    print("+ " + " ".join(str(part) for part in command))
    subprocess.run(command, cwd=cwd, check=True)


if __name__ == "__main__":
    raise SystemExit(main())
