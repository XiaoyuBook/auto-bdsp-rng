# Release Checklist

Use this checklist for `v0.0.4` and future Windows releases.

## Version

Update the version in:

- `pyproject.toml`
- `src/auto_bdsp_rng/__init__.py`
- release docs when examples need to mention a concrete version

For this release, the version is `v0.0.4`.

## Build And Test

```powershell
python -m pytest
.\build_exe.bat
```

Then test:

```powershell
.\dist\auto-bdsp-rng\珍钻复刻定点自动乱数.exe
```

Confirm:

- `dist/auto-bdsp-rng/珍钻复刻定点自动乱数.exe` starts the GUI,
- no console window appears for normal GUI launch,
- scripts and Project_Xs configs can be found,
- `_native` imports,
- EasyConBridge is present if it was publishable,
- `release/auto-bdsp-rng-v0.0.4-windows-x64.zip` exists.

## Tag

```powershell
git tag v0.0.4
git push origin v0.0.4
```

## GitHub Release

Create a new GitHub Release:

- tag: `v0.0.4`
- title: `auto-bdsp-rng v0.0.4`
- asset: `auto-bdsp-rng-v0.0.4-windows-x64.zip`

Do not tell normal users to download the Source code zip.

## Release Notes Template

```markdown
## auto-bdsp-rng v0.0.4

### 下载

请下载 `auto-bdsp-rng-v0.0.4-windows-x64.zip`，不要下载 GitHub 自动生成的 Source code zip。

### 使用

解压 zip，进入 `auto-bdsp-rng` 文件夹，双击 `珍钻复刻定点自动乱数.exe`。

### 说明

- Windows x64 绿色版，目标电脑不需要安装 Python。
- 首次启动可能较慢。
- 需要保留 exe 旁边的 `_internal`、`script`、`bridge` 等目录。
- 自动乱数流程仍需要游戏窗口/采集环境、串口/驱动、EasyCon 或兼容后端。
- v0.0.4 Windows zip 已内置 paddle OCR 依赖，首次 OCR 初始化可能较慢。
```
