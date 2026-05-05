from __future__ import annotations

import csv
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QAction, QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
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
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from auto_bdsp_rng.blink_detection import (
    BlinkCaptureConfig,
    ProjectXsIntegrationError,
    ProjectXsTrackingConfig,
    capture_player_blinks,
    capture_preview_frame,
    load_project_xs_config,
    recover_seed_from_observation,
    render_eye_preview,
    save_project_xs_config,
)
from auto_bdsp_rng.data import GameVersion, StaticEncounterCategory, StaticEncounterRecord, get_static_encounters
from auto_bdsp_rng.gen8_static import Lead, Profile8, State8, StateFilter, StaticGenerator8
from auto_bdsp_rng.rng_core import SeedPair64, SeedState32


PROJECT_ROOT = Path(__file__).resolve().parents[3]
PROJECT_XS_CONFIGS = PROJECT_ROOT / "third_party" / "Project_Xs_CHN" / "configs"
DEFAULT_BLINK_COUNT = 40

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

TEXT = {
    "en": {
        "title": "BDSP Static RNG Workbench",
        "language": "Language",
        "capture": "Blink capture",
        "seed": "Seed",
        "static": "BDSP static target",
        "profile": "Profile",
        "filters": "Filters",
        "preview": "Preview",
        "project_xs": "Project_Xs",
        "bdsp_search": "BDSP / PokeFinder",
        "status": "Status",
        "config": "Config",
        "browse": "Browse",
        "monitor_window": "Monitor Window",
        "window_prefix": "Window Prefix",
        "camera": "Camera",
        "x": "X",
        "y": "Y",
        "w": "W",
        "h": "H",
        "threshold": "Threshold",
        "time_delay": "Time Delay",
        "advance_delay": "Advance Delay",
        "advance_delay_2": "Advance Delay 2",
        "npcs": "NPCs",
        "timeline_npcs": "NPCs during Timeline",
        "pokemon_npcs": "Pokemon NPCs",
        "display_percent": "Display Percent",
        "capture_seed": "Capture Seed",
        "preview_button": "Preview",
        "stop_preview": "Stop Preview",
        "save_config": "Save Config",
        "raw_screenshot": "Raw Screenshot",
        "generate": "Generate",
        "copy": "Copy",
        "export": "Export CSV",
        "ready": "Ready",
        "seed_linked": "Seed[0-1] linked",
        "no_preview": "Preview is stopped",
        "capturing": "Capturing 40 blinks...",
        "seed_captured": "Seed captured",
        "config_saved": "Config saved",
        "preview_running": "Preview running",
        "results": "results",
    },
    "zh": {
        "title": "BDSP 定点乱数工作台",
        "language": "语言",
        "capture": "眨眼捕捉",
        "seed": "Seed",
        "static": "BDSP 定点目标",
        "profile": "玩家档案",
        "filters": "筛选",
        "preview": "捕获预览",
        "project_xs": "Project_Xs",
        "bdsp_search": "BDSP / PokeFinder",
        "status": "状态",
        "config": "配置",
        "browse": "浏览",
        "monitor_window": "捕捉窗口",
        "window_prefix": "窗口前缀",
        "camera": "摄像头",
        "x": "X",
        "y": "Y",
        "w": "W",
        "h": "H",
        "threshold": "阈值",
        "time_delay": "时间延迟",
        "advance_delay": "Advance 延迟",
        "advance_delay_2": "Advance 延迟 2",
        "npcs": "NPC 数",
        "timeline_npcs": "Timeline NPC 数",
        "pokemon_npcs": "Pokemon NPC 数",
        "display_percent": "显示百分比",
        "capture_seed": "捕捉 Seed",
        "preview_button": "预览",
        "stop_preview": "停止预览",
        "save_config": "保存配置",
        "raw_screenshot": "原始截图",
        "generate": "生成",
        "copy": "复制",
        "export": "导出 CSV",
        "ready": "就绪",
        "seed_linked": "Seed[0-1] 已同步",
        "no_preview": "预览已停止",
        "capturing": "正在捕捉 40 次眨眼...",
        "seed_captured": "Seed 捕捉完成",
        "config_saved": "配置已保存",
        "preview_running": "正在预览",
        "results": "条结果",
    },
}


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("auto_bdsp_rng")
        self.resize(1480, 900)
        self.lang = "zh"
        self._records: tuple[StaticEncounterRecord, ...] = ()
        self._states: list[State8] = []
        self._preview_timer = QTimer(self)
        self._preview_timer.setInterval(250)
        self._preview_timer.timeout.connect(self._update_preview_frame)
        self._build_actions()
        self._build_ui()
        self._apply_theme()
        self._refresh_config_list()
        self._refresh_encounters()
        self._sync_seed64_from_state32()
        self._apply_language()
        self.statusBar().showMessage(self._text("ready"))

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
        self.title_label = QLabel()
        self.title_label.setObjectName("WindowTitle")
        self.seed_badge = QLabel()
        self.seed_badge.setObjectName("Badge")
        self.language_label = QLabel()
        self.language_combo = QComboBox()
        self.language_combo.addItem("中文", "zh")
        self.language_combo.addItem("English", "en")
        self.language_combo.currentIndexChanged.connect(self._change_language)
        header_layout.addWidget(self.title_label)
        header_layout.addStretch(1)
        header_layout.addWidget(self.seed_badge)
        header_layout.addSpacing(16)
        header_layout.addWidget(self.language_label)
        header_layout.addWidget(self.language_combo)
        root_layout.addWidget(header)

        self.tabs = QTabWidget()
        self.project_xs_tab = self._build_project_xs_tab()
        self.bdsp_tab = self._build_bdsp_tab()
        self.tabs.addTab(self.project_xs_tab, self._text("project_xs"))
        self.tabs.addTab(self.bdsp_tab, self._text("bdsp_search"))
        root_layout.addWidget(self.tabs, 1)

        self.setCentralWidget(root)
        self.setStatusBar(QStatusBar())

    def _build_project_xs_tab(self) -> QWidget:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 8, 0)
        left_layout.setSpacing(10)
        self.status_group = self._build_project_status_group()
        self.capture_group = self._build_blink_group()
        self.seed_group = self._build_seed_group()
        left_layout.addWidget(self.status_group)
        left_layout.addWidget(self.capture_group)
        left_layout.addWidget(self.seed_group)
        left_layout.addStretch(1)

        splitter.addWidget(left)
        splitter.addWidget(self._build_preview_panel())
        splitter.setSizes([430, 1050])
        return splitter

    def _build_bdsp_tab(self) -> QWidget:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        controls = QScrollArea()
        controls.setWidgetResizable(True)
        control_panel = QWidget()
        control_layout = QVBoxLayout(control_panel)
        control_layout.setContentsMargins(0, 0, 8, 0)
        control_layout.setSpacing(10)
        self.static_group = self._build_static_group()
        self.profile_group = self._build_profile_group()
        self.filter_group = self._build_filter_group()
        control_layout.addWidget(self.static_group)
        control_layout.addWidget(self.profile_group)
        control_layout.addWidget(self.filter_group)
        control_layout.addStretch(1)
        controls.setWidget(control_panel)

        splitter.addWidget(controls)
        splitter.addWidget(self._build_results())
        splitter.setSizes([430, 1050])
        return splitter

    def _build_project_status_group(self) -> QGroupBox:
        group = QGroupBox()
        layout = QGridLayout(group)
        self.progress_label = QLabel("Progress:")
        self.progress_value = QLabel("0/0")
        self.advances_label = QLabel("Advances:")
        self.advances_value = QLabel("0")
        self.timer_label = QLabel("Timer:")
        self.timer_value = QLabel("0")
        self.x_to_advance_label = QLabel("X to advance:")
        self.x_to_advance = self._spin(0, 10_000_000, 165)
        self.advance_button = QPushButton("Advance")
        self.advance_button.setEnabled(False)
        layout.addWidget(self.progress_label, 0, 0)
        layout.addWidget(self.progress_value, 0, 1)
        layout.addWidget(self.advances_label, 1, 0)
        layout.addWidget(self.advances_value, 1, 1)
        layout.addWidget(self.timer_label, 2, 0)
        layout.addWidget(self.timer_value, 2, 1)
        layout.addWidget(self.x_to_advance_label, 3, 0)
        layout.addWidget(self.x_to_advance, 3, 1)
        layout.addWidget(self.advance_button, 4, 1)
        return group

    def _build_controls(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(10)
        self.capture_group = self._build_blink_group()
        self.seed_group = self._build_seed_group()
        self.static_group = self._build_static_group()
        self.profile_group = self._build_profile_group()
        self.filter_group = self._build_filter_group()
        layout.addWidget(self.capture_group)
        layout.addWidget(self.seed_group)
        layout.addWidget(self.static_group)
        layout.addWidget(self.profile_group)
        layout.addWidget(self.filter_group)
        layout.addStretch(1)
        scroll.setWidget(panel)
        return scroll

    def _build_blink_group(self) -> QGroupBox:
        group = QGroupBox()
        layout = QGridLayout(group)
        self.config_label = QLabel()
        self.config_combo = QComboBox()
        self.config_combo.setEditable(True)
        self.config_combo.currentTextChanged.connect(self._load_config_to_form)
        self.config_combo.currentIndexChanged.connect(lambda _index: self._load_config_to_form(self.config_combo.currentText()))
        self.browse_button = QPushButton()
        self.browse_button.clicked.connect(self._browse_config)
        self.capture_button = QPushButton()
        self.capture_button.setObjectName("PrimaryButton")
        self.capture_button.clicked.connect(self.capture_seed)
        self.preview_button = QPushButton()
        self.preview_button.clicked.connect(self.toggle_preview)
        self.save_config_button = QPushButton()
        self.save_config_button.clicked.connect(self.save_current_config)
        self.raw_screenshot_button = QPushButton()
        self.raw_screenshot_button.clicked.connect(self.save_raw_screenshot)

        self.monitor_window = QCheckBox()
        self.window_prefix = QLineEdit()
        self.camera = self._spin(0, 99, 0)
        self.x = self._spin(0, 10000, 0)
        self.y = self._spin(0, 10000, 0)
        self.w = self._spin(1, 10000, 40)
        self.h = self._spin(1, 10000, 40)
        self.threshold = self._double_spin(0.0, 1.0, 0.9, 2)
        self.white_delay = self._double_spin(0.0, 999.0, 0.0, 1)
        self.advance_delay = self._spin(0, 9999, 0)
        self.advance_delay_2 = self._spin(0, 9999, 0)
        self.npc_count = self._spin(0, 999, 0)
        self.timeline_npc = self._spin(0, 999, 0)
        self.pokemon_npc = self._spin(0, 999, 0)
        self.display_percent = self._spin(1, 300, 80)
        self.blink_count = DEFAULT_BLINK_COUNT

        layout.addWidget(self.config_label, 0, 0)
        layout.addWidget(self.config_combo, 0, 1, 1, 2)
        layout.addWidget(self.browse_button, 0, 3)
        layout.addWidget(self.monitor_window, 1, 1)
        layout.addWidget(self.capture_button, 1, 2)
        layout.addWidget(self.preview_button, 1, 3)
        layout.addWidget(QLabel(), 2, 0)
        layout.addWidget(self.window_prefix, 2, 1, 1, 3)
        self._add_form_row(layout, 3, "camera", self.camera)
        self._add_form_row(layout, 4, "x", self.x)
        self._add_form_row(layout, 5, "y", self.y)
        self._add_form_row(layout, 6, "w", self.w)
        self._add_form_row(layout, 7, "h", self.h)
        self._add_form_row(layout, 8, "threshold", self.threshold)
        self._add_form_row(layout, 9, "time_delay", self.white_delay)
        self._add_form_row(layout, 10, "advance_delay", self.advance_delay)
        self._add_form_row(layout, 11, "advance_delay_2", self.advance_delay_2)
        self._add_form_row(layout, 12, "npcs", self.npc_count)
        self._add_form_row(layout, 13, "timeline_npcs", self.timeline_npc)
        self._add_form_row(layout, 14, "pokemon_npcs", self.pokemon_npc)
        self._add_form_row(layout, 15, "display_percent", self.display_percent)
        layout.addWidget(self.save_config_button, 16, 2)
        layout.addWidget(self.raw_screenshot_button, 16, 3)
        return group

    def _add_form_row(self, layout: QGridLayout, row: int, key: str, widget: QWidget) -> None:
        label = QLabel()
        label.setProperty("i18n", key)
        layout.addWidget(label, row, 0)
        layout.addWidget(widget, row, 1, 1, 3)

    def _build_seed_group(self) -> QGroupBox:
        group = QGroupBox()
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
        group = QGroupBox()
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
        group = QGroupBox()
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
        group = QGroupBox()
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
        self.all_natures_button = QPushButton("All natures")
        self.all_natures_button.clicked.connect(lambda: self._set_all_natures(Qt.CheckState.Checked))
        self.clear_natures_button = QPushButton("Clear")
        self.clear_natures_button.clicked.connect(lambda: self._set_all_natures(Qt.CheckState.Unchecked))
        nature_buttons.addWidget(self.all_natures_button)
        nature_buttons.addWidget(self.clear_natures_button)
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

    def _build_right_side(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self._build_preview_panel(), 1)
        layout.addWidget(self._build_results(), 1)
        return panel

    def _build_preview_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 0, 0, 0)
        layout.setSpacing(10)
        self.preview_group = QGroupBox()
        preview_layout = QVBoxLayout(self.preview_group)
        self.preview_label = QLabel()
        self.preview_label.setObjectName("Preview")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumHeight(560)
        preview_layout.addWidget(self.preview_label)
        layout.addWidget(self.preview_group, 1)
        return panel

    def _build_results(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        toolbar = QHBoxLayout()
        self.generate_button = QPushButton()
        self.generate_button.setObjectName("PrimaryButton")
        self.generate_button.clicked.connect(self.generate_results)
        self.copy_button = QPushButton()
        self.copy_button.clicked.connect(self.copy_results)
        self.export_button = QPushButton()
        self.export_button.clicked.connect(self.export_results)
        self.result_count = QLabel("0 results")
        self.result_count.setObjectName("ResultCount")
        toolbar.addWidget(self.generate_button)
        toolbar.addWidget(self.copy_button)
        toolbar.addWidget(self.export_button)
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
            QLabel#Preview {
                background: #070A0D;
                border: 1px solid #2D3B3F;
                border-radius: 4px;
                color: #6F7D80;
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
            QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QListWidget {
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

    def _double_spin(self, minimum: float, maximum: float, value: float, decimals: int) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(decimals)
        spin.setSingleStep(0.1)
        spin.setValue(value)
        return spin

    def _text(self, key: str) -> str:
        return TEXT[self.lang][key]

    def _change_language(self) -> None:
        self.lang = self.language_combo.currentData()
        self._apply_language()

    def _apply_language(self) -> None:
        self.title_label.setText(self._text("title"))
        self.language_label.setText(self._text("language"))
        self.tabs.setTabText(0, self._text("project_xs"))
        self.tabs.setTabText(1, self._text("bdsp_search"))
        self.status_group.setTitle(self._text("status"))
        self.capture_group.setTitle(self._text("capture"))
        self.seed_group.setTitle(self._text("seed"))
        self.static_group.setTitle(self._text("static"))
        self.profile_group.setTitle(self._text("profile"))
        self.filter_group.setTitle(self._text("filters"))
        self.preview_group.setTitle(self._text("preview"))
        self.config_label.setText(self._text("config"))
        self.browse_button.setText(self._text("browse"))
        self.monitor_window.setText(self._text("monitor_window"))
        self.capture_button.setText(self._text("capture_seed"))
        self.preview_button.setText(self._text("stop_preview") if self._preview_timer.isActive() else self._text("preview_button"))
        self.save_config_button.setText(self._text("save_config"))
        self.raw_screenshot_button.setText(self._text("raw_screenshot"))
        self.generate_button.setText(self._text("generate"))
        self.copy_button.setText(self._text("copy"))
        self.export_button.setText(self._text("export"))
        self.seed_badge.setText(self._text("seed_linked"))
        self.preview_label.setText(self._text("no_preview"))
        self.result_count.setText(f"{len(self._states)} {self._text('results')}")
        for label in self.findChildren(QLabel):
            key = label.property("i18n")
            if key:
                label.setText(self._text(str(key)))

    def _refresh_config_list(self) -> None:
        self.config_combo.blockSignals(True)
        self.config_combo.clear()
        configs = sorted(PROJECT_XS_CONFIGS.glob("*.json"))
        for path in configs:
            self.config_combo.addItem(path.name, str(path))
        if configs:
            default = next((index for index, path in enumerate(configs) if path.name == "config_bebe.json"), 0)
            self.config_combo.setCurrentIndex(default)
        self.config_combo.blockSignals(False)
        self._load_config_to_form(self.config_combo.currentText())

    def _browse_config(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Project_Xs config", str(PROJECT_XS_CONFIGS), "JSON files (*.json);;All files (*)")
        if path:
            index = self.config_combo.findData(path)
            if index < 0:
                self.config_combo.addItem(Path(path).name, path)
                index = self.config_combo.count() - 1
            self.config_combo.setCurrentIndex(index)

    def _selected_config_path(self) -> str:
        data = self.config_combo.currentData()
        return str(data or self.config_combo.currentText())

    def _load_config_to_form(self, _text: str) -> None:
        if not hasattr(self, "monitor_window"):
            return
        try:
            config = load_project_xs_config(self._selected_config_path(), blink_count=DEFAULT_BLINK_COUNT)
        except ProjectXsIntegrationError:
            return
        roi_x, roi_y, roi_w, roi_h = config.capture.roi
        self.monitor_window.setChecked(config.capture.monitor_window)
        self.window_prefix.setText(config.capture.window_prefix)
        self.camera.setValue(config.capture.camera)
        self.x.setValue(roi_x)
        self.y.setValue(roi_y)
        self.w.setValue(roi_w)
        self.h.setValue(roi_h)
        self.threshold.setValue(config.capture.threshold)
        self.white_delay.setValue(config.white_delay)
        self.advance_delay.setValue(config.advance_delay)
        self.advance_delay_2.setValue(config.advance_delay_2)
        self.npc_count.setValue(config.npc)
        self.timeline_npc.setValue(config.timeline_npc)
        self.pokemon_npc.setValue(config.pokemon_npc)
        self.display_percent.setValue(config.display_percent)

    def _config_from_form(self) -> ProjectXsTrackingConfig:
        loaded = load_project_xs_config(self._selected_config_path(), blink_count=DEFAULT_BLINK_COUNT)
        capture = BlinkCaptureConfig(
            eye_image_path=loaded.capture.eye_image_path,
            roi=(self.x.value(), self.y.value(), self.w.value(), self.h.value()),
            threshold=self.threshold.value(),
            blink_count=DEFAULT_BLINK_COUNT,
            monitor_window=self.monitor_window.isChecked(),
            window_prefix=self.window_prefix.text(),
            crop=loaded.capture.crop,
            camera=self.camera.value(),
        )
        return ProjectXsTrackingConfig(
            source_path=loaded.source_path,
            capture=capture,
            white_delay=self.white_delay.value(),
            advance_delay=self.advance_delay.value(),
            advance_delay_2=self.advance_delay_2.value(),
            npc=self.npc_count.value(),
            pokemon_npc=self.pokemon_npc.value(),
            timeline_npc=self.timeline_npc.value(),
            display_percent=self.display_percent.value(),
        )

    def save_current_config(self) -> None:
        try:
            config = self._config_from_form()
            save_project_xs_config(config, config.source_path)
        except ProjectXsIntegrationError as exc:
            self._show_error("Save config failed", exc)
            return
        self.statusBar().showMessage(self._text("config_saved"))

    def toggle_preview(self) -> None:
        if self._preview_timer.isActive():
            self._preview_timer.stop()
            self.preview_button.setText(self._text("preview_button"))
            self.preview_label.setText(self._text("no_preview"))
            return
        self._preview_timer.start()
        self.preview_button.setText(self._text("stop_preview"))
        self.statusBar().showMessage(self._text("preview_running"))

    def _update_preview_frame(self) -> None:
        try:
            config = self._config_from_form().capture
            frame = capture_preview_frame(config)
            annotated, preview = render_eye_preview(config, frame)
            pixmap = self._frame_to_pixmap(annotated)
        except Exception as exc:
            self._preview_timer.stop()
            self.preview_button.setText(self._text("preview_button"))
            self._show_error("Preview failed", exc if isinstance(exc, Exception) else Exception(str(exc)))
            return
        target = self.preview_label.size()
        self.preview_label.setPixmap(pixmap.scaled(target, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.statusBar().showMessage(f"{self._text('preview_running')} | score {preview.match_score:.3f}")

    def _frame_to_pixmap(self, frame: object) -> QPixmap:
        import cv2

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        height, width, channel = rgb.shape
        image = QImage(rgb.data, width, height, channel * width, QImage.Format.Format_RGB888).copy()
        return QPixmap.fromImage(image)

    def save_raw_screenshot(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Raw screenshot", "raw_screenshot.png", "PNG files (*.png)")
        if not path:
            return
        try:
            import cv2

            frame = capture_preview_frame(self._config_from_form().capture)
            if not cv2.imwrite(path, frame):
                raise ProjectXsIntegrationError(f"Cannot save raw screenshot: {path}")
        except Exception as exc:
            self._show_error("Raw screenshot failed", exc if isinstance(exc, Exception) else Exception(str(exc)))
            return
        self.statusBar().showMessage(f"Saved {path}")

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
        self.seed_badge.setText(self._text("seed_linked"))

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
            config = self._config_from_form()
            self.progress_value.setText(f"0/{DEFAULT_BLINK_COUNT}")
            self.statusBar().showMessage(self._text("capturing"))
            QApplication.processEvents()
            observation = capture_player_blinks(config.capture)
            result = recover_seed_from_observation(observation, npc=config.npc)
        except ProjectXsIntegrationError as exc:
            self._show_error("Blink capture failed", exc)
            return
        for box, text in zip(self.seed32_inputs, result.state.format_words()):
            box.setText(text)
        self.progress_value.setText(f"{DEFAULT_BLINK_COUNT}/{DEFAULT_BLINK_COUNT}")
        self._sync_seed64_from_state32()
        self.statusBar().showMessage(self._text("seed_captured"))

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
        self.statusBar().showMessage(f"{len(states)} {self._text('results')}")

    def _populate_table(self, states: list[State8]) -> None:
        self.table.setRowCount(len(states))
        for row, state in enumerate(states):
            values = self._state_row(state)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 3 and value != "-":
                    item.setForeground(Qt.GlobalColor.yellow)
                self.table.setItem(row, column, item)
        self.result_count.setText(f"{len(states)} {self._text('results')}")

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
