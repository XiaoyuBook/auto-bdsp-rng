# Project_Xs 接入边界

第一阶段不直接修改 `third_party/Project_Xs_CHN`，也不驱动它的 Tk GUI。我们只把已经验证过的核心流程包装成自己的接口。

当前确认的 Project_Xs 调用链：

```text
rngtool.tracking_blink(...)
  -> blinks, intervals, offset_time
  -> rngtool.recov(blinks, intervals, npc)
  -> Xorshift.get_state()
  -> Seed[0-3]
  -> Seed[0-1]
```

对应到本项目：

- `src/auto_bdsp_rng/blink_detection`：本项目自己的 Project_Xs 适配层。
- `auto_bdsp_rng.blink_detection.BlinkCaptureConfig`：描述眼部模板、ROI、窗口/摄像头来源等捕获配置。
- `auto_bdsp_rng.blink_detection.BlinkObservation`：保存 Project_Xs 捕获出来的 blink 类型和间隔。
- `auto_bdsp_rng.blink_detection.recover_seed_from_observation()`：把 observation 转成规范化的 `SeedState32`。
- `SeedState32.format_words()`：输出 `Seed[0-3]`，固定 8 位大写十六进制。
- `SeedState32.format_seed64_pair()`：输出 `Seed[0-1]`，固定 16 位大写十六进制。

后续阶段接 UI 时，UI 只依赖这些本项目接口，不直接依赖 Project_Xs 的 Tk 控件和全局工作目录。
