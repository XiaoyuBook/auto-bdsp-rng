# Release Checklist

Use this checklist for every Windows release.

## Version Policy

- Breaking compatibility for users, settings, data, or automation scripts: increment `major`.
- New backward-compatible user-facing functionality: increment `minor`.
- Bug fixes, performance improvements, and backward-compatible operational changes: increment `patch`.
- A published tag and its GitHub Release are immutable. If code changes after publishing, release the next version instead of moving or recreating the old tag. A failed build with no code change may be rerun for the same tag.

## Release Metadata And Notes

For a target version `X.Y.Z`, update these files in the same release commit:

- `pyproject.toml`
- `src/auto_bdsp_rng/__init__.py`
- `CHANGELOG.md`

`CHANGELOG.md` must start with a non-empty top-level heading exactly matching `# X.Y.Z`. Write its entries as user-facing release notes; GitHub Actions places that section under **本次更新** in the GitHub Release body.

The release workflow refuses to publish when the pushed tag, `pyproject.toml`, package `__version__`, and matching changelog heading disagree.

## Build And Test

```powershell
python -m pytest
.\build_exe.bat
python .\scripts\build_update_package.py
python .\scripts\generate_release_notes.py --tag vX.Y.Z --output .\release\release-notes.md
Get-Content -Raw .\release\release-notes.md
```

Then test:

```powershell
.\dist\auto-bdsp-rng\珍钻复刻自动乱数.exe
```

Confirm:

- `dist/auto-bdsp-rng/珍钻复刻自动乱数.exe` starts the GUI,
- `dist/auto-bdsp-rng/auto-bdsp-rng-updater.exe` exists, `--help` exits successfully, and normal use starts without a console window,
- no console window appears for normal GUI launch,
- scripts and Project_Xs configs can be found,
- `_native` imports,
- native EasyCon can connect to the shared Broker in a test setup, and the packaged Tesseract smoke test succeeds,
- `_internal/easycon_native/x64` and `_internal/easycon_native/Tessdata` contain the bundled native EasyCon OCR runtime,
- `release/auto-bdsp-rng-vX.Y.Z-windows-x64.zip` exists,
- `release/auto-bdsp-rng-vX.Y.Z-windows-x64.manifest.json` exists and lists the complete `dist/auto-bdsp-rng/` tree,
- `release/release-notes.md` shows the intended **本次更新** content.
- “帮助 -> 检查更新…” performs a manual check without any startup network request; source mode only offers the Release page.

To test an incremental artifact locally, download the previous Release manifest and run:

```powershell
python .\scripts\build_update_package.py `
  --previous-manifest .\build\previous\auto-bdsp-rng-vPREVIOUS-windows-x64.manifest.json
```

Confirm that `release/auto-bdsp-rng-vPREVIOUS-to-vX.Y.Z-windows-x64.update.zip` contains `update.json`, includes payloads only for added or changed files, and lists removed files under `remove`. User-edited scripts, logs, Project_Xs configs, and custom eye images must have `preserve_if_modified: true`.

Apply the patch to a copy of the previous extracted package. Confirm successful restart, changed-file hashes, rollback after an injected replacement failure, recovery from a retained `transaction.json`, rollback when the updated executable exits during startup confirmation, and preservation of locally edited user files. An existing `.new-v<version>` sidecar must never be overwritten, and removing packaged files must preserve non-empty user directories while allowing a later release to reuse an empty directory path as a file. Also confirm that cancelling main-window shutdown leaves the helper unapproved and unable to install later, commit failure stops the new process before rollback while holding the installation mutex, and a cleanup failure cannot repeat a completed rollback. Verify that case-only paths and direct cross-version file/directory topology changes are rejected at build time. Keep the full zip in every Release as the bootstrap and repair path.

## Tag

```powershell
git tag -a vX.Y.Z -m "发布 X.Y.Z"
git push --atomic origin main vX.Y.Z
```

## GitHub Release

Pushing `vX.Y.Z` triggers GitHub Actions. The workflow generates the Release body from the matching `CHANGELOG.md` entry and always uploads the complete zip plus its manifest. When the previous Release also has a manifest, it uploads a direct incremental `.update.zip`; otherwise this version is the bootstrap for later incremental updates. Do not manually duplicate or replace the generated update notes.
