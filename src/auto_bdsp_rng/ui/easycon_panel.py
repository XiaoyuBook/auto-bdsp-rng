from __future__ import annotations

from dataclasses import replace
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
    QSplitter,
    QStatusBar,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from auto_bdsp_rng.automation.easycon import (
    EasyConConfig,
    EasyConInstallation,
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
        self.current_script_path: Path | None = None
        self.current_script_name = "未命名脚本"
        self.parameter_widgets: dict[str, QLineEdit | QSpinBox] = {}
        self.process: QProcess | None = None
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
        self.version_label = QLabel("EasyCon: 未检测")
        self.backend_label = QLabel("后端: CLI")
        self.port_combo = QComboBox()
        self.refresh_ports_button = QPushButton("刷新串口")
        self.refresh_ports_button.clicked.connect(self.refresh_ports)
        self.mock_check = QCheckBox("mock 模式")
        self.mock_check.setChecked(self.config.mock_enabled)
        self.mock_check.toggled.connect(self._save_config_from_ui)
        config_layout.addWidget(QLabel("ezcon"), 0, 0)
        config_layout.addWidget(self.ezcon_path, 0, 1)
        config_layout.addWidget(self.browse_ezcon_button, 0, 2)
        config_layout.addWidget(self.version_label, 1, 0, 1, 3)
        config_layout.addWidget(self.backend_label, 2, 0, 1, 3)
        config_layout.addWidget(QLabel("串口"), 3, 0)
        config_layout.addWidget(self.port_combo, 3, 1)
        config_layout.addWidget(self.refresh_ports_button, 3, 2)
        config_layout.addWidget(self.mock_check, 4, 1, 1, 2)
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
        log_layout.addWidget(self.log_view, 1)
        log_layout.addWidget(self.clear_log_button)
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
        if self.process is not None and self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()
            self._finish_run("已中止")
            return
        self.run_script()

    def run_script(self) -> None:
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
        self.run_seconds = 0
        self.elapsed_label.setText("00:00:00")
        self.run_timer.start()
        self.run_button.setText("停止脚本")
        self._append_log("info", f"开始运行脚本，端口: {port}")
        self.process.start()

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
        status = "已完成" if exit_code == 0 else f"失败，exit code: {exit_code}"
        self._finish_run(status)

    def _process_error(self, error) -> None:  # type: ignore[no-untyped-def]
        self._append_log("error", f"进程启动失败: {error}")
        self._finish_run("失败")

    def _finish_run(self, status: str) -> None:
        self.run_timer.stop()
        self.run_button.setText("运行脚本")
        self.easycon_status.showMessage(status)
        self._append_log("info", status)
        self._update_run_enabled()

    def _tick_run_timer(self) -> None:
        self.run_seconds += 1
        hours, remainder = divmod(self.run_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        self.elapsed_label.setText(f"{hours:02d}:{minutes:02d}:{seconds:02d}")

    def _can_run(self) -> bool:
        if not self.installation.is_available:
            return False
        if not self.editor.toPlainText().strip():
            return False
        if not self.mock_check.isChecked() and not self.port_combo.currentText():
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

    def _save_config_from_ui(self) -> None:
        if not hasattr(self, "ezcon_path"):
            return
        ezcon_text = self.ezcon_path.text().strip()
        self.config = EasyConConfig(
            ezcon_path=Path(ezcon_text) if ezcon_text else None,
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


def _first_supported_drop(mime_data) -> Path | None:  # type: ignore[no-untyped-def]
    for url in mime_data.urls():
        path = Path(url.toLocalFile())
        if path.suffix.lower() in {".txt", ".ecs"}:
            return path
    return None
