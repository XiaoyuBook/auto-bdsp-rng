# auto_bdsp_rng

`auto_bdsp_rng` 是一个面向《宝可梦 晶灿钻石 / 明亮珍珠》（BDSP）的 Windows 桌面乱数辅助工具。它把 Project_Xs 的眨眼测种、PokeFinder 的 Gen 8 BDSP 定点生成逻辑、EasyCon / 伊机控脚本执行和自动定点乱数流程整合到同一个 PySide6 应用里，目标是减少在多个工具之间复制 Seed、手动过帧和人工判断撞闪时机的成本。

当前项目已经从最初的“Seed 捕捉 + 定点搜索”规划，演进为一个包含以下工作区的桌面应用：

- Seed 捕捉：复用 Project_Xs 的画面捕获、眼部模板、眨眼识别、Seed 恢复、重新识别和时间线能力。
- 定点数据区：在本仓库内实现 BDSP Gen 8 Static 生成、筛选、结果表格、存档信息和个体值计算器。
- 伊机控：提供 EasyCon 风格的脚本编辑、串口选择、Bridge 常驻连接、CLI 诊断后端、虚拟手柄和按键映射。
- 自动定点乱数：串联测种、目标搜索、过帧脚本、reidentify / 重新测种、最终撞闪脚本和 OCR 闪光判定。

## 适用范围

本项目优先服务 Windows 64-bit 环境，默认使用 Python 3.12、PySide6、OpenCV、Project_Xs_CHN、PokeFinder 参考实现，以及 EasyCon / 伊机控相关工具链。仓库内的 `third_party` 目录用于固定上游版本和对照实现，项目自身代码位于 `src/auto_bdsp_rng`。

> 这不是通用的宝可梦乱数工具，而是围绕 BDSP 定点 / 游走定点、眨眼测种、EasyCon 自动执行流程做的集成工作台。

## 功能概览

### Seed 捕捉

- 读取 Project_Xs 配置，支持窗口捕获和摄像头捕获。
- 预览画面、截取眼睛模板、拖拽框选 ROI。
- 捕捉玩家眨眼并恢复 `Seed[0-3]`。
- 自动转换为 PokeFinder / Gen 8 定点使用的 `Seed[0-1]`。
- 支持 reidentify、手动推进、TID/SID 流程、眨眼监控和 timeline 规划。

Seed 转换关系：

```text
seed0 = S0S1
seed1 = S2S3
```

示例：

```text
Seed[0-3]
S0 = 12345678
S1 = 9ABCDEF0
S2 = 11111111
S3 = 22222222

Seed[0-1]
seed0 = 123456789ABCDEF0
seed1 = 1111111122222222
```

### BDSP 定点搜索

- Python 移植 BDSP Gen 8 Static RNG 核心。
- 支持初始帧、最大帧数、Offset、队首特性、版本、TID/SID/TSV、闪符等输入。
- 支持定点目标、游走目标、固定 IV、性格、特性、性别、身高、体重、异色筛选。
- 结果表格支持复制、导出 CSV / TXT、列展示和中文化显示。
- 个体值计算器参考 PokeFinder IVChecker 逻辑，支持中文宝可梦名搜索。

### 伊机控 / EasyCon

- 提供浅色桌面风格的脚本编辑界面。
- 支持 `.txt` / `.ecs` 脚本加载、编辑、保存、未保存标记和 `Ctrl+S`。
- 支持脚本参数扫描、生成临时脚本、日志保留和文本选择。
- 支持串口自动选择、CLI 模式诊断、Bridge 常驻连接模式。
- Bridge 通过 UTF-8 JSON Lines 协议复用同一个串口连接，避免每次脚本执行都重连。
- 支持按键映射对话框、手柄背景图定位、键盘虚拟手柄、按键按下/释放与摇杆方向事件。

### 自动定点乱数

- 自动执行“测 seed -> 搜索目标 -> 过帧 -> 重新识别 / 重新测 seed -> 最终撞闪”的状态机。
- 支持单次、循环 N 次和无限循环。
- 可选择测种脚本、过帧脚本、撞闪脚本。
- 以 `fixed_delay`、脚本内固定 `_闪帧` 和目标帧计算撞闪脚本启动点。
- 最终等待阶段通过计时和实时 advances 修正，避免重复扣除闪帧或错过目标。
- 支持过帧过头后的跳过防死循环逻辑。
- 可选 OCR 闪光判定间隔校准和并行监测。
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

## EasyCon Bridge

仓库内包含 `bridge/EasyConBridge`，它是 Python 应用和 EasyCon 串口会话之间的常驻后端。Bridge 连接一次串口后，会复用同一个会话运行多个脚本、发送按键和处理虚拟手柄事件。

构建示例：

```powershell
dotnet build .\bridge\EasyConBridge\EasyConBridge.csproj -p:EasyConSourceRoot=D:\path\to\EasyCon
```

Mock session 可用于协议和连接生命周期冒烟测试：

```powershell
.\bridge\EasyConBridge\bin\Debug\net10.0\EasyConBridge.exe --mock-session
```

协议细节见：

- `docs/easycon_bridge_protocol.md`
- `bridge/EasyConBridge/README.md`

## 目录结构

```text
auto_bdsp_rng/
  bridge/EasyConBridge/             EasyCon 常驻 Bridge 后端
  docs/                             设计文档、协议说明和验证记录
  script/                           内置测种、过帧、撞闪脚本
  src/auto_bdsp_rng/
    automation/
      auto_rng/                     自动定点乱数状态机、脚本处理、搜索封装
      easycon/                      EasyCon CLI / Bridge 后端、发现、脚本生成
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
- EasyCon 脚本、CLI 后端、Bridge 后端和串口发现。
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

最近的修正重点集中在自动流程的实机时序：保持 reidentify 后原始 seed 基准、修正非游走定点 SID/TID RNG 序列、使用 EC 计算个性、避免 FINAL_WAIT 双重扣除闪帧、在 CLI / Bridge 路径上减少额外延迟，并修复定点结果个性显示偏差。

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
