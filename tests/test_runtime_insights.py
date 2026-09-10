from dataclasses import replace
from pathlib import Path

from auto_bdsp_rng.automation.auto_rng.models import AutoRngPhase, AutoRngProgress
from auto_bdsp_rng.automation.auto_tid_rng import AutoTidRngPhase, AutoTidRngProgress
from tests.test_start_readiness import window  # isolated real Qt window
import pytest


def test_runtime_snapshot_detaches_inputs_and_ignores_draft_edits(window):
    p = window.auto_rng_tab
    inputs = {"targets": [{"species": 25}], "path": Path("seed.json")}
    p.runtime_insights.pending_inputs = inputs
    config = p.build_config()
    p.runtime_insights.start(config)
    inputs["targets"][0]["species"] = 99
    p.max_wait_frames.setValue(config.max_wait_frames + 10)
    p._save_config_state()
    snap = p.runtime_insights.snapshot()
    assert snap["启动参数"]["max_wait_frames"] == config.max_wait_frames
    assert snap["实际服务输入"]["targets"][0]["species"] == 25
    p.runtime_insights.active["实际服务输入"]["targets"][0]["species"] = 30
    assert snap["实际服务输入"]["targets"][0]["species"] == 25


def test_branch_feedback_and_round_dynamic_values(window):
    p = window.auto_rng_tab
    p.apply_progress(AutoRngProgress(phase=AutoRngPhase.FINAL_WAIT, loop_index=1,
                                   fixed_delay=127, log_message="校正完成"))
    info = p.runtime_insights
    info.capture_signal(5, 40)
    event = info.last_event
    p.apply_progress(AutoRngProgress(phase=AutoRngPhase.FINAL_WAIT, loop_index=1, current_advances=100))
    assert info.last_event == event  # Predicted frames are not observations.
    assert info.round_values["fixed_delay"] == 127
    p.apply_progress(AutoRngProgress(phase=AutoRngPhase.RUN_ESCAPE_SCRIPT, loop_index=1,
                                   log_message="未出闪，逃跑续搜"))
    assert "本轮继续搜索" in info.next_label.text()
    assert info.reason == "未出闪，逃跑续搜"
    p.apply_progress(AutoRngProgress(phase=AutoRngPhase.RUN_SEED_SCRIPT, loop_index=2,
                                   fixed_delay=127))
    assert not info.round_values and not info.last_signal
    assert "剩余时间待运行反馈" in info.wait_label.text()


def test_tid_eta_uses_reported_blink_timing_and_stops_on_finish(window):
    info = window.auto_tid_rng_tab.runtime_insights
    progress = AutoTidRngProgress(phase=AutoTidRngPhase.WAIT_NAME_TRIGGER, loop_index=1,
                                  current_advances=10, remaining_to_trigger=600,
                                  wait_started_at=100, wait_elapsed_seconds=20, wait_target_at=245)
    info.update_progress(progress)
    assert "125 秒" in info.wait_label.text()
    info.update_progress(replace(progress, phase=AutoTidRngPhase.COMPLETED))
    assert not info.timer.isActive() and info.wait_label.isHidden()


def test_snapshot_dialog_does_not_save_or_start(window, monkeypatch):
    p = window.auto_rng_tab
    before = p._settings.allKeys()
    def forbidden(*a, **kw):
        raise AssertionError("read-only dialog called a mutation")
    monkeypatch.setattr(p, "_save_panel_state", forbidden)
    monkeypatch.setattr(p, "_start_with_phase", forbidden)
    p.runtime_insights.show_snapshot()
    assert p._settings.allKeys() == before


@pytest.mark.parametrize("tid", [False, True])
def test_folded_runtime_details_keep_progress_and_user_expansion(window, tid):
    panel = window.auto_tid_rng_tab if tid else window.auto_rng_tab
    window.tabs.setCurrentWidget(panel)
    assert panel.runtime_metrics.isHidden()
    assert panel.runtime_details.isHidden()
    phase = AutoTidRngPhase if tid else AutoRngPhase
    progress_type = AutoTidRngProgress if tid else AutoRngProgress
    waiting = phase.WAIT_NAME_TRIGGER if tid else phase.FINAL_WAIT
    progress = progress_type(phase=waiting, loop_index=1, current_advances=100,
                             trigger_advances=200)
    panel.apply_progress(progress)
    assert not panel.runtime_metrics.isHidden()
    assert panel.runtime_details.isHidden()
    panel.runtime_details_toggle.click()
    panel.apply_progress(replace(progress, current_advances=142))
    assert not panel.runtime_details.isHidden()
    assert panel.runtime_remaining_value.text() == "58 帧"
    panel.runtime_details_toggle.click()
    panel.apply_progress(replace(progress, current_advances=151))
    assert panel.runtime_details.isHidden()
    assert panel.runtime_remaining_value.text() == "49 帧"
    panel.apply_progress(progress_type(phase=phase.IDLE))
    assert panel.runtime_metrics.isHidden()
    assert panel.runtime_footer.isHidden()
    assert not panel.runtime_details_toggle.isChecked()


@pytest.mark.parametrize("text", ["—", "158 帧", "-300 帧", "2,147,483,647 帧", "<无数据>"])
def test_runtime_value_selection_preserves_plain_text(window, text):
    label = window.auto_rng_tab.runtime_remaining_value
    label.setText(text)
    label.setSelection(0, len(text))
    assert label.text() == text
    assert label.selectedText() == text
    assert label.accessibleName() == text
