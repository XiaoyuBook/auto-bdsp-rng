"""Small native controls shared by the confirmed workspace and device dialogs."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QIconEngine, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QStyle, QStyleOptionToolButton,
    QStylePainter, QToolButton, QVBoxLayout, QWidget,
)


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
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
}


class _LineIconEngine(QIconEngine):
    def __init__(self, name: str, color: str) -> None:
        super().__init__()
        self.name, self.color = name, color

    def clone(self) -> QIconEngine:
        return _LineIconEngine(self.name, self.color)

    def paint(self, painter, rect, mode, state) -> None:
        color = "#9AA9A2" if mode == QIcon.Mode.Disabled and self.color != "#FFFFFF" else self.color
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
            f'fill="none" stroke="{color}" stroke-width="1.7" '
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


def workspace_icon(name: str, color: str = "#68766F") -> QIcon:
    return QIcon(_LineIconEngine(name, color))


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
        painter.setPen(QColor("#24312D" if self.isEnabled() else "#9AA9A2"))
        painter.drawText(QRect(33, 0, 38, self.height()), Qt.AlignmentFlag.AlignVCenter, self.device_name)
        color = {
            "connected": "#087C58", "connecting": "#B7791F", "failed": "#B4443C",
        }.get(self.property("state"), "#68766F")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color if self.property("state") != "disconnected" else "#9AA9A2"))
        painter.drawEllipse(QPointF(82, self.height() / 2), 3, 3)
        painter.setPen(QColor(color if self.isEnabled() else "#9AA9A2"))
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
    """Opaque draggable dialog with the mockup's title, form and action footer."""

    def __init__(self, title: str, object_name: str, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setWindowTitle(title)
        self.setModal(False)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.setFixedWidth(490)
        self.setStyleSheet(f"""
            QDialog#{object_name} {{ background: white; border: 1px solid #E2E8E4; border-radius: 8px; }}
            QDialog#{object_name} QWidget {{ font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 13px; color: #24312D; }}
            QDialog#{object_name} QLabel {{ background: transparent; border: 0; }}
            QDialog#{object_name} QFrame#ConnectionTitleBar {{ background: white; border: 0; border-bottom: 1px solid #E2E8E4; }}
            QDialog#{object_name} QLabel#ConnectionTitle {{ font-size: 16px; font-weight: 500; }}
            QDialog#{object_name} QFrame#ConnectionFooter {{ background: white; border: 0; border-top: 1px solid #E2E8E4; }}
            QDialog#{object_name} QComboBox, QDialog#{object_name} QPushButton {{ background: white; border: 1px solid #E2E8E4; border-radius: 5px; min-height: 30px; max-height: 30px; padding: 0 12px; }}
            QDialog#{object_name} QComboBox {{ padding-right: 32px; }}
            QDialog#{object_name} QComboBox::drop-down {{ border: 0; width: 28px; }}
            QDialog#{object_name} QComboBox::down-arrow {{ image: none; }}
            QDialog#{object_name} QComboBox:focus {{ border-color: #087C58; }}
            QDialog#{object_name} QPushButton:hover {{ background: #F6F8F7; }}
            QDialog#{object_name} QPushButton#PrimaryButton {{ background: #087C58; color: white; border-color: #087C58; }}
            QDialog#{object_name} QPushButton#PrimaryButton:hover {{ background: #066A4B; }}
            QDialog#{object_name} QPushButton#PrimaryButton[disconnect="true"] {{ background: white; color: #AC4B42; border-color: #D9AAA6; }}
            QDialog#{object_name} QPushButton#PrimaryButton[disconnect="true"]:hover {{ background: #FFF7F6; }}
            QDialog#{object_name} QPushButton#PrimaryButton:disabled, QDialog#{object_name} QPushButton:disabled, QDialog#{object_name} QComboBox:disabled {{ background: #F6F8F7; color: #9AA9A2; border-color: #E2E8E4; }}
            QDialog#{object_name} QToolButton {{ background: white; border: 1px solid #E2E8E4; border-radius: 5px; padding: 0; }}
            QDialog#{object_name} QToolButton#ConnectionClose {{ border: 0; }}
            QDialog#{object_name} QToolButton:hover {{ background: #F6F8F7; }}
            QDialog#{object_name} QMenu {{ background: white; border: 1px solid #E2E8E4; padding: 4px; }}
            QDialog#{object_name} QMenu::item {{ padding: 7px 28px 7px 10px; }}
            QDialog#{object_name} QMenu::item:selected {{ background: #EDF7F1; color: #087C58; }}
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


def set_disconnect_action(button, disconnect: bool) -> None:
    if button.property("disconnect") != disconnect:
        button.setProperty("disconnect", disconnect)
        button.style().unpolish(button)
        button.style().polish(button)
        button.update()
