from __future__ import annotations

import json
import re
import csv
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QSize, QSettings, QThread, Qt, Signal, Slot
from PySide6.QtGui import QAction, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
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
    QListView,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QSpinBox,
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
from auto_bdsp_rng.ui.numeric_locale import set_c_locale
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

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        item = self.itemAt(event.position().toPoint())
        if item is not None:
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


class AutoTidRngPanel(QWidget):
    startRequested = Signal(object)
    stopRequested = Signal()
    progressChanged = Signal(object)
    ocrSettingsRequested = Signal()
    runLogRequested = Signal()
    runStateChanged = Signal(bool)

    def __init__(
        self,
        parent: QWidget | None = None,
        script_dir: Path = SCRIPT_DIR,
        run_log_sink: Callable[[str, str], None] | None = None,
        settings: QSettings | None = None,
    ) -> None:
        super().__init__(parent)
        self.script_dir = script_dir
        self._run_log_sink = run_log_sink
        self._scripts: list[Path] = []
        self._runner_thread: QThread | None = None
        self._runner_worker: AutoTidRngWorker | None = None
        self._run_state_active = False
        self._settings = settings or QSettings("auto-bdsp-rng", "AutoTidRngPanel")
        self._ocr_region = load_tid_ocr_region()
        self._build_ui()
        self.refresh_scripts()
        self._restore_panel_state()
        self._refresh_ocr_region_text()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self._build_toolbar())

        content = QWidget(self)
        content.setObjectName("AutoTidContent")
        grid = QGridLayout(content)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        grid.addWidget(self._build_top_controls_group(), 0, 0, 1, 2)
        self._legacy_log_group = self._build_log_group()
        self._legacy_log_group.setVisible(False)
        grid.addWidget(self._build_target_group(), 1, 0, 1, 2)
        grid.addWidget(self._build_id_table_group(), 2, 0, 1, 2)
        grid.setColumnMinimumWidth(0, 360)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 2)
        grid.setRowStretch(2, 1)

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
        self.status_badge.setObjectName("Badge")
        self.status_badge.setFixedHeight(34)
        self.start_button = QToolButton()
        self.start_button.setText("开始")
        self.start_button.setObjectName("PrimaryButton")
        self.start_button.setFixedHeight(34)
        self.start_button.setMinimumWidth(88)
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
        self.stop_button.setObjectName("DangerButton")
        self.stop_button.setFixedHeight(34)
        self.stop_button.setMinimumWidth(80)
        self.stop_button.clicked.connect(self._stop_clicked)
        self.ocr_button = QPushButton("OCR 设置")
        self.ocr_button.setObjectName("SecondaryButton")
        self.ocr_button.setFixedHeight(34)
        self.ocr_button.setMinimumWidth(120)
        self.ocr_button.clicked.connect(self.ocrSettingsRequested.emit)
        self.ocr_button.setVisible(False)

        row.addWidget(QLabel("自动 TID：按 Display TID 命中后取名"))
        row.addSpacing(8)
        row.addWidget(QLabel("最近："))
        self.latest_log_label = QLabel("暂无消息")
        self.latest_log_label.setObjectName("LatestLogLabel")
        self.latest_log_label.setMaximumHeight(34)
        self.latest_log_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.latest_log_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(self.latest_log_label, 1)
        self.view_log_button = QPushButton("查看日志")
        self.view_log_button.setObjectName("SecondaryButton")
        self.view_log_button.setFixedHeight(34)
        self.view_log_button.setMinimumWidth(88)
        self.view_log_button.clicked.connect(self.runLogRequested.emit)
        row.addWidget(self.view_log_button)
        row.addWidget(self.status_badge)
        row.addWidget(self.start_button)
        row.addWidget(self.stop_button)
        row.addWidget(self.ocr_button)
        return toolbar

    def _build_config_group(self) -> QGroupBox:
        group = QGroupBox("基础参数")
        group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(group)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        self.frame_threshold = self._spin(0, 1_000_000_000, 300)
        self.delay = self._spin(0, 1_000_000_000, 0)
        self.reverse_lookup_window = self._spin(0, 10_000, 50)
        self.reverse_lookup_window.setPrefix("±")
        self.reverse_lookup_window.setSuffix(" 帧")
        self.reverse_lookup_window.setVisible(False)
        for spin in (self.frame_threshold, self.delay, self.reverse_lookup_window):
            spin.setMinimumWidth(150)
        form.addWidget(QLabel("帧数阈值"), 0, 0)
        form.addWidget(self.frame_threshold, 0, 1)
        form.addWidget(QLabel("delay"), 0, 2)
        form.addWidget(self.delay, 0, 3)
        form.setColumnStretch(1, 1)
        form.setColumnStretch(3, 1)
        layout.addLayout(form)
        return group

    def _build_top_controls_group(self) -> QGroupBox:
        group = QGroupBox("基础参数与脚本")
        group.setObjectName("AutoTidTopControls")
        group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        layout = QGridLayout(group)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(8)

        self.frame_threshold = self._spin(0, 1_000_000_000, 300)
        self.delay = self._spin(0, 1_000_000_000, 0)
        self.reverse_lookup_window = self._spin(0, 10_000, 50)
        self.reverse_lookup_window.setPrefix("±")
        self.reverse_lookup_window.setSuffix(" 帧")
        self.reverse_lookup_window.setVisible(False)
        self.frame_threshold.setFixedWidth(130)
        self.delay.setFixedWidth(110)
        self.reverse_lookup_window.setFixedWidth(110)

        self.seed_script_combo = QComboBox()
        self.name_script_combo = QComboBox()
        self.reverse_id_script_combo = QComboBox()
        self.reverse_id_script_combo.setVisible(False)
        for combo, width in (
            (self.seed_script_combo, 220),
            (self.name_script_combo, 220),
            (self.reverse_id_script_combo, 220),
        ):
            combo.setFixedHeight(34)
            combo.setFixedWidth(width)
        self.refresh_scripts_button = QPushButton("刷新脚本列表")
        self.refresh_scripts_button.clicked.connect(self.refresh_scripts)
        self.refresh_scripts_button.setFixedHeight(34)
        self.refresh_scripts_button.setFixedWidth(116)

        layout.addWidget(QLabel("帧数阈值"), 0, 0)
        layout.addWidget(self.frame_threshold, 0, 1)
        layout.addWidget(QLabel("delay"), 0, 2)
        layout.addWidget(self.delay, 0, 3)
        layout.addWidget(QLabel("测种脚本"), 0, 4)
        layout.addWidget(self.seed_script_combo, 0, 5)
        layout.addWidget(QLabel("取名脚本"), 0, 6)
        layout.addWidget(self.name_script_combo, 0, 7)
        layout.addWidget(self.refresh_scripts_button, 0, 8)
        layout.setColumnStretch(9, 1)

        self.ocr_region_label = QLabel("TID ROI：未设置")
        self.ocr_region_label.setVisible(False)
        self.ocr_region_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        layout.addWidget(self.ocr_region_label, 1, 0, 1, 9)

        result_group = QGroupBox("校准结果")
        result_group.setVisible(False)
        result_form = QFormLayout(result_group)
        result_form.setVerticalSpacing(8)
        self.target_result = QLabel("-")
        self.trigger_result = QLabel("-")
        self.ocr_result = QLabel("-")
        self.actual_delay_result = QLabel("-")
        for label in (self.target_result, self.trigger_result, self.ocr_result, self.actual_delay_result):
            label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
            )
        result_form.addRow("命中目标", self.target_result)
        result_form.addRow("反查启动帧", self.trigger_result)
        result_form.addRow("OCR TID", self.ocr_result)
        result_form.addRow("实际 delay", self.actual_delay_result)
        layout.addWidget(result_group, 2, 0, 1, 9)
        return group

    def _build_target_group(self) -> QGroupBox:
        group = QGroupBox("目标 Display TID")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        target_panel = QWidget()
        target_panel.setObjectName("InlinePanel")
        target_layout = QGridLayout(target_panel)
        target_layout.setContentsMargins(12, 10, 12, 12)
        target_layout.setHorizontalSpacing(12)
        target_layout.setVerticalSpacing(8)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        self.target_count_label = QLabel("0 个目标")
        self.target_count_label.setObjectName("MutedLabel")
        title_row.addStretch(1)
        title_row.addWidget(self.target_count_label)
        target_layout.addLayout(title_row, 0, 0, 1, 2)
        self.target_list = _TargetListWidget()
        self.target_list.setObjectName("TargetPool")
        self.target_list.setViewMode(QListView.ViewMode.IconMode)
        self.target_list.setFlow(QListView.Flow.LeftToRight)
        self.target_list.setWrapping(True)
        self.target_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.target_list.setMovement(QListView.Movement.Static)
        self.target_list.setSpacing(6)
        self.target_list.setGridSize(QSize(92, 32))
        self.target_list.setUniformItemSizes(True)
        self.target_list.setMinimumHeight(116)
        self.target_list.setMaximumHeight(140)
        self.target_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.target_list.itemChanged.connect(self._normalize_edited_target_item)
        self.target_list.targetRemoved.connect(self._refresh_target_count)
        target_layout.addWidget(self.target_list, 1, 0, 2, 1)

        action_panel = QWidget()
        action_panel.setObjectName("TargetPoolActions")
        action_panel.setFixedWidth(360)
        action_panel.setMinimumHeight(116)
        action_panel.setMaximumHeight(140)
        action_layout = QVBoxLayout(action_panel)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(8)
        self.target_input = QLineEdit()
        self.target_input.setPlaceholderText("000000-999999，可粘贴多个")
        self.target_input.setFixedHeight(34)
        self.target_input.setMaximumWidth(360)
        self.add_target_button = QPushButton("添加")
        self.add_target_button.clicked.connect(self._add_target_from_input)
        self.update_target_button = QPushButton("更新")
        self.update_target_button.clicked.connect(self._update_selected_target)
        self.update_target_button.setVisible(False)
        self.delete_target_button = QPushButton("删除")
        self.delete_target_button.clicked.connect(self._delete_selected_target)
        self.delete_target_button.setVisible(False)
        self.clear_targets_button = QPushButton("清空")
        self.clear_targets_button.clicked.connect(self._clear_targets)
        for button in (self.add_target_button, self.update_target_button, self.delete_target_button, self.clear_targets_button):
            button.setFixedHeight(34)
        action_layout.addWidget(self.target_input)
        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        self.add_target_button.setFixedWidth(176)
        self.clear_targets_button.setFixedWidth(176)
        button_row.addWidget(self.add_target_button)
        button_row.addWidget(self.clear_targets_button)
        action_layout.addLayout(button_row)
        action_layout.addWidget(self.update_target_button)
        action_layout.addWidget(self.delete_target_button)
        action_layout.addStretch(1)
        target_layout.addWidget(action_panel, 1, 1, 2, 1, Qt.AlignmentFlag.AlignTop)
        target_layout.setColumnStretch(0, 1)
        layout.addWidget(target_panel, 1)
        return group

    def _build_runtime_group(self) -> QGroupBox:
        group = QGroupBox("脚本")
        group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(group)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        script_grid = QFormLayout()
        script_grid.setHorizontalSpacing(10)
        script_grid.setVerticalSpacing(10)
        script_grid.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.seed_script_combo = QComboBox()
        self.name_script_combo = QComboBox()
        self.reverse_id_script_combo = QComboBox()
        self.reverse_id_script_combo.setVisible(False)
        for combo in (self.seed_script_combo, self.name_script_combo, self.reverse_id_script_combo):
            combo.setFixedHeight(34)
            combo.setMinimumWidth(260)
        self.refresh_scripts_button = QPushButton("刷新脚本列表")
        self.refresh_scripts_button.clicked.connect(self.refresh_scripts)
        self.refresh_scripts_button.setFixedHeight(34)
        script_grid.addRow("测种脚本", self.seed_script_combo)
        script_grid.addRow("取名脚本", self.name_script_combo)
        layout.addLayout(script_grid)
        layout.addWidget(self.refresh_scripts_button, 0, Qt.AlignmentFlag.AlignRight)

        self.ocr_region_label = QLabel("TID ROI：未设置")
        self.ocr_region_label.setVisible(False)
        self.ocr_region_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        layout.addWidget(self.ocr_region_label)

        result_group = QGroupBox("校准结果")
        result_group.setVisible(False)
        result_form = QFormLayout(result_group)
        result_form.setVerticalSpacing(8)
        self.target_result = QLabel("-")
        self.trigger_result = QLabel("-")
        self.ocr_result = QLabel("-")
        self.actual_delay_result = QLabel("-")
        for label in (self.target_result, self.trigger_result, self.ocr_result, self.actual_delay_result):
            label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
            )
        result_form.addRow("命中目标", self.target_result)
        result_form.addRow("反查启动帧", self.trigger_result)
        result_form.addRow("OCR TID", self.ocr_result)
        result_form.addRow("实际 delay", self.actual_delay_result)
        layout.addWidget(result_group)
        return group

    def _build_log_group(self) -> QGroupBox:
        group = QGroupBox("日志")
        layout = QVBoxLayout(group)
        self.log_view = _CopyableLog()
        self.log_view.setObjectName("LogView")
        self.log_view.setFont(QFont("Consolas", 10))
        layout.addWidget(self.log_view)
        self.log_view.setVisible(False)
        return group

    def _build_id_table_group(self) -> QGroupBox:
        group = QGroupBox("ID 数据表")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        toolbar = QHBoxLayout()
        self.id_result_count = QLabel("0 条结果")
        seed_bar = QHBoxLayout()
        seed_bar.setSpacing(6)
        self.tid_seed_inputs: list[QLineEdit] = []
        for label_text in ("Seed0", "Seed1"):
            label = QLabel(label_text)
            seed_box = QLineEdit()
            seed_box.setReadOnly(True)
            seed_box.setFixedHeight(32)
            seed_box.setMinimumWidth(230)
            seed_box.setPlaceholderText("未捕获")
            self.tid_seed_inputs.append(seed_box)
            seed_bar.addWidget(label)
            seed_bar.addWidget(seed_box, 1)
        self.copy_button = QPushButton("复制")
        self.copy_button.setFixedHeight(32)
        self.copy_button.setFixedWidth(72)
        self.copy_button.clicked.connect(self.copy_results)
        self.export_button = QPushButton("导出 CSV")
        self.export_button.setFixedHeight(32)
        self.export_button.setFixedWidth(88)
        self.export_button.clicked.connect(self.export_results)
        toolbar.addWidget(self.id_result_count)
        toolbar.addSpacing(18)
        toolbar.addLayout(seed_bar, 1)
        toolbar.addSpacing(18)
        toolbar.addWidget(self.copy_button)
        toolbar.addWidget(self.export_button)
        layout.addLayout(toolbar)

        self._id_states: list[IDState8] = []
        self.id_table = _IdResultTable()
        self.id_table.setColumnCount(5)
        self.id_table.setHorizontalHeaderLabels(("Adv", "TID", "SID", "TSV", "Display TID"))
        self.id_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.id_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.id_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.id_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.id_table.customContextMenuRequested.connect(self._show_table_context_menu)
        self.id_table.searchStatusChanged.connect(self.status_badge.setText)
        self.id_table.verticalHeader().setVisible(False)
        self.id_table.horizontalHeader().setStretchLastSection(True)
        self.id_table.setMinimumHeight(360)
        layout.addWidget(self.id_table, 1)
        return group

    def refresh_scripts(self) -> None:
        self._scripts = list_auto_scripts(self.script_dir)
        for combo in (self.seed_script_combo, self.name_script_combo, self.reverse_id_script_combo):
            current = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("请选择", None)
            for path in self._scripts:
                combo.addItem(path.name, str(path))
            combo.blockSignals(False)
            if current:
                self._select_script_by_path(combo, str(current))
        self._select_script(self.seed_script_combo, choose_default_script(self._scripts, DEFAULT_SEED_SCRIPT_NAME))
        self._select_script(self.name_script_combo, self._choose_script_by_keywords(("取名", "name")))
        self._select_script(self.reverse_id_script_combo, self._choose_script_by_keywords(("反查ID", "反查 ID", "id")))

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

    def set_tid_seed(self, seed: SeedPair64 | SeedState32) -> None:
        seed_pair = seed.to_seed_pair64() if isinstance(seed, SeedState32) else seed
        for box, text in zip(self.tid_seed_inputs, seed_pair.format_seeds()):
            box.setText(text)
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
        thread = QThread(self)
        worker = AutoTidRngWorker(runner)
        worker.moveToThread(thread)
        worker.progressChanged.connect(self.apply_progress)
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
        self._run_state_active = True
        self.runStateChanged.emit(True)
        thread.start()

    def apply_progress(self, progress: AutoTidRngProgress) -> None:
        phase_text = progress.phase.value if hasattr(progress.phase, "value") else str(progress.phase)
        self.status_badge.setText(f"状态：{phase_text}")
        self.progressChanged.emit(progress)
        if progress.id_states:
            self.set_id_states(list(progress.id_states))
        if progress.target_tid is not None and progress.target_advances is not None:
            sid_text = "-" if progress.target_sid is None else str(progress.target_sid)
            display = "-" if progress.target_display_tid is None else f"{progress.target_display_tid:06d}"
            self.target_result.setText(
                f"Display TID {display} / TID {progress.target_tid} / SID {sid_text} / Adv {progress.target_advances}"
            )
        if progress.trigger_advances is not None:
            self.trigger_result.setText(str(progress.trigger_advances))
        if progress.ocr_tid is not None:
            self.ocr_result.setText(f"{progress.ocr_tid}（原始：{progress.ocr_text or '-'}）")
        elif progress.ocr_text:
            self.ocr_result.setText(progress.ocr_text)
        if progress.actual_delay is not None:
            self.actual_delay_result.setText(str(progress.actual_delay))
        if progress.log_message:
            level = "ERROR" if progress.phase == AutoTidRngPhase.FAILED else "INFO"
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
        stamped = [line if _TIMESTAMP_RE.match(line) else f"[{timestamp}] {line}" for line in lines]
        self.log_view.appendPlainText("\n".join(stamped))
        latest_line = next((line.strip() for line in reversed(lines) if line.strip()), None)
        if latest_line is not None:
            self.latest_log_label.setText(latest_line)
            self.latest_log_label.setToolTip(text)

    def set_id_states(self, states: list[IDState8]) -> None:
        self._id_states = list(states)
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
            phase_text = progress.phase.value if hasattr(progress.phase, "value") else str(progress.phase)
            self.status_badge.setText(f"状态：{phase_text}")
        self._clear_runner_thread()

    def _runner_failed(self, message: str) -> None:
        self.status_badge.setText("状态：失败")
        self.add_log(message, level="ERROR")
        self._clear_runner_thread()

    def _clear_runner_thread(self) -> None:
        self._runner_thread = None
        self._runner_worker = None
        self.start_button.setEnabled(True)
        if self._run_state_active:
            self._run_state_active = False
            self.runStateChanged.emit(False)

    def _start_clicked(self) -> None:
        self._start_with_phase(AutoTidRngPhase.RUN_SEED_SCRIPT)

    def _start_from_capture_clicked(self) -> None:
        self._start_with_phase(AutoTidRngPhase.CAPTURE_TIDSID)

    def _start_with_phase(self, start_phase: AutoTidRngPhase) -> None:
        self._save_panel_state()
        try:
            config = self.build_config(start_phase=start_phase)
            self._validate_config(config)
        except Exception as exc:
            self.status_badge.setText("状态：配置错误")
            self.add_log(str(exc), level="WARNING")
            return
        self.startRequested.emit(config)

    def _stop_clicked(self) -> None:
        if self._runner_worker is not None:
            request_stop = getattr(self._runner_worker, "request_stop", None)
            if callable(request_stop):
                try:
                    request_stop("用户点击停止按钮")
                except TypeError:
                    request_stop()
            else:
                # Keep simple test/extension worker objects compatible.
                self._runner_worker.stop()
        self.stopRequested.emit()

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

    def _refresh_target_count(self) -> None:
        self.target_count_label.setText(f"{self.target_list.count()} 个目标")

    def _parse_tid(self, text: str) -> int | None:
        try:
            match = re.search(r"\d{1,6}", str(text))
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
