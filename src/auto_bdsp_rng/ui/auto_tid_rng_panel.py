from __future__ import annotations

import json
import re
import csv
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QSize, QSettings, QThread, Qt, Signal, Slot
from PySide6.QtGui import QAction, QColor, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QScrollArea,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QListView,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from auto_bdsp_rng.automation.auto_rng.ocr_regions import OcrRegion
from auto_bdsp_rng.automation.auto_rng.scripts import DEFAULT_SEED_SCRIPT_NAME, choose_default_script, list_auto_scripts
from auto_bdsp_rng.automation.auto_tid_rng import AutoTidRngConfig, AutoTidRngPhase, AutoTidRngProgress
from auto_bdsp_rng.gen8_id import IDFilter, IDState8, generate_ids
from auto_bdsp_rng.rng_core import SeedPair64, SeedState32
from auto_bdsp_rng.resources import remap_legacy_script_path, script_directory
from auto_bdsp_rng.ui.automation_lifecycle import AutomationLifecycle
from auto_bdsp_rng.ui.check_box import CheckmarkCheckBox as QCheckBox
from auto_bdsp_rng.ui.combo_box import ChevronComboBox as QComboBox
from auto_bdsp_rng.ui.delay_strategy_dialog import delay_lucide_icon
from auto_bdsp_rng.ui.numeric_locale import set_c_locale
from auto_bdsp_rng.ui.workspace_controls import workspace_icon
from auto_bdsp_rng.ui.table_empty_state import TableEmptyState
from auto_bdsp_rng.ui.spin_box import ChevronSpinBox as QSpinBox
from auto_bdsp_rng.ui.tid_ocr_dialog import load_tid_ocr_region


SCRIPT_DIR = script_directory()
_TIMESTAMP_RE = re.compile(r"^\[\d{2}:\d{2}:\d{2}\]\s*")


class _CopyableLog(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        self.setUndoRedoEnabled(False)


class _IdResultTable(QTableWidget):
    searchStatusChanged = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._search_text = ""
        self._last_search_at = 0.0

    def keyPressEvent(self, event) -> None:  # noqa: N802
        text = event.text()
        if text and text.isprintable() and not event.modifiers() & (
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier
        ):
            now = time.monotonic()
            if now - self._last_search_at > 1.0:
                self._search_text = ""
            self._last_search_at = now
            self._search_text += text
            if self._select_next_prefix_match(self._search_text):
                self.searchStatusChanged.emit(f"查找: {self._search_text}")
            else:
                self.searchStatusChanged.emit(f"未找到: {self._search_text}")
            event.accept()
            return
        self._search_text = ""
        super().keyPressEvent(event)

    def _select_next_prefix_match(self, prefix: str) -> bool:
        if self.rowCount() <= 0 or self.columnCount() <= 0:
            return False
        column = self.currentColumn()
        if column < 0:
            column = 0
        start = self.currentRow()
        for offset in range(1, self.rowCount() + 1):
            row = (start + offset) % self.rowCount()
            item = self.item(row, column)
            if item is not None and item.text().lower().startswith(prefix.lower()):
                self.setCurrentCell(row, column)
                self.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)
                return True
        return False


class _TargetListWidget(QListWidget):
    targetRemoved = Signal()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.currentRow() >= 0:
            self.takeItem(self.currentRow())
            self.targetRemoved.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        item = self.itemAt(event.position().toPoint())
        if item is not None and event.button() == Qt.MouseButton.LeftButton:
            rect = self.visualItemRect(item)
            if event.position().toPoint().x() >= rect.right() - 24:
                row = self.row(item)
                if row >= 0:
                    self.takeItem(row)
                    self.targetRemoved.emit()
                    event.accept()
                    return
        super().mouseReleaseEvent(event)


class AutoTidRngWorker(QObject):
    progressChanged = Signal(object)
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

    def request_stop(self, reason: str | None = None) -> None:
        """Forward a stop request while tolerating legacy runner doubles."""

        stop = getattr(self.runner, "stop", None)
        if callable(stop):
            if reason is None:
                stop()
                return
            try:
                stop(reason=reason)
            except TypeError:
                # Older injected runners expose only ``stop()``.  Keep that
                # compatibility path usable while the production runner gets
                # the richer reason field.
                try:
                    stop()
                except TypeError:
                    # Preserve the original failure for a genuinely broken
                    # runner rather than silently reporting a successful stop.
                    raise

    @Slot()
    def stop(self, reason: str | None = None) -> None:
        self.request_stop(reason)


class AutoTidRngPanel(AutomationLifecycle, QWidget):
    startRequested = Signal(object)
    stopRequested = Signal()
    progressChanged = Signal(object)
    ocrSettingsRequested = Signal()
    runLogRequested = Signal()
    roundRecordsRequested = Signal()
    runStateChanged = Signal(bool)
    scriptEditRequested = Signal(object)

    def __init__(
        self,
        parent: QWidget | None = None,
        script_dir: Path = SCRIPT_DIR,
        run_log_sink: Callable[[str, str], None] | None = None,
        settings: QSettings | None = None,
    ) -> None:
        super().__init__(parent)
        self._init_lifecycle()
        self.script_dir = script_dir
        self._run_log_sink = run_log_sink
        self._scripts: list[Path] = []
        self._runner_thread: QThread | None = None
        self._runner_worker: AutoTidRngWorker | None = None
        self._run_state_active = False
        self._settings = settings or QSettings("auto-bdsp-rng", "AutoTidRngPanel")
        self._ocr_region = load_tid_ocr_region()
        self._restoring_state = True
        self._active_config: AutoTidRngConfig | None = None
        self._last_progress = AutoTidRngProgress()
        self._runtime_loop_index = 0
        self._last_id_states: tuple[IDState8, ...] | None = None
        self._target_key: tuple[int, int] | None = None
        self._highlighted_target_row: int | None = None
        self._reseed_reason = ""
        self._build_ui()
        self.refresh_scripts()
        self._restore_panel_state()
        self._restoring_state = False
        self._saved_panel_values = self._panel_values()
        self._refresh_script_summary()
        self._mark_config_dirty()
        self._sync_run_controls()
        self._refresh_ocr_region_text()

    def _build_ui(self) -> None:
        self.setObjectName("AutoTidRngPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.toolbar = self._build_toolbar()
        layout.addWidget(self.toolbar)
        content = QWidget(self)
        content.setObjectName("AutoTidContent")
        row = QHBoxLayout(content)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.config_panel = self._build_config_group()
        row.addWidget(self.config_panel)
        self.runtime_scroll = QScrollArea()
        self.runtime_scroll.setObjectName("AutoTidRuntimeScroll")
        self.runtime_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.runtime_scroll.setWidgetResizable(True)
        self.runtime_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.runtime_content = QWidget()
        self.runtime_content.setObjectName("AutoTidRuntimeContent")
        runtime_layout = QVBoxLayout(self.runtime_content)
        runtime_layout.setContentsMargins(18, 16, 18, 14)
        runtime_layout.setSpacing(10)
        header = QHBoxLayout()
        header.addWidget(self._section_title("运行现场"))
        header.addStretch(1)
        self.view_round_button = self._link_button("轮次记录", self.roundRecordsRequested.emit)
        self.view_round_button.setIcon(workspace_icon("external", "#087C58"))
        self.view_round_button.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        header.addWidget(self.view_round_button)
        runtime_layout.addLayout(header)
        runtime_layout.addWidget(self._build_runtime_group())
        self.script_group = self._build_script_group()
        runtime_layout.addWidget(self.script_group)
        self.id_table_group = self._build_id_table_group()
        runtime_layout.addWidget(self.id_table_group, 1)
        self.runtime_scroll.setWidget(self.runtime_content)
        row.addWidget(self.runtime_scroll, 1)
        self._legacy_log_group = self._build_log_group()
        self._legacy_log_group.setParent(self)
        self._legacy_log_group.hide()
        layout.addWidget(content, 1)
        self._apply_panel_style()
        for spin in (self.frame_threshold, self.delay, self.reverse_lookup_window, self.loop_count):
            spin.valueChanged.connect(self._mark_config_dirty)
        for combo in (self.seed_script_combo, self.name_script_combo, self.reverse_id_script_combo, self.mode_combo):
            combo.currentIndexChanged.connect(self._mark_config_dirty)
        self.debug_output_check.toggled.connect(self._mark_config_dirty)
        model = self.target_list.model()
        model.rowsInserted.connect(self._refresh_target_count)
        model.rowsRemoved.connect(self._refresh_target_count)
        model.modelReset.connect(self._refresh_target_count)

    def _apply_panel_style(self) -> None:
        self.setStyleSheet("""
            QWidget { color: #24312d; font-size: 14px; }
            QPushButton#PrimaryButton, QToolButton#PrimaryButton { color: #ffffff; background: #087c58; }
            QPushButton#PrimaryButton:disabled, QToolButton#PrimaryButton:disabled {
                color: #97a79f; background: #eff3f1; border-color: #eff3f1; }
            QPushButton#DangerButton { color: #ac4b42; }
            QPushButton#DangerButton:disabled { color: #97a79f; }
            QWidget#AutoTidRngPanel, QWidget#AutoTidContent,
            QWidget#AutoTidRuntimeContent, QScrollArea#AutoTidRuntimeScroll { background: #ffffff; }
            QFrame#AutoTidToolbar { background: #ffffff; border: 0; border-bottom: 1px solid #e2e8e4; }
            QFrame#AutoTidConfigPanel { background: #f6f8f7; border: 0; border-right: 1px solid #e2e8e4; }
            QLabel#AutoTidTitle, QLabel#AutoTidSectionTitle { font-size: 16px; font-weight: 700; }
            QLabel#AutoTidSubtitle, QLabel#AutoTidMuted, QLabel#AutoTidResultCount,
            QLabel#AutoTidTargetCount, QLabel#AutoTidSaveState { color: #596c62; font-size: 12px; }
            QLabel#AutoTidSaveState[dirty="true"] { color: #9e600e; }
            QFrame#AutoTidRuntimeCard { background: #f6f8f7; border: 0; border-radius: 6px; }
            QLabel#AutoTidRuntimePhase { font-size: 20px; font-weight: 700; }
            QLabel#AutoTidRuntimeValue { font-size: 26px; font-weight: 600; }
            QLabel#AutoTidRuntimeValue[accent="true"] { color: #087c58; }
            QLabel#AutoTidStateDot { color: #087c58; }
            QFrame#AutoTidRuntimeCard[state="failed"] QLabel#AutoTidStateDot { color: #ac4b42; }
            QFrame#AutoTidRuntimeCard[state="idle"] QLabel#AutoTidStateDot { color: #8da299; }
            QFrame#AutoTidDivider { border: 0; background: #e2e8e4; max-height: 1px; }
            QFrame#AutoTidScriptCard { background: #ffffff; border: 1px solid #dce4df; border-radius: 5px; }
            QWidget#AutoTidTargets, QWidget#AutoTidTopControls, QWidget#TargetPoolActions, QWidget#AutoTidScriptFields,
            QWidget#AutoTidScriptPicker, QWidget#AutoTidSeedFields { background: transparent; }
            QListWidget#TargetPool { background: transparent; border: 0; padding: 0; }
            QListWidget#TargetPool::item { background: #ffffff; border: 1px solid #dce4df;
                border-radius: 4px; padding: 3px 7px; margin: 1px; }
            QListWidget#TargetPool::item:selected { background: #edf7f1; color: #087c58; border-color: #87b9a6; }
            QPushButton#AutoTidLink, QToolButton#AutoTidLink { color: #087c58; background: transparent;
                border: 0; padding: 2px 0; font-size: 12px; }
            QPushButton#AutoTidLink:hover, QToolButton#AutoTidLink:hover { color: #066a4b; }
            QPushButton#AutoTidLink:disabled { color: #8da299; }
            QToolButton#AutoTidScriptEditButton { background: #ffffff; border: 1px solid #e2e8e4;
                border-radius: 5px; padding: 0; }
            QLineEdit#AutoTidTargetInput { font-size: 12px; }
            QLineEdit#AutoTidSeed { background: #f2f5f3; color: #596c62; font-size: 12px;
                font-family: "Cascadia Mono", "Consolas", monospace; }
            QTableWidget#TidResultsTable { font-size: 13px; border: 0; gridline-color: #e2e8e4; }
            QTableWidget#TidResultsTable QHeaderView::section { background: #f6f8f7; color: #596c62;
                border: 0; border-bottom: 1px solid #e2e8e4; padding: 8px; font-size: 12px; font-weight: 700; }
        """)

    def _section_title(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("AutoTidSectionTitle")
        return label

    def _muted_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("AutoTidMuted")
        return label

    def _link_button(self, text: str, callback: Callable) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("AutoTidLink")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(callback)
        return button

    def _divider(self) -> QFrame:
        line = QFrame()
        line.setObjectName("AutoTidDivider")
        line.setFixedHeight(1)
        return line

    def _sync_run_controls(self) -> None:
        super()._sync_run_controls()
        if hasattr(self, "target_list") and not self.target_list.count():
            self.start_button.setEnabled(False)
            for action in self.start_menu.actions():
                action.setEnabled(False)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._save_panel_state()
        super().closeEvent(event)

    def _build_toolbar(self) -> QWidget:
        toolbar = QFrame()
        toolbar.setObjectName("AutoTidToolbar")
        toolbar.setFixedHeight(56)
        row = QHBoxLayout(toolbar)
        row.setContentsMargins(14, 0, 14, 0)
        row.setSpacing(10)
        row.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("单次", "single")
        self.mode_combo.addItem("循环 N 次", "count")
        self.mode_combo.addItem("无限循环", "infinite")
        self.mode_combo.setVisible(False)
        self.mode_combo.setFixedHeight(34)
        self.mode_combo.setFixedWidth(120)
        self.loop_count = self._spin(1, 9999, 1)
        self.loop_count.setFixedWidth(80)
        self.loop_count.setVisible(False)
        self.debug_output_check = QCheckBox("调试")
        self.debug_output_check.setFixedHeight(34)
        self.debug_output_check.setFixedWidth(64)
        self.debug_output_check.setVisible(False)

        self.status_badge = QLabel("状态：空闲")
        self.status_badge.setObjectName("AutoTidStatus")
        self.status_badge.setFixedHeight(32)
        self.start_button = QToolButton()
        self.start_button.setIcon(workspace_icon("play", "#FFFFFF"))
        self.start_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.start_button.setText("开始")
        self.start_button.setObjectName("PrimaryButton")
        self.start_button.setFixedHeight(32)
        self.start_button.setFixedWidth(88)
        self.start_menu = QMenu(self.start_button)
        self.start_from_seed_action = QAction("从测种脚本开始", self.start_button)
        self.start_from_capture_action = QAction("从捕获 Seed 开始", self.start_button)
        self.start_menu.addAction(self.start_from_seed_action)
        self.start_menu.addAction(self.start_from_capture_action)
        self.start_button.setMenu(self.start_menu)
        self.start_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.start_button.clicked.connect(self._start_clicked)
        self.start_from_seed_action.triggered.connect(self._start_clicked)
        self.start_from_capture_action.triggered.connect(self._start_from_capture_clicked)
        self.stop_button = QPushButton("停止")
        self.stop_button.setIcon(workspace_icon("stop", "#AC4B42"))
        self.stop_button.setObjectName("DangerButton")
        self.stop_button.setFixedHeight(32)
        self.stop_button.setFixedWidth(104)
        self.stop_button.clicked.connect(self._stop_clicked)
        self.ocr_button = QPushButton("OCR 设置")
        self.ocr_button.setObjectName("SecondaryButton")
        self.ocr_button.setFixedHeight(34)
        self.ocr_button.setMinimumWidth(120)
        self.ocr_button.clicked.connect(self.ocrSettingsRequested.emit)
        self.ocr_button.setVisible(False)

        self.title_label = QLabel("自动 TID 乱数")
        self.title_label.setObjectName("AutoTidTitle")
        row.addWidget(self.title_label)
        self.subtitle_label = QLabel("未命中时自动重新测种")
        self.subtitle_label.setObjectName("AutoTidSubtitle")
        row.addWidget(self.subtitle_label)
        row.addSpacing(10)
        self.latest_log_label = QLabel("暂无消息")
        self.latest_log_label.setObjectName("AutoTidLatest")
        self.latest_log_label.setMaximumHeight(32)
        self.latest_log_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.latest_log_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.latest_log_label.setParent(toolbar)
        self.latest_log_label.hide()
        row.addStretch(1)
        self.view_log_button = QPushButton("查看日志")
        self.view_log_button.setObjectName("AutoTidLink")
        self.view_log_button.setFixedSize(76, 32)
        self.view_log_button.clicked.connect(self.runLogRequested.emit)
        row.addWidget(self.view_log_button)
        self.status_badge.setParent(toolbar)
        self.status_badge.hide()
        row.addWidget(self.start_button)
        row.addWidget(self.stop_button)
        row.addWidget(self.ocr_button)
        return toolbar

    def _build_config_group(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("AutoTidConfigPanel")
        panel.setFixedWidth(326)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(18, 16, 18, 18)
        layout.setSpacing(18)
        header = QHBoxLayout()
        header.addWidget(self._section_title("任务配置"))
        header.addStretch(1)
        self.save_state_label = QLabel("已保存")
        self.save_state_label.setObjectName("AutoTidSaveState")
        header.addWidget(self.save_state_label)
        layout.addLayout(header)
        self.target_group = self._build_target_group()
        layout.addWidget(self.target_group)
        self.top_controls_group = self._build_top_controls_group()
        layout.addWidget(self.top_controls_group)
        layout.addWidget(self._divider())
        footer = QHBoxLayout()
        footer.addWidget(self._muted_label("配置下次启动生效"))
        footer.addStretch(1)
        self.save_button = QPushButton("保存")
        self.save_button.setObjectName("PrimaryButton")
        self.save_button.setFixedSize(64, 34)
        self.save_button.setToolTip("保存当前配置；点击开始也会自动保存。运行中的任务使用启动时的配置。")
        self.save_button.clicked.connect(self._save_panel_state)
        footer.addWidget(self.save_button)
        layout.addLayout(footer)
        layout.addStretch(1)
        return panel

    def _build_top_controls_group(self) -> QWidget:
        group = QWidget()
        group.setObjectName("AutoTidTopControls")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.frame_threshold = self._spin(0, 1_000_000_000, 300)
        self.delay = self._spin(0, 1_000_000_000, 0)
        self.frame_threshold.setSuffix(" 帧")
        self.delay.setSuffix(" 帧")
        self.frame_threshold.setToolTip("生成从 0 到此帧数的 ID 数据，选择最早匹配的目标 Display TID。")
        self.delay.setToolTip("取名脚本启动帧 = 目标帧数 − delay。修改后下次启动生效。")
        for title, field in (("搜索范围", self.frame_threshold), ("取名 delay", self.delay)):
            if layout.count():
                layout.addSpacing(10)
            label = QLabel(title)
            label.setBuddy(field)
            layout.addWidget(label)
            layout.addWidget(field)
        self.reverse_lookup_window = self._spin(0, 10_000, 50)
        self.reverse_lookup_window.setParent(group)
        self.reverse_lookup_window.hide()
        return group

    def _build_script_picker(
        self,
        parent: QWidget,
        combo: QComboBox,
        label: str,
    ) -> QWidget:
        picker = QWidget(parent)
        picker.setObjectName("AutoTidScriptPicker")
        picker_layout = QHBoxLayout(picker)
        picker_layout.setContentsMargins(0, 0, 0, 0)
        picker_layout.setSpacing(5)
        picker_layout.addWidget(combo)
        edit_button = QToolButton(picker)
        edit_button.setObjectName("AutoTidScriptEditButton")
        edit_button.setFixedSize(32, 32)
        edit_button.setIcon(delay_lucide_icon("square-pen", "#5F6C66", 16))
        edit_button.setIconSize(QSize(16, 16))
        edit_button.setToolTip(f"编辑{label}")
        edit_button.setAccessibleName(f"编辑{label}")
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
        picker_layout.addWidget(edit_button)
        self.script_edit_buttons[combo] = edit_button
        self.script_picker_widgets[combo] = picker
        return picker

    def _build_target_group(self) -> QWidget:
        group = QWidget()
        group.setObjectName("AutoTidTargets")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.target_title_label = QLabel("目标 Display TID")
        layout.addWidget(self.target_title_label)
        self.target_list = _TargetListWidget()
        self.target_list.setObjectName("TargetPool")
        self.target_list.setAccessibleName("目标 Display TID 列表；点击标签右侧 × 或按 Delete 删除")
        self.target_list.setToolTip("点击右侧 × 删除目标；也可选中后按 Delete。双击数字可编辑。")
        self.target_list.setViewMode(QListView.ViewMode.IconMode)
        self.target_list.setFlow(QListView.Flow.LeftToRight)
        self.target_list.setWrapping(True)
        self.target_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.target_list.setMovement(QListView.Movement.Static)
        self.target_list.setSpacing(5)
        self.target_list.setGridSize(QSize(116, 34))
        self.target_list.setUniformItemSizes(True)
        self.target_list.setFixedHeight(124)
        self.target_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.target_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.target_list.itemChanged.connect(self._normalize_edited_target_item)
        self.target_list.targetRemoved.connect(self._refresh_target_count)
        layout.addWidget(self.target_list)
        action_panel = QWidget()
        action_panel.setObjectName("TargetPoolActions")
        actions = QGridLayout(action_panel)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setHorizontalSpacing(6)
        actions.setVerticalSpacing(6)
        self.target_input = QLineEdit()
        self.target_input.setObjectName("AutoTidTargetInput")
        self.target_input.setPlaceholderText("000000–999999，可粘贴多个")
        self.target_input.setAccessibleName("添加目标 Display TID")
        self.target_input.setFixedHeight(34)
        self.target_input.returnPressed.connect(self._add_target_from_input)
        self.add_target_button = QPushButton("+")
        self.add_target_button.setFixedSize(34, 34)
        self.add_target_button.setAccessibleName("添加目标 Display TID")
        self.add_target_button.clicked.connect(self._add_target_from_input)
        actions.addWidget(self.target_input, 0, 0)
        actions.addWidget(self.add_target_button, 0, 1)
        self.target_count_label = self._muted_label("0 个目标")
        self.target_count_label.setObjectName("AutoTidTargetCount")
        actions.addWidget(self.target_count_label, 1, 0)
        self.clear_targets_button = self._link_button("清空", self._clear_targets)
        actions.addWidget(self.clear_targets_button, 1, 1)
        self.update_target_button = QPushButton("更新", action_panel)
        self.update_target_button.clicked.connect(self._update_selected_target)
        self.update_target_button.hide()
        self.delete_target_button = QPushButton("删除", action_panel)
        self.delete_target_button.clicked.connect(self._delete_selected_target)
        self.delete_target_button.hide()
        layout.addWidget(action_panel)
        self.target_hint_label = self._muted_label("添加目标后即可开始")
        layout.addWidget(self.target_hint_label)
        return group

    def _build_runtime_group(self) -> QFrame:
        self.runtime_card = QFrame()
        self.runtime_card.setObjectName("AutoTidRuntimeCard")
        self.runtime_card.setProperty("state", "idle")
        layout = QVBoxLayout(self.runtime_card)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(8)
        top = QHBoxLayout()
        self.runtime_state_dot = QLabel("●")
        self.runtime_state_dot.setObjectName("AutoTidStateDot")
        top.addWidget(self.runtime_state_dot)
        self.runtime_phase_label = QLabel("准备就绪")
        self.runtime_phase_label.setObjectName("AutoTidRuntimePhase")
        top.addWidget(self.runtime_phase_label)
        top.addStretch(1)
        self.runtime_round_label = self._muted_label("尚未开始")
        top.addWidget(self.runtime_round_label)
        layout.addLayout(top)
        self.runtime_description_label = self._muted_label("设置目标 Display TID 和脚本，然后开始任务。")
        self.runtime_description_label.setWordWrap(True)
        self.runtime_description_label.setMinimumHeight(38)
        layout.addWidget(self.runtime_description_label)
        metrics = QHBoxLayout()
        metrics.setSpacing(16)
        for title, attr, accent in (("当前帧数", "runtime_current_value", False),
                                    ("目标帧数", "runtime_target_value", False),
                                    ("距离取名启动", "runtime_remaining_value", True)):
            field = QVBoxLayout()
            field.setSpacing(4)
            field.addWidget(self._muted_label(title))
            value = QLabel("—")
            value.setObjectName("AutoTidRuntimeValue")
            value.setProperty("accent", accent)
            font = QFont()
            font.setFamilies(["Microsoft YaHei UI", "PingFang SC", "Noto Sans CJK SC", "Segoe UI", "sans-serif"])
            font.setPixelSize(26)
            font.setWeight(QFont.Weight.DemiBold)
            font.setFeature(QFont.Tag("tnum"), 1)
            value.setFont(font)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            setattr(self, attr, value)
            field.addWidget(value)
            metrics.addLayout(field, 1)
        self.runtime_current_value.setToolTip("根据小卡比兽眨眼间隔更新 RNG 帧数。取名脚本执行后不再进行实时计数。")
        self.runtime_remaining_value.setToolTip("取名脚本触发帧减去当前帧数；按眨眼推进，不按固定 FPS 换算。")
        layout.addLayout(metrics)
        layout.addWidget(self._divider())
        footer = QHBoxLayout()
        footer.addWidget(self._muted_label("本次 delay"))
        self.runtime_delay_value = QLabel("—")
        footer.addWidget(self.runtime_delay_value)
        footer.addStretch(1)
        self.target_data_button = self._link_button("查看目标数据", self._locate_target)
        self.target_data_button.setEnabled(False)
        footer.addWidget(self.target_data_button)
        layout.addLayout(footer)
        # Hidden compatibility fields are still updated for OCR integrations.
        self.target_result = QLabel("—", self)
        self.trigger_result = QLabel("—", self)
        self.ocr_result = QLabel("—", self)
        self.actual_delay_result = QLabel("—", self)
        for label in (self.target_result, self.trigger_result, self.ocr_result, self.actual_delay_result):
            label.hide()
        return self.runtime_card

    def _build_script_group(self) -> QWidget:
        group = QWidget()
        layout = QVBoxLayout(group)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        header = QHBoxLayout()
        header.addWidget(self._section_title("任务脚本"))
        header.addStretch(1)
        header.addWidget(self._muted_label("下次启动生效"))
        layout.addLayout(header)
        card = QFrame()
        card.setObjectName("AutoTidScriptCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        summary = QHBoxLayout()
        labels = QVBoxLayout()
        labels.setSpacing(2)
        labels.addWidget(QLabel("TID 测种与取名"))
        self.script_summary_label = self._muted_label("测种 / 取名 · 0 个脚本")
        labels.addWidget(self.script_summary_label)
        summary.addLayout(labels, 1)
        self.script_toggle = QToolButton()
        self.script_toggle.setObjectName("AutoTidLink")
        self.script_toggle.setText("展开编辑")
        self.script_toggle.setCheckable(True)
        self.script_toggle.setArrowType(Qt.ArrowType.DownArrow)
        self.script_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.script_toggle.setAccessibleName("展开任务脚本编辑")
        summary.addWidget(self.script_toggle)
        card_layout.addLayout(summary)
        self.script_fields = QWidget()
        self.script_fields.setObjectName("AutoTidScriptFields")
        fields = QGridLayout(self.script_fields)
        fields.setContentsMargins(0, 10, 0, 0)
        fields.setHorizontalSpacing(12)
        fields.setVerticalSpacing(8)
        self.seed_script_combo = QComboBox()
        self.name_script_combo = QComboBox()
        self.reverse_id_script_combo = QComboBox(self.script_fields)
        self.reverse_id_script_combo.hide()
        self.script_edit_buttons = {}
        self.script_picker_widgets = {}
        for column, (title, combo) in enumerate((("测种脚本", self.seed_script_combo), ("取名脚本", self.name_script_combo))):
            combo.setMinimumWidth(160)
            combo.setFixedHeight(32)
            combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            picker = self._build_script_picker(self.script_fields, combo, title)
            fields.addWidget(self._muted_label(title), 0, column)
            fields.addWidget(picker, 1, column)
            fields.setColumnStretch(column, 1)
            combo.currentIndexChanged.connect(self._refresh_script_summary)
        self.seed_script_picker = self.script_picker_widgets[self.seed_script_combo]
        self.name_script_picker = self.script_picker_widgets[self.name_script_combo]
        self.refresh_scripts_button = self._link_button("刷新脚本列表", self.refresh_scripts)
        fields.addWidget(self.refresh_scripts_button, 2, 0, 1, 2, Qt.AlignmentFlag.AlignRight)
        card_layout.addWidget(self.script_fields)
        self.script_fields.hide()
        self.script_toggle.toggled.connect(self._set_scripts_expanded)
        self.ocr_region_label = QLabel("", self.script_fields)
        self.ocr_region_label.hide()
        layout.addWidget(card)
        return group

    def _set_scripts_expanded(self, expanded: bool) -> None:
        self.script_fields.setVisible(expanded)
        self.script_toggle.setText("收起编辑" if expanded else "展开编辑")
        self.script_toggle.setArrowType(Qt.ArrowType.UpArrow if expanded else Qt.ArrowType.DownArrow)
        self.script_toggle.setAccessibleName("收起任务脚本编辑" if expanded else "展开任务脚本编辑")

    def _refresh_script_summary(self) -> None:
        combos = (self.seed_script_combo, self.name_script_combo)
        paths = [self._selected_path(combo) for combo in combos]
        self.script_summary_label.setText(f"测种 / 取名 · {sum(path is not None for path in paths)} 个脚本")
        self.script_summary_label.setToolTip(" / ".join(path.name if path else "未选择" for path in paths))

    def _build_log_group(self) -> QGroupBox:
        group = QGroupBox("日志")
        layout = QVBoxLayout(group)
        self.log_view = _CopyableLog()
        self.log_view.setObjectName("LogView")
        self.log_view.setFont(QFont("Consolas", 10))
        layout.addWidget(self.log_view)
        self.log_view.setVisible(False)
        return group

    def _build_id_table_group(self) -> QWidget:
        group = QWidget()
        group.setObjectName("AutoTidResults")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        toolbar = QHBoxLayout()
        toolbar.addWidget(self._section_title("ID 数据"))
        toolbar.addStretch(1)
        self.id_result_count = self._muted_label("0 条结果")
        self.id_result_count.setObjectName("AutoTidResultCount")
        toolbar.addWidget(self.id_result_count)
        self.copy_button = self._link_button("复制", self.copy_results)
        self.export_button = self._link_button("导出 CSV", self.export_results)
        toolbar.addWidget(self.copy_button)
        toolbar.addWidget(self.export_button)
        layout.addLayout(toolbar)
        self.seed_toggle = QToolButton()
        self.seed_toggle.setObjectName("AutoTidLink")
        self.seed_toggle.setText("Seed 信息")
        self.seed_toggle.setCheckable(True)
        self.seed_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.seed_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        layout.addWidget(self.seed_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        self.seed_fields = QWidget()
        self.seed_fields.setObjectName("AutoTidSeedFields")
        seed_bar = QGridLayout(self.seed_fields)
        seed_bar.setContentsMargins(0, 0, 0, 0)
        seed_bar.setHorizontalSpacing(8)
        self.tid_seed_inputs = []
        for column, label_text in enumerate(("Seed0", "Seed1")):
            seed_box = QLineEdit()
            seed_box.setObjectName("AutoTidSeed")
            seed_box.setReadOnly(True)
            seed_box.setFixedHeight(32)
            seed_box.setMinimumWidth(160)
            seed_box.setPlaceholderText("未捕获")
            seed_box.setAccessibleName(label_text)
            self.tid_seed_inputs.append(seed_box)
            seed_bar.addWidget(self._muted_label(label_text), 0, column)
            seed_bar.addWidget(seed_box, 1, column)
        layout.addWidget(self.seed_fields)
        self.seed_fields.hide()
        self.seed_toggle.toggled.connect(self._set_seed_expanded)
        self._id_states: list[IDState8] = []
        self.id_table = _IdResultTable()
        self.id_table.setObjectName("TidResultsTable")
        self.id_table.setShowGrid(False)
        self.id_table.setColumnCount(5)
        self.id_table.setHorizontalHeaderLabels(("帧数", "TID", "SID", "TSV", "Display TID"))
        self.id_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.id_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.id_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.id_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.id_table.customContextMenuRequested.connect(self._show_table_context_menu)
        self.id_table.searchStatusChanged.connect(self._show_id_search_status)
        self.id_table.verticalHeader().setVisible(False)
        self.id_table.verticalHeader().setDefaultSectionSize(32)
        self.id_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.id_table.horizontalHeader().setStretchLastSection(True)
        self.id_table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.id_table.setMinimumHeight(202)
        layout.addWidget(self.id_table, 1)
        self.id_empty_state = TableEmptyState(self.id_table)
        self._id_result_state = "initial"
        self._refresh_id_result_state()
        return group

    def _set_seed_expanded(self, expanded: bool) -> None:
        self.seed_fields.setVisible(expanded)
        self.seed_toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)

    def _show_id_search_status(self, message: str) -> None:
        summary = message if len(message) <= 20 else message[:20] + "…"
        self.id_result_count.setText(f"{len(self._id_states)} 条结果 · {summary}")
        self.id_result_count.setToolTip(message)

    def refresh_scripts(self) -> None:
        self._scripts = list_auto_scripts(self.script_dir)
        defaults = (
            (self.seed_script_combo, choose_default_script(self._scripts, DEFAULT_SEED_SCRIPT_NAME)),
            (self.name_script_combo, self._choose_script_by_keywords(("取名", "name"))),
            (self.reverse_id_script_combo, self._choose_script_by_keywords(("反查ID", "反查 ID", "id"))),
        )
        for combo, default in defaults:
            current = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("请选择", None)
            for path in self._scripts:
                combo.addItem(path.name, str(path))
            combo.blockSignals(False)
            if current:
                self._select_script_by_path(combo, str(current))
            elif self._restoring_state:
                self._select_script(combo, default)
        for combo in (self.seed_script_combo, self.name_script_combo):
            self._update_script_edit_button(combo)
        self._refresh_script_summary()
        self._mark_config_dirty()

    def _update_script_edit_button(self, combo: QComboBox) -> None:
        button = self.script_edit_buttons.get(combo)
        if button is not None:
            button.setEnabled(self._selected_path(combo) is not None)

    def _request_script_edit(self, combo: QComboBox) -> None:
        path = self._selected_path(combo)
        if path is not None:
            self.scriptEditRequested.emit(path)

    def add_target_display_tid(self, tid: int) -> None:
        tid = self._validate_display_tid_value(tid)
        if tid in self.target_display_tids():
            return
        item = QListWidgetItem(self._target_item_text(tid))
        item.setData(Qt.ItemDataRole.UserRole, tid)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        self.target_list.addItem(item)
        self._refresh_target_count()

    def add_target_tid(self, tid: int) -> None:
        self.add_target_display_tid(tid)

    def target_display_tids(self) -> tuple[int, ...]:
        values: list[int] = []
        for row in range(self.target_list.count()):
            item = self.target_list.item(row)
            value = item.data(Qt.ItemDataRole.UserRole)
            if value is None:
                value = self._parse_tid(item.text())
            if value is not None:
                values.append(int(value))
        return tuple(values)

    def target_tids(self) -> tuple[int, ...]:
        return self.target_display_tids()

    def set_tid_seed(self, seed: SeedPair64 | SeedState32, *, generate: bool = True) -> None:
        seed_pair = seed.to_seed_pair64() if isinstance(seed, SeedState32) else seed
        for box, text in zip(self.tid_seed_inputs, seed_pair.format_seeds()):
            box.setText(text)
        if not generate:
            self._id_result_state = "searching"
            self._refresh_id_result_state()
            return
        self.set_id_states(
            generate_ids(
                seed_pair,
                initial_advances=0,
                max_advances=max(0, int(self.frame_threshold.value())) + 1,
                state_filter=IDFilter(),
            )
        )

    def build_config(self, *, start_phase: AutoTidRngPhase = AutoTidRngPhase.RUN_SEED_SCRIPT) -> AutoTidRngConfig:
        target_display_tids = self.target_display_tids()
        if not target_display_tids:
            raise ValueError("请至少添加一个目标 Display TID")
        return AutoTidRngConfig(
            script_dir=self.script_dir,
            seed_script_path=self._selected_path(self.seed_script_combo),
            name_script_path=self._selected_path(self.name_script_combo),
            reverse_id_script_path=self._selected_path(self.reverse_id_script_combo),
            start_phase=start_phase,
            frame_threshold=self.frame_threshold.value(),
            target_display_tids=target_display_tids,
            delay=self.delay.value(),
            reverse_lookup_window=self.reverse_lookup_window.value(),
            ocr_region=self._ocr_region,
            loop_mode=str(self.mode_combo.currentData()),
            loop_count=self.loop_count.value(),
            debug_output=self.debug_output_check.isChecked(),
        )

    def set_ocr_region(self, region: OcrRegion | tuple[int, int, int, int]) -> None:
        if not isinstance(region, OcrRegion):
            region = OcrRegion(*(int(value) for value in region))
        self._ocr_region = region
        self._refresh_ocr_region_text()

    def run_with_runner(self, runner: object) -> None:
        if self._runner_thread is not None:
            self.add_log("自动 TID 乱数已在运行", level="WARNING")
            return
        config = getattr(runner, "config", None)
        if isinstance(config, AutoTidRngConfig):
            self._active_config = config
        self._runtime_loop_index = 0
        self._reseed_reason = ""
        self._clear_runtime_data()
        thread = QThread(self)
        worker = AutoTidRngWorker(runner)
        worker.moveToThread(thread)
        worker.progressChanged.connect(self.apply_progress)
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

    def apply_progress(self, progress: AutoTidRngProgress) -> None:
        previous = self._last_progress
        new_cycle = progress.loop_index > 0 and progress.loop_index != self._runtime_loop_index
        entering_seed = progress.phase in (AutoTidRngPhase.RUN_SEED_SCRIPT, AutoTidRngPhase.CAPTURE_TIDSID)
        if new_cycle or (entering_seed and previous.phase not in (
            AutoTidRngPhase.RUN_SEED_SCRIPT, AutoTidRngPhase.CAPTURE_TIDSID
        )):
            self._clear_runtime_data()
        if new_cycle:
            self._runtime_loop_index = progress.loop_index
            if progress.loop_index <= 1:
                self._reseed_reason = ""
        if entering_seed and progress.loop_index > 1:
            if "测种失败" in progress.log_message:
                self._reseed_reason = "上一轮测种失败，正在重新运行测种脚本。"
            elif "超过" in progress.log_message or "小于 delay" in progress.log_message:
                self._reseed_reason = "上一轮取名触发帧不可用，正在重新运行测种脚本。"
        if progress.seed_text:
            words = progress.seed_text.split()
            if len(words) == 2:
                for box, text in zip(self.tid_seed_inputs, words):
                    box.setText(text)
        if progress.id_states or progress.id_search_completed:
            # Ordinary wait callbacks retain the same immutable result tuple.
            # Avoid rebuilding the table or resetting the user's selection/scroll.
            if progress.id_states is not self._last_id_states:
                self.set_id_states(list(progress.id_states))
                self._last_id_states = progress.id_states
            if progress.phase == AutoTidRngPhase.SEARCH_TARGET and progress.target_advances is None:
                self._reseed_reason = "上一轮搜索范围内未找到目标，正在重新运行测种脚本。"
        elif progress.phase == AutoTidRngPhase.SEARCH_TARGET:
            self._id_result_state = "searching"
        elif progress.phase == AutoTidRngPhase.FAILED:
            self._id_result_state = "failed"
        elif progress.phase == AutoTidRngPhase.IDLE and self._id_result_state == "searching":
            self._id_result_state = "initial"
        target_key = None
        if progress.target_advances is not None and progress.target_display_tid is not None:
            target_key = (progress.target_advances, progress.target_display_tid)
        if self._target_key != target_key:
            self._target_key = target_key
            self._highlight_target()
        self._refresh_id_result_state()
        self._last_progress = progress
        self._render_runtime(progress)
        if progress.target_tid is not None and progress.target_advances is not None:
            display = "—" if progress.target_display_tid is None else f"{progress.target_display_tid:06d}"
            sid = "—" if progress.target_sid is None else str(progress.target_sid)
            self.target_result.setText(f"Display TID {display} / TID {progress.target_tid} / SID {sid} / 帧数 {progress.target_advances}")
        if progress.trigger_advances is not None:
            self.trigger_result.setText(str(progress.trigger_advances))
        if progress.ocr_tid is not None:
            self.ocr_result.setText(f"{progress.ocr_tid}（原始：{progress.ocr_text or '—'}）")
        elif progress.ocr_text:
            self.ocr_result.setText(progress.ocr_text)
        if progress.actual_delay is not None:
            self.actual_delay_result.setText(str(progress.actual_delay))
        self.progressChanged.emit(progress)
        if progress.log_message:
            level = "ERROR" if progress.phase == AutoTidRngPhase.FAILED else "INFO"
            self.add_log(progress.log_message, level=level)

    def _clear_runtime_data(self) -> None:
        self._target_key = None
        self._highlighted_target_row = None
        self.set_id_states([])
        self._last_id_states = None
        self._id_result_state = "initial"
        for box in self.tid_seed_inputs:
            box.clear()
        for label in (self.target_result, self.trigger_result, self.ocr_result, self.actual_delay_result,
                      self.runtime_current_value, self.runtime_target_value, self.runtime_remaining_value):
            label.setText("—")
        self._refresh_id_result_state()

    @staticmethod
    def _runtime_number(value: int | None) -> str:
        return "—" if value is None else f"{value:,}"

    def _render_runtime(self, progress: AutoTidRngProgress) -> None:
        phase = progress.phase
        phase_text = phase.value if hasattr(phase, "value") else str(phase)
        self.status_badge.setText(f"状态：{phase_text}")
        title = phase_text
        description = ""
        display = "—" if progress.target_display_tid is None else f"{progress.target_display_tid:06d}"
        current = progress.current_advances
        target = progress.target_advances
        remaining = None
        state = "running"
        if phase == AutoTidRngPhase.IDLE:
            stopped = bool(progress.stop_reason or progress.loop_index or self._stop_pending)
            title = "任务已停止" if stopped else "准备就绪"
            description = "已结束本次任务。" if stopped else "设置目标 Display TID 和脚本，然后开始任务。"
            current = None
            state = "idle"
        elif phase == AutoTidRngPhase.RUN_SEED_SCRIPT:
            title = "重新测种" if progress.loop_index > 1 else "运行测种脚本"
            description = self._reseed_reason if progress.loop_index > 1 and self._reseed_reason else "正在执行测种脚本，完成后自动捕获 Seed。"
            current = target = None
        elif phase == AutoTidRngPhase.CAPTURE_TIDSID:
            title = "TID/SID 测种"
            description = "正在采集小卡比兽眨眼，恢复当前 Seed。"
            current = target = None
        elif phase == AutoTidRngPhase.SEARCH_TARGET:
            title = "搜索目标 TID"
            description = "正在生成当前 Seed 的 ID 数据，匹配目标 Display TID。"
            if progress.id_search_completed and target is None:
                description = "搜索范围内未找到目标，即将重新测种。"
        elif phase == AutoTidRngPhase.WAIT_NAME_TRIGGER:
            title = "等待取名"
            trigger = progress.trigger_advances
            description = f"已选中 Display TID {display}，到达 {self._runtime_number(trigger)} 帧时执行取名脚本。"
            if trigger is not None and current is not None:
                remaining = max(0, trigger - current)
            else:
                remaining = progress.remaining_to_trigger
        elif phase == AutoTidRngPhase.RUN_NAME_SCRIPT:
            title = "执行取名脚本"
            description = f"已到达取名触发帧，正在执行取名脚本。目标 Display TID {display}。"
            current = None
        elif phase == AutoTidRngPhase.COMPLETED:
            title = "delay 校准已完成" if progress.actual_delay is not None else "取名脚本已完成"
            description = (f"实际 delay {progress.actual_delay} 帧。" if progress.actual_delay is not None
                           else f"本次任务已结束，目标 Display TID 为 {display}。")
            current = None
            state = "completed"
        elif phase == AutoTidRngPhase.FAILED:
            title = "任务失败"
            description = "任务执行失败，请查看日志中的错误信息。"
            current = None
            state = "failed"
        else:
            # Retained reverse/OCR integration phases are reported when actually received.
            description = phase_text
        self.runtime_phase_label.setText(title)
        self.runtime_description_label.setText(description)
        self.runtime_description_label.setToolTip(progress.log_message or description)
        self.runtime_round_label.setText(f"第 {progress.loop_index} 轮" if progress.loop_index > 0 else "尚未开始")
        self.runtime_current_value.setText(self._runtime_number(current))
        self.runtime_target_value.setText(self._runtime_number(target))
        self.runtime_remaining_value.setText("—" if remaining is None else f"{remaining:,} 帧")
        delay = self._active_config.delay if self._active_config is not None else None
        if delay is None and progress.target_advances is not None and progress.trigger_advances is not None:
            delay = progress.target_advances - progress.trigger_advances
        self.runtime_delay_value.setText("—" if delay is None else f"{delay:,} 帧")
        if self.runtime_card.property("state") != state:
            self.runtime_card.setProperty("state", state)
            self.runtime_state_dot.style().unpolish(self.runtime_state_dot)
            self.runtime_state_dot.style().polish(self.runtime_state_dot)

    def _highlight_target(self) -> None:
        previous = self._highlighted_target_row
        self._highlighted_target_row = None
        if previous is not None and previous < self.id_table.rowCount():
            for column in range(self.id_table.columnCount()):
                item = self.id_table.item(previous, column)
                if item is not None:
                    item.setData(Qt.ItemDataRole.BackgroundRole, None)
                    item.setData(Qt.ItemDataRole.ForegroundRole, None)
                    item.setToolTip("")
        if self._target_key is not None:
            for row, state in enumerate(self._id_states):
                if (state.advances, state.display_tid) == self._target_key:
                    self._highlighted_target_row = row
                    for column in range(self.id_table.columnCount()):
                        item = self.id_table.item(row, column)
                        item.setBackground(QColor("#edf7f1"))
                        item.setForeground(QColor("#087c58"))
                        item.setToolTip(f"本轮目标 Display TID {state.display_tid:06d}")
                    break
        self.target_data_button.setEnabled(self._highlighted_target_row is not None)

    def _locate_target(self) -> None:
        row = self._highlighted_target_row
        if row is None:
            return
        self.runtime_scroll.ensureWidgetVisible(self.id_table_group)
        self.id_table.setCurrentCell(row, 4)
        self.id_table.scrollToItem(self.id_table.item(row, 4), QAbstractItemView.ScrollHint.PositionAtCenter)

    def add_log(self, message: str, *, level: str = "INFO") -> None:
        text = str(message)
        if self._run_log_sink is not None:
            try:
                self._run_log_sink(level, text)
            except Exception:
                pass
        timestamp = datetime.now().strftime("%H:%M:%S")
        lines = text.splitlines() or [""]
        stamped = [line if _TIMESTAMP_RE.match(line) else f"[{timestamp}] {line}" for line in lines]
        self.log_view.appendPlainText("\n".join(stamped))
        latest_line = next((line.strip() for line in reversed(lines) if line.strip()), None)
        if latest_line is not None:
            self.latest_log_label.setText(latest_line)
            self.latest_log_label.setToolTip(text)

    def set_id_states(self, states: list[IDState8]) -> None:
        self._id_result_state = "complete"
        self._id_states = list(states)
        self._last_id_states = tuple(states)
        self._highlighted_target_row = None
        self.id_table.setRowCount(len(self._id_states))
        for row, state in enumerate(self._id_states):
            values = (
                str(state.advances),
                str(state.tid),
                str(state.sid),
                str(state.tsv),
                f"{state.display_tid:06d}",
            )
            for column, value in enumerate(values):
                self.id_table.setItem(row, column, QTableWidgetItem(value))
        self.id_result_count.setText(f"{len(self._id_states)} 条结果")
        self._highlight_target()
        self._refresh_id_result_state()

    def _refresh_id_result_state(self) -> None:
        messages = {
            "searching": ("正在搜索", "正在生成当前 Seed 的 ID 数据"),
            "complete": ("没有符合当前条件的结果", "请检查帧数阈值和搜索范围"),
            "failed": ("搜索未完成", "请查看日志中的错误信息后重试"),
        }
        initial = ("尚未生成 ID 数据", "运行测种流程后将在这里显示 ID 数据") if all(box.text() for box in self.tid_seed_inputs) else ("尚未捕获 Seed", "运行测种流程后将在这里显示 ID 数据")
        title, detail = messages.get(self._id_result_state, initial)
        has_results = bool(self._id_states)
        self.id_empty_state.show_message(title, detail, has_results=has_results)
        self.copy_button.setEnabled(has_results)
        self.export_button.setEnabled(has_results)

    def _table_text(self) -> str:
        rows = ["Adv\tTID\tSID\tTSV\tDisplay TID"]
        for state in self._id_states:
            rows.append(
                "\t".join(
                    (
                        str(state.advances),
                        str(state.tid),
                        str(state.sid),
                        str(state.tsv),
                        f"{state.display_tid:06d}",
                    )
                )
            )
        return "\n".join(rows)

    def _show_table_context_menu(self, position) -> None:
        menu = QMenu(self.id_table)
        copy_action = menu.addAction("复制")
        csv_action = menu.addAction("导出 CSV")
        copy_action.setEnabled(bool(self._id_states))
        csv_action.setEnabled(bool(self._id_states))
        selected = menu.exec(self.id_table.viewport().mapToGlobal(position))
        if selected == copy_action:
            self.copy_results()
        elif selected == csv_action:
            self.export_results()

    def copy_results(self) -> None:
        if not self._id_states:
            self.status_badge.setText("没有可复制的 ID 数据")
            return
        QGuiApplication.clipboard().setText(self._table_text())
        self.status_badge.setText(f"已复制 {len(self._id_states)} 条 ID 数据")

    def export_results(self) -> None:
        if not self._id_states:
            self.status_badge.setText("没有可导出的 ID 数据")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出 ID 数据", "auto_tid_id_results.csv", "CSV files (*.csv)")
        if not path:
            return
        output = Path(path)
        with output.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(("Adv", "TID", "SID", "TSV", "Display TID"))
            for state in self._id_states:
                writer.writerow((state.advances, state.tid, state.sid, state.tsv, f"{state.display_tid:06d}"))
        self.status_badge.setText(f"已导出 {output.name}")

    def _runner_finished(self, progress: object) -> None:
        if isinstance(progress, AutoTidRngProgress):
            # The worker already emitted this progress; don't log or publish it twice.
            self._last_progress = progress
            self._render_runtime(progress)
        self._runner_returned()

    def _runner_failed(self, message: str) -> None:
        progress = replace(self._last_progress, phase=AutoTidRngPhase.FAILED, log_message=message)
        self._last_progress = progress
        self._render_runtime(progress)
        self._id_result_state = "failed"
        self._refresh_id_result_state()
        self.add_log(message, level="ERROR")
        self._runner_returned()

    def _start_clicked(self) -> None:
        self._start_with_phase(AutoTidRngPhase.RUN_SEED_SCRIPT)

    def _start_from_capture_clicked(self) -> None:
        self._start_with_phase(AutoTidRngPhase.CAPTURE_TIDSID)

    def _start_with_phase(self, start_phase: AutoTidRngPhase) -> None:
        if not self.start_button.isEnabled():
            return
        self._save_panel_state()
        try:
            config = self.build_config(start_phase=start_phase)
            self._validate_config(config)
        except Exception as exc:
            self.status_badge.setText("状态：配置错误")
            self.runtime_phase_label.setText("配置错误")
            self.runtime_description_label.setText(str(exc))
            self.add_log(str(exc), level="WARNING")
            return
        self._active_config = config
        self._runtime_loop_index = 0
        self._reseed_reason = ""
        self._clear_runtime_data()
        self.startRequested.emit(config)

    def _stop_clicked(self) -> None:
        self.request_stop("用户点击停止按钮")

    def _validate_config(self, config: AutoTidRngConfig) -> None:
        if config.start_phase == AutoTidRngPhase.RUN_SEED_SCRIPT and config.seed_script_path is None:
            raise ValueError("请选择测种脚本")
        if config.name_script_path is None:
            raise ValueError("请选择取名脚本")
        paths = [config.name_script_path]
        if config.start_phase == AutoTidRngPhase.RUN_SEED_SCRIPT:
            paths.append(config.seed_script_path)
        for path in paths:
            if path is None:
                continue
            path.read_text(encoding="utf-8")

    def _selected_path(self, combo: QComboBox) -> Path | None:
        value = combo.currentData()
        return Path(value) if value else None

    def _select_script(self, combo: QComboBox, path: Path | None) -> None:
        if path is None:
            return
        self._select_script_by_path(combo, str(path))

    def _select_script_by_path(self, combo: QComboBox, path_str: str) -> Path | None:
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

    def _choose_script_by_keywords(self, keywords: tuple[str, ...]) -> Path | None:
        for path in self._scripts:
            lowered = path.name.casefold()
            if any(keyword.casefold().replace(" ", "") in lowered.replace(" ", "") for keyword in keywords):
                return path
        return None

    def _add_target_from_input(self) -> None:
        values = self._parse_tid_list(self.target_input.text())
        if not values:
            self.target_hint_label.setText("请输入 0–999999 的数字")
            self.add_log("目标 Display TID 必须是 0-999999 的数字", level="WARNING")
            return
        for value in values:
            self.add_target_display_tid(value)
        self.target_input.clear()

    def _update_selected_target(self) -> None:
        item = self.target_list.currentItem()
        value = self._parse_tid(self.target_input.text())
        if item is None or value is None:
            self.add_log("请选择目标并输入 0-999999 的 Display TID", level="WARNING")
            return
        item.setData(Qt.ItemDataRole.UserRole, value)
        item.setText(self._target_item_text(value))

    def _delete_selected_target(self) -> None:
        row = self.target_list.currentRow()
        if row >= 0:
            self.target_list.takeItem(row)
            self._refresh_target_count()

    def _clear_targets(self) -> None:
        self.target_list.clear()
        self._refresh_target_count()

    def _normalize_edited_target_item(self, item: QListWidgetItem) -> None:
        value = self._parse_tid(item.text())
        if value is None:
            value = 0
        item.setData(Qt.ItemDataRole.UserRole, value)
        text = self._target_item_text(value)
        if item.text() != text:
            item.setText(text)
        self._refresh_target_count()

    def _target_item_text(self, value: int) -> str:
        return f"{value:06d} ×"

    def _refresh_target_count(self, *_args: object) -> None:
        count = self.target_list.count()
        self.target_count_label.setText(f"{count} 个目标")
        self.target_hint_label.setText("按最早匹配帧数选择" if count else "添加目标后即可开始")
        self.clear_targets_button.setEnabled(count > 0)
        self._sync_run_controls()
        self._mark_config_dirty()

    def _panel_values(self) -> tuple[object, ...]:
        return (self.frame_threshold.value(), self.delay.value(), self.target_display_tids(),
                self._selected_path(self.seed_script_combo), self._selected_path(self.name_script_combo),
                self._selected_path(self.reverse_id_script_combo), self.reverse_lookup_window.value(),
                self.mode_combo.currentIndex(), self.loop_count.value(), self.debug_output_check.isChecked())

    def _mark_config_dirty(self, *_args: object) -> None:
        if self._restoring_state:
            return
        dirty = self._panel_values() != self._saved_panel_values
        self.save_state_label.setText("有未保存修改" if dirty else "已保存")
        self.save_state_label.setProperty("dirty", dirty)
        self.save_state_label.style().unpolish(self.save_state_label)
        self.save_state_label.style().polish(self.save_state_label)

    def _parse_tid(self, text: str) -> int | None:
        try:
            match = re.fullmatch(r"[0-9]{1,6}", str(text).removesuffix(" ×").strip())
            if match is None:
                return None
            return self._validate_display_tid_value(int(match.group(0), 10))
        except Exception:
            return None

    def _parse_tid_list(self, text: str) -> list[int]:
        values: list[int] = []
        seen: set[int] = set()
        for token in re.split(r"[\s,，;；|]+", str(text).strip()):
            if not token:
                continue
            value = self._parse_tid(token)
            if value is None:
                continue
            if value in seen:
                continue
            seen.add(value)
            values.append(value)
        return values

    def _validate_tid_value(self, value: int) -> int:
        return self._validate_display_tid_value(value)

    def _validate_display_tid_value(self, value: int) -> int:
        value = int(value)
        if not 0 <= value <= 999_999:
            raise ValueError("Display TID 必须在 0-999999 范围内")
        return value

    def _refresh_ocr_region_text(self) -> None:
        if self._ocr_region is None:
            self.ocr_region_label.setText("TID ROI：未设置")
            return
        self.ocr_region_label.setText(
            f"TID ROI：X={self._ocr_region.x}, Y={self._ocr_region.y}, "
            f"W={self._ocr_region.width}, H={self._ocr_region.height}"
        )

    def _save_panel_state(self) -> None:
        s = self._settings
        s.setValue("mode_index", self.mode_combo.currentIndex())
        s.setValue("loop_count", self.loop_count.value())
        s.setValue("frame_threshold", self.frame_threshold.value())
        s.setValue("delay", self.delay.value())
        s.setValue("reverse_lookup_window", self.reverse_lookup_window.value())
        s.setValue("target_tids", json.dumps(list(self.target_display_tids()), separators=(",", ":")))
        for key, combo in (
            ("seed_script", self.seed_script_combo),
            ("name_script", self.name_script_combo),
            ("reverse_id_script", self.reverse_id_script_combo),
        ):
            path = self._selected_path(combo)
            if path is None:
                s.remove(key)
            else:
                s.setValue(key, str(path))
        s.setValue("debug_output", self.debug_output_check.isChecked())
        s.sync()
        self._saved_panel_values = self._panel_values()
        self._mark_config_dirty()

    def _restore_panel_state(self) -> None:
        s = self._settings
        if s.contains("mode_index"):
            idx = int(s.value("mode_index", 0))
            if 0 <= idx < self.mode_combo.count():
                self.mode_combo.setCurrentIndex(idx)
        if s.contains("loop_count"):
            self.loop_count.setValue(int(s.value("loop_count", 1)))
        if s.contains("frame_threshold"):
            self.frame_threshold.setValue(int(s.value("frame_threshold", 300)))
        if s.contains("delay"):
            self.delay.setValue(int(s.value("delay", 0)))
        if s.contains("reverse_lookup_window"):
            self.reverse_lookup_window.setValue(int(s.value("reverse_lookup_window", 50)))
        if s.contains("target_tids"):
            try:
                values = json.loads(str(s.value("target_tids", "[]")))
            except json.JSONDecodeError:
                values = []
            self.target_list.clear()
            for value in values:
                try:
                    self.add_target_tid(int(value))
                except ValueError:
                    pass
        for key, combo in (
            ("seed_script", self.seed_script_combo),
            ("name_script", self.name_script_combo),
            ("reverse_id_script", self.reverse_id_script_combo),
        ):
            if not s.contains(key):
                continue
            saved_path = str(s.value(key, ""))
            selected_path = self._select_script_by_path(combo, saved_path)
            if selected_path is not None and str(selected_path) != saved_path:
                s.setValue(key, str(selected_path))
        if s.contains("debug_output"):
            self.debug_output_check.setChecked(s.value("debug_output") == "true")
        self._refresh_target_count()

    def _spin(self, minimum: int, maximum: int, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        spin.setFixedHeight(34)
        set_c_locale(spin)
        return spin
