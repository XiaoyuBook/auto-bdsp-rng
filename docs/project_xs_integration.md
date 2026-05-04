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

## 配置调试命令

可以先只解析 Project_Xs 配置，不启动窗口或摄像头捕获：

```powershell
.\.venv\Scripts\python.exe -m auto_bdsp_rng blink-config --project-xs-config config_cave.json
```

配置名会从 `third_party/Project_Xs_CHN/configs` 中查找；也可以传入 JSON 文件的绝对路径。

也可以按配置捕获一帧画面并保存，方便检查窗口或摄像头输入是否正确：

```powershell
.\.venv\Scripts\python.exe -m auto_bdsp_rng capture-frame --project-xs-config config_cave.json --output .\debug\preview.png
```

眼部模板和 ROI 的调试可以使用带标注的预览命令：

```powershell
.\.venv\Scripts\python.exe -m auto_bdsp_rng preview-eye --project-xs-config config_cave.json --output .\debug\eye_preview.png
```

输出图片中红框表示 ROI，绿框表示模板匹配达到阈值，黄框表示未达到阈值。命令行会同时打印匹配分数、匹配位置和模板尺寸。

确认预览正确后，可以启动 Project_Xs 眨眼捕获并恢复 Seed：

```powershell
.\.venv\Scripts\python.exe -m auto_bdsp_rng capture-blinks --project-xs-config config_cave.json
```

默认捕获 40 次眨眼。可以临时覆盖捕获数量和 NPC 数：

```powershell
.\.venv\Scripts\python.exe -m auto_bdsp_rng capture-blinks --project-xs-config config_cave.json --blink-count 40 --npc 0
```

命令会输出 `Seed[0-3]`、`Seed[0-1]`、原始 blink 类型和 interval，供后续 UI 和定点生成器接入。
