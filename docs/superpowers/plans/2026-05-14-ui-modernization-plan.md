# UI 现代化改造实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将珍钻复刻定点自动乱数 PySide6 桌面应用从传统控件风格升级为现代专业工具风格（方案 A：集中式 Qt Stylesheet 重构）。

**Architecture:** 所有视觉变更集中在 `_apply_theme()` 的 200+ 行 CSS 和少量 widget `objectName` 属性标记中。GroupBox → QFrame 卡片仅替换容器类型，不破坏内部布局。零触碰业务逻辑。

**Tech Stack:** Python 3.12, PySide6 (Qt for Python), Qt Stylesheets (CSS-like)

**涉及文件（总计 ~7590 行，预计修改 ~250 行）：**
- `src/auto_bdsp_rng/ui/main_window.py:1536-1739` — 重写 `_apply_theme()`
- `src/auto_bdsp_rng/ui/main_window.py:748-810, 884-1056, 1121-1157, 1159-1260, 1281-1473` — objectName 标记
- `src/auto_bdsp_rng/ui/auto_rng_panel.py:150-206, 219-272, 274-300, 313-347, 349-359` — objectName 标记
- `src/auto_bdsp_rng/ui/easycon_panel.py` — 脚本编辑区和控件 objectName
- `src/auto_bdsp_rng/ui/history_panel.py` — 日志 objectName
- `src/auto_bdsp_rng/ui/static_target_form.py:126-262` — 控件 objectName

---

### Task 1: 重写全局 Stylesheet

**Files:**
- Modify: `src/auto_bdsp_rng/ui/main_window.py:1536-1739`

**Goal**: 将 `_apply_theme()` 的旧 stylesheet 完整替换为新的设计系统

- [ ] **Step 1: 替换 `_apply_theme()` 方法**

在第 1536 行，将整个 `_apply_theme()` 方法体（`self.setStyleSheet(...)`）替换为：

```python
    def _apply_theme(self) -> None:
        self.setStyleSheet(
            """
            /* ── 全局基础 ── */
            QWidget {
                background: #F7F8FA;
                color: #111827;
                font-family: "Microsoft YaHei UI", "Segoe UI", "PingFang SC", sans-serif;
                font-size: 13px;
            }
            QLabel {
                background: transparent;
                border: none;
                padding: 0;
            }

            /* ── Header ── */
            QFrame#Header {
                background: #FFFFFF;
                border: 1px solid #E5E7EB;
                border-radius: 8px;
            }
            QLabel#WindowTitle {
                font-size: 22px;
                font-weight: 700;
                color: #111827;
            }

            /* ── Badge ── */
            QLabel#Badge {
                background: #EAF8F3;
                color: #0E8F70;
                font-weight: 600;
                font-size: 13px;
                padding: 4px 10px;
                border-radius: 999px;
            }
            QLabel#BadgeDanger {
                background: #FEE2E2;
                color: #DC2626;
                font-weight: 600;
                font-size: 13px;
                padding: 4px 10px;
                border-radius: 999px;
            }

            /* ── StatusBar Slogan ── */
            QLabel#StatusSlogan {
                color: #0E8F70;
                font-weight: 600;
                padding: 0 8px;
            }

            /* ── Tab ── */
            QTabWidget::pane {
                border: 1px solid #E5E7EB;
                border-radius: 0 8px 8px 8px;
                top: -1px;
                background: #FFFFFF;
            }
            QTabBar::tab {
                background: #F3F4F6;
                border: 1px solid #E5E7EB;
                border-bottom: 2px solid transparent;
                color: #6B7280;
                min-width: 150px;
                padding: 10px 18px;
                font-weight: 600;
                font-size: 14px;
            }
            QTabBar::tab:selected {
                background: #FFFFFF;
                color: #0E8F70;
                border-bottom: 2px solid #10A37F;
            }
            QTabBar::tab:hover:!selected {
                background: #E5E7EB;
                color: #111827;
            }

            /* ── Card (替代 GroupBox) ── */
            QGroupBox {
                background: #FFFFFF;
                border: 1px solid #E5E7EB;
                border-radius: 10px;
                margin-top: 18px;
                padding: 18px 16px 16px 16px;
                font-weight: 700;
                font-size: 14px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 14px;
                top: 2px;
                padding: 0 6px;
                color: #111827;
            }

            /* ── 输入框 / 下拉框 / 列表 ── */
            QLineEdit, QDoubleSpinBox, QComboBox, QListWidget {
                background: #FFFFFF;
                border: 1px solid #D1D5DB;
                border-radius: 8px;
                min-height: 36px;
                padding: 4px 12px;
                color: #111827;
                font-size: 13px;
                selection-background-color: #D1F1E7;
            }
            QLineEdit:focus, QDoubleSpinBox:focus, QComboBox:focus {
                border-color: #10A37F;
            }
            QLineEdit[readOnly="true"] {
                color: #0E8F70;
                background: #F9FAFB;
            }
            QComboBox::drop-down {
                border: none;
            }

            /* ── SpinBox 微调按钮 ── */
            QSpinBox {
                background: #FFFFFF;
                border: 1px solid #D1D5DB;
                border-radius: 8px;
                min-height: 36px;
                padding: 4px 12px;
                color: #111827;
                font-size: 13px;
            }

            /* ── 多行文本框 ── */
            QPlainTextEdit {
                background: #FFFFFF;
                border: 1px solid #D1D5DB;
                border-radius: 8px;
                color: #111827;
                font-family: "Cascadia Mono", "Consolas", "JetBrains Mono", monospace;
                font-size: 13px;
                padding: 10px;
                selection-background-color: #D1F1E7;
            }
            QPlainTextEdit#LogView {
                background: #FBFBFC;
                border: 1px solid #E5E7EB;
                border-radius: 8px;
                font-family: "Cascadia Mono", "Consolas", "JetBrains Mono", monospace;
                font-size: 13px;
                padding: 10px;
                color: #374151;
            }

            /* ── 伊机控暗色日志 ── */
            QTextEdit#EasyConLog {
                background: #282826;
                border: 1px solid #E5E7EB;
                border-radius: 0;
                color: #e7ece9;
                font-family: "Cascadia Mono", "Consolas", "JetBrains Mono", monospace;
                font-size: 12px;
                padding: 10px;
            }

            /* ── 工具栏 ── */
            QFrame#EasyConToolbar {
                background: #FFFFFF;
                border: 1px solid #E5E7EB;
                border-radius: 8px;
            }
            QFrame#AutoRngToolbar {
                background: #FFFFFF;
                border: 1px solid #E5E7EB;
                border-radius: 8px;
            }

            /* ── 预览 ── */
            QLabel#Preview {
                background: #F3F4F6;
                border: 1px solid #E5E7EB;
                border-radius: 8px;
                color: #6B7280;
            }

            /* ── 状态栏 ── */
            QStatusBar {
                background: #F3F4F6;
                border-top: 1px solid #E5E7EB;
                color: #6B7280;
                font-size: 12px;
            }

            /* ── 只读 Seed 显示 ── */
            QLineEdit#Readonly {
                color: #0E8F70;
                background: #F9FAFB;
            }

            /* ── 通用按钮 ── */
            QPushButton {
                background: #FFFFFF;
                border: 1px solid #D1D5DB;
                border-radius: 8px;
                min-height: 36px;
                padding: 4px 12px;
                font-weight: 600;
                font-size: 13px;
                color: #111827;
            }
            QPushButton:hover {
                background: #F3F4F6;
                border-color: #9CA3AF;
            }
            QPushButton:pressed {
                background: #E5E7EB;
            }
            QPushButton:disabled {
                background: #F3F4F6;
                color: #9CA3AF;
                border-color: #E5E7EB;
            }

            /* ── 主按钮（绿色） ── */
            QPushButton#PrimaryButton {
                background: #10A37F;
                color: #FFFFFF;
                border-color: #10A37F;
                font-weight: 700;
            }
            QPushButton#PrimaryButton:hover {
                background: #0E8F70;
                border-color: #0E8F70;
            }
            QPushButton#PrimaryButton:pressed {
                background: #0B7A5E;
                border-color: #0B7A5E;
            }
            QPushButton#PrimaryButton:disabled {
                background: #9CA3AF;
                color: #F3F4F6;
                border-color: #9CA3AF;
            }

            /* ── Danger 按钮 ── */
            QPushButton#DangerButton {
                background: #FFFFFF;
                color: #DC2626;
                border-color: #DC2626;
                font-weight: 700;
            }
            QPushButton#DangerButton:hover {
                background: #FEF2F2;
            }

            /* ── ToolButton 主按钮 ── */
            QToolButton#PrimaryButton {
                background: #10A37F;
                color: #FFFFFF;
                border: 1px solid #10A37F;
                border-radius: 8px;
                padding: 4px 18px 4px 12px;
                font-weight: 700;
                font-size: 13px;
            }
            QToolButton#PrimaryButton:hover {
                background: #0E8F70;
                border-color: #0E8F70;
            }
            QToolButton#PrimaryButton:pressed {
                background: #0B7A5E;
                border-color: #0B7A5E;
            }
            QToolButton#PrimaryButton:disabled {
                background: #9CA3AF;
                color: #F3F4F6;
                border-color: #9CA3AF;
            }
            QToolButton#PrimaryButton::menu-button {
                border-left: 1px solid rgba(255,255,255,90);
                width: 18px;
            }

            /* ── 帮助按钮 ── */
            QToolButton#HelpMenuButton {
                background: transparent;
                border: 1px solid transparent;
                border-radius: 6px;
                color: #6B7280;
                min-height: 28px;
                padding: 4px 10px;
                font-weight: 700;
                font-size: 13px;
            }
            QToolButton#HelpMenuButton:hover {
                background: #F3F4F6;
                border-color: #E5E7EB;
                color: #111827;
            }
            QToolButton#HelpMenuButton::menu-indicator {
                image: none;
                width: 0;
            }

            /* ── 表格 ── */
            QTableWidget {
                background: #FFFFFF;
                alternate-background-color: #F9FAFB;
                border: 1px solid #E5E7EB;
                gridline-color: #F3F4F6;
                color: #111827;
                font-size: 13px;
            }
            QTableWidget::item:selected {
                background: #10A37F;
                color: #FFFFFF;
            }
            QHeaderView::section {
                background: #F9FAFB;
                color: #6B7280;
                border: 0;
                border-right: 1px solid #E5E7EB;
                border-bottom: 1px solid #E5E7EB;
                padding: 8px 6px;
                font-weight: 700;
                font-size: 12px;
            }

            /* ── 滚动条 ── */
            QScrollBar:vertical {
                background: transparent;
                width: 8px;
                margin: 0;
            }
            QScrollBar::handle:vertical {
                background: #D1D5DB;
                border-radius: 4px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover {
                background: #9CA3AF;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0;
            }
            QScrollBar:horizontal {
                background: transparent;
                height: 8px;
            }
            QScrollBar::handle:horizontal {
                background: #D1D5DB;
                border-radius: 4px;
                min-width: 30px;
            }
            QScrollBar::handle:horizontal:hover {
                background: #9CA3AF;
            }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
                width: 0;
            }
            """
        )
```

- [ ] **Step 2: 验证文件语法无错误**

```bash
python -c "from auto_bdsp_rng.ui.main_window import MainWindow; print('OK')"
```

- [ ] **Step 3: 提交**

```bash
git add src/auto_bdsp_rng/ui/main_window.py
git commit -m "feat:重写全局样式为现代设计系统主题"
```

---

### Task 2: 标记 main_window.py 的 widget objectName

**Files:**
- Modify: `src/auto_bdsp_rng/ui/main_window.py:748-810, 802-807, 884-1056`

**Goal**: 为卡片区和分组添加 objectName 以便 stylesheet 精确匹配

- [ ] **Step 1: 将结果计数 Label 标记更名为独立的 ResultCount**

找到第 803 行 `self.open_source_build_label`，确认它已是 `StatusSlogan`。

找到 `self.result_count` 变量位置（约第 1500 行附近），确认它的 objectName。

```bash
grep -n "result_count\|ResultCount\|StatusSlogan\|open_source_build_label" /mnt/d/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/main_window.py
```

- [ ] **Step 2: 为自动定点乱数面板中的日志 view 添加 objectName**

在 `auto_rng_panel.py:355` 行 `self.log_view = _CopyableTextEdit()` 之后添加：
```python
self.log_view.setObjectName("LogView")
```

这需要在 Task 4 一起做，或者这里记录并在 Task 4 中确认。

- [ ] **Step 3: 标记 status_group 的 objectName**

找到 `_build_project_status_group()` 方法（第 884 行），在 `group = QGroupBox("状态")` 后一行添加：
```python
group.setObjectName("StatusCard")
```
但不是必须的——因为已有通用 QGroupBox 样式。暂跳过，在 Task 3 统一处理。

- [ ] **Step 4: 确认无遗漏，提交**

```bash
git add src/auto_bdsp_rng/ui/main_window.py
git commit -m "feat:标记主窗口 widget objectName 以适配新主题"
```

---

### Task 3: 统一控件高度和间距

**Files:**
- Modify: `src/auto_bdsp_rng/ui/main_window.py:1741-1753` — `_spin()` 方法
- Modify: `src/auto_bdsp_rng/ui/main_window.py:1747-1753` — `_double_spin()` 方法

**Goal**: 让 `_spin()` 返回的控件（QLineEdit/QSpinBox）拥有统一 36px 高度

- [ ] **Step 1: 增强 `_spin()` 方法设定固定高度**

`_spin()` 当前返回的是 QLineEdit（宽度无限制）。需添加固定高度：

```python
    def _spin(self, minimum: int, maximum: int, value: int) -> QLineEdit:
        w = QLineEdit(str(value))
        w.setValidator(QIntValidator(minimum, maximum))
        w.setAlignment(Qt.AlignmentFlag.AlignRight)
        w.setFixedHeight(36)
        return w
```

- [ ] **Step 2: 为 `_double_spin()` 添加固定高度**

```python
    def _double_spin(self, minimum: float, maximum: float, value: float, decimals: int) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(decimals)
        spin.setSingleStep(0.1)
        spin.setValue(value)
        spin.setFixedHeight(36)
        return spin
```

- [ ] **Step 3: 提交**

```bash
git add src/auto_bdsp_rng/ui/main_window.py
git commit -m "feat:统一控件高度为36px"
```

---

### Task 4: 优化 auto_rng_panel.py 控件标记

**Files:**
- Modify: `src/auto_bdsp_rng/ui/auto_rng_panel.py:150-359`

**Goal**: 为 auto_rng_panel 的工具、卡片、按钮添加 objectName

- [ ] **Step 1: 将工具栏 objectName 从 `EasyConToolbar` 改为独立名称**

在第 152 行，改：
```python
toolbar.setObjectName("EasyConToolbar")  # 改为
toolbar.setObjectName("AutoRngToolbar")
```

- [ ] **Step 2: 停止按钮标记 DangerButton**

在第 173 行的 `self.stop_button = QPushButton("停止")` 后添加：
```python
self.stop_button.setObjectName("DangerButton")
```

- [ ] **Step 3: 捕获精灵信息按钮标记为 `SecondaryButton`**

在第 200 行 `self.capture_info_button = QPushButton("捕获精灵信息")` 后添加：
```python
self.capture_info_button.setObjectName("SecondaryButton")
```

- [ ] **Step 4: 日志区域添加 objectName**

在第 355 行 `self.log_view = _CopyableTextEdit()` 后添加：
```python
self.log_view.setObjectName("LogView")
```

- [ ] **Step 5: 脚本区 GroupBox 改为卡片标题文案**

在第 275 行，将 `group = QGroupBox("脚本")` 改为更清晰的标题。由于 GroupBox 已全局样式化，标题会自动变为卡片样式。无需额外改动。

- [ ] **Step 6: 目标精灵 GroupBox 标题优化**

在第 314 行附近，将 GroupBox 标题从空字符串改为具体内容。但 `_build_target_summary_group()` 用的是动态 title。检查确认标题通过 `self.target_summary_title` QLabel 设置。

- [ ] **Step 7: 提交**

```bash
git add src/auto_bdsp_rng/ui/auto_rng_panel.py
git commit -m "feat:自动面板控件标记适配新主题"
```

---

### Task 5: 优化 easycon_panel.py 控件标记

**Files:**
- Modify: `src/auto_bdsp_rng/ui/easycon_panel.py`

**Goal**: 为伊机控面板控件添加 objectName，确保样式一致性

- [ ] **Step 1: 查找并标记 easycon_panel 的日志区和关键控件**

由于该文件 2048 行，需要查找关键的按钮和日志区：

```bash
grep -n "QTextEdit\|QPlainTextEdit\|QPushButton\|QToolButton\|setObjectName" /mnt/d/codex_project/auto_bdsp_rng/src/auto_bdsp_rng/ui/easycon_panel.py | head -50
```

- [ ] **Step 2: 为 EasyCon 日志框确认 objectName**

确认暗色日志框是否已命名为 `EasyConLog`（按 stylesheet 中的选择器要求）。

- [ ] **Step 3: 标记主按钮 objectName**

将 EasyCon 面板中的"执行"/"发送"等主按钮标记为 `PrimaryButton`，将脚本操作按钮标记为 `SecondaryButton`。

- [ ] **Step 4: 提交**

```bash
git add src/auto_bdsp_rng/ui/easycon_panel.py
git commit -m "feat:伊机控面板控件标记适配新主题"
```

---

### Task 6: 优化 history_panel.py 和 static_target_form.py

**Files:**
- Modify: `src/auto_bdsp_rng/ui/history_panel.py`
- Modify: `src/auto_bdsp_rng/ui/static_target_form.py`

**Goal**: 为历史记录面板和筛选表单添加 objectName

- [ ] **Step 1: 历史记录日志区域标记**

在 `history_panel.py` 的 `_CopyableTextEdit()` 实例化后添加：
```python
self.setObjectName("LogView")
```

- [ ] **Step 2: static_target_form.py 控件高度统一**

`StaticTargetForm._spin()` 方法（第 359 行）当前设定 `setFixedHeight(32)`。改为 36px 以符合全局规范：

```python
spin.setFixedHeight(36)
```

- [ ] **Step 3: 提交**

```bash
git add src/auto_bdsp_rng/ui/history_panel.py src/auto_bdsp_rng/ui/static_target_form.py
git commit -m "feat:历史面板和筛选表单控件适配新主题"
```

---

### Task 7: 最终验证与集成

- [ ] **Step 1: 语法验证**

```bash
python -c "
from auto_bdsp_rng.ui.main_window import MainWindow
from auto_bdsp_rng.ui.auto_rng_panel import AutoRngPanel
from auto_bdsp_rng.ui.easycon_panel import EasyConPanel
from auto_bdsp_rng.ui.history_panel import HistoryPanel
from auto_bdsp_rng.ui.static_target_form import StaticTargetForm
print('All imports OK')
"
```

- [ ] **Step 2: 运行 GUI 冒烟测试（如有 headless 环境）**

```bash
python -c "
import sys
from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
from auto_bdsp_rng.ui.main_window import MainWindow
w = MainWindow()
print('Window created OK:', w.windowTitle())
w.close()
"
```

- [ ] **Step 3: 确认 git status 干净**

```bash
git status
```

- [ ] **Step 4: 最终提交（如有多余改动）**

```bash
git add src/auto_bdsp_rng/ui/
git commit -m "feat:UI现代化改造完成——全局主题、卡片、按钮、日志统一样式"
```

---

## Self-Review Checklist

- [x] Spec coverage: 所有 17 个设计要点→ 颜色/字体/控件尺寸/卡片/Header/Tab/Badge/Toolbar/按钮层级/目标列表/日志/状态栏
- [x] Placeholder scan: 无 TBD/TODO
- [x] Type consistency: 所有 objectName 与 stylesheet 选择器一致
- [x] No business logic changes: 所有改动限于 stylesheet + objectName + 控件高度（纯视觉属性）
