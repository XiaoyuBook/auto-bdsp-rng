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
| 自动定点 | 运行模式、开始、停止、开始前检查、OCR | 顶部并列显示当前目标与流程状态，窄栏自动上下排列；下方连续显示策略和脚本。状态卡片的“详情”打开实时帧数、阶段记录及本轮候选。 |
| 自动 TID | 运行模式、开始、停止、开始前检查、日志中心 | “任务设置”包含目标列表、搜索参数及脚本；“运行数据”显示当前/目标帧、完整 Display TID、等待倒计时和 ID 表。Seed 信息与阶段记录可展开。 |
| Seed 捕捉 | 编辑配置、浏览/新增/保存、捕捉 Seed、TID/SID 测种、校正 | 依照参考项目分为捕捉操作、识别参数、高级时序；七项现有时序参数直接展开，阈值在视频右上角编辑，自动流程配置保留折叠入口。动作不会随表单滚走。 |
| 定点数据 | 查询条件、生成、更多 | 存档摘要分两行；查询条件分“乱数/遭遇/筛选”。显式生成完成后收起条件，自动刷新完成时保留条件面板当时的展开状态；候选表为主体。复制、导出、筛选方案及表格设置在更多菜单。 |
| 伊机控 | 脚本库、打开、新建、保存、控制与录制、运行/暂停/停止 | 编辑器默认占左栏正文。脚本库及控制/录制为临时工具面板；关闭后恢复原编辑器。运行输出默认折叠，可展开或进入日志中心。 |
| 日志中心 | 轮次记录/详细日志 | 窄栏以当前轮次的摘要和候选快照为主体，“选择轮次”临时切到列表。Seed/轮次详情、处理详情可展开并纵向滚动。详细日志的来源/级别、关键词及开关分行显示。 |

自动定点开始时滚回目标与状态卡片，配置仍在同一页；运行详情为非模态窗口，关闭、重开均保留数据并持续更新。自动 TID 开始时切到运行数据，停止或完成后保留结果，用户仍可切回任务设置。运行中禁用及冻结配置按原流程处理；开始前检查和缺少脚本的快捷入口会定位相应配置控件。

结果表可以横向滚动查看全部字段；表单和操作栏按可用宽度排列。复制/导出仍使用完整数据，不受屏幕当前可见列影响。轮次快照保留锁定、同步与异色颜色，完整事件记录仍可查看和复制。

大范围定点查询走异步生成时，自动刷新完成也保留用户在等待期间自行展开或收起的状态；显式点击生成仍在完成后收起。自动 TID 表的帧数、TID、SID、TSV 列按内容保留宽度，十位帧数可完整显示；窄栏通过表格横向滚动查看后续时间列，字体和右栏尺寸保持原设计。

在 854 × 480 的短窗口中，脚本工具临时占用正文，避免将编辑器压成残片；关闭工具或增高窗口后恢复同一个编辑器，保留草稿、撤销与执行位置。600 像素高时，工具与编辑器可同时显示。

## 可复现视觉验收

在项目根目录执行：

```powershell
.venv\Scripts\python.exe scripts/render_workspace_review.py --dpi 100
.venv\Scripts\python.exe scripts/render_workspace_review.py --dpi 150
```

脚本使用离屏 Qt 窗口、临时 INI 配置、模拟控制后端及已有游戏截图。定点结果通过小范围真实生成获得；运行状态、TID、脚本和日志为演示数据，不连接采集卡、串口或 Switch，不执行游戏操作。

截图覆盖 860 × 600、1150 × 900、1440 × 960、854 × 480；包括空状态、六页代表性数据、自动流程状态、日志两种内容以及展开工具/条件/详情。默认输出目录为 `logs/ui-review/redesign`，可用 `--output` 修改。

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

相关测试覆盖布局迁移、主导航、开始前检查定位、录制、自动流程数据、脚本草稿/执行状态、表格复制、日志与 Seed 信息层。每条命令在独立进程执行，启动 WebView 单独验证；避免 Qt 窗口/线程退出状态影响下一组。

```powershell
.venv\Scripts\python.exe -m pytest tests/test_ui.py tests/test_easycon_panel.py -q -k "not startup_webview_and_help"
.venv\Scripts\python.exe -m pytest tests/test_ui.py -q -k startup_webview_and_help
.venv\Scripts\python.exe -m pytest tests/test_auto_tid_rng_ui.py -q
.venv\Scripts\python.exe -m pytest tests/test_ui_polish.py -q
.venv\Scripts\python.exe -m pytest tests/test_workspace_layout.py tests/test_responsive_layout.py tests/test_run_records_ui.py tests/test_run_log_ui.py -q
.venv\Scripts\python.exe -m pytest tests/test_easycon_execution_ui.py tests/test_easycon_pause_ui.py tests/test_start_readiness.py tests/test_table_workbench.py tests/test_monitor_workspace.py tests/test_video_info_overlay.py -q
```

最终分组复验：自动 TID 47 项、生命周期/交互 15 项、布局/响应式/轮次与日志 92 项全部通过。此前广泛回归中的旧结构断言及窄窗几何失败节点均已修正并复测；配置服务测试先处理启动时排队的配置刷新，再检查服务使用的配置路径。

退回项专项回归：异步/同步生成相关 10 项、自动 TID 47 项、工作区布局与定点结果统计 19 项、150% 缩放数字可读性 2 项全部通过。异步测试通过真实后台线程和 Qt 完成回调覆盖等待期间用户改变展开状态，以及显式生成完成后收起。原验收方 `acceptance_behavior_probe.py` 在 100%/150% 均复验：自动刷新 `during=true/after=true/expected_after=true`；原四个样例数字列宽均为 `73/58/61/54`，超过文字所需的 `51/36/39/32` 像素。

本次验收针对源码界面与模拟交互，不包含真实游戏联机、采集卡稳定性或安装包构建。

## 自动定点目标与状态卡片

参考 `auto-poke-rng` 的 `TargetSummaryCard.tsx`、`StaticFlowStatusCard.tsx`、`AutomationWorkspace.tsx` 及其样式，将自动定点改为连续工作区，移除“任务设置 / 运行现场”分页。保留本项目浅色主题、原有配置及自动流程。

目标卡片展示精灵图像、名称、类别、等级和逐组筛选条件，通过“目标设置”修改。状态卡片展示本轮进度图、当前阶段、说明和关键帧数；等待阶段直接显示距撞帧启动的剩余帧数。状态图只记录实际收到的阶段和经过的连线，跳过的步骤不标记完成，失败保留所在节点，新轮次清除上一轮轨迹。

两张卡片在可用宽度不少于 570 逻辑像素时并列，否则上下排列并随正文滚动。参数和脚本配置位于其下，右侧视频与日志继续共享。详细帧数、delay、阶段记录和候选表从“详情”进入，数据保持更新，不另占主页面标签。

```powershell
.venv\Scripts\python.exe scripts/render_workspace_review.py --auto-only --dpi 100 --output logs/ui-review/auto-overview
.venv\Scripts\python.exe scripts/render_workspace_review.py --auto-only --dpi 125 --output logs/ui-review/auto-overview
.venv\Scripts\python.exe scripts/render_workspace_review.py --auto-only --dpi 150 --output logs/ui-review/auto-overview
```

每种缩放输出 32 张截图，覆盖四种窗口尺寸、空/运行状态、卡片、策略、脚本展开及详情窗口。`tests/test_auto_rng_overview.py` 检查真实阶段映射、回跳/重测轨迹重置、详情关闭重开后数据持续更新；布局和开始前检查测试共同覆盖同页配置入口。

## 引导模式移除

首次启动欢迎页改为直接进入工作区，删除经验等级选择、顶部引导按钮和接受欢迎页后自动启动引导的逻辑。引导控制器、高亮提示、专属演示流程及其测试一并移除；旧配置中的引导进度仅作为历史数据保留，不再读取或执行。正常框选、录制、OCR、开始前检查和 QQ 配置教程继续使用原入口。

## Seed 捕捉页面参考实现

2026-10-09 再次调整 Seed 页，以本地 `auto-poke-rng` 的 `3f2916c` 版本中 `src/components/BlinkWorkspace.tsx`、`BlinkVideoOverlay.tsx` 及 `src/styles.css` 的当前布局为依据。参考仓库内已有的旧截图与当前组件不同，没有据此复刻旧版双列 Seed 表单。

左栏采用平面的分区标题与分隔线。编辑配置、浏览、新增、保存放在同一行；捕捉 Seed 与 TID/SID 测种并排，校正在下一行。顶部操作区固定，下面的识别参数、高级时序与自动流程配置独立纵向滚动。新增操作会将当前参数另存为新配置，并切换至新文件；取消时保留原选择及未保存值，自动流程所选配置不随之切换。

识别参数保留框选眼睛区域、截取眼睛与本项目的闪光判定校准入口。左栏不再重复放置识别阈值；Seed 页在右侧视频右上角直接编辑，离线时也可修改和保存，捕捉期间禁用，框选期间隐藏信息层。七项原有参数依次为 NPC 数、时间延迟、帧数延迟、帧数延迟 2、Timeline NPC 数、宝可梦 NPC 数及 1 PK NPC 校正。保持本项目原有模型与取值范围；此次没有引入参考项目额外的手动 Timeline 切换或“关闭菜单 +1”运行功能。

Seed 专项截图命令：

```powershell
.venv\Scripts\python.exe scripts/render_workspace_review.py --seed-only --dpi 100 --output logs/ui-review/seed-reference
.venv\Scripts\python.exe scripts/render_workspace_review.py --seed-only --dpi 150 --output logs/ui-review/seed-reference
```

两种缩放各24张真实Qt截图，覆盖四种窗口尺寸、空/有数据状态及默认、时序滚动位置、自动流程配置展开状态。参数与动作无横向溢出。专项回归见 `tests/test_seed_capture_workspace.py`，验证离线阈值编辑、小窗口操作可达、框选隐藏/恢复、捕捉锁定阈值、新增配置保留原文件与完整参数、取消新增保留草稿。现有捕捉、信息层及开始前检查回归共同验证业务兼容。
