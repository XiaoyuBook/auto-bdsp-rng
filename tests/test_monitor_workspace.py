from threading import Thread

import numpy as np
import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from auto_bdsp_rng.rng_core import SeedState32
from auto_bdsp_rng.ui.live_log_panel import LiveLogPanel
from auto_bdsp_rng.ui.run_log_panel import RunLogBuffer
from tests.test_start_readiness import window


def settle():
    for _ in range(5):
        QApplication.processEvents()
        QTest.qWait(5)


@pytest.mark.parametrize("size", [(860, 600), (1150, 900), (1500, 960)])
def test_video_and_logs_remain_visible_and_keep_state_across_pages(window, monkeypatch, size):
    w = window
    monkeypatch.setattr(w, "_screen_available_geometry", lambda: QRect(0, 0, 1900, 1200))
    w.resize(*size)
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame[:, :, 1] = 96
    w._latest_preview_frame = frame
    w._latest_annotated_preview_frame = frame
    for box, word in zip(w.seed32_inputs, SeedState32(1, 2, 3, 4).format_words()):
        box.setText(word)
    w._sync_seed64_from_state32()
    w._handle_auto_capture_progress(12, 40)
    w._update_auto_rng_header(advances=9876)
    w._run_log_buffer.publish("Seed 捕捉", "保留本次捕捉日志")
    expected_seed = [box.text() for box in w.seed64_outputs]
    for index in range(w.tabs.count()):
        w.tabs.setCurrentIndex(index)
        settle()
        w._refresh_preview_presentation()
        assert w.size().width() == size[0]
        assert w.workspace_splitter.orientation() == Qt.Orientation.Horizontal
        assert w.preview_label.isVisible() and w.live_log_panel.isVisible()
        assert w.monitor_sidebar.geometry().left() > w.tabs.geometry().right()
        assert not w.preview_label.pixmap().isNull()
        assert w.progress_value.text() == "12/40"
        assert w.advances_value.text() == "9876"
        assert [box.text() for box in w.seed64_outputs] == expected_seed
        assert "保留本次捕捉日志" in w.live_log_panel.text.toPlainText()
        assert abs(w.preview_aspect_container.width() * 9 - w.preview_aspect_container.height() * 16) <= 8
    assert w.monitor_preview_scroll.verticalScrollBar().maximum() == 0


def test_narrow_toolbar_keeps_start_stop_and_script_controls_reachable(window, monkeypatch):
    w = window
    monkeypatch.setattr(w, "_screen_available_geometry", lambda: QRect(0, 0, 1800, 1100))
    w.resize(860, 600)
    for page, controls in (
        (w.auto_rng_tab, [w.auto_rng_tab.start_button, w.auto_rng_tab.stop_button, w.auto_rng_tab.capture_info_button]),
        (w.auto_tid_rng_tab, [w.auto_tid_rng_tab.start_button, w.auto_tid_rng_tab.stop_button]),
        (w.easycon_tab, [w.easycon_tab.open_button, w.easycon_tab.save_button, w.easycon_tab.run_button, w.easycon_tab.pause_button, w.easycon_tab.stop_button]),
    ):
        w.tabs.setCurrentWidget(page)
        settle()
        rects = [QRect(control.mapTo(page, QPoint()), control.size()) for control in controls]
        assert all(page.rect().contains(rect) for rect in rects)
        assert all(not a.intersects(b) for i, a in enumerate(rects) for b in rects[i + 1:])


def test_live_logs_filter_queued_messages_without_modifying_session(window):
    buffer = RunLogBuffer(max_entries=8)
    buffer.publish("Seed 捕捉", "捕捉开始")
    panel = LiveLogPanel(buffer, window)
    panel.show()
    worker = Thread(target=lambda: (
        buffer.publish("自动定点", "<测试> 保留原始文本", "ERROR"),
        buffer.publish("Seed 捕捉", "捕捉完成"),
    ))
    worker.start()
    worker.join()
    settle()
    assert panel.text.toPlainText().count("捕捉开始") == 1
    assert "<测试> 保留原始文本" in panel.text.toPlainText()
    panel.source_combo.setCurrentIndex(panel.source_combo.findData("Seed 捕捉"))
    assert "捕捉完成" in panel.text.toPlainText()
    assert "<测试>" not in panel.text.toPlainText()
    assert len(buffer.snapshot()) == 3
    received = []
    panel.expandRequested.connect(received.append)
    panel.expand_button.click()
    assert received == ["Seed 捕捉"]
    panel.source_combo.setCurrentIndex(0)
    assert panel.text.toPlainText().count("<测试>") == 1


def test_live_log_follow_can_pause_and_resume(window):
    panel = window.live_log_panel
    for index in range(60):
        window._run_log_buffer.publish("Seed 捕捉", f"眨眼事件 {index}")
    settle()
    scrollbar = panel.text.verticalScrollBar()
    assert scrollbar.value() == scrollbar.maximum() > 0
    panel.follow_check.setChecked(False)
    scrollbar.setValue(0)
    window._run_log_buffer.publish("Seed 捕捉", "最后一条")
    settle()
    assert scrollbar.value() == 0
    panel.follow_check.setChecked(True)
    assert scrollbar.value() == scrollbar.maximum()
