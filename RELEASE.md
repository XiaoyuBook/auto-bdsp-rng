# Release Checklist

Use this checklist for `v1.0.0` and future Windows releases.

## Version

Update the version in:

- `pyproject.toml`
- `src/auto_bdsp_rng/__init__.py`
- release docs and build script constants when the zip name changes

For this release, the version is `v1.0.0`.

## Build And Test

```powershell
python -m pytest
.\build_exe.bat
```

Then test:

```powershell
.\dist\auto-bdsp-rng\auto-bdsp-rng.exe
```

Confirm:

- `dist/auto-bdsp-rng/auto-bdsp-rng.exe` starts the GUI,
- no console window appears for normal GUI launch,
- scripts and Project_Xs configs can be found,
- `_native` imports,
- EasyConBridge is present if it was publishable,
- `release/auto-bdsp-rng-v1.0.0-windows-x64.zip` exists.

## Tag

```powershell
git tag v1.0.0
git push origin v1.0.0
```

## GitHub Release

Create a new GitHub Release:

- tag: `v1.0.0`
- title: `auto-bdsp-rng v1.0.0`
- asset: `auto-bdsp-rng-v1.0.0-windows-x64.zip`

Do not tell normal users to download the Source code zip.

## Release Notes Template

```markdown
## auto-bdsp-rng v1.0.0

### 下载

请下载 `auto-bdsp-rng-v1.0.0-windows-x64.zip`，不要下载 GitHub 自动生成的 Source code zip。

### 使用

解压 zip，进入 `auto-bdsp-rng` 文件夹，双击 `auto-bdsp-rng.exe`。

### 说明

- Windows x64 绿色版，目标电脑不需要安装 Python。
- 首次启动可能较慢。
- 需要保留 exe 旁边的 `_internal`、`script`、`bridge` 等目录。
- 自动乱数流程仍需要游戏窗口/采集环境、串口/驱动、EasyCon 或兼容后端。
- 基础版不包含 paddle OCR 依赖。
```

