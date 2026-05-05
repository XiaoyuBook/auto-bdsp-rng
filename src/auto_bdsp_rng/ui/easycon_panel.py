from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QProcess, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QTextCursor
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
    discover_ezcon,
    generate_script_file,
    list_ports,
    load_config,
    parse_script_parameters,
    save_config,
    scan_builtin_scripts,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = PROJECT_ROOT / "script"
GENERATED_DIR = SCRIPT_DIR / ".generated"


class EasyConScriptEditor(QPlainTextEdit):
    fileDropped = Signal(Path)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setPlaceholderText("选择或拖入 .txt / .ecs 脚本")

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


class EasyConPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = load_config()
        self.installation = EasyConInstallation(path=None, error="未检测")
        self.bridge_backend: BridgeEasyConBackend | None = None
        self.bridge_status = EasyConStatus.BRIDGE_DISCONNECTED
        self.current_script_path: Path | None = None
        self.current_script_name = "未命名脚本"
        self.parameter_widgets: dict[str, QLineEdit | QSpinBox] = {}
        self.process: QProcess | None = None
        self.current_run_started_at: datetime | None = None
        self.current_run_script_path: Path | None = None
        self.current_run_port: str | None = None
        self.stop_requested = False
        self.run_seconds = 0
        self.run_timer = QTimer(self)
        self.run_timer.setInterval(1000)
        self.run_timer.timeout.connect(self._tick_run_timer)

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
        self.open_button = QPushButton("打开脚本")
        self.open_button.clicked.connect(self.open_script_dialog)
        self.save_generated_button = QPushButton("保存临时脚本")
        self.save_generated_button.clicked.connect(self.save_generated_script)
        self.detect_button = QPushButton("检测 EasyCon")
        self.detect_button.clicked.connect(self.detect_easycon)
        self.connect_button = QPushButton("连接伊机控")
        self.connect_button.clicked.connect(self.toggle_bridge_connection)
        self.run_button = QPushButton("运行脚本")
        self.run_button.setObjectName("PrimaryButton")
        self.run_button.clicked.connect(self.toggle_run)
        self.elapsed_label = QLabel("00:00:00")
        self.elapsed_label.setObjectName("Badge")
        toolbar_layout.addWidget(QLabel("伊机控"))
        toolbar_layout.addWidget(self.script_name_label, 1)
        toolbar_layout.addWidget(self.open_button)
        toolbar_layout.addWidget(self.save_generated_button)
        toolbar_layout.addWidget(self.detect_button)
        toolbar_layout.addWidget(self.connect_button)
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
        self.port_combo = QComboBox()
        self.refresh_ports_button = QPushButton("刷新串口")
        self.refresh_ports_button.clicked.connect(self.refresh_ports)
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
        config_layout.addWidget(QLabel("串口"), 5, 0)
        config_layout.addWidget(self.port_combo, 5, 1)
        config_layout.addWidget(self.refresh_ports_button, 5, 2)
        config_layout.addWidget(self.mock_check, 6, 1, 1, 2)
        layout.addWidget(config_group)

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
        param_layout.addWidget(self.parameter_scroll)
        param_layout.addWidget(self.rescan_button)
        layout.addWidget(param_group, 1)

        log_group = QGroupBox("输出日志")
        log_layout = QVBoxLayout(log_group)
        self.log_view = QTextEdit()
        self.log_view.setObjectName("EasyConLog")
        self.log_view.setReadOnly(True)
        self.clear_log_button = QPushButton("清空日志")
        self.clear_log_button.clicked.connect(self.log_view.clear)
        self.copy_log_button = QPushButton("复制日志")
        self.copy_log_button.clicked.connect(self.copy_all_logs)
        self.save_log_button = QPushButton("保存日志")
        self.save_log_button.clicked.connect(self.save_logs_dialog)
        log_buttons = QHBoxLayout()
        log_buttons.addWidget(self.clear_log_button)
        log_buttons.addWidget(self.copy_log_button)
        log_buttons.addWidget(self.save_log_button)
        log_layout.addWidget(self.log_view, 1)
        log_layout.addLayout(log_buttons)
        layout.addWidget(log_group, 1)
        return panel

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
            self._append_log("warn", self.installation.error or "未找到 ezcon.exe")
        self._save_config_from_ui()
        self._update_run_enabled()

    def refresh_ports(self) -> None:
        current = self.config.last_port
        ports = list_ports(self.installation) if self.installation.is_available else []
        self.port_combo.blockSignals(True)
        self.port_combo.clear()
        self.port_combo.addItems(ports)
        if current and current in ports:
            self.port_combo.setCurrentText(current)
        elif len(ports) == 1:
            self.port_combo.setCurrentIndex(0)
        self.port_combo.blockSignals(False)
        self._append_log("info", f"已刷新串口: {', '.join(ports) if ports else '未发现'}")
        self._save_config_from_ui()
        self._update_run_enabled()

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
        self.script_name_label.setText(path.name)
        self.editor.setPlainText(text)
        self._rescan_parameters()
        self._append_log("info", f"已加载脚本: {path.name}")

    def _load_script_item(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(path, Path):
            self.load_script(path)

    def _rescan_parameters(self) -> None:
        while self.parameter_form.rowCount():
            self.parameter_form.removeRow(0)
        self.parameter_widgets.clear()
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
            label = f"{parameter.name}{' *' if parameter.required else ''}"
            self.parameter_form.addRow(label, widget)
        if not self.parameter_widgets:
            self.parameter_form.addRow("参数", QLabel("未发现脚本参数"))
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
        self._update_run_enabled()

    def save_generated_script(self) -> Path | None:
        if not self.editor.toPlainText().strip():
            self._append_log("warn", "没有可保存的脚本内容")
            return None
        self._sync_parameters_to_editor()
        try:
            path = generate_script_file(self.editor.toPlainText(), self.current_script_name, GENERATED_DIR)
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
        self.current_run_started_at = datetime.now()
        self.current_run_script_path = script_path
        self.current_run_port = port
        self.run_seconds = 0
        self.elapsed_label.setText("00:00:00")
        self.run_timer.start()
        self.run_button.setText("停止脚本")
        self._append_log("info", f"开始运行脚本，端口: {port}")
        self.process.start()

    def run_script_via_bridge(self) -> None:
        if not self._can_run():
            self._append_log("warn", "请先连接伊机控，再运行脚本")
            return
        self._sync_parameters_to_editor()
        script_text = self.editor.toPlainText()
        started_at = datetime.now()
        self.run_seconds = 0
        self.elapsed_label.setText("00:00:00")
        self.run_timer.start()
        self.run_button.setText("执行中")
        self.run_button.setEnabled(False)
        self._append_log("info", "通过常驻连接运行脚本")
        try:
            result = self._ensure_bridge_backend().run_script_text(script_text, self.current_script_name)
        except Exception as exc:
            self._append_log("error", f"Bridge 运行失败: {exc}")
            self._finish_run(
                "失败",
                exit_code=1,
                started_at=started_at,
                script_path=Path(self.current_script_name),
                port=self.port_combo.currentText(),
            )
            self.bridge_status = EasyConStatus.FAILED
            self._update_bridge_controls()
            return
        if result.stdout:
            self._append_log("stdout", result.stdout.rstrip())
        if result.stderr:
            self._append_log("stderr", result.stderr.rstrip())
        status = "已完成，连接保持" if result.status == EasyConStatus.COMPLETED else "失败"
        self.bridge_status = self._ensure_bridge_backend().status()
        self._finish_run(
            status,
            exit_code=result.exit_code,
            started_at=result.started_at,
            ended_at=result.ended_at,
            script_path=result.script_path,
            port=result.port,
        )
        self._update_bridge_controls()

    def _read_stdout(self) -> None:
        if self.process is None:
            return
        text = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        if text:
            self._append_log("stdout", text.rstrip())

    def _read_stderr(self) -> None:
        if self.process is None:
            return
        text = bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace")
        if text:
            self._append_log("stderr", text.rstrip())

    def _process_finished(self, exit_code: int, _status) -> None:  # type: ignore[no-untyped-def]
        if self.stop_requested:
            self.stop_requested = False
            self._finish_run("已中止", exit_code=130)
            return
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
        self._append_log("info", status)
        self._append_run_summary(exit_code, started_at, ended_at, script_path, port)
        self.current_run_started_at = None
        self.current_run_script_path = None
        self.current_run_port = None
        self._update_run_enabled()

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
        for widget in self.parameter_widgets.values():
            if (
                isinstance(widget, QLineEdit)
                and widget.placeholderText().startswith("填入这里")
                and not widget.text().strip()
            ):
                return False
        if self.process is not None and self.process.state() != QProcess.ProcessState.NotRunning:
            return False
        return True

    def _update_run_enabled(self) -> None:
        if hasattr(self, "run_button"):
            self.run_button.setEnabled(self._can_run())
        if hasattr(self, "connect_button"):
            self._update_bridge_controls()

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
            keep_generated=self.config.keep_generated,
        )
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
        try:
            self._ensure_bridge_backend().connect(port)
        except Exception as exc:
            self.bridge_status = EasyConStatus.FAILED
            self._append_log("error", f"连接伊机控失败: {exc}")
            self._update_bridge_controls()
            return
        self.bridge_status = EasyConStatus.BRIDGE_CONNECTED
        self._append_log("info", f"已连接伊机控: {port}")
        self.easycon_status.showMessage("已长期连接")
        self._update_bridge_controls()
        self._update_run_enabled()

    def disconnect_bridge(self) -> None:
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

    def _ensure_bridge_backend(self) -> BridgeEasyConBackend:
        bridge_path = self._bridge_path_from_ui()
        if self.bridge_backend is None:
            self.bridge_backend = BridgeEasyConBackend(bridge_path=bridge_path, log_callback=self._append_log)
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

    def _update_bridge_controls(self) -> None:
        if not hasattr(self, "connect_button"):
            return
        is_bridge = self._is_bridge_mode()
        self.connect_button.setEnabled(is_bridge)
        self.bridge_path.setEnabled(is_bridge)
        self.browse_bridge_button.setEnabled(is_bridge)
        self.mock_check.setEnabled(not is_bridge)
        if self.bridge_status == EasyConStatus.BRIDGE_CONNECTED:
            self.connect_button.setText("断开连接")
            self.backend_label.setText("单片机: 已长期连接")
        elif self.bridge_status == EasyConStatus.FAILED:
            self.connect_button.setText("连接伊机控")
            self.backend_label.setText("单片机: 连接失败")
        elif is_bridge:
            self.connect_button.setText("连接伊机控")
            status_text = "未选择串口" if not self.port_combo.currentText() else "未连接"
            self.backend_label.setText(f"单片机: {status_text}")
        else:
            self.connect_button.setText("连接伊机控")
            self.backend_label.setText("单片机: CLI 诊断模式")


def _first_supported_drop(mime_data) -> Path | None:  # type: ignore[no-untyped-def]
    for url in mime_data.urls():
        path = Path(url.toLocalFile())
        if path.suffix.lower() in {".txt", ".ecs"}:
            return path
    return None
