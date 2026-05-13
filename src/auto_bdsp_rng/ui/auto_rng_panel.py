from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, QThread, Qt, Signal, Slot
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from auto_bdsp_rng.automation.auto_rng.models import AutoRngConfig, AutoRngPhase, AutoRngProgress
from auto_bdsp_rng.automation.auto_rng.scripts import (
    DEFAULT_ADVANCE_SCRIPT_NAME,
    DEFAULT_SEED_SCRIPT_NAME,
    AutoScriptError,
    choose_default_script,
    list_auto_scripts,
    validate_auto_scripts,
)
from auto_bdsp_rng.data import GameVersion, StaticEncounterRecord, get_static_encounters
from auto_bdsp_rng.gen8_static import StateFilter
from auto_bdsp_rng.resources import resource_path
from auto_bdsp_rng.ui.static_target_form import StaticTargetForm
from auto_bdsp_rng.ui.target_dialog import TargetDialog, POKEMON_LABELS_ZH, NATURES_ZH


class _CopyableTextEdit(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)
        self.setUndoRedoEnabled(False)

    def contextMenuEvent(self, event):
        menu = self.createStandardContextMenu()
        if menu is None or menu.isEmpty():
            from PySide6.QtWidgets import QMenu
            from PySide6.QtGui import QAction
            menu = QMenu(self)
            menu.addAction("复制", self.copy, QAction.Shortcut("Ctrl+C"))
            menu.addAction("全选", self.selectAll, QAction.Shortcut("Ctrl+A"))
        menu.exec(event.globalPos())


SCRIPT_DIR = resource_path("script")


class AutoRngWorker(QObject):
    progressChanged = Signal(object)
    logEmitted = Signal(str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, runner: object) -> None:
        super().__init__()
        self.runner = runner
        setattr(self.runner, "progress_callback", self.progressChanged.emit)

    @Slot()
    def run(self) -> None:
        try:
            result = self.runner.run()
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.finished.emit(result)

    @Slot()
    def stop(self) -> None:
        stop = getattr(self.runner, "stop", None)
        if callable(stop):
            stop()


class AutoRngPanel(QWidget):
    startRequested = Signal(object)
    stopRequested = Signal()
    autoProgressChanged = Signal(object)
    ivCalculatorRequested = Signal()
    captureInfoRequested = Signal()  # 临时：手动触发精灵信息捕获
    captureLog = Signal(str)  # 临时：后台线程日志输出
    requestStatsCapture = Signal(object, object)  # 临时：后台请求主线程截图能力页(nature, characteristic)

    def __init__(self, parent: QWidget | None = None, script_dir: Path = SCRIPT_DIR) -> None:
        super().__init__(parent)
        self.script_dir = script_dir
        self._scripts: list[Path] = []
        self._runner_thread: QThread | None = None
        self._runner_worker: AutoRngWorker | None = None
        self._target_version = GameVersion.BD
        self._targets: list[tuple[StaticEncounterRecord, StateFilter, str]] = []
        self._settings = QSettings("auto-bdsp-rng", "AutoRngPanel")
        self._build_ui()
        self.refresh_scripts()
        self._restore_panel_state()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.toolbar = self._build_toolbar()
        layout.addWidget(self.toolbar)

        content = QWidget(self)
        self.content_grid = QGridLayout(content)
        self.content_grid.setContentsMargins(0, 0, 0, 0)
        self.content_grid.setHorizontalSpacing(12)
        self.content_grid.setVerticalSpacing(12)
        self.config_panel = self._build_config_panel()
        self.runtime_panel = self._build_runtime_panel()
        self.content_grid.addWidget(self.config_panel, 0, 0)
        self.content_grid.addWidget(self.runtime_panel, 0, 1)
        self.content_grid.addWidget(self._build_log_group(), 1, 0, 1, 2)
        self.content_grid.setColumnStretch(0, 0)
        self.content_grid.setColumnStretch(1, 1)
        self.content_grid.setRowStretch(0, 0)
        self.content_grid.setRowStretch(1, 1)
        layout.addWidget(content, 1)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._save_panel_state()
        super().closeEvent(event)

    def _build_toolbar(self) -> QWidget:
        toolbar = QFrame()
        toolbar.setObjectName("EasyConToolbar")
        toolbar.setMaximumHeight(60)
        toolbar.setMinimumHeight(56)
        row = QHBoxLayout(toolbar)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(8)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("单次", "single")
        self.mode_combo.addItem("循环 N 次", "count")
        self.mode_combo.addItem("无限循环", "infinite")
        self.loop_count = self._spin(1, 9999, 1)
        self.start_button = QToolButton()
        self.start_button.setText("开始")
        self.start_button.setObjectName("PrimaryButton")
        self.start_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.start_menu = QMenu(self.start_button)
        self.start_from_seed_action = QAction("从测种开始", self.start_button)
        self.start_from_capture_action = QAction("从捕获 Seed 开始", self.start_button)
        self.start_menu.addAction(self.start_from_seed_action)
        self.start_menu.addAction(self.start_from_capture_action)
        self.start_button.setMenu(self.start_menu)
        self.stop_button = QPushButton("停止")
        self.status_badge = QLabel("空闲")
        self.status_badge.setObjectName("Badge")
        for widget in (self.mode_combo, self.loop_count):
            widget.setFixedHeight(34)
        self.mode_combo.setFixedWidth(110)
        self.loop_count.setFixedWidth(80)
        self.start_button.setFixedHeight(34)
        self.stop_button.setFixedHeight(34)
        self.start_button.setMinimumWidth(68)
        self.stop_button.setMinimumWidth(68)
        self.start_button.clicked.connect(self._start_clicked)
        self.start_from_seed_action.triggered.connect(self._start_clicked)
        self.start_from_capture_action.triggered.connect(self._start_from_capture_clicked)
        self.stop_button.clicked.connect(self._stop_clicked)
        self.debug_output_check = QCheckBox("调试")
        self.debug_output_check.setToolTip("输出 CLI 耗时、时间戳等调试信息")
        row.addWidget(QLabel("运行模式"))
        row.addWidget(self.mode_combo)
        row.addWidget(QLabel("次数"))
        row.addWidget(self.loop_count)
        row.addWidget(self.debug_output_check)
        row.addStretch(1)
        row.addWidget(self.status_badge)
        row.addWidget(self.start_button)
        row.addWidget(self.stop_button)
        # 临时按钮：手动捕获精灵信息
        self.capture_info_button = QPushButton("捕获精灵信息")
        self.capture_info_button.setFixedHeight(34)
        self.capture_info_button.setMinimumWidth(110)
        self.capture_info_button.setToolTip("在笔记页点击，自动切换能力页并提取全部信息")
        self.capture_info_button.clicked.connect(self.captureInfoRequested.emit)
        row.addWidget(self.capture_info_button)
        return toolbar

    def _build_config_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMinimumWidth(450)
        panel.setMaximumWidth(450)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.strategy_group = self._build_strategy_group()
        layout.addWidget(self.strategy_group, 1)
        return panel

    def _build_strategy_group(self) -> QGroupBox:
        group = QGroupBox("自动策略")
        group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        form = QFormLayout(group)
        form.setContentsMargins(12, 12, 12, 12)
        form.setVerticalSpacing(8)
        self.max_advances = self._spin(0, 1_000_000_000, 100_000)
        self.fixed_delay = self._spin(0, 1_000_000_000, 100)
        self.max_wait_frames = self._spin(1, 1_000_000_000, 300)
        self.shiny_threshold_seconds = QDoubleSpinBox()
        self.shiny_threshold_seconds.setRange(0.0, 999.0)
        self.shiny_threshold_seconds.setDecimals(3)
        self.shiny_threshold_seconds.setSingleStep(0.1)
        self.shiny_threshold_seconds.setValue(0.0)
        for spin in (self.max_advances, self.fixed_delay, self.max_wait_frames):
            spin.setFixedWidth(215)
        self.shiny_threshold_seconds.setFixedWidth(215)
        form.addRow("最大帧数", self.max_advances)
        form.addRow("delay", self.fixed_delay)
        form.addRow("最大等待窗口", self.max_wait_frames)
        form.addRow("闪光阈值(秒)", self.shiny_threshold_seconds)
        # 同步开关（三态下拉框 + 性格输入）
        sync_row = QHBoxLayout()
        self.sync_combo = QComboBox()
        self.sync_combo.addItems(["同步：关闭", "同步：首位普通精灵", "同步：首位同步精灵"])
        self.sync_combo.setFixedHeight(34)
        self.sync_combo.setMinimumWidth(160)
        self.sync_combo.currentIndexChanged.connect(self._on_sync_changed)
        self.sync_nature_input = QLineEdit()
        self.sync_nature_input.setPlaceholderText("性格")
        self.sync_nature_input.setFixedHeight(34)
        self.sync_nature_input.setFixedWidth(72)
        self.sync_nature_input.setEnabled(False)
        sync_row.addWidget(self.sync_combo)
        sync_row.addWidget(self.sync_nature_input)
        form.addRow(sync_row)
        # 自动反查下拉框
        self.auto_reverse_combo = QComboBox()
        self.auto_reverse_combo.addItems(["自动反查：关闭", "自动反查：开启"])
        self.auto_reverse_combo.setFixedHeight(34)
        self.auto_reverse_combo.setMinimumWidth(150)
        self.reverse_lookup_window = QSpinBox()
        self.reverse_lookup_window.setRange(0, 10_000)
        self.reverse_lookup_window.setValue(500)
        self.reverse_lookup_window.setPrefix("±")
        self.reverse_lookup_window.setSuffix(" 帧")
        self.reverse_lookup_window.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.reverse_lookup_window.setFixedHeight(34)
        self.reverse_lookup_window.setFixedWidth(100)
        reverse_row = QHBoxLayout()
        reverse_row.addWidget(self.auto_reverse_combo)
        reverse_row.addWidget(self.reverse_lookup_window)
        form.addRow(reverse_row)
        return group

    def _build_script_group(self) -> QGroupBox:
        group = QGroupBox("脚本")
        layout = QGridLayout(group)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(8)
        self.seed_script_combo = QComboBox()
        self.advance_script_combo = QComboBox()
        self.hit_script_combo = QComboBox()
        self.reverse_script_combo = QComboBox()
        self.refresh_scripts_button = QPushButton("刷新脚本列表")
        self.refresh_scripts_button.clicked.connect(self.refresh_scripts)
        for combo in (self.seed_script_combo, self.advance_script_combo, self.hit_script_combo, self.reverse_script_combo):
            combo.setFixedHeight(34)
            combo.setFixedWidth(160)
        self.refresh_scripts_button.setFixedHeight(34)
        self.refresh_scripts_button.setMaximumWidth(250)
        layout.addWidget(QLabel("测种脚本"), 0, 0)
        layout.addWidget(self.seed_script_combo, 0, 1)
        layout.addWidget(QLabel("过帧脚本"), 0, 2)
        layout.addWidget(self.advance_script_combo, 0, 3)
        layout.addWidget(QLabel("撞闪脚本"), 1, 0)
        layout.addWidget(self.hit_script_combo, 1, 1)
        layout.addWidget(QLabel("反查脚本"), 1, 2)
        layout.addWidget(self.reverse_script_combo, 1, 3)
        layout.addWidget(self.refresh_scripts_button, 2, 0, 1, 4)
        return group

    def _build_runtime_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        # 脚本区（从左侧移入，位于右侧顶部）
        self.script_group = self._build_script_group()
        layout.addWidget(self.script_group)
        layout.addWidget(self._build_target_summary_group())
        return panel

    def _build_target_summary_group(self) -> QGroupBox:
        group = QGroupBox()
        layout = QVBoxLayout(group)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        header = QHBoxLayout()
        self.target_summary_title = QLabel("精灵筛选列表：-")
        self.target_button = QPushButton("目标精灵设置...")
        self.target_button.setFixedHeight(34)
        self.target_button.setMinimumWidth(150)
        self.target_button.clicked.connect(self.open_target_dialog)
        header.addWidget(self.target_summary_title)
        header.addStretch(1)
        header.addWidget(self.target_button)
        layout.addLayout(header)

        self.target_summary_scroll = QScrollArea()
        self.target_summary_scroll.setWidgetResizable(True)
        self.target_summary_scroll.setMaximumHeight(140)
        self.target_summary_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.target_summary_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.target_summary_container = QWidget()
        self.target_summary_layout = QVBoxLayout(self.target_summary_container)
        self.target_summary_layout.setContentsMargins(0, 0, 0, 0)
        self.target_summary_layout.setSpacing(6)
        self.target_summary_scroll.setWidget(self.target_summary_container)
        layout.addWidget(self.target_summary_scroll)
        self.target_summary_group = group

        self.target_form = StaticTargetForm(self)
        self.target_form.show_stats_check.hide()
        self.target_form.iv_calculator_button.hide()
        self.target_form.hide()
        self._refresh_target_summary()
        return group

    def _build_log_group(self) -> QGroupBox:
        group = QGroupBox("日志")
        group.setMaximumWidth(16777215)
        group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.log_group = group
        layout = QVBoxLayout(group)
        self.log_view = _CopyableTextEdit()
        self.log_view.setFont(QFont("Consolas", 10))
        self.log_view.setStyleSheet("QPlainTextEdit { padding: 12px; }")
        layout.addWidget(self.log_view)
        return group

    def refresh_scripts(self) -> None:
        self._scripts = list_auto_scripts(self.script_dir)
        for combo in (self.seed_script_combo, self.advance_script_combo, self.hit_script_combo, self.reverse_script_combo):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("请选择", None)
            for path in self._scripts:
                combo.addItem(path.name, str(path))
            combo.blockSignals(False)
        self._select_script(self.seed_script_combo, choose_default_script(self._scripts, DEFAULT_SEED_SCRIPT_NAME))
        self._select_script(self.advance_script_combo, choose_default_script(self._scripts, DEFAULT_ADVANCE_SCRIPT_NAME))

    def set_phase_text(self, text: str) -> None:
        self.status_badge.setText(text)

    def set_live_advances(self, advances: int) -> None:
        _ = advances

    def apply_progress(self, progress: AutoRngProgress) -> None:
        phase_text = progress.phase.value if hasattr(progress.phase, "value") else str(progress.phase)
        self.status_badge.setText(phase_text)
        self.autoProgressChanged.emit(progress)
        if progress.log_message:
            self.add_log(progress.log_message)

    def add_log(self, message: str) -> None:
        self.log_view.appendPlainText(message)

    def set_candidates(self, rows: list[list[str]], locked_index: int | None = None) -> None:
        locked_text = ""
        if locked_index is not None and 0 <= locked_index < len(rows):
            locked_text = f"，锁定 {rows[locked_index]}"
        self.add_log(f"候选结果 {len(rows)} 个{locked_text}")

    def set_target_version(self, version: GameVersion) -> None:
        self._target_version = version
        self.target_form.set_version(version)
        self._refresh_target_summary()

    def set_targets(self, targets: list[tuple[StaticEncounterRecord, StateFilter, str]]) -> None:
        self._targets = list(targets)
        self._refresh_target_summary()

    def targets(self) -> list[tuple[StaticEncounterRecord, StateFilter, str]]:
        if self._targets:
            return list(self._targets)
        record = self.target_form.selected_record()
        state_filter, shiny_mode = self.target_form.current_filter()
        return [(record, state_filter, shiny_mode)]

    def open_target_dialog(self) -> None:
        dialog = TargetDialog(self, self._target_version)
        dialog.set_targets(self.targets())
        if dialog.exec() == dialog.DialogCode.Accepted:
            self.set_targets(dialog.get_targets())

    def target_summary_text(self) -> str:
        return "; ".join(label.text() for label in getattr(self, "target_summary_labels", []))

    def _refresh_target_summary(self) -> None:
        if not hasattr(self, "target_summary_layout"):
            return
        while self.target_summary_layout.count():
            item = self.target_summary_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.target_summary_labels: list[QLabel] = []
        targets = self.targets() if hasattr(self, "target_form") else []
        if not targets:
            self.target_summary_title.setText("精灵筛选列表：-")
            return
        record = targets[0][0]
        name = POKEMON_LABELS_ZH.get(record.description, record.description)
        self.target_summary_title.setText(f"精灵筛选列表：{name}")
        for index, (_record, state_filter, shiny_mode) in enumerate(targets, start=1):
            label = QLabel(f"{index}. {_target_condition_text(state_filter, shiny_mode)}")
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)
            self.target_summary_layout.addWidget(label)
            self.target_summary_labels.append(label)
        self.target_summary_layout.addStretch(1)

    def set_search_context_summary(
        self,
        *,
        target: str,
        profile: str,
        filters: str,
        seed: str,
        max_advances: int,
    ) -> None:
        self.add_log(
            "\n".join(
                (
                    f"搜索目标: {target or '-'}",
                    f"个体筛选: {filters or '-'}",
                    f"最大帧数: {max_advances}",
                )
            )
        )

    def _start_clicked(self) -> None:
        self._start_with_phase(AutoRngPhase.RUN_SEED_SCRIPT)

    def _start_from_capture_clicked(self) -> None:
        self._start_with_phase(AutoRngPhase.CAPTURE_SEED)

    def _start_with_phase(self, start_phase: AutoRngPhase) -> None:
        self._save_panel_state()
        config = self.build_config(start_phase=start_phase)
        try:
            validate_auto_scripts(
                config.seed_script_path,
                config.advance_script_path,
                config.hit_script_path,
            )
        except AutoScriptError as exc:
            self.set_phase_text("配置错误")
            self.add_log(str(exc))
            return
        self.startRequested.emit(config)

    def _stop_clicked(self) -> None:
        if self._runner_worker is not None:
            self._runner_worker.stop()
        self.stopRequested.emit()

    def build_config(self, *, start_phase: AutoRngPhase = AutoRngPhase.RUN_SEED_SCRIPT) -> AutoRngConfig:
        return AutoRngConfig(
            script_dir=self.script_dir,
            seed_script_path=self._selected_path(self.seed_script_combo),
            advance_script_path=self._selected_path(self.advance_script_combo),
            hit_script_path=self._selected_path(self.hit_script_combo),
            reverse_script_path=self._selected_path(self.reverse_script_combo),
            auto_reverse=self.auto_reverse_combo.currentIndex() == 1,
            reverse_lookup_window=self.reverse_lookup_window.value(),
            sync_mode=self.sync_combo.currentIndex(),
            sync_nature=self.sync_nature_input.text().strip(),
            fixed_delay=self.fixed_delay.value(),
            max_wait_frames=self.max_wait_frames.value(),
            loop_mode=str(self.mode_combo.currentData()),
            loop_count=self.loop_count.value(),
            start_phase=start_phase,
            max_advances=self.max_advances.value(),
            shiny_threshold_seconds=self.shiny_threshold_seconds.value() or None,
            debug_output=self.debug_output_check.isChecked(),
        )

    def run_with_runner(self, runner: object) -> None:
        if self._runner_thread is not None:
            self.add_log("自动流程已在运行")
            return
        thread = QThread(self)
        worker = AutoRngWorker(runner)
        worker.moveToThread(thread)
        worker.progressChanged.connect(self.apply_progress)
        worker.logEmitted.connect(self.add_log)
        worker.finished.connect(self._runner_finished)
        worker.failed.connect(self._runner_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.started.connect(worker.run)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._runner_thread = thread
        self._runner_worker = worker
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        thread.start()

    def _runner_finished(self, progress: object) -> None:
        # 不重复 apply_progress：最后一条进度已通过 progressChanged 信号输出
        if isinstance(progress, AutoRngProgress):
            self.set_phase_text("已完成")
        self._clear_runner_thread()

    def _runner_failed(self, message: str) -> None:
        self.set_phase_text("失败")
        self.add_log(message)
        self._clear_runner_thread()

    def _clear_runner_thread(self) -> None:
        self._runner_thread = None
        self._runner_worker = None
        self.start_button.setEnabled(True)

    def _selected_path(self, combo: QComboBox) -> Path | None:
        value = combo.currentData()
        return Path(value) if value else None

    def _select_script(self, combo: QComboBox, path: Path | None) -> None:
        if path is None:
            combo.setCurrentIndex(0)
            return
        index = combo.findData(str(path))
        combo.setCurrentIndex(max(0, index))

    def _serialize_targets(self) -> str:
        rows = []
        for record, state_filter, shiny_mode in self.targets():
            rows.append({
                "category": str(record.category.value),
                "description": record.description,
                "version": str(record.version.value),
                "shiny_mode": shiny_mode,
                "filter": {
                    "gender": state_filter.gender,
                    "ability": state_filter.ability,
                    "shiny": state_filter.shiny,
                    "height_min": state_filter.height_min,
                    "height_max": state_filter.height_max,
                    "weight_min": state_filter.weight_min,
                    "weight_max": state_filter.weight_max,
                    "skip": state_filter.skip,
                    "iv_min": list(state_filter.iv_min),
                    "iv_max": list(state_filter.iv_max),
                    "natures": list(state_filter.natures),
                    "hidden_powers": list(state_filter.hidden_powers),
                },
            })
        return json.dumps(rows, ensure_ascii=False, separators=(",", ":"))

    def _restore_targets_json(self, text: str) -> bool:
        try:
            rows = json.loads(text)
        except (TypeError, json.JSONDecodeError):
            return False
        if not isinstance(rows, list):
            return False
        restored: list[tuple[StaticEncounterRecord, StateFilter, str]] = []
        all_records = get_static_encounters()
        for row in rows:
            if not isinstance(row, dict):
                return False
            category = row.get("category")
            description = row.get("description")
            version = row.get("version")
            filter_data = row.get("filter")
            if not isinstance(category, str) or not isinstance(description, str) or not isinstance(version, str):
                return False
            if not isinstance(filter_data, dict):
                return False
            record = next(
                (
                    candidate
                    for candidate in all_records
                    if candidate.category.value == category
                    and candidate.description == description
                    and candidate.version.value == version
                ),
                None,
            )
            if record is None:
                return False
            try:
                state_filter = StateFilter(
                    gender=int(filter_data.get("gender", 255)),
                    ability=int(filter_data.get("ability", 255)),
                    shiny=int(filter_data.get("shiny", 255)),
                    height_min=int(filter_data.get("height_min", 0)),
                    height_max=int(filter_data.get("height_max", 255)),
                    weight_min=int(filter_data.get("weight_min", 0)),
                    weight_max=int(filter_data.get("weight_max", 255)),
                    skip=bool(filter_data.get("skip", False)),
                    iv_min=tuple(int(value) for value in filter_data.get("iv_min", (0, 0, 0, 0, 0, 0))),
                    iv_max=tuple(int(value) for value in filter_data.get("iv_max", (31, 31, 31, 31, 31, 31))),
                    natures=tuple(bool(value) for value in filter_data.get("natures", (True,) * 25)),
                    hidden_powers=tuple(bool(value) for value in filter_data.get("hidden_powers", (True,) * 16)),
                )
            except (TypeError, ValueError):
                return False
            restored.append((record, state_filter, str(row.get("shiny_mode", "any"))))
        if not restored:
            return False
        self._targets = restored
        return True

    def _save_panel_state(self) -> None:
        """持久化当前面板设置。"""
        s = self._settings
        s.setValue("mode_index", self.mode_combo.currentIndex())
        s.setValue("loop_count", self.loop_count.value())
        s.setValue("max_advances", self.max_advances.value())
        s.setValue("fixed_delay", self.fixed_delay.value())
        s.setValue("max_wait_frames", self.max_wait_frames.value())
        s.setValue("shiny_threshold", self.shiny_threshold_seconds.value())
        seed_path = self._selected_path(self.seed_script_combo)
        advance_path = self._selected_path(self.advance_script_combo)
        hit_path = self._selected_path(self.hit_script_combo)
        if seed_path is not None:
            s.setValue("seed_script", str(seed_path))
        if advance_path is not None:
            s.setValue("advance_script", str(advance_path))
        if hit_path is not None:
            s.setValue("hit_script", str(hit_path))
        reverse_path = self._selected_path(self.reverse_script_combo)
        if reverse_path is not None:
            s.setValue("reverse_script", str(reverse_path))
        s.setValue("sync_state", self.sync_combo.currentIndex())
        s.setValue("sync_nature", self.sync_nature_input.text())
        s.setValue("auto_reverse", self.auto_reverse_combo.currentIndex())
        s.setValue("reverse_lookup_window", self.reverse_lookup_window.value())
        s.setValue("target_list_json", self._serialize_targets())
        # 目标精灵设置
        tf = self.target_form
        s.setValue("target_category", tf.category_combo.currentIndex())
        s.setValue("target_encounter", tf.encounter_combo.currentIndex())
        s.setValue("target_shiny_filter", tf.shiny_filter.currentIndex())
        s.setValue("target_ability_filter", tf.ability_filter.currentIndex())
        s.setValue("target_gender_filter", tf.gender_filter.currentIndex())
        s.setValue("target_nature", tf.nature_combo.currentIndex())
        s.setValue("target_skip_filter", tf.skip_filter.isChecked())

    def _restore_panel_state(self) -> None:
        """恢复上次持久化的面板设置。"""
        s = self._settings
        if s.contains("mode_index"):
            idx = int(s.value("mode_index", 0))
            if 0 <= idx < self.mode_combo.count():
                self.mode_combo.setCurrentIndex(idx)
        if s.contains("loop_count"):
            self.loop_count.setValue(int(s.value("loop_count", 1)))
        if s.contains("max_advances"):
            self.max_advances.setValue(int(s.value("max_advances", 100_000)))
        if s.contains("fixed_delay"):
            self.fixed_delay.setValue(int(s.value("fixed_delay", 100)))
        if s.contains("max_wait_frames"):
            self.max_wait_frames.setValue(int(s.value("max_wait_frames", 300)))
        if s.contains("shiny_threshold"):
            self.shiny_threshold_seconds.setValue(float(s.value("shiny_threshold", 0.0)))
        # 恢复脚本选择（脚本列表已通过 refresh_scripts 加载）
        if s.contains("seed_script"):
            self._select_script_by_path(self.seed_script_combo, str(s.value("seed_script", "")))
        if s.contains("advance_script"):
            self._select_script_by_path(self.advance_script_combo, str(s.value("advance_script", "")))
        if s.contains("hit_script"):
            self._select_script_by_path(self.hit_script_combo, str(s.value("hit_script", "")))
        if s.contains("reverse_script"):
            self._select_script_by_path(self.reverse_script_combo, str(s.value("reverse_script", "")))
        if s.contains("sync_state"):
            idx = int(s.value("sync_state", 0))
            if 0 <= idx < self.sync_combo.count():
                self.sync_combo.setCurrentIndex(idx)
        if s.contains("sync_nature"):
            self.sync_nature_input.setText(str(s.value("sync_nature", "")))
        if s.contains("auto_reverse"):
            idx = int(s.value("auto_reverse", 0))
            if 0 <= idx < self.auto_reverse_combo.count():
                self.auto_reverse_combo.setCurrentIndex(idx)
        if s.contains("reverse_lookup_window"):
            self.reverse_lookup_window.setValue(int(s.value("reverse_lookup_window", 500)))
        if s.contains("target_list_json"):
            self._restore_targets_json(str(s.value("target_list_json", "")))
        # 目标精灵设置
        tf = self.target_form
        if s.contains("target_category"):
            idx = int(s.value("target_category", 0))
            if 0 <= idx < tf.category_combo.count():
                tf.category_combo.setCurrentIndex(idx)
        if s.contains("target_encounter"):
            idx = int(s.value("target_encounter", 0))
            if 0 <= idx < tf.encounter_combo.count():
                tf.encounter_combo.setCurrentIndex(idx)
        if s.contains("target_shiny_filter"):
            idx = int(s.value("target_shiny_filter", 0))
            if 0 <= idx < tf.shiny_filter.count():
                tf.shiny_filter.setCurrentIndex(idx)
        if s.contains("target_ability_filter"):
            idx = int(s.value("target_ability_filter", 0))
            if 0 <= idx < tf.ability_filter.count():
                tf.ability_filter.setCurrentIndex(idx)
        if s.contains("target_gender_filter"):
            idx = int(s.value("target_gender_filter", 0))
            if 0 <= idx < tf.gender_filter.count():
                tf.gender_filter.setCurrentIndex(idx)
        if s.contains("target_nature"):
            idx = int(s.value("target_nature", 0))
            if 0 <= idx < tf.nature_combo.count():
                tf.nature_combo.setCurrentIndex(idx)
        if s.contains("target_skip_filter"):
            tf.skip_filter.setChecked(s.value("target_skip_filter") == "true")
        self._refresh_target_summary()

    def _select_script_by_path(self, combo: QComboBox, path_str: str) -> None:
        if not path_str:
            return
        index = combo.findData(path_str)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _on_sync_changed(self, index: int) -> None:
        """同步状态改变时启用/禁用性格输入框。"""
        if index == 0:  # 关闭
            self.sync_nature_input.setEnabled(False)
            self.sync_nature_input.clear()
        else:
            self.sync_nature_input.setEnabled(True)

    def _spin(self, minimum: int, maximum: int, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        spin.setFixedHeight(34)
        return spin


def _target_condition_text(state_filter: StateFilter, shiny_mode: str) -> str:
    parts: list[str] = []
    shiny_label = {
        "shiny": "异色",
        "star": "星闪",
        "square": "方闪",
        "none": "非异色",
    }.get(shiny_mode)
    if shiny_label is not None:
        parts.append(f"异色：{shiny_label}")
    if state_filter.ability != 255:
        ability_label = {0: "0", 1: "1", 2: "隐藏"}.get(state_filter.ability, str(state_filter.ability))
        parts.append(f"特性：{ability_label}")
    if state_filter.gender != 255:
        gender_label = {0: "雄性", 1: "雌性", 2: "无性别"}.get(state_filter.gender, str(state_filter.gender))
        parts.append(f"性别：{gender_label}")
    if state_filter.height_min != 0 or state_filter.height_max != 255:
        value = str(state_filter.height_min) if state_filter.height_min == state_filter.height_max else f"{state_filter.height_min}-{state_filter.height_max}"
        parts.append(f"身高：{value}")
    if state_filter.weight_min != 0 or state_filter.weight_max != 255:
        value = str(state_filter.weight_min) if state_filter.weight_min == state_filter.weight_max else f"{state_filter.weight_min}-{state_filter.weight_max}"
        parts.append(f"体重：{value}")
    if not all(state_filter.natures):
        locked = [NATURES_ZH[index] for index, enabled in enumerate(state_filter.natures) if enabled]
        if locked:
            parts.append(f"性格：{','.join(locked)}")
    iv_parts = [
        f"{label}{lo}" if lo == hi else f"{label}{lo}-{hi}"
        for label, lo, hi in zip(("HP", "攻击", "防御", "特攻", "特防", "速度"), state_filter.iv_min, state_filter.iv_max)
        if lo != 0 or hi != 31
    ]
    if iv_parts:
        parts.append("个体：" + "/".join(iv_parts))
    return " | ".join(parts) if parts else "无额外筛选"
