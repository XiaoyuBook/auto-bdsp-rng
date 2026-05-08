# 自动定点乱数 TODO

## 0. 设计确认

- [x] 确认页面名称使用 `自动定点乱数`。
- [x] 确认脚本目录使用现有 `script`，不新增 `scripts`。
- [x] 确认 `fixed_delay` 的语义为：目标帧前提前运行撞闪脚本。
- [x] 确认过帧脚本 `_目标帧数` 填 `remaining_to_trigger`，不额外扣脚本内部预留值。
- [x] 确认撞闪脚本 `_闪帧` 填最终实时校准后的 `flash_frames`，不是旧的 `remaining_to_trigger`。
- [x] 确认默认 `max_wait_frames = 300`。
- [x] 确认默认 `reseed_threshold_frames = 1_000_000`。
- [x] 确认默认 `min_final_flash_frames = 30` 或 `50`。

## 1. 数据模型

- [x] 新增 `AutoRngConfig`：循环模式、循环次数、最大帧数、fixed delay、最大等待帧数、重新测 seed 阈值、三个脚本路径、搜索条件。
- [x] 新增 `AutoRngProgress`：当前阶段、循环序号、seed、锁定目标、当前 advances、剩余帧、最终校准帧、最后脚本路径、日志。
- [x] 新增 `AutoRngDecision`：`no_target`、`run_seed_script`、`run_advance_script`、`run_hit_script`、`reidentify`、`capture_seed`、`complete`、`failed`。
- [x] 抽出或定义 `StaticSearchCriteria`，让自动页和 BDSP 页能共享筛选语义。

## 2. 脚本适配

- [x] 复用 `scan_builtin_scripts()` 获取 `.txt` / `.ecs`。
- [x] 新增脚本默认选择规则：测种优先 `BDSP测种.txt`，过帧优先 `bdsp过帧.txt`，撞闪不强制默认或优先最近选择。
- [x] 新增 `validate_auto_scripts()`：检查文件存在、UTF-8、必需参数。
- [x] 新增 `prepare_advance_script(path, frames)`：填 `_目标帧数`。
- [x] 新增 `prepare_hit_script(path, flash_frames)`：填 `_闪帧`。
- [x] 保留临时脚本到 `script\.generated`，日志记录生成路径。

## 3. 搜索服务

- [x] 将当前 `generate_results()` 中的搜索逻辑抽出为纯函数或服务。
- [x] 输入：SeedPair64、Profile8、StaticEncounterRecord、StateFilter、initial advances、max advances、offset、lead。
- [x] 输出：按 `advances` 升序排列的 `State8` 列表。
- [x] 增加选择策略：第一版固定选择最低帧。
- [x] 处理 `shiny_mode == none` 的后置过滤，保持与当前 BDSP 页一致。

## 4. 自动流程 Runner

- [x] 新增 runner 状态机：`Idle -> CaptureSeed -> SearchTarget -> DecideAdvance -> RunAdvanceScript/Reidentify/CaptureSeed/RunHitScript -> LoopCheck`。
- [ ] CaptureSeed 阶段复用 Project_Xs 捕捉与 `recover_seed_from_observation()`。
- [ ] Reidentify 阶段复用 `reidentify_seed_from_observation()`。
- [x] 过帧后若请求过帧量 `> 1_000_000`，下一步走 CaptureSeed。
- [x] 过帧后若请求过帧量 `<= 1_000_000`，下一步走 Reidentify。
- [x] SearchTarget 无结果时运行测种脚本，然后回到 CaptureSeed。
- [x] SearchTarget 有结果时锁定最低帧目标。
- [x] DecideAdvance 计算 `trigger_advances = raw_target_advances - fixed_delay`。
- [x] 确保 `fixed_delay` 不修改 seed、不修改搜索结果、不加到 `current_advances`。
- [x] DecideAdvance 计算 `remaining_to_trigger = trigger_advances - current_advances`。
- [x] `remaining_to_trigger <= 0` 时判定错过目标，不运行撞闪。
- [x] `remaining_to_trigger <= max_wait_frames` 时进入 FinalCalibrate，不直接运行撞闪脚本。
- [x] `remaining_to_trigger > max_wait_frames` 时运行过帧脚本。
- [ ] FinalCalibrate 执行最终 reidentify 或最终 capture seed。
- [x] FinalCalibrate 记录 `current_advances_at_ref` 和 `ref_time`。
- [x] FinalCalibrate 在提交撞闪脚本前用时间差计算 `live_current_advances`。
- [x] FinalCalibrate 计算 `flash_frames = trigger_advances - live_current_advances`。
- [x] `flash_frames <= 0` 时判定错过目标，不运行撞闪。
- [x] `flash_frames < min_final_flash_frames` 时判定距离太近，放弃本目标并重新测 seed / 搜索。
- [x] FinalCalibrate 安全通过后运行撞闪脚本，填 `_闪帧 = flash_frames`。
- [x] LoopCheck 支持单次、循环 N 次、无限循环。
- [x] 支持用户停止，并能停止当前 EasyCon Bridge 脚本或 Project_Xs 捕捉。

## 5. UI

- [x] 在 `MainWindow` 的 `QTabWidget` 中新增第四个 Tab。
- [x] 新建 `AutoRngPanel`，不要把自动流程直接塞进 `MainWindow`。
- [x] 顶部操作栏：循环模式、循环次数、开始、暂停、停止、状态徽标。
- [ ] 左侧配置区：目标、存档、Seed/最大帧、筛选项。
- [x] 中间策略区：fixed delay、最大等待帧数、重新测 seed 阈值。
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
- [x] 单元测试：过帧请求超过 1,000,000 后下一步为重新捕获 seed。
- [x] 单元测试：过帧请求不超过 1,000,000 后下一步为 reidentify。
- [x] 单元测试：runner 无候选时运行测种脚本并回到 CaptureSeed。
- [x] 单元测试：runner 过帧脚本填 `_目标帧数` 后按阈值进入 reidentify。
- [x] 单元测试：runner FinalCalibrate 后填 `_闪帧` 并运行撞闪脚本。
- [x] 单元测试：runner 单次和循环 N 次模式按 LoopCheck 完成。
- [x] UI 测试：新增 Tab 存在。
- [x] UI 测试：开始前缺少必需脚本参数会显示错误。
- [x] UI 测试：开始时发出完整 `AutoRngConfig`。
- [x] UI 测试：`AutoRngProgress` 通过面板入口更新摘要和日志。
- [x] UI 测试：`AutoRngWorker` 通过 Qt Signal 发出 progress/log/finished。

## 7. 风险与后续问题

- [ ] 明确“暂停”是否需要真正暂停状态机，还是第一版只支持停止。
- [ ] 明确撞闪脚本内部已有的固定扣帧逻辑是否需要 UI 提示；自动流程第一版只填最终实时剩余帧 `flash_frames`，不额外替脚本扣内置值。
- [ ] 明确重新捕获 seed 后是否必须重新锁定目标；当前方案建议重新搜索并锁定新最低帧。
- [ ] 后续补充循环停止条件，例如成功识别闪、用户确认、截图判定、脚本输出关键字。
