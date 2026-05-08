# Findings & Decisions

## Requirements
- 新增页面：自动定点乱数界面。
- 页面包含精灵个体筛选设置，可参考“定点数据区”的筛选方法。
- 用户设置最大帧数范围，例如 1000 万帧；测完 seed 后在该范围内搜索目标个体。
- 若范围内无目标，运行“测种脚本”进入下一轮。
- 需要三个脚本下拉框：测种脚本、过帧脚本、撞闪脚本；下拉项读取脚本目录中的所有文件。
- 若范围内存在目标，默认选择帧数最低的目标进行乱数。
- 用户设置固定 delay；目标帧减 delay 得到过帧目标。
- 过帧脚本需要自动填入参数 `_目标帧数`。
- 用户设置最大等待帧数；若剩余帧数小于等于该值，不再运行过帧脚本，改由撞闪脚本等待。
- 撞闪脚本目前包括 `谢米.txt`、`玫瑰公园.txt`，它们都有 `_闪帧` 参数，需要自动填入最终等待帧。
- 每次过帧脚本完成后，需要根据过帧量判断：超过 100 万帧则重新捕获 seed；未超过则调用 project_xs 的 reidentify 识别当前帧。
- 单次自动化流程：测 seed -> 搜目标 -> 过帧 -> 重新测 seed 或 reidentify -> 循环过帧 -> 进入最大等待范围 -> 运行撞闪脚本。
- 首页/页面上需要选择无限循环或循环指定次数；停止条件后续补充。
- 本轮不写业务代码，只设计 UI 与实现思路，并生成 TODO 文件。

## Research Findings
- 项目是 Python 桌面应用，UI 技术栈为 PySide6。
- 源码主目录为 `src\auto_bdsp_rng`，UI 位于 `src\auto_bdsp_rng\ui`。
- 脚本目录实际为 `script`，不是复数 `scripts`。
- 当前脚本文件包括 `BDSP测种.txt`、`BDSP地下测种.txt`、`bdsp过帧.txt`、`谢米.txt`、`玫瑰公园.txt`、`谢米运行情况.txt`。
- project_xs 适配代码位于 `src\auto_bdsp_rng\blink_detection\project_xs.py`。
- `MainWindow` 当前以 `QTabWidget` 组织三个页面：Project_Xs、BDSP / PokeFinder、EasyCon。
- BDSP 定点页面已有可复用控件组：`_build_rng_info_group()` 负责 Seed、初始帧、最大帧数、Offset；`_build_static_group()` 负责目标精灵；`_build_filter_group()` 负责 IV、特性、性别、性格、异色、身高、体重筛选。
- `generate_results()` 已经基于当前 Seed、目标、筛选项和最大帧数生成 `State8` 列表，并保存到 `self._states`；结果中的 `state.advances` 就是目标帧。
- `capture_seed()` 和 `reidentify_seed()` 已有后台线程、Project_Xs 捕捉、结果写回 Seed 输入框、状态栏更新等流程，可作为自动流程调用能力的基础。
- `reidentify_seed_from_observation()` 已支持 `search_max`，当前 UI 里默认使用 `max(100_000, self.max_advances.value())`。
- EasyCon 面板已有 `scan_builtin_scripts(SCRIPT_DIR)` 列出 `script` 目录下 `.txt`/`.ecs` 文件。
- `parse_script_parameters()` 能识别 `_参数名 = 值` 形式；`apply_parameter_values()` 能替换参数；`generate_script_file()` 会写入 `script\.generated` 临时脚本。
- EasyCon Bridge 后端有 `run_script_text(script_text, name)`，脚本执行完成后返回 `EasyConRunResult`，比直接复用面板按钮更适合作为自动流程的服务接口。
- `bdsp过帧.txt` 中存在 `_目标帧数 = 填写目标帧数`，自动流程可以填入本轮过帧帧数。
- `谢米.txt` 和 `玫瑰公园.txt` 中均存在 `_闪帧`，自动流程可以填入进入最后等待区间后的剩余帧数。

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| 自动流程按状态机设计 | 测种、搜目标、过帧、识别、撞闪、循环这些阶段有明确状态与转移条件 |
| 将“最大等待帧数”作为用户可调安全阈值 | 避免小帧数仍调用过帧脚本导致过头 |
| 新页面作为第四个 Tab 接入主窗口 | 当前 UI 已用 `QTabWidget` 分隔 Project_Xs、BDSP 和 EasyCon，自动页应成为同级工作台 |
| 自动页复用现有定点筛选控件的数据模型 | 避免维护两套筛选逻辑，并保证搜索结果与手动 BDSP 页一致 |
| 自动页通过服务层调用 EasyCon Bridge 而不是直接操纵 EasyConPanel UI | 自动流程需要串行等待脚本完成、获取成功/失败状态，服务接口更稳定 |

## Issues Encountered
| Issue | Resolution |
|-------|------------|

## Resources
- Project root: `D:\codex_project\auto_bdsp_rng`
- UI: `D:\codex_project\auto_bdsp_rng\src\auto_bdsp_rng\ui`
- EasyCon automation: `D:\codex_project\auto_bdsp_rng\src\auto_bdsp_rng\automation\easycon`
- Scripts: `D:\codex_project\auto_bdsp_rng\script`

## Visual/Browser Findings
- 本轮暂未使用浏览器或截图。
