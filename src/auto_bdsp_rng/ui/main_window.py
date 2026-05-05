from __future__ import annotations

import csv
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from auto_bdsp_rng.blink_detection import (
    ProjectXsIntegrationError,
    capture_player_blinks,
    load_project_xs_config,
    recover_seed_from_observation,
)
from auto_bdsp_rng.data import GameVersion, StaticEncounterCategory, StaticEncounterRecord, get_static_encounters
from auto_bdsp_rng.gen8_static import Lead, Profile8, State8, StateFilter, StaticGenerator8
from auto_bdsp_rng.rng_core import SeedPair64, SeedState32


NATURES = (
    "Hardy",
    "Lonely",
    "Brave",
    "Adamant",
    "Naughty",
    "Bold",
    "Docile",
    "Relaxed",
    "Impish",
    "Lax",
    "Timid",
    "Hasty",
    "Serious",
    "Jolly",
    "Naive",
    "Modest",
    "Mild",
    "Quiet",
    "Bashful",
    "Rash",
    "Calm",
    "Gentle",
    "Sassy",
    "Careful",
    "Quirky",
)
IV_LABELS = ("HP", "Atk", "Def", "SpA", "SpD", "Spe")
RESULT_HEADERS = (
    "Adv",
    "EC",
    "PID",
    "Shiny",
    "Nature",
    "Ability",
    "Gender",
    "HP",
    "Atk",
    "Def",
    "SpA",
    "SpD",
    "Spe",
    "Height",
    "Weight",
)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("auto_bdsp_rng")
        self.resize(1320, 820)
        self._records: tuple[StaticEncounterRecord, ...] = ()
        self._states: list[State8] = []
        self._build_actions()
        self._build_ui()
        self._apply_theme()
        self._refresh_encounters()
        self._sync_seed64_from_state32()
        self.statusBar().showMessage("Ready")

    def _build_actions(self) -> None:
        generate = QAction("Generate", self)
        generate.setShortcut("Ctrl+R")
        generate.triggered.connect(self.generate_results)
        self.addAction(generate)

        copy = QAction("Copy Results", self)
        copy.setShortcut("Ctrl+C")
        copy.triggered.connect(self.copy_results)
        self.addAction(copy)

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(14, 12, 14, 12)
        root_layout.setSpacing(10)

        header = QFrame()
        header.setObjectName("Header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 10, 16, 10)
        title = QLabel("BDSP Static RNG Workbench")
        title.setObjectName("WindowTitle")
        self.seed_badge = QLabel("Seed[0-1] linked")
        self.seed_badge.setObjectName("Badge")
        header_layout.addWidget(title)
        header_layout.addStretch(1)
        header_layout.addWidget(self.seed_badge)
        root_layout.addWidget(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_controls())
        splitter.addWidget(self._build_results())
        splitter.setSizes([430, 890])
        root_layout.addWidget(splitter, 1)

        self.setCentralWidget(root)
        self.setStatusBar(QStatusBar())

    def _build_controls(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(10)
        layout.addWidget(self._build_blink_group())
        layout.addWidget(self._build_seed_group())
        layout.addWidget(self._build_static_group())
        layout.addWidget(self._build_profile_group())
        layout.addWidget(self._build_filter_group(), 1)
        return panel

    def _build_blink_group(self) -> QGroupBox:
        group = QGroupBox("Blink detection")
        layout = QGridLayout(group)
        self.config_path = QLineEdit("config_cave.json")
        browse = QPushButton("Browse")
        browse.clicked.connect(self._browse_config)
        self.blink_count = self._spin(1, 999, 40)
        self.npc_count = self._spin(0, 999, 0)
        capture = QPushButton("Capture Seed")
        capture.clicked.connect(self.capture_seed)

        layout.addWidget(QLabel("Config"), 0, 0)
        layout.addWidget(self.config_path, 0, 1)
        layout.addWidget(browse, 0, 2)
        layout.addWidget(QLabel("Blinks"), 1, 0)
        layout.addWidget(self.blink_count, 1, 1)
        layout.addWidget(QLabel("NPC"), 2, 0)
        layout.addWidget(self.npc_count, 2, 1)
        layout.addWidget(capture, 2, 2)
        return group

    def _build_seed_group(self) -> QGroupBox:
        group = QGroupBox("Seed")
        layout = QGridLayout(group)
        self.seed32_inputs = [QLineEdit(text) for text in ("12345678", "9ABCDEF0", "11111111", "22222222")]
        self.seed64_outputs = [QLineEdit() for _ in range(2)]
        for input_box in self.seed32_inputs:
            input_box.setMaxLength(8)
            input_box.editingFinished.connect(self._sync_seed64_from_state32)
        for output in self.seed64_outputs:
            output.setReadOnly(True)
            output.setObjectName("Readonly")

        for index, input_box in enumerate(self.seed32_inputs):
            layout.addWidget(QLabel(f"S{index}"), index // 2, (index % 2) * 2)
            layout.addWidget(input_box, index // 2, (index % 2) * 2 + 1)
        layout.addWidget(QLabel("Seed0"), 2, 0)
        layout.addWidget(self.seed64_outputs[0], 2, 1, 1, 3)
        layout.addWidget(QLabel("Seed1"), 3, 0)
        layout.addWidget(self.seed64_outputs[1], 3, 1, 1, 3)
        return group

    def _build_static_group(self) -> QGroupBox:
        group = QGroupBox("BDSP static target")
        form = QFormLayout(group)
        self.version_combo = QComboBox()
        self.version_combo.addItems([version.value for version in GameVersion])
        self.version_combo.setCurrentText(GameVersion.BDSP.value)
        self.version_combo.currentTextChanged.connect(self._refresh_encounters)
        self.category_combo = QComboBox()
        self.category_combo.addItem("All", None)
        for category in StaticEncounterCategory:
            self.category_combo.addItem(category.value, category.value)
        self.category_combo.currentIndexChanged.connect(self._refresh_encounters)
        self.encounter_combo = QComboBox()
        self.initial_advances = self._spin(0, 10_000_000, 0)
        self.max_advances = self._spin(0, 100_000, 100)
        self.offset = self._spin(0, 1_000_000, 0)
        self.lead_combo = QComboBox()
        self.lead_combo.addItem("None", int(Lead.NONE))
        self.lead_combo.addItem("Synchronize Hardy", int(Lead.SYNCHRONIZE_START))
        self.lead_combo.addItem("Cute Charm F", int(Lead.CUTE_CHARM_F))
        self.lead_combo.addItem("Cute Charm M", int(Lead.CUTE_CHARM_M))
        form.addRow("Version", self.version_combo)
        form.addRow("Category", self.category_combo)
        form.addRow("Encounter", self.encounter_combo)
        form.addRow("Initial advances", self.initial_advances)
        form.addRow("Max advances", self.max_advances)
        form.addRow("Offset", self.offset)
        form.addRow("Lead", self.lead_combo)
        return group

    def _build_profile_group(self) -> QGroupBox:
        group = QGroupBox("Profile")
        form = QFormLayout(group)
        self.profile_name = QLineEdit("-")
        self.tid = self._spin(0, 65535, 12345)
        self.sid = self._spin(0, 65535, 54321)
        self.tsv = QLineEdit()
        self.tsv.setReadOnly(True)
        self.national_dex = QCheckBox("National Dex")
        self.shiny_charm = QCheckBox("Shiny Charm")
        self.oval_charm = QCheckBox("Oval Charm")
        self.tid.valueChanged.connect(self._update_tsv)
        self.sid.valueChanged.connect(self._update_tsv)
        self._update_tsv()

        charms = QWidget()
        charm_layout = QHBoxLayout(charms)
        charm_layout.setContentsMargins(0, 0, 0, 0)
        charm_layout.addWidget(self.national_dex)
        charm_layout.addWidget(self.shiny_charm)
        charm_layout.addWidget(self.oval_charm)
        form.addRow("Name", self.profile_name)
        form.addRow("TID", self.tid)
        form.addRow("SID", self.sid)
        form.addRow("TSV", self.tsv)
        form.addRow("Flags", charms)
        return group

    def _build_filter_group(self) -> QGroupBox:
        group = QGroupBox("Filters")
        layout = QVBoxLayout(group)
        row = QHBoxLayout()
        self.shiny_filter = QComboBox()
        self.shiny_filter.addItem("Any", "any")
        self.shiny_filter.addItem("Shiny", "shiny")
        self.shiny_filter.addItem("Star", "star")
        self.shiny_filter.addItem("Square", "square")
        self.shiny_filter.addItem("Non-shiny", "none")
        self.ability_filter = QComboBox()
        self.ability_filter.addItem("Any ability", 255)
        self.ability_filter.addItem("Ability 0", 0)
        self.ability_filter.addItem("Ability 1", 1)
        self.ability_filter.addItem("Hidden", 2)
        self.gender_filter = QComboBox()
        self.gender_filter.addItem("Any gender", 255)
        self.gender_filter.addItem("Male", 0)
        self.gender_filter.addItem("Female", 1)
        self.gender_filter.addItem("Genderless", 2)
        row.addWidget(self.shiny_filter)
        row.addWidget(self.ability_filter)
        row.addWidget(self.gender_filter)
        layout.addLayout(row)

        self.nature_list = QListWidget()
        self.nature_list.setMaximumHeight(126)
        for nature in NATURES:
            item = QListWidgetItem(nature)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.nature_list.addItem(item)
        nature_buttons = QHBoxLayout()
        all_natures = QPushButton("All natures")
        all_natures.clicked.connect(lambda: self._set_all_natures(Qt.CheckState.Checked))
        clear_natures = QPushButton("Clear")
        clear_natures.clicked.connect(lambda: self._set_all_natures(Qt.CheckState.Unchecked))
        nature_buttons.addWidget(all_natures)
        nature_buttons.addWidget(clear_natures)
        layout.addWidget(self.nature_list)
        layout.addLayout(nature_buttons)

        iv_grid = QGridLayout()
        self.iv_min: list[QSpinBox] = []
        self.iv_max: list[QSpinBox] = []
        for column, label in enumerate(IV_LABELS):
            iv_grid.addWidget(QLabel(label), 0, column)
            min_spin = self._spin(0, 31, 0)
            max_spin = self._spin(0, 31, 31)
            self.iv_min.append(min_spin)
            self.iv_max.append(max_spin)
            iv_grid.addWidget(min_spin, 1, column)
            iv_grid.addWidget(max_spin, 2, column)
        layout.addLayout(iv_grid)
        return group

    def _build_results(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 0, 0, 0)
        layout.setSpacing(10)

        toolbar = QHBoxLayout()
        self.generate_button = QPushButton("Generate")
        self.generate_button.setObjectName("PrimaryButton")
        self.generate_button.clicked.connect(self.generate_results)
        copy_button = QPushButton("Copy")
        copy_button.clicked.connect(self.copy_results)
        export_button = QPushButton("Export CSV")
        export_button.clicked.connect(self.export_results)
        self.result_count = QLabel("0 results")
        self.result_count.setObjectName("ResultCount")
        toolbar.addWidget(self.generate_button)
        toolbar.addWidget(copy_button)
        toolbar.addWidget(export_button)
        toolbar.addStretch(1)
        toolbar.addWidget(self.result_count)
        layout.addLayout(toolbar)

        self.table = QTableWidget(0, len(RESULT_HEADERS))
        self.table.setHorizontalHeaderLabels(RESULT_HEADERS)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        layout.addWidget(self.table, 1)
        return panel

    def _apply_theme(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
                background: #101418;
                color: #E7ECE9;
                font-family: "Segoe UI";
                font-size: 12px;
            }
            QFrame#Header {
                background: #182027;
                border: 1px solid #2D3B3F;
                border-radius: 6px;
            }
            QLabel#WindowTitle {
                font-size: 20px;
                font-weight: 700;
                color: #F4F1E8;
            }
            QLabel#Badge, QLabel#ResultCount {
                color: #91E0C3;
                font-weight: 600;
            }
            QGroupBox {
                border: 1px solid #2B373A;
                border-radius: 6px;
                margin-top: 9px;
                padding: 10px 8px 8px 8px;
                background: #141B20;
                font-weight: 700;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 9px;
                padding: 0 4px;
                color: #D7C17C;
            }
            QLineEdit, QSpinBox, QComboBox, QListWidget {
                background: #0C1014;
                border: 1px solid #324046;
                border-radius: 4px;
                min-height: 24px;
                padding: 2px 6px;
                selection-background-color: #2C6F73;
            }
            QLineEdit#Readonly {
                color: #91E0C3;
                background: #10171A;
            }
            QPushButton {
                background: #26343A;
                border: 1px solid #405156;
                border-radius: 4px;
                min-height: 26px;
                padding: 4px 10px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #30444B;
                border-color: #5B7478;
            }
            QPushButton#PrimaryButton {
                background: #D7C17C;
                color: #101418;
                border-color: #E6D79B;
            }
            QTableWidget {
                background: #0C1014;
                alternate-background-color: #111A1E;
                border: 1px solid #2D3B3F;
                gridline-color: #203036;
            }
            QHeaderView::section {
                background: #19252A;
                color: #F4F1E8;
                border: 0;
                border-right: 1px solid #2D3B3F;
                padding: 6px;
                font-weight: 700;
            }
            """
        )

    def _spin(self, minimum: int, maximum: int, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        return spin

    def _browse_config(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Project_Xs config", "", "JSON files (*.json);;All files (*)")
        if path:
            self.config_path.setText(path)

    def _refresh_encounters(self) -> None:
        version = self.version_combo.currentText() if hasattr(self, "version_combo") else GameVersion.BDSP.value
        category = self.category_combo.currentData() if hasattr(self, "category_combo") else None
        try:
            self._records = get_static_encounters(category, version)
        except Exception as exc:  # pragma: no cover - defensive UI boundary
            self._show_error("Unable to load encounters", exc)
            self._records = ()
        self.encounter_combo.clear()
        for record in self._records:
            suffix = " roamer" if record.template.roamer else ""
            self.encounter_combo.addItem(f"{record.description} [{record.category.value}]{suffix}", record)

    def _update_tsv(self) -> None:
        self.tsv.setText(str(self.tid.value() ^ self.sid.value()))

    def _set_all_natures(self, state: Qt.CheckState) -> None:
        for row in range(self.nature_list.count()):
            self.nature_list.item(row).setCheckState(state)

    def _sync_seed64_from_state32(self) -> None:
        try:
            state = SeedState32.from_hex_words([box.text() for box in self.seed32_inputs])
        except ValueError as exc:
            self.seed_badge.setText(str(exc))
            return
        for output, text in zip(self.seed64_outputs, state.format_seed64_pair()):
            output.setText(text)
        self.seed_badge.setText("Seed[0-1] linked")

    def _current_seed_pair(self) -> SeedPair64:
        state = SeedState32.from_hex_words([box.text() for box in self.seed32_inputs])
        return state.to_seed_pair64()

    def _current_profile(self) -> Profile8:
        return Profile8(
            name=self.profile_name.text() or "-",
            version=self.version_combo.currentText(),
            tid=self.tid.value(),
            sid=self.sid.value(),
            national_dex=self.national_dex.isChecked(),
            shiny_charm=self.shiny_charm.isChecked(),
            oval_charm=self.oval_charm.isChecked(),
        )

    def _current_filter(self) -> tuple[StateFilter, str]:
        shiny_mode = self.shiny_filter.currentData()
        shiny_value = {
            "any": 255,
            "shiny": 1 | 2,
            "star": 1,
            "square": 2,
            "none": 255,
        }[shiny_mode]
        natures = tuple(
            self.nature_list.item(row).checkState() == Qt.CheckState.Checked
            for row in range(self.nature_list.count())
        )
        return (
            StateFilter.from_iv_ranges(
                [spin.value() for spin in self.iv_min],
                [spin.value() for spin in self.iv_max],
                ability=self.ability_filter.currentData(),
                gender=self.gender_filter.currentData(),
                shiny=shiny_value,
                natures=natures,
            ),
            shiny_mode,
        )

    def capture_seed(self) -> None:
        try:
            config = load_project_xs_config(self.config_path.text(), blink_count=self.blink_count.value())
            observation = capture_player_blinks(config.capture)
            result = recover_seed_from_observation(observation, npc=self.npc_count.value())
        except ProjectXsIntegrationError as exc:
            self._show_error("Blink capture failed", exc)
            return
        for box, text in zip(self.seed32_inputs, result.state.format_words()):
            box.setText(text)
        self._sync_seed64_from_state32()
        self.statusBar().showMessage("Seed captured")

    def generate_results(self) -> None:
        try:
            record = self.encounter_combo.currentData()
            if record is None:
                raise ValueError("Select a static encounter")
            seed = self._current_seed_pair()
            state_filter, shiny_mode = self._current_filter()
            template = replace(record.template, version=self.version_combo.currentText())
            generator = StaticGenerator8(
                self.initial_advances.value(),
                self.max_advances.value(),
                self.offset.value(),
                self.lead_combo.currentData(),
                template,
                self._current_profile(),
                state_filter,
            )
            states = generator.generate(seed)
            if shiny_mode == "none":
                states = [state for state in states if state.shiny == 0]
        except Exception as exc:
            self._show_error("Generation failed", exc)
            return
        self._states = states
        self._populate_table(states)
        self.statusBar().showMessage(f"Generated {len(states)} result(s)")

    def _populate_table(self, states: list[State8]) -> None:
        self.table.setRowCount(len(states))
        for row, state in enumerate(states):
            values = self._state_row(state)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 3 and value != "-":
                    item.setForeground(Qt.GlobalColor.yellow)
                self.table.setItem(row, column, item)
        self.result_count.setText(f"{len(states)} results")

    def _state_row(self, state: State8) -> list[str]:
        shiny = {0: "-", 1: "Star", 2: "Square"}.get(state.shiny, str(state.shiny))
        gender = {0: "M", 1: "F", 2: "-"}.get(state.gender, str(state.gender))
        return [
            str(state.advances),
            f"{state.ec:08X}",
            f"{state.pid:08X}",
            shiny,
            NATURES[state.nature],
            str(state.ability),
            gender,
            *(str(iv) for iv in state.ivs),
            str(state.height),
            str(state.weight),
        ]

    def _table_text(self) -> str:
        rows = ["\t".join(RESULT_HEADERS)]
        for state in self._states:
            rows.append("\t".join(self._state_row(state)))
        return "\n".join(rows)

    def copy_results(self) -> None:
        if not self._states:
            self.statusBar().showMessage("No results to copy")
            return
        QGuiApplication.clipboard().setText(self._table_text())
        self.statusBar().showMessage(f"Copied {len(self._states)} result(s)")

    def export_results(self) -> None:
        if not self._states:
            self.statusBar().showMessage("No results to export")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export results", "bdsp_static_results.csv", "CSV files (*.csv)")
        if not path:
            return
        output = Path(path)
        with output.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(RESULT_HEADERS)
            for state in self._states:
                writer.writerow(self._state_row(state))
        self.statusBar().showMessage(f"Exported {output}")

    def _show_error(self, title: str, error: Exception) -> None:
        QMessageBox.critical(self, title, str(error))
        self.statusBar().showMessage(str(error))


def create_window() -> MainWindow:
    return MainWindow()


def run() -> int:
    app = QApplication.instance() or QApplication([])
    window = create_window()
    window.show()
    return app.exec()
