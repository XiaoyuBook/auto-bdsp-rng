from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QRect, QSize, QProcess, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QColor, QKeySequence, QPainter, QPixmap, QTextCursor, QTextFormat
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMenuBar,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QApplication,
    QSplitter,
    QStatusBar,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from auto_bdsp_rng.automation.easycon import (
    BridgeEasyConBackend,
    EasyConConfig,
    EasyConInstallation,
    EasyConStatus,
    classify_cli_failure,
    cli_connection_notice,
    detect_newline_style,
    discover_ezcon,
    extract_compile_error_line,
    apply_parameter_values,
    generate_script_file,
    list_ports,
    load_config,
    parse_script_parameters,
    prune_generated_scripts,
    save_config,
    scan_builtin_scripts,
)
from auto_bdsp_rng.resources import bundled_easycon_bridge_path, resource_path


SCRIPT_DIR = resource_path("script")
GENERATED_DIR = SCRIPT_DIR / ".generated"

# ── 可配置按键映射 ──────────────────────────────────

DEFAULT_KEY_MAPPING = {
    "A": Qt.Key.Key_L, "B": Qt.Key.Key_K, "X": Qt.Key.Key_I, "Y": Qt.Key.Key_J,
    "L": Qt.Key.Key_G, "R": Qt.Key.Key_T, "ZL": Qt.Key.Key_F, "ZR": Qt.Key.Key_R,
    "Plus": Qt.Key.Key_Plus, "Minus": Qt.Key.Key_Minus,
    "Capture": Qt.Key.Key_Z, "Home": Qt.Key.Key_C,
    "LClick": Qt.Key.Key_Q, "RClick": Qt.Key.Key_E,
    "Up": 0, "Down": 0, "Left": 0, "Right": 0,
    "UpLeft": 0, "DownLeft": 0, "UpRight": 0, "DownRight": 0,
    "LSUp": Qt.Key.Key_W, "LSDown": Qt.Key.Key_S, "LSLeft": Qt.Key.Key_A, "LSRight": Qt.Key.Key_D,
    "RSUp": Qt.Key.Key_Up, "RSDown": Qt.Key.Key_Down, "RSLeft": Qt.Key.Key_Left, "RSRight": Qt.Key.Key_Right,
}

_KEY_TO_QT = {int(v): k for k, v in Qt.Key.__dict__.items() if isinstance(v, int) and not k.startswith("_")}

def _qt_key_name(key: int) -> str:
    if key == 0:
        return ""
    name = _KEY_TO_QT.get(key, "")
    if name.startswith("Key_"):
        name = name[4:]
    return name

def _resolve_vpad_button(key: int, mapping: dict[str, int]) -> tuple[str, str, str] | None:
    """返回 (kind, side/direction) 或 None — kind 为 'button' 或 'stick'"""
    # 先查方向按键 (stick)
    stick_map = {
        "LSUp": ("left", "Up"), "LSDown": ("left", "Down"),
        "LSLeft": ("left", "Left"), "LSRight": ("left", "Right"),
        "RSUp": ("right", "Up"), "RSDown": ("right", "Down"),
        "RSLeft": ("right", "Left"), "RSRight": ("right", "Right"),
        "Up": ("hat", "Up"), "Down": ("hat", "Down"),
        "Left": ("hat", "Left"), "Right": ("hat", "Right"),
        "UpLeft": ("hat", "UpLeft"), "DownLeft": ("hat", "DownLeft"),
        "UpRight": ("hat", "UpRight"), "DownRight": ("hat", "DownRight"),
    }
    for name, stick_info in stick_map.items():
        if mapping.get(name, 0) == key:
            return ("stick", stick_info[0], stick_info[1])
    # 再查普通按键
    button_names = ["A", "B", "X", "Y", "L", "R", "ZL", "ZR", "Plus", "Minus", "Capture", "Home", "LClick", "RClick"]
    for name in button_names:
        if mapping.get(name, 0) == key:
            return ("button", name, None)
    return None


def _default_bridge_path() -> Path | None:
    candidate = bundled_easycon_bridge_path()
    return candidate if candidate.exists() else None


class LineNumberArea(QWidget):
    def __init__(self, editor: EasyConScriptEditor) -> None:
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self.editor.line_number_area_width(), 0)

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self.editor.line_number_area_paint_event(event)


class EasyConScriptEditor(QPlainTextEdit):
    fileDropped = Signal(Path)

    def __init__(self) -> None:
        super().__init__()
        self.line_number_area = LineNumberArea(self)
        self.setAcceptDrops(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setPlaceholderText("选择或拖入 .txt / .ecs 脚本")
        self.blockCountChanged.connect(self._update_line_number_area_width)
        self.updateRequest.connect(self._update_line_number_area)
        self.cursorPositionChanged.connect(self._highlight_current_line)
        self._update_line_number_area_width()
        self._highlight_current_line()

    def line_number_area_width(self) -> int:
        digits = len(str(max(1, self.blockCount())))
        return 12 + self.fontMetrics().horizontalAdvance("9") * digits

    def line_number_area_paint_event(self, event) -> None:  # type: ignore[no-untyped-def]
        painter = QPainter(self.line_number_area)
        painter.fillRect(event.rect(), QColor("#F0F3F6"))

        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = int(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + int(self.blockBoundingRect(block).height())

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                number = str(block_number + 1)
                painter.setPen(QColor("#6B7280"))
                painter.drawText(
                    0,
                    top,
                    self.line_number_area.width() - 4,
                    self.fontMetrics().height(),
                    Qt.AlignmentFlag.AlignRight,
                    number,
                )
            block = block.next()
            top = bottom
            bottom = top + int(self.blockBoundingRect(block).height())
            block_number += 1

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        rect = self.contentsRect()
        self.line_number_area.setGeometry(QRect(rect.left(), rect.top(), self.line_number_area_width(), rect.height()))

    def go_to_line(self, line_number: int) -> None:
        block = self.document().findBlockByNumber(max(0, line_number - 1))
        if not block.isValid():
            return
        cursor = QTextCursor(block)
        self.setTextCursor(cursor)
        self.centerCursor()
        self.setFocus()

    def _update_line_number_area_width(self) -> None:
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def _update_line_number_area(self, rect: QRect, dy: int) -> None:
        if dy:
            self.line_number_area.scroll(0, dy)
        else:
            self.line_number_area.update(0, rect.y(), self.line_number_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_line_number_area_width()

    def _highlight_current_line(self) -> None:
        selection = QTextEdit.ExtraSelection()
        selection.format.setBackground(QColor("#FFF8DC"))
        selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        selection.cursor = self.textCursor()
        selection.cursor.clearSelection()
        self.setExtraSelections([selection])

    def dragEnterEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if _first_supported_drop(event.mimeData()) is not None:
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        path = _first_supported_drop(event.mimeData())
        if path is None:
            super().dropEvent(event)
            return
        self.fileDropped.emit(path)
        event.acceptProposedAction()


class BridgeScriptWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, backend: BridgeEasyConBackend, script_text: str, script_name: str) -> None:
        super().__init__()
        self.backend = backend
        self.script_text = script_text
        self.script_name = script_name

    def run(self) -> None:
        try:
            result = self.backend.run_script_text(self.script_text, self.script_name)
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.finished.emit(result)


class KeyMappingDialog(QDialog):
    """按键映射对话框 — 手柄背景图 + 可点击按键位置绑定"""

    _WINDOW_W, _WINDOW_H = 999, 830

    # Positions mirror the original EasyCon WinForms mapping dialog.
    _BTN_POSITIONS: list[tuple[int, int, int, int, str, str]] = [
        (280, 134, 62, 41, "ZL", "ZL"), (280, 185, 62, 41, "L", "L"),
        (643, 134, 62, 41, "ZR", "ZR"), (643, 185, 62, 41, "R", "R"),
        (365, 241, 62, 41, "Minus", "Minus"), (417, 292, 62, 41, "Capture", "Capture"),
        (522, 292, 62, 41, "Home", "Home"), (571, 239, 62, 41, "Plus", "Plus"),
        (218, 239, 62, 41, "LSUp", "LSUp"), (218, 340, 62, 41, "LSDown", "LSDown"),
        (146, 292, 62, 41, "LSLeft", "LSLeft"), (290, 292, 62, 41, "LSRight", "LSRight"),
        (218, 292, 62, 41, "LClick", "LClick"),
        (352, 358, 49, 41, "Up", "Up"), (352, 439, 49, 41, "Down", "Down"),
        (303, 395, 49, 41, "Left", "Left"), (402, 395, 49, 41, "Right", "Right"),
        (303, 358, 49, 41, "UpLeft", "UpLeft"), (402, 358, 49, 41, "UpRight", "UpRight"),
        (303, 439, 49, 41, "DownLeft", "DownLeft"), (402, 439, 49, 41, "DownRight", "DownRight"),
        (571, 357, 62, 41, "RSUp", "RSUp"), (571, 459, 62, 41, "RSDown", "RSDown"),
        (499, 409, 62, 41, "RSLeft", "RSLeft"), (643, 409, 62, 41, "RSRight", "RSRight"),
        (571, 409, 62, 41, "RClick", "RClick"),
        (691, 250, 62, 41, "X", "X"), (633, 292, 62, 41, "Y", "Y"),
        (758, 292, 62, 41, "A", "A"), (691, 340, 62, 41, "B", "B"),
    ]

    def __init__(self, mapping: dict[str, int], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("按键设置")
        self.setFixedSize(self._WINDOW_W, self._WINDOW_H)
        self.setStyleSheet("background: #f2f1ee;")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._mapping = dict(mapping)
        self._active_name: str | None = None
        self._buttons: dict[str, QPushButton] = {}
        self._build_ui()
        self._load_mapping()

    def _build_ui(self) -> None:
        bg_path = str(Path(__file__).resolve().parent / "controller_bg.png")
        panel = QLabel(self)
        panel.setGeometry(0, 0, self._WINDOW_W, self._WINDOW_H)
        panel.setPixmap(QPixmap(bg_path).scaled(
            self._WINDOW_W,
            self._WINDOW_H,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))
        panel.setScaledContents(True)
        panel.lower()

        btn_style = (
            "QPushButton {"
            "  background: #f7f7f7; color: #000000;"
            "  border: 1px solid #cfcfcf; border-radius: 3px;"
            "  font-family: Arial, \"Microsoft YaHei UI\";"
            "  font-size: 13px; font-weight: 400;"
            "}"
            " QPushButton:hover {"
            "  background: #ffffff; border-color: #8ab4f8;"
            "}"
            " QPushButton:checked {"
            "  background: #ffffff; border: 2px solid #0078d7;"
            "}"
        )

        for x, y, w, h, name, label in self._BTN_POSITIONS:
            btn = QPushButton("", self)
            btn.setCheckable(True)
            btn.setGeometry(x, y, w, h)
            btn.setStyleSheet(btn_style)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setProperty("label", label)
            btn.setToolTip(f"{label}: {_qt_key_name(self._mapping.get(name, 0)) or '未绑定'}")
            btn.clicked.connect(lambda checked, n=name: self._select_button(n))
            self._buttons[name] = btn

        ok_btn = QPushButton("确定")
        ok_btn.setGeometry(239, 652, 223, 53)
        ok_btn.setStyleSheet(
            "QPushButton { background: #f7f7f7; border: 1px solid #cfcfcf; border-radius: 3px; font-size: 14px; }"
            " QPushButton:hover { background: #ffffff; border-color: #8ab4f8; }"
        )
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("取消")
        cancel_btn.setGeometry(537, 652, 223, 53)
        cancel_btn.setStyleSheet(
            "QPushButton { background: #f7f7f7; border: 1px solid #cfcfcf; border-radius: 3px; font-size: 14px; }"
            " QPushButton:hover { background: #ffffff; border-color: #8ab4f8; }"
        )
        cancel_btn.clicked.connect(self.reject)

    def _select_button(self, name: str) -> None:
        self._active_name = name
        for n, btn in self._buttons.items():
            btn.setChecked(n == name)
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def _load_mapping(self) -> None:
        for name, key in self._mapping.items():
            btn = self._buttons.get(name)
            if btn is not None:
                key_name = _qt_key_name(key)
                label = btn.property("label") or name
                btn.setText(key_name if key_name else "")
                btn.setToolTip(f"{label}: {key_name or '未绑定'}")

    def keyPressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self._active_name is None:
            super().keyPressEvent(event)
            return
        key = event.key()
        if key == Qt.Key.Key_Escape:
            key = 0
        self._mapping[self._active_name] = key
        btn = self._buttons[self._active_name]
        key_name = _qt_key_name(key)
        label = btn.property("label") or self._active_name
        btn.setText(key_name if key_name else "")
        btn.setToolTip(f"{label}: {key_name or '未绑定'}")
        btn.setChecked(False)
        self._active_name = None

    def get_mapping(self) -> dict[str, int]:
        return dict(self._mapping)


class EasyConPanel(QWidget):
    bridge_log = Signal(str, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = load_config()
        self._preferred_last_port = self.config.last_port
        self.installation = EasyConInstallation(path=None, error="未检测")
        self.bridge_backend: BridgeEasyConBackend | None = None
        self.bridge_run_thread: QThread | None = None
        self.bridge_run_worker: BridgeScriptWorker | None = None
        self.bridge_status = EasyConStatus.BRIDGE_DISCONNECTED
        self.bridge_connecting = False
        self.current_script_path: Path | None = None
        self.current_script_name = "未命名脚本"
        self.current_script_newline = "\n"
        self._saved_editor_text = ""
        self.parameter_widgets: dict[str, QLineEdit | QSpinBox] = {}
        self.parameter_defaults: dict[str, str] = {}
        self._parameter_line_indices: dict[str, int] = {}
        self._syncing_parameters = False
        self.virtual_controller_enabled = False
        self.virtual_controller_keys: dict[int, tuple[str, str, str | None]] = {}
        self.key_mapping: dict[str, int] = dict(DEFAULT_KEY_MAPPING)
        self._recording = False
        self._recorded_lines: list[str] = []
        self._last_record_ts: float = 0.0
        self.process: QProcess | None = None
        self.current_run_started_at: datetime | None = None
        self.current_run_script_path: Path | None = None
        self.current_run_port: str | None = None
        self.current_run_stdout: list[str] = []
        self.current_run_stderr: list[str] = []
        self.stop_requested = False
        self.task_state_text = "未检测"
        self.run_seconds = 0
        self.run_timer = QTimer(self)
        self.run_timer.setInterval(1000)
        self.run_timer.timeout.connect(self._tick_run_timer)
        self.bridge_log.connect(self._append_log)

        self._build_ui()
        # Ctrl+S 快捷键
        save_action = QAction("保存", self)
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(self.save_script)
        self.addAction(save_action)
        self._refresh_script_list()
        self.detect_easycon()
        self.refresh_ports()
        self._update_run_enabled()

    # ── 浅色主题颜色常量 ──────────────────────────────────
    CLR_BG = "#f2f1ee"
    CLR_PANEL_BG = "#e8e6e1"
    CLR_BORDER = "#c8c6c0"
    CLR_TEXT = "#1a1a1a"
    CLR_HINT = "#767676"
    CLR_LOG_BG = "#282826"
    CLR_LOG_TEXT = "#e7ece9"
    CLR_RUN_BTN = "#23936b"
    CLR_WHITE = "#ffffff"
    CLR_TIMER_BG = "#1a1a1a"
    CLR_TIMER_TEXT = "#ffffff"
    CLR_STATUSBAR_BG = "#e8e6e1"

    def _easycon_light_button(self, text: str, fixed_width: int = 0) -> QPushButton:
        btn = QPushButton(text)
        btn.setStyleSheet(
            f"""
            QPushButton {{
                background: {self.CLR_WHITE};
                border: 1px solid {self.CLR_BORDER};
                border-radius: 3px;
                padding: 4px 12px;
                color: {self.CLR_TEXT};
                font-size: 12px;
            }}
            QPushButton:hover {{ background: #e8e6e1; }}
            QPushButton:pressed {{ background: #d4d2cc; }}
            """
        )
        if fixed_width:
            btn.setFixedWidth(fixed_width)
        return btn

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.setStyleSheet(f"QWidget {{ background: {self.CLR_BG}; color: {self.CLR_TEXT}; font-size: 12px; }}")

        # 主内容区
        content = QWidget()
        content.setStyleSheet(f"background: {self.CLR_BG};")
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(6, 6, 6, 6)
        content_layout.setSpacing(6)

        content_layout.addWidget(self._build_log_area(), 42)
        content_layout.addWidget(self._build_editor_area(), 55)
        content_layout.addWidget(self._build_right_buttons(), 0)

        layout.addWidget(content, 1)

        # 底部三栏
        layout.addWidget(self._build_bottom_panel())

        # 状态栏
        layout.addWidget(self._build_bottom_status())

    # ── 菜单栏 ──────────────────────────────────────────

    def _build_menu_bar(self) -> QMenuBar:
        menubar = QMenuBar()
        menubar.setStyleSheet(
            f"""
            QMenuBar {{ background: {self.CLR_WHITE}; border-bottom: 1px solid {self.CLR_BORDER}; padding: 2px 6px; }}
            QMenuBar::item {{ padding: 3px 10px; color: {self.CLR_TEXT}; }}
            QMenuBar::item:selected {{ background: {self.CLR_PANEL_BG}; }}
            QMenu {{ background: {self.CLR_WHITE}; border: 1px solid {self.CLR_BORDER}; }}
            QMenu::item {{ padding: 4px 28px 4px 12px; }}
            QMenu::item:selected {{ background: {self.CLR_PANEL_BG}; }}
            """
        )

        file_menu = menubar.addMenu("文件")
        new_action = QAction("新建", self)
        new_action.setShortcut("Ctrl+N")
        file_menu.addAction(new_action)

        open_action = QAction("打开", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.open_script_dialog)
        file_menu.addAction(open_action)

        save_action = QAction("保存", self)
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(self.save_script)
        file_menu.addAction(save_action)

        file_menu.addSeparator()
        exit_action = QAction("退出", self)
        file_menu.addAction(exit_action)

        script_menu = menubar.addMenu("脚本")
        run_menu_action = QAction("运行", self)
        run_menu_action.setShortcut("F5")
        run_menu_action.triggered.connect(self.toggle_run)
        script_menu.addAction(run_menu_action)

        stop_action = QAction("停止", self)
        stop_action.triggered.connect(lambda: self.stop_bridge_script() if self._is_bridge_mode() else None)
        script_menu.addAction(stop_action)

        compile_action = QAction("编译", self)
        script_menu.addAction(compile_action)

        format_action = QAction("格式化", self)
        script_menu.addAction(format_action)

        menubar.addMenu("搜图")
        menubar.addMenu("设置")
        menubar.addMenu("ESP32")
        menubar.addMenu("画图")

        help_menu = menubar.addMenu("帮助")
        about_action = QAction("关于", self)
        help_menu.addAction(about_action)

        return menubar

    # ── 左区：输出日志 + 计时 + 运行 ─────────────────────

    def _build_log_area(self) -> QWidget:
        area = QWidget()
        area.setStyleSheet(f"background: {self.CLR_BG};")
        layout = QVBoxLayout(area)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 日志区标题
        log_header = QLabel("输出")
        log_header.setStyleSheet(
            f"font-weight: 700; font-size: 12px; padding: 2px 4px; border: 0; background: {self.CLR_BG};"
        )
        layout.addWidget(log_header)

        # 黑色日志面板
        log_panel = QFrame()
        log_panel.setStyleSheet(
            f"QFrame {{ background: {self.CLR_LOG_BG}; border: 1px solid {self.CLR_BORDER}; border-radius: 0; }}"
        )
        log_panel_layout = QVBoxLayout(log_panel)
        log_panel_layout.setContentsMargins(4, 4, 4, 4)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setObjectName("EasyConLog")
        self.log_view.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        self.log_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.log_view.customContextMenuRequested.connect(self._log_context_menu)
        self.log_view.setStyleSheet(
            f"""
            QTextEdit {{
                background: {self.CLR_LOG_BG};
                color: {self.CLR_LOG_TEXT};
                border: 0;
                font-family: "Cascadia Mono", "Consolas", "Microsoft YaHei UI";
                font-size: 11px;
            }}
            """
        )
        self.log_view.document().setMaximumBlockCount(self.config.keep_log_lines)
        log_panel_layout.addWidget(self.log_view)
        layout.addWidget(log_panel, 1)

        # 计时 + 运行按钮（横向排列）
        action_row = QWidget()
        action_row.setStyleSheet(f"background: {self.CLR_BG};")
        action_layout = QHBoxLayout(action_row)
        action_layout.setContentsMargins(0, 3, 0, 0)
        action_layout.setSpacing(4)

        self.elapsed_label = QLabel("00:00:00")
        self.elapsed_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.elapsed_label.setMinimumHeight(70)
        self.elapsed_label.setStyleSheet(
            f"""
            QLabel {{
                background: {self.CLR_TIMER_BG};
                color: {self.CLR_TIMER_TEXT};
                font-size: 28px;
                font-weight: 700;
                font-family: "Cascadia Mono", "Consolas", "Microsoft YaHei UI";
                border: 0;
            }}
            """
        )
        action_layout.addWidget(self.elapsed_label, 5)

        self.run_button = QPushButton("运行脚本")
        self.run_button.setObjectName("PrimaryButton")
        self.run_button.setMinimumHeight(70)
        self.run_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.run_button.setStyleSheet(
            f"""
            QPushButton {{
                background: {self.CLR_RUN_BTN};
                color: white;
                font-size: 16px;
                font-weight: 700;
                border: 0;
                border-radius: 0;
            }}
            QPushButton:hover {{ background: #1e7d5a; }}
            QPushButton:pressed {{ background: #186b4c; }}
            QPushButton:disabled {{ background: #a0a0a0; }}
            """
        )
        self.run_button.clicked.connect(self.toggle_run)
        action_layout.addWidget(self.run_button, 5)

        layout.addWidget(action_row)
        return area

    # ── 中区：脚本编辑器 ────────────────────────────────

    def _build_editor_area(self) -> QWidget:
        area = QWidget()
        area.setStyleSheet(f"background: {self.CLR_BG};")
        layout = QVBoxLayout(area)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 编辑器标题行
        editor_header = QWidget()
        editor_header.setStyleSheet(f"background: {self.CLR_WHITE}; border-bottom: 1px solid {self.CLR_BORDER};")
        editor_header_layout = QHBoxLayout(editor_header)
        editor_header_layout.setContentsMargins(8, 3, 8, 3)
        self.script_name_label = QLabel("未命名脚本")
        self.script_name_label.setStyleSheet(f"font-size: 12px; color: {self.CLR_TEXT}; border: 0; background: transparent;")
        editor_header_layout.addWidget(self.script_name_label)
        self.template_mode_label = QLabel("普通脚本")
        self.template_mode_label.setStyleSheet(f"font-size: 12px; color: {self.CLR_HINT}; border: 0; background: transparent;")
        editor_header_layout.addWidget(self.template_mode_label)
        editor_header_layout.addStretch()

        layout.addWidget(editor_header)

        # 编辑器本体（白色背景）
        editor_frame = QFrame()
        editor_frame.setStyleSheet(
            f"QFrame {{ background: {self.CLR_WHITE}; border: 1px solid {self.CLR_BORDER}; border-top: 0; }}"
        )
        editor_frame_layout = QVBoxLayout(editor_frame)
        editor_frame_layout.setContentsMargins(0, 0, 0, 0)
        self.editor = EasyConScriptEditor()
        self.editor.fileDropped.connect(self.load_script)
        self.editor.textChanged.connect(self._on_editor_changed)
        self.editor.setStyleSheet(
            f"""
            QPlainTextEdit {{
                background: {self.CLR_WHITE};
                color: {self.CLR_TEXT};
                border: 0;
                font-family: "Cascadia Mono", "Consolas", "Microsoft YaHei UI";
                font-size: 13px;
                selection-background-color: #c8e0d0;
            }}
            """
        )
        editor_frame_layout.addWidget(self.editor, 1)
        layout.addWidget(editor_frame, 1)
        return area

    # ── 右区：操作按钮 ──────────────────────────────────

    def _build_right_buttons(self) -> QWidget:
        area = QWidget()
        area.setFixedWidth(130)
        area.setStyleSheet(f"background: {self.CLR_BG};")
        layout = QVBoxLayout(area)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)

        btn_style = (
            f"QPushButton {{ background: {self.CLR_WHITE}; border: 1px solid {self.CLR_BORDER};"
            f" border-radius: 2px; padding: 6px 8px; font-size: 12px; }}"
            f" QPushButton:hover {{ background: {self.CLR_PANEL_BG}; }}"
        )

        new_btn = QPushButton("新建")
        new_btn.setStyleSheet(btn_style)
        new_btn.clicked.connect(self.new_script)
        layout.addWidget(new_btn)

        self.open_button = QPushButton("打开")
        self.open_button.setStyleSheet(btn_style)
        self.open_button.clicked.connect(self.open_script_dialog)
        layout.addWidget(self.open_button)

        self.save_button = QPushButton("保存")
        self.save_button.setStyleSheet(btn_style)
        self.save_button.clicked.connect(self.save_script)
        layout.addWidget(self.save_button)

        layout.addStretch()
        return area

    # ── 底部三栏 ────────────────────────────────────────

    def _build_bottom_panel(self) -> QWidget:
        panel = QWidget()
        panel.setStyleSheet(f"background: {self.CLR_PANEL_BG}; border-top: 1px solid {self.CLR_BORDER};")
        outer = QHBoxLayout(panel)
        outer.setContentsMargins(6, 4, 6, 4)
        outer.setSpacing(6)

        outer.addWidget(self._build_serial_column(), 50)
        outer.addWidget(self._build_vpad_column(), 50)
        return panel

    def _build_serial_column(self) -> QWidget:
        group = QGroupBox("串口连接")
        group.setStyleSheet(
            f"QGroupBox {{ font-weight: 700; border: 1px solid {self.CLR_BORDER}; margin-top: 8px; padding-top: 14px;"
            f" background: {self.CLR_PANEL_BG}; }}"
            f" QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; }}"
        )
        layout = QVBoxLayout(group)
        layout.setContentsMargins(8, 4, 8, 8)
        layout.setSpacing(4)

        btn_style = (
            f"QPushButton {{ background: {self.CLR_WHITE}; border: 1px solid {self.CLR_BORDER};"
            f" border-radius: 2px; padding: 4px 10px; font-size: 11px; }}"
            f" QPushButton:hover {{ background: #e8e6e1; }}"
        )
        combo_style = (
            f"QComboBox {{ background: {self.CLR_WHITE}; border: 1px solid {self.CLR_BORDER};"
            f" padding: 3px 6px; font-size: 11px; min-height: 24px; max-height: 24px; }}"
            f" QComboBox::drop-down {{ border: 1px solid {self.CLR_BORDER}; width: 18px; }}"
            f" QComboBox QAbstractItemView {{ font-size: 11px; }}"
        )

        self.connect_button = QPushButton("自动连接(推荐)")
        self.connect_button.setStyleSheet(btn_style)
        self.connect_button.clicked.connect(self.toggle_bridge_connection)
        layout.addWidget(self.connect_button)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("连接模式"))
        self.backend_mode = QComboBox()
        self.backend_mode.addItem("常驻连接（Bridge）", "bridge")
        self.backend_mode.addItem("CLI 模式", "cli")
        self.backend_mode.setStyleSheet(combo_style)
        self.backend_mode.setCurrentIndex(1)  # 默认 CLI 模式
        self.backend_mode.currentIndexChanged.connect(self._backend_mode_changed)
        mode_row.addWidget(self.backend_mode, 1)
        layout.addLayout(mode_row)

        serial_row = QHBoxLayout()
        self.port_combo = QComboBox()
        self.port_combo.setEditable(True)
        self.port_combo.setCurrentText("下拉选择串口")
        self.port_combo.setStyleSheet(combo_style)
        self.port_combo.currentIndexChanged.connect(self._port_changed)
        serial_row.addWidget(self.port_combo, 1)

        self.disconnect_button = QPushButton("手动连接")
        self.disconnect_button.setStyleSheet(btn_style)
        self.disconnect_button.clicked.connect(self.connect_bridge)
        serial_row.addWidget(self.disconnect_button)
        layout.addLayout(serial_row)

        # 隐藏的后端配置（保留原有控件，用户可通过设置菜单访问）
        self._hidden_config_widgets()
        return group

    def _hidden_config_widgets(self) -> None:
        """创建隐藏的配置控件，保留原有业务逻辑所需的所有属性"""
        self.ezcon_path = QLineEdit(str(self.config.ezcon_path or ""))
        self.ezcon_path.setVisible(False)
        bridge_default = self.config.bridge_path or _default_bridge_path()
        self.bridge_path = QLineEdit(str(bridge_default or ""))
        self.bridge_path.setVisible(False)
        self.browse_ezcon_button = QPushButton()
        self.browse_ezcon_button.clicked.connect(self.choose_ezcon)
        self.browse_bridge_button = QPushButton()
        self.browse_bridge_button.clicked.connect(self.choose_bridge)
        self.version_label = QLabel("EasyCon: 未检测")
        self.backend_label = QLabel("单片机: 未连接")
        self.connection_state_label = QLabel("连接: 未检测")
        self.task_state_label = QLabel("任务: 未检测")
        self.refresh_ports_button = QPushButton("刷新串口")
        self.refresh_ports_button.clicked.connect(self.refresh_ports)
        self.auto_select_port_button = QPushButton("自动选择串口")
        self.auto_select_port_button.clicked.connect(self.auto_select_port)
        self.mock_check = QCheckBox("mock 模式")
        self.mock_check.setChecked(self.config.mock_enabled)
        self.mock_check.toggled.connect(self._save_config_from_ui)
        self.cli_test_button = QPushButton("测试 CLI 运行")
        self.cli_test_button.clicked.connect(self.run_cli_smoke_test)
        self.detect_button = QPushButton("检测 EasyCon")
        self.detect_button.clicked.connect(self.detect_easycon)
        self.toolbar_connect_button = QPushButton("连接伊机控")
        self.toolbar_connect_button.clicked.connect(self.toggle_bridge_connection)
        self.clear_log_button = QPushButton("清空日志")
        self.clear_log_button.clicked.connect(self.log_view.clear)
        self.copy_log_button = QPushButton("复制日志")
        self.copy_log_button.clicked.connect(self.copy_all_logs)
        self.save_log_button = QPushButton("保存日志")
        self.save_log_button.clicked.connect(self.save_logs_dialog)
        self.log_keep_lines = QSpinBox()
        self.log_keep_lines.setValue(self.config.keep_log_lines)
        self.log_keep_lines.valueChanged.connect(self._log_retention_changed)

        # 脚本列表（保留，通过菜单访问）
        self.script_list = QListWidget()
        self.script_list.itemDoubleClicked.connect(self._load_script_item)


    def _build_vpad_column(self) -> QWidget:
        group = QGroupBox("虚拟手柄")
        group.setStyleSheet(
            f"QGroupBox {{ font-weight: 700; border: 1px solid {self.CLR_BORDER}; margin-top: 8px; padding-top: 14px;"
            f" background: {self.CLR_PANEL_BG}; }}"
            f" QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; }}"
        )
        layout = QGridLayout(group)
        layout.setContentsMargins(8, 4, 8, 8)
        layout.setSpacing(4)

        btn_style = (
            f"QPushButton {{ background: {self.CLR_WHITE}; border: 1px solid {self.CLR_BORDER};"
            f" border-radius: 2px; padding: 4px 8px; font-size: 11px; }}"
            f" QPushButton:hover {{ background: #e8e6e1; }}"
        )
        combo_style = (
            f"QComboBox {{ background: {self.CLR_WHITE}; border: 1px solid {self.CLR_BORDER}; padding: 3px 6px; font-size: 11px; }}"
        )

        vpad_combo = QComboBox()
        vpad_combo.addItem("键盘")
        vpad_combo.setStyleSheet(combo_style)
        layout.addWidget(vpad_combo, 0, 0, 1, 1)

        self.keyboard_controller_check = QCheckBox("连接")
        self.keyboard_controller_check.setStyleSheet(f"font-size: 11px; background: transparent;")
        self.keyboard_controller_check.toggled.connect(self.set_keyboard_controller_enabled)
        layout.addWidget(self.keyboard_controller_check, 0, 1, 1, 1)

        mapping_btn = QPushButton("按键映射")
        mapping_btn.setStyleSheet(btn_style)
        mapping_btn.clicked.connect(self.open_key_mapping)
        layout.addWidget(mapping_btn, 1, 0, 1, 1)

        help_btn = QPushButton("帮助")
        help_btn.setStyleSheet(btn_style)
        layout.addWidget(help_btn, 1, 1, 1, 1)

        self.record_btn = QPushButton("录制脚本")
        self.record_btn.setStyleSheet(btn_style)
        self.record_btn.clicked.connect(self._start_recording)
        layout.addWidget(self.record_btn, 2, 0, 1, 1)

        self.pause_btn = QPushButton("暂停录制")
        self.pause_btn.setStyleSheet(btn_style)
        self.pause_btn.setEnabled(False)
        self.pause_btn.clicked.connect(self._toggle_pause_recording)
        layout.addWidget(self.pause_btn, 2, 1, 1, 1)

        # 手柄测试按钮（保留原有功能）
        self.controller_duration = QSpinBox()
        self.controller_duration.setRange(20, 5000)
        self.controller_duration.setValue(100)
        self.test_a_button = QPushButton("A")
        self.test_a_button.clicked.connect(lambda: self.send_controller_press("A"))
        self.test_b_button = QPushButton("B")
        self.test_b_button.clicked.connect(lambda: self.send_controller_press("B"))
        self.test_home_button = QPushButton("HOME")
        self.test_home_button.clicked.connect(lambda: self.send_controller_press("HOME"))
        self.test_ls_reset_button = QPushButton("LS RESET")
        self.test_ls_reset_button.clicked.connect(lambda: self.send_controller_stick("left", "RESET"))
        self.test_rs_reset_button = QPushButton("RS RESET")
        self.test_rs_reset_button.clicked.connect(lambda: self.send_controller_stick("right", "RESET"))

        self.keyboard_mapping_label = QLabel("L/K/I/J=A/B/X/Y，WASD=左摇杆，方向键=右摇杆")
        self.keyboard_mapping_label.setStyleSheet(f"font-size: 10px; color: {self.CLR_HINT}; background: transparent;")

        return group

    # ── 底部状态栏 ──────────────────────────────────────

    def _build_bottom_status(self) -> QStatusBar:
        self.easycon_status = QStatusBar()
        self.easycon_status.setSizeGripEnabled(False)
        self.easycon_status.setStyleSheet(
            f"""
            QStatusBar {{ background: {self.CLR_STATUSBAR_BG}; border-top: 1px solid {self.CLR_BORDER}; padding: 2px 8px; }}
            QStatusBar::item {{ border: 0; }}
            """
        )

        self.status_easycon_label = QLabel("LOG")
        self.status_controller_label = QLabel("单片机未连接")
        self.status_backend_label = QLabel("后端: 常驻连接")

        for label in (
            self.status_easycon_label,
            self.status_controller_label,
            self.status_backend_label,
        ):
            label.setStyleSheet(
                f"font-size: 11px; color: {self.CLR_HINT}; padding: 0 10px; border: 0; background: transparent;"
            )
            self.easycon_status.addPermanentWidget(label)

        return self.easycon_status

    def _refresh_script_list(self) -> None:
        self.script_list.clear()
        for path in scan_builtin_scripts(SCRIPT_DIR):
            item = QListWidgetItem(path.name)
            item.setData(Qt.ItemDataRole.UserRole, path)
            self.script_list.addItem(item)

    def detect_easycon(self) -> None:
        override_path = self.ezcon_path.text().strip() if hasattr(self, "ezcon_path") else ""
        config = replace(self.config, ezcon_path=Path(override_path) if override_path else self.config.ezcon_path)
        self.installation = discover_ezcon(config)
        if self.installation.path is not None:
            self.ezcon_path.setText(str(self.installation.path))
        if self.installation.is_available:
            self.version_label.setText(f"EasyCon: {self.installation.source}")
        else:
            self.version_label.setText("EasyCon: 未找到")
            self._append_log("warn", _easycon_unavailable_message(self.installation, self.ezcon_path.text().strip()))
        self._save_config_from_ui()
        self._update_run_enabled()

    def refresh_ports(self) -> None:
        ports = list_ports(self.installation) if self.installation.is_available else []
        self.port_combo.blockSignals(True)
        self.port_combo.clear()
        self.port_combo.addItems(ports)
        selected = self._select_preferred_port(ports)
        if selected is not None:
            self.port_combo.setCurrentText(selected)
        self.port_combo.blockSignals(False)
        self._append_log("info", f"已刷新串口: {', '.join(ports) if ports else '未发现'}")
        if not ports and not self.mock_check.isChecked():
            self._append_log("warn", "未发现串口；请选择串口或启用 mock 模式，运行按钮已禁用")
        self._save_config_from_ui()
        self._update_run_enabled()
        self._update_status_labels()

    def auto_select_port(self) -> None:
        ports = [self.port_combo.itemText(index) for index in range(self.port_combo.count())]
        selected = self._select_preferred_port(ports)
        if selected is None:
            self._append_log("warn", "未发现可自动选择的串口，请刷新串口或启用 mock 模式")
            self._update_run_enabled()
            return
        self.port_combo.setCurrentText(selected)
        self._preferred_last_port = selected
        self._append_log("info", f"已自动选择串口: {selected}")
        self._save_config_from_ui()
        self._update_run_enabled()
        self._update_status_labels()

    def _select_preferred_port(self, ports: list[str]) -> str | None:
        if self._preferred_last_port in ports:
            return self._preferred_last_port
        if self.config.last_port in ports:
            return self.config.last_port
        if len(ports) == 1:
            return ports[0]
        if self.port_combo.currentText() in ports:
            return self.port_combo.currentText()
        return None

    def choose_ezcon(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择 ezcon.exe", "", "EasyCon CLI (ezcon.exe);;All files (*.*)")
        if not path:
            return
        self.ezcon_path.setText(path)
        self.detect_easycon()
        self.refresh_ports()

    def choose_bridge(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 EasyConBridge.exe",
            "",
            "EasyCon Bridge (EasyConBridge.exe);;All files (*.*)",
        )
        if not path:
            return
        self.bridge_path.setText(path)
        self.bridge_backend = None
        self._save_config_from_ui()
        self._update_bridge_controls()

    def open_script_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "打开伊机控脚本", str(SCRIPT_DIR), "EasyCon scripts (*.txt *.ecs)")
        if path:
            self.load_script(Path(path))

    def load_script(self, path: Path) -> None:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            QMessageBox.warning(self, "脚本编码不明确", "脚本不是 UTF-8 编码，暂不加载以避免乱码。")
            return
        parameters = parse_script_parameters(text)
        self.parameter_defaults = {parameter.name: parameter.default for parameter in parameters}
        saved_values = self.config.script_parameters.get(self._script_config_key(path), {})
        if saved_values:
            text = apply_parameter_values(text, saved_values)
            parameters = parse_script_parameters(text)
        self.current_script_path = path
        self.current_script_name = path.name
        self.current_script_newline = detect_newline_style(text)
        self._saved_editor_text = text
        self.script_name_label.setText(path.name)
        self._syncing_parameters = True
        self.editor.setPlainText(text)
        self._syncing_parameters = False
        self._rebuild_parameter_widgets(parameters)
        self.template_mode_label.setText("模板副本" if any(value == "填入这里" for value in self.parameter_defaults.values()) else "普通脚本")
        self._update_run_enabled()
        self._append_log("info", f"已加载脚本: {path.name}")
        self._remember_recent_script(path)

    def _load_script_item(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(path, Path):
            self.load_script(path)

    def new_script(self) -> None:
        self.current_script_path = None
        self.current_script_name = "未命名文档.txt"
        self.current_script_newline = "\n"
        self.script_name_label.setText("未命名文档.txt")
        self._saved_editor_text = ""
        self.editor.setPlainText("")
        self._append_log("info", "已新建空白脚本")

    def save_script(self) -> Path | None:
        if not self.editor.toPlainText().strip():
            self._append_log("warn", "没有可保存的脚本内容")
            return None
        if self.current_script_path is not None:
            # 已有文件路径，直接覆盖保存
            try:
                self.current_script_path.write_text(
                    self.editor.toPlainText(),
                    encoding="utf-8",
                    newline=self.current_script_newline,
                )
            except OSError as exc:
                self._append_log("error", f"保存脚本失败: {exc}")
                return None
            self._saved_editor_text = self.editor.toPlainText()
            # 同步持久化参数值到当前编辑器状态，避免下次打开时旧参数覆盖新内容
            self._sync_persisted_params_from_editor()
            self._update_dirty_indicator()
            self._append_log("info", f"已保存: {self.current_script_path.name}")
            return self.current_script_path
        # 新文件，弹出保存对话框
        path, _ = QFileDialog.getSaveFileName(
            self, "保存脚本", str(SCRIPT_DIR), "EasyCon scripts (*.txt *.ecs)"
        )
        if not path:
            return None
        output = Path(path)
        try:
            output.write_text(
                self.editor.toPlainText(),
                encoding="utf-8",
                newline=self.current_script_newline,
            )
        except OSError as exc:
            self._append_log("error", f"保存脚本失败: {exc}")
            return None
        self.current_script_path = output
        self.current_script_name = output.name
        self._saved_editor_text = self.editor.toPlainText()
        self.script_name_label.setText(output.name)
        self._update_dirty_indicator()
        self._remember_recent_script(output)
        self._append_log("info", f"已保存: {output.name}")
        return output

    def _rebuild_parameter_widgets(self, parameters) -> None:
        self.parameter_widgets = {}
        self._parameter_line_indices = {}
        for parameter in parameters:
            self._parameter_line_indices[parameter.name] = parameter.line_index
            default_value = self.parameter_defaults.get(parameter.name, parameter.default)
            if default_value.isdigit():
                widget = QSpinBox()
                widget.setRange(0, 1_000_000_000)
                widget.setValue(int(parameter.value) if parameter.value.isdigit() else int(default_value))
                widget.valueChanged.connect(lambda _value, name=parameter.name: self._parameter_value_changed(name))
            else:
                widget = QLineEdit(parameter.value)
                widget.textChanged.connect(lambda _text, name=parameter.name: self._parameter_value_changed(name))
            self.parameter_widgets[parameter.name] = widget

    def _parameter_value_changed(self, name: str) -> None:
        if self._syncing_parameters:
            return
        widget = self.parameter_widgets.get(name)
        if widget is None:
            return
        value = str(widget.value()) if isinstance(widget, QSpinBox) else widget.text()
        self._syncing_parameters = True
        self.editor.setPlainText(apply_parameter_values(self.editor.toPlainText(), {name: value}))
        self._syncing_parameters = False
        self._persist_parameter_value(name, value)
        self._update_run_enabled()
        self._update_dirty_indicator()

    def _persist_parameter_value(self, name: str, value: str) -> None:
        if self.current_script_path is None:
            return
        key = self._script_config_key(self.current_script_path)
        script_parameters = {item_key: dict(values) for item_key, values in self.config.script_parameters.items()}
        script_parameters.setdefault(key, {})[name] = value
        self.config = replace(self.config, script_parameters=script_parameters)
        save_config(self.config)

    def _sync_persisted_params_from_editor(self) -> None:
        """保存时同步持久化参数到编辑器当前值，避免下次加载时旧值覆盖。"""
        if self.current_script_path is None:
            return
        key = self._script_config_key(self.current_script_path)
        script_parameters = {item_key: dict(values) for item_key, values in self.config.script_parameters.items()}
        editor_params = {p.name: p.value for p in parse_script_parameters(self.editor.toPlainText())}
        script_parameters[key] = editor_params
        self.config = replace(self.config, script_parameters=script_parameters)
        save_config(self.config)

    def _script_config_key(self, path: Path) -> str:
        try:
            if path.resolve().parent == SCRIPT_DIR.resolve():
                return f"script/{path.name}"
        except OSError:
            pass
        try:
            return path.resolve().as_posix()
        except OSError:
            return path.as_posix()

    def restore_template_defaults(self) -> None:
        if not self.parameter_defaults:
            return
        self._syncing_parameters = True
        self.editor.setPlainText(apply_parameter_values(self.editor.toPlainText(), self.parameter_defaults))
        parameters = parse_script_parameters(self.editor.toPlainText())
        for parameter in parameters:
            widget = self.parameter_widgets.get(parameter.name)
            if isinstance(widget, QSpinBox):
                widget.setValue(int(parameter.value) if parameter.value.isdigit() else 0)
            elif isinstance(widget, QLineEdit):
                widget.setText(parameter.value)
        self._syncing_parameters = False
        self._update_run_enabled()
        self._update_dirty_indicator()

    def _validate_parameters_for_run(self, *, focus: bool = False) -> bool:
        for parameter in parse_script_parameters(self.editor.toPlainText()):
            if not parameter.required:
                continue
            if focus:
                block = self.editor.document().findBlockByNumber(parameter.line_index)
                cursor = QTextCursor(block)
                self.editor.setTextCursor(cursor)
                self.editor.setFocus()
                self._append_log("error", f"第 {parameter.line_index + 1} 行参数 {parameter.name} 需要填写")
            return False
        return True

    def save_generated_script(self) -> Path | None:
        if not self.editor.toPlainText().strip():
            self._append_log("warn", "没有可保存的脚本内容")
            return None
        try:
            path = generate_script_file(
                self.editor.toPlainText(),
                self.current_script_name,
                GENERATED_DIR,
                newline=self.current_script_newline,
            )
        except OSError as exc:
            self._append_log("error", f"保存临时脚本失败: {exc}")
            return None
        self._append_log("info", f"已保存临时脚本: {path.name}")
        return path





    def toggle_run(self) -> None:
        if self._is_bridge_mode():
            self.run_script()
            return
        if self.process is not None and self.process.state() != QProcess.ProcessState.NotRunning:
            self.stop_requested = True
            self.process.kill()
            self._append_log("warn", "正在停止脚本")
            return
        self.run_script()

    def run_script(self) -> None:
        if self._is_bridge_mode():
            self.run_script_via_bridge()
            return
        self.detect_easycon()
        if not self._can_run():
            self._append_log("warn", "配置未完成，无法运行脚本")
            return
        script_path = self.save_generated_script()
        if script_path is None:
            return
        port = "mock" if self.mock_check.isChecked() else self.port_combo.currentText()
        self.process = QProcess(self)
        self.process.setProgram(str(self.installation.path))
        self.process.setArguments(["run", str(script_path), "-p", port])
        self.process.readyReadStandardOutput.connect(self._read_stdout)
        self.process.readyReadStandardError.connect(self._read_stderr)
        self.process.finished.connect(self._process_finished)
        self.process.errorOccurred.connect(self._process_error)
        self.stop_requested = False
        self.current_run_stdout = []
        self.current_run_stderr = []
        self.current_run_started_at = datetime.now()
        self.current_run_script_path = script_path
        self.current_run_port = port
        self.run_seconds = 0
        self.elapsed_label.setText("00:00:00")
        self.run_timer.start()
        self.run_button.setText("停止脚本")
        self._append_log("warn", cli_connection_notice())
        self._append_log("info", f"开始运行脚本，端口: {port}")
        self.task_state_text = "执行中"
        self._update_status_labels()
        self.process.start()

    def run_script_via_bridge(self) -> None:
        if self.bridge_status == EasyConStatus.RUNNING:
            self.stop_bridge_script()
            return
        if not self._can_run():
            self._append_log("warn", "请先连接伊机控，再运行脚本")
            return
        generated_script = self.save_generated_script()
        if generated_script is None:
            return
        script_text = self.editor.toPlainText()
        started_at = datetime.now()
        self.stop_requested = False
        self.current_run_stdout = []
        self.current_run_stderr = []
        self.current_run_started_at = started_at
        self.current_run_script_path = generated_script
        self.current_run_port = self.port_combo.currentText()
        self.run_seconds = 0
        self.elapsed_label.setText("00:00:00")
        self.run_timer.start()
        self.run_button.setText("停止脚本")
        self.run_button.setEnabled(True)
        self.bridge_status = EasyConStatus.RUNNING
        self.task_state_text = "执行中"
        self._update_bridge_controls()
        # 释放虚拟手柄状态，避免 eventFilter 残留输入影响脚本时序
        self._release_virtual_controller_keys()
        self._append_log("info", "通过常驻连接运行脚本")
        thread = QThread(self)
        worker = BridgeScriptWorker(self._ensure_bridge_backend(), script_text, self.current_script_name)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._bridge_run_finished)
        worker.failed.connect(self._bridge_run_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(self._bridge_run_thread_finished)
        self.bridge_run_thread = thread
        self.bridge_run_worker = worker
        thread.start()

    def _bridge_run_finished(self, result: object) -> None:
        if result.stdout:
            self._append_log("stdout", result.stdout.rstrip())
        if result.stderr:
            self._append_log("stderr", result.stderr.rstrip())
        status = (
            "已中止"
            if self.stop_requested or result.exit_code == 130
            else "已完成，连接保持"
            if result.status == EasyConStatus.COMPLETED
            else "失败"
        )
        self.stop_requested = False
        try:
            self.bridge_status = self._ensure_bridge_backend().status()
        except Exception as exc:
            self.bridge_status = EasyConStatus.FAILED
            self._append_log("error", f"Bridge 状态刷新失败: {exc}")
        self._finish_run(
            status,
            exit_code=result.exit_code,
            started_at=result.started_at,
            ended_at=result.ended_at,
            script_path=self.current_run_script_path,
            port=result.port,
        )
        self._update_bridge_controls()

    def _bridge_run_failed(self, error: str) -> None:
        self.stop_requested = False
        self._append_log("error", f"Bridge 运行失败: {error}")
        self.bridge_status = EasyConStatus.FAILED
        self._finish_run(
            "失败",
            exit_code=1,
            started_at=self.current_run_started_at,
            script_path=self.current_run_script_path,
            port=self.current_run_port,
        )
        self._update_bridge_controls()

    def _bridge_run_thread_finished(self) -> None:
        thread = self.bridge_run_thread
        self.bridge_run_thread = None
        self.bridge_run_worker = None
        if thread is not None:
            thread.deleteLater()

    def stop_bridge_script(self) -> None:
        try:
            self._ensure_bridge_backend().stop_current_script()
        except Exception as exc:
            self._append_log("error", f"停止 Bridge 当前任务失败: {exc}")
            return
        self.stop_requested = True
        self.task_state_text = "正在停止"
        self._append_log("warn", "已请求停止当前 Bridge 任务，等待脚本退出")
        self._update_status_labels()

    def begin_external_bridge_script(self, name: str) -> None:
        started_at = datetime.now()
        self.stop_requested = False
        self.current_run_stdout = []
        self.current_run_stderr = []
        self.current_run_started_at = started_at
        self.current_run_script_path = Path(name)
        self.current_run_port = self.port_combo.currentText()
        self.run_seconds = 0
        self.elapsed_label.setText("00:00:00")
        self.run_timer.start()
        self.run_button.setText("停止脚本")
        self.run_button.setEnabled(True)
        self.bridge_status = EasyConStatus.RUNNING
        self.task_state_text = "执行中"
        self._append_log("info", f"自动流程运行脚本: {name}")
        self._update_bridge_controls()

    def finish_external_bridge_script(self, result: object) -> None:
        stdout = getattr(result, "stdout", "")
        stderr = getattr(result, "stderr", "")
        if stdout:
            self._append_log("stdout", str(stdout).rstrip())
        if stderr:
            self._append_log("stderr", str(stderr).rstrip())
        status = (
            "已中止"
            if self.stop_requested or getattr(result, "exit_code", None) == 130
            else "已完成，连接保持"
            if getattr(result, "status", None) == EasyConStatus.COMPLETED
            else "失败"
        )
        self.stop_requested = False
        self.bridge_status = EasyConStatus.BRIDGE_CONNECTED if status.startswith("已完成") else EasyConStatus.FAILED
        self._finish_run(
            status,
            exit_code=getattr(result, "exit_code", None),
            started_at=getattr(result, "started_at", None),
            ended_at=getattr(result, "ended_at", None),
            script_path=getattr(result, "script_path", None),
            port=getattr(result, "port", None),
        )

    def fail_external_bridge_script(self, error: str) -> None:
        self.stop_requested = False
        self.bridge_status = EasyConStatus.FAILED
        self._append_log("error", f"自动流程脚本失败: {error}")
        self._finish_run("失败", exit_code=1)

    @staticmethod
    def _filter_cli_line(line: str) -> str | None:
        """过滤 EasyCon CLI 输出：去掉 ANSI 码和冗余启动信息，保留关键行"""
        import re
        # 去掉 ANSI 转义码
        clean = re.sub(r'\x1b\[[0-9;]*m', '', line).strip()
        if not clean:
            return None
        # 跳过的冗余启动信息
        skip_prefixes = (
            "---- EasyCon CLI Runner",
            "---- 仅供内部测试",
            "------------------------------------------",
            "配置文件不存在",
            "准备执行脚本... 环境信息",
            "准备加载搜图标签...",
            "标签（脚本）路径",
            "已加载标签",
            "正在解析脚本...",
            "准备连接单片机...",
            "==>开始执行脚本",
            "已完成，连接保持",
        )
        for prefix in skip_prefixes:
            if clean.startswith(prefix):
                return None
        # 跳过 exit code 0（正常完成不需要显示）
        if clean == "exit code: 0" or clean.startswith("exit code: 0"):
            return None
        return clean

    def _read_stdout(self) -> None:
        if self.process is None:
            return
        text = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        if text:
            self.current_run_stdout.append(text)
            for raw_line in text.splitlines():
                filtered = self._filter_cli_line(raw_line)
                if filtered:
                    self._append_log("stdout", filtered)

    def _read_stderr(self) -> None:
        if self.process is None:
            return
        text = bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace")
        if text:
            self.current_run_stderr.append(text)
            import re
            for raw_line in text.splitlines():
                clean = re.sub(r'\x1b\[[0-9;]*m', '', raw_line).strip()
                if clean:
                    self._append_log("stderr", clean)

    def _process_finished(self, exit_code: int, _status) -> None:  # type: ignore[no-untyped-def]
        if self.stop_requested:
            self.stop_requested = False
            self._finish_run("已中止", exit_code=130)
            return
        if exit_code != 0:
            self._report_cli_failure(exit_code)
        status = "已完成" if exit_code == 0 else f"失败，exit code: {exit_code}"
        self._finish_run(status, exit_code=exit_code)

    def _process_error(self, error) -> None:  # type: ignore[no-untyped-def]
        self._append_log("error", f"进程启动失败: {error}")
        self._finish_run("失败", exit_code=None)

    def _finish_run(
        self,
        status: str,
        exit_code: int | None = None,
        started_at: datetime | None = None,
        ended_at: datetime | None = None,
        script_path: Path | None = None,
        port: str | None = None,
    ) -> None:
        ended_at = ended_at or datetime.now()
        started_at = started_at or self.current_run_started_at
        script_path = script_path or self.current_run_script_path
        port = port or self.current_run_port
        self.run_timer.stop()
        self.run_button.setText("运行脚本")
        self.easycon_status.showMessage(status)
        self.task_state_text = "已完成" if status.startswith("已完成") else status
        self._append_log("info", status)
        self._append_run_summary(exit_code, started_at, ended_at, script_path, port)
        if exit_code == 0 and status.startswith("已完成"):
            self._prune_successful_generated_scripts()
        self.current_run_started_at = None
        self.current_run_script_path = None
        self.current_run_port = None
        self.current_run_stdout = []
        self.current_run_stderr = []
        self._update_run_enabled()

    def _prune_successful_generated_scripts(self) -> None:
        try:
            removed = prune_generated_scripts(GENERATED_DIR, self.config.keep_generated)
        except OSError as exc:
            self._append_log("warn", f"清理临时脚本失败: {exc}")
            return
        if removed:
            self._append_log("info", f"已按配置保留最近 {self.config.keep_generated} 个临时脚本，清理 {len(removed)} 个")

    def _report_cli_failure(self, exit_code: int | None) -> None:
        stdout = "".join(self.current_run_stdout)
        stderr = "".join(self.current_run_stderr)
        failure_type = classify_cli_failure(stdout, stderr, exit_code)
        if failure_type == "script_compile_failed":
            line = extract_compile_error_line(stdout, stderr)
            if line is None:
                self._append_log("error", "脚本编译失败，请查看 stderr/stdout。")
                return
            self._append_log("error", f"脚本编译失败，疑似行号: {line}")
            self.editor.go_to_line(line)
            return
        if failure_type == "device_connection_failed":
            self._append_log("error", "单片机连接失败，请检查串口、reset 和端口占用情况。")

    def _tick_run_timer(self) -> None:
        self.run_seconds += 1
        hours, remainder = divmod(self.run_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        self.elapsed_label.setText(f"{hours:02d}:{minutes:02d}:{seconds:02d}")

    def _can_run(self) -> bool:
        if self._is_bridge_mode():
            if self.bridge_status != EasyConStatus.BRIDGE_CONNECTED:
                return False
        elif not self.installation.is_available:
            return False
        if not self.editor.toPlainText().strip():
            return False
        if not self._validate_parameters_for_run(focus=False):
            return False
        if not self._is_bridge_mode() and not self.mock_check.isChecked() and not self.port_combo.currentText():
            return False
        if self.process is not None and self.process.state() != QProcess.ProcessState.NotRunning:
            return False
        return True

    def _on_editor_changed(self) -> None:
        self._update_run_enabled()
        self._update_dirty_indicator()

    def _update_dirty_indicator(self) -> None:
        if not hasattr(self, "script_name_label"):
            return
        current = self.editor.toPlainText()
        dirty = current != self._saved_editor_text
        name = self.current_script_name
        if dirty and not name.startswith("*"):
            name = "*" + name
        elif not dirty and name.startswith("*"):
            name = name[1:]
        self.script_name_label.setText(name)

    def _update_run_enabled(self) -> None:
        if hasattr(self, "run_button"):
            self.run_button.setEnabled(self._can_run())
        if hasattr(self, "connect_button"):
            self._update_bridge_controls()
        if hasattr(self, "connection_state_label"):
            self._update_status_labels()



    def _save_config_from_ui(self) -> None:
        if not hasattr(self, "ezcon_path"):
            return
        ezcon_text = self.ezcon_path.text().strip()
        bridge_text = self.bridge_path.text().strip() if hasattr(self, "bridge_path") else ""
        self.config = EasyConConfig(
            ezcon_path=Path(ezcon_text) if ezcon_text else None,
            bridge_path=Path(bridge_text) if bridge_text else None,
            last_port=self.port_combo.currentText() or self.config.last_port,
            mock_enabled=self.mock_check.isChecked(),
            recent_scripts=self.config.recent_scripts,
            script_parameters=self.config.script_parameters,
            keep_generated=self.config.keep_generated,
            keep_log_lines=self.log_keep_lines.value() if hasattr(self, "log_keep_lines") else self.config.keep_log_lines,
        )
        save_config(self.config)

    def _remember_recent_script(self, path: Path) -> None:
        try:
            normalized = path.resolve()
        except OSError:
            normalized = path
        recent = [normalized]
        for item in self.config.recent_scripts:
            try:
                existing = item.resolve()
            except OSError:
                existing = item
            if existing != normalized:
                recent.append(existing)
        self.config = replace(self.config, recent_scripts=tuple(recent[:10]))
        save_config(self.config)

    def _log_retention_changed(self) -> None:
        self.log_view.document().setMaximumBlockCount(self.log_keep_lines.value())
        self._save_config_from_ui()

    def _append_log(self, level: str, message: str) -> None:
        color = {
            "info": "#E7ECE9",
            "warn": "#E6D79B",
            "error": "#FF8A8A",
            "stdout": "#E7ECE9",
            "stderr": "#FFB1A8",
        }.get(level, "#E7ECE9")
        ts = datetime.now().strftime("%H:%M:%S")
        for line in message.splitlines() or [""]:
            self.log_view.append(f'<span style="color:{color}">[{ts}] {line}</span>')
        self.log_view.moveCursor(QTextCursor.MoveOperation.End)

    def _log_context_menu(self, pos):
        menu = self.log_view.createStandardContextMenu()
        if menu is None or menu.isEmpty():
            menu = QMenu(self)
            menu.addAction("复制", self.log_view.copy, QKeySequence("Ctrl+C"))
            menu.addAction("全选", self.log_view.selectAll, QKeySequence("Ctrl+A"))
        menu.exec(self.log_view.mapToGlobal(pos))

    def copy_all_logs(self) -> None:
        QApplication.clipboard().setText(self.log_view.toPlainText())
        self._append_log("info", "已复制全部日志")

    def save_logs_dialog(self) -> Path | None:
        default_name = f"easycon_log_{datetime.now():%Y%m%d_%H%M%S}.txt"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "保存伊机控日志",
            default_name,
            "Text files (*.txt);;All files (*.*)",
        )
        if not path:
            return None
        output = Path(path)
        try:
            output.write_text(self.log_view.toPlainText(), encoding="utf-8")
        except OSError as exc:
            self._append_log("error", f"保存日志失败: {exc}")
            return None
        self._append_log("info", f"已保存日志: {output}")
        return output

    def _append_run_summary(
        self,
        exit_code: int | None,
        started_at: datetime | None,
        ended_at: datetime,
        script_path: Path | None,
        port: str | None,
    ) -> None:
        if started_at is None:
            return
        duration = max(0.0, (ended_at - started_at).total_seconds())
        self._append_log("info", f"exit code: {exit_code if exit_code is not None else '无'}")
        self._append_log("info", f"开始时间: {started_at:%Y-%m-%d %H:%M:%S}")
        self._append_log("info", f"结束时间: {ended_at:%Y-%m-%d %H:%M:%S}")
        self._append_log("info", f"运行时长: {duration:.2f}s")
        if script_path is not None:
            self._append_log("info", f"脚本路径: {script_path}")
        if port:
            self._append_log("info", f"串口: {port}")

    def toggle_bridge_connection(self) -> None:
        if self.bridge_status == EasyConStatus.BRIDGE_CONNECTED:
            self.disconnect_bridge()
            return
        self.connect_bridge()

    def connect_bridge(self) -> None:
        if not self._bridge_path_from_ui():
            self._append_log("warn", "请先选择 EasyConBridge.exe")
            return
        port = self.port_combo.currentText()
        if not port:
            self._append_log("warn", "请先选择串口")
            return
        self.bridge_connecting = True
        self.task_state_text = "正在连接"
        self._update_bridge_controls()
        try:
            self._ensure_bridge_backend().connect(port)
        except Exception as exc:
            self.bridge_connecting = False
            self.bridge_status = EasyConStatus.FAILED
            self.task_state_text = "连接失败"
            self._append_log("error", f"连接伊机控失败: {exc}")
            self._update_bridge_controls()
            return
        self.bridge_connecting = False
        self.bridge_status = EasyConStatus.BRIDGE_CONNECTED
        self.task_state_text = "已完成"
        self._append_log("info", f"已连接伊机控: {port}")
        self.easycon_status.showMessage("已长期连接")
        self._show_connection_toast(port)
        self._update_bridge_controls()
        self._update_run_enabled()

    def disconnect_bridge(self) -> None:
        if self.virtual_controller_enabled:
            self.keyboard_controller_check.setChecked(False)
        try:
            self._ensure_bridge_backend().disconnect()
        except Exception as exc:
            self._append_log("error", f"断开伊机控失败: {exc}")
            return
        self.bridge_status = EasyConStatus.BRIDGE_DISCONNECTED
        self._append_log("info", "已断开伊机控")
        self.easycon_status.showMessage("已断开")
        self._update_bridge_controls()
        self._update_run_enabled()

    def _show_connection_toast(self, port: str) -> None:
        """连接成功弹出提示窗口，2秒后自动消失。"""
        toast = QLabel(f" 已连接单片机\n{port}", self)
        toast.setStyleSheet("""
            QLabel {
                background-color: #27ae60;
                color: white;
                padding: 14px 28px;
                border-radius: 10px;
                font-size: 15px;
                font-weight: bold;
            }
        """)
        toast.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        toast.setAlignment(Qt.AlignmentFlag.AlignCenter)
        toast.adjustSize()
        # 定位在主窗口中央
        window = self.window()
        if window is not None:
            center = window.geometry().center()
            toast.move(center.x() - toast.width() // 2, center.y() - toast.height() // 2)
        toast.show()
        QTimer.singleShot(2000, toast.deleteLater)

    def send_controller_press(self, button: str) -> None:
        duration = self.controller_duration.value()
        if self._is_bridge_mode():
            if self.bridge_status != EasyConStatus.BRIDGE_CONNECTED:
                self._append_log("warn", "请先连接伊机控，再测试手柄按钮")
                return
            try:
                self._ensure_bridge_backend().press(button, duration)
            except Exception as exc:
                self.bridge_status = EasyConStatus.FAILED
                self.task_state_text = "连接失败"
                self._append_log("error", f"发送手柄按钮失败: {exc}")
                self._update_bridge_controls()
                return
            self.task_state_text = "已完成"
            self._append_log("info", f"手柄测试: {button} {duration}ms")
            self._update_status_labels()
            return
        self._run_inline_cli_script(f"test_{button.lower()}", f"{button} {duration}\n")

    def send_controller_stick(self, side: str, direction: str) -> None:
        duration = self.controller_duration.value()
        label = f"{side.upper()} {direction}"
        if self._is_bridge_mode():
            if self.bridge_status != EasyConStatus.BRIDGE_CONNECTED:
                self._append_log("warn", "请先连接伊机控，再测试摇杆")
                return
            try:
                self._ensure_bridge_backend().stick(side, direction, duration)
            except Exception as exc:
                self.bridge_status = EasyConStatus.FAILED
                self.task_state_text = "连接失败"
                self._append_log("error", f"发送摇杆动作失败: {exc}")
                self._update_bridge_controls()
                return
            self.task_state_text = "已完成"
            self._append_log("info", f"手柄测试: {label} {duration}ms")
            self._update_status_labels()
            return
        self._run_inline_cli_script(f"test_{side}_{direction.lower()}", f"{label}\n")

    def open_key_mapping(self) -> None:
        dialog = KeyMappingDialog(self.key_mapping, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.key_mapping = dialog.get_mapping()
        self._append_log("info", "按键映射已更新")
        # 如果虚拟手柄已启用，先关闭再重新启用以应用新映射
        if self.virtual_controller_enabled:
            self.keyboard_controller_check.setChecked(False)

    def set_keyboard_controller_enabled(self, enabled: bool) -> None:
        if enabled:
            if not self._is_bridge_mode() or self.bridge_status != EasyConStatus.BRIDGE_CONNECTED:
                self.keyboard_controller_check.blockSignals(True)
                self.keyboard_controller_check.setChecked(False)
                self.keyboard_controller_check.blockSignals(False)
                self._append_log("warn", "请先使用常驻 Bridge 连接伊机控，再启用键盘虚拟手柄")
                return
            QApplication.instance().installEventFilter(self)
            self.virtual_controller_enabled = True
            self._append_log("info", "键盘虚拟手柄已启用")
            self.setFocus()
            return
        self._release_virtual_controller_keys()
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self.virtual_controller_enabled = False
        self._append_log("info", "键盘虚拟手柄已关闭")

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if not self.virtual_controller_enabled:
            return super().eventFilter(watched, event)
        if event.type() == QEvent.Type.KeyPress:
            if event.isAutoRepeat():  # type: ignore[attr-defined]
                return True
            return self._handle_virtual_controller_key(event.key(), down=True)  # type: ignore[attr-defined]
        if event.type() == QEvent.Type.KeyRelease:
            if event.isAutoRepeat():  # type: ignore[attr-defined]
                return True
            return self._handle_virtual_controller_key(event.key(), down=False)  # type: ignore[attr-defined]
        if event.type() in (QEvent.Type.ApplicationDeactivate, QEvent.Type.WindowDeactivate):
            self._release_virtual_controller_keys()
        return super().eventFilter(watched, event)

    def _handle_virtual_controller_key(self, key: int, down: bool) -> bool:
        if key == Qt.Key.Key_Escape:
            self.keyboard_controller_check.setChecked(False)
            return True
        if down and key in self.virtual_controller_keys:
            return True
        action = _resolve_vpad_button(key, self.key_mapping)
        if action is None:
            return False
        if not down and key not in self.virtual_controller_keys:
            return True
        kind, value, direction = action
        # 录制：生成 EasyCon 脚本命令
        if self._recording and down:
            self._append_recorded_action(action)
        try:
            if kind == "button":
                if down:
                    self._ensure_bridge_backend().key_down(value)
                    self.virtual_controller_keys[key] = action
                else:
                    self._ensure_bridge_backend().key_up(value)
                    self.virtual_controller_keys.pop(key, None)
                    # 录制按键释放
                    if self._recording:
                        now = datetime.now().timestamp()
                        wait_ms = int((now - self._last_record_ts) * 1000) if self._last_record_ts else 0
                        if wait_ms > 0:
                            self._recorded_lines.append(f"WAIT {wait_ms}")
                        self._recorded_lines.append(f"{value.upper()} UP")
                        self._last_record_ts = now
            elif direction is not None:
                self._ensure_bridge_backend().stick_direction(value, direction, down)
                if down:
                    self.virtual_controller_keys[key] = action
                else:
                    self.virtual_controller_keys.pop(key, None)
        except Exception as exc:
            self.bridge_status = EasyConStatus.FAILED
            self._append_log("error", f"键盘虚拟手柄发送失败: {exc}")
            self.keyboard_controller_check.setChecked(False)
        return True

    def _append_recorded_action(self, action: tuple[str, str, str | None]) -> None:
        kind, value, direction = action
        now = datetime.now().timestamp()
        wait_ms = int((now - self._last_record_ts) * 1000) if self._last_record_ts else 0
        if wait_ms > 0:
            self._recorded_lines.append(f"WAIT {wait_ms}")
        self._last_record_ts = now
        if kind == "button":
            self._recorded_lines.append(f"{value.upper()} DOWN")
        elif kind == "stick":
            # EasyCon 脚本语法: LS UP / RS DOWN / UP (hat)
            if value == "left":
                self._recorded_lines.append(f"LS {direction}")
            elif value == "right":
                self._recorded_lines.append(f"RS {direction}")
            elif value == "hat":
                self._recorded_lines.append(direction)

    def _start_recording(self) -> None:
        if self._recording:
            self._stop_recording()
            return
        if not self._is_bridge_mode():
            self._append_log("warn", "录制脚本仅支持常驻连接（Bridge）模式，请切换连接模式")
            return
        if not self.virtual_controller_enabled:
            self._append_log("warn", "请先启用键盘虚拟手柄（勾选连接）再开始录制")
            return
        self._recording = True
        self._recorded_lines = []
        self._last_record_ts = datetime.now().timestamp()
        self.record_btn.setText("停止录制")
        self.record_btn.setStyleSheet(self.record_btn.styleSheet() + "QPushButton { color: #DC2626; }")
        self.pause_btn.setEnabled(True)
        self._append_log("info", "开始录制脚本，在键盘虚拟手柄上操作即可...")

    def _stop_recording(self) -> None:
        self._recording = False
        self.record_btn.setText("录制脚本")
        self.pause_btn.setEnabled(False)
        if self._recorded_lines:
            script = "\n".join(self._recorded_lines) + "\n"
            editor = self.editor
            cursor = editor.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            editor.setTextCursor(cursor)
            editor.insertPlainText(script)
            self._append_log("info", f"录制完成，已插入 {len(self._recorded_lines)} 行脚本")
            self._recorded_lines = []
        else:
            self._append_log("info", "录制已停止（无操作记录）")

    def _toggle_pause_recording(self) -> None:
        if not self._recording:
            return
        self._recording = False
        self.pause_btn.setText("继续录制")
        self.pause_btn.setStyleSheet(self.pause_btn.styleSheet() + "QPushButton { color: #10A37F; }")
        self._append_log("info", "录制已暂停")
        # 重新点击时恢复
        try:
            self.pause_btn.clicked.disconnect()
        except Exception:
            pass
        self.pause_btn.clicked.connect(self._resume_recording)

    def _resume_recording(self) -> None:
        self._recording = True
        self._last_record_ts = datetime.now().timestamp()
        self.pause_btn.setText("暂停录制")
        self.pause_btn.setEnabled(True)
        self._append_log("info", "录制已恢复")
        try:
            self.pause_btn.clicked.disconnect()
        except Exception:
            pass
        self.pause_btn.clicked.connect(self._toggle_pause_recording)

    def _release_virtual_controller_keys(self) -> None:
        for key, action in list(self.virtual_controller_keys.items()):
            kind, value, direction = action
            try:
                if kind == "button":
                    self._ensure_bridge_backend().key_up(value)
                elif direction is not None:
                    self._ensure_bridge_backend().stick_direction(value, direction, False)
            except Exception as exc:
                self._append_log("error", f"释放键盘虚拟手柄按键失败: {exc}")
            finally:
                self.virtual_controller_keys.pop(key, None)

    def run_cli_smoke_test(self) -> None:
        self._append_log("warn", "测试 CLI 运行会触发一次 CLI 连接，不代表常驻连接验收。")
        self._run_inline_cli_script("cli_smoke", "WAIT 50\n", task_type="cli_smoke")

    def _run_inline_cli_script(self, task_name: str, script_text: str, task_type: str = "controller") -> None:
        if self.process is not None and self.process.state() != QProcess.ProcessState.NotRunning:
            self._append_log("warn", "已有 CLI 任务执行中，暂不能启动手柄测试")
            return
        self.detect_easycon()
        if not self.installation.is_available:
            self._append_log("warn", "CLI 不可用，无法执行手柄测试脚本")
            return
        if not self.mock_check.isChecked() and not self.port_combo.currentText():
            self._append_log("warn", "请先选择串口；CLI 手柄测试会触发一次连接")
            return
        script_path = generate_script_file(script_text, f"{task_name}.ecs", GENERATED_DIR, task_type=task_type)
        port = "mock" if self.mock_check.isChecked() else self.port_combo.currentText()
        self.process = QProcess(self)
        self.process.setProgram(str(self.installation.path))
        self.process.setArguments(["run", str(script_path), "-p", port])
        self.process.readyReadStandardOutput.connect(self._read_stdout)
        self.process.readyReadStandardError.connect(self._read_stderr)
        self.process.finished.connect(self._process_finished)
        self.process.errorOccurred.connect(self._process_error)
        self.stop_requested = False
        self.current_run_stdout = []
        self.current_run_stderr = []
        self.current_run_started_at = datetime.now()
        self.current_run_script_path = script_path
        self.current_run_port = port
        self.run_seconds = 0
        self.elapsed_label.setText("00:00:00")
        self.run_timer.start()
        self.run_button.setText("停止脚本")
        self.task_state_text = "执行中"
        self._append_log("warn", cli_connection_notice())
        self._update_status_labels()
        self.process.start()

    def _ensure_bridge_backend(self) -> BridgeEasyConBackend:
        bridge_path = self._bridge_path_from_ui()
        if self.bridge_backend is None:
            self.bridge_backend = BridgeEasyConBackend(bridge_path=bridge_path, log_callback=self.bridge_log.emit)
        return self.bridge_backend

    def _bridge_path_from_ui(self) -> Path | None:
        bridge_text = self.bridge_path.text().strip()
        return Path(bridge_text) if bridge_text else None

    def _is_bridge_mode(self) -> bool:
        return self.backend_mode.currentData() == "bridge"

    def _backend_mode_changed(self) -> None:
        # 切换到 Bridge 模式时，如果之前 CLI 运行残留了已连接状态，重置之
        if self._is_bridge_mode() and self.bridge_status == EasyConStatus.BRIDGE_CONNECTED:
            if self.bridge_backend is None:
                self.bridge_status = EasyConStatus.BRIDGE_DISCONNECTED
        self._update_bridge_controls()
        self._update_run_enabled()
        self._save_config_from_ui()
        self._update_status_labels()

    def _port_changed(self) -> None:
        self._save_config_from_ui()
        self._update_run_enabled()
        self._update_status_labels()

    def _update_bridge_controls(self) -> None:
        if not hasattr(self, "connect_button"):
            return
        is_bridge = self._is_bridge_mode()
        self.connect_button.setEnabled(is_bridge)
        self.toolbar_connect_button.setEnabled(is_bridge)
        self.bridge_path.setEnabled(is_bridge)
        self.browse_bridge_button.setEnabled(is_bridge)
        self.mock_check.setEnabled(not is_bridge)
        self.auto_select_port_button.setEnabled(bool(self.port_combo.count()))
        if self.bridge_status == EasyConStatus.BRIDGE_CONNECTED:
            self.connect_button.setText("断开连接")
            self.toolbar_connect_button.setText("断开连接")
            self.disconnect_button.setEnabled(True)
            self.backend_label.setText("单片机: 已长期连接")
        elif self.bridge_connecting:
            self.connect_button.setText("正在连接")
            self.toolbar_connect_button.setText("正在连接")
            self.disconnect_button.setEnabled(False)
            self.backend_label.setText("单片机: 正在连接")
        elif self.bridge_status == EasyConStatus.RUNNING:
            self.connect_button.setText("连接伊机控")
            self.toolbar_connect_button.setText("连接伊机控")
            self.disconnect_button.setEnabled(False)
            self.backend_label.setText("单片机: 执行中")
        elif self.bridge_status == EasyConStatus.FAILED:
            self.connect_button.setText("连接伊机控")
            self.toolbar_connect_button.setText("连接伊机控")
            self.disconnect_button.setEnabled(False)
            self.backend_label.setText("单片机: 连接失败")
        elif is_bridge:
            self.connect_button.setText("连接伊机控")
            self.toolbar_connect_button.setText("连接伊机控")
            self.disconnect_button.setEnabled(False)
            status_text = "未选择串口" if not self.port_combo.currentText() else "未连接"
            self.backend_label.setText(f"单片机: {status_text}")
        else:
            self.connect_button.setText("连接伊机控")
            self.toolbar_connect_button.setText("连接伊机控")
            self.disconnect_button.setEnabled(False)
            self.backend_label.setText("单片机: CLI 过渡后端可用（未长期连接）")
        self._update_controller_controls()
        self._update_status_labels()

    def _update_controller_controls(self) -> None:
        enabled = self.bridge_status != EasyConStatus.RUNNING and (
            (self._is_bridge_mode() and self.bridge_status == EasyConStatus.BRIDGE_CONNECTED)
            or (not self._is_bridge_mode() and self.installation.is_available)
        )
        for button in (
            self.test_a_button,
            self.test_b_button,
            self.test_home_button,
            self.test_ls_reset_button,
            self.test_rs_reset_button,
        ):
            button.setEnabled(enabled)
        self.keyboard_controller_check.setEnabled(self._is_bridge_mode() and self.bridge_status == EasyConStatus.BRIDGE_CONNECTED)
        self.cli_test_button.setEnabled(
            not self._is_bridge_mode()
            and self.installation.is_available
            and self.bridge_status != EasyConStatus.RUNNING
            and not (self.process is not None and self.process.state() != QProcess.ProcessState.NotRunning)
        )

    def _update_status_labels(self) -> None:
        if not hasattr(self, "connection_state_label"):
            return
        connection_text = self._connection_state_text()
        backend_text = "常驻连接" if self._is_bridge_mode() else "CLI 过渡"
        easycon_text = "LOG"
        self.connection_state_label.setText(f"连接: {connection_text}")
        self.task_state_label.setText(f"任务: {self.task_state_text}")
        self.status_easycon_label.setText(easycon_text)
        self.status_controller_label.setText(f"单片机{'已连接' if connection_text == '已长期连接' else '未连接'}")
        self.status_backend_label.setText(f"后端: {backend_text}")

    def _connection_state_text(self) -> str:
        if self._is_bridge_mode():
            if self.bridge_connecting:
                return "正在连接"
            if self.bridge_status == EasyConStatus.RUNNING:
                return "执行中"
            if self.bridge_status == EasyConStatus.BRIDGE_CONNECTED:
                return "已长期连接"
            if self.bridge_status == EasyConStatus.FAILED:
                return "连接失败"
            if not self._bridge_path_from_ui() and not self.installation.is_available:
                return "未检测"
            if not self.port_combo.currentText():
                return "未选择串口"
            return "未连接"
        if self.process is not None and self.process.state() != QProcess.ProcessState.NotRunning:
            return "执行中"
        if not self.installation.is_available:
            return "未检测"
        if not self.mock_check.isChecked() and not self.port_combo.currentText():
            return "未选择串口"
        return "CLI 可用（未长期连接）"


def _first_supported_drop(mime_data) -> Path | None:  # type: ignore[no-untyped-def]
    for url in mime_data.urls():
        path = Path(url.toLocalFile())
        if path.suffix.lower() in {".txt", ".ecs"}:
            return path
    return None





def _easycon_unavailable_message(installation: EasyConInstallation, requested_path: str = "") -> str:
    error = installation.error or ""
    if requested_path and "does not exist" not in error:
        return "ezcon 路径可能无效或文件损坏，请重新选择 ezcon.exe。"
    return "请选择 ezcon.exe 或设置 EASYCON_ROOT。"
