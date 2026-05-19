# OCR ROI Settings Design

## Goal

Replace the temporary "捕获精灵信息" entry point with an OCR settings workflow that lets users configure precise OCR regions from the existing Seed capture preview. The workflow should reduce OCR work by reading small, user-defined regions for nature, characteristic, and the six stat values.

## Entry Point

The Auto RNG panel button labeled "捕获精灵信息" becomes "OCR设置".

Clicking it opens an independent, non-modal settings window named "OCR区域设置". The window must not block the main window, because users need to keep operating the Seed capture preview while the settings window stays open. The recommended default is "always on top" while remaining non-modal.

## Settings Window Layout

The window contains a compact table with eight rows:

- 性格
- 个性
- HP
- 攻击
- 防御
- 特攻
- 特防
- 速度

Each row shows:

- Item name.
- Saved region as `X, Y, W, H`, or `未设置`.
- Status, such as `已设置` or `未设置`.
- Actions: `框选`, `显示`, `识别`, `重置`.
- Last OCR result, initially `未测试`.

Rows are visually grouped into:

- 笔记页: 性格, 个性.
- 能力页: HP, 攻击, 防御, 特攻, 特防, 速度.

The bottom action row provides `测试当前项`, `测试全部`, `导入默认区域`, and `关闭`.

## Region Selection Flow

When the user clicks `框选` for one row, the main preview enters OCR selection mode for that specific field. The status bar should clearly name the active target, for example `正在框选 OCR 区域：个性`.

Selection uses the existing preview right-drag behavior:

1. The user holds right mouse button on the preview.
2. The user drags a rectangle over the target field.
3. Mouse release opens a confirmation dialog.
4. Confirm saves immediately.
5. Cancel keeps the previous saved region unchanged.

The confirmation dialog shows the field name and selected coordinates:

```text
是否保存“个性”区域？
X=..., Y=..., W=..., H=...
```

The flow must reuse the existing confirm-before-apply behavior: do not mutate persisted config before confirmation.

## Display Flow

Clicking `显示` does not run OCR. It only overlays the saved rectangle on the Seed capture preview so the user can inspect the position.

Only one row needs to be highlighted at a time. Showing a new row replaces the previous highlighted OCR overlay. If a row has no saved region, `显示` should report that the region is not set.

## Recognition Preview Flow

OCR preview is on demand, not continuous.

Clicking `识别`, `测试当前项`, or `测试全部` captures the current preview image and runs OCR only for the selected saved region or regions. The result is written into the `上次识别` column.

Continuous OCR preview is intentionally out of scope because it would reintroduce high CPU usage while the settings window is open.

## Runtime OCR Use

Once regions are configured, normal Pokemon info OCR should prefer the small per-field regions:

- Nature and characteristic use the note-page regions.
- Stats use the six stat-value regions.

If a required region is missing, empty, or fails validation, the existing larger ROI logic remains as fallback. This prevents the new settings from making OCR unusable when a user has not configured every field yet.

## Characteristic Tolerance

The characteristic row needs extra tolerance because the line can be slightly misaligned or OCR can read extra or missing characters.

The recognition strategy is:

1. OCR the saved characteristic region.
2. Normalize punctuation and whitespace.
3. Match against the legal characteristic list.
4. If no legal match is found, retry once with a slightly expanded region.
5. If still unmatched, keep the raw result visible in `上次识别` and let the caller fall back to existing broader OCR logic.

This keeps the fast path small while preserving a recovery path for difficult characteristic text.

## Persistence

The eight regions are saved in application settings. Coordinates should use preview/source-image coordinates, not scaled label coordinates, so they remain stable across window size changes.

The settings window loads saved regions on open and saves each field immediately after confirmation.

## Testing

Tests should cover:

- Opening OCR settings does not block the main window.
- Clicking each row's `框选` puts the preview into the correct selection target.
- Confirming a selection saves only the active field.
- Canceling a selection preserves the previous field value.
- `显示` overlays a saved field without running OCR.
- `识别` runs only on demand.
- Missing per-field regions fall back to existing broad ROI OCR.
- Characteristic OCR retries with an expanded region before falling back.
