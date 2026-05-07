from __future__ import annotations

import csv
import threading
import time
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QColor, QGuiApplication, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
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
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
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
    advance_seed_state,
    capture_player_blinks,
    capture_preview_frame,
    load_project_xs_config,
    reidentify_seed_from_observation,
    recover_seed_from_observation,
    render_eye_preview,
    save_project_xs_config,
)
from auto_bdsp_rng.data import GameVersion, StaticEncounterCategory, StaticEncounterRecord, get_static_encounters
from auto_bdsp_rng.gen8_static import Lead, Profile8, Shiny, State8, StateFilter, StaticGenerator8
from auto_bdsp_rng.rng_core import SeedPair64, SeedState32
from auto_bdsp_rng.ui.easycon_panel import EasyConPanel


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
STAT_LABELS_ZH = ("HP能力", "攻击能力", "防御能力", "特攻能力", "特防能力", "速度能力")
NATURE_MODIFIERS = (
    (-1, -1),
    (1, 2),
    (1, 5),
    (1, 3),
    (1, 4),
    (2, 1),
    (-1, -1),
    (2, 5),
    (2, 3),
    (2, 4),
    (5, 1),
    (5, 2),
    (-1, -1),
    (5, 3),
    (5, 4),
    (3, 1),
    (3, 2),
    (3, 5),
    (-1, -1),
    (3, 4),
    (4, 1),
    (4, 2),
    (4, 5),
    (4, 3),
    (-1, -1),
)
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
    "Characteristic",
)
RESULT_HEADERS_ZH = (
    "帧数",
    "EC",
    "PID",
    "异色",
    "性格",
    "特性",
    "性别",
    "HP",
    "攻击",
    "防御",
    "特攻",
    "特防",
    "速度",
    "身高",
    "体重",
    "个性",
)
GAME_LABELS_ZH = {
    GameVersion.BD: "晶灿钻石",
    GameVersion.SP: "明亮珍珠",
    GameVersion.BDSP: "晶灿钻石 / 明亮珍珠",
}
GAME_LABELS_EN = {
    GameVersion.BD: "Brilliant Diamond",
    GameVersion.SP: "Shining Pearl",
    GameVersion.BDSP: "BDSP",
}
CHARACTERISTICS_ZH = (
    ("非常喜欢吃", "经常打瞌睡", "经常午睡", "经常乱扔东西", "喜欢放松"),
    ("以力气自豪", "喜欢打闹", "有点易怒", "喜欢打架", "血气方刚"),
    ("身体强壮", "能忍耐", "抗打能力强", "不屈不挠", "毅力十足"),
    ("好奇心强", "爱恶作剧", "考虑周到", "经常思考", "非常讲究"),
    ("意志坚强", "有点固执", "讨厌输", "有点爱逞强", "忍耐力强"),
    ("喜欢跑步", "警觉性高", "冲动", "有点轻浮", "逃得快"),
)
ABILITY_NAMES_ZH = {
    65: "茂盛",
    66: "猛火",
    67: "激流",
    75: "硬壳盔甲",
}
NATURES_ZH = (
    "勤奋",
    "怕寂寞",
    "勇敢",
    "固执",
    "顽皮",
    "大胆",
    "坦率",
    "悠闲",
    "淘气",
    "乐天",
    "胆小",
    "急躁",
    "认真",
    "爽朗",
    "天真",
    "内敛",
    "慢吞吞",
    "冷静",
    "害羞",
    "马虎",
    "温和",
    "温顺",
    "自大",
    "慎重",
    "浮躁",
)
CATEGORY_LABELS_ZH = {
    None: "全部",
    "starters": "御三家",
    "gifts": "赠送",
    "fossils": "化石",
    "stationary": "定点",
    "roamers": "游走",
    "legends": "传说",
    "ramanasParkPureSpace": "玫瑰公园（纯净空间）",
    "ramanasParkStrangeSpace": "玫瑰公园（奇异空间）",
    "mythics": "幻兽",
}
POKEMON_LABELS_ZH = {
    "Turtwig": "草苗龟",
    "Chimchar": "小火焰猴",
    "Piplup": "波加曼",
    "Eevee": "伊布",
    "Happiny egg": "小福蛋蛋",
    "Riolu egg": "利欧路蛋",
    "Omanyte": "菊石兽",
    "Kabuto": "化石盔",
    "Aerodactyl": "化石翼龙",
    "Lileep": "触手百合",
    "Anorith": "太古羽虫",
    "Cranidos": "头盖龙",
    "Shieldon": "盾甲龙",
    "Drifloon": "飘飘球",
    "Spiritomb": "花岩怪",
    "Rotom": "洛托姆",
    "Mespirit": "艾姆利多",
    "Cresselia": "克雷色利亚",
    "Uxie": "由克希",
    "Azelf": "亚克诺姆",
    "Dialga": "帝牙卢卡",
    "Palkia": "帕路奇亚",
    "Heatran": "席多蓝恩",
    "Regigigas": "雷吉奇卡斯",
    "Giratina": "骑拉帝纳",
    "Articuno": "急冻鸟",
    "Zapdos": "闪电鸟",
    "Moltres": "火焰鸟",
    "Raikou": "雷公",
    "Entei": "炎帝",
    "Suicune": "水君",
    "Regirock": "雷吉洛克",
    "Regice": "雷吉艾斯",
    "Registeel": "雷吉斯奇鲁",
    "Latias": "拉帝亚斯",
    "Latios": "拉帝欧斯",
    "Mewtwo": "超梦",
    "Lugia": "洛奇亚",
    "Ho-Oh": "凤王",
    "Kyogre": "盖欧卡",
    "Groudon": "固拉多",
    "Rayquaza": "烈空坐",
    "Mew": "梦幻",
    "Jirachi": "基拉祈",
    "Darkrai": "达克莱伊",
    "Shaymin": "谢米",
    "Arceus": "阿尔宙斯",
}

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
        "project_xs": "Seed 捕捉",
        "bdsp_search": "BDSP / PokeFinder",
        "easycon": "EasyCon",
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
        "reidentify_seed": "Reidentify",
        "stop_capture": "Stop Capture",
        "preview_button": "Preview",
        "stop_preview": "Stop Preview",
        "save_config": "Save Config",
        "raw_screenshot": "Capture Eye",
        "select_roi": "框选眼睛区域",
        "eye_selecting": "Right-drag on preview to capture the eye template",
        "eye_captured": "Eye template captured",
        "eye_captured_select_roi": "Eye template captured. Redraw the ROI around the eye.",
        "roi_selected": "ROI selected",
        "roi_selecting": "Right-drag on preview to select ROI",
        "roi_too_small": "ROI is smaller than the eye template. Restored previous ROI.",
        "generate": "Generate",
        "copy": "Copy",
        "export": "Export CSV",
        "ready": "Ready",
        "seed_linked": "Seed[0-1] linked",
        "no_preview": "Preview is stopped",
        "capturing": "Capturing 40 blinks...",
        "capture_stopping": "Stopping blink capture...",
        "capture_stopped": "Blink capture stopped",
        "seed_captured_npc_fallback": "Seed captured with NPCs reset to 0",
        "seed_captured": "Seed captured",
        "seed_reidentified": "Seed reidentified",
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
        "project_xs": "Seed 捕捉",
        "bdsp_search": "BDSP / PokeFinder",
        "easycon": "伊机控",
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
        "reidentify_seed": "重新识别",
        "stop_capture": "停止捕捉",
        "preview_button": "预览",
        "stop_preview": "停止预览",
        "save_config": "保存配置",
        "raw_screenshot": "截取眼睛",
        "select_roi": "框选眼睛区域",
        "eye_selecting": "请在预览图上按住右键框选眼睛模板",
        "eye_captured": "眼睛模板已应用",
        "eye_captured_select_roi": "眼睛模板已应用，请重新框选眼睛 ROI",
        "roi_selected": "ROI 已选择",
        "roi_selecting": "请在预览图上按住右键拖拽选择 ROI",
        "roi_too_small": "ROI 小于眼睛模板，已恢复之前的 ROI。",
        "generate": "生成",
        "copy": "复制",
        "export": "导出 CSV",
        "ready": "就绪",
        "seed_linked": "Seed[0-1] 已同步",
        "no_preview": "预览已停止",
        "capturing": "正在捕捉 40 次眨眼...",
        "capture_stopping": "正在停止捕捉...",
        "capture_stopped": "捕捉已停止",
        "seed_captured_npc_fallback": "Seed 捕捉完成，已将 NPC 数重置为 0",
        "seed_captured": "Seed 捕捉完成",
        "seed_reidentified": "Seed 重新识别完成",
        "config_saved": "配置已保存",
        "preview_running": "正在预览",
        "results": "条结果",
    },
}


class RoiPreviewLabel(QLabel):
    roiSelected = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self._selection_enabled = False
        self._drag_start: QPoint | None = None
        self._drag_current: QPoint | None = None
        self._image_width = 0
        self._image_height = 0
        self._pixmap_rect = QRect()

    def set_image_geometry(self, image_width: int, image_height: int, pixmap_rect: QRect) -> None:
        self._image_width = image_width
        self._image_height = image_height
        self._pixmap_rect = QRect(pixmap_rect)

    def set_selection_enabled(self, enabled: bool) -> None:
        self._selection_enabled = enabled
        self._drag_start = None
        self._drag_current = None
        self.setCursor(Qt.CursorShape.CrossCursor if enabled else Qt.CursorShape.ArrowCursor)
        self.update()

    def mousePressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if (
            self._selection_enabled
            and event.button() == Qt.MouseButton.RightButton
            and self._pixmap_rect.contains(event.position().toPoint())
        ):
            self._drag_start = event.position().toPoint()
            self._drag_current = self._drag_start
            self.update()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self._selection_enabled and self._drag_start is not None:
            self._drag_current = event.position().toPoint()
            self.update()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self._selection_enabled and event.button() == Qt.MouseButton.RightButton and self._drag_start is not None:
            self._drag_current = event.position().toPoint()
            selected = QRect(self._drag_start, self._drag_current).normalized().intersected(self._pixmap_rect)
            self._drag_start = None
            self._drag_current = None
            self.update()
            if selected.isValid() and self._pixmap_rect.width() > 0 and self._pixmap_rect.height() > 0:
                scale_x = self._image_width / self._pixmap_rect.width()
                scale_y = self._image_height / self._pixmap_rect.height()
                x = round((selected.left() - self._pixmap_rect.left()) * scale_x)
                y = round((selected.top() - self._pixmap_rect.top()) * scale_y)
                width = max(1, round(selected.width() * scale_x))
                height = max(1, round(selected.height() * scale_y))
                self.roiSelected.emit((x, y, width, height))
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().paintEvent(event)
        if self._selection_enabled and self._drag_start is not None and self._drag_current is not None:
            painter = QPainter(self)
            pen = QPen(QColor("#D7C17C"))
            pen.setWidth(2)
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawRect(QRect(self._drag_start, self._drag_current).normalized().intersected(self._pixmap_rect))


class PokeFinderTableWidget(QTableWidget):
    """QTableWidget with PokeFinder-style prefix search on the active column."""

    searchStatusChanged = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._search_text = ""
        self._last_search_at = 0.0

    def keyPressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
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


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("auto_bdsp_rng")
        self.setMinimumSize(960, 640)
        self.resize(1480, 900)
        self.lang = "zh"
        self._profile_version = GameVersion.BD
        self._active_record: StaticEncounterRecord | None = None
        self._records: tuple[StaticEncounterRecord, ...] = ()
        self._states: list[State8] = []
        self._eye_image_path: Path | None = None
        self._latest_preview_frame: object | None = None
        self._roi_before_selection: tuple[int, int, int, int] | None = None
        self._selection_mode: str | None = None
        self._resume_preview_after_selection = False
        self._preview_timer = QTimer(self)
        self._preview_timer.setInterval(100)
        self._preview_timer.timeout.connect(self._update_preview_frame)
        self._capture_timer = QTimer(self)
        self._capture_timer.setInterval(100)
        self._capture_timer.timeout.connect(self._poll_capture_thread)
        self._capture_cancel = threading.Event()
        self._capture_lock = threading.Lock()
        self._capture_thread: threading.Thread | None = None
        self._capture_result: object | None = None
        self._capture_error: Exception | None = None
        self._capture_frame: object | None = None
        self._capture_mode = "seed"
        self._capture_progress = (0, DEFAULT_BLINK_COUNT)
        self._advance_timer = QTimer(self)
        self._advance_timer.setInterval(1018)
        self._advance_timer.timeout.connect(self._advance_tick)
        self._tracked_advances = 0
        self._advance_step = 1
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
        self.easycon_tab = EasyConPanel()
        self.tabs.addTab(self.project_xs_tab, self._text("project_xs"))
        self.tabs.addTab(self.bdsp_tab, self._text("bdsp_search"))
        self.tabs.addTab(self.easycon_tab, self._text("easycon"))
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
        panel = QWidget()
        layout = QGridLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(10)

        self.profile_group = self._build_profile_group()
        self.rng_info_group = self._build_rng_info_group()
        self.static_group = self._build_static_group()
        self.filter_group = self._build_filter_group()
        self.results_panel = self._build_results()

        layout.addWidget(self.profile_group, 0, 0, 1, 3)
        layout.addWidget(self.rng_info_group, 1, 0)
        layout.addWidget(self.static_group, 1, 1)
        layout.addWidget(self.filter_group, 1, 2)
        layout.addWidget(self.results_panel, 2, 0, 1, 3)
        layout.setRowMinimumHeight(1, 320)
        layout.setColumnStretch(0, 0)
        layout.setColumnStretch(1, 0)
        layout.setColumnStretch(2, 1)
        layout.setRowStretch(2, 1)
        return panel

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
        self.advance_button.clicked.connect(self.advance_current_seed)
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
        self.reidentify_button = QPushButton()
        self.reidentify_button.clicked.connect(self.reidentify_seed)
        self.preview_button = QPushButton()
        self.preview_button.clicked.connect(self.toggle_preview)
        self.save_config_button = QPushButton()
        self.save_config_button.clicked.connect(self.save_current_config)
        self.raw_screenshot_button = QPushButton()
        self.raw_screenshot_button.clicked.connect(self.start_eye_capture_selection)
        self.select_roi_button = QPushButton()
        self.select_roi_button.clicked.connect(self.start_roi_selection)

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
        layout.addWidget(self.preview_button, 1, 0)
        layout.addWidget(self.capture_button, 1, 2)
        layout.addWidget(self.reidentify_button, 1, 3)
        layout.addWidget(QLabel(), 2, 0)
        layout.addWidget(self.window_prefix, 2, 1, 1, 3)
        self._add_form_row(layout, 3, "camera", self.camera)
        layout.addWidget(self.select_roi_button, 4, 1, 1, 3)
        self._add_form_row(layout, 5, "threshold", self.threshold)
        self._add_form_row(layout, 6, "time_delay", self.white_delay)
        self._add_form_row(layout, 7, "advance_delay", self.advance_delay)
        self._add_form_row(layout, 8, "advance_delay_2", self.advance_delay_2)
        self._add_form_row(layout, 9, "npcs", self.npc_count)
        self._add_form_row(layout, 10, "timeline_npcs", self.timeline_npc)
        self._add_form_row(layout, 11, "pokemon_npcs", self.pokemon_npc)
        self._add_form_row(layout, 12, "display_percent", self.display_percent)
        layout.addWidget(self.save_config_button, 13, 2)
        layout.addWidget(self.raw_screenshot_button, 13, 3)
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

    def _build_rng_info_group(self) -> QGroupBox:
        group = QGroupBox("乱数信息")
        layout = QGridLayout(group)
        self.lead_label = QLabel("队首")
        self.lead_combo = QComboBox()
        self.lead_combo.addItem("无", int(Lead.NONE))
        self.lead_combo.addItem("同步：勤奋", int(Lead.SYNCHRONIZE_START))
        self.lead_combo.addItem("迷人之躯 ♀", int(Lead.CUTE_CHARM_F))
        self.lead_combo.addItem("迷人之躯 ♂", int(Lead.CUTE_CHARM_M))
        self.bdsp_seed64_inputs = [QLineEdit(text) for text in ("123456789ABCDEF0", "1111111122222222")]
        for input_box in self.bdsp_seed64_inputs:
            input_box.setMaxLength(16)
            input_box.editingFinished.connect(self._sync_state32_from_bdsp_seed64)
        self.initial_advances = self._spin(0, 10_000_000, 0)
        self.max_advances = self._spin(0, 1_000_000_000, 100_000)
        self.offset = self._spin(0, 1_000_000, 0)
        self.generate_button = QPushButton("生成")
        self.generate_button.clicked.connect(self.generate_results)

        layout.addWidget(self.lead_label, 0, 0)
        layout.addWidget(self.lead_combo, 0, 1)
        layout.addWidget(QLabel("Seed 0"), 1, 0)
        layout.addWidget(self.bdsp_seed64_inputs[0], 1, 1)
        layout.addWidget(QLabel("Seed 1"), 2, 0)
        layout.addWidget(self.bdsp_seed64_inputs[1], 2, 1)
        layout.addWidget(QLabel("初始帧"), 3, 0)
        layout.addWidget(self.initial_advances, 3, 1)
        layout.addWidget(QLabel("最大帧数"), 4, 0)
        layout.addWidget(self.max_advances, 4, 1)
        layout.addWidget(QLabel("Offset"), 5, 0)
        layout.addWidget(self.offset, 5, 1)
        layout.addWidget(self.generate_button, 6, 0, 1, 2)
        group.setMinimumWidth(260)
        return group

    def _build_static_group(self) -> QGroupBox:
        group = QGroupBox()
        layout = QGridLayout(group)
        self.category_combo = QComboBox()
        self.category_combo.addItem("御三家", StaticEncounterCategory.STARTERS.value)
        self.category_combo.addItem("全部", None)
        for category in StaticEncounterCategory:
            if category == StaticEncounterCategory.STARTERS:
                continue
            self.category_combo.addItem(CATEGORY_LABELS_ZH.get(category.value, category.value), category.value)
        self.category_combo.currentIndexChanged.connect(self._refresh_encounters)
        self.encounter_combo = QComboBox()
        self.encounter_combo.currentIndexChanged.connect(self._update_encounter_details)
        self.level_display = self._spin(1, 100, 1)
        self.level_display.setEnabled(False)
        self.template_ability_display = QComboBox()
        self.template_ability_display.addItems(["0", "1", "隐藏", "0/1", "任意"])
        self.template_ability_display.setEnabled(False)
        self.template_shiny_display = QComboBox()
        self.template_shiny_display.addItems(["随机", "锁闪"])
        self.template_shiny_display.setEnabled(False)
        self.iv_count_display = self._spin(0, 6, 0)
        self.iv_count_display.setEnabled(False)

        rows = (
            ("分类", self.category_combo),
            ("宝可梦", self.encounter_combo),
            ("等级", self.level_display),
            ("特性", self.template_ability_display),
            ("异色", self.template_shiny_display),
            ("IV Count", self.iv_count_display),
        )
        for row, (label, widget) in enumerate(rows):
            layout.addWidget(QLabel(label), row, 0)
            layout.addWidget(widget, row, 1)
        group.setMinimumWidth(300)
        return group

    def _build_profile_group(self) -> QGroupBox:
        group = QGroupBox("存档信息")
        layout = QGridLayout(group)
        self.profile_name = QLineEdit("-")
        self.profile_name.setPlaceholderText("存档信息")
        self.tid = self._spin(0, 65535, 12345)
        self.sid = self._spin(0, 65535, 54321)
        self.tsv = QLineEdit()
        self.tsv.setReadOnly(True)
        self.national_dex = QCheckBox("全国图鉴")
        self.shiny_charm = QCheckBox("闪耀护符")
        self.oval_charm = QCheckBox("圆形护符")
        self.tid.valueChanged.connect(self._update_tsv)
        self.sid.valueChanged.connect(self._update_tsv)
        self._update_tsv()

        self.profile_manager_button = QPushButton("存档信息管理")
        self.profile_manager_button.clicked.connect(self.open_profile_manager)
        self.profile_game_value = QLabel(self._game_label(self._profile_version))
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.VLine)
        divider.setFrameShadow(QFrame.Shadow.Sunken)

        layout.addWidget(QLabel("存档信息"), 0, 0)
        layout.addWidget(self.profile_name, 0, 1)
        layout.addWidget(self.profile_manager_button, 1, 1)
        layout.addWidget(QLabel("TID"), 0, 2)
        layout.addWidget(self.tid, 0, 3)
        layout.addWidget(QLabel("SID"), 1, 2)
        layout.addWidget(self.sid, 1, 3)
        layout.addWidget(QLabel("TSV"), 2, 2)
        layout.addWidget(self.tsv, 2, 3)
        layout.addWidget(divider, 0, 4, 3, 1)
        layout.addWidget(QLabel("游戏"), 0, 5)
        layout.addWidget(self.profile_game_value, 0, 6)
        layout.addWidget(self.national_dex, 1, 5)
        layout.addWidget(self.shiny_charm, 1, 6)
        layout.addWidget(self.oval_charm, 2, 5)
        layout.setColumnStretch(6, 1)
        return group

    def _build_filter_group(self) -> QGroupBox:
        group = QGroupBox()
        layout = QGridLayout(group)
        self.shiny_filter = QComboBox()
        self.shiny_filter.addItem("任意", "any")
        self.shiny_filter.addItem("异色", "shiny")
        self.shiny_filter.addItem("Star", "star")
        self.shiny_filter.addItem("Square", "square")
        self.shiny_filter.addItem("非异色", "none")
        self.ability_filter = QComboBox()
        self.ability_filter.addItem("任意", 255)
        self.ability_filter.addItem("0", 0)
        self.ability_filter.addItem("1", 1)
        self.ability_filter.addItem("隐藏", 2)
        self.gender_filter = QComboBox()
        self.gender_filter.addItem("任意", 255)
        self.gender_filter.addItem("雄性", 0)
        self.gender_filter.addItem("雌性", 1)
        self.gender_filter.addItem("无性别", 2)
        self.nature_combo = QComboBox()
        self.nature_combo.addItem("任意", -1)
        for index, nature in enumerate(NATURES_ZH):
            self.nature_combo.addItem(nature, index)
        self.height_min = self._spin(0, 255, 0)
        self.height_max = self._spin(0, 255, 255)
        self.weight_min = self._spin(0, 255, 0)
        self.weight_max = self._spin(0, 255, 255)
        self.skip_filter = QCheckBox("取消筛选")
        self.show_stats_check = QCheckBox("显示能力值")
        self.show_stats_check.stateChanged.connect(lambda _state: self._refresh_result_columns())
        self.iv_calculator_button = QPushButton("个体值计算器")
        self.iv_calculator_button.clicked.connect(self.open_iv_calculator)

        self.nature_list = QListWidget()
        self.nature_list.setVisible(False)
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
        self.all_natures_button.setVisible(False)
        self.clear_natures_button.setVisible(False)

        iv_grid = QGridLayout()
        self.iv_min: list[QSpinBox] = []
        self.iv_max: list[QSpinBox] = []
        iv_labels = ("HP", "攻击", "防御", "特攻", "特防", "速度")
        for row, label in enumerate(iv_labels):
            min_spin = self._spin(0, 31, 0)
            max_spin = self._spin(0, 31, 31)
            self.iv_min.append(min_spin)
            self.iv_max.append(max_spin)
            iv_grid.addWidget(QLabel(label), row, 0)
            iv_grid.addWidget(min_spin, row, 1)
            iv_grid.addWidget(max_spin, row, 2)
        left = QWidget()
        left.setLayout(iv_grid)
        layout.addWidget(left, 0, 0, 6, 3)
        layout.addWidget(QLabel("特性"), 0, 4)
        layout.addWidget(self.ability_filter, 0, 5, 1, 2)
        layout.addWidget(QLabel("性别"), 1, 4)
        layout.addWidget(self.gender_filter, 1, 5, 1, 2)
        layout.addWidget(QLabel("Height"), 2, 4)
        layout.addWidget(self.height_min, 2, 5)
        layout.addWidget(self.height_max, 2, 6)
        layout.addWidget(QLabel("性格"), 3, 4)
        layout.addWidget(self.nature_combo, 3, 5, 1, 2)
        layout.addWidget(QLabel("异色"), 4, 4)
        layout.addWidget(self.shiny_filter, 4, 5, 1, 2)
        layout.addWidget(QLabel("Weight"), 5, 4)
        layout.addWidget(self.weight_min, 5, 5)
        layout.addWidget(self.weight_max, 5, 6)
        layout.addWidget(self.show_stats_check, 6, 0, 1, 3)
        layout.addWidget(self.iv_calculator_button, 7, 0, 1, 3)
        layout.addWidget(self.skip_filter, 6, 4, 1, 3)
        layout.setColumnStretch(3, 1)
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
        self.preview_label = RoiPreviewLabel()
        self.preview_label.roiSelected.connect(self._handle_preview_selection)
        self.preview_label.setObjectName("Preview")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumSize(480, 360)
        self.preview_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.preview_label.setScaledContents(False)
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

        self.table = PokeFinderTableWidget()
        self.table.setColumnCount(len(self._result_headers()))
        self.table.setHorizontalHeaderLabels(self._result_headers())
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_result_context_menu)
        self.table.searchStatusChanged.connect(self.statusBar().showMessage)
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
                background: #f2f1ee;
                color: #1a1a1a;
                font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI";
                font-size: 12px;
            }
            QFrame#Header {
                background: #ffffff;
                border: 1px solid #c8c6c0;
                border-radius: 4px;
            }
            QLabel#WindowTitle {
                font-size: 20px;
                font-weight: 700;
                color: #1a1a1a;
            }
            QLabel#Badge, QLabel#ResultCount {
                color: #23936b;
                font-weight: 600;
            }
            QTabWidget::pane {
                border: 1px solid #c8c6c0;
                border-radius: 4px;
                top: -1px;
                background: #f2f1ee;
            }
            QTabBar::tab {
                background: #e8e6e1;
                border: 1px solid #c8c6c0;
                color: #555;
                min-width: 150px;
                padding: 8px 18px;
                font-weight: 700;
            }
            QTabBar::tab:selected {
                background: #ffffff;
                color: #1a1a1a;
                border-bottom-color: #ffffff;
            }
            QTabBar::tab:hover:!selected {
                background: #ddd9d2;
                color: #1a1a1a;
            }
            QLabel#Preview {
                background: #e8e6e1;
                border: 1px solid #c8c6c0;
                border-radius: 4px;
                color: #767676;
            }
            QGroupBox {
                border: 1px solid #c8c6c0;
                border-radius: 6px;
                margin-top: 9px;
                padding: 10px 8px 8px 8px;
                background: #ffffff;
                font-weight: 700;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 9px;
                padding: 0 4px;
                color: #23936b;
            }
            QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QListWidget {
                background: #ffffff;
                border: 1px solid #c8c6c0;
                border-radius: 4px;
                min-height: 24px;
                padding: 2px 6px;
                color: #1a1a1a;
                selection-background-color: #c8e0d0;
            }
            QPlainTextEdit {
                background: #ffffff;
                border: 1px solid #c8c6c0;
                border-radius: 4px;
                color: #1a1a1a;
                font-family: "Cascadia Mono", "Consolas", "Microsoft YaHei UI";
                font-size: 12px;
                padding: 10px;
                selection-background-color: #c8e0d0;
            }
            QTextEdit#EasyConLog {
                background: #282826;
                border: 1px solid #c8c6c0;
                border-radius: 0;
                color: #e7ece9;
                font-family: "Cascadia Mono", "Consolas", "Microsoft YaHei UI";
                font-size: 11px;
                padding: 8px;
            }
            QFrame#EasyConToolbar {
                background: #ffffff;
                border: 1px solid #c8c6c0;
                border-radius: 4px;
            }
            QStatusBar {
                background: #e8e6e1;
                border-top: 1px solid #c8c6c0;
                color: #767676;
            }
            QLineEdit#Readonly {
                color: #23936b;
                background: #e8e6e1;
            }
            QPushButton {
                background: #ffffff;
                border: 1px solid #c8c6c0;
                border-radius: 4px;
                min-height: 26px;
                padding: 4px 10px;
                font-weight: 600;
                color: #1a1a1a;
            }
            QPushButton:hover {
                background: #e8e6e1;
                border-color: #aaa;
            }
            QPushButton#PrimaryButton {
                background: #23936b;
                color: #ffffff;
                border-color: #23936b;
            }
            QPushButton#PrimaryButton:hover {
                background: #1e7d5a;
            }
            QTableWidget {
                background: #ffffff;
                alternate-background-color: #f5f4f0;
                border: 1px solid #c8c6c0;
                gridline-color: #e0ded8;
                color: #1a1a1a;
            }
            QTableWidget::item:selected {
                background: #23936b;
                color: #ffffff;
            }
            QHeaderView::section {
                background: #e8e6e1;
                color: #1a1a1a;
                border: 0;
                border-right: 1px solid #c8c6c0;
                border-bottom: 1px solid #c8c6c0;
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
        return TEXT[self.lang].get(key, TEXT["en"].get(key, key))

    def _base_result_headers(self) -> tuple[str, ...]:
        return RESULT_HEADERS_ZH if self.lang == "zh" else RESULT_HEADERS

    def _result_headers(self) -> list[str]:
        headers = list(self._base_result_headers())
        if hasattr(self, "show_stats_check") and self.show_stats_check.isChecked():
            stat_headers = STAT_LABELS_ZH if self.lang == "zh" else tuple(f"{label} Stat" for label in IV_LABELS)
            headers[7:13] = stat_headers
        return headers

    def _refresh_result_columns(self) -> None:
        if not hasattr(self, "table"):
            return
        self.table.setColumnCount(len(self._result_headers()))
        self.table.setHorizontalHeaderLabels(self._result_headers())
        if self._states:
            self._populate_table(self._states)

    def _game_label(self, version: GameVersion) -> str:
        labels = GAME_LABELS_ZH if self.lang == "zh" else GAME_LABELS_EN
        return labels.get(version, str(version))

    def _set_profile_version(self, version: GameVersion) -> None:
        self._profile_version = version
        self.profile_game_value.setText(self._game_label(version))
        self._refresh_encounters()

    def open_profile_manager(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("存档信息管理" if self.lang == "zh" else "Profile Manager")
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        name = QLineEdit(self.profile_name.text())
        tid = self._spin(0, 65535, self.tid.value())
        sid = self._spin(0, 65535, self.sid.value())
        version = QComboBox()
        for game_version in (GameVersion.BD, GameVersion.SP):
            version.addItem(self._game_label(game_version), game_version.value)
        version.setCurrentIndex(max(0, version.findData(self._profile_version.value)))
        national_dex = QCheckBox("全国图鉴" if self.lang == "zh" else "National Dex")
        shiny_charm = QCheckBox("闪耀护符" if self.lang == "zh" else "Shiny Charm")
        oval_charm = QCheckBox("圆形护符" if self.lang == "zh" else "Oval Charm")
        national_dex.setChecked(self.national_dex.isChecked())
        shiny_charm.setChecked(self.shiny_charm.isChecked())
        oval_charm.setChecked(self.oval_charm.isChecked())
        form.addRow("存档信息" if self.lang == "zh" else "Profile", name)
        form.addRow("TID", tid)
        form.addRow("SID", sid)
        form.addRow("游戏" if self.lang == "zh" else "Game", version)
        form.addRow("", national_dex)
        form.addRow("", shiny_charm)
        form.addRow("", oval_charm)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.profile_name.setText(name.text() or "-")
        self.tid.setValue(tid.value())
        self.sid.setValue(sid.value())
        self.national_dex.setChecked(national_dex.isChecked())
        self.shiny_charm.setChecked(shiny_charm.isChecked())
        self.oval_charm.setChecked(oval_charm.isChecked())
        self._set_profile_version(GameVersion(version.currentData()))
        self.statusBar().showMessage("存档信息已应用" if self.lang == "zh" else "Profile applied")

    def _change_language(self) -> None:
        self.lang = self.language_combo.currentData()
        self._apply_language()

    def _apply_language(self) -> None:
        self.title_label.setText(self._text("title"))
        self.language_label.setText(self._text("language"))
        self.tabs.setTabText(0, self._text("project_xs"))
        self.tabs.setTabText(1, self._text("bdsp_search"))
        self.tabs.setTabText(2, self._text("easycon"))
        self.status_group.setTitle(self._text("status"))
        self.capture_group.setTitle(self._text("capture"))
        self.seed_group.setTitle(self._text("seed"))
        self.rng_info_group.setTitle("乱数信息" if self.lang == "zh" else "RNG Info")
        self.static_group.setTitle("设置" if self.lang == "zh" else "Settings")
        self.profile_group.setTitle("存档信息" if self.lang == "zh" else "Profile")
        self.filter_group.setTitle("筛选项" if self.lang == "zh" else "Filters")
        self.profile_manager_button.setText("存档信息管理" if self.lang == "zh" else "Profile Manager")
        self.profile_game_value.setText(self._game_label(self._profile_version))
        self.national_dex.setText("全国图鉴" if self.lang == "zh" else "National Dex")
        self.shiny_charm.setText("闪耀护符" if self.lang == "zh" else "Shiny Charm")
        self.oval_charm.setText("圆形护符" if self.lang == "zh" else "Oval Charm")
        self.preview_group.setTitle(self._text("preview"))
        self.config_label.setText(self._text("config"))
        self.browse_button.setText(self._text("browse"))
        self.monitor_window.setText(self._text("monitor_window"))
        self.capture_button.setText(self._text("stop_capture") if self._is_capturing() else self._text("capture_seed"))
        self.reidentify_button.setText(self._text("reidentify_seed"))
        self.preview_button.setText(self._text("stop_preview") if self._preview_timer.isActive() else self._text("preview_button"))
        self.save_config_button.setText(self._text("save_config"))
        self.raw_screenshot_button.setText(self._text("raw_screenshot"))
        self.select_roi_button.setText(self._text("select_roi"))
        self.generate_button.setText(self._text("generate"))
        self.copy_button.setText(self._text("copy"))
        self.export_button.setText(self._text("export"))
        self.show_stats_check.setText("显示能力值" if self.lang == "zh" else "Show Stats")
        self.iv_calculator_button.setText("个体值计算器" if self.lang == "zh" else "IV Calculator")
        self._refresh_result_columns()
        self.seed_badge.setText(self._text("seed_linked"))
        if not self._preview_timer.isActive():
            self.preview_label.clear()
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
        self._eye_image_path = config.capture.eye_image_path

    def _config_from_form(self) -> ProjectXsTrackingConfig:
        loaded = load_project_xs_config(self._selected_config_path(), blink_count=DEFAULT_BLINK_COUNT)
        capture = BlinkCaptureConfig(
            eye_image_path=self._eye_image_path or loaded.capture.eye_image_path,
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
        if self._is_capturing():
            return
        if self._preview_timer.isActive():
            self._preview_timer.stop()
            self.preview_button.setText(self._text("preview_button"))
            self.preview_label.set_selection_enabled(False)
            self.preview_label.clear()
            self.preview_label.setText(self._text("no_preview"))
            return
        self._preview_timer.start()
        self.preview_button.setText(self._text("stop_preview"))
        self.statusBar().showMessage(self._text("preview_running"))

    def start_roi_selection(self) -> None:
        if self._is_capturing():
            return
        self._roi_before_selection = (self.x.value(), self.y.value(), self.w.value(), self.h.value())
        self._begin_preview_selection("roi")
        self.statusBar().showMessage(self._text("roi_selecting"))

    def start_eye_capture_selection(self) -> None:
        if self._is_capturing():
            return
        self._begin_preview_selection("eye")
        self.statusBar().showMessage(self._text("eye_selecting"))

    def _begin_preview_selection(self, mode: str) -> None:
        self._selection_mode = mode
        self._resume_preview_after_selection = self._preview_timer.isActive()
        if self._preview_timer.isActive():
            self._preview_timer.stop()
            self.preview_button.setText(self._text("preview_button"))
        if self._latest_preview_frame is None:
            try:
                frame = capture_preview_frame(self._config_from_form().capture)
                frame_copy = getattr(frame, "copy", None)
                self._latest_preview_frame = frame_copy() if callable(frame_copy) else frame
            except Exception as exc:
                self._selection_mode = None
                self._show_error("Preview failed", exc if isinstance(exc, Exception) else Exception(str(exc)))
                return
        self._display_frame(self._latest_preview_frame)
        self.preview_label.set_selection_enabled(True)

    def _handle_preview_selection(self, roi: object) -> None:
        if self._selection_mode == "eye":
            self.apply_selected_eye(roi)
        else:
            self.apply_selected_roi(roi)

    def apply_selected_roi(self, roi: object) -> None:
        old_roi = self._roi_before_selection or (self.x.value(), self.y.value(), self.w.value(), self.h.value())
        x, y, width, height = (int(value) for value in roi)  # type: ignore[union-attr]
        try:
            import cv2

            config = self._config_from_form().capture
            eye_image = cv2.imread(str(config.eye_image_path), cv2.IMREAD_GRAYSCALE)
            if eye_image is None:
                raise ProjectXsIntegrationError(f"Cannot read eye template image: {config.eye_image_path}")
            eye_width, eye_height = eye_image.shape[::-1]
            if width < eye_width or height < eye_height:
                raise ValueError(self._text("roi_too_small"))
        except Exception as exc:
            self._set_roi_values(old_roi)
            self.preview_label.set_selection_enabled(False)
            self._selection_mode = None
            self._show_error("ROI failed", exc if isinstance(exc, Exception) else Exception(str(exc)))
            return
        self._set_roi_values((x, y, width, height))
        self.preview_label.set_selection_enabled(False)
        self._roi_before_selection = None
        self._selection_mode = None
        if self._resume_preview_after_selection:
            self._preview_timer.start()
            self.preview_button.setText(self._text("stop_preview"))
        if self._latest_preview_frame is not None:
            try:
                annotated, _preview = render_eye_preview(self._config_from_form().capture, self._latest_preview_frame)
                self._display_frame(annotated)
            except Exception:
                pass
        self.statusBar().showMessage(f"{self._text('roi_selected')}: {x}, {y}, {width}, {height}")

    def apply_selected_eye(self, roi: object) -> None:
        x, y, width, height = (int(value) for value in roi)  # type: ignore[union-attr]
        try:
            import cv2

            frame = self._latest_preview_frame
            if frame is None:
                frame = capture_preview_frame(self._config_from_form().capture)
            frame_height, frame_width = frame.shape[:2]
            left = max(0, min(x, frame_width - 1))
            top = max(0, min(y, frame_height - 1))
            right = max(left + 1, min(x + width, frame_width))
            bottom = max(top + 1, min(y + height, frame_height))
            eye = frame[top:bottom, left:right]
            if len(eye.shape) == 3:
                eye = cv2.cvtColor(eye, cv2.COLOR_BGR2GRAY)
            output_dir = PROJECT_ROOT / "third_party" / "Project_Xs_CHN" / "images" / "custom"
            output_dir.mkdir(parents=True, exist_ok=True)
            config_name = Path(self._selected_config_path()).stem or "current"
            output_path = output_dir / f"{config_name}_eye.png"
            if not cv2.imwrite(str(output_path), eye):
                raise ProjectXsIntegrationError(f"Cannot save eye template: {output_path}")
        except Exception as exc:
            self.preview_label.set_selection_enabled(False)
            self._selection_mode = None
            self._show_error("Eye capture failed", exc if isinstance(exc, Exception) else Exception(str(exc)))
            return
        self._eye_image_path = output_path
        self._selection_mode = "roi"
        self._roi_before_selection = (self.x.value(), self.y.value(), self.w.value(), self.h.value())
        self._display_frame(self._latest_preview_frame if self._latest_preview_frame is not None else frame)
        self.preview_label.set_selection_enabled(True)
        self.statusBar().showMessage(f"{self._text('eye_captured_select_roi')}: {output_path}")

    def _set_roi_values(self, roi: tuple[int, int, int, int]) -> None:
        self.x.setValue(roi[0])
        self.y.setValue(roi[1])
        self.w.setValue(roi[2])
        self.h.setValue(roi[3])

    def _update_preview_frame(self) -> None:
        try:
            config = self._config_from_form().capture
            frame = capture_preview_frame(config)
            frame_copy = getattr(frame, "copy", None)
            self._latest_preview_frame = frame_copy() if callable(frame_copy) else frame
            annotated, preview = render_eye_preview(config, frame)
        except Exception as exc:
            self._preview_timer.stop()
            self.preview_button.setText(self._text("preview_button"))
            self._show_error("Preview failed", exc if isinstance(exc, Exception) else Exception(str(exc)))
            return
        self._display_frame(annotated)
        self.statusBar().showMessage(f"{self._text('preview_running')} | score {preview.match_score:.3f}")

    def _display_frame(self, frame: object) -> None:
        pixmap = self._frame_to_pixmap(frame)
        target = self.preview_label.contentsRect().size()
        if target.width() <= 0 or target.height() <= 0:
            return
        scaled = pixmap.scaled(target, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        contents = self.preview_label.contentsRect()
        left = contents.left() + (contents.width() - scaled.width()) // 2
        top = contents.top() + (contents.height() - scaled.height()) // 2
        self.preview_label.set_image_geometry(
            pixmap.width(),
            pixmap.height(),
            QRect(left, top, scaled.width(), scaled.height()),
        )
        self.preview_label.setPixmap(scaled)

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
        version = self._profile_version.value
        category = self.category_combo.currentData() if hasattr(self, "category_combo") else None
        try:
            self._records = get_static_encounters(category, version)
        except Exception as exc:  # pragma: no cover - defensive UI boundary
            self._show_error("Unable to load encounters", exc)
            self._records = ()
        self.encounter_combo.clear()
        for record in self._records:
            suffix = " roamer" if record.template.roamer else ""
            name = POKEMON_LABELS_ZH.get(record.description, record.description) if self.lang == "zh" else record.description
            category_text = CATEGORY_LABELS_ZH.get(record.category.value, record.category.value) if self.lang == "zh" else record.category.value
            roamer_text = " 游走" if self.lang == "zh" and record.template.roamer else suffix
            self.encounter_combo.addItem(f"{name} [{category_text}]{roamer_text}", record)
        self._update_encounter_details()

    def _update_encounter_details(self) -> None:
        if not hasattr(self, "encounter_combo"):
            return
        record = self.encounter_combo.currentData()
        if record is None:
            return
        template = record.template
        if hasattr(self, "level_display"):
            self.level_display.setValue(template.level)
        if hasattr(self, "iv_count_display"):
            self.iv_count_display.setValue(template.iv_count)
        if hasattr(self, "template_ability_display"):
            ability_text = {0: "0", 1: "1", 2: "隐藏", 255: "0/1"}.get(template.ability, "任意")
            index = self.template_ability_display.findText(ability_text)
            self.template_ability_display.setCurrentIndex(max(0, index))
        if hasattr(self, "template_shiny_display"):
            self.template_shiny_display.setCurrentText("锁闪" if template.shiny == Shiny.NEVER else "随机")

    def _update_tsv(self) -> None:
        self.tsv.setText(str(self.tid.value() ^ self.sid.value()))

    def _set_all_natures(self, state: Qt.CheckState) -> None:
        for row in range(self.nature_list.count()):
            self.nature_list.item(row).setCheckState(state)

    def _ability_text(self, state: State8) -> str:
        if self.lang != "zh" or self._active_record is None:
            return str(state.ability)
        abilities = self._active_record.species_info.abilities
        slot = state.ability
        ability_id = abilities[slot] if 0 <= slot < len(abilities) and abilities[slot] else abilities[0]
        name = ABILITY_NAMES_ZH.get(ability_id)
        return f"{slot}: {name}" if name else str(slot)

    def _characteristic_text(self, state: State8) -> str:
        max_iv = max(state.ivs)
        start = state.pid % 6
        stat_index = next(index for offset in range(6) for index in ((start + offset) % 6,) if state.ivs[index] == max_iv)
        characteristic_index = max_iv % 5
        if self.lang == "zh":
            return CHARACTERISTICS_ZH[stat_index][characteristic_index]
        return f"{IV_LABELS[stat_index]} {max_iv}"

    def _stat_values(self, state: State8) -> tuple[int, int, int, int, int, int]:
        if self._active_record is None:
            return (0, 0, 0, 0, 0, 0)
        stats = self._active_record.species_info.stats
        level = state.level
        hp = ((2 * stats[0] + state.ivs[0]) * level) // 100 + level + 10
        values = []
        increased, decreased = NATURE_MODIFIERS[state.nature]
        for index in range(1, 6):
            value = ((2 * stats[index] + state.ivs[index]) * level) // 100 + 5
            if index == increased:
                value = (value * 110) // 100
            elif index == decreased:
                value = (value * 90) // 100
            values.append(value)
        return (hp, *values)

    def open_iv_calculator(self) -> None:
        row = self.table.currentRow() if hasattr(self, "table") else -1
        state = self._states[row] if 0 <= row < len(self._states) else None
        dialog = QDialog(self)
        dialog.setWindowTitle("个体值计算器" if self.lang == "zh" else "IV Calculator")
        layout = QGridLayout(dialog)
        labels = ("HP", "攻击", "防御", "特攻", "特防", "速度") if self.lang == "zh" else IV_LABELS
        ivs = state.ivs if state is not None else tuple(spin.value() for spin in self.iv_min)
        for index, (label, value) in enumerate(zip(labels, ivs)):
            layout.addWidget(QLabel(label), index, 0)
            spin = self._spin(0, 31, value)
            layout.addWidget(spin, index, 1)
        if state is not None:
            layout.addWidget(QLabel("个性" if self.lang == "zh" else "Characteristic"), 6, 0)
            layout.addWidget(QLabel(self._characteristic_text(state)), 6, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons, 7, 0, 1, 2)
        dialog.exec()

    def _sync_seed64_from_state32(self) -> None:
        try:
            state = SeedState32.from_hex_words([box.text() for box in self.seed32_inputs])
        except ValueError as exc:
            self.seed_badge.setText(str(exc))
            return
        seed64_pair = state.format_seed64_pair()
        for output, text in zip(self.seed64_outputs, seed64_pair):
            output.setText(text)
        if hasattr(self, "bdsp_seed64_inputs"):
            for output, text in zip(self.bdsp_seed64_inputs, seed64_pair):
                output.setText(text)
        self.seed_badge.setText(self._text("seed_linked"))
        self._auto_refresh_results()

    def _current_seed_pair(self) -> SeedPair64:
        if hasattr(self, "bdsp_seed64_inputs"):
            return SeedPair64.from_hex_words([box.text() for box in self.bdsp_seed64_inputs])
        state = SeedState32.from_hex_words([box.text() for box in self.seed32_inputs])
        return state.to_seed_pair64()

    def _sync_state32_from_bdsp_seed64(self) -> None:
        try:
            seed_pair = SeedPair64.from_hex_words([box.text() for box in self.bdsp_seed64_inputs])
        except ValueError as exc:
            self.seed_badge.setText(str(exc))
            return
        state = seed_pair.to_state32()
        for input_box, text in zip(self.seed32_inputs, state.format_words()):
            input_box.setText(text)
        for output, text in zip(self.seed64_outputs, seed_pair.format_seeds()):
            output.setText(text)
        self.seed_badge.setText(self._text("seed_linked"))
        self._auto_refresh_results()

    def _auto_refresh_results(self) -> None:
        if getattr(self, "_states", None):
            self.generate_results()

    def _current_profile(self) -> Profile8:
        return Profile8(
            name=self.profile_name.text() or "-",
            version=self._profile_version.value,
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
        nature_index = self.nature_combo.currentData() if hasattr(self, "nature_combo") else -1
        if nature_index == -1:
            natures = (True,) * len(NATURES)
        else:
            natures = tuple(index == nature_index for index in range(len(NATURES)))
        return (
            StateFilter.from_iv_ranges(
                [spin.value() for spin in self.iv_min],
                [spin.value() for spin in self.iv_max],
                ability=self.ability_filter.currentData(),
                gender=self.gender_filter.currentData(),
                shiny=shiny_value,
                height_min=self.height_min.value() if hasattr(self, "height_min") else 0,
                height_max=self.height_max.value() if hasattr(self, "height_max") else 255,
                weight_min=self.weight_min.value() if hasattr(self, "weight_min") else 0,
                weight_max=self.weight_max.value() if hasattr(self, "weight_max") else 255,
                skip=self.skip_filter.isChecked() if hasattr(self, "skip_filter") else False,
                natures=natures,
            ),
            shiny_mode,
        )

    def _is_capturing(self) -> bool:
        return self._capture_thread is not None and self._capture_thread.is_alive()

    def _stop_advance_tracking(self) -> None:
        self._advance_timer.stop()
        self._tracked_advances = 0
        self.advances_value.setText("0")
        self.timer_value.setText("0")

    def _advance_tick(self) -> None:
        self._tracked_advances += self._advance_step
        self.advances_value.setText(str(self._tracked_advances))

    def advance_current_seed(self) -> None:
        advances = self.x_to_advance.value()
        if advances <= 0:
            return
        try:
            state = SeedState32.from_hex_words([box.text() for box in self.seed32_inputs])
            advanced = advance_seed_state(state, advances).state
        except Exception as exc:
            self._show_error("Advance failed", exc if isinstance(exc, Exception) else Exception(str(exc)))
            return
        for box, text in zip(self.seed32_inputs, advanced.format_words()):
            box.setText(text)
        self._sync_seed64_from_state32()
        self._tracked_advances += advances
        self.advances_value.setText(str(self._tracked_advances))

    def capture_seed(self) -> None:
        if self._is_capturing():
            self._capture_cancel.set()
            self.capture_button.setText(self._text("stop_capture"))
            self.statusBar().showMessage(self._text("capture_stopping"))
            return
        try:
            config = self._config_from_form()
        except ProjectXsIntegrationError as exc:
            self._show_error("Blink capture failed", exc)
            return

        if self._preview_timer.isActive():
            self._preview_timer.stop()
            self.preview_button.setText(self._text("preview_button"))
        self.preview_button.setEnabled(False)
        self.preview_label.set_selection_enabled(False)
        self._stop_advance_tracking()
        self._capture_cancel.clear()
        self._capture_result = None
        self._capture_error = None
        self._capture_mode = "seed"
        self._capture_progress = (0, DEFAULT_BLINK_COUNT)
        with self._capture_lock:
            self._capture_frame = None
        self.progress_value.setText(f"0/{DEFAULT_BLINK_COUNT}")
        self.capture_button.setText(self._text("stop_capture"))
        self.reidentify_button.setEnabled(False)
        self.statusBar().showMessage(self._text("capturing"))

        last_display_frame_at = 0.0

        def store_frame(frame: object) -> None:
            nonlocal last_display_frame_at
            now = time.perf_counter()
            if now - last_display_frame_at < 0.1:
                return
            last_display_frame_at = now
            with self._capture_lock:
                copy = getattr(frame, "copy", None)
                self._capture_frame = copy() if callable(copy) else frame

        def store_progress(done: int, total: int) -> None:
            with self._capture_lock:
                self._capture_progress = (done, total)

        def run_capture() -> None:
            try:
                observation = capture_player_blinks(
                    config.capture,
                    should_stop=self._capture_cancel.is_set,
                    frame_callback=store_frame,
                    progress_callback=store_progress,
                    show_window=False,
                )
                result = recover_seed_from_observation(observation, npc=config.npc)
                elapsed_seconds = max(0, round(time.perf_counter() - observation.offset_time))
                elapsed_advances = elapsed_seconds * (config.npc + 1)
                if elapsed_advances:
                    result = replace(result, state=advance_seed_state(result.state, elapsed_advances).state)
                self._capture_result = result
            except Exception as exc:  # pragma: no cover - exercised through UI polling
                self._capture_error = exc if isinstance(exc, Exception) else Exception(str(exc))

        self._capture_thread = threading.Thread(target=run_capture, daemon=True)
        self._capture_thread.start()
        self._capture_timer.start()

    def reidentify_seed(self) -> None:
        if self._is_capturing():
            self._capture_cancel.set()
            self.capture_button.setText(self._text("stop_capture"))
            self.statusBar().showMessage(self._text("capture_stopping"))
            return
        try:
            config = self._config_from_form()
            current_state = SeedState32.from_hex_words([box.text() for box in self.seed32_inputs])
        except Exception as exc:
            self._show_error("Reidentify failed", exc if isinstance(exc, Exception) else Exception(str(exc)))
            return

        if self._preview_timer.isActive():
            self._preview_timer.stop()
            self.preview_button.setText(self._text("preview_button"))
        self.preview_button.setEnabled(False)
        self.reidentify_button.setEnabled(False)
        self.preview_label.set_selection_enabled(False)
        self._stop_advance_tracking()
        self._capture_cancel.clear()
        self._capture_result = None
        self._capture_error = None
        self._capture_mode = "reidentify"
        self._capture_progress = (0, DEFAULT_BLINK_COUNT)
        with self._capture_lock:
            self._capture_frame = None
        self.progress_value.setText(f"0/{DEFAULT_BLINK_COUNT}")
        self.capture_button.setText(self._text("stop_capture"))
        self.statusBar().showMessage(self._text("capturing"))

        last_display_frame_at = 0.0

        def store_frame(frame: object) -> None:
            nonlocal last_display_frame_at
            now = time.perf_counter()
            if now - last_display_frame_at < 0.1:
                return
            last_display_frame_at = now
            with self._capture_lock:
                copy = getattr(frame, "copy", None)
                self._capture_frame = copy() if callable(copy) else frame

        def store_progress(done: int, total: int) -> None:
            with self._capture_lock:
                self._capture_progress = (done, total)

        def run_reidentify() -> None:
            try:
                observation = capture_player_blinks(
                    config.capture,
                    should_stop=self._capture_cancel.is_set,
                    frame_callback=store_frame,
                    progress_callback=store_progress,
                    show_window=False,
                )
                self._capture_result = reidentify_seed_from_observation(
                    current_state,
                    observation,
                    npc=config.npc,
                    search_min=0,
                    search_max=max(100_000, self.max_advances.value() if hasattr(self, "max_advances") else 100_000),
                )
            except Exception as exc:  # pragma: no cover - exercised through UI polling
                self._capture_error = exc if isinstance(exc, Exception) else Exception(str(exc))

        self._capture_thread = threading.Thread(target=run_reidentify, daemon=True)
        self._capture_thread.start()
        self._capture_timer.start()

    def _poll_capture_thread(self) -> None:
        with self._capture_lock:
            frame = self._capture_frame
            self._capture_frame = None
            done, total = self._capture_progress
        if frame is not None:
            self._display_frame(frame)
        self.progress_value.setText(f"{done}/{total}")
        if self._is_capturing():
            return

        self._capture_timer.stop()
        thread = self._capture_thread
        self._capture_thread = None
        if thread is not None:
            thread.join(timeout=0)
        self.preview_button.setEnabled(True)
        self.reidentify_button.setEnabled(True)
        self.capture_button.setText(self._text("capture_seed"))
        if self._capture_error is not None:
            if self._capture_cancel.is_set():
                self.statusBar().showMessage(self._text("capture_stopped"))
            else:
                title = "Reidentify failed" if self._capture_mode == "reidentify" else "Blink capture failed"
                self._show_error(title, self._capture_error)
            return

        result = self._capture_result
        if result is None:
            self.statusBar().showMessage(self._text("capture_stopped"))
            return
        for box, text in zip(self.seed32_inputs, result.state.format_words()):
            box.setText(text)
        self.progress_value.setText(f"{DEFAULT_BLINK_COUNT}/{DEFAULT_BLINK_COUNT}")
        self._sync_seed64_from_state32()
        self._advance_step = self.npc_count.value() + 1
        self._tracked_advances = getattr(result, "advances", 0) if self._capture_mode == "reidentify" else 0
        self.advances_value.setText("0")
        self.timer_value.setText("0")
        self._advance_timer.start()
        if self._capture_mode == "reidentify":
            self.advances_value.setText(str(self._tracked_advances))
            self.statusBar().showMessage(self._text("seed_reidentified"))
        else:
            self.statusBar().showMessage(self._text("seed_captured"))

    def generate_results(self) -> None:
        try:
            record = self.encounter_combo.currentData()
            if record is None:
                raise ValueError("Select a static encounter")
            seed = self._current_seed_pair()
            state_filter, shiny_mode = self._current_filter()
            self._active_record = record
            template = replace(record.template, version=self._profile_version.value)
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
        self.table.setColumnCount(len(self._result_headers()))
        self.table.setHorizontalHeaderLabels(self._result_headers())
        self.table.setRowCount(len(states))
        for row, state in enumerate(states):
            values = self._state_row(state)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 3 and value not in ("-", "否"):
                    item.setForeground(Qt.GlobalColor.yellow)
                self.table.setItem(row, column, item)
        self.result_count.setText(f"{len(states)} {self._text('results')}")

    def _state_row(self, state: State8) -> list[str]:
        if self.lang == "zh":
            shiny = {0: "否", 1: "星闪", 2: "方闪"}.get(state.shiny, str(state.shiny))
            gender = {0: "雄", 1: "雌", 2: "-"}.get(state.gender, str(state.gender))
            nature = NATURES_ZH[state.nature]
        else:
            shiny = {0: "-", 1: "Star", 2: "Square"}.get(state.shiny, str(state.shiny))
            gender = {0: "M", 1: "F", 2: "-"}.get(state.gender, str(state.gender))
            nature = NATURES[state.nature]
        row = [
            str(state.advances),
            f"{state.ec:08X}",
            f"{state.pid:08X}",
            shiny,
            nature,
            self._ability_text(state),
            gender,
            *(str(iv) for iv in state.ivs),
            str(state.height),
            str(state.weight),
            self._characteristic_text(state),
        ]
        if hasattr(self, "show_stats_check") and self.show_stats_check.isChecked():
            row[7:13] = [str(value) for value in self._stat_values(state)]
        return row

    def _table_text(self) -> str:
        rows = ["\t".join(self._result_headers())]
        for state in self._states:
            rows.append("\t".join(self._state_row(state)))
        return "\n".join(rows)

    def _show_result_context_menu(self, position: QPoint) -> None:
        menu = QMenu(self.table)
        copy_action = menu.addAction("复制" if self.lang == "zh" else "Copy")
        txt_action = menu.addAction("导出 TXT" if self.lang == "zh" else "Export TXT")
        csv_action = menu.addAction("导出 CSV" if self.lang == "zh" else "Export CSV")
        selected = menu.exec(self.table.viewport().mapToGlobal(position))
        if selected == copy_action:
            self.copy_results()
        elif selected == txt_action:
            self.export_results_txt()
        elif selected == csv_action:
            self.export_results()

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
            writer.writerow(self._result_headers())
            for state in self._states:
                writer.writerow(self._state_row(state))
        self.statusBar().showMessage(f"Exported {output}")

    def export_results_txt(self) -> None:
        if not self._states:
            self.statusBar().showMessage("No results to export")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export results", "bdsp_static_results.txt", "Text files (*.txt)")
        if not path:
            return
        output = Path(path)
        output.write_text(self._table_text(), encoding="utf-8")
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
