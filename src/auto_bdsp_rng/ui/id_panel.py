from __future__ import annotations

import csv
import re
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from auto_bdsp_rng.gen8_id import IDFilter, IDState8, generate_ids
from auto_bdsp_rng.rng_core import SeedPair64
from auto_bdsp_rng.ui.main_window import PokeFinderTableWidget


ID_HEADERS = ("Adv", "TID", "SID", "TSV", "Display TID")


class IdPanel(QWidget):
    seedChanged = Signal(object)

    def __init__(self, parent: QWidget | None = None, *, status_callback: Callable[[str], None] | None = None) -> None:
        super().__init__(parent)
        self._states: list[IDState8] = []
        self._status_callback = status_callback
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        seed_group = QGroupBox("Seed")
        seed_grid = QGridLayout(seed_group)
        seed_grid.setContentsMargins(12, 10, 12, 10)
        seed_grid.setHorizontalSpacing(8)
        seed_grid.setVerticalSpacing(8)
        self.seed_inputs = [QLineEdit() for _ in range(2)]
        for box in self.seed_inputs:
            box.setMaxLength(16)
            box.setFixedHeight(30)
            box.editingFinished.connect(self._emit_seed_changed)
        seed_grid.addWidget(QLabel("Seed 0"), 0, 0)
        seed_grid.addWidget(self.seed_inputs[0], 0, 1)
        seed_grid.addWidget(QLabel("Seed 1"), 1, 0)
        seed_grid.addWidget(self.seed_inputs[1], 1, 1)
        layout.addWidget(seed_group)

        params_group = QGroupBox("参数")
        params_grid = QGridLayout(params_group)
        params_grid.setContentsMargins(12, 10, 12, 10)
        params_grid.setHorizontalSpacing(8)
        params_grid.setVerticalSpacing(8)
        self.initial_advances = self._spin(0, 10_000_000, 0)
        self.max_advances = self._spin(0, 1_000_000_000, 100_000)
        self.tid_filter = QLineEdit()
        self.sid_filter = QLineEdit()
        self.tsv_filter = QLineEdit()
        self.display_tid_filter = QLineEdit()
        fields = (
            ("初始帧", self.initial_advances),
            ("最大帧数", self.max_advances),
            ("TID过滤", self.tid_filter),
            ("SID过滤", self.sid_filter),
            ("TSV过滤", self.tsv_filter),
            ("DisplayTID过滤", self.display_tid_filter),
        )
        for row, (label, widget) in enumerate(fields):
            widget.setFixedHeight(30)
            params_grid.addWidget(QLabel(label), row // 2, (row % 2) * 2)
            params_grid.addWidget(widget, row // 2, (row % 2) * 2 + 1)
        layout.addWidget(params_group)

        toolbar = QHBoxLayout()
        self.result_count = QLabel("0 条结果")
        self.generate_button = QPushButton("生成")
        self.generate_button.setObjectName("PrimaryButton")
        self.generate_button.setFixedHeight(32)
        self.generate_button.setFixedWidth(80)
        self.generate_button.clicked.connect(self.generate_results)
        self.copy_button = QPushButton("复制")
        self.copy_button.setFixedHeight(32)
        self.copy_button.setFixedWidth(72)
        self.copy_button.clicked.connect(self.copy_results)
        self.export_button = QPushButton("导出 CSV")
        self.export_button.setFixedHeight(32)
        self.export_button.setFixedWidth(88)
        self.export_button.clicked.connect(self.export_results)
        toolbar.addWidget(self.result_count)
        toolbar.addStretch(1)
        toolbar.addWidget(self.generate_button)
        toolbar.addWidget(self.copy_button)
        toolbar.addWidget(self.export_button)
        layout.addLayout(toolbar)

        self.table = PokeFinderTableWidget()
        self.table.setColumnCount(len(ID_HEADERS))
        self.table.setHorizontalHeaderLabels(ID_HEADERS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)

    def _spin(self, minimum: int, maximum: int, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        return spin

    def set_seed_pair(self, seed: SeedPair64) -> None:
        for box, text in zip(self.seed_inputs, seed.format_seeds()):
            box.blockSignals(True)
            box.setText(text)
            box.blockSignals(False)

    def _seed_pair(self) -> SeedPair64:
        return SeedPair64.from_hex_words([box.text() for box in self.seed_inputs])

    def _emit_seed_changed(self) -> None:
        try:
            seed = self._seed_pair()
        except ValueError as exc:
            self._set_status(str(exc))
            return
        self.seedChanged.emit(seed)

    def _current_filter(self) -> IDFilter:
        return IDFilter(
            tid=self._parse_filter_values(self.tid_filter.text()),
            sid=self._parse_filter_values(self.sid_filter.text()),
            tsv=self._parse_filter_values(self.tsv_filter.text()),
            display_tid=self._parse_filter_values(self.display_tid_filter.text()),
        )

    def _parse_filter_values(self, text: str) -> list[int] | None:
        tokens = [token for token in re.split(r"[\s,;]+", text.strip()) if token]
        if not tokens:
            return None
        values: list[int] = []
        for token in tokens:
            values.append(int(token, 0))
        return values

    def generate_results(self) -> None:
        try:
            seed = self._seed_pair()
            states = generate_ids(
                seed,
                initial_advances=self.initial_advances.value(),
                max_advances=self.max_advances.value(),
                state_filter=self._current_filter(),
            )
        except Exception as exc:
            self._set_status(str(exc))
            return
        self._states = states
        self._populate_table(states)
        self._set_status(f"{len(states)} 条结果")

    def _populate_table(self, states: list[IDState8]) -> None:
        self.table.setRowCount(len(states))
        for row, state in enumerate(states):
            for column, value in enumerate(self._state_row(state)):
                self.table.setItem(row, column, QTableWidgetItem(value))
        self.result_count.setText(f"{len(states)} 条结果")

    def _state_row(self, state: IDState8) -> list[str]:
        return [
            str(state.advances),
            str(state.tid),
            str(state.sid),
            str(state.tsv),
            f"{state.display_tid:06d}",
        ]

    def _table_text(self) -> str:
        rows = ["\t".join(ID_HEADERS)]
        rows.extend("\t".join(self._state_row(state)) for state in self._states)
        return "\n".join(rows)

    def _show_context_menu(self, position: QPoint) -> None:
        menu = QMenu(self.table)
        copy_action = menu.addAction("复制")
        csv_action = menu.addAction("导出 CSV")
        selected = menu.exec(self.table.viewport().mapToGlobal(position))
        if selected == copy_action:
            self.copy_results()
        elif selected == csv_action:
            self.export_results()

    def copy_results(self) -> None:
        if not self._states:
            self._set_status("No results to copy")
            return
        QGuiApplication.clipboard().setText(self._table_text())
        self._set_status(f"Copied {len(self._states)} result(s)")

    def export_results(self) -> None:
        if not self._states:
            self._set_status("No results to export")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export ID results", "gen8_id_results.csv", "CSV files (*.csv)")
        if not path:
            return
        output = Path(path)
        with output.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(ID_HEADERS)
            for state in self._states:
                writer.writerow(self._state_row(state))
        self._set_status(f"Exported {output}")

    def _set_status(self, text: str) -> None:
        if self._status_callback is not None:
            self._status_callback(text)
