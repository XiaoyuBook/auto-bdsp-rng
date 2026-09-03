from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path
from time import monotonic

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPaintEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from auto_bdsp_rng.automation.easycon.native.device import SwitchButton, SwitchHat, SwitchReport


ReportProvider = Callable[[], object | None]
StateProvider = Callable[[], bool]

_ASSET_DIR = Path(__file__).resolve().parent / "vpad_assets"
_SHOULDER_ASSETS = (
    (SwitchButton.ZL, "JoyCon_ZL"),
    (SwitchButton.ZR, "JoyCon_ZR"),
    (SwitchButton.L, "JoyCon_L"),
    (SwitchButton.R, "JoyCon_R"),
)
_WINDOWS_FRAME_ATTRIBUTES = (
    (2, 1),  # DWMWA_NCRENDERING_POLICY = DWMNCRP_DISABLED
    (33, 1),  # DWMWA_WINDOW_CORNER_PREFERENCE = DWMWCP_DONOTROUND
    (34, 0xFFFFFFFE),  # DWMWA_BORDER_COLOR = DWMWA_COLOR_NONE
)


def _report_value(report: object, name: str, default: int) -> int:
    value = default
    for candidate in (name, name.upper(), name.capitalize()):
        if hasattr(report, candidate):
            value = getattr(report, candidate)
            break
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _load_asset(name: str) -> QPixmap:
    path = _ASSET_DIR / f"{name}.png"
    pixmap = QPixmap(str(path))
    if pixmap.isNull():
        raise RuntimeError(f"Cannot load EasyCon VPad asset: {path}")
    return pixmap


def _load_background_asset() -> QPixmap:
    image = _load_asset("JoyCon").toImage().convertToFormat(QImage.Format.Format_ARGB32)
    transparent = QColor(Qt.GlobalColor.transparent)
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            if min(color.red(), color.green(), color.blue()) >= 253:
                image.setPixelColor(x, y, transparent)
    return QPixmap.fromImage(image)


def _disable_windows_frame_effects(hwnd: int) -> None:
    if sys.platform != "win32":
        return
    try:
        set_attribute = ctypes.WinDLL("dwmapi", use_last_error=True).DwmSetWindowAttribute
    except (AttributeError, OSError):
        return
    set_attribute.argtypes = (
        wintypes.HWND,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    set_attribute.restype = ctypes.c_long
    for attribute, value in _WINDOWS_FRAME_ATTRIBUTES:
        setting = wintypes.DWORD(value)
        set_attribute(
            wintypes.HWND(hwnd),
            wintypes.DWORD(attribute),
            ctypes.byref(setting),
            ctypes.sizeof(setting),
        )


class ControllerStateOverlay(QWidget):
    """PySide6 port of EasyCon's original 100x100 VPad overlay."""

    toggleRequested = Signal()
    hideRequested = Signal()

    WIDTH = 100
    HEIGHT = 100
    REFRESH_MS = 100

    def __init__(
        self,
        report_provider: ReportProvider,
        *,
        connected_provider: StateProvider | None = None,
        running_provider: StateProvider | None = None,
        parent: QWidget | None = None,
    ) -> None:
        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        no_focus = getattr(Qt.WindowType, "WindowDoesNotAcceptFocus", None)
        if no_focus is not None:
            flags |= no_focus
        super().__init__(parent, flags)
        self.setObjectName("ControllerStateOverlay")
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self.setWindowOpacity(0.5)

        self._report_provider = report_provider
        self._connected_provider = connected_provider
        self._running_provider = running_provider
        self._report = SwitchReport()
        self._active = False
        self._connected = False
        self._running = False
        self._running_started_at: float | None = None
        self._position_initialized = False
        self._right_press_global: QPoint | None = None
        self._right_press_window: QPoint | None = None
        self._right_dragged = False
        self._shutting_down = False

        self._background = _load_background_asset()
        self._shoulder_layers = {
            (button, pressed): _load_asset(f"{name}_{int(pressed)}")
            for button, name in _SHOULDER_ASSETS
            for pressed in (False, True)
        }

        self._timer = QTimer(self)
        self._timer.setInterval(self.REFRESH_MS)
        self._timer.timeout.connect(self.refresh_state)

    @property
    def active(self) -> bool:
        return self._active

    @property
    def report(self) -> SwitchReport:
        return self._report.copy()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.WIDTH, self.HEIGHT)

    def set_active(self, active: bool) -> None:
        active = bool(active)
        self._active = active
        self.setWindowOpacity(1.0 if active else 0.5)
        self.update()

    def show_overlay(self) -> None:
        if self._shutting_down:
            return
        if not self._position_initialized:
            self.reset_position()
        else:
            self._clamp_to_visible_screen()
        self.refresh_state()
        self.show()
        self.raise_()

    def reset_position(self) -> None:
        app = QApplication.instance()
        screen = app.primaryScreen() if app is not None else self.screen()
        if screen is None:
            self.move(24, 24)
        else:
            area = screen.availableGeometry()
            self.move(
                area.x() + area.width() // 2,
                area.y() + (area.height() - self.height()) // 2,
            )
        self._position_initialized = True
        self._clamp_to_visible_screen()

    def _clamp_to_visible_screen(self) -> None:
        app = QApplication.instance()
        screen = app.screenAt(self.frameGeometry().center()) if app is not None else None
        if screen is None:
            parent = self.parentWidget()
            screen = parent.window().screen() if parent is not None else self.screen()
        if screen is None and app is not None:
            screen = app.primaryScreen()
        if screen is None:
            return

        area = screen.availableGeometry()
        max_x = max(area.x(), area.x() + area.width() - self.width())
        max_y = max(area.y(), area.y() + area.height() - self.height())
        self.move(
            min(max(self.x(), area.x()), max_x),
            min(max(self.y(), area.y()), max_y),
        )

    def refresh_state(self) -> None:
        try:
            report = self._report_provider()
        except Exception:
            report = None
        self._connected = self._read_state(self._connected_provider, report is not None)
        self._report = (
            SwitchReport(
                button=_report_value(report, "button", 0),
                hat=_report_value(report, "hat", int(SwitchHat.CENTER)),
                lx=_report_value(report, "lx", 128),
                ly=_report_value(report, "ly", 128),
                rx=_report_value(report, "rx", 128),
                ry=_report_value(report, "ry", 128),
            )
            if report is not None and self._connected
            else SwitchReport()
        )
        running = self._read_state(self._running_provider, False)
        if running and not self._running:
            self._running_started_at = monotonic()
        elif not running:
            self._running_started_at = None
        self._running = running
        self.update()

    def shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        self._timer.stop()
        self.hide()

    @staticmethod
    def _read_state(provider: StateProvider | None, default: bool) -> bool:
        if provider is None:
            return default
        try:
            return bool(provider())
        except Exception:
            return default

    def showEvent(self, event) -> None:  # noqa: N802
        _disable_windows_frame_effects(int(self.winId()))
        self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._timer.stop()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._timer.stop()
        if not self._shutting_down:
            self.hideRequested.emit()
            self.hide()
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.RightButton:
            self._right_press_global = event.globalPosition().toPoint()
            self._right_press_window = self.pos()
            self._right_dragged = False
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if (
            self._right_press_global is not None
            and self._right_press_window is not None
            and event.buttons() & Qt.MouseButton.RightButton
        ):
            delta = event.globalPosition().toPoint() - self._right_press_global
            if not delta.isNull():
                self._right_dragged = True
            self.move(self._right_press_window + delta)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.RightButton:
            if not self._right_dragged:
                self.reset_position()
            self._right_press_global = None
            self._right_press_window = None
            self._right_dragged = False
        elif event.button() == Qt.MouseButton.LeftButton:
            self.toggleRequested.emit()
        elif event.button() == Qt.MouseButton.MiddleButton:
            self.hideRequested.emit()
        event.accept()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawPixmap(self.rect(), self._background)
        self._draw_script_lights(painter)
        self._draw_stick(painter, 11, 21, self._report.lx, self._report.ly, SwitchButton.LCLICK)
        self._draw_stick(painter, 63, 52, self._report.rx, self._report.ry, SwitchButton.RCLICK)
        self._draw_hat(painter)
        self._draw_shoulders(painter)
        self._draw_buttons(painter)
        painter.end()

    @staticmethod
    def _pen() -> QPen:
        return QPen(QColor(0, 0, 0))

    def _draw_script_lights(self, painter: QPainter) -> None:
        flash_index = -1
        if self._running:
            started_at = self._running_started_at
            if started_at is None:
                started_at = monotonic()
                self._running_started_at = started_at
            phase = int((monotonic() - started_at) * 1000) // 150
            flash_index = abs(3 - phase % 6)

        painter.setPen(self._pen())
        for index in range(4):
            if index == flash_index:
                color = QColor(255, 255, 255)
            elif self._running:
                color = QColor(255, 255, 255, 50)
            else:
                color = QColor(0, 0, 0, 50)
            painter.setBrush(color)
            painter.drawRect(QRectF(47, 32 + 10 * index, 5, 5))

    def _draw_stick(
        self,
        painter: QPainter,
        x0: int,
        y0: int,
        x_value: int,
        y_value: int,
        click_button: SwitchButton,
    ) -> None:
        area_size = 25
        knob_size = 15
        padding = 2
        x = x0 + (area_size - knob_size) * x_value // 255
        y = y0 + (area_size - knob_size) * y_value // 255
        moved = x_value != 128 or y_value != 128
        clicked = bool(int(self._report.button) & int(click_button))

        painter.setPen(self._pen())
        painter.setBrush(QColor(0, 255, 0, 200) if moved else QColor(0, 0, 0, 50))
        painter.drawEllipse(
            QRectF(
                x0 + padding,
                y0 + padding,
                area_size - padding * 2,
                area_size - padding * 2,
            )
        )
        painter.setBrush(QColor(0, 255, 0) if clicked else QColor(50, 50, 50))
        painter.drawEllipse(QRectF(x, y, knob_size, knob_size))

    def _draw_hat(self, painter: QPainter) -> None:
        hat = int(self._report.hat)
        directions = {
            int(SwitchHat.TOP): (True, False, False, False),
            int(SwitchHat.TOP_RIGHT): (True, False, False, True),
            int(SwitchHat.RIGHT): (False, False, False, True),
            int(SwitchHat.BOTTOM_RIGHT): (False, True, False, True),
            int(SwitchHat.BOTTOM): (False, True, False, False),
            int(SwitchHat.BOTTOM_LEFT): (False, True, True, False),
            int(SwitchHat.LEFT): (False, False, True, False),
            int(SwitchHat.TOP_LEFT): (True, False, True, False),
        }.get(hat, (False, False, False, False))
        rectangles = (
            (QRectF(21, 55, 6, 6), directions[0]),
            (QRectF(21, 67, 6, 6), directions[1]),
            (QRectF(15, 61, 6, 6), directions[2]),
            (QRectF(27, 61, 6, 6), directions[3]),
        )
        painter.setPen(self._pen())
        for rectangle, pressed in rectangles:
            painter.setBrush(QColor(0, 255, 0) if pressed else QColor(50, 50, 50))
            painter.drawRoundedRect(rectangle, 2, 2)

    def _draw_shoulders(self, painter: QPainter) -> None:
        buttons = int(self._report.button)
        for button, _name in _SHOULDER_ASSETS:
            layer = self._shoulder_layers[(button, bool(buttons & int(button)))]
            painter.drawPixmap(self.rect(), layer)

    def _draw_buttons(self, painter: QPainter) -> None:
        buttons = int(self._report.button)
        painter.setPen(self._pen())
        for button, rectangle in (
            (SwitchButton.A, QRectF(79, 29, 9, 9)),
            (SwitchButton.B, QRectF(71, 37, 9, 9)),
            (SwitchButton.X, QRectF(71, 21, 9, 9)),
            (SwitchButton.Y, QRectF(63, 29, 9, 9)),
        ):
            painter.setBrush(QColor(0, 255, 0) if buttons & int(button) else QColor(50, 50, 50))
            painter.drawEllipse(rectangle)

        for button, rectangle in (
            (SwitchButton.MINUS, QRectF(29, 12, 5, 5)),
            (SwitchButton.PLUS, QRectF(65, 12, 5, 5)),
            (SwitchButton.CAPTURE, QRectF(27, 82, 5, 5)),
            (SwitchButton.HOME, QRectF(67, 82, 5, 5)),
        ):
            painter.setBrush(QColor(0, 255, 0) if buttons & int(button) else QColor(50, 50, 50))
            painter.drawRoundedRect(rectangle, 1, 1)


__all__ = ("ControllerStateOverlay",)
