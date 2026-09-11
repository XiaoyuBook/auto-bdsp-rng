"""Small native controls shared by the confirmed workspace and device dialogs."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QEasingCurve, QPointF, QRect, QRectF, QSize, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QIcon, QIconEngine, QPainter, QPen, QPixmap, QRegion
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QStyle, QStyleOptionToolButton,
    QStylePainter, QToolButton, QVBoxLayout, QWidget,
)

from auto_bdsp_rng.resources import resource_path
from auto_bdsp_rng.ui.workspace_theme import primary_button_styles, ui_font, ui_styles


_SYMBOLS = {
    "video": '<rect x="3" y="6" width="12" height="12" rx="2"/><path d="m15 10 6-4v12l-6-4"/>',
    "controller": '<path d="M7 7h10c2 0 3 2 3.5 4l1 6c.4 2-1.5 3-3 1l-2-2h-9l-2 2c-1.5 2-3.4 1-3-1l1-6C4 9 5 7 7 7Z"/><path d="M6 11h4m-2-2v4m8-3h.01M18 13h.01"/>',
    "help": '<circle cx="12" cy="12" r="9"/><path d="M9 9a3 3 0 0 1 6 0c0 2-3 2-3 4m0 3h.01"/>',
    "close": '<path d="m6 6 12 12M6 18 18 6"/>',
    "refresh": '<path d="M20 7v5h-5M4 17v-5h5M6 7a7 7 0 0 1 12-1l2 3M4 15l2 3a7 7 0 0 0 12-1"/>',
    "open": '<path d="M3 18V6a2 2 0 0 1 2-2h4l3 3h7a2 2 0 0 1 2 2v2M3 18l3-7h16l-3 8H5a2 2 0 0 1-2-1Z"/>',
    "new": '<path d="M14 2H5v20h14V7l-5-5v5h5M8 14h8m-4-4v8"/>',
    "save": '<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h12l4 4v12a2 2 0 0 1-2 2ZM7 3v6h9V3M7 21v-8h10v8"/>',
    "play": '<path d="m8 4 12 8-12 8Z"/>',
    "stop": '<rect x="5" y="5" width="14" height="14" rx="1"/>',
    "record": '<circle cx="12" cy="12" r="7"/>',
    "pause": '<path d="M8 5v14M16 5v14"/>',
    "external": '<path d="M7 17 17 7M7 7h10v10"/>',
    "locate-fixed": '<circle cx="12" cy="12" r="7"/><circle cx="12" cy="12" r="3"/><path d="M12 1v2m0 18v2M1 12h2m18 0h2"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "mail": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 6 9 7 9-7"/>',
    "tv": '<rect x="3" y="7" width="18" height="14" rx="3"/><path d="m8 3 4 4 4-4M8 12v3m8-3v3"/>',
    "github": '<path d="M9 19c-4 1-4-2-6-2m6 5v-4c-4-1-6-3-6-6 0-2 1-4 3-5V3l4 2h4l4-2v4c2 1 3 3 3 5 0 3-2 5-6 6v4"/>',
    "journal": '<rect x="5" y="3" width="15" height="18" rx="2"/><path d="M3 7h4M3 12h4M3 17h4m3-9h6m-6 4h6m-6 4h3"/>',
    "viewfinder": '<path d="M8 3H5a2 2 0 0 0-2 2v3m13-5h3a2 2 0 0 1 2 2v3M3 16v3a2 2 0 0 0 2 2h3m8 0h3a2 2 0 0 0 2-2v-3"/><rect x="7" y="8" width="10" height="8" rx="2"/><circle cx="12" cy="12" r="2"/>',
}


_DELAY_SYMBOLS = {
    "settings-2": (
        '<path d="M20 7h-9"/><path d="M14 17H5"/>'
        '<circle cx="17" cy="17" r="3"/><circle cx="7" cy="7" r="3"/>'
    ),
    "x": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    "chevron-right": '<path d="m9 18 6-6-6-6"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "arrow-up-right": '<path d="M7 7h10v10"/><path d="M7 17 17 7"/>',
    "arrow-left": '<path d="m12 19-7-7 7-7"/><path d="M19 12H5"/>',
    "square-pen": (
        '<path d="M12 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>'
        '<path d="M18.375 2.625a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4Z"/>'
    ),
    "trash-2": (
        '<path d="M3 6h18"/><path d="M19 6l-1 14H6L5 6"/>'
        '<path d="M8 6V4h8v2"/><path d="M10 11v6"/><path d="M14 11v6"/>'
    ),
    "rotate-ccw": (
        '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/>'
    ),
}
_SYMBOLS.update(_DELAY_SYMBOLS)
_SYMBOLS["empty"] = '<circle cx="10" cy="10" r="6"/><path d="m15 15 5 5M7 10h6m-3-3v6"/>'
_SYMBOLS["pokeball"] = '<circle cx="12" cy="12" r="9"/><path d="M3 12h6m6 0h6"/><circle cx="12" cy="12" r="3"/>'


class _LineIconEngine(QIconEngine):
    def __init__(self, name: str, color: str) -> None:
        super().__init__()
        self.name, self.color = name, color

    def clone(self) -> QIconEngine:
        return _LineIconEngine(self.name, self.color)

    def paint(self, painter, rect, mode, state) -> None:
        color = "#97A1AB" if mode == QIcon.Mode.Disabled else self.color
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
            f'fill="none" stroke="{color}" stroke-width="1.8" '
            'stroke-linecap="round" stroke-linejoin="round">'
            + _SYMBOLS[self.name] + "</svg>"
        )
        QSvgRenderer(QByteArray(svg.encode("ascii"))).render(painter, QRectF(rect))

    def pixmap(self, size, mode, state) -> QPixmap:
        pixmap = QPixmap(size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        self.paint(painter, QRect(0, 0, size.width(), size.height()), mode, state)
        painter.end()
        return pixmap


def workspace_icon(name: str, color: str = "#687480") -> QIcon:
    return QIcon(_LineIconEngine(name, color))


class EmptyIllustration(QWidget):
    """Decorative vector artwork; never intercepts the underlying UI's input."""

    def __init__(self, symbol: str = "journal", parent=None) -> None:
        super().__init__(parent)
        self.symbol = symbol
        self.setFixedSize(56, 56)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#F4F6F8"))
        painter.drawRoundedRect(QRectF(0, 0, 56, 56), 16, 16)
        workspace_icon(self.symbol, "#93A1AE").paint(painter, QRect(10, 10, 36, 36))
        painter.setBrush(QColor("#81B5A0"))
        painter.drawEllipse(QPointF(45, 11), 3, 3)


class SpeciesAvatar(QWidget):
    """Decorative species art, independent of the target selection model."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(36, 36)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._sprite = QPixmap()

    def set_species(self, species: int | None, name: str = "") -> None:
        self.setProperty("speciesId", species)
        self.setAccessibleName(name or "目标精灵")
        self._sprite = QPixmap(str(resource_path("docs", "assets", "pokemon", f"{species}.png"))) if species else QPixmap()
        if not self._sprite.isNull():
            bounds = QRegion(self._sprite.mask()).boundingRect()
            if not bounds.isEmpty():
                self._sprite = self._sprite.copy(bounds)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#EAF7F1"))
        painter.drawRoundedRect(QRectF(self.rect()), 10, 10)
        if self._sprite.isNull():
            workspace_icon("pokeball", "#087C58").paint(painter, self.rect().adjusted(8, 8, -8, -8))
        else:
            size = self._sprite.size().scaled(30, 30, Qt.AspectRatioMode.KeepAspectRatio)
            rect = QRect((36 - size.width()) // 2, (36 - size.height()) // 2, size.width(), size.height())
            painter.drawPixmap(rect, self._sprite)


class _ButtonMotion:
    """Short paint-only feedback; native clicks, menus and enabled state remain intact."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._highlight = 0.0
        self._hover_animation = QVariantAnimation(self)
        self._hover_animation.setDuration(140)
        self._hover_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._hover_animation.valueChanged.connect(self._set_highlight)

    def _set_highlight(self, value) -> None:
        self._highlight = float(value)
        self.update()

    def _animate_highlight(self, value: float) -> None:
        self._hover_animation.stop()
        self._hover_animation.setStartValue(self._highlight)
        self._hover_animation.setEndValue(value)
        self._hover_animation.start()

    def enterEvent(self, event) -> None:  # noqa: N802
        super().enterEvent(event)
        self._animate_highlight(1.0)

    def leaveEvent(self, event) -> None:  # noqa: N802
        super().leaveEvent(event)
        self._animate_highlight(0.0)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._hover_animation.stop()
        self._highlight = 0.0
        super().hideEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if self.isEnabled() and not self.isDown() and self._highlight:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, round(15 * self._highlight)))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 6, 6)


class PrimaryButton(_ButtonMotion, QPushButton):
    pass


class PrimaryToolButton(_ButtonMotion, QToolButton):
    pass


class DeviceStatusButton(QToolButton):
    """Keep the device name, status dot and state readable at a stable width."""

    def __init__(self, name: str, symbol: str) -> None:
        super().__init__()
        self.device_name = name
        self.setIcon(workspace_icon(symbol))
        self.setIconSize(QSize(16, 16))
        self.setFixedSize(150, 32)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.set_status(f"{name} 未连接", "disconnected")

    def set_status(self, summary: str, state: str) -> None:
        # Preserve the complete text for accessibility and existing callers.
        self.setText(summary)
        self.setProperty("state", state)
        self.status_text = {
            "connected": "已连接", "connecting": "连接中",
            "failed": "故障", "disconnected": "未连接",
        }.get(state, "未连接")
        if state == "connecting" and "断开" in summary:
            self.status_text = "断开中"
        self.setAccessibleName(f"{self.device_name} {self.status_text}")
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QStylePainter(self)
        option = QStyleOptionToolButton()
        self.initStyleOption(option)
        option.text, option.icon = "", QIcon()
        painter.drawComplexControl(QStyle.ComplexControl.CC_ToolButton, option)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        mode = QIcon.Mode.Normal if self.isEnabled() else QIcon.Mode.Disabled
        self.icon().paint(painter, QRect(10, (self.height() - 16) // 2, 16, 16), mode=mode)
        font = self.font()
        font.setPixelSize(12)
        painter.setFont(font)
        painter.setPen(QColor("#202A33" if self.isEnabled() else "#97A1AB"))
        painter.drawText(QRect(33, 0, 38, self.height()), Qt.AlignmentFlag.AlignVCenter, self.device_name)
        color = {
            "connected": "#087C58", "connecting": "#B7791F", "failed": "#B4443C",
        }.get(self.property("state"), "#687480")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color if self.property("state") != "disconnected" else "#97A1AB"))
        painter.drawEllipse(QPointF(82, self.height() / 2), 3, 3)
        painter.setPen(QColor(color if self.isEnabled() else "#97A1AB"))
        painter.drawText(QRect(91, 0, self.width() - 99, self.height()), Qt.AlignmentFlag.AlignVCenter, self.status_text)


class _ConnectionTitleBar(QFrame):
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
        super().mousePressEvent(event)


class ConnectionDialog(QDialog):
    """Draggable dialog with a continuous rounded surface and transparent corners."""

    def __init__(self, title: str, object_name: str, parent: QWidget) -> None:
        super().__init__(parent)
        self.setFont(ui_font())
        self.setObjectName(object_name)
        self.setWindowTitle(title)
        self.setModal(False)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(490)
        self.setStyleSheet(ui_styles(f"""
            QDialog#{object_name} {{ background: transparent; border: 0; }}
            QDialog#{object_name} QWidget {{ font-family: "MiSans", "Noto Sans SC", "Source Han Sans SC", "Noto Sans CJK SC", "Microsoft YaHei UI", "PingFang SC", "Segoe UI", sans-serif; font-size: 13px; color: #202A33; }}
            QDialog#{object_name} QLabel {{ background: transparent; border: 0; }}
            QDialog#{object_name} QFrame#ConnectionTitleBar {{ background: transparent; border: 0; border-bottom: 1px solid #E0E5EB; }}
            QDialog#{object_name} QLabel#ConnectionTitle {{ font-size: 16px; font-weight: 500; }}
            QDialog#{object_name} QFrame#ConnectionFooter {{ background: transparent; border: 0; border-top: 1px solid #E0E5EB; }}
            QDialog#{object_name} QComboBox, QDialog#{object_name} QPushButton {{ background: white; border: 1px solid #E0E5EB; border-radius: 7px; min-height: 30px; max-height: 30px; padding: 0 12px; }}
            QDialog#{object_name} QComboBox {{ padding-right: 32px; }}
            QDialog#{object_name} QComboBox::drop-down {{ border: 0; width: 28px; }}
            QDialog#{object_name} QComboBox::down-arrow {{ image: none; }}
            QDialog#{object_name} QComboBox:focus {{ border-color: #087C58; }}
            QDialog#{object_name} QPushButton:hover {{ background: #F7F8FA; }}
            QDialog#{object_name} QPushButton#PrimaryButton:disabled, QDialog#{object_name} QPushButton:disabled, QDialog#{object_name} QComboBox:disabled {{ background: #F7F8FA; color: #97A1AB; border-color: #E0E5EB; }}
            QDialog#{object_name} QToolButton {{ background: white; border: 1px solid #E0E5EB; border-radius: 7px; padding: 0; }}
            QDialog#{object_name} QToolButton#ConnectionClose {{ border: 0; }}
            QDialog#{object_name} QToolButton:hover {{ background: #F7F8FA; }}
            QDialog#{object_name} QMenu {{ background: white; border: 1px solid #E0E5EB; padding: 4px; }}
            QDialog#{object_name} QMenu::item {{ padding: 7px 28px 7px 10px; }}
            QDialog#{object_name} QMenu::item:selected {{ background: #EAF7F1; color: #087C58; }}
        """))
        self.setStyleSheet(self.styleSheet() + primary_button_styles(
            f'QDialog#{object_name} QPushButton#PrimaryButton'
        ) + f"""
            QDialog#{object_name} QPushButton#PrimaryButton[disconnect="true"] {{ background: white; color: #AC4B42; border-color: #D9AAA6; }}
            QDialog#{object_name} QPushButton#PrimaryButton[disconnect="true"]:hover {{ background: #FFF7F6; }}
            QDialog#{object_name} QPushButton#PrimaryButton[disconnect="true"]:pressed {{ background: #FFE9E5; border-color: #AC4B42; }}
            QDialog#{object_name} QPushButton#PrimaryButton[disconnect="true"]:disabled {{ background: #EDF0F3; color: #929CA6; border-color: #E0E5EB; }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        self.title_bar = _ConnectionTitleBar()
        self.title_bar.setObjectName("ConnectionTitleBar")
        self.title_bar.setFixedHeight(56)
        title_layout = QHBoxLayout(self.title_bar)
        title_layout.setContentsMargins(20, 0, 14, 0)
        label = QLabel(title)
        label.setObjectName("ConnectionTitle")
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        title_layout.addWidget(label)
        title_layout.addStretch(1)
        self.close_button = QToolButton()
        self.close_button.setObjectName("ConnectionClose")
        self.close_button.setIcon(workspace_icon("close"))
        self.close_button.setAccessibleName("关闭")
        self.close_button.setToolTip("关闭")
        self.close_button.setFixedSize(30, 30)
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.clicked.connect(self.hide)
        title_layout.addWidget(self.close_button)
        layout.addWidget(self.title_bar)
        self.body_layout = QVBoxLayout()
        self.body_layout.setContentsMargins(20, 18, 20, 18)
        self.body_layout.setSpacing(10)
        layout.addLayout(self.body_layout)
        footer = QFrame()
        footer.setObjectName("ConnectionFooter")
        self.footer_layout = QHBoxLayout(footer)
        self.footer_layout.setContentsMargins(20, 13, 20, 13)
        self.footer_layout.setSpacing(8)
        self.footer_layout.addStretch(1)
        layout.addWidget(footer)

    def paintEvent(self, event) -> None:  # noqa: N802
        # Child title/footer frames remain transparent, so all four corners belong
        # to this one antialiased surface (QSS radii alone do not clip a window).
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#E0E5EB"), 1))
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 14, 14)


def set_disconnect_action(button, disconnect: bool) -> None:
    if button.property("disconnect") != disconnect:
        button.setProperty("disconnect", disconnect)
        button.style().unpolish(button)
        button.style().polish(button)
        button.update()
