# 日志文案审阅清单

核对日期：2026-10-25（北京时间）。代码基准：`aa39517`，项目版本 `3.4.4`；包含当前本地修改后的文案，并不表示这些修改已发布。

跨轮输出可直接查看 [报错后进入下一轮的完整模拟记录](D:/codex_project/auto_bdsp_rng/docs/LOG_RECOVERY_SIMULATION.md)：包含测种失败重试、校正失败换轮、连续失败保护停止和脚本异常停止，正文取自实际自动测试输出。

这里列的是当前代码会生成的文案模板，不是运行记录，也不是待上线的改稿。花括号表示运行时替换的真实数值；推进数通常带千位分隔，目标 TID 显示六位数字。重复使用同一文案的入口合并并保留代码定位。

本次核对覆盖 **146 处带摘要的调用**，展开条件分支并合并相同文案；另逐项检查 **134 个原始警告 / 错误入口**，多数仍使用通用提示，部分脚本错误可转为专用行号提示。原始异常、路径、第三方输出的内容可以无限变化，不逐字穷举；其用户展示方式与所有已发现入口均在下表交代。

审阅范围是右下角实时日志、完整日志的“用户日志 / 仅看问题”以及默认详情。页面内的原始输出框、错误弹窗和状态栏不属于本表的展示保证；特别是错误弹窗仍可能展示更详细的原文。

可以直接按编号反馈，例如：`E01 保留；T01 改为……；F148 应显示具体操作失败。` 本次记录当前文案与触发规则，后续修改以你的审阅意见为准。

## 优先审阅

共 315 个编号审阅项，包含同一通用文案在不同触发入口的使用情况，不等于同样数量的不同原句。

- **E 表 37 条**：专门编写的失败、重试和需要人工确认的提示。
- **T 表 21 条**：不同任务步骤的统一失败提示。
- **C 表 6 条**：仅在原因明确时追加的补充说明。
- **G 表 2 条 + F 表 134 个入口**：仍共用空泛提示，需要确认哪些应补写。
- **R / N 表 105 条**：轮次结果与正常进展。
- **H 表 10 条**：有摘要但仅保留在诊断视图的记录。

当前仍需关注：一些文案保留“请检查画面 / 手柄连接”等建议；过帧脚本启动失败先记信息，随后可能再记停止错误；自动反查失败实际记为信息级别；手动测种报错仍可能只显示通用警告；历史记录中错过目标等警告仍可能额外出现通用提醒。以下保留现状供逐条确认。

**建议先看这些编号：**

- E02、E17、E22、E31、E36：仍带检查画面、识别设置或手柄等建议。
- E02：同一次启动失败可能先出现信息，再出现 T06 的停止错误。
- E07：反查失败当前为信息级别，流程会继续到下一轮判断。
- E14、E37、F006、F148：标题仍没有交代具体受阻操作或原因。
- E15：仍提示“请展开详情”，需要确认是否应明确指向“原始诊断”。
- F059：程序已得到疑似报错行号，但当前文案格式未被再次提取，用户区仍显示通用错误。
- F139、F140：历史记录的错过目标警告仍使用通用提醒。

## E：失败、重试和人工确认

补充说明会接在正文下方。标为“信息”的条目确实是当前代码的级别，并非本次重新指定。

| 编号 | 来源 / 触发场景 | 当前用户文案 | 实际级别 / 审阅备注 | 代码 |
| --- | --- | --- | --- | --- |
| E01 | 自动定点<br>自动定点测种未成功，尚未达到保护停止次数 | 测种失败（连续 {失败次数}/5），将重新运行测种脚本 | 警告<br>确认原因时才追加 C 类补充；没有明确原因时留空。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:679) |
| E02 | 自动定点<br>过帧脚本启动或执行 | 过帧脚本未能启动，请检查脚本和手柄连接 | 信息<br>含检查建议，需审阅是否保留、是否够具体。实际先记信息，异常上抛后还会出现 T 表的过帧脚本执行失败停止提示。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1021) |
| E03 | 自动定点<br>普通校正连续失败后的重测补救 | 位置校正连续 {校正失败次数} 次失败，正在重新测种补救 | 警告 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1151) |
| E04 | 自动定点<br>普通校正连续失败后的重测补救 | 补救测种失败，准备第 {下次尝试次数}/{补救上限} 次尝试 | 警告 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1177) |
| E05 | 自动定点<br>动态调整撞闪时机并执行脚本；撞闪或御三家选择操作 | 撞闪脚本已执行完成，请人工确认游戏结果<br>补充：本轮没有自动判闪结果；脚本执行完成不能代替游戏结果确认。 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1386) [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1502) |
| E06 | 自动定点<br>取得判闪结果后的处理分支 | 检测到疑似闪光，已停止自动流程，请人工确认<br>补充：识别间隔 {间隔秒数} 秒。本轮判闪阈值 {阈值秒数} 秒。请核对当前游戏画面，确认前不要重启任务。 | 信息<br>判闪阈值只在已配置时显示；间隔、阈值保留三位小数。未取得计时时第一句也可能为“未取得完整判闪计时。” | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1568) |
| E07 | 自动定点<br>自动反查个体及 delay | 自动反查失败，请查看完整日志中的诊断详情 | 信息<br>文案包含失败，实际日志级别为信息。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1729) |
| E08 | 自动定点<br>自动反查个体及 delay | 延迟样本保存失败，本轮继续使用原策略 | 警告 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1764) |
| E09 | 自动定点<br>自动反查个体及 delay | 反查结果疑似闪光，流程已停止，请人工确认 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1775) |
| E10 | 自动定点<br>没有完整判闪计时，需要人工确认并停止 | 无法确认是否为闪光，流程已停止，请人工确认当前战斗<br>补充：没有获得完整的判闪计时。请先核对当前游戏结果，确认后再开始下一轮。 | 错误 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1825) |
| E11 | 自动定点<br>校正、过场后校正或过场校正失败，仍有重试次数 | 校正失败，准备第 {下次尝试次数}/{尝试上限} 次尝试 | 警告<br>确认原因时才追加 C 类补充；没有明确原因时留空。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1971) |
| E12 | 自动定点<br>校正、过场后校正或过场校正失败，仍有重试次数 | 过场后校正失败，准备第 {下次尝试次数}/{尝试上限} 次尝试 | 警告<br>确认原因时才追加 C 类补充；没有明确原因时留空。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1971) |
| E13 | 自动定点<br>校正、过场后校正或过场校正失败，仍有重试次数 | 过场校正失败，准备第 {下次尝试次数}/{尝试上限} 次尝试 | 警告<br>确认原因时才追加 C 类补充；没有明确原因时留空。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1971) |
| E14 | 自动定点<br>本轮不能继续，运行测种脚本重新开始；原事件已有用户提示时保留原提示 | 本轮未能继续，正在重新测种 | 警告 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1991) |
| E15 | 自动 TID<br>用户请求停止任务 | 已请求停止自动 TID，停止脚本时发生错误，请展开详情 | 警告<br>入口用语仍是“展开详情”；默认详情当前只显示用户说明。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_tid_rng.py:495) |
| E16 | 自动 TID<br>自动 TID 测种结果 | 测种失败，将重新运行测种脚本 | 警告<br>确认原因时才追加 C 类补充；没有明确原因时留空。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_tid_rng.py:987) |
| E17 | 自动 TID<br>识别游戏内 TID | 未识别到有效 TID，流程已停止，请检查游戏画面 | 错误<br>含检查建议，需审阅是否保留、是否够具体。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_tid_rng.py:1304) |
| E18 | 自动 TID<br>根据识别 TID 匹配并校准 delay | 未找到匹配的 TID，流程已停止，请检查识别结果 | 错误<br>含检查建议，需审阅是否保留、是否够具体。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_tid_rng.py:1318) |
| E19 | 通用日志<br>脚本标准输出或错误输出 | 脚本返回错误输出 | 错误<br>确认原因时才追加 C 类补充；没有明确原因时留空。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/log_events.py:168) |
| E20 | 伊机控 / 脚本<br>刷新串口列表 | 刷新串口失败，请检查设备连接后重试 | 警告<br>含检查建议，需审阅是否保留、是否够具体。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2073) |
| E21 | 伊机控 / 脚本<br>刷新串口列表 | 未发现串口，请连接设备后刷新 | 警告 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2096) |
| E22 | 伊机控 / 脚本<br>脚本执行失败 | 脚本执行失败，请检查脚本和手柄连接 | 错误<br>含检查建议，需审阅是否保留、是否够具体。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2575) [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2721) |
| E23 | 伊机控 / 脚本<br>自动流程调用脚本失败 | 自动流程脚本执行失败，请查看完整日志中的诊断详情 | 错误 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2798) [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2849) |
| E24 | 伊机控 / 脚本<br>脚本运行终态 | 脚本执行失败，请查看完整日志中的诊断详情 | 错误 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2945) |
| E25 | 伊机控 / 脚本<br>脚本报错中能提取到有效行号；有堆栈的原文不套用此行号提示 | 脚本第 {行号} 行执行失败<br>补充：请在脚本编辑区查看已标记的错误行，修正后重新运行。 | 错误 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:3193) |
| E26 | 伊机控 / 脚本<br>伊机控连接、取消连接或断开结果；连接伊机控 | 连接伊机控失败，请检查串口和设备连接 | 错误<br>含检查建议，需审阅是否保留、是否够具体。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:3475) [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:3537) |
| E27 | 伊机控 / 脚本<br>伊机控连接、取消连接或断开结果；断开伊机控 | 未能断开伊机控，请查看完整日志中的诊断详情 | 错误 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:3498) [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:3556) |
| E28 | 对应任务<br>关闭窗口时请求任务停止发生异常 | 停止流程时出现问题，请检查任务是否已退出 | 警告<br>含检查建议，需审阅是否保留、是否够具体。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:4947) |
| E29 | 视频源<br>刷新视频采集设备失败 | 刷新采集设备失败，请检查连接后重试 | 警告<br>含检查建议，需审阅是否保留、是否够具体。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:6004) |
| E30 | 视频源<br>视频源连接结果 | 视频源连接失败，请检查采集设备和连接设置 | 错误<br>含检查建议，需审阅是否保留、是否够具体。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:6263) |
| E31 | OCR<br>手动文字识别失败；启动文字识别失败；后台文字识别失败；手动 TID 文字识别失败 | 文字识别失败，请检查画面和识别设置后重试 | 错误<br>含检查建议，需审阅是否保留、是否够具体。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:7334) [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:7427) [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:7458) [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:7496) [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:7573) |
| E32 | 视频源<br>预览识别设置无效，视频预览保留 | 预览识别设置无效，请检查识别设置，视频预览继续显示 | 警告<br>含检查建议，需审阅是否保留、是否够具体。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:7836) |
| E33 | 视频源<br>识别框绘制失败，回退到原始画面 | 识别框暂时无法显示，已恢复原始画面 | 警告 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:7906) |
| E34 | 视频源<br>实际读取视频画面失败 | 视频画面读取失败，正在停止视频源，请检查采集设备 | 错误<br>含检查建议，需审阅是否保留、是否够具体。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:7939) |
| E35 | 历史记录<br>历史记录或本轮结果回调 | 本轮结果：检测到疑似闪光，请人工确认 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:9016) |
| E36 | Seed 捕捉<br>手动测种或校正结束后的结果处理 | 未取得捕捉结果，请检查画面后重新尝试 | 警告<br>含检查建议，需审阅是否保留、是否够具体。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:10899) |
| E37 | 应用 / 调用方指定来源<br>应用统一错误处理入口 | 操作失败，请查看完整日志中的诊断详情 | 错误<br>目前不同操作共用这一条提示，操作名称保留在原始诊断。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:11291) |

## T：按受阻步骤生成的停止提示

这些是步骤中发生异常后的标题模板；具体异常内容留在原始诊断。没有明确原因时不追加推测性说明。

| 编号 | 来源 / 触发场景 | 当前用户文案 | 实际级别 / 审阅备注 | 代码 |
| --- | --- | --- | --- | --- |
| T01 | 自动定点 / 自动 TID<br>异常发生在对应步骤：捕获Seed | 测种失败，流程已停止 | 错误<br>受阻步骤名来自当前任务阶段，不能据此推断失败原因。有明确原因时追加 C 类补充。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/log_events.py:158) |
| T02 | 自动定点 / 自动 TID<br>异常发生在对应步骤：TID/SID测种 | TID 测种失败，流程已停止 | 错误<br>受阻步骤名来自当前任务阶段，不能据此推断失败原因。有明确原因时追加 C 类补充。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/log_events.py:158) |
| T03 | 自动定点 / 自动 TID<br>异常发生在对应步骤：搜索目标 | 目标搜索失败，流程已停止 | 错误<br>受阻步骤名来自当前任务阶段，不能据此推断失败原因。有明确原因时追加 C 类补充。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/log_events.py:158) |
| T04 | 自动定点 / 自动 TID<br>异常发生在对应步骤：搜索目标Display TID | 目标 TID 搜索失败，流程已停止 | 错误<br>受阻步骤名来自当前任务阶段，不能据此推断失败原因。有明确原因时追加 C 类补充。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/log_events.py:158) |
| T05 | 自动定点 / 自动 TID<br>异常发生在对应步骤：运行测种脚本 | 测种脚本执行失败，流程已停止 | 错误<br>受阻步骤名来自当前任务阶段，不能据此推断失败原因。有明确原因时追加 C 类补充。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/log_events.py:158) |
| T06 | 自动定点 / 自动 TID<br>异常发生在对应步骤：运行过帧脚本 | 过帧脚本执行失败，流程已停止 | 错误<br>受阻步骤名来自当前任务阶段，不能据此推断失败原因。有明确原因时追加 C 类补充。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/log_events.py:158) |
| T07 | 自动定点 / 自动 TID<br>异常发生在对应步骤：运行撞闪脚本 | 撞闪脚本执行失败，流程已停止 | 错误<br>受阻步骤名来自当前任务阶段，不能据此推断失败原因。有明确原因时追加 C 类补充。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/log_events.py:158) |
| T08 | 自动定点 / 自动 TID<br>异常发生在对应步骤：运行取名脚本 | 取名脚本执行失败，流程已停止 | 错误<br>受阻步骤名来自当前任务阶段，不能据此推断失败原因。有明确原因时追加 C 类补充。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/log_events.py:158) |
| T09 | 自动定点 / 自动 TID<br>异常发生在对应步骤：运行反查ID脚本 | 反查脚本执行失败，流程已停止 | 错误<br>受阻步骤名来自当前任务阶段，不能据此推断失败原因。有明确原因时追加 C 类补充。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/log_events.py:158) |
| T10 | 自动定点 / 自动 TID<br>异常发生在对应步骤：运行逃跑脚本 | 逃跑脚本执行失败，流程已停止 | 错误<br>受阻步骤名来自当前任务阶段，不能据此推断失败原因。有明确原因时追加 C 类补充。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/log_events.py:158) |
| T11 | 自动定点 / 自动 TID<br>异常发生在对应步骤：校正位置 | 位置校正失败，流程已停止 | 错误<br>受阻步骤名来自当前任务阶段，不能据此推断失败原因。有明确原因时追加 C 类补充。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/log_events.py:158) |
| T12 | 自动定点 / 自动 TID<br>异常发生在对应步骤：最终校准 | 最终位置校正失败，流程已停止 | 错误<br>受阻步骤名来自当前任务阶段，不能据此推断失败原因。有明确原因时追加 C 类补充。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/log_events.py:158) |
| T13 | 自动定点 / 自动 TID<br>异常发生在对应步骤：运行过场脚本 | 过场校正失败，流程已停止 | 错误<br>受阻步骤名来自当前任务阶段，不能据此推断失败原因。有明确原因时追加 C 类补充。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/log_events.py:158) |
| T14 | 自动定点 / 自动 TID<br>异常发生在对应步骤：反查个体 | 个体反查失败，流程已停止 | 错误<br>受阻步骤名来自当前任务阶段，不能据此推断失败原因。有明确原因时追加 C 类补充。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/log_events.py:158) |
| T15 | 自动定点 / 自动 TID<br>异常发生在对应步骤：等待触发 | 撞闪等待失败，流程已停止 | 错误<br>受阻步骤名来自当前任务阶段，不能据此推断失败原因。有明确原因时追加 C 类补充。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/log_events.py:158) |
| T16 | 自动定点 / 自动 TID<br>异常发生在对应步骤：等待取名帧 | 取名等待失败，流程已停止 | 错误<br>受阻步骤名来自当前任务阶段，不能据此推断失败原因。有明确原因时追加 C 类补充。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/log_events.py:158) |
| T17 | 自动定点 / 自动 TID<br>异常发生在对应步骤：等待反查帧 | 反查等待失败，流程已停止 | 错误<br>受阻步骤名来自当前任务阶段，不能据此推断失败原因。有明确原因时追加 C 类补充。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/log_events.py:158) |
| T18 | 自动定点 / 自动 TID<br>异常发生在对应步骤：OCR识别TID | TID 识别失败，流程已停止 | 错误<br>受阻步骤名来自当前任务阶段，不能据此推断失败原因。有明确原因时追加 C 类补充。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/log_events.py:158) |
| T19 | 自动定点 / 自动 TID<br>异常发生在对应步骤：校准delay | 延迟校准失败，流程已停止 | 错误<br>受阻步骤名来自当前任务阶段，不能据此推断失败原因。有明确原因时追加 C 类补充。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/log_events.py:158) |
| T20 | 自动定点<br>异常发生时没有匹配到更具体的步骤名 | 自动定点失败，流程已停止 | 错误<br>任务级通用兜底；有明确原因时追加 C 类补充。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/log_events.py:158) |
| T21 | 自动 TID<br>异常发生时没有匹配到更具体的步骤名 | 自动 TID失败，流程已停止 | 错误<br>任务级通用兜底；有明确原因时追加 C 类补充。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/log_events.py:158) |

## C：已确认原因的补充说明

当事件已有明确编写的补充时使用该补充；否则警告、错误可匹配下表。普通失败不会自动等同于超时、画面问题、框选问题或模板问题。

| 编号 | 来源 / 触发场景 | 当前用户文案 | 实际级别 / 审阅备注 | 代码 |
| --- | --- | --- | --- | --- |
| C01 | 通用补充<br>连续测得相同 Seed 且无候选 | 连续测得相同 Seed 且未找到目标。请确认测种流程已正确重置。 | 跟随原记录<br>按本表顺序取首个匹配条件；不是每次失败都显示。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/log_events.py:132) |
| C02 | 通用补充<br>达到连续 5 次测种失败的保护条件 | 已连续 5 次测种失败，触发保护停止。 | 跟随原记录<br>按本表顺序取首个匹配条件；不是每次失败都显示。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/log_events.py:134) |
| C03 | 通用补充<br>明确发生串口连接异常或单片机连接失败 | 请检查单片机连接和串口选择，关闭占用串口的其他程序，然后重新连接伊机控。 | 跟随原记录<br>按本表顺序取首个匹配条件；不是每次失败都显示。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/log_events.py:136) |
| C04 | 通用补充<br>已捕获明确的文件不存在异常 | 未找到所需文件，请检查对应设置。 | 跟随原记录<br>按本表顺序取首个匹配条件；不是每次失败都显示。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/log_events.py:138) |
| C05 | 通用补充<br>已捕获明确的权限异常 | 访问被拒绝，请检查对应文件或设备的权限。 | 跟随原记录<br>按本表顺序取首个匹配条件；不是每次失败都显示。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/log_events.py:140) |
| C06 | 通用补充<br>已捕获明确的超时异常 | 等待操作结果超时。 | 跟随原记录<br>按本表顺序取首个匹配条件；不是每次失败都显示。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/log_events.py:142) |

## G：通用兜底原句

| 编号 | 来源 / 触发场景 | 当前用户文案 | 实际级别 / 审阅备注 | 代码 |
| --- | --- | --- | --- | --- |
| G01 | 通用兜底<br>没有适配用户摘要的警告 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>适用入口逐项见 F 表；不是具体失败原因。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/log_events.py:61) |
| G02 | 通用兜底<br>没有适配用户摘要的错误或严重错误 | 操作未能完成，请查看完整日志中的诊断详情 | 错误 / 严重<br>适用入口逐项见 F 表；不是具体失败原因。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/log_events.py:63) |

## F：仍使用通用提示的警告与错误入口

这部分最能反映当前仍然“看不出发生了什么”的场景。此表逐个保留入口，编号延续扫描序号，跳号项是转发或脚本 stdout/stderr，已在其它表中列出，不代表漏项。伊机控错误若能提取脚本行号，会先转为 E 表的行号提示；异常原文不能直接进入用户正文。

| 编号 | 来源 / 触发场景 | 当前用户文案 | 实际级别 / 审阅备注 | 代码 |
| --- | --- | --- | --- | --- |
| F001 | 自动定点<br>已选脚本文件不存在 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/auto_rng_panel.py:3277) |
| F003 | 自动定点<br>自动定点启动前校验失败 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/auto_rng_panel.py:3596) |
| F004 | 自动定点<br>重复启动自动定点 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/auto_rng_panel.py:3665) |
| F006 | 自动 TID<br>手动 ID 搜索失败 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/auto_tid_rng_panel.py:1421) |
| F007 | 自动 TID<br>重复启动自动 TID | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/auto_tid_rng_panel.py:1463) |
| F009 | 自动 TID<br>ID 结果复制或导出失败 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/auto_tid_rng_panel.py:1998) |
| F011 | 自动 TID<br>自动 TID 启动前校验失败 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/auto_tid_rng_panel.py:2079) |
| F012 | 自动 TID<br>添加目标时输入的 Display TID 不合法 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/auto_tid_rng_panel.py:2142) |
| F013 | 自动 TID<br>编辑目标时未选择目标或输入不合法 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/auto_tid_rng_panel.py:2152) |
| F014 | 自动定点 / 自动 TID<br>请求停止自动任务失败 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/automation_lifecycle.py:60) |
| F015 | 伊机控 / 脚本<br>无法查看脚本库 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_execution.py:426) |
| F016 | 伊机控 / 脚本<br>旧版脚本路径迁移后保存配置失败 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:902) |
| F017 | 伊机控 / 脚本<br>旧版运行快照清理失败 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:909) |
| F018 | 伊机控 / 脚本<br>打开菜单时暂停键盘捕获失败 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 若原文能识别出脚本报错行号，会优先显示 E 类“脚本第 {行号} 行执行失败”。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:1678) |
| F019 | 伊机控 / 脚本<br>关闭菜单后恢复键盘捕获失败 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 若原文能识别出脚本报错行号，会优先显示 E 类“脚本第 {行号} 行执行失败”。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:1688) |
| F020 | 伊机控 / 脚本<br>连接状态轮询报告连接问题 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2013) |
| F021 | 伊机控 / 脚本<br>伊机控运行环境不可用 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2063) |
| F022 | 伊机控 / 脚本<br>自动选择串口时未发现可用串口 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2105) |
| F023 | 伊机控 / 脚本<br>录制中尝试打开其他脚本 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2182) |
| F024 | 伊机控 / 脚本<br>脚本运行中尝试打开其他脚本 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2185) |
| F025 | 伊机控 / 脚本<br>读取脚本文件失败 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2194) |
| F026 | 伊机控 / 脚本<br>保存最近使用脚本记录失败 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2218) |
| F027 | 伊机控 / 脚本<br>录制中尝试保存完整脚本 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2240) |
| F028 | 伊机控 / 脚本<br>当前运行状态不允许保存脚本 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2243) |
| F029 | 伊机控 / 脚本<br>脚本内容为空，无法保存 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2246) |
| F030 | 伊机控 / 脚本<br>保存现有脚本失败 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 若原文能识别出脚本报错行号，会优先显示 E 类“脚本第 {行号} 行执行失败”。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2257) |
| F031 | 伊机控 / 脚本<br>另存脚本失败 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 若原文能识别出脚本报错行号，会优先显示 E 类“脚本第 {行号} 行执行失败”。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2279) |
| F032 | 伊机控 / 脚本<br>运行前脚本参数校验失败 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 若原文能识别出脚本报错行号，会优先显示 E 类“脚本第 {行号} 行执行失败”。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2372) |
| F033 | 伊机控 / 脚本<br>运行脚本的配置尚未完成 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2421) |
| F034 | 伊机控 / 脚本<br>创建运行用临时脚本失败 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 若原文能识别出脚本报错行号，会优先显示 E 类“脚本第 {行号} 行执行失败”。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2429) |
| F035 | 伊机控 / 脚本<br>使用命令行运行方式时的连接提示 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2450) |
| F036 | 伊机控 / 脚本<br>未连接伊机控就运行脚本 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2468) |
| F037 | 伊机控 / 脚本<br>脚本内容或参数尚未准备完成 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2470) |
| F038 | 伊机控 / 脚本<br>脚本执行服务不可用 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 若原文能识别出脚本报错行号，会优先显示 E 类“脚本第 {行号} 行执行失败”。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2477) |
| F039 | 伊机控 / 脚本<br>已有脚本运行，再次请求运行 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2480) |
| F040 | 伊机控 / 脚本<br>脚本运行线程启动失败 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 若原文能识别出脚本报错行号，会优先显示 E 类“脚本第 {行号} 行执行失败”。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2530) |
| F043 | 伊机控 / 脚本<br>暂停后的修改未能应用，脚本仍保持暂停 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 若原文能识别出脚本报错行号，会优先显示 E 类“脚本第 {行号} 行执行失败”。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2622) |
| F044 | 伊机控 / 脚本<br>停止当前脚本失败 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 若原文能识别出脚本报错行号，会优先显示 E 类“脚本第 {行号} 行执行失败”。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2635) |
| F045 | 伊机控 / 脚本<br>未连接伊机控就运行脚本 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2649) |
| F048 | 伊机控 / 脚本<br>脚本结束后刷新连接状态失败 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 若原文能识别出脚本报错行号，会优先显示 E 类“脚本第 {行号} 行执行失败”。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2704) |
| F049 | 伊机控 / 脚本<br>停止当前脚本任务失败 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 若原文能识别出脚本报错行号，会优先显示 E 类“脚本第 {行号} 行执行失败”。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2743) |
| F056 | 伊机控 / 脚本<br>脚本进程启动失败 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 若原文能识别出脚本报错行号，会优先显示 E 类“脚本第 {行号} 行执行失败”。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2917) |
| F057 | 伊机控 / 脚本<br>清理运行用临时脚本失败 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2965) |
| F058 | 伊机控 / 脚本<br>脚本编译失败，未得到报错行号 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 若原文能识别出脚本报错行号，会优先显示 E 类“脚本第 {行号} 行执行失败”。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2975) |
| F059 | 伊机控 / 脚本<br>脚本编译失败，已得到疑似报错行号 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 若原文能识别出脚本报错行号，会优先显示 E 类“脚本第 {行号} 行执行失败”。 此处已有疑似行号，但“疑似行号: 12”这种原句经现有提取函数验证仍返回无行号，因此用户区仍丢失这项信息。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2977) |
| F060 | 伊机控 / 脚本<br>命令行运行时单片机连接失败 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 若原文能识别出脚本报错行号，会优先显示 E 类“脚本第 {行号} 行执行失败”。 同时补充 C03 串口连接检查提示。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2982) |
| F061 | 伊机控 / 脚本<br>关闭确认前释放键盘控制失败 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 若原文能识别出脚本报错行号，会优先显示 E 类“脚本第 {行号} 行执行失败”。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:3059) |
| F062 | 伊机控 / 脚本<br>保存伊机控日志文件失败 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 若原文能识别出脚本报错行号，会优先显示 E 类“脚本第 {行号} 行执行失败”。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:3294) |
| F063 | 伊机控 / 脚本<br>脚本运行中尝试切换串口 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:3344) |
| F064 | 伊机控 / 脚本<br>连接前未选择串口 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:3347) |
| F065 | 伊机控 / 脚本<br>连接服务不可用 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 若原文能识别出脚本报错行号，会优先显示 E 类“脚本第 {行号} 行执行失败”。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:3357) |
| F066 | 伊机控 / 脚本<br>断开连接时服务不可用 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 若原文能识别出脚本报错行号，会优先显示 E 类“脚本第 {行号} 行执行失败”。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:3392) |
| F067 | 伊机控 / 脚本<br>处理伊机控连接结果失败 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 若原文能识别出脚本报错行号，会优先显示 E 类“脚本第 {行号} 行执行失败”。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:3429) |
| F068 | 伊机控 / 脚本<br>串口连接成功，但保存配置失败 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:3488) |
| F069 | 伊机控 / 脚本<br>连接时未选择所需连接程序 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:3521) |
| F070 | 伊机控 / 脚本<br>连接前未选择串口 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:3525) |
| F071 | 伊机控 / 脚本<br>未连接伊机控就发送手柄按键 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:3637) |
| F072 | 伊机控 / 脚本<br>发送手柄按键失败 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 若原文能识别出脚本报错行号，会优先显示 E 类“脚本第 {行号} 行执行失败”。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:3643) |
| F073 | 伊机控 / 脚本<br>未连接伊机控就发送手柄按键 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:3653) |
| F074 | 伊机控 / 脚本<br>发送手柄按键失败 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 若原文能识别出脚本报错行号，会优先显示 E 类“脚本第 {行号} 行执行失败”。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:3660) |
| F075 | 伊机控 / 脚本<br>未连接伊机控就请求亮屏保活 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:3679) |
| F076 | 伊机控 / 脚本<br>未连接伊机控就请求亮屏保活 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:3683) |
| F077 | 伊机控 / 脚本<br>亮屏保活按键发送失败，捕捉继续 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:3705) |
| F078 | 伊机控 / 脚本<br>亮屏保活按键发送失败，捕捉继续 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:3772) |
| F079 | 伊机控 / 脚本<br>清理失效的连接失败 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:3789) |
| F080 | 伊机控 / 脚本<br>退出时停止伊机控脚本失败 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:3860) |
| F081 | 伊机控 / 脚本<br>退出时释放或停止设备失败 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:3904) |
| F082 | 伊机控 / 脚本<br>关闭程序时伊机控或脚本进程尚未退出 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:3917) |
| F083 | 伊机控 / 脚本<br>亮屏保活所需运行方式不可用 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:3957) |
| F084 | 伊机控 / 脚本<br>亮屏保活前未选择串口 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:3960) |
| F085 | 伊机控 / 脚本<br>生成亮屏保活脚本失败，捕捉继续 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:3965) |
| F086 | 伊机控 / 脚本<br>亮屏保活命令行任务失败，捕捉继续 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:4011) |
| F087 | 伊机控 / 脚本<br>亮屏保活命令行任务启动失败，捕捉继续 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:4021) |
| F088 | 伊机控 / 脚本<br>亮屏保活命令行任务超时，捕捉继续 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:4028) |
| F089 | 伊机控 / 脚本<br>清理亮屏保活临时脚本失败 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:4045) |
| F090 | 伊机控 / 脚本<br>亮屏保活任务未及时停止，取消后续脚本启动 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:4056) |
| F091 | 伊机控 / 脚本<br>未连接伊机控就测试摇杆 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:4072) |
| F092 | 伊机控 / 脚本<br>发送摇杆动作失败 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 若原文能识别出脚本报错行号，会优先显示 E 类“脚本第 {行号} 行执行失败”。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:4078) |
| F093 | 伊机控 / 脚本<br>未连接伊机控就测试摇杆 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:4088) |
| F094 | 伊机控 / 脚本<br>发送摇杆动作失败 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 若原文能识别出脚本报错行号，会优先显示 E 类“脚本第 {行号} 行执行失败”。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:4095) |
| F095 | 伊机控 / 脚本<br>键盘捕获尚未完全停止，不能打开按键映射 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 若原文能识别出脚本报错行号，会优先显示 E 类“脚本第 {行号} 行执行失败”。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:4111) |
| F096 | 伊机控 / 脚本<br>当前运行方式不支持手柄状态悬浮窗 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:4232) |
| F097 | 伊机控 / 脚本<br>未连接伊机控就打开手柄状态悬浮窗 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:4235) |
| F098 | 伊机控 / 脚本<br>脚本运行中尝试启用键盘控制 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:4280) |
| F099 | 伊机控 / 脚本<br>未连接伊机控就启用键盘控制 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:4284) |
| F100 | 伊机控 / 脚本<br>键盘捕获已停止，不能恢复控制 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 若原文能识别出脚本报错行号，会优先显示 E 类“脚本第 {行号} 行执行失败”。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:4293) |
| F101 | 伊机控 / 脚本<br>恢复键盘捕获失败 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 若原文能识别出脚本报错行号，会优先显示 E 类“脚本第 {行号} 行执行失败”。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:4299) |
| F102 | 伊机控 / 脚本<br>上次键盘捕获尚未停止，不能重新启用 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 若原文能识别出脚本报错行号，会优先显示 E 类“脚本第 {行号} 行执行失败”。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:4318) |
| F103 | 伊机控 / 脚本<br>键盘捕获启动失败且停止失败 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 若原文能识别出脚本报错行号，会优先显示 E 类“脚本第 {行号} 行执行失败”。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:4349) |
| F104 | 伊机控 / 脚本<br>全局键盘捕获不可用，回退到窗口内捕获 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:4354) |
| F105 | 伊机控 / 脚本<br>应用尚未就绪，不能启用键盘控制 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 若原文能识别出脚本报错行号，会优先显示 E 类“脚本第 {行号} 行执行失败”。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:4363) |
| F106 | 伊机控 / 脚本<br>暂停键盘捕获失败 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 若原文能识别出脚本报错行号，会优先显示 E 类“脚本第 {行号} 行执行失败”。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:4398) |
| F107 | 伊机控 / 脚本<br>停止键盘捕获失败 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 若原文能识别出脚本报错行号，会优先显示 E 类“脚本第 {行号} 行执行失败”。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:4467) |
| F108 | 伊机控 / 脚本<br>键盘捕获尚未停止，取消脚本启动 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 若原文能识别出脚本报错行号，会优先显示 E 类“脚本第 {行号} 行执行失败”。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:4490) |
| F109 | 伊机控 / 脚本<br>键盘捕获意外停止 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 若原文能识别出脚本报错行号，会优先显示 E 类“脚本第 {行号} 行执行失败”。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:4518) |
| F110 | 伊机控 / 脚本<br>键盘控制发送失败 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 若原文能识别出脚本报错行号，会优先显示 E 类“脚本第 {行号} 行执行失败”。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:4631) |
| F111 | 伊机控 / 脚本<br>当前连接方式不支持录制脚本 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:4738) |
| F112 | 伊机控 / 脚本<br>尚未启用键盘控制就开始录制 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:4741) |
| F113 | 伊机控 / 脚本<br>手动命令行测试会触发临时连接的提示 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:4830) |
| F114 | 伊机控 / 脚本<br>已有命令行任务时请求手柄测试 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:4837) |
| F115 | 伊机控 / 脚本<br>手柄测试所需运行方式不可用 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:4844) |
| F116 | 伊机控 / 脚本<br>手柄测试前未选择串口 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:4847) |
| F117 | 伊机控 / 脚本<br>创建手柄测试临时脚本失败 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 若原文能识别出脚本报错行号，会优先显示 E 类“脚本第 {行号} 行执行失败”。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:4852) |
| F118 | 伊机控 / 脚本<br>手柄命令行测试的连接提示 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:4874) |
| F119 | 自动定点<br>自动定点捕捉错误信号 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:2118) |
| F121 | 软件更新<br>启动时自动检查更新失败 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:2457) |
| F122 | 自动定点<br>关闭操作取消了 OCR 初始化，自动任务未启动 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:5037) |
| F123 | 视频源<br>停止视频采集服务失败 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:5895) |
| F124 | 视频源<br>等待视频采集服务退出时仍有错误 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:5937) |
| F125 | Seed 捕捉<br>释放视频预览失败 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:6536) |
| F126 | OCR<br>OCR 自定义识别区域无效，显示默认范围 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:7283) |
| F127 | OCR<br>OCR 自定义识别区域无效，回退到替代范围 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:7469) |
| F128 | OCR<br>OCR 初始化失败 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。初始化成功走信息级别，原文只入诊断；此处列失败分支。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:7614) |
| F129 | 应用 / 视频 / OCR / 测种<br>OCR 初始化失败导致自动任务未启动 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:7637) |
| F130 | OCR<br>测试全部 OCR 的配置准备失败 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:7687) |
| F131 | OCR<br>测试全部 OCR 的后台执行失败 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:7746) |
| F132 | OCR<br>已有 OCR 任务，无法开始全部测试 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:7752) |
| F133 | OCR<br>判闪阈值校准中的自定义识别区域无效，使用替代范围 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:8140) |
| F134 | 应用 / 视频 / OCR / 测种<br>判闪阈值校准失败 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:8222) |
| F135 | 应用 / 视频 / OCR / 测种<br>启动自动任务前自动连接设备失败 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:8490) |
| F136 | 自动定点<br>启动自动定点服务时发生异常 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:8540) |
| F137 | 历史记录<br>历史候选快照未能受理 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:8961) |
| F138 | 历史记录<br>历史候选快照未能受理 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:8975) |
| F139 | 历史记录<br>历史记录收到错过目标事件 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:8987) |
| F140 | 历史记录<br>历史记录归档本轮错过目标的结果 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:8996) |
| F141 | 历史记录<br>反查候选快照未能受理 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:9043) |
| F142 | 历史记录<br>反查时个性未匹配，忽略个性条件后继续 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:9046) |
| F143 | 应用 / 视频 / OCR / 测种<br>主窗口请求亮屏保活按键失败，捕捉继续 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:9136) |
| F144 | 自动定点<br>捕捉精灵信息时读取截图配置失败 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:10423) |
| F145 | 自动定点<br>捕捉精灵笔记页截图失败 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:10430) |
| F146 | 自动定点<br>捕捉精灵能力页截图失败 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:10469) |
| F147 | 自动定点<br>识别精灵能力页失败 | 操作未能完成，请查看完整日志中的诊断详情 | 错误<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:10474) |
| F148 | Seed 捕捉<br>手动测种或校正线程报错 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。这里仍没有直接显示“测种失败”或“校正失败”，需重点审阅。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:10881) |
| F149 | Seed 捕捉<br>退出配置编辑时清理眼睛模板草稿失败 | 操作遇到问题，请查看完整日志中的诊断详情 | 警告<br>尚未编写此场景的用户摘要。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/seed_config_editor.py:299) |

## R：轮次结果、未命中与错过时机

这类事件不一定是程序错误；保留其实际级别和下一步安排。

| 编号 | 来源 / 触发场景 | 当前用户文案 | 实际级别 / 审阅备注 | 代码 |
| --- | --- | --- | --- | --- |
| R01 | 自动定点<br>搜索目标后的分支处理 | 本轮没有可用目标，开始下一轮测种<br>补充：搜索上限 {搜索上限} 帧，匹配 {匹配个体数} 个个体，当前可达候选 0 个（当前推进 {当前推进}）。若持续无候选，可扩大搜索范围或放宽筛选条件。 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:830) [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:833) |
| R02 | 自动定点<br>搜索目标后的分支处理 | 本轮没有可用目标，自动定点流程已结束<br>补充：搜索上限 {搜索上限} 帧，匹配 {匹配个体数} 个个体，当前可达候选 0 个（当前推进 {当前推进}）。若持续无候选，可扩大搜索范围或放宽筛选条件。 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:839) |
| R03 | 自动定点<br>撞闪或御三家选择操作 | 已错过本轮选择时机，本轮未选择精灵 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1412) |
| R04 | 自动定点<br>取得判闪结果后的处理分支 | 本次未检测到闪光，准备逃跑并尝试后续 {后续候选数} 个候选<br>补充：识别间隔 {间隔秒数} 秒。本轮判闪阈值 {阈值秒数} 秒。 | 信息<br>判闪阈值只在已配置时显示；间隔、阈值保留三位小数。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1589) |
| R05 | 自动定点<br>取得判闪结果后的处理分支 | 本次判闪未能确认，准备逃跑并尝试后续 {后续候选数} 个候选<br>补充：未取得完整判闪计时。本轮判闪阈值 {阈值秒数} 秒。 | 信息<br>判闪阈值只在已配置时显示；间隔、阈值保留三位小数。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1589) |
| R06 | 自动定点<br>取得判闪结果后的处理分支 | 本次未检测到闪光，开始自动反查<br>补充：识别间隔 {间隔秒数} 秒。本轮判闪阈值 {阈值秒数} 秒。 | 信息<br>判闪阈值只在已配置时显示；间隔、阈值保留三位小数。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1604) |
| R07 | 自动定点<br>取得判闪结果后的处理分支 | 本次判闪未能确认，开始自动反查<br>补充：未取得完整判闪计时。本轮判闪阈值 {阈值秒数} 秒。 | 信息<br>判闪阈值只在已配置时显示；间隔、阈值保留三位小数。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1604) |
| R08 | 自动定点<br>取得判闪结果后的处理分支 | 本次未检测到闪光，开始下一轮测种<br>补充：识别间隔 {间隔秒数} 秒。本轮判闪阈值 {阈值秒数} 秒。 | 信息<br>判闪阈值只在已配置时显示；间隔、阈值保留三位小数。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1617) [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1627) |
| R09 | 自动定点<br>取得判闪结果后的处理分支 | 本次判闪未能确认，开始下一轮测种<br>补充：未取得完整判闪计时。本轮判闪阈值 {阈值秒数} 秒。 | 信息<br>判闪阈值只在已配置时显示；间隔、阈值保留三位小数。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1617) [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1627) |
| R10 | 自动定点<br>取得判闪结果后的处理分支 | 本次未检测到闪光，自动流程已结束<br>补充：识别间隔 {间隔秒数} 秒。本轮判闪阈值 {阈值秒数} 秒。 | 信息<br>判闪阈值只在已配置时显示；间隔、阈值保留三位小数。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1636) |
| R11 | 自动定点<br>取得判闪结果后的处理分支 | 本次判闪未能确认，自动流程已结束<br>补充：未取得完整判闪计时。本轮判闪阈值 {阈值秒数} 秒。 | 信息<br>判闪阈值只在已配置时显示；间隔、阈值保留三位小数。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1636) |
| R12 | 自动定点<br>根据当前推进与目标决定下一步 | 已错过目标，正在重新搜索<br>补充：当前推进 {当前推进}；目标推进 {目标推进}；脚本启动点 {启动点}；本次过帧 {本次过帧} | 信息<br>只显示当前已有数值的项目。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1844) |
| R13 | 自动定点<br>根据当前推进与目标决定下一步 | 距离目标过近，正在重新搜索<br>补充：当前推进 {当前推进}；目标推进 {目标推进}；脚本启动点 {启动点}；本次过帧 {本次过帧} | 信息<br>只显示当前已有数值的项目。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1844) |
| R14 | 自动 TID<br>搜索目标后的分支处理 | 本轮搜索范围内未找到目标 TID，将重新测种<br>补充：已搜索 {搜索上限} 帧，生成 {生成 ID 数量} 个 ID，目标池 {目标池数量} 个 TID 均未命中。 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_tid_rng.py:1032) |
| R15 | 自动 TID<br>搜索目标后的分支处理 | 目标 TID {目标 TID} 来不及触发，重新测种<br>补充：目标推进 {目标推进}；delay {delay}，脚本应在测种起点之前启动。本轮不执行取名。 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_tid_rng.py:1060) |
| R16 | 自动 TID<br>等待取名时机及检查是否错过 | 已错过取名时机，本轮不执行取名，正在重新测种<br>补充：取名启动点 {触发推进}；当前推进 {当前推进}，已超过 {超出帧数} 帧。 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_tid_rng.py:1107) |
| R17 | 自动 TID<br>等待取名时机及检查是否错过 | 已错过取名时机，本轮不执行取名，正在重新测种<br>补充：取名启动点 {触发推进}；当前推进 {实际推进}，已超过 {超出帧数} 帧。 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_tid_rng.py:1177) |
| R18 | 历史记录<br>历史记录或本轮结果回调 | 本轮结果：未检测到闪光 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:9016) |

## N：正常进展

| 编号 | 来源 / 触发场景 | 当前用户文案 | 实际级别 / 审阅备注 | 代码 |
| --- | --- | --- | --- | --- |
| N01 | 自动定点<br>用户请求停止任务 | 已请求停止自动定点流程 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:567) |
| N02 | 自动定点<br>启动自动定点任务 | 自动定点已开始，正在捕捉 Seed | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:594) |
| N03 | 自动定点<br>启动自动定点任务 | 自动定点已开始，正在校正位置 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:613) |
| N04 | 自动定点<br>测种或补救测种成功 | 测种完成，正在搜索可用目标<br>补充：当前推进 {当前推进}；搜索上限 {搜索上限} 帧。 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:708) |
| N05 | 自动定点<br>搜索目标后的分支处理 | 已锁定目标，开始御三家自动操作<br>补充：可达候选 {可达候选数} 个；目标推进 {目标推进}；本轮 delay {本轮 delay}。 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:870) |
| N06 | 自动定点<br>搜索目标后的分支处理 | 找到 {可达候选数} 个可达候选，已锁定目标<br>补充：目标推进 {目标推进}；当前 {当前推进}；脚本在 {触发推进} 启动（delay {本轮 delay}）。 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:884) |
| N07 | 自动定点<br>测种脚本执行 | 第 {轮次} 轮开始，正在执行测种脚本<br>补充：搜索上限 {搜索上限} 帧；本轮 delay {本轮 delay}。 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:926) |
| N08 | 自动定点<br>测种脚本执行 | 测种脚本执行完成，开始识别 Seed | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:937) |
| N09 | 自动定点<br>过帧脚本启动或执行 | 开始执行过帧脚本，本次推进 {本次推进数} 次 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1011) |
| N10 | 自动定点<br>位置校正成功 | 位置校正完成，当前推进数 {当前推进}<br>补充：上次实际推进 {上次实际推进} 帧；将按新位置检查后续目标。 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1087) |
| N11 | 自动定点<br>过场校正成功 | 过场校正完成，正在重新搜索目标 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1136) |
| N12 | 自动定点<br>等待到达撞闪脚本启动点 | 正在等待撞闪时机，预计还需 {预计秒数} 秒<br>补充：当前推进 {当前推进}；触发推进 {触发推进}；剩余 {剩余帧数} 帧。到点后自动运行撞闪脚本。 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1251) |
| N13 | 自动定点<br>长时间等待进度或内部计时采样 | 仍在等待撞闪，约剩余 {预计秒数} 秒<br>补充：当前推进 {当前推进}；触发推进 {触发推进}；剩余 {剩余帧数} 帧。 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1273) |
| N14 | 自动定点<br>动态调整撞闪时机并执行脚本 | 正在调整撞闪时机 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1345) |
| N15 | 自动定点<br>动态调整撞闪时机并执行脚本 | 开始执行撞闪脚本 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1355) |
| N16 | 自动定点<br>动态调整撞闪时机并执行脚本；撞闪或御三家选择操作 | 判闪结果尚未确认，开始自动反查 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1378) [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1491) |
| N17 | 自动定点<br>撞闪或御三家选择操作 | 开始执行撞闪脚本（本轮第 {本轮尝试次数} 次尝试）<br>补充：实际启动推进 {实际启动推进}；目标推进 {目标推进}；本次 delay {本次 delay}。 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1468) |
| N18 | 自动定点<br>未出闪后执行逃跑并继续尝试 | 开始执行逃跑脚本，准备尝试下一目标 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1650) |
| N19 | 自动定点<br>未出闪后执行逃跑并继续尝试 | 逃跑脚本已执行完成，正在校正位置 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1685) |
| N20 | 自动定点<br>自动反查个体及 delay | 反查完成，找到 {匹配个体数} 个匹配个体<br>补充：匹配 delay：{候选 delay 列表}。 / 没有得到可用的 delay 样本，本轮保留原 delay 设置。 | 信息<br>两种补充择一；最多列出前 8 个 delay，更多时追加“等 {数量} 个”。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1750) |
| N21 | 自动定点<br>自动反查个体及 delay | 反查完成<br>补充：匹配 delay：{候选 delay 列表}。 / 没有得到可用的 delay 样本，本轮保留原 delay 设置。 | 信息<br>两种补充择一；最多列出前 8 个 delay，更多时追加“等 {数量} 个”。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1750) |
| N22 | 自动定点<br>本轮结束后决定继续或结束 | 开始下一轮测种 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1787) [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1791) |
| N23 | 自动定点<br>本轮结束后决定继续或结束 | 自动定点流程已结束，请确认本轮结果 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1794) |
| N24 | 自动定点<br>根据当前推进与目标决定下一步 | 开始执行测种脚本<br>补充：当前推进 {当前推进}；目标推进 {目标推进}；脚本启动点 {启动点}；本次过帧 {本次过帧} | 信息<br>只显示当前已有数值的项目。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1844) |
| N25 | 自动定点<br>根据当前推进与目标决定下一步 | 正在准备过帧<br>补充：当前推进 {当前推进}；目标推进 {目标推进}；脚本启动点 {启动点}；本次过帧 {本次过帧} | 信息<br>只显示当前已有数值的项目。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1844) |
| N26 | 自动定点<br>根据当前推进与目标决定下一步 | 正在进行最终位置校正<br>补充：当前推进 {当前推进}；目标推进 {目标推进}；脚本启动点 {启动点}；本次过帧 {本次过帧} | 信息<br>只显示当前已有数值的项目。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1844) |
| N27 | 自动定点<br>根据当前推进与目标决定下一步 | 正在调整撞闪时机<br>补充：当前推进 {当前推进}；目标推进 {目标推进}；脚本启动点 {启动点}；本次过帧 {本次过帧} | 信息<br>只显示当前已有数值的项目。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1844) |
| N28 | 自动定点<br>根据当前推进与目标决定下一步 | 正在校正当前位置<br>补充：当前推进 {当前推进}；目标推进 {目标推进}；脚本启动点 {启动点}；本次过帧 {本次过帧} | 信息<br>只显示当前已有数值的项目。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1844) |
| N29 | 自动定点<br>根据当前推进与目标决定下一步 | 正在重新测种<br>补充：当前推进 {当前推进}；目标推进 {目标推进}；脚本启动点 {启动点}；本次过帧 {本次过帧} | 信息<br>只显示当前已有数值的项目。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1844) |
| N30 | 自动定点<br>根据当前推进与目标决定下一步 | 自动定点流程已完成<br>补充：当前推进 {当前推进}；目标推进 {目标推进}；脚本启动点 {启动点}；本次过帧 {本次过帧} | 信息<br>只显示当前已有数值的项目。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1844) |
| N31 | 自动 TID<br>用户请求停止任务 | 已请求停止自动 TID | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_tid_rng.py:495) |
| N32 | 自动 TID<br>开始等待取名或反查 ID 的脚本触发点 | 正在等待取名帧，剩余 {剩余推进数} 次推进<br>补充：当前推进 {当前推进}；触发推进 {触发推进}。到点后自动执行脚本，无需手动操作。 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_tid_rng.py:892) |
| N33 | 自动 TID<br>开始等待取名或反查 ID 的脚本触发点 | 正在等待反查帧，剩余 {剩余推进数} 次推进<br>补充：当前推进 {当前推进}；触发推进 {触发推进}。到点后自动执行脚本，无需手动操作。 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_tid_rng.py:892) |
| N34 | 自动 TID<br>开始新一轮自动 TID；原事件已有摘要与补充说明时沿用原事件 | 开始新一轮自动 TID 测种<br>补充：目标池 {目标池数量} 个 TID；搜索上限 {搜索上限} 帧；delay {delay}。 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_tid_rng.py:906) |
| N35 | 自动 TID<br>测种脚本执行 | 测种脚本执行完成，开始识别 Seed | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_tid_rng.py:976) |
| N36 | 自动 TID<br>自动 TID 测种结果 | 测种完成，正在搜索目标 TID<br>补充：当前推进 {当前推进}；搜索上限 {搜索上限} 帧；目标池 {目标池数量} 个 TID。 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_tid_rng.py:1004) |
| N37 | 自动 TID<br>搜索目标后的分支处理 | 已找到目标 TID：{目标 TID}，正在等待取名时机<br>补充：目标推进 {目标推进}；当前 {当前推进}；取名脚本在 {触发推进} 启动（delay {delay}）。 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_tid_rng.py:1067) |
| N38 | 自动 TID<br>取名脚本执行完成 | 取名脚本执行完成，请确认游戏内 TID<br>补充：本轮目标 TID {目标 TID}；请核对游戏内最终显示的 TID。 / 请核对游戏内最终显示的 TID。 | 信息<br>“/”分隔的是按条件择一显示的补充。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_tid_rng.py:1094) |
| N39 | 自动 TID<br>长时间等待进度或内部计时采样 | 仍在等待取名，剩余 {剩余推进数} 次推进<br>补充：已等待 {已等待秒数} 秒；当前推进 {当前推进}；触发推进 {触发推进}。 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_tid_rng.py:1147) |
| N40 | 自动 TID<br>等待取名时机及检查是否错过 | 已到达取名时机，开始执行取名脚本<br>补充：当前推进 {实际推进}；本轮目标 TID {目标 TID}。 / 当前推进 {实际推进}。 | 信息<br>“/”分隔的是按条件择一显示的补充。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_tid_rng.py:1187) |
| N41 | 自动 TID<br>长时间等待进度或内部计时采样 | 仍在等待反查，剩余 {剩余推进数} 次推进<br>补充：已等待 {已等待秒数} 秒；当前推进 {当前推进}；触发推进 {触发推进}。 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_tid_rng.py:1245) |
| N42 | 自动 TID<br>等待反查 ID 时机 | 已到达反查时机，开始执行反查 ID 脚本<br>补充：计划启动点 {触发推进}；实际推进 {实际推进}。反查结果将用于计算实际 delay。 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_tid_rng.py:1275) |
| N43 | 自动 TID<br>反查 ID 脚本执行完成 | 反查脚本已执行完成，正在识别 TID | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_tid_rng.py:1294) |
| N44 | 自动 TID<br>识别游戏内 TID | TID 识别完成，正在校准延迟<br>补充：识别到的游戏内 TID：{识别 TID}。 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_tid_rng.py:1306) |
| N45 | 自动 TID<br>根据识别 TID 匹配并校准 delay | TID 延迟校准已完成<br>补充：实际 delay {实际 delay} 帧；原设置 {delay} 帧。请核对后应用到后续任务。 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_tid_rng.py:1324) |
| N46 | 伊机控 / 脚本<br>刷新串口列表 | 串口列表已刷新 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2094) |
| N47 | 伊机控 / 脚本<br>自动选择可用串口 | 已自动选择可用串口 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2110) |
| N48 | 伊机控 / 脚本<br>打开脚本 | 脚本已加载，可以检查并运行 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2213) |
| N49 | 伊机控 / 脚本<br>新建脚本 | 已新建空白脚本 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2235) |
| N50 | 伊机控 / 脚本<br>保存脚本 | 脚本已保存 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2263) [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2288) |
| N51 | 伊机控 / 脚本<br>停止脚本 | 正在停止脚本 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2408) |
| N52 | 伊机控 / 脚本<br>运行脚本 | 开始执行脚本 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2451) [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2497) [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2666) |
| N53 | 伊机控 / 脚本<br>用户暂停脚本 | 已请求暂停，当前动作结束后暂停脚本 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2608) |
| N54 | 伊机控 / 脚本<br>暂停后应用修改并继续 | 已应用修改，脚本继续运行 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2629) |
| N55 | 伊机控 / 脚本<br>用户停止脚本 | 已请求停止脚本，正在等待退出 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2639) [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2747) |
| N56 | 伊机控 / 脚本<br>脚本运行终态 | 脚本已停止 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2945) |
| N57 | 伊机控 / 脚本<br>脚本运行终态 | 脚本已执行完成 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:2945) |
| N58 | 伊机控 / 脚本<br>伊机控连接、取消连接或断开结果 | 已取消连接伊机控 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:3481) |
| N59 | 伊机控 / 脚本<br>伊机控连接、取消连接或断开结果；连接伊机控 | 伊机控已连接，可以运行脚本 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:3489) [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:3544) |
| N60 | 伊机控 / 脚本<br>伊机控连接、取消连接或断开结果；断开伊机控 | 伊机控已断开 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:3506) [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:3559) |
| N61 | 伊机控 / 脚本<br>启用键盘控制 | 键盘控制已启用 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:4308) [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:4376) |
| N62 | 伊机控 / 脚本<br>键盘控制待机 | 键盘控制已切换为待机 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:4413) |
| N63 | 伊机控 / 脚本<br>关闭键盘控制 | 键盘控制已关闭 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:4483) |
| N64 | 伊机控 / 脚本<br>开始录制脚本 | 开始录制，操作会实时追加到当前脚本 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:4754) |
| N65 | 伊机控 / 脚本<br>停止录制脚本 | 录制已完成，操作已追加到当前脚本 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:4766) |
| N66 | 伊机控 / 脚本<br>停止录制脚本 | 录制已停止，没有记录到操作 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:4769) |
| N67 | 伊机控 / 脚本<br>暂停录制 | 录制已暂停 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:4781) |
| N68 | 伊机控 / 脚本<br>恢复录制 | 录制已恢复 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py:4792) |
| N69 | 视频源<br>视频源连接结果 | 视频源已连接，可以开始预览和识别 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:6303) |
| N70 | OCR<br>开始测试全部文字识别项目 | 开始测试全部文字识别项目 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:7679) |
| N71 | 自动定点<br>御三家自动操作中的阶段切换 | 正在确认起点对话 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:10310) |
| N72 | 自动定点<br>御三家自动操作中的阶段切换 | 正在推进至博士对话 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:10310) |
| N73 | 自动定点<br>御三家自动操作中的阶段切换 | 正在等待目标时机 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:10310) |
| N74 | 自动定点<br>御三家自动操作中的阶段切换 | 正在等待下一段对话 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:10310) |
| N75 | 自动定点<br>御三家自动操作中的阶段切换 | 正在推进游戏对话 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:10310) |
| N76 | 自动定点<br>御三家自动操作中的阶段切换 | 正在等待精灵球出现 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:10310) |
| N77 | 自动定点<br>御三家自动操作中的阶段切换 | 正在进入精灵球界面 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:10310) |
| N78 | 自动定点<br>御三家自动操作中的阶段切换 | 正在选择目标精灵球 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:10310) |
| N79 | 自动定点<br>御三家自动操作中的阶段切换 | 已选中目标精灵球，正在等待触发 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:10310) |
| N80 | Seed 捕捉<br>手动 Seed 测种的开始或停止请求；手动位置校正的开始或停止请求；手动 TID 测种的开始或停止请求 | 已请求停止捕捉 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:10586) [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:10671) [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:10774) |
| N81 | Seed 捕捉<br>手动 Seed 测种的开始或停止请求 | 开始识别 Seed，需要采集 {眨眼次数} 次眨眼 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:10617) |
| N82 | Seed 捕捉<br>手动位置校正的开始或停止请求 | 开始校正位置，需要采集 {眨眼次数} 次眨眼 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:10712) |
| N83 | Seed 捕捉<br>手动 TID 测种的开始或停止请求 | 开始 TID 测种，需要采集 {眨眼次数} 次眨眼 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:10803) |
| N84 | Seed 捕捉<br>手动测种或校正结束后的结果处理 | 捕捉已停止 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:10878) |
| N85 | Seed 捕捉<br>手动测种或校正结束后的结果处理 | 位置校正已完成 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:10933) |
| N86 | Seed 捕捉<br>手动测种或校正结束后的结果处理 | TID 测种完成，Seed 已回填 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:10938) |
| N87 | Seed 捕捉<br>手动测种或校正结束后的结果处理 | Seed 识别完成，结果已回填 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:10947) |

## H：仅诊断视图中的已命名记录

下列有内部摘要，但不进入右下角实时日志或默认用户列表。高频等待计时中，只有约每 30 秒的进度通知进入用户视图，其余采样仅诊断。

| 编号 | 来源 / 触发场景 | 当前用户文案 | 实际级别 / 审阅备注 | 代码 |
| --- | --- | --- | --- | --- |
| H01 | 自动定点<br>等待到达撞闪脚本启动点 | 已到达撞闪时机，正在准备执行脚本 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1308) |
| H02 | 自动定点<br>根据当前推进与目标决定下一步 | 正在等待撞闪时机<br>补充：当前推进 {当前推进}；目标推进 {目标推进}；脚本启动点 {启动点}；本次过帧 {本次过帧} | 信息<br>只显示当前已有数值的项目。此阶段切换记录为诊断，用户区由其它等待/执行事件提供进度。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1844) |
| H03 | 自动定点<br>根据当前推进与目标决定下一步 | 正在准备撞闪脚本<br>补充：当前推进 {当前推进}；目标推进 {目标推进}；脚本启动点 {启动点}；本次过帧 {本次过帧} | 信息<br>只显示当前已有数值的项目。此阶段切换记录为诊断，用户区由其它等待/执行事件提供进度。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_rng/runner.py:1844) |
| H04 | 自动 TID<br>搜索目标后的分支处理 | 本轮 TID 搜索参数 | 调试 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_tid_rng.py:1041) |
| H05 | 自动 TID<br>长时间等待进度或内部计时采样 | 取名等待计时采样<br>补充：已等待 {已等待秒数} 秒；当前推进 {当前推进}；触发推进 {触发推进}。 | 调试 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_tid_rng.py:1147) |
| H06 | 自动 TID<br>长时间等待进度或内部计时采样 | 反查等待计时采样<br>补充：已等待 {已等待秒数} 秒；当前推进 {当前推进}；触发推进 {触发推进}。 | 调试 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/automation/auto_tid_rng.py:1245) |
| H07 | 通用日志<br>脚本标准输出或错误输出 | 脚本输出已更新（{输出行数} 行） | 信息（诊断）<br>确认原因时才追加 C 类补充；没有明确原因时留空。 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/log_events.py:168) |
| H08 | 自动 TID<br>自动 TID 操作按钮内部状态 | TID 操作按钮状态已更新 | 调试 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/auto_tid_rng_panel.py:664) |
| H09 | 历史记录<br>历史记录或本轮结果回调 | 已归档本轮重试原因 | 信息 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:8938) |
| H10 | 历史记录<br>历史记录或本轮结果回调 | 已记录本轮 Seed，起始推进数 {起始推进} | 调试 | [定位](D:/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py:8947) |

## 展示规则与其它原始诊断

- 相邻同类记录合并时，除等待进度外，用户标题可能追加“（累计 {次数} 次）”；首次与最近原文保留在原始诊断中。
- 默认详情显示用户正文、已确认的补充、时间、中文级别、来源和轮次。内部组件名、异常类型、原因链、路径、运行 ID、事件标识及阶段内部值收在主动打开的“原始诊断”中。
- “全部诊断”列表会使用记录自带摘要，或在没有摘要时使用原始消息；不能把诊断视图中的文本视为用户文案。
- 未配摘要的普通 INFO / DEBUG 字符串不会显示在用户区，例如候选明细、OCR 原文、按钮状态、按键明细、原始计时、线程状态、文件路径以及脚本 stdout。
- 脚本 stderr 使用 E 表的“脚本返回错误输出”；完整内容留在原始诊断。脚本自身可以输出任意文字，因此没有有限的逐字枚举。
- “复制详情”复制当前看到的详情；用户视图的“复制 / 导出”使用用户说明，“全部诊断”的复制和导出含原文。

## 覆盖核对

逐个核对 146 处 `summary=` 调用；其中 4 处仅复制现有元数据，不生成新文案，已排除重复展示。任务失败映射、动态字典分支、等待通知与诊断采样分支分别展开。另检查 149 处非内联摘要的问题 / 转发调用，排除 5 处复用已整理消息的转发和 10 处已归入脚本输出规则的入口，余 134 处在 F 表逐项列出。
