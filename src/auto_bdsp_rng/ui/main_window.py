from __future__ import annotations

import csv
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSettings, QSize, QThread, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QDesktopServices,
    QGuiApplication,
    QIcon,
    QImage,
    QIntValidator,
    QPainter,
    QPen,
    QPixmap,
)
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
    QStyle,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from auto_bdsp_rng import __version__
from auto_bdsp_rng.blink_detection import (
    BlinkCaptureConfig,
    PreviewFrameCapture,
    ProjectXsCaptureConfigError,
    ProjectXsIntegrationError,
    ProjectXsNoFrameError,
    ProjectXsReidentifyResult,
    ProjectXsTrackingConfig,
    advance_seed_state,
    capture_pokemon_blinks,
    capture_player_blinks,
    capture_preview_frame,
    load_project_xs_config,
    reidentify_seed_from_observation,
    reidentify_seed_from_observation_noisy,
    recover_tidsid_seed_from_observation,
    recover_seed_from_observation,
    render_eye_preview,
    save_project_xs_config,
)
from auto_bdsp_rng.automation.auto_rng import AutoRngConfig, AutoRngPhase, AutoRngProgress, AutoRngSeedResult
from auto_bdsp_rng.automation.auto_tid_rng import (
    AutoTidRngConfig,
    AutoTidRngRunner,
    AutoTidRngServices,
    AutoTidSeedResult,
    ProjectXsMunchlaxAdvanceCounter,
)
from auto_bdsp_rng.automation.auto_rng.dialog_timing import (
    DialogKeywordTimeoutError,
    DialogScriptTimeoutError,
    DialogTimingEvent,
    measure_keyword_interval,
    read_ocr_text,
    suggested_shiny_threshold,
)
from auto_bdsp_rng.automation.auto_rng.models import ShinyCheckResult
from auto_bdsp_rng.automation.auto_rng.ocr_regions import (
    DYNAMIC_DEFAULT_REGION_FIELDS,
    NOTE_REGION_FIELDS,
    OCR_REGION_LABELS,
    SHINY_DIALOG_REGION_FIELD,
    STARTER_BATTLE_REGION_FIELD,
    STAT_REGION_FIELDS,
    OcrRegion,
)
from auto_bdsp_rng.automation.auto_rng.pokemon_info_ocr import (
    extract_pokemon_info,
    recognize_ocr_field,
    warm_up_pokemon_info_ocr,
)
from auto_bdsp_rng.automation.auto_rng.runner import AutoRngRunner, AutoRngServices, ProjectXsAdvanceCounter
from auto_bdsp_rng.automation.auto_rng.scripts import ROAMER_SPECIES, STARTER_SPECIES
from auto_bdsp_rng.automation.auto_rng.search import (
    StaticSearchCriteria,
    StaticSearchTarget,
    generate_static_candidates,
    generate_static_candidates_multi,
)
from auto_bdsp_rng.automation.auto_rng.zoom_recovery import recover_zoom_overlay
from auto_bdsp_rng.app_settings import (
    is_auto_update_check_enabled,
    is_run_log_enabled,
    set_auto_update_check_enabled,
    set_run_log_enabled,
    set_startup_notice_acknowledged,
    should_show_startup_notice,
)
from auto_bdsp_rng.automation.easycon import EasyConRunResult, EasyConStatus
from auto_bdsp_rng.capture_broker import CAPTURE_API_DIRECTSHOW, CAPTURE_API_MSMF
from auto_bdsp_rng.data import GameVersion, StaticEncounterCategory, StaticEncounterRecord, get_static_encounters
from auto_bdsp_rng.gen8_id import IDFilter, generate_ids
from auto_bdsp_rng.gen8_static import Lead, Profile8, Shiny, State8, StateFilter
from auto_bdsp_rng.rng_core import SeedPair64, SeedState32
from auto_bdsp_rng.resources import app_base_dir, app_icon_path, resource_path, script_directory
from auto_bdsp_rng.run_log import ExceptionHookGuard, RunLogError, RunLogManager
from auto_bdsp_rng.ui.about_dialog import StartupNoticeDialog
from auto_bdsp_rng.ui.auto_rng_panel import AutoRngPanel
from auto_bdsp_rng.ui.auto_tid_rng_panel import AutoTidRngPanel
from auto_bdsp_rng.ui.easycon_panel import CAPTURE_KEEP_AWAKE_BUTTON, EasyConPanel
from auto_bdsp_rng.ui.help_menu import HelpMenuController
from auto_bdsp_rng.ui.history_panel import HistoryPanel
from auto_bdsp_rng.ui.numeric_locale import set_c_locale
from auto_bdsp_rng.ui.ocr_settings_dialog import OcrSettingsDialog, load_ocr_region_config
from auto_bdsp_rng.ui.tid_ocr_dialog import TidOcrDialog
from auto_bdsp_rng.ui.update_dialog import UpdateController
from auto_bdsp_rng.update_core import (
    UpdatePackageError,
    has_uncommitted_update_transaction,
    migrate_legacy_internal_scripts,
)


PROJECT_XS_CONFIGS = resource_path("third_party", "Project_Xs_CHN", "configs")
APP_TITLE = "珍钻复刻自动乱数"
APP_DISPLAY_TITLE = f"{APP_TITLE} v{__version__}"
APP_USER_MODEL_ID = "XiaoyuBook.auto-bdsp-rng"
DEFAULT_BLINK_COUNT = 40
TIDSID_BLINK_COUNT = 64
REIDENTIFY_BLINK_COUNT = 7
NOISY_REIDENTIFY_BLINK_COUNT = 20
CAPTURE_KEEP_AWAKE_BLINK_COUNTS = frozenset((
    DEFAULT_BLINK_COUNT,
    TIDSID_BLINK_COUNT,
    NOISY_REIDENTIFY_BLINK_COUNT,
))
CAPTURE_KEEP_AWAKE_INTERVAL = 10
CAPTURE_KEEP_AWAKE_PRESS_MS = 100
ROAMER_BATTLE_LABEL = "宝可表"
ROAMER_BATTLE_MATCH_THRESHOLD = 95
AUTO_CAPTURE_WARMUP_DISCARD_SECONDS = 1.0
REIDENTIFY_HINT_BEFORE_FRAMES = 10_000
REIDENTIFY_HINT_AFTER_FRAMES = 20_000
NOISY_REIDENTIFY_MAX_SEARCH_FRAMES = 100_000
PREVIEW_REFRESH_FPS = 30
PREVIEW_REFRESH_INTERVAL_MS = round(1000 / PREVIEW_REFRESH_FPS)
STARTUP_UPDATE_CHECK_DELAY_MS = 1000
STARTUP_UPDATE_CHECK_MODAL_RETRY_MS = 500
CAPTURE_API_SETTINGS_VERSION = 1
CAPTURE_API_SETTINGS_VERSION_KEY = "video_source/capture_api_settings_version"

# The original 1150x900 layout is comfortable on a desktop monitor, but its
# hard minimum made the application impossible to fit on common 1366x768
# displays.  Keep a readable compact floor and let page-level scroll areas
# expose the controls that do not fit vertically.
MAIN_WINDOW_DEFAULT_SIZE = QSize(1150, 900)
MAIN_WINDOW_COMPACT_MIN_SIZE = QSize(900, 600)
MAIN_WINDOW_SCREEN_MARGIN = 16
MAIN_WINDOW_GEOMETRY_KEYS = (
    "window/x",
    "window/y",
    "window/width",
    "window/height",
)
# The Project_Xs controls and preview are both useful side by side on a
# desktop, but the preview's minimum width leaves too little room on compact
# displays.  The comparison is made against the splitter's logical width so
# window decorations and high-DPI scaling do not affect the breakpoint.
PROJECT_XS_VERTICAL_BREAKPOINT = 1100
PROJECT_XS_HORIZONTAL_LEFT_WIDTH = 550
PROJECT_XS_VERTICAL_LEFT_MIN_HEIGHT = 180
PROJECT_XS_VERTICAL_LEFT_MAX_HEIGHT = 420
PROJECT_XS_PREVIEW_MIN_HEIGHT = 260
PROJECT_XS_COMPACT_PREVIEW_MIN_HEIGHT = 140


def _exception_chain_text(error: BaseException) -> str:
    """Render an exception and its direct causes without a full traceback."""

    parts: list[str] = []
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen and len(parts) < 5:
        seen.add(id(current))
        detail = str(current).strip() or "-"
        parts.append(f"{type(current).__name__}: {detail}")
        next_error = current.__cause__
        if next_error is None and not current.__suppress_context__:
            next_error = current.__context__
        current = next_error
    return " <- ".join(parts)


def _status_dot_icon(color: str) -> QIcon:
    pixmap = QPixmap(12, 12)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawEllipse(2, 2, 8, 8)
    painter.end()
    return QIcon(pixmap)


class _MenuPopupComboBox(QComboBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._popup_menu: QMenu | None = None

    def showPopup(self) -> None:  # noqa: N802
        if not self.isEnabled() or self.count() == 0:
            return
        if self._popup_menu is not None:
            self._popup_menu.close()

        menu = QMenu(self)
        menu.setObjectName("VideoSourceComboMenu")
        menu.setMinimumWidth(self.width())
        menu.setAutoFillBackground(True)
        menu.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        current = self.currentIndex()
        for row in range(self.count()):
            action = menu.addAction(self.itemIcon(row), self.itemText(row))
            action.setCheckable(True)
            action.setChecked(row == current)
            action.triggered.connect(
                lambda _checked=False, index=row: self.setCurrentIndex(index)
            )

        self._popup_menu = menu
        menu.aboutToHide.connect(lambda current_menu=menu: self._dispose_popup(current_menu))
        menu.popup(self.mapToGlobal(QPoint(0, self.height())))
        menu.setAutoFillBackground(True)
        QTimer.singleShot(0, menu.repaint)

    def hidePopup(self) -> None:  # noqa: N802
        if self._popup_menu is not None:
            self._popup_menu.close()

    def _dispose_popup(self, menu: QMenu) -> None:
        if self._popup_menu is menu:
            self._popup_menu = None
        menu.deleteLater()


def _uses_same_capture_source(left: BlinkCaptureConfig, right: BlinkCaptureConfig) -> bool:
    if left.uses_shared_video_source or right.uses_shared_video_source:
        return (
            left.uses_shared_video_source
            and right.uses_shared_video_source
            and left.broker_session == right.broker_session
        )
    if left.monitor_window != right.monitor_window:
        return False
    if not left.monitor_window:
        return left.camera == right.camera

    def normalized_crop(crop: tuple[int, int, int, int] | None) -> tuple[int, int, int, int] | None:
        return None if crop is None or crop == (0, 0, 0, 0) else crop

    return left.window_prefix == right.window_prefix and normalized_crop(left.crop) == normalized_crop(right.crop)


def _scale_preview_pixmap(pixmap: QPixmap, target: QSize, device_pixel_ratio: float) -> tuple[QPixmap, QSize]:
    dpr = max(1.0, float(device_pixel_ratio))
    physical_target = QSize(
        min(pixmap.width(), max(1, round(target.width() * dpr))),
        min(pixmap.height(), max(1, round(target.height() * dpr))),
    )
    scaled = pixmap.scaled(
        physical_target,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    scaled.setDevicePixelRatio(dpr)
    return scaled, scaled.deviceIndependentSize().toSize()


def _fit_window_rect(
    available: QRect,
    desired: QSize = MAIN_WINDOW_DEFAULT_SIZE,
    *,
    minimum: QSize = MAIN_WINDOW_COMPACT_MIN_SIZE,
    margin: int = MAIN_WINDOW_SCREEN_MARGIN,
) -> QRect:
    """Return a centered window rectangle that fits a screen work area.

    ``QScreen.availableGeometry()`` is already expressed in Qt logical
    pixels.  Keeping this helper independent of ``QScreen`` makes the
    geometry policy easy to test with several monitor sizes and avoids a
    second device-pixel-ratio conversion.
    """

    margin = max(0, int(margin))
    work = available.adjusted(margin, margin, -margin, -margin)
    if work.width() <= 0 or work.height() <= 0:
        work = QRect(available)
    max_width = max(1, work.width())
    max_height = max(1, work.height())
    min_width = min(max(1, int(minimum.width())), max_width)
    min_height = min(max(1, int(minimum.height())), max_height)
    width = max(min_width, min(max_width, int(desired.width())))
    height = max(min_height, min(max_height, int(desired.height())))
    x = work.left() + max(0, (work.width() - width) // 2)
    y = work.top() + max(0, (work.height() - height) // 2)
    return QRect(x, y, width, height)


def _clamp_window_rect(
    rect: QRect,
    available: QRect,
    *,
    minimum: QSize = MAIN_WINDOW_COMPACT_MIN_SIZE,
    margin: int = MAIN_WINDOW_SCREEN_MARGIN,
) -> QRect:
    """Keep a saved window rectangle visible on the current monitor."""

    fitted = _fit_window_rect(
        available,
        rect.size(),
        minimum=minimum,
        margin=margin,
    )
    margin = max(0, int(margin))
    work = available.adjusted(margin, margin, -margin, -margin)
    if work.width() <= 0 or work.height() <= 0:
        work = QRect(available)
    x = min(max(rect.left(), work.left()), work.right() - fitted.width() + 1)
    y = min(max(rect.top(), work.top()), work.bottom() - fitted.height() + 1)
    fitted.moveTopLeft(QPoint(x, y))
    return fitted


def _scrollable_page(
    content: QWidget,
    *,
    object_name: str = "ResponsivePageScroll",
    horizontal: bool = False,
) -> QScrollArea:
    """Put a page body behind a compact, style-consistent scroll viewport."""

    scroll = QScrollArea()
    scroll.setObjectName(object_name)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(
        Qt.ScrollBarPolicy.ScrollBarAsNeeded if horizontal else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    scroll.setWidget(content)
    content.setMinimumSize(0, 0)
    content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    return scroll


class _ResponsiveTabWidget(QTabWidget):
    """Keep page internals from forcing the main window beyond the screen."""

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(0, 0)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(960, 640)


class _ResponsiveProjectSplitter(QSplitter):
    """Splitter whose page hint remains compact until a page is displayed."""

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(0, 0)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(960, 640)


def _enumerate_capture_devices(capture_api: int) -> list[tuple[int, str]]:
    """Return OpenCV device indices and names for the selected backend."""

    try:
        from cv2_enumerate_cameras import enumerate_cameras

        return [
            (int(camera.index), str(camera.name).strip())
            for camera in enumerate_cameras(int(capture_api))
        ]
    except Exception:
        return []


def _draw_easycon_search_overlay(frame: object, result: object | None) -> object:
    """Draw EasyCon search metadata on an owned display copy."""

    copy_frame = getattr(frame, "copy", None)
    annotated = copy_frame() if callable(copy_frame) else frame
    if result is None:
        return annotated
    try:
        import cv2

        rectangles = (
            (getattr(result, "range_rect"), (0, 196, 255), 2),
            (getattr(result, "match_rect"), (76, 210, 76), 3),
        )
        for raw_rect, color, width in rectangles:
            x, y, rect_width, rect_height = (int(value) for value in raw_rect)
            if rect_width <= 0 or rect_height <= 0:
                continue
            cv2.rectangle(
                annotated,
                (x, y),
                (x + rect_width - 1, y + rect_height - 1),
                color,
                width,
            )
    except Exception:
        return annotated
    return annotated


def configure_application_identity(app: QApplication) -> QIcon:
    app.setApplicationName(APP_DISPLAY_TITLE)
    app.setApplicationDisplayName(APP_DISPLAY_TITLE)
    app.setOrganizationName("XiaoyuBook")
    if app_icon_path().exists():
        icon = QIcon(str(app_icon_path()))
        app.setWindowIcon(icon)
    else:
        icon = QIcon()
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
        except Exception:
            pass
    return icon


def _make_labels_copyable(root: QWidget) -> None:
    flags = (
        Qt.TextInteractionFlag.TextSelectableByMouse
        | Qt.TextInteractionFlag.TextSelectableByKeyboard
        | Qt.TextInteractionFlag.LinksAccessibleByMouse
    )
    for label in root.findChildren(QLabel):
        label.setTextInteractionFlags(label.textInteractionFlags() | flags)


def _reverse_lookup_search_span(target_advances: int, window: int) -> tuple[int, int, int]:
    clamped_window = max(0, min(10_000, int(window)))
    start = max(0, int(target_advances) - clamped_window)
    end = int(target_advances) + clamped_window
    return start, end, end - start


def _reverse_species_label(description: str) -> str:
    return POKEMON_LABELS_ZH.get(description, description)


_REVERSE_LOOKUP_GROUPS: tuple[tuple[str, ...], ...] = (
    ("Articuno", "Zapdos", "Moltres"),
    ("Raikou", "Entei", "Suicune"),
    ("Regirock", "Regice", "Registeel"),
    ("Latias", "Latios"),
)
_REVERSE_LOOKUP_GROUP_BY_DESCRIPTION = {
    description: group
    for group in _REVERSE_LOOKUP_GROUPS
    for description in group
}


def _reverse_lookup_group_descriptions(description: str) -> tuple[str, ...]:
    return _REVERSE_LOOKUP_GROUP_BY_DESCRIPTION.get(description, (description,))


def _normalize_iv_ranges(ranges: object) -> tuple[list[int], list[int]] | None:
    iv_min: list[int] = []
    iv_max: list[int] = []
    try:
        items = list(ranges)  # type: ignore[arg-type]
    except TypeError:
        return None
    if len(items) != 6:
        return None
    for item in items:
        try:
            low = int(item[0])  # type: ignore[index]
            high = int(item[1])  # type: ignore[index]
        except (TypeError, ValueError, IndexError):
            return None
        if low < 0 or high > 31 or low > high:
            return None
        iv_min.append(low)
        iv_max.append(high)
    return iv_min, iv_max


def _auto_script_failure_message(result: object, name: str) -> str | None:
    if not isinstance(result, EasyConRunResult):
        return None
    if result.exit_code in (None, 0) and result.status == EasyConStatus.COMPLETED:
        return None
    if result.exit_code == 130 or result.status == EasyConStatus.CANCELLED:
        return None
    detail = (result.stderr or result.stdout or result.status.value).strip()
    if not detail:
        detail = f"exit code: {result.exit_code if result.exit_code is not None else '无'}"
    return f"伊机控脚本执行失败——{name}: {detail}"


def _project_xs_capture_error_dialog(
    error: object,
    *,
    fallback_title: str = "捕捉失败",
) -> tuple[str, str]:
    """Return a user-facing title and detail without hiding the failure kind."""

    details: list[str] = []
    chain_errors: list[object] = []
    current = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain_errors.append(current)
        details.append(str(current))
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
    combined = "\n".join(details).lower()

    # The explicit subclasses are used by current Project_Xs adapters.  The
    # text fallback keeps dialogs useful with an older packaged adapter whose
    # errors were wrapped as a plain ProjectXsIntegrationError.
    has_config_error = any(isinstance(item, ProjectXsCaptureConfigError) for item in chain_errors)
    if has_config_error or any(
        marker in combined
        for marker in (
            "matchtemplate",
            "templmatch.cpp",
            "_img.size",
            "_templ.size",
            "eye template",
            "cannot read eye template",
            "roi is smaller",
            "眼睛模板",
            "眼睛 roi",
            "眼睛 roi 必须",
            "捕捉配置",
            "config field 'view'",
            "required field: image",
            "required field: view",
        )
    ):
        return (
            "眼睛配置无效",
            "未找到可用的眼睛模板，或眼睛 ROI 与模板尺寸不匹配。\n"
            "请在 Seed 捕捉预览中重新框选眼睛模板和眼睛 ROI，"
            "点击“保存配置”后再重试。",
        )

    has_no_frame_error = any(isinstance(item, ProjectXsNoFrameError) for item in chain_errors)
    if has_no_frame_error or any(
        marker in combined
        for marker in ("未检测到捕捉画面", "empty frame", "首帧")
    ):
        return (
            "捕捉失败",
            "未检测到捕捉画面，请确认 Project_Xs 捕捉窗口已打开且未被最小化，然后重新开始。",
        )

    # Keep unexpected integration failures visible; they often contain the
    # broker/device detail needed to diagnose a hardware or packaging issue.
    return fallback_title, str(error)


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
    ("非常喜欢吃东西", "经常睡午觉", "常常打瞌睡", "经常乱扔东西", "喜欢悠然自在"),
    ("以力气大为傲", "喜欢胡闹", "有点容易生气", "喜欢打架", "血气方刚"),
    ("身体强壮", "抗打能力强", "顽强不屈", "能吃苦耐劳", "善于忍耐"),
    ("好奇心强", "喜欢恶作剧", "做事万无一失", "经常思考", "一丝不苟"),
    ("性格强势", "有一点点爱慕虚荣", "争强好胜", "不服输", "有一点点固执"),
    ("喜欢比谁跑得快", "对声音敏感", "冒冒失失", "有点容易得意忘形", "逃得快"),
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


class _LeadMenuComboBox(_MenuPopupComboBox):
    """Combo box whose Synchronize choices live in a hover submenu."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._lang = "zh"
        self._populate_items()

    def set_language(self, lang: str) -> None:
        selected = self.currentData()
        self._lang = "zh" if lang == "zh" else "en"
        blocked = self.blockSignals(True)
        try:
            self._populate_items()
            index = self.findData(selected)
            self.setCurrentIndex(index if index >= 0 else 0)
        finally:
            self.blockSignals(blocked)

    def showPopup(self) -> None:  # noqa: N802
        if not self.isEnabled() or self.count() == 0:
            return
        if self._popup_menu is not None:
            self._popup_menu.close()

        menu = self._new_menu(self)
        menu.setMinimumWidth(self.width())
        self._add_choice(menu, "无" if self._lang == "zh" else "None", int(Lead.NONE))

        sync_menu = self._new_menu(menu)
        sync_menu.setTitle("同步" if self._lang == "zh" else "Synchronize")
        nature_names = NATURES_ZH if self._lang == "zh" else NATURES
        for value, nature in enumerate(nature_names):
            self._add_choice(sync_menu, nature, value)
        menu.addMenu(sync_menu)

        menu.addSeparator()
        cute_charm = "迷人之躯" if self._lang == "zh" else "Cute Charm"
        self._add_choice(menu, f"{cute_charm} ♀", int(Lead.CUTE_CHARM_F))
        self._add_choice(menu, f"{cute_charm} ♂", int(Lead.CUTE_CHARM_M))

        self._popup_menu = menu
        menu.aboutToHide.connect(lambda current_menu=menu: self._dispose_popup(current_menu))
        menu.popup(self.mapToGlobal(QPoint(0, self.height())))
        QTimer.singleShot(0, menu.repaint)

    def _populate_items(self) -> None:
        self.clear()
        if self._lang == "zh":
            none_text = "无"
            sync_prefix = "同步"
            nature_names = NATURES_ZH
            cute_charm = "迷人之躯"
        else:
            none_text = "None"
            sync_prefix = "Synchronize"
            nature_names = NATURES
            cute_charm = "Cute Charm"
        self.addItem(none_text, int(Lead.NONE))
        for value, nature in enumerate(nature_names):
            text = f"{sync_prefix}：{nature}" if self._lang == "zh" else f"{sync_prefix}: {nature}"
            self.addItem(text, value)
        self.addItem(f"{cute_charm} ♀", int(Lead.CUTE_CHARM_F))
        self.addItem(f"{cute_charm} ♂", int(Lead.CUTE_CHARM_M))

    @staticmethod
    def _new_menu(parent: QWidget) -> QMenu:
        menu = QMenu(parent)
        menu.setObjectName("LeadComboMenu")
        menu.setAutoFillBackground(True)
        menu.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        return menu

    def _add_choice(self, menu: QMenu, text: str, value: int) -> None:
        action = menu.addAction(text)
        action.setData(value)
        action.setCheckable(True)
        action.setChecked(self.currentData() == value)
        action.triggered.connect(
            lambda _checked=False, selected=value: self.setCurrentIndex(self.findData(selected))
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
        "auto_tid_rng": "Auto TID RNG",
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
        "title": "珍钻复刻自动乱数工作台",
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
        "auto_tid_rng": "自动 TID 乱数",
        "status": "状态",
        "config": "配置",
        "browse": "浏览",
        "monitor_window": "捕捉窗口",
        "reidentify_1_pk_npc": "1 PK NPC 校正",
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
        "reidentify_seed": "校正",
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
        "seed_reidentified": "Seed 校正完成",
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
        self._overlay_enabled = True
        self._drag_start: QPoint | None = None
        self._drag_current: QPoint | None = None
        self._image_width = 0
        self._image_height = 0
        self._pixmap_rect = QRect()
        self._ocr_overlay_field: str | None = None
        self._ocr_overlay_region: OcrRegion | None = None

    def set_overlay_enabled(self, enabled: bool) -> None:
        """Toggle recognition overlays without changing the underlying frame."""

        self._overlay_enabled = bool(enabled)
        self.update()

    def overlay_enabled(self) -> bool:
        return self._overlay_enabled

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

    def selection_enabled(self) -> bool:
        return self._selection_enabled

    def set_ocr_overlay(self, field: str, region: OcrRegion | tuple[int, int, int, int]) -> None:
        self._ocr_overlay_field = field
        self._ocr_overlay_region = region if isinstance(region, OcrRegion) else OcrRegion(*(int(value) for value in region))
        self.update()

    def clear_ocr_overlay(self) -> None:
        self._ocr_overlay_field = None
        self._ocr_overlay_region = None
        self.update()

    def _image_rect_to_widget_rect(self, region: OcrRegion) -> QRect:
        if self._image_width <= 0 or self._image_height <= 0 or self._pixmap_rect.isNull():
            return QRect()
        scale_x = self._pixmap_rect.width() / self._image_width
        scale_y = self._pixmap_rect.height() / self._image_height
        return QRect(
            self._pixmap_rect.left() + round(region.x * scale_x),
            self._pixmap_rect.top() + round(region.y * scale_y),
            max(1, round(region.width * scale_x)),
            max(1, round(region.height * scale_y)),
        ).intersected(self._pixmap_rect)

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
        needs_drag_rect = self._selection_enabled and self._drag_start is not None and self._drag_current is not None
        needs_ocr_overlay = self._overlay_enabled and self._ocr_overlay_region is not None
        if not needs_drag_rect and not needs_ocr_overlay:
            return
        painter = QPainter(self)
        if self._selection_enabled and self._drag_start is not None and self._drag_current is not None:
            pen = QPen(QColor("#D7C17C"))
            pen.setWidth(2)
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawRect(QRect(self._drag_start, self._drag_current).normalized().intersected(self._pixmap_rect))
        if self._overlay_enabled and self._ocr_overlay_region is not None:
            pen = QPen(QColor("#00A88B"))
            pen.setWidth(3)
            painter.setPen(pen)
            painter.drawRect(self._image_rect_to_widget_rect(self._ocr_overlay_region))
        end = getattr(painter, "end", None)
        if callable(end):
            end()


class PictureInPicturePreview(QDialog):
    """Non-modal preview of the same raw frame consumed by the application.

    The window owns no capture handle.  ``set_frames`` receives independent
    raw/annotated images from :class:`MainWindow`; toggling either option only
    changes this widget's presentation and cannot affect Broker consumers.
    """

    overlayChanged = Signal(bool)
    alwaysOnTopChanged = Signal(bool)
    roiSelected = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(None)
        if parent is not None:
            self.setStyleSheet(parent.styleSheet())
            parent.destroyed.connect(self.deleteLater)
        self.setWindowTitle("独立预览")
        self.setModal(False)
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setMinimumSize(320, 220)
        self.resize(480, 300)
        self._raw_frame: object | None = None
        self._annotated_frame: object | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        self.frame_label = RoiPreviewLabel()
        self.frame_label.setObjectName("Preview")
        self.frame_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.frame_label.setMinimumSize(300, 180)
        self.frame_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.frame_label.roiSelected.connect(self.roiSelected.emit)
        layout.addWidget(self.frame_label, 1)
        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        self.overlay_check = QCheckBox("显示识别框")
        self.overlay_check.setChecked(True)
        self.overlay_check.toggled.connect(self._handle_overlay_toggled)
        self.always_on_top_check = QCheckBox("始终置顶")
        self.always_on_top_check.toggled.connect(self._handle_always_on_top_toggled)
        controls.addWidget(self.overlay_check)
        controls.addWidget(self.always_on_top_check)
        controls.addStretch(1)
        layout.addLayout(controls)

    def _handle_overlay_toggled(self, enabled: bool) -> None:
        self.frame_label.set_overlay_enabled(bool(enabled))
        self.overlayChanged.emit(bool(enabled))
        self._refresh_frame()

    def _handle_always_on_top_toggled(self, enabled: bool) -> None:
        was_visible = self.isVisible()
        window_geometry = self.geometry()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, bool(enabled))
        if was_visible:
            self.setGeometry(window_geometry)
            self.show()
            self.raise_()
        self.alwaysOnTopChanged.emit(bool(enabled))

    def set_overlay_enabled(self, enabled: bool) -> None:
        self.overlay_check.setChecked(bool(enabled))

    def overlay_enabled(self) -> bool:
        return self.overlay_check.isChecked()

    def set_selection_enabled(self, enabled: bool) -> None:
        self.frame_label.set_selection_enabled(bool(enabled))

    def selection_enabled(self) -> bool:
        return self.frame_label.selection_enabled()

    def set_ocr_overlay(self, field: str, region: OcrRegion | tuple[int, int, int, int]) -> None:
        self.frame_label.set_ocr_overlay(field, region)

    def clear_ocr_overlay(self) -> None:
        self.frame_label.clear_ocr_overlay()

    def set_always_on_top(self, enabled: bool) -> None:
        self.always_on_top_check.setChecked(bool(enabled))

    def always_on_top(self) -> bool:
        return self.always_on_top_check.isChecked()

    def set_frames(self, raw_frame: object, annotated_frame: object | None = None) -> None:
        self._raw_frame = raw_frame
        self._annotated_frame = annotated_frame if annotated_frame is not None else raw_frame
        self._refresh_frame()

    def _refresh_frame(self) -> None:
        frame = self._annotated_frame if self.overlay_enabled() else self._raw_frame
        if frame is None:
            return
        try:
            import cv2

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            height, width, channel = rgb.shape
            image = QImage(rgb.data, width, height, channel * width, QImage.Format.Format_RGB888).copy()
            pixmap = QPixmap.fromImage(image)
        except Exception:
            return
        target = self.frame_label.contentsRect().size()
        if target.width() <= 0 or target.height() <= 0:
            return
        scaled, logical_size = _scale_preview_pixmap(
            pixmap,
            target,
            self.frame_label.devicePixelRatioF(),
        )
        contents = self.frame_label.contentsRect()
        left = contents.left() + (contents.width() - logical_size.width()) // 2
        top = contents.top() + (contents.height() - logical_size.height()) // 2
        self.frame_label.set_image_geometry(
            pixmap.width(),
            pixmap.height(),
            QRect(left, top, logical_size.width(), logical_size.height()),
        )
        self.frame_label.setPixmap(scaled)

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self._refresh_frame()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        # Closing PiP only hides this display; the Broker remains connected.
        event.ignore()
        self.hide()


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    def wheelEvent(self, event) -> None:  # noqa: N802
        event.ignore()


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


class ShinyThresholdCalibrationWorker(QObject):
    finished = Signal(float)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        capture_frame: Callable[[], object],
        *,
        first_keyword: str | tuple[str, ...] = "出现了！",
        second_keyword: str | tuple[str, ...] = ("去吧", "上吧"),
        second_capture_frame: Callable[[], object] | None = None,
    ) -> None:
        super().__init__()
        self._capture_frame = capture_frame
        self._first_keyword = first_keyword
        self._second_keyword = second_keyword
        self._second_capture_frame = second_capture_frame
        self._cancel = threading.Event()

    def run(self) -> None:
        try:
            result = measure_keyword_interval(
                self._capture_frame,
                read_ocr_text,
                first_keyword=self._first_keyword,
                second_keyword=self._second_keyword,
                second_capture_frame=self._second_capture_frame,
                should_stop=self._cancel.is_set,
                timeout_seconds=45.0,
                poll_interval_seconds=0.1,
            )
        except RuntimeError as exc:
            if self._cancel.is_set():
                self.cancelled.emit()
            else:
                self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.finished.emit(result.interval_seconds)

    def stop(self) -> None:
        self._cancel.set()


class OcrWarmupThread(QThread):
    """Own the non-blocking OCR warm-up lifecycle for one main window."""

    completed = Signal(bool, str)

    def __init__(self, warm_up: Callable[[], None], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._warm_up = warm_up

    def run(self) -> None:
        if self.isInterruptionRequested():
            return
        try:
            self._warm_up()
        except Exception as exc:
            success = False
            message = f"OCR预热失败: {exc}"
        else:
            success = True
            message = "OCR预热完成"
        if not self.isInterruptionRequested():
            self.completed.emit(success, message)


class OcrTaskThread(QThread):
    """Run one on-demand OCR action while keeping its lifetime owned by the window."""

    completed = Signal(bool, object)

    def __init__(
        self,
        task: Callable[[Callable[[], bool]], object],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._task = task

    def run(self) -> None:
        if self.isInterruptionRequested():
            return
        try:
            result = self._task(self.isInterruptionRequested)
        except BaseException as exc:
            success = False
            result = exc
        else:
            success = True
        if not self.isInterruptionRequested():
            self.completed.emit(success, result)


class CaptureBrokerStartThread(QThread):
    """Wait for the Broker's first frame without blocking the Qt event loop."""

    completed = Signal(bool, object)

    def __init__(
        self,
        start_broker: Callable[[], bool],
        probe_client: Callable[[], object],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._start_broker = start_broker
        self._probe_client = probe_client

    def run(self) -> None:
        try:
            if not self._start_broker():
                raise ProjectXsIntegrationError("5 秒内未收到采集卡首帧")
            if self.isInterruptionRequested():
                raise ProjectXsIntegrationError("视频源连接已取消")
            client = self._probe_client()
            try:
                if self.isInterruptionRequested():
                    raise ProjectXsIntegrationError("视频源连接已取消")
            finally:
                close = getattr(client, "close", None)
                if callable(close):
                    close()
        except BaseException as exc:
            self.completed.emit(False, exc)
        else:
            self.completed.emit(True, None)


class MainWindow(QMainWindow):
    autoCaptureFrameChanged = Signal(object)
    autoCaptureProgressChanged = Signal(int, int)
    captureKeepAwakeRequested = Signal(int, int)
    autoSeedCaptured = Signal(object)
    autoScriptStarted = Signal(str)
    autoScriptFinished = Signal(object)
    autoScriptFailed = Signal(str)
    autoHistoryEvent = Signal(str, object)
    ocrRegionSelected = Signal(str, object)
    tidOcrRegionSelected = Signal(object)
    ocrWarmupFinished = Signal(bool, str)
    ocrFullTestFinished = Signal(bool, str)
    runLogFailed = Signal(str)
    uiCallRequested = Signal(object, object, object, object)
    easyConImageSearchResultChanged = Signal(int, int, object)

    def __init__(
        self,
        profile_settings: QSettings | None = None,
        run_log_manager: RunLogManager | None = None,
        capture_broker_process: object | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle(APP_DISPLAY_TITLE)
        if app_icon_path().exists():
            self.setWindowIcon(QIcon(str(app_icon_path())))
        # Set a compact floor before constructing the pages.  Page-level
        # scroll areas below keep the layout usable when the screen is shorter
        # than the desktop-oriented default size.
        self.setMinimumSize(MAIN_WINDOW_COMPACT_MIN_SIZE)
        self.resize(MAIN_WINDOW_DEFAULT_SIZE)
        self.lang = "zh"
        self._run_log_manager = run_log_manager or RunLogManager()
        self._run_log_manager.set_error_callback(self._queue_run_log_failure)
        self.runLogFailed.connect(self._handle_run_log_failure, Qt.ConnectionType.QueuedConnection)
        self._profile_settings = profile_settings or QSettings("auto-bdsp-rng", "MainWindowProfile")
        self._window_geometry_restored = False
        self._window_restore_maximized = False
        self._profile_version = GameVersion.BD
        self._active_record: StaticEncounterRecord | None = None
        self._records: tuple[StaticEncounterRecord, ...] = ()
        self._states: list[State8] = []
        self._eye_image_path: Path | None = None
        self._latest_preview_frame: object | None = None
        self._latest_annotated_preview_frame: object | None = None
        self._latest_easycon_image_search_result: object | None = None
        self._easycon_image_result_observer_serial = 0
        self._easycon_image_result_observers: dict[
            int,
            tuple[int, int, Callable[[object], None]],
        ] = {}
        self._easycon_image_result_observers_lock = threading.Lock()
        self._capture_broker_process = capture_broker_process
        self._capture_broker_start_thread: CaptureBrokerStartThread | None = None
        self._capture_broker_attempt = 0
        self._video_source_connected = False
        self._video_source_connecting = False
        self._video_source_cancel_requested = False
        self._video_source_stop_pending = False
        self._video_source_stop_error: str | None = None
        self._video_source_pending_status = "未连接"
        self._video_source_generation = 0
        self._easycon_run_generation = 0
        self._preview_annotation_error_reported = False
        self._picture_in_picture: PictureInPicturePreview | None = None
        self._roi_before_selection: tuple[int, int, int, int] | None = None
        self._ocr_selection_field: str | None = None
        self._ocr_settings_dialog: OcrSettingsDialog | None = None
        self._tid_ocr_dialog: TidOcrDialog | None = None
        self._ocr_warmup_running = False
        self._ocr_warmup_thread: OcrWarmupThread | None = None
        self._ocr_warmup_result: tuple[bool, str] | None = None
        self._ocr_after_warmup: tuple[str, Callable[[], None]] | None = None
        self._ocr_task_thread: OcrTaskThread | None = None
        self._ocr_task_label: str | None = None
        self._ocr_task_completed: Callable[[bool, object], None] | None = None
        self._ocr_shutdown_requested = False
        self._is_closing = False
        self._startup_update_check_scheduled = False
        self._startup_update_check_started = False
        self._ocr_full_test_running = False
        self._selection_mode: str | None = None
        self._selection_preview_frame: object | None = None
        self._resume_preview_after_selection = False
        self._resume_preview_after_capture = False
        self._preview_timer = QTimer(self)
        self._preview_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._preview_timer.setInterval(PREVIEW_REFRESH_INTERVAL_MS)
        self._preview_timer.timeout.connect(self._update_preview_frame)
        self._preview_capture: PreviewFrameCapture | None = None
        self._capture_timer = QTimer(self)
        self._capture_timer.setInterval(100)
        self._capture_timer.timeout.connect(self._poll_capture_thread)
        self._capture_cancel = threading.Event()
        self._capture_lock = threading.Lock()
        self._capture_thread: threading.Thread | None = None
        self._capture_result: object | None = None
        self._capture_error: Exception | None = None
        self._capture_frame: object | None = None
        self._shiny_calibration_thread: QThread | None = None
        self._shiny_calibration_worker: ShinyThresholdCalibrationWorker | None = None
        self._static_generation_thread: threading.Thread | None = None
        self._static_generation_result: tuple[StaticEncounterRecord, list[State8]] | None = None
        self._static_generation_error: Exception | None = None
        self._static_generation_timer = QTimer(self)
        self._static_generation_timer.setInterval(50)
        self._static_generation_timer.timeout.connect(self._poll_static_generation_thread)
        self._capture_mode = "seed"
        self._capture_progress = (0, DEFAULT_BLINK_COUNT)
        self._advance_timer = QTimer(self)
        self._advance_timer.setInterval(1018)
        self._advance_timer.timeout.connect(self._advance_tick)
        self._tracked_advances = 0
        self._advance_step = 1
        self._advance_counter = ProjectXsAdvanceCounter()
        self._advance_counter.reset(current_advances=0, npc=0, now=time.monotonic())
        self.easyConImageSearchResultChanged.connect(
            self._handle_easycon_image_search_result,
            Qt.ConnectionType.QueuedConnection,
        )
        self._build_actions()
        self._build_ui()
        self._restore_profile_settings()
        self._connect_auto_rng_sync_signals()
        self._apply_theme()
        self._refresh_config_list()
        self._refresh_encounters()
        self._sync_seed64_from_state32()
        self._apply_language()
        self._restore_window_geometry()
        self.statusBar().showMessage(self._text("ready"))
        QTimer.singleShot(0, self._maybe_show_startup_notice)

    def _connect_auto_rng_sync_signals(self) -> None:
        self.autoCaptureFrameChanged.connect(self._handle_auto_capture_frame)
        self.autoCaptureProgressChanged.connect(self._handle_auto_capture_progress)
        self.captureKeepAwakeRequested.connect(self._handle_capture_keep_awake_requested)
        self.autoSeedCaptured.connect(self._handle_auto_seed_captured)
        self.autoScriptStarted.connect(self.easycon_tab.begin_external_native_script)
        self.autoScriptStarted.connect(self._begin_easycon_image_search_run)
        self.easycon_tab.nativeScriptStarted.connect(self._begin_easycon_image_search_run)
        self.autoScriptFinished.connect(self.easycon_tab.finish_external_native_script)
        self.autoScriptFailed.connect(self.easycon_tab.fail_external_native_script)
        self.autoHistoryEvent.connect(self._handle_auto_history_event)
        self.uiCallRequested.connect(self._handle_ui_call_requested)

    def _call_on_ui_thread(self, callback: object) -> object:
        if QThread.currentThread() == self.thread():
            return callback()  # type: ignore[operator]
        done = threading.Event()
        result: list[object] = []
        errors: list[BaseException] = []
        self.uiCallRequested.emit(callback, result, errors, done)
        done.wait()
        if errors:
            raise errors[0]
        return result[0] if result else None

    def _finalize_auto_script_result(self, result: object, name: str) -> object:
        def finish_script() -> None:
            try:
                self.autoScriptFinished.emit(result)
            finally:
                self.easycon_tab.release_native_script_run()

        self._call_on_ui_thread(finish_script)
        failure_message = _auto_script_failure_message(result, name)
        if failure_message is not None:
            raise RuntimeError(failure_message)
        return result

    def _fail_auto_script(self, error: BaseException) -> None:
        def fail_script() -> None:
            try:
                self.autoScriptFailed.emit(str(error))
            finally:
                self.easycon_tab.release_native_script_run()

        self._call_on_ui_thread(fail_script)

    def _prepare_auto_easycon_script(self, script_name: str) -> object:
        if not self._video_source_connected:
            raise RuntimeError("请先在 Seed 捕捉页面连接视频源")
        native_status = self.easycon_tab._native_status()
        if native_status != EasyConStatus.BRIDGE_CONNECTED:
            if native_status == EasyConStatus.RUNNING:
                raise RuntimeError("已有伊机控脚本正在运行")
            raise RuntimeError("请先连接伊机控")
        if not self.easycon_tab.reserve_native_script_run():
            raise RuntimeError("已有伊机控脚本正在运行")
        try:
            backend = self.easycon_tab._ensure_native_backend()
            self.autoScriptStarted.emit(script_name)
        except BaseException:
            self.easycon_tab.release_native_script_run()
            raise
        return backend

    @staticmethod
    def _native_script_context(config: object, script_text: str, name: str) -> tuple[str, Path]:
        supplied = Path(name)
        if supplied.is_absolute() or supplied.parent != Path("."):
            return supplied.name, supplied.parent

        matching_paths: list[Path] = []
        for key, value in vars(config).items():
            if not key.endswith("_script_path") or value is None:
                continue
            candidate = Path(value)
            if candidate.name == supplied.name:
                matching_paths.append(candidate)
        if len(matching_paths) > 1:
            for candidate in matching_paths:
                try:
                    if candidate.read_text(encoding="utf-8") == script_text:
                        return candidate.name, candidate.parent
                except OSError:
                    continue
        if matching_paths:
            return matching_paths[0].name, matching_paths[0].parent

        configured_dir = getattr(config, "script_dir", None)
        return supplied.name, Path(configured_dir) if configured_dir is not None else script_directory()

    def _handle_ui_call_requested(
        self,
        callback: object,
        result: list[object],
        errors: list[BaseException],
        done: threading.Event,
    ) -> None:
        try:
            result.append(callback())  # type: ignore[operator]
        except BaseException as exc:
            errors.append(exc)
        finally:
            done.set()

    def _build_actions(self) -> None:
        generate = QAction("Generate", self)
        generate.setShortcut("Ctrl+R")
        generate.triggered.connect(self.generate_results)
        self.addAction(generate)

        copy = QAction("Copy Results", self)
        copy.triggered.connect(self.copy_results)
        self.addAction(copy)

    def _build_ui(self) -> None:
        from auto_bdsp_rng.ui.id_panel import IdPanel

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(14, 12, 14, 12)
        root_layout.setSpacing(10)

        self.video_source_dialog = self._build_video_source_dialog()

        header = QFrame()
        header.setObjectName("Header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 10, 16, 10)
        self.title_label = QLabel()
        self.title_label.setObjectName("WindowTitle")
        self.auto_loop_badge = QLabel("循环 0")
        self.auto_loop_badge.setObjectName("Badge")
        self.auto_phase_badge = QLabel("阶段 空闲")
        self.auto_phase_badge.setObjectName("Badge")
        self.auto_advance_badge = QLabel("advance 0")
        self.auto_advance_badge.setObjectName("Badge")
        self.video_source_header_button = QToolButton()
        self.video_source_header_button.setObjectName("VideoSourceHeaderButton")
        self.video_source_header_button.setText("视频源 未连接")
        self.video_source_header_button.setIcon(_status_dot_icon("#9CA3AF"))
        self.video_source_header_button.setIconSize(QSize(12, 12))
        self.video_source_header_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.video_source_header_button.setFixedSize(190, 30)
        self.video_source_header_button.setToolTip("打开视频源设置")
        self.video_source_header_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.video_source_header_button.clicked.connect(self.show_video_source_dialog)
        self.help_button = QToolButton()
        self.help_button.setObjectName("HelpMenuButton")
        self.help_button.setText("帮助 ▾")
        self.help_button.setCursor(Qt.CursorShape.PointingHandCursor)
        header_layout.addWidget(self.title_label)
        header_layout.addStretch(1)
        header_layout.addWidget(self.auto_loop_badge)
        header_layout.addSpacing(8)
        header_layout.addWidget(self.auto_phase_badge)
        header_layout.addSpacing(8)
        header_layout.addWidget(self.auto_advance_badge)
        header_layout.addSpacing(8)
        header_layout.addWidget(self.video_source_header_button)
        header_layout.addSpacing(8)
        header_layout.addWidget(self.help_button)
        root_layout.addWidget(header)

        self.tabs = _ResponsiveTabWidget()
        self.tabs.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.project_xs_tab = self._build_project_xs_tab()
        self.bdsp_tab = self._build_bdsp_tab()
        self.easycon_tab = EasyConPanel(
            run_log_sink=self._run_log_sink("伊机控"),
            video_source_connected=lambda: self._video_source_connected,
            frame_client_factory=self._new_broker_client,
        )
        self._install_easycon_image_result_callback(
            self._video_source_generation,
            self._easycon_run_generation,
        )
        self.auto_rng_tab = AutoRngPanel(run_log_sink=self._run_log_sink("自动定点"))
        self.auto_tid_rng_tab = AutoTidRngPanel(run_log_sink=self._run_log_sink("自动 TID"))
        self.history_tab = HistoryPanel()
        self.id_tab = IdPanel(status_callback=lambda text: self.statusBar().showMessage(text))
        self.id_tab.seedChanged.connect(self._sync_state32_from_id_seed64)
        self.auto_rng_tab.startRequested.connect(self._start_auto_rng)
        self.auto_rng_tab.autoProgressChanged.connect(self._apply_auto_rng_header_progress)
        self.auto_rng_tab.runStateChanged.connect(self._set_ocr_automation_active)
        self.auto_rng_tab.ivCalculatorRequested.connect(self.open_iv_calculator)
        self.auto_rng_tab.captureInfoRequested.connect(self.open_ocr_settings)
        self.auto_rng_tab.captureLog.connect(self.auto_rng_tab.add_log)
        self.auto_rng_tab.captureError.connect(
            lambda message: self.auto_rng_tab.add_log(message, level="ERROR")
        )
        self.auto_rng_tab.requestStatsCapture.connect(self._on_request_stats_capture)
        self.auto_tid_rng_tab.startRequested.connect(self._start_auto_tid_rng)
        self.auto_tid_rng_tab.progressChanged.connect(self._apply_auto_rng_header_progress)
        self.auto_tid_rng_tab.ocrSettingsRequested.connect(self.open_tid_ocr_settings)
        self.tidOcrRegionSelected.connect(self.auto_tid_rng_tab.set_ocr_region)
        self.tabs.addTab(self.auto_rng_tab, self._text("auto_rng"))
        self.tabs.addTab(self.auto_tid_rng_tab, self._text("auto_tid_rng"))
        self.tabs.addTab(self.project_xs_tab, self._text("project_xs"))
        self.tabs.addTab(self.bdsp_tab, self._text("bdsp_search"))
        self.tabs.addTab(self.easycon_tab, self._text("easycon"))
        self.tabs.addTab(self.history_tab, "历史记录")
        self.tabs.currentChanged.connect(lambda _index: QTimer.singleShot(0, self._update_responsive_layout))
        root_layout.addWidget(self.tabs, 1)
        _make_labels_copyable(self.tabs)

        self.setCentralWidget(root)
        self.setStatusBar(QStatusBar())
        self.update_controller = UpdateController(self)
        self.help_menu_controller = HelpMenuController(
            self,
            run_log_enabled=self._run_log_manager.enabled,
            set_run_log_enabled=self._set_run_log_enabled,
            open_run_log_dir=self._open_run_log_dir,
            check_updates=self.update_controller.check_for_updates,
            auto_update_check_enabled=is_auto_update_check_enabled(),
            set_auto_update_check_enabled=set_auto_update_check_enabled,
        )
        self.help_menu_controller.install(self.help_button)
        self.update_controller.busyChanged.connect(
            lambda busy: self.help_menu_controller.check_updates_action.setEnabled(not busy)
        )
        self.update_controller.silentCheckCompleted.connect(
            self._handle_silent_update_check_completed
        )
        self.update_controller.silentCheckFailed.connect(self._handle_silent_update_check_failed)

    def _run_log_sink(self, source: str) -> Callable[[str, str], None]:
        def write(level: str, message: str) -> None:
            self._write_run_log(source, message, level=level)

        return write

    def _write_run_log(self, source: str, message: object, *, level: str = "INFO") -> None:
        self._run_log_manager.write(source, str(message), level=level)

    def _handle_silent_update_check_completed(self, plan: object) -> None:
        current_version = str(getattr(plan, "current_version", __version__))
        latest_version = str(getattr(plan, "latest_version", "未知"))
        result = "发现新版本" if bool(getattr(plan, "update_available", False)) else "无需更新"
        self._write_run_log(
            "软件更新",
            f"启动自动检查完成；当前版本 {current_version}；线上版本 {latest_version}；结果：{result}",
        )

    def _handle_silent_update_check_failed(self, message: str) -> None:
        self._write_run_log(
            "软件更新",
            f"启动自动检查失败：{message}",
            level="WARNING",
        )

    def _queue_run_log_failure(self, message: str) -> None:
        # The callback may run on a worker thread or after Qt starts shutting down.
        # Persist the safe state before relying on the queued UI notification.
        try:
            set_run_log_enabled(False)
        except OSError:
            pass
        self.runLogFailed.emit(message)

    def _set_run_log_enabled(self, enabled: bool) -> bool:
        if enabled:
            try:
                path = self._run_log_manager.enable()
                set_run_log_enabled(True)
            except (OSError, RunLogError) as exc:
                self._run_log_manager.disable()
                try:
                    set_run_log_enabled(False)
                except OSError:
                    pass
                QMessageBox.warning(self, "运行日志", f"无法开启运行日志：\n{exc}")
                self.statusBar().showMessage("运行日志开启失败", 5000)
                return False
            self._write_run_log(
                "应用",
                f"自动保存运行日志已开启；版本 {__version__}；模式 "
                f"{'打包版' if getattr(sys, 'frozen', False) else '源码版'}",
            )
            self.statusBar().showMessage(f"运行日志已开启：{path}", 5000)
            return True

        self._write_run_log("应用", "自动保存运行日志已关闭")
        self._run_log_manager.disable()
        try:
            set_run_log_enabled(False)
        except OSError as exc:
            QMessageBox.warning(self, "运行日志", f"运行日志已关闭，但无法保存开关状态：\n{exc}")
        self.statusBar().showMessage("运行日志已关闭", 3000)
        return False

    def _open_run_log_dir(self) -> None:
        path = self._run_log_manager.directory
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "运行日志", f"无法创建日志目录：\n{exc}")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve()))):
            QMessageBox.warning(self, "运行日志", f"无法打开日志目录：\n{path}")

    def _handle_run_log_failure(self, message: str) -> None:
        self._run_log_manager.disable()
        settings_error: OSError | None = None
        try:
            set_run_log_enabled(False)
        except OSError as exc:
            settings_error = exc
        controller = getattr(self, "help_menu_controller", None)
        if controller is not None:
            controller.set_run_log_state(False)
        detail = f"运行日志写入失败，已停止保存：\n{message}"
        if settings_error is not None:
            detail += f"\n\n同时无法保存关闭状态，下次启动可能再次尝试：\n{settings_error}"
        QMessageBox.warning(self, "运行日志", detail)
        self.statusBar().showMessage("运行日志写入失败，已停止保存", 5000)

    def show_run_log_startup_error(self, message: str) -> None:
        QMessageBox.warning(self, "运行日志", f"上次启用了运行日志，但本次无法创建日志文件：\n{message}")
        self.statusBar().showMessage("运行日志启动失败", 5000)

    def show_legacy_script_migration_result(self, backup_count: int) -> None:
        backup_dir = script_directory() / ".legacy-internal-backup"
        QMessageBox.information(
            self,
            "脚本目录已整理",
            f"已移除旧版 _internal\\script。\n"
            f"检测到 {backup_count} 个与外层脚本不同的文件，已备份到：\n{backup_dir}\n\n"
            "软件现在只使用 exe 同级的 script 目录。备份目录不会被程序执行；"
            "如需恢复，请先对比内容，再复制到外层 script 目录。",
        )
        self.statusBar().showMessage("旧版内部脚本已备份并清理", 5000)

    def show_legacy_script_migration_error(self, message: str) -> None:
        QMessageBox.warning(
            self,
            "脚本目录整理失败",
            "无法完全清理旧版 _internal\\script。文件内容仍会保留，可能位于旧目录，"
            "也可能位于外层 script\\.legacy-internal-backup 中的迁移暂存目录。"
            f"下次启动会自动重试，请勿手动删除这些目录。\n\n{message}",
        )
        self.statusBar().showMessage("旧版内部脚本清理失败", 5000)

    def schedule_startup_update_check(self) -> None:
        if self._startup_update_check_scheduled or self._startup_update_check_started:
            return
        self._startup_update_check_scheduled = True
        QTimer.singleShot(STARTUP_UPDATE_CHECK_DELAY_MS, self._start_startup_update_check)

    def _start_startup_update_check(self) -> None:
        if self._is_closing or self._startup_update_check_started:
            return
        if QApplication.activeModalWidget() is not None:
            QTimer.singleShot(
                STARTUP_UPDATE_CHECK_MODAL_RETRY_MS,
                self._start_startup_update_check,
            )
            return
        self._startup_update_check_started = True
        if not is_auto_update_check_enabled():
            return
        self._write_run_log("软件更新", "开始启动自动检查")
        self.update_controller.check_for_updates(silent=True)

    def _maybe_show_startup_notice(self) -> None:
        if not should_show_startup_notice():
            return
        dialog = StartupNoticeDialog(self)
        dialog.setModal(True)

        def persist_choice() -> None:
            if dialog.dont_show_again.isChecked():
                set_startup_notice_acknowledged(True)

        dialog.accepted.connect(persist_choice)
        dialog.show()
        self._startup_notice_dialog = dialog

    def _build_project_xs_tab(self) -> QWidget:
        splitter = _ResponsiveProjectSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("ProjectXsSplitter")
        splitter.setChildrenCollapsible(False)

        # Left side follows the compact single-column layout from 0940b1b.
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 8, 0)
        left_layout.setSpacing(10)
        self.capture_group = self._build_blink_group()
        self.seed_group = self._build_seed_group()
        left_layout.addWidget(self.capture_group)
        left_layout.addWidget(self.seed_group)
        left_layout.addStretch(1)
        self._project_xs_left_layout = left_layout
        left.setMinimumSize(0, 0)
        left_scroll = _scrollable_page(
            left,
            object_name="ProjectXsControlsScroll",
            # The capture form contains a few deliberately fixed-width
            # controls.  Keep a horizontal escape hatch when the splitter is
            # narrower than their natural width instead of silently clipping
            # the rightmost buttons.
            horizontal=True,
        )
        left_scroll.setMinimumSize(0, 0)
        left_scroll.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.project_xs_controls_scroll = left_scroll

        # 右侧：状态条（紧凑） + 预览（下部）
        self.status_group = self._build_project_status_group()
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)
        right_layout.addWidget(self.status_group)
        right_layout.addWidget(self._build_preview_panel(), 1)
        self._project_xs_right_layout = right_layout
        right.setMinimumSize(0, 0)
        right.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)

        splitter.addWidget(left_scroll)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([PROJECT_XS_HORIZONTAL_LEFT_WIDTH, 1050])
        self._project_xs_vertical: bool | None = None
        return splitter

    def _build_bdsp_tab(self) -> QWidget:
        panel = QWidget()
        panel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # Keep the profile/parameter/results structure intact inside one
        # scroll viewport.  Several BDSP filter groups have fixed-width
        # controls and a natural height larger than a compact monitor; a
        # viewport lets users reach those controls without compressing rows
        # until they overlap.
        content = QWidget()
        content.setObjectName("BdspContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(8)

        # 第 1 行：存档信息 (90-100px)
        self.profile_group = self._build_profile_group()
        self.profile_group.setMaximumHeight(106)
        content_layout.addWidget(self.profile_group)

        # 第 2 行：参数区（三列：乱数信息 + 设置 + 筛选项）
        params_widget = QWidget()
        params_row = QHBoxLayout(params_widget)
        params_row.setContentsMargins(0, 0, 0, 0)
        params_row.setSpacing(10)
        self.rng_info_group = self._build_rng_info_group()
        self.rng_info_group.setMinimumWidth(240)
        self.static_group = self._build_static_group()
        self.static_group.setMinimumWidth(260)
        self.filter_group = self._build_filter_group()
        params_row.addWidget(self.rng_info_group)
        params_row.addWidget(self.static_group)
        params_row.addWidget(self.filter_group, 1)
        content_layout.addWidget(params_widget)

        # 第 3 行 + 第 4 行：结果表格（工具栏 + 表格）
        self.results_panel = self._build_results()
        content_layout.addWidget(self.results_panel, 1)

        self.bdsp_content_scroll = _scrollable_page(
            content,
            object_name="BdspContentScroll",
            horizontal=True,
        )
        self.bdsp_content_scroll.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Expanding,
        )
        layout.addWidget(self.bdsp_content_scroll, 1)
        return panel

    def _build_project_status_group(self) -> QGroupBox:
        group = QGroupBox("配置")
        group.setMaximumHeight(150)
        group.setMaximumWidth(740)

        outer = QGridLayout(group)
        outer.setContentsMargins(12, 8, 12, 8)
        outer.setHorizontalSpacing(10)
        outer.setVerticalSpacing(10)

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
        for widget in (
            self.timer_label,
            self.timer_value,
            self.x_to_advance_label,
            self.x_to_advance,
            self.advance_button,
        ):
            widget.hide()

        self.seed_config_combo = QComboBox()
        self.seed_config_combo.setFixedHeight(32)
        self.seed_config_combo.setMinimumWidth(340)
        self.reidentify_config_combo = QComboBox()
        self.reidentify_config_combo.setFixedHeight(32)
        self.reidentify_config_combo.setMinimumWidth(340)
        self.refresh_seed_configs_button = QPushButton("刷新")
        self.refresh_seed_configs_button.setFixedHeight(32)
        self.refresh_seed_configs_button.setFixedWidth(80)
        self.refresh_seed_configs_button.clicked.connect(self._refresh_config_list)
        self.refresh_seed_configs_button.hide()

        outer.addWidget(self.progress_label, 0, 0)
        outer.addWidget(self.progress_value, 0, 1)
        outer.addWidget(QLabel("Seed 配置"), 0, 2)
        outer.addWidget(self.seed_config_combo, 0, 3)
        outer.addWidget(self.advances_label, 1, 0)
        outer.addWidget(self.advances_value, 1, 1)
        outer.addWidget(QLabel("校正配置"), 1, 2)
        outer.addWidget(self.reidentify_config_combo, 1, 3)
        outer.setColumnStretch(3, 1)
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
        group = QGroupBox("捕捉配置")
        layout = QGridLayout(group)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(6)
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
        self.tidsid_button = QPushButton("TID/SID 测种")
        self.tidsid_button.setFixedHeight(30)
        self.tidsid_button.clicked.connect(self.capture_tidsid_seed)
        self.save_config_button = QPushButton()
        self.save_config_button.clicked.connect(self.save_current_config)
        self.raw_screenshot_button = QPushButton()
        self.raw_screenshot_button.clicked.connect(self.start_eye_capture_selection)
        self.select_roi_button = QPushButton()
        self.select_roi_button.clicked.connect(self.start_roi_selection)
        self.calibrate_shiny_threshold_button = QPushButton()
        self.calibrate_shiny_threshold_button.clicked.connect(self.calibrate_shiny_threshold)

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

        # Keep legacy Project_Xs fields alive for lossless config load/save.
        for legacy_widget in (
            self.monitor_window,
            self.window_prefix,
            self.camera,
            self.display_percent,
        ):
            legacy_widget.setParent(group)
            legacy_widget.hide()

        compact_fields = [
            self.config_combo,
            self.window_prefix,
            self.camera,
            self.x,
            self.y,
            self.w,
            self.h,
            self.threshold,
            self.white_delay,
            self.advance_delay,
            self.advance_delay_2,
            self.npc_count,
            self.timeline_npc,
            self.pokemon_npc,
            self.display_percent,
        ]
        compact_field_style = (
            "QLineEdit, QComboBox, QDoubleSpinBox {"
            " min-height: 30px; max-height: 30px; padding: 0 8px; border-radius: 6px;"
            "}"
        )
        for widget in compact_fields:
            widget.setFixedHeight(32)
            widget.setStyleSheet(compact_field_style)
        compact_button_style = "QPushButton { min-height: 32px; max-height: 32px; padding: 0 10px; border-radius: 6px; }"
        for button in (
            self.browse_button,
            self.preview_button,
            self.tidsid_button,
            self.capture_button,
            self.reidentify_button,
            self.select_roi_button,
            self.save_config_button,
            self.raw_screenshot_button,
        ):
            button.setFixedHeight(34)
            button.setStyleSheet(compact_button_style)
        self.tidsid_button.setFixedHeight(30)
        self.tidsid_button.setStyleSheet("QPushButton { min-height: 30px; max-height: 30px; padding: 0 10px; border-radius: 6px; }")
        self.monitor_window.setFixedHeight(28)
        self.reidentify_1_pk_npc.setFixedHeight(28)

        # 配置选择行：配置 [下拉] [浏览]
        layout.addWidget(self.config_label, 0, 0)
        layout.addWidget(self.config_combo, 0, 1, 1, 2)
        layout.addWidget(self.browse_button, 0, 3)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(8)
        button_row.addWidget(self.preview_button)
        button_row.addWidget(self.capture_button)
        button_row.addWidget(self.reidentify_button)
        layout.addLayout(button_row, 1, 0, 1, 4)

        checks_row = QHBoxLayout()
        checks_row.setContentsMargins(0, 0, 0, 0)
        checks_row.setSpacing(12)
        checks_row.addWidget(self.reidentify_1_pk_npc)
        checks_row.addStretch(1)
        checks_row.addWidget(self.calibrate_shiny_threshold_button)
        layout.addWidget(self.tidsid_button, 2, 0)
        layout.addLayout(checks_row, 2, 1, 1, 3)
        layout.addWidget(self.select_roi_button, 3, 1, 1, 3)
        self._add_form_row(layout, 4, "threshold", self.threshold)
        self._add_form_row(layout, 5, "time_delay", self.white_delay)
        self._add_form_row(layout, 6, "advance_delay", self.advance_delay)
        self._add_form_row(layout, 7, "advance_delay_2", self.advance_delay_2)
        self._add_form_row(layout, 8, "npcs", self.npc_count)
        self._add_form_row(layout, 9, "timeline_npcs", self.timeline_npc)
        self._add_form_row(layout, 10, "pokemon_npcs", self.pokemon_npc)
        layout.addWidget(self.save_config_button, 11, 2)
        layout.addWidget(self.raw_screenshot_button, 11, 3)
        return group

    def _add_form_row(self, layout: QGridLayout, row: int, key: str, widget: QWidget) -> None:
        label = QLabel()
        label.setProperty("i18n", key)
        layout.addWidget(label, row, 0)
        layout.addWidget(widget, row, 1, 1, 3)

    def _build_seed_group(self) -> QGroupBox:
        group = QGroupBox("Seed")
        layout = QGridLayout(group)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(8)
        self.seed32_inputs = [QLineEdit(group) for _ in range(4)]
        for box in self.seed32_inputs:
            box.setReadOnly(True)
            box.setMaxLength(8)
            box.setPlaceholderText("—")
            box.setVisible(False)
        self.seed64_outputs = [QLineEdit() for _ in range(2)]
        for output in self.seed64_outputs:
            output.setReadOnly(True)
            output.setObjectName("Readonly")
            output.setFixedHeight(32)

        layout.addWidget(QLabel("Seed0"), 0, 0)
        layout.addWidget(self.seed64_outputs[0], 0, 1, 1, 3)
        layout.addWidget(QLabel("Seed1"), 1, 0)
        layout.addWidget(self.seed64_outputs[1], 1, 1, 1, 3)
        return group

    def _build_rng_info_group(self) -> QGroupBox:
        group = QGroupBox("乱数信息")
        group.setMinimumWidth(240)
        grid = QGridLayout(group)
        grid.setContentsMargins(12, 10, 12, 10)
        grid.setVerticalSpacing(6)
        grid.setHorizontalSpacing(8)

        self.lead_label = QLabel("队首")
        self.lead_label.setFixedWidth(64)
        self.lead_combo = _LeadMenuComboBox()
        self.lead_combo.setFixedHeight(30)

        self.bdsp_seed64_inputs = [QLineEdit() for _ in range(2)]
        for input_box in self.bdsp_seed64_inputs:
            input_box.setMaxLength(16)
            input_box.editingFinished.connect(self._sync_state32_from_bdsp_seed64)
            input_box.setFixedHeight(30)

        self.initial_advances = self._spin(0, 10_000_000, 0)
        self.initial_advances.setFixedHeight(30)
        self.max_advances = self._spin(0, 1_000_000_000, 100_000)
        self.max_advances.setFixedHeight(30)
        self.offset = self._spin(0, 1_000_000, 0)
        self.offset.setFixedHeight(30)

        LABEL_W = 64
        for label_text, widget, row in [
            (self.lead_label,        self.lead_combo,            0),
            (QLabel("Seed 0"),       self.bdsp_seed64_inputs[0], 1),
            (QLabel("Seed 1"),       self.bdsp_seed64_inputs[1], 2),
            (QLabel("初始帧"),       self.initial_advances,      3),
            (QLabel("最大帧数"),     self.max_advances,          4),
            (QLabel("Offset"),       self.offset,                5),
        ]:
            label_text.setFixedWidth(LABEL_W)
            grid.addWidget(label_text, row, 0)
            grid.addWidget(widget, row, 1)

        return group

    def _build_static_group(self) -> QGroupBox:
        group = QGroupBox("设置")
        group.setMinimumWidth(260)
        grid = QGridLayout(group)
        grid.setContentsMargins(12, 10, 12, 10)
        grid.setVerticalSpacing(6)
        grid.setHorizontalSpacing(8)

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

        LABEL_W = 64
        rows = (
            ("分类",     self.category_combo),
            ("宝可梦",   self.encounter_combo),
            ("等级",     self.level_display),
            ("特性",     self.template_ability_display),
            ("异色",     self.template_shiny_display),
            ("IV Count", self.iv_count_display),
        )
        for row, (label_text, widget) in enumerate(rows):
            lbl = QLabel(label_text)
            lbl.setFixedWidth(LABEL_W)
            widget.setFixedHeight(30)
            grid.addWidget(lbl, row, 0)
            grid.addWidget(widget, row, 1)

        return group


    def _build_profile_group(self) -> QGroupBox:
        group = QGroupBox("存档信息")
        group.setObjectName("ProfileGroup")
        group.setMinimumHeight(96)
        group.setMaximumHeight(106)

        outer = QHBoxLayout(group)
        outer.setContentsMargins(12, 8, 12, 12)
        outer.setSpacing(10)
        outer.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        # 名称 + 管理
        outer.addWidget(QLabel("名称"))
        self.profile_name = QLineEdit("-")
        self.profile_name.setPlaceholderText("存档名称")
        self.profile_name.setFixedHeight(30)
        self.profile_name.setFixedWidth(140)
        outer.addWidget(self.profile_name)
        self.profile_manager_button = QPushButton("管理")
        self.profile_manager_button.setFixedHeight(30)
        self.profile_manager_button.clicked.connect(self.open_profile_manager)
        outer.addWidget(self.profile_manager_button)

        outer.addSpacing(14)

        # TID / SID / TSV
        self.tid = QLineEdit("12345")
        self.sid = QLineEdit("54321")
        self.tsv = QLineEdit("58376")
        self.tsv.setReadOnly(True)
        self.tid.editingFinished.connect(self._update_tsv)
        self.sid.editingFinished.connect(self._update_tsv)
        for w in (self.tid, self.sid, self.tsv):
            w.setFixedHeight(30)
            w.setFixedWidth(88)
        outer.addWidget(QLabel("TID"))
        outer.addWidget(self.tid)
        outer.addWidget(QLabel("SID"))
        outer.addWidget(self.sid)
        outer.addWidget(QLabel("TSV"))
        outer.addWidget(self.tsv)

        outer.addSpacing(14)

        # 游戏 + checkbox 行
        outer.addWidget(QLabel("游戏"))
        self.profile_game_value = QLabel(self._game_label(self._profile_version))
        outer.addWidget(self.profile_game_value)

        self.national_dex = QCheckBox("全国图鉴")
        self.shiny_charm = QCheckBox("闪耀护符")
        self.oval_charm = QCheckBox("圆形护符")
        outer.addWidget(self.national_dex)
        outer.addWidget(self.shiny_charm)
        outer.addWidget(self.oval_charm)

        outer.addStretch()
        return group

    def _build_filter_group(self) -> QGroupBox:
        group = QGroupBox("筛选项")
        outer = QHBoxLayout(group)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(18)

        # ===== 左: 能力值范围 =====
        left_col = QVBoxLayout()
        left_col.setSpacing(6)

        iv_grid = QGridLayout()
        iv_grid.setVerticalSpacing(5)
        iv_grid.setHorizontalSpacing(6)
        self.iv_min: list[QLineEdit] = []
        self.iv_max: list[QLineEdit] = []
        iv_labels = ("HP", "攻击", "防御", "特攻", "特防", "速度")
        for i, text in enumerate(iv_labels):
            lbl = QLabel(text)
            lbl.setFixedWidth(38)
            min_spin = self._spin(0, 31, 0)
            min_spin.setFixedWidth(54)
            min_spin.setFixedHeight(30)
            max_spin = self._spin(0, 31, 31)
            max_spin.setFixedWidth(54)
            max_spin.setFixedHeight(30)
            self.iv_min.append(min_spin)
            self.iv_max.append(max_spin)
            iv_grid.addWidget(lbl,       i, 0)
            iv_grid.addWidget(min_spin,  i, 1)
            iv_grid.addWidget(max_spin,  i, 2)

        left_col.addLayout(iv_grid)
        left_col.addSpacing(10)
        self.show_stats_check = QCheckBox("显示能力值")
        self.show_stats_check.stateChanged.connect(lambda _state: self._refresh_result_columns())
        left_col.addWidget(self.show_stats_check)
        left_col.addStretch()
        outer.addLayout(left_col)

        # ===== 右: 其他筛选 =====
        right_col = QVBoxLayout()
        right_col.setSpacing(5)

        right_form = QGridLayout()
        right_form.setVerticalSpacing(5)
        right_form.setHorizontalSpacing(8)

        LABEL_W = 60
        COMBO_W = 190
        SPIN_W = 68

        self.ability_filter = QComboBox()
        for text, value in [("任意",255),("0",0),("1",1),("隐藏",2)]:
            self.ability_filter.addItem(text, value)
        self.ability_filter.setFixedHeight(32)
        self.ability_filter.setFixedWidth(COMBO_W)

        self.gender_filter = QComboBox()
        for text, value in [("任意",255),("雄性",0),("雌性",1),("无性别",2)]:
            self.gender_filter.addItem(text, value)
        self.gender_filter.setFixedHeight(32)
        self.gender_filter.setFixedWidth(COMBO_W)

        self.nature_combo = QComboBox()
        self.nature_combo.addItem("任意", -1)
        for index, nature in enumerate(NATURES_ZH):
            self.nature_combo.addItem(nature, index)
        self.nature_combo.setFixedHeight(32)
        self.nature_combo.setFixedWidth(COMBO_W)

        self.shiny_filter = QComboBox()
        for text, value in [("任意","any"),("异色","shiny"),("Star","star"),("Square","square"),("非异色","none")]:
            self.shiny_filter.addItem(text, value)
        self.shiny_filter.setFixedHeight(32)
        self.shiny_filter.setFixedWidth(COMBO_W)

        self.height_min = self._spin(0, 255, 0)
        self.height_min.setFixedWidth(SPIN_W); self.height_min.setFixedHeight(32)
        self.height_max = self._spin(0, 255, 255)
        self.height_max.setFixedWidth(SPIN_W); self.height_max.setFixedHeight(32)
        self.weight_min = self._spin(0, 255, 0)
        self.weight_min.setFixedWidth(SPIN_W); self.weight_min.setFixedHeight(32)
        self.weight_max = self._spin(0, 255, 255)
        self.weight_max.setFixedWidth(SPIN_W); self.weight_max.setFixedHeight(32)

        # 一行一个字段
        for row, (label_text, widget) in enumerate([
            ("特性",   self.ability_filter),
            ("性别",   self.gender_filter),
            ("性格",   self.nature_combo),
            ("异色",   self.shiny_filter),
        ]):
            lbl = QLabel(label_text)
            lbl.setFixedWidth(LABEL_W)
            right_form.addWidget(lbl, row, 0)
            right_form.addWidget(widget, row, 1)

        # Height 行
        right_form.addWidget(QLabel("Height"), 4, 0)
        ht_row = QHBoxLayout()
        ht_row.setSpacing(8)
        ht_row.addWidget(self.height_min)
        ht_row.addWidget(self.height_max)
        ht_row.addStretch()
        right_form.addLayout(ht_row, 4, 1)

        # Weight 行
        right_form.addWidget(QLabel("Weight"), 5, 0)
        wt_row = QHBoxLayout()
        wt_row.setSpacing(8)
        wt_row.addWidget(self.weight_min)
        wt_row.addWidget(self.weight_max)
        wt_row.addStretch()
        right_form.addLayout(wt_row, 5, 1)

        right_col.addLayout(right_form)

        # 个体值计算器按钮
        self.iv_calculator_button = QPushButton("个体值计算器")
        self.iv_calculator_button.setFixedHeight(32)
        self.iv_calculator_button.setFixedWidth(190)
        self.iv_calculator_button.clicked.connect(self.open_iv_calculator)
        right_col.addWidget(self.iv_calculator_button)
        right_col.addStretch()
        outer.addLayout(right_col, 1)

        # skip_filter 被业务代码引用，保留为隐藏
        self.skip_filter = QCheckBox()
        self.skip_filter.setVisible(False)

        # 保留旧隐藏控件
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

    def _build_video_source_dialog(self) -> QDialog:
        dialog = QDialog(self)
        dialog.setObjectName("VideoSourceDialog")
        dialog.setWindowTitle("视频源设置")
        dialog.setModal(False)
        dialog.setMinimumWidth(520)
        dialog.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(14)
        form = QGridLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(12)

        self.capture_device_label = QLabel("采集设备")
        self.capture_device_combo = _MenuPopupComboBox()
        self.capture_device_combo.setObjectName("CaptureDeviceCombo")
        saved_device_index = self._profile_settings_int(
            self._profile_settings.value("video_source/device_index", 0),
            0,
        )
        initial_device_indices = list(range(10))
        if saved_device_index not in initial_device_indices:
            initial_device_indices.append(saved_device_index)
        for index in initial_device_indices:
            self.capture_device_combo.addItem(str(index), index)
        self.capture_device_combo.setCurrentIndex(
            max(0, self.capture_device_combo.findData(saved_device_index))
        )
        self.capture_device_combo.setMinimumWidth(240)
        self.capture_device_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.capture_device_refresh_button = QToolButton()
        self.capture_device_refresh_button.setObjectName("CaptureDeviceRefreshButton")
        self.capture_device_refresh_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload)
        )
        self.capture_device_refresh_button.setToolTip("刷新采集设备")
        self.capture_device_refresh_button.setFixedSize(34, 34)
        self.capture_device_refresh_button.clicked.connect(self.refresh_capture_devices)

        self.capture_api_label = QLabel("采集方式")
        self.capture_api_combo = _MenuPopupComboBox()
        self.capture_api_combo.setObjectName("CaptureApiCombo")
        self.capture_api_combo.addItem("Media Foundation（推荐）", CAPTURE_API_MSMF)
        self.capture_api_combo.addItem("DirectShow（兼容）", CAPTURE_API_DIRECTSHOW)
        self.capture_api_combo.addItem("自动选择", 0)
        self.capture_api_combo.setMinimumWidth(240)
        saved_api = self._restore_capture_api_setting()
        api_index = self.capture_api_combo.findData(saved_api)
        self.capture_api_combo.setCurrentIndex(max(0, api_index))
        self.capture_api_combo.currentIndexChanged.connect(
            lambda _index: self.refresh_capture_devices()
        )

        self.video_source_button = QPushButton("连接视频源")
        self.video_source_button.setObjectName("PrimaryButton")
        self.video_source_button.setMinimumWidth(116)
        self.video_source_button.clicked.connect(self.toggle_video_source)
        close_button = QPushButton("关闭")
        close_button.clicked.connect(dialog.hide)
        self.video_source_status_dot = QFrame()
        self.video_source_status_dot.setObjectName("VideoSourceStatusDot")
        self.video_source_status_dot.setProperty("state", "disconnected")
        self.video_source_status_dot.setFixedSize(10, 10)
        self.video_source_status = QLabel("未连接")
        self.video_source_status.setObjectName("VideoSourceStatus")
        self.video_source_status.setMinimumWidth(112)

        form.addWidget(self.capture_device_label, 0, 0)
        form.addWidget(self.capture_device_combo, 0, 1)
        form.addWidget(self.capture_device_refresh_button, 0, 2)
        form.addWidget(self.capture_api_label, 1, 0)
        form.addWidget(self.capture_api_combo, 1, 1, 1, 2)
        layout.addLayout(form)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(8)
        footer.addWidget(
            self.video_source_status_dot,
            0,
            Qt.AlignmentFlag.AlignVCenter,
        )
        footer.addWidget(self.video_source_status)
        footer.addStretch(1)
        footer.addWidget(close_button)
        footer.addWidget(self.video_source_button)
        layout.addLayout(footer)
        QTimer.singleShot(0, self.refresh_capture_devices)
        return dialog

    def _build_preview_panel(self) -> QWidget:
        panel = QWidget()
        panel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 0, 0, 0)
        layout.setSpacing(10)
        self.preview_group = QGroupBox()
        self.preview_group.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        preview_layout = QVBoxLayout(self.preview_group)

        preview_controls = QHBoxLayout()
        preview_controls.setContentsMargins(0, 0, 0, 0)
        self.main_preview_overlay_check = QCheckBox("显示识别框")
        self.main_preview_overlay_check.setChecked(True)
        self.main_preview_overlay_check.toggled.connect(self._refresh_preview_presentation)
        self.picture_in_picture_button = QPushButton("独立预览")
        self.picture_in_picture_button.clicked.connect(self.show_picture_in_picture)
        preview_controls.addWidget(self.main_preview_overlay_check)
        preview_controls.addWidget(self.picture_in_picture_button)
        preview_controls.addStretch(1)
        preview_layout.addLayout(preview_controls)
        self.preview_label = RoiPreviewLabel()
        self.preview_label.roiSelected.connect(self._handle_preview_selection)
        self.preview_label.setObjectName("Preview")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumSize(480, 260)
        self.preview_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.preview_label.setScaledContents(False)
        preview_layout.addWidget(self.preview_label)
        layout.addWidget(self.preview_group, 1)
        return panel

    def _build_results(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # 工具栏: 40px
        toolbar_widget = QWidget()
        toolbar_widget.setObjectName("ResultsToolbar")
        toolbar_widget.setFixedHeight(38)
        toolbar = QHBoxLayout(toolbar_widget)
        toolbar.setContentsMargins(0, 0, 0, 2)
        toolbar.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self.result_count = QLabel("0 条结果")
        self.result_count.setObjectName("ResultCount")

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
        layout.addWidget(toolbar_widget)

        # 表格
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
            /* ── 全局基础 ── */
            QWidget {
                background: #F7F8FA;
                color: #111827;
                font-family: "Microsoft YaHei UI", "Segoe UI", "PingFang SC", sans-serif;
                font-size: 13px;
            }
            QLabel {
                background: transparent;
                border: none;
                padding: 0;
            }

            /* ── Header ── */
            QFrame#Header {
                background: #FFFFFF;
                border: 1px solid #E5E7EB;
                border-radius: 8px;
            }
            QLabel#WindowTitle {
                font-size: 22px;
                font-weight: 700;
                color: #111827;
            }
            QToolButton#VideoSourceHeaderButton {
                background: #F9FAFB;
                border: 1px solid #D1D5DB;
                border-radius: 7px;
                color: #374151;
                font-weight: 600;
                padding: 0 10px;
            }
            QToolButton#VideoSourceHeaderButton:hover {
                background: #F3F4F6;
                border-color: #9CA3AF;
            }
            QToolButton#VideoSourceHeaderButton:pressed {
                background: #E5E7EB;
            }

            /* ── Badge ── */
            QLabel#Badge {
                background: #EAF8F3;
                color: #0E8F70;
                font-weight: 600;
                font-size: 13px;
                padding: 4px 10px;
                border-radius: 999px;
            }
            QLabel#BadgeDanger {
                background: #FEE2E2;
                color: #DC2626;
                font-weight: 600;
                font-size: 13px;
                padding: 4px 10px;
                border-radius: 999px;
            }


            /* ── Tab ── */
            QTabWidget::pane {
                border: 1px solid #E5E7EB;
                border-radius: 0 8px 8px 8px;
                top: -1px;
                background: #FFFFFF;
            }
            QTabBar::tab {
                background: #F3F4F6;
                border: 1px solid #E5E7EB;
                border-bottom: 2px solid transparent;
                color: #6B7280;
                min-width: 150px;
                padding: 10px 18px;
                font-weight: 600;
                font-size: 14px;
            }
            QTabBar::tab:selected {
                background: #FFFFFF;
                color: #0E8F70;
                border-bottom: 2px solid #10A37F;
            }
            QTabBar::tab:hover:!selected {
                background: #E5E7EB;
                color: #111827;
            }

            /* ── Card (替代 GroupBox) ── */
            QGroupBox {
                background: #FFFFFF;
                border: 1px solid #E5E7EB;
                border-radius: 10px;
                margin-top: 18px;
                padding: 18px 16px 16px 16px;
                font-weight: 700;
                font-size: 14px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 14px;
                top: 2px;
                padding: 0 6px;
                color: #111827;
            }
            QGroupBox#ProfileGroup {
                margin-top: 12px;
                padding: 12px 16px 12px 16px;
            }
            QGroupBox#ProfileGroup::title {
                top: 0;
            }
            QGroupBox#ProfileGroup QLineEdit,
            QGroupBox#ProfileGroup QPushButton {
                min-height: 34px;
                max-height: 34px;
                padding: 0 12px;
            }
            QGroupBox#ProfileGroup QCheckBox {
                min-height: 34px;
                max-height: 34px;
                padding: 0;
            }

            /* ── 输入框 / 下拉框 / 列表 ── */
            QLineEdit, QDoubleSpinBox, QComboBox, QListWidget {
                background: #FFFFFF;
                border: 1px solid #D1D5DB;
                border-radius: 8px;
                min-height: 36px;
                padding: 4px 12px;
                color: #111827;
                font-size: 13px;
                selection-background-color: #D1F1E7;
            }
            QLineEdit:focus, QDoubleSpinBox:focus, QComboBox:focus {
                border-color: #10A37F;
            }
            QLineEdit[readOnly="true"] {
                color: #0E8F70;
                background: #F9FAFB;
            }
            QComboBox::drop-down {
                border: none;
            }
            QDialog#VideoSourceDialog QComboBox,
            QDialog#VideoSourceDialog QPushButton {
                min-height: 34px;
                max-height: 34px;
                padding-top: 0;
                padding-bottom: 0;
            }
            QComboBox#CaptureDeviceCombo::drop-down,
            QComboBox#CaptureApiCombo::drop-down {
                width: 26px;
                border-left: 1px solid #E5E7EB;
            }
            QMenu#VideoSourceComboMenu,
            QMenu#LeadComboMenu {
                background: #FFFFFF;
                color: #111827;
                border: 1px solid #D1D5DB;
                padding: 4px;
            }
            QMenu#VideoSourceComboMenu::item,
            QMenu#LeadComboMenu::item {
                min-height: 28px;
                padding: 4px 28px 4px 10px;
                border-radius: 4px;
            }
            QMenu#VideoSourceComboMenu::item:selected,
            QMenu#LeadComboMenu::item:selected {
                background: #F3F4F6;
                color: #111827;
            }
            QMenu#VideoSourceComboMenu::item:checked,
            QMenu#LeadComboMenu::item:checked {
                background: #E6F6F0;
                color: #0E8F70;
                font-weight: 600;
            }
            QToolButton#CaptureDeviceRefreshButton {
                background: #FFFFFF;
                border: 1px solid #D1D5DB;
                border-radius: 8px;
            }
            QToolButton#CaptureDeviceRefreshButton:hover {
                background: #F3F4F6;
                border-color: #9CA3AF;
            }
            QToolButton#CaptureDeviceRefreshButton:disabled {
                background: #F3F4F6;
                border-color: #E5E7EB;
            }
            QLabel#VideoSourceStatus {
                color: #374151;
                font-weight: 600;
            }
            QFrame#VideoSourceStatusDot {
                min-width: 10px;
                max-width: 10px;
                min-height: 10px;
                max-height: 10px;
                border: none;
                border-radius: 5px;
                background: #9CA3AF;
            }
            QFrame#VideoSourceStatusDot[state="connecting"] {
                background: #D97706;
            }
            QFrame#VideoSourceStatusDot[state="connected"] {
                background: #0E8F70;
            }
            QFrame#VideoSourceStatusDot[state="failed"] {
                background: #DC2626;
            }

            /* ── SpinBox 微调按钮 ── */
            QSpinBox {
                background: #FFFFFF;
                border: 1px solid #D1D5DB;
                border-radius: 8px;
                min-height: 36px;
                padding: 4px 12px;
                color: #111827;
                font-size: 13px;
            }

            /* ── 多行文本框 ── */
            QPlainTextEdit {
                background: #FFFFFF;
                border: 1px solid #D1D5DB;
                border-radius: 8px;
                color: #111827;
                font-family: "Cascadia Mono", "Consolas", "JetBrains Mono", monospace;
                font-size: 13px;
                padding: 10px;
                selection-background-color: #D1F1E7;
            }
            QPlainTextEdit#LogView {
                background: #FBFBFC;
                border: 1px solid #E5E7EB;
                border-radius: 8px;
                font-family: "Cascadia Mono", "Consolas", "JetBrains Mono", monospace;
                font-size: 13px;
                padding: 10px;
                color: #374151;
            }

            /* ── 伊机控暗色日志 ── */
            QTextEdit#EasyConLog {
                background: #282826;
                border: 1px solid #E5E7EB;
                border-radius: 0;
                color: #e7ece9;
                font-family: "Cascadia Mono", "Consolas", "JetBrains Mono", monospace;
                font-size: 12px;
                padding: 10px;
            }

            /* ── 工具栏 ── */
            QFrame#EasyConToolbar {
                background: #FFFFFF;
                border: 1px solid #E5E7EB;
                border-radius: 8px;
            }
            QFrame#AutoRngToolbar {
                background: #FFFFFF;
                border: 1px solid #E5E7EB;
                border-radius: 8px;
            }
            QFrame#AutoRngToolbar QComboBox,
            QFrame#AutoRngToolbar QSpinBox {
                min-height: 34px;
                max-height: 34px;
                padding: 0 12px;
            }
            QFrame#AutoRngToolbar QCheckBox {
                min-height: 34px;
                max-height: 34px;
                padding: 0;
            }
            QFrame#AutoRngToolbar QPushButton,
            QFrame#AutoRngToolbar QToolButton {
                min-height: 34px;
                max-height: 34px;
                padding: 0 12px;
            }
            QGroupBox#TargetSummaryGroup {
                margin-top: 0;
                padding: 8px 16px 8px 16px;
            }
            QGroupBox#TargetSummaryGroup QPushButton {
                min-height: 34px;
                max-height: 34px;
                padding: 0 14px;
            }
            QWidget#InlinePanel {
                background: #F9FAFB;
                border: 1px solid #E5E7EB;
                border-radius: 8px;
            }
            QListWidget#TargetPool {
                background: #FFFFFF;
                border: 1px solid #D1D5DB;
                border-radius: 8px;
                min-height: 116px;
                max-height: 140px;
                padding: 8px;
            }
            QListWidget#TargetPool::item {
                background: #FFFFFF;
                border: 1px solid #D1D5DB;
                border-radius: 12px;
                padding: 4px 10px;
                margin: 2px;
            }
            QListWidget#TargetPool::item:selected {
                background: #E6F6F0;
                border-color: #10A37F;
                color: #0E8F70;
            }
            QListWidget#TargetPool::item:hover:!selected {
                background: #F3F4F6;
                border-color: #9CA3AF;
            }
            QLabel#SectionTitle {
                color: #111827;
                font-weight: 700;
                font-size: 13px;
            }

            /* ── 预览 ── */
            QLabel#Preview {
                background: #F3F4F6;
                border: 1px solid #E5E7EB;
                border-radius: 8px;
                color: #6B7280;
            }

            /* ── 状态栏 ── */
            QStatusBar {
                background: #F3F4F6;
                border-top: 1px solid #E5E7EB;
                color: #6B7280;
                font-size: 12px;
            }

            /* ── 只读 Seed 显示 ── */
            QLineEdit#Readonly {
                color: #0E8F70;
                background: #F9FAFB;
            }

            /* ── 通用按钮 ── */
            QPushButton {
                background: #FFFFFF;
                border: 1px solid #D1D5DB;
                border-radius: 8px;
                min-height: 36px;
                padding: 4px 12px;
                font-weight: 600;
                font-size: 13px;
                color: #111827;
            }
            QPushButton:hover {
                background: #F3F4F6;
                border-color: #9CA3AF;
            }
            QPushButton:pressed {
                background: #E5E7EB;
            }
            QPushButton:disabled {
                background: #F3F4F6;
                color: #9CA3AF;
                border-color: #E5E7EB;
            }

            /* ── 主按钮（绿色） ── */
            QPushButton#PrimaryButton {
                background: #10A37F;
                color: #FFFFFF;
                border-color: #10A37F;
                font-weight: 700;
            }
            QPushButton#PrimaryButton:hover {
                background: #0E8F70;
                border-color: #0E8F70;
            }
            QPushButton#PrimaryButton:pressed {
                background: #0B7A5E;
                border-color: #0B7A5E;
            }
            QPushButton#PrimaryButton:disabled {
                background: #9CA3AF;
                color: #F3F4F6;
                border-color: #9CA3AF;
            }

            /* ── Danger 按钮 ── */
            QWidget#ResultsToolbar QPushButton {
                min-height: 32px;
                max-height: 32px;
                padding: 0 14px;
            }
            QPushButton#DangerButton {
                background: #FFFFFF;
                color: #DC2626;
                border-color: #DC2626;
                font-weight: 700;
            }
            QPushButton#DangerButton:hover {
                background: #FEF2F2;
            }

            /* ── ToolButton 主按钮 ── */
            QToolButton#PrimaryButton {
                background: #10A37F;
                color: #FFFFFF;
                border: 1px solid #10A37F;
                border-radius: 8px;
                padding: 4px 18px 4px 12px;
                font-weight: 700;
                font-size: 13px;
            }
            QToolButton#PrimaryButton:hover {
                background: #0E8F70;
                border-color: #0E8F70;
            }
            QToolButton#PrimaryButton:pressed {
                background: #0B7A5E;
                border-color: #0B7A5E;
            }
            QToolButton#PrimaryButton:disabled {
                background: #9CA3AF;
                color: #F3F4F6;
                border-color: #9CA3AF;
            }
            QToolButton#PrimaryButton::menu-button {
                border-left: 1px solid rgba(255,255,255,90);
                width: 18px;
            }

            /* ── 帮助按钮 ── */
            QToolButton#HelpMenuButton {
                background: transparent;
                border: 1px solid transparent;
                border-radius: 6px;
                color: #6B7280;
                min-height: 28px;
                padding: 4px 10px;
                font-weight: 700;
                font-size: 13px;
            }
            QToolButton#HelpMenuButton:hover {
                background: #F3F4F6;
                border-color: #E5E7EB;
                color: #111827;
            }
            QToolButton#HelpMenuButton::menu-indicator {
                image: none;
                width: 0;
            }

            /* ── 表格 ── */
            QTableWidget {
                background: #FFFFFF;
                alternate-background-color: #F9FAFB;
                border: 1px solid #E5E7EB;
                gridline-color: #F3F4F6;
                color: #111827;
                font-size: 13px;
            }
            QTableWidget::item:selected {
                background: #10A37F;
                color: #FFFFFF;
            }
            QHeaderView::section {
                background: #F9FAFB;
                color: #6B7280;
                border: 0;
                border-right: 1px solid #E5E7EB;
                border-bottom: 1px solid #E5E7EB;
                padding: 8px 6px;
                font-weight: 700;
                font-size: 12px;
            }

            /* ── 滚动条 ── */
            QScrollBar:vertical {
                background: transparent;
                width: 8px;
                margin: 0;
            }
            QScrollBar::handle:vertical {
                background: #D1D5DB;
                border-radius: 4px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover {
                background: #9CA3AF;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0;
            }
            QScrollBar:horizontal {
                background: transparent;
                height: 8px;
            }
            QScrollBar::handle:horizontal {
                background: #D1D5DB;
                border-radius: 4px;
                min-width: 30px;
            }
            QScrollBar::handle:horizontal:hover {
                background: #9CA3AF;
            }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
                width: 0;
            }
            """
        )

    def _spin(self, minimum: int, maximum: int, value: int) -> QLineEdit:
        w = QLineEdit(str(value))
        w.setValidator(QIntValidator(minimum, maximum))
        set_c_locale(w)
        w.setAlignment(Qt.AlignmentFlag.AlignRight)
        w.setFixedHeight(36)
        return w

    def _double_spin(self, minimum: float, maximum: float, value: float, decimals: int) -> QDoubleSpinBox:
        spin = NoWheelDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(decimals)
        spin.setSingleStep(0.1)
        set_c_locale(spin)
        spin.setValue(value)
        spin.setFixedHeight(36)
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

    @staticmethod
    def _profile_settings_bool(value: object, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
        return default

    @staticmethod
    def _profile_settings_int(value: object, default: int) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return default
        return max(0, min(65535, number))

    def _restore_capture_api_setting(self) -> int:
        settings = self._profile_settings
        saved_api = self._profile_settings_int(
            settings.value("video_source/capture_api", CAPTURE_API_MSMF),
            CAPTURE_API_MSMF,
        )
        settings_version = self._profile_settings_int(
            settings.value(CAPTURE_API_SETTINGS_VERSION_KEY, 0),
            0,
        )
        if settings_version < CAPTURE_API_SETTINGS_VERSION:
            if saved_api == CAPTURE_API_DIRECTSHOW:
                saved_api = CAPTURE_API_MSMF
            settings.setValue("video_source/capture_api", saved_api)
            settings.setValue(CAPTURE_API_SETTINGS_VERSION_KEY, CAPTURE_API_SETTINGS_VERSION)
        return saved_api

    def _restore_profile_settings(self) -> None:
        settings = self._profile_settings
        self.profile_name.setText(str(settings.value("name", self.profile_name.text()) or "-"))
        self.tid.setText(str(self._profile_settings_int(settings.value("tid", self.tid.text()), 12345)))
        self.sid.setText(str(self._profile_settings_int(settings.value("sid", self.sid.text()), 54321)))
        self.national_dex.setChecked(
            self._profile_settings_bool(settings.value("national_dex"), self.national_dex.isChecked())
        )
        self.shiny_charm.setChecked(
            self._profile_settings_bool(settings.value("shiny_charm"), self.shiny_charm.isChecked())
        )
        self.oval_charm.setChecked(
            self._profile_settings_bool(settings.value("oval_charm"), self.oval_charm.isChecked())
        )
        version_value = str(settings.value("version", self._profile_version.value) or self._profile_version.value)
        try:
            version = GameVersion(version_value)
        except ValueError:
            version = self._profile_version
        self._set_profile_version(version)
        self._update_tsv()

    def _save_profile_settings(self) -> None:
        settings = self._profile_settings
        settings.setValue("name", self.profile_name.text() or "-")
        settings.setValue("tid", self._profile_settings_int(self.tid.text(), 0))
        settings.setValue("sid", self._profile_settings_int(self.sid.text(), 0))
        settings.setValue("version", self._profile_version.value)
        settings.setValue("national_dex", self.national_dex.isChecked())
        settings.setValue("shiny_charm", self.shiny_charm.isChecked())
        settings.setValue("oval_charm", self.oval_charm.isChecked())
        settings.sync()

    @staticmethod
    def _window_setting_int(value: object, default: int = 0) -> int:
        """Parse a persisted geometry value without applying profile limits."""

        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _screen_available_geometry(self) -> QRect:
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return QRect(0, 0, MAIN_WINDOW_DEFAULT_SIZE.width(), MAIN_WINDOW_DEFAULT_SIZE.height())
        geometry = QRect(screen.availableGeometry())
        if geometry.width() <= 0 or geometry.height() <= 0:
            return QRect(0, 0, MAIN_WINDOW_DEFAULT_SIZE.width(), MAIN_WINDOW_DEFAULT_SIZE.height())
        return geometry

    def _screen_available_geometry_for_rect(self, rect: QRect) -> QRect:
        """Choose the work area containing a persisted window position."""

        fallback = self._screen_available_geometry()
        if fallback.contains(rect.center()):
            return fallback

        # Before the window is shown ``self.screen()`` normally points at the
        # primary monitor.  Looking up the saved position first preserves a
        # normal window that was last used on a secondary monitor.
        for point in (rect.center(), rect.topLeft()):
            try:
                screen = QGuiApplication.screenAt(point)
            except (AttributeError, RuntimeError):
                screen = None
            if screen is None:
                continue
            geometry = QRect(screen.availableGeometry())
            if geometry.width() > 0 and geometry.height() > 0:
                return geometry
        return fallback

    def _restore_window_geometry(self) -> None:
        """Restore a saved rect while keeping it inside the current work area."""

        settings = self._profile_settings
        values = [self._window_setting_int(settings.value(key), 0) for key in MAIN_WINDOW_GEOMETRY_KEYS]
        x, y, width, height = values
        saved = QRect(x, y, width, height) if width > 0 and height > 0 else None
        available = (
            self._screen_available_geometry_for_rect(saved)
            if saved is not None
            else self._screen_available_geometry()
        )
        minimum = QSize(
            min(
                MAIN_WINDOW_COMPACT_MIN_SIZE.width(),
                max(1, available.width() - 2 * MAIN_WINDOW_SCREEN_MARGIN),
            ),
            min(
                MAIN_WINDOW_COMPACT_MIN_SIZE.height(),
                max(1, available.height() - 2 * MAIN_WINDOW_SCREEN_MARGIN),
            ),
        )
        self.setMinimumSize(minimum)

        if saved is not None:
            rect = _clamp_window_rect(saved, available, minimum=minimum)
        else:
            rect = _fit_window_rect(available, minimum=minimum)
        self.setGeometry(rect)
        self._window_restore_maximized = self._profile_settings_bool(
            settings.value("window/maximized"),
            False,
        )
        self._window_geometry_restored = True
        self._update_responsive_layout()
        if self._window_restore_maximized:
            QTimer.singleShot(0, self._restore_maximized_window)

    def _restore_maximized_window(self) -> None:
        if self._window_restore_maximized and not self._is_closing:
            self.showMaximized()

    def _save_window_geometry(self) -> None:
        """Persist normal geometry and maximized state for the next monitor."""

        if not self._window_geometry_restored:
            return
        settings = self._profile_settings
        normal = self.normalGeometry() if self.isMaximized() else self.geometry()
        if normal.width() > 0 and normal.height() > 0:
            settings.setValue("window/x", normal.x())
            settings.setValue("window/y", normal.y())
            settings.setValue("window/width", normal.width())
            settings.setValue("window/height", normal.height())
        settings.setValue("window/maximized", self.isMaximized())
        settings.sync()

    def _keep_window_on_screen(self) -> None:
        if not self._window_geometry_restored or self.isFullScreen() or self.isMaximized():
            return
        available = self._screen_available_geometry()
        minimum = QSize(
            min(
                MAIN_WINDOW_COMPACT_MIN_SIZE.width(),
                max(1, available.width() - 2 * MAIN_WINDOW_SCREEN_MARGIN),
            ),
            min(
                MAIN_WINDOW_COMPACT_MIN_SIZE.height(),
                max(1, available.height() - 2 * MAIN_WINDOW_SCREEN_MARGIN),
            ),
        )
        self.setMinimumSize(minimum)
        rect = _clamp_window_rect(self.geometry(), available, minimum=minimum)
        if rect != self.geometry():
            self.setGeometry(rect)

    def _handle_screen_geometry_change(self) -> None:
        self._keep_window_on_screen()
        self._update_responsive_layout()

    def _update_responsive_layout(self) -> None:
        """Switch the Project_Xs split view when the available width changes.

        The method is intentionally idempotent: a normal resize should leave a
        user's splitter adjustment untouched.  Sizes are only initialized when
        the orientation changes (or on the first usable layout pass).
        """

        splitter = getattr(self, "project_xs_tab", None)
        if not isinstance(splitter, _ResponsiveProjectSplitter):
            return
        # A hidden tab keeps its last page geometry (often the Qt default
        # 640x480).  Use the tab widget's current content size in that case so
        # changing the window while another page is selected still prepares
        # Project_Xs for the correct orientation before it is shown.
        tabs = getattr(self, "tabs", None)
        if tabs is not None and tabs.width() > 0 and tabs.height() > 0:
            # During a top-level resize the tab layout can lag one event
            # behind.  The client area is a conservative fallback for the
            # breakpoint, while the tab's own width remains preferred once
            # it has been laid out.
            width = max(tabs.width(), self.width() - 40)
            height = max(1, tabs.height() - tabs.tabBar().height())
        else:
            width = splitter.width()
            height = splitter.height()
        if width <= 0 or height <= 0:
            return

        vertical = width < PROJECT_XS_VERTICAL_BREAKPOINT
        orientation = Qt.Orientation.Vertical if vertical else Qt.Orientation.Horizontal
        previous = getattr(self, "_project_xs_vertical", None)
        orientation_changed = splitter.orientation() != orientation
        if previous is not None and previous == vertical and not orientation_changed:
            return

        preview_label = getattr(self, "preview_label", None)
        if preview_label is not None:
            # The desktop preview is intentionally large, but its minimum
            # height should not consume the whole vertical splitter on a
            # short display.  The image itself continues to use KeepAspectRatio
            # and all source-space coordinates remain unchanged.
            if vertical:
                preview_label.setMinimumHeight(PROJECT_XS_COMPACT_PREVIEW_MIN_HEIGHT)
            else:
                preview_label.setMinimumHeight(PROJECT_XS_PREVIEW_MIN_HEIGHT)

        splitter.setOrientation(orientation)
        self._project_xs_vertical = vertical

        left_layout = getattr(self, "_project_xs_left_layout", None)
        right_layout = getattr(self, "_project_xs_right_layout", None)
        status_group = getattr(self, "status_group", None)
        if left_layout is not None and right_layout is not None and status_group is not None:
            if vertical:
                right_layout.removeWidget(status_group)
                # Keep the capture controls at the top of the scroll view;
                # status/config selectors follow them on a narrow screen.
                left_layout.insertWidget(2, status_group)
            else:
                left_layout.removeWidget(status_group)
                right_layout.insertWidget(0, status_group)

        if vertical:
            # Leave enough room for the preview while exposing the complete
            # control column through its own vertical scroll bar.
            usable = max(1, height - splitter.handleWidth())
            desired_first = min(
                PROJECT_XS_VERTICAL_LEFT_MAX_HEIGHT,
                max(PROJECT_XS_VERTICAL_LEFT_MIN_HEIGHT, int(usable * 0.46)),
            )
            right = splitter.widget(1)
            right_minimum = right.minimumSizeHint().height() if right is not None else 0
            first = min(desired_first, max(1, usable - max(1, right_minimum)))
            second = max(1, usable - first)
            splitter.setSizes([first, second])
        else:
            usable = max(1, width - splitter.handleWidth())
            first = min(PROJECT_XS_HORIZONTAL_LEFT_WIDTH, max(280, usable // 2))
            second = max(1, usable - first)
            splitter.setSizes([first, second])

    def _set_profile_version(self, version: GameVersion) -> None:
        self._profile_version = version
        self.profile_game_value.setText(self._game_label(version))
        self.auto_rng_tab.set_target_version(version)
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
        self._save_profile_settings()
        self.statusBar().showMessage("存档信息已应用" if self.lang == "zh" else "Profile applied")

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if not self._confirm_unsaved_easycon_script():
            event.ignore()
            return
        self._is_closing = True
        if not self.update_controller.shutdown():
            self._is_closing = False
            QMessageBox.warning(
                self,
                "正在停止更新任务",
                "更新网络任务尚未退出，请稍后再关闭程序。",
            )
            event.ignore()
            return
        self._capture_timer.stop()
        self._capture_cancel.set()
        self._request_automation_runner_stop_with_reason(
            self.auto_rng_tab,
            "自动定点",
            "主窗口关闭",
        )
        self._request_automation_runner_stop_with_reason(
            self.auto_tid_rng_tab,
            "自动 TID",
            "主窗口关闭",
        )
        self._ocr_shutdown_requested = True
        self._request_ocr_background_stop()
        broker_start_stopped = self._shutdown_capture_broker_start_thread()
        easycon_stopped = self.easycon_tab.shutdown() if broker_start_stopped else True

        pending_tasks: list[str] = []
        if not self._shutdown_automation_runner(
            self.auto_rng_tab,
            "自动定点",
            request_stop=False,
        ):
            pending_tasks.append("自动定点")
        if not self._shutdown_automation_runner(
            self.auto_tid_rng_tab,
            "自动 TID",
            request_stop=False,
        ):
            pending_tasks.append("自动 TID")
        if not easycon_stopped:
            pending_tasks.append("伊机控或 CLI")
        if not broker_start_stopped:
            pending_tasks.append("视频源连接")
        if not self._shutdown_worker_thread(
            self._shiny_calibration_worker,
            self._shiny_calibration_thread,
            "闪光判定校准",
        ):
            pending_tasks.append("闪光判定校准")
        if not self._shutdown_capture_thread():
            pending_tasks.append("Seed 捕捉")
        self._release_preview_capture()
        if not self._shutdown_ocr_warmup_thread():
            pending_tasks.append("OCR 预热")
        if not self._shutdown_ocr_task_thread():
            pending_tasks.append("OCR 识别")

        if pending_tasks:
            self._is_closing = False
            self._restore_interrupted_ocr_state_if_idle()
            if self._capture_thread is not None:
                self._capture_timer.start()
            if pending_tasks == ["伊机控或 CLI"]:
                title = "正在停止伊机控任务"
                message = "伊机控或 CLI 任务尚未退出，请稍后再关闭程序。"
            else:
                title = "正在停止后台任务"
                message = f"{'、'.join(pending_tasks)}尚未退出，请稍后再关闭程序。"
            QMessageBox.warning(self, title, message)
            event.ignore()
            return
        if not self.disconnect_video_source(force=True, reason="主窗口关闭"):
            self._is_closing = False
            QMessageBox.warning(
                self,
                "视频源未能停止",
                "共享视频源尚未确认停止，请重试断开后再关闭程序。",
            )
            event.ignore()
            return
        self._save_profile_settings()
        self._save_window_geometry()
        if self._picture_in_picture is not None:
            self._picture_in_picture.hide()
        super().closeEvent(event)

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self._update_responsive_layout()
        self._keep_window_on_screen()

    def event(self, event) -> bool:  # type: ignore[override]
        handled = super().event(event)
        if event.type() == QEvent.Type.ScreenChangeInternal:
            QTimer.singleShot(0, self._handle_screen_geometry_change)
        return handled

    def _confirm_unsaved_easycon_script(self) -> bool:
        if not self.easycon_tab.has_unsaved_script_changes():
            return True
        choice = QMessageBox.question(
            self,
            "未保存的伊机控脚本",
            "伊机控脚本有未保存的修改。关闭程序前是否保存？",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if choice == QMessageBox.StandardButton.Cancel:
            return False
        if choice == QMessageBox.StandardButton.Save:
            return self.easycon_tab.save_script() is not None
        return choice == QMessageBox.StandardButton.Discard

    def _request_automation_runner_stop(self, panel: object, label: str) -> None:
        self._request_worker_stop(
            getattr(panel, "_runner_worker", None),
            label,
            reason=getattr(self, "_automation_stop_reason", None),
        )

    def _request_automation_runner_stop_with_reason(
        self,
        panel: object,
        label: str,
        reason: str,
    ) -> None:
        """Invoke the legacy two-argument helper with temporary context.

        Keeping the actual call at two positional arguments is important for
        existing integrations and test doubles that replace the helper.  The
        production helper reads this short-lived context and forwards it to
        the TID worker.
        """

        previous = getattr(self, "_automation_stop_reason", None)
        self._automation_stop_reason = str(reason)
        try:
            self._request_automation_runner_stop(panel, label)
        finally:
            self._automation_stop_reason = previous

    def _shutdown_capture_broker_start_thread(self, *, wait_ms: int = 3000) -> bool:
        thread = self._capture_broker_start_thread
        if thread is None:
            return True
        self._video_source_cancel_requested = True
        self._video_source_pending_status = "连接已取消"
        if self._video_source_connected or self._video_source_connecting:
            self._invalidate_video_source_consumers()
        else:
            self._clear_video_source_preview()
        thread.requestInterruption()
        process = self._capture_broker_process
        stopped, stop_error = self._stop_capture_broker_process(
            process,
            context="关闭时停止 Broker 失败",
        )
        deadline = time.monotonic() + max(0, wait_ms) / 1000.0
        while thread.isRunning() and time.monotonic() < deadline:
            QApplication.processEvents()
            remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
            thread.wait(min(50, remaining_ms))
        if thread.isRunning():
            if not stopped:
                self._set_video_source_stop_failure(stop_error or "Broker 未确认停止")
            else:
                self._video_source_connecting = True
                self.video_source_button.setEnabled(False)
                self.video_source_button.setText("正在断开...")
                self._set_video_source_status("正在结束视频源连接", "connecting")
            self._write_run_log("视频源", "关闭时视频源连接线程未能及时退出", level="WARNING")
            return False
        stopped, stop_error = self._stop_capture_broker_process(
            process,
            context="关闭时最终确认 Broker 停止失败",
        )
        if self._capture_broker_start_thread is thread:
            self._capture_broker_start_thread = None
        self._video_source_connecting = False
        self._video_source_cancel_requested = False
        if not stopped:
            self._set_video_source_stop_failure(stop_error or "Broker 未确认停止")
            return False
        self._video_source_stop_pending = False
        self._video_source_stop_error = None
        self._set_video_source_disconnected_ui(self._video_source_pending_status)
        return True

    def _shutdown_automation_runner(
        self,
        panel: object,
        label: str,
        *,
        request_stop: bool = True,
        wait_ms: int = 2000,
    ) -> bool:
        return self._shutdown_worker_thread(
            getattr(panel, "_runner_worker", None),
            getattr(panel, "_runner_thread", None),
            label,
            request_stop=request_stop,
            wait_ms=wait_ms,
        )

    def _request_worker_stop(
        self,
        worker: object,
        label: str,
        *,
        reason: str | None = None,
    ) -> None:
        if worker is not None:
            try:
                request_stop = getattr(worker, "request_stop", None)
                if reason and callable(request_stop):
                    try:
                        request_stop(reason)
                    except TypeError:
                        # A legacy worker may expose ``request_stop()``
                        # without the optional reason argument.
                        try:
                            request_stop()
                        except TypeError:
                            worker.stop()  # type: ignore[attr-defined]
                else:
                    worker.stop()  # type: ignore[attr-defined]
            except Exception as exc:
                self._write_run_log(label, f"关闭时停止流程失败: {exc}", level="WARNING")

    def _shutdown_worker_thread(
        self,
        worker: object,
        thread: object,
        label: str,
        *,
        request_stop: bool = True,
        wait_ms: int = 2000,
    ) -> bool:
        if request_stop:
            self._request_worker_stop(worker, label)
        if thread is None:
            return True
        try:
            thread.quit()  # type: ignore[attr-defined]
            deadline = time.monotonic() + max(0, wait_ms) / 1000.0
            while thread.isRunning() and time.monotonic() < deadline:  # type: ignore[attr-defined]
                QApplication.processEvents()
                remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
                thread.wait(min(50, remaining_ms))  # type: ignore[attr-defined]
            if thread.isRunning():  # type: ignore[attr-defined]
                timeout_seconds = max(0, wait_ms) / 1000.0
                self._write_run_log(
                    label,
                    f"关闭时流程未能在 {timeout_seconds:g} 秒内退出",
                    level="WARNING",
                )
                return False
        except RuntimeError:
            return True
        return True

    def _shutdown_capture_thread(self, *, wait_ms: int = 2000) -> bool:
        capture_thread = self._capture_thread
        if capture_thread is None:
            return True
        deadline = time.monotonic() + max(0, wait_ms) / 1000.0
        while capture_thread.is_alive() and time.monotonic() < deadline:
            QApplication.processEvents()
            remaining_seconds = max(0.001, deadline - time.monotonic())
            capture_thread.join(timeout=min(0.05, remaining_seconds))
        if capture_thread.is_alive():
            timeout_seconds = max(0, wait_ms) / 1000.0
            self._write_run_log(
                "Seed 捕捉",
                f"关闭时捕捉线程未能在 {timeout_seconds:g} 秒内退出",
                level="WARNING",
            )
            return False
        return True

    def _shutdown_ocr_warmup_thread(self, *, wait_ms: int = 2000) -> bool:
        warmup_thread = self._ocr_warmup_thread
        if warmup_thread is None:
            self._ocr_warmup_running = False
            return True
        try:
            warmup_thread.requestInterruption()
            deadline = time.monotonic() + max(0, wait_ms) / 1000.0
            while warmup_thread.isRunning() and time.monotonic() < deadline:
                QApplication.processEvents()
                remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
                warmup_thread.wait(min(50, remaining_ms))
            if warmup_thread.isRunning():
                timeout_seconds = max(0, wait_ms) / 1000.0
                self._write_run_log(
                    "OCR",
                    f"关闭时预热线程未能在 {timeout_seconds:g} 秒内退出",
                    level="WARNING",
                )
                return False
            try:
                warmup_thread.completed.disconnect(self._handle_ocr_warmup_completed)
            except (RuntimeError, TypeError):
                pass
        except RuntimeError:
            pass
        self._ocr_warmup_running = False
        return True

    def _request_ocr_background_stop(self) -> None:
        for thread in (self._ocr_warmup_thread, self._ocr_task_thread):
            if thread is None:
                continue
            try:
                thread.requestInterruption()
            except RuntimeError:
                pass

    def _shutdown_ocr_task_thread(self, *, wait_ms: int = 2000) -> bool:
        task_thread = self._ocr_task_thread
        if task_thread is None:
            return True
        try:
            task_thread.requestInterruption()
            deadline = time.monotonic() + max(0, wait_ms) / 1000.0
            while task_thread.isRunning() and time.monotonic() < deadline:
                QApplication.processEvents()
                remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
                task_thread.wait(min(50, remaining_ms))
            if task_thread.isRunning():
                timeout_seconds = max(0, wait_ms) / 1000.0
                self._write_run_log(
                    "OCR",
                    f"关闭时{self._ocr_task_label or '识别任务'}未能在 {timeout_seconds:g} 秒内退出",
                    level="WARNING",
                )
                return False
        except RuntimeError:
            pass
        self._ocr_task_thread = None
        self._ocr_task_label = None
        self._ocr_task_completed = None
        return True

    def _restore_interrupted_ocr_state_if_idle(self) -> None:
        if not self._ocr_shutdown_requested or self._is_closing:
            return
        warmup_thread = self._ocr_warmup_thread
        task_thread = self._ocr_task_thread
        if warmup_thread is not None and warmup_thread.isRunning():
            return
        if task_thread is not None and task_thread.isRunning():
            return
        pending_kind = self._ocr_after_warmup[0] if self._ocr_after_warmup is not None else None
        self._ocr_shutdown_requested = False
        self._ocr_warmup_running = False
        self._ocr_after_warmup = None
        self._ocr_full_test_running = False
        if self._ocr_settings_dialog is not None:
            self._ocr_settings_dialog.cancel_background_activity("OCR 任务因关闭操作取消")
        if pending_kind == "auto_rng":
            message = "OCR 初始化因关闭操作取消，自动流程未启动"
            self.auto_rng_tab.set_phase_text(AutoRngPhase.IDLE.value)
            self.auto_rng_tab.add_log(message, level="WARNING")
            self.statusBar().showMessage(message)
        self._refresh_shiny_calibration_button_state()

    def _change_language(self) -> None:
        self.lang = "zh"
        self._apply_language()

    def _apply_language(self) -> None:
        self.title_label.setText(self._text("title"))
        self.tabs.setTabText(0, self._text("auto_rng"))
        self.tabs.setTabText(1, self._text("auto_tid_rng"))
        self.tabs.setTabText(2, self._text("project_xs"))
        self.tabs.setTabText(3, self._text("bdsp_search"))
        self.tabs.setTabText(4, self._text("easycon"))
        self.tabs.setTabText(5, "历史记录" if self.lang == "zh" else "History")
        self.status_group.setTitle("配置" if self.lang == "zh" else "Config")
        self.video_source_dialog.setWindowTitle(
            "视频源设置" if self.lang == "zh" else "Video Source"
        )
        self.capture_device_label.setText("采集设备" if self.lang == "zh" else "Capture Device")
        self.capture_api_label.setText("采集方式" if self.lang == "zh" else "Capture API")
        self.capture_group.setTitle(self._text("capture"))
        self.seed_group.setTitle(self._text("seed"))
        self.rng_info_group.setTitle("乱数信息" if self.lang == "zh" else "RNG Info")
        self.lead_label.setText("队首" if self.lang == "zh" else "Lead")
        self.lead_combo.set_language(self.lang)
        self.static_group.setTitle("设置" if self.lang == "zh" else "Settings")
        self.profile_group.setTitle("存档信息" if self.lang == "zh" else "Profile")
        self.filter_group.setTitle("筛选项" if self.lang == "zh" else "Filters")
        self.profile_manager_button.setText("管理" if self.lang == "zh" else "Manager")
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
        self.tidsid_button.setText("TID/SID 测种")
        self.preview_button.setText(self._text("stop_preview") if self._preview_timer.isActive() else self._text("preview_button"))
        self.calibrate_shiny_threshold_button.setText("校准闪光判定")
        self.save_config_button.setText(self._text("save_config"))
        self.raw_screenshot_button.setText(self._text("raw_screenshot"))
        self.select_roi_button.setText(self._text("select_roi"))
        self.generate_button.setText(self._text("generate"))
        self.copy_button.setText(self._text("copy"))
        self.export_button.setText(self._text("export"))
        self.show_stats_check.setText("显示能力值" if self.lang == "zh" else "Show Stats")
        self.iv_calculator_button.setText("个体值计算器" if self.lang == "zh" else "IV Calculator")
        self._refresh_result_columns()
        self._update_auto_rng_header(advances=self._tracked_advances)
        if not self._preview_timer.isActive():
            self.preview_label.clear()
            self.preview_label.setText(self._text("no_preview"))
        self.result_count.setText(f"{len(self._states)} {self._text('results')}")
        for label in self.findChildren(QLabel):
            key = label.property("i18n")
            if key:
                label.setText(self._text(str(key)))

    def _update_auto_rng_header(
        self,
        *,
        loop_index: int | None = None,
        phase_text: str | None = None,
        advances: int | None = None,
    ) -> None:
        if loop_index is not None:
            self.auto_loop_badge.setText(f"循环 {loop_index}")
        if phase_text is not None:
            self.auto_phase_badge.setText(f"阶段 {phase_text}")
        if advances is not None:
            self.auto_advance_badge.setText(f"advance {advances}")

    def _apply_auto_rng_header_progress(self, progress: object) -> None:
        phase_text = progress.phase.value if hasattr(progress.phase, "value") else str(progress.phase)
        current_advances = getattr(progress, "current_advances", None)
        if current_advances is not None:
            self._display_tracked_advances(int(current_advances))
        advances = self._tracked_advances
        self._update_auto_rng_header(
            loop_index=getattr(progress, "loop_index", None),
            phase_text=phase_text,
            advances=advances,
        )
        if phase_text in {"已完成", "失败", "空闲"}:
            self._stop_advance_tracking()

    def _refresh_config_list(self) -> None:
        previous_main = self.config_combo.currentData() if hasattr(self, "config_combo") else None
        previous_seed = self.seed_config_combo.currentData() if hasattr(self, "seed_config_combo") else None
        previous_reidentify = self.reidentify_config_combo.currentData() if hasattr(self, "reidentify_config_combo") else None
        self.config_combo.blockSignals(True)
        self.config_combo.clear()
        configs = sorted(PROJECT_XS_CONFIGS.glob("*.json"))
        for path in configs:
            self.config_combo.addItem(path.name, str(path))
        if configs:
            default = next((index for index, path in enumerate(configs) if path.name == "config_bebe.json"), 0)
            index = self.config_combo.findData(previous_main) if previous_main else -1
            self.config_combo.setCurrentIndex(index if index >= 0 else default)
        self.config_combo.blockSignals(False)
        if hasattr(self, "seed_config_combo"):
            self._populate_project_xs_combo(self.seed_config_combo, configs, previous_seed)
        if hasattr(self, "reidentify_config_combo"):
            self._populate_project_xs_combo(self.reidentify_config_combo, configs, previous_reidentify)
        self._load_config_to_form(self.config_combo.currentText())

    def _populate_project_xs_combo(self, combo: QComboBox, configs: list[Path], previous: object | None) -> None:
        combo.blockSignals(True)
        combo.clear()
        for path in configs:
            combo.addItem(path.name, str(path))
        if configs:
            default = next((index for index, path in enumerate(configs) if path.name == "config_bebe.json"), 0)
            index = combo.findData(previous) if previous else -1
            combo.setCurrentIndex(index if index >= 0 else default)
        combo.blockSignals(False)

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

    def _selected_auto_seed_config_path(self) -> str:
        data = self.seed_config_combo.currentData()
        return str(data or self.seed_config_combo.currentText())

    def _selected_auto_reidentify_config_path(self) -> str:
        data = self.reidentify_config_combo.currentData()
        return str(data or self.reidentify_config_combo.currentText())

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
        capture = self._shared_capture_config(capture)
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
            if search_max <= search_min:
                noisy_search_max = NOISY_REIDENTIFY_MAX_SEARCH_FRAMES
            elif search_min > 0:
                noisy_search_max = min(NOISY_REIDENTIFY_MAX_SEARCH_FRAMES, search_max - search_min)
            else:
                noisy_search_max = min(search_max, NOISY_REIDENTIFY_MAX_SEARCH_FRAMES)
            return reidentify_seed_from_observation_noisy(
                state,
                observation,  # type: ignore[arg-type]
                search_min=search_min,
                search_max=noisy_search_max,
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

    def set_capture_broker_process(self, process: object | None) -> None:
        """Install the independently hosted Broker process controller."""

        start_thread = self._capture_broker_start_thread
        if (
            self._video_source_connected
            or self._video_source_connecting
            or self._video_source_stop_pending
            or start_thread is not None
        ):
            raise RuntimeError("请先断开当前视频源")
        self._capture_broker_process = process

    def _clear_easycon_image_search_result(self, *_args: object) -> None:
        self._latest_easycon_image_search_result = None

    def _begin_easycon_image_search_run(self, *_args: object) -> None:
        self._easycon_run_generation += 1
        self._clear_easycon_image_search_result()
        self._install_easycon_image_result_callback(
            self._video_source_generation,
            self._easycon_run_generation,
        )

    def _install_easycon_image_result_callback(
        self,
        source_generation: int,
        run_generation: int,
    ) -> None:
        backend = self.easycon_tab._ensure_native_backend()
        setter = getattr(backend, "set_image_result_callback", None)
        if callable(setter):
            def publish_result(
                result: object,
                source: int = int(source_generation),
                run: int = int(run_generation),
            ) -> None:
                self._notify_easycon_image_result_observers(source, run, result)
                self.easyConImageSearchResultChanged.emit(source, run, result)

            setter(publish_result)

    def _invalidate_video_source_consumers(self) -> None:
        self._video_source_connected = False
        self._video_source_generation += 1
        self._preview_annotation_error_reported = False
        self._latest_preview_frame = None
        self._latest_annotated_preview_frame = None
        self._clear_easycon_image_search_result()
        self.easycon_tab.video_source_state_changed()

    def _clear_video_source_preview(self) -> None:
        self._latest_preview_frame = None
        self._latest_annotated_preview_frame = None
        self._clear_easycon_image_search_result()
        self.preview_label.clear()
        self.preview_label.setText(self._text("no_preview"))
        if self._picture_in_picture is not None:
            self._picture_in_picture.hide()

    def _set_video_source_status(self, text: str, state: str) -> None:
        self.video_source_status.setText(text)
        self._update_video_source_header(text, state)
        if self.video_source_status_dot.property("state") == state:
            return
        self.video_source_status_dot.setProperty("state", state)
        style = self.video_source_status_dot.style()
        style.unpolish(self.video_source_status_dot)
        style.polish(self.video_source_status_dot)
        self.video_source_status_dot.update()

    def _update_video_source_header(self, status: str, state: str) -> None:
        button = getattr(self, "video_source_header_button", None)
        if button is None:
            return
        colors = {
            "disconnected": "#9CA3AF",
            "connecting": "#D97706",
            "connected": "#0E8F70",
            "failed": "#DC2626",
        }
        if state == "connected":
            device_text = self.capture_device_combo.currentText().strip()
            device_name = device_text.partition(" - ")[2].strip() or device_text or "视频源"
            full_text = f"已连接 · {device_name}"
        elif state == "connecting":
            full_text = "视频源 断开中" if "断开" in status or "结束" in status else "视频源 连接中"
        elif state == "failed":
            full_text = "视频源 故障"
        else:
            full_text = "视频源 未连接"
        button.setText(
            button.fontMetrics().elidedText(
                full_text,
                Qt.TextElideMode.ElideRight,
                145,
            )
        )
        button.setIcon(_status_dot_icon(colors.get(state, colors["disconnected"])))
        button.setToolTip(f"{full_text}\n{status}\n点击打开视频源设置")

    def _set_video_source_config_enabled(self, enabled: bool) -> None:
        for widget in (
            self.capture_device_label,
            self.capture_device_combo,
            self.capture_device_refresh_button,
            self.capture_api_label,
            self.capture_api_combo,
        ):
            widget.setEnabled(enabled)

    def _set_video_source_disconnected_ui(self, status: str = "未连接") -> None:
        self.video_source_button.setEnabled(True)
        self.video_source_button.setText("连接视频源")
        state = "failed" if status == "连接失败" else "disconnected"
        self._set_video_source_status(status, state)
        self._set_video_source_config_enabled(True)
        self.preview_button.setEnabled(True)
        self.preview_button.setText(self._text("preview_button"))
        self._clear_video_source_preview()

    def _set_video_source_stop_failure(self, message: str) -> None:
        self._video_source_stop_pending = True
        self._video_source_stop_error = str(message)
        self._invalidate_video_source_consumers()
        start_thread = self._capture_broker_start_thread
        self.video_source_button.setEnabled(start_thread is None)
        self.video_source_button.setText("重试断开")
        self._set_video_source_status("停止失败，请重试", "failed")
        self._set_video_source_config_enabled(False)
        self.preview_button.setEnabled(False)
        self._clear_video_source_preview()

    def _video_source_diagnostic_snapshot(self) -> str:
        try:
            return self._video_source_diagnostic_snapshot_impl()
        except Exception as exc:
            # Diagnostics are strictly best-effort and must never prevent the
            # release/disconnect path from running.
            try:
                generation = self._video_source_generation
            except Exception:
                generation = "-"
            return (
                f"source_generation={generation}；diagnostic_error="
                f"{type(exc).__name__}: {exc}"
            )

    def _video_source_diagnostic_snapshot_impl(self) -> str:
        """Collect bounded Broker metadata for a video-source failure log.

        This function is deliberately best-effort.  It never reads or writes
        frame pixels and must not prevent the normal disconnect path when a
        process, manifest, or test double has already disappeared.
        """

        fields: list[str] = [f"source_generation={self._video_source_generation}"]

        def clean(value: object) -> str:
            return " ".join(str(value).split()).replace("；", "/") or "-"

        def append(name: str, value: object) -> None:
            if value is not None and value != "":
                fields.append(f"{name}={clean(value)}")

        process = self._capture_broker_process
        manifest = None
        manifest_path = None
        owned_pid: object | None = None
        owned_session: object | None = None
        manifest_verified = False
        if process is None:
            append("broker_process", "-")
        else:
            try:
                status = getattr(process, "status", None)
                if callable(status):
                    status = status()
                if status is not None:
                    status_name = getattr(status, "wire_name", None) or getattr(status, "name", None) or status
                    append("broker_state", status_name)
            except Exception as exc:
                append("broker_state_error", f"{type(exc).__name__}: {exc}")
            try:
                failure = getattr(process, "failure", None)
                if callable(failure):
                    failure = failure()
                append("broker_failure", failure)
            except Exception as exc:
                append("broker_failure_error", f"{type(exc).__name__}: {exc}")
            try:
                child = getattr(process, "process", None)
                owned_pid = getattr(child, "pid", None)
                if owned_pid is None:
                    owned_pid = getattr(process, "pid", None)
                append("broker_pid", owned_pid)
                poll = getattr(child, "poll", None)
                append("broker_exit", poll() if callable(poll) else None)
            except Exception as exc:
                append("broker_exit_error", f"{type(exc).__name__}: {exc}")

            try:
                owned_session = getattr(process, "_session_id", None)
                append("broker_session", owned_session)
            except Exception as exc:
                append("broker_session_error", f"{type(exc).__name__}: {exc}")

            try:
                manifest_path = getattr(process, "manifest_path", None)
            except Exception as exc:
                append("manifest_path_error", f"{type(exc).__name__}: {exc}")
                manifest_path = None
            append("manifest_path", manifest_path)
            if manifest_path is not None:
                try:
                    from auto_bdsp_rng.capture_broker import BrokerManifest

                    manifest = BrokerManifest.load(manifest_path)
                except Exception:
                    manifest = None
            if manifest is None:
                try:
                    manifest = getattr(process, "manifest", None)
                    if callable(manifest):
                        manifest = manifest()
                except Exception:
                    manifest = None

        if manifest is not None:
            manifest_pid = getattr(manifest, "pid", None)
            manifest_session = getattr(manifest, "session_id", None)
            mismatch: list[str] = []
            if owned_pid not in (None, "") and manifest_pid not in (None, ""):
                try:
                    if int(owned_pid) != int(manifest_pid):
                        mismatch.append(f"pid:{owned_pid}!={manifest_pid}")
                except (TypeError, ValueError, OverflowError):
                    if str(owned_pid) != str(manifest_pid):
                        mismatch.append(f"pid:{owned_pid}!={manifest_pid}")
            if owned_session not in (None, "") and manifest_session not in (None, ""):
                if str(owned_session) != str(manifest_session):
                    mismatch.append(f"session:{owned_session}!={manifest_session}")
            if mismatch:
                append("manifest_identity", "mismatch/" + "/".join(mismatch))
                append("manifest_pid", manifest_pid)
                append("manifest_session", manifest_session)
                manifest = None
            else:
                # A PID-only match is useful context but is not sufficient to
                # open a replacement ring: PIDs can be reused after a child
                # exits.  The controller normally records both values after
                # startup; legacy/test doubles may expose neither.
                manifest_verified = (
                    owned_pid not in (None, "")
                    and owned_session not in (None, "")
                )
                append(
                    "manifest_identity",
                    "verified" if manifest_verified else "unverified",
                )
                state = getattr(manifest, "state", None)
                state_name = getattr(state, "wire_name", None) or getattr(state, "name", None) or state
                append("manifest_state", state_name)
                append("manifest_session", manifest_session)
                append("manifest_pid", manifest_pid)
                append("manifest_failure", getattr(manifest, "failure_message", None))
                append("frame_timeout_s", getattr(manifest, "frame_timeout_seconds", None))
                capture_info = getattr(manifest, "capture", None)
                if isinstance(capture_info, dict):
                    for key in ("device_index", "api", "fourcc", "fps"):
                        append(f"capture_{key}", capture_info.get(key))

        if manifest is None:
            # A failed/removed manifest should not hide the controller's own
            # configuration.  These are only fallback fields; a verified
            # manifest remains the authoritative source above.
            for attribute, field_name in (
                ("device_index", "capture_device_index"),
                ("capture_api", "capture_api"),
            ):
                try:
                    append(field_name, getattr(process, attribute, None))
                except Exception as exc:
                    append(f"{field_name}_error", f"{type(exc).__name__}: {exc}")
            try:
                if process is None or getattr(process, "device_index", None) is None:
                    append("capture_device_index", self._capture_device_index())
            except Exception:
                pass
            try:
                if process is None or getattr(process, "capture_api", None) is None:
                    append("capture_api", self._capture_api())
            except Exception:
                pass

        # The reusable preview adapter may still expose the ring client here.
        # Include sequence/heartbeat ages when available, but tolerate the
        # adapter having already released itself after a read failure.
        preview = self._preview_capture
        video = getattr(preview, "_video", None) if preview is not None else None
        client = getattr(video, "_client", None)
        temporary_client = None
        if client is None and manifest_path is not None and manifest_verified:
            try:
                from auto_bdsp_rng.capture_broker import CaptureBrokerClient

                temporary_client = CaptureBrokerClient.connect(
                    manifest_path,
                    require_running=False,
                    require_live_pid=False,
                )
                client = temporary_client
            except Exception:
                temporary_client = None
        if client is not None:
            try:
                append("preview_last_sequence", getattr(video, "_last_sequence", None))
                header_reader = getattr(client, "snapshot_header", None)
                header = header_reader() if callable(header_reader) else None
                if not isinstance(header, dict):
                    ring = getattr(client, "_ring", None)
                    snapshot = getattr(ring, "snapshot_header", None)
                    header = snapshot() if callable(snapshot) else None
                if isinstance(header, dict):
                    append("latest_sequence", header.get("latest_sequence"))
                    heartbeat_ns = int(header.get("heartbeat_monotonic_ns", 0) or 0)
                    if heartbeat_ns > 0:
                        append("heartbeat_age_ms", f"{max(0, time.monotonic_ns() - heartbeat_ns) / 1_000_000:.1f}")
                    append("ring_state_code", header.get("state_code"))
                metadata_reader = getattr(client, "read_latest_metadata", None)
                if callable(metadata_reader):
                    metadata = metadata_reader()
                else:
                    ring = getattr(client, "_ring", None)
                    metadata_reader = getattr(ring, "read_latest_metadata", None)
                    metadata = metadata_reader() if callable(metadata_reader) else None
                if metadata is not None and len(metadata) >= 2:
                    append("latest_frame_sequence", metadata[0])
                    timestamp_ns = int(metadata[1] or 0)
                    if timestamp_ns > 0:
                        append("latest_frame_age_ms", f"{max(0, time.monotonic_ns() - timestamp_ns) / 1_000_000:.1f}")
            except Exception as exc:
                append("preview_metadata_error", f"{type(exc).__name__}: {exc}")
            finally:
                if temporary_client is not None:
                    try:
                        temporary_client.close()
                    except Exception:
                        pass

        return "；".join(fields)

    def _stop_capture_broker_process(
        self,
        process: object | None,
        *,
        context: str,
    ) -> tuple[bool, str | None]:
        if process is None:
            if self._video_source_stop_pending:
                message = "共享视频源进程控制器不存在，无法确认停止"
                self._write_run_log("视频源", f"{context}: {message}", level="WARNING")
                return False, message
            return True, None
        stop = getattr(process, "stop", None)
        if not callable(stop):
            message = "共享视频源进程控制器不支持停止"
            self._write_run_log("视频源", f"{context}: {message}", level="WARNING")
            return False, message
        try:
            result = stop()
        except Exception as exc:
            message = str(exc) or type(exc).__name__
            self._write_run_log("视频源", f"{context}: {message}", level="WARNING")
            return False, message
        if result is False:
            message = "Broker 未确认停止"
            self._write_run_log("视频源", f"{context}: {message}", level="WARNING")
            return False, message
        return True, None

    def _capture_device_index(self) -> int:
        data = self.capture_device_combo.currentData()
        text = self.capture_device_combo.currentText().strip()
        try:
            if self.capture_device_combo.currentIndex() >= 0 and data is not None:
                return max(0, int(data))
            index_text = text.partition(" - ")[0].strip()
            return max(0, int(index_text if index_text else data))
        except (TypeError, ValueError) as exc:
            raise ProjectXsIntegrationError("采集卡设备序号无效") from exc

    def refresh_capture_devices(self) -> None:
        selected_name = self.capture_device_combo.currentText().partition(" - ")[2].strip()
        try:
            selected_index = self._capture_device_index()
        except ProjectXsIntegrationError:
            selected_index = self._profile_settings_int(
                self._profile_settings.value("video_source/device_index", 0),
                0,
            )
        devices = _enumerate_capture_devices(self._capture_api())
        if not devices:
            fallback_indices = list(range(10))
            if selected_index not in fallback_indices:
                fallback_indices.append(selected_index)
            devices = [(index, "") for index in fallback_indices]
        self.capture_device_combo.blockSignals(True)
        self.capture_device_combo.clear()
        for index, name in devices:
            label = f"{index} - {name}" if name else str(index)
            self.capture_device_combo.addItem(label, index)
        selected = -1
        if selected_name:
            selected = next(
                (
                    row
                    for row, (_index, name) in enumerate(devices)
                    if name == selected_name
                ),
                -1,
            )
        if selected < 0:
            selected = self.capture_device_combo.findData(selected_index)
        self.capture_device_combo.setCurrentIndex(max(0, selected))
        self.capture_device_combo.blockSignals(False)

    def _capture_api(self) -> int:
        return int(self.capture_api_combo.currentData() or 0)

    def _new_broker_client_for(self, controller: object | None) -> object:
        if controller is not None:
            for name in ("client", "connect_client", "create_client"):
                value = getattr(controller, name, None)
                if callable(value):
                    client = value()
                    if client is not None:
                        return client
                elif value is not None:
                    return value
        from auto_bdsp_rng.capture_broker import CaptureBrokerClient

        return CaptureBrokerClient.connect(require_running=True)

    def _new_broker_client(self) -> object:
        return self._new_broker_client_for(self._capture_broker_process)

    def _shared_capture_config(self, capture: BlinkCaptureConfig) -> BlinkCaptureConfig:
        if not self._video_source_connected:
            return capture
        return replace(
            capture,
            source="broker",
            video_source="broker",
            frame_source_factory=self._new_broker_client,
        )

    def _create_capture_broker_process(self, device_index: int, capture_api: int) -> object:
        try:
            module = __import__("auto_bdsp_rng.capture_broker_process", fromlist=["CaptureBrokerProcess"])
            process_type = getattr(module, "CaptureBrokerProcess")
        except (ImportError, AttributeError) as exc:
            raise ProjectXsIntegrationError("共享视频源进程组件尚未安装") from exc
        try:
            return process_type(device_index=device_index, capture_api=capture_api)
        except TypeError:
            return process_type(device_index, capture_api)

    def _start_capture_broker_process(self, process: object, device_index: int, capture_api: int) -> bool:
        configure = getattr(process, "configure", None)
        if callable(configure):
            configure(device_index=device_index, capture_api=capture_api)
        start = getattr(process, "start", None)
        if not callable(start):
            raise ProjectXsIntegrationError("共享视频源进程控制器不支持启动")
        for kwargs in (
            {"device_index": device_index, "capture_api": capture_api},
            {},
        ):
            try:
                result = start(**kwargs)
                if result is False:
                    failure = getattr(process, "failure", None)
                    if failure:
                        raise RuntimeError(str(failure))
                    return False
                return True
            except TypeError:
                continue
        raise ProjectXsIntegrationError("共享视频源进程启动参数不兼容")

    def toggle_video_source(self) -> None:
        if self._video_source_connecting:
            return
        if self._video_source_connected or self._video_source_stop_pending:
            self.disconnect_video_source()
        else:
            self.connect_video_source()

    def connect_video_source(self) -> bool:
        if self._video_source_connected:
            return True
        start_thread = self._capture_broker_start_thread
        if self._video_source_connecting or self._video_source_stop_pending:
            return False
        if start_thread is not None:
            return False
        try:
            device_index = self._capture_device_index()
            capture_api = self._capture_api()
            process = self._capture_broker_process
            if process is None:
                process = self._create_capture_broker_process(device_index, capture_api)
                self._capture_broker_process = process
        except Exception as exc:
            self._show_error("视频源连接失败", exc if isinstance(exc, Exception) else Exception(str(exc)))
            return False

        self._capture_broker_attempt += 1
        attempt = self._capture_broker_attempt
        thread = CaptureBrokerStartThread(
            lambda: self._start_capture_broker_process(process, device_index, capture_api),
            lambda: self._new_broker_client_for(process),
            self,
        )
        thread.completed.connect(
            lambda success, payload, current=thread, token=attempt, controller=process: (
                self._finish_video_source_connection(
                    current,
                    token,
                    controller,
                    device_index,
                    capture_api,
                    success,
                    payload,
                )
            )
        )
        thread.finished.connect(
            lambda current=thread, token=attempt, controller=process: self._capture_broker_start_finished(
                current,
                token,
                controller,
            )
        )
        thread.finished.connect(thread.deleteLater)
        self._capture_broker_start_thread = thread
        self._video_source_connecting = True
        self._video_source_cancel_requested = False
        self._video_source_pending_status = "连接失败"
        self._clear_easycon_image_search_result()
        self.video_source_button.setEnabled(False)
        self.video_source_button.setText("连接中...")
        self._set_video_source_status("正在等待第一帧", "connecting")
        self._set_video_source_config_enabled(False)
        thread.start()
        return True

    def _finish_video_source_connection(
        self,
        thread: CaptureBrokerStartThread,
        attempt: int,
        process: object,
        device_index: int,
        capture_api: int,
        success: bool,
        payload: object,
    ) -> None:
        if (
            thread is not self._capture_broker_start_thread
            or attempt != self._capture_broker_attempt
            or process is not self._capture_broker_process
        ):
            return
        if self._is_closing or self._video_source_cancel_requested:
            return
        if not success:
            stopped, stop_error = self._stop_capture_broker_process(
                process,
                context="清理启动失败的 Broker 时出错",
            )
            self._video_source_connected = False
            self._video_source_pending_status = "连接失败"
            if stopped:
                self._video_source_stop_pending = False
                self._video_source_stop_error = None
                self._set_video_source_status("连接失败，正在清理", "connecting")
            else:
                self._set_video_source_stop_failure(stop_error or "Broker 未确认停止")
            error = payload if isinstance(payload, BaseException) else RuntimeError(str(payload))
            self._show_error("视频源连接失败", error)
            return

        self._video_source_connecting = False
        self._video_source_connected = True
        self._video_source_stop_pending = False
        self._video_source_stop_error = None
        self._video_source_cancel_requested = False
        self._video_source_pending_status = "未连接"
        self._video_source_generation += 1
        self._preview_annotation_error_reported = False
        self._clear_easycon_image_search_result()
        self._install_easycon_image_result_callback(
            self._video_source_generation,
            self._easycon_run_generation,
        )
        self.easycon_tab.video_source_state_changed()
        self.video_source_button.setEnabled(True)
        self.video_source_button.setText("断开视频源")
        self._set_video_source_status("已连接", "connected")
        self._set_video_source_config_enabled(False)
        self.preview_button.setEnabled(False)
        self.preview_button.setText("预览常驻")
        self._profile_settings.setValue("video_source/device_index", device_index)
        self._profile_settings.setValue("video_source/capture_api", capture_api)
        self._latest_preview_frame = None
        self._release_preview_capture()
        self._preview_timer.start()
        self.statusBar().showMessage("共享视频源已连接，预览将持续显示")
        self.video_source_dialog.hide()

    def _capture_broker_start_finished(
        self,
        thread: CaptureBrokerStartThread,
        attempt: int,
        process: object | None = None,
    ) -> None:
        if thread is not self._capture_broker_start_thread or attempt != self._capture_broker_attempt:
            return
        if self._video_source_connected:
            self._capture_broker_start_thread = None
            return
        controller = self._capture_broker_process if process is None else process
        stopped, stop_error = self._stop_capture_broker_process(
            controller,
            context="连接线程结束后最终确认 Broker 停止失败",
        )
        self._capture_broker_start_thread = None
        self._video_source_connecting = False
        self._video_source_cancel_requested = False
        if not stopped:
            self._set_video_source_stop_failure(stop_error or "Broker 未确认停止")
            return
        self._video_source_stop_pending = False
        self._video_source_stop_error = None
        self._set_video_source_disconnected_ui(self._video_source_pending_status)

    def disconnect_video_source(
        self,
        *,
        force: bool = False,
        reason: str | None = None,
    ) -> bool:
        start_thread = self._capture_broker_start_thread
        starter_pending = start_thread is not None
        starter_running = start_thread is not None and start_thread.isRunning()
        was_starting = self._video_source_connecting and not self._video_source_connected
        if (
            not self._video_source_connected
            and not self._video_source_connecting
            and not self._video_source_stop_pending
            and not starter_pending
        ):
            return True
        active = (
            self._is_capturing()
            or self.auto_rng_tab._runner_thread is not None
            or self.auto_tid_rng_tab._runner_thread is not None
            or self.easycon_tab._native_status() == EasyConStatus.RUNNING
        )
        if active and not force:
            choice = QMessageBox.question(
                self,
                "切换视频源",
                "正在运行的任务需要当前视频源。是否停止这些任务并断开？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if choice != QMessageBox.StandardButton.Yes:
                return False
        if active:
            stop_reason = reason or "用户断开/切换视频源"
            self._capture_cancel.set()
            self._request_automation_runner_stop_with_reason(
                self.auto_rng_tab,
                "自动定点",
                stop_reason,
            )
            self._request_automation_runner_stop_with_reason(
                self.auto_tid_rng_tab,
                "自动 TID",
                stop_reason,
            )
            if self.easycon_tab._native_status() == EasyConStatus.RUNNING:
                self.easycon_tab.stop_native_script()

        self._preview_timer.stop()
        self._release_preview_capture()
        if starter_pending and start_thread is not None:
            self._video_source_cancel_requested = True
            if was_starting:
                self._video_source_pending_status = "连接已取消"
            start_thread.requestInterruption()
        process = self._capture_broker_process
        if self._video_source_connected or self._video_source_connecting or starter_pending:
            self._invalidate_video_source_consumers()
        else:
            self._clear_video_source_preview()
        stopped, stop_error = self._stop_capture_broker_process(
            process,
            context="停止 Broker 失败",
        )
        starter_running = start_thread is not None and start_thread.isRunning()
        if starter_pending and not starter_running:
            stopped, stop_error = self._stop_capture_broker_process(
                process,
                context="取消连接后最终确认 Broker 停止失败",
            )
        if starter_pending and not starter_running and self._capture_broker_start_thread is start_thread:
            self._capture_broker_start_thread = None
        if not stopped:
            if not starter_running:
                self._video_source_connecting = False
                self._video_source_cancel_requested = False
            self._set_video_source_stop_failure(stop_error or "Broker 未确认停止")
            return False
        self._video_source_stop_pending = False
        self._video_source_stop_error = None
        if starter_running:
            self._video_source_connecting = True
            self.video_source_button.setEnabled(False)
            self.video_source_button.setText("正在断开...")
            self._set_video_source_status("正在结束视频源连接", "connecting")
            return False
        self._video_source_connecting = False
        self._video_source_cancel_requested = False
        status = self._video_source_pending_status if starter_pending else "未连接"
        self._video_source_pending_status = "未连接"
        self._set_video_source_disconnected_ui(status)
        return True

    def show_picture_in_picture(self) -> None:
        if self._picture_in_picture is None:
            self._picture_in_picture = PictureInPicturePreview(self)
            self._picture_in_picture.roiSelected.connect(self._handle_preview_selection)
            overlay_region = self.preview_label._ocr_overlay_region
            if overlay_region is not None:
                self._picture_in_picture.set_ocr_overlay(
                    self.preview_label._ocr_overlay_field or "ocr",
                    overlay_region,
                )
        self._picture_in_picture.set_selection_enabled(self._selection_mode is not None)
        self._sync_picture_in_picture_frame()
        self._picture_in_picture.show()
        self._picture_in_picture.raise_()

    def _sync_picture_in_picture_frame(self) -> None:
        if self._picture_in_picture is None:
            return
        if self._selection_preview_frame is not None:
            self._picture_in_picture.set_frames(
                self._selection_preview_frame,
                self._selection_preview_frame,
            )
            return
        if self._latest_preview_frame is not None:
            self._picture_in_picture.set_frames(
                self._latest_preview_frame,
                self._latest_annotated_preview_frame,
            )

    def _set_preview_selection_enabled(self, enabled: bool) -> None:
        self.preview_label.set_selection_enabled(bool(enabled))
        if self._picture_in_picture is not None:
            self._picture_in_picture.set_selection_enabled(bool(enabled))

    def _set_preview_ocr_overlay(
        self,
        field: str,
        region: OcrRegion | tuple[int, int, int, int],
    ) -> None:
        self.preview_label.set_ocr_overlay(field, region)
        if self._picture_in_picture is not None:
            self._picture_in_picture.set_ocr_overlay(field, region)

    def show_video_source_dialog(self) -> None:
        self.video_source_dialog.adjustSize()
        self.video_source_dialog.show()
        self.video_source_dialog.raise_()
        self.video_source_dialog.activateWindow()

    def _refresh_preview_presentation(self) -> None:
        self.preview_label.set_overlay_enabled(self.main_preview_overlay_check.isChecked())
        frame = (
            self._latest_annotated_preview_frame
            if self.main_preview_overlay_check.isChecked()
            else self._latest_preview_frame
        )
        if frame is not None:
            self._display_frame(frame)

    def _release_preview_capture(self) -> None:
        capture = self._preview_capture
        self._preview_capture = None
        if capture is None:
            return
        try:
            capture.release()
        except Exception as exc:
            self._write_run_log("Seed 捕捉", f"关闭预览捕捉源失败: {exc}", level="WARNING")

    def _read_live_preview_frame(self, config: BlinkCaptureConfig) -> object:
        capture = self._preview_capture
        if capture is None or not _uses_same_capture_source(capture.config, config):
            self._release_preview_capture()
            capture = PreviewFrameCapture(config)
            if capture.keep_open_for_preview:
                self._preview_capture = capture
        try:
            return capture.read()
        except Exception:
            if capture is self._preview_capture:
                self._release_preview_capture()
            raise
        finally:
            if capture is not self._preview_capture:
                capture.release()

    def _capture_preview_frame_for_config(self, config: BlinkCaptureConfig) -> object:
        def read_active_preview() -> tuple[bool, object | None]:
            if not self._preview_timer.isActive():
                return False, None
            try:
                active_config = self._config_from_form().capture
            except Exception:
                return False, None
            if not _uses_same_capture_source(active_config, config):
                return False, None
            return True, self._read_live_preview_frame(config)

        used_preview, frame = self._call_on_ui_thread(read_active_preview)
        if used_preview:
            return frame
        return capture_preview_frame(config)

    def toggle_preview(self) -> None:
        if self._is_capturing():
            return
        if self._video_source_connected:
            return
        if self._preview_timer.isActive():
            self._preview_timer.stop()
            self._release_preview_capture()
            self.preview_button.setText(self._text("preview_button"))
            self._set_preview_selection_enabled(False)
            self.preview_label.clear()
            self.preview_label.setText(self._text("no_preview"))
            return
        self._preview_timer.start()
        self.preview_button.setText(self._text("stop_preview"))
        self.statusBar().showMessage(self._text("preview_running"))

    def _pause_preview_for_capture(self) -> None:
        self._resume_preview_after_capture = self._preview_timer.isActive()
        if self._video_source_connected:
            return
        if self._resume_preview_after_capture:
            self._preview_timer.stop()
            self._release_preview_capture()
            self.preview_button.setText(self._text("stop_preview"))

    def _restore_preview_after_capture(self) -> None:
        if self._video_source_connected:
            if not self._preview_timer.isActive():
                self._preview_timer.start()
            self._resume_preview_after_capture = False
            return
        if self._resume_preview_after_capture:
            self._preview_timer.start()
            self.preview_button.setText(self._text("stop_preview"))
        self._resume_preview_after_capture = False

    def _ensure_preview_frame_before_capture(self) -> bool:
        if self._capture_broker_process is not None and not self._video_source_connected:
            QMessageBox.warning(self, "视频源未连接", "请先连接视频源后再运行需要画面的任务。")
            return False
        if self._latest_preview_frame is not None:
            return True
        if not self._preview_timer.isActive():
            self._preview_timer.start()
            self.preview_button.setText(self._text("stop_preview"))
            self.statusBar().showMessage("预览已自动启动，等待摄像头就绪…")
        # 等待摄像头首帧到达（最多等 5 秒）
        waited = 0.0
        while self._latest_preview_frame is None and waited < 5.0:
            time.sleep(0.2)
            waited += 0.2
            self._update_preview_frame()
        # Legacy camera capture must release the handle before blink tracking;
        # Broker consumers can keep the shared preview alive throughout.
        if (
            not self._video_source_connected
            and self._latest_preview_frame is not None
            and self._preview_timer.isActive()
        ):
            self._preview_timer.stop()
            self._release_preview_capture()
            self.preview_button.setText(self._text("preview_button"))
            self.preview_label.clear()
            self.preview_label.setText(self._text("no_preview"))
        return self._latest_preview_frame is not None

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

    def start_ocr_region_selection(self, field: str) -> None:
        if self._is_capturing():
            return
        self._ocr_selection_field = field
        self._begin_preview_selection("ocr_region")
        label = OCR_REGION_LABELS.get(field, field)
        self.statusBar().showMessage(f"正在框选 OCR 区域：{label}")

    def start_tid_ocr_region_selection(self) -> None:
        if self._is_capturing():
            return
        self._begin_preview_selection("tid_ocr_region")
        self.statusBar().showMessage("正在框选 TID OCR 区域")

    def _begin_preview_selection(self, mode: str) -> None:
        self._selection_mode = mode
        self._resume_preview_after_selection = self._preview_timer.isActive()
        if self._preview_timer.isActive() and not self._video_source_connected:
            self._preview_timer.stop()
            self._release_preview_capture()
            self.preview_button.setText(self._text("preview_button"))
        if self._latest_preview_frame is None:
            try:
                frame = self._capture_preview_frame_for_config(self._config_from_form().capture)
                frame_copy = getattr(frame, "copy", None)
                self._latest_preview_frame = frame_copy() if callable(frame_copy) else frame
            except Exception as exc:
                self._selection_mode = None
                self._ocr_selection_field = None
                self._roi_before_selection = None
                self._restore_preview_after_selection()
                self._show_error("Preview failed", exc if isinstance(exc, Exception) else Exception(str(exc)))
                return
        frame_copy = getattr(self._latest_preview_frame, "copy", None)
        self._selection_preview_frame = (
            frame_copy() if callable(frame_copy) else self._latest_preview_frame
        )
        self._display_frame(self._selection_preview_frame)
        self._set_preview_selection_enabled(True)
        self._sync_picture_in_picture_frame()

    def _handle_preview_selection(self, roi: object) -> None:
        if self._selection_mode not in {"eye", "roi", "ocr_region", "tid_ocr_region"}:
            return
        if not self._confirm_preview_selection(roi):
            self._cancel_preview_selection()
            return
        if self._selection_mode == "eye":
            self.apply_selected_eye(roi)
        elif self._selection_mode == "ocr_region":
            self.apply_selected_ocr_region(roi)
        elif self._selection_mode == "tid_ocr_region":
            self.apply_selected_tid_ocr_region(roi)
        elif self._selection_mode == "roi":
            self.apply_selected_roi(roi)

    def _confirm_preview_selection(self, roi: object) -> bool:
        try:
            x, y, width, height = (int(value) for value in roi)  # type: ignore[union-attr]
        except Exception:
            x = y = width = height = 0
        if self._selection_mode == "eye":
            title = "确认眼睛模板"
            message = f"是否使用当前框选区域作为眼睛模板？\n区域: X={x}, Y={y}, W={width}, H={height}"
        elif self._selection_mode == "ocr_region":
            label = OCR_REGION_LABELS.get(self._ocr_selection_field or "", self._ocr_selection_field or "OCR")
            title = "确认 OCR 区域"
            message = f"是否保存“{label}”区域？\n区域: X={x}, Y={y}, W={width}, H={height}"
        elif self._selection_mode == "tid_ocr_region":
            title = "确认 TID OCR 区域"
            message = f"是否保存当前区域作为 TID OCR ROI？\n区域: X={x}, Y={y}, W={width}, H={height}"
        else:
            title = "确认眼睛区域"
            message = f"是否使用当前框选区域作为眼睛 ROI？\n区域: X={x}, Y={y}, W={width}, H={height}"
        dialog_parent = (
            self._picture_in_picture
            if self._picture_in_picture is not None and self._picture_in_picture.isVisible()
            else self
        )
        return QMessageBox.question(
            dialog_parent,
            title,
            message,
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Ok,
        ) == QMessageBox.StandardButton.Ok

    def _cancel_preview_selection(self) -> None:
        self._set_preview_selection_enabled(False)
        self._roi_before_selection = None
        self._ocr_selection_field = None
        self._selection_mode = None
        self._restore_preview_after_selection()
        self.statusBar().showMessage("已取消框选，继续使用之前的设置")

    def _restore_preview_after_selection(self) -> None:
        self._selection_preview_frame = None
        resume_preview = self._resume_preview_after_selection
        self._resume_preview_after_selection = False
        if resume_preview and not self._preview_timer.isActive():
            self._preview_timer.start()
        if self._video_source_connected:
            self.preview_button.setText("预览常驻")
        elif resume_preview:
            self.preview_button.setText(self._text("stop_preview"))
        self._refresh_preview_presentation()
        self._sync_picture_in_picture_frame()

    def apply_selected_ocr_region(self, roi: object) -> None:
        field = self._ocr_selection_field
        if not field:
            self._cancel_preview_selection()
            return
        x, y, width, height = (int(value) for value in roi)  # type: ignore[union-attr]
        region = OcrRegion(x, y, width, height)
        self._set_preview_ocr_overlay(field, region)
        self._set_preview_selection_enabled(False)
        self._selection_mode = None
        self._ocr_selection_field = None
        self._restore_preview_after_selection()
        self.ocrRegionSelected.emit(field, region.as_tuple())
        label = OCR_REGION_LABELS.get(field, field)
        self.statusBar().showMessage(f"OCR 区域已保存：{label} X={x}, Y={y}, W={width}, H={height}")

    def apply_selected_tid_ocr_region(self, roi: object) -> None:
        x, y, width, height = (int(value) for value in roi)  # type: ignore[union-attr]
        region = OcrRegion(x, y, width, height)
        self._set_preview_ocr_overlay("tid", region)
        self._set_preview_selection_enabled(False)
        self._selection_mode = None
        self._restore_preview_after_selection()
        self.tidOcrRegionSelected.emit(region.as_tuple())
        self.statusBar().showMessage(f"TID OCR 区域已保存：X={x}, Y={y}, W={width}, H={height}")

    def apply_selected_roi(self, roi: object) -> None:
        old_roi = self._roi_before_selection or (int(self.x.text() or 0), int(self.y.text() or 0), int(self.w.text() or 0), int(self.h.text() or 0))
        x, y, width, height = (int(value) for value in roi)  # type: ignore[union-attr]
        try:
            config = self._config_from_form().capture
            from auto_bdsp_rng.blink_detection.project_xs import _load_eye_template

            eye_image = _load_eye_template(config)
            eye_width, eye_height = eye_image.shape[::-1]
            if width < eye_width or height < eye_height:
                raise ValueError(self._text("roi_too_small"))
        except Exception as exc:
            self._set_roi_values(old_roi)
            self._set_preview_selection_enabled(False)
            self._roi_before_selection = None
            self._selection_mode = None
            self._restore_preview_after_selection()
            self._show_error("ROI failed", exc if isinstance(exc, Exception) else Exception(str(exc)))
            return
        self._set_roi_values((x, y, width, height))
        self._set_preview_selection_enabled(False)
        self._roi_before_selection = None
        self._selection_mode = None
        self._restore_preview_after_selection()
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

            frame = self._selection_preview_frame
            if frame is None:
                frame = self._latest_preview_frame
            if frame is None:
                frame = self._capture_preview_frame_for_config(self._config_from_form().capture)
            frame_height, frame_width = frame.shape[:2]
            left = max(0, min(x, frame_width - 1))
            top = max(0, min(y, frame_height - 1))
            right = max(left + 1, min(x + width, frame_width))
            bottom = max(top + 1, min(y + height, frame_height))
            eye = frame[top:bottom, left:right]
            if len(eye.shape) == 3:
                eye = cv2.cvtColor(eye, cv2.COLOR_BGR2GRAY)
            output_dir = resource_path("third_party", "Project_Xs_CHN", "images", "custom")
            output_dir.mkdir(parents=True, exist_ok=True)
            config_name = Path(self._selected_config_path()).stem or "current"
            output_path = output_dir / f"{config_name}_eye.png"
            if not cv2.imwrite(str(output_path), eye):
                raise ProjectXsIntegrationError(f"Cannot save eye template: {output_path}")
        except Exception as exc:
            self._set_preview_selection_enabled(False)
            self._selection_mode = None
            self._restore_preview_after_selection()
            self._show_error("Eye capture failed", exc if isinstance(exc, Exception) else Exception(str(exc)))
            return
        self._eye_image_path = output_path
        self._selection_mode = "roi"
        self._roi_before_selection = (int(self.x.text() or 0), int(self.y.text() or 0), int(self.w.text() or 0), int(self.h.text() or 0))
        self._restore_preview_after_selection()
        self._display_frame(self._latest_preview_frame if self._latest_preview_frame is not None else frame)
        self._set_preview_selection_enabled(True)
        self.statusBar().showMessage(f"{self._text('eye_captured_select_roi')}: {output_path}")

    def _set_roi_values(self, roi: tuple[int, int, int, int]) -> None:
        self.x.setText(str(roi[0]))
        self.y.setText(str(roi[1]))
        self.w.setText(str(roi[2]))
        self.h.setText(str(roi[3]))

    def open_ocr_settings(self) -> None:
        if self._ocr_settings_dialog is None:
            dialog = OcrSettingsDialog(self)
            dialog.regionSelectionRequested.connect(self.start_ocr_region_selection)
            dialog.regionDisplayRequested.connect(self._show_ocr_region_overlay)
            dialog.recognitionRequested.connect(self._request_ocr_region_recognition)
            dialog.warmupRequested.connect(self._start_ocr_warmup)
            dialog.fullTestRequested.connect(self._start_ocr_full_test)
            self.ocrRegionSelected.connect(dialog.set_region)
            self.ocrWarmupFinished.connect(dialog.finish_warmup)
            self.ocrFullTestFinished.connect(dialog.finish_full_test)
            self._ocr_settings_dialog = dialog
            if self._ocr_warmup_result is not None:
                dialog.finish_warmup(*self._ocr_warmup_result)
            elif self._ocr_warmup_running:
                dialog.show_warmup_running()
            dialog.set_automation_active(
                self.auto_rng_tab._runner_thread is not None
                or self._shiny_calibration_worker is not None
            )
            if self._latest_preview_frame is not None:
                image_shape = getattr(self._latest_preview_frame, "shape", None)
                if image_shape is not None:
                    dialog.set_preview_frame_shape(tuple(image_shape))
        self._ocr_settings_dialog.show()
        self._ocr_settings_dialog.raise_()
        self._ocr_settings_dialog.activateWindow()

    def open_tid_ocr_settings(self) -> None:
        if self._tid_ocr_dialog is None:
            dialog = TidOcrDialog(self, recognizer=self._recognize_tid_ocr_region)
            dialog.regionSelectionRequested.connect(self.start_tid_ocr_region_selection)
            dialog.regionDisplayRequested.connect(self._show_tid_ocr_region_overlay)
            self.tidOcrRegionSelected.connect(dialog.set_region)
            self._tid_ocr_dialog = dialog
        self._tid_ocr_dialog.show()
        self._tid_ocr_dialog.raise_()
        self._tid_ocr_dialog.activateWindow()

    def _show_ocr_region_overlay(self, field: str, region: object) -> None:
        configured_region = region if isinstance(region, OcrRegion) else None
        if region is not None and configured_region is None:
            configured_region = OcrRegion(*(int(value) for value in region))  # type: ignore[arg-type]
        try:
            frame = self._latest_preview_frame
            if frame is None:
                if self.auto_rng_tab._runner_thread is not None:
                    self.statusBar().showMessage("自动流程运行中，当前没有可显示的 OCR 预览帧")
                    return
                frame = self._current_preview_frame_for_ocr()
            image_shape = tuple(getattr(frame, "shape"))
            effective_region = self._ocr_region_config().resolve(field, image_shape)
            if effective_region is None:
                self.statusBar().showMessage(f"无法在当前画面中显示 OCR 区域：{OCR_REGION_LABELS.get(field, field)}")
                return
            if configured_region is not None:
                image_height, image_width = image_shape[:2]
                if not configured_region.clip(image_width, image_height).is_valid():
                    self._write_run_log(
                        "OCR",
                        f"{OCR_REGION_LABELS.get(field, field)}自定义 ROI 在当前画面中无效，已使用默认范围",
                        level="WARNING",
                    )
            self._set_preview_ocr_overlay(field, effective_region)
            self._display_frame(frame)
            if self._ocr_settings_dialog is not None:
                self._ocr_settings_dialog.set_preview_frame_shape(image_shape)
        except Exception:
            if self._latest_preview_frame is not None:
                self._display_frame(self._latest_preview_frame)
            return
        label = OCR_REGION_LABELS.get(field, field)
        self.statusBar().showMessage(
            f"显示 OCR 区域：{label} X={effective_region.x}, Y={effective_region.y}, "
            f"W={effective_region.width}, H={effective_region.height}"
        )

    def _show_tid_ocr_region_overlay(self, region: object) -> None:
        if not isinstance(region, OcrRegion):
            region = OcrRegion(*(int(value) for value in region))  # type: ignore[arg-type]
        self._set_preview_ocr_overlay("tid", region)
        try:
            self._display_frame(self._current_preview_frame_for_ocr())
        except Exception:
            if self._latest_preview_frame is not None:
                self._display_frame(self._latest_preview_frame)
        self.statusBar().showMessage("显示 TID OCR 区域")

    def _current_preview_frame_for_ocr(self) -> object:
        if self._latest_preview_frame is not None:
            return self._latest_preview_frame
        frame = self._capture_preview_frame_for_config(self._config_from_form().capture)
        frame_copy = getattr(frame, "copy", None)
        self._latest_preview_frame = frame_copy() if callable(frame_copy) else frame
        self._display_frame(self._latest_preview_frame)
        return self._latest_preview_frame

    def _recognize_ocr_region(self, field: str, region: OcrRegion) -> str:
        label = OCR_REGION_LABELS.get(field, field)
        try:
            frame = self._current_preview_frame_for_ocr()
            text = recognize_ocr_field(frame, field, region)
        except Exception as exc:
            self._write_run_log("OCR", f"{label}识别失败: {exc}", level="ERROR")
            raise
        self._write_run_log("OCR", f"{label}识别结果: {text or '空'}")
        return text

    def _set_ocr_automation_active(self, active: bool) -> None:
        if self._ocr_settings_dialog is not None:
            self._ocr_settings_dialog.set_automation_active(active)
        self._refresh_shiny_calibration_button_state()

    def _ocr_activity_running(self) -> bool:
        if self._ocr_warmup_running or self._ocr_full_test_running:
            return True
        thread = self._ocr_task_thread
        if thread is None:
            return False
        try:
            return thread.isRunning()
        except RuntimeError:
            return False

    def _refresh_shiny_calibration_button_state(self) -> None:
        if self._shiny_calibration_worker is not None:
            return
        auto_rng_active = self.auto_rng_tab._runner_thread is not None
        self.calibrate_shiny_threshold_button.setEnabled(
            not auto_rng_active and not self._ocr_activity_running()
        )

    def _request_ocr_region_recognition(self, field: str, region: object) -> None:
        configured_region = region if isinstance(region, OcrRegion) else None
        if self.auto_rng_tab._runner_thread is not None:
            if self._ocr_settings_dialog is not None:
                self._ocr_settings_dialog.fail_recognition(field, "自动流程运行中")
            return
        if self._shiny_calibration_worker is not None:
            if self._ocr_settings_dialog is not None:
                self._ocr_settings_dialog.fail_recognition(field, "闪光判定校准运行中")
            return
        action = lambda: self._start_ocr_region_recognition(field, configured_region)
        if self._ocr_warmup_result is not None and self._ocr_warmup_result[0]:
            action()
            return
        self._ocr_after_warmup = (f"recognize:{field}", action)
        self._start_ocr_warmup()

    def _start_managed_ocr_task(
        self,
        label: str,
        task: Callable[[Callable[[], bool]], object],
        completed: Callable[[bool, object], None],
    ) -> bool:
        current = self._ocr_task_thread
        if current is not None and current.isRunning():
            return False
        self._ocr_shutdown_requested = False
        thread = OcrTaskThread(task, self)
        thread.completed.connect(self._handle_ocr_task_completed)
        thread.finished.connect(self._handle_ocr_task_thread_finished)
        thread.finished.connect(thread.deleteLater)
        self._ocr_task_thread = thread
        self._ocr_task_label = label
        self._ocr_task_completed = completed
        thread.start()
        self._refresh_shiny_calibration_button_state()
        return True

    def _handle_ocr_task_completed(self, success: bool, payload: object) -> None:
        if self._is_closing:
            return
        completed = self._ocr_task_completed
        if completed is not None:
            completed(success, payload)

    def _handle_ocr_task_thread_finished(self) -> None:
        if self.sender() is self._ocr_task_thread:
            self._ocr_task_thread = None
            self._ocr_task_label = None
            self._ocr_task_completed = None
            self._refresh_shiny_calibration_button_state()
            self._restore_interrupted_ocr_state_if_idle()

    def _start_ocr_region_recognition(self, field: str, configured_region: OcrRegion | None) -> None:
        if self._is_closing:
            return
        dialog = self._ocr_settings_dialog
        label = OCR_REGION_LABELS.get(field, field)
        try:
            capture_config = self._config_from_form().capture
            regions = self._ocr_region_config()
        except Exception as exc:
            if dialog is not None:
                dialog.fail_recognition(field, str(exc))
            self._write_run_log("OCR", f"{label}识别失败: {exc}", level="ERROR")
            return

        def task(should_stop: Callable[[], bool]) -> object:
            if should_stop():
                raise InterruptedError("OCR 识别已取消")
            frame = self._capture_preview_frame_for_config(capture_config)
            if should_stop():
                raise InterruptedError("OCR 识别已取消")
            image_shape = tuple(getattr(frame, "shape"))
            effective_region = regions.resolve(field, image_shape)
            if effective_region is None:
                raise RuntimeError(f"{label} ROI 在当前画面中无效")
            used_fallback = regions.has_invalid_custom(field)
            if field in DYNAMIC_DEFAULT_REGION_FIELDS and configured_region is not None:
                image_height, image_width = image_shape[:2]
                used_fallback = used_fallback or not configured_region.clip(image_width, image_height).is_valid()
            text = recognize_ocr_field(frame, field, effective_region)
            if should_stop():
                raise InterruptedError("OCR 识别已取消")
            return frame, image_shape, effective_region, used_fallback, text

        def completed(success: bool, payload: object) -> None:
            if self._is_closing:
                return
            if not success:
                message = str(payload)
                self._write_run_log("OCR", f"{label}识别失败: {message}", level="ERROR")
                if dialog is not None:
                    dialog.fail_recognition(field, message)
                return
            frame, image_shape, effective_region, used_fallback, text = payload  # type: ignore[misc]
            if used_fallback:
                fallback_label = (
                    "画面下方 50%"
                    if field == SHINY_DIALOG_REGION_FIELD
                    else "当前画面的御三家战斗按钮默认范围"
                )
                self._write_run_log(
                    "OCR",
                    f"{label}自定义 ROI 在当前画面中无效，已回退到{fallback_label}",
                    level="WARNING",
                )
            self._write_run_log(
                "OCR",
                f"{label}有效 ROI: X={effective_region.x}, Y={effective_region.y}, "
                f"W={effective_region.width}, H={effective_region.height}",
            )
            self._write_run_log("OCR", f"{label}识别结果: {text or '空'}")
            frame_copy = getattr(frame, "copy", None)
            self._latest_preview_frame = frame_copy() if callable(frame_copy) else frame
            if dialog is not None:
                dialog.set_preview_frame_shape(image_shape)
                dialog.finish_recognition(field, text)

        if not self._start_managed_ocr_task(f"{label}识别", task, completed):
            if dialog is not None:
                dialog.fail_recognition(field, "已有 OCR 任务正在运行")

    def _recognize_tid_ocr_region(self, region: OcrRegion) -> str:
        try:
            frame = self._current_preview_frame_for_ocr()
            text = recognize_ocr_field(frame, "tid", region)
        except Exception as exc:
            self._write_run_log("OCR", f"TID识别失败: {exc}", level="ERROR")
            raise
        self._write_run_log("OCR", f"TID识别结果: {text or '空'}")
        return text

    def _ocr_region_config(self):
        if self._ocr_settings_dialog is not None:
            return self._ocr_settings_dialog.region_config
        return load_ocr_region_config()

    def _start_ocr_warmup(self) -> None:
        if self._is_closing or self._ocr_warmup_running:
            return
        if self._shiny_calibration_worker is not None:
            if self._ocr_settings_dialog is not None:
                self._ocr_settings_dialog.finish_warmup(False, "闪光判定校准运行中")
            return
        if self._ocr_task_thread is not None and self._ocr_task_thread.isRunning():
            return
        self._ocr_shutdown_requested = False
        self._ocr_warmup_running = True
        self._ocr_warmup_result = None
        if self._ocr_settings_dialog is not None:
            self._ocr_settings_dialog.show_warmup_running()
        thread = OcrWarmupThread(warm_up_pokemon_info_ocr, self)
        thread.completed.connect(self._handle_ocr_warmup_completed)
        thread.finished.connect(self._handle_ocr_warmup_thread_finished)
        thread.finished.connect(thread.deleteLater)
        self._ocr_warmup_thread = thread
        thread.start()
        self._refresh_shiny_calibration_button_state()

    def _handle_ocr_warmup_completed(self, success: bool, message: str) -> None:
        if self._is_closing:
            return
        self._ocr_warmup_running = False
        self._ocr_warmup_result = (success, message)
        self._write_run_log("OCR", message, level="INFO" if success else "ERROR")
        self.ocrWarmupFinished.emit(success, message)
        pending = self._ocr_after_warmup
        self._ocr_after_warmup = None
        if pending is None:
            return
        kind, action = pending
        if success:
            QTimer.singleShot(0, action)
            return
        if kind.startswith("recognize:"):
            field = kind.split(":", 1)[1]
            if self._ocr_settings_dialog is not None:
                self._ocr_settings_dialog.fail_recognition(field, message)
        elif kind == "full_test":
            self._ocr_full_test_running = False
            self.ocrFullTestFinished.emit(False, message)
        elif kind == "auto_rng":
            self.auto_rng_tab.set_phase_text("OCR 初始化失败")
            self.auto_rng_tab.add_log(f"自动流程未启动：{message}", level="ERROR")
            self.statusBar().showMessage(message)
            QMessageBox.critical(self, "OCR 初始化失败", f"自动流程未启动。\n{message}")

    def _handle_ocr_warmup_thread_finished(self) -> None:
        if self.sender() is self._ocr_warmup_thread:
            self._ocr_warmup_thread = None
            self._ocr_warmup_running = False
            self._refresh_shiny_calibration_button_state()
            self._restore_interrupted_ocr_state_if_idle()

    def _set_ocr_test_result_on_ui(self, field: str, text: str) -> None:
        dialog = self._ocr_settings_dialog
        if dialog is not None:
            dialog.set_recognition_result(field, text)

    def _start_ocr_full_test(self) -> None:
        if self._ocr_full_test_running or self.auto_rng_tab._runner_thread is not None:
            return
        if self._shiny_calibration_worker is not None:
            message = "测试全部失败: 闪光判定校准运行中"
            self.ocrFullTestFinished.emit(False, message)
            return
        self._ocr_full_test_running = True
        self._refresh_shiny_calibration_button_state()
        if self._ocr_warmup_result is None or not self._ocr_warmup_result[0]:
            self._ocr_after_warmup = ("full_test", self._start_ocr_full_test_after_warmup)
            self._start_ocr_warmup()
            return
        self._start_ocr_full_test_after_warmup()

    def _start_ocr_full_test_after_warmup(self) -> None:
        if self._is_closing:
            self._ocr_full_test_running = False
            return
        self._ocr_full_test_running = True
        self._write_run_log("OCR", "测试全部开始")

        try:
            regions = self._ocr_region_config()
            capture_config = self._config_from_form().capture
        except Exception as exc:
            message = f"测试全部失败: {exc}"
            self._ocr_full_test_running = False
            self._write_run_log("OCR", message, level="ERROR")
            self.ocrFullTestFinished.emit(False, message)
            return

        def task(should_stop: Callable[[], bool]) -> object:
            def check_cancelled() -> None:
                if should_stop():
                    raise InterruptedError("OCR 测试已取消")

            def wait_interruptibly(seconds: float) -> None:
                remaining = max(0.0, seconds)
                while remaining > 0:
                    check_cancelled()
                    delay = min(0.05, remaining)
                    time.sleep(delay)
                    remaining -= delay
                check_cancelled()

            check_cancelled()
            notes_frame = self._capture_preview_frame_for_config(capture_config)
            check_cancelled()
            for field in NOTE_REGION_FIELDS:
                check_cancelled()
                region = regions.get(field)
                if region is None:
                    continue
                text = recognize_ocr_field(notes_frame, field, region)
                check_cancelled()
                label = OCR_REGION_LABELS.get(field, field)
                self._write_run_log("OCR", f"测试全部/{label}识别结果: {text or '空'}")
                self._call_on_ui_thread(lambda field=field, text=text: self._set_ocr_test_result_on_ui(field, text))

            check_cancelled()
            self._send_easycon_right()
            wait_interruptibly(2.0)

            stats_frame = self._capture_preview_frame_for_config(capture_config)
            check_cancelled()
            for field in STAT_REGION_FIELDS:
                check_cancelled()
                region = regions.get(field)
                if region is None:
                    continue
                text = recognize_ocr_field(stats_frame, field, region)
                check_cancelled()
                label = OCR_REGION_LABELS.get(field, field)
                self._write_run_log("OCR", f"测试全部/{label}识别结果: {text or '空'}")
                self._call_on_ui_thread(lambda field=field, text=text: self._set_ocr_test_result_on_ui(field, text))
            return "测试全部完成"

        def completed(success: bool, payload: object) -> None:
            if self._is_closing:
                return
            self._ocr_full_test_running = False
            if success:
                message = str(payload)
                self._write_run_log("OCR", message)
            else:
                message = f"测试全部失败: {payload}"
                self._write_run_log("OCR", message, level="ERROR")
            self.ocrFullTestFinished.emit(success, message)

        if not self._start_managed_ocr_task("测试全部", task, completed):
            self._ocr_full_test_running = False
            message = "测试全部失败: 已有 OCR 任务正在运行"
            self._write_run_log("OCR", message, level="ERROR")
            self.ocrFullTestFinished.emit(False, message)

    def _add_easycon_image_result_observer(
        self,
        observer: Callable[[object], None],
        *,
        source_generation: int,
        run_generation: int,
    ) -> int:
        with self._easycon_image_result_observers_lock:
            self._easycon_image_result_observer_serial += 1
            token = self._easycon_image_result_observer_serial
            self._easycon_image_result_observers[token] = (
                int(source_generation),
                int(run_generation),
                observer,
            )
            return token

    def _remove_easycon_image_result_observer(self, token: int) -> None:
        with self._easycon_image_result_observers_lock:
            self._easycon_image_result_observers.pop(int(token), None)

    def _notify_easycon_image_result_observers(
        self,
        source_generation: int,
        run_generation: int,
        result: object,
    ) -> None:
        if not self._video_source_connected:
            return
        if source_generation != self._video_source_generation:
            return
        if run_generation != self._easycon_run_generation:
            return
        with self._easycon_image_result_observers_lock:
            observers = tuple(self._easycon_image_result_observers.values())
        for observer_source, observer_run, observer in observers:
            if observer_source != source_generation or observer_run != run_generation:
                continue
            try:
                observer(result)
            except Exception:
                continue

    def _handle_easycon_image_search_result(
        self,
        source_generation: int,
        run_generation: int,
        result: object,
    ) -> None:
        if not self._video_source_connected:
            return
        if source_generation != self._video_source_generation:
            return
        if run_generation != self._easycon_run_generation:
            return
        self._latest_easycon_image_search_result = result

    def _update_preview_frame(self) -> None:
        config_error: Exception | None = None
        try:
            config = self._config_from_form().capture
        except Exception as exc:
            config_error = exc if isinstance(exc, Exception) else Exception(str(exc))
            if not self._preview_annotation_error_reported:
                self._preview_annotation_error_reported = True
                self._write_run_log(
                    "视频源",
                    f"预览识别配置无效，继续保持视频源: {exc}",
                    level="WARNING",
                )
            self.statusBar().showMessage(f"预览识别配置无效，显示原始画面: {exc}")
            if self._preview_capture is not None:
                config = self._preview_capture.config
            elif self._video_source_connected:
                config = self._shared_capture_config(
                    BlinkCaptureConfig(
                        eye_image_path=Path(),
                        roi=(0, 0, 0, 0),
                    )
                )
            else:
                return

        try:
            frame = self._read_live_preview_frame(config)
        except Exception as exc:
            shared_source_failed = self._video_source_connected
            error = exc if isinstance(exc, BaseException) else Exception(str(exc))
            error_detail = _exception_chain_text(error)
            diagnostic = self._video_source_diagnostic_snapshot()
            try:
                self._write_run_log(
                    "视频源",
                    f"预览读帧失败，准备停止视频源；异常链={error_detail}；{diagnostic}",
                    level="ERROR",
                )
            except Exception:
                pass
            self._preview_timer.stop()
            self._release_preview_capture()
            stopped = True
            if shared_source_failed:
                stopped = self.disconnect_video_source(
                    force=True,
                    reason=f"视频源读帧失败（{error_detail}）",
                )
            else:
                self.preview_button.setText(self._text("preview_button"))
            self._show_error(
                "Preview failed",
                error,
                source="视频源",
                write_log=not shared_source_failed,
            )
            if shared_source_failed:
                self._set_video_source_status(
                    "视频源故障" if stopped else "停止失败，请重试",
                    "failed",
                )
            return

        frame_copy = getattr(frame, "copy", None)
        self._latest_preview_frame = frame_copy() if callable(frame_copy) else frame
        preview = None
        if config_error is not None:
            annotated_copy = getattr(self._latest_preview_frame, "copy", None)
            annotated = (
                annotated_copy()
                if callable(annotated_copy)
                else self._latest_preview_frame
            )
        else:
            try:
                annotated, preview = render_eye_preview(config, frame)
                annotated = _draw_easycon_search_overlay(
                    annotated,
                    self._latest_easycon_image_search_result,
                )
            except Exception as exc:
                annotated_copy = getattr(self._latest_preview_frame, "copy", None)
                annotated = (
                    annotated_copy()
                    if callable(annotated_copy)
                    else self._latest_preview_frame
                )
                if not self._preview_annotation_error_reported:
                    self._preview_annotation_error_reported = True
                    self._write_run_log(
                        "视频源",
                        f"预览识别框绘制失败，已回退到原始画面: {exc}",
                        level="WARNING",
                    )
        annotated_copy = getattr(annotated, "copy", None)
        self._latest_annotated_preview_frame = annotated_copy() if callable(annotated_copy) else annotated
        self._refresh_preview_presentation()
        if self._picture_in_picture is not None and self._picture_in_picture.isVisible():
            self._sync_picture_in_picture_frame()
        resolution = ""
        if self._preview_capture is not None and self._preview_capture.keep_open_for_preview:
            frame_height, frame_width = frame.shape[:2]
            resolution = f" | OBS {frame_width}x{frame_height}"
            if frame_width < 1280 or frame_height < 720:
                resolution += "（放大投影窗口可提高清晰度）"
        score = "" if preview is None else f" | score {preview.match_score:.3f}"
        if config_error is not None:
            annotation_status = " | 识别配置无效，显示原始画面"
        else:
            annotation_status = " | 识别框不可用" if preview is None else ""
        self.statusBar().showMessage(
            f"{self._text('preview_running')}{resolution}{score}{annotation_status}"
        )

    def _display_frame(self, frame: object) -> None:
        if self._selection_preview_frame is not None:
            frame = self._selection_preview_frame
        pixmap = self._frame_to_pixmap(frame)
        target = self.preview_label.contentsRect().size()
        if target.width() <= 0 or target.height() <= 0:
            return
        scaled, logical_size = _scale_preview_pixmap(
            pixmap,
            target,
            self.preview_label.devicePixelRatioF(),
        )
        contents = self.preview_label.contentsRect()
        left = contents.left() + (contents.width() - logical_size.width()) // 2
        top = contents.top() + (contents.height() - logical_size.height()) // 2
        self.preview_label.set_image_geometry(
            pixmap.width(),
            pixmap.height(),
            QRect(left, top, logical_size.width(), logical_size.height()),
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

            frame = self._capture_preview_frame_for_config(self._config_from_form().capture)
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
        order = (0, 1, 2, 5, 3, 4)
        char_order = (0, 1, 2, 3, 4, 5, 0, 1, 2, 3, 4)
        ec_index = state.ec % 6
        char_index = ec_index
        max_iv = 0
        for offset in range(6):
            index = char_order[ec_index + offset]
            if state.ivs[order[index]] > max_iv:
                char_index = index
                max_iv = state.ivs[order[index]]
        stat_index = order[char_index]
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

    def calibrate_shiny_threshold(self) -> None:
        if self._shiny_calibration_worker is not None:
            self._stop_shiny_threshold_calibration()
            return
        if self._ocr_activity_running() or self._ocr_after_warmup is not None:
            QMessageBox.warning(
                self,
                "OCR 正在使用",
                "请等待当前 OCR 预热、识别或测试完成后再校准闪光判定。",
            )
            return

        tracking_config = self._config_from_form()
        regions = self._ocr_region_config()
        try:
            target_species = int(self.auto_rng_tab.targets()[0][0].template.species)
        except (AttributeError, IndexError, TypeError, ValueError):
            target_species = None
        is_starter = target_species in STARTER_SPECIES
        logged_fields: set[str] = set()

        def capture_region(field: str) -> object:
            frame = self._capture_preview_frame_for_config(tracking_config.capture)
            image_shape = tuple(getattr(frame, "shape"))
            region = regions.resolve(field, image_shape)
            label = OCR_REGION_LABELS[field]
            if region is None:
                raise RuntimeError(f"{label} ROI 在当前画面中无效")
            if field not in logged_fields:
                logged_fields.add(field)
                configured_region = regions.get(field)
                image_height, image_width = image_shape[:2]
                if regions.has_invalid_custom(field) or (
                    configured_region is not None
                    and not configured_region.clip(image_width, image_height).is_valid()
                ):
                    fallback_label = (
                        "画面下方 50%"
                        if field == SHINY_DIALOG_REGION_FIELD
                        else "当前画面的御三家战斗按钮默认范围"
                    )
                    self._write_run_log(
                        "OCR",
                        f"闪光判定校准的{label}自定义 ROI 无效，已回退到{fallback_label}",
                        level="WARNING",
                    )
                self._write_run_log(
                    "OCR",
                    f"闪光判定校准{label}有效 ROI: "
                    f"X={region.x}, Y={region.y}, W={region.width}, H={region.height}",
                )
                dialog = self._ocr_settings_dialog
                if dialog is not None:
                    self._call_on_ui_thread(lambda: dialog.set_preview_frame_shape(image_shape))
            x, y, width, height = region.as_tuple()
            return frame[y : y + height, x : x + width]

        def capture_dialog_region() -> object:
            return capture_region(SHINY_DIALOG_REGION_FIELD)

        def capture_starter_battle_region() -> object:
            return capture_region(STARTER_BATTLE_REGION_FIELD)

        first_keyword: str | tuple[str, ...] = ("去吧", "上吧") if is_starter else "出现了！"
        second_keyword: str | tuple[str, ...] = ("战斗", "戰鬥") if is_starter else ("去吧", "上吧")
        worker = ShinyThresholdCalibrationWorker(
            capture_dialog_region,
            first_keyword=first_keyword,
            second_keyword=second_keyword,
            second_capture_frame=capture_starter_battle_region if is_starter else None,
        )
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._shiny_threshold_calibration_finished)
        worker.failed.connect(self._shiny_threshold_calibration_failed)
        worker.cancelled.connect(self._shiny_threshold_calibration_cancelled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._shiny_calibration_worker = worker
        self._shiny_calibration_thread = thread
        if self._ocr_settings_dialog is not None:
            self._ocr_settings_dialog.set_automation_active(True)
        self.calibrate_shiny_threshold_button.setText("停止校准")
        event_text = "去吧/上吧 -> 战斗按钮出现" if is_starter else "出现了！ -> 去吧/上吧"
        self.auto_rng_tab.captureLog.emit(f"[闪光判定校准] 开始监控 {event_text}")
        self.statusBar().showMessage(f"正在后台监控 {event_text}...")
        thread.start()

    def _stop_shiny_threshold_calibration(self) -> None:
        if self._shiny_calibration_worker is None:
            return
        self.calibrate_shiny_threshold_button.setEnabled(False)
        self.auto_rng_tab.captureLog.emit("[闪光判定校准] 正在停止...")
        self.statusBar().showMessage("正在停止闪光判定校准...")
        self._shiny_calibration_worker.stop()

    def _reset_shiny_threshold_calibration(self) -> None:
        self._shiny_calibration_worker = None
        self._shiny_calibration_thread = None
        auto_rng_active = self.auto_rng_tab._runner_thread is not None
        if self._ocr_settings_dialog is not None:
            self._ocr_settings_dialog.set_automation_active(auto_rng_active)
        self._refresh_shiny_calibration_button_state()
        self.calibrate_shiny_threshold_button.setText("校准闪光判定")

    def _shiny_threshold_calibration_finished(self, interval_seconds: float) -> None:
        self._reset_shiny_threshold_calibration()
        suggested = suggested_shiny_threshold(interval_seconds)
        self.auto_rng_tab.captureLog.emit(
            f"[闪光判定校准] 当前间隔 {interval_seconds:.3f}s，建议阈值 {suggested:.3f}s"
        )
        self._show_shiny_threshold_dialog(interval_seconds)

    def _shiny_threshold_calibration_failed(self, message: str) -> None:
        self._reset_shiny_threshold_calibration()
        log_message = f"[闪光判定校准] 失败: {message}"
        self.auto_rng_tab.add_log(log_message, level="ERROR")
        QMessageBox.critical(self, "闪光判定校准失败", message)
        self.statusBar().showMessage(message)

    def _shiny_threshold_calibration_cancelled(self) -> None:
        self._reset_shiny_threshold_calibration()
        self.auto_rng_tab.captureLog.emit("[闪光判定校准] 已停止")
        self.statusBar().showMessage("闪光判定校准已停止")

    def _show_shiny_threshold_dialog(self, interval_seconds: float) -> None:
        suggested = suggested_shiny_threshold(interval_seconds)
        dialog = QDialog(self)
        dialog.setWindowTitle("闪光判定校准")
        layout = QVBoxLayout(dialog)
        message = QLabel(f"当前间隔 {interval_seconds:.3f}s，是否以 {suggested:.3f}s 作为闪光判定？")
        message.setWordWrap(True)
        threshold = QDoubleSpinBox()
        threshold.setRange(0.0, 999.0)
        threshold.setDecimals(3)
        threshold.setSingleStep(0.1)
        set_c_locale(threshold)
        threshold.setValue(suggested)
        form = QFormLayout()
        form.addRow("闪光判定阈值(秒)", threshold)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(message)
        layout.addLayout(form)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self.auto_rng_tab.captureLog.emit("[闪光判定校准] 已取消应用阈值")
            return
        self.auto_rng_tab.shiny_threshold_seconds.setValue(threshold.value())
        self.auto_rng_tab.captureLog.emit(f"[闪光判定校准] 阈值已设置为 {threshold.value():.3f}s")
        self.statusBar().showMessage(f"闪光判定阈值已设置为 {threshold.value():.3f}s")


    def _sync_seed64_from_state32(self) -> None:
        try:
            state = SeedState32.from_hex_words([box.text() for box in self.seed32_inputs])
        except ValueError as exc:
            self.statusBar().showMessage(str(exc))
            return
        seed64_pair = state.format_seed64_pair()
        for output, text in zip(self.seed64_outputs, seed64_pair):
            output.setText(text)
        if hasattr(self, "bdsp_seed64_inputs"):
            for output, text in zip(self.bdsp_seed64_inputs, seed64_pair):
                output.setText(text)
        if hasattr(self, "id_tab"):
            self.id_tab.set_seed_pair(state.to_seed_pair64())
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
            self.statusBar().showMessage(str(exc))
            return
        state = seed_pair.to_state32()
        for input_box, text in zip(self.seed32_inputs, state.format_words()):
            input_box.setText(text)
        for output, text in zip(self.seed64_outputs, seed_pair.format_seeds()):
            output.setText(text)
        if hasattr(self, "id_tab"):
            self.id_tab.set_seed_pair(seed_pair)
        self._auto_refresh_results()

    def _sync_state32_from_id_seed64(self, seed_pair: object) -> None:
        if not isinstance(seed_pair, SeedPair64):
            return
        if hasattr(self, "bdsp_seed64_inputs"):
            for input_box, text in zip(self.bdsp_seed64_inputs, seed_pair.format_seeds()):
                input_box.setText(text)
        state = seed_pair.to_state32()
        for input_box, text in zip(self.seed32_inputs, state.format_words()):
            input_box.setText(text)
        for output, text in zip(self.seed64_outputs, seed_pair.format_seeds()):
            output.setText(text)
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

    def _capture_mode_label(self) -> str:
        return {
            "seed": "Seed 捕捉",
            "reidentify": "校正捕捉",
            "tidsid": "TID/SID 捕捉",
        }.get(self._capture_mode, self._capture_mode)

    def _ensure_preview_for_auto_rng(self) -> bool:
        return self._ensure_preview_frame_before_capture()

    def _pause_auto_preview_for_capture(self) -> bool:
        preview_was_running = self._preview_timer.isActive()
        if self._video_source_connected:
            return False
        if preview_was_running:
            self._preview_timer.stop()
            self._release_preview_capture()
            self.preview_button.setText(self._text("preview_button"))
            self.preview_label.clear()
            self.preview_label.setText(self._text("no_preview"))
        return preview_was_running

    def _restore_auto_preview_after_capture(self, preview_was_running: bool) -> None:
        if (preview_was_running or self._video_source_connected) and not self._preview_timer.isActive():
            self._preview_timer.start()
            self.preview_button.setText(self._text("stop_preview"))

    def _recover_zoom_mode_with_preview_paused(
        self,
        capture_config: BlinkCaptureConfig,
        run_script_text: Callable[[str, str], object],
    ) -> bool:
        """Recover the Switch zoom overlay without sharing VideoCapture with preview."""
        preview_was_running = bool(self._call_on_ui_thread(self._pause_auto_preview_for_capture))
        if preview_was_running:
            time.sleep(0.3)  # 与测种捕获使用相同的摄像头释放等待
        try:
            return recover_zoom_overlay(
                lambda: self._capture_preview_frame_for_config(capture_config),
                run_script_text,
                should_stop=self._capture_cancel.is_set,
            )
        finally:
            self._call_on_ui_thread(lambda: self._restore_auto_preview_after_capture(preview_was_running))

    def _start_auto_rng(self, config: AutoRngConfig) -> None:
        if config.start_phase == AutoRngPhase.REIDENTIFY:
            try:
                self._current_auto_rng_seed_result()
            except ValueError as exc:
                QMessageBox.warning(self, "缺少 Seed", f"从校正开始需要先填入有效 Seed。\n{exc}")
                return
        if self._ocr_task_thread is not None and self._ocr_task_thread.isRunning():
            QMessageBox.warning(self, "OCR 正在使用", "请等待当前 OCR 识别任务完成后再启动自动流程。")
            return
        if self._ocr_full_test_running or self._shiny_calibration_worker is not None:
            QMessageBox.warning(self, "OCR 正在使用", "请等待当前 OCR 测试或闪光判定校准完成后再启动自动流程。")
            return
        if self._ocr_warmup_result is None or not self._ocr_warmup_result[0]:
            pending = self._ocr_after_warmup
            if pending is not None and pending[0].startswith("recognize:"):
                field = pending[0].split(":", 1)[1]
                if self._ocr_settings_dialog is not None:
                    self._ocr_settings_dialog.fail_recognition(field, "自动流程启动，已取消手动识别")
            self._ocr_after_warmup = ("auto_rng", lambda config=config: self._start_auto_rng_after_warmup(config))
            self.auto_rng_tab.set_phase_text("初始化 OCR")
            self.auto_rng_tab.add_log("正在初始化 OCR，完成后将自动启动流程")
            self.statusBar().showMessage("正在初始化 OCR，完成后将自动启动自动定点流程")
            self._start_ocr_warmup()
            return
        self._start_auto_rng_after_warmup(config)

    def _start_auto_rng_after_warmup(self, config: AutoRngConfig) -> None:
        if self._is_closing:
            return
        if not self._ensure_preview_for_auto_rng():
            return
        # 自动连接伊机控（如果尚未连接）
        if not self._ensure_bridge_connected():
            return
        config = replace(
            config,
            seed_config_path=self._selected_auto_seed_config_path(),
            reidentify_config_path=self._selected_auto_reidentify_config_path(),
        )
        services = self._build_auto_rng_services(config)

        def history_callback(event: str, args: tuple[object, ...]) -> None:
            self.autoHistoryEvent.emit(event, args)

        self.auto_rng_tab.run_with_runner(AutoRngRunner(config, services=services, history_callback=history_callback))

    def _start_auto_tid_rng(self, config: AutoTidRngConfig) -> None:
        if not self._ensure_preview_for_auto_rng():
            return
        if not self._ensure_bridge_connected():
            return
        services = self._build_auto_tid_rng_services(config)
        runner = AutoTidRngRunner(
            config,
            services=services,
            log_callback=lambda message: self.autoHistoryEvent.emit("auto_tid_log", (message,)),
        )
        self.auto_tid_rng_tab.run_with_runner(runner)

    def _build_auto_tid_rng_services(self, config: AutoTidRngConfig) -> AutoTidRngServices:
        seed_config_path = self._selected_auto_seed_config_path()
        tracking_config = load_project_xs_config(seed_config_path, blink_count=TIDSID_BLINK_COUNT)
        tracking_config = replace(
            tracking_config,
            capture=self._shared_capture_config(tracking_config.capture),
        )

        def seed_pair_from_result(seed_result: AutoTidSeedResult) -> SeedPair64:
            seed = seed_result.seed
            if isinstance(seed, SeedPair64):
                return seed
            if isinstance(seed, SeedState32):
                return seed.to_seed_pair64()
            to_seed_pair64 = getattr(seed, "to_seed_pair64", None)
            if callable(to_seed_pair64):
                return to_seed_pair64()
            raise TypeError("Auto TID RNG seed result must contain SeedPair64 or SeedState32")

        def sync_tid_seed_to_ui(seed_pair: SeedPair64) -> None:
            state = seed_pair.to_state32()
            for box, text in zip(self.seed32_inputs, state.format_words()):
                box.setText(text)
            self._sync_seed64_from_state32()
            if hasattr(self, "id_tab"):
                self.id_tab.set_seed_pair(seed_pair)
            if hasattr(self, "auto_tid_rng_tab"):
                self.auto_tid_rng_tab.set_tid_seed(seed_pair)

        def capture_tidsid_seed_service() -> AutoTidSeedResult:
            self._capture_cancel.clear()

            def store_frame(frame: object) -> None:
                self.autoCaptureFrameChanged.emit(frame)

            def store_progress(done: int, total: int) -> None:
                self.autoCaptureProgressChanged.emit(done, total)

            store_progress = self._wrap_capture_progress_with_keep_awake(store_progress)

            preview_was_running = bool(self._call_on_ui_thread(self._pause_auto_preview_for_capture))
            if preview_was_running:
                time.sleep(0.3)
            try:
                observation = capture_pokemon_blinks(
                    tracking_config.capture,
                    should_stop=self._capture_cancel.is_set,
                    frame_callback=store_frame,
                    progress_callback=store_progress,
                    show_window=False,
                    discard_first_blink_within_seconds=AUTO_CAPTURE_WARMUP_DISCARD_SECONDS,
                )
                result = recover_tidsid_seed_from_observation(observation)
            finally:
                self._call_on_ui_thread(lambda: self._restore_auto_preview_after_capture(preview_was_running))
            seed_pair = result.state.to_seed_pair64()
            self._call_on_ui_thread(lambda: sync_tid_seed_to_ui(seed_pair))
            return AutoTidSeedResult(
                seed=seed_pair,
                current_advances=0,
                npc=max(0, int(tracking_config.pokemon_npc)),
                seed_text=" ".join(seed_pair.format_seeds()),
                measured_at=time.monotonic(),
            )

        def search_id_states_service(seed_result: AutoTidSeedResult, threshold: int, target_display_tids: Sequence[int]):
            return generate_ids(
                seed_pair_from_result(seed_result),
                initial_advances=0,
                max_advances=max(0, int(threshold)) + 1,
                state_filter=IDFilter(),
            )

        def lookup_tid_state_service(seed_result: AutoTidSeedResult, tid: int, center_advances: int, window: int):
            from auto_bdsp_rng.automation.auto_tid_rng import reverse_lookup_span

            start, _end, count = reverse_lookup_span(center_advances, window)
            states = generate_ids(
                seed_pair_from_result(seed_result),
                initial_advances=start,
                max_advances=count,
                state_filter=IDFilter(tid=[int(tid)]),
            )
            if not states:
                return None
            return min(states, key=lambda state: (abs(int(state.advances) - int(center_advances)), int(state.advances)))

        def run_script_text_service(script_text: str, name: str) -> object:
            script_name, script_dir = self._native_script_context(config, script_text, name)
            native_backend = self._call_on_ui_thread(
                lambda: self._prepare_auto_easycon_script(script_name)
            )
            try:
                result = native_backend.run_script_text(
                    script_text,
                    script_name,
                    script_dir=script_dir,
                )
            except BaseException as exc:
                self._fail_auto_script(exc)
                raise
            return self._finalize_auto_script_result(result, script_name)

        def recognize_tid_service() -> str:
            if config.ocr_region is None:
                raise RuntimeError("未设置 TID OCR ROI")
            frame = self._capture_preview_frame_for_config(tracking_config.capture)
            text = recognize_ocr_field(frame, "tid", config.ocr_region)
            self.auto_tid_rng_tab.add_log(f"[自动TID] OCR 原始结果：{text or '空'}")
            return text

        def stop_current_script_service() -> None:
            self._capture_cancel.set()
            try:
                native_backend = self._call_on_ui_thread(self.easycon_tab._ensure_native_backend)
            except Exception:
                return
            try:
                native_backend.stop_current_script()
            except Exception:
                pass

        def recover_zoom_mode_service() -> bool:
            return self._recover_zoom_mode_with_preview_paused(
                tracking_config.capture,
                run_script_text_service,
            )

        return AutoTidRngServices(
            capture_seed=capture_tidsid_seed_service,
            search_id_states=search_id_states_service,
            lookup_tid_state=lookup_tid_state_service,
            run_script_text=run_script_text_service,
            recognize_tid=recognize_tid_service,
            recover_zoom_mode=recover_zoom_mode_service,
            stop_current_script=stop_current_script_service,
        )

    def _handle_auto_history_event(self, event: str, args: object) -> None:
        values = tuple(args) if isinstance(args, tuple) else tuple()
        h = self.history_tab
        if event == "cycle_start" and len(values) >= 1:
            h.cycle_start(int(values[0]))
            self._write_run_log("历史记录", f"自动定点第 {int(values[0])} 轮开始")
        elif event == "seed_captured" and len(values) >= 4:
            h.seed_captured(str(values[0]), int(values[1]), int(values[2]), int(values[3]))
            self._write_run_log(
                "历史记录",
                f"捕获 Seed {values[0]}；初始 Adv {int(values[1])}；NPC {int(values[2])}；最大搜索 {int(values[3])}",
            )
        elif event == "auto_tid_log" and len(values) >= 1:
            h.auto_tid_log(str(values[0]))
        elif event == "candidates_found" and len(values) >= 2:
            flags = list(values[2]) if len(values) >= 3 else None
            candidates = list(values[0])
            locked_index = int(values[1])
            h.candidates_found(candidates, locked_index, flags)  # type: ignore[arg-type]
            locked_adv = (
                getattr(candidates[locked_index], "advances", "-")
                if 0 <= locked_index < len(candidates)
                else "-"
            )
            self._write_run_log("历史记录", f"搜索到 {len(candidates)} 个候选；锁定 Adv {locked_adv}")
        elif event == "candidates_refiltered" and len(values) >= 2:
            flags = list(values[2]) if len(values) >= 3 else None
            candidates = list(values[0])
            locked_index = int(values[1])
            h.candidates_refiltered(candidates, locked_index, flags)  # type: ignore[arg-type]
            locked_adv = (
                getattr(candidates[locked_index], "advances", "-")
                if 0 <= locked_index < len(candidates)
                else "-"
            )
            self._write_run_log("历史记录", f"重新筛选后剩余 {len(candidates)} 个候选；锁定 Adv {locked_adv}")
        elif event == "target_missed" and len(values) >= 2:
            target_adv = int(values[0]) if values[0] is not None else 0
            current_adv = int(values[1]) if values[1] is not None else 0
            h.target_missed(target_adv, current_adv)
            self._write_run_log(
                "历史记录",
                f"错过目标；目标 Adv {target_adv}；当前 Adv {current_adv}",
                level="WARNING",
            )
        elif event == "cycle_no_candidate":
            h.cycle_no_candidate()
            self._write_run_log("历史记录", "本轮结果：无候选")
        elif event == "cycle_result" and len(values) >= 3:
            is_shiny = bool(values[0])
            interval = float(values[1]) if values[1] is not None else None
            trigger = int(values[2]) if values[2] is not None else None
            used_delay = int(values[3]) if len(values) >= 4 and values[3] is not None else None
            h.cycle_result(is_shiny, interval, trigger, used_delay)
            interval_text = interval if interval is not None else "-"
            trigger_text = trigger if trigger is not None else "-"
            delay_text = used_delay if used_delay is not None else "-"
            self._write_run_log(
                "历史记录",
                f"本轮结果：{'出闪' if is_shiny else '未出闪'}；间隔 {interval_text}；"
                f"启动 Adv {trigger_text}；delay {delay_text}",
            )
        elif event == "attempt_result" and len(values) >= 6:
            loop_index = int(values[0])
            attempt_index = int(values[1])
            interval = float(values[3]) if values[3] is not None else None
            trigger = int(values[4]) if values[4] is not None else None
            used_delay = int(values[5]) if values[5] is not None else None
            h.attempt_result(loop_index, attempt_index, interval, trigger, used_delay)
            self._write_run_log(
                "历史记录",
                f"第 {loop_index} 轮 / 第 {attempt_index} 次撞闪未出闪；"
                f"间隔 {interval if interval is not None else '-'}；"
                f"启动 Adv {trigger if trigger is not None else '-'}；"
                f"delay {used_delay if used_delay is not None else '-'}",
            )
        elif event == "reverse_lookup_results" and len(values) >= 1:
            chara = str(values[1]) if len(values) >= 2 and values[1] is not None else None
            delays = list(values[2]) if len(values) >= 3 and values[2] is not None else None
            ocr = dict(values[3]) if len(values) >= 4 and values[3] is not None else None
            results = list(values[0])
            h.reverse_lookup_results(results, chara, delays, ocr)  # type: ignore[arg-type]
            self._write_run_log("历史记录", f"反查完成；候选 {len(results)} 个；个性 {chara or '-'}")

    def _ensure_bridge_connected(self) -> bool:
        """Compatibility name for ensuring the native persistent backend is ready."""

        if not self._video_source_connected:
            self._show_error("视频源未连接", "请先在 Seed 捕捉页面连接视频源")
            return False
        status = self.easycon_tab._native_status()
        if status == EasyConStatus.BRIDGE_CONNECTED:
            return True
        if status == EasyConStatus.RUNNING:
            self._show_error("伊机控正在运行", "已有伊机控脚本正在运行，请先停止当前脚本")
            return False
        port = self.easycon_tab.port_combo.currentText()
        if not port:
            port = self.easycon_tab.config.last_port or ""
            if port and self.easycon_tab.port_combo.findText(port) >= 0:
                self.easycon_tab.port_combo.setCurrentText(port)
        if not port:
            self._show_error("自动连接失败", "请先在伊机控面板选择串口并连接单片机")
            return False
        if not self.easycon_tab.connect_native():
            self._show_error("自动连接失败", "无法连接到单片机，请检查串口和连接")
            return False
        return True

    def _handle_auto_capture_frame(self, frame: object) -> None:
        if self._video_source_connected:
            self._refresh_preview_presentation()
        else:
            self._display_frame(frame)

    def _handle_auto_capture_progress(self, done: int, total: int) -> None:
        self.progress_value.setText(f"{done}/{total}")

    def _wrap_capture_progress_with_keep_awake(
        self,
        progress_callback: Callable[[int, int], None],
    ) -> Callable[[int, int], None]:
        triggered_milestones: set[int] = set()

        def wrapped(done: int, total: int) -> None:
            progress_callback(done, total)
            if total not in CAPTURE_KEEP_AWAKE_BLINK_COUNTS or done <= 0 or done >= total:
                return
            milestone = (done // CAPTURE_KEEP_AWAKE_INTERVAL) * CAPTURE_KEEP_AWAKE_INTERVAL
            if milestone < CAPTURE_KEEP_AWAKE_INTERVAL or milestone in triggered_milestones:
                return
            triggered_milestones.add(milestone)
            self.captureKeepAwakeRequested.emit(milestone, total)

        return wrapped

    def _handle_capture_keep_awake_requested(self, done: int, total: int) -> None:
        try:
            self.easycon_tab.request_capture_keep_awake(
                done,
                total,
                duration_ms=CAPTURE_KEEP_AWAKE_PRESS_MS,
            )
        except Exception as exc:
            self.easycon_tab._append_log(
                "warn",
                f"捕捉亮屏保活发送 {CAPTURE_KEEP_AWAKE_BUTTON} 失败，继续捕捉: {exc}",
            )

    def _handle_auto_seed_captured(self, seed_result: AutoRngSeedResult) -> None:
        state = self._state32_from_auto_seed_result(seed_result)
        incoming_words = state.format_words()
        # 检测 seed 是否变化：未变化说明是 reidentify，不应覆盖数据区
        seed_changed = any(
            box.text() != word
            for box, word in zip(self.seed32_inputs, incoming_words)
        )
        if seed_changed:
            for box, text in zip(self.seed32_inputs, incoming_words):
                box.setText(text)
            self._sync_seed64_from_state32()
            self._sync_bdsp_data_from_auto_rng(state.to_seed_pair64())
        self._start_auto_advance_tracking(seed_result)

    def _state32_from_auto_seed_result(self, seed_result: AutoRngSeedResult) -> SeedState32:
        seed = seed_result.seed
        if isinstance(seed, SeedState32):
            return seed
        if isinstance(seed, SeedPair64):
            return seed.to_state32()
        to_state32 = getattr(seed, "to_state32", None)
        if callable(to_state32):
            return to_state32()
        raise TypeError("Auto RNG seed result must contain SeedPair64 or SeedState32")

    def _current_auto_rng_seed_result(self) -> AutoRngSeedResult:
        state = SeedState32.from_hex_words([box.text() for box in self.seed32_inputs])
        return AutoRngSeedResult(
            seed=state,
            current_advances=0,
            npc=max(0, int(self.npc_count.text() or 0)),
            seed_text=" ".join(state.format_seed64_pair()),
            measured_at=time.monotonic(),
        )

    def _start_auto_advance_tracking(self, seed_result: AutoRngSeedResult) -> None:
        measured_at = seed_result.measured_at
        self._advance_step = seed_result.npc + 1
        self._advance_counter = ProjectXsAdvanceCounter()
        self._advance_counter.reset(
            current_advances=seed_result.current_advances,
            npc=seed_result.npc,
            now=measured_at if measured_at is not None else time.monotonic(),
        )
        self._refresh_tracked_advances_from_clock()
        self.timer_value.setText("0")
        self._advance_timer.stop()

    def _sync_bdsp_data_from_auto_rng(self, seed: SeedPair64) -> None:
        self.auto_rng_tab.set_target_version(self._profile_version)
        record, state_filter, shiny_mode = self.auto_rng_tab.targets()[0]
        self._apply_auto_target_to_bdsp_controls(record, state_filter, shiny_mode)
        self._active_record = record
        try:
            states = generate_static_candidates(
                StaticSearchCriteria(
                    seed=seed,
                    profile=self._current_profile(),
                    record=record,
                    state_filter=state_filter,
                    initial_advances=0,
                    max_advances=self.auto_rng_tab.max_advances.value(),
                    offset=0,
                    lead=Lead.NONE,
                    shiny_mode=shiny_mode,
                )
            )
        except Exception as exc:
            self._show_error("Generation failed", exc)
            return
        self._states = states
        self._populate_table(states)
        self.statusBar().showMessage(f"{len(states)} {self._text('results')}")

    def _apply_auto_target_to_bdsp_controls(self, record: StaticEncounterRecord, state_filter: StateFilter, shiny_mode: str) -> None:
        category_index = self.category_combo.findData(record.category.value)
        if category_index >= 0 and self.category_combo.currentIndex() != category_index:
            self.category_combo.setCurrentIndex(category_index)
        for index in range(self.encounter_combo.count()):
            if getattr(self.encounter_combo.itemData(index), "description", None) == record.description:
                self.encounter_combo.setCurrentIndex(index)
                break
        self.level_display.setText(str(record.template.level))
        self.iv_count_display.setText(str(record.template.iv_count))
        ability_text = {0: "0", 1: "1", 2: "隐藏", 255: "0/1"}.get(record.template.ability, "任意")
        ability_index = self.template_ability_display.findText(ability_text)
        self.template_ability_display.setCurrentIndex(max(0, ability_index))
        self.template_shiny_display.setCurrentText("锁闪" if record.template.shiny == Shiny.NEVER else "随机")
        for spin, value in zip(self.iv_min, state_filter.iv_min):
            spin.setText(str(value))
        for spin, value in zip(self.iv_max, state_filter.iv_max):
            spin.setText(str(value))
        self.ability_filter.setCurrentIndex(max(0, self.ability_filter.findData(state_filter.ability)))
        self.gender_filter.setCurrentIndex(max(0, self.gender_filter.findData(state_filter.gender)))
        self.height_min.setText(str(state_filter.height_min))
        self.height_max.setText(str(state_filter.height_max))
        self.weight_min.setText(str(state_filter.weight_min))
        self.weight_max.setText(str(state_filter.weight_max))
        self.shiny_filter.setCurrentIndex(max(0, self.shiny_filter.findData(shiny_mode)))
        self.skip_filter.setChecked(state_filter.skip)
        nature_index = -1
        if state_filter.natures and not all(state_filter.natures):
            try:
                nature_index = next(index for index, enabled in enumerate(state_filter.natures) if enabled)
            except StopIteration:
                nature_index = -1
        self.nature_combo.setCurrentIndex(max(0, self.nature_combo.findData(nature_index)))

    def _build_auto_rng_services(self, config: AutoRngConfig) -> AutoRngServices:
        seed_config_path = config.seed_config_path or self._selected_auto_seed_config_path()
        reidentify_config_path = config.reidentify_config_path or self._selected_auto_reidentify_config_path()
        tracking_config = load_project_xs_config(seed_config_path, blink_count=DEFAULT_BLINK_COUNT)
        exit_tracking_config = load_project_xs_config(reidentify_config_path, blink_count=NOISY_REIDENTIFY_BLINK_COUNT)
        tracking_config = replace(
            tracking_config,
            capture=self._shared_capture_config(tracking_config.capture),
        )
        exit_tracking_config = replace(
            exit_tracking_config,
            capture=self._shared_capture_config(exit_tracking_config.capture),
        )
        exit_tracking_config = replace(exit_tracking_config, reidentify_1_pk_npc=True)
        ocr_region_config = self._ocr_region_config()
        shiny_dialog_region = ocr_region_config.get(SHINY_DIALOG_REGION_FIELD)
        starter_battle_region = ocr_region_config.get(STARTER_BATTLE_REGION_FIELD)
        self.auto_rng_tab.set_target_version(self._profile_version)
        target_entries = self.auto_rng_tab.targets()
        record, state_filter, shiny_mode = target_entries[0]
        search_targets = [StaticSearchTarget(sf, mode) for _record, sf, mode in target_entries]
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
        self._update_auto_rng_search_summary(search_criteria, search_targets)

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

        def reidentify_capture_for(source_config: ProjectXsTrackingConfig) -> BlinkCaptureConfig:
            blink_count = NOISY_REIDENTIFY_BLINK_COUNT if source_config.reidentify_1_pk_npc else REIDENTIFY_BLINK_COUNT
            return replace(source_config.capture, blink_count=blink_count)

        def reidentify_from_observation_for(
            current_state: SeedState32,
            observation: object,
            source_config: ProjectXsTrackingConfig,
            *,
            search_min: int,
            search_max: int,
        ) -> ProjectXsReidentifyResult:
            if source_config.reidentify_1_pk_npc:
                return reidentify_seed_from_observation_noisy(
                    current_state,
                    observation,  # type: ignore[arg-type]
                    search_min=search_min,
                    search_max=search_max,
                )
            return reidentify_seed_from_observation(
                current_state,
                observation,  # type: ignore[arg-type]
                npc=source_config.npc,
                search_min=search_min,
                search_max=search_max,
            )

        def reidentify_search_args(
            source_config: ProjectXsTrackingConfig,
            hint: int | None,
        ) -> tuple[int, int]:
            if hint is not None:
                search_min = max(0, int(hint) - REIDENTIFY_HINT_BEFORE_FRAMES)
                search_upper = max(search_min + 1, int(hint) + REIDENTIFY_HINT_AFTER_FRAMES)
                if source_config.reidentify_1_pk_npc:
                    return search_min, max(1, search_upper - search_min)
                return search_min, search_upper
            if source_config.reidentify_1_pk_npc:
                return 0, NOISY_REIDENTIFY_MAX_SEARCH_FRAMES
            return 0, max(100_000, config.max_advances, search_criteria.max_advances)

        def log_reidentify_debug(
            label: str,
            source_config: ProjectXsTrackingConfig,
            search_min: int,
            search_max: int,
            elapsed_seconds: float,
            result: ProjectXsReidentifyResult,
        ) -> None:
            if not config.debug_output:
                return
            if source_config.reidentify_1_pk_npc:
                search_text = f"{search_min}..{search_min + search_max}（窗口 {search_max}）"
            else:
                search_text = f"{search_min}..{search_max}"
            backend = getattr(result, "backend", "unknown")
            self.auto_rng_tab.captureLog.emit(
                f"{label} 搜索范围 {search_text}，后端 {backend}，计算耗时 {elapsed_seconds:.3f}s"
            )

        def capture_seed_service() -> AutoRngSeedResult:
            self._capture_cancel.clear()

            def store_frame(frame: object) -> None:
                self.autoCaptureFrameChanged.emit(frame)

            def store_progress(done: int, total: int) -> None:
                self.autoCaptureProgressChanged.emit(done, total)

            store_progress = self._wrap_capture_progress_with_keep_awake(store_progress)

            # 测种前先暂停预览，避免抢摄像头；测完后恢复
            preview_was_running = bool(self._call_on_ui_thread(self._pause_auto_preview_for_capture))
            if preview_was_running:
                time.sleep(0.3)  # 等摄像头释放

            try:
                observation = capture_player_blinks(
                    tracking_config.capture,
                    should_stop=self._capture_cancel.is_set,
                    frame_callback=store_frame,
                    progress_callback=store_progress,
                    show_window=False,
                    discard_first_blink_within_seconds=AUTO_CAPTURE_WARMUP_DISCARD_SECONDS,
                )
            except ProjectXsIntegrationError as exc:
                if not self._capture_cancel.is_set():
                    title, message = _project_xs_capture_error_dialog(exc)
                    self._call_on_ui_thread(
                        lambda title=title, message=message: QMessageBox.warning(
                            self, title, message
                        )
                    )
                raise
            finally:
                self._call_on_ui_thread(lambda: self._restore_auto_preview_after_capture(preview_was_running))
            result = recover_seed_from_observation(observation, npc=tracking_config.npc)
            elapsed_seconds = max(0, round(time.perf_counter() - observation.offset_time))
            elapsed_advances = elapsed_seconds * (tracking_config.npc + 1)
            if elapsed_advances:
                result = replace(result, state=advance_seed_state(result.state, elapsed_advances).state)
            seed_result = AutoRngSeedResult(
                seed=result.state,
                current_advances=0,
                npc=tracking_config.npc,
                seed_text=" ".join(result.state.format_seed64_pair()),
                measured_at=time.monotonic(),
            )
            self.autoSeedCaptured.emit(seed_result)
            return seed_result

        def current_seed_service() -> AutoRngSeedResult:
            return self._call_on_ui_thread(self._current_auto_rng_seed_result)  # type: ignore[return-value]

        def reidentify_service(seed_result: AutoRngSeedResult) -> AutoRngSeedResult:
            self._capture_cancel.clear()
            source_config = exit_tracking_config if seed_result.after_exit_reseed else tracking_config
            source_npc = source_config.npc

            def store_frame(frame: object) -> None:
                self.autoCaptureFrameChanged.emit(frame)

            def store_progress(done: int, total: int) -> None:
                self.autoCaptureProgressChanged.emit(done, total)

            store_progress = self._wrap_capture_progress_with_keep_awake(store_progress)

            # 测种前暂停预览，避免抢摄像头
            preview_was_running = bool(self._call_on_ui_thread(self._pause_auto_preview_for_capture))
            if preview_was_running:
                time.sleep(0.3)

            hint = seed_result.expected_advances_hint
            search_min, search_max = reidentify_search_args(source_config, hint)
            observation = capture_player_blinks(
                reidentify_capture_for(source_config),
                should_stop=self._capture_cancel.is_set,
                frame_callback=store_frame,
                progress_callback=store_progress,
                show_window=False,
                discard_first_blink_within_seconds=AUTO_CAPTURE_WARMUP_DISCARD_SECONDS,
            )
            reidentify_started_at = time.perf_counter()
            result = reidentify_from_observation_for(
                state32_from_result(seed_result),
                observation,
                source_config,
                search_min=search_min,
                search_max=search_max,
            )
            log_reidentify_debug(
                "校正",
                source_config,
                search_min,
                search_max,
                time.perf_counter() - reidentify_started_at,
                result,
            )
            elapsed_seconds = 0
            offset_time = float(getattr(observation, "offset_time", 0.0) or 0.0)
            if offset_time > 0:
                elapsed_seconds = max(0, round(time.perf_counter() - offset_time))
            elapsed_advances = elapsed_seconds * (source_npc + 1)
            current_advances = result.advances + elapsed_advances
            timing_seed = result.state
            if elapsed_advances:
                timing_seed = advance_seed_state(timing_seed, elapsed_advances).state
            # 校验 reidentify 结果与预期的偏差
            if hint is not None and abs(current_advances - hint) > 20_000:
                self.auto_rng_tab.captureLog.emit(
                    f"校正结果 {current_advances} 偏离预期 {hint} 超过 20000，"
                    f"可能识别错误，但仍继续（由上层决策判断）"
                )
            # reidentify 不改变 seed，只更新 current_advances 位置
            # 保留原始 seed，避免数据区被推进后的状态覆盖
            reidentified = AutoRngSeedResult(
                seed=seed_result.seed,
                current_advances=current_advances,
                npc=source_npc,
                seed_text=seed_result.seed_text,
                measured_at=time.monotonic(),
                after_exit_reseed=seed_result.after_exit_reseed,
                advance_mode="timeline" if source_config.reidentify_1_pk_npc else "linear",
                timing_seed=timing_seed if source_config.reidentify_1_pk_npc else None,
                timeline_npc=source_config.timeline_npc,
                pokemon_npc=max(1, source_config.pokemon_npc) if source_config.reidentify_1_pk_npc else source_config.pokemon_npc,
                white_delay=source_config.white_delay,
                advance_delay=source_config.advance_delay,
                advance_delay_2=source_config.advance_delay_2,
            )
            self.autoSeedCaptured.emit(reidentified)
            self._call_on_ui_thread(lambda: self._restore_auto_preview_after_capture(preview_was_running))
            return reidentified

        def reidentify_exit_service(seed_result: AutoRngSeedResult) -> AutoRngSeedResult:
            self._capture_cancel.clear()

            def store_frame(frame: object) -> None:
                self.autoCaptureFrameChanged.emit(frame)

            def store_progress(done: int, total: int) -> None:
                self.autoCaptureProgressChanged.emit(done, total)

            store_progress = self._wrap_capture_progress_with_keep_awake(store_progress)

            preview_was_running = bool(self._call_on_ui_thread(self._pause_auto_preview_for_capture))
            if preview_was_running:
                time.sleep(0.3)

            observation = capture_player_blinks(
                replace(exit_tracking_config.capture, blink_count=NOISY_REIDENTIFY_BLINK_COUNT),
                should_stop=self._capture_cancel.is_set,
                frame_callback=store_frame,
                progress_callback=store_progress,
                show_window=False,
                discard_first_blink_within_seconds=AUTO_CAPTURE_WARMUP_DISCARD_SECONDS,
            )
            search_min, search_max = reidentify_search_args(exit_tracking_config, None)
            reidentify_started_at = time.perf_counter()
            result = reidentify_seed_from_observation_noisy(
                state32_from_result(seed_result),
                observation,
                search_min=search_min,
                search_max=search_max,
            )
            log_reidentify_debug(
                "过场校正",
                exit_tracking_config,
                search_min,
                search_max,
                time.perf_counter() - reidentify_started_at,
                result,
            )
            elapsed_seconds = 0
            offset_time = float(getattr(observation, "offset_time", 0.0) or 0.0)
            if offset_time > 0:
                elapsed_seconds = max(0, round(time.perf_counter() - offset_time))
            npc = exit_tracking_config.npc
            elapsed_advances = elapsed_seconds * (npc + 1)
            timing_seed = result.state
            if elapsed_advances:
                timing_seed = advance_seed_state(timing_seed, elapsed_advances).state
            reidentified = AutoRngSeedResult(
                seed=seed_result.seed,
                current_advances=result.advances + elapsed_advances,
                npc=npc,
                seed_text=seed_result.seed_text,
                measured_at=time.monotonic(),
                advance_mode="timeline",
                timing_seed=timing_seed,
                timeline_npc=exit_tracking_config.timeline_npc,
                pokemon_npc=max(1, exit_tracking_config.pokemon_npc),
                white_delay=exit_tracking_config.white_delay,
                advance_delay=exit_tracking_config.advance_delay,
                advance_delay_2=exit_tracking_config.advance_delay_2,
            )
            self.autoSeedCaptured.emit(reidentified)
            self._call_on_ui_thread(lambda: self._restore_auto_preview_after_capture(preview_was_running))
            return reidentified

        def search_candidates_service(seed_result: AutoRngSeedResult) -> list[State8]:
            candidates = generate_static_candidates_multi(
                replace(search_criteria, seed=seed_pair_from_result(seed_result)),
                search_targets,
            )
            locked = candidates[0].advances if candidates else None
            if locked is None:
                self.auto_rng_tab.captureLog.emit("找到 0 个候选")
            else:
                self.auto_rng_tab.captureLog.emit(f"找到 {len(candidates)} 个候选，最低帧 Adv={locked}")
            return candidates

        def search_sync_service(seed_result: AutoRngSeedResult, lead: int, nature_locked: int | None) -> list[State8]:
            """按指定 lead 和锁定性格搜索。lead=255 为无同步，0-24 为同步对应性格值。"""
            from auto_bdsp_rng.gen8_static.models import Lead
            crit = replace(search_criteria, seed=seed_pair_from_result(seed_result),
                          lead=int(lead))
            if nature_locked is not None and 0 <= nature_locked <= 24:
                natures = tuple(i == nature_locked for i in range(25))
                crit = replace(crit, state_filter=replace(crit.state_filter, natures=natures))
            sync_targets = [
                StaticSearchTarget(replace(target.state_filter, natures=crit.state_filter.natures), target.shiny_mode)
                for target in search_targets
            ]
            candidates = generate_static_candidates_multi(crit, sync_targets)
            return list(candidates)

        def run_script_text_service(
            script_text: str,
            name: str,
            *,
            image_result_observer: Callable[[object], None] | None = None,
        ) -> object:
            script_name, script_dir = self._native_script_context(config, script_text, name)

            def prepare_script() -> tuple[object, int | None]:
                backend = self._prepare_auto_easycon_script(script_name)
                try:
                    observer_token = (
                        self._add_easycon_image_result_observer(
                            image_result_observer,
                            source_generation=self._video_source_generation,
                            run_generation=self._easycon_run_generation,
                        )
                        if image_result_observer is not None
                        else None
                    )
                except BaseException as exc:
                    try:
                        self.autoScriptFailed.emit(str(exc))
                    finally:
                        self.easycon_tab.release_native_script_run()
                    raise
                return backend, observer_token

            native_backend, observer_token = self._call_on_ui_thread(prepare_script)
            try:
                try:
                    result = native_backend.run_script_text(
                        script_text,
                        script_name,
                        script_dir=script_dir,
                    )
                except BaseException as exc:
                    self._fail_auto_script(exc)
                    raise
                return self._finalize_auto_script_result(result, script_name)
            finally:
                if observer_token is not None:
                    self._remove_easycon_image_result_observer(observer_token)

        def stop_current_script_service() -> None:
            self._capture_cancel.set()
            try:
                native_backend = self._call_on_ui_thread(self.easycon_tab._ensure_native_backend)
            except Exception:
                return
            try:
                native_backend.stop_current_script()
            except Exception:
                pass

        def run_hit_script_with_shiny_check(script_text: str, name: str, threshold_seconds: float) -> ShinyCheckResult:
            self._capture_cancel.clear()
            is_roamer = config.target_species in ROAMER_SPECIES
            is_starter = config.target_species in STARTER_SPECIES
            first_keywords: str | tuple[str, ...] = ("去吧", "上吧") if is_starter else "出现了！"
            second_keywords: tuple[str, ...] = ("战斗", "戰鬥") if is_starter else ("去吧", "上吧")
            first_event_label = "去吧/上吧" if is_starter else "出现了! / 出现了！"
            second_event_label = "战斗" if is_starter else "去吧/上吧"
            errors: list[BaseException] = []
            script_done = threading.Event()
            roamer_battle_started = threading.Event()
            monitor_started_at = time.monotonic()
            wall_clock_offset = time.time() - time.monotonic()
            logged_roi_fields: set[str] = set()
            first_event: DialogTimingEvent | None = None

            def observe_roamer_battle(result: object) -> None:
                if getattr(result, "label_name", None) != ROAMER_BATTLE_LABEL:
                    return
                try:
                    score = int(getattr(result, "script_value"))
                except (AttributeError, TypeError, ValueError):
                    return
                if score < ROAMER_BATTLE_MATCH_THRESHOLD:
                    roamer_battle_started.set()

            def run_script() -> None:
                try:
                    run_script_text_service(
                        script_text,
                        name,
                        image_result_observer=(observe_roamer_battle if is_roamer else None),
                    )
                except BaseException as exc:
                    errors.append(exc)
                finally:
                    script_done.set()

            # 普通目标和御三家立即并行 OCR；游走目标先等待脚本确认进入战斗。
            script_thread = threading.Thread(target=run_script, daemon=True)
            try:
                script_thread.start()
                if is_roamer:
                    self.auto_rng_tab.captureLog.emit(
                        "[OCR判闪] 游走模式：等待脚本检测进入战斗，等待期间不运行 OCR"
                    )
                    while not roamer_battle_started.wait(timeout=0.1):
                        if errors:
                            raise errors[0]
                        if self._capture_cancel.is_set():
                            raise RuntimeError("自动流程已停止")
                        if script_done.is_set():
                            script_thread.join(timeout=0.1)
                            if errors:
                                raise errors[0]
                            self.auto_rng_tab.captureLog.emit(
                                "[OCR判闪] 游走脚本已结束，但未检测到进入战斗；判定结果未知"
                            )
                            return ShinyCheckResult(is_shiny=False)
                    if errors:
                        raise errors[0]
                    monitor_started_at = time.monotonic()
                    self.auto_rng_tab.captureLog.emit(
                        "[OCR判闪] 已检测到进入战斗，开始监控战斗关键词"
                    )
            except BaseException:
                stop_current_script_service()
                if script_thread.is_alive():
                    script_thread.join(timeout=5.0)
                raise

            def capture_ocr_region(field: str) -> object:
                frame = self._capture_preview_frame_for_config(tracking_config.capture)
                image_shape = tuple(getattr(frame, "shape"))
                effective_region = ocr_region_config.resolve(field, image_shape)
                label = OCR_REGION_LABELS[field]
                if effective_region is None:
                    raise RuntimeError(f"{label} ROI 在当前画面中无效，且无法使用默认范围")
                if field not in logged_roi_fields:
                    logged_roi_fields.add(field)
                    configured_region = (
                        shiny_dialog_region
                        if field == SHINY_DIALOG_REGION_FIELD
                        else starter_battle_region
                    )
                    fallback_label = (
                        "当前画面下方 50%"
                        if field == SHINY_DIALOG_REGION_FIELD
                        else "当前画面的御三家战斗按钮默认范围"
                    )
                    if ocr_region_config.has_invalid_custom(field):
                        self.auto_rng_tab.captureLog.emit(
                            f"[OCR判闪] {label} ROI 配置无效，已回退到{fallback_label}"
                        )
                    elif configured_region is not None:
                        image_height, image_width = image_shape[:2]
                        if not configured_region.clip(image_width, image_height).is_valid():
                            self.auto_rng_tab.captureLog.emit(
                                f"[OCR判闪] 自定义{label} ROI 超出当前画面，已回退到{fallback_label}"
                            )
                    self.auto_rng_tab.captureLog.emit(
                        f"[OCR判闪] {label}有效 ROI：X={effective_region.x}, Y={effective_region.y}, "
                        f"W={effective_region.width}, H={effective_region.height}"
                    )
                    dialog = self._ocr_settings_dialog
                    if dialog is not None:
                        self._call_on_ui_thread(lambda: dialog.set_preview_frame_shape(image_shape))
                x, y, width, height = effective_region.as_tuple()
                return frame[y : y + height, x : x + width]

            def capture_dialog_region() -> object:
                return capture_ocr_region(SHINY_DIALOG_REGION_FIELD)

            def capture_starter_battle_region() -> object:
                return capture_ocr_region(STARTER_BATTLE_REGION_FIELD)

            def wall_clock(observed_at: float) -> str:
                timestamp = observed_at + wall_clock_offset
                whole = time.strftime("%H:%M:%S", time.localtime(timestamp))
                milliseconds = int(timestamp % 1 * 1000)
                return f"{whole}.{milliseconds:03d}"

            def log_ocr_event(event: DialogTimingEvent) -> None:
                nonlocal first_event
                observed = wall_clock(event.observed_at)
                if event.event == "monitor_started":
                    first_keyword_rule = (
                        "首关键词：确认进入战斗后开始监控，脚本结束后宽限 30.000s；"
                        if is_roamer
                        else "首关键词：撞闪脚本运行期间持续监控，脚本结束后宽限 30.000s；"
                    )
                    self.auto_rng_tab.captureLog.emit(
                        f"[OCR判闪] 开始监控：{observed}；"
                        f"判定事件：「{first_event_label}」->「{second_event_label}」；"
                        f"{first_keyword_rule}"
                        "次关键词：识别首关键词后等待 30.000s；脚本硬超时 300.000s"
                    )
                elif event.event == "first_seen":
                    first_event = event
                    recognized_label = event.keyword if is_starter and event.keyword else first_event_label
                    self.auto_rng_tab.captureLog.emit(
                        f"[OCR判闪] 识别到「{recognized_label}」：{observed}；"
                        f"监控累计 {event.elapsed_seconds:.3f}s"
                    )
                elif event.event == "second_seen":
                    keyword = event.keyword or second_event_label
                    interval = event.interval_seconds or 0.0
                    self.auto_rng_tab.captureLog.emit(
                        f"[OCR判闪] 识别到「{keyword}」：{observed}；"
                        f"监控累计 {event.elapsed_seconds:.3f}s；关键词间隔 {interval:.3f}s"
                    )
                elif event.event == "timeout_before_first":
                    self.auto_rng_tab.captureLog.emit(
                        f"[OCR判闪] 超时：阶段=等待「{first_event_label}」；"
                        f"原因=撞闪脚本完成后 30.000s 内未识别；超时时间={observed}；"
                        f"监控累计 {event.elapsed_seconds:.3f}s"
                    )
                elif event.event == "timeout_after_first":
                    first_time = "未知" if first_event is None else wall_clock(first_event.observed_at)
                    interval = event.interval_seconds or 0.0
                    self.auto_rng_tab.captureLog.emit(
                        f"[OCR判闪] 超时：阶段=等待「{second_event_label}」；"
                        f"原因=识别首关键词后 30.000s 内未识别；首关键词时间={first_time}；"
                        f"超时时间={observed}；实际等待 {interval:.3f}s"
                    )
                elif event.event == "script_timeout":
                    self.auto_rng_tab.captureLog.emit(
                        f"[OCR判闪] 超时：阶段=撞闪脚本运行；原因=脚本 300.000s 内未完成；"
                        f"超时时间={observed}；监控累计 {event.elapsed_seconds:.3f}s"
                    )

            def wait_for_script_after_keyword_timeout() -> None:
                if script_done.is_set():
                    return
                self.auto_rng_tab.captureLog.emit("[OCR判闪] OCR 已超时，等待撞闪脚本完成后继续")
                deadline = monitor_started_at + 300.0
                while not script_done.is_set():
                    if self._capture_cancel.is_set():
                        raise RuntimeError("自动流程已停止")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self.auto_rng_tab.captureLog.emit(
                            "[OCR判闪] 超时：阶段=撞闪脚本运行；原因=监控开始后 300.000s 脚本仍未完成"
                        )
                        stop_current_script_service()
                        script_thread.join(timeout=5.0)
                        raise RuntimeError("撞闪脚本运行超过 300 秒，已停止自动流程")
                    script_done.wait(timeout=min(0.2, remaining))

            try:
                timing = measure_keyword_interval(
                    capture_dialog_region,
                    read_ocr_text,
                    first_keyword=first_keywords,
                    second_keyword=second_keywords,
                    second_capture_frame=(capture_starter_battle_region if is_starter else None),
                    should_stop=lambda: self._capture_cancel.is_set() or bool(errors),
                    poll_interval_seconds=0.1,
                    script_done=script_done,
                    grace_seconds=30.0,
                    hard_timeout_seconds=300.0,
                    event_callback=log_ocr_event,
                )
            except DialogKeywordTimeoutError:
                wait_for_script_after_keyword_timeout()
                script_thread.join(timeout=0.1)
                if errors:
                    raise errors[0]
                timeout_action = (
                    "停止自动流程并等待人工确认"
                    if is_roamer or is_starter
                    else "按未出闪继续自动流程"
                )
                self.auto_rng_tab.captureLog.emit(
                    f"[OCR判闪] 关键词识别超时，判定结果未知；{timeout_action}"
                )
                return ShinyCheckResult(
                    is_shiny=False,
                    first_event_text="去吧/上吧" if is_starter else "出现了",
                    second_event_text="战斗" if is_starter else "去吧",
                )
            except DialogScriptTimeoutError as exc:
                stop_current_script_service()
                script_thread.join(timeout=5.0)
                if errors:
                    raise errors[0]
                raise RuntimeError("撞闪脚本运行超过 300 秒，已停止自动流程") from exc
            except Exception as exc:
                stop_current_script_service()
                script_thread.join(timeout=5.0)
                if errors:
                    raise errors[0]
                raise RuntimeError(f"OCR 判闪基础设施故障，已停止自动流程: {exc}") from exc
            is_shiny = timing.interval_seconds >= threshold_seconds
            if not is_shiny:
                stop_current_script_service()
                script_thread.join(timeout=5.0)
                if script_thread.is_alive():
                    raise RuntimeError("撞闪脚本停止超时，已停止自动流程")
            elif script_thread.is_alive():
                self.auto_rng_tab.captureLog.emit("[OCR判闪] 关键词判定完成，等待撞闪脚本结束")
                deadline = monitor_started_at + 300.0
                while script_thread.is_alive() and time.monotonic() < deadline:
                    if self._capture_cancel.is_set():
                        stop_current_script_service()
                        raise RuntimeError("自动流程已停止")
                    script_thread.join(timeout=min(0.2, max(0.0, deadline - time.monotonic())))
                if script_thread.is_alive():
                    self.auto_rng_tab.captureLog.emit(
                        "[OCR判闪] 超时：阶段=撞闪脚本运行；原因=监控开始后 300.000s 脚本仍未完成"
                    )
                    stop_current_script_service()
                    script_thread.join(timeout=5.0)
                    raise RuntimeError("撞闪脚本运行超过 300 秒，已停止自动流程")
            if errors:
                raise errors[0]
            self.auto_rng_tab.captureLog.emit(
                f"[OCR判闪] 判定：关键词间隔 {timing.interval_seconds:.3f}s，"
                f"阈值 {threshold_seconds:.3f}s，结果={'疑似出闪' if is_shiny else '未出闪'}"
            )
            matched_first_keyword = next(
                (event.keyword for event in timing.events if event.event == "first_seen" and event.keyword),
                None,
            )
            return ShinyCheckResult(
                is_shiny=is_shiny,
                interval_seconds=timing.interval_seconds,
                first_event_text=(matched_first_keyword or "去吧/上吧") if is_starter else "出现了",
                second_event_text="战斗" if is_starter else "去吧",
            )

        def reverse_lookup_service(seed_result: AutoRngSeedResult, target: object) -> None:
            import time
            try:
                from auto_bdsp_rng.rng_core._native import compute_iv_ranges as _compute_iv_ranges
            except ImportError:
                _compute_iv_ranges = None

            log = self.auto_rng_tab.captureLog.emit
            path = config.reverse_script_path
            if path is None:
                raise RuntimeError("反查脚本未配置")
            text = path.read_text(encoding="utf-8")
            log("[自动反查] 运行反查脚本…")
            run_script_text_service(text, path.name)
            time.sleep(1.0)

            # OCR 笔记页 → 性格 + 个性（只做一次，识别可靠）
            log("[自动反查] 截图笔记页…")
            notes_frame = self._capture_preview_frame_for_config(tracking_config.capture)
            ocr_regions = self._ocr_region_config()
            notes_result = extract_pokemon_info(notes_image=notes_frame, ocr_regions=ocr_regions)
            nature = notes_result.get("nature")
            characteristic = notes_result.get("characteristic")
            log(f"[自动反查] 性格={nature}, 个性={characteristic}")

            # RIGHT → 能力页
            self._pause_ocr_and_turn_to_stats_page(log_details=False)
            time.sleep(2.0)

            # 能力值 → 个体值 反算（用到的辅助函数只定义一次）
            from auto_bdsp_rng.automation.auto_rng.pokemon_info_ocr import compute_characteristic
            from auto_bdsp_rng.gen8_static.models import StateFilter
            from auto_bdsp_rng.automation.auto_rng.runner import _NATURE_MAP

            target_desc = search_criteria.record.description
            group_species = _reverse_lookup_group_descriptions(target_desc)
            if len(group_species) > 1:
                log(f"[自动反查] 多地精灵组: {', '.join(_reverse_species_label(desc) for desc in group_species)}")

            all_records = get_static_encounters()
            species_records = {
                desc: next((r for r in all_records if r.description == desc), search_criteria.record)
                for desc in group_species
            }
            ni = _NATURE_MAP.get(str(nature)) if nature else None
            # 构建搜索条件模板（性格+个性锁定，帧数范围可配置）
            natures_locked = (True,) * 25
            if ni is not None and 0 <= ni < 25:
                natures_locked = tuple(i == ni for i in range(25))
            reverse_start, reverse_end, reverse_count = _reverse_lookup_search_span(
                target.raw_target_advances,
                config.reverse_lookup_window,
            )
            reverse_lead = search_criteria.lead
            if getattr(target, "sync_source", None) == "sync" and getattr(target, "sync_nature", None) is not None:
                reverse_lead = int(getattr(target, "sync_nature"))
            criteria = replace(search_criteria, seed=seed_pair_from_result(seed_result),
                              shiny_mode="any",
                              initial_advances=reverse_start,
                              max_advances=reverse_count,
                              lead=reverse_lead)
            log(f"[自动反查] 搜索范围: Adv {reverse_start}-{reverse_end} (±{min(10_000, max(0, int(config.reverse_lookup_window)))})")

            # 能力页 OCR 重试逻辑（最多3次）
            stat_names = ["HP", "攻击", "防御", "特攻", "特防", "速度"]
            candidates: list[object] = []
            last_ocr_stats: dict[str, object] | None = None
            prev_ocr_key: str | None = None
            for attempt in range(1, 4):
                log(f"[自动反查] 能力页 OCR 第{attempt}次…")
                stats_frame = self._capture_preview_frame_for_config(tracking_config.capture)
                stats_result = extract_pokemon_info(stats_image=stats_frame, ocr_regions=ocr_regions)
                stats = stats_result.get("stats")
                if not stats:
                    log(f"[自动反查] 第{attempt}次能力值识别失败")
                    if attempt < 3:
                        time.sleep(0.5)
                    continue

                stat_vals = [int(stats.get(n, 0)) for n in stat_names]
                use_nature = ni if (ni is not None and 0 <= ni < 25) else 255
                is_new_ocr = True  # 每个 OCR 尝试默认视作新结果
                last_ocr_stats = {
                    "stats": dict(zip(stat_names, stat_vals)),
                    "nature": nature,
                    "characteristic": characteristic,
                }

                # 对组内所有精灵遍历搜索
                for species_desc in group_species:
                    species_label = _reverse_species_label(species_desc)
                    species_record = species_records[species_desc]
                    species_base = species_record.species_info.stats
                    species_level = species_record.template.level

                    if _compute_iv_ranges is not None:
                        ranges = _compute_iv_ranges(list(species_base), stat_vals, use_nature, species_level)
                    else:
                        ranges = []
                        for i, name in enumerate(stat_names):
                            possible = []
                            for iv in range(32):
                                c = _compute_stat(species_base[i], iv, species_level, use_nature, i)
                                if c == stat_vals[i]:
                                    possible.append(iv)
                            ranges.append((min(possible), max(possible)) if possible else (31, 0))
                    normalized_ranges = _normalize_iv_ranges(ranges)
                    if normalized_ranges is None:
                        log(f"[自动反查] {species_label} 能力值无法反算出合法个体值范围")
                        continue
                    iv_min, iv_max = normalized_ranges
                    last_ocr_stats["iv_min"] = list(iv_min)
                    last_ocr_stats["iv_max"] = list(iv_max)
                    curr_key = f"{stat_vals}|{iv_min}|{iv_max}"
                    is_new_ocr = curr_key != prev_ocr_key
                    if is_new_ocr:
                        prev_ocr_key = curr_key
                        for i, name in enumerate(stat_names):
                            log(f"[自动反查] {species_label} {name}={stat_vals[i]} → IV {iv_min[i]}-{iv_max[i]} (基础{species_base[i]} Lv{species_level})")
                    else:
                        log(f"[自动反查] 第{attempt}次 OCR 结果与上次相同，跳过重复输出")

                    species_criteria = replace(criteria, record=species_record)
                    reverse_filter = StateFilter(
                        iv_min=tuple(iv_min), iv_max=tuple(iv_max),
                        natures=natures_locked,
                    )
                    attempt_criteria = replace(species_criteria, state_filter=reverse_filter)
                    species_candidates = list(generate_static_candidates(attempt_criteria))
                    # 标记反查精灵名
                    for c in species_candidates:
                        object.__setattr__(c, "_reverse_species", species_desc)
                    if is_new_ocr:
                        log(f"[自动反查] {species_label} PokeFinder 搜索: {len(species_candidates)} 个候选")

                    if characteristic and species_candidates:
                        matched: list[object] = []
                        for state in species_candidates:
                            state_ivs = [int(v) for v in (getattr(state, "ivs", None) or [])]
                            if len(state_ivs) != 6:
                                state_ivs = [0] * 6
                            ec_val = int(getattr(state, "ec", 0))
                            if compute_characteristic(ec_val, state_ivs) == characteristic:
                                matched.append(state)
                        log(f"[自动反查] {species_label} 个性({characteristic})匹配: {len(matched)} 个")
                        species_candidates = matched

                    candidates.extend(species_candidates)

                if candidates:
                    break
                log(f"[自动反查] 第{attempt}次未找到匹配个体，{'重试…' if attempt < 3 else '已达上限'}")

            # 输出结果
            if not candidates:
                log("[自动反查] 3次尝试均未找到匹配个体")
                self.autoHistoryEvent.emit("reverse_lookup_results", ([], characteristic, None, last_ocr_stats))
            else:
                delays = [int(getattr(s, "advances", 0)) - target.raw_target_advances + config.fixed_delay
                          if target.raw_target_advances else int(getattr(s, "advances", 0))
                          for s in candidates]
                self.autoHistoryEvent.emit("reverse_lookup_results", (candidates, characteristic, delays))
                for state in candidates:
                    adv = int(getattr(state, "advances", 0))
                    actual_delay = adv - target.raw_target_advances + config.fixed_delay if target.raw_target_advances else adv
                    state_ivs = getattr(state, "ivs", None)
                    iv_text = " / ".join(f"{name}={int(state_ivs[i])}" for i, name in enumerate(["HP","攻击","防御","特攻","特防","速度"])) if state_ivs is not None and len(state_ivs) == 6 else "?"
                    pid_val = int(getattr(state, "pid", 0))
                    species = getattr(state, "_reverse_species", "")
                    species_tag = f"[{_reverse_species_label(species)}] " if species else ""
                    log(f"[自动反查] {species_tag}advances={adv} delay={actual_delay} EC={getattr(state,'ec','?')} PID={pid_val:08X} {iv_text}")

        def recover_zoom_mode_service() -> bool:
            return self._recover_zoom_mode_with_preview_paused(
                tracking_config.capture,
                run_script_text_service,
            )

        return AutoRngServices(
            current_seed=current_seed_service,
            capture_seed=capture_seed_service,
            reidentify=reidentify_service,
            reidentify_exit=reidentify_exit_service,
            search_candidates=search_candidates_service,
            search_sync=search_sync_service,
            run_script_text=run_script_text_service,
            run_hit_script_with_shiny_check=run_hit_script_with_shiny_check,
            run_reverse_lookup=reverse_lookup_service,
            recover_zoom_mode=recover_zoom_mode_service,
            stop_current_script=stop_current_script_service,
        )

    def _update_auto_rng_search_summary(self, criteria: StaticSearchCriteria, targets: list[StaticSearchTarget]) -> None:
        target_text = self.auto_rng_tab.target_summary_title.text()
        profile_text = (
            f"{criteria.profile.name} / TID {criteria.profile.tid} / SID {criteria.profile.sid} / "
            f"{GAME_LABELS_EN.get(self._profile_version, self._profile_version.value)}"
        )
        iv_text = ", ".join(
            f"{label} {low}-{high}"
            for label, low, high in zip(IV_LABELS, criteria.state_filter.iv_min, criteria.state_filter.iv_max)
        )
        filter_text = self.auto_rng_tab.target_summary_text() or (
            f"异色={criteria.shiny_mode}; 特性={criteria.state_filter.ability}; "
            f"性别={criteria.state_filter.gender}; 身高 {criteria.state_filter.height_min}-{criteria.state_filter.height_max}; "
            f"体重 {criteria.state_filter.weight_min}-{criteria.state_filter.weight_max}; {iv_text}"
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
        self._advance_counter = ProjectXsAdvanceCounter()
        self._advance_counter.reset(current_advances=0, npc=max(0, self._advance_step - 1), now=time.monotonic())
        self.advances_value.setText("0")
        self._update_auto_rng_header(advances=0)
        self.timer_value.setText("0")

    def _capture_pokemon_info(self) -> None:
        """临时功能：手动触发精灵信息捕获。

        主线程截图 → 后台 OCR + RIGHT → 主线程再截图 → 后台 OCR → 输出。
        截图始终在主线程，避免摄像头多线程闪退。
        """
        import threading

        self.auto_rng_tab.add_log("[捕获精灵信息] 开始…")

        try:
            capture_config = self._config_from_form().capture
        except Exception as exc:
            self.auto_rng_tab.add_log(f"[捕获精灵信息] 获取截图配置失败: {exc}", level="ERROR")
            return

        # 主线程截图笔记页
        try:
            notes_frame = self._capture_preview_frame_for_config(capture_config)
        except Exception as exc:
            self.auto_rng_tab.add_log(f"[捕获精灵信息] 截图笔记页失败: {exc}", level="ERROR")
            return

        self._capture_config = capture_config  # 暂存供后续用
        thread = threading.Thread(target=self._do_capture_pokemon_info, args=(notes_frame,), daemon=True)
        thread.start()

    def _do_capture_pokemon_info(self, notes_frame: object) -> None:
        import time

        log_error = self.auto_rng_tab.captureError.emit

        # 1) OCR 笔记页
        try:
            ocr_regions = self._ocr_region_config()
            notes_result = extract_pokemon_info(notes_image=notes_frame, ocr_regions=ocr_regions)
        except Exception as exc:
            log_error(f"[捕获精灵信息] OCR 笔记页失败: {exc}")
            return
        nature = notes_result.get("nature")
        characteristic = notes_result.get("characteristic")

        # 2) 发送 RIGHT 切换页面
        try:
            self._pause_ocr_and_turn_to_stats_page()
        except Exception as exc:
            log_error(f"[捕获精灵信息] 发送 RIGHT 指令失败: {exc}")
            return
        time.sleep(2.0)

        # 3) 请求主线程截图能力页
        self.auto_rng_tab.requestStatsCapture.emit(nature, characteristic)

    def _on_request_stats_capture(self, nature: str | None, characteristic: str | None) -> None:
        """主线程回调：截图能力页 → OCR → 输出。"""
        log = self.auto_rng_tab.captureLog.emit
        try:
            stats_frame = self._capture_preview_frame_for_config(self._capture_config)
        except Exception as exc:
            self.auto_rng_tab.add_log(f"[捕获精灵信息] 截图能力页失败: {exc}", level="ERROR")
            return
        try:
            stats_result = extract_pokemon_info(stats_image=stats_frame, ocr_regions=self._ocr_region_config())
        except Exception as exc:
            self.auto_rng_tab.add_log(f"[捕获精灵信息] OCR 能力页失败: {exc}", level="ERROR")
            return
        stats = stats_result.get("stats")

        stat_order = ["性格", "个性", "HP", "攻击", "防御", "特攻", "特防", "速度"]
        parts: list[str] = []
        for key in stat_order:
            if key == "性格":
                parts.append(f"性格={nature}")
            elif key == "个性":
                parts.append(f"个性={characteristic}")
            elif stats and key in stats:
                parts.append(f"{key}={stats[key]}")
            else:
                parts.append(f"{key}=?")
        log(f"[捕获精灵信息] {' / '.join(parts)}")

    def _send_easycon_right(self, *, log_details: bool = True) -> None:
        """通过伊机控发送 RIGHT d-pad 按钮。"""
        log = self.auto_rng_tab.captureLog.emit
        video_connected, native_status, native_backend = self._call_on_ui_thread(
            lambda: (
                self._video_source_connected,
                self.easycon_tab._native_status(),
                self.easycon_tab._ensure_native_backend(),
            )
        )
        if not video_connected:
            raise RuntimeError("请先在 Seed 捕捉页面连接视频源")
        if native_status != EasyConStatus.BRIDGE_CONNECTED:
            raise RuntimeError("请先连接伊机控")
        if log_details:
            log("[捕获精灵信息] Python 原生后端发送 RIGHT 200ms")
        native_backend.press("RIGHT", 200)

    def _pause_ocr_and_turn_to_stats_page(self, *, log_details: bool = True) -> None:
        time.sleep(0.1)
        self._send_easycon_right(log_details=log_details)
        time.sleep(0.1)

    def _advance_tick(self) -> None:
        self._refresh_tracked_advances_from_clock()
        self._schedule_advance_timer()

    def _refresh_tracked_advances_from_clock(self) -> None:
        self._advance_counter.advance_to(time.monotonic())
        self._display_tracked_advances(self._advance_counter.current_advances)

    def _schedule_advance_timer(self) -> None:
        interval_ms = 1018
        if isinstance(self._advance_counter, ProjectXsMunchlaxAdvanceCounter):
            seconds = self._advance_counter.next_tick_at - time.monotonic()
            interval_ms = max(1, round(seconds * 1000))
        self._advance_timer.setInterval(interval_ms)
        if not self._advance_timer.isActive():
            self._advance_timer.start()

    def _display_tracked_advances(self, advances: int) -> None:
        self._tracked_advances = int(advances)
        self.advances_value.setText(str(self._tracked_advances))
        self._update_auto_rng_header(advances=self._tracked_advances)
        self.auto_rng_tab.set_live_advances(self._tracked_advances)

    def _set_tracked_advances(self, advances: int) -> None:
        now = time.monotonic()
        if isinstance(self._advance_counter, ProjectXsMunchlaxAdvanceCounter):
            try:
                seed = SeedState32.from_hex_words([box.text() for box in self.seed32_inputs])
            except ValueError:
                self._advance_counter = ProjectXsAdvanceCounter()
                self._advance_counter.reset(
                    current_advances=int(advances),
                    npc=max(0, self._advance_step - 1),
                    now=now,
                )
            else:
                self._advance_counter.reset(current_advances=int(advances), seed=seed, now=now)
        else:
            self._advance_counter.reset(
                current_advances=int(advances),
                npc=max(0, self._advance_step - 1),
                now=now,
            )
        self._display_tracked_advances(self._advance_counter.current_advances)

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
        if self._advance_timer.isActive():
            self._refresh_tracked_advances_from_clock()
        self._set_tracked_advances(self._tracked_advances + advances)

    def capture_seed(self) -> None:
        if self._is_capturing():
            self._capture_cancel.set()
            self.capture_button.setText(self._text("stop_capture"))
            self.statusBar().showMessage(self._text("capture_stopping"))
            self._write_run_log("Seed 捕捉", f"已请求停止{self._capture_mode_label()}", level="WARNING")
            return
        try:
            config = self._config_from_form()
        except ProjectXsIntegrationError as exc:
            self._show_error("Blink capture failed", exc)
            return
        if not self._ensure_preview_frame_before_capture():
            return

        self._pause_preview_for_capture()
        self.preview_button.setEnabled(False)
        self._set_preview_selection_enabled(False)
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
        self.tidsid_button.setEnabled(False)
        self.statusBar().showMessage(self._text("capturing"))
        self._write_run_log(
            "Seed 捕捉",
            f"开始普通 Seed 捕捉；眨眼数 {config.capture.blink_count}；NPC {config.npc}",
        )

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

        store_progress = self._wrap_capture_progress_with_keep_awake(store_progress)

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
            self._write_run_log("Seed 捕捉", f"已请求停止{self._capture_mode_label()}", level="WARNING")
            return
        try:
            config = self._config_from_form()
            current_state = SeedState32.from_hex_words([box.text() for box in self.seed32_inputs])
        except Exception as exc:
            self._show_error("校正失败", exc if isinstance(exc, Exception) else Exception(str(exc)))
            return
        if not self._ensure_preview_frame_before_capture():
            return

        self._pause_preview_for_capture()
        self.preview_button.setEnabled(False)
        self.reidentify_button.setEnabled(False)
        self._set_preview_selection_enabled(False)
        tracked_advances = self._tracked_advances
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
        self.tidsid_button.setEnabled(False)
        self.statusBar().showMessage(f"Capturing {reidentify_blink_count} blinks...")
        self._write_run_log(
            "Seed 捕捉",
            f"开始校正；眨眼数 {reidentify_blink_count}；NPC {config.npc}；当前 Adv {tracked_advances}",
        )

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

        store_progress = self._wrap_capture_progress_with_keep_awake(store_progress)

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
                    search_min=max(0, tracked_advances - 10_000) if tracked_advances else 0,
                    search_max=max(100_000, int(self.max_advances.text() or 0) if hasattr(self, "max_advances") else 100_000),
                )
            except Exception as exc:  # pragma: no cover - exercised through UI polling
                self._capture_error = exc if isinstance(exc, Exception) else Exception(str(exc))

        self._capture_thread = threading.Thread(target=run_reidentify, daemon=True)
        self._capture_thread.start()
        self._capture_timer.start()

    def capture_tidsid_seed(self) -> None:
        if self._is_capturing():
            self._capture_cancel.set()
            self.capture_button.setText(self._text("stop_capture"))
            self.statusBar().showMessage(self._text("capture_stopping"))
            self._write_run_log("Seed 捕捉", f"已请求停止{self._capture_mode_label()}", level="WARNING")
            return
        try:
            config = self._config_from_form()
            config = replace(config, capture=replace(config.capture, blink_count=TIDSID_BLINK_COUNT))
        except ProjectXsIntegrationError as exc:
            self._show_error("TID/SID capture failed", exc)
            return
        if not self._ensure_preview_frame_before_capture():
            return

        self._pause_preview_for_capture()
        self.preview_button.setEnabled(False)
        self.reidentify_button.setEnabled(False)
        self.tidsid_button.setEnabled(False)
        self._set_preview_selection_enabled(False)
        self._stop_advance_tracking()
        self._capture_cancel.clear()
        self._capture_result = None
        self._capture_error = None
        self._capture_mode = "tidsid"
        self._capture_progress = (0, config.capture.blink_count)
        with self._capture_lock:
            self._capture_frame = None
        self.progress_value.setText(f"0/{config.capture.blink_count}")
        self.capture_button.setText(self._text("stop_capture"))
        self.statusBar().showMessage(f"Capturing {config.capture.blink_count} Pokemon blinks...")
        self._write_run_log(
            "Seed 捕捉",
            f"开始 TID/SID Seed 捕捉；眨眼数 {config.capture.blink_count}",
        )

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

        store_progress = self._wrap_capture_progress_with_keep_awake(store_progress)

        def run_tidsid() -> None:
            try:
                observation = capture_pokemon_blinks(
                    config.capture,
                    should_stop=self._capture_cancel.is_set,
                    frame_callback=store_frame,
                    progress_callback=store_progress,
                    show_window=False,
                )
                self._capture_result = recover_tidsid_seed_from_observation(observation)
                with self._capture_lock:
                    self._capture_progress = (config.capture.blink_count, config.capture.blink_count)
            except Exception as exc:  # pragma: no cover - exercised through UI polling
                self._capture_error = exc if isinstance(exc, Exception) else Exception(str(exc))

        self._capture_thread = threading.Thread(target=run_tidsid, daemon=True)
        self._capture_thread.start()
        self._capture_timer.start()

    def _poll_capture_thread(self) -> None:
        with self._capture_lock:
            frame = self._capture_frame
            self._capture_frame = None
            done, total = self._capture_progress
        if frame is not None:
            if self._video_source_connected:
                self._refresh_preview_presentation()
            else:
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
        self.tidsid_button.setEnabled(True)
        self.capture_button.setText(self._text("capture_seed"))
        self._restore_preview_after_capture()
        if self._capture_error is not None:
            if self._capture_cancel.is_set():
                self.statusBar().showMessage(self._text("capture_stopped"))
                self._write_run_log("Seed 捕捉", f"{self._capture_mode_label()}已停止", level="WARNING")
            else:
                title = (
                    "校正失败"
                    if self._capture_mode == "reidentify"
                    else "TID/SID capture failed"
                    if self._capture_mode == "tidsid"
                    else "Blink capture failed"
                )
                self._show_error(title, self._capture_error, source="Seed 捕捉")
            return

        result = self._capture_result
        if result is None:
            self.statusBar().showMessage(self._text("capture_stopped"))
            self._write_run_log("Seed 捕捉", f"{self._capture_mode_label()}未返回结果", level="WARNING")
            return
        # reidentify 不修改 seed，只更新 current_advances
        if self._capture_mode != "reidentify":
            for box, text in zip(self.seed32_inputs, result.state.format_words()):
                box.setText(text)
            self._sync_seed64_from_state32()
        self.progress_value.setText(f"{total}/{total}")
        initial_advances = getattr(result, "advances", 0) if self._capture_mode == "reidentify" else 0
        if self._capture_mode == "tidsid":
            self.auto_tid_rng_tab.set_tid_seed(result.state)
            self._advance_step = 1
            self._advance_counter = ProjectXsMunchlaxAdvanceCounter()
            self._advance_counter.reset(current_advances=0, seed=result.state, now=time.monotonic())
        else:
            self._advance_step = int(self.npc_count.text() or 0) + 1
            self._advance_counter = ProjectXsAdvanceCounter()
            self._advance_counter.reset(
                current_advances=initial_advances,
                npc=max(0, self._advance_step - 1),
                now=time.monotonic(),
            )
        self._tracked_advances = self._advance_counter.current_advances
        self.advances_value.setText("0")
        self.timer_value.setText("0")
        self._schedule_advance_timer()
        if self._capture_mode == "reidentify":
            self.advances_value.setText(str(self._tracked_advances))
            self.statusBar().showMessage(self._text("seed_reidentified"))
            self._write_run_log("Seed 捕捉", f"校正完成；当前 Adv {self._tracked_advances}")
        elif self._capture_mode == "tidsid":
            self.statusBar().showMessage("TID/SID 测种完成")
            self._write_run_log(
                "Seed 捕捉",
                f"TID/SID Seed 捕捉完成；Seed {' '.join(result.state.format_words())}",
            )
        else:
            self.statusBar().showMessage(self._text("seed_captured"))
            self._write_run_log(
                "Seed 捕捉",
                f"普通 Seed 捕捉完成；Seed {' '.join(result.state.format_words())}",
            )

    def generate_results(self) -> None:
        try:
            record = self.encounter_combo.currentData()
            if record is None:
                raise ValueError("Select a static encounter")
            state_filter, shiny_mode = self._current_filter()
            criteria = StaticSearchCriteria(
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
        except Exception as exc:
            self._show_error("Generation failed", exc)
            return
        if criteria.max_advances >= 1_000_000:
            self._start_static_generation(record, criteria)
            return
        try:
            states = generate_static_candidates(criteria)
        except Exception as exc:
            self._show_error("Generation failed", exc)
            return
        self._finish_static_generation(record, states)

    def _start_static_generation(self, record: StaticEncounterRecord, criteria: StaticSearchCriteria) -> None:
        if self._static_generation_thread is not None and self._static_generation_thread.is_alive():
            self.statusBar().showMessage("Static generation is already running")
            return
        self._static_generation_result = None
        self._static_generation_error = None

        def run() -> None:
            try:
                states = generate_static_candidates(criteria)
            except Exception as exc:
                self._static_generation_error = exc
            else:
                self._static_generation_result = (record, states)

        thread = threading.Thread(target=run, name="static-generation", daemon=True)
        self._static_generation_thread = thread
        self.generate_button.setEnabled(False)
        self.statusBar().showMessage("Generating static results...")
        self._static_generation_timer.start()
        thread.start()

    def _poll_static_generation_thread(self) -> None:
        thread = self._static_generation_thread
        if thread is None or thread.is_alive():
            return
        self._static_generation_timer.stop()
        self._static_generation_thread = None
        self.generate_button.setEnabled(True)
        if self._static_generation_error is not None:
            error = self._static_generation_error
            self._static_generation_error = None
            self._show_error("Generation failed", error)
            return
        result = self._static_generation_result
        self._static_generation_result = None
        if result is None:
            self._show_error("Generation failed", RuntimeError("Static generation finished without results"))
            return
        record, states = result
        self._finish_static_generation(record, states)

    def _finish_static_generation(self, record: StaticEncounterRecord, states: list[State8]) -> None:
        self._states = states
        self._active_record = record
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

    def _show_error(
        self,
        title: str,
        error: object,
        *,
        source: str = "应用",
        write_log: bool = True,
    ) -> None:
        if write_log:
            self._write_run_log(source, f"{title}: {error}", level="ERROR")
        if isinstance(error, ProjectXsIntegrationError):
            display_title, display_message = _project_xs_capture_error_dialog(
                error,
                fallback_title=title,
            )
        else:
            display_title, display_message = title, str(error)
        QMessageBox.critical(self, display_title, display_message)
        self.statusBar().showMessage(display_message)




def _compute_iv_range(base_stats, stats, levels, nature, characteristic, hidden_power):
    """基于 PokeFinder IVChecker 算法计算个体值范围"""
    iv_order = [0, 1, 2, 5, 3, 4]

    try:
        from auto_bdsp_rng.rng_core._native import compute_iv_ranges as _native_iv_ranges
    except ImportError:
        _native_iv_ranges = None

    def _calc_single(bs, st, lv, nat, charac):
        if _native_iv_ranges is not None:
            ranges = _native_iv_ranges(list(bs), list(st), nat, lv)
            min_ivs = [max(0, r[0]) for r in ranges]
            max_ivs = [min(31, r[1]) for r in ranges]
        else:
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
        names_path = resource_path("third_party", "PokeFinder", "Core", "Resources", "i18n", "zh", "species_zh.txt")
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
            set_c_locale(wgt)
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


def create_window(run_log_manager: RunLogManager | None = None) -> MainWindow:
    return MainWindow(run_log_manager=run_log_manager)


def run() -> int:
    app = QApplication.instance() or QApplication([])
    configure_application_identity(app)
    run_log_manager = RunLogManager()
    run_log_errors: list[str] = []
    run_log_manager.set_error_callback(run_log_errors.append)
    run_log_manager.cleanup()
    run_log_startup_error: str | None = None
    run_log_requested = is_run_log_enabled()
    if run_log_requested:
        try:
            run_log_manager.enable()
        except RunLogError as exc:
            run_log_startup_error = str(exc)

    exception_hooks = ExceptionHookGuard(run_log_manager).install()
    if run_log_manager.enabled:
        run_log_manager.write(
            "应用",
            f"应用启动；版本 {__version__}；模式 "
            f"{'打包版' if getattr(sys, 'frozen', False) else '源码版'}",
        )
    if run_log_requested and not run_log_manager.enabled:
        if run_log_startup_error is None:
            run_log_startup_error = run_log_errors[-1] if run_log_errors else "运行日志已意外停用"
        try:
            set_run_log_enabled(False)
        except OSError as exc:
            run_log_startup_error += f"\n同时无法保存关闭状态，下次启动可能再次尝试：{exc}"

    legacy_script_backups: tuple[Path, ...] = ()
    legacy_script_migration_error: str | None = None
    packaged_install_dir = app_base_dir()
    if getattr(sys, "frozen", False) and not has_uncommitted_update_transaction(
        packaged_install_dir
    ):
        try:
            legacy_script_backups = migrate_legacy_internal_scripts(
                packaged_install_dir,
                log=lambda message: run_log_manager.write("脚本目录迁移", message),
            )
        except UpdatePackageError as exc:
            legacy_script_migration_error = str(exc)
            run_log_manager.write(
                "脚本目录迁移",
                f"旧版内部脚本清理失败：{legacy_script_migration_error}",
                level="ERROR",
            )

    shutdown_run_log_errors: list[str] = []
    try:
        window = create_window(run_log_manager=run_log_manager)
        if run_log_startup_error is not None:
            QTimer.singleShot(
                0,
                lambda message=run_log_startup_error: window.show_run_log_startup_error(message),
            )
        if legacy_script_backups:
            QTimer.singleShot(
                0,
                lambda count=len(legacy_script_backups): window.show_legacy_script_migration_result(count),
            )
        if legacy_script_migration_error is not None:
            QTimer.singleShot(
                0,
                lambda message=legacy_script_migration_error: window.show_legacy_script_migration_error(message),
            )
        window.show()
        schedule_update_check = getattr(window, "schedule_startup_update_check", None)
        if is_auto_update_check_enabled() and callable(schedule_update_check):
            schedule_update_check()
        exit_code = app.exec()
        run_log_manager.set_error_callback(shutdown_run_log_errors.append)
        run_log_manager.write("应用", f"应用正常退出；退出码 {exit_code}")
        return exit_code
    except BaseException:
        run_log_manager.set_error_callback(shutdown_run_log_errors.append)
        exc_type, exc_value, tb = sys.exc_info()
        if exc_type is not None and exc_value is not None:
            run_log_manager.write_exception("应用启动或事件循环异常", exc_type, exc_value, tb)
        raise
    finally:
        run_log_manager.set_error_callback(shutdown_run_log_errors.append)
        run_log_manager.close()
        if shutdown_run_log_errors:
            try:
                set_run_log_enabled(False)
            except OSError:
                pass
        exception_hooks.restore()
