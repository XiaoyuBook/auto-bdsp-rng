# Task Plan: 自动定点乱数实现

## Goal
实现 `自动定点乱数` 页面与服务层，把 Project_Xs 测 seed、BDSP 定点搜索、EasyCon 过帧脚本、最终 reidentify / capture seed 与撞闪脚本串成可测试、可停止、可观察的自动流程。

## Current Phase
Phase 7: Verification & Delivery

## Phases

### Phase 1: Requirements & Discovery
- [x] 记录自动 RNG 联动需求、delay 语义、脚本参数规则和 UI 验收项
- [x] 调研现有主窗口、BDSP 定点搜索、Project_Xs 和 EasyCon Bridge 接口
- [x] Document findings in findings.md
- **Status:** complete

### Phase 2: Design & TODO
- [x] 设计自动定点乱数页面信息架构
- [x] 设计状态机、脚本填参、过帧后校准和最终实时校准规则
- [x] 生成 `docs/auto_rng_design.md` 与 `docs/auto_rng_todo.md`
- **Status:** complete

### Phase 3: Foundation Implementation
- [x] 新增 `automation.auto_rng` 包与基础模型
- [x] 新增脚本读取、校验、`_目标帧数` / `_闪帧` 替换工具
- [x] 抽出 BDSP 定点搜索服务并保持手动 BDSP 页行为
- [x] 新增自动决策纯函数和单元测试
- [x] 新增 `AutoRngPanel` UI 骨架并接入 MainWindow 第四个 Tab
- **Status:** complete

### Phase 4: Rebase Conflict Resolution
- [x] 解决 `main_window.py` rebase 冲突
- [x] 保留当前基线的 `QLineEdit` 数值读取方式
- [x] 调整 UI 测试并完成 rebase
- **Status:** complete

### Phase 5: Runner 状态机与 mock 集成
- [x] 为 `AutoRngRunner` 定义可注入 Project_Xs / EasyCon / search 接口
- [x] 用 mock 测试覆盖无候选 -> 测种脚本 -> CaptureSeed
- [x] 用 mock 测试覆盖候选 -> 过帧脚本 -> reidentify / capture seed
- [x] 用 mock 测试覆盖 FinalCalibrate -> 生成撞闪脚本 -> run_script_text
- [x] 支持 loop single / count / infinite 的第一版控制
- [x] 支持停止请求与当前 EasyCon 脚本 stop
- **Status:** complete

### Phase 6: UI 与真实接口接线
- [x] `AutoRngPanel` 收集真实搜索条件，而不是占位说明
- [x] 通过 QThread/Signal 驱动 runner，避免 UI 线程执行长流程
- [x] 接入 Project_Xs capture/reidentify 封装
- [x] 接入 EasyCon Bridge `run_script_text()` 与 `stop_current_script()`
- **Status:** complete

### Phase 7: Verification & Delivery
- [x] 跑自动 RNG 目标测试
- [x] 跑可用的全量测试并记录已知无关失败
- [x] 更新 TODO、progress 和 findings
- [x] 输出剩余真机验证项与建议提交信息
- **Status:** complete

## Key Decisions
| Decision | Rationale |
|----------|-----------|
| 第四个 Tab 名称固定为 `自动定点乱数` | 用户明确指定 |
| 使用现有 `script` 目录 | 用户明确要求不新增 `scripts` |
| `fixed_delay` 只用于计算撞闪脚本启动点 | 避免污染 seed、搜索结果或 current advances |
| 过帧脚本填理论 `remaining_to_trigger` | `bdsp过帧.txt` 内部已有预留逻辑 |
| `_闪帧` 必须在 FinalCalibrate 后实时计算 | 避免使用旧搜索剩余帧造成启动延迟误差 |
| Runner 先做接口注入 + mock 测试 | 不阻塞在真机 Project_Xs / EasyCon 环境 |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| 系统 `python` 为 3.14 且无 pytest | 首次运行目标测试 | 改用 `.venv\Scripts\python.exe` |
| rebase 冲突在 `main_window.py` | `git rebase --continue` 前 | 保留搜索服务接入与当前基线 `.text()` 读取 |
| 全量测试中 EasyConPanel 旧测试失败 | rebase 后全量 pytest | 判定与本次自动 RNG 冲突无关，记录为现存基线差异 |

## Notes
- 当前工作树只有 `third_party/Project_Xs_CHN` submodule 显示未暂存改动；不属于本轮变更，不处理。
- 继续实现时遵循 TDD：先补 mock runner 测试，再写状态机代码。

## 2026-05-08 UI Layout Correction

### Goal
按用户澄清修正“自动定点乱数”页：删除错误摘要/候选主区域，新增独立完整的“目标精灵设置”，自动流程使用自动页自己的目标与筛选条件。

### Checklist
- [x] 使用 superpowers 与 planning-with-files
- [x] 写失败测试保护布局与自动搜索数据源
- [x] 删除脚本信息/校验摘要主区域
- [x] 删除 `定点目标 / 存档信息 / 个体筛选` 摘要框
- [x] 删除候选结果主表
- [x] 左侧移动为自动策略、脚本、日志
- [x] 右侧保留运行摘要并加入目标精灵设置
- [x] 自动搜索读取自动页独立目标表单
- [x] 日志记录搜索候选数量和最低帧锁定结果
- [x] 跑全量测试

### Verification
- `.venv\Scripts\python.exe -m pytest tests\test_ui.py -k "auto_rng_panel_has_editable_target_form or auto_rng_services_search_uses_auto_target_form"` 先失败，确认测试覆盖旧错误。
- `.venv\Scripts\python.exe -m pytest` 通过：173 passed。

## 2026-05-08 Locked Target Detail UI

### Goal
优化自动页右侧顶部：运行摘要更紧凑，并新增独立“锁定目标”单体详情区，展示最低帧候选的完整目标信息。

### Checklist
- [x] 写失败测试覆盖锁定目标详情显示
- [x] 写失败测试覆盖太近/错过时清空 locked target
- [x] 右侧顶部改为运行摘要与锁定目标并排
- [x] 运行摘要改为紧凑 key-value 网格
- [x] 锁定目标显示 Adv、EC、PID、Shiny、Nature、Ability、IVs、Gender、Height/Weight、Characteristic
- [x] 锁定目标显示 raw/trigger/delay 与 current/remaining/final
- [x] 不恢复候选结果大表
- [x] 保持目标精灵设置完整可编辑，日志仍在左侧
- [x] 跑全量测试

### Verification
- `.venv\Scripts\python.exe -m pytest tests\test_ui.py -k "locked_target or auto_rng_summary"` 通过：3 passed。
- `.venv\Scripts\python.exe -m pytest tests\automation\test_auto_rng_runner.py -k "clears_locked_target"` 通过：1 passed。
- `.venv\Scripts\python.exe -m pytest` 通过：177 passed。
