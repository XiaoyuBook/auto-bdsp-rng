from types import SimpleNamespace
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from auto_bdsp_rng.automation.auto_rng import starter_flow
from auto_bdsp_rng.automation.auto_rng.models import AutoRngSeedResult, AutoRngTarget, ShinyCheckResult
from auto_bdsp_rng.automation.auto_rng.dialog_timing import DialogTimingEvent, DialogTimingResult
from auto_bdsp_rng.blink_detection.models import BlinkObservation
from auto_bdsp_rng.rng_core.seed import SeedState32
from auto_bdsp_rng.ui import main_window as mw
from tests.test_start_readiness import window, checks


def starter_config(window, species, tmp_path):
    from tests.test_starter_automation_ui import select
    select(window.auto_rng_tab, species)
    window.auto_rng_tab.starter_automation_check.setChecked(True)
    return window.auto_rng_tab.build_config()


def test_starter_capture_preset_is_in_memory_and_baseline_matches_clock(window, monkeypatch, tmp_path):
    config = starter_config(window, 390, tmp_path)
    saved = window._selected_auto_seed_config_path()
    before = Path(saved).read_bytes()
    state = SeedState32(1, 2, 3, 4)
    capture_configs, npcs = [], []
    observation = BlinkObservation.from_sequences([1, 0], [12, 24], offset_time=100.0)
    monkeypatch.setattr(window, "_call_on_ui_thread", lambda fn: fn())
    monkeypatch.setattr(mw.time, "perf_counter", lambda: 100.0)
    monkeypatch.setattr(mw, "capture_player_blinks", lambda config, **kwargs: capture_configs.append(config) or observation)
    monkeypatch.setattr(mw, "recover_seed_from_observation", lambda obs, npc: npcs.append(npc) or SimpleNamespace(state=state))
    services = window._build_auto_rng_services(config)
    result = services.capture_seed()
    actual = window.auto_rng_tab.runtime_insights.pending_inputs["实际采集配置"]
    assert (actual.white_delay, actual.advance_delay, actual.advance_delay_2) == (0, 41, 48)
    assert (actual.npc, actual.timeline_npc, actual.pokemon_npc) == (1, -1, 2)
    assert capture_configs[0].threshold == .7
    assert capture_configs[0].blink_count == 40 and npcs == [1]
    assert result.current_advances == 1 and result.seed == state and result.npc == 1
    assert Path(saved).read_bytes() == before


@pytest.mark.parametrize("species, slots", [(387, 0), (390, 1), (393, 2)])
def test_service_reuses_zoom_recovery_positions_cursor_and_hands_selection_to_shiny_ocr(window, monkeypatch, tmp_path, species, slots):
    config = starter_config(window, species, tmp_path)
    backend = window.easycon_tab._ensure_native_backend()
    backend.connect("COM3")
    window._video_source_connected = True
    monkeypatch.setattr(window, "_call_on_ui_thread", lambda fn: fn())
    zoom_calls, flows, measurements = [], [], []
    monkeypatch.setattr(window, "_recover_zoom_mode_with_preview_paused", lambda capture, run: zoom_calls.append(capture) or False)
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame[520:640, 200:1100] = 255
    monkeypatch.setattr(window, "_capture_preview_frame_for_config", lambda capture: frame)
    monkeypatch.setattr(mw, "read_ocr_text", lambda crop: "怎么回事？刚才那两人……")
    monkeypatch.setattr(mw, "measure_keyword_interval", lambda *args, **kwargs: measurements.append(kwargs) or DialogTimingResult(
        100, 101, 1, (DialogTimingEvent("first_seen", 100, 0, keyword="去吧"),),
    ))

    class Observer:
        def __init__(self, read, stop):
            self.read = read
        def start(self): pass
        def close(self): pass
        def set_enabled(self, enabled): pass
        def latest(self): return self.read()

    class Flow:
        def __init__(self, seed, target, delay, species, blink, **kwargs):
            self.kwargs = kwargs
            flows.append((seed, target, delay, species, blink))
        def run(self):
            assert "怎么回事" in self.kwargs["latest_text"]()
            self.kwargs["position_cursor"]()
            self.kwargs["on_balls"]()
            return starter_flow.selection_script(species)

    monkeypatch.setattr(starter_flow, "DialogObserver", Observer)
    monkeypatch.setattr(starter_flow, "StarterFlow", Flow)
    services = window._build_auto_rng_services(config)
    seed = AutoRngSeedResult(SeedState32(1, 2, 3, 4), current_advances=1, npc=1)
    target = AutoRngTarget(401, used_delay=43)
    assert services.run_starter_flow(seed, target, 43) == ShinyCheckResult(False, 1, "去吧", "战斗")
    assert len(zoom_calls) == 1 and flows[0][2:4] == (43, species)
    selection = backend.script_runs[-1][0]
    assert selection == "A 50\nWAIT 800\nUP 50\nWAIT 100\nA 50\n"
    if slots:
        assert backend.script_runs[0][0] == "RIGHT 100\nWAIT 120\n" * slots
    else:
        assert len(backend.script_runs) == 1
    assert measurements[0]["first_keyword"] == ("去吧", "上吧")
    assert measurements[0]["second_keyword"] == ("战斗", "戰鬥")
    assert callable(measurements[0]["second_capture_frame"])
    QApplication.processEvents()
    window._video_source_connected = False


def test_starter_readiness_requires_bundles_and_no_manual_scripts(window, tmp_path):
    starter_config(window, 387, tmp_path)
    result = checks(window)
    assert result["scripts"].state == "ok"
    window.auto_rng_tab.fixed_delay.setValue(78)
    result = checks(window)
    assert result["scripts"].state == "blocked"
    assert "小于 78" in result["scripts"].detail


@pytest.mark.parametrize("size", [(860, 600), (1150, 900)])
def test_starter_script_controls_fit_compact_and_wide_layouts(window, tmp_path, size):
    panel = window.auto_rng_tab
    starter_config(window, 390, tmp_path)
    window.tabs.setCurrentWidget(panel)
    window.resize(*size)
    panel._runtime_script_editor_expanded = True
    panel._set_runtime_script_summary_visible(True)
    QApplication.processEvents()
    panel.runtime_panel.ensureWidgetVisible(panel.starter_automation_check)
    QTest.qWait(20)
    assert panel.starter_automation_check.isVisible()
    assert panel.starter_automation_check.width() >= panel.starter_automation_check.sizeHint().width()
    assert panel.script_group.rect().contains(panel.starter_automation_check.geometry())
    assert panel.starter_script_description.isVisible()
    assert panel.script_group.rect().contains(panel.starter_script_description.geometry())
    assert not panel.starter_script_description.geometry().intersects(panel.starter_automation_check.geometry())
    assert panel.start_button.isVisible() and panel.stop_button.isVisible()
    screenshot = tmp_path / f"starter-{size[0]}x{size[1]}.png"
    assert window.grab().save(str(screenshot))
    print(f"Starter layout screenshot: {screenshot}")
