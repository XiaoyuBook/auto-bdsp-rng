from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from auto_bdsp_rng.ui.runtime_insights import RuntimeInsights
from auto_bdsp_rng.ui.table_workbench import ResultItem, TableWorkbench
from auto_bdsp_rng.ui.workspace_layout import WorkspaceSplit

from PySide6.QtCore import QObject, QSize, QSettings, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QAction, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from auto_bdsp_rng.automation.auto_rng.delay_strategy import (
    DelayEstimate,
    DelaySampleRound,
    DelayStrategyConfig,
    estimate_delay,
)
from auto_bdsp_rng.automation.auto_rng.delay_profiles import (
    DelayProfile,
    decode_delay_profiles,
    encode_delay_profiles,
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
from auto_bdsp_rng.data import (
    GameVersion,
    StaticEncounterRecord,
    get_recommended_delay,
    get_static_encounters,
)
from auto_bdsp_rng.gen8_static import StateFilter
from auto_bdsp_rng.resources import remap_legacy_script_path, script_directory
from auto_bdsp_rng.ui.automation_lifecycle import AutomationLifecycle
from auto_bdsp_rng.ui.check_box import CheckmarkCheckBox as QCheckBox
from auto_bdsp_rng.ui.delay_strategy_dialog import (
    DELAY_STRATEGY_LABEL_BY_ID,
    DelaySummaryButton,
    DelayStrategyDialog,
    delay_lucide_icon,
)
from auto_bdsp_rng.ui.combo_box import ChevronComboBox as QComboBox
from auto_bdsp_rng.ui.numeric_locale import set_c_locale
from auto_bdsp_rng.ui.workspace_controls import PrimaryToolButton, SpeciesAvatar, workspace_icon
from auto_bdsp_rng.ui.table_delegate import RowSeparatorDelegate
from auto_bdsp_rng.ui.workspace_theme import add_card_shadow, focus_styles, primary_button_styles, ui_font, workspace_styles
from auto_bdsp_rng.ui.spin_box import (
    ChevronDoubleSpinBox as QDoubleSpinBox,
    ChevronSpinBox as QSpinBox,
)
from auto_bdsp_rng.ui.static_target_form import StaticTargetForm
from auto_bdsp_rng.ui.target_dialog import TargetDialog, POKEMON_LABELS_ZH, NATURES_ZH


RUNTIME_CANDIDATE_DISPLAY_LIMIT = 20
RUNTIME_CANDIDATE_VISIBLE_ROWS = 5
_RUNTIME_STEP_LABELS = ("测种", "搜索", "过帧", "校正", "撞闪", "结果")
_RUNTIME_STEP_TOOLTIPS = (
    "运行测种脚本并捕获当前 Seed",
    "生成、过滤并锁定本轮可达候选",
    "执行过帧脚本，或由软件活帧等待推进到撞闪启动点",
    "过帧后重新识别位置；过场脚本也在这里完成校正",
    "进行最终闪帧校准、动态调整并执行撞闪脚本；OCR 检测与脚本联动",
    "显示运行结果，或处理未出闪后的逃跑续搜、自动反查；完成不等于出闪",
)
_RUNTIME_STEP_PHASES = {
    AutoRngPhase.RUN_SEED_SCRIPT: 0,
    AutoRngPhase.CAPTURE_SEED: 0,
    AutoRngPhase.SEARCH_TARGET: 1,
    # DECIDE_ADVANCE 已经完成候选搜索，正在决定“过帧脚本 / 活帧等待 /
    # 撞闪准备”中的下一条推进路径，因此在宏流程中归入过帧。
    AutoRngPhase.DECIDE_ADVANCE: 2,
    AutoRngPhase.RUN_ADVANCE_SCRIPT: 2,
    # FINAL_WAIT 使用 Project_Xs 计数器实时推进 Adv 到脚本启动帧，
    # 这是软件过帧，不应在视觉上伪装成已经开始撞闪。
    AutoRngPhase.FINAL_WAIT: 2,
    AutoRngPhase.REIDENTIFY: 3,
    AutoRngPhase.EXIT_RESEED: 3,
    AutoRngPhase.FINAL_CALIBRATE: 4,
    AutoRngPhase.FINAL_ADJUST: 4,
    AutoRngPhase.RUN_HIT_SCRIPT: 4,
    AutoRngPhase.RUN_ESCAPE_SCRIPT: 5,
    AutoRngPhase.REVERSE_LOOKUP: 5,
    AutoRngPhase.LOOP_CHECK: 5,
}
_RUNTIME_PHASE_TITLES = {
    AutoRngPhase.DECIDE_ADVANCE: "选择推进方式",
    AutoRngPhase.FINAL_WAIT: "活帧等待 · 过帧中",
    AutoRngPhase.EXIT_RESEED: "过场与校正",
    AutoRngPhase.FINAL_CALIBRATE: "撞闪准备 · 最终校准",
    AutoRngPhase.FINAL_ADJUST: "撞闪准备 · 调整闪帧",
    AutoRngPhase.RUN_ESCAPE_SCRIPT: "未出闪 · 逃跑续搜",
    AutoRngPhase.REVERSE_LOOKUP: "未出闪 · 反查个体",
}
_RUNTIME_PHASE_DESCRIPTIONS = {
    AutoRngPhase.RUN_SEED_SCRIPT: "运行测种脚本，随后捕获 Seed。",
    AutoRngPhase.CAPTURE_SEED: "捕获 Seed，随后重新搜索可达候选。",
    AutoRngPhase.SEARCH_TARGET: "搜索并筛选当前可达的目标。",
    AutoRngPhase.DECIDE_ADVANCE: "根据距离选择过帧脚本、活帧等待或撞闪准备。",
    AutoRngPhase.RUN_ADVANCE_SCRIPT: "执行过帧脚本，完成后校正位置或重新捕获 Seed。",
    AutoRngPhase.REIDENTIFY: "重新识别当前位置，再搜索目标或决定如何推进。",
    AutoRngPhase.EXIT_RESEED: "执行过场脚本并校正位置，完成后重新搜索目标。",
    AutoRngPhase.FINAL_CALIBRATE: "根据当前帧数校准撞闪时机。",
    AutoRngPhase.FINAL_ADJUST: "调整撞闪脚本的闪帧参数，准备启动。",
    AutoRngPhase.RUN_HIT_SCRIPT: "执行撞闪脚本，并按配置检测出闪结果。",
    AutoRngPhase.RUN_ESCAPE_SCRIPT: "执行逃跑脚本，随后校正位置并续搜本轮后续候选。",
    AutoRngPhase.REVERSE_LOOKUP: "反查本次个体，完成后决定是否进入下一轮。",
    AutoRngPhase.LOOP_CHECK: "本次尝试已结束，检查是否进入下一轮。",
}
_RUNTIME_SHINY_LABELS = {0: "否", 1: "星闪", 2: "方闪", 3: "星闪", 4: "方闪"}


def _ui_numeric_font(
    pixel_size: int,
    weight: QFont.Weight = QFont.Weight.Normal,
) -> QFont:
    """Use the CJK UI face for mixed Chinese units and tabular digits."""

    return ui_font(pixel_size, weight)


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
        spin.setFont(_ui_numeric_font(14))
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


class AutoRngPanel(AutomationLifecycle, QWidget):
    startRequested = Signal(object)
    stopRequested = Signal()
    autoProgressChanged = Signal(object)
    runStateChanged = Signal(bool)
    runLogRequested = Signal()
    roundRecordsRequested = Signal()
    targetDataRequested = Signal()
    scriptEditRequested = Signal(object)
    latestMessageChanged = Signal(str)
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
        self._init_lifecycle()
        self.script_dir = script_dir
        self._run_log_sink = run_log_sink
        self._scripts: list[Path] = []
        self._scripts_initialized = False
        self._runner_thread: QThread | None = None
        self._runner_worker: AutoRngWorker | None = None
        self._last_failed_progress_message: str | None = None
        self._target_version = GameVersion.BD
        self._targets: list[tuple[StaticEncounterRecord, StateFilter, str]] = []
        self._delay_profiles: dict[int, DelayProfile] = {}
        self._delay_species_id: int | None = None
        self._delay_strategy_config = DelayStrategyConfig()
        self._delay_sample_rounds: list[DelaySampleRound] = []
        self._delay_show_sample_time = False
        self._active_delay_by_species: dict[int, int] = {}
        self._active_delay: int | None = None
        self._updating_fixed_delay = False
        self._runtime_trigger_advances: int | None = None
        self._runtime_step_index = 0
        self._runtime_phase: AutoRngPhase | None = None
        self._runtime_completed_steps: set[int] = set()
        self._runtime_loop_index = 0
        self._runtime_candidate_total = 0
        self._runtime_script_editor_expanded = True
        self._config_state_tracking_ready = False
        self._settings = settings or QSettings("auto-bdsp-rng", "AutoRngPanel")
        self._build_ui()
        self.refresh_scripts()
        self._restore_panel_state()
        self._sync_run_controls()
        self._saved_script_values = self._script_values()
        self._connect_config_state_tracking()
        self._config_state_tracking_ready = True
        self._set_config_saved(True)
        self._mark_scripts_dirty()
        self._update_script_status()

    def _build_ui(self) -> None:
        self.setObjectName("AutoRngPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.toolbar = self._build_toolbar()
        layout.addWidget(self.toolbar)

        content = QWidget(self)
        content.setObjectName("AutoRngContent")
        self.content_grid = QGridLayout(content)
        self.content_grid.setContentsMargins(0, 0, 0, 0)
        self.content_grid.setHorizontalSpacing(0)
        self.content_grid.setVerticalSpacing(0)
        self.config_panel = self._build_config_panel()
        self.runtime_panel = self._build_runtime_panel()
        self.workspace_splitter = WorkspaceSplit(self._settings, "static")
        self.workspace_splitter.addWidget(self.config_panel)
        self.workspace_splitter.addWidget(self.runtime_panel)
        self.workspace_splitter.restore_sizes()
        self.content_grid.addWidget(self.workspace_splitter, 0, 0, 1, 2)
        # Keep the old message widgets as compatibility state surfaces.  The
        # visible message and log entry now live in the main window footer.
        self.content_grid.addWidget(self._build_log_group(), 1, 0, 1, 2)
        self.log_group.hide()
        self.content_grid.setColumnStretch(0, 0)
        self.content_grid.setColumnStretch(1, 1)
        self.content_grid.setRowStretch(0, 1)
        self.content_grid.setRowStretch(1, 0)

        layout.addWidget(content, 1)
        self._apply_panel_style()

    def _sync_run_controls(self) -> None:
        """Keep the toolbar status hint in step with lifecycle controls."""

        super()._sync_run_controls()
        self._update_toolbar_status()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._save_panel_state()
        super().closeEvent(event)

    def _build_toolbar(self) -> QWidget:
        toolbar = QFrame()
        toolbar.setObjectName("AutoRngToolbar")
        toolbar.setFixedHeight(56)
        row = QHBoxLayout(toolbar)
        row.setContentsMargins(18, 0, 18, 0)
        row.setSpacing(0)
        row.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("单次", "single")
        self.mode_combo.addItem("循环 N 次", "count")
        self.mode_combo.addItem("无限循环", "infinite")
        self.loop_count = self._spin(1, 9999, 1)
        self.start_button = PrimaryToolButton()
        self.start_button.setIcon(workspace_icon("play", "#FFFFFF"))
        self.start_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.start_button.setText("开始")
        self.start_button.setObjectName("PrimaryButton")
        self.start_button.setAccessibleName("开始自动定点乱数")
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
        self.stop_button.setIcon(workspace_icon("stop", "#AC4B42"))
        self.stop_button.setObjectName("DangerButton")
        self.stop_button.setAccessibleName("停止自动定点乱数")
        self.status_badge = QLabel("状态：空闲")
        self.status_badge.setObjectName("Badge")
        self.status_badge.hide()
        self.debug_output_check = QCheckBox("调试")
        self.debug_output_check.setToolTip("输出 CLI 耗时、时间戳等调试信息")
        self.debug_output_check.setFixedHeight(34)
        self.toolbar_status = QLabel("准备就绪 · 检查配置后开始")
        self.toolbar_status.setObjectName("ToolbarStatus")
        self.toolbar_status.setProperty("state", "idle")
        self.toolbar_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.toolbar_status.setMinimumWidth(220)
        self.toolbar_status.setMinimumHeight(30)
        self.toolbar_status.setToolTip(
            "开始前请确认目标、过帧脚本和撞闪脚本；运行中会显示当前阶段。"
        )

        # 工具栏保留稍大的点击区域，工作区字段使用 32px 紧凑高度。
        self.status_badge.setFixedHeight(34)
        self.mode_combo.setFixedHeight(34)
        self.mode_combo.setFixedWidth(120)
        self.loop_count.setFixedHeight(34)
        self.loop_count.setFixedWidth(70)
        self.start_button.setFixedHeight(34)
        self.start_button.setMinimumWidth(88)
        self.stop_button.setFixedHeight(34)
        self.stop_button.setFixedWidth(104)

        self.start_button.clicked.connect(self._start_clicked)
        self.start_from_seed_action.triggered.connect(self._start_clicked)
        self.start_from_capture_action.triggered.connect(self._start_from_capture_clicked)
        self.start_from_reidentify_action.triggered.connect(self._start_from_reidentify_clicked)
        self.stop_button.clicked.connect(self._stop_clicked)

        left_layout = QHBoxLayout()
        left_layout.setSpacing(10)
        left_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        mode_label = QLabel("运行模式")
        mode_label.setObjectName("ToolbarFieldLabel")
        left_layout.addWidget(mode_label)
        left_layout.addWidget(self.mode_combo)
        self.loop_count_label = QLabel("次数")
        self.loop_count_label.setObjectName("ToolbarFieldLabel")
        left_layout.addWidget(self.loop_count_label)
        left_layout.addWidget(self.loop_count)
        left_layout.addWidget(self.debug_output_check)
        self.mode_combo.currentIndexChanged.connect(self._update_loop_count_visibility)
        self._update_loop_count_visibility()

        self.capture_info_button = QPushButton("OCR设置")
        self.capture_info_button.setObjectName("SecondaryButton")
        self.capture_info_button.setAccessibleName("打开 OCR 设置")
        self.capture_info_button.setFixedHeight(34)
        self.capture_info_button.setMinimumWidth(120)
        self.capture_info_button.setToolTip("打开 OCR 区域设置窗口")
        self.capture_info_button.clicked.connect(self.captureInfoRequested.emit)

        right_layout = QHBoxLayout()
        right_layout.setSpacing(8)
        right_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        right_layout.addWidget(self.capture_info_button)
        right_layout.addWidget(self.start_button)
        right_layout.addWidget(self.stop_button)

        row.addLayout(left_layout)
        row.addStretch(1)
        row.addWidget(self.toolbar_status)
        row.addStretch(1)
        row.addLayout(right_layout)
        return toolbar

    def _build_config_panel(self) -> QScrollArea:
        panel = QScrollArea()
        panel.setObjectName("AutoRngConfigPanel")
        panel.setWidgetResizable(True)
        panel.setFrameShape(QFrame.Shape.NoFrame)
        panel.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        panel.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        panel.setMinimumWidth(326)
        panel.setMaximumWidth(16777215)

        contents = QWidget()
        contents.setObjectName("AutoRngConfigContents")
        contents.setMinimumWidth(300)
        layout = QVBoxLayout(contents)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel("任务配置")
        title.setObjectName("SectionTitle")
        self.config_saved_label = QLabel("已保存")
        self.config_saved_label.setObjectName("ConfigSavedLabel")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.config_saved_label)
        layout.addLayout(header)

        layout.addWidget(self._build_target_summary_group())
        self.strategy_group = self._build_strategy_group()
        layout.addWidget(self.strategy_group)

        footer = QFrame()
        footer.setObjectName("ConfigFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 10, 0, 0)
        footer_layout.setSpacing(8)
        note = QLabel("常规配置下次启动生效\ndelay 策略下轮生效")
        note.setObjectName("MutedLabel")
        note.setToolTip(
            "保存会记住当前配置；正在运行的任务继续使用启动时的常规参数。\n"
            "delay 策略在每轮开始时重新计算，本轮冻结值不变。"
        )
        self.save_config_button = QPushButton("保存")
        self.save_config_button.setObjectName("ConfigSaveButton")
        self.save_config_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_config_button.setAccessibleName("保存任务配置")
        self.save_config_button.setToolTip("保存左侧任务配置；右侧脚本选择单独保存。点击开始时会自动保存全部配置。")
        self.save_config_button.clicked.connect(self._save_config_state)
        footer_layout.addWidget(note)
        footer_layout.addStretch(1)
        footer_layout.addWidget(self.save_config_button)
        layout.addWidget(footer)
        layout.addStretch(1)

        panel.setWidget(contents)
        self.config_contents = contents
        return panel

    def _build_strategy_group(self) -> QGroupBox:
        group = QGroupBox()
        group.setObjectName("AutoRngStrategyGroup")
        group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        form = QFormLayout(group)
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(12)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.strategy_form = form
        self.max_advances = self._spin(0, 1_000_000_000, 100_000)
        self.max_advances.setSuffix(" 帧")
        self.fixed_delay = self._spin(0, QT_INT_MAX, 100)
        self.fixed_delay.setParent(group)
        self.fixed_delay.hide()
        self.max_wait_frames = self._spin(1, 1_000_000_000, 300)
        self.max_wait_frames.setSuffix(" 帧")
        self.delay_strategy_dialog = DelayStrategyDialog(self)
        self.delay_settings_button = DelaySummaryButton()
        self.delay_settings_button.setObjectName("SecondaryButton")
        self.delay_settings_button.setFixedSize(180, 32)
        self.delay_settings_button.setIcon(
            delay_lucide_icon("settings-2", "#626D79", 16)
        )
        self.delay_settings_button.setIconSize(QSize(16, 16))
        self.delay_active_label = QLabel("下轮预计 - 帧")
        self.delay_active_label.setObjectName("DelayActiveLabel")
        self.delay_settings_field = QWidget()
        self.delay_settings_field.setObjectName("DelaySettingsField")
        self.delay_settings_field.setFixedSize(180, 52)
        delay_field_layout = QVBoxLayout(self.delay_settings_field)
        delay_field_layout.setContentsMargins(0, 0, 0, 0)
        delay_field_layout.setSpacing(3)
        delay_field_layout.addWidget(self.delay_settings_button)
        delay_field_layout.addWidget(self.delay_active_label)
        self.delay_settings_button.clicked.connect(self.open_delay_strategy_dialog)
        self.delay_strategy_dialog.settingsEdited.connect(self._refresh_delay_dialog_preview)
        self.delay_strategy_dialog.settingsSaveRequested.connect(
            self._save_delay_strategy_draft
        )
        self.delay_strategy_dialog.clearSamplesRequested.connect(self._confirm_clear_delay_samples)
        self.delay_strategy_dialog.sampleExclusionRequested.connect(
            self.set_delay_sample_excluded
        )
        self.delay_strategy_dialog.showSampleTimeChanged.connect(
            self._set_delay_show_sample_time
        )
        self.fixed_delay.valueChanged.connect(self._legacy_fixed_delay_changed)
        self.strategy_dialog = AutoRngStrategyDialog(self)
        self.reseed_threshold_frames = self.strategy_dialog.reseed_threshold_frames
        self.reidentify_max_attempts = self.strategy_dialog.reidentify_max_attempts
        self.reidentify_failure_policy = self.strategy_dialog.reidentify_failure_policy
        self.reidentify_seed_max_attempts = self.strategy_dialog.reidentify_seed_max_attempts
        self.reseeding_threshold = self.strategy_dialog.reseeding_threshold
        self.strategy_settings_button = QPushButton("设置")
        self.strategy_settings_button.setObjectName("SecondaryButton")
        self.strategy_settings_button.setFixedSize(180, 32)
        self.strategy_settings_button.clicked.connect(self.open_strategy_dialog)
        self.shiny_threshold_seconds = QDoubleSpinBox()
        self.shiny_threshold_seconds.setRange(0.0, 999.0)
        self.shiny_threshold_seconds.setDecimals(3)
        self.shiny_threshold_seconds.setSingleStep(0.1)
        self.shiny_threshold_seconds.setValue(DEFAULT_SHINY_THRESHOLD_SECONDS)
        self.shiny_threshold_seconds.setSuffix(" 秒")
        set_c_locale(self.shiny_threshold_seconds)
        self.shiny_threshold_seconds.setFont(_ui_numeric_font(14))
        for spin in (self.max_advances, self.fixed_delay, self.max_wait_frames):
            spin.setFixedWidth(180)
        self.shiny_threshold_seconds.setFixedSize(180, 32)
        explained_rows = (
            (
                "搜索范围",
                self.max_advances,
                "设置当前 Seed 下搜索目标候选的最大帧数。\n"
                "数值越大，能搜索到更远的候选，但命中较远目标时需要更长的过帧时间。\n"
                "全国图鉴齐全的情况下，过 100 万帧大约需要 10 分钟。",
            ),
            (
                "delay 策略",
                self.delay_settings_field,
                "表示脚本等待结束后（无 _闪帧时为脚本启动后）到实际撞到目标之间经过的帧数。\n"
                "含 _闪帧的旧脚本按“目标帧 - delay - _闪帧”启动；无 _闪帧时由软件等待到“目标帧 - delay”再启动；"
                "delay 越大，撞闪脚本启动得越早。\n点击编辑固定或动态 delay 策略。",
            ),
            (
                "最大等待",
                self.max_wait_frames,
                "决定何时停止运行过帧脚本，改为软件实时等待。\n"
                "距离撞闪脚本启动帧不超过该帧数时，不再运行过帧脚本，而是根据当前活帧等待到启动时机。\n"
                "数值越大，流程越早进入实时等待；数值越小，越依赖过帧脚本接近目标。",
            ),
            (
                "闪光阈值",
                self.shiny_threshold_seconds,
                "使用 OCR 测量战斗文本“出现了！”到“去吧/上吧”之间的时间间隔。\n"
                "测得的间隔大于或等于该值时，判定为疑似闪光并停止自动流程。\n"
                "艾姆利多和克雷色利亚会先等待脚本检测到进入战斗，再启动 OCR。\n"
                "可先在 Seed 捕获页面使用“校准闪光判定”测量合适的阈值。\n"
                "设为 0 时关闭自动 OCR 判闪。",
            ),
        )
        for label_text, field, tooltip in explained_rows[:3]:
            form.addRow(label_text, field)
            field.setToolTip(tooltip)
            label = form.labelForField(field)
            if label is not None:
                label.setToolTip(tooltip)
            if field is self.delay_settings_field:
                self.delay_settings_label = label
                self.fixed_delay.setToolTip(tooltip)
                self.delay_settings_button.setToolTip(tooltip)

        self.more_strategy_button = QToolButton()
        self.more_strategy_button.setObjectName("MoreStrategyButton")
        self.more_strategy_button.setText("更多策略 · 4 项")
        self.more_strategy_button.setCheckable(True)
        self.more_strategy_button.setArrowType(Qt.ArrowType.RightArrow)
        self.more_strategy_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.more_strategy_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.more_strategy_button.setAccessibleName("展开更多策略")
        self.more_strategy_button.setFixedHeight(30)
        self.more_strategy_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        form.addRow(self.more_strategy_button)

        shiny_tooltip = explained_rows[3][2]
        form.addRow(explained_rows[3][0], self.shiny_threshold_seconds)
        self.shiny_threshold_seconds.setToolTip(shiny_tooltip)
        shiny_label = form.labelForField(self.shiny_threshold_seconds)
        if shiny_label is not None:
            shiny_label.setToolTip(shiny_tooltip)

        self.sync_field = QWidget()
        self.sync_field.setObjectName("CompactStrategyField")
        self.sync_field.setFixedSize(180, 32)
        sync_row = QHBoxLayout(self.sync_field)
        sync_row.setContentsMargins(0, 0, 0, 0)
        sync_row.setSpacing(6)
        self.sync_combo = QComboBox()
        self.sync_combo.addItems(["关闭", "首位普通精灵", "首位同步精灵"])
        self.sync_combo.setFixedHeight(32)
        self.sync_combo.setMinimumWidth(112)
        self.sync_combo.currentIndexChanged.connect(self._on_sync_changed)
        self.sync_nature_input = QLineEdit()
        self.sync_nature_input.setPlaceholderText("性格")
        self.sync_nature_input.setFixedHeight(32)
        self.sync_nature_input.setFixedWidth(62)
        self.sync_nature_input.setEnabled(False)
        sync_row.addWidget(self.sync_combo)
        sync_row.addWidget(self.sync_nature_input)
        form.addRow("同步", self.sync_field)

        self.reverse_field = QWidget()
        self.reverse_field.setObjectName("CompactStrategyField")
        self.reverse_field.setFixedSize(180, 32)
        reverse_row = QHBoxLayout(self.reverse_field)
        reverse_row.setContentsMargins(0, 0, 0, 0)
        reverse_row.setSpacing(6)
        self.auto_reverse_combo = QComboBox()
        self.auto_reverse_combo.addItems(["关闭", "开启"])
        self.auto_reverse_combo.setFixedHeight(32)
        self.auto_reverse_combo.setMinimumWidth(88)
        self.auto_reverse_combo.currentIndexChanged.connect(
            lambda _index: self._refresh_delay_ui()
        )
        self.reverse_lookup_window = QSpinBox()
        self.reverse_lookup_window.setRange(0, 10_000)
        self.reverse_lookup_window.setValue(500)
        self.reverse_lookup_window.setPrefix("±")
        self.reverse_lookup_window.setSuffix(" 帧")
        self.reverse_lookup_window.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.reverse_lookup_window.setFixedHeight(32)
        self.reverse_lookup_window.setFixedWidth(86)
        set_c_locale(self.reverse_lookup_window)
        self.reverse_lookup_window.setFont(_ui_numeric_font(14))
        reverse_row.addWidget(self.auto_reverse_combo)
        reverse_row.addWidget(self.reverse_lookup_window)
        form.addRow("自动反查", self.reverse_field)
        form.addRow("校正策略", self.strategy_settings_button)

        self._advanced_strategy_fields = (
            self.shiny_threshold_seconds,
            self.sync_field,
            self.reverse_field,
            self.strategy_settings_button,
        )
        self.more_strategy_button.toggled.connect(self._set_advanced_strategies_visible)
        self._set_advanced_strategies_visible(False)
        self._refresh_delay_ui()
        return group

    def open_delay_strategy_dialog(self) -> None:
        self.delay_strategy_dialog.set_values(self._delay_strategy_config)
        self.delay_strategy_dialog.set_show_time(self._delay_show_sample_time)
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

    @Slot(object)
    def _save_delay_strategy_draft(self, config: object) -> None:
        if not isinstance(config, DelayStrategyConfig):
            return
        self._commit_delay_strategy_config(config, persist=True, emit=True)

    def delay_strategy_config(self) -> DelayStrategyConfig:
        return self._delay_strategy_config

    def delay_samples(self) -> list[tuple[int, ...]]:
        return [sample.candidates for sample in self._delay_sample_rounds if not sample.excluded]

    def delay_sample_records(self, species_id: int | None = None) -> list[DelaySampleRound]:
        target_species = self._delay_species_id if species_id is None else int(species_id)
        if target_species is None:
            return []
        profile = self._delay_profiles.get(target_species)
        return [] if profile is None else list(profile.samples)

    def effective_delay_for_next_round(self) -> int:
        return self._estimate_delay(self._delay_strategy_config).value

    def effective_delay_for_species(self, species_id: int | None) -> int:
        if species_id is None:
            return self.effective_delay_for_next_round()
        profile = self._profile_for_species(int(species_id))
        return estimate_delay(
            profile.config,
            profile.samples,
            reference_delay=self._active_delay_by_species.get(int(species_id)),
        ).value

    @Slot(object)
    def record_delay_sample(
        self,
        candidates: object,
        *,
        species_id: int | None = None,
        observed_at: str | None = None,
    ) -> None:
        target_species = self._delay_species_id if species_id is None else int(species_id)
        if target_species is None:
            return
        profile = self._profile_for_species(target_species)
        sample = DelaySampleRound.from_candidates(
            candidates,
            round_number=profile.next_round_number,
            observed_at=observed_at or datetime.now().astimezone().isoformat(timespec="seconds"),
        )
        if not sample.candidates:
            return
        profile.samples.append(sample)
        profile.next_round_number += 1
        if target_species == self._delay_species_id:
            self._delay_sample_rounds = profile.samples
        self._save_delay_samples()
        self._refresh_delay_ui()
        self.delaySamplesChanged.emit(
            [row.candidates for row in profile.samples if not row.excluded]
        )

    @Slot(int, bool)
    def set_delay_sample_excluded(self, round_number: int, excluded: bool) -> None:
        if self._delay_species_id is None:
            return
        profile = self._profile_for_species(self._delay_species_id)
        for index, sample in enumerate(profile.samples):
            if sample.round_number != int(round_number) or sample.excluded == bool(excluded):
                continue
            profile.samples[index] = replace(sample, excluded=bool(excluded))
            self._delay_sample_rounds = profile.samples
            self._save_delay_samples()
            self._refresh_delay_ui()
            self.delaySamplesChanged.emit(self.delay_samples())
            return

    @Slot()
    def clear_delay_samples(self) -> None:
        if not self._delay_sample_rounds:
            return
        self._delay_sample_rounds.clear()
        if self._delay_species_id is not None:
            profile = self._profile_for_species(self._delay_species_id)
            profile.next_round_number = 1
        self._save_delay_samples()
        self._refresh_delay_ui()
        self.delaySamplesChanged.emit([])
        self.delaySamplesCleared.emit()

    @Slot()
    def _confirm_clear_delay_samples(self) -> None:
        if not self._delay_sample_rounds:
            return
        # The dialog already collected confirmation inline. Let the sending
        # button finish dispatching before the refresh disables it.
        QTimer.singleShot(0, self.clear_delay_samples)

    @Slot(object)
    def set_active_delay(self, value: object, *, species_id: int | None = None) -> None:
        target_species = self._delay_species_id if species_id is None else int(species_id)
        if target_species is None:
            return
        if value is None:
            self._active_delay_by_species.pop(target_species, None)
        else:
            self._active_delay_by_species[target_species] = max(0, int(value))
        if target_species == self._delay_species_id:
            self._active_delay = self._active_delay_by_species.get(target_species)
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
            species_name=self._current_delay_species_name(),
            recommended_delay=get_recommended_delay(self._delay_species_id),
            auto_reverse_enabled=self.auto_reverse_combo.currentIndex() == 1,
            active_delay=self._active_delay,
            estimate=estimate,
            sample_rounds=self._delay_sample_rounds,
        )

    def _refresh_delay_ui(self) -> None:
        estimate = self._estimate_delay(self._delay_strategy_config)
        strategy_id = self._delay_strategy_config.strategy.value
        label = DELAY_STRATEGY_LABEL_BY_ID[strategy_id]
        self.delay_settings_button.set_summary(label, estimate.value)
        if hasattr(self, "delay_active_label"):
            self.delay_active_label.setText(f"下轮预计 {estimate.value} 帧")
        if hasattr(self, "runtime_delay_value"):
            self.runtime_delay_value.setText(self._runtime_value(self._active_delay))
        self._refresh_previous_delay_summary()
        tooltip = (
            "delay 越大，撞闪脚本启动得越早。点击编辑固定或动态 delay 策略。\n"
            "策略修改从下一轮生效，本轮冻结值不变。\n"
            f"本轮使用：{'-' if self._active_delay is None else self._active_delay}\n"
            f"下轮预计：{estimate.value}\n"
            f"有效样本：{estimate.valid_round_count} 轮"
        )
        self.delay_settings_button.setToolTip(tooltip)
        if getattr(self, "delay_settings_label", None) is not None:
            self.delay_settings_label.setToolTip(tooltip)
        self._refresh_delay_dialog_preview()

    def _refresh_previous_delay_summary(self) -> None:
        if not hasattr(self, "previous_round_label"):
            return
        if not self._delay_sample_rounds:
            self.previous_round_label.setText("暂无 delay 样本")
            return
        sample = self._delay_sample_rounds[-1]
        values = ", ".join(str(value) for value in sample.candidates) or "—"
        status = "已排除" if sample.excluded else "已纳入统计"
        self.previous_round_label.setText(
            f"最近样本 · 第 {sample.round_number} 轮 · delay {values} 帧 · {status}"
        )

    def _commit_delay_strategy_config(
        self,
        config: DelayStrategyConfig,
        *,
        persist: bool,
        emit: bool,
    ) -> None:
        self._delay_strategy_config = config
        if self._delay_species_id is not None:
            self._profile_for_species(self._delay_species_id).config = config
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
        if self._delay_species_id is not None:
            self._profile_for_species(self._delay_species_id).config = self._delay_strategy_config
        if not self.delay_strategy_dialog.isVisible():
            self.delay_strategy_dialog.set_values(self._delay_strategy_config)
        self._refresh_delay_ui()

    @Slot(bool)
    def _set_delay_show_sample_time(self, checked: bool) -> None:
        self._delay_show_sample_time = bool(checked)
        self._settings.setValue("delay_show_sample_time", self._delay_show_sample_time)

    def _current_delay_species(self) -> int | None:
        if not hasattr(self, "target_form"):
            return None
        try:
            targets = self.targets()
        except (TypeError, ValueError):
            return None
        if not targets:
            return None
        return int(targets[0][0].template.species)

    def _current_delay_species_name(self) -> str:
        if not hasattr(self, "target_form"):
            return "未选择"
        try:
            targets = self.targets()
        except (TypeError, ValueError):
            return "未选择"
        if not targets:
            return "未选择"
        record = targets[0][0]
        return POKEMON_LABELS_ZH.get(record.description, record.description)

    def _profile_for_species(self, species_id: int) -> DelayProfile:
        profile = self._delay_profiles.get(int(species_id))
        if profile is None:
            profile = DelayProfile()
            self._delay_profiles[int(species_id)] = profile
        return profile

    def _activate_delay_profile(self, species_id: int | None) -> None:
        if species_id is None:
            return
        species_id = int(species_id)
        profile = self._profile_for_species(species_id)
        self._delay_species_id = species_id
        self._delay_strategy_config = profile.config
        self._delay_sample_rounds = profile.samples
        self._active_delay = self._active_delay_by_species.get(species_id)
        self._updating_fixed_delay = True
        try:
            self.fixed_delay.setValue(profile.config.baseline_delay)
        finally:
            self._updating_fixed_delay = False
        self.delay_strategy_dialog.set_values(profile.config)
        self._refresh_delay_ui()

    def open_strategy_dialog(self) -> None:
        original_values = self.strategy_dialog.values()
        if self.strategy_dialog.exec() == QDialog.DialogCode.Accepted:
            self._save_strategy_settings()
            return
        self.strategy_dialog.set_values(*original_values)

    def _build_script_group(self) -> QGroupBox:
        group = QGroupBox()
        group.setObjectName("AutoRngScriptGroup")
        group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = QGridLayout(group)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(16)
        layout.setVerticalSpacing(4)

        self.script_group_title = QLabel("任务脚本")
        self.script_group_title.setObjectName("SectionTitle")
        self.script_group_title.setParent(group)
        self.script_group_title.hide()  # The persistent section header owns the title.
        self.script_status_label = QLabel("脚本待配置")
        self.script_status_label.setObjectName("ScriptStatusLabel")
        self.script_status_label.setProperty("state", "warning")
        self.script_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.script_status_label.setMinimumWidth(92)
        self.refresh_scripts_button = QPushButton("刷新")
        self.refresh_scripts_button.setObjectName("AutoRngRefreshScripts")
        # Show the focus cue for keyboard navigation, without retaining it after mouse clicks.
        self.refresh_scripts_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.refresh_scripts_button.setIcon(workspace_icon("refresh", "#087C58"))
        self.refresh_scripts_button.setToolTip("刷新脚本列表")
        self.refresh_scripts_button.setAccessibleName("刷新任务脚本列表")
        self.refresh_scripts_button.setFixedSize(72, 32)
        self.refresh_scripts_button.setStyleSheet("QPushButton {color: #087C58; border: 0; background: transparent; padding: 0;} QPushButton:disabled {color: #9AA8A1;}")
        self.refresh_scripts_button.clicked.connect(self.refresh_scripts)
        self.runtime_script_header.layout().insertWidget(1, self.script_status_label)
        self.runtime_script_header.layout().insertWidget(2, self.refresh_scripts_button)

        def combo_factory() -> _RefreshingScriptComboBox:
            return _RefreshingScriptComboBox(lambda: self.refresh_scripts())

        self.seed_script_combo = combo_factory()
        self.advance_script_combo = combo_factory()
        self.hit_script_combo = combo_factory()
        self.escape_script_combo = combo_factory()
        self.exit_script_combo = combo_factory()
        self.reverse_script_combo = combo_factory()
        self.script_edit_buttons: dict[QComboBox, QToolButton] = {}
        self.script_picker_widgets: dict[QComboBox, QWidget] = {}
        for combo in self._script_combos():
            combo.setFixedHeight(32)
            combo.setMinimumWidth(160)
            combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.escape_continue_check = QCheckBox("未命中时逃跑续搜")
        self.escape_continue_check.setFixedHeight(30)
        self.escape_continue_check.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
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
        self.escape_continue_check.toggled.connect(lambda: self._update_script_edit_button(self.escape_script_combo))
        script_fields = (
            ("测种脚本", self.seed_script_combo, 0, 0),
            ("过帧脚本", self.advance_script_combo, 0, 1),
            ("撞闪脚本", self.hit_script_combo, 3, 0),
            ("过场脚本", self.exit_script_combo, 3, 1),
            ("反查脚本", self.reverse_script_combo, 6, 0),
            ("逃跑脚本", self.escape_script_combo, 6, 1),
        )
        self.script_labels: dict[QComboBox, QLabel] = {}
        for label_text, combo, row, column in script_fields:
            label = QLabel(label_text)
            label.setObjectName("ScriptFieldLabel")
            combo.setAccessibleName(label_text)
            label.setBuddy(combo)
            self.script_labels[combo] = label
            picker = QWidget(group)
            picker.setObjectName("ScriptPicker")
            picker_layout = QHBoxLayout(picker)
            picker_layout.setContentsMargins(0, 0, 0, 0)
            picker_layout.setSpacing(5)
            picker_layout.addWidget(combo, 1)
            edit_button = QToolButton(picker)
            edit_button.setObjectName("ScriptEditButton")
            edit_button.setFixedSize(32, 32)
            edit_button.setIcon(delay_lucide_icon("square-pen", "#626D79", 16))
            edit_button.setIconSize(QSize(16, 16))
            edit_button.setToolTip(f"编辑{label_text}")
            edit_button.setAccessibleName(f"编辑{label_text}")
            edit_button.setCursor(Qt.CursorShape.PointingHandCursor)
            edit_button.clicked.connect(
                lambda _checked=False, selected_combo=combo: self._request_script_edit(
                    selected_combo
                )
            )
            combo.currentIndexChanged.connect(
                lambda _index, selected_combo=combo: self._update_script_edit_button(
                    selected_combo
                )
            )
            combo.currentIndexChanged.connect(lambda _index: self._update_script_status())
            picker_layout.addWidget(edit_button)
            self.script_edit_buttons[combo] = edit_button
            self.script_picker_widgets[combo] = picker
            layout.addWidget(label, row + 1, column)
            layout.addWidget(picker, row + 2, column)
        layout.addWidget(
            self.escape_continue_check,
            10,
            0,
            1,
            2,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )
        for spacer_row in (3, 6, 9):
            layout.setRowMinimumHeight(spacer_row, 6)
        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 1)
        self.escape_continue_check.toggled.connect(self._update_script_status)
        self._update_script_status()
        return group

    def _build_runtime_panel(self) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setObjectName("AutoRngRuntimePanel")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        panel = QWidget()
        panel.setObjectName("AutoRngRuntimeContent")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(8)
        runtime_title = QLabel("运行现场")
        runtime_title.setObjectName("SectionTitle")
        self.runtime_log_button = QPushButton("轮次记录")
        self.runtime_log_button.setIcon(workspace_icon("external", "#687480"))
        self.runtime_log_button.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.view_round_button = self.runtime_log_button
        self.runtime_log_button.setObjectName("InlineLinkButton")
        self.runtime_log_button.setAccessibleName("查看轮次记录")
        self.runtime_log_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.runtime_log_button.clicked.connect(self.roundRecordsRequested.emit)
        header.addWidget(runtime_title)
        header.addStretch(1)
        header.addWidget(self.runtime_log_button)
        layout.addLayout(header)

        self.runtime_card = QFrame()
        self.runtime_card.setObjectName("RuntimeCard")
        add_card_shadow(self.runtime_card)
        self.runtime_card.setProperty("state", "idle")
        self.runtime_card.setMinimumHeight(220)
        self.runtime_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        runtime_layout = QVBoxLayout(self.runtime_card)
        runtime_layout.setContentsMargins(16, 13, 16, 12)
        runtime_layout.setSpacing(6)

        runtime_top = QHBoxLayout()
        runtime_top.setSpacing(8)
        self.runtime_state_dot = QLabel("●")
        self.runtime_state_dot.setObjectName("RuntimeStateDot")
        self.runtime_phase_label = QLabel("准备就绪")
        self.runtime_phase_label.setObjectName("RuntimePhaseLabel")
        self.runtime_round_label = QLabel("任务已停止")
        self.runtime_round_label.setObjectName("RuntimeRoundLabel")
        runtime_top.addWidget(self.runtime_state_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        runtime_top.addWidget(self.runtime_phase_label, 0, Qt.AlignmentFlag.AlignVCenter)
        runtime_top.addStretch(1)
        runtime_top.addWidget(self.runtime_round_label, 0, Qt.AlignmentFlag.AlignVCenter)
        runtime_layout.addLayout(runtime_top)

        self.runtime_description_label = QLabel(
            "请先确认目标并选择过帧、撞闪脚本，然后点击右上角“开始”。"
        )
        self.runtime_description_label.setObjectName("RuntimeDescriptionLabel")
        self.runtime_description_label.setWordWrap(True)
        self.runtime_description_label.setTextFormat(Qt.TextFormat.PlainText)
        self.runtime_description_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        description_row = QHBoxLayout()
        description_row.setSpacing(12)
        description_row.addWidget(self.runtime_description_label, 1)
        self.runtime_setup_button = QPushButton("补全脚本")
        self.runtime_setup_button.setObjectName("RuntimeSetupButton")
        self.runtime_setup_button.setMinimumSize(88, 32)
        self.runtime_setup_button.setAccessibleName("定位到第一个缺少的必需脚本")
        self.runtime_setup_button.clicked.connect(self._focus_missing_script)
        description_row.addWidget(self.runtime_setup_button)
        runtime_layout.addLayout(description_row)
        self.runtime_steps = self._build_runtime_steps()
        self.runtime_steps.setToolTip("仅标记实际经过的阶段；流程可跳过、回跳或重新测种。")

        metrics = QHBoxLayout()
        metrics.setContentsMargins(0, 3, 0, 3)
        metrics.setSpacing(16)
        (current_metric, self.runtime_current_value) = self._runtime_metric("当前帧数")
        (target_metric, self.runtime_target_value) = self._runtime_metric("目标帧数")
        (remaining_metric, self.runtime_remaining_value) = self._runtime_metric("距离撞闪启动", accent=True)
        current_metric.setToolTip("过帧脚本执行期间的数值来自最近一次定位及计时估算；脚本完成后以校正结果为准。")
        remaining_metric.setToolTip("扣除本轮 delay 和撞闪脚本等待帧数后的启动点，与当前帧数之间的差值。")
        metrics.addWidget(current_metric, 105)
        metrics.addWidget(target_metric, 100)
        metrics.addWidget(remaining_metric, 90)
        runtime_layout.addLayout(metrics)
        self.runtime_insights = RuntimeInsights(self)
        runtime_layout.addWidget(self.runtime_insights)
        runtime_layout.addWidget(self.runtime_steps)

        runtime_footer = QFrame()
        runtime_footer.setObjectName("RuntimeFooter")
        runtime_footer_layout = QHBoxLayout(runtime_footer)
        runtime_footer_layout.setContentsMargins(0, 8, 0, 0)
        runtime_footer_layout.setSpacing(6)
        runtime_delay_caption = QLabel("本轮 delay")
        runtime_delay_caption.setObjectName("MutedLabel")
        self.runtime_delay_value = QLabel("—")
        self.runtime_delay_value.setObjectName("RuntimeMonoSmall")
        self.runtime_delay_value.setFont(_ui_numeric_font(14, QFont.Weight.DemiBold))
        self.runtime_delay_state_label = QLabel("已冻结")
        self.runtime_delay_state_label.setObjectName("RuntimeDelayState")
        self.runtime_delay_state_label.hide()
        runtime_footer_layout.addWidget(runtime_delay_caption)
        runtime_footer_layout.addWidget(self.runtime_delay_value)
        runtime_footer_layout.addWidget(QLabel("帧"))
        runtime_footer_layout.addWidget(self.runtime_delay_state_label)
        runtime_footer_layout.addStretch(1)
        self.target_data_button = QPushButton("查看目标数据")
        self.target_data_button.setIcon(workspace_icon("external", "#687480"))
        self.target_data_button.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.target_data_button.setObjectName("InlineLinkButton")
        self.target_data_button.setAccessibleName("查看目标数据")
        self.target_data_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.target_data_button.clicked.connect(self.targetDataRequested.emit)
        runtime_footer_layout.addWidget(self.target_data_button)
        runtime_layout.addWidget(runtime_footer)
        layout.addWidget(self.runtime_card)

        self.previous_round_label = QLabel("暂无 delay 样本")
        self.previous_round_label.setObjectName("PreviousRoundLabel")
        self.previous_round_label.setMinimumHeight(24)
        self.previous_round_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        layout.addWidget(self.previous_round_label)

        self.candidate_section = self._build_candidate_section()
        layout.addWidget(self.candidate_section)

        self.runtime_script_header = self._build_runtime_script_header()
        layout.addWidget(self.runtime_script_header)
        self.runtime_script_summary = self._build_runtime_script_summary()
        layout.addWidget(self.runtime_script_summary)
        self.script_group = self._build_script_group()
        layout.addWidget(self.script_group)
        layout.addStretch(1)
        scroll.setWidget(panel)
        self.runtime_content = panel
        self._refresh_runtime_steps(AutoRngPhase.IDLE)
        self._set_runtime_script_summary_visible(True)
        return scroll

    def _build_runtime_script_header(self) -> QWidget:
        header = QWidget()
        header.setObjectName("RuntimeScriptHeader")
        row = QHBoxLayout(header)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        title = QLabel("任务脚本")
        title.setObjectName("SectionTitle")
        row.addWidget(title)
        row.addStretch(1)
        state = QLabel("下次启动生效")
        state.setObjectName("RuntimeScriptHeaderState")
        state.setParent(header)
        state.hide()
        header.setToolTip("脚本选择下次启动生效；点击开始时会自动保存全部配置。")
        self.script_save_state_label = QLabel("已保存")
        self.script_save_state_label.setObjectName("ScriptSaveStateLabel")
        row.addWidget(self.script_save_state_label)
        self.save_scripts_button = QPushButton("保存脚本选择")
        self.save_scripts_button.setObjectName("ScriptSaveButton")
        self.save_scripts_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_scripts_button.setToolTip("保存本区域的脚本选择及逃跑续搜开关，下次启动生效；脚本内容请通过编辑按钮修改。")
        self.save_scripts_button.clicked.connect(self._save_script_state)
        row.addWidget(self.save_scripts_button)
        header.hide()
        return header

    def _build_runtime_script_summary(self) -> QWidget:
        card = QFrame()
        card.setObjectName("RuntimeScriptSummary")
        card_layout = QHBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(10)
        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)
        self.runtime_script_summary_title = QLabel("自动定点脚本配置")
        self.runtime_script_summary_title.setObjectName("RuntimeScriptSummaryTitle")
        self.runtime_script_summary_detail = QLabel("尚未选择脚本")
        self.runtime_script_summary_detail.setObjectName("RuntimeScriptSummaryDetail")
        self.runtime_script_summary_detail.setWordWrap(True)
        self.runtime_script_summary_title.setParent(card)
        self.runtime_script_summary_title.hide()
        text_layout.addWidget(self.runtime_script_summary_detail)
        card_layout.addLayout(text_layout, 1)
        self.runtime_script_summary_toggle = QToolButton()
        self.runtime_script_summary_toggle.setObjectName("RuntimeScriptSummaryToggle")
        self.runtime_script_summary_toggle.setText("展开编辑")
        self.runtime_script_summary_toggle.setArrowType(Qt.ArrowType.DownArrow)
        self.runtime_script_summary_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.runtime_script_summary_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.runtime_script_summary_toggle.setAccessibleName("展开任务脚本编辑")
        self.runtime_script_summary_toggle.setMinimumSize(88, 32)
        self.runtime_script_summary_toggle.clicked.connect(self._toggle_runtime_script_editor)
        self.runtime_script_header.layout().addWidget(self.runtime_script_summary_toggle)
        card.hide()
        return card

    def _refresh_runtime_script_summary(self) -> None:
        if not hasattr(self, "runtime_script_summary_detail"):
            return
        script_specs = (
            ("测种", self.seed_script_combo),
            ("过帧", self.advance_script_combo),
            ("撞闪", self.hit_script_combo),
            ("反查", self.reverse_script_combo),
            ("过场", self.exit_script_combo),
            ("逃跑", self.escape_script_combo),
        )
        selected = [label for label, combo in script_specs if self._selected_path(combo) is not None]
        if selected:
            detail = f"已选择 {len(selected)} 个脚本 · " + " / ".join(selected)
        else:
            detail = "尚未选择脚本"
        self.runtime_script_summary_detail.setText(detail)
        self.runtime_script_summary_detail.setToolTip("\n".join(
            f"{label}：{self._selected_path(combo) or '未选择'}"
            for label, combo in script_specs
        ))
        species_name = self._current_delay_species_name()
        self.runtime_script_summary_title.setText(
            f"{species_name}脚本配置" if species_name != "未选择" else "自动定点脚本配置"
        )

    def _set_runtime_script_summary_visible(self, visible: bool) -> None:
        if (
            not hasattr(self, "runtime_script_summary")
            or not hasattr(self, "runtime_script_header")
            or not hasattr(self, "script_group")
        ):
            return
        if not visible:
            self._runtime_script_editor_expanded = False
        if hasattr(self, "previous_round_label"):
            self.previous_round_label.setVisible(not visible)
        self.runtime_script_header.setVisible(visible)
        self._refresh_runtime_script_summary()
        self.runtime_script_summary.setVisible(visible and not self._runtime_script_editor_expanded)
        self.script_group.setVisible(not visible or self._runtime_script_editor_expanded)
        self._sync_script_toggle()

    @Slot()
    def _toggle_runtime_script_editor(self) -> None:
        if not hasattr(self, "runtime_script_summary"):
            return
        self._runtime_script_editor_expanded = not self._runtime_script_editor_expanded
        self.script_group.setVisible(self._runtime_script_editor_expanded)
        self.runtime_script_summary.setVisible(not self._runtime_script_editor_expanded)
        self._sync_script_toggle()

    def _sync_script_toggle(self) -> None:
        self.runtime_script_summary_toggle.setText(
            "收起编辑" if self._runtime_script_editor_expanded else "展开编辑"
        )
        self.runtime_script_summary_toggle.setAccessibleName(
            "收起任务脚本编辑" if self._runtime_script_editor_expanded else "展开任务脚本编辑"
        )
        self.runtime_script_summary_toggle.setArrowType(
            Qt.ArrowType.UpArrow if self._runtime_script_editor_expanded else Qt.ArrowType.DownArrow
        )

    def _build_runtime_steps(self) -> QWidget:
        container = QWidget()
        container.setObjectName("RuntimeSteps")
        container.setToolTip("高亮为当前阶段，✓ 为本轮已经过的阶段；流程可跳过或返回某个阶段。")
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.runtime_step_circles: list[QLabel] = []
        self.runtime_step_labels: list[QLabel] = []
        self.runtime_step_connectors: list[QFrame] = []
        for index, label_text in enumerate(_RUNTIME_STEP_LABELS):
            item = QWidget(container)
            item_layout = QVBoxLayout(item)
            item_layout.setContentsMargins(0, 0, 0, 0)
            item_layout.setSpacing(4)
            circle = QLabel(str(index + 1))
            circle.setObjectName("RuntimeStepCircle")
            circle.setAlignment(Qt.AlignmentFlag.AlignCenter)
            circle.setFixedSize(22, 22)
            label = QLabel(label_text)
            label.setObjectName("RuntimeStepLabel")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            tooltip = _RUNTIME_STEP_TOOLTIPS[index]
            item.setToolTip(tooltip)
            circle.setToolTip(tooltip)
            label.setToolTip(tooltip)
            item_layout.addWidget(circle, 0, Qt.AlignmentFlag.AlignHCenter)
            item_layout.addWidget(label, 0, Qt.AlignmentFlag.AlignHCenter)
            row.addWidget(item, 1)
            self.runtime_step_circles.append(circle)
            self.runtime_step_labels.append(label)
            if index < len(_RUNTIME_STEP_LABELS) - 1:
                connector = QFrame(container)
                connector.setObjectName("RuntimeStepConnector")
                connector.setFrameShape(QFrame.Shape.HLine)
                connector.setFrameShadow(QFrame.Shadow.Plain)
                connector.setFixedHeight(1)
                row.addWidget(connector, 1, Qt.AlignmentFlag.AlignVCenter)
                self.runtime_step_connectors.append(connector)
        return container

    def _build_candidate_section(self) -> QWidget:
        section = QWidget()
        section.setObjectName("RuntimeCandidatesSection")
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        title = QLabel("本轮目标")
        title.setObjectName("SectionTitle")
        self.candidate_count_label = QLabel("0 条候选")
        self.candidate_count_label.setObjectName("CandidateCountLabel")
        self.candidate_count_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.candidate_count_label)
        layout.addLayout(header)

        self.candidate_table = QTableWidget(0, 5, section)
        self.candidate_table.setObjectName("RuntimeCandidateTable")
        self.candidate_table.setAccessibleName("本轮候选目标表")
        self.candidate_table.setFont(_ui_numeric_font(13))
        self.candidate_table.setHorizontalHeaderLabels(("状态", "帧数", "异色", "性格", "个体值"))
        self.candidate_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.candidate_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.candidate_table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.candidate_table.setAlternatingRowColors(True)
        self.candidate_table.setShowGrid(False)
        self.candidate_table.setItemDelegate(RowSeparatorDelegate(self.candidate_table))
        self.candidate_table.setWordWrap(False)
        self.candidate_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.candidate_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.candidate_table.verticalHeader().setVisible(False)
        self.candidate_table.verticalHeader().setDefaultSectionSize(30)
        table_header = self.candidate_table.horizontalHeader()
        table_header.setFixedHeight(30)
        table_header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        for column, width in enumerate((76, 100, 78, 82)):
            self.candidate_table.setColumnWidth(column, width)
        table_header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.candidate_table.hide()
        self.candidate_tools = TableWorkbench(self.candidate_table, header, self._settings, "candidates", pinned_columns=2)
        layout.addWidget(self.candidate_table)
        self.candidate_empty_label = QLabel("开始运行后显示本轮候选目标")
        self.candidate_empty_label.setObjectName("RuntimeCandidatesEmpty")
        self.candidate_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.candidate_empty_label.setMinimumHeight(42)
        self.candidate_empty_label.setWordWrap(True)
        layout.addWidget(self.candidate_empty_label)
        return section

    def _refresh_runtime_steps(self, phase: AutoRngPhase | str) -> None:
        try:
            phase_value = phase if isinstance(phase, AutoRngPhase) else AutoRngPhase(str(phase))
        except ValueError:
            return
        if phase_value == self._runtime_phase:
            return
        previous_index = _RUNTIME_STEP_PHASES.get(self._runtime_phase)
        active_index = _RUNTIME_STEP_PHASES.get(phase_value)
        if phase_value == AutoRngPhase.COMPLETED:
            if previous_index is not None:
                self._runtime_completed_steps.add(previous_index)
            self._runtime_completed_steps.add(5)
            terminal = "completed"
        elif phase_value == AutoRngPhase.IDLE:
            terminal = "idle"
            self._runtime_step_index = 0
            self._runtime_completed_steps.clear()
        elif phase_value == AutoRngPhase.FAILED:
            active_index = self._runtime_step_index
            terminal = "failed"
        else:
            terminal = "active"
            if previous_index is not None and previous_index != active_index:
                self._runtime_completed_steps.add(previous_index)
            if active_index is not None:
                self._runtime_completed_steps.discard(active_index)
                self._runtime_step_index = active_index
        self._runtime_phase = phase_value
        for index, circle in enumerate(self.runtime_step_circles):
            if index == active_index:
                if terminal == "failed":
                    circle.setText("!")
                    state = "failed"
                elif terminal == "active":
                    circle.setText(str(index + 1))
                    state = "active"
                else:
                    circle.setText(str(index + 1))
                    state = "idle"
            elif index in self._runtime_completed_steps:
                circle.setText("✓")
                state = "completed"
            else:
                circle.setText(str(index + 1))
                state = "idle"
            circle.setProperty("state", state)
            circle.style().unpolish(circle)
            circle.style().polish(circle)
            label = self.runtime_step_labels[index]
            label.setProperty("state", state)
            label.style().unpolish(label)
            label.style().polish(label)
        reached = self._runtime_completed_steps | ({active_index} if active_index is not None else set())
        for index, connector in enumerate(self.runtime_step_connectors):
            state = "completed" if index in reached and index + 1 in reached else "idle"
            connector.setProperty("state", state)
            connector.style().unpolish(connector)
            connector.style().polish(connector)

    def begin_runtime_cycle(self, loop_index: int) -> None:
        # cycle_start arrives after RUN_SEED_SCRIPT is announced, but before
        # CAPTURE_SEED / REIDENTIFY when starting from those entry points.
        seed_script_active = self._runtime_phase == AutoRngPhase.RUN_SEED_SCRIPT
        self._runtime_phase = None
        self._runtime_completed_steps.clear()
        self._runtime_loop_index = loop_index
        self._refresh_runtime_steps(AutoRngPhase.IDLE)
        if seed_script_active:
            self._refresh_runtime_steps(AutoRngPhase.RUN_SEED_SCRIPT)
        self._runtime_trigger_advances = None
        for label in (self.runtime_current_value, self.runtime_target_value,
                      self.runtime_remaining_value, self.runtime_delay_value):
            label.setText("—")
        self.runtime_delay_state_label.hide()
        self.runtime_round_label.setText(self._runtime_round_text(loop_index))
        self.runtime_description_label.setText("本轮开始，等待测种或校正结果。")
        self.runtime_card.setToolTip("")
        self.clear_candidate_targets("正在准备本轮候选目标")

    def _runtime_round_text(self, loop_index: int) -> str:
        if loop_index <= 0:
            return "任务已停止"
        mode = self.mode_combo.currentData() if hasattr(self, "mode_combo") else "single"
        if mode == "count":
            return f"第 {loop_index} / {self.loop_count.value()} 轮"
        return f"第 {loop_index} 轮"

    @staticmethod
    def _candidate_field(state: object, name: str, default: object = "-") -> object:
        value = getattr(state, name, default)
        return default if value is None else value

    def _runtime_candidate_row(
        self, state: object, *, locked: bool, synchronized: bool, index: int
    ) -> tuple[str, ...]:
        status_parts = []
        if locked:
            status_parts.append("已锁定")
        elif synchronized:
            status_parts.append("同步")
        else:
            status_parts.append("候选")
        advances = self._candidate_field(state, "advances")
        try:
            advances_text = f"{int(advances):,}"
        except (TypeError, ValueError):
            advances_text = str(advances)
        shiny = self._candidate_field(state, "shiny")
        try:
            shiny_text = _RUNTIME_SHINY_LABELS.get(int(shiny), str(shiny))
        except (TypeError, ValueError):
            shiny_text = str(shiny)
        nature = self._candidate_field(state, "nature")
        try:
            nature_index = int(nature)
            nature_text = NATURES_ZH[nature_index] if 0 <= nature_index < len(NATURES_ZH) else str(nature)
        except (TypeError, ValueError):
            nature_text = str(nature)
        ivs = self._candidate_field(state, "ivs", ())
        try:
            iv_text = " / ".join(str(int(value)) for value in ivs)
        except (TypeError, ValueError):
            iv_text = "-"
        return str(index + 1), " · ".join(status_parts), advances_text, shiny_text, nature_text, iv_text

    def set_candidate_targets(
        self,
        candidates: Sequence[object],
        locked_index: int | None = None,
        sync_flags: Sequence[str] | None = None,
    ) -> None:
        total = len(candidates)
        self._runtime_candidate_total = total
        self.candidate_count_label.setText(f"{total} 条候选")
        if total == 0:
            self.clear_candidate_targets("本轮未找到可达候选")
            return
        display_indexes = list(range(min(total, RUNTIME_CANDIDATE_DISPLAY_LIMIT)))
        if locked_index is not None and 0 <= locked_index < total and locked_index not in display_indexes:
            if display_indexes:
                display_indexes[-1] = locked_index
            else:
                display_indexes.append(locked_index)
        def sort_key(index: int) -> tuple[int, int]:
            try:
                advances = int(getattr(candidates[index], "advances", 0))
            except (TypeError, ValueError, OverflowError):
                advances = 0
            return advances, index

        display_indexes = sorted(set(display_indexes), key=sort_key)
        # Keep the selected target in the first visible row so its status is
        # immediately clear even when it is far beyond the display cap.
        if locked_index in display_indexes:
            display_indexes.remove(locked_index)
            display_indexes.insert(0, locked_index)
        sorting = self.candidate_table.isSortingEnabled()
        self.candidate_table.setSortingEnabled(False)
        self.candidate_table.setRowCount(len(display_indexes))
        for row, candidate_index in enumerate(display_indexes):
            state = candidates[candidate_index]
            locked = candidate_index == locked_index
            synchronized = bool(sync_flags and candidate_index < len(sync_flags) and sync_flags[candidate_index] == "sync")
            values = self._runtime_candidate_row(
                state, locked=locked, synchronized=synchronized, index=candidate_index
            )
            for column, value in enumerate(values[1:]):
                item = ResultItem(value)
                item.setToolTip(value)
                item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignLeft if column == 0 else Qt.AlignmentFlag.AlignHCenter))
                if locked:
                    item.setBackground(QColor("#EAF7F1"))
                    item.setForeground(QColor("#087C58" if column == 0 else "#202A33"))
                self.candidate_table.setItem(row, column, item)
        self.candidate_table.setSortingEnabled(sorting)
        visible_rows = min(RUNTIME_CANDIDATE_VISIBLE_ROWS, max(1, len(display_indexes)))
        self.candidate_table.setFixedHeight(30 + visible_rows * 30 + 2)
        if total > len(display_indexes):
            self.candidate_count_label.setText(
                f"{total} 条候选 · 显示 {len(display_indexes)} 条"
            )
            self.candidate_count_label.setToolTip(
                f"运行页最多显示 {RUNTIME_CANDIDATE_DISPLAY_LIMIT} 条；完整列表请查看轮次记录。"
            )
        else:
            self.candidate_count_label.setToolTip("本轮候选目标")
        self.candidate_empty_label.hide()
        self.candidate_table.show()
        self.candidate_section.show()

    def clear_candidate_targets(self, message: str = "开始运行后显示本轮候选目标") -> None:
        self._runtime_candidate_total = 0
        self.candidate_count_label.setText("0 条候选")
        self.candidate_count_label.setToolTip("本轮候选目标")
        self.candidate_empty_label.setText(message)
        self.candidate_table.setRowCount(0)
        self.candidate_table.hide()
        self.candidate_empty_label.show()
        self.candidate_section.show()

    def _build_target_summary_group(self) -> QGroupBox:
        group = QGroupBox()
        group.setObjectName("TargetSummaryGroup")
        group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        group.setMaximumHeight(176)
        layout = QVBoxLayout(group)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        target_card = QFrame()
        target_card.setObjectName("TargetCard")
        add_card_shadow(target_card)
        target_card.setMinimumHeight(66)
        target_card_layout = QHBoxLayout(target_card)
        target_card_layout.setContentsMargins(12, 8, 8, 8)
        target_card_layout.setSpacing(8)
        self.target_avatar = SpeciesAvatar()
        target_card_layout.addWidget(self.target_avatar)
        target_text_layout = QVBoxLayout()
        target_text_layout.setContentsMargins(0, 0, 0, 0)
        target_text_layout.setSpacing(0)
        target_caption = QLabel("目标精灵")
        target_caption.setObjectName("MutedLabel")
        self.target_name_label = QLabel("-")
        self.target_name_label.setObjectName("TargetNameLabel")
        target_text_layout.addWidget(target_caption)
        target_text_layout.addWidget(self.target_name_label)
        target_card_layout.addLayout(target_text_layout, 1)
        self.target_summary_title = QLabel("精灵筛选列表：-")
        self.target_summary_title.hide()
        self.target_button = QPushButton("设置")
        self.target_button.setObjectName("TargetOpenButton")
        self.target_button.setAccessibleName("设置目标精灵")
        self.target_button.setFixedSize(54, 32)
        self.target_button.setToolTip("打开目标精灵设置")
        self.target_button.clicked.connect(self.open_target_dialog)
        target_card_layout.addWidget(self.target_summary_title)
        target_card_layout.addWidget(self.target_button)
        layout.addWidget(target_card)

        target_tags_widget = QWidget(group)
        target_tags_widget.setObjectName("TargetTags")
        target_tags = QHBoxLayout(target_tags_widget)
        target_tags.setContentsMargins(0, 0, 0, 10)
        target_tags.setSpacing(6)
        self.target_count_label = QLabel("0 组目标条件")
        self.target_count_label.setObjectName("GreenTag")
        self.target_match_label = QLabel("匹配任一即可")
        self.target_match_label.setObjectName("NeutralTag")
        target_tags.addWidget(self.target_count_label)
        target_tags.addWidget(self.target_match_label)
        target_tags.addStretch(1)
        layout.addWidget(target_tags_widget)

        self.target_summary_scroll = QScrollArea()
        self.target_summary_scroll.setObjectName("TargetSummaryScroll")
        self.target_summary_scroll.setWidgetResizable(True)
        self.target_summary_scroll.setMinimumHeight(42)
        self.target_summary_scroll.setMaximumHeight(70)
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
        group = QGroupBox()
        group.setObjectName("CurrentMessageGroup")
        group.setMaximumWidth(16777215)
        group.setFixedHeight(44)
        group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.log_group = group
        layout = QHBoxLayout(group)
        layout.setContentsMargins(18, 5, 18, 5)
        layout.setSpacing(9)

        self.latest_log_time_label = QLabel("—")
        self.latest_log_time_label.setObjectName("MessageTimeLabel")
        self.latest_log_time_label.setFixedWidth(52)
        layout.addWidget(self.latest_log_time_label)

        self.latest_log_label = QLabel("暂无消息")
        self.latest_log_label.setObjectName("LatestLogLabel")
        self.latest_log_label.setWordWrap(True)
        self.latest_log_label.setMaximumHeight(30)
        self.latest_log_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.latest_log_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.latest_log_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.latest_log_label, 1)

        self.view_log_button = QPushButton("日志中心")
        self.view_log_button.setObjectName("InlineLinkButton")
        self.view_log_button.setFixedHeight(30)
        self.view_log_button.setMinimumWidth(72)
        self.view_log_button.clicked.connect(self.runLogRequested.emit)
        layout.addWidget(self.view_log_button, 0, Qt.AlignmentFlag.AlignVCenter)

        self.log_view = _CopyableTextEdit(group)
        self.log_view.setObjectName("LogView")
        self.log_view.setFont(QFont("Consolas", 10))
        self.log_view.setStyleSheet("QPlainTextEdit { padding: 12px; }")
        self.log_view.setVisible(False)
        layout.addWidget(self.log_view)
        return group

    def _runtime_metric(
        self, caption: str, suffix: str = "", *, accent: bool = False
    ) -> tuple[QWidget, QLabel]:
        metric = QWidget()
        metric.setObjectName("RuntimeMetric")
        metric_layout = QVBoxLayout(metric)
        metric_layout.setContentsMargins(0, 0, 0, 0)
        metric_layout.setSpacing(4)
        caption_label = QLabel(caption)
        caption_label.setObjectName("RuntimeMetricCaption")
        value_label = QLabel("—")
        value_label.setObjectName("RuntimeMetricValueAccent" if accent else "RuntimeMetricValue")
        value_label.setFont(_ui_numeric_font(28, QFont.Weight.Medium))
        metric_layout.addWidget(caption_label)
        metric_layout.addWidget(value_label)
        if suffix:
            suffix_label = QLabel(suffix)
            suffix_label.setObjectName("RuntimeMetricSuffix")
            metric_layout.addWidget(suffix_label)
        return metric, value_label

    def _update_loop_count_visibility(self, _index: int | None = None) -> None:
        visible = self.mode_combo.currentData() == "count"
        self.loop_count_label.setVisible(visible)
        self.loop_count.setVisible(visible)

    def _set_advanced_strategies_visible(self, visible: bool) -> None:
        self.more_strategy_button.setArrowType(
            Qt.ArrowType.DownArrow if visible else Qt.ArrowType.RightArrow
        )
        for field in self._advanced_strategy_fields:
            self.strategy_form.setRowVisible(field, visible)
        self.more_strategy_button.setText("收起更多策略" if visible else "更多策略 · 4 项")
        self.more_strategy_button.setAccessibleName("收起更多策略" if visible else "展开更多策略")

    def _connect_config_state_tracking(self) -> None:
        for spin in (
            self.max_advances,
            self.max_wait_frames,
            self.shiny_threshold_seconds,
            self.reverse_lookup_window,
            self.loop_count,
        ):
            spin.valueChanged.connect(self._mark_config_dirty)
        for combo in (self.mode_combo, self.sync_combo, self.auto_reverse_combo):
            combo.currentIndexChanged.connect(self._mark_config_dirty)
        self.sync_nature_input.textEdited.connect(self._mark_config_dirty)
        self.escape_continue_check.toggled.connect(self._mark_scripts_dirty)
        self.debug_output_check.toggled.connect(self._mark_config_dirty)
        for combo in self._script_combos():
            combo.currentIndexChanged.connect(self._mark_scripts_dirty)

    def _script_values(self) -> tuple[object, ...]:
        return tuple(self._selected_path(combo) for combo in self._script_combos()) + (
            self.escape_continue_check.isChecked(),
        )

    def _mark_scripts_dirty(self, *_args: object) -> None:
        if not self._config_state_tracking_ready:
            return
        dirty = self._script_values() != self._saved_script_values
        self.script_save_state_label.setText("有未保存修改" if dirty else "已保存")
        self.script_save_state_label.setProperty("saved", not dirty)
        self.script_save_state_label.style().unpolish(self.script_save_state_label)
        self.script_save_state_label.style().polish(self.script_save_state_label)
        self.save_scripts_button.setEnabled(dirty)
        self.save_scripts_button.setProperty("state", "dirty" if dirty else "saved")
        self.save_scripts_button.style().unpolish(self.save_scripts_button)
        self.save_scripts_button.style().polish(self.save_scripts_button)
        self._update_toolbar_status()

    def _mark_config_dirty(self, *_args: object) -> None:
        if self._config_state_tracking_ready:
            self._set_config_saved(False)

    def _set_config_saved(self, saved: bool) -> None:
        if not hasattr(self, "config_saved_label"):
            return
        self.config_saved_label.setText("已保存" if saved else "有未保存修改")
        self.config_saved_label.setProperty("saved", saved)
        self.config_saved_label.style().unpolish(self.config_saved_label)
        self.config_saved_label.style().polish(self.config_saved_label)
        if hasattr(self, "save_config_button"):
            self.save_config_button.setProperty("state", "saved" if saved else "dirty")
            self.save_config_button.style().unpolish(self.save_config_button)
            self.save_config_button.style().polish(self.save_config_button)
        self._update_toolbar_status()

    def _missing_script_fields(self) -> list[tuple[QComboBox, str]]:
        required = [
            (self.advance_script_combo, "过帧"),
            (self.hit_script_combo, "撞闪"),
        ]
        if self.escape_continue_check.isChecked():
            required.append((self.escape_script_combo, "逃跑"))
        return [(combo, label) for combo, label in required if self._selected_path(combo) is None]

    def _focus_missing_script(self) -> None:
        missing = self._missing_script_fields()
        if not missing:
            return
        self._runtime_script_editor_expanded = True
        self._set_runtime_script_summary_visible(True)
        combo = missing[0][0]
        self.runtime_content.layout().activate()
        self.runtime_panel.ensureWidgetVisible(combo)
        combo.setFocus(Qt.FocusReason.OtherFocusReason)

    def _update_script_status(self) -> None:
        """Report configuration readiness without implying a hardware connection."""
        if not hasattr(self, "script_status_label"):
            return
        self._refresh_runtime_script_summary()
        missing_fields = self._missing_script_fields()
        missing = [label for _combo, label in missing_fields]
        for combo, label in self.script_labels.items():
            required = combo in (self.advance_script_combo, self.hit_script_combo) or (
                combo is self.escape_script_combo and self.escape_continue_check.isChecked()
            )
            absent = any(combo is field for field, _name in missing_fields)
            label.setText(f"{combo.accessibleName()} · {'待选择' if absent else '必需' if required else '可选'}")
            for widget in (label, combo):
                widget.setProperty("missing", absent)
                widget.style().unpolish(widget)
                widget.style().polish(widget)
        if missing:
            self.script_status_label.setText(f"待配置 · 缺少 {len(missing)} 项")
            self.script_status_label.setToolTip("还需要选择：" + "、".join(missing))
            state = "warning"
        else:
            self.script_status_label.setText("脚本已就绪")
            self.script_status_label.setToolTip("必需脚本已选择；开始前仍需确认视频源与伊机控连接。")
            state = "ready"
        self.script_status_label.setProperty("state", state)
        self.script_status_label.style().unpolish(self.script_status_label)
        self.script_status_label.style().polish(self.script_status_label)
        self._update_toolbar_status()

    def _update_toolbar_status(self) -> None:
        """Render a concise, actionable state in the toolbar center."""

        if not hasattr(self, "toolbar_status"):
            return
        busy = (
            self._preparing
            or self._runner_thread is not None
            or self._run_state_active
            or (
                hasattr(self, "runtime_card")
                and self.runtime_card.property("state") == "active"
            )
        )
        if self._stop_pending:
            text = "正在停止 · 请稍候"
            state = "warning"
            self.runtime_phase_label.setText("正在停止")
            self.runtime_description_label.setText("已请求停止，正在等待当前任务结束。")
        elif self._preparing:
            text = "正在准备 · 请稍候"
            state = "active"
            self.runtime_phase_label.setText("正在准备")
            self.runtime_description_label.setText("正在准备自动流程，请稍候。")
        elif busy:
            phase = (
                self.runtime_phase_label.text().strip()
                if hasattr(self, "runtime_phase_label")
                else "运行中"
            )
            text = "正在收尾 · 请稍候" if self._worker_done else f"运行中 · {phase or '处理中'}"
            state = "active"
        elif (
            hasattr(self, "runtime_card")
            and self.runtime_card.property("state") == "failed"
        ):
            text = self.runtime_phase_label.text().strip() or "运行失败"
            state = "failed"
        elif hasattr(self, "runtime_card") and self.runtime_card.property("state") == "completed":
            text = "流程已完成 · 可查看轮次"
            state = "idle"
        elif (
            hasattr(self, "script_status_label")
            and self.script_status_label.property("state") == "warning"
        ):
            text = "待选择 · " + "、".join(name for _field, name in self._missing_script_fields())
            state = "warning"
        elif not self._start_available:
            text = "其他任务占用 · 暂不可开始"
            state = "warning"
        elif any(
            label is not None and label.property("saved") is False
            for label in (getattr(self, "config_saved_label", None),
                          getattr(self, "script_save_state_label", None))
        ):
            text = "有未保存修改"
            state = "warning"
        else:
            text = "配置已就绪"
            state = "idle"
        self.toolbar_status.setText(text)
        self.toolbar_status.setProperty("state", state)
        self.toolbar_status.style().unpolish(self.toolbar_status)
        self.toolbar_status.style().polish(self.toolbar_status)
        if hasattr(self, "runtime_setup_button") and hasattr(self, "script_labels"):
            missing = self._missing_script_fields()
            idle = self._runtime_phase in (None, AutoRngPhase.IDLE) and not busy and not self._stop_pending
            self.runtime_setup_button.setVisible(idle and bool(missing))
            if idle and self.runtime_phase_label.text() not in ("已停止", "已完成"):
                self.runtime_phase_label.setText("待完成配置" if missing else "等待开始")
                self.runtime_description_label.setText(
                    "请先选择" + "、".join(name for _field, name in missing) + "脚本；下方已标出缺项。"
                    if missing else "目标与脚本配置完成后，确认顶部设备连接，再点击“开始”。"
                )

    def _set_runtime_card_state(self, state: str) -> None:
        if self.runtime_card.property("state") == state:
            return
        if state == "active":
            # Fold once on entry, never on each progress tick: users may reopen it.
            self._runtime_script_editor_expanded = False
        self.runtime_card.setProperty("state", state)
        self.runtime_card.style().unpolish(self.runtime_card)
        self.runtime_card.style().polish(self.runtime_card)
        if hasattr(self, "runtime_state_dot"):
            self.runtime_state_dot.style().unpolish(self.runtime_state_dot)
            self.runtime_state_dot.style().polish(self.runtime_state_dot)

    @staticmethod
    def _runtime_value(value: object, *, suffix: str = "") -> str:
        if value is None or isinstance(value, bool):
            return "—"
        try:
            return f"{int(value):,}{suffix}"
        except (TypeError, ValueError, OverflowError):
            return "—"

    def _apply_panel_style(self) -> None:
        self.setStyleSheet(
            workspace_styles("""
            QWidget#AutoRngPanel {
                background: $surface;
                color: $text;
                font-family: "BDSP UI Sans", "Noto Sans SC", "Source Han Sans SC", "Noto Sans CJK SC", "Microsoft YaHei UI", "PingFang SC", "Segoe UI", sans-serif;
                font-size: 14px;
            }
            QLabel,
            QComboBox,
            QSpinBox,
            QDoubleSpinBox,
            QPushButton,
            QToolButton,
            QTableWidget {
                font-family: "BDSP UI Sans", "Noto Sans SC", "Source Han Sans SC", "Noto Sans CJK SC", "Microsoft YaHei UI", "PingFang SC", "Segoe UI", sans-serif;
                font-size: 14px;
            }
            QFrame#AutoRngToolbar {
                background: $surface;
                border: 0;
                border-bottom: 1px solid $separator;
                border-radius: 0;
            }
            QFrame#AutoRngToolbar QComboBox,
            QFrame#AutoRngToolbar QSpinBox,
            QFrame#AutoRngToolbar QPushButton,
            QFrame#AutoRngToolbar QToolButton {
                min-height: 32px;
                max-height: 34px;
                border-radius: 7px;
            }
            QLabel#ToolbarStatus {
                background: $surface_muted;
                border: 1px solid $border;
                border-radius: 7px;
                color: #687480;
                padding: 0 12px;
                font-size: 12px;
                font-weight: 400;
            }
            QLabel#ToolbarStatus[state="active"] {
                background: $accent_soft;
                border-color: #C7E6D5;
                color: $accent;
            }
            QLabel#ToolbarStatus[state="warning"] {
                background: $warning_soft;
                border-color: #F0D7A7;
                color: $warning;
            }
            QLabel#ToolbarStatus[state="failed"] {
                background: $error_soft;
                border-color: #E9C8C0;
                color: $error;
            }
            QLabel#ToolbarFieldLabel,
            QLabel#RuntimeMetricCaption,
            QLabel#RuntimeMetricSuffix,
            QLabel#ScriptFieldLabel {
                color: #52606D;
                font-size: 13px;
                font-weight: 400;
            }
            QLabel#MutedLabel,
            QLabel#RuntimeDescriptionLabel,
            QLabel#PreviousRoundLabel,
            QLabel#MessageTimeLabel,
            QLabel#DelayActiveLabel {
                color: $text_secondary;
                font-size: 12px;
                font-weight: 400;
            }
            QLabel#SectionTitle {
                color: $text;
                font-size: 16px;
                font-weight: 500;
            }
            QScrollArea#AutoRngConfigPanel {
                background: $surface_muted;
                border: 0;
                border-right: 1px solid $separator;
            }
            QScrollArea#AutoRngConfigPanel > QWidget > QWidget,
            QWidget#AutoRngConfigContents {
                background: $surface_muted;
            }
            QScrollArea#AutoRngConfigPanel QScrollBar:vertical {
                width: 7px;
                margin: 1px;
                background: transparent;
            }
            QScrollArea#AutoRngConfigPanel QScrollBar::handle:vertical {
                min-height: 28px;
                border-radius: 3px;
                background: #CCD4DD;
            }
            QScrollArea#AutoRngConfigPanel QScrollBar::add-line:vertical,
            QScrollArea#AutoRngConfigPanel QScrollBar::sub-line:vertical {
                height: 0;
            }
            QLabel#ConfigSavedLabel, QLabel#ScriptSaveStateLabel {
                color: $text_secondary;
                font-size: 12px;
            }
            QLabel#ConfigSavedLabel[saved="false"], QLabel#ScriptSaveStateLabel[saved="false"] {
                color: $warning;
            }
            QLabel#ScriptStatusLabel {
                border-radius: 10px;
                padding: 2px 8px;
                color: #687480;
                background: #F0F3F6;
                font-size: 12px;
                font-weight: 400;
            }
            QLabel#ScriptStatusLabel[state="ready"] {
                color: #597467;
                background: #F2F8F5;
            }
            QLabel#ScriptStatusLabel[state="warning"] {
                color: $warning;
                background: $warning_soft;
            }
            QGroupBox#TargetSummaryGroup,
            QGroupBox#AutoRngStrategyGroup {
                background: transparent;
                border: 0;
                border-radius: 0;
                margin: 0;
                padding: 0;
                font-weight: 400;
            }
            QFrame#TargetCard {
                background: $surface;
                border: 1px solid $card_border;
                border-radius: 12px;
            }
            QWidget#TargetTags {
                background: transparent;
            }
            QLabel#TargetNameLabel {
                color: $text;
                font-size: 20px;
                font-weight: 500;
            }
            QLabel#GreenTag,
            QLabel#NeutralTag,
            QLabel#RuntimeRoundLabel {
                border-radius: 4px;
                padding: 2px 7px;
                font-size: 12px;
                font-weight: 400;
            }
            QLabel#GreenTag {
                background: $accent_soft;
                color: $accent;
            }
            QLabel#NeutralTag,
            QLabel#RuntimeRoundLabel {
                background: #F0F3F6;
                color: #687480;
            }
            QPushButton#TargetOpenButton,
            QPushButton#ScriptSaveButton,
            QPushButton#ConfigSaveButton,
            QPushButton#InlineLinkButton {
                background: transparent;
                border: 0;
                color: $text_secondary;
                padding: 0 4px;
                font-size: 13px;
                font-weight: 400;
            }
            QPushButton#ConfigSaveButton, QPushButton#ScriptSaveButton {
                min-height: 30px;
                padding: 0 12px;
                border-radius: 7px;
            }
            QPushButton#ConfigSaveButton[state="dirty"], QPushButton#ScriptSaveButton[state="dirty"] {
                background: $accent;
                border: 1px solid $accent;
                color: $surface;
                font-weight: 500;
            }
            QPushButton#ConfigSaveButton[state="dirty"]:hover, QPushButton#ScriptSaveButton[state="dirty"]:hover {
                background: #066A4B;
                border-color: #066A4B;
                color: $surface;
                text-decoration: none;
            }
            QPushButton#TargetOpenButton:hover,
            QPushButton#ScriptSaveButton:hover,
            QPushButton#ConfigSaveButton:hover,
            QPushButton#InlineLinkButton:hover {
                color: #066A4B;
                text-decoration: underline;
            }
            QPushButton#ScriptSaveButton:disabled {
                color: #9AA8A1;
            }
            QScrollArea#TargetSummaryScroll {
                background: transparent;
                border: 0;
            }
            QScrollArea#TargetSummaryScroll > QWidget > QWidget {
                background: transparent;
            }
            QGroupBox#AutoRngStrategyGroup QLabel {
                font-weight: 400;
            }
            QGroupBox#AutoRngStrategyGroup QSpinBox,
            QGroupBox#AutoRngStrategyGroup QDoubleSpinBox,
            QGroupBox#AutoRngStrategyGroup QComboBox,
            QGroupBox#AutoRngStrategyGroup QLineEdit {
                min-height: 30px;
                max-height: 32px;
                background: $surface;
                border: 1px solid $border;
                border-radius: 7px;
                padding: 0 8px;
                color: $text;
                font-size: 14px;
                font-weight: 400;
            }
            QGroupBox#AutoRngStrategyGroup QSpinBox,
            QGroupBox#AutoRngStrategyGroup QDoubleSpinBox {
                font-family: "BDSP UI Sans", "Noto Sans SC", "Source Han Sans SC", "Noto Sans CJK SC", "Microsoft YaHei UI", "PingFang SC", "Segoe UI", sans-serif;
            }
            QToolButton#PrimaryButton,
            QPushButton#DangerButton {
                font-size: 14px;
                font-weight: 500;
            }
            QPushButton#SecondaryButton,
            QPushButton#ScriptSaveButton,
            QPushButton#ConfigSaveButton,
            QPushButton#TargetOpenButton,
            QPushButton#InlineLinkButton,
            QToolButton#RuntimeScriptSummaryToggle,
            QPushButton#AutoRngRefreshScripts {
                font-size: 13px;
            }
            QGroupBox#AutoRngStrategyGroup QSpinBox QLineEdit,
            QGroupBox#AutoRngStrategyGroup QDoubleSpinBox QLineEdit {
                background: transparent;
                border: 0;
                min-height: 0;
                max-height: 16777215px;
                padding: 0;
            }
            QWidget#DelaySettingsField,
            QWidget#CompactStrategyField {
                background: transparent;
            }
            QLabel#DelayActiveLabel {
                padding-left: 1px;
            }
            QToolButton#MoreStrategyButton {
                background: transparent;
                border: 0;
                border-top: 1px solid $separator;
                border-radius: 0;
                color: #687480;
                padding: 7px 0 0 0;
                text-align: left;
                font-size: 12px;
                font-weight: 400;
            }
            QToolButton#MoreStrategyButton:hover {
                color: $accent;
            }
            QFrame#ConfigFooter {
                background: transparent;
                border: 0;
                border-top: 1px solid $separator;
            }
            QScrollArea#AutoRngRuntimePanel {
                background: $background;
                border: 0;
            }
            QScrollArea#AutoRngRuntimePanel > QWidget > QWidget,
            QWidget#AutoRngRuntimeContent {
                background: $background;
            }
            QScrollArea#AutoRngRuntimePanel QScrollBar:vertical {
                width: 7px;
                margin: 1px;
                background: transparent;
            }
            QScrollArea#AutoRngRuntimePanel QScrollBar::handle:vertical {
                min-height: 28px;
                border-radius: 3px;
                background: #CCD4DD;
            }
            QScrollArea#AutoRngRuntimePanel QScrollBar::add-line:vertical,
            QScrollArea#AutoRngRuntimePanel QScrollBar::sub-line:vertical {
                height: 0;
            }
            QFrame#RuntimeCard {
                background: $runtime_gradient;
                border: 1px solid $card_border;
                border-radius: 12px;
            }
            QFrame#RuntimeCard[state="active"] {
                border-color: #DCECE4;
            }
            QFrame#RuntimeCard[state="completed"] {
                border-color: #DCECE4;
            }
            QFrame#RuntimeCard[state="failed"] {
                background: $error_soft;
            }
            QWidget#RuntimeSteps,
            QWidget#RuntimeSteps QWidget {
                background: transparent;
            }
            QWidget#RuntimeScriptHeader, QWidget#RuntimeCandidatesSection,
            QWidget#RuntimeMetric {
                background: transparent;
            }
            QLabel#RuntimeStateDot {
                color: #99A4AF;
                font-size: 12px;
            }
            QFrame#RuntimeCard[state="active"] QLabel#RuntimeStateDot {
                color: $accent;
            }
            QFrame#RuntimeCard[state="failed"] QLabel#RuntimeStateDot {
                color: $error;
            }
            QFrame#RuntimeCard[state="completed"] QLabel#RuntimeStateDot {
                color: $accent;
            }
            QLabel#RuntimeStepCircle {
                background: #E7ECF1;
                border-radius: 11px;
                border: 1px solid #D5DEE7;
                color: $text_secondary;
                font-size: 11px;
                font-weight: 500;
            }
            QLabel#RuntimeStepCircle[state="active"] {
                background: $accent;
                border-color: $accent;
                color: $surface;
            }
            QLabel#RuntimeStepCircle[state="completed"] {
                background: #CDEBDD;
                border-color: #B9DEC9;
                color: $accent;
            }
            QLabel#RuntimeStepCircle[state="failed"] {
                background: $error;
                border-color: $error;
                color: $surface;
            }
            QLabel#RuntimeStepLabel {
                color: $text_secondary;
                font-size: 12px;
                font-weight: 400;
            }
            QLabel#RuntimeStepLabel[state="active"] {
                color: $accent;
                font-weight: 500;
            }
            QLabel#RuntimeStepLabel[state="completed"] {
                color: #4F665A;
            }
            QLabel#RuntimeStepLabel[state="failed"] {
                color: $error;
                font-weight: 500;
            }
            QFrame#RuntimeStepConnector {
                background: #D8E1DC;
                border: 0;
            }
            QFrame#RuntimeStepConnector[state="completed"] {
                background: #9DD5B8;
            }
            QLabel#RuntimeDelayState {
                background: #F2F8F5;
                border-radius: 9px;
                color: #597467;
                padding: 2px 7px;
                font-size: 12px;
            }
            QFrame#RuntimeScriptSummary {
                background: transparent;
                border: 0;
                border-radius: 0;
            }
            QLabel#RuntimeScriptSummaryIcon {
                background: $accent_soft;
                border-radius: 12px;
                color: $accent;
                font-size: 16px;
            }
            QLabel#RuntimeScriptSummaryTitle {
                color: $text;
                font-size: 14px;
                font-weight: 500;
            }
            QLabel#RuntimeScriptSummaryDetail,
            QLabel#RuntimeScriptHeaderState {
                color: $text_secondary;
                font-size: 12px;
            }
            QToolButton#RuntimeScriptSummaryToggle {
                background: transparent;
                border: 0;
                color: $text_secondary;
                padding: 2px 0;
                font-size: 13px;
            }
            QToolButton#RuntimeScriptSummaryToggle:hover {
                color: #066A4B;
            }
            QLabel#CandidateCountLabel {
                color: $text_secondary;
                font-size: 12px;
            }
            QLabel#RuntimeCandidatesEmpty {
                background: $surface;
                border: 1px dashed $border;
                border-radius: 7px;
                color: $text_secondary;
                font-size: 12px;
            }
            QTableWidget#RuntimeCandidateTable {
                background: $surface;
                alternate-background-color: #FAFBFC;
                border: 1px solid $card_border;
                border-radius: 7px;
                color: $text;
                font-size: 13px;
            }
            QTableWidget#RuntimeCandidateTable::item { padding: 0 8px; }
            QTableWidget#RuntimeCandidateTable QHeaderView::section {
                background: #F8F9FB;
                border: 0;
                border-bottom: 1px solid $separator;
                color: $text_secondary;
                padding: 0 8px;
                font-size: 12px;
                font-weight: 500;
            }
            QTableWidget#RuntimeCandidateTable QTableCornerButton::section {
                background: #F0F3F6;
                border: 0;
            }
            QTableWidget#RuntimeCandidateTable QScrollBar:vertical {
                width: 7px;
                margin: 1px;
                background: transparent;
            }
            QTableWidget#RuntimeCandidateTable QScrollBar::handle:vertical {
                min-height: 22px;
                border-radius: 3px;
                background: #CCD4DD;
            }
            QTableWidget#RuntimeCandidateTable QScrollBar::add-line:vertical,
            QTableWidget#RuntimeCandidateTable QScrollBar::sub-line:vertical {
                height: 0;
            }
            QLabel#RuntimePhaseLabel {
                color: $text;
                font-size: 20px;
                font-weight: 500;
            }
            QLabel#RuntimeMetricValue,
            QLabel#RuntimeMetricValueAccent {
                color: $text;
                font-size: 28px;
                font-weight: 500;
                letter-spacing: 0;
            }
            QLabel#RuntimeMetricValueAccent {
                color: $accent;
            }
            QLabel#RuntimeMonoSmall {
                color: $text;
                font-size: 14px;
                font-weight: 500;
                letter-spacing: 0;
            }
            QFrame#RuntimeFooter {
                background: transparent;
                border: 0;
                border-top: 1px solid $separator;
            }
            QLabel#PreviousRoundLabel {
                padding-left: 1px;
            }
            QLabel#TargetConditionLabel {
                color: $text_secondary;
                font-size: 12px;
                font-weight: 400;
            }
            QGroupBox#AutoRngScriptGroup {
                background: transparent;
                border: 0;
                border-radius: 0;
                margin-top: 0;
                padding: 0;
                color: $text;
                font-size: 14px;
                font-weight: 500;
            }
            QGroupBox#AutoRngScriptGroup::title {
                subcontrol-origin: margin;
                left: 0;
                top: 0;
                padding: 0;
                background: transparent;
            }
            QGroupBox#AutoRngScriptGroup QComboBox {
                min-height: 30px;
                max-height: 32px;
                background: $surface;
                border: 1px solid $border;
                border-radius: 7px;
                padding: 0 8px;
                color: $text;
                font-size: 14px;
                font-weight: 400;
            }
            QGroupBox#AutoRngScriptGroup QComboBox[missing="true"] {
                border-color: #C29D5F;
                background: $warning_soft;
            }
            QLabel#ScriptFieldLabel[missing="true"] {
                color: $warning;
            }
            QPushButton#RuntimeSetupButton {
                background: $surface;
                color: $accent;
                border: 1px solid #B8C4CE;
                border-radius: 7px;
                padding: 0 10px;
                font-size: 13px;
            }
            QPushButton#RuntimeSetupButton:hover {
                background: $accent_soft;
                border-color: $accent;
            }
            QWidget#ScriptPicker {
                background: transparent;
            }
            QToolButton#ScriptEditButton {
                background: $surface;
                border: 1px solid $border;
                border-radius: 7px;
                padding: 0;
            }
            QToolButton#ScriptEditButton:hover {
                background: $surface_muted;
                border-color: #B8C4CE;
            }
            QToolButton#ScriptEditButton:disabled {
                background: #FAFBFA;
                border-color: #EEF1EF;
            }
            QGroupBox#CurrentMessageGroup {
                background: $surface;
                border: 0;
                border-top: 1px solid $separator;
                border-radius: 0;
                margin: 0;
                padding: 0;
                font-weight: 400;
            }
            QLabel#LatestLogLabel {
                color: #465460;
                font-size: 12px;
                font-weight: 400;
            }
            QLabel#MessageTimeLabel {
                font-family: "Consolas", "Cascadia Mono", monospace;
            }
            QPlainTextEdit#LogView {
                font-family: "Consolas", "Cascadia Mono", monospace;
                font-size: 10px;
            }
            """) + primary_button_styles("QToolButton#PrimaryButton", 'QPushButton#ConfigSaveButton[state="dirty"]', 'QPushButton#ScriptSaveButton[state="dirty"]') + focus_styles(
                "QPushButton#RuntimeSetupButton", "QPushButton#TargetOpenButton",
                "QPushButton#ConfigSaveButton", "QPushButton#ScriptSaveButton",
                "QPushButton#InlineLinkButton", "QPushButton#AutoRngRefreshScripts",
                "QToolButton#PrimaryButton", "QPushButton#DangerButton",
                "QPushButton#SecondaryButton", "QToolButton#ScriptEditButton",
                "QToolButton#RuntimeScriptSummaryToggle", "QToolButton#MoreStrategyButton",
                "QGroupBox#AutoRngScriptGroup QComboBox",
                "QGroupBox#AutoRngStrategyGroup QSpinBox",
                "QGroupBox#AutoRngStrategyGroup QDoubleSpinBox",
                "QGroupBox#AutoRngStrategyGroup QComboBox",
                "QGroupBox#AutoRngStrategyGroup QLineEdit",
                "QFrame#AutoRngToolbar QComboBox", "QFrame#AutoRngToolbar QSpinBox",
                "QTableWidget#RuntimeCandidateTable",
            )
        )

    def refresh_scripts(self) -> None:
        if self._runner_thread is not None or self._preparing:
            return
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
                if path is not None and self._selected_path(combo) is None:
                    self.add_log(f"脚本已不存在，请重新选择：{path.name}", level="WARNING")
        else:
            self._select_script(self.seed_script_combo, choose_default_script(self._scripts, DEFAULT_SEED_SCRIPT_NAME))
            self._select_script(self.advance_script_combo, choose_default_script(self._scripts, DEFAULT_ADVANCE_SCRIPT_NAME))
            self._scripts_initialized = True
        for combo in self._script_combos():
            self._update_script_edit_button(combo)
        self._mark_scripts_dirty()
        self._update_script_status()

    def _update_script_edit_button(self, combo: QComboBox) -> None:
        button = self.script_edit_buttons.get(combo)
        if button is not None:
            button.setEnabled(combo.isEnabled() and self._selected_path(combo) is not None)

    def _request_script_edit(self, combo: QComboBox) -> None:
        path = self._selected_path(combo)
        if path is not None:
            self.scriptEditRequested.emit(path)

    def set_phase_text(self, text: str) -> None:
        self.status_badge.setText(text)
        normalized = str(text).strip()
        try:
            phase_value: AutoRngPhase | None = AutoRngPhase(normalized)
        except ValueError:
            phase_value = None
        if phase_value is not None:
            self._refresh_runtime_steps(phase_value)
        elif normalized == "已停止":
            self._refresh_runtime_steps(AutoRngPhase.IDLE)
        elif "失败" in normalized or "错误" in normalized:
            self._refresh_runtime_steps(AutoRngPhase.FAILED)
        if normalized in {AutoRngPhase.IDLE.value, "已停止", "已完成"}:
            self.runtime_phase_label.setText("准备就绪" if normalized == AutoRngPhase.IDLE.value else normalized)
            self.runtime_round_label.setText("任务已停止")
            self._set_runtime_card_state("completed" if normalized == "已完成" else "idle")
            self._set_runtime_script_summary_visible(True)
        elif "失败" in normalized or "错误" in normalized:
            self.runtime_phase_label.setText(normalized or "失败")
            self._set_runtime_card_state("failed")
            self._set_runtime_script_summary_visible(True)
        else:
            self.runtime_phase_label.setText(_RUNTIME_PHASE_TITLES.get(phase_value, normalized or "运行中"))
            self._set_runtime_card_state("active")
            self._set_runtime_script_summary_visible(True)
        self._update_toolbar_status()

    def set_live_advances(self, advances: int) -> None:
        current = int(advances)
        self.runtime_current_value.setText(self._runtime_value(current))
        if self._runtime_trigger_advances is not None:
            self.runtime_remaining_value.setText(
                self._runtime_value(self._runtime_trigger_advances - current, suffix=" 帧")
            )

    def apply_progress(self, progress: AutoRngProgress) -> None:
        self.runtime_insights.update_progress(progress)
        if progress.loop_index > 0:
            if self._runtime_loop_index > 0 and progress.loop_index != self._runtime_loop_index:
                self.begin_runtime_cycle(progress.loop_index)
            self._runtime_loop_index = progress.loop_index
        previous_phase = self._runtime_phase
        phase_text = progress.phase.value if hasattr(progress.phase, "value") else str(progress.phase)
        self.set_phase_text(phase_text)
        self._runtime_trigger_advances = progress.trigger_advances
        target_advances = progress.raw_target_advances
        if target_advances is None and progress.locked_target is not None:
            target_advances = progress.locked_target.raw_target_advances
        delay = progress.fixed_delay
        if delay is None and progress.locked_target is not None:
            delay = progress.locked_target.used_delay
        current_advances = progress.current_advances
        remaining = progress.remaining_to_trigger
        if progress.phase in {AutoRngPhase.RUN_SEED_SCRIPT, AutoRngPhase.CAPTURE_SEED}:
            # A new capture can retain fields from the previous target in the
            # runner's progress; those are not measurements for the new Seed.
            current_advances = target_advances = remaining = delay = None
            self._runtime_trigger_advances = None
        elif progress.phase == AutoRngPhase.SEARCH_TARGET:
            target_advances = remaining = None
            self._runtime_trigger_advances = None
        elif self._runtime_trigger_advances is not None and current_advances is not None:
            remaining = self._runtime_trigger_advances - current_advances
        self.runtime_current_value.setText(self._runtime_value(current_advances))
        self.runtime_target_value.setText(self._runtime_value(target_advances))
        self.runtime_remaining_value.setText(self._runtime_value(remaining, suffix=" 帧"))
        self.runtime_delay_value.setText(self._runtime_value(delay))
        delay_frozen = delay is not None and progress.phase not in {
            AutoRngPhase.IDLE,
            AutoRngPhase.COMPLETED,
            AutoRngPhase.FAILED,
        }
        self.runtime_delay_state_label.setVisible(delay_frozen)
        if progress.loop_index > 0:
            self.runtime_round_label.setText(self._runtime_round_text(progress.loop_index))
        elif progress.phase not in {
            AutoRngPhase.IDLE,
            AutoRngPhase.COMPLETED,
            AutoRngPhase.FAILED,
        }:
            self.runtime_round_label.setText("准备第 1 轮")
        else:
            self.runtime_round_label.setText("任务已停止")
        if progress.phase == AutoRngPhase.FINAL_WAIT:
            remaining_text = self._runtime_value(remaining)
            self.runtime_description_label.setText(
                f"启动点已过去 {self._runtime_value(-remaining)} 帧；请留意后续阶段和日志。"
                if remaining is not None and remaining < 0
                else f"软件活帧等待，还需过 {remaining_text} 帧；到达撞闪启动点后自动运行脚本。"
            )
        elif progress.log_message:
            self.runtime_description_label.setText(progress.log_message)
        elif progress.phase == AutoRngPhase.IDLE:
            self.runtime_description_label.setText("等待自动流程开始。")
        elif previous_phase != progress.phase:
            self.runtime_description_label.setText(
                _RUNTIME_PHASE_DESCRIPTIONS.get(progress.phase, phase_text)
            )
        if previous_phase != progress.phase and progress.phase in {
            AutoRngPhase.RUN_SEED_SCRIPT, AutoRngPhase.CAPTURE_SEED, AutoRngPhase.SEARCH_TARGET,
        }:
            self.clear_candidate_targets("正在搜索本轮可达候选" if progress.phase == AutoRngPhase.SEARCH_TARGET
                                         else "等待测种后搜索本轮候选")
        if self.candidate_table.rowCount() == 0:
            if progress.phase == AutoRngPhase.FAILED:
                self.clear_candidate_targets("本轮暂无候选，请查看上方错误信息。")
            elif progress.phase == AutoRngPhase.COMPLETED:
                self.clear_candidate_targets("流程已结束，本轮没有可显示的候选。")
        runtime_tooltip = self.runtime_description_label.text()
        if progress.seed_text:
            runtime_tooltip = (
                f"Seed: {progress.seed_text}"
                + (f"\n{runtime_tooltip}" if runtime_tooltip else "")
            )
        self.runtime_card.setToolTip(runtime_tooltip)
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
            self.latest_log_time_label.setText(timestamp)
            self.latest_log_label.setText(latest_line)
            self.latest_log_label.setToolTip(text)
            self.latestMessageChanged.emit(latest_line)

    def set_candidates(self, rows: list[list[str]], locked_index: int | None = None) -> None:
        locked_text = ""
        if locked_index is not None and 0 <= locked_index < len(rows):
            locked_text = f"，锁定 {rows[locked_index]}"
        self.add_log(f"候选结果 {len(rows)} 个{locked_text}")

    def set_target_version(self, version: GameVersion) -> None:
        self._target_version = version
        self.target_form.set_version(version)
        self._activate_delay_profile(self._current_delay_species())
        self._refresh_target_summary()

    def set_targets(self, targets: list[tuple[StaticEncounterRecord, StateFilter, str]]) -> None:
        self._targets = list(targets)
        self._activate_delay_profile(self._current_delay_species())
        self._save_delay_profiles()
        self._refresh_target_summary()
        self._mark_config_dirty()

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
            self.target_name_label.setText("-")
            self.target_avatar.set_species(None)
            self.target_count_label.setText("0 组目标条件")
            self._refresh_runtime_script_summary()
            return
        record = targets[0][0]
        name = POKEMON_LABELS_ZH.get(record.description, record.description)
        self.target_summary_title.setText(f"精灵筛选列表：{name}")
        self.target_name_label.setText(name)
        self.target_avatar.set_species(int(record.template.species), name)
        self.target_count_label.setText(f"{len(targets)} 组目标条件")
        for index, (_record, state_filter, shiny_mode) in enumerate(targets, start=1):
            label = QLabel(f"{index}. {_target_condition_text(state_filter, shiny_mode)}")
            label.setObjectName("TargetConditionLabel")
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)
            self.target_summary_layout.addWidget(label)
            self.target_summary_labels.append(label)
        self._refresh_runtime_script_summary()

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
        if not self.start_button.isEnabled():
            return
        self._save_panel_state()
        try:
            config = self.build_config(start_phase=start_phase)
            validate_auto_scripts(
                config.seed_script_path,
                config.advance_script_path,
                config.hit_script_path,
                escape_continue=config.escape_continue,
                escape_script_path=config.escape_script_path,
                shiny_threshold_seconds=config.shiny_threshold_seconds,
                target_species=config.target_species,
            )
        except (AutoScriptError, ValueError) as exc:
            self.set_phase_text("配置错误")
            self.runtime_description_label.setText(str(exc))
            self.add_log(str(exc), level="WARNING")
            return
        self.startRequested.emit(config)

    def _stop_clicked(self) -> None:
        self.request_stop("用户点击停止按钮")

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
            delay_sample_rounds=tuple(self.delay_samples()),
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
        self.begin_runtime_cycle(0)
        self.runtime_insights.start(getattr(runner, "config", None))
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
        thread.finished.connect(self._clear_runner_thread)
        thread.finished.connect(worker.deleteLater)
        self._runner_thread = thread
        self._runner_worker = worker
        self._preparing = False
        self._stop_pending = False
        self._worker_done = False
        self._run_state_active = True
        self._sync_run_controls()
        self.runStateChanged.emit(True)
        thread.start()

    def _runner_finished(self, progress: object) -> None:
        # 不重复 apply_progress：最后一条进度已通过 progressChanged 信号输出
        if isinstance(progress, AutoRngProgress):
            self.set_phase_text("已停止" if progress.phase == AutoRngPhase.IDLE else "失败" if progress.phase == AutoRngPhase.FAILED else "已完成")
        self._runner_returned()

    def _runner_failed(self, message: str) -> None:
        self.set_phase_text("失败")
        if message != self._last_failed_progress_message:
            self.add_log(message, level="ERROR")
        self._last_failed_progress_message = None
        self._runner_returned()


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
        self._save_config_state()
        self._save_script_state()

    def _save_config_state(self) -> None:
        s = self._settings
        s.setValue("mode_index", self.mode_combo.currentIndex())
        s.setValue("loop_count", self.loop_count.value())
        s.setValue("max_advances", self.max_advances.value())
        self._save_delay_settings()
        self._save_delay_samples()
        s.setValue("max_wait_frames", self.max_wait_frames.value())
        self._save_strategy_settings()
        s.setValue("shiny_threshold", self.shiny_threshold_seconds.value())
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
        s.sync()
        self._set_config_saved(True)

    def _save_script_state(self) -> None:
        s = self._settings
        for key, combo in (
            ("seed_script", self.seed_script_combo),
            ("advance_script", self.advance_script_combo),
            ("hit_script", self.hit_script_combo),
            ("escape_script", self.escape_script_combo),
            ("exit_script", self.exit_script_combo),
            ("reverse_script", self.reverse_script_combo),
        ):
            path = self._selected_path(combo)
            # An explicit empty choice must override first-launch defaults.
            s.setValue(key, str(path) if path is not None else "")
        s.setValue("escape_continue", self.escape_continue_check.isChecked())
        s.sync()
        self._saved_script_values = self._script_values()
        self._mark_scripts_dirty()

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
        if self._delay_species_id is not None:
            self._profile_for_species(self._delay_species_id).config = config
        self._save_delay_profiles()
        # Keep fixed_delay as the baseline key for compatibility with older releases.
        s.setValue("fixed_delay", config.baseline_delay)
        s.setValue("delay_strategy", config.strategy.value)
        s.setValue("delay_multi_candidate_policy", config.multi_candidate_policy.value)
        s.setValue("delay_sample_window", config.window_size)
        s.setValue("delay_ewma_alpha", config.ewma_alpha)
        s.setValue("delay_dense_interval_width", config.dense_interval_width)

    def _save_delay_samples(self) -> None:
        self._save_delay_profiles()
        self._settings.setValue(
            "delay_sample_rounds_json",
            json.dumps(self.delay_samples(), separators=(",", ":")),
        )

    def _save_delay_profiles(self) -> None:
        if self._delay_profiles:
            self._settings.setValue(
                "delay_profiles_json",
                encode_delay_profiles(self._delay_profiles),
            )

    def _legacy_delay_config(self) -> DelayStrategyConfig:
        s = self._settings
        defaults = DelayStrategyConfig()
        window_key = "delay_sample_window" if s.contains("delay_sample_window") else "delay_window_size"
        try:
            return DelayStrategyConfig(
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
            return defaults

    def _legacy_delay_samples(self) -> list[DelaySampleRound]:
        raw_value = self._settings.value("delay_sample_rounds_json", "[]")
        try:
            decoded = json.loads(str(raw_value))
        except (TypeError, ValueError, json.JSONDecodeError):
            decoded = []
        restored: list[DelaySampleRound] = []
        if isinstance(decoded, list):
            for round_number, raw_round in enumerate(decoded, start=1):
                sample = DelaySampleRound.from_candidates(
                    raw_round,
                    round_number=round_number,
                )
                if sample.candidates:
                    restored.append(sample)
        return restored

    def _restore_delay_settings(self, *, allow_legacy_migration: bool) -> None:
        s = self._settings
        profiles = decode_delay_profiles(s.value("delay_profiles_json", ""))
        if profiles:
            self._delay_profiles = profiles
            self._activate_delay_profile(self._current_delay_species())
            return

        species_id = self._current_delay_species()
        if species_id is None:
            self._delay_profiles = {}
            self._delay_species_id = None
            self._delay_strategy_config = self._legacy_delay_config()
            self._delay_sample_rounds = []
            self._refresh_delay_ui()
            return

        samples = self._legacy_delay_samples() if allow_legacy_migration else []
        profile = DelayProfile(
            config=self._legacy_delay_config(),
            samples=samples,
            next_round_number=len(samples) + 1,
        )
        self._delay_profiles = {species_id: profile}
        self._activate_delay_profile(species_id)
        self._save_delay_profiles()

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
        target_restore_ok = self._restore_target_state()
        self._delay_show_sample_time = s.value(
            "delay_show_sample_time",
            False,
            type=bool,
        )
        self.delay_strategy_dialog.set_show_time(self._delay_show_sample_time)
        self._restore_delay_settings(allow_legacy_migration=target_restore_ok)
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
            if not saved_path:
                self._select_script(combo, None)
                continue
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
        self._refresh_target_summary()
        self._refresh_delay_ui()

    def _restore_target_state(self) -> bool:
        s = self._settings
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
        if not s.contains("target_list_json"):
            return True
        return self._restore_targets_json(str(s.value("target_list_json", "")))

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
        spin.setFont(_ui_numeric_font(14))
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
