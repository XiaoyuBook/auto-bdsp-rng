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

`CHANGELOG.md` must start with a non-empty top-level heading exactly matching `# X.Y.Z`. Write its entries as user-facing release notes; GitHub Actions places that section under **本次更新** in the GitHub Release body. Keep previous release entries intact; a new section may also summarize earlier changes when cumulative notes are needed.

Before the new package is published, identify features documented from `main` as pending release and keep the README download section pointing to the latest published package. Prefer `/releases/latest` and the version displayed in its Assets to avoid advertising an archive before it exists. Update any fixed download/version references when the new package is published.

Review every commit from the latest published tag through the release commit. Summarize the final user-visible behavior in the new changelog section, including fixes already committed to `main` but absent from the last package; omit abandoned approaches and unrelated local drafts.

Also review `README.md`, `docs/USAGE.md`, `docs/DEVELOPMENT.md`, `BUILD.md`, and this checklist for changed entry points, defaults, and operational behavior. Refresh the README screenshot when the workspace layout changes and update `docs/assets/screenshots/README.md` with the actual version and capture conditions. Historical changelog entries and dated design evidence describe their original versions and should not be rewritten as current behavior.

The release workflow refuses to publish when the pushed tag, `pyproject.toml`, package `__version__`, and matching changelog heading disagree.

## v3.4.0 Release Scope

The previous published release is **v3.3.3** (`5af44d2`, published 2026-10-03).
This release reviews all **37 commits** in `v3.3.3..08cd090`, followed by the
v3.4.0 metadata and documentation commit. The user-facing notes live only in
the [3.4.0 changelog section](CHANGELOG.md#340); the workflow generates the
GitHub Release body from that section.

This is a minor release for the new starter automation and workspace behavior.
Review these release-specific cases in addition to the general checks below:

- Exercise all three starter species, built-in script/config binding, ROI/threshold-only editing, existing delay preservation, the 66-frame initial preset, the strict delay `< 78` limit, and the separate 5-second shiny threshold. Verify successful reverse lookup, missed targets, unknown shiny checks, and user stop; missed targets must not select a Pokemon or run reverse lookup.
- Check all six workspace tabs, inline Seed configuration, the manual tools entry, standalone OCR settings, global controller menu, script error navigation, and direct welcome-page entry. The removed RNG guide must not restart from old settings; the QQ tutorial must still work.
- Check static/TID pagination, whole-result sorting/search, target selection, cancellation, and clipboard/CSV/TXT output. Copy/export must include all applicable results, preserve old content on preparation cancellation, and complete a save already in progress before shutdown.
- Check new-round candidate refresh, locked-target snapshots, separate search/reverse counts, and historical round log context. Verify 100-row history pages and full export before session history is released on exit.
- Check slow device enumeration, connect/cancel/disconnect, source switching, first-frame cancellation, and shutdown with real capture/serial hardware. Confirm stopped calibration does not retry and old frames/OCR results cannot replace the new source.
- Run the packaged Chinese/space-path image and real OCR probes, including a Chinese user profile and unwritable cache fallback. Confirm both bundled starter scripts and the Project_Xs template exist in the frozen package.
- Apply the v3.3.3-to-v3.4.0 update to a disposable copy with modified scripts, Project_Xs configs, and custom eye images. Existing files must survive; changed defaults use `.new-v3.4.0` sidecars. Export needed session history before closing the old app.

Expected artifacts are `auto-bdsp-rng-v3.4.0-windows-x64.zip`,
`auto-bdsp-rng-v3.4.0-windows-x64.manifest.json`, and, when the v3.3.3 manifest
is available, `auto-bdsp-rng-v3.3.3-to-v3.4.0-windows-x64.update.zip`.
Automated tests and offscreen screenshots do not establish real-hardware or
frozen-package validation; record those results from their actual runs.

Local preparation verified on 2026-10-12: the complete pytest suite finished
with **2,374 passed, 1 skipped, 0 failed**. The skipped sidecar-path case requires
Windows symbolic-link creation privileges (`WinError 1314`); the run also
reported existing Qt `invalidateFilter()` deprecation warnings. The CLI reports
3.4.0, the release-notes generator accepts `v3.4.0`, and UTF-8, local document
links, and Git whitespace checks pass. The README screenshot was rendered and
inspected at 1440 × 960 logical pixels / DPR 1.25 with isolated settings and
no connected hardware. Frozen-package builds, real-hardware checks, and an
actual v3.3.3 package upgrade remain to be performed; local preparation does
not publish the GitHub Release.

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
- “帮助 -> 检查更新…” performs a manual check; startup checks follow “帮助 -> 启动时自动检查更新” (enabled by default), show a prompt only for a newer release, and respect the saved opt-out. Source mode only offers the Release page.
- the shared light UI style and bounded chevrons render consistently at 100%, 125%, and 150% scaling; the six tabs, monitor, real-time log, and independent full-log window remain usable at 860×600 and 1150×900, with working pagination, unread counts, per-round log context, and split-button menus,
- both sponsor QR images were restored and verified by the build; private images remain excluded from Git.

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

## Gitee mirror

Every push to GitHub `main` runs **Sync Gitee Code**, which pushes the latest `main` to Gitee `main` using the same token configured below. It can also be run manually to retry. Runs are serialized and never force-push: if Gitee has divergent commits, synchronization fails for review rather than overwriting them. Keep GitHub as the primary development repository. Code synchronization is independent of release attachment synchronization and does not build or publish a new version.

Set the GitHub repository Actions secret `GITEE_ACCESS_TOKEN` to a Gitee token with repository (`projects`) access for `shekongsk/auto-bdsp-rng`. After GitHub publication, the workflow mirrors the release tag, incremental packages and file manifests up to 90 MiB each to Gitee. This is our conservative transfer policy, not a statement of Gitee's quota. Complete archives and larger patches remain on GitHub, and the Gitee release prominently links to the complete GitHub download. Only the selected mirror assets are downloaded by the sync job. It verifies every mirrored anonymous attachment download against the GitHub SHA-256 before publishing the machine-readable update metadata in the release body. Do not edit that metadata manually.

If mirroring fails, GitHub remains available. Run **Sync Gitee Release** manually with the existing tag to retry without rebuilding or replacing the GitHub release. Existing Gitee attachments must match; conflicting attachments stop the sync rather than being overwritten. Attachment size limits and anonymous downloads must be verified by the first successful sync.

The publisher requires `curl`. Each upload or verification download has a 15-minute total limit and stops after a minute below 32 KiB/s. Each attachment gets at most three attempts, checking the remote attachment list before every attempt to recover from an upload whose response was lost. Logs identify the active file and phase immediately. The sync step has a 45-minute limit; retry the standalone workflow from `main` to use the latest publishing fixes while keeping the original release assets and tag unchanged.

Starting with v3.3.1, the updater checks Gitee first and falls back to GitHub if discovery fails; failed Gitee patch downloads retry the identical GitHub assets with the same hashes. Older clients must first obtain v3.3.1 through GitHub or manually install its complete archive. Historical incremental chains require mirroring their releases too.
