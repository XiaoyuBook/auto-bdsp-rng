# UI 现代化改造设计文档

**日期**: 2026-05-14
**状态**: 已确认
**策略**: 方案 A — 集中式 Qt Stylesheet 重构

## 目标

将"珍钻复刻定点自动乱数"PySide6 桌面应用的 UI 从传统控件风格升级为现代专业工具风格，参考 GitHub Desktop / JetBrains Toolbox 浅色工具界面。

## 约束

- 不改变任何业务逻辑、运行逻辑、脚本逻辑、按钮功能、数据结构
- 所有视觉变更集中在 stylesheet + objectName 标记
- 保持浅色主题
- 保持当前布局结构不变

## 改动范围

| 文件 | 改动类型 |
|------|----------|
| `src/auto_bdsp_rng/ui/main_window.py` | 重写 `_apply_theme()`，为卡片区域添加 objectName，替换 GroupBox 标题 |
| `src/auto_bdsp_rng/ui/auto_rng_panel.py` | objectName 标记，控件统一尺寸，badge 标记 |
| `src/auto_bdsp_rng/ui/easycon_panel.py` | objectName 标记，控件统一高度 |
| `src/auto_bdsp_rng/ui/history_panel.py` | 日志区域 objectName |
| `src/auto_bdsp_rng/ui/static_target_form.py` | 微调控件 objectName |

## 设计系统

### 颜色

| Token | 色值 | 用途 |
|-------|------|------|
| bg-primary | #F7F8FA | 窗口背景 |
| bg-card | #FFFFFF | 卡片背景 |
| border-card | #E5E7EB | 卡片边框 |
| border-input | #D1D5DB | 输入框边框 |
| border-focus | #10A37F | 聚焦边框 |
| text-primary | #111827 | 主文字 |
| text-secondary | #6B7280 | 次要文字 |
| accent | #10A37F | 主色绿 |
| accent-dark | #0E8F70 | 深绿(hover/pressed) |
| accent-bg | #EAF8F3 | 浅绿背景(badge) |
| log-bg | #FBFBFC | 日志背景 |
| danger | #DC2626 | 错误/停止 |
| warning | #F59E0B | 警告 |
| success | #16A34A | 成功 |
| disabled-bg | #F3F4F6 | 禁用背景 |
| disabled-text | #9CA3AF | 禁用文字 |
| statusbar-bg | #F3F4F6 | 状态栏背景 |

### 字体

| 层级 | 大小 | 粗细 | 用途 |
|------|------|------|------|
| 主标题 | 22px | 700 | 窗口标题 |
| Tab | 14px | 600 | Tab 标签 |
| 卡片标题 | 14px | 700 | GroupBox 替代 |
| 正文 | 13px | 400 | 标签、输入 |
| 按钮 | 13px | 600 | 所有按钮 |
| 日志 | 13px | 400 | 等宽字体 |
| 状态栏 | 12px | 400 | 底部状态 |

字体族: `"Microsoft YaHei UI", "Segoe UI", "PingFang SC", sans-serif`
等宽: `"Cascadia Mono", "Consolas", "JetBrains Mono", monospace`

### 控件尺寸

| 控件 | 高度 | 圆角 | 内边距 |
|------|------|------|--------|
| 输入框/下拉 | 36px | 8px | 8px 12px |
| 按钮 | 36px | 8px | 12px 18px |
| Badge | auto | 999px | 6px 10px |
| 卡片内边距 | — | 10px | 16px |

### 卡片间距

- 卡片间: 16px
- 表单行: 8px
- 控件间: 12px

## 逐区域设计

### 1. 头部 Header

- 背景 `#FFFFFF`，圆角 8px，边框 `#E5E7EB`
- 标题 22px Bold
- Badge: `[循环 0]` `[阶段 空闲]` `[advance 0]` — accent-bg 背景，accent-dark 文字，圆角 999px
- 帮助按钮保持 QToolButton + InstantPopup

### 2. Tab 栏

- 选中: `#FFFFFF` 背景，`accent-dark` 文字，底部 2px `accent` 强调线
- 未选中: `transparent` 背景，`text-secondary` 文字
- Tab 高度 40px，最小宽度 150px
- pane 无边框

### 3. 自动定点乱数面板 (auto_rng_panel.py)

- 工具栏: 白色卡片，56px 高，控件垂直居中
- 开始按钮: `PrimaryButton` — 绿底白字
- 停止按钮: 白底灰边框
- 捕获精灵信息: 白底灰边框
- 策略卡片: Card 样式替代 GroupBox
- 脚本卡片: 两列 Grid 布局
- 目标精灵列表: `#F9FAFB` 背景，8px 圆角
- 日志: `#FBFBFC` 背景，等宽字体，8px 圆角

### 4. Seed 捕捉面板 (project_xs_tab in main_window.py)

- 左右分栏保持
- 状态组: QFrame card 替代 QGroupBox
- 预览区域: card 样式
- 输入框统一样式

### 5. 定点数据区 (bdsp_tab in main_window.py)

- 存档信息: QFrame card，水平布局
- 乱数信息/设置/筛选: 三列保持比例
- 结果表格: 交替行色 `#F5F4F0`，选中行 `accent`

### 6. 伊机控面板 (easycon_panel.py)

- 工具栏: card 样式
- 脚本编辑区: 保持暗色背景 `#282826`
- 按键映射: card 容器

### 7. 历史记录面板 (history_panel.py)

- 日志文本框: `#FBFBFC` 背景，等宽字体

### 8. 底部状态栏

- 背景 `#F3F4F6`，高度 24px
- 左侧: `text-secondary`，12px
- 右侧标语: `accent-dark`，12px

## 实现步骤

1. **重写 `_apply_theme()`** — 全新全局 stylesheet，大约 200-250 行 CSS
2. **标记 objectName** — 为关键区域添加/调整 objectName（Card, Badge, PrimaryButton 等）
3. **替换 GroupBox** — 将部分 GroupBox 改为 QFrame + 标题 QLabel 的卡片模式（仅视觉，不破坏布局）
4. **统一控件尺寸** — 为输入框、下拉框、按钮设置固定高度
5. **日志区域** — 调整 objectName 和字体
6. **自测验证** — 在 Windows 上构建并确认视觉效果

## 风险评估

- **风险极低**: 所有改动限于 stylesheet 字符串和 objectName 属性，不触碰任何业务逻辑
- **可回滚**: 如果视觉效果不满意，只需恢复 `_apply_theme()` 方法和少量 objectName
- **不影响功能**: 信号/槽、运行流程、数据处理全部保持不变
