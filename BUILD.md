# Windows Release Build

This document is for maintainers who need to build the Windows x64 green package for `auto-bdsp-rng v0.0.2`.

## Requirements

- Windows 64-bit
- Python 3.12
- Git
- Visual Studio Build Tools with MSVC C++ compiler
- .NET SDK for the EasyConBridge target framework
- Network access for Python packages and NuGet packages during the first build

## First-Time Setup

Clone the repository with submodules, or let the build script initialize them:

```powershell
git submodule update --init --recursive
```

If EasyConBridge needs an EasyCon source checkout outside `third_party/EasyCon`, set:

```powershell
$env:EASYCON_SOURCE_ROOT = 'D:\path\to\EasyCon'
```

## One-Command Build

```powershell
.\build_exe.bat
```

The script will:

1. verify Windows x64 and Python 3.12,
2. initialize git submodules,
3. create or reuse `.venv`,
4. install `.[dev]`, PyInstaller, and Pillow,
5. build the pybind11 native extension,
6. run a lightweight version check,
7. generate `docs/assets/app-icon.ico` from `docs/assets/app-icon.png`,
8. run PyInstaller with `packaging/auto-bdsp-rng.spec`,
9. try to publish EasyConBridge for `win-x64`,
10. write `dist/auto-bdsp-rng/README.txt`,
11. create `release/auto-bdsp-rng-v0.0.2-windows-x64.zip`.

## Clean Build Outputs

```powershell
.\build_exe.bat clean
```

or:

```powershell
python .\scripts\build_exe.py --clean
```

This removes only `build/`, `dist/`, and `release/`.

## Output

- onedir app: `dist/auto-bdsp-rng/`
- executable: `dist/auto-bdsp-rng/auto-bdsp-rng.exe`
- release zip: `release/auto-bdsp-rng-v0.0.2-windows-x64.zip`

The package is intentionally onedir, not onefile. Users must keep `_internal`, `script`, `bridge`, `docs`, and other sibling directories beside the exe.

## EasyConBridge

The build script publishes `bridge/EasyConBridge/EasyConBridge.csproj` with:

```powershell
dotnet publish .\bridge\EasyConBridge\EasyConBridge.csproj -c Release -r win-x64 --self-contained true -p:PublishSingleFile=true
```

It uses `third_party/EasyCon` by default. If EasyCon is elsewhere, set `EASYCON_SOURCE_ROOT`. If the EasyCon source or `dotnet` is missing, the script skips Bridge publishing and prints a clear message. In that case, the GUI can still start, but Bridge mode will require the user to provide a working EasyConBridge or use the CLI backend with `ezcon.exe`.

## OCR

The default package is the base build. It excludes `paddlepaddle` and `paddleocr` from PyInstaller so the GUI and core workflows are not blocked by OCR. OCR-dependent features should show a friendly UI or log message when OCR packages are unavailable.

## Troubleshooting

- PySide6 platform plugin not found: rebuild with the provided spec; it collects PySide6 plugins including `platforms`, `styles`, and `imageformats`.
- OpenCV DLL not found: verify `opencv-python` installed inside `.venv`, then rebuild with `.\build_exe.bat`.
- pywin32 DLL not found: reinstall dependencies in `.venv` and rebuild.
- `_native.pyd` not found: install Visual Studio Build Tools with MSVC, then rerun `.\build_exe.bat`.
- MSVC compile failed: confirm Python 3.12 x64 and the C++ desktop workload are installed.
- `.NET publish` failed: confirm .NET SDK can build the EasyCon target and `EASYCON_SOURCE_ROOT` points to a checkout with `src/EasyCon.Core/EasyCon.Core.csproj`.
- Chinese path or space path resource errors: use the resource helper in `auto_bdsp_rng.resources`; avoid adding new hard-coded cwd-relative paths.
- Windows SmartScreen unknown publisher: this build is unsigned. Users can choose “More info” and “Run anyway” after confirming the Release source.
- Antivirus false positive: confirm the zip is from the official GitHub Release, then submit the file to the vendor as a false positive or add a local exception.
