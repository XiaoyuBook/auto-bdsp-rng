"""Read-only preparation checks and navigation for the existing start paths."""

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from auto_bdsp_rng.automation.auto_rng.models import AutoRngPhase
from auto_bdsp_rng.automation.auto_rng.scripts import validate_auto_scripts
from auto_bdsp_rng.automation.auto_tid_rng import AutoTidRngConfig, AutoTidRngPhase
from auto_bdsp_rng.automation.easycon import EasyConStatus
from auto_bdsp_rng.ui.combo_box import ChevronComboBox
from auto_bdsp_rng.ui.workspace_theme import primary_button_styles, ui_font, workspace_styles


@dataclass(frozen=True)
class ReadinessItem:
    key: str
    title: str
    state: str
    detail: str
    action: str = ""
    action_text: str = "去设置"


class ReadinessDialog(QDialog):
    selectionChanged = Signal()
    actionRequested = Signal(str)
    startRequested = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("StartReadinessDialog")
        self.setWindowTitle("开始前检查")
        self.setFont(ui_font())
        self.resize(700, 640)
        self.setMinimumSize(560, 420)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)
        title = QLabel("开始前检查")
        title.setObjectName("ReadinessTitle")
        root.addWidget(title)
        selectors = QHBoxLayout()
        self.module_combo = ChevronComboBox()
        self.module_combo.addItems(("自动定点", "自动 TID"))
        self.mode_combo = ChevronComboBox()
        selectors.addWidget(QLabel("任务"))
        selectors.addWidget(self.module_combo, 1)
        selectors.addWidget(QLabel("启动方式"))
        selectors.addWidget(self.mode_combo, 2)
        root.addLayout(selectors)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        root.addWidget(self.summary)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        self.rows_layout = QVBoxLayout(content)
        self.rows_layout.setContentsMargins(0, 0, 4, 0)
        self.rows_layout.setSpacing(8)
        self.rows_layout.addStretch()
        scroll.setWidget(content)
        root.addWidget(scroll, 1)
        note = QLabel("点击开始将自动保存当前配置，并完成必要的连接与初始化。运行中的常规配置下次启动生效，定点 delay 策略下轮生效。")
        note.setObjectName("ReadinessNote")
        note.setWordWrap(True)
        root.addWidget(note)
        footer = QHBoxLayout()
        self.close_button = QPushButton("关闭")
        self.close_button.clicked.connect(self.hide)
        self.start_button = QPushButton("按此方式开始")
        self.start_button.setObjectName("PrimaryButton")
        self.start_button.clicked.connect(self.startRequested.emit)
        footer.addStretch()
        footer.addWidget(self.close_button)
        footer.addWidget(self.start_button)
        root.addLayout(footer)
        self.rows = {}
        self._last_view = None
        self.module_combo.currentIndexChanged.connect(self._update_modes)
        self.mode_combo.currentIndexChanged.connect(lambda _index: self.selectionChanged.emit())
        self._update_modes()
        self.setStyleSheet(workspace_styles("""
            QDialog#StartReadinessDialog { background: $background; color: $text; }
            QDialog#StartReadinessDialog QLabel { background: transparent; border: 0; }
            QLabel#ReadinessTitle { font-size: 20px; font-weight: 500; }
            QLabel#ReadinessNote { color: $text_secondary; font-size: 12px; }
            QFrame#ReadinessRow { background: $surface; border: 1px solid $card_border; border-radius: 10px; }
            QLabel#ReadinessRowTitle { font-size: 14px; font-weight: 500; }
            QLabel#ReadinessRowDetail { color: $text_secondary; font-size: 12px; }
            QLabel#ReadinessRowTitle[state="blocked"] { color: $warning; }
            QLabel#ReadinessRowTitle[state="ok"] { color: $text; }
            QDialog#StartReadinessDialog QPushButton { min-height: 32px; padding: 0 12px; border-radius: 7px; }
        """) + primary_button_styles("QPushButton#PrimaryButton"))

    def _update_modes(self):
        self.mode_combo.blockSignals(True)
        self.mode_combo.clear()
        self.mode_combo.addItem("从测种脚本开始", "script")
        self.mode_combo.addItem("从捕获 Seed 开始", "capture")
        if self.module_combo.currentIndex() == 0:
            self.mode_combo.addItem("从校正开始", "reidentify")
        self.mode_combo.blockSignals(False)
        self.selectionChanged.emit()

    def set_items(self, items: tuple[ReadinessItem, ...], summary: str, can_start: bool):
        view = (items, summary, can_start)
        if view == self._last_view:
            return
        self._last_view = view
        self.summary.setText(summary)
        self.start_button.setEnabled(can_start)
        keys = {item.key for item in items}
        for key in tuple(self.rows):
            if key not in keys:
                self.rows.pop(key)[0].deleteLater()
        for index, item in enumerate(items):
            if item.key not in self.rows:
                frame = QFrame()
                frame.setObjectName("ReadinessRow")
                layout = QHBoxLayout(frame)
                layout.setContentsMargins(12, 10, 12, 10)
                words = QVBoxLayout()
                words.setSpacing(4)
                title, detail = QLabel(), QLabel()
                title.setObjectName("ReadinessRowTitle")
                detail.setObjectName("ReadinessRowDetail")
                detail.setWordWrap(True)
                detail.setTextFormat(Qt.TextFormat.PlainText)
                words.addWidget(title)
                words.addWidget(detail)
                layout.addLayout(words, 1)
                button = QPushButton()
                button.clicked.connect(lambda _checked=False, b=button: self.actionRequested.emit(b.property("action")))
                layout.addWidget(button)
                self.rows[item.key] = (frame, title, detail, button)
            frame, title, detail, button = self.rows[item.key]
            self.rows_layout.insertWidget(index, frame)
            prefix = {"ok": "已就绪", "blocked": "待处理", "pending": "启动时处理", "info": "提示"}[item.state]
            title.setText(f"{item.title} · {prefix}")
            if title.property("state") != item.state:
                title.setProperty("state", item.state)
                title.style().unpolish(title)
                title.style().polish(title)
            detail.setText(item.detail)
            button.setText(item.action_text)
            button.setProperty("action", item.action)
            button.setVisible(bool(item.action))


class StartReadinessController(QObject):
    """Observe UI state without calling mutating ensure/save/start helpers."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.dialog = ReadinessDialog(window)
        self.dialog.selectionChanged.connect(self.refresh)
        self.dialog.actionRequested.connect(self.navigate)
        self.dialog.startRequested.connect(self.start)
        self.button = QPushButton("开始前检查")
        self.button.setObjectName("ReadinessButton")
        self.button.setFixedHeight(32)
        self.button.clicked.connect(self.show)
        window.header_layout.insertWidget(window.header_layout.count() - 3, self.button)
        self.items = ()
        self._cache = {}
        self.preview_button = QPushButton("打开视频源设置", window.preview_label)
        self.preview_button.setStyleSheet("QPushButton { background: #2F3D48; color: #E7EEF3; border: 1px solid #617180; border-radius: 7px; padding: 0 12px; min-height: 32px; }")
        self.preview_button.clicked.connect(window.show_video_source_dialog)
        window.preview_label.installEventFilter(self)
        self.timer = QTimer(self)
        self.timer.setInterval(1500)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        window.tabs.currentChanged.connect(self._page_changed)
        window.easycon_tab.connectionPresentationChanged.connect(self.refresh)
        # Defer the first read until MainWindow finishes loading its settings.
        QTimer.singleShot(0, self.refresh)

    def _page_changed(self, *_args):
        tab = self.window.tabs.currentWidget()
        if tab in (self.window.auto_rng_tab, self.window.auto_tid_rng_tab):
            self.dialog.module_combo.setCurrentIndex(int(tab is self.window.auto_tid_rng_tab))
        self.refresh()

    @property
    def panel(self):
        return self.window.auto_tid_rng_tab if self.dialog.module_combo.currentIndex() else self.window.auto_rng_tab

    def phase(self):
        enum = AutoTidRngPhase if self.dialog.module_combo.currentIndex() else AutoRngPhase
        return {"script": enum.RUN_SEED_SCRIPT,
                "capture": enum.CAPTURE_TIDSID if enum is AutoTidRngPhase else enum.CAPTURE_SEED,
                "reidentify": AutoRngPhase.REIDENTIFY}[self.dialog.mode_combo.currentData()]

    def _validate_files(self, key, paths, signature, validate):
        stamps = []
        for path in paths:
            try:
                stat = Path(path).stat()
                stamps.append((str(path), stat.st_mtime_ns, stat.st_size))
            except (OSError, TypeError, ValueError):
                stamps.append((str(path), None))
        token = (tuple(stamps), signature)
        if self._cache.get(key, (None,))[0] != token:
            try:
                validate()
                error = ""
            except (OSError, ValueError, RuntimeError, TypeError) as exc:
                error = str(exc)
            self._cache[key] = (token, error)
        return self._cache[key][1]

    def collect(self) -> tuple[ReadinessItem, ...]:
        from auto_bdsp_rng.ui.main_window import load_project_xs_config, TIDSID_BLINK_COUNT, DEFAULT_BLINK_COUNT

        w, panel = self.window, self.panel
        tid = panel is w.auto_tid_rng_tab
        items = []
        if w._video_source_connecting or w._video_source_stop_pending:
            video = ("blocked", "视频源正在连接或断开，请等待完成。")
        elif w._video_source_connected:
            video = ("ok", "已连接；启动时仍会确认可用画面。")
        else:
            video = ("blocked", "请先连接视频源。")
        items.append(ReadinessItem("video", "视频源", *video, "video", "连接设置"))
        status = w.easycon_tab._native_status()
        port = w.easycon_tab._connection_port() or w.easycon_tab.config.last_port
        if status == EasyConStatus.BRIDGE_CONNECTED:
            controller = ("ok", "伊机控已连接。")
        elif status == EasyConStatus.RUNNING:
            controller = ("blocked", "伊机控脚本正在运行。")
        elif port and str(port).casefold() != "mock":
            controller = ("pending", f"未连接，启动时将尝试连接 {port}；连接成功后继续。")
        else:
            controller = ("blocked", "尚未选择可用串口，请打开连接设置。")
        items.append(ReadinessItem("controller", "伊机控", *controller, "controller", "连接设置"))
        paths = [w._selected_auto_seed_config_path()]
        if not tid:
            paths.append(w._selected_auto_reidentify_config_path())
        error = self._validate_files("seed", paths, tid, lambda: [
            load_project_xs_config(path, blink_count=TIDSID_BLINK_COUNT if tid else DEFAULT_BLINK_COUNT)
            for path in paths
        ])
        items.append(ReadinessItem("seed", "Seed 配置", "blocked" if error else "ok",
                                   error or " / ".join(f"{label}：{Path(path).name}" for label, path in zip(("测种", "校正"), paths)), "seed"))
        if self.phase() == AutoRngPhase.REIDENTIFY:
            try:
                w._current_auto_rng_seed_result()
                error = ""
            except ValueError as exc:
                error = str(exc)
            items.append(ReadinessItem("seed_value", "当前 Seed", "blocked" if error else "ok",
                                       error or "已填入可解析的 Seed；实际校正结果由捕获确定。", "seed_value", "查看 Seed"))
        try:
            count = len(panel.target_display_tids() if tid else panel.targets())
            target_error = "" if count else "请至少添加一个目标 Display TID。"
        except ValueError as exc:
            count, target_error = 0, str(exc)
        items.append(ReadinessItem("targets", "目标条件", "blocked" if target_error else "ok",
                                   target_error or f"当前有 {count} 组目标条件。", "targets", "查看目标"))
        try:
            if tid:
                # Script validation is independent of an empty target list.
                config = AutoTidRngConfig(
                    script_dir=panel.script_dir, start_phase=self.phase(),
                    seed_script_path=panel._selected_path(panel.seed_script_combo),
                    name_script_path=panel._selected_path(panel.name_script_combo),
                )
                required = [(config.name_script_path, "取名")]
                if config.start_phase == AutoTidRngPhase.RUN_SEED_SCRIPT:
                    required.append((config.seed_script_path, "测种"))
                validator = lambda: panel._validate_config(config)
            else:
                config = panel.build_config(start_phase=self.phase())
                required = [(config.advance_script_path, "过帧"), (config.hit_script_path, "撞闪")]
                if config.escape_continue:
                    required.append((config.escape_script_path, "逃跑"))
                validator = lambda: validate_auto_scripts(
                    config.seed_script_path, config.advance_script_path, config.hit_script_path,
                    escape_continue=config.escape_continue, escape_script_path=config.escape_script_path,
                    shiny_threshold_seconds=config.shiny_threshold_seconds, target_species=config.target_species,
                )
            missing = [name for path, name in required if path is None]
            script_paths = [value for key, value in vars(config).items() if key.endswith("_script_path")]
            error = self._validate_files("scripts", script_paths, repr(config), validator)
            script_text = "缺少：" + "、".join(missing) if missing else error or "所选启动方式的必需脚本检查通过。"
        except ValueError:
            error, script_text = "targets", "请先完善目标条件，再检查此启动方式的脚本。"
        items.append(ReadinessItem("scripts", "脚本", "blocked" if error else "ok", script_text, "scripts", "查看脚本"))
        if not tid:
            ocr_busy = (w._ocr_task_thread is not None and w._ocr_task_thread.isRunning()) or w._ocr_full_test_running or w._shiny_calibration_worker is not None
            ready = w._ocr_warmup_result is not None and w._ocr_warmup_result[0]
            items.append(ReadinessItem("ocr", "OCR", "blocked" if ocr_busy else "ok" if ready else "pending",
                                       "OCR 正在被识别或校准任务使用。" if ocr_busy else "OCR 初始化完成。" if ready else "启动时自动初始化，完成后继续。", "ocr", "OCR 设置"))
        busy = w._automation_script_busy() or any(p._runner_thread is not None or p._preparing for p in (w.auto_rng_tab, w.auto_tid_rng_tab))
        items.append(ReadinessItem("busy", "任务占用", "blocked" if busy else "ok",
                                   "已有任务正在运行或准备；结束后可启动新任务。" if busy else "当前没有自动任务或伊机控脚本占用。", "task", "返回任务"))
        if tid:
            dirty = panel.save_state_label.property("dirty") or panel.script_save_state_label.property("dirty")
        else:
            dirty = panel.config_saved_label.property("saved") is False or panel.script_save_state_label.property("saved") is False
        items.append(ReadinessItem("saved", "配置保存", "info" if dirty else "ok",
                                   "有未保存修改，点击开始会沿用现有流程自动保存。" if dirty else "配置已保存；运行时按现有生效规则使用。"))
        return tuple(items)

    def refresh(self, *_args):
        if self.window._is_closing:
            return
        self.items = self.collect()
        blocked = sum(item.state == "blocked" for item in self.items)
        module = self.dialog.module_combo.currentText()
        mode = self.dialog.mode_combo.currentText()
        missing = "、".join(item.title for item in self.items if item.state == "blocked")
        summary = f"{module} / {mode}\n还需处理：{missing}" if blocked else f"{module} / {mode}\n配置检查通过，启动时完成连接及初始化。"
        self.button.setText(f"开始前检查 · {blocked}" if blocked else "开始前检查 ✓")
        self.button.setToolTip(summary + "\n" + "\n".join(f"{item.title}：{item.detail}" for item in self.items if item.state != "ok"))
        if self.dialog.isVisible():
            self.dialog.set_items(self.items, summary, self.panel.start_button.isEnabled())

    def show(self, *_args):
        self.dialog.show()
        self.refresh()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def show_for(self, panel):
        self.dialog.module_combo.setCurrentIndex(int(panel is self.window.auto_tid_rng_tab))
        self.show()

    def eventFilter(self, watched, event):
        if watched is self.window.preview_label and event.type() in (QEvent.Type.Resize, QEvent.Type.Paint):
            visible = watched.pixmap().isNull() and watched.height() >= 170 and not watched._selection_enabled
            self.preview_button.setVisible(visible)
            if visible:
                size = self.preview_button.sizeHint()
                self.preview_button.resize(size.width(), 34)
                self.preview_button.move((watched.width() - size.width()) // 2, watched.height() // 2 + 26)
        return False

    def start(self):
        panel, phase = self.panel, self.phase()
        self.dialog.hide()
        self.window.tabs.setCurrentWidget(panel)
        panel._start_with_phase(phase)
        self.refresh()

    def navigate(self, action):
        w, panel = self.window, self.panel
        self.dialog.hide()
        if action == "video":
            w.show_video_source_dialog()
        elif action == "controller":
            w.easycon_tab.show_connection_dialog()
        elif action in ("seed", "seed_value"):
            w.tabs.setCurrentWidget(w.project_xs_tab)
            (w.seed32_inputs[0] if action == "seed_value" else w.seed_config_combo).setFocus()
        elif action == "ocr":
            w.open_ocr_settings()
        else:
            w.tabs.setCurrentWidget(panel)
            if action == "targets":
                if panel is w.auto_rng_tab:
                    panel.open_target_dialog()
                else:
                    panel.target_input.setFocus()
            elif action == "scripts":
                if panel is w.auto_rng_tab:
                    panel._runtime_script_editor_expanded = True
                    panel._set_runtime_script_summary_visible(True)
                    panel.runtime_panel.ensureWidgetVisible(panel.script_group)
                    missing = panel._missing_script_fields()
                    focus = missing[0][0] if missing else panel.seed_script_combo
                else:
                    panel.script_toggle.setChecked(True)
                    panel._set_scripts_expanded(True)
                    panel.runtime_scroll.ensureWidgetVisible(panel.script_fields)
                    focus = panel.name_script_combo if self.phase() == AutoTidRngPhase.CAPTURE_TIDSID else panel.seed_script_combo
                focus.setFocus()
