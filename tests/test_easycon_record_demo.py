from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget

from auto_bdsp_rng.ui import easycon_panel
from auto_bdsp_rng.ui.easycon_record_demo_dialog import EasyConRecordDemoDialog
from tests.test_ui import app, isolated_ui_qsettings  # noqa: F401


@pytest.fixture
def demo(app, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("The recording demonstration must not write settings or install a keyboard hook")

    monkeypatch.setattr(easycon_panel, "load_config", forbidden)
    monkeypatch.setattr(easycon_panel, "save_config", forbidden)
    monkeypatch.setattr(easycon_panel, "discard_legacy_generated_snapshots", forbidden)
    monkeypatch.setattr(easycon_panel, "WindowsKeyboardHook", forbidden)
    parent = QWidget()
    dialog = EasyConRecordDemoDialog(parent)
    dialog.show()
    QTest.qWait(50)
    dialog.timer.stop()
    dialog.restart()
    dialog.timer.stop()
    yield dialog
    dialog.reject()
    dialog.deleteLater()
    parent.deleteLater()


def test_native_recording_w_moves_report_and_streams_commands(demo):
    panel = demo.panel
    initial = panel.editor.toPlainText()
    assert not demo.vpad_proxy.isVisible()
    demo.advance_to(1899)
    assert not panel.virtual_controller_enabled
    demo.advance_to(1900)
    assert panel.controller_mode_buttons["active"].isChecked()
    assert demo.vpad_proxy.isVisible()
    assert panel.record_btn.text() == "开始录制"
    demo.advance_to(5000)
    assert panel.record_btn.text() == "停止录制"
    assert panel.recording_state_label.text() == "录制中"
    demo.advance_to(7400)
    assert demo.keyboard.down
    assert demo.backend.get_report().ly == 1
    assert demo.vpad.report.ly == 1
    assert panel.editor.toPlainText() == initial + "LS UP\n"
    assert panel.script_save_state_label.text() == "未保存"
    demo.advance_to(9200)
    assert not demo.keyboard.down
    assert demo.vpad.report.ly == 128
    assert panel.editor.toPlainText() == initial + "LS UP\nWAIT 1800\nLS RESET\n"
    demo.advance_to(11600)
    assert panel.editor.toPlainText() == initial + "LS UP\nWAIT 1800\nLS RESET\n"
    assert panel.record_btn.text() == "开始录制"
    assert panel.script_save_state_label.text() == "未保存"
    demo.advance_to(22700)
    assert not demo.vpad_proxy.isVisible()
    assert panel.script_save_state_label.text() == "已保存"
    assert panel._keyboard_hook is None
    assert panel._vpad_input_source is None


def test_pause_replay_and_exit_release_memory_device(demo):
    demo.advance_to(8000)
    demo.toggle_playing()
    before = demo.elapsed
    QTest.qWait(50)
    demo._tick()
    assert demo.elapsed == before
    assert demo.keyboard.down
    demo.toggle_playing()
    demo.advance_to(11600)
    assert "WAIT 1800" in demo.panel.editor.toPlainText()
    demo.restart()
    demo.timer.stop()
    assert "LS UP" not in demo.panel.editor.toPlainText()
    assert not demo.keyboard.down
    assert not demo.vpad_proxy.isVisible()
    demo.advance_to(8000)
    demo.reject()
    assert not demo.timer.isActive()
    assert not demo.backend.connected_port
    assert not demo.panel.virtual_controller_keys


def test_footer_does_not_cover_live_scene_and_cursor_reaches_before_click(demo):
    for width, height in ((1240, 860), (860, 640), (600, 490)):
        demo.resize(width, height)
        QTest.qWait(30)
        assert demo.title.y() >= demo.view.geometry().bottom()
        assert demo.rect().contains(demo.learned_button.mapTo(demo, demo.learned_button.rect().bottomRight()))
        demo.advance_to(1800)
        assert not demo.panel.virtual_controller_enabled
        cursor, _ = demo.cursor_state()
        assert demo.rect_for("active").contains(cursor)
        demo.restart()
        demo.timer.stop()


def test_learned_requires_persist_ack_and_freezes_playback(demo):
    requested = []
    demo.learnedRequested.connect(lambda: requested.append(True))
    demo.learned_button.click()
    assert requested == [True]
    assert not demo.playing
    demo.accept()
    assert demo.isVisible()
    demo.save_failed()
    assert demo.learned_button.isEnabled()
    demo.learned_button.click()
    demo.finish_learning()
    assert demo.acknowledged
    assert not demo.isVisible()
    assert not demo.backend.connected_port
