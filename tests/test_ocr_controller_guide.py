from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from tests.test_guide_flow import guided, assert_body_text_is_readable
from tests.test_script_guide import connected_guide, click_guided_tab
from tests.test_ui import app, isolated_ui_qsettings


@pytest.mark.parametrize("state", ["off", "standby", "active"])
@pytest.mark.parametrize("width", [860, 1150])
def test_ocr_entry_requires_active_control_and_waits_for_actual_click(guided, connected_guide, monkeypatch, state, width):
    w, p, c = guided
    e, o = w.easycon_tab, c.script_guide.ocr
    monkeypatch.setattr(e, "_keyboard_hook_factory", SimpleNamespace(is_supported=lambda: False))
    w.resize(width, 600 if width == 860 else 760)
    if state != "off":
        e.controller_active_button.click()
        if state == "standby":
            e._set_virtual_controller_standby()
    w.tabs.setCurrentWidget(p)
    c._go("auto_script_config", "ocr:select")
    if state == "active":
        assert not c.overlay.waiting_for_page
        assert c.overlay.focus_target is p.capture_info_button
    else:
        assert o.demo is None and o.tip is None
        click_guided_tab(w, c, e)
        QTest.qWait(150)
        assert c.overlay.focus_target is e.controller_active_button
        assert_body_text_is_readable(c.overlay.tip)
        center = c.overlay.mapFromGlobal(e.controller_active_button.mapToGlobal(e.controller_active_button.rect().center()))
        assert not c.overlay.mask().contains(center)
        assert not c.overlay.tip.geometry().contains(center)
        c.next()
        c.skip()
        assert c.detail == "ocr:select" and o.demo is None
        QTest.mouseClick(e.controller_active_button, Qt.MouseButton.LeftButton)
        assert e.virtual_controller_enabled
        c.script_guide.poll()
        assert e._controller_overlay.isVisible()
        assert w.tabs.currentWidget() is e
        click_guided_tab(w, c, p)
    c.next()
    assert o.phase == "initial_test" and o.demo is None
    QTest.qWait(50)
    assert o.dialog.isVisible() and e.virtual_controller_enabled
    # Force the guide's filter first, as happens when resuming a guide with an
    # existing Qt controller. Keys must reach the device even over the shade.
    QApplication.instance().installEventFilter(c)
    QTest.keyPress(w, Qt.Key.Key_W)
    assert connected_guide.get_report().ly == 1
    QTest.keyRelease(w, Qt.Key.Key_W)
    assert connected_guide.get_report().ly == 128
    QTest.keyClick(o.dialog, Qt.Key.Key_Escape)
    assert not e.virtual_controller_enabled and c.active
    assert o.dialog.isVisible()  # Standby must not dismiss OCR or pause the guide.
    QTest.keyClick(o.dialog, Qt.Key.Key_Escape)
    assert e.virtual_controller_enabled
    c.pause()
    assert not e.virtual_controller_enabled
    assert connected_guide.get_report().ly == 128


def test_ocr_preparation_follows_connection_and_keeps_saved_phase(guided, monkeypatch):
    w, p, c = guided
    e, o = w.easycon_tab, c.script_guide.ocr
    monkeypatch.setattr(e, "_keyboard_hook_factory", SimpleNamespace(is_supported=lambda: False))
    w.tabs.setCurrentWidget(e)
    c._go("auto_script_config", "ocr:notes")
    assert c.overlay.focus_target is w.easycon_header_button
    e.controller_active_button.click()
    c.script_guide.poll()
    assert o.tip is None and not c.overlay.tip.next_button.isEnabled()
    from auto_bdsp_rng.automation.easycon.native.device import MemoryTransport, NintendoSwitchDevice
    from auto_bdsp_rng.automation.easycon.native_backend import NativeEasyConBackend
    backend = NativeEasyConBackend(device=NintendoSwitchDevice(transport_factory=lambda port, baud: MemoryTransport(port, baud)))
    e.native_backend = backend
    try:
        backend.connect("mock")
        e._update_run_enabled()
        c.script_guide.poll()
        assert c.overlay.focus_target is e.controller_active_button
        QTest.mouseClick(e.controller_active_button, Qt.MouseButton.LeftButton)
        c.script_guide.poll()
        assert o.phase == "initial_test" and o.dialog.isVisible()
    finally:
        c.pause()
        backend.close()
