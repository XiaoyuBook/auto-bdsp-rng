from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
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
    AUTO_ADVANCE_PARAMETER,
    AUTO_HIT_PARAMETER,
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
        layout = QGridLayout(self)
        fields = (
            ("帧数", "Adv"),
            ("EC", "EC"),
            ("PID", "PID"),
            ("异色", "Shiny"),
            ("性格", "Nature"),
            ("特性", "Ability"),
            ("性别", "Gender"),
            ("个性", "Characteristic"),
            ("IVs", "IVs"),
            ("身高 / 体重", "Height / Weight"),
            ("raw / trigger / delay", "raw / trigger / delay"),
            ("current / remaining / final", "current / remaining / final"),
        )
        for index, (label_text, key) in enumerate(fields):
            label = QLabel(label_text)
            label.setObjectName("MutedLabel")
            value = QLabel("-")
            value.setObjectName("Badge")
            value.setWordWrap(True)
            self.values[key] = value
            row = index // 4
            column = (index % 4) * 2
            layout.addWidget(label, row, column)
            layout.addWidget(value, row, column + 1)
        self.clear()

    def clear(self, status: str = "未锁定") -> None:
        for key, label in self.values.items():
            label.setText(status if key == "Adv" else "-")

    def apply_progress(self, progress: AutoRngProgress) -> None:
        target = progress.locked_target
        if target is None:
            self.clear()
            return
        state = target.state
        raw = _display_value(progress.raw_target_advances or target.raw_target_advances)
        trigger = _display_value(progress.trigger_advances)
        delay = _display_value(progress.fixed_delay)
        current = _display_value(progress.current_advances)
        remaining = _display_value(progress.remaining_to_trigger)
        final = _display_value(progress.final_flash_frames)
        self.values["raw / trigger / delay"].setText(f"{raw} / {trigger} / {delay}")
        self.values["current / remaining / final"].setText(f"{current} / {remaining} / {final}")
        if state is None:
            self.values["Adv"].setText(str(target.raw_target_advances))
            return
        self.values["Adv"].setText(str(getattr(state, "advances", target.raw_target_advances)))
        self.values["EC"].setText(_hex32(getattr(state, "ec", None)))
        self.values["PID"].setText(_hex32(getattr(state, "pid", None)))
        self.values["Shiny"].setText(_shiny_text(getattr(state, "shiny", None)))
        self.values["Nature"].setText(_nature_text(getattr(state, "nature", None)))
        self.values["Ability"].setText(_display_value(getattr(state, "ability", None)))
        self.values["Gender"].setText(_gender_text(getattr(state, "gender", None)))
        ivs = getattr(state, "ivs", None)
        self.values["IVs"].setText(_ivs_text(ivs))
        self.values["Height / Weight"].setText(f"{_display_value(getattr(state, 'height', None))} / {_display_value(getattr(state, 'weight', None))}")
        self.values["Characteristic"].setText(_characteristic_text(state))


class AutoRngWorker(QObject):
    progressChanged = Signal(object)
    logEmitted = Signal(str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, runner: object) -> None:
        super().__init__()
        self.runner = runner
        setattr(self.runner, "progress_callback", self.progressChanged.emit)
        setattr(self.runner, "log_callback", self.logEmitted.emit)

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

    def __init__(self, parent: QWidget | None = None, script_dir: Path = SCRIPT_DIR) -> None:
        super().__init__(parent)
        self.script_dir = script_dir
        self._scripts: list[Path] = []
        self._runner_thread: QThread | None = None
        self._runner_worker: AutoRngWorker | None = None
        self._build_ui()
        self.refresh_scripts()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self._build_toolbar())

        splitter = QSplitter()
        splitter.addWidget(self._build_config_panel())
        splitter.addWidget(self._build_runtime_panel())
        splitter.setSizes([480, 900])
        layout.addWidget(splitter, 1)

    def _build_toolbar(self) -> QWidget:
        toolbar = QFrame()
        toolbar.setObjectName("EasyConToolbar")
        row = QHBoxLayout(toolbar)
        row.setContentsMargins(10, 8, 10, 8)
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
        self.start_button.clicked.connect(self._start_clicked)
        self.stop_button.clicked.connect(self.stopRequested.emit)
        row.addWidget(QLabel("运行模式"))
        row.addWidget(self.mode_combo)
        row.addWidget(QLabel("次数"))
        row.addWidget(self.loop_count)
        row.addStretch(1)
        row.addWidget(self.status_badge)
        row.addWidget(self.start_button)
        row.addWidget(self.stop_button)
        return toolbar

    def _build_config_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.addWidget(self._build_strategy_group())
        layout.addWidget(self._build_script_group())
        layout.addWidget(self._build_log_group(), 1)
        return panel

    def _build_strategy_group(self) -> QGroupBox:
        group = QGroupBox("自动策略")
        form = QFormLayout(group)
        self.max_advances = self._spin(0, 1_000_000_000, 100_000)
        self.fixed_delay = self._spin(0, 1_000_000_000, 100)
        self.max_wait_frames = self._spin(1, 1_000_000_000, 300)
        form.addRow("最大帧数", self.max_advances)
        form.addRow("delay", self.fixed_delay)
        form.addRow("最大等待帧数", self.max_wait_frames)
        return group

    def _build_script_group(self) -> QGroupBox:
        group = QGroupBox("脚本")
        layout = QGridLayout(group)
        self.seed_script_combo = QComboBox()
        self.advance_script_combo = QComboBox()
        self.hit_script_combo = QComboBox()
        self.refresh_scripts_button = QPushButton("刷新脚本列表")
        self.refresh_scripts_button.clicked.connect(self.refresh_scripts)
        self.preview_button = QPushButton("参数预览")
        self.preview_button.clicked.connect(self.update_parameter_preview)
        self.parameter_preview = QPlainTextEdit()
        self.parameter_preview.setReadOnly(True)
        self.parameter_preview.setMaximumHeight(130)
        self.parameter_preview.setVisible(False)
        layout.addWidget(QLabel("测种脚本"), 0, 0)
        layout.addWidget(self.seed_script_combo, 0, 1)
        layout.addWidget(QLabel("过帧脚本"), 1, 0)
        layout.addWidget(self.advance_script_combo, 1, 1)
        layout.addWidget(QLabel("撞闪脚本"), 2, 0)
        layout.addWidget(self.hit_script_combo, 2, 1)
        layout.addWidget(self.refresh_scripts_button, 3, 0)
        layout.addWidget(self.preview_button, 3, 1)
        return group

    def _build_runtime_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        top = QWidget()
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(8)
        top_layout.addWidget(self._build_summary_group(), 2)
        self.locked_target_view = LockedTargetView()
        top_layout.addWidget(self.locked_target_view, 3)
        layout.addWidget(top)
        layout.addWidget(self._build_target_form_group(), 1)
        return panel

    def _build_summary_group(self) -> QGroupBox:
        group = QGroupBox("运行摘要")
        grid = QGridLayout(group)
        fields = (
            ("当前循环", "summary_loop"),
            ("当前阶段", "summary_phase"),
            ("Seed", "summary_seed"),
            ("原始目标帧", "summary_raw"),
            ("delay", "summary_delay"),
            ("触发帧", "summary_trigger"),
            ("当前帧", "summary_current"),
            ("剩余", "summary_remaining"),
            ("最终闪帧", "summary_flash"),
        )
        for row, (label_text, attr) in enumerate(fields):
            label = QLabel("-")
            label.setObjectName("Badge")
            setattr(self, attr, label)
            grid.addWidget(QLabel(label_text), row // 2, (row % 2) * 2)
            grid.addWidget(label, row // 2, (row % 2) * 2 + 1)
        return group

    def _build_target_form_group(self) -> QGroupBox:
        group = QGroupBox("目标精灵设置")
        layout = QVBoxLayout(group)
        self.target_form = StaticTargetForm(self)
        self.target_form.iv_calculator_button.clicked.connect(self.ivCalculatorRequested.emit)
        layout.addWidget(self.target_form)
        return group

    def _build_log_group(self) -> QGroupBox:
        group = QGroupBox("日志")
        layout = QVBoxLayout(group)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        layout.addWidget(self.log_view)
        return group

    def refresh_scripts(self) -> None:
        self._scripts = list_auto_scripts(self.script_dir)
        for combo in (self.seed_script_combo, self.advance_script_combo, self.hit_script_combo):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("请选择", None)
            for path in self._scripts:
                combo.addItem(path.name, str(path))
            combo.blockSignals(False)
        self._select_script(self.seed_script_combo, choose_default_script(self._scripts, DEFAULT_SEED_SCRIPT_NAME))
        self._select_script(self.advance_script_combo, choose_default_script(self._scripts, DEFAULT_ADVANCE_SCRIPT_NAME))
        self.update_parameter_preview()

    def update_parameter_preview(self) -> None:
        lines = [
            f"脚本目录: {self.script_dir}",
            f"过帧必需参数: {AUTO_ADVANCE_PARAMETER}",
            f"撞闪必需参数: {AUTO_HIT_PARAMETER}",
        ]
        try:
            validate_auto_scripts(self._selected_path(self.seed_script_combo), self._selected_path(self.advance_script_combo), self._selected_path(self.hit_script_combo))
        except AutoScriptError as exc:
            lines.append(f"校验: {exc}")
        else:
            lines.append("校验: 通过")
        self.parameter_preview.setPlainText("\n".join(lines))
        if hasattr(self, "log_view"):
            self.add_log("\n".join(lines))

    def set_phase_text(self, text: str) -> None:
        self.status_badge.setText(text)
        self.summary_phase.setText(text)

    def apply_progress(self, progress: AutoRngProgress) -> None:
        phase_text = progress.phase.value if hasattr(progress.phase, "value") else str(progress.phase)
        self.status_badge.setText(phase_text)
        self.summary_phase.setText(phase_text)
        self.summary_loop.setText(_display_value(progress.loop_index))
        self.summary_seed.setText(progress.seed_text or "-")
        self.summary_raw.setText(_display_value(progress.raw_target_advances))
        self.summary_delay.setText(_display_value(progress.fixed_delay))
        self.summary_trigger.setText(_display_value(progress.trigger_advances))
        self.summary_current.setText(_display_value(progress.current_advances))
        self.summary_remaining.setText(_display_value(progress.remaining_to_trigger))
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
                    f"存档信息: {profile or '-'}",
                    f"个体筛选: {filters or '-'}",
                    f"Seed: {seed or '-'}",
                    f"最大帧数: {max_advances}",
                )
            )
        )

    def _start_clicked(self) -> None:
        self.update_parameter_preview()
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
        self.stopRequested.connect(worker.stop)
        self._runner_thread = thread
        self._runner_worker = worker
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        thread.start()

    def _runner_finished(self, progress: object) -> None:
        if isinstance(progress, AutoRngProgress):
            self.apply_progress(progress)
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

    def _spin(self, minimum: int, maximum: int, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
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


def _characteristic_text(state: object) -> str:
    ivs = getattr(state, "ivs", None)
    pid = getattr(state, "pid", None)
    if ivs is None or pid is None:
        return "-"
    values = tuple(int(value) for value in ivs)
    if len(values) != 6:
        return "-"
    zh = (
        ("非常喜欢吃", "经常打瞌睡", "经常午睡", "经常乱扔东西", "喜欢放松"),
        ("以力量自豪", "喜欢打闹", "有点易怒", "喜欢打架", "血气方刚"),
        ("身体强壮", "能忍耐", "抗打能力强", "不屈不挠", "毅力十足"),
        ("好奇心强", "爱恶作剧", "考虑周到", "经常思考", "非常讲究"),
        ("意志坚强", "有点固执", "讨厌输", "有点爱逞强", "忍耐力强"),
        ("喜欢跑步", "警觉性高", "冲动", "有点轻浮", "逃得快"),
    )
    max_iv = max(values)
    start = int(pid) % 6
    stat_index = next(index for offset in range(6) for index in ((start + offset) % 6,) if values[index] == max_iv)
    return zh[stat_index][max_iv % 5]

