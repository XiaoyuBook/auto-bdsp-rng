# Windows Release Build

This document is for maintainers who need to build the Windows x64 green package. The package version is read from `pyproject.toml`.

## Requirements

- Windows 64-bit
- Python 3.12
- Git
- Visual Studio Build Tools with MSVC C++ compiler
- Network access for Python packages during the first build

## First-Time Setup

Clone the repository with submodules, or let the build script initialize them:

```powershell
git submodule update --init --recursive
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
9. build the small onefile updater with `packaging/auto-bdsp-rng-updater.spec`,
10. verify packaged Paddle OCR, native EasyCon Tesseract, and bundled Project_Xs assets,
11. write `dist/auto-bdsp-rng/README.txt`,
12. create `release/auto-bdsp-rng-v<version>-windows-x64.zip`.

The executable build remains responsible for the complete green package. Generate update metadata after it finishes:

```powershell
python .\scripts\build_update_package.py
```

This writes the SHA-256 manifest for the current `dist/auto-bdsp-rng/` tree. To also create an incremental package, provide a manifest downloaded from an older Release; the old full zip is not needed:

```powershell
python .\scripts\build_update_package.py `
  --previous-manifest .\build\previous\auto-bdsp-rng-v2.2.0-windows-x64.manifest.json
```

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
- executable: `dist/auto-bdsp-rng/珍钻复刻自动乱数.exe`
- updater helper: `dist/auto-bdsp-rng/auto-bdsp-rng-updater.exe`
- release zip: `release/auto-bdsp-rng-v<version>-windows-x64.zip`
- release manifest: `release/auto-bdsp-rng-v<version>-windows-x64.manifest.json`
- optional incremental update: `release/auto-bdsp-rng-v<from>-to-v<version>-windows-x64.update.zip`

The package is intentionally onedir, not onefile. Users must keep `_internal`, `script`, `docs`, and other sibling directories beside the exe.

The manifest records every packaged file's relative path, byte size, SHA-256, and whether a locally modified copy must be preserved. Incremental packages contain `update.json` at the zip root and only added or changed files below `payload/`; removed files are metadata entries rather than empty payloads. Paths below `script/`, `logs/`, `third_party/Project_Xs_CHN/configs/`, and `third_party/Project_Xs_CHN/images/custom/` are marked as user-editable. Case-only path changes and cross-version file-to-directory or directory-to-file changes are rejected because the file-level installer cannot apply those topologies safely.

GitHub Actions searches older Releases from newest to oldest for the nearest usable manifest and builds a direct file-level patch. If no older manifest exists, it deliberately publishes only the full zip and current manifest as the one-time bootstrap release. GitHub API or asset download failures fail the build; only a confirmed missing manifest may fall back to an older Release or bootstrap mode. CI also starts the frozen updater with `--help` as a packaging smoke test.

At runtime, the GUI passes each GitHub Release asset SHA-256 to a onefile helper copied under `install_dir/.auto-bdsp-rng-updater/`. The approval file starts in a `pending` state and is atomically changed to `approved` only after the main window has successfully closed; a refused shutdown records `cancelled` and stops the helper. The helper atomically claims that one-time approval, verifies the archives before taking the installation mutex and again inside it, and the core copies each patch into the transaction directory while verifying the official digest a third time. Existing files are backed up with hard links when possible and otherwise copied; replacements use atomic `os.replace()`. Modified user files are preserved, and a new default is written to an unused `.new-v<version>[.<number>]` sidecar. Disk-space checks include full-copy backup, sidecar copy, and rollback requirements. A durable `transaction.json` under `.auto-bdsp-update-*` keeps the rollback map across process termination or power loss; all compensation stays inside the mutex, and a completed rollback is marked before its directory is atomically quarantined as `.auto-bdsp-cleanup-*`. Failed cleanup is retried before a later update without repeating rollback. If recovery actually rolls files back, the current patch chain is stopped and the restored old version is restarted so a new plan can be built from its real version. Backups are committed only after the updated GUI remains running for the startup confirmation window.

## Native EasyCon Runtime

The application packages its Python EasyCon parser/runtime, `pyserial`, and the Tesseract files under `packaging/easycon_native/`. Building and running the product does not require .NET, an EasyCon installation, `EASYCON_ROOT`, EasyConBridge, or `ezcon.exe`. The old Bridge project remains in the repository for compatibility reference only and is not published by `scripts/build_exe.py`.

## OCR

The Windows release package includes `paddlepaddle` and `paddleocr` so OCR shiny checks, stats-page OCR, and notes-page OCR are available from the green zip. This makes the zip larger and can make the first OCR use slower while Paddle initializes its models.

## Troubleshooting

- PySide6 platform plugin not found: rebuild with the provided spec; it collects PySide6 plugins including `platforms`, `styles`, and `imageformats`.
- OpenCV DLL not found: verify `opencv-python` installed inside `.venv`, then rebuild with `.\build_exe.bat`.
- pywin32 DLL not found: reinstall dependencies in `.venv` and rebuild.
- `_native.pyd` not found: install Visual Studio Build Tools with MSVC, then rerun `.\build_exe.bat`.
- MSVC compile failed: confirm Python 3.12 x64 and the C++ desktop workload are installed.
- Native EasyCon OCR smoke failed: verify `packaging/easycon_native/x64/*.dll` and `packaging/easycon_native/Tessdata/chi_sim.traineddata` are present, then rebuild.
- Chinese path or space path resource errors: use the resource helper in `auto_bdsp_rng.resources`; avoid adding new hard-coded cwd-relative paths.
- Windows SmartScreen unknown publisher: this build is unsigned. Users can choose “More info” and “Run anyway” after confirming the Release source.
- Antivirus false positive: confirm the zip is from the official GitHub Release, then submit the file to the vendor as a false positive or add a local exception.
