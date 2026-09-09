from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QComboBox, QLineEdit, QScrollArea, QVBoxLayout, QWidget,
)

from auto_bdsp_rng.ui.combo_box import ChevronComboBox, NoWheelComboBox
from auto_bdsp_rng.ui.delay_strategy_dialog import _DelayComboBox
from auto_bdsp_rng.ui.easycon_panel import SerialPortComboBox
from auto_bdsp_rng.ui.spin_box import ChevronDoubleSpinBox, ChevronSpinBox


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    application = QApplication.instance() or QApplication([])
    yield application
    for widget in application.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    application.processEvents()


def wheel_over(widget):
    window = widget.window()
    QTest.wheelEvent(
        window.windowHandle(),
        widget.mapTo(window, widget.rect().center()),
        QPoint(0, -120),
    )


@pytest.mark.parametrize("focused", [False, True])
@pytest.mark.parametrize("control_type", [
    NoWheelComboBox, ChevronComboBox, _DelayComboBox, SerialPortComboBox,
    ChevronSpinBox, ChevronDoubleSpinBox,
])
def test_wheel_over_control_scrolls_page_without_changing_value(app, control_type, focused):
    page = QScrollArea()
    page.setWidgetResizable(True)
    content = QWidget()
    content.setMinimumHeight(900)
    layout = QVBoxLayout(content)
    field = control_type()
    other_input = QLineEdit()
    layout.addWidget(field)
    layout.addWidget(other_input)
    layout.addStretch()
    page.setWidget(content)
    page.resize(360, 240)
    changes = []
    if isinstance(field, QComboBox):
        field.addItems([str(i) for i in range(100)])
        field.setCurrentIndex(50)
        field.currentIndexChanged.connect(changes.append)
        current_value = field.currentIndex
    else:
        field.setRange(0, 100)
        field.setValue(50)
        field.valueChanged.connect(changes.append)
        current_value = field.value
    page.show()
    page.activateWindow()
    (field if focused else other_input).setFocus()
    app.processEvents()
    assert field.hasFocus() is focused
    assert page.verticalScrollBar().value() == 0

    # Send through the window so Qt performs normal hit testing and propagation.
    wheel_over(field)
    app.processEvents()

    assert current_value() == 50
    assert not changes
    assert page.verticalScrollBar().value() > 0


@pytest.mark.parametrize("combo_type", [NoWheelComboBox, ChevronComboBox, _DelayComboBox])
def test_popup_still_scrolls_and_click_selects(app, combo_type):
    combo = combo_type()
    combo.addItems([f"Script {i}" for i in range(100)])
    combo.setStyleSheet("QComboBox { combobox-popup: 0; }")
    combo.setMaxVisibleItems(6)
    combo.resize(240, 32)
    combo.show()
    combo.showPopup()
    app.processEvents()
    view = combo.view()
    assert view.verticalScrollBar().maximum() > 0

    wheel_over(view.viewport())
    app.processEvents()

    assert view.verticalScrollBar().value() > 0
    assert combo.currentIndex() == 0
    point = view.viewport().rect().center()
    chosen_row = view.indexAt(point).row()
    assert chosen_row > 0
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert combo.currentIndex() == chosen_row


@pytest.mark.parametrize("control_type", [ChevronSpinBox, ChevronDoubleSpinBox])
def test_number_inputs_keep_keyboard_editing_and_step_buttons(app, control_type):
    from PySide6.QtWidgets import QStyle, QStyleOptionSpinBox

    field = control_type()
    field.setRange(0, 100)
    field.setValue(50)
    field.show()
    field.setFocus()
    app.processEvents()
    field.selectAll()
    QTest.keyClicks(field, "42")
    QTest.keyClick(field, Qt.Key.Key_Return)
    assert field.value() == 42
    QTest.keyClick(field, Qt.Key.Key_Up)
    assert field.value() == 43
    option = QStyleOptionSpinBox()
    field.initStyleOption(option)
    down_button = field.style().subControlRect(
        QStyle.ComplexControl.CC_SpinBox, option,
        QStyle.SubControl.SC_SpinBoxDown, field,
    )
    QTest.mouseClick(field, Qt.MouseButton.LeftButton, pos=down_button.center())
    assert field.value() == 42


def test_editable_combo_ignores_wheel_over_text_but_keeps_keyboard_selection(app):
    combo = ChevronComboBox()
    combo.setEditable(True)
    combo.addItems(["First", "Second", "Third"])
    combo.setCurrentIndex(1)
    combo.show()
    combo.setFocus()
    app.processEvents()
    wheel_over(combo.lineEdit())
    assert combo.currentIndex() == 1
    QTest.keyClick(combo, Qt.Key.Key_Down)
    assert combo.currentIndex() == 2
