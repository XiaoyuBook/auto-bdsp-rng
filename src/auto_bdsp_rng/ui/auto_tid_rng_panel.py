from __future__ import annotations

import json
import re
import csv
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, QThread, Qt, Signal, Slot
from PySide6.QtGui import QFont, QGuiApplication
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
    QMenu,
    QPlainTextEdit,
    QPushButton,
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
from auto_bdsp_rng.gen8_id import IDState8
from auto_bdsp_rng.resources import resource_path
from auto_bdsp_rng.ui.tid_ocr_dialog import load_tid_ocr_region


SCRIPT_DIR = resource_path("script")
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

    @Slot()
    def stop(self) -> None:
        stop = getattr(self.runner, "stop", None)
        if callable(stop):
            stop()


class AutoTidRngPanel(QWidget):
    startRequested = Signal(object)
    stopRequested = Signal()
    progressChanged = Signal(object)
    ocrSettingsRequested = Signal()

    def __init__(self, parent: QWidget | None = None, script_dir: Path = SCRIPT_DIR) -> None:
        super().__init__(parent)
        self.script_dir = script_dir
        self._scripts: list[Path] = []
        self._runner_thread: QThread | None = None
        self._runner_worker: AutoTidRngWorker | None = None
        self._settings = QSettings("auto-bdsp-rng", "AutoTidRngPanel")
        self._ocr_region = load_tid_ocr_region()
        self._build_ui()
        self.refresh_scripts()
        self._restore_panel_state()
        self._refresh_ocr_region_text()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(self._build_toolbar())

        content = QWidget(self)
        grid = QGridLayout(content)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        grid.addWidget(self._build_config_group(), 0, 0)
        grid.addWidget(self._build_runtime_group(), 0, 1)
        self._legacy_log_group = self._build_log_group()
        self._legacy_log_group.setVisible(False)
        grid.addWidget(self._build_id_table_group(), 1, 0, 1, 2)
        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(1, 1)
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
        self.start_button.clicked.connect(self._start_clicked)
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
        row.addStretch(1)
        row.addWidget(self.status_badge)
        row.addWidget(self.start_button)
        row.addWidget(self.stop_button)
        row.addWidget(self.ocr_button)
        return toolbar

    def _build_config_group(self) -> QGroupBox:
        group = QGroupBox("基础参数")
        group.setMinimumWidth(380)
        group.setMaximumWidth(420)
        layout = QVBoxLayout(group)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setVerticalSpacing(8)
        self.frame_threshold = self._spin(0, 1_000_000_000, 300)
        self.delay = self._spin(0, 1_000_000_000, 0)
        self.reverse_lookup_window = self._spin(0, 10_000, 50)
        self.reverse_lookup_window.setPrefix("±")
        self.reverse_lookup_window.setSuffix(" 帧")
        self.reverse_lookup_window.setVisible(False)
        for spin in (self.frame_threshold, self.delay, self.reverse_lookup_window):
            spin.setFixedWidth(215)
        form.addRow("帧数阈值", self.frame_threshold)
        form.addRow("delay", self.delay)
        layout.addLayout(form)

        target_group = QGroupBox("目标 Display TID 列表")
        target_group.setMaximumHeight(190)
        target_layout = QVBoxLayout(target_group)
        target_layout.setContentsMargins(10, 10, 10, 10)
        target_layout.setSpacing(6)
        self.target_list = QListWidget()
        self.target_list.setMaximumHeight(110)
        self.target_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.target_list.itemChanged.connect(self._normalize_edited_target_item)
        target_layout.addWidget(self.target_list, 1)
        edit_row = QHBoxLayout()
        edit_row.setSpacing(6)
        self.target_input = QLineEdit()
        self.target_input.setPlaceholderText("000000-999999")
        self.target_input.setFixedHeight(34)
        self.add_target_button = QPushButton("添加")
        self.add_target_button.clicked.connect(self._add_target_from_input)
        self.update_target_button = QPushButton("更新")
        self.update_target_button.clicked.connect(self._update_selected_target)
        self.delete_target_button = QPushButton("删除")
        self.delete_target_button.clicked.connect(self._delete_selected_target)
        for button in (self.add_target_button, self.update_target_button, self.delete_target_button):
            button.setFixedHeight(34)
            button.setFixedWidth(58)
            edit_row.addWidget(button)
        edit_row.insertWidget(0, self.target_input, 1)
        target_layout.addLayout(edit_row)
        layout.addWidget(target_group, 1)
        return group

    def _build_runtime_group(self) -> QGroupBox:
        group = QGroupBox("脚本与结果")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        script_grid = QGridLayout()
        script_grid.setHorizontalSpacing(8)
        script_grid.setVerticalSpacing(8)
        self.seed_script_combo = QComboBox()
        self.name_script_combo = QComboBox()
        self.reverse_id_script_combo = QComboBox()
        self.reverse_id_script_combo.setVisible(False)
        for combo in (self.seed_script_combo, self.name_script_combo, self.reverse_id_script_combo):
            combo.setFixedHeight(34)
            combo.setMinimumWidth(220)
        self.refresh_scripts_button = QPushButton("刷新脚本列表")
        self.refresh_scripts_button.clicked.connect(self.refresh_scripts)
        self.refresh_scripts_button.setFixedHeight(34)
        script_grid.addWidget(QLabel("测种脚本"), 0, 0)
        script_grid.addWidget(self.seed_script_combo, 0, 1)
        script_grid.addWidget(QLabel("取名脚本"), 1, 0)
        script_grid.addWidget(self.name_script_combo, 1, 1)
        script_grid.addWidget(self.refresh_scripts_button, 2, 1)
        layout.addLayout(script_grid)

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
        layout.addStretch(1)
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
        self.copy_button = QPushButton("复制")
        self.copy_button.setFixedHeight(32)
        self.copy_button.setFixedWidth(72)
        self.copy_button.clicked.connect(self.copy_results)
        self.export_button = QPushButton("导出 CSV")
        self.export_button.setFixedHeight(32)
        self.export_button.setFixedWidth(88)
        self.export_button.clicked.connect(self.export_results)
        toolbar.addWidget(self.id_result_count)
        toolbar.addStretch(1)
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
        item = QListWidgetItem(f"{tid:06d}")
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        self.target_list.addItem(item)

    def add_target_tid(self, tid: int) -> None:
        self.add_target_display_tid(tid)

    def target_display_tids(self) -> tuple[int, ...]:
        values: list[int] = []
        for row in range(self.target_list.count()):
            value = self._parse_tid(self.target_list.item(row).text())
            if value is not None:
                values.append(value)
        return tuple(values)

    def target_tids(self) -> tuple[int, ...]:
        return self.target_display_tids()

    def build_config(self) -> AutoTidRngConfig:
        target_display_tids = self.target_display_tids()
        if not target_display_tids:
            raise ValueError("请至少添加一个目标 Display TID")
        return AutoTidRngConfig(
            script_dir=self.script_dir,
            seed_script_path=self._selected_path(self.seed_script_combo),
            name_script_path=self._selected_path(self.name_script_combo),
            reverse_id_script_path=self._selected_path(self.reverse_id_script_combo),
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
            self.add_log("自动 TID 乱数已在运行")
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
            self.add_log(progress.log_message)

    def add_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        lines = str(message).splitlines() or [""]
        stamped = [line if _TIMESTAMP_RE.match(line) else f"[{timestamp}] {line}" for line in lines]
        self.log_view.appendPlainText("\n".join(stamped))

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
            self.apply_progress(progress)
        self._clear_runner_thread()

    def _runner_failed(self, message: str) -> None:
        self.status_badge.setText("状态：失败")
        self.add_log(message)
        self._clear_runner_thread()

    def _clear_runner_thread(self) -> None:
        self._runner_thread = None
        self._runner_worker = None
        self.start_button.setEnabled(True)

    def _start_clicked(self) -> None:
        self._save_panel_state()
        try:
            config = self.build_config()
            self._validate_config(config)
        except Exception as exc:
            self.status_badge.setText("状态：配置错误")
            self.add_log(str(exc))
            return
        self.startRequested.emit(config)

    def _stop_clicked(self) -> None:
        if self._runner_worker is not None:
            self._runner_worker.stop()
        self.stopRequested.emit()

    def _validate_config(self, config: AutoTidRngConfig) -> None:
        if config.seed_script_path is None:
            raise ValueError("请选择测种脚本")
        if config.name_script_path is None:
            raise ValueError("请选择取名脚本")
        for path in (config.seed_script_path, config.name_script_path):
            path.read_text(encoding="utf-8")

    def _selected_path(self, combo: QComboBox) -> Path | None:
        value = combo.currentData()
        return Path(value) if value else None

    def _select_script(self, combo: QComboBox, path: Path | None) -> None:
        if path is None:
            return
        self._select_script_by_path(combo, str(path))

    def _select_script_by_path(self, combo: QComboBox, path_str: str) -> None:
        index = combo.findData(path_str)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _choose_script_by_keywords(self, keywords: tuple[str, ...]) -> Path | None:
        for path in self._scripts:
            lowered = path.name.casefold()
            if any(keyword.casefold().replace(" ", "") in lowered.replace(" ", "") for keyword in keywords):
                return path
        return None

    def _add_target_from_input(self) -> None:
        value = self._parse_tid(self.target_input.text())
        if value is None:
            self.add_log("目标 Display TID 必须是 0-999999 的数字")
            return
        self.add_target_display_tid(value)
        self.target_input.clear()

    def _update_selected_target(self) -> None:
        item = self.target_list.currentItem()
        value = self._parse_tid(self.target_input.text())
        if item is None or value is None:
            self.add_log("请选择目标并输入 0-999999 的 Display TID")
            return
        item.setText(f"{value:06d}")

    def _delete_selected_target(self) -> None:
        row = self.target_list.currentRow()
        if row >= 0:
            self.target_list.takeItem(row)

    def _normalize_edited_target_item(self, item: QListWidgetItem) -> None:
        value = self._parse_tid(item.text())
        if value is None:
            item.setText("000000")
            return
        item.setText(f"{value:06d}")

    def _parse_tid(self, text: str) -> int | None:
        try:
            return self._validate_display_tid_value(int(str(text).strip(), 10))
        except Exception:
            return None

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
        if s.contains("seed_script"):
            self._select_script_by_path(self.seed_script_combo, str(s.value("seed_script", "")))
        if s.contains("name_script"):
            self._select_script_by_path(self.name_script_combo, str(s.value("name_script", "")))
        if s.contains("reverse_id_script"):
            self._select_script_by_path(self.reverse_id_script_combo, str(s.value("reverse_id_script", "")))
        if s.contains("debug_output"):
            self.debug_output_check.setChecked(s.value("debug_output") == "true")

    def _spin(self, minimum: int, maximum: int, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        spin.setFixedHeight(34)
        return spin
