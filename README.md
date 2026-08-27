# 珍钻复刻自动乱数

<p align="center">
  <img src="docs/assets/app-icon.png" alt="auto_bdsp_rng 图标" width="220">
</p>

## 下载与使用

普通用户请不要下载 GitHub 绿色 `Code` 按钮里的源码压缩包。请到 [GitHub Releases](https://github.com/XiaoyuBook/auto-bdsp-rng/releases) 下载：

```text
auto-bdsp-rng-v1.0.0-windows-x64.zip
```

下载后解压 zip，进入 `auto-bdsp-rng` 文件夹，双击：

```text
珍钻复刻自动乱数.exe
```

目标电脑不需要安装 Python、EasyCon、EasyConBridge 或 `ezcon.exe`。请保留 exe 旁边的 `_internal`、`script`、`docs` 等目录，不要只复制单独的 exe。

从首个内置升级器的 Windows 正式版开始，后续可在软件右上角“帮助 -> 检查更新…”中升级。软件会优先下载只包含变化文件的增量包，校验完成后自动退出、替换并重启；独立升级器会再次校验 Release SHA-256，并在新版确认启动前保留旧文件，替换失败或新版启动失败时自动恢复。首次安装、较老版本没有连续升级链或需要修复程序文件时，仍使用完整 Release zip。旧版因为本身没有升级器，需要最后完整下载一次带升级器的新版本。

升级不会强行覆盖用户修改过的 `script` 脚本、运行日志、Project_Xs 配置和自定义眼图；新版默认文件会以 `.new-v<版本>` 副本保留在原文件旁边，同名副本已存在时会追加编号而不会覆盖。应用不会在启动时自动联网，只有用户主动点击“检查更新…”才会访问 GitHub Releases。

`auto_bdsp_rng` 是一个面向《宝可梦 晶灿钻石 / 明亮珍珠》（BDSP）的 Windows 桌面乱数辅助工具。它把 Project_Xs 的眨眼测种、PokeFinder 的 Gen 8 BDSP 定点生成逻辑、EasyCon / 伊机控脚本执行和自动定点乱数流程整合到同一个 PySide6 应用里，目标是减少在多个工具之间复制 Seed、手动过帧和人工判断撞闪时机的成本。

当前项目已经从最初的“Seed 捕捉 + 定点搜索”规划，演进为一个包含以下工作区的桌面应用：

- 自动定点乱数：串联测种、目标搜索、过帧脚本、reidentify / 重新测种、最终撞闪脚本和 OCR 闪光判定。
- Seed 捕捉：复用 Project_Xs 的画面捕获、眼部模板、眨眼识别、Seed 恢复、重新识别和时间线能力。
- 定点数据区：在本仓库内实现 BDSP Gen 8 Static 生成、筛选、结果表格、存档信息和个体值计算器。
- 伊机控：由 Python 原生后端长期连接串口，直接运行 EasyCon 风格脚本、`.IL` 搜图和 `TesserDetect`，并提供虚拟手柄和按键映射。
- 历史记录：按轮次记录测种、候选、锁定、错过、反查和最终结果，便于复盘实机流程。
- 软件更新：从 GitHub Releases 检查正式版，下载并校验文件级增量包，由独立升级器在主程序退出后以持久事务完成替换、启动确认、回滚和重启。

## 适用范围

本项目优先服务 Windows 64-bit 环境，默认使用 Python 3.12、PySide6、OpenCV、Project_Xs_CHN、PokeFinder 参考实现，以及项目内维护的 Python 原生 EasyCon 运行时。仓库内的 `third_party` 目录用于固定上游版本和对照实现，项目自身代码位于 `src/auto_bdsp_rng`。

> 这不是通用的宝可梦乱数工具，而是围绕 BDSP 定点 / 游走定点、眨眼测种、EasyCon 自动执行流程做的集成工作台。

## 功能概览

### Seed 捕捉

- 连接一个由独立 Capture Broker 独占的采集卡视频源，同时供常驻预览、眨眼识别、OCR 和伊机控搜图读取最新帧。
- 读取 Project_Xs 配置，支持窗口捕获和摄像头捕获。
- 预览画面、截取眼睛模板、拖拽框选 ROI。
- 捕捉玩家眨眼并恢复 `Seed[0-3]`。
- 自动转换为 PokeFinder / Gen 8 定点使用的 `Seed[0-1]`。
- 支持 reidentify、手动推进、TID/SID 流程、眨眼监控和 timeline 规划。

### BDSP 定点搜索

- 支持初始帧、最大帧数、Offset、队首特性、版本、TID/SID/TSV、闪符等输入。
- 支持定点目标、游走目标、固定 IV、性格、特性、性别、身高、体重、异色筛选。
- 结果表格支持复制、导出 CSV / TXT、列展示和中文化显示。
- 个体值计算器参考 PokeFinder IVChecker 逻辑，支持中文宝可梦名搜索。

### 伊机控 / EasyCon

- 提供浅色桌面风格的脚本编辑界面。
- 支持 `.txt` / `.ecs` 脚本加载、编辑、保存、未保存标记和 `Ctrl+S`。
- 支持脚本参数扫描、生成临时脚本、日志保留和文本选择。
- Python 原生后端复刻 EasyCon 串口握手并长期保持连接，脚本结束不会主动断开。
- 支持 `.IL` 模板搜图、XY/Laplacian 边缘匹配、`TesserDetect`、`IMPORT` 和脚本目录下的 `lib/`；第一版不支持 `.ILX`。
- 所有脚本复用 Seed 页连接的共享视频源，搜图框只叠加到主预览或画中画副本，不会污染 OCR、眨眼识别或其他消费者的原始帧。
- 支持按键映射对话框、手柄背景图定位、键盘虚拟手柄、按键按下/释放与摇杆方向事件。

### 自动定点乱数

- 自动执行“测 seed -> 搜索目标 -> 过帧 -> 重新识别 / 重新测 seed -> 最终撞闪”的状态机。
- 默认作为主工作区显示，顶部状态区会同步展示循环次数、当前阶段和实时 advance。
- 支持单次、循环 N 次和无限循环。
- 支持从测种脚本开始，也支持从捕获 Seed 直接进入后续搜索流程。
- 可选择测种脚本、过帧脚本、撞闪脚本。
- 支持多个目标精灵筛选条件，并会记忆最近使用的多目标配置。
- 以 `fixed_delay`、脚本内固定 `_闪帧` 和目标帧计算撞闪脚本启动点。
- 最终等待阶段通过计时和实时 advances 修正，避免重复扣除闪帧或错过目标。
- 支持过帧过头后的跳过防死循环逻辑。
- 支持自动反查范围设置、能力页 OCR 重试和个性 / IV 范围匹配。
- 可选 OCR 闪光判定间隔校准和并行监测。
- 判定出闪后可自动向伊机控发送 Capture 录像指令。
- 自动面板会保存最近设置，并可在连接成功后自动连接伊机控。

核心公式：

```text
trigger_advances = raw_target_advances - fixed_delay - fixed_flash_frames
remaining_to_trigger = trigger_advances - current_advances
```

## 安装

首次克隆后先初始化子模块：

```powershell
git submodule update --init --recursive
```

创建虚拟环境并安装项目：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .[dev]
```

如果需要 OCR 闪光判定能力，再安装可选依赖：

```powershell
python -m pip install -e .[ocr]
```

Windows Release green packages starting with `v1.0.0` include PaddleOCR/PaddlePaddle in the zip, so normal users do not need to install OCR dependencies separately.

## 运行

启动图形界面：

```powershell
python -m auto_bdsp_rng gui
```

安装为 editable 后，也可以运行脚本入口：

```powershell
auto-bdsp-rng gui
```

常用 CLI：

```powershell
# 查看版本
python -m auto_bdsp_rng --version

# 读取 Project_Xs 配置
python -m auto_bdsp_rng blink-config --project-xs-config config_camera.json

# 转换 Seed[0-3] / Seed[0-1]
python -m auto_bdsp_rng convert-seed --seed 12345678 9ABCDEF0 11111111 22222222

# 从当前配置捕获一帧预览图
python -m auto_bdsp_rng capture-frame --project-xs-config config_camera.json --output preview.png

# 捕捉眨眼并恢复 seed
python -m auto_bdsp_rng capture-blinks --project-xs-config config_camera.json --blink-count 40

# 对已有 seed 做重新识别
python -m auto_bdsp_rng reidentify --project-xs-config config_camera.json --seed 12345678 9ABCDEF0 11111111 22222222
```

## Python 原生 EasyCon

产品主路径完全由 `src/auto_bdsp_rng/automation/easycon/native/` 和 `native_backend.py` 实现，不启动 EasyCon GUI、CLI、Bridge 或 `ezcon.exe`，也不读取 `EASYCON_ROOT`。用户先在 Seed 捕捉页连接采集卡，再在伊机控页连接串口，之后脚本、自动定点、自动 TID、RIGHT 按键和捕捉亮屏保活都复用同一后端。

仓库仍保留旧 CLI/Bridge 源码和协议测试，供兼容回归与上游实现对照；它们不会被默认界面选择，也不会进入正式包的主执行路径。旧 Bridge 的开发资料见：

- `docs/easycon_bridge_protocol.md`（旧兼容协议）
- `bridge/EasyConBridge/README.md`（旧 Bridge 开发说明）

## 目录结构

```text
auto_bdsp_rng/
  bridge/EasyConBridge/             保留的旧 EasyCon Bridge 兼容源码
  docs/                             设计文档、协议说明和验证记录
    assets/                         README 图标等展示资源
  script/                           内置测种、过帧、撞闪脚本
  src/auto_bdsp_rng/
    automation/
      auto_rng/                     自动定点乱数状态机、脚本处理、搜索封装
      easycon/                      Python 原生 EasyCon、旧兼容后端和脚本工具
    blink_detection/                Project_Xs 捕获、眨眼、reidentify 适配
    data/                           BDSP 定点数据加载
    gen8_static/                    Gen 8 BDSP 定点生成器
    rng_core/                       Seed、Xorshift、Xoroshiro 等 RNG 基础
    ui/                             PySide6 主窗口、自动页、伊机控页、目标表单
  tests/                            pytest 测试
  third_party/
    Project_Xs_CHN/                 上游 Project_Xs_CHN 子模块
    PokeFinder/                     上游 PokeFinder 子模块
```

## 测试

运行全部 Python 测试：

```powershell
python -m pytest
```

测试覆盖重点包括：

- Seed 数据模型和转换。
- RNG 核心与 BDSP Static 生成器。
- Project_Xs 适配层。
- BDSP 数据表加载校验。
- EasyCon 原生脚本引擎、串口设备、`.IL` 搜图、Tesseract、Broker 接入及旧后端兼容。
- 自动定点乱数状态机、脚本参数和最终等待逻辑。
- PySide6 界面启动、布局和信号层。

## 开发脉络

README 依据当前仓库完整提交历史整理。项目大致经历了以下阶段：

| 时间 | 重点变化 |
| --- | --- |
| 2026-05-04 | 建立项目规划，引入 Project_Xs_CHN 和 PokeFinder 子模块。 |
| 2026-05-05 | 接入 Project_Xs 画面捕获、眼部预览、眨眼捕获、reidentify、TID/SID、眨眼监控、配置保存和 timeline。 |
| 2026-05-05 | 增加 Seed 模型、RNG 核心、BDSP Static 生成器、数据表加载校验和初版 UI 整合。 |
| 2026-05-05 | 完善捕捉界面、ROI 选择、结果表格、筛选、闪光验证、存档管理和定点页面布局。 |
| 2026-05-05 至 2026-05-06 | 规划并实现伊机控后端、常驻 Bridge 协议、CLI 诊断、串口选择、日志和脚本体验。 |
| 2026-05-07 | 大幅重做桌面 UI：窗口自由缩放、浅色原生风格、EasyCon 风格脚本面板、按键映射和虚拟手柄。 |
| 2026-05-07 | 重构定点数据区、修复多处中文布局问题，并恢复 PokeFinder 原版个体值计算器逻辑。 |
| 2026-05-08 | 新增自动定点乱数基础流程、状态机、UI 信号层、真实流程接入和目标锁定展示。 |
| 2026-05-08 至 2026-05-09 | 调整过帧、reidentify、最终撞闪校准、脚本闪帧口径和 OCR 闪光判定。 |
| 2026-05-10 | 集中修正自动撞闪触发时机、FINAL_WAIT、Bridge 执行时序、CLI 模式、诊断日志和 RNG 细节。 |
| 2026-05-12 | C++ 原生 RNG 扩展，搜索性能提升 ~200 倍；修复 early rejection RNG 序列错误；反查 IV 公式修正并接入 C++ PokeFinder 公式；delay 三处修正；文本复制支持；CLI 默认模式；历史记录单行紧凑格式；单次模式死循环修复。 |
| 2026-05-13 | 继续打磨自动定点乱数实机体验：增加多目标筛选弹窗和多条件记忆、从捕获 Seed 开始的入口、顶部自动流程状态、自动反查范围与文本复制；修复无候选循环停止、跨线程更新 Qt 界面、反查性格 / 个性口径、日志与策略栏布局；调整标签页顺序，并让开始按钮以主按钮样式显示。 |

最近的修正重点集中在自动流程的实机时序：保持 reidentify 后原始 seed 基准、修正非游走定点 SID/TID RNG 序列、使用 EC 计算个性、避免 FINAL_WAIT 双重扣除闪帧、在 CLI / Bridge 路径上减少额外延迟，并修复定点结果个性显示偏差。

## C++ 原生 RNG 扩展

BDSP 定点搜索和 Project_Xs reidentify 的核心 RNG 计算已从纯 Python 移植为 C++17 + pybind11 扩展模块（`src/auto_bdsp_rng/rng_core/native/`）。模块包含 Xorshift / Xoroshiro / RNGList 环形缓冲区 / StaticGenerator8，以及普通 reidentify 与 `Reidentify 1 PK NPC` noisy reidentify；原生结果按 Project_Xs 逻辑对齐。Python 侧在 import 成功时优先走 C++ 路径，未编译、输入不支持或原生搜索无匹配时回退到 Python 实现。

构建依赖 `pybind11>=2.12` 和 `/utf-8` MSVC 编译选项已加入 `setup.py` 与 `pyproject.toml`。安装项目时自动编译：

```powershell
pip install -e .
```

### RNG 序列修正

早期版本在非游走定点生成器中使用了“提前拒绝”优化：当异色不匹配时直接 `continue` 跳过 IV 等后续 RNG 消耗。这导致 RNGList 环形缓冲区位点偏移，后续帧读取的 height / weight / nature 等值与 PokeFinder 不一致。现已改为每帧完整计算所有属性、末尾统一过滤，与 PokeFinder 行为完全一致。

### 反查 IV 反算

反查流程中的“能力值 → 个体值范围”反算，之前 Python 实现缺少 Gen 8 非 HP 公式中的 `+ 5` 项，导致推算出的 IV 范围与实际不符，反查搜索始终零候选。修复后改用 C++ 内置的 `compute_iv_ranges`（基于 PokeFinder `Nature::computeStat` 公式），带 Python fallback。

### IV 计算器

定点数据区的个体值计算器同样接入 C++ 公式，支持未知性格时尝试全部三种修正（1.0 / 0.9 / 1.1）。

### delay 定义

三处 delay 含义全部修正：
- **周期结果显示**：改为用户填入的 `fixed_delay`（之前错误地使用 `remaining_to_trigger`）
- **反查日志**：`actual_delay = 实际帧数 - 目标帧数 + fixed_delay`
- **历史面板反查候选**：由调用方传入计算好的 delay 值

### 文本复制

所有只读文本视图（自动定点日志、历史记录、当前目标列表、伊机控输出）统一添加右键上下文菜单，`createStandardContextMenu` 返回空时显式添加「复制 Ctrl+C」「全选 Ctrl+A」菜单项。

### 历史记录候选格式

候选从多行改为单行紧凑格式：
```
候选1 (锁定) adv=1500 EC=3D587C81 PID=12345678 HP=30 / 攻击=31 / ... 性格=胆小 异色=星闪 身高=148 体重=152
候选2 (同步) adv=2300 EC=40B922D8 PID=9ABCDEF0 ...
```
`(锁定)` 表示被选中的最低帧，`(同步)` 表示该候选来自同步搜索（另一队首状态）。
普通搜索候选不显示全局 `fixed_delay`，因为它不是候选本身的属性；反查候选仍显示逐项计算得到的实际 `delay`。

### 历史：CLI 默认模式

旧版本曾默认选中「CLI 模式」。当前版本已由 Python 原生后端取代该产品路径；这段仅保留为版本演进记录。

### 单次模式死循环修复

单次模式下无候选时直接结束（`COMPLETED`），不再重新跑测种脚本死循环。

## 上游依赖与许可

- Project_Xs_CHN: https://github.com/HaKu76/Project_Xs_CHN
  - 当前子模块版本：`b6cfaaeca8aa6a95e2f07ccaef606e301fa8ad7a`
  - 许可：MIT License
- PokeFinder: https://github.com/Admiral-Fish/PokeFinder
  - 当前子模块版本：`2d5c6afed9240f2bdb98634b5b8b1fab352aefa5`（v4.3.2）
  - 许可：GPL-3.0 License

本项目 `pyproject.toml` 声明为 `GPL-3.0-or-later`。如果分发包含或移植自 PokeFinder 的实现，需要遵守 GPL-3.0 及相关源代码开放要求。

## 当前注意事项

- 该工具强依赖 Windows 桌面环境、游戏画面捕获、脚本执行时序和本机串口状态，实机运行前请先用小脚本确认伊机控连接正常。
- 自动定点乱数流程对 `fixed_delay`、脚本内 `_闪帧`、OCR 阈值和实际画面响应时间敏感，建议先做少量目标校准。
- `third_party` 目录主要用于上游参考和对照，不建议直接在子模块内改业务逻辑。
