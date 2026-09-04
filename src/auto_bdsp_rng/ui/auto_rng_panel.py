from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from auto_bdsp_rng.automation.auto_rng.delay_strategy import (
    DelayEstimate,
    DelaySampleRound,
    DelayStrategy,
    DelayStrategyConfig,
    MultiCandidatePolicy,
    estimate_delay,
)
from auto_bdsp_rng.automation.auto_rng.models import AutoRngConfig, AutoRngPhase, AutoRngProgress
from auto_bdsp_rng.automation.auto_rng.scripts import (
    DEFAULT_ADVANCE_SCRIPT_NAME,
    DEFAULT_RECORD_SCRIPT_NAME,
    DEFAULT_SEED_SCRIPT_NAME,
    AutoScriptError,
    choose_default_script,
    list_auto_scripts,
    validate_auto_scripts,
)
from auto_bdsp_rng.data import GameVersion, StaticEncounterRecord, get_static_encounters
from auto_bdsp_rng.gen8_static import StateFilter
from auto_bdsp_rng.resources import remap_legacy_script_path, script_directory
from auto_bdsp_rng.ui.numeric_locale import set_c_locale
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


class _RefreshingScriptComboBox(QComboBox):
    def __init__(self, before_popup: Callable[[], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._before_popup = before_popup

    def showPopup(self) -> None:  # noqa: N802
        self._before_popup()
        super().showPopup()


SCRIPT_DIR = script_directory()
DEFAULT_SHINY_THRESHOLD_SECONDS = 4.0
DEFAULT_RESEED_THRESHOLD_FRAMES = 900_000
DEFAULT_RESEEDING_THRESHOLD_FRAMES = 500_000
DEFAULT_REIDENTIFY_MAX_ATTEMPTS = 2
DEFAULT_REIDENTIFY_FAILURE_POLICY = "next_round"
DEFAULT_REIDENTIFY_SEED_MAX_ATTEMPTS = 1
QT_INT_MAX = 2_147_483_647
_TIMESTAMP_RE = re.compile(r"^\[\d{2}:\d{2}:\d{2}\]\s*")

DELAY_STRATEGY_LABELS = (
    (DelayStrategy.FIXED, "固定 delay"),
    (DelayStrategy.LAST, "上次实际 delay"),
    (DelayStrategy.MODE, "众数"),
    (DelayStrategy.MEDIAN, "中位数"),
    (DelayStrategy.ROLLING_MEAN, "滚动平均值"),
    (DelayStrategy.EWMA, "指数平滑"),
    (DelayStrategy.TRIMMED_MEAN, "截尾平均"),
    (DelayStrategy.DENSE_INTERVAL, "密集区间"),
)
DELAY_STRATEGY_LABEL_BY_ID = {strategy.value: label for strategy, label in DELAY_STRATEGY_LABELS}
DELAY_STRATEGY_TOOLTIPS = {
    DelayStrategy.FIXED: "始终使用基准 delay；结果最稳定，不会随反查样本自动调整。",
    DelayStrategy.LAST: (
        "使用最近一次唯一候选；多候选轮次一律跳过。响应最快，但容易受单次误差影响；"
        "没有单候选样本时使用基准 delay。"
    ),
    DelayStrategy.MODE: (
        "取最近 N 轮累计权重最高的 delay，适合实际值长期稳定在同一帧附近；"
        "没有有效样本时使用基准 delay。"
    ),
    DelayStrategy.MEDIAN: (
        "取最近 N 轮的加权中位数，能抵抗偶发异常值，通常比平均值稳健；"
        "没有有效样本时使用基准 delay。"
    ),
    DelayStrategy.ROLLING_MEAN: (
        "取最近 N 轮的按轮加权平均值，变化平滑，但容易被异常值拉偏；"
        "没有有效样本时使用基准 delay。"
    ),
    DelayStrategy.EWMA: (
        "从基准值开始逐轮融合；权重越高越跟随最新结果，适合缓慢漂移；"
        "没有有效样本时使用基准 delay。"
    ),
    DelayStrategy.TRIMMED_MEAN: (
        "去掉最近 N 轮两端各一轮权重后求平均，能减弱异常值影响；"
        "不足 5 个有效轮次时使用滚动平均，没有样本时使用基准 delay。"
    ),
    DelayStrategy.DENSE_INTERVAL: (
        "寻找跨度内权重最集中的候选群并取加权中位数，适合 1451/1452/1453 这类相邻帧抖动；"
        "没有有效样本时使用基准 delay。"
    ),
}


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
        if isinstance(result, AutoRngProgress) and result.phase == AutoRngPhase.FAILED:
            self.failed.emit(result.log_message)
            return
        self.finished.emit(result)

    @Slot()
    def stop(self) -> None:
        stop = getattr(self.runner, "stop", None)
        if callable(stop):
            stop()


class AutoRngStrategyDialog(QDialog):
    """Edit correction-related settings as one transactional unit."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("校正策略设置")
        self.setMinimumWidth(470)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 14)
        layout.setSpacing(16)
        self.form = QFormLayout()
        self.form.setHorizontalSpacing(18)
        self.form.setVerticalSpacing(12)

        self.reseed_threshold_frames = self._spin(0, DEFAULT_RESEED_THRESHOLD_FRAMES)
        self.reidentify_max_attempts = self._spin(1, DEFAULT_REIDENTIFY_MAX_ATTEMPTS)
        self.reidentify_failure_policy = QComboBox()
        self.reidentify_failure_policy.addItem("进入下一轮", "next_round")
        self.reidentify_failure_policy.addItem("先重测 Seed", "recapture_seed")
        self.reidentify_failure_policy.setFixedSize(215, 34)
        self.reidentify_seed_max_attempts = self._spin(1, DEFAULT_REIDENTIFY_SEED_MAX_ATTEMPTS)
        self.reseeding_threshold = self._spin(0, DEFAULT_RESEEDING_THRESHOLD_FRAMES)

        rows = (
            (
                "校正帧数上限",
                self.reseed_threshold_frames,
                "普通流程中，本次过帧量不超过该值时执行校正；超过该值时重新捕获 Seed。\n"
                "过场脚本运行后若超过该值，会直接进入下一轮，不会原地重测 Seed。",
            ),
            (
                "普通校正最大尝试次数",
                self.reidentify_max_attempts,
                "普通校正达到该尝试次数仍未成功后，执行所选失败策略。",
            ),
            (
                "普通校正连续失败后",
                self.reidentify_failure_policy,
                "仅影响普通校正；过场校正失败后始终进入下一轮，不会重测 Seed。",
            ),
            (
                "重测 Seed 最大尝试次数",
                self.reidentify_seed_max_attempts,
                "可随时预先设置；仅在失败策略为“先重测 Seed”时生效。",
            ),
            (
                "过场预留帧数",
                self.reseeding_threshold,
                "仅在选择了过场脚本时生效；设为 0 时关闭过场策略。",
            ),
        )
        for label_text, field, tooltip in rows:
            self.form.addRow(label_text, field)
            field.setToolTip(tooltip)
            label = self.form.labelForField(field)
            if label is not None:
                label.setToolTip(tooltip)
        layout.addLayout(self.form)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.RestoreDefaults
            | QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.restore_defaults_button = self.button_box.button(QDialogButtonBox.StandardButton.RestoreDefaults)
        self.ok_button = self.button_box.button(QDialogButtonBox.StandardButton.Ok)
        self.cancel_button = self.button_box.button(QDialogButtonBox.StandardButton.Cancel)
        self.restore_defaults_button.setText("恢复默认值")
        self.ok_button.setText("确定")
        self.cancel_button.setText("取消")
        self.ok_button.setObjectName("PrimaryButton")
        self.restore_defaults_button.clicked.connect(self.restore_defaults)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

    @staticmethod
    def _spin(minimum: int, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, QT_INT_MAX)
        spin.setValue(value)
        spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        spin.setFixedSize(215, 34)
        set_c_locale(spin)
        return spin

    def policy(self) -> str:
        return str(self.reidentify_failure_policy.currentData())

    def set_policy(self, policy: str) -> None:
        index = self.reidentify_failure_policy.findData(policy)
        self.reidentify_failure_policy.setCurrentIndex(index if index >= 0 else 0)

    def values(self) -> tuple[int, int, str, int, int]:
        return (
            self.reseed_threshold_frames.value(),
            self.reidentify_max_attempts.value(),
            self.policy(),
            self.reidentify_seed_max_attempts.value(),
            self.reseeding_threshold.value(),
        )

    def set_values(
        self,
        reseed_threshold_frames: int,
        reidentify_max_attempts: int,
        reidentify_failure_policy: str,
        reidentify_seed_max_attempts: int,
        reseeding_threshold: int,
    ) -> None:
        self.reseed_threshold_frames.setValue(reseed_threshold_frames)
        self.reidentify_max_attempts.setValue(reidentify_max_attempts)
        self.set_policy(reidentify_failure_policy)
        self.reidentify_seed_max_attempts.setValue(reidentify_seed_max_attempts)
        self.reseeding_threshold.setValue(reseeding_threshold)

    @Slot()
    def restore_defaults(self) -> None:
        self.set_values(
            DEFAULT_RESEED_THRESHOLD_FRAMES,
            DEFAULT_REIDENTIFY_MAX_ATTEMPTS,
            DEFAULT_REIDENTIFY_FAILURE_POLICY,
            DEFAULT_REIDENTIFY_SEED_MAX_ATTEMPTS,
            DEFAULT_RESEEDING_THRESHOLD_FRAMES,
        )


class DelayStrategyDialog(QDialog):
    """Edit the delay estimator without changing the active round."""

    settingsEdited = Signal()
    clearSamplesRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DelayStrategyDialog")
        self.setWindowTitle("delay 策略设置")
        self.setMinimumWidth(520)
        self.setStyleSheet(
            "QDialog#DelayStrategyDialog QComboBox,"
            " QDialog#DelayStrategyDialog QSpinBox,"
            " QDialog#DelayStrategyDialog QLineEdit { min-height: 34px; max-height: 34px;"
            " padding-top: 0; padding-bottom: 0; }"
            " QDialog#DelayStrategyDialog QDialogButtonBox QPushButton { min-height: 34px;"
            " max-height: 34px; padding-top: 0; padding-bottom: 0; }"
            " QDialog#DelayStrategyDialog QToolButton#DelayClearSamplesButton { min-width: 34px;"
            " max-width: 34px; min-height: 34px; max-height: 34px; }"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 14)
        layout.setSpacing(14)

        self.form = QFormLayout()
        self.form.setHorizontalSpacing(18)
        self.form.setVerticalSpacing(10)

        self.strategy_combo = QComboBox()
        for strategy, label in DELAY_STRATEGY_LABELS:
            self.strategy_combo.addItem(label, strategy.value)
        self.strategy_combo.setFixedSize(250, 34)
        self.strategy_description = QLabel()
        self.strategy_description.setObjectName("DelayStrategyDescription")
        self.strategy_description.setWordWrap(True)
        self.strategy_description.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self.strategy_description.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        self.strategy_description.setStyleSheet(
            "color: palette(window-text); font-size: 12px;"
        )

        self.baseline_delay = self._spin(0, DelayStrategyConfig().baseline_delay)
        self.window_size = self._spin(1, DelayStrategyConfig().window_size)
        self.ewma_weight_percent = self._spin(
            1,
            round(DelayStrategyConfig().ewma_alpha * 100),
            maximum=100,
        )
        self.dense_interval_width = self._spin(0, DelayStrategyConfig().dense_interval_width)

        self.multi_candidate_widget = QFrame()
        self.multi_candidate_widget.setObjectName("DelayCandidatePolicy")
        self.multi_candidate_widget.setFixedSize(250, 34)
        candidate_row = QHBoxLayout(self.multi_candidate_widget)
        candidate_row.setContentsMargins(1, 1, 1, 1)
        candidate_row.setSpacing(0)
        self.multi_candidate_group = QButtonGroup(self)
        self.multi_candidate_group.setExclusive(True)
        self.ignore_multi_candidate_button = QPushButton("忽略该轮")
        self.weight_multi_candidate_button = QPushButton("按轮加权")
        for button, policy in (
            (self.ignore_multi_candidate_button, MultiCandidatePolicy.IGNORE),
            (self.weight_multi_candidate_button, MultiCandidatePolicy.WEIGHTED),
        ):
            button.setCheckable(True)
            button.setProperty("delayPolicy", policy.value)
            button.setFixedHeight(32)
            candidate_row.addWidget(button, 1)
            self.multi_candidate_group.addButton(button)
        self.ignore_multi_candidate_button.setChecked(True)
        self.multi_candidate_widget.setStyleSheet(
            "QFrame#DelayCandidatePolicy { background: palette(base); border: 1px solid palette(mid);"
            " border-radius: 8px; }"
            " QFrame#DelayCandidatePolicy QPushButton { background: transparent; border: 0; border-radius: 6px;"
            " min-height: 30px; max-height: 30px; padding: 0 8px; color: palette(window-text); }"
            " QFrame#DelayCandidatePolicy QPushButton:hover { background: palette(alternate-base); }"
            " QFrame#DelayCandidatePolicy QPushButton:checked { background: #10A37F; color: #FFFFFF; }"
        )

        rows = (
            (
                "delay 策略",
                self.strategy_combo,
                "选择下一轮使用的 delay 计算方式；本轮已经锁定的值不会改变。",
            ),
            (
                "基准 delay（帧）",
                self.baseline_delay,
                "固定策略直接使用此值；动态策略没有有效样本时回退到此值。",
            ),
            (
                "多候选轮次",
                self.multi_candidate_widget,
                "忽略该轮：一轮得到多个候选时不计入统计。\n"
                "按轮加权：每轮总权重为 1，由本轮所有候选平均分配。",
            ),
            (
                "统计窗口（轮）",
                self.window_size,
                "使用最近 N 个有效轮次；上次实际 delay 策略始终只看最近一轮。",
            ),
            (
                "指数平滑权重（%）",
                self.ewma_weight_percent,
                "新一轮实际 delay 在指数平滑结果中所占的比例。",
            ),
            (
                "密集区间跨度（帧）",
                self.dense_interval_width,
                "密集区间允许的最大候选跨度；设为 2 可覆盖 1451、1452、1453。",
            ),
        )
        for label_text, field, tooltip in rows:
            self.form.addRow(label_text, field)
            field.setToolTip(tooltip)
            label = self.form.labelForField(field)
            if label is not None:
                label.setToolTip(tooltip)
            if field is self.strategy_combo:
                self.form.addRow(self.strategy_description)
        layout.addLayout(self.form)

        runtime_title = QLabel("运行状态")
        runtime_title.setObjectName("DelaySectionTitle")
        runtime_title.setStyleSheet("font-weight: 600;")
        layout.addWidget(runtime_title)
        self.runtime_summary = QFrame()
        self.runtime_summary.setObjectName("DelayRuntimeSummary")
        runtime_row = QHBoxLayout(self.runtime_summary)
        runtime_row.setContentsMargins(14, 10, 14, 10)
        runtime_row.setSpacing(12)
        self.current_delay_value = QLabel("-")
        self.next_delay_value = QLabel(str(DelayStrategyConfig().baseline_delay))
        self.valid_sample_count = QLabel("0 轮")
        for index, (caption, value_label) in enumerate(
            (
                ("本次使用", self.current_delay_value),
                ("下轮预计", self.next_delay_value),
                ("有效样本", self.valid_sample_count),
            )
        ):
            column = QVBoxLayout()
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(3)
            caption_label = QLabel(caption)
            caption_label.setObjectName("DelayRuntimeCaption")
            value_label.setObjectName("DelayRuntimeValue")
            column.addWidget(caption_label)
            column.addWidget(value_label)
            runtime_row.addLayout(column, 1)
            if index < 2:
                divider = QFrame()
                divider.setObjectName("DelayRuntimeDivider")
                divider.setFixedWidth(1)
                runtime_row.addWidget(divider)
        self.runtime_summary.setStyleSheet(
            "QFrame#DelayRuntimeSummary { background: palette(alternate-base); border: 1px solid palette(mid);"
            " border-radius: 8px; }"
            " QLabel#DelayRuntimeCaption { color: palette(window-text); font-size: 12px; }"
            " QLabel#DelayRuntimeValue { color: palette(window-text); font-size: 16px; font-weight: 700; }"
            " QFrame#DelayRuntimeDivider { background: palette(mid); border: 0; }"
        )
        layout.addWidget(self.runtime_summary)

        recent_row = QHBoxLayout()
        recent_row.setSpacing(8)
        recent_label = QLabel("最近样本")
        recent_label.setFixedWidth(138)
        self.recent_samples = QLineEdit("暂无样本")
        self.recent_samples.setReadOnly(True)
        self.recent_samples.setFixedHeight(34)
        self.clear_samples_button = QToolButton()
        self.clear_samples_button.setObjectName("DelayClearSamplesButton")
        self.clear_samples_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon))
        self.clear_samples_button.setFixedSize(34, 34)
        self.clear_samples_button.setToolTip("立即清空统计样本")
        recent_row.addWidget(recent_label)
        recent_row.addWidget(self.recent_samples, 1)
        recent_row.addWidget(self.clear_samples_button)
        layout.addLayout(recent_row)

        self.apply_status = QLabel("设置确认后从下一轮开始使用")
        self.apply_status.setObjectName("DelayApplyStatus")
        self.apply_status.setWordWrap(True)
        self.apply_status.setStyleSheet("color: palette(window-text); font-size: 12px;")
        layout.addWidget(self.apply_status)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.RestoreDefaults
            | QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.restore_defaults_button = self.button_box.button(QDialogButtonBox.StandardButton.RestoreDefaults)
        self.ok_button = self.button_box.button(QDialogButtonBox.StandardButton.Ok)
        self.cancel_button = self.button_box.button(QDialogButtonBox.StandardButton.Cancel)
        self.restore_defaults_button.setText("恢复默认值")
        self.ok_button.setText("确定")
        self.cancel_button.setText("取消")
        self.ok_button.setObjectName("PrimaryButton")
        layout.addWidget(self.button_box)

        self.strategy_combo.currentIndexChanged.connect(self._strategy_changed)
        for spin in (
            self.baseline_delay,
            self.window_size,
            self.ewma_weight_percent,
            self.dense_interval_width,
        ):
            spin.valueChanged.connect(lambda _value: self.settingsEdited.emit())
        self.multi_candidate_group.buttonClicked.connect(lambda _button: self.settingsEdited.emit())
        self.clear_samples_button.clicked.connect(self.clearSamplesRequested.emit)
        self.restore_defaults_button.clicked.connect(self.restore_defaults)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        self._update_strategy_rows()

    @staticmethod
    def _spin(minimum: int, value: int, *, maximum: int = QT_INT_MAX) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        spin.setFixedSize(250, 34)
        set_c_locale(spin)
        return spin

    def values(self) -> DelayStrategyConfig:
        checked_button = self.multi_candidate_group.checkedButton()
        policy = (
            str(checked_button.property("delayPolicy"))
            if checked_button is not None
            else MultiCandidatePolicy.IGNORE.value
        )
        return DelayStrategyConfig(
            strategy=str(self.strategy_combo.currentData()),
            baseline_delay=self.baseline_delay.value(),
            multi_candidate_policy=policy,
            window_size=self.window_size.value(),
            ewma_alpha=self.ewma_weight_percent.value() / 100.0,
            dense_interval_width=self.dense_interval_width.value(),
        )

    def set_values(self, config: DelayStrategyConfig) -> None:
        controls = (
            self.strategy_combo,
            self.baseline_delay,
            self.window_size,
            self.ewma_weight_percent,
            self.dense_interval_width,
            self.ignore_multi_candidate_button,
            self.weight_multi_candidate_button,
        )
        for control in controls:
            control.blockSignals(True)
        try:
            strategy_id = config.strategy.value
            index = self.strategy_combo.findData(strategy_id)
            self.strategy_combo.setCurrentIndex(index if index >= 0 else 0)
            self.baseline_delay.setValue(config.baseline_delay)
            self.window_size.setValue(config.window_size)
            self.ewma_weight_percent.setValue(round(config.ewma_alpha * 100))
            self.dense_interval_width.setValue(config.dense_interval_width)
            policy_buttons = {
                MultiCandidatePolicy.IGNORE: self.ignore_multi_candidate_button,
                MultiCandidatePolicy.WEIGHTED: self.weight_multi_candidate_button,
            }
            policy_buttons[config.multi_candidate_policy].setChecked(True)
        finally:
            for control in controls:
                control.blockSignals(False)
        self._update_strategy_rows()
        self.settingsEdited.emit()

    @Slot()
    def restore_defaults(self) -> None:
        self.set_values(DelayStrategyConfig())

    @Slot(int)
    def _strategy_changed(self, _index: int) -> None:
        self._update_strategy_rows()
        self.settingsEdited.emit()

    def _update_strategy_rows(self) -> None:
        strategy = DelayStrategy(str(self.strategy_combo.currentData()))
        strategy_tooltip = DELAY_STRATEGY_TOOLTIPS[strategy]
        self.strategy_description.setMinimumHeight(0)
        self.strategy_description.setText(strategy_tooltip)
        self.strategy_description.updateGeometry()
        self.strategy_combo.setToolTip(strategy_tooltip)
        strategy_label = self.form.labelForField(self.strategy_combo)
        if strategy_label is not None:
            strategy_label.setToolTip(strategy_tooltip)
        self._set_form_row_visible(
            self.multi_candidate_widget,
            strategy not in (DelayStrategy.FIXED, DelayStrategy.LAST),
        )
        self._set_form_row_visible(
            self.window_size,
            strategy not in (DelayStrategy.FIXED, DelayStrategy.LAST),
        )
        self._set_form_row_visible(self.ewma_weight_percent, strategy is DelayStrategy.EWMA)
        self._set_form_row_visible(self.dense_interval_width, strategy is DelayStrategy.DENSE_INTERVAL)
        self._fit_strategy_description()

    def _fit_strategy_description(self) -> None:
        description_width = self.strategy_description.width() if self.isVisible() else 0
        if description_width <= 0:
            left, _top, right, _bottom = self.layout().getContentsMargins()
            description_width = max(1, self.minimumWidth() - left - right)
        required_height = self.strategy_description.heightForWidth(description_width)
        if required_height >= 0:
            self.strategy_description.setMinimumHeight(required_height + 1)
        self.strategy_description.updateGeometry()
        self.form.invalidate()
        self.layout().activate()
        self.adjustSize()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        QTimer.singleShot(0, self._fit_strategy_description)

    def _set_form_row_visible(self, field: QWidget, visible: bool) -> None:
        field.setVisible(visible)
        label = self.form.labelForField(field)
        if label is not None:
            label.setVisible(visible)

    def set_runtime_state(
        self,
        *,
        active_delay: int | None,
        estimate: DelayEstimate,
        sample_rounds: list[tuple[int, ...]],
    ) -> None:
        self.current_delay_value.setText("-" if active_delay is None else str(active_delay))
        self.next_delay_value.setText(str(estimate.value))
        self.valid_sample_count.setText(f"{estimate.valid_round_count} 轮")
        recent_text = " / ".join(self._format_sample_round(row) for row in sample_rounds[-5:])
        self.recent_samples.setText(recent_text or "暂无样本")
        self.recent_samples.setToolTip(recent_text)
        self.clear_samples_button.setEnabled(bool(sample_rounds))
        if active_delay is None:
            self.apply_status.setText("设置确认后从下一轮开始使用")
        else:
            self.apply_status.setText(f"当前目标继续使用 {active_delay}；新设置从下一轮生效")

    @staticmethod
    def _format_sample_round(candidates: tuple[int, ...]) -> str:
        if len(candidates) <= 1:
            return str(candidates[0]) if candidates else "-"
        if candidates == tuple(range(candidates[0], candidates[-1] + 1)):
            return f"{candidates[0]}~{candidates[-1]}"
        return ",".join(str(value) for value in candidates)


class AutoRngPanel(QWidget):
    startRequested = Signal(object)
    stopRequested = Signal()
    autoProgressChanged = Signal(object)
    runStateChanged = Signal(bool)
    runLogRequested = Signal()
    ivCalculatorRequested = Signal()
    captureInfoRequested = Signal()  # 临时：手动触发精灵信息捕获
    captureLog = Signal(str)  # 临时：后台线程日志输出
    captureError = Signal(str)  # 临时：后台线程错误日志输出
    requestStatsCapture = Signal(object, object)  # 临时：后台请求主线程截图能力页(nature, characteristic)
    delayStrategyChanged = Signal(object)
    delaySamplesChanged = Signal(object)
    delaySamplesCleared = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        script_dir: Path = SCRIPT_DIR,
        settings: QSettings | None = None,
        run_log_sink: Callable[[str, str], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.script_dir = script_dir
        self._run_log_sink = run_log_sink
        self._scripts: list[Path] = []
        self._scripts_initialized = False
        self._runner_thread: QThread | None = None
        self._runner_worker: AutoRngWorker | None = None
        self._last_failed_progress_message: str | None = None
        self._target_version = GameVersion.BD
        self._targets: list[tuple[StaticEncounterRecord, StateFilter, str]] = []
        self._delay_strategy_config = DelayStrategyConfig()
        self._delay_sample_rounds: list[tuple[int, ...]] = []
        self._active_delay: int | None = None
        self._updating_fixed_delay = False
        self._settings = settings or QSettings("auto-bdsp-rng", "AutoRngPanel")
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
        content.setObjectName("AutoRngContent")
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
        self.content_grid.setRowStretch(0, 1)
        self.content_grid.setRowStretch(1, 0)

        layout.addWidget(content, 1)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._save_panel_state()
        super().closeEvent(event)

    def _build_toolbar(self) -> QWidget:
        toolbar = QFrame()
        toolbar.setObjectName("AutoRngToolbar")
        toolbar.setFixedHeight(56)
        row = QHBoxLayout(toolbar)
        row.setContentsMargins(14, 0, 14, 0)
        row.setSpacing(0)
        row.setAlignment(Qt.AlignmentFlag.AlignVCenter)
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
        self.start_from_reidentify_action = QAction("从校正开始", self.start_button)
        self.start_menu.addAction(self.start_from_seed_action)
        self.start_menu.addAction(self.start_from_capture_action)
        self.start_menu.addAction(self.start_from_reidentify_action)
        self.start_button.setMenu(self.start_menu)
        self.stop_button = QPushButton("停止")
        self.stop_button.setObjectName("DangerButton")
        self.status_badge = QLabel("状态：空闲")
        self.status_badge.setObjectName("Badge")
        self.debug_output_check = QCheckBox("调试")
        self.debug_output_check.setToolTip("输出 CLI 耗时、时间戳等调试信息")
        self.debug_output_check.setFixedHeight(34)

        # 统一控件尺寸：全部 34px
        self.status_badge.setFixedHeight(34)
        self.mode_combo.setFixedHeight(34)
        self.mode_combo.setFixedWidth(120)
        self.loop_count.setFixedHeight(34)
        self.loop_count.setFixedWidth(80)
        self.start_button.setFixedHeight(34)
        self.start_button.setMinimumWidth(88)
        self.stop_button.setFixedHeight(34)
        self.stop_button.setMinimumWidth(80)

        # 信号/槽（保持不变）
        self.start_button.clicked.connect(self._start_clicked)
        self.start_from_seed_action.triggered.connect(self._start_clicked)
        self.start_from_capture_action.triggered.connect(self._start_from_capture_clicked)
        self.start_from_reidentify_action.triggered.connect(self._start_from_reidentify_clicked)
        self.stop_button.clicked.connect(self._stop_clicked)

        # ── 左分区：运行模式 + 次数 + 调试 ──
        left_layout = QHBoxLayout()
        left_layout.setSpacing(12)
        left_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        left_layout.addWidget(QLabel("运行模式"))
        left_layout.addWidget(self.mode_combo)
        left_layout.addWidget(QLabel("次数"))
        left_layout.addWidget(self.loop_count)
        left_layout.addWidget(self.debug_output_check)

        # ── 右分区：状态 + 按钮 ──
        self.capture_info_button = QPushButton("OCR设置")
        self.capture_info_button.setObjectName("SecondaryButton")
        self.capture_info_button.setFixedHeight(34)
        self.capture_info_button.setMinimumWidth(120)
        self.capture_info_button.setToolTip("打开 OCR 区域设置窗口")
        self.capture_info_button.clicked.connect(self.captureInfoRequested.emit)

        right_layout = QHBoxLayout()
        right_layout.setSpacing(10)
        right_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        right_layout.addWidget(self.status_badge)
        right_layout.addSpacing(16)
        right_layout.addWidget(self.start_button)
        right_layout.addWidget(self.stop_button)
        right_layout.addWidget(self.capture_info_button)

        row.addLayout(left_layout)
        row.addStretch(1)
        row.addLayout(right_layout)
        return toolbar

    def _build_config_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMinimumWidth(450)
        panel.setMaximumWidth(450)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.strategy_group = self._build_strategy_group()
        layout.addWidget(self.strategy_group)
        return panel

    def _build_strategy_group(self) -> QGroupBox:
        group = QGroupBox("自动策略")
        group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        form = QFormLayout(group)
        form.setContentsMargins(12, 12, 12, 12)
        form.setVerticalSpacing(8)
        self.max_advances = self._spin(0, 1_000_000_000, 100_000)
        self.fixed_delay = self._spin(0, QT_INT_MAX, 100)
        self.fixed_delay.setParent(group)
        self.fixed_delay.hide()
        self.max_wait_frames = self._spin(1, 1_000_000_000, 300)
        self.delay_strategy_dialog = DelayStrategyDialog(self)
        self.delay_settings_button = QPushButton()
        self.delay_settings_button.setObjectName("SecondaryButton")
        self.delay_settings_button.setFixedSize(215, 34)
        self.delay_settings_button.clicked.connect(self.open_delay_strategy_dialog)
        self.delay_strategy_dialog.settingsEdited.connect(self._refresh_delay_dialog_preview)
        self.delay_strategy_dialog.clearSamplesRequested.connect(self._confirm_clear_delay_samples)
        self.fixed_delay.valueChanged.connect(self._legacy_fixed_delay_changed)
        self.strategy_dialog = AutoRngStrategyDialog(self)
        self.reseed_threshold_frames = self.strategy_dialog.reseed_threshold_frames
        self.reidentify_max_attempts = self.strategy_dialog.reidentify_max_attempts
        self.reidentify_failure_policy = self.strategy_dialog.reidentify_failure_policy
        self.reidentify_seed_max_attempts = self.strategy_dialog.reidentify_seed_max_attempts
        self.reseeding_threshold = self.strategy_dialog.reseeding_threshold
        self.strategy_settings_button = QPushButton("校正策略设置...")
        self.strategy_settings_button.setObjectName("SecondaryButton")
        self.strategy_settings_button.setFixedSize(215, 34)
        self.strategy_settings_button.clicked.connect(self.open_strategy_dialog)
        self.shiny_threshold_seconds = QDoubleSpinBox()
        self.shiny_threshold_seconds.setRange(0.0, 999.0)
        self.shiny_threshold_seconds.setDecimals(3)
        self.shiny_threshold_seconds.setSingleStep(0.1)
        self.shiny_threshold_seconds.setValue(DEFAULT_SHINY_THRESHOLD_SECONDS)
        set_c_locale(self.shiny_threshold_seconds)
        for spin in (self.max_advances, self.fixed_delay, self.max_wait_frames):
            spin.setFixedWidth(215)
        self.shiny_threshold_seconds.setFixedWidth(215)
        explained_rows = (
            (
                "搜索范围",
                self.max_advances,
                "设置当前 Seed 下搜索目标候选的最大帧数。\n"
                "数值越大，能搜索到更远的候选，但命中较远目标时需要更长的过帧时间。\n"
                "全国图鉴齐全的情况下，过 100 万帧大约需要 10 分钟。",
            ),
            (
                "delay",
                self.delay_settings_button,
                "表示脚本等待结束后（无 _闪帧时为脚本启动后）到实际撞到目标之间经过的帧数。\n"
                "含 _闪帧的旧脚本按“目标帧 - delay - _闪帧”启动；无 _闪帧时由软件等待到“目标帧 - delay”再启动；"
                "delay 越大，撞闪脚本启动得越早。\n点击编辑固定或动态 delay 策略。",
            ),
            (
                "最大等待窗口",
                self.max_wait_frames,
                "决定何时停止运行过帧脚本，改为软件实时等待。\n"
                "距离撞闪脚本启动帧不超过该帧数时，不再运行过帧脚本，而是根据当前活帧等待到启动时机。\n"
                "数值越大，流程越早进入实时等待；数值越小，越依赖过帧脚本接近目标。",
            ),
            (
                "闪光阈值（秒）",
                self.shiny_threshold_seconds,
                "使用 OCR 测量战斗文本“出现了！”到“去吧/上吧”之间的时间间隔。\n"
                "测得的间隔大于或等于该值时，判定为疑似闪光并停止自动流程。\n"
                "艾姆利多和克雷色利亚会先等待脚本检测到进入战斗，再启动 OCR。\n"
                "可先在 Seed 捕获页面使用“校准闪光判定”测量合适的阈值。\n"
                "设为 0 时关闭自动 OCR 判闪。",
            ),
        )
        for row_index, (label_text, field, tooltip) in enumerate(explained_rows):
            if row_index == 3:
                form.addRow("", self.strategy_settings_button)
            form.addRow(label_text, field)
            field.setToolTip(tooltip)
            label = form.labelForField(field)
            if label is not None:
                label.setToolTip(tooltip)
            if field is self.delay_settings_button:
                self.delay_settings_label = label
                self.fixed_delay.setToolTip(tooltip)
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
        set_c_locale(self.reverse_lookup_window)
        reverse_row = QHBoxLayout()
        reverse_row.addWidget(self.auto_reverse_combo)
        reverse_row.addWidget(self.reverse_lookup_window)
        form.addRow(reverse_row)
        self._refresh_delay_ui()
        return group

    def open_delay_strategy_dialog(self) -> None:
        self.delay_strategy_dialog.set_values(self._delay_strategy_config)
        self._refresh_delay_dialog_preview()
        if self.delay_strategy_dialog.exec() != QDialog.DialogCode.Accepted:
            self.delay_strategy_dialog.set_values(self._delay_strategy_config)
            self._refresh_delay_dialog_preview()
            return
        self._commit_delay_strategy_config(
            self.delay_strategy_dialog.values(),
            persist=True,
            emit=True,
        )

    def delay_strategy_config(self) -> DelayStrategyConfig:
        return self._delay_strategy_config

    def delay_samples(self) -> list[tuple[int, ...]]:
        return list(self._delay_sample_rounds)

    def effective_delay_for_next_round(self) -> int:
        return self._estimate_delay(self._delay_strategy_config).value

    @Slot(object)
    def record_delay_sample(self, candidates: object) -> None:
        sample = DelaySampleRound.from_candidates(candidates)
        if not sample.candidates:
            return
        self._delay_sample_rounds.append(sample.candidates)
        self._save_delay_samples()
        self._refresh_delay_ui()
        self.delaySamplesChanged.emit(self.delay_samples())

    @Slot()
    def clear_delay_samples(self) -> None:
        if not self._delay_sample_rounds:
            return
        self._delay_sample_rounds.clear()
        self._save_delay_samples()
        self._refresh_delay_ui()
        self.delaySamplesChanged.emit([])
        self.delaySamplesCleared.emit()

    @Slot()
    def _confirm_clear_delay_samples(self) -> None:
        if not self._delay_sample_rounds:
            return
        answer = QMessageBox.question(
            self.delay_strategy_dialog,
            "清空 delay 样本",
            "确定清空全部 delay 统计样本吗？\n清空后立即生效，无法通过“取消”恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            # Let the tool button finish dispatching ``clicked`` before the
            # refresh disables it after the sample list becomes empty.
            QTimer.singleShot(0, self.clear_delay_samples)

    @Slot(object)
    def set_active_delay(self, value: object) -> None:
        self._active_delay = None if value is None else max(0, int(value))
        self._refresh_delay_ui()

    def _estimate_delay(self, config: DelayStrategyConfig) -> DelayEstimate:
        return estimate_delay(
            config,
            self._delay_sample_rounds,
            reference_delay=self._active_delay,
        )

    @Slot()
    def _refresh_delay_dialog_preview(self) -> None:
        estimate = self._estimate_delay(self.delay_strategy_dialog.values())
        self.delay_strategy_dialog.set_runtime_state(
            active_delay=self._active_delay,
            estimate=estimate,
            sample_rounds=self._delay_sample_rounds,
        )

    def _refresh_delay_ui(self) -> None:
        estimate = self._estimate_delay(self._delay_strategy_config)
        strategy_id = self._delay_strategy_config.strategy.value
        label = DELAY_STRATEGY_LABEL_BY_ID[strategy_id]
        self.delay_settings_button.setText(f"{label} · {estimate.value}")
        tooltip = (
            "delay 越大，撞闪脚本启动得越早。点击编辑固定或动态 delay 策略。\n"
            f"本次使用：{'-' if self._active_delay is None else self._active_delay}\n"
            f"下轮预计：{estimate.value}\n"
            f"有效样本：{estimate.valid_round_count} 轮"
        )
        self.delay_settings_button.setToolTip(tooltip)
        if getattr(self, "delay_settings_label", None) is not None:
            self.delay_settings_label.setToolTip(tooltip)
        self._refresh_delay_dialog_preview()

    def _commit_delay_strategy_config(
        self,
        config: DelayStrategyConfig,
        *,
        persist: bool,
        emit: bool,
    ) -> None:
        self._delay_strategy_config = config
        self._updating_fixed_delay = True
        try:
            self.fixed_delay.setValue(config.baseline_delay)
        finally:
            self._updating_fixed_delay = False
        self.delay_strategy_dialog.set_values(config)
        if persist:
            self._save_delay_settings()
        self._refresh_delay_ui()
        if emit:
            self.delayStrategyChanged.emit(config)

    @Slot(int)
    def _legacy_fixed_delay_changed(self, value: int) -> None:
        if self._updating_fixed_delay:
            return
        current = self._delay_strategy_config
        self._delay_strategy_config = DelayStrategyConfig(
            strategy=current.strategy,
            baseline_delay=value,
            multi_candidate_policy=current.multi_candidate_policy,
            window_size=current.window_size,
            ewma_alpha=current.ewma_alpha,
            dense_interval_width=current.dense_interval_width,
        )
        if not self.delay_strategy_dialog.isVisible():
            self.delay_strategy_dialog.set_values(self._delay_strategy_config)
        self._refresh_delay_ui()

    def open_strategy_dialog(self) -> None:
        original_values = self.strategy_dialog.values()
        if self.strategy_dialog.exec() == QDialog.DialogCode.Accepted:
            self._save_strategy_settings()
            return
        self.strategy_dialog.set_values(*original_values)

    def _build_script_group(self) -> QGroupBox:
        group = QGroupBox("脚本")
        layout = QGridLayout(group)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(8)
        def combo_factory() -> _RefreshingScriptComboBox:
            return _RefreshingScriptComboBox(lambda: self.refresh_scripts())

        self.seed_script_combo = combo_factory()
        self.advance_script_combo = combo_factory()
        self.hit_script_combo = combo_factory()
        self.escape_script_combo = combo_factory()
        self.exit_script_combo = combo_factory()
        self.reverse_script_combo = combo_factory()
        for combo in self._script_combos():
            combo.setFixedHeight(34)
            combo.setFixedWidth(160)
        self.escape_continue_check = QCheckBox("逃跑续搜")
        self.escape_continue_check.setFixedHeight(34)
        self.escape_continue_check.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.escape_continue_check.setStyleSheet("background: transparent;")
        self.escape_continue_check.setToolTip(
            "OCR 明确判定未出闪，且当前搜索范围内仍有后续候选时，运行所选逃跑脚本；\n"
            "脚本完成后校正当前位置，并继续选择最近的可达目标。\n"
            "之后每次未出闪都会重复该流程，直到出闪或搜索范围内没有可达目标。"
        )
        self.escape_script_combo.setToolTip(
            "脚本从未出闪后的战斗画面开始执行，结束时必须回到能够捕捉玩家眨眼并进行校正的位置。\n"
            "逃跑阶段不会再次执行 OCR 判闪。"
        )
        self.escape_script_combo.setEnabled(False)
        self.escape_continue_check.toggled.connect(self.escape_script_combo.setEnabled)
        layout.addWidget(QLabel("测种脚本"), 0, 0)
        layout.addWidget(self.seed_script_combo, 0, 1)
        layout.addWidget(QLabel("过帧脚本"), 0, 2)
        layout.addWidget(self.advance_script_combo, 0, 3)
        layout.addWidget(QLabel("撞闪脚本"), 1, 0)
        layout.addWidget(self.hit_script_combo, 1, 1)
        layout.addWidget(
            self.escape_continue_check,
            1,
            2,
            Qt.AlignmentFlag.AlignLeft
            | Qt.AlignmentFlag.AlignVCenter
            | Qt.AlignmentFlag.AlignAbsolute,
        )
        layout.addWidget(self.escape_script_combo, 1, 3)
        layout.addWidget(QLabel("过场脚本"), 2, 0)
        layout.addWidget(self.exit_script_combo, 2, 1)
        layout.addWidget(QLabel("反查脚本"), 2, 2)
        layout.addWidget(self.reverse_script_combo, 2, 3)
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
        layout.addStretch(1)
        return panel

    def _build_target_summary_group(self) -> QGroupBox:
        group = QGroupBox()
        group.setObjectName("TargetSummaryGroup")
        group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        group.setMaximumHeight(150)
        layout = QVBoxLayout(group)
        layout.setContentsMargins(12, 6, 12, 8)
        layout.setSpacing(5)
        header = QHBoxLayout()
        header.setAlignment(Qt.AlignmentFlag.AlignVCenter)
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
        self.target_summary_scroll.setMinimumHeight(56)
        self.target_summary_scroll.setMaximumHeight(62)
        self.target_summary_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.target_summary_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.target_summary_container = QWidget()
        self.target_summary_layout = QVBoxLayout(self.target_summary_container)
        self.target_summary_layout.setContentsMargins(0, 0, 0, 0)
        self.target_summary_layout.setSpacing(3)
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
        group = QGroupBox("当前消息")
        group.setObjectName("CurrentMessageGroup")
        group.setMaximumWidth(16777215)
        group.setFixedHeight(90)
        group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        group.setStyleSheet(
            "QGroupBox#CurrentMessageGroup { margin-top: 12px; padding: 6px 10px 8px 10px; }"
            "QGroupBox#CurrentMessageGroup::title { left: 12px; top: 0; padding: 0 4px; }"
            "QGroupBox#CurrentMessageGroup QPushButton#SecondaryButton { "
            "min-height: 32px; max-height: 32px; padding: 0 12px; }"
        )
        self.log_group = group
        layout = QHBoxLayout(group)
        layout.setContentsMargins(10, 4, 10, 6)
        layout.setSpacing(10)

        self.latest_log_label = QLabel("暂无消息")
        self.latest_log_label.setObjectName("LatestLogLabel")
        self.latest_log_label.setWordWrap(True)
        self.latest_log_label.setMaximumHeight(42)
        self.latest_log_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.latest_log_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.latest_log_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.latest_log_label, 1)

        self.view_log_button = QPushButton("查看日志")
        self.view_log_button.setObjectName("SecondaryButton")
        self.view_log_button.setFixedHeight(34)
        self.view_log_button.setMinimumWidth(96)
        self.view_log_button.clicked.connect(self.runLogRequested.emit)
        layout.addWidget(self.view_log_button, 0, Qt.AlignmentFlag.AlignVCenter)

        self.log_view = _CopyableTextEdit(group)
        self.log_view.setObjectName("LogView")
        self.log_view.setFont(QFont("Consolas", 10))
        self.log_view.setStyleSheet("QPlainTextEdit { padding: 12px; }")
        self.log_view.setVisible(False)
        layout.addWidget(self.log_view)
        return group

    def refresh_scripts(self) -> None:
        selected_paths = {
            combo: self._selected_path(combo)
            for combo in self._script_combos()
        }
        self._scripts = list_auto_scripts(self.script_dir)
        for combo in self._script_combos():
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("请选择", None)
            for path in self._scripts:
                combo.addItem(path.name, str(path))
            combo.blockSignals(False)
        if self._scripts_initialized:
            for combo, path in selected_paths.items():
                self._select_script(combo, path)
        else:
            self._select_script(self.seed_script_combo, choose_default_script(self._scripts, DEFAULT_SEED_SCRIPT_NAME))
            self._select_script(self.advance_script_combo, choose_default_script(self._scripts, DEFAULT_ADVANCE_SCRIPT_NAME))
            self._scripts_initialized = True

    def set_phase_text(self, text: str) -> None:
        self.status_badge.setText(text)

    def set_live_advances(self, advances: int) -> None:
        _ = advances

    def apply_progress(self, progress: AutoRngProgress) -> None:
        phase_text = progress.phase.value if hasattr(progress.phase, "value") else str(progress.phase)
        self.status_badge.setText(phase_text)
        self.autoProgressChanged.emit(progress)
        self._last_failed_progress_message = (
            progress.log_message if progress.phase == AutoRngPhase.FAILED else None
        )
        if progress.log_message:
            level = "ERROR" if progress.phase == AutoRngPhase.FAILED else "INFO"
            self.add_log(progress.log_message, level=level)

    def add_log(self, message: str, *, level: str = "INFO") -> None:
        text = str(message)
        if self._run_log_sink is not None:
            try:
                self._run_log_sink(level, text)
            except Exception:
                pass
        timestamp = datetime.now().strftime("%H:%M:%S")
        lines = text.splitlines() or [""]
        stamped = [
            line if _TIMESTAMP_RE.match(line) else f"[{timestamp}] {line}"
            for line in lines
        ]
        self.log_view.appendPlainText("\n".join(stamped))
        latest_line = next((line.strip() for line in reversed(lines) if line.strip()), None)
        if latest_line is not None:
            self.latest_log_label.setText(latest_line)
            self.latest_log_label.setToolTip(text)

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

    def _start_from_reidentify_clicked(self) -> None:
        self._start_with_phase(AutoRngPhase.REIDENTIFY)

    def _start_with_phase(self, start_phase: AutoRngPhase) -> None:
        self._save_panel_state()
        config = self.build_config(start_phase=start_phase)
        try:
            validate_auto_scripts(
                config.seed_script_path,
                config.advance_script_path,
                config.hit_script_path,
                escape_continue=config.escape_continue,
                escape_script_path=config.escape_script_path,
                shiny_threshold_seconds=config.shiny_threshold_seconds,
                target_species=config.target_species,
            )
        except AutoScriptError as exc:
            self.set_phase_text("配置错误")
            self.add_log(str(exc), level="WARNING")
            return
        self.startRequested.emit(config)

    def _stop_clicked(self) -> None:
        if self._runner_worker is not None:
            self._runner_worker.stop()
        self.stopRequested.emit()

    def build_config(self, *, start_phase: AutoRngPhase = AutoRngPhase.RUN_SEED_SCRIPT) -> AutoRngConfig:
        targets = self.targets()
        return AutoRngConfig(
            script_dir=self.script_dir,
            seed_script_path=self._selected_path(self.seed_script_combo),
            advance_script_path=self._selected_path(self.advance_script_combo),
            hit_script_path=self._selected_path(self.hit_script_combo),
            escape_script_path=self._selected_path(self.escape_script_combo),
            exit_script_path=self._selected_path(self.exit_script_combo),
            reverse_script_path=self._selected_path(self.reverse_script_combo),
            record_script_path=choose_default_script(self._scripts, DEFAULT_RECORD_SCRIPT_NAME),
            auto_reverse=self.auto_reverse_combo.currentIndex() == 1,
            escape_continue=self.escape_continue_check.isChecked(),
            reverse_lookup_window=self.reverse_lookup_window.value(),
            sync_mode=self.sync_combo.currentIndex(),
            sync_nature=self.sync_nature_input.text().strip(),
            target_species=int(targets[0][0].template.species) if targets else None,
            fixed_delay=self.fixed_delay.value(),
            delay_strategy=self._delay_strategy_config.strategy.value,
            delay_multi_candidate_policy=self._delay_strategy_config.multi_candidate_policy.value,
            delay_sample_window=self._delay_strategy_config.window_size,
            delay_ewma_alpha=self._delay_strategy_config.ewma_alpha,
            delay_dense_interval_width=self._delay_strategy_config.dense_interval_width,
            delay_sample_rounds=tuple(self._delay_sample_rounds),
            max_wait_frames=self.max_wait_frames.value(),
            reseed_threshold_frames=self.reseed_threshold_frames.value(),
            reidentify_max_attempts=self.reidentify_max_attempts.value(),
            reidentify_failure_policy=str(self.reidentify_failure_policy.currentData()),
            reidentify_seed_max_attempts=self.reidentify_seed_max_attempts.value(),
            reseeding_threshold=self.reseeding_threshold.value(),
            loop_mode=str(self.mode_combo.currentData()),
            loop_count=self.loop_count.value(),
            start_phase=start_phase,
            max_advances=self.max_advances.value(),
            shiny_threshold_seconds=self.shiny_threshold_seconds.value() or None,
            debug_output=self.debug_output_check.isChecked(),
            has_body_filters=any(
                sf.height_min != 0 or sf.height_max != 255
                or sf.weight_min != 0 or sf.weight_max != 255
                for _record, sf, _mode in targets
            ),
        )

    def run_with_runner(self, runner: object) -> None:
        if self._runner_thread is not None:
            self.add_log("自动流程已在运行", level="WARNING")
            return
        self._last_failed_progress_message = None
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
        self.runStateChanged.emit(True)
        thread.start()

    def _runner_finished(self, progress: object) -> None:
        # 不重复 apply_progress：最后一条进度已通过 progressChanged 信号输出
        if isinstance(progress, AutoRngProgress):
            self.set_phase_text("已停止" if progress.phase == AutoRngPhase.IDLE else "已完成")
        self._clear_runner_thread()

    def _runner_failed(self, message: str) -> None:
        self.set_phase_text("失败")
        if message != self._last_failed_progress_message:
            self.add_log(message, level="ERROR")
        self._last_failed_progress_message = None
        self._clear_runner_thread()

    def _clear_runner_thread(self) -> None:
        self._runner_thread = None
        self._runner_worker = None
        self.start_button.setEnabled(True)
        self.runStateChanged.emit(False)

    def _selected_path(self, combo: QComboBox) -> Path | None:
        value = combo.currentData()
        return Path(value) if value else None

    def _script_combos(self) -> tuple[QComboBox, ...]:
        return (
            self.seed_script_combo,
            self.advance_script_combo,
            self.hit_script_combo,
            self.escape_script_combo,
            self.exit_script_combo,
            self.reverse_script_combo,
        )

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
        self._save_delay_settings()
        self._save_delay_samples()
        s.setValue("max_wait_frames", self.max_wait_frames.value())
        self._save_strategy_settings()
        s.setValue("shiny_threshold", self.shiny_threshold_seconds.value())
        seed_path = self._selected_path(self.seed_script_combo)
        advance_path = self._selected_path(self.advance_script_combo)
        hit_path = self._selected_path(self.hit_script_combo)
        escape_path = self._selected_path(self.escape_script_combo)
        exit_path = self._selected_path(self.exit_script_combo)
        if seed_path is not None:
            s.setValue("seed_script", str(seed_path))
        if advance_path is not None:
            s.setValue("advance_script", str(advance_path))
        if hit_path is not None:
            s.setValue("hit_script", str(hit_path))
        if escape_path is not None:
            s.setValue("escape_script", str(escape_path))
        else:
            s.remove("escape_script")
        if exit_path is not None:
            s.setValue("exit_script", str(exit_path))
        else:
            s.remove("exit_script")
        reverse_path = self._selected_path(self.reverse_script_combo)
        if reverse_path is not None:
            s.setValue("reverse_script", str(reverse_path))
        s.setValue("sync_state", self.sync_combo.currentIndex())
        s.setValue("sync_nature", self.sync_nature_input.text())
        s.setValue("auto_reverse", self.auto_reverse_combo.currentIndex())
        s.setValue("escape_continue", self.escape_continue_check.isChecked())
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

    def _save_strategy_settings(self) -> None:
        s = self._settings
        s.setValue("reseed_threshold_frames", self.reseed_threshold_frames.value())
        s.setValue("reidentify_max_attempts", self.reidentify_max_attempts.value())
        s.setValue("reidentify_failure_policy", self.reidentify_failure_policy.currentData())
        s.setValue("reidentify_seed_max_attempts", self.reidentify_seed_max_attempts.value())
        # Keep the existing key for compatibility with saved configurations.
        s.setValue("reseeding_threshold", self.reseeding_threshold.value())

    def _save_delay_settings(self) -> None:
        s = self._settings
        config = self._delay_strategy_config
        # Keep fixed_delay as the baseline key for compatibility with older releases.
        s.setValue("fixed_delay", config.baseline_delay)
        s.setValue("delay_strategy", config.strategy.value)
        s.setValue("delay_multi_candidate_policy", config.multi_candidate_policy.value)
        s.setValue("delay_sample_window", config.window_size)
        s.setValue("delay_ewma_alpha", config.ewma_alpha)
        s.setValue("delay_dense_interval_width", config.dense_interval_width)

    def _save_delay_samples(self) -> None:
        self._settings.setValue(
            "delay_sample_rounds_json",
            json.dumps(self._delay_sample_rounds, separators=(",", ":")),
        )

    def _restore_delay_settings(self) -> None:
        s = self._settings
        defaults = DelayStrategyConfig()
        window_key = "delay_sample_window" if s.contains("delay_sample_window") else "delay_window_size"
        try:
            config = DelayStrategyConfig(
                strategy=str(s.value("delay_strategy", defaults.strategy.value)),
                baseline_delay=int(s.value("fixed_delay", defaults.baseline_delay)),
                multi_candidate_policy=str(
                    s.value("delay_multi_candidate_policy", defaults.multi_candidate_policy.value)
                ),
                window_size=int(s.value(window_key, defaults.window_size)),
                ewma_alpha=float(s.value("delay_ewma_alpha", defaults.ewma_alpha)),
                dense_interval_width=int(
                    s.value("delay_dense_interval_width", defaults.dense_interval_width)
                ),
            )
        except (TypeError, ValueError, OverflowError):
            config = defaults
        self._commit_delay_strategy_config(config, persist=False, emit=False)

    def _restore_delay_samples(self) -> None:
        raw_value = self._settings.value("delay_sample_rounds_json", "[]")
        try:
            decoded = json.loads(str(raw_value))
        except (TypeError, ValueError, json.JSONDecodeError):
            decoded = []
        restored: list[tuple[int, ...]] = []
        if isinstance(decoded, list):
            for raw_round in decoded:
                sample = DelaySampleRound.from_candidates(raw_round)
                if sample.candidates:
                    restored.append(sample.candidates)
        self._delay_sample_rounds = restored
        self._refresh_delay_ui()

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
        self._restore_delay_settings()
        self._restore_delay_samples()
        if s.contains("max_wait_frames"):
            self.max_wait_frames.setValue(int(s.value("max_wait_frames", 300)))
        if s.contains("reseed_threshold_frames"):
            self.reseed_threshold_frames.setValue(
                int(s.value("reseed_threshold_frames", DEFAULT_RESEED_THRESHOLD_FRAMES))
            )
        if s.contains("reidentify_max_attempts"):
            self.reidentify_max_attempts.setValue(
                int(s.value("reidentify_max_attempts", DEFAULT_REIDENTIFY_MAX_ATTEMPTS))
            )
        if s.contains("reidentify_failure_policy"):
            self.strategy_dialog.set_policy(
                str(s.value("reidentify_failure_policy", DEFAULT_REIDENTIFY_FAILURE_POLICY))
            )
        if s.contains("reidentify_seed_max_attempts"):
            self.reidentify_seed_max_attempts.setValue(
                int(s.value("reidentify_seed_max_attempts", DEFAULT_REIDENTIFY_SEED_MAX_ATTEMPTS))
            )
        if s.contains("reseeding_threshold"):
            self.reseeding_threshold.setValue(int(s.value("reseeding_threshold", DEFAULT_RESEEDING_THRESHOLD_FRAMES)))
        if s.contains("shiny_threshold"):
            self.shiny_threshold_seconds.setValue(float(s.value("shiny_threshold", 0.0)))
        # 恢复脚本选择（脚本列表已通过 refresh_scripts 加载）
        for key, combo in (
            ("seed_script", self.seed_script_combo),
            ("advance_script", self.advance_script_combo),
            ("hit_script", self.hit_script_combo),
            ("escape_script", self.escape_script_combo),
            ("exit_script", self.exit_script_combo),
            ("reverse_script", self.reverse_script_combo),
        ):
            if not s.contains(key):
                continue
            saved_path = str(s.value(key, ""))
            selected_path = self._select_script_by_path(combo, saved_path)
            if selected_path is not None and str(selected_path) != saved_path:
                s.setValue(key, str(selected_path))
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
        self.escape_continue_check.setChecked(s.value("escape_continue", False, type=bool))
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

    def _select_script_by_path(self, combo: QComboBox, path_str: str) -> Path | None:
        if not path_str:
            return None
        path = remap_legacy_script_path(path_str, script_dir=self.script_dir)
        index = combo.findData(str(path))
        if index < 0:
            for candidate_index in range(combo.count()):
                candidate = Path(str(combo.itemData(candidate_index)))
                try:
                    if candidate.samefile(path):
                        index = candidate_index
                        break
                except OSError:
                    continue
        if index >= 0:
            combo.setCurrentIndex(index)
            return Path(str(combo.itemData(index)))
        return None

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
        set_c_locale(spin)
        return spin


def _target_condition_text(state_filter: StateFilter, shiny_mode: str) -> str:
    parts: list[str] = []
    shiny_label = {
        "shiny": "仅异色",
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
