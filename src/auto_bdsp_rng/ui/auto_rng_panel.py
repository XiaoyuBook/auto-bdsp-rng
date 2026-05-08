from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
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
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from auto_bdsp_rng.automation.auto_rng.models import AutoRngConfig
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


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = PROJECT_ROOT / "script"


class AutoRngPanel(QWidget):
    startRequested = Signal(object)
    stopRequested = Signal()

    def __init__(self, parent: QWidget | None = None, script_dir: Path = SCRIPT_DIR) -> None:
        super().__init__(parent)
        self.script_dir = script_dir
        self._scripts: list[Path] = []
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
        layout.addWidget(self._build_target_group())
        layout.addStretch(1)
        return panel

    def _build_strategy_group(self) -> QGroupBox:
        group = QGroupBox("自动策略")
        form = QFormLayout(group)
        self.max_advances = self._spin(0, 1_000_000_000, 100_000)
        self.fixed_delay = self._spin(0, 1_000_000_000, 100)
        self.max_wait_frames = self._spin(1, 1_000_000_000, 300)
        self.reseed_threshold_frames = self._spin(1, 1_000_000_000, 1_000_000)
        self.min_final_flash_frames = self._spin(1, 1_000_000, 30)
        form.addRow("最大帧数", self.max_advances)
        form.addRow("fixed delay", self.fixed_delay)
        form.addRow("max_wait_frames", self.max_wait_frames)
        form.addRow("reseed_threshold_frames", self.reseed_threshold_frames)
        form.addRow("min_final_flash_frames", self.min_final_flash_frames)
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
        layout.addWidget(QLabel("测种脚本"), 0, 0)
        layout.addWidget(self.seed_script_combo, 0, 1)
        layout.addWidget(QLabel("过帧脚本"), 1, 0)
        layout.addWidget(self.advance_script_combo, 1, 1)
        layout.addWidget(QLabel("撞闪脚本"), 2, 0)
        layout.addWidget(self.hit_script_combo, 2, 1)
        layout.addWidget(self.refresh_scripts_button, 3, 0)
        layout.addWidget(self.preview_button, 3, 1)
        layout.addWidget(self.parameter_preview, 4, 0, 1, 2)
        return group

    def _build_target_group(self) -> QGroupBox:
        group = QGroupBox("定点目标 / 存档信息 / 个体筛选")
        layout = QVBoxLayout(group)
        label = QLabel("第一版复用 BDSP 定点搜索服务；筛选项接入在 runner 真机联调前继续补齐。")
        label.setWordWrap(True)
        layout.addWidget(label)
        return group

    def _build_runtime_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._build_summary_group())
        layout.addWidget(self._build_candidates_group(), 1)
        layout.addWidget(self._build_log_group(), 1)
        return panel

    def _build_summary_group(self) -> QGroupBox:
        group = QGroupBox("运行摘要")
        grid = QGridLayout(group)
        fields = (
            ("当前循环", "summary_loop"),
            ("当前阶段", "summary_phase"),
            ("seed", "summary_seed"),
            ("锁定目标", "summary_target"),
            ("raw target", "summary_raw"),
            ("fixed delay", "summary_delay"),
            ("trigger advances", "summary_trigger"),
            ("current advances", "summary_current"),
            ("remaining_to_trigger", "summary_remaining"),
            ("final flash_frames", "summary_flash"),
        )
        for row, (label_text, attr) in enumerate(fields):
            label = QLabel("-")
            label.setObjectName("Badge")
            setattr(self, attr, label)
            grid.addWidget(QLabel(label_text), row // 2, (row % 2) * 2)
            grid.addWidget(label, row // 2, (row % 2) * 2 + 1)
        return group

    def _build_candidates_group(self) -> QGroupBox:
        group = QGroupBox("候选结果")
        layout = QVBoxLayout(group)
        self.candidate_table = QTableWidget(0, 5)
        self.candidate_table.setHorizontalHeaderLabels(["锁定", "Adv", "PID", "Shiny", "Nature"])
        self.candidate_table.verticalHeader().setVisible(False)
        layout.addWidget(self.candidate_table)
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

    def set_phase_text(self, text: str) -> None:
        self.status_badge.setText(text)
        self.summary_phase.setText(text)

    def add_log(self, message: str) -> None:
        self.log_view.appendPlainText(message)

    def set_candidates(self, rows: list[list[str]], locked_index: int | None = None) -> None:
        self.candidate_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            values = [("当前锁定" if row_index == locked_index else ""), *row]
            for column, value in enumerate(values[: self.candidate_table.columnCount()]):
                self.candidate_table.setItem(row_index, column, QTableWidgetItem(value))

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
            reseed_threshold_frames=self.reseed_threshold_frames.value(),
            min_final_flash_frames=self.min_final_flash_frames.value(),
            loop_mode=str(self.mode_combo.currentData()),
            loop_count=self.loop_count.value(),
            max_advances=self.max_advances.value(),
        )

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
