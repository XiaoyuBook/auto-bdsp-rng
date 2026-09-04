# 自动定点乱数 TODO

## 0. 设计确认

- [x] 确认页面名称使用 `自动定点乱数`。
- [x] 确认脚本目录使用现有 `script`，不新增 `scripts`。
- [x] 确认兼容字段 `fixed_delay` 保存基准 delay；固定策略直接使用，动态策略无有效样本时回退使用。
- [x] 确认过帧脚本 `_目标帧数` 填 `remaining_to_trigger`，不额外扣脚本内部预留值。
- [x] 确认撞闪脚本 `_闪帧` 固定为数字，自动流程读取该数字但不再动态改写。
- [x] 确认默认 `max_wait_frames = 300`。
- [x] 确认 `reseed_threshold_frames` 默认 900,000，并在“校正策略设置”中由用户配置。
- [x] 确认普通校正最大尝试次数默认 2、最小 1，失败后可选择进入下一轮或先完整重测 Seed。
- [x] 确认补救测 Seed 最大尝试次数默认 1、最小 1；过场脚本运行后禁止使用该补救。
- [x] 确认 delay 支持固定、上次实际、众数、中位数、滚动平均、指数平滑、截尾平均和密集区间 8 种策略；没有有效样本时回退基准 delay。
- [x] 确认多候选轮可忽略或按轮等权处理，“上次实际 delay”始终只接受单候选轮。
- [x] 确认 delay 在新轮次开始时冻结，同轮重搜、逃跑续搜、补救测 Seed 和过场流程不重新计算。
- [x] 确认内置 `min_final_flash_frames = 5`，不在 UI 展示。

## 1. 数据模型

- [x] 新增 `AutoRngConfig`：循环模式、循环次数、最大帧数、基准/动态 delay 配置及原始轮次样本、最大等待帧数、重新测 seed 阈值、三个脚本路径、搜索条件。
- [x] 新增 `AutoRngProgress`：当前阶段、循环序号、seed、锁定目标、当前 advances、剩余帧、最终校准帧、最后脚本路径、日志。
- [x] 新增 `AutoRngDecision`：`no_target`、`run_seed_script`、`run_advance_script`、`run_hit_script`、`reidentify`、`capture_seed`、`complete`、`failed`。
- [x] 抽出或定义 `StaticSearchCriteria`，让自动页和 BDSP 页能共享筛选语义。

## 2. 脚本适配

- [x] 复用 `scan_builtin_scripts()` 获取 `.txt` / `.ecs`。
- [x] 新增脚本默认选择规则：测种优先 `BDSP测种.txt`，过帧优先 `bdsp过帧.txt`，撞闪不强制默认或优先最近选择。
- [x] 新增 `validate_auto_scripts()`：检查文件存在、UTF-8、必需参数。
- [x] 新增 `prepare_advance_script(path, frames)`：填 `_目标帧数`。
- [x] 新增 `prepare_hit_script(path, flash_frames)`：填 `_闪帧`。
- [x] 原生运行直接执行内存脚本文本；不再生成或保留 `script\.generated` ECS 快照。

## 3. 搜索服务

- [x] 将当前 `generate_results()` 中的搜索逻辑抽出为纯函数或服务。
- [x] 输入：SeedPair64、Profile8、StaticEncounterRecord、StateFilter、initial advances、max advances、offset、lead。
- [x] 输出：按 `advances` 升序排列的 `State8` 列表。
- [x] 增加选择策略：第一版固定选择最低帧。
- [x] 处理 `shiny_mode == none` 的后置过滤，保持与当前 BDSP 页一致。

## 4. 自动流程 Runner

- [x] 新增 runner 状态机：`Idle -> CaptureSeed -> SearchTarget -> DecideAdvance -> RunAdvanceScript/Reidentify/CaptureSeed/RunHitScript -> LoopCheck`。
- [x] CaptureSeed 阶段复用 Project_Xs 捕捉与 `recover_seed_from_observation()`。
- [x] Reidentify 阶段复用 `reidentify_seed_from_observation()`。
- [x] 普通流程过帧后若请求过帧量大于用户配置的校正帧数上限，下一步走 CaptureSeed。
- [x] 普通流程过帧后若请求过帧量不超过用户配置的校正帧数上限，下一步走 Reidentify。
- [x] 普通校正按配置次数重试；失败策略可直接进入下一轮，或在当前轮按配置次数完整重测 Seed。
- [x] 补救测 Seed 成功后清除旧目标并重新搜索；全部失败后清空 Seed/目标并运行测种脚本进入下一轮。
- [x] 过场校正及过场后的校正固定最多 2 次，且任何过场脚本运行后都禁止原地完整测 Seed。
- [x] SearchTarget 无结果时运行测种脚本，然后回到 CaptureSeed。
- [x] SearchTarget 有结果时锁定最低帧目标。
- [x] 每轮开始时按动态策略计算并冻结 `round_delay`；直接构造 runner 时兼容回退 `fixed_delay`。
- [x] DecideAdvance 计算 `trigger_advances = raw_target_advances - round_delay - fixed_flash_frames`。
- [x] 确保 `round_delay` 不修改 seed、不修改搜索结果、不加到 `current_advances`。
- [x] 自动反查成功且候选非空时按轮保存原始 delay 候选，新样本只影响下一轮。
- [x] DecideAdvance 计算 `remaining_to_trigger = trigger_advances - current_advances`。
- [x] `remaining_to_trigger <= 0` 时判定错过目标，不运行撞闪。
- [x] `remaining_to_trigger <= max_wait_frames` 时进入 FinalCalibrate，不直接运行撞闪脚本。
- [x] `remaining_to_trigger > max_wait_frames` 时运行过帧脚本。
- [x] FinalCalibrate 执行最终 reidentify 或最终 capture seed。
- [x] FinalCalibrate 记录 `current_advances_at_ref` 和 `ref_time`。
- [x] FinalCalibrate 在提交撞闪脚本前用时间差计算 `live_current_advances`。
- [x] FinalCalibrate 计算 `remaining_to_trigger = trigger_advances - live_current_advances`。
- [x] `remaining_to_trigger <= 0` 时判定错过脚本启动点，不运行撞闪。
- [x] `remaining_to_trigger < min_final_flash_frames` 时判定距离太近，放弃本目标并重新测 seed / 搜索。
- [x] FinalCalibrate 安全通过后按原文运行固定 `_闪帧` 的撞闪脚本。
- [x] LoopCheck 支持单次、循环 N 次、无限循环。
- [x] 支持用户停止，并能停止当前 EasyCon Bridge 脚本或 Project_Xs 捕捉。

## 5. UI

- [x] 在 `MainWindow` 的 `QTabWidget` 中新增第四个 Tab。
- [x] 新建 `AutoRngPanel`，不要把自动流程直接塞进 `MainWindow`。
- [x] 顶部操作栏：循环模式、循环次数、开始、暂停、停止、状态徽标。
- [x] 左侧配置区：目标、存档、Seed/最大帧、筛选项。
- [x] 中间策略区：可点击的 delay 策略摘要、最大等待帧数和“校正策略设置...”按钮；delay 弹窗集中配置策略与样本，校正弹窗集中配置阈值、失败策略、相关尝试次数与过场预留帧数，最终安全帧保留为内部常量。
- [x] 中间脚本区：测种脚本、过帧脚本、撞闪脚本、刷新脚本、参数预览。
- [x] 右侧运行摘要：当前循环、阶段、seed、锁定目标、当前帧、剩余帧。
- [x] 右侧运行摘要显示 delay、trigger advances、final flash frames。
- [x] 右侧候选表：显示本次搜索结果并高亮锁定目标。
- [x] 右侧日志：滚动显示每一步决策与脚本结果。
- [x] 状态变化通过 Qt Signal 更新 UI，避免 worker 直接操作控件。

## 6. 测试

- [x] 单元测试：脚本下拉读取 `script` 目录。
- [x] 单元测试：`_目标帧数` 参数填充。
- [x] 单元测试：`_闪帧` 参数填充。
- [x] 单元测试：目标帧 1000、delay 100、current 0、max wait 300 时决策为过帧，填 900。
- [x] 单元测试：目标帧 1000、delay 100、current 600、max wait 300 时决策为 FinalCalibrate，而不是直接撞闪。
- [x] 单元测试：目标帧 1300、delay 1200、current 0 时，实时无误差下 `_闪帧 = 100`。
- [x] 单元测试：final calibration 参考 current=600、elapsed 约 2.036s、npc=0 时，`flash_frames` 从 300 修正为 298。
- [x] 单元测试：`flash_frames <= 0` 时不运行撞闪。
- [x] 单元测试：`flash_frames < min_final_flash_frames` 时不运行撞闪。
- [x] 单元测试：无候选时决策为运行测种脚本。
- [x] 单元测试：普通流程过帧请求超过配置上限后下一步为重新捕获 Seed。
- [x] 单元测试：普通流程过帧请求不超过配置上限后下一步为 reidentify。
- [x] 单元测试：普通校正次数、补救测 Seed 次数、失败回到下一轮和停止时不重试。
- [x] 单元测试：过场校正固定 2 次，过场后失败或过帧超上限都不直接完整测 Seed。
- [x] UI 测试：策略按钮、弹窗默认值、补救次数始终可编辑、取消回滚、恢复默认值、持久化和无额外次数上限。
- [x] 单元测试：8 种 delay 策略、无样本回退、多候选忽略/按轮加权、窗口和确定性取整。
- [x] 单元测试：runner 逐轮冻结 delay，锁定目标和反查继续使用本轮值，新样本从下一轮生效。
- [x] UI 测试：delay 策略弹窗的说明、专属参数显隐、历史样本持久化/清空和摘要刷新。
- [x] 单元测试：runner 无候选时运行测种脚本并回到 CaptureSeed。
- [x] 单元测试：runner 过帧脚本填 `_目标帧数` 后按阈值进入 reidentify。
- [x] 单元测试：runner FinalCalibrate 后填 `_闪帧` 并运行撞闪脚本。
- [x] 单元测试：runner 单次和循环 N 次模式按 LoopCheck 完成。
- [x] UI 测试：新增 Tab 存在。
- [x] UI 测试：开始前缺少必需脚本参数会显示错误。
- [x] UI 测试：开始时发出完整 `AutoRngConfig`。
- [x] UI 测试：`AutoRngProgress` 通过面板入口更新摘要和日志。
- [x] UI 测试：`AutoRngWorker` 通过 Qt Signal 发出 progress/log/finished。
- [x] UI 测试：MainWindow 启动自动流程时创建 `AutoRngRunner` 并交给 `AutoRngPanel.run_with_runner()`。
- [x] UI 测试：MainWindow 自动 RNG search service 使用 BDSP 当前搜索快照。
- [x] UI 测试：自动页展示启动时实际采用的 BDSP 搜索上下文摘要。
- [x] UI 测试：MainWindow 自动 RNG Project_Xs capture/reidentify 适配可通过 mock 测试。
- [x] UI 测试：MainWindow 自动 RNG EasyCon Bridge `run_script_text()` 适配可通过 mock 测试。

## 7. 风险与后续问题

- [ ] 明确“暂停”是否需要真正暂停状态机，还是第一版只支持停止。
- [x] 明确撞闪脚本内部固定扣帧逻辑应移除；自动流程使用固定 `_闪帧`，并通过本轮冻结的 `round_delay` 决定脚本启动点。
- [x] 明确重新捕获 Seed 后必须废弃旧锁定目标，并重新搜索、锁定新最低帧。
- [ ] 后续补充循环停止条件，例如成功识别闪、用户确认、截图判定、脚本输出关键字。
