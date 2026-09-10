"""Small shared design tokens for native Qt workspaces (logical pixels)."""

import logging
from string import Template

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication
from PySide6.QtWidgets import QGraphicsDropShadowEffect

from auto_bdsp_rng.resources import resource_path

BACKGROUND = "#F2F4F7"
SURFACE = "#FFFFFF"
SURFACE_MUTED = "#F7F8FA"
TEXT = "#202A33"
TEXT_SECONDARY = "#626D79"
BORDER = "#E0E5EB"
CARD_BORDER = "#E9EDF2"
SEPARATOR = "#F0F2F5"
ACCENT = "#087C58"
ACCENT_SOFT = "#EAF7F1"
WARNING = "#906423"
WARNING_SOFT = "#FFF7E8"
ERROR = "#AC4B42"
ERROR_SOFT = "#FFF4F1"
FOCUS = "#176B97"
CONTROL_HEIGHT = 32
CONTROL_RADIUS = 7
BUNDLED_FONT_FAMILY = "BDSP UI Sans"
BUNDLED_FONT_FILES = ("BDSPUISans-Regular.otf", "BDSPUISans-Medium.otf")
UI_FONT_FAMILIES = (
    BUNDLED_FONT_FAMILY,
    "Noto Sans SC", "Source Han Sans SC", "Noto Sans CJK SC",
    "Microsoft YaHei UI", "PingFang SC", "Segoe UI", "sans-serif",
)
RUNTIME_GRADIENT = "qlineargradient(x1:0, y1:1, x2:1, y2:0, stop:0 #FFFFFF, stop:0.65 #FFFFFF, stop:1 #F0F9F5)"


def ensure_ui_fonts() -> tuple[int, ...]:
    """Register packaged faces once per Qt application, without installing OS fonts."""
    app = QGuiApplication.instance()
    if app is None:
        return ()
    if not hasattr(app, "_bdsp_ui_font_ids"):
        font_ids = []
        for filename in BUNDLED_FONT_FILES:
            path = resource_path("docs", "assets", "fonts", filename)
            font_id = QFontDatabase.addApplicationFont(str(path))
            if font_id < 0:
                logging.getLogger(__name__).warning("Unable to load UI font %s; using font fallback", path)
            else:
                font_ids.append(font_id)
        app._bdsp_ui_font_ids = tuple(font_ids)
    return app._bdsp_ui_font_ids


def ui_font(pixel_size: int = 14, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    ensure_ui_fonts()
    font = QFont()
    font.setFamilies(list(UI_FONT_FAMILIES))
    font.setStyleHint(QFont.StyleHint.SansSerif)
    font.setPixelSize(pixel_size)
    font.setWeight(weight)
    font.setFeature(QFont.Tag("tnum"), 1)
    return font


def primary_button_styles(*selectors: str) -> str:
    """Native QSS surfaces; disabled and pressed states keep their own contrast."""
    def rule(state: str, body: str) -> str:
        return ", ".join(selector + state for selector in selectors) + " { " + body + " }\n"

    return (
        rule("", "background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #10956B, stop:1 #087C58); "
             "color: #FFFFFF; border: 1px solid #087C58; border-top-color: #36A883; border-radius: 7px;")
        + rule(":hover", "background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #119E72, stop:1 #07845D);")
        + rule(":pressed", "background: #066A4B; border-color: #055B41;")
        + rule(":disabled", "background: #EDF0F3; color: #929CA6; border-color: #E0E5EB;")
    )


class _CardShadow(QObject):
    """Qt effect radii/offsets are device pixels; scale them with the window."""

    def __init__(self, widget) -> None:
        super().__init__(widget)
        self.widget = widget
        self.effect = QGraphicsDropShadowEffect(widget)
        self.effect.setColor(QColor(26, 42, 56, 14))
        widget.setGraphicsEffect(self.effect)
        widget.installEventFilter(self)
        self._sync()

    def _sync(self) -> None:
        ratio = self.widget.devicePixelRatioF()
        self.effect.setBlurRadius(20 * ratio)
        self.effect.setOffset(0, 3 * ratio)

    def eventFilter(self, watched, event) -> bool:
        if event.type() in (QEvent.Type.Show, QEvent.Type.DevicePixelRatioChange):
            self._sync()
        return False


def add_card_shadow(widget) -> None:
    if not hasattr(widget, "_card_shadow"):
        widget._card_shadow = _CardShadow(widget)


def workspace_styles(template: str) -> str:
    """Resolve shared colors without interfering with QSS block braces."""
    return Template(template).substitute(
        background=BACKGROUND, surface=SURFACE, surface_muted=SURFACE_MUTED, text=TEXT,
        text_secondary=TEXT_SECONDARY, border=BORDER, card_border=CARD_BORDER,
        separator=SEPARATOR, accent=ACCENT,
        accent_soft=ACCENT_SOFT, warning=WARNING, warning_soft=WARNING_SOFT,
        error=ERROR, error_soft=ERROR_SOFT, runtime_gradient=RUNTIME_GRADIENT,
    )


def focus_styles(*selectors: str) -> str:
    """Keep input borders at one pixel and add an inset outline on buttons."""
    return ",\n".join(f"{selector}:focus" for selector in selectors) + (
        f" {{ border: 1px solid {FOCUS}; outline: 2px dotted {FOCUS}; outline-offset: -3px; }}"
    )
