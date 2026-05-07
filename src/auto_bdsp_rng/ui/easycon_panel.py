from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QRect, QSize, QProcess, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QColor, QPainter, QTextCursor, QTextFormat
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
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
    apply_parameter_values,
    classify_cli_failure,
    cli_connection_notice,
    detect_newline_style,
    discover_ezcon,
    extract_compile_error_line,
    generate_script_file,
    list_ports,
    load_config,
    parse_script_parameters,
    prune_generated_scripts,
    save_config,
    scan_builtin_scripts,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = PROJECT_ROOT / "script"
GENERATED_DIR = SCRIPT_DIR / ".generated"

KEYBOARD_VPAD_BUTTONS = {
    Qt.Key.Key_L: "A",
    Qt.Key.Key_K: "B",
    Qt.Key.Key_I: "X",
    Qt.Key.Key_J: "Y",
    Qt.Key.Key_G: "L",
    Qt.Key.Key_T: "R",
    Qt.Key.Key_F: "ZL",
    Qt.Key.Key_R: "ZR",
    Qt.Key.Key_Plus: "PLUS",
    Qt.Key.Key_Equal: "PLUS",
    Qt.Key.Key_Minus: "MINUS",
    Qt.Key.Key_Z: "CAPTURE",
    Qt.Key.Key_C: "HOME",
    Qt.Key.Key_Q: "LCLICK",
    Qt.Key.Key_E: "RCLICK",
}
KEYBOARD_VPAD_DIRECTIONS = {
    Qt.Key.Key_W: ("left", "Up"),
    Qt.Key.Key_S: ("left", "Down"),
    Qt.Key.Key_A: ("left", "Left"),
    Qt.Key.Key_D: ("left", "Right"),
    Qt.Key.Key_Up: ("right", "Up"),
    Qt.Key.Key_Down: ("right", "Down"),
    Qt.Key.Key_Left: ("right", "Left"),
    Qt.Key.Key_Right: ("right", "Right"),
}


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


class EasyConPanel(QWidget):
    bridge_log = Signal(str, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = load_config()
        self.installation = EasyConInstallation(path=None, error="未检测")
        self.bridge_backend: BridgeEasyConBackend | None = None
        self.bridge_run_thread: QThread | None = None
        self.bridge_run_worker: BridgeScriptWorker | None = None
        self.bridge_status = EasyConStatus.BRIDGE_DISCONNECTED
        self.bridge_connecting = False
        self.current_script_path: Path | None = None
        self.current_script_name = "未命名脚本"
        self.current_script_is_template = False
        self.current_script_newline = "\n"
        self.template_script_text = ""
        self.parameter_widgets: dict[str, QLineEdit | QSpinBox] = {}
        self.parameter_defaults: dict[str, str] = {}
        self.parameter_lines: dict[str, int] = {}
        self.virtual_controller_enabled = False
        self.virtual_controller_keys: dict[int, tuple[str, str, str | None]] = {}
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
        self._build_actions()
        self._refresh_script_list()
        self.detect_easycon()
        self.refresh_ports()
        self._update_run_enabled()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        toolbar = QFrame()
        toolbar.setObjectName("EasyConToolbar")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(10, 8, 10, 8)
        toolbar_layout.setSpacing(8)
        self.script_name_label = QLabel("未命名脚本")
        self.script_name_label.setObjectName("Badge")
        self.template_mode_label = QLabel("普通脚本")
        self.template_mode_label.setObjectName("Badge")
        self.open_button = QPushButton("打开脚本")
        self.open_button.clicked.connect(self.open_script_dialog)
        self.save_generated_button = QPushButton("保存临时脚本")
        self.save_generated_button.clicked.connect(self.save_generated_script)
        self.save_original_button = QPushButton("保存到原文件")
        self.save_original_button.clicked.connect(self.save_to_original_script)
        self.detect_button = QPushButton("检测 EasyCon")
        self.detect_button.clicked.connect(self.detect_easycon)
        self.toolbar_connect_button = QPushButton("连接伊机控")
        self.toolbar_connect_button.clicked.connect(self.toggle_bridge_connection)
        self.run_button = QPushButton("运行脚本")
        self.run_button.setObjectName("PrimaryButton")
        self.run_button.clicked.connect(self.toggle_run)
        self.elapsed_label = QLabel("00:00:00")
        self.elapsed_label.setObjectName("Badge")
        toolbar_layout.addWidget(QLabel("伊机控"))
        toolbar_layout.addWidget(self.script_name_label, 1)
        toolbar_layout.addWidget(self.template_mode_label)
        toolbar_layout.addWidget(self.open_button)
        toolbar_layout.addWidget(self.save_generated_button)
        toolbar_layout.addWidget(self.save_original_button)
        toolbar_layout.addWidget(self.detect_button)
        toolbar_layout.addWidget(self.toolbar_connect_button)
        toolbar_layout.addWidget(self.elapsed_label)
        toolbar_layout.addWidget(self.run_button)
        layout.addWidget(toolbar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_editor_panel())
        splitter.setSizes([430, 1000])
        layout.addWidget(splitter, 1)

        self.easycon_status = QStatusBar()
        self.easycon_status.setSizeGripEnabled(False)
        self.status_easycon_label = QLabel("EasyCon: 未检测")
        self.status_controller_label = QLabel("单片机: 未检测")
        self.status_capture_label = QLabel("采集卡: 不使用")
        self.status_labels_label = QLabel("标签: 0")
        self.status_backend_label = QLabel("后端: 常驻连接")
        for label in (
            self.status_easycon_label,
            self.status_controller_label,
            self.status_capture_label,
            self.status_labels_label,
            self.status_backend_label,
        ):
            label.setObjectName("Badge")
            self.easycon_status.addPermanentWidget(label)
        layout.addWidget(self.easycon_status)

    def _build_actions(self) -> None:
        save = QAction("Save generated script", self)
        save.setShortcut("Ctrl+S")
        save.triggered.connect(self.save_generated_script)
        self.addAction(save)

        open_action = QAction("Open EasyCon script", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.open_script_dialog)
        self.addAction(open_action)

        run = QAction("Run EasyCon script", self)
        run.setShortcut("F5")
        run.triggered.connect(self.toggle_run)
        self.addAction(run)

    def _build_left_panel(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(8)

        script_group = QGroupBox("内置脚本")
        script_layout = QVBoxLayout(script_group)
        self.script_list = QListWidget()
        self.script_list.itemDoubleClicked.connect(self._load_script_item)
        script_layout.addWidget(self.script_list)
        layout.addWidget(script_group, 1)

        config_group = QGroupBox("连接配置")
        config_layout = QGridLayout(config_group)
        self.ezcon_path = QLineEdit(str(self.config.ezcon_path or ""))
        self.ezcon_path.setPlaceholderText("请选择 ezcon.exe 或设置 EASYCON_ROOT")
        self.browse_ezcon_button = QPushButton("选择")
        self.browse_ezcon_button.clicked.connect(self.choose_ezcon)
        self.bridge_path = QLineEdit(str(self.config.bridge_path or ""))
        self.bridge_path.setPlaceholderText("请选择 EasyConBridge.exe")
        self.browse_bridge_button = QPushButton("选择")
        self.browse_bridge_button.clicked.connect(self.choose_bridge)
        self.version_label = QLabel("EasyCon: 未检测")
        self.backend_mode = QComboBox()
        self.backend_mode.addItem("常驻连接（Bridge）", "bridge")
        self.backend_mode.addItem("CLI 诊断", "cli")
        self.backend_mode.currentIndexChanged.connect(self._backend_mode_changed)
        self.backend_label = QLabel("单片机: 未连接")
        self.connection_state_label = QLabel("连接: 未检测")
        self.connection_state_label.setObjectName("Badge")
        self.task_state_label = QLabel("任务: 未检测")
        self.task_state_label.setObjectName("Badge")
        self.port_combo = QComboBox()
        self.port_combo.currentIndexChanged.connect(self._port_changed)
        self.refresh_ports_button = QPushButton("刷新串口")
        self.refresh_ports_button.clicked.connect(self.refresh_ports)
        self.auto_select_port_button = QPushButton("自动选择串口")
        self.auto_select_port_button.clicked.connect(self.auto_select_port)
        self.connect_button = QPushButton("连接伊机控")
        self.connect_button.clicked.connect(self.toggle_bridge_connection)
        self.disconnect_button = QPushButton("断开连接")
        self.disconnect_button.clicked.connect(self.disconnect_bridge)
        self.cli_test_button = QPushButton("测试 CLI 运行")
        self.cli_test_button.clicked.connect(self.run_cli_smoke_test)
        self.mock_check = QCheckBox("mock 模式")
        self.mock_check.setChecked(self.config.mock_enabled)
        self.mock_check.toggled.connect(self._save_config_from_ui)
        config_layout.addWidget(QLabel("ezcon"), 0, 0)
        config_layout.addWidget(self.ezcon_path, 0, 1)
        config_layout.addWidget(self.browse_ezcon_button, 0, 2)
        config_layout.addWidget(QLabel("Bridge"), 1, 0)
        config_layout.addWidget(self.bridge_path, 1, 1)
        config_layout.addWidget(self.browse_bridge_button, 1, 2)
        config_layout.addWidget(self.version_label, 2, 0, 1, 3)
        config_layout.addWidget(QLabel("后端"), 3, 0)
        config_layout.addWidget(self.backend_mode, 3, 1, 1, 2)
        config_layout.addWidget(self.backend_label, 4, 0, 1, 3)
        config_layout.addWidget(QLabel("状态"), 5, 0)
        config_layout.addWidget(self.connection_state_label, 5, 1)
        config_layout.addWidget(self.task_state_label, 5, 2)
        config_layout.addWidget(QLabel("串口"), 6, 0)
        config_layout.addWidget(self.port_combo, 6, 1)
        config_layout.addWidget(self.refresh_ports_button, 6, 2)
        config_layout.addWidget(self.auto_select_port_button, 7, 1, 1, 2)
        config_layout.addWidget(self.connect_button, 8, 1)
        config_layout.addWidget(self.disconnect_button, 8, 2)
        config_layout.addWidget(self.mock_check, 9, 1, 1, 2)
        config_layout.addWidget(self.cli_test_button, 10, 1, 1, 2)
        layout.addWidget(config_group)

        controller_group = QGroupBox("手柄测试")
        controller_layout = QGridLayout(controller_group)
        self.controller_duration = QSpinBox()
        self.controller_duration.setRange(20, 5000)
        self.controller_duration.setSingleStep(20)
        self.controller_duration.setValue(100)
        self.controller_duration.setSuffix(" ms")
        self.test_a_button = QPushButton("A")
        self.test_b_button = QPushButton("B")
        self.test_home_button = QPushButton("HOME")
        self.test_ls_reset_button = QPushButton("LS RESET")
        self.test_rs_reset_button = QPushButton("RS RESET")
        self.test_a_button.clicked.connect(lambda: self.send_controller_press("A"))
        self.test_b_button.clicked.connect(lambda: self.send_controller_press("B"))
        self.test_home_button.clicked.connect(lambda: self.send_controller_press("HOME"))
        self.test_ls_reset_button.clicked.connect(lambda: self.send_controller_stick("left", "RESET"))
        self.test_rs_reset_button.clicked.connect(lambda: self.send_controller_stick("right", "RESET"))
        controller_layout.addWidget(QLabel("时长"), 0, 0)
        controller_layout.addWidget(self.controller_duration, 0, 1, 1, 2)
        controller_layout.addWidget(self.test_a_button, 1, 0)
        controller_layout.addWidget(self.test_b_button, 1, 1)
        controller_layout.addWidget(self.test_home_button, 1, 2)
        controller_layout.addWidget(self.test_ls_reset_button, 2, 0, 1, 2)
        controller_layout.addWidget(self.test_rs_reset_button, 2, 2)
        self.keyboard_controller_check = QCheckBox("键盘虚拟手柄")
        self.keyboard_controller_check.toggled.connect(self.set_keyboard_controller_enabled)
        self.keyboard_mapping_label = QLabel("L/K/I/J=A/B/X/Y，WASD=左摇杆，方向键=右摇杆，Esc=退出")
        self.keyboard_mapping_label.setObjectName("Hint")
        controller_layout.addWidget(self.keyboard_controller_check, 3, 0, 1, 3)
        controller_layout.addWidget(self.keyboard_mapping_label, 4, 0, 1, 3)
        layout.addWidget(controller_group)

        param_group = QGroupBox("脚本参数")
        param_layout = QVBoxLayout(param_group)
        self.parameter_scroll = QScrollArea()
        self.parameter_scroll.setWidgetResizable(True)
        self.parameter_panel = QWidget()
        self.parameter_form = QFormLayout(self.parameter_panel)
        self.parameter_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.parameter_scroll.setWidget(self.parameter_panel)
        self.rescan_button = QPushButton("重新扫描参数")
        self.rescan_button.clicked.connect(self._rescan_parameters)
        self.restore_defaults_button = QPushButton("恢复模板默认值")
        self.restore_defaults_button.clicked.connect(self.restore_template_defaults)
        param_buttons = QHBoxLayout()
        param_buttons.addWidget(self.rescan_button)
        param_buttons.addWidget(self.restore_defaults_button)
        param_layout.addWidget(self.parameter_scroll)
        param_layout.addLayout(param_buttons)
        layout.addWidget(param_group, 1)

        log_group = QGroupBox("输出日志")
        log_layout = QVBoxLayout(log_group)
        self.log_view = QTextEdit()
        self.log_view.setObjectName("EasyConLog")
        self.log_view.setReadOnly(True)
        self.log_view.document().setMaximumBlockCount(self.config.keep_log_lines)
        self.log_keep_lines = QSpinBox()
        self.log_keep_lines.setRange(1, 100_000)
        self.log_keep_lines.setValue(self.config.keep_log_lines)
        self.log_keep_lines.setSuffix(" 行")
        self.log_keep_lines.setToolTip("日志区最多保留的行数")
        self.log_keep_lines.valueChanged.connect(self._log_retention_changed)
        self.clear_log_button = QPushButton("清空日志")
        self.clear_log_button.clicked.connect(self.log_view.clear)
        self.copy_log_button = QPushButton("复制日志")
        self.copy_log_button.clicked.connect(self.copy_all_logs)
        self.save_log_button = QPushButton("保存日志")
        self.save_log_button.clicked.connect(self.save_logs_dialog)
        log_buttons = QHBoxLayout()
        log_buttons.addWidget(QLabel("保留"))
        log_buttons.addWidget(self.log_keep_lines)
        log_buttons.addWidget(self.clear_log_button)
        log_buttons.addWidget(self.copy_log_button)
        log_buttons.addWidget(self.save_log_button)
        log_layout.addWidget(self.log_view, 1)
        log_layout.addLayout(log_buttons)
        layout.addWidget(log_group, 1)
        scroll.setWidget(panel)
        return scroll

    def _build_editor_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        self.editor = EasyConScriptEditor()
        self.editor.fileDropped.connect(self.load_script)
        self.editor.textChanged.connect(self._update_run_enabled)
        layout.addWidget(self.editor, 1)
        return panel

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
            self.version_label.setText(f"EasyCon: {self.installation.version} ({self.installation.source})")
            self._append_log("info", f"已检测到 EasyCon: {self.installation.version}")
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
        self._append_log("info", f"已自动选择串口: {selected}")
        self._save_config_from_ui()
        self._update_run_enabled()
        self._update_status_labels()

    def _select_preferred_port(self, ports: list[str]) -> str | None:
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
        self.current_script_path = path
        self.current_script_name = path.name
        self.current_script_is_template = _is_builtin_template(path)
        self.current_script_newline = detect_newline_style(text)
        self.template_script_text = text
        self.script_name_label.setText(path.name)
        self._update_template_mode_label()
        self.editor.setPlainText(text)
        self._rescan_parameters()
        self._restore_recent_parameters()
        mode = "模板副本" if self.current_script_is_template else "外部脚本"
        self._append_log("info", f"已加载{mode}: {path.name}")
        self._remember_recent_script(path)

    def _load_script_item(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(path, Path):
            self.load_script(path)

    def _rescan_parameters(self) -> None:
        while self.parameter_form.rowCount():
            self.parameter_form.removeRow(0)
        self.parameter_widgets.clear()
        self.parameter_defaults.clear()
        self.parameter_lines.clear()
        for parameter in parse_script_parameters(self.editor.toPlainText()):
            if parameter.is_integer:
                widget = QSpinBox()
                widget.setRange(-1_000_000_000, 1_000_000_000)
                if parameter.value.strip().lstrip("+-").isdigit():
                    widget.setValue(int(parameter.value))
                widget.valueChanged.connect(self._sync_parameters_to_editor)
            else:
                widget = QLineEdit("" if parameter.required else parameter.value)
                widget.textChanged.connect(self._sync_parameters_to_editor)
                if parameter.required:
                    widget.setPlaceholderText("填入这里（必填）")
            if parameter.comment:
                widget.setToolTip(parameter.comment)
            self.parameter_widgets[parameter.name] = widget
            self.parameter_defaults[parameter.name] = parameter.default
            self.parameter_lines[parameter.name] = parameter.line_index + 1
            label = f"{parameter.name}{' *' if parameter.required else ''}"
            field = QWidget()
            field_layout = QHBoxLayout(field)
            field_layout.setContentsMargins(0, 0, 0, 0)
            field_layout.addWidget(widget, 1)
            default_label = QLabel(f"默认: {parameter.default}")
            default_label.setObjectName("Hint")
            if parameter.comment:
                default_label.setToolTip(parameter.comment)
            field_layout.addWidget(default_label)
            self.parameter_form.addRow(label, field)
        if not self.parameter_widgets:
            self.parameter_form.addRow("参数", QLabel("未发现脚本参数"))
        self.restore_defaults_button.setEnabled(bool(self.parameter_widgets))
        self._update_run_enabled()

    def _sync_parameters_to_editor(self) -> None:
        if not self.parameter_widgets:
            return
        values = {
            name: widget.value() if isinstance(widget, QSpinBox) else widget.text()
            for name, widget in self.parameter_widgets.items()
        }
        cursor = self.editor.textCursor()
        self.editor.blockSignals(True)
        self.editor.setPlainText(apply_parameter_values(self.editor.toPlainText(), values))
        self.editor.setTextCursor(cursor)
        self.editor.blockSignals(False)
        self._save_current_parameters()
        self._update_run_enabled()

    def save_generated_script(self) -> Path | None:
        if not self.editor.toPlainText().strip():
            self._append_log("warn", "没有可保存的脚本内容")
            return None
        self._sync_parameters_to_editor()
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

    def save_to_original_script(self) -> Path | None:
        if self.current_script_path is None:
            self._append_log("warn", "当前脚本没有原文件路径")
            return None
        self._sync_parameters_to_editor()
        try:
            self.current_script_path.write_text(
                self.editor.toPlainText(),
                encoding="utf-8",
                newline=self.current_script_newline,
            )
        except OSError as exc:
            self._append_log("error", f"保存到原文件失败: {exc}")
            return None
        self.template_script_text = self.editor.toPlainText()
        self.current_script_is_template = _is_builtin_template(self.current_script_path)
        self._update_template_mode_label()
        self._append_log("info", f"已保存到原文件: {self.current_script_path.name}")
        return self.current_script_path

    def restore_template_defaults(self) -> None:
        source_text = self.template_script_text or self.editor.toPlainText()
        defaults = {parameter.name: parameter.default for parameter in parse_script_parameters(source_text)}
        if not defaults:
            self._append_log("warn", "当前脚本没有可恢复的模板默认值")
            return
        cursor = self.editor.textCursor()
        self.editor.blockSignals(True)
        self.editor.setPlainText(apply_parameter_values(self.editor.toPlainText(), defaults))
        self.editor.setTextCursor(cursor)
        self.editor.blockSignals(False)
        self._rescan_parameters()
        self._save_current_parameters()
        self._append_log("info", "已恢复模板默认值")

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
        if not self._validate_parameters_for_run(focus=True):
            return
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
        if not self._validate_parameters_for_run(focus=True):
            return
        if not self._can_run():
            self._append_log("warn", "请先连接伊机控，再运行脚本")
            return
        self._sync_parameters_to_editor()
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

    def _read_stdout(self) -> None:
        if self.process is None:
            return
        text = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        if text:
            self.current_run_stdout.append(text)
            self._append_log("stdout", text.rstrip())

    def _read_stderr(self) -> None:
        if self.process is None:
            return
        text = bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace")
        if text:
            self.current_run_stderr.append(text)
            self._append_log("stderr", text.rstrip())

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
        if not self._is_bridge_mode() and not self.mock_check.isChecked() and not self.port_combo.currentText():
            return False
        if self._first_invalid_required_parameter() is not None:
            return False
        if self.process is not None and self.process.state() != QProcess.ProcessState.NotRunning:
            return False
        return True

    def _first_invalid_required_parameter(self) -> str | None:
        for name, widget in self.parameter_widgets.items():
            if (
                isinstance(widget, QLineEdit)
                and widget.placeholderText().startswith("填入这里")
                and not widget.text().strip()
            ):
                return name
        return None

    def _validate_parameters_for_run(self, focus: bool = False) -> bool:
        invalid = self._first_invalid_required_parameter()
        if invalid is None:
            return True
        line = self.parameter_lines.get(invalid)
        if line is not None:
            self.editor.go_to_line(line)
            self._append_log("error", f"参数 {invalid} 未填写，已定位到第 {line} 行")
        else:
            self._append_log("error", f"参数 {invalid} 未填写")
        widget = self.parameter_widgets.get(invalid)
        if focus and widget is not None:
            widget.setFocus()
        return False

    def _update_run_enabled(self) -> None:
        if hasattr(self, "run_button"):
            self.run_button.setEnabled(self._can_run())
        if hasattr(self, "save_original_button"):
            self.save_original_button.setEnabled(self.current_script_path is not None)
        if hasattr(self, "connect_button"):
            self._update_bridge_controls()
        if hasattr(self, "connection_state_label"):
            self._update_status_labels()

    def _update_template_mode_label(self) -> None:
        if not hasattr(self, "template_mode_label"):
            return
        if self.current_script_is_template:
            self.template_mode_label.setText("模板副本")
            self.template_mode_label.setToolTip("来自 script 目录，运行和 Ctrl+S 只保存临时副本，不覆盖原模板。")
            self.save_original_button.setEnabled(True)
            return
        self.template_mode_label.setText("普通脚本")
        self.template_mode_label.setToolTip("")
        self.save_original_button.setEnabled(self.current_script_path is not None)

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

    def _restore_recent_parameters(self) -> None:
        key = _script_parameter_config_key(self.current_script_path)
        if key is None:
            return
        values = self.config.script_parameters.get(key)
        if not values:
            return
        restored = False
        for name, value in values.items():
            widget = self.parameter_widgets.get(name)
            if widget is None:
                continue
            if isinstance(widget, QSpinBox):
                try:
                    widget.setValue(int(value))
                except ValueError:
                    continue
            else:
                widget.setText(value)
            restored = True
        if restored:
            self._append_log("info", "已恢复该脚本上次使用的参数")

    def _save_current_parameters(self) -> None:
        key = _script_parameter_config_key(self.current_script_path)
        if key is None or not self.parameter_widgets:
            return
        values = {
            name: str(widget.value() if isinstance(widget, QSpinBox) else widget.text())
            for name, widget in self.parameter_widgets.items()
        }
        script_parameters = {script: dict(parameters) for script, parameters in self.config.script_parameters.items()}
        if script_parameters.get(key) == values:
            return
        script_parameters[key] = values
        self.config = replace(self.config, script_parameters=script_parameters)
        save_config(self.config)

    def _append_log(self, level: str, message: str) -> None:
        color = {
            "info": "#91E0C3",
            "warn": "#E6D79B",
            "error": "#FF8A8A",
            "stdout": "#E7ECE9",
            "stderr": "#FFB1A8",
        }.get(level, "#E7ECE9")
        for line in message.splitlines() or [""]:
            self.log_view.append(f'<span style="color:{color}">[{level}] {line}</span>')
        self.log_view.moveCursor(QTextCursor.MoveOperation.End)

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
        action = _keyboard_virtual_controller_action(key)
        if action is None:
            return False
        if not down and key not in self.virtual_controller_keys:
            return True
        kind, value, direction = action
        try:
            if kind == "button":
                if down:
                    self._ensure_bridge_backend().key_down(value)
                    self.virtual_controller_keys[key] = action
                else:
                    self._ensure_bridge_backend().key_up(value)
                    self.virtual_controller_keys.pop(key, None)
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
        return not hasattr(self, "backend_mode") or self.backend_mode.currentData() == "bridge"

    def _backend_mode_changed(self) -> None:
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
        easycon_text = (
            f"EasyCon: {self.installation.version}"
            if self.installation.is_available and self.installation.version
            else "EasyCon: 未检测"
        )
        self.connection_state_label.setText(f"连接: {connection_text}")
        self.task_state_label.setText(f"任务: {self.task_state_text}")
        self.status_easycon_label.setText(easycon_text)
        self.status_controller_label.setText(f"单片机: {connection_text}")
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


def _is_builtin_template(path: Path) -> bool:
    try:
        resolved = path.resolve()
        script_root = SCRIPT_DIR.resolve()
        generated_root = GENERATED_DIR.resolve()
    except OSError:
        return False
    return resolved.is_relative_to(script_root) and not resolved.is_relative_to(generated_root)


def _script_parameter_config_key(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        resolved = path.resolve()
        script_root = SCRIPT_DIR.resolve()
        if resolved.is_relative_to(script_root):
            return f"script/{resolved.relative_to(script_root).as_posix()}"
        return str(resolved)
    except OSError:
        return str(path)


def _easycon_unavailable_message(installation: EasyConInstallation, requested_path: str = "") -> str:
    error = installation.error or ""
    if requested_path and "does not exist" not in error:
        return "ezcon 路径可能无效或文件损坏，请重新选择 ezcon.exe。"
    return "请选择 ezcon.exe 或设置 EASYCON_ROOT。"


def _keyboard_virtual_controller_action(key: int) -> tuple[str, str, str | None] | None:
    button = KEYBOARD_VPAD_BUTTONS.get(Qt.Key(key))
    if button is not None:
        return ("button", button, None)
    direction = KEYBOARD_VPAD_DIRECTIONS.get(Qt.Key(key))
    if direction is not None:
        side, value = direction
        return ("stick", side, value)
    return None
