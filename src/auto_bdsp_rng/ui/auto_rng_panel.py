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
    QLineEdit,
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
from auto_bdsp_rng.ui.static_target_form import StaticTargetForm  # noqa: F401  # 保留兼容旧引用
from auto_bdsp_rng.ui.target_dialog import TargetDialog


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = PROJECT_ROOT / "script"


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
        self._targets: list[tuple[object, object, str]] = []
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
        self.target_button = QPushButton("目标精灵设置...")
        self.target_button.setFixedHeight(34)
        self.target_button.setMinimumWidth(130)
        self.target_button.clicked.connect(self._open_target_dialog)
        self.target_summary = QLabel("")
        self.target_summary.setObjectName("MutedLabel")
        self.target_summary.setMaximumWidth(180)
        self.target_summary.setWordWrap(False)
        row.addWidget(self.target_button)
        row.addWidget(self.target_summary)
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
        self.script_group = self._build_script_group()
        layout.addWidget(self.script_group)
        top = QWidget()
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(8)
        top_layout.addWidget(self._build_summary_group())
        layout.addWidget(top)
        return panel

    def _open_target_dialog(self) -> None:
        dlg = TargetDialog(self)
        dlg.set_targets(self._targets)
        if dlg.exec() == TargetDialog.Accepted:
            self._targets = dlg.get_targets()
            if self._targets:
                names = [r.description for r, _, _ in self._targets]
                self.target_summary.setText(f"{len(self._targets)}个目标: {', '.join(names)}")
            else:
                self.target_summary.setText("")

    def get_targets(self) -> list[tuple[object, object, str]]:
        return self._targets

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
            reverse_script_path=self._selected_path(self.reverse_script_combo),
            auto_reverse=self.auto_reverse_combo.currentIndex() == 1,
            sync_mode=self.sync_combo.currentIndex(),
            sync_nature=self.sync_nature_input.text().strip(),
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
        s.setValue("sync_nature", self.sync_nature_input.text())
        s.setValue("auto_reverse", self.auto_reverse_combo.currentIndex())
        # 目标精灵设置持久化
        s.setValue("target_count", len(self._targets))
        for i, (record, sf, sm) in enumerate(self._targets):
            s.setValue(f"target_{i}_name", record.description)
            s.setValue(f"target_{i}_shiny", sm)
            s.setValue(f"target_{i}_iv_min", list(sf.iv_min))
            s.setValue(f"target_{i}_iv_max", list(sf.iv_max))
            locked_natures = [i for i, v in enumerate(sf.natures) if v]
            if len(locked_natures) == 25:
                s.setValue(f"target_{i}_nature", -1)
            elif len(locked_natures) == 1:
                s.setValue(f"target_{i}_nature", locked_natures[0])
            else:
                s.setValue(f"target_{i}_nature", -2)
            s.setValue(f"target_{i}_ability", sf.ability)
            s.setValue(f"target_{i}_gender", sf.gender)
            s.setValue(f"target_{i}_height_min", sf.height_min)
            s.setValue(f"target_{i}_height_max", sf.height_max)
            s.setValue(f"target_{i}_weight_min", sf.weight_min)
            s.setValue(f"target_{i}_weight_max", sf.weight_max)
            s.setValue(f"target_{i}_skip", sf.skip)

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
        # 目标精灵设置恢复
        if s.contains("target_count"):
            from auto_bdsp_rng.data import get_static_encounters
            from auto_bdsp_rng.gen8_static import StateFilter
            count = int(s.value("target_count", 0))
            self._targets = []
            all_records = {r.description: r for r in get_static_encounters()}
            for i in range(count):
                name = str(s.value(f"target_{i}_name", ""))
                sm = str(s.value(f"target_{i}_shiny", "any"))
                record = all_records.get(name)
                if record is None:
                    continue
                iv_min_raw = s.value(f"target_{i}_iv_min")
                iv_max_raw = s.value(f"target_{i}_iv_max")
                iv_min = tuple(int(v) for v in (iv_min_raw if isinstance(iv_min_raw, list) else [0]*6))
                iv_max = tuple(int(v) for v in (iv_max_raw if isinstance(iv_max_raw, list) else [31]*6))
                nature_idx = int(s.value(f"target_{i}_nature", -1))
                if 0 <= nature_idx < 25:
                    natures = tuple(j == nature_idx for j in range(25))
                else:
                    natures = (True,) * 25
                sf = StateFilter(
                    iv_min=iv_min, iv_max=iv_max, natures=natures,
                    ability=int(s.value(f"target_{i}_ability", 255)),
                    gender=int(s.value(f"target_{i}_gender", 255)),
                    height_min=int(s.value(f"target_{i}_height_min", 0)),
                    height_max=int(s.value(f"target_{i}_height_max", 255)),
                    weight_min=int(s.value(f"target_{i}_weight_min", 0)),
                    weight_max=int(s.value(f"target_{i}_weight_max", 255)),
                    skip=s.value(f"target_{i}_skip") == "true",
                )
                self._targets.append((record, sf, sm))
            if self._targets:
                names = [r.description for r, _, _ in self._targets]
                self.target_summary.setText(f"{len(self._targets)}个目标: {', '.join(names)}")

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


def _display_value(value: object) -> str:
    return "-" if value is None else str(value)
