from threading import Event
from types import SimpleNamespace
from dataclasses import make_dataclass

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from auto_bdsp_rng.ui import main_window as module
from tests.test_guide_flow import guided
from tests.test_ui import app, isolated_ui_qsettings, SeedState32
from tests.test_script_guide import connected_guide, fake_run, open_for_guide, select, click_guided_tab
from tests.test_easycon_panel import process_events_until


def configure_capture(guided, monkeypatch, phase="capture_config", kind="seed"):
    w, p, c = guided
    monkeypatch.setattr(module, "save_project_xs_config", lambda *_args: None)
    monkeypatch.setattr(w, "_show_error", lambda *_args, **_kwargs: None)
    w._latest_preview_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    w.tabs.setCurrentWidget(w.project_xs_tab)
    c._go("auto_script_config", kind + ":" + phase)
    return w, c


def mock_capture(monkeypatch, *, fail=False, release=None):
    counts = []
    observation = SimpleNamespace(offset_time=module.time.perf_counter())
    state = SeedState32(0xAAAAAAAA, 0xBBBBBBBB, 0xCCCCCCCC, 0xDDDDDDDD)

    def capture(config, **kwargs):
        counts.append(config.blink_count)
        kwargs["progress_callback"](config.blink_count, config.blink_count)
        if release is not None:
            release.wait(2)
        if fail:
            raise RuntimeError("模拟识别失败")
        return observation

    monkeypatch.setattr(module, "capture_player_blinks", capture)
    result_type = make_dataclass("CaptureResult", [("state", object)])
    monkeypatch.setattr(module, "recover_seed_from_observation", lambda *_a, **_k: result_type(state))
    return counts, state


def finish_capture(w):
    process_events_until(lambda: not w._is_capturing())
    if w._capture_thread is not None:
        w._poll_capture_thread()


def test_40_blinks_waits_for_real_result_then_requires_confirmation(guided, monkeypatch):
    w, c = configure_capture(guided, monkeypatch)
    release = Event()
    counts, _ = mock_capture(monkeypatch, release=release)
    c.next()
    assert c.detail == "seed:capture_save"
    assert not c.overlay.tip.next_button.isEnabled()
    w.save_config_button.click()
    c.next()
    assert c.detail == "seed:capture_start"
    w.capture_button.click()
    token = w._capture_thread
    try:
        assert c.detail == "seed:capture_wait"
        c.script_guide.poll()
        assert counts == [40]
        assert not c.overlay.tip.next_button.isEnabled()
        w.manualCaptureFinished.emit("seed", object(), True, "")
        assert c.detail == "seed:capture_wait"  # An unrelated result cannot advance it.
        c.pause()
        release.set()
        finish_capture(w)
        c.begin_or_resume()
        c.script_guide.poll()
        assert c.detail == "seed:capture_result"
        assert c.overlay.tip.isVisible()
        assert c.overlay.focus_target is w.seed64_outputs[0]
        assert c.script_guide.capture.attempt[0] is token
        assert c.overlay.tip.next_button.isEnabled()
        c.next()
        assert c.detail == "advance:select"
        assert w.tabs.currentWidget() is w.project_xs_tab
        click_guided_tab(w, c, w.auto_rng_tab)
    finally:
        release.set()
        finish_capture(w)


@pytest.mark.parametrize("kind,script_phase", [("seed", "retry"), ("advance", "restore")])
def test_failed_capture_can_edit_config_or_correct_script(guided, monkeypatch, tmp_path, kind, script_phase):
    w, p, c = guided
    open_for_guide(guided, tmp_path, kind)
    w, c = configure_capture(guided, monkeypatch, "capture_start", kind)
    mock_capture(monkeypatch, fail=True)
    if kind == "advance":
        for box in w.seed32_inputs:
            box.setText("12345678")
    button = w.capture_button if kind == "seed" else w.reidentify_button
    button.click()
    finish_capture(w)
    assert c.detail == kind + ":capture_failed"
    assert c.overlay.tip.next_button.text() == "修改配置"
    c.next()
    assert c.detail == kind + ":capture_config"
    c._go("auto_script_config", kind + ":capture_start")
    button.click()
    finish_capture(w)
    c.previous()
    assert c.detail == kind + ":" + script_phase
    assert c.overlay.page_widget is w.easycon_tab


def test_actual_reidentify_success_gates_hit_and_changed_config_requires_retry(guided, monkeypatch):
    w, c = configure_capture(guided, monkeypatch, "capture_start", "advance")
    counts, state = mock_capture(monkeypatch)
    for box, word in zip(w.seed32_inputs, state.format_words()):
        box.setText(word)
    monkeypatch.setattr(w, "_reidentify_from_observation", lambda *_a, **_k: SimpleNamespace(advances=1000, state=state))
    w.reidentify_1_pk_npc.setChecked(False)
    w.reidentify_button.click()
    finish_capture(w)
    assert counts == [7]
    assert c.detail == "advance:capture_result"
    assert int(w.advances_value.text()) >= 1000
    assert c.script_guide.capture.succeeded()
    w.threshold.setValue(w.threshold.value() + 0.01)
    c.script_guide.poll()
    assert not c.overlay.tip.next_button.isEnabled()
    c.previous()
    assert c.detail == "advance:capture_config"
    c._go("auto_script_config", "advance:capture_start")
    w.reidentify_button.click()
    finish_capture(w)
    c.next()
    assert c.detail == "hit:select"
    assert w.tabs.currentWidget() is w.project_xs_tab
    click_guided_tab(w, c, w.auto_rng_tab)


def test_advance_requires_1000_and_controller_restoration_receives_keys(guided, connected_guide, tmp_path):
    w, p, c = guided
    path = open_for_guide(guided, tmp_path, "advance", "_目标帧数 = 2000\nWAIT 100\n")
    c.next()
    c.next()
    assert c.detail == "advance:advance_frames"
    assert not c.overlay.tip.next_button.isEnabled()
    assert c.overlay.tip.isVisible()
    assert c.overlay.focus_target is w.easycon_tab.editor
    w.easycon_tab.editor.setPlainText("_目标帧数 = 1000\nWAIT 100\n")
    c.script_guide.poll()
    c.next()
    assert c.detail == "advance:run"
    assert "1000" in path.read_text(encoding="utf-8")
    e = w.easycon_tab
    e.run_button.click()
    process_events_until(lambda: e.native_run_thread is None)
    c.script_guide.poll()
    c.previous()
    assert c.detail == "advance:restore"
    assert "第一行第一列" in c.overlay.copy.text()
    e._keyboard_hook_factory = SimpleNamespace(is_supported=lambda: False)
    e.controller_active_button.click()
    assert e._controller_overlay.isVisible()
    QTest.keyPress(e, Qt.Key.Key_W)
    assert connected_guide.get_report().ly == 1
    QTest.keyRelease(e, Qt.Key.Key_W)
    assert connected_guide.get_report().ly == 128
    c.next()
    assert c.detail == "advance:retry"
    assert not e.virtual_controller_enabled


@pytest.mark.parametrize("target,destination", [("hit", "reverse"), ("reverse", "ocr")])
def test_modified_script_replays_prerequisites_before_target_confirmation(guided, connected_guide, tmp_path, target, destination):
    w, p, c = guided
    paths = {}
    for kind in ("seed", "advance", "hit", "reverse"):
        paths[kind] = tmp_path / (kind + ".txt")
        paths[kind].write_text(("_目标帧数 = 1000\n" if kind == "advance" else "") + "WAIT 70\n", encoding="utf-8")
        select(p, kind, paths[kind])
    e = w.easycon_tab
    e.load_script(paths[target])
    w.tabs.setCurrentWidget(e)
    c._go("auto_script_config", target + ":run")
    e.run_button.click()
    process_events_until(lambda: e.native_run_thread is None)
    c.script_guide.poll()
    c.previous()
    assert c.detail == target + ":retry"
    e.editor.appendPlainText("WAIT 30")
    c.script_guide.poll()
    assert c.overlay.tip.next_button.text() == "修改完成"
    c.next()
    assert c.detail == "seed:run:" + target
    assert "WAIT 30" in paths[target].read_text(encoding="utf-8")
    order = ("seed", "advance", "hit") + (("reverse",) if target == "reverse" else ())
    for cycle in range(2):
        for kind in order:
            assert e.current_script_path == paths[kind]
            assert c.script_guide.replay_target == target
            if kind == "advance":
                assert c.script_guide.pos[1] == "advance_frames"
                c.next()
            e.run_button.click()
            process_events_until(lambda: e.native_run_thread is None)
            c.script_guide.poll()
            assert c.script_guide.completed()
            assert not c.overlay.tip.skip_button.isVisible()
            if kind == "seed":
                c.pause()
                c.begin_or_resume()
                assert c.script_guide.replay_target == target
            if kind == target and cycle == 0:
                c.previous()  # The game result can still be wrong after a completed run.
                assert c.script_guide.pos == (target, "retry")
                e.editor.appendPlainText("WAIT 20")
                c.script_guide.poll()
                c.next()
                assert c.detail == "seed:run:" + target
            else:
                c.next()
    assert c.detail == destination + ":select"
    assert w.tabs.currentWidget() is e
    click_guided_tab(w, c, p)
