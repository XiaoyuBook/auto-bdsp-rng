from __future__ import annotations

import csv
import threading
import time
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QRect, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QColor, QGuiApplication, QImage, QIntValidator, QPainter, QPen, QPixmap
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
    ProjectXsReidentifyResult,
    ProjectXsTrackingConfig,
    advance_seed_state,
    capture_player_blinks,
    capture_preview_frame,
    load_project_xs_config,
    reidentify_seed_from_observation,
    reidentify_seed_from_observation_noisy,
    recover_seed_from_observation,
    render_eye_preview,
    save_project_xs_config,
)
from auto_bdsp_rng.automation.auto_rng import AutoRngConfig, AutoRngSeedResult
from auto_bdsp_rng.automation.auto_rng.runner import AutoRngRunner, AutoRngServices
from auto_bdsp_rng.automation.auto_rng.search import StaticSearchCriteria, generate_static_candidates
from auto_bdsp_rng.automation.easycon import EasyConStatus
from auto_bdsp_rng.data import GameVersion, StaticEncounterCategory, StaticEncounterRecord, get_static_encounters
from auto_bdsp_rng.gen8_static import Lead, Profile8, Shiny, State8, StateFilter
from auto_bdsp_rng.rng_core import SeedPair64, SeedState32
from auto_bdsp_rng.ui.auto_rng_panel import AutoRngPanel
from auto_bdsp_rng.ui.easycon_panel import EasyConPanel


PROJECT_ROOT = Path(__file__).resolve().parents[3]
PROJECT_XS_CONFIGS = PROJECT_ROOT / "third_party" / "Project_Xs_CHN" / "configs"
DEFAULT_BLINK_COUNT = 40
REIDENTIFY_BLINK_COUNT = 7
NOISY_REIDENTIFY_BLINK_COUNT = 20

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
        "bdsp_search": "定点数据区",
        "easycon": "EasyCon",
        "auto_rng": "Auto Static RNG",
        "status": "Status",
        "config": "Config",
        "browse": "Browse",
        "monitor_window": "Monitor Window",
        "reidentify_1_pk_npc": "Reidentify 1 PK NPC",
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
        "bdsp_search": "定点数据区",
        "easycon": "伊机控",
        "auto_rng": "自动定点乱数",
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
        self.setMinimumSize(1150, 900)
        self.resize(1150, 900)
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
        self.auto_rng_tab = AutoRngPanel()
        self.auto_rng_tab.startRequested.connect(self._start_auto_rng)
        self.auto_rng_tab.ivCalculatorRequested.connect(self.open_iv_calculator)
        self.tabs.addTab(self.project_xs_tab, self._text("project_xs"))
        self.tabs.addTab(self.bdsp_tab, self._text("bdsp_search"))
        self.tabs.addTab(self.easycon_tab, self._text("easycon"))
        self.tabs.addTab(self.auto_rng_tab, self._text("auto_rng"))
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
        self.capture_group = self._build_blink_group()
        self.seed_group = self._build_seed_group()
        left_layout.addWidget(self.capture_group)
        left_layout.addWidget(self.seed_group)
        left_layout.addStretch(1)

        # 右侧：状态条（紧凑） + 预览（下部）
        self.status_group = self._build_project_status_group()
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)
        right_layout.addWidget(self.status_group)
        right_layout.addWidget(self._build_preview_panel(), 1)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([430, 1050])
        return splitter

    def _build_bdsp_tab(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # ── 顶部：存档信息 ──
        self.profile_group = self._build_profile_group()
        self.profile_group.setMaximumHeight(120)
        layout.addWidget(self.profile_group)

        # ── 中部：乱数信息 / 设置 / 筛选项 ──
        mid_row = QHBoxLayout()
        mid_row.setSpacing(10)
        self.rng_info_group = self._build_rng_info_group()
        self.rng_info_group.setMinimumWidth(100)
        self.static_group = self._build_static_group()
        self.static_group.setMinimumWidth(140)
        self.filter_group = self._build_filter_group()
        self.filter_group.setMinimumWidth(620)
        mid_row.addWidget(self.rng_info_group, 1)
        mid_row.addWidget(self.static_group, 1)
        mid_row.addWidget(self.filter_group, 2)
        mid_widget = QWidget()
        mid_widget.setLayout(mid_row)
        mid_widget.setMaximumHeight(360)
        layout.addWidget(mid_widget)

        # ── 下部：结果表格（主区域） ──
        self.results_panel = self._build_results()
        layout.addWidget(self.results_panel, 1)
        return panel

    def _build_project_status_group(self) -> QGroupBox:
        group = QGroupBox("状态")
        group.setMaximumHeight(95)

        outer = QVBoxLayout(group)
        outer.setContentsMargins(12, 8, 12, 8)
        outer.setSpacing(0)
        outer.addStretch()

        # 控件统一样式
        stat_label_css = "font-size: 12px; color: #666; border: 0; background: transparent;"
        stat_value_css = "font-size: 12px; font-weight: 600; color: #1a1a1a; border: 0; background: transparent;"
        spin_css = "QLineEdit { min-height: 30px; max-height: 30px; min-width: 140px; }"
        btn_css = (
            "QPushButton { min-height: 30px; max-height: 30px;"
            " min-width: 86px; max-width: 100px; padding: 0 14px; }"
        )

        row = QHBoxLayout()
        row.setSpacing(28)
        row.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        # Progress
        self.progress_label = QLabel("Progress:")
        self.progress_label.setStyleSheet(stat_label_css)
        self.progress_value = QLabel("0/0")
        self.progress_value.setStyleSheet(stat_value_css)
        pg = QHBoxLayout()
        pg.setSpacing(4)
        pg.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        pg.addWidget(self.progress_label)
        pg.addWidget(self.progress_value)
        row.addLayout(pg)

        # Advances
        self.advances_label = QLabel("Advances:")
        self.advances_label.setStyleSheet(stat_label_css)
        self.advances_value = QLabel("0")
        self.advances_value.setStyleSheet(stat_value_css)
        ag = QHBoxLayout()
        ag.setSpacing(4)
        ag.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        ag.addWidget(self.advances_label)
        ag.addWidget(self.advances_value)
        row.addLayout(ag)

        # Timer
        self.timer_label = QLabel("Timer:")
        self.timer_label.setStyleSheet(stat_label_css)
        self.timer_value = QLabel("0")
        self.timer_value.setStyleSheet(stat_value_css)
        tg = QHBoxLayout()
        tg.setSpacing(4)
        tg.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        tg.addWidget(self.timer_label)
        tg.addWidget(self.timer_value)
        row.addLayout(tg)

        # 分隔
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("color: #c8c6c0;")
        sep.setFixedHeight(22)
        row.addWidget(sep)
        row.setSpacing(12)

        # X to advance
        self.x_to_advance_label = QLabel("X to advance:")
        self.x_to_advance_label.setStyleSheet(stat_label_css)
        self.x_to_advance = self._spin(0, 10_000_000, 165)
        self.x_to_advance.setStyleSheet(spin_css)
        self.advance_button = QPushButton("Advance")
        self.advance_button.setStyleSheet(btn_css)
        self.advance_button.clicked.connect(self.advance_current_seed)
        row.addWidget(self.x_to_advance_label)
        row.addWidget(self.x_to_advance)
        row.addWidget(self.advance_button)

        row.addStretch()
        outer.addLayout(row)
        outer.addStretch()
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
        self.reidentify_1_pk_npc = QCheckBox()
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
        layout.addWidget(self.reidentify_1_pk_npc, 2, 1, 1, 3)
        layout.addWidget(QLabel(), 3, 0)
        layout.addWidget(self.window_prefix, 3, 1, 1, 3)
        self._add_form_row(layout, 4, "camera", self.camera)
        layout.addWidget(self.select_roi_button, 5, 1, 1, 3)
        self._add_form_row(layout, 6, "threshold", self.threshold)
        self._add_form_row(layout, 7, "time_delay", self.white_delay)
        self._add_form_row(layout, 8, "advance_delay", self.advance_delay)
        self._add_form_row(layout, 9, "advance_delay_2", self.advance_delay_2)
        self._add_form_row(layout, 10, "npcs", self.npc_count)
        self._add_form_row(layout, 11, "timeline_npcs", self.timeline_npc)
        self._add_form_row(layout, 12, "pokemon_npcs", self.pokemon_npc)
        self._add_form_row(layout, 13, "display_percent", self.display_percent)
        layout.addWidget(self.save_config_button, 14, 2)
        layout.addWidget(self.raw_screenshot_button, 14, 3)
        return group

    def _add_form_row(self, layout: QGridLayout, row: int, key: str, widget: QWidget) -> None:
        label = QLabel()
        label.setProperty("i18n", key)
        layout.addWidget(label, row, 0)
        layout.addWidget(widget, row, 1, 1, 3)

    def _build_seed_group(self) -> QGroupBox:
        group = QGroupBox()
        layout = QGridLayout(group)
        self.seed32_inputs = [QLineEdit() for _ in range(4)]
        for box in self.seed32_inputs:
            box.setReadOnly(True)
            box.setMaxLength(8)
            box.setPlaceholderText("—")
        self.seed64_outputs = [QLineEdit() for _ in range(2)]
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
        self.bdsp_seed64_inputs = [QLineEdit() for _ in range(2)]
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
        group.setMinimumWidth(250)
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
        self.level_display.setReadOnly(True)
        self.template_ability_display = QComboBox()
        self.template_ability_display.addItems(["0", "1", "隐藏", "0/1", "任意"])
        self.template_ability_display.setEnabled(False)
        self.template_shiny_display = QComboBox()
        self.template_shiny_display.addItems(["随机", "锁闪"])
        self.template_shiny_display.setEnabled(False)
        self.iv_count_display = self._spin(0, 6, 0)
        self.iv_count_display.setReadOnly(True)

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
        group.setMinimumWidth(290)
        return group

    def _build_profile_group(self) -> QGroupBox:
        group = QGroupBox("存档信息")
        group.setMinimumHeight(150)
        group.setMaximumHeight(155)

        outer = QHBoxLayout(group)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(24)
        outer.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        css_label = "font-size: 12px; color: #555; border: 0; background: transparent;"
        css_cb = "QCheckBox { font-size: 12px; spacing: 6px; border: 0; background: transparent; }"

        input_css = (
            "QLineEdit {"
            " min-height: 30px; max-height: 30px; min-width: 220px; max-width: 220px;"
            " background: #ffffff; color: #1a1a1a;"
            " border: 1px solid #c8c8c8; border-radius: 2px;"
            " padding-left: 8px;"
            "}"
        )

        # ── 左列：存档选择 ──
        left = QVBoxLayout()
        left.setSpacing(6)

        row1 = QHBoxLayout()
        row1.setSpacing(8)
        lbl = QLabel("存档信息")
        lbl.setStyleSheet(css_label)
        lbl.setFixedWidth(58)
        self.profile_name = QLineEdit("-")
        self.profile_name.setPlaceholderText("存档信息")
        self.profile_name.setFixedWidth(180)
        self.profile_name.setStyleSheet(input_css)
        row1.addWidget(lbl)
        row1.addWidget(self.profile_name)
        row1.addStretch()
        left.addLayout(row1)

        self.profile_manager_button = QPushButton("存档信息管理")
        self.profile_manager_button.setFixedWidth(180)
        self.profile_manager_button.setStyleSheet("QPushButton { min-height: 30px; max-height: 30px; }")
        self.profile_manager_button.clicked.connect(self.open_profile_manager)
        left.addWidget(self.profile_manager_button)
        left.addStretch()
        outer.addLayout(left)

        # ── 竖线 ──
        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.VLine)
        sep1.setStyleSheet("color: #c8c6c0;")
        sep1.setFixedHeight(120)
        outer.addWidget(sep1)

        # ── 中列：TID / SID / TSV — 三行纯文本框 ──
        self.tid = QLineEdit("12345")
        self.tid.setStyleSheet(input_css)
        self.sid = QLineEdit("54321")
        self.sid.setStyleSheet(input_css)
        self.tsv = QLineEdit("58376")
        self.tsv.setReadOnly(True)
        self.tsv.setStyleSheet(input_css)
        self.tid.editingFinished.connect(self._update_tsv)
        self.sid.editingFinished.connect(self._update_tsv)

        mid = QGridLayout()
        mid.setVerticalSpacing(8)
        mid.setHorizontalSpacing(4)
        for row, (label_text, widget) in enumerate([("TID", self.tid), ("SID", self.sid), ("TSV", self.tsv)]):
            lbl = QLabel(label_text)
            lbl.setStyleSheet(css_label)
            lbl.setFixedSize(42, 30)
            lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            widget.setFixedSize(220, 30)
            mid.addWidget(lbl, row, 0)
            mid.addWidget(widget, row, 1)
        outer.addLayout(mid)

        # ── 竖线 ──
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setStyleSheet("color: #c8c6c0;")
        sep2.setFixedHeight(120)
        outer.addWidget(sep2)

        # ── 右列：游戏与护符 ──
        right = QVBoxLayout()
        right.setSpacing(8)

        game_row = QHBoxLayout()
        game_row.setSpacing(8)
        game_lbl = QLabel("游戏")
        game_lbl.setStyleSheet(css_label)
        game_lbl.setFixedWidth(36)
        self.profile_game_value = QLabel(self._game_label(self._profile_version))
        self.profile_game_value.setStyleSheet("font-size: 12px; font-weight: 600; border: 0; background: transparent;")
        game_row.addWidget(game_lbl)
        game_row.addWidget(self.profile_game_value)
        game_row.addStretch()
        right.addLayout(game_row)

        self.national_dex = QCheckBox("全国图鉴")
        self.shiny_charm = QCheckBox("闪耀护符")
        self.oval_charm = QCheckBox("圆形护符")

        charms_row1 = QHBoxLayout()
        charms_row1.setSpacing(20)
        charms_row1.addWidget(self.national_dex)
        charms_row1.addWidget(self.shiny_charm)
        charms_row1.addStretch()
        right.addLayout(charms_row1)
        right.addWidget(self.oval_charm)

        for cb in (self.national_dex, self.shiny_charm, self.oval_charm):
            cb.setStyleSheet(css_cb)

        right.addStretch()
        outer.addLayout(right)
        outer.addStretch()
        return group

    def _build_filter_group(self) -> QGroupBox:
        group = QGroupBox("筛选项")
        group.setMaximumHeight(340)

        outer = QHBoxLayout(group)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(28)

        css_label = "font-size: 12px; color: #555; border: 0; background: transparent;"
        css_ctrl = "QLineEdit { min-height: 30px; max-height: 30px; min-width: 64px; }"
        css_combo = "QComboBox { min-height: 30px; max-height: 30px; min-width: 180px; }"
        css_cb = "font-size: 12px; spacing: 6px; border: 0; background: transparent;"

        # ── 左列：IV 范围 + 底部按钮 ──
        left_col = QVBoxLayout()
        left_col.setSpacing(8)

        iv_grid = QGridLayout()
        iv_grid.setVerticalSpacing(7)
        iv_grid.setHorizontalSpacing(6)
        self.iv_min: list[QLineEdit] = []
        self.iv_max: list[QLineEdit] = []
        iv_labels = ("HP", "攻击", "防御", "特攻", "特防", "速度")
        for row, label in enumerate(iv_labels):
            lbl = QLabel(label)
            lbl.setStyleSheet(css_label)
            lbl.setFixedWidth(50)
            lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            min_spin = self._spin(0, 31, 0)
            min_spin.setFixedWidth(68)
            min_spin.setStyleSheet(css_ctrl)
            max_spin = self._spin(0, 31, 31)
            max_spin.setFixedWidth(68)
            max_spin.setStyleSheet(css_ctrl)
            self.iv_min.append(min_spin)
            self.iv_max.append(max_spin)
            iv_grid.addWidget(lbl, row, 0)
            iv_grid.addWidget(min_spin, row, 1)
            iv_grid.addWidget(max_spin, row, 2)
        left_col.addLayout(iv_grid)

        # checkbox + 按钮
        self.show_stats_check = QCheckBox("显示能力值")
        self.show_stats_check.stateChanged.connect(lambda _state: self._refresh_result_columns())
        self.show_stats_check.setStyleSheet(css_cb)
        left_col.addWidget(self.show_stats_check)

        self.iv_calculator_button = QPushButton("个体值计算器")
        self.iv_calculator_button.clicked.connect(self.open_iv_calculator)
        self.iv_calculator_button.setStyleSheet("QPushButton { min-height: 30px; max-height: 30px; }")
        self.iv_calculator_button.setFixedWidth(230)
        left_col.addWidget(self.iv_calculator_button)

        left_col.addStretch()
        outer.addLayout(left_col)

        # ── 竖线分隔 ──
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("color: #c8c6c0;")
        sep.setMinimumHeight(260)
        outer.addWidget(sep)

        # ── 右列：筛选条件 + 底部复选框 ──
        right_col = QVBoxLayout()
        right_col.setSpacing(8)

        right = QGridLayout()
        right.setVerticalSpacing(7)
        right.setHorizontalSpacing(8)

        # 特性
        self.ability_filter = QComboBox()
        self.ability_filter.addItem("任意", 255)
        self.ability_filter.addItem("0", 0)
        self.ability_filter.addItem("1", 1)
        self.ability_filter.addItem("隐藏", 2)
        self.ability_filter.setStyleSheet(css_combo)
        lbl = QLabel("特性")
        lbl.setStyleSheet(css_label)
        lbl.setFixedWidth(70)
        right.addWidget(lbl, 0, 0)
        right.addWidget(self.ability_filter, 0, 1)

        # 性别
        self.gender_filter = QComboBox()
        self.gender_filter.addItem("任意", 255)
        self.gender_filter.addItem("雄性", 0)
        self.gender_filter.addItem("雌性", 1)
        self.gender_filter.addItem("无性别", 2)
        self.gender_filter.setStyleSheet(css_combo)
        lbl = QLabel("性别")
        lbl.setStyleSheet(css_label)
        lbl.setFixedWidth(70)
        right.addWidget(lbl, 1, 0)
        right.addWidget(self.gender_filter, 1, 1)

        # Height
        self.height_min = self._spin(0, 255, 0)
        self.height_min.setFixedWidth(80)
        self.height_min.setStyleSheet(css_ctrl)
        self.height_max = self._spin(0, 255, 255)
        self.height_max.setFixedWidth(80)
        self.height_max.setStyleSheet(css_ctrl)
        lbl = QLabel("Height")
        lbl.setStyleSheet(css_label)
        lbl.setFixedWidth(70)
        right.addWidget(lbl, 2, 0)
        ht = QHBoxLayout()
        ht.setSpacing(6)
        ht.addWidget(self.height_min)
        ht.addWidget(self.height_max)
        ht.addStretch()
        right.addLayout(ht, 2, 1)

        # 性格
        self.nature_combo = QComboBox()
        self.nature_combo.addItem("任意", -1)
        for index, nature in enumerate(NATURES_ZH):
            self.nature_combo.addItem(nature, index)
        self.nature_combo.setStyleSheet(css_combo)
        lbl = QLabel("性格")
        lbl.setStyleSheet(css_label)
        lbl.setFixedWidth(70)
        right.addWidget(lbl, 3, 0)
        right.addWidget(self.nature_combo, 3, 1)

        # 异色
        self.shiny_filter = QComboBox()
        self.shiny_filter.addItem("任意", "any")
        self.shiny_filter.addItem("异色", "shiny")
        self.shiny_filter.addItem("Star", "star")
        self.shiny_filter.addItem("Square", "square")
        self.shiny_filter.addItem("非异色", "none")
        self.shiny_filter.setStyleSheet(css_combo)
        lbl = QLabel("异色")
        lbl.setStyleSheet(css_label)
        lbl.setFixedWidth(70)
        right.addWidget(lbl, 4, 0)
        right.addWidget(self.shiny_filter, 4, 1)

        # Weight
        self.weight_min = self._spin(0, 255, 0)
        self.weight_min.setFixedWidth(80)
        self.weight_min.setStyleSheet(css_ctrl)
        self.weight_max = self._spin(0, 255, 255)
        self.weight_max.setFixedWidth(80)
        self.weight_max.setStyleSheet(css_ctrl)
        lbl = QLabel("Weight")
        lbl.setStyleSheet(css_label)
        lbl.setFixedWidth(70)
        right.addWidget(lbl, 5, 0)
        wt = QHBoxLayout()
        wt.setSpacing(6)
        wt.addWidget(self.weight_min)
        wt.addWidget(self.weight_max)
        wt.addStretch()
        right.addLayout(wt, 5, 1)

        right_col.addLayout(right)

        # 取消筛选
        self.skip_filter = QCheckBox("取消筛选")
        self.skip_filter.setStyleSheet(css_cb)
        right_col.addWidget(self.skip_filter)

        right_col.addStretch()
        outer.addLayout(right_col, 1)

        # 保留旧控件引用（隐藏）
        self.nature_list = QListWidget()
        self.nature_list.setVisible(False)
        for nature in NATURES:
            item = QListWidgetItem(nature)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.nature_list.addItem(item)
        self.all_natures_button = QPushButton("All natures")
        self.all_natures_button.clicked.connect(lambda: self._set_all_natures(Qt.CheckState.Checked))
        self.clear_natures_button = QPushButton("Clear")
        self.clear_natures_button.clicked.connect(lambda: self._set_all_natures(Qt.CheckState.Unchecked))

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
        toolbar.setContentsMargins(0, 0, 0, 0)
        btn_css = "QPushButton { min-height: 30px; max-height: 30px; padding: 0 14px; }"
        self.generate_button = QPushButton("生成")
        self.generate_button.setStyleSheet(btn_css)
        self.generate_button.setObjectName("PrimaryButton")
        self.generate_button.clicked.connect(self.generate_results)
        self.copy_button = QPushButton("复制")
        self.copy_button.setStyleSheet(btn_css)
        self.copy_button.clicked.connect(self.copy_results)
        self.export_button = QPushButton("导出 CSV")
        self.export_button.setStyleSheet(btn_css)
        self.export_button.clicked.connect(self.export_results)
        self.result_count = QLabel("0 条结果")
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
            QLabel {
                background: transparent;
                border: none;
                padding: 0;
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
            QLineEdit, QDoubleSpinBox, QComboBox, QListWidget {
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

    def _spin(self, minimum: int, maximum: int, value: int) -> QLineEdit:
        w = QLineEdit(str(value))
        w.setValidator(QIntValidator(minimum, maximum))
        w.setAlignment(Qt.AlignmentFlag.AlignRight)
        return w

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
        tid = self._spin(0, 65535, int(self.tid.text() or 0))
        sid = self._spin(0, 65535, int(self.sid.text() or 0))
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
        self.tid.setText(str(int(tid.text() or 0)))
        self.sid.setText(str(int(sid.text() or 0)))
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
        self.tabs.setTabText(3, self._text("auto_rng"))
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
        self.reidentify_1_pk_npc.setText(self._text("reidentify_1_pk_npc"))
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
        self.reidentify_1_pk_npc.setChecked(config.reidentify_1_pk_npc)
        self.window_prefix.setText(config.capture.window_prefix)
        self.camera.setText(str(config.capture.camera))
        self.x.setText(str(roi_x))
        self.y.setText(str(roi_y))
        self.w.setText(str(roi_w))
        self.h.setText(str(roi_h))
        self.threshold.setValue(config.capture.threshold)
        self.white_delay.setValue(config.white_delay)
        self.advance_delay.setText(str(config.advance_delay))
        self.advance_delay_2.setText(str(config.advance_delay_2))
        self.npc_count.setText(str(config.npc))
        self.timeline_npc.setText(str(config.timeline_npc))
        self.pokemon_npc.setText(str(config.pokemon_npc))
        self.display_percent.setText(str(config.display_percent))
        self._eye_image_path = config.capture.eye_image_path

    def _config_from_form(self) -> ProjectXsTrackingConfig:
        loaded = load_project_xs_config(self._selected_config_path(), blink_count=DEFAULT_BLINK_COUNT)
        capture = BlinkCaptureConfig(
            eye_image_path=self._eye_image_path or loaded.capture.eye_image_path,
            roi=(int(self.x.text() or 0), int(self.y.text() or 0), int(self.w.text() or 0), int(self.h.text() or 0)),
            threshold=self.threshold.value(),
            blink_count=DEFAULT_BLINK_COUNT,
            monitor_window=self.monitor_window.isChecked(),
            window_prefix=self.window_prefix.text(),
            crop=loaded.capture.crop,
            camera=int(self.camera.text() or 0),
        )
        return ProjectXsTrackingConfig(
            source_path=loaded.source_path,
            capture=capture,
            white_delay=self.white_delay.value(),
            advance_delay=int(self.advance_delay.text() or 0),
            advance_delay_2=int(self.advance_delay_2.text() or 0),
            npc=int(self.npc_count.text() or 0),
            pokemon_npc=int(self.pokemon_npc.text() or 0),
            timeline_npc=int(self.timeline_npc.text() or 0),
            display_percent=int(self.display_percent.text() or 0),
            reidentify_1_pk_npc=self.reidentify_1_pk_npc.isChecked(),
        )

    def _reidentify_blink_count(self) -> int:
        return NOISY_REIDENTIFY_BLINK_COUNT if self.reidentify_1_pk_npc.isChecked() else REIDENTIFY_BLINK_COUNT

    def _reidentify_capture_config(self, capture: BlinkCaptureConfig) -> BlinkCaptureConfig:
        return replace(capture, blink_count=self._reidentify_blink_count())

    def _reidentify_from_observation(
        self,
        state: SeedState32,
        observation: object,
        *,
        npc: int,
        search_min: int,
        search_max: int,
    ) -> ProjectXsReidentifyResult:
        if self.reidentify_1_pk_npc.isChecked():
            return reidentify_seed_from_observation_noisy(
                state,
                observation,  # type: ignore[arg-type]
                search_min=search_min,
                search_max=search_max,
            )
        return reidentify_seed_from_observation(
            state,
            observation,  # type: ignore[arg-type]
            npc=npc,
            search_min=search_min,
            search_max=search_max,
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
        self._roi_before_selection = (int(self.x.text() or 0), int(self.y.text() or 0), int(self.w.text() or 0), int(self.h.text() or 0))
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
        old_roi = self._roi_before_selection or (int(self.x.text() or 0), int(self.y.text() or 0), int(self.w.text() or 0), int(self.h.text() or 0))
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
        self._roi_before_selection = (int(self.x.text() or 0), int(self.y.text() or 0), int(self.w.text() or 0), int(self.h.text() or 0))
        self._display_frame(self._latest_preview_frame if self._latest_preview_frame is not None else frame)
        self.preview_label.set_selection_enabled(True)
        self.statusBar().showMessage(f"{self._text('eye_captured_select_roi')}: {output_path}")

    def _set_roi_values(self, roi: tuple[int, int, int, int]) -> None:
        self.x.setText(str(roi[0]))
        self.y.setText(str(roi[1]))
        self.w.setText(str(roi[2]))
        self.h.setText(str(roi[3]))

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
            self.level_display.setText(str(template.level))
        if hasattr(self, "iv_count_display"):
            self.iv_count_display.setText(str(template.iv_count))
        if hasattr(self, "template_ability_display"):
            ability_text = {0: "0", 1: "1", 2: "隐藏", 255: "0/1"}.get(template.ability, "任意")
            index = self.template_ability_display.findText(ability_text)
            self.template_ability_display.setCurrentIndex(max(0, index))
        if hasattr(self, "template_shiny_display"):
            self.template_shiny_display.setCurrentText("锁闪" if template.shiny == Shiny.NEVER else "随机")

    def _update_tsv(self) -> None:
        self.tsv.setText(str(int(self.tid.text() or 0) ^ int(self.sid.text() or 0)))

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
        from auto_bdsp_rng.data import load_species_info, get_species_info

        species_table = load_species_info()
        dialog = _IVCalculatorDialog(species_table, self)
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
            tid=int(self.tid.text() or 0),
            sid=int(self.sid.text() or 0),
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
                [int(spin.text() or 0) for spin in self.iv_min],
                [int(spin.text() or 0) for spin in self.iv_max],
                ability=self.ability_filter.currentData(),
                gender=self.gender_filter.currentData(),
                shiny=shiny_value,
                height_min=int(self.height_min.text() or 0) if hasattr(self, "height_min") else 0,
                height_max=int(self.height_max.text() or 0) if hasattr(self, "height_max") else 255,
                weight_min=int(self.weight_min.text() or 0) if hasattr(self, "weight_min") else 0,
                weight_max=int(self.weight_max.text() or 0) if hasattr(self, "weight_max") else 255,
                skip=self.skip_filter.isChecked() if hasattr(self, "skip_filter") else False,
                natures=natures,
            ),
            shiny_mode,
        )

    def _is_capturing(self) -> bool:
        return self._capture_thread is not None and self._capture_thread.is_alive()

    def _start_auto_rng(self, config: AutoRngConfig) -> None:
        services = self._build_auto_rng_services(config)
        self.auto_rng_tab.run_with_runner(AutoRngRunner(config, services=services))

    def _build_auto_rng_services(self, config: AutoRngConfig) -> AutoRngServices:
        tracking_config = self._config_from_form()
        self.auto_rng_tab.target_form.set_version(self._profile_version)
        record = self.auto_rng_tab.target_form.selected_record()
        state_filter, shiny_mode = self.auto_rng_tab.target_form.current_filter()
        try:
            initial_seed = self._current_seed_pair()
        except ValueError:
            initial_seed = SeedPair64(0, 0)
        search_criteria = StaticSearchCriteria(
            seed=initial_seed,
            profile=self._current_profile(),
            record=record,
            state_filter=state_filter,
            initial_advances=0,
            max_advances=config.max_advances,
            offset=0,
            lead=Lead.NONE,
            shiny_mode=shiny_mode,
        )
        self._update_auto_rng_search_summary(search_criteria)

        def seed_pair_from_result(seed_result: AutoRngSeedResult) -> SeedPair64:
            seed = seed_result.seed
            if isinstance(seed, SeedPair64):
                return seed
            if isinstance(seed, SeedState32):
                return seed.to_seed_pair64()
            to_seed_pair64 = getattr(seed, "to_seed_pair64", None)
            if callable(to_seed_pair64):
                return to_seed_pair64()
            raise TypeError("Auto RNG seed result must contain SeedPair64 or SeedState32")

        def state32_from_result(seed_result: AutoRngSeedResult) -> SeedState32:
            seed = seed_result.seed
            if isinstance(seed, SeedState32):
                return seed
            if isinstance(seed, SeedPair64):
                return seed.to_state32()
            to_state32 = getattr(seed, "to_state32", None)
            if callable(to_state32):
                return to_state32()
            raise TypeError("Auto RNG seed result must contain SeedPair64 or SeedState32")

        def capture_seed_service() -> AutoRngSeedResult:
            self._capture_cancel.clear()
            observation = capture_player_blinks(
                tracking_config.capture,
                should_stop=self._capture_cancel.is_set,
                show_window=False,
            )
            result = recover_seed_from_observation(observation, npc=tracking_config.npc)
            elapsed_seconds = max(0, round(time.perf_counter() - observation.offset_time))
            elapsed_advances = elapsed_seconds * (tracking_config.npc + 1)
            if elapsed_advances:
                result = replace(result, state=advance_seed_state(result.state, elapsed_advances).state)
            return AutoRngSeedResult(
                seed=result.state,
                current_advances=0,
                npc=tracking_config.npc,
                seed_text=" ".join(result.state.format_seed64_pair()),
            )

        def reidentify_service(seed_result: AutoRngSeedResult) -> AutoRngSeedResult:
            self._capture_cancel.clear()
            observation = capture_player_blinks(
                self._reidentify_capture_config(tracking_config.capture),
                should_stop=self._capture_cancel.is_set,
                show_window=False,
            )
            result = self._reidentify_from_observation(
                state32_from_result(seed_result),
                observation,
                npc=tracking_config.npc,
                search_min=0,
                search_max=max(100_000, config.max_advances, search_criteria.max_advances),
            )
            return AutoRngSeedResult(
                seed=result.state,
                current_advances=result.advances,
                npc=tracking_config.npc,
                seed_text=" ".join(result.state.format_seed64_pair()),
            )

        def search_candidates_service(seed_result: AutoRngSeedResult) -> list[State8]:
            candidates = generate_static_candidates(replace(search_criteria, seed=seed_pair_from_result(seed_result)))
            locked = candidates[0].advances if candidates else None
            if locked is None:
                self.auto_rng_tab.add_log("找到 0 个候选")
            else:
                self.auto_rng_tab.add_log(f"找到 {len(candidates)} 个候选，锁定最低帧 Adv={locked}")
            return candidates

        def run_script_text_service(script_text: str, name: str) -> object:
            if self.easycon_tab.bridge_status != EasyConStatus.BRIDGE_CONNECTED:
                raise RuntimeError("请先连接伊机控 Bridge")
            return self.easycon_tab._ensure_bridge_backend().run_script_text(script_text, name)

        def stop_current_script_service() -> None:
            self._capture_cancel.set()
            try:
                self.easycon_tab._ensure_bridge_backend().stop_current_script()
            except Exception:
                pass

        return AutoRngServices(
            capture_seed=capture_seed_service,
            reidentify=reidentify_service,
            search_candidates=search_candidates_service,
            run_script_text=run_script_text_service,
            stop_current_script=stop_current_script_service,
        )

    def _update_auto_rng_search_summary(self, criteria: StaticSearchCriteria) -> None:
        target_text = self.auto_rng_tab.target_form.summary_text()
        profile_text = (
            f"{criteria.profile.name} / TID {criteria.profile.tid} / SID {criteria.profile.sid} / "
            f"{GAME_LABELS_EN.get(self._profile_version, self._profile_version.value)}"
        )
        iv_text = ", ".join(
            f"{label} {low}-{high}"
            for label, low, high in zip(IV_LABELS, criteria.state_filter.iv_min, criteria.state_filter.iv_max)
        )
        filter_text = (
            f"shiny={criteria.shiny_mode}; ability={criteria.state_filter.ability}; "
            f"gender={criteria.state_filter.gender}; Height {criteria.state_filter.height_min}-{criteria.state_filter.height_max}; "
            f"Weight {criteria.state_filter.weight_min}-{criteria.state_filter.weight_max}; {iv_text}"
        )
        self.auto_rng_tab.set_search_context_summary(
            target=target_text,
            profile=profile_text,
            filters=filter_text,
            seed=" ".join(criteria.seed.format_seeds()),
            max_advances=criteria.max_advances,
        )

    def _stop_advance_tracking(self) -> None:
        self._advance_timer.stop()
        self._tracked_advances = 0
        self.advances_value.setText("0")
        self.timer_value.setText("0")

    def _advance_tick(self) -> None:
        self._tracked_advances += self._advance_step
        self.advances_value.setText(str(self._tracked_advances))

    def advance_current_seed(self) -> None:
        advances = int(self.x_to_advance.text() or 0)
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
        reidentify_capture = self._reidentify_capture_config(config.capture)
        reidentify_blink_count = reidentify_capture.blink_count
        self._capture_progress = (0, reidentify_blink_count)
        with self._capture_lock:
            self._capture_frame = None
        self.progress_value.setText(f"0/{reidentify_blink_count}")
        self.capture_button.setText(self._text("stop_capture"))
        self.statusBar().showMessage(f"Capturing {reidentify_blink_count} blinks...")

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
                    reidentify_capture,
                    should_stop=self._capture_cancel.is_set,
                    frame_callback=store_frame,
                    progress_callback=store_progress,
                    show_window=False,
                )
                self._capture_result = self._reidentify_from_observation(
                    current_state,
                    observation,
                    npc=config.npc,
                    search_min=0,
                    search_max=max(100_000, int(self.max_advances.text() or 0) if hasattr(self, "max_advances") else 100_000),
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
        self.progress_value.setText(f"{total}/{total}")
        self._sync_seed64_from_state32()
        self._advance_step = int(self.npc_count.text() or 0) + 1
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
            state_filter, shiny_mode = self._current_filter()
            self._active_record = record
            states = generate_static_candidates(
                StaticSearchCriteria(
                    seed=self._current_seed_pair(),
                    profile=self._current_profile(),
                    record=record,
                    state_filter=state_filter,
                    initial_advances=int(self.initial_advances.text() or 0),
                    max_advances=int(self.max_advances.text() or 0),
                    offset=int(self.offset.text() or 0),
                    lead=self.lead_combo.currentData(),
                    shiny_mode=shiny_mode,
                )
            )
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




def _compute_iv_range(base_stats, stats, levels, nature, characteristic, hidden_power):
    """基于 PokeFinder IVChecker 算法计算个体值范围"""
    iv_order = [0, 1, 2, 5, 3, 4]
    labels = ("HP", "攻击", "防御", "特攻", "特防", "速度")

    def _calc_single(bs, st, lv, nat, charac):
        min_ivs = [31] * 6
        max_ivs = [0] * 6
        for i in range(6):
            for iv in range(32):
                if nat != 255:
                    increased, decreased = NATURE_MODIFIERS[nat]
                    base = ((2 * bs[i] + iv) * lv) // 100 + 5
                    if i == 0:
                        base = ((2 * bs[i] + iv) * lv) // 100 + lv + 10
                    if i == increased:
                        base = (base * 110) // 100
                    elif i == decreased:
                        base = (base * 90) // 100
                    if base == st[i]:
                        min_ivs[i] = min(iv, min_ivs[i])
                        max_ivs[i] = max(iv, max_ivs[i])
                else:
                    if i == 0:
                        base = ((2 * bs[i] + iv) * lv) // 100 + lv + 10
                    else:
                        base = ((2 * bs[i] + iv) * lv) // 100 + 5
                    if base == st[i] or (i != 0 and (int(base * 0.9) == st[i] or int(base * 1.1) == st[i])):
                        min_ivs[i] = min(iv, min_ivs[i])
                        max_ivs[i] = max(iv, max_ivs[i])

        possible = [[] for _ in range(6)]
        char_high = 31
        char_idx = -1
        if charac != 255:
            char_idx = iv_order[charac // 5]
            result = charac % 5
            for iv_val in range(min_ivs[char_idx], max_ivs[char_idx] + 1):
                if (iv_val % 5) == result:
                    if all(iv_val >= min_ivs[j] for j in range(6)):
                        possible[char_idx].append(iv_val)
                        char_high = iv_val
        for i in range(6):
            if i == char_idx:
                continue
            for iv_val in range(min_ivs[i], min(max_ivs[i], char_high) + 1):
                possible[i].append(iv_val)
        return possible

    result = None
    for idx in range(len(stats)):
        current = _calc_single(base_stats, stats[idx], levels[idx], nature, characteristic)
        if result is None:
            result = current
        else:
            for j in range(6):
                result[j] = sorted(set(result[j]) & set(current[j]))

    if hidden_power != 255 and result is not None:
        parity = [[] for _ in range(6)]
        for i in range(6):
            has_even = any(v % 2 == 0 for v in result[i])
            has_odd = any(v % 2 == 1 for v in result[i])
            if has_even:
                parity[i].append(0)
            if has_odd:
                parity[i].append(1)
        temp = [[] for _ in range(6)]
        for hp in parity[0]:
            for atk in parity[1]:
                for def_ in parity[2]:
                    for spa in parity[3]:
                        for spd in parity[4]:
                            for spe in parity[5]:
                                t = ((hp + 2 * atk + 4 * def_ + 16 * spa + 32 * spd + 8 * spe) * 15) // 63
                                if t == hidden_power:
                                    for j, p in enumerate([hp, atk, def_, spa, spd, spe]):
                                        temp[j].extend(v for v in result[j] if v % 2 == p)
        for i in range(6):
            result[i] = sorted(set(temp[i]))

    return result if result else [[] for _ in range(6)]


def _format_iv_range(ivs):
    if not ivs:
        return "无效"
    if len(ivs) == 1:
        return str(ivs[0])
    parts = []
    start = ivs[0]
    for i in range(1, len(ivs)):
        if ivs[i] != ivs[i - 1] + 1:
            if start == ivs[i - 1]:
                parts.append(str(start))
            else:
                parts.append(f"{start}-{ivs[i - 1]}")
            start = ivs[i]
    if start == ivs[-1]:
        parts.append(str(start))
    else:
        parts.append(f"{start}-{ivs[-1]}")
    return ", ".join(parts)


def _compute_stat(base, iv, lv, nature, stat_index):
    if stat_index == 0:
        s = ((2 * base + iv) * lv) // 100 + lv + 10
    else:
        s = ((2 * base + iv) * lv) // 100 + 5
    if nature != 255:
        increased, decreased = NATURE_MODIFIERS[nature]
        if stat_index == increased:
            s = (s * 110) // 100
        elif stat_index == decreased:
            s = (s * 90) // 100
    return s


def _compute_next_level(base_stats, ivs, level, nature):
    labels = ("HP", "攻击", "防御", "特攻", "特防", "速度")
    result = [level] * 6
    for i in range(6):
        if len(ivs[i]) < 2:
            continue
        for lv in range(level + 1, 101):
            found = False
            for j in range(1, len(ivs[i])):
                prev = _compute_stat(base_stats[i], ivs[i][j - 1], lv, nature, i)
                curr = _compute_stat(base_stats[i], ivs[i][j], lv, nature, i)
                if prev < curr:
                    result[i] = lv
                    found = True
                    break
            if found:
                break
    return result


class _IVCalculatorDialog(QDialog):
    """个体值计算器 — 基于 PokeFinder IVChecker 算法"""

    _SPECIES_NAMES: dict[int, str] | None = None

    def __init__(self, species_table, parent=None):
        super().__init__(parent)
        self._species_table = species_table
        self._rows = 0
        self._entry_grid = None

        self.setWindowTitle("个体值计算器")
        self.setMinimumSize(860, 580)
        self.resize(900, 620)
        self.setStyleSheet("background: #f2f1ee;")

        self._build_ui()
        self._add_entry()
        self._on_game_changed()

    @classmethod
    def _load_species_names(cls):
        if cls._SPECIES_NAMES is not None:
            return cls._SPECIES_NAMES
        cls._SPECIES_NAMES = {}
        names_path = Path(__file__).resolve().parents[3] / "third_party" / "PokeFinder" / "Core" / "Resources" / "i18n" / "zh" / "species_zh.txt"
        if names_path.exists():
            with open(names_path, encoding="utf-8-sig") as f:
                for i, line in enumerate(f, start=1):
                    name = line.strip()
                    if name:
                        cls._SPECIES_NAMES[i] = name
        return cls._SPECIES_NAMES

    def _build_ui(self):
        main = QHBoxLayout(self)
        main.setContentsMargins(16, 16, 16, 16)
        main.setSpacing(14)

        css_ctrl = "QComboBox, QLineEdit { min-height: 32px; max-height: 32px; }"
        css_btn = (
            "QPushButton { min-height: 34px; max-height: 34px; min-width: 90px; max-width: 110px;"
            " background: #ffffff; border: 1px solid #c8c6c0; border-radius: 3px; color: #1a1a1a;"
            " font-size: 12px; }"
            " QPushButton:hover { background: #e8e6e1; }"
        )
        css_primary = (
            "QPushButton { min-height: 34px; max-height: 34px; min-width: 90px; max-width: 110px;"
            " background: #159a6a; border: 1px solid #12845b; border-radius: 3px; color: #ffffff;"
            " font-size: 12px; font-weight: 700; }"
            " QPushButton:hover { background: #12845b; }"
        )
        css_entry = "QLineEdit { min-height: 32px; max-height: 32px; max-width: 75px; }"

        # ── 左侧 ──
        left = QVBoxLayout()
        left.setSpacing(10)

        # 设置分组
        settings = QGroupBox("设置")
        sl = QVBoxLayout(settings)
        sl.setSpacing(8)

        # 第一行
        r1 = QHBoxLayout()
        r1.setSpacing(10)
        r1.addWidget(QLabel("游戏"))
        self._game_combo = QComboBox()
        self._game_combo.setFixedWidth(200)
        self._game_combo.addItem("晶灿钻石/明亮珍珠", "BDSP")
        self._game_combo.currentIndexChanged.connect(self._on_game_changed)
        r1.addWidget(self._game_combo)
        r1.addWidget(QLabel("宝可梦"))
        self._pokemon_combo = QComboBox()
        self._pokemon_combo.setEditable(True)
        self._pokemon_combo.setFixedWidth(170)
        self._pokemon_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._pokemon_combo.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        self._pokemon_combo.installEventFilter(self)
        self._pokemon_combo.currentIndexChanged.connect(self._on_pokemon_changed)
        r1.addWidget(self._pokemon_combo)
        r1.addStretch()
        sl.addLayout(r1)

        # 第二行
        r2 = QHBoxLayout()
        r2.setSpacing(10)
        r2.addWidget(QLabel("个性"))
        self._char_combo = QComboBox()
        self._char_combo.setFixedWidth(150)
        self._char_combo.addItem("无", 255)
        chars = ["非常喜欢吃", "经常打瞌睡", "经常午睡", "经常乱扔东西", "喜欢放松",
                 "以力气自豪", "喜欢打闹", "有点易怒", "喜欢打架", "血气方刚",
                 "身体强壮", "能忍耐", "抗打能力强", "不屈不挠", "毅力十足",
                 "好奇心强", "爱恶作剧", "考虑周到", "经常思考", "非常讲究",
                 "意志坚强", "有点固执", "讨厌输", "有点爱逞强", "忍耐力强",
                 "喜欢跑步", "警觉性高", "冲动", "有点轻浮", "逃得快"]
        for i, c in enumerate(chars):
            self._char_combo.addItem(c, i)
        r2.addWidget(self._char_combo)
        r2.addWidget(QLabel("觉醒力量"))
        self._hp_combo = QComboBox()
        self._hp_combo.setFixedWidth(150)
        self._hp_combo.addItem("无", 255)
        hp_types = ["格斗", "飞行", "毒", "地面", "岩石", "虫", "幽灵", "钢",
                    "火", "水", "草", "电", "超能力", "冰", "龙", "恶"]
        for i, t in enumerate(hp_types):
            self._hp_combo.addItem(t, i)
        r2.addWidget(self._hp_combo)
        r2.addWidget(QLabel("性格"))
        self._nature_combo = QComboBox()
        self._nature_combo.setFixedWidth(150)
        self._nature_combo.addItem("无", 255)
        for i, n in enumerate(NATURES_ZH):
            self._nature_combo.addItem(n, i)
        r2.addWidget(self._nature_combo)
        r2.addStretch()
        sl.addLayout(r2)

        # 第三行：操作按钮
        r3 = QHBoxLayout()
        r3.setSpacing(10)
        add_btn = QPushButton("新增行")
        add_btn.setStyleSheet(css_btn)
        add_btn.clicked.connect(self._add_entry)
        r3.addWidget(add_btn)
        del_btn = QPushButton("删除行")
        del_btn.setStyleSheet(css_btn)
        del_btn.clicked.connect(self._remove_entry)
        r3.addWidget(del_btn)
        calc_btn = QPushButton("计算")
        calc_btn.setStyleSheet(css_primary)
        calc_btn.clicked.connect(self._calculate)
        r3.addWidget(calc_btn)
        r3.addStretch()
        sl.addLayout(r3)
        left.addWidget(settings)

        # 能力值输入
        input_group = QGroupBox("能力值输入")
        ivl = QVBoxLayout(input_group)
        ivl.setSpacing(6)

        hdr = QHBoxLayout()
        hdr.setSpacing(6)
        widths = [70, 75, 75, 75, 75, 75, 75]
        for h, w in zip(("等级", "HP", "攻击", "防御", "特攻", "特防", "速度"), widths):
            lbl = QLabel(h)
            lbl.setFixedWidth(w)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("font-weight: 700;")
            hdr.addWidget(lbl)
        hdr.addStretch()
        ivl.addLayout(hdr)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(240)
        self._entry_container = QWidget()
        self._entry_grid = QGridLayout(self._entry_container)
        self._entry_grid.setContentsMargins(0, 4, 0, 0)
        self._entry_grid.setSpacing(4)
        scroll.setWidget(self._entry_container)
        ivl.addWidget(scroll)
        left.addWidget(input_group, 1)
        main.addLayout(left, 7)

        # ── 右侧 ──
        right = QVBoxLayout()
        right.setSpacing(10)

        base_group = QGroupBox("种族值")
        bl = QGridLayout(base_group)
        bl.setVerticalSpacing(8)
        bl.setHorizontalSpacing(10)
        self._base_labels = {}
        for i, label in enumerate(("HP", "攻击", "防御", "特攻", "特防", "速度")):
            lbl = QLabel(label)
            lbl.setFixedWidth(40)
            bl.addWidget(lbl, i, 0)
            val = QLabel("-")
            val.setStyleSheet("font-weight: 600;")
            val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._base_labels[label] = val
            bl.addWidget(val, i, 1)
        bl.setRowStretch(6, 1)
        right.addWidget(base_group, 2)

        result_group = QGroupBox("计算结果")
        rl = QGridLayout(result_group)
        rl.setVerticalSpacing(8)
        rl.setHorizontalSpacing(10)
        self._result_labels = {}
        self._next_level_label = None
        for i, label in enumerate(("HP", "攻击", "防御", "特攻", "特防", "速度")):
            lbl = QLabel(label)
            lbl.setFixedWidth(40)
            rl.addWidget(lbl, i, 0)
            val = QLabel("-")
            val.setStyleSheet("font-weight: 600; color: #1a1a1a;")
            self._result_labels[label] = val
            rl.addWidget(val, i, 1)
        rl.addWidget(QLabel("下一级"), 6, 0)
        self._next_level_label = QLabel("-")
        self._next_level_label.setStyleSheet("font-weight: 600; color: #1a1a1a;")
        self._next_level_label.setWordWrap(True)
        rl.addWidget(self._next_level_label, 6, 1)
        rl.setRowStretch(7, 1)
        right.addWidget(result_group, 3)

        close_btn = QPushButton("关闭")
        close_btn.setStyleSheet(
            "QPushButton { min-height: 36px; max-height: 36px;"
            " background: #ffffff; border: 1px solid #c8c6c0; border-radius: 3px; color: #1a1a1a; }"
            " QPushButton:hover { background: #e8e6e1; }"
        )
        close_btn.clicked.connect(self.close)
        right.addWidget(close_btn)

        main.addLayout(right, 3)

    def _add_entry(self):
        self._rows += 1
        r = self._rows
        defaults = [1, 0, 0, 0, 0, 0, 0]
        widths = [70, 75, 75, 75, 75, 75, 75]
        for col, (default, w) in enumerate(zip(defaults, widths)):
            wgt = QLineEdit(str(default))
            wgt.setFixedWidth(w)
            wgt.setAlignment(Qt.AlignmentFlag.AlignCenter)
            wgt.setValidator(QIntValidator(0, 999 if col == 0 else 9999))
            self._entry_grid.addWidget(wgt, r, col)

    def eventFilter(self, obj, event):
        if obj is self._pokemon_combo and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                popup = self._pokemon_combo.completer().popup()
                if popup is not None:
                    popup.hide()
                return True
        return super().eventFilter(obj, event)

    def _remove_entry(self):
        if self._rows <= 1:
            return
        for col in range(7):
            item = self._entry_grid.itemAtPosition(self._rows, col)
            if item and item.widget():
                item.widget().deleteLater()
        self._rows -= 1

    def _on_game_changed(self):
        species_names = self._load_species_names()
        specie_list = []
        for idx, info in enumerate(self._species_table):
            if idx > 0 and info.present:
                name = species_names.get(info.species, f"#{info.species}")
                specie_list.append((name, idx))
        self._pokemon_combo.blockSignals(True)
        self._pokemon_combo.clear()
        for name, idx in specie_list:
            self._pokemon_combo.addItem(name, idx)
        self._pokemon_combo.blockSignals(False)
        if self._pokemon_combo.count() > 0:
            self._on_pokemon_changed()

    def _on_pokemon_changed(self):
        idx = self._pokemon_combo.currentData()
        if idx is None:
            return
        info = self._species_table[idx]
        stat_names = ("HP", "攻击", "防御", "特攻", "特防", "速度")
        for i, name in enumerate(stat_names):
            self._base_labels[name].setText(str(info.stats[i]))

    def _calculate(self):
        base_stats = [0] * 6
        species_idx = self._pokemon_combo.currentData()
        if species_idx is not None:
            info = self._species_table[species_idx]
            base_stats = list(info.stats)

        stats = []
        levels = []
        for row in range(1, self._rows + 1):
            row_stats = []
            for col in range(7):
                item = self._entry_grid.itemAtPosition(row, col)
                if item and item.widget():
                    val = int(item.widget().text() or 0)
                else:
                    val = 0
                if col == 0:
                    levels.append(val if val > 0 else 1)
                else:
                    row_stats.append(val)
            if len(row_stats) == 6:
                stats.append(row_stats)

        if not stats:
            return

        nature = self._nature_combo.currentData()
        characteristic = self._char_combo.currentData()
        hidden_power = self._hp_combo.currentData()

        ivs = _compute_iv_range(base_stats, stats, levels, nature, characteristic, hidden_power)

        stat_names = ("HP", "攻击", "防御", "特攻", "特防", "速度")
        for i, name in enumerate(stat_names):
            self._result_labels[name].setText(_format_iv_range(ivs[i]))

        next_levels = _compute_next_level(base_stats, ivs, levels[-1], nature)
        self._next_level_label.setText(", ".join(str(l) for l in next_levels))

def create_window() -> MainWindow:
    return MainWindow()


def run() -> int:
    app = QApplication.instance() or QApplication([])
    window = create_window()
    window.show()
    return app.exec()
