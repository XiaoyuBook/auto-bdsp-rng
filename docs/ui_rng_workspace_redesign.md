# 六页工作区与视频监看布局

实现日期：2026-10-09。界面仍使用 PySide6；本次调整主导航、六页内容和共享视频/实时日志区域。

主导航移到整窗顶部，六页在 860 像素宽度下均可直接点击。右栏使用更宽的默认尺寸；左栏按页面重排表单和动作，不改变字体倍率。自动流程的配置、候选与脚本对象会在视图切换时保留。

## 主窗口与右栏

默认窗口为 1150 × 900，最小窗口为 854 × 480，尺寸均为 Qt 逻辑像素。标题、主导航和状态栏分别为 50、40、24 像素。

右栏默认宽度按可用工作区的 44.5% 计算，最低约 400、最高 720 像素。用户可以拖动左右分隔线；拖动后的尺寸存入 `workspace_layout/monitor-v2/horizontal`，以后调整窗口或重新打开时保留。

旧版 310/360 像素的默认右栏更新为新布局。旧版右栏不少于 390 像素的自定义尺寸会迁移；其他应用配置不受布局迁移影响。

右栏从视频标题、16:9 画面和来源信息开始，下方实时日志填满剩余高度。短窗口会缩小画面的可见宽度以保留完整画面及日志操作。默认高度下，Seed/当前帧与匹配/眨眼进度分别位于画面左右上角；极窄画面精简标签并上下错开，两组 16 位 Seed 保持完整。

视频预览、连接、Seed 状态和实时日志缓冲由全部页面复用。框选时隐藏信息层，取消后恢复；自动重新捕捉时隐藏旧 Seed，取得新结果后显示。截图与原始视频帧不包含界面叠加层。

## 页面内容

| 页面 | 常驻动作 | 正文与次级入口 |
| --- | --- | --- |
| 自动定点 | 运行模式、开始、停止、开始前检查、OCR | “任务设置”连续显示目标、策略、六项脚本；“运行现场”显示阶段、实时帧摘要和候选。阶段记录可展开。 |
| 自动 TID | 运行模式、开始、停止、开始前检查、日志中心 | “任务设置”包含目标列表、搜索参数及脚本；“运行数据”显示当前/目标帧、完整 Display TID、等待倒计时和 ID 表。Seed 信息与阶段记录可展开。 |
| Seed 捕捉 | 捕捉 Seed、校正、TID/SID 测种 | 配置选择/保存、框选/截取、阈值/NPC、高级时序、自动流程配置。动作不会随表单滚走。 |
| 定点数据 | 查询条件、生成、更多 | 存档摘要分两行；查询条件分“乱数/遭遇/筛选”。显式生成完成后收起条件，自动刷新完成时保留条件面板当时的展开状态；候选表为主体。复制、导出、筛选方案及表格设置在更多菜单。 |
| 伊机控 | 脚本库、打开、新建、保存、控制与录制、运行/暂停/停止 | 编辑器默认占左栏正文。脚本库及控制/录制为临时工具面板；关闭后恢复原编辑器。运行输出默认折叠，可展开或进入日志中心。 |
| 日志中心 | 轮次记录/详细日志 | 窄栏以当前轮次的摘要和候选快照为主体，“选择轮次”临时切到列表。Seed/轮次详情、处理详情可展开并纵向滚动。详细日志的来源/级别、关键词及开关分行显示。 |

真正开始自动任务时切到运行视图；停止或完成后保留结果。用户仍可切回任务设置，运行中禁用及冻结配置按原流程处理。开始前检查、引导和缺少脚本的快捷入口会打开对应设置视图并定位控件。

结果表可以横向滚动查看全部字段；表单和操作栏按可用宽度排列。复制/导出仍使用完整数据，不受屏幕当前可见列影响。轮次快照保留锁定、同步与异色颜色，完整事件记录仍可查看和复制。

大范围定点查询走异步生成时，自动刷新完成也保留用户在等待期间自行展开或收起的状态；显式点击生成仍在完成后收起。自动 TID 表的帧数、TID、SID、TSV 列按内容保留宽度，十位帧数可完整显示；窄栏通过表格横向滚动查看后续时间列，字体和右栏尺寸保持原设计。

在 854 × 480 的短窗口中，脚本工具临时占用正文，避免将编辑器压成残片；关闭工具或增高窗口后恢复同一个编辑器，保留草稿、撤销与执行位置。600 像素高时，工具与编辑器可同时显示，兼容录制引导。

## 可复现视觉验收

在项目根目录执行：

```powershell
.venv\Scripts\python.exe scripts/render_workspace_review.py --dpi 100
.venv\Scripts\python.exe scripts/render_workspace_review.py --dpi 150
```

脚本使用离屏 Qt 窗口、临时 INI 配置、模拟控制后端及已有游戏截图。定点结果通过小范围真实生成获得；运行状态、TID、脚本和日志为演示数据，不连接采集卡、串口或 Switch，不执行游戏操作。

每种 DPI 输出 108 张截图，覆盖 860 × 600、1150 × 900、1440 × 960、854 × 480；包括空状态、六页代表性数据、自动流程运行视图、日志两种内容以及展开工具/条件/详情。默认输出目录为 `logs/ui-review/redesign`，可用 `--output` 修改。

`dimensions-100.json` 与 `dimensions-150.json` 记录请求/实际窗口尺寸、左右工作区、实际可见画面、信息层位置、滚动范围和操作控件横向溢出。渲染脚本不调整左右分隔线，使用应用的新默认尺寸。

2026-10-09 初版六页布局（`99f775a`）的矩阵共 216 张截图，所有实际窗口尺寸与请求一致，表单/动作无横向溢出，预览滚动范围均为 0。以下为该版本的逻辑像素实测值；实际画面尺寸扣除了预览边框。

| 主窗口 | 右栏宽度 | 100% 实际画面 | 150% 实际画面 | 日志正文可见高度 |
| --- | --- | --- | --- | --- |
| 860 × 600 | 400 | 375 × 211 | 374 × 211 | 85 |
| 1150 × 900 | 498 | 472 × 266 | 473 × 266 | 330 |
| 1440 × 960 | 627 | 602 × 339 | 602 × 339 | 317 |
| 854 × 480 | 400 | 259 × 146 | 259 × 146 | 30 |

标准高度下两角信息层并排；854 × 480 时上下错开以保留完整 Seed。短窗日志正文仍可读至少一行消息，展开入口与来源筛选保持可见。

退回项修复后的 TID 专项截图单独生成：

```powershell
.venv\Scripts\python.exe scripts/render_workspace_review.py --dpi 100 --tid-columns-only --output logs/ui-review/acceptance01
.venv\Scripts\python.exe scripts/render_workspace_review.py --dpi 150 --tid-columns-only --output logs/ui-review/acceptance01
```

`logs/ui-review/acceptance01` 共 8 张截图，覆盖 860 × 600、1150 × 900、100%/150% 缩放，以及数字列和预计时间列两个滚动位置。逐张核对前四列数字与完整日期时间；专项数据含 `1234567/54321/65432/4095` 和十位帧数 `1000000000`，前四列宽度均为 `99/62/62/54` 逻辑像素。窗口尺寸正确、控件无横向溢出，右栏仍分别为 400/498 像素。该目录的两份 `dimensions-*.json` 另记录全部列宽、数值和水平滚动位置。

## 行为回归

相关测试覆盖布局迁移、主导航、开始前检查定位、引导聚光与录制、自动流程数据、脚本草稿/执行状态、表格复制、日志与 Seed 信息层。每条命令在独立进程执行，启动 WebView 单独验证；避免 Qt 窗口/线程退出状态影响下一组。

```powershell
.venv\Scripts\python.exe -m pytest tests/test_ui.py tests/test_easycon_panel.py -q -k "not startup_webview_and_help"
.venv\Scripts\python.exe -m pytest tests/test_ui.py -q -k startup_webview_and_help
.venv\Scripts\python.exe -m pytest tests/test_auto_tid_rng_ui.py -q
.venv\Scripts\python.exe -m pytest tests/test_ui_polish.py -q
.venv\Scripts\python.exe -m pytest tests/test_workspace_layout.py tests/test_responsive_layout.py tests/test_run_records_ui.py tests/test_run_log_ui.py -q
.venv\Scripts\python.exe -m pytest tests/test_guide_flow.py tests/test_guide_mock_recording.py tests/test_script_guide.py tests/test_script_capture_guide.py tests/test_easycon_execution_ui.py tests/test_easycon_pause_ui.py tests/test_easycon_record_demo.py tests/test_start_readiness.py tests/test_table_workbench.py tests/test_monitor_workspace.py tests/test_video_info_overlay.py -q
```

最终分组复验：自动 TID 47 项、生命周期/交互 15 项、布局/响应式/轮次与日志 92 项全部通过。此前广泛回归中的旧结构断言及窄窗几何失败节点均已修正并复测；配置服务测试先处理启动时排队的配置刷新，再检查服务使用的配置路径。

退回项专项回归：异步/同步生成相关 10 项、自动 TID 47 项、工作区布局与定点结果统计 19 项、150% 缩放数字可读性 2 项全部通过。异步测试通过真实后台线程和 Qt 完成回调覆盖等待期间用户改变展开状态，以及显式生成完成后收起。原验收方 `acceptance_behavior_probe.py` 在 100%/150% 均复验：自动刷新 `during=true/after=true/expected_after=true`；原四个样例数字列宽均为 `73/58/61/54`，超过文字所需的 `51/36/39/32` 像素。

本次验收针对源码界面与模拟交互，不包含真实游戏联机、采集卡稳定性或安装包构建。
