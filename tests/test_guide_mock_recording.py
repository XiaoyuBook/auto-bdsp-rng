from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest

from auto_bdsp_rng.ui import main_window as module
from tests.test_guide_flow import guided, assert_body_text_is_readable
from tests.test_ui import app, isolated_ui_qsettings
from tests.test_script_guide import connected_guide, open_for_guide
from tests.test_script_capture_guide import finish_capture


def start_mock(guided, monkeypatch, kind="seed", noisy=False):
    w, p, c = guided
    monkeypatch.setattr(w, "_dev_mock_devices", True)
    w._connect_mock_video_source()
    monkeypatch.setattr(w, "_new_broker_client", lambda: pytest.fail("Mock opened the real Broker"))
    monkeypatch.setattr(module, "capture_player_blinks", lambda *_a, **_k: pytest.fail("Mock ran blink detection"))
    w.tabs.setCurrentWidget(w.project_xs_tab)
    w.reidentify_1_pk_npc.setChecked(noisy)
    c._go("auto_script_config", kind + ":capture_start")
    (w.reidentify_button if kind == "advance" else w.capture_button).click()
    return w, c


@pytest.mark.parametrize("kind,noisy,total", [("seed", False, 40), ("advance", False, 7), ("advance", True, 20)])
def test_mock_immediate_progress_user_chooses_success_then_failure(guided, monkeypatch, kind, noisy, total):
    w, c = start_mock(guided, monkeypatch, kind, noisy)
    try:
        assert w._capture_progress == (total, total)
        assert c.detail == kind + ":capture_wait"
        assert not c.overlay.tip.next_button.isEnabled()
        assert w._mock_capture_dialog.isVisible()
        w._update_preview_frame()
        assert w._video_source_connected
        w._mock_capture_dialog.success_button.click()
        finish_capture(w)
        assert c.detail == kind + ":capture_result"
        assert c.script_guide.capture.succeeded()
        if kind == "seed":
            assert all(box.text() for box in w.seed32_inputs)
        else:
            assert int(w.advances_value.text()) >= 1000
        w._update_preview_frame()
        assert w._video_source_connected
        start_mock(guided, monkeypatch, kind, noisy)
        w._mock_capture_dialog.failure_button.click()
        finish_capture(w)
        assert c.detail == kind + ":capture_failed"
        assert "Mock" in c.overlay.tip.status.text()
        assert c.overlay.tip.next_button.text() == "修改配置"
        assert not c.script_guide.capture.succeeded()
    finally:
        w._capture_cancel.set()
        finish_capture(w)


@pytest.mark.parametrize("action", ["close", "stop", "disconnect"])
def test_mock_cancel_never_counts_as_success(guided, monkeypatch, action):
    w, c = start_mock(guided, monkeypatch)
    if action == "close":
        w._mock_capture_dialog.close_button.click()
    elif action == "stop":
        w.capture_seed()
    else:
        w.disconnect_video_source(force=True)
    finish_capture(w)
    assert c.detail == "seed:capture_failed"
    assert not c.script_guide.capture.succeeded()
    assert w._mock_capture_dialog is None


@pytest.mark.parametrize("width,kind,phase", [(1150, "seed", "edit"), (860, "hit", "retry"),
                                             (860, "advance", "advance_frames"), (1150, "reverse", "retry")])
def test_guide_editing_allows_real_recording_and_keeps_controls_clear(guided, connected_guide, monkeypatch, tmp_path, width, kind, phase):
    w, p, c = guided
    e = w.easycon_tab
    monkeypatch.setattr(w, "_confirm_unsaved_easycon_script", lambda **_kwargs: True)
    w.resize(width, 600 if width == 860 else 760)
    open_for_guide(guided, tmp_path, kind, "_目标帧数 = 1000\nWAIT 100\n")
    c._go("auto_script_config", kind + ":" + phase)
    monkeypatch.setattr(e, "_keyboard_hook_factory", SimpleNamespace(is_supported=lambda: False))
    clock = [10.0]
    monkeypatch.setattr(e, "_recording_clock", lambda: clock[0])
    QTest.qWait(120)
    o = c.overlay
    assert o.tip.isVisible()
    assert_body_text_is_readable(o.tip)
    for button in (e.controller_active_button, e.record_btn, e.pause_btn, e.save_button):
        point = o.mapFromGlobal(button.mapToGlobal(button.rect().center()))
        assert any(rect.contains(point) for rect in o.holes), button.text()
        assert not o.tip.geometry().contains(point), button.text()
        assert not o.mask().contains(point), button.text()
    e.controller_active_button.click()
    assert e.virtual_controller_enabled
    assert e._controller_overlay.isVisible()
    e.record_btn.click()
    c.script_guide.poll()
    assert e._recording
    assert not c.overlay.tip.next_button.isEnabled()
    QTest.keyPress(e, Qt.Key.Key_W)
    assert connected_guide.get_report().ly == 1
    assert "LS UP" in e.editor.toPlainText()
    clock[0] += 1.5
    QTest.keyRelease(e, Qt.Key.Key_W)
    assert "WAIT 1500\nLS RESET" in e.editor.toPlainText()
    e.pause_btn.click()
    assert e._recording_paused
    e.pause_btn.click()
    e.record_btn.click()
    c.script_guide.poll()
    assert not e._recording
    assert c.overlay.tip.next_button.isEnabled()
    c.pause()
    assert not e.virtual_controller_enabled
    assert connected_guide.get_report().ly == 128
    saved = e.save_script()
    assert saved is not None
    assert "WAIT 1500\nLS RESET" in saved.read_text(encoding="utf-8")
