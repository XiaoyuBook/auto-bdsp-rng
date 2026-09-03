from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QCloseEvent, QImage, QMouseEvent, QPainter
from PySide6.QtWidgets import QApplication

import auto_bdsp_rng.ui.controller_overlay as controller_overlay_module
from auto_bdsp_rng.automation.easycon.native.device import SwitchButton, SwitchHat, SwitchReport
from auto_bdsp_rng.ui.controller_overlay import ControllerStateOverlay


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VPAD_ASSET_NAMES = (
    "JoyCon.png",
    "JoyCon_L_0.png",
    "JoyCon_L_1.png",
    "JoyCon_R_0.png",
    "JoyCon_R_1.png",
    "JoyCon_ZL_0.png",
    "JoyCon_ZL_1.png",
    "JoyCon_ZR_0.png",
    "JoyCon_ZR_1.png",
)


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def _mouse_release(button: Qt.MouseButton) -> QMouseEvent:
    return QMouseEvent(
        QMouseEvent.Type.MouseButtonRelease,
        QPointF(20, 20),
        QPointF(20, 20),
        button,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )


def _render_overlay(overlay: ControllerStateOverlay) -> QImage:
    image = QImage(overlay.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    overlay.render(painter, QPoint())
    painter.end()
    return image


def test_overlay_reads_a_copy_of_the_real_report(app):
    source = SwitchReport(
        button=int(SwitchButton.A | SwitchButton.LCLICK),
        hat=int(SwitchHat.TOP_RIGHT),
        lx=255,
        ly=0,
        rx=64,
        ry=192,
    )
    overlay = ControllerStateOverlay(lambda: source)

    overlay.refresh_state()
    snapshot = overlay.report
    source.reset()

    assert snapshot.button == int(SwitchButton.A | SwitchButton.LCLICK)
    assert snapshot.hat == int(SwitchHat.TOP_RIGHT)
    assert (snapshot.lx, snapshot.ly, snapshot.rx, snapshot.ry) == (255, 0, 64, 192)
    assert overlay.size().width() == ControllerStateOverlay.WIDTH
    assert overlay.size().height() == ControllerStateOverlay.HEIGHT
    assert overlay.size() == overlay.sizeHint()
    assert (overlay.width(), overlay.height()) == (100, 100)


def test_overlay_surface_is_transparent_and_has_no_window_shadow(app):
    overlay = ControllerStateOverlay(lambda: SwitchReport())

    image = _render_overlay(overlay)
    background = overlay._background.toImage()

    assert overlay.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert overlay.windowFlags() & Qt.WindowType.NoDropShadowWindowHint
    for point in ((0, 0), (99, 0), (0, 99), (99, 99)):
        assert image.pixelColor(*point).alpha() == 0
    for point in ((2, 25), (25, 2), (97, 25)):
        assert background.pixelColor(*point).alpha() == 0
    assert background.pixelColor(3, 25) == QColor(0, 0, 0)


def test_overlay_uses_unmodified_original_easycon_vpad_assets(app):
    del app
    source_dir = (
        PROJECT_ROOT
        / "third_party"
        / "EasyCon"
        / "src"
        / "EasyCon2.Avalonia.Core"
        / "VPad"
        / "Resources"
    )
    runtime_dir = PROJECT_ROOT / "src" / "auto_bdsp_rng" / "ui" / "vpad_assets"

    for name in VPAD_ASSET_NAMES:
        assert (runtime_dir / name).read_bytes() == (source_dir / name).read_bytes()


def test_overlay_matches_original_pressed_button_and_hat_coordinates(app):
    report = SwitchReport(
        button=int(SwitchButton.A),
        hat=int(SwitchHat.TOP_RIGHT),
    )
    overlay = ControllerStateOverlay(lambda: report)
    overlay.refresh_state()

    image = _render_overlay(overlay)

    assert image.pixelColor(83, 33).name() == "#00ff00"
    assert image.pixelColor(24, 58).name() == "#00ff00"
    assert image.pixelColor(30, 64).name() == "#00ff00"
    assert image.pixelColor(24, 70).name() == "#323232"
    assert image.pixelColor(18, 64).name() == "#323232"


def test_overlay_matches_original_161_script_light_opacity(app):
    overlay = ControllerStateOverlay(
        lambda: SwitchReport(),
        running_provider=lambda: True,
    )
    overlay.refresh_state()
    image = QImage(overlay.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    overlay._draw_script_lights(painter)
    painter.end()

    weak_light = image.pixelColor(49, 34)
    assert weak_light.name() == "#ffffff"
    assert weak_light.alpha() == 50

    overlay._running = False
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    overlay._draw_script_lights(painter)
    painter.end()
    idle_light = image.pixelColor(49, 34)
    assert idle_light.name() == "#000000"
    assert idle_light.alpha() == 50


def test_overlay_neutralizes_stale_report_when_disconnected(app):
    overlay = ControllerStateOverlay(
        lambda: SwitchReport(button=int(SwitchButton.A), lx=255),
        connected_provider=lambda: False,
    )

    overlay.refresh_state()

    assert overlay.report == SwitchReport()


def test_overlay_tolerates_missing_or_uppercase_report_fields(app):
    class LegacyReport:
        Button = int(SwitchButton.HOME)
        HAT = int(SwitchHat.LEFT)
        LX = 0
        LY = 128
        RX = 255
        RY = 128

    reports = iter((None, LegacyReport()))
    overlay = ControllerStateOverlay(lambda: next(reports))

    overlay.refresh_state()
    assert overlay.report == SwitchReport()
    overlay.refresh_state()
    assert overlay.report == SwitchReport(
        button=int(SwitchButton.HOME),
        hat=int(SwitchHat.LEFT),
        lx=0,
        ly=128,
        rx=255,
        ry=128,
    )


def test_overlay_mouse_commands_emit_without_taking_keyboard_focus(app):
    overlay = ControllerStateOverlay(lambda: SwitchReport())
    toggles: list[bool] = []
    hides: list[bool] = []
    overlay.toggleRequested.connect(lambda: toggles.append(True))
    overlay.hideRequested.connect(lambda: hides.append(True))

    overlay.mouseReleaseEvent(_mouse_release(Qt.MouseButton.LeftButton))
    overlay.mouseReleaseEvent(_mouse_release(Qt.MouseButton.MiddleButton))

    assert toggles == [True]
    assert hides == [True]
    assert overlay.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

    overlay.set_active(True)
    assert overlay.windowOpacity() == pytest.approx(1.0)
    overlay.set_active(False)
    assert overlay.windowOpacity() == pytest.approx(0.5, abs=1 / 255)
    assert overlay.cursor().shape() == Qt.CursorShape.PointingHandCursor


def test_overlay_refreshes_connection_and_running_state(app):
    state = {"connected": True, "running": False}
    overlay = ControllerStateOverlay(
        lambda: SwitchReport(),
        connected_provider=lambda: state["connected"],
        running_provider=lambda: state["running"],
    )

    overlay.refresh_state()
    assert overlay._connected is True
    assert overlay._running is False

    state.update(connected=False, running=True)
    overlay.refresh_state()
    assert overlay._connected is False
    assert overlay._running is True


def test_overlay_timer_and_shutdown_follow_visibility(app):
    overlay = ControllerStateOverlay(lambda: SwitchReport())

    overlay.show_overlay()
    app.processEvents()
    assert overlay._timer.isActive()
    assert overlay.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert overlay.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert not overlay.grab().isNull()

    overlay.hide()
    app.processEvents()
    assert not overlay._timer.isActive()


def test_overlay_disables_native_frame_effects_when_shown(app, monkeypatch):
    handles: list[int] = []
    monkeypatch.setattr(controller_overlay_module, "_disable_windows_frame_effects", handles.append)
    overlay = ControllerStateOverlay(lambda: SwitchReport())

    overlay.show_overlay()
    app.processEvents()

    assert handles
    assert handles[-1] == int(overlay.winId())


def test_overlay_is_clamped_back_onto_a_screen_when_reshown(app):
    overlay = ControllerStateOverlay(lambda: SwitchReport())
    overlay.show_overlay()
    app.processEvents()
    overlay.hide()
    overlay.move(-10000, -10000)

    overlay.show_overlay()
    app.processEvents()

    area = (overlay.screen() or app.primaryScreen()).availableGeometry()
    assert area.contains(overlay.frameGeometry())

    overlay.shutdown()
    overlay.shutdown()
    overlay.show_overlay()
    app.processEvents()
    assert not overlay.isVisible()
    assert not overlay._timer.isActive()


def test_overlay_close_requests_deactivation_and_hides(app):
    overlay = ControllerStateOverlay(lambda: SwitchReport())
    hides: list[bool] = []
    overlay.hideRequested.connect(lambda: hides.append(True))
    overlay.show_overlay()
    app.processEvents()

    assert overlay.close()
    app.processEvents()

    assert hides == [True]
    assert not overlay.isVisible()
    assert not overlay._timer.isActive()


def test_overlay_shutdown_suppresses_later_close_request(app):
    overlay = ControllerStateOverlay(lambda: SwitchReport())
    hides: list[bool] = []
    overlay.hideRequested.connect(lambda: hides.append(True))
    overlay.show_overlay()
    app.processEvents()

    overlay.shutdown()
    close_event = QCloseEvent()
    overlay.closeEvent(close_event)

    assert close_event.isAccepted()
    assert hides == []
    assert not overlay.isVisible()
    assert not overlay._timer.isActive()
