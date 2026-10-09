from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from auto_bdsp_rng.automation.auto_rng import AutoRngPhase, AutoRngSeedResult
from auto_bdsp_rng.automation.auto_tid_rng import AutoTidRngConfig, AutoTidRngPhase
from auto_bdsp_rng.rng_core import SeedState32
from auto_bdsp_rng.ui import main_window as mw
from tests.test_monitor_workspace import settle
from tests.test_start_readiness import window


def show_frame(w, width=1280, height=720):
    frame = np.full((height, width, 3), 80, dtype=np.uint8)
    w._latest_preview_frame = frame
    w._latest_annotated_preview_frame = frame.copy()
    w._refresh_preview_presentation()
    return frame


def set_seed(w):
    seed = SeedState32(0xFFFFFFFF, 0xFFFFFFFF, 0x87654321, 0x12345678)
    for field, word in zip(w.seed32_inputs, seed.format_words()):
        field.setText(word)
    w._sync_seed64_from_state32()
    w._update_auto_rng_header(advances=12568)
    return seed


@pytest.mark.parametrize("sidebar_width,frame_size", [(310, (1280, 720)), (640, (1280, 720)), (310, (640, 480))])
def test_information_fits_inside_video_without_collisions_or_truncated_seeds(window, monkeypatch, sidebar_width, frame_size):
    w = window
    monkeypatch.setattr(w, "_screen_available_geometry", lambda: QRect(0, 0, 2200, 1400))
    w.resize(1500, 960)
    w.workspace_splitter.setSizes([800, sidebar_width])
    settle()
    frame = show_frame(w, *frame_size)
    set_seed(w)
    w.video_overlay.set_capture_progress(12, 40)
    w.video_overlay.set_match_score(0.9876)
    settle()
    w._refresh_preview_presentation()

    image = w.preview_label._pixmap_rect
    left = w.video_overlay.seed_panel
    right = w.video_overlay.status_panel
    assert left.parentWidget() is right.parentWidget() is w.preview_label
    assert left.isVisible() and right.isVisible()
    assert image.contains(left.geometry()) and image.contains(right.geometry())
    assert not left.geometry().intersects(right.geometry())
    assert left.x() == image.left() + 6
    assert right.geometry().right() == image.right() - 6
    for field in w.seed64_outputs:
        assert len(field.text()) == 16
        assert field.width() >= field.fontMetrics().horizontalAdvance(field.text()) + 4
        field.setFocus()
        QTest.keyClick(field, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(field, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        assert QApplication.clipboard().text() == field.text()
    assert w.advances_value.text() == "12568"
    assert w.progress_value.text() == "12/40"
    assert w.video_overlay.score_value.text() == "0.9876"
    assert np.all(frame == 80)
    assert not w.monitor_preview_scroll.verticalScrollBar().maximum()


def test_selection_hides_information_and_drag_uses_original_image_coordinates(window):
    w = window
    show_frame(w)
    set_seed(w)
    w.video_overlay.set_capture_progress(8, 40)
    settle()
    label = w.preview_label
    w._set_preview_selection_enabled(True)
    assert not w.seed_group.isVisible()
    assert not w.video_overlay.status_panel.isVisible()
    # Drag starts where the Seed box was. Child widgets must not intercept it.
    start = label._pixmap_rect.topLeft() + QPoint(12, 12)
    end = start + QPoint(80, 40)
    selected = []
    label.roiSelected.disconnect()
    label.roiSelected.connect(selected.append)
    target = label.childAt(start) or label
    assert target is label
    QTest.mousePress(target, Qt.MouseButton.RightButton, pos=start)
    QTest.mouseMove(target, end)
    QTest.mouseRelease(target, Qt.MouseButton.RightButton, pos=end)
    assert len(selected) == 1
    scale = 1280 / label._pixmap_rect.width()
    assert selected[0][:2] == (round(12 * scale), round(12 * scale))
    w._set_preview_selection_enabled(False)
    assert w.seed_group.isVisible() and w.video_overlay.status_panel.isVisible()


@pytest.mark.parametrize("phase", [AutoRngPhase.CAPTURE_SEED, AutoRngPhase.REIDENTIFY, AutoTidRngPhase.CAPTURE_TIDSID])
def test_auto_recapture_hides_old_seed_until_new_result_and_clears_on_disconnect(window, monkeypatch, phase):
    w = window
    show_frame(w)
    seed = set_seed(w)
    w._apply_common_auto_header_progress(SimpleNamespace(), phase.value, 1, task_label="定点")
    assert not w.seed_group.isVisible()
    w._handle_auto_capture_progress(12, 40)
    assert w.video_overlay.status_panel.isVisible()
    assert w.progress_value.text() == "12/40"
    # Solving after the last blink still counts as capture, and must hide old data.
    w._handle_auto_capture_progress(40, 40)
    assert not w.seed_group.isVisible()
    monkeypatch.setattr(w, "_sync_bdsp_data_from_auto_rng", lambda *args: None)
    w._handle_auto_seed_captured(AutoRngSeedResult(seed=seed, current_advances=43, npc=0))
    assert w.seed_group.isVisible()
    assert w.advances_value.text() == "43"
    assert not w.video_overlay.progress_row.isVisible()
    w._apply_common_auto_header_progress(SimpleNamespace(), "搜索目标", 1, task_label="定点")
    w._apply_common_auto_header_progress(SimpleNamespace(), phase.value, 2, task_label="定点")
    w._apply_common_auto_header_progress(SimpleNamespace(), "失败", 2, task_label="定点")
    assert not w.seed_group.isVisible()
    assert not w.video_overlay.progress_row.isVisible()
    w._clear_video_source_preview()
    assert not w.video_overlay.status_panel.isVisible()


def test_matching_uses_live_result_and_threshold_is_synced_and_locked_during_capture(window, monkeypatch):
    w = window
    frame = show_frame(w)
    monkeypatch.setattr(w, "_read_live_preview_frame", lambda config: frame)
    monkeypatch.setattr(mw, "render_eye_preview", lambda config, frame: (frame.copy(), SimpleNamespace(match_score=0.87654)))
    w._update_preview_frame()
    overlay = w.video_overlay
    assert overlay.score_value.text() == "0.8765"
    assert overlay.score_value.property("blinking")
    assert overlay.match_row.isVisible()
    overlay.threshold.setValue(0.83)
    assert w.threshold.value() == 0.83
    assert not overlay.score_value.property("blinking")
    w.threshold.setValue(0.92)
    assert overlay.threshold.value() == 0.92
    overlay.set_capture_progress(0, 40)
    assert not overlay.threshold.isEnabled()
    overlay.finish_capture()
    assert overlay.threshold.isEnabled()
    monkeypatch.setattr(mw, "render_eye_preview", lambda *args: (_ for _ in ()).throw(ValueError("invalid ROI")))
    w._update_preview_frame()
    assert not overlay.match_row.isVisible()
    assert not overlay.status_panel.isVisible()
    assert w.monitor_frame_info.text() == "1280 × 720"


def test_tid_capture_service_publishes_new_seed_in_video(window, monkeypatch, tmp_path):
    w = window
    show_frame(w)
    set_seed(w)
    seed = SeedState32(1, 2, 3, 4)

    def capture(config, **kwargs):
        assert not w.seed_group.isVisible()
        kwargs["progress_callback"](12, 64)
        assert w.progress_value.text() == "12/64"
        assert w.video_overlay.progress_row.isVisible()
        return SimpleNamespace()

    monkeypatch.setattr(mw, "capture_pokemon_blinks", capture)
    monkeypatch.setattr(mw, "recover_tidsid_seed_from_observation", lambda _: SimpleNamespace(state=seed))
    services = w._build_auto_tid_rng_services(AutoTidRngConfig(script_dir=tmp_path))
    services.capture_seed()
    assert w.seed_group.isVisible()
    assert [field.text() for field in w.seed64_outputs] == list(seed.format_seed64_pair())
    assert not w.video_overlay.progress_row.isVisible()
    assert w.advances_value.text() == "0"


@pytest.mark.parametrize("handler", ["_handle_auto_rng_run_state_changed", "_handle_auto_tid_run_state_changed"])
def test_worker_stopping_without_terminal_progress_hides_capture_count(window, handler):
    w = window
    show_frame(w)
    set_seed(w)
    w._handle_auto_capture_progress(12, 40)
    getattr(w, handler)(False)
    assert not w.video_overlay.progress_row.isVisible()
    assert not w.seed_group.isVisible()


def test_completed_manual_seed_remains_copyable_after_video_disconnect(window):
    w = window
    show_frame(w)
    set_seed(w)
    w._clear_video_source_preview()
    w.readiness.navigate("seed_value")
    settle()
    field = w.seed64_outputs[0]
    assert field.isVisible()
    assert w.preview_label.contentsRect().contains(w.seed_group.geometry())
    field.selectAll()
    field.copy()
    assert QApplication.clipboard().text() == "FFFFFFFFFFFFFFFF"
