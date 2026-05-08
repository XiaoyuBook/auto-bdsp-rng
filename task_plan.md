# Task Plan: 自动定点乱数界面设计

## Goal
为“自动定点乱数界面”设计完整 UI、流程状态机、脚本参数填充策略与后续实现 TODO，只产出方案文档，不撰写业务代码。

## Current Phase
Phase 4

## Phases

### Phase 1: Requirements & Discovery
- [x] 记录用户描述的自动 RNG 联动需求
- [x] 调研现有项目结构、页面入口、定点数据区筛选逻辑、脚本目录约定
- [x] Document findings in findings.md
- **Status:** complete

### Phase 2: UI & Workflow Design
- [x] 设计自动定点乱数页面的信息架构与控件布局
- [x] 设计全自动流程状态机与循环策略
- [x] 设计脚本选择、参数填充、过帧/重识别/重新测 seed 决策
- **Status:** complete

### Phase 3: Implementation TODO
- [x] 拆分前端、后端/服务、脚本适配、测试任务
- [x] 明确依赖、风险、验收标准
- [x] 生成 TODO 文档
- **Status:** complete

### Phase 4: Delivery
- [x] 回顾规划文件与 TODO 文档
- [x] 向用户交付简明设计总结
- **Status:** complete

## Key Questions
1. 现有“定点数据区”的筛选控件与数据结构在哪里？
2. 脚本目录实际叫 `script` 还是 `scripts`，脚本执行与参数替换目前如何实现？
3. project_xs 的 seed 捕获与 reidentify 在项目里已有怎样的接口？
4. 自动流程应如何呈现当前阶段、目标帧、剩余帧、脚本运行状态与错误恢复？

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| 本轮只产出设计与 TODO 文档 | 用户明确要求本次会话不执行代码撰写 |
| 使用 Planning with Files 管理本轮设计上下文 | 用户明确要求使用该工作流记录进度 |
| 自动页作为第四个 Tab 设计 | 当前主窗口已用 Tab 分隔 Project_Xs、BDSP 和 EasyCon |
| 过帧脚本填 `_目标帧数 = remaining_to_trigger` | 脚本内部已有预留逻辑，自动流程不额外扣预留 |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|

## Notes
- 设计需贴合现有项目结构，不引入与当前 UI 风格冲突的新范式。
- 所有代码实现动作仅写入 TODO，不在本轮执行。
