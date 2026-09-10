from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QPointF, QSettings, QSize, Qt
from PySide6.QtGui import QEnterEvent, QIcon
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QLabel, QMenu, QPushButton, QWidget

from auto_bdsp_rng.data import get_static_encounters
from auto_bdsp_rng.gen8_static import StateFilter
from auto_bdsp_rng.ui.auto_rng_panel import AutoRngPanel
from auto_bdsp_rng.ui.delay_strategy_dialog import delay_lucide_icon
from auto_bdsp_rng.ui.workspace_controls import (
    ConnectionDialog, PrimaryButton, PrimaryToolButton, SpeciesAvatar,
    set_disconnect_action, workspace_icon,
)
from auto_bdsp_rng.ui.workspace_theme import primary_button_styles


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("button_type", [PrimaryButton, PrimaryToolButton])
def test_button_feedback_preserves_click_keyboard_and_disabled_state(app, button_type):
    button = button_type()
    button.setText("开始")
    button.setObjectName("PrimaryButton")
    button.setStyleSheet(primary_button_styles("QPushButton#PrimaryButton", "QToolButton#PrimaryButton"))
    button.resize(120, 34)
    button.show()
    app.processEvents()
    clicks = QSignalSpy(button.clicked)
    QApplication.sendEvent(button, QEnterEvent(QPointF(10, 10), QPointF(10, 10), QPointF(10, 10)))
    QTest.qWait(180)
    assert button._highlight > 0.9
    QTest.mouseClick(button, Qt.MouseButton.LeftButton, pos=QPoint(20, 16))
    QTest.keyClick(button, Qt.Key.Key_Space)
    assert clicks.count() == 2
    button.setEnabled(False)
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    assert clicks.count() == 2
    button.hide()
    assert button._highlight == 0
    button.deleteLater()


def test_animated_split_button_retains_real_menu_actions(app):
    button = PrimaryToolButton()
    button.resize(120, 34)
    button.setPopupMode(PrimaryToolButton.ToolButtonPopupMode.MenuButtonPopup)
    menu = QMenu(button)
    action = menu.addAction("从捕获 Seed 开始")
    button.setMenu(menu)
    selected = QSignalSpy(action.triggered)
    button.show()
    menu.popup(button.mapToGlobal(QPoint(0, button.height())))
    app.processEvents()
    QTest.mouseClick(menu, Qt.MouseButton.LeftButton, pos=menu.actionGeometry(action).center())
    assert selected.count() == 1
    button.close()
    button.deleteLater()


@pytest.mark.parametrize("ratio", [1.0, 1.25, 1.5, 2.0])
def test_shared_and_delay_icons_render_at_requested_density(app, ratio):
    for size in (16, 20):
        for name in ("settings-2", "square-pen", "chevron-down", "x"):
            common = workspace_icon(name).pixmap(QSize(size, size), ratio)
            delay = delay_lucide_icon(name, size=size).pixmap(QSize(size, size), ratio)
            assert common.size() == QSize(round(size * ratio), round(size * ratio))
            assert common.devicePixelRatioF() == ratio
            assert common.toImage() == delay.toImage()
            assert not common.mask().isNull()
    icon = workspace_icon("play", "#FFFFFF")
    assert icon.pixmap(QSize(16, 16), ratio, QIcon.Mode.Disabled).toImage() != icon.pixmap(QSize(16, 16), ratio).toImage()


def test_species_art_covers_existing_encounters_and_falls_back_without_changing_targets(app, tmp_path):
    avatar = SpeciesAvatar()
    for record in get_static_encounters():
        avatar.set_species(int(record.template.species), record.description)
        assert not avatar._sprite.isNull(), record.description
    avatar.set_species(999999)
    assert avatar._sprite.isNull()
    assert not avatar.grab().isNull()

    panel = AutoRngPanel(script_dir=tmp_path, settings=QSettings(str(tmp_path / "avatar.ini"), QSettings.Format.IniFormat))
    for name in ("Shaymin", "Dialga"):
        record = next(r for r in get_static_encounters() if r.description == name)
        targets = [(record, StateFilter(shiny=2), "square")]
        panel.set_targets(targets)
        assert panel.targets() == targets
        assert panel.target_avatar.property("speciesId") == int(record.template.species)
        assert not panel.target_avatar._sprite.isNull()
    panel.close()
    panel.deleteLater()


def test_connection_surface_and_initial_action_render_before_status_updates(app):
    parent = QWidget()
    dialog = ConnectionDialog("连接设置", "TestConnectionDialog", parent)
    dialog.body_layout.addWidget(QLabel("设备"))
    button = QPushButton("连接")
    button.setObjectName("PrimaryButton")
    button.setFixedWidth(100)
    dialog.footer_layout.addWidget(button)
    dialog.show()
    app.processEvents()
    surface = dialog.grab().toImage()
    width, height = surface.width(), surface.height()
    for x, y in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)):
        assert surface.pixelColor(x, y).alpha() == 0
    assert surface.pixelColor(width // 2, 2).alpha() == 255
    assert surface.pixelColor(width // 2, height - 3).alpha() == 255
    assert button.property("disconnect") is None

    def colors():
        picture = button.grab().toImage()
        dpr = picture.devicePixelRatio()
        x = round(12 * dpr)  # Inside the fill, away from text and corner edges.
        return (picture.pixelColor(x, round(6 * dpr)),
                picture.pixelColor(x, picture.height() - round(6 * dpr)))

    initial = colors()
    assert initial[0].green() > initial[1].green() + 8
    set_disconnect_action(button, False)
    assert colors() == initial
    set_disconnect_action(button, True)
    disconnected = colors()
    assert disconnected != initial
    assert disconnected[0].green() > 230
    button.setEnabled(False)
    assert colors() != disconnected
    button.setEnabled(True)
    set_disconnect_action(button, False)
    assert colors() == initial
    clicks = QSignalSpy(button.clicked)
    button.click()
    assert clicks.count() == 1
    dialog.close_button.click()
    assert not dialog.isVisible()
    parent.close()
    parent.deleteLater()
