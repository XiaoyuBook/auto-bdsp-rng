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

### Direct local publication (no GitHub Actions)

Run `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\publish_gitee_release.ps1 -Tag v3.4.0` from a checkout. The wrapper finds the project's Python 3.12 environment, uses the configured GitHub Git credential only for release discovery, and prompts for the Gitee token with hidden input if it is not already in the process environment. Tokens are never saved to disk or printed; previous environment values are restored on exit. It downloads the immutable original incremental assets, checks the tag, reuses already-uploaded attachments, verifies all anonymous downloads, and publishes the release metadata last. It does not register a runner, trigger Actions, rebuild the app, or push code.

Use `-Directory <existing-asset-directory> -UseExistingFiles` to reuse downloaded ZIP/manifest files. Add `-PrepareOnly` to export `gitee-release-body.md` and `gitee-update-metadata.json` without a Gitee token or remote changes. After verifying all attachments, the generated body can also be pasted into the Gitee web release editor. Gitee's update API requires **tag_name, name and body** even when editing an existing release; the publisher supplies all three.

Every push to GitHub `main` runs **Sync Gitee Code**, which pushes the latest `main` to Gitee `main` using the same token configured below. It can also be run manually to retry. Runs are serialized and never force-push: if Gitee has divergent commits, synchronization fails for review rather than overwriting them. Keep GitHub as the primary development repository. Code synchronization is independent of release attachment synchronization and does not build or publish a new version.

Set the GitHub repository Actions secret `GITEE_ACCESS_TOKEN` to a Gitee token with repository (`projects`) access for `shekongsk/auto-bdsp-rng`. After **Build Windows Release** succeeds for a stable tag, the independent **Sync Gitee Release** workflow runs through `workflow_run`. A mirror failure does not change the successful build/publication result. Main-branch builds, failed builds, fork builds, and prerelease tags do not trigger publishing. The publisher checks out trusted `main` for the latest fixes and verifies that the tag commit matches the completed build before mirroring. Use `workflow_run`, since a release created with `GITHUB_TOKEN` does not trigger another workflow through the `release` event.

The workflow mirrors release tags, incremental packages and file manifests to Gitee. Complete archives remain on GitHub. Gitee's documented ordinary-project attachment limit is 100 MB, so attachments are bounded to 90 MiB. Incremental ZIPs over that threshold (up to the updater's 2 GiB safety limit) are split into 8 MiB binary parts instead of being silently excluded. Each part is downloaded anonymously and SHA-256 verified before publishing metadata. The reconstructed original ZIP retains its exact GitHub bytes, size and SHA-256. A failed part or helper upload never publishes ready metadata. Do not edit that metadata manually.

Split releases include `upgrade-from-gitee.ps1`. For an existing client without part support, download that helper from the Gitee release and run `powershell -NoProfile -ExecutionPolicy Bypass -File .\upgrade-from-gitee.ps1 -Tag v3.4.0 -CurrentVersion 3.3.3`. Use the actual installed version; this helper requires a direct incremental ZIP from that version to the requested release. It downloads only Gitee URLs, verifies each part and the complete ZIP, validates the internal versions, and places the ZIP in `%LOCALAPPDATA%\auto_bdsp_rng\updates\downloads\v3.4.0`. Then use the application's ordinary update dialog. Its existing verified-cache path needs no GitHub download and its original installer still handles preservation and rollback. The helper never modifies installed application files. Newly built clients support the optional `parts` field directly; already-published clients require the helper until they install a client with that support.

If mirroring fails, GitHub remains available. Run **Sync Gitee Release** manually with the existing tag to retry without rebuilding or replacing the GitHub release. Existing Gitee attachments must match; conflicting attachments stop the sync rather than being overwritten. Attachment size limits and anonymous downloads must be verified by the first successful sync.

The publisher requires `curl`. Uploads authenticate in the Authorization header before the multipart body, use HTTP/1.1 and disable `Expect: 100-continue`; verification downloads retain HTTPS-only redirects. Each transfer has a 15-minute total limit. Uploads stop after two minutes below 1 KiB/s; verification downloads stop after one minute below 32 KiB/s. This permits progressing uploads that previously failed below the overly strict 32 KiB/s cutoff. Each attachment gets at most three attempts, checking the remote attachment list before every attempt to recover a lost response. Logs identify the active file and phase, HTTP code, byte counts and elapsed time without credentials, raw responses or URLs. An HTTP 100 response alone is not success. The sync step has a 45-minute limit; retry from `main` to use the latest fixes with immutable original release assets.

The standalone workflow accepts a `runner` label. Use a trusted domestic self-hosted runner if GitHub-hosted cross-border upload throughput is insufficient. Self-hosted runners must have Python 3.12 on PATH before starting the listener; the workflow validates and uses that interpreter instead of installing Python or changing the machine's PowerShell execution policy. GitHub-hosted runners still use `actions/setup-python`. A temporary runner may be registered with `--ephemeral --disableupdate` and a dedicated label, without installing a service; it accepts one sync job and automatically unregisters after completion. Never select a runner shared with untrusted pull-request jobs. No application rebuild is needed to republish verified parts of an existing release.

Starting with v3.3.1, the updater checks Gitee first and falls back to GitHub if discovery fails; failed Gitee patch downloads retry the identical GitHub assets with the same hashes. Older clients must first obtain v3.3.1 through GitHub or manually install its complete archive. Historical incremental chains require mirroring their releases too.
