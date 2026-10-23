from dataclasses import replace
from types import SimpleNamespace
import threading
import time

import cv2
import numpy as np
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from auto_bdsp_rng.blink_detection import load_project_xs_config, save_project_xs_config
from auto_bdsp_rng.blink_detection.models import BlinkObservation
from auto_bdsp_rng.rng_core import SeedState32
from auto_bdsp_rng.ui import main_window as mw
import auto_bdsp_rng.blink_detection.project_xs as project_xs_module
from auto_bdsp_rng.automation.auto_rng import starter_scripts
from tests.test_starter_automation_ui import select
from tests.test_start_readiness import window
from tests.ui_wait import wait_until


@pytest.fixture
def configs(window, monkeypatch, tmp_path):
    template = tmp_path / "eye.png"
    assert cv2.imwrite(str(template), np.full((8, 8), 127, dtype=np.uint8))
    config = window._config_from_form()
    config = replace(config, capture=replace(config.capture, eye_image_path=template, roi=(0, 0, 40, 40)),
                     reidentify_1_pk_npc=False)
    for name, npc in (("a.json", 1), ("b.json", 9)):
        path = tmp_path / name
        save_project_xs_config(replace(config, source_path=path, npc=npc), path)
    monkeypatch.setattr(mw, "PROJECT_XS_CONFIGS", tmp_path)
    window._refresh_config_list()
    return tmp_path / "a.json", tmp_path / "b.json", template


def binding_for(w, kind):
    if kind == "tid":
        return w.auto_tid_rng_tab, w.auto_tid_rng_tab.seed_config_binding
    return w.auto_rng_tab, getattr(w.auto_rng_tab, "reidentify_config_binding" if kind == "reidentify" else "seed_config_binding")


def open_editor(w, kind, path):
    panel, binding = binding_for(w, kind)
    w.tabs.setCurrentWidget(panel)
    binding.combo.setCurrentIndex(binding.combo.findData(str(path)))
    if panel is w.auto_rng_tab:
        panel.config_groups.reveal(binding)
    binding.edit_button.click()
    QApplication.processEvents()
    QApplication.processEvents()
    return panel, binding, w.seed_config_editor


@pytest.mark.parametrize("kind", ["seed", "tid", "reidentify"])
def test_editor_stays_in_task_and_cancels_without_changing_shared_files(window, configs, kind):
    first, second, _template = configs
    before = second.read_bytes()
    window.npc_count.setText("12")  # A separate unsaved manual-tool draft.
    panel, binding, editor = open_editor(window, kind, second)
    assert window.tabs.currentWidget() is panel
    assert binding.isAncestorOf(editor.editor)
    assert editor.editor.isVisible()
    scroll = panel.config_scroll if kind == "tid" else panel.config_panel
    assert 0 <= editor.title.mapTo(scroll.viewport(), QPoint(0, 0)).y() < 140
    assert "b.json" in editor.title.text()
    assert window.npc_count.text() == "9"
    assert window.capture_timing_group.isVisible()
    assert not window.threshold.isVisible()
    assert window.video_overlay.threshold.isVisible()
    assert not panel.start_button.isEnabled()
    assert not binding.combo.isEnabled()
    assert editor.save_button.isEnabled()
    assert window.monitor_sidebar.isVisible()
    editor.advanced_toggle.setChecked(False)
    assert not window.capture_timing_group.isVisible()
    editor.advanced_toggle.setChecked(True)
    assert window.capture_timing_group.isVisible()
    window.npc_count.setText("31")
    window.video_overlay.threshold.setValue(.73)
    editor.cancel_button.click()
    assert second.read_bytes() == before
    assert window.npc_count.text() == "12"
    assert window._selected_config_path() == str(first)
    assert not editor.active
    assert binding.combo.isEnabled()
    assert window.project_xs_config_scroll.isAncestorOf(window.capture_timing_group)


def test_save_changes_same_shared_file_and_preserves_independent_correction(window, configs):
    first, second, _template = configs
    _panel, binding, editor = open_editor(window, "seed", second)
    window.npc_count.setText("17")
    window.video_overlay.threshold.setValue(.81)
    editor.save_button.click()
    saved = load_project_xs_config(second)
    assert saved.npc == 17 and saved.capture.threshold == .81
    assert window.auto_tid_rng_tab.seed_config_binding.combo.currentData() == str(second)
    assert window.auto_rng_tab.reidentify_config_binding.combo.currentData() == str(first)
    assert not editor.active
    assert binding.combo.isEnabled()
    assert window.auto_rng_tab.start_button.isEnabled()


def test_save_failure_keeps_edit_open_and_file_intact(window, configs, monkeypatch):
    _first, second, _template = configs
    before = second.read_bytes()
    _panel, _binding, editor = open_editor(window, "seed", second)
    window.npc_count.setText("17")
    def fail(*_args):
        raise OSError("write denied")
    monkeypatch.setattr(mw, "save_project_xs_config", fail)
    monkeypatch.setattr(window, "_show_error", lambda *_args: None)
    editor.save_button.click()
    assert editor.active and editor.editor.isVisible()
    assert "write denied" in editor.status.text()
    assert window.npc_count.text() == "17"
    assert second.read_bytes() == before


@pytest.mark.parametrize("saved", [False, True])
def test_eye_selection_uses_draft_and_cancel_preserves_existing_template(window, configs, monkeypatch, tmp_path, saved):
    _first, second, template = configs
    original_image = template.read_bytes()
    original_config = second.read_bytes()
    output_dir = tmp_path / "eyes"
    monkeypatch.setattr(mw, "resource_path", lambda *_args: output_dir)
    _panel, _binding, editor = open_editor(window, "seed", second)
    window._latest_preview_frame = np.full((40, 40, 3), 255, dtype=np.uint8)
    window.apply_selected_eye((4, 4, 8, 8), preserve_roi=True)
    draft = window._eye_image_path
    assert draft != template and draft.is_file()
    assert template.read_bytes() == original_image
    if saved:
        editor.save_button.click()
        assert load_project_xs_config(second).capture.eye_image_path == draft
        assert draft.is_file()
    else:
        editor.cancel_button.click()
        assert second.read_bytes() == original_config
        assert not draft.exists()
    assert template.read_bytes() == original_image


@pytest.mark.parametrize("kind", ["seed", "tid", "reidentify"])
def test_capture_test_uses_unsaved_config_through_existing_pipeline(window, configs, monkeypatch, kind):
    _first, second, _template = configs
    before = second.read_bytes()
    _panel, _binding, editor = open_editor(window, kind, second)
    window.threshold.setValue(.67)
    window.npc_count.setText("3")
    state = SeedState32(1, 2, 3, 4)
    for field, word in zip(window.seed32_inputs, state.format_words()):
        field.setText(word)
    draft = window._config_from_form()
    assert draft.source_path == second and draft.npc == 3
    source = window._synchronize_preview_source(draft.capture)
    window._latest_preview_frame = np.zeros((40, 40, 3), dtype=np.uint8)
    window._latest_preview_source = source
    captures = []
    recovery_npcs = []
    completed = []
    window.manualCaptureFinished.connect(lambda *args: completed.append(args))

    def capture(config, **_kwargs):
        captures.append(config)
        return BlinkObservation.from_sequences([1, 0], [12, 24], offset_time=time.perf_counter())

    def recover(_observation, *, npc):
        recovery_npcs.append(npc)
        return SimpleNamespace(state=state)

    def reidentify(*_args, npc, **_kwargs):
        recovery_npcs.append(npc)
        return SimpleNamespace(state=state, advances=25)

    monkeypatch.setattr(mw, "capture_player_blinks", capture)
    monkeypatch.setattr(mw, "capture_pokemon_blinks", capture)
    monkeypatch.setattr(mw, "recover_seed_from_observation", recover)
    monkeypatch.setattr(mw, "recover_tidsid_seed_from_observation", lambda *_args: SimpleNamespace(state=state))
    monkeypatch.setattr(window, "_reidentify_from_observation", reidentify)
    editor.test_button.click()
    thread = window._capture_thread
    assert thread is not None
    wait_until(lambda: window._capture_thread is None)
    assert not thread.is_alive() and window._capture_error is None
    assert completed == [("tidsid" if kind == "tid" else kind, thread, True, "")]
    assert captures == [replace(draft.capture, blink_count={"seed": 40, "tid": 64, "reidentify": 7}[kind])]
    assert captures[0].threshold == .67
    assert captures[0].blink_count == {"seed": 40, "tid": 64, "reidentify": 7}[kind]
    assert recovery_npcs == ([] if kind == "tid" else [3])
    assert window._latest_preview_source == source
    assert "成功" in editor.status.text()
    assert editor.active and editor.save_button.isEnabled()
    assert second.read_bytes() == before


@pytest.fixture
def deferred_editor_capture(window, configs, monkeypatch):
    first, second, _template = configs
    state = SeedState32(1, 2, 3, 4)
    probe = SimpleNamespace(
        entered=threading.Event(), release=threading.Event(), released=threading.Event(),
        release_entered=threading.Event(), close_gate=threading.Event(),
        capture_entered=threading.Event(), capture_gate=threading.Event(),
        captures=[], recovery_npcs=[], completed=[], errors=[], read_error=None, close_error=None,
        release_attempts=[], backends=[],
        before=second.read_bytes(), first_before=first.read_bytes(), first=first, second=second,
    )
    probe.close_gate.set()
    probe.capture_gate.set()
    frame = np.full((40, 40, 3), 128, dtype=np.uint8)

    class Preview:
        keep_open_for_preview = True

        def __init__(self, config):
            self.config = config
            self.read_count = 0

        def read(self):
            self.read_count += 1
            probe.entered.set()
            assert probe.release.wait(3)
            if probe.read_error is not None:
                raise probe.read_error
            return frame

        def release(self):
            probe.release_attempts.append(threading.get_ident())
            probe.release_entered.set()
            assert probe.close_gate.wait(3)
            if probe.close_error is not None:
                raise probe.close_error
            probe.released.set()

    def capture(config, **_kwargs):
        assert probe.released.is_set()
        probe.capture_entered.set()
        assert probe.capture_gate.wait(3)
        probe.captures.append(config)
        return BlinkObservation.from_sequences([1, 0], [12, 24], offset_time=time.perf_counter())

    def recover(_observation, **kwargs):
        if "npc" in kwargs:
            probe.recovery_npcs.append(kwargs["npc"])
        return SimpleNamespace(state=state)

    def reidentify(*_args, npc, **_kwargs):
        probe.recovery_npcs.append(npc)
        return SimpleNamespace(state=state, advances=25)

    monkeypatch.setattr(mw, "PreviewFrameCapture", Preview)
    monkeypatch.setattr(mw, "render_eye_preview", lambda _config, raw: (raw.copy(), SimpleNamespace(match_score=.8)))
    monkeypatch.setattr(mw, "capture_player_blinks", capture)
    monkeypatch.setattr(mw, "capture_pokemon_blinks", capture)
    monkeypatch.setattr(mw, "recover_seed_from_observation", recover)
    monkeypatch.setattr(mw, "recover_tidsid_seed_from_observation", recover)
    monkeypatch.setattr(window, "_reidentify_from_observation", reidentify)
    monkeypatch.setattr(window, "_show_error", lambda _title, error, **_kwargs: probe.errors.append(str(error)))
    window.manualCaptureFinished.connect(lambda *args: probe.completed.append(args))

    def start(kind="seed", *, cached_preview=False, real_adapter=False, gated_capture=False):
        if gated_capture:
            probe.capture_gate.clear()
        if real_adapter:
            class PreviewBackend(Preview):
                def read(self):
                    return True, super().read()

            def open_backend(config):
                backend = PreviewBackend(config)
                probe.backends.append(backend)
                return backend

            monkeypatch.setattr(project_xs_module, "_open_capture_source", open_backend)
            monkeypatch.setattr(mw, "PreviewFrameCapture", project_xs_module.PreviewFrameCapture)
        # Keep the manual draft on the same physical source. Restoring it on
        # cancel must invalidate the pending operation even without a source
        # change, rather than relying on incidental camera changes to stop it.
        window.monitor_window.setChecked(False)
        window.camera.setText("0")
        _panel, _binding, probe.editor = open_editor(window, kind, second)
        window.threshold.setValue(.67)
        window.npc_count.setText("3")
        window.monitor_window.setChecked(False)
        window.camera.setText("0")
        for field, word in zip(window.seed32_inputs, state.format_words()):
            field.setText(word)
        probe.draft = window._config_from_form()
        if cached_preview:
            probe.close_gate.clear()
            window._preview_timer.start()
            window._update_preview_frame()
            wait_until(probe.entered.is_set)
            probe.release.set()
            wait_until(lambda: window._latest_preview_frame is frame)
            assert window._preview_thread is not None and not probe.released.is_set()
        probe.editor.test_button.click()
        wait_until(probe.entered.is_set)
        return probe

    yield start
    probe.close_error = None
    probe.release.set()
    probe.close_gate.set()
    probe.capture_gate.set()
    window._cancel_preview_interaction(restore_preview=False)
    window._cancel_capture()
    window._preview_timer.stop()
    window._release_preview_capture()
    wait_until(lambda: window._preview_thread is None and not window._preview_cleanup_threads
               and window._capture_thread is None)


@pytest.mark.parametrize("kind", ["seed", "tid", "reidentify"])
def test_editor_capture_waits_for_first_frame_and_keeps_unsaved_configuration(window, deferred_editor_capture, kind, monkeypatch, capsys):
    probe = deferred_editor_capture(kind)
    editor = probe.editor
    assert editor.testing and editor.active and window._capture_thread is None
    assert not editor.save_button.isEnabled() and editor.cancel_button.isEnabled()
    assert not window.capture_recognition_group.isEnabled()
    assert not window.capture_timing_group.isEnabled()
    assert not window.video_overlay.threshold.isEnabled()
    assert not window.raw_screenshot_button.isEnabled()
    editor.refresh()
    assert editor.testing and not editor.save_button.isEnabled()
    # A later form read must not replace the configuration accepted by this
    # test request, even when only annotation/NPC fields have changed.
    monkeypatch.setattr(window, "_config_from_form", lambda: replace(
        probe.draft, capture=replace(probe.draft.capture, threshold=.91), npc=19,
    ))
    editor.save_button.click()
    window.save_current_config()
    assert editor.active and probe.second.read_bytes() == probe.before
    probe.release.set()
    wait_until(lambda: bool(probe.completed))
    assert window._capture_thread is None and probe.released.is_set()
    assert probe.completed[0][0] == ("tidsid" if kind == "tid" else kind)
    assert probe.completed[0][2:] == (True, "")
    assert probe.captures == [replace(probe.draft.capture, blink_count={"seed": 40, "tid": 64, "reidentify": 7}[kind])]
    assert probe.recovery_npcs == ([] if kind == "tid" else [3])
    assert not probe.errors and not editor.testing
    assert editor.active and editor.save_button.isEnabled()
    assert "成功" in editor.status.text()
    assert window._selected_config_path() == str(probe.second)
    assert window.npc_count.text() == "3" and window.threshold.value() == .67
    assert probe.first.read_bytes() == probe.first_before and probe.second.read_bytes() == probe.before
    stderr = capsys.readouterr().err
    assert "RuntimeError" not in stderr and "already deleted" not in stderr, stderr


@pytest.mark.parametrize("action", ["stop", "cancel", "close", "source", "timeout", "read-error"])
def test_editor_capture_cancelled_before_first_frame_never_starts_late_capture(window, deferred_editor_capture, action, capsys):
    probe = deferred_editor_capture()
    editor = probe.editor
    assert window._capture_thread is None and not probe.captures
    if action == "stop":
        editor.test_button.click()
    elif action == "cancel":
        editor.cancel_button.click()
    elif action == "close":
        assert window.close() is False
    elif action == "source":
        window.camera.setText("1")
    elif action == "timeout":
        assert window._preview_action is not None
        window._preview_action.deadline = time.monotonic() - 1
        window._poll_preview_action()
    else:
        probe.read_error = OSError("camera removed")
    probe.release.set()
    wait_until(lambda: window._preview_thread is None and not window._preview_cleanup_threads
               and window._capture_thread is None)
    if action == "close":
        wait_until(lambda: window._close_accepted)
        assert window.easycon_tab._shutdown_complete
    # Drain the poll and any already queued continuation after the late frame.
    window._poll_first_preview_frame()
    QApplication.processEvents()
    wait_until(lambda: window._capture_thread is None)
    assert not probe.captures and not probe.completed
    assert window._preview_action is None and not window._first_preview_pending
    assert not editor.testing
    if action in {"cancel", "close"}:
        assert not editor.active and window._selected_config_path() == str(probe.first)
    else:
        assert editor.active and editor.save_button.isEnabled() and editor.cancel_button.isEnabled()
        assert window.capture_recognition_group.isEnabled()
    assert probe.first.read_bytes() == probe.first_before and probe.second.read_bytes() == probe.before
    assert probe.errors == (["camera removed"] if action == "read-error" else [])
    stderr = capsys.readouterr().err
    assert "RuntimeError" not in stderr and "already deleted" not in stderr, stderr


def test_editor_capture_waits_for_cached_preview_owner_to_release(window, deferred_editor_capture, capsys):
    probe = deferred_editor_capture(cached_preview=True)
    owner = window._preview_capture
    wait_until(probe.release_entered.is_set)
    assert probe.editor.testing and window._capture_thread is None
    assert not probe.editor.save_button.isEnabled() and not probe.released.is_set()
    assert not probe.captures and not probe.completed
    probe.close_gate.set()
    wait_until(lambda: bool(probe.completed))
    assert probe.released.is_set() and window._capture_thread is None
    assert window._preview_capture is not owner and window._preview_timer.isActive()
    assert probe.captures == [probe.draft.capture] and probe.recovery_npcs == [3]
    assert probe.completed[0][2:] == (True, "")
    assert probe.editor.active and not probe.editor.testing and probe.editor.save_button.isEnabled()
    assert probe.second.read_bytes() == probe.before and not probe.errors
    stderr = capsys.readouterr().err
    assert "RuntimeError" not in stderr and "already deleted" not in stderr, stderr


@pytest.mark.parametrize("kind", ["seed", "tid", "reidentify"])
@pytest.mark.parametrize("preview_running", [True, False], ids=["running", "stopped"])
def test_editor_real_adapter_capture_restores_only_the_previous_preview_state(window, deferred_editor_capture, kind, preview_running, capsys):
    probe = deferred_editor_capture(kind, cached_preview=preview_running, real_adapter=True)
    owner = window._preview_capture
    assert type(owner) is project_xs_module.PreviewFrameCapture
    assert owner._video is probe.backends[0] and not owner._released
    assert window._preview_action.started_preview is (not preview_running)
    assert window._capture_thread is None and not probe.captures
    probe.release.set()
    probe.close_gate.set()
    wait_until(lambda: bool(probe.completed))
    assert probe.completed[0][0] == ("tidsid" if kind == "tid" else kind)
    assert probe.completed[0][2:] == (True, "")
    assert owner._released and probe.released.is_set()
    assert probe.editor.active and not probe.editor.testing
    assert probe.editor.save_button.isEnabled() and not probe.errors
    assert probe.captures == [replace(probe.draft.capture, blink_count={"seed": 40, "tid": 64, "reidentify": 7}[kind])]
    assert probe.recovery_npcs == ([] if kind == "tid" else [3])
    assert window._preview_timer.isActive() is preview_running
    assert not window._resume_preview_after_capture
    if preview_running:
        window._update_preview_frame()
        wait_until(lambda: len(probe.backends) == 2 and probe.backends[1].read_count > 0
                   and window._preview_capture is not None
                   and window._latest_preview_source == window._preview_source)
        assert window._preview_capture._video is probe.backends[1]
        assert probe.backends[1].config == probe.draft.capture
    else:
        assert len(probe.backends) == 1 and window._preview_capture is None
        assert window._preview_thread is None
    assert window.threshold.value() == .67 and window.npc_count.text() == "3"
    assert probe.first.read_bytes() == probe.first_before and probe.second.read_bytes() == probe.before
    stderr = capsys.readouterr().err
    assert "RuntimeError" not in stderr and "already deleted" not in stderr, stderr


@pytest.mark.parametrize("action", ["stop", "cancel", "source", "close"])
@pytest.mark.parametrize("real_adapter", [False, True], ids=["fake_adapter", "real_adapter"])
def test_editor_capture_cancelled_during_preview_release_never_starts_late_capture(window, deferred_editor_capture, action, real_adapter, capsys):
    probe = deferred_editor_capture(cached_preview=True, real_adapter=real_adapter)
    owner = window._preview_capture
    wait_until(probe.release_entered.is_set)
    assert window._capture_thread is None and not probe.released.is_set()
    if action == "stop":
        probe.editor.test_button.click()
    elif action == "cancel":
        probe.editor.cancel_button.click()
    elif action == "source":
        window.camera.setText("1")
    else:
        assert window.close() is False
    probe.close_gate.set()
    wait_until(lambda: window._preview_thread is None and not window._preview_cleanup_threads)
    QApplication.processEvents()
    if action == "close":
        wait_until(lambda: window._close_accepted)
        assert window.easycon_tab._shutdown_complete
    assert window._capture_thread is None and not probe.captures and not probe.completed
    assert not window._preview_timer.isActive() and not window._resume_preview_after_capture
    if real_adapter:
        assert owner._released and len(probe.backends) == 1
    assert window._preview_action is None and not window._first_preview_pending
    assert not probe.editor.testing and not probe.errors
    assert probe.editor.active == (action in {"stop", "source"})
    assert probe.first.read_bytes() == probe.first_before and probe.second.read_bytes() == probe.before
    stderr = capsys.readouterr().err
    assert "RuntimeError" not in stderr and "already deleted" not in stderr, stderr


@pytest.mark.parametrize("action", ["source", "source-stop", "close"])
def test_editor_real_adapter_capture_never_resumes_preview_after_source_change_or_close(window, deferred_editor_capture, action, capsys):
    probe = deferred_editor_capture(cached_preview=True, real_adapter=True, gated_capture=True)
    probe.close_gate.set()
    wait_until(probe.capture_entered.is_set)
    thread = window._capture_thread
    assert thread is not None and window._resume_preview_after_capture
    assert not window._preview_timer.isActive() and window._preview_capture is None
    if action == "close":
        assert window.close() is False
    else:
        window.camera.setText("1")
        if action == "source-stop":
            probe.editor.test_button.click()
    probe.capture_gate.set()
    wait_until(lambda: not thread.is_alive())
    window._poll_capture_thread()
    if action == "close":
        wait_until(lambda: window._close_accepted)
        assert not probe.editor.active and window.easycon_tab._shutdown_complete
    else:
        assert probe.editor.active and not probe.editor.testing
        assert window.camera.text() == "1"
    assert window._capture_thread is None and not window._resume_preview_after_capture
    assert not window._preview_timer.isActive() and window._preview_capture is None
    assert window._preview_thread is None and len(probe.backends) == 1
    assert probe.completed[0][2] is (action == "source")
    assert not probe.errors
    assert probe.first.read_bytes() == probe.first_before and probe.second.read_bytes() == probe.before
    stderr = capsys.readouterr().err
    assert "RuntimeError" not in stderr and "already deleted" not in stderr, stderr


@pytest.mark.parametrize("cached_preview", [False, True])
@pytest.mark.parametrize("real_adapter", [False, True], ids=["fake_adapter", "real_adapter"])
def test_editor_capture_release_failure_restores_controls_and_retry_uses_retained_owner(window, deferred_editor_capture, cached_preview, real_adapter, capsys):
    probe = deferred_editor_capture(cached_preview=cached_preview, real_adapter=real_adapter)
    owner = window._preview_capture
    if real_adapter:
        assert type(owner) is project_xs_module.PreviewFrameCapture
        assert owner._video is probe.backends[0] and len(probe.backends) == 1
    probe.close_error = OSError("release failed")
    probe.release.set()
    probe.close_gate.set()
    wait_until(lambda: not probe.editor.testing)
    assert not probe.captures and not probe.completed and not probe.errors
    assert not probe.released.is_set() and window._preview_capture is owner
    if real_adapter:
        assert not owner._released
    assert window._preview_thread is None and not window._preview_cleanup_threads
    assert probe.editor.active and probe.editor.save_button.isEnabled() and probe.editor.cancel_button.isEnabled()
    assert window.capture_recognition_group.isEnabled() and window.video_overlay.threshold.isEnabled()
    assert "release failed" in probe.editor.status.text()
    assert probe.second.read_bytes() == probe.before
    probe.close_error = None
    probe.editor.test_button.click()
    wait_until(lambda: bool(probe.completed))
    assert probe.completed[0][2:] == (True, "")
    assert probe.captures == [probe.draft.capture] and probe.recovery_npcs == [3]
    assert probe.released.is_set() and window._preview_capture is None
    assert window._preview_thread is None and not window._preview_cleanup_threads
    assert len(probe.release_attempts) == 2
    if real_adapter:
        assert owner._released and len(probe.backends) == 1
        owner.release()
        assert len(probe.release_attempts) == 2
    assert all(thread_id != threading.get_ident() for thread_id in probe.release_attempts)
    assert not probe.editor.testing and probe.editor.active and not probe.errors
    assert probe.first.read_bytes() == probe.first_before and probe.second.read_bytes() == probe.before
    stderr = capsys.readouterr().err
    assert "RuntimeError" not in stderr and "already deleted" not in stderr, stderr


@pytest.mark.parametrize("real_adapter", [False, True], ids=["fake_adapter", "real_adapter"])
def test_editor_capture_failed_preview_release_refuses_close_then_user_can_retry_close(window, deferred_editor_capture, monkeypatch, real_adapter, capsys):
    from PySide6.QtWidgets import QMessageBox

    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, title, message: warnings.append((title, message)))
    probe = deferred_editor_capture(real_adapter=real_adapter)
    owner = window._preview_capture
    if real_adapter:
        assert type(owner) is project_xs_module.PreviewFrameCapture
        assert owner._video is probe.backends[0] and len(probe.backends) == 1
    probe.close_error = OSError("release failed")
    probe.release.set()
    wait_until(lambda: not probe.editor.testing)
    assert window._preview_capture is not None
    assert window.close() is False
    wait_until(lambda: bool(warnings))
    assert not window._close_accepted and not window._is_closing
    assert window.isVisible() and window._preview_capture is owner
    if real_adapter:
        assert not owner._released
    assert window._preview_thread is None and not window._preview_cleanup_threads
    assert "预览" in warnings[0][0] and "重试" in warnings[0][1]
    assert probe.editor.active and probe.editor.save_button.isEnabled() and not probe.editor.testing
    assert not probe.captures and not probe.completed and not probe.errors
    probe.close_error = None
    window.close()
    wait_until(lambda: window._close_accepted)
    assert window.easycon_tab._shutdown_complete and window._preview_capture is None
    assert window._preview_thread is None and not window._preview_cleanup_threads
    assert not probe.editor.active and not probe.captures and not probe.completed
    assert len(probe.release_attempts) == 3
    if real_adapter:
        assert owner._released and len(probe.backends) == 1
        owner.release()
        assert len(probe.release_attempts) == 3
    assert probe.first.read_bytes() == probe.first_before and probe.second.read_bytes() == probe.before
    stderr = capsys.readouterr().err
    assert "RuntimeError" not in stderr and "already deleted" not in stderr, stderr


def test_tools_entry_opens_manual_capture_without_primary_navigation(window):
    window.tabs.setCurrentWidget(window.auto_tid_rng_tab)
    index = window.tabs.indexOf(window.project_xs_tab)
    assert not window.tabs.tabBar().isTabVisible(index)
    window.manual_seed_action.trigger()
    QApplication.processEvents()
    assert window.tabs.currentWidget() is window.project_xs_tab
    assert window.capture_button.isVisible() and window.tidsid_button.isVisible()
    assert window.reidentify_button.isVisible()
    window.manual_seed_return_button.click()
    assert window.tabs.currentWidget() is window.auto_tid_rng_tab


def test_switching_edit_targets_keeps_current_draft_and_blocks_start(window, configs):
    first, second, _template = configs
    _panel, binding, editor = open_editor(window, "seed", second)
    window.npc_count.setText("27")
    window._edit_automation_config(str(first), window.auto_rng_tab.reidentify_config_binding)
    assert editor.binding is binding
    assert window.npc_count.text() == "27"
    assert window._selected_config_path() == str(second)
    assert not window._ensure_automation_start_available()
    editor.cancel_button.click()
    assert window._ensure_automation_start_available()


@pytest.mark.parametrize("opened,requested", [
    ("seed", "seed"), ("seed", "tid"), ("tid", "reidentify"), ("reidentify", "seed"),
])
def test_edit_button_reveals_existing_draft_after_switching_pages(window, configs, opened, requested):
    first, second, _template = configs
    before = [path.read_bytes() for path in (first, second)]
    panel, binding, editor = open_editor(window, opened, second)
    window.npc_count.setText("27")
    window.tabs.setCurrentWidget(window.bdsp_tab)
    requested_panel, requested_binding = binding_for(window, requested)
    window.tabs.setCurrentWidget(requested_panel)
    window.reveal_page_configuration(requested_panel)
    requested_panel.config_groups.reveal(requested_binding)
    scroll = requested_panel.config_scroll if requested == "tid" else requested_panel.config_panel
    scroll.ensureWidgetVisible(requested_binding.edit_button)
    QApplication.processEvents()
    QApplication.processEvents()

    button = requested_binding.edit_button
    assert button.isVisible() and button.isEnabled()
    assert window.childAt(button.mapTo(window, button.rect().center())) is button
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    QApplication.processEvents()
    QApplication.processEvents()
    assert window.tabs.currentWidget() is panel
    assert editor.editor.isVisible() and editor.binding is binding
    assert window.npc_count.text() == "27"
    assert window._selected_config_path() == str(second)
    window._refresh_task_config_bindings()
    QApplication.processEvents()
    for view in editor._bindings():
        assert not view.combo.isEnabled()
        assert view.edit_button.isEnabled()
    assert [path.read_bytes() for path in (first, second)] == before
    editor.cancel_button.click()
    for view in editor._bindings():
        assert view.combo.isEnabled()


@pytest.mark.parametrize("busy", ["capture", "selection", "preparing", "running"])
def test_blocked_edit_shows_reason_at_button_and_can_retry_when_idle(window, configs, monkeypatch, busy):
    _first, second, _template = configs
    before = second.read_bytes()
    panel, binding = binding_for(window, "seed")
    window.tabs.setCurrentWidget(panel)
    binding.combo.setCurrentIndex(binding.combo.findData(str(second)))
    with monkeypatch.context() as state:
        if busy == "capture":
            state.setattr(window, "_capture_thread", object())
        elif busy == "selection":
            state.setattr(window, "_selection_mode", "roi")
        elif busy == "preparing":
            state.setattr(window.auto_tid_rng_tab, "_preparing", True)
        else:
            state.setattr(window.auto_tid_rng_tab, "_runner_thread", object())
        binding.edit_button.click()
        assert not window.seed_config_editor.active
        assert binding.edit_message.isVisible()
        assert "请先结束捕捉、框选或自动任务" in binding.edit_message.text()
    window.statusBar().showMessage("其他状态更新")
    QApplication.processEvents()
    assert binding.edit_message.isVisible()
    binding.edit_button.click()
    assert window.seed_config_editor.active and window.seed_config_editor.editor.isVisible()
    assert not binding.edit_message.isVisible()
    window.seed_config_editor.cancel_button.click()
    assert second.read_bytes() == before


def test_saved_manual_tool_index_restores_to_primary_task(window):
    window._profile_settings.setValue(mw.MAIN_WINDOW_CURRENT_TAB_KEY, window.tabs.indexOf(window.project_xs_tab))
    window._restore_window_geometry()
    assert window.tabs.currentWidget() is window.auto_rng_tab


def test_cancel_restores_imported_windows_path_spelling_and_manual_draft(window, configs):
    first, second, _template = configs
    imported_path = first.as_posix()
    window.config_combo.addItem(first.name, imported_path)
    window.config_combo.setCurrentIndex(window.config_combo.count() - 1)
    window.npc_count.setText("11")
    _panel, _binding, editor = open_editor(window, "seed", second)
    editor.cancel_button.click()
    assert window._selected_config_path() == imported_path
    assert window._config_from_form().npc == 11


@pytest.fixture
def starter_file(window, configs, monkeypatch):
    first, second, _template = configs
    path = first.parent / "config_starter.json"
    original = replace(load_project_xs_config(second), source_path=path,
                       white_delay=.25, advance_delay=99, npc=9)
    save_project_xs_config(original, path)
    monkeypatch.setattr(starter_scripts, "resource_path", lambda *_parts: path)
    window._refresh_config_list()
    window.seed_config_combo.setCurrentIndex(window.seed_config_combo.findData(str(second)))
    return path


@pytest.mark.parametrize("species", [387, 390, 393])
def test_starter_pins_its_seed_file_without_changing_tid_or_normal_selection(window, configs, starter_file, species):
    _first, second, _template = configs
    panel = window.auto_rng_tab
    binding = panel.seed_config_binding
    select(panel, species)
    panel.starter_automation_check.setChecked(True)
    assert panel.build_config().seed_config_path == str(starter_file)
    assert binding.combo.currentData() == str(starter_file)
    assert not binding.combo.isEnabled() and binding.edit_button.isEnabled()
    window._refresh_config_list()
    QApplication.processEvents()
    assert binding.combo.count() == 1 and binding.combo.currentData() == str(starter_file)
    assert window._selected_auto_seed_config_path() == str(second)
    assert window.auto_tid_rng_tab.seed_config_binding.combo.currentData() == str(second)
    assert "怎么回事？刚才那两人" in panel.starter_script_description.text()
    select(panel, 480)
    assert not panel.starter_automation_check.isChecked()
    assert binding.combo.isEnabled() and binding.combo.currentData() == str(second)


@pytest.mark.parametrize("saved", [False, True])
def test_starter_editor_only_changes_roi_and_threshold_and_restores_normal_editing(window, configs, starter_file, saved, monkeypatch):
    _first, second, template = configs
    original = load_project_xs_config(starter_file)
    before = starter_file.read_bytes()
    manual_before = second.read_bytes()
    template_before = template.read_bytes()
    errors = []
    monkeypatch.setattr(window, "_show_error", lambda title, error: errors.append(error))
    panel = window.auto_rng_tab
    select(panel, 390)
    panel.starter_automation_check.setChecked(True)
    panel, binding, editor = open_editor(window, "seed", starter_file)
    assert editor.starter_locked
    assert window.select_roi_button.isEnabled() and window.video_overlay.threshold.isEnabled()
    assert not window.capture_timing_group.isEnabled()
    assert not window.npc_count.isEnabled() and not window.raw_screenshot_button.isEnabled()
    assert not panel.starter_automation_check.isEnabled()
    editor.refresh()
    assert not window.capture_timing_group.isEnabled()
    assert (window._config_from_form().npc, window._config_from_form().advance_delay) == (1, 41)
    window.video_overlay.threshold.setValue(.79)
    window._latest_preview_frame = np.full((40, 40, 3), 127, dtype=np.uint8)
    roi = (4, 6, 30, 28)
    window.apply_selected_roi(roi)
    # Even a stale signal cannot change a fixed template or non-editable field.
    window.apply_selected_eye((0, 0, 8, 8))
    window.npc_count.setText("invalid")
    window.camera.setText("invalid")
    window._eye_image_path = template.with_name("wrong.png")
    assert window._config_from_form().capture.eye_image_path == template
    if saved:
        editor.save_button.click()
        assert load_project_xs_config(starter_file) == replace(
            original, capture=replace(original.capture, roi=roi, threshold=.79),
        )
    else:
        editor.cancel_button.click()
        assert starter_file.read_bytes() == before
    assert not editor.active
    assert not binding.combo.isEnabled() and binding.edit_button.isEnabled()
    assert window.auto_tid_rng_tab.seed_config_binding.combo.currentData() == str(second)
    assert second.read_bytes() == manual_before and template.read_bytes() == template_before
    panel.starter_automation_check.setChecked(False)
    assert binding.combo.isEnabled() and binding.combo.currentData() == str(second)
    _panel, _binding, editor = open_editor(window, "seed", second)
    assert not errors
    assert not editor.starter_locked
    assert window.npc_count.isEnabled() and window.raw_screenshot_button.isEnabled()
    editor.cancel_button.click()


def test_missing_starter_config_does_not_fall_back_to_another_seed_file(window, configs, starter_file, monkeypatch):
    from tests.test_start_readiness import checks

    missing = starter_file.parent / "missing" / "config_starter.json"
    monkeypatch.setattr(starter_scripts, "resource_path", lambda *_parts: missing)
    panel = window.auto_rng_tab
    select(panel, 387)
    panel.starter_automation_check.setChecked(True)
    assert window._selected_auto_rng_seed_config_path() == str(missing)
    assert panel.seed_config_binding.combo.currentData() == str(missing)
    assert not panel.seed_config_binding.combo.isEnabled()
    seed = checks(window)["seed"]
    assert seed.state == "blocked" and "config_starter.json" in seed.detail
