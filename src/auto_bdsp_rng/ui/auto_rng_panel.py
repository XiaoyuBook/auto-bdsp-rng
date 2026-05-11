from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QSettings, QThread, Qt, Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from auto_bdsp_rng.automation.auto_rng.models import AutoRngConfig, AutoRngProgress
from auto_bdsp_rng.automation.auto_rng.scripts import (
    DEFAULT_ADVANCE_SCRIPT_NAME,
    DEFAULT_SEED_SCRIPT_NAME,
    AutoScriptError,
    choose_default_script,
    list_auto_scripts,
    validate_auto_scripts,
)
from auto_bdsp_rng.ui.static_target_form import NATURES_ZH, StaticTargetForm


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = PROJECT_ROOT / "script"
IV_LABELS_ZH = ("HP", "攻击", "防御", "特攻", "特防", "速度")


class LockedTargetView(QGroupBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("锁定目标", parent)
        self.values: dict[str, QLabel] = {}
        self.setMaximumHeight(82)
        layout = QGridLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setHorizontalSpacing(18)
        layout.setVerticalSpacing(6)
        fields = (
            ("状态", "Status"),
            ("PID", "PID"),
            ("异色", "Shiny"),
            ("性格", "Nature"),
            ("特性", "Ability"),
            ("性别", "Gender"),
            ("IVs", "IVs"),
            ("身高", "Height"),
            ("体重", "Weight"),
        )
        for index, (label_text, key) in enumerate(fields):
            label = QLabel(label_text)
            label.setObjectName("MutedLabel")
            value = QLabel("-")
            value.setObjectName("Badge")
            value.setWordWrap(False)
            value.setMinimumWidth(220 if key == "IVs" else 48)
            self.values[key] = value
            layout.addWidget(label, 0, index)
            layout.addWidget(value, 1, index)
            layout.setColumnStretch(index, 5 if key == "IVs" else 1)
        self.clear()

    def clear(self, status: str = "未锁定") -> None:
        for key, label in self.values.items():
            label.setText(status if key == "Status" else "-")

    def apply_progress(self, progress: AutoRngProgress) -> None:
        target = progress.locked_target
        if target is None:
            self.clear()
            return
        state = target.state
        self.values["Status"].setText("已锁定")
        if state is None:
            return
        self.values["PID"].setText(_hex32(getattr(state, "pid", None)))
        self.values["Shiny"].setText(_shiny_text(getattr(state, "shiny", None)))
        self.values["Nature"].setText(_nature_text(getattr(state, "nature", None)))
        self.values["Ability"].setText(_display_value(getattr(state, "ability", None)))
        self.values["Gender"].setText(_gender_text(getattr(state, "gender", None)))
        ivs = getattr(state, "ivs", None)
        self.values["IVs"].setText(_ivs_text(ivs))
        self.values["Height"].setText(_display_value(getattr(state, "height", None)))
        self.values["Weight"].setText(_display_value(getattr(state, "weight", None)))


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

        splitter = QSplitter()
        self.config_panel = self._build_config_panel()
        splitter.addWidget(self.config_panel)
        splitter.addWidget(self._build_runtime_panel())
        splitter.setSizes([300, 1120])
        layout.addWidget(splitter, 1)

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
        self.start_button = QPushButton("开始")
        self.start_button.setObjectName("PrimaryButton")
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
        panel.setMinimumWidth(300)
        panel.setMaximumWidth(300)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.strategy_group = self._build_strategy_group()
        layout.addWidget(self.strategy_group)
        layout.addWidget(self._build_log_group(), 1)
        return panel

    def _build_strategy_group(self) -> QGroupBox:
        group = QGroupBox("自动策略")
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
            spin.setFixedWidth(145)
        self.shiny_threshold_seconds.setFixedWidth(145)
        form.addRow("最大帧数", self.max_advances)
        form.addRow("delay", self.fixed_delay)
        form.addRow("最大等待窗口", self.max_wait_frames)
        form.addRow("闪光阈值(秒)", self.shiny_threshold_seconds)
        # 同步开关（三态下拉框）
        self.sync_combo = QComboBox()
        self.sync_combo.addItems(["同步：关闭", "同步：首位普通精灵", "同步：首位同步精灵"])
        self.sync_combo.setFixedHeight(34)
        self.sync_combo.setMinimumWidth(220)
        form.addRow(self.sync_combo)
        # 自动反查下拉框
        self.auto_reverse_combo = QComboBox()
        self.auto_reverse_combo.addItems(["自动反查：关闭", "自动反查：开启"])
        self.auto_reverse_combo.setFixedHeight(34)
        self.auto_reverse_combo.setMinimumWidth(220)
        form.addRow(self.auto_reverse_combo)
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
        top = QWidget()
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(8)
        top_layout.addWidget(self._build_summary_group())
        self.locked_target_view = LockedTargetView()
        top_layout.addWidget(self.locked_target_view)
        layout.addWidget(top)
        layout.addWidget(self._build_target_form_group())
        return panel

    def _build_summary_group(self) -> QGroupBox:
        group = QGroupBox("运行摘要")
        group.setMaximumHeight(96)
        grid = QGridLayout(group)
        grid.setContentsMargins(12, 10, 12, 10)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(6)
        self.summary_group = group
        fields = (
            ("当前循环", "summary_loop"),
            ("当前阶段", "summary_phase"),
            ("原始目标帧", "summary_raw"),
            ("delay", "summary_delay"),
            ("目前帧数", "summary_current"),
            ("最终闪帧", "summary_flash"),
        )
        for index, (label_text, attr) in enumerate(fields):
            label = QLabel("-")
            label.setObjectName("Badge")
            setattr(self, attr, label)
            row = index // 3
            column = (index % 3) * 2
            grid.addWidget(QLabel(label_text), row, column)
            grid.addWidget(label, row, column + 1)
            grid.setColumnStretch(column + 1, 1)
        return group

    def _build_target_form_group(self) -> QGroupBox:
        group = QGroupBox("目标精灵设置")
        group.setMaximumHeight(390)
        layout = QVBoxLayout(group)
        self.target_form = StaticTargetForm(self)
        self.target_form.show_stats_check.hide()
        self.target_form.iv_calculator_button.hide()
        layout.addWidget(self.target_form)
        self.target_form_group = group
        return group

    def _build_log_group(self) -> QGroupBox:
        group = QGroupBox("日志")
        layout = QVBoxLayout(group)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)
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
        self.summary_phase.setText(text)

    def set_live_advances(self, advances: int) -> None:
        self.summary_current.setText(str(advances))

    def apply_progress(self, progress: AutoRngProgress) -> None:
        phase_text = progress.phase.value if hasattr(progress.phase, "value") else str(progress.phase)
        self.status_badge.setText(phase_text)
        self.summary_phase.setText(phase_text)
        self.summary_loop.setText(_display_value(progress.loop_index))
        self.summary_raw.setText(_display_value(progress.raw_target_advances))
        self.summary_delay.setText(_display_value(progress.fixed_delay))
        self.summary_current.setText(_display_value(progress.current_advances))
        self.summary_flash.setText(_display_value(progress.final_flash_frames))
        self.locked_target_view.apply_progress(progress)
        if progress.log_message:
            self.add_log(progress.log_message)

    def add_log(self, message: str) -> None:
        self.log_view.appendPlainText(message)

    def set_candidates(self, rows: list[list[str]], locked_index: int | None = None) -> None:
        locked_text = ""
        if locked_index is not None and 0 <= locked_index < len(rows):
            locked_text = f"，锁定 {rows[locked_index]}"
        self.add_log(f"候选结果 {len(rows)} 个{locked_text}")

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
        self._save_panel_state()
        config = self.build_config()
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

    def build_config(self) -> AutoRngConfig:
        return AutoRngConfig(
            script_dir=self.script_dir,
            seed_script_path=self._selected_path(self.seed_script_combo),
            advance_script_path=self._selected_path(self.advance_script_combo),
            hit_script_path=self._selected_path(self.hit_script_combo),
            fixed_delay=self.fixed_delay.value(),
            max_wait_frames=self.max_wait_frames.value(),
            loop_mode=str(self.mode_combo.currentData()),
            loop_count=self.loop_count.value(),
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
        s.setValue("auto_reverse", self.auto_reverse_combo.currentIndex())
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
        if s.contains("auto_reverse"):
            idx = int(s.value("auto_reverse", 0))
            if 0 <= idx < self.auto_reverse_combo.count():
                self.auto_reverse_combo.setCurrentIndex(idx)
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

    def _select_script_by_path(self, combo: QComboBox, path_str: str) -> None:
        if not path_str:
            return
        index = combo.findData(path_str)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _spin(self, minimum: int, maximum: int, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        spin.setFixedHeight(34)
        return spin


def _display_value(value: object) -> str:
    return "-" if value is None else str(value)


def _hex32(value: object) -> str:
    return "-" if value is None else f"{int(value):08X}"


def _shiny_text(value: object) -> str:
    return {0: "否", 1: "星闪", 2: "方闪"}.get(value, _display_value(value))


def _gender_text(value: object) -> str:
    return {0: "雄", 1: "雌", 2: "-"}.get(value, _display_value(value))


def _nature_text(value: object) -> str:
    if value is None:
        return "-"
    index = int(value)
    if 0 <= index < len(NATURES_ZH):
        return NATURES_ZH[index]
    return str(value)


def _ivs_text(ivs: object) -> str:
    if ivs is None:
        return "-"
    values = tuple(int(value) for value in ivs)
    if len(values) != 6:
        return "-"
    return " / ".join(f"{label} {value}" for label, value in zip(IV_LABELS_ZH, values))
