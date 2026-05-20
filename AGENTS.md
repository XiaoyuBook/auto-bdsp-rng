# Agent 维护规则

## 项目上下文文档

- 修改代码前，优先阅读 `AGENT_PROJECT_CONTEXT.md` 中与当前任务相关的说明。
- 如果 `AGENT_PROJECT_CONTEXT.md` 与代码不一致，以代码为准，并在本次修改中同步修正文档。
- 每次 agent 修改代码后，必须及时更新 `AGENT_PROJECT_CONTEXT.md` 中与本次修改相关的部分。
- 更新范围只限于本次代码修改相关内容，不要为了维护文档而重写无关章节。
- 不要把 `AGENT_PROJECT_CONTEXT.md` 从 `.gitignore` 中移除。
- 不要把 `AGENT_PROJECT_CONTEXT.md` 提交到版本库。
- 修改完成后的最终回复中，需要说明是否更新了 `AGENT_PROJECT_CONTEXT.md`。

## 本地约束

- Windows 环境优先使用 PowerShell 命令和 Windows 路径。
- 修改包含中文的文件前，确认并保留 UTF-8 编码，避免引入乱码。
- 保持现有 PySide6 UI、Project_Xs、EasyCon、PokeFinder 兼容逻辑，不做无关重构。
