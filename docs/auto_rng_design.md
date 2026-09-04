# 自动定点乱数界面设计方案

> 历史设计记录：文中的 EasyCon Bridge 服务边界已由同一个 Python 原生 EasyCon
> backend 取代；当前流程还要求先连接共享 Capture Broker。状态机和帧数公式部分仍有效。

## 目标

新增一个同级 Tab：`自动定点乱数`，把 Project_Xs 测 seed、BDSP 定点结果搜索、EasyCon 过帧脚本、Project_Xs 重新识别/重新测 seed、撞闪脚本串成一个可观察、可停止、可循环的全自动流程。

本轮只做 UI 与实现思路设计，不写业务代码。

## 核心概念

### 帧数定义

| 名称 | 含义 | 示例 |
|------|------|------|
| `raw_target_advances` | BDSP 定点搜索结果中的目标帧，即 `State8.advances` | 1000 |
| `fixed_delay` | 用户填写的固定 delay，表示脚本等待结束后（无 `_闪帧` 时为脚本启动后）到实际撞到之间的延迟 | 1400 |
| `fixed_flash_frames` | 撞闪脚本声明的整数 `_闪帧`；脚本未声明时为 `None`，计算偏移按 0 | 60 / `None` |
| `trigger_advances` | 撞闪脚本理论启动帧，`raw_target_advances - fixed_delay - (fixed_flash_frames or 0)` | 340 |
| `current_advances` | 当前已前进帧数，初次测 seed 后为 0，reidentify 后更新 | 600 |
| `remaining_to_trigger` | 距离运行撞闪脚本还剩多少帧，`trigger_advances - current_advances` | 300 |
| `flash_frames` | 兼容旧脚本的脚本内等待量；新式脚本可以省略并交给 runner 等待 | 60 / 无 |
| `max_wait_frames` | 最大等待帧数；剩余帧数小于等于它时，不再调用过帧脚本 | 300 |
| `reseed_threshold_frames` | 单次过帧超过该值后不用普通校正，改为重新捕获 Seed | 默认 900,000，可在“校正策略设置”修改 |
| `reidentify_max_attempts` | 普通校正的最大尝试次数 | 默认 2，最小 1 |
| `reidentify_failure_policy` | 普通校正用尽次数后的处理 | 默认进入下一轮，也可先完整重测 Seed |
| `reidentify_seed_max_attempts` | 补救测 Seed 的最大尝试次数 | 默认 1，最小 1 |
| `min_final_flash_frames` | 最终撞闪前的最小安全剩余帧；太近则放弃本目标 | 内置 5，不在 UI 展示 |

关键规则：
- 搜索目标时，按当前 seed 和筛选条件生成结果。
- 有多个结果时，默认锁定 `advances` 最低的结果。
- 真正决定是否进入撞闪的是 `remaining_to_trigger <= max_wait_frames`。
- 进入撞闪阶段后必须做最终实时校准；有 `_闪帧` 的旧脚本沿用脚本内等待和动态调整，无 `_闪帧` 的脚本由 runner 等到启动点后原样运行。
- 还没进入等待范围时，给过帧脚本填 `_目标帧数 = remaining_to_trigger`。
- 过帧脚本本身已有内部预留逻辑，例如 `bdsp过帧.txt` 内部会用 `_目标帧数 - 300`，所以自动流程只填理论剩余帧，不额外替脚本扣预留值。
- 普通校正用尽配置次数后，按失败策略直接进入下一轮，或先在当前轮完整重测 Seed；补救成功必须清除旧目标并重新搜索，补救次数全部失败后才清空 Seed/目标并进入下一轮。
- 过场校正不使用普通校正的失败策略。任何过场脚本运行后都禁止原地完整重测 Seed，过场校正固定最多尝试 2 次，失败或后续过帧超过校正帧数上限时进入下一轮。

### delay 对 advances 的影响

`fixed_delay` 不参与 seed 搜索，也不修改当前 advances。它表示脚本等待结束后（无 `_闪帧` 时为脚本启动后）到实际撞到之间的用户校准延迟；自动流程只在脚本声明 `_闪帧` 时额外扣除该等待量。

严格公式：

```text
raw_target_advances = state.advances
script_wait_frames = fixed_flash_frames if declared else 0
trigger_advances = raw_target_advances - fixed_delay - script_wait_frames
remaining_to_trigger = trigger_advances - current_advances
```

示例：

```text
raw_target_advances = 1800
fixed_delay = 1400
fixed_flash_frames = 60
trigger_advances = 340

current_advances = 0 时，remaining_to_trigger = 340，需要先过帧。
current_advances = 40 时，remaining_to_trigger = 300，可以进入最终撞闪等待区。
```

错误理解要避免：
- 不要把 `fixed_delay` 加到 `current_advances`。
- 不要用 `fixed_delay` 修改 seed 或搜索结果。
- 不要在 `_目标帧数` 里额外扣 delay；过帧阶段逼近的是 `trigger_advances`，不是 `raw_target_advances`。

### `_闪帧` 兼容模式

撞闪脚本声明整数 `_闪帧` 时按旧模式运行：软件读取该等待量并用于 `trigger_advances` 计算，必要时仍可在过帧过头分支动态缩短本次提交文本中的 `_闪帧`。脚本未声明 `_闪帧` 时按新模式运行：等待偏移按 0 计算，runner 通过实时 advances 等到 `raw_target_advances - fixed_delay`，再原样启动脚本，不新增或改写 `_闪帧`。

因此进入 `max_wait_frames` 范围后，自动流程必须执行最终实时校准：

1. 做一次 final reidentify，或在需要重新测 seed 的场景做 final capture seed。
2. 用最新 seed / current advances 重新搜索或重新确认锁定目标。
3. 读取可选 `_闪帧`；存在时扣除其整数值，不存在时按 0，得到脚本启动帧。
4. 记录校准参考点：`current_advances_at_ref` 和 `ref_time`。
5. 在即将提交撞闪脚本前，用当前时间修正已经流逝的 advances。
6. 计算 `remaining_to_trigger = trigger_advances - live_current_advances`。
7. 旧模式在安全窗口内运行或动态调整脚本等待；新模式等待到 `remaining_to_trigger == 0` 后原样运行脚本。

建议实时修正式：

```text
elapsed_seconds = now_monotonic - ref_time
elapsed_advances = floor(elapsed_seconds / 1.018) * (npc + 1)
live_current_advances = current_advances_at_ref + elapsed_advances
remaining_to_trigger = trigger_advances - live_current_advances
```

第一版安全规则：
- `remaining_to_trigger < 0`：已错过脚本启动点，不运行撞闪脚本；无 `_闪帧` 模式在恰好为 0 时可以立即启动。
- `remaining_to_trigger < min_final_flash_frames`：距离太近，脚本启动和通信误差可能导致错过；放弃本目标并回到测 seed / 搜索流程。
- final reidentify / final capture 到运行撞闪脚本之间的 UI 和文件生成路径要尽量短，不做额外弹窗确认。

### 游走目标的 OCR 门控

艾姆利多（481）和克雷色利亚（488）的游走遭遇耗时不固定，不能从撞闪脚本启动时就运行 OCR 或开始 300 秒硬超时。当前流程复用脚本自身的搜图条件，以 `@宝可表` 返回的截断整数小于 `95` 作为进入战斗事件：

```text
启动游走脚本
  -> 寻找草丛或水域：不运行 OCR，也不限制等待时长
  -> `宝可表 < 95`：确认进入战斗，开始 OCR 并从此刻计算 300 秒
  -> 监测 `出现了！ -> 去吧/上吧` 的关键词间隔
```

搜图事件由原生 backend 的现有结果 callback 分发，观察者同时绑定视频源 generation 和脚本 run generation，并在本次脚本结束后移除，不能覆盖预览识别框使用的 callback。游走脚本未产生战斗事件便结束，或战斗后的关键词 OCR 超时，都属于结果未知：runner 进入 `FAILED` 并等待人工确认，不能按未出闪逃跑续搜。其他定点目标仍在撞闪脚本运行期间立即启动 OCR，并保留普通 OCR 超时按未出闪继续的兼容行为。

## 页面布局

### 顶部操作栏

放在页面最上方，始终可见：
- 运行模式：`单次` / `循环 N 次` / `无限循环`
- 循环次数：仅在 `循环 N 次` 时启用
- 主按钮：`开始自动乱数`
- 次按钮：`暂停`、`停止`
- 状态徽标：`空闲`、`测 seed`、`搜索目标`、`过帧中`、`重新识别`、`撞闪中`、`完成`、`失败`

停止行为先设计为软停止：
- 若当前没有脚本运行，立即停止。
- 若 EasyCon 脚本正在运行，调用 Bridge 的 `stop_current_script()`，等待返回后停止。
- Project_Xs 捕捉中则复用现有 `capture_cancel` 思路。

### 左侧：目标与筛选

这一块复用 BDSP 定点页面的概念，但在自动页独立展示：
- 存档信息：版本、TID、SID、TSV、闪符等。
- 定点目标：分类、宝可梦、等级、模板特性、锁闪信息、固定 IV 数。
- 乱数信息：Seed0/Seed1、初始帧、最大帧数、Offset、队首特性。
- 个体筛选：IV 范围、特性、性别、性格、异色、身高、体重、取消筛选。

推荐实现时抽出 `StaticSearchCriteria` 和 `StaticSearchForm`：
- BDSP 手动页和自动页都从控件生成同一个 criteria。
- 自动页可以提供 `从 BDSP 页同步` 按钮，但不依赖 BDSP 页当前控件状态。

### 中间：自动决策参数

使用一个紧凑的 `自动策略` 分组：
- 最大帧数范围：默认可沿用用户填写，支持到 1,000,000,000。
- 固定 delay：默认 100，可手动改。
- 最大等待帧数：默认 300，可手动改。
- `校正策略设置...` 按钮：打开模态设置窗口，不在主表单逐项展开以下参数。
  - 校正帧数上限：默认 900,000，可随时修改；当前运行使用启动时的配置快照。
  - 普通校正最大尝试次数：默认 2，最小 1，无额外业务上限。
  - 普通校正连续失败后：选择 `进入下一轮` 或 `先重测 Seed`。
  - 重测 Seed 最大尝试次数：默认 1，最小 1，仅在选择补救策略时启用。
  - 过场预留帧数：默认 500,000，设为 0 时关闭过场策略。
- 最终撞闪安全下限：内置 5，不在界面展示。
- 无目标处理：默认 `运行测种脚本后重新捕获 seed`。
- 目标选择：默认 `最低帧数`，未来可扩展 `手动选择`。

### 中间：脚本选择

三个下拉框都读取 `D:\codex_project\auto_bdsp_rng\script` 下 `.txt` 和 `.ecs` 文件：
- 测种脚本：默认匹配 `BDSP测种.txt`。
- 过帧脚本：默认匹配 `bdsp过帧.txt`。
- 撞闪脚本：默认让用户选择，常用 `谢米.txt` 或 `玫瑰公园.txt`。

辅助按钮：
- `刷新脚本列表`
- `查看参数`：显示当前脚本可被扫描到的 `_参数名`
- `试填预览`：不运行，只显示参数填充后的脚本文本和参数值

参数填充规则：
- 过帧脚本必须包含 `_目标帧数`，否则开始前报错。
- 撞闪脚本可选整数 `_闪帧`；声明后必须是固定数字，未声明则自动使用 runner 软件等待模式。
- 测种脚本可以无参数。

### 右侧：实时运行面板

建议包含四个区域：

1. 当前循环摘要
- 当前循环：`3 / 10` 或 `无限循环第 3 次`
- 当前 seed：S0-S3 与 Seed0/Seed1
- 锁定目标：宝可梦、raw target、trigger advances、delay
- 当前 advances：reidentify 后更新
- 剩余到撞闪：`remaining_to_trigger`

2. 决策时间线
- `测 seed`
- `搜索目标`
- `运行测种脚本`
- `运行过帧脚本`
- `重新识别 / 重新测 seed`
- `运行撞闪脚本`

每个节点显示：等待中、运行中、成功、失败、跳过。

3. 候选结果表
- 复用 BDSP 结果列。
- 默认高亮最低帧结果。
- 若已锁定目标，单独标记“当前锁定”。

4. 日志
- 每一步写明输入和决策结果，例如：
  - `捕获 Seed 成功：Seed0=..., Seed1=...`
  - `在 10,000,000 帧内找到 4 个目标，锁定最低帧 1,000`
  - `trigger=900, current=0, remaining=900 > max_wait=300，运行过帧脚本`
  - `本轮过帧请求 900，超过阈值？否，下一步 reidentify`
  - `reidentify 得到 current=600，remaining=300，进入最终校准`
  - `final reidentify current=335，实时修正后 remaining_to_trigger=5，运行固定 _闪帧 脚本`

## 状态机设计

```mermaid
stateDiagram-v2
    [*] --> Idle
    Idle --> CaptureSeed: start
    CaptureSeed --> SearchTarget: seed ok
    CaptureSeed --> Failed: seed failed
    SearchTarget --> RunSeedScript: no target
    SearchTarget --> DecideAdvance: target found
    RunSeedScript --> CaptureSeed: script completed
    RunSeedScript --> Failed: script failed
    DecideAdvance --> FinalCalibrate: remaining_to_trigger <= max_wait_frames
    DecideAdvance --> RunAdvanceScript: remaining_to_trigger > max_wait_frames
    RunAdvanceScript --> CaptureSeed: requested_advance > reseed_threshold
    RunAdvanceScript --> Reidentify: requested_advance <= reseed_threshold
    RunAdvanceScript --> Failed: script failed
    Reidentify --> DecideAdvance: target still locked
    Reidentify --> SearchTarget: target lock invalid
    Reidentify --> CaptureSeed: ordinary failure and recapture policy
    Reidentify --> RunSeedScript: ordinary failure and next-round policy
    CaptureSeed --> SearchTarget: recovery succeeded in same cycle
    CaptureSeed --> RunSeedScript: recovery attempts exhausted
    FinalCalibrate --> RunHitScript: remaining_to_trigger safe
    FinalCalibrate --> SearchTarget: missed or too close
    FinalCalibrate --> Failed: calibration failed
    RunHitScript --> LoopCheck: script completed
    RunHitScript --> Failed: script failed
    LoopCheck --> CaptureSeed: next cycle
    LoopCheck --> Completed: finite loop done
    Completed --> Idle
    Failed --> Idle
```

## 目标锁定策略

推荐第一版采用“锁定目标 + 必要时重搜”的折中策略：

- 初次测 seed 后搜索目标，选最低帧并锁定。
- 普通流程中过帧量不超过用户配置的校正帧数上限（默认 90 万）时，使用 reidentify；reidentify 返回的 advances 可继续用于同一个锁定目标，计算 `remaining_to_trigger`。
- 普通流程中过帧量超过该上限时，重新捕获 Seed；旧目标与当前 Seed 的相对关系不再可靠，必须清除旧目标并重新搜索、锁定新的最低帧。
- 过场脚本运行后是例外：无论校正失败还是后续过帧超过上限，都不得在当前位置完整重测 Seed，而是运行测种脚本进入下一轮。
- 如果 reidentify 后发现 `remaining_to_trigger <= 0`，说明已经错过触发点，本轮标记为 `target_missed`，回到 `SearchTarget` 或进入下一循环。
- 如果已经进入 `max_wait_frames`，先进入 `FinalCalibrate`，确认脚本启动点仍未错过，再运行固定 `_闪帧` 的撞闪脚本。

这个策略既符合“过帧后知道当前第几帧继续逼近目标”的需求，又允许长距离过帧后重新校准。

## 服务层设计

建议新增一个自动流程服务，而不是让自动页直接调用按钮：

```text
auto_bdsp_rng/
  automation/
    auto_rng/
      models.py        # AutoRngConfig, AutoRngState, AutoRngDecision
      runner.py        # 状态机与循环控制
      scripts.py       # 脚本选择、参数校验、试填
      search.py        # criteria -> StaticGenerator8 -> candidates
  ui/
    auto_rng_panel.py  # 新 Tab
```

服务层职责：
- 从 UI 配置生成 `AutoRngConfig`。
- 调用 Project_Xs 捕获 seed / reidentify。
- 调用 StaticGenerator8 搜索候选。
- 调用 EasyCon Bridge 运行脚本文本。
- 发出状态、日志、候选列表、错误事件给 UI。

UI 职责：
- 展示配置与状态。
- 接收用户开始/暂停/停止。
- 不直接做长耗时逻辑。

线程模型：
- 自动 runner 放入 `QThread` 或独立 worker。
- Project_Xs 捕获与 EasyCon Bridge 脚本运行都由 runner 串行等待。
- UI 只通过 Qt Signal 更新。

## 异常与安全规则

- 开始前校验 EasyCon Bridge 已连接。
- 开始前校验三个脚本文件存在且编码为 UTF-8。
- 开始前校验过帧脚本包含 `_目标帧数`。
- 开始前校验撞闪脚本中的可选 `_闪帧`（若声明）是固定数字。
- `raw_target_advances <= fixed_delay + (fixed_flash_frames or 0)` 时不允许启动撞闪，提示 delay 或脚本等待量过大。
- `remaining_to_trigger < 0` 时判定已错过目标，不运行撞闪；无 `_闪帧` 模式允许恰好为 0 时启动。
- 最终校准后若已经越过脚本启动点则不运行撞闪。
- 最终校准后的 `remaining_to_trigger < min_final_flash_frames` 时判定距离太近，放弃本目标。
- 普通校正和补救测 Seed 的尝试次数最小为 1；用户主动停止时立即退出，不计失败，也不再触发重试或下一轮脚本。
- 过场脚本运行后只能使用固定 2 次的过场校正；不得调用完整 Seed 捕获作为失败补救。
- `max_wait_frames` 建议最小 1，避免填入 0 导致脚本边界不清。
- 每次脚本完成后必须记录 exit code、stdout、stderr。
- 自动流程失败时保留日志、源脚本名称和本次使用的参数；不生成运行快照。

## 第一版验收标准

- 新 Tab 能展示完整自动配置。
- 三个脚本下拉框能读取 `script` 目录。
- 自动页能用与 BDSP 页面一致的筛选条件得到候选结果。
- 无目标时能运行测种脚本并回到测 seed。
- 有目标时能按最低帧锁定目标。
- 能按 `fixed_delay` 计算 `trigger_advances`。
- 能按 `max_wait_frames` 决定过帧还是撞闪。
- 过帧脚本运行前能填 `_目标帧数`。
- 撞闪脚本运行前能做最终实时校准，并保持脚本内固定 `_闪帧` 不被自动流程改写。
- 过帧脚本完成后能按用户配置的校正帧数上限（默认 90 万）选择普通校正或重新捕获 Seed。
- 普通校正次数和失败策略可配置；补救测 Seed 成功后在同一轮清除旧目标并重新搜索，全部失败后进入下一轮。
- 过场校正固定最多 2 次，过场脚本运行后所有失败和超阈值分支都不会原地完整测 Seed。
- 支持单次、循环 N 次、无限循环。
- 支持停止当前流程。
