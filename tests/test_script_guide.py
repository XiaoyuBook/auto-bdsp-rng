from pathlib import Path
from time import monotonic

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox, QPushButton

from auto_bdsp_rng import app_settings
from auto_bdsp_rng.automation.easycon.native.device import MemoryTransport, NintendoSwitchDevice
from auto_bdsp_rng.automation.easycon.native.errors import SourceLocation
from auto_bdsp_rng.automation.easycon.native.trace import ExecutionPoint, ExecutionTrace
from auto_bdsp_rng.automation.easycon.native_backend import NativeEasyConBackend
from auto_bdsp_rng.ui.ocr_demo_dialog import OcrDemoDialog
from tests.test_guide_flow import guided, assert_body_text_is_readable
from tests.test_ui import app, isolated_ui_qsettings
from tests.test_easycon_panel import process_events_until


def select(panel, kind, path):
    combo = getattr(panel, kind + "_script_combo")
    combo.addItem(path.name, path)
    combo.setCurrentIndex(combo.count() - 1)


def click_guided_tab(window, controller, page):
    assert window.tabs.currentWidget() is not page
    assert controller.overlay.waiting_for_page
    assert controller.overlay.focus_target is window.tabs.tabBar()
    assert not controller.overlay.tip.next_button.isEnabled()
    assert not controller.overlay.tip.skip_button.isEnabled()
    detail = controller.detail
    controller.next()
    assert controller.detail == detail
    bar = window.tabs.tabBar()
    QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=bar.tabRect(window.tabs.indexOf(page)).center())
    assert window.tabs.currentWidget() is page
    assert not controller.overlay.waiting_for_page
    assert controller.detail == detail


def open_for_guide(guided, tmp_path, kind="seed", text="WAIT 100\n"):
    window, panel, c = guided
    path = tmp_path / (kind + ".txt")
    path.write_text(text, encoding="utf-8")
    select(panel, kind, path)
    window.tabs.setCurrentWidget(panel)
    c._go("auto_script_config", kind + ":select")
    assert c.overlay.focus_target is panel.script_picker_widgets[getattr(panel, kind + "_script_combo")]
    c.next()
    if kind == "reverse":
        assert c.detail == "reverse:ball_check"
        c.next()
        assert c.detail == "reverse:ball_restore"
        c.next()
    assert c.detail == kind + ":edit"
    assert window.tabs.currentWidget() is window.easycon_tab
    assert window.easycon_tab.current_script_path == path
    return path


def fake_run(guided, state="running"):
    window, panel, c = guided
    e = window.easycon_tab
    trace = ExecutionTrace()
    e.native_backend = NativeEasyConBackend()
    e.native_backend.execution_trace = trace
    e.nativeScriptStarted.emit()
    source, text = str(e.current_script_path), e.editor.toPlainText()
    trace.begin(source)
    trace.set_sources(((source, text),))
    trace.record(ExecutionPoint(SourceLocation(source, 1), "等待", monotonic(), 100))
    trace.finish(state)
    e.execution.poll()
    c.script_guide.poll()
    return trace


def test_script_selection_draft_cancel_and_selected_file(guided, tmp_path, monkeypatch):
    w, p, c = guided
    e = w.easycon_tab
    e.editor.setPlainText("# 我的未保存录制\nWAIT 200")
    original = e.editor.toPlainText()
    path = tmp_path / "选择的脚本.txt"
    path.write_text("WAIT 100", encoding="utf-8")
    select(p, "seed", path)
    c._go("auto_script_config", "seed:select")
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Cancel)
    c.next()
    assert c.detail == "seed:select"
    assert e.editor.toPlainText() == original
    assert w.tabs.currentWidget() is p
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Discard)
    p.script_edit_buttons[p.seed_script_combo].click()
    assert c.detail == "seed:edit"
    assert e.current_script_path == path
    assert c.overlay.focus_target is e.editor


def test_seed_actual_run_and_manual_result_confirmation(guided, tmp_path):
    w, p, c = guided
    path = open_for_guide(guided, tmp_path, text="FOR 2\nWAIT 120\nNEXT\n")
    e = w.easycon_tab
    device = NintendoSwitchDevice(transport_factory=lambda port, baud: MemoryTransport(port, baud))
    backend = NativeEasyConBackend(device=device)
    backend.connect("mock")
    e.native_backend = backend
    e.port_combo.addItem("mock", "mock")
    e.port_combo.setCurrentIndex(e.port_combo.count() - 1)
    e._update_run_enabled()
    c.next()
    assert c.detail == "seed:run"
    assert not c.overlay.tip.next_button.isEnabled()
    try:
        QTest.mouseClick(e.run_button, Qt.MouseButton.LeftButton)
        process_events_until(lambda: c.detail == "seed:review" and e.execution.snapshot.point is not None)
        c.script_guide.poll()
        assert "玫瑰公园" in c.overlay.copy.text()
        assert "独立预览" in c.overlay.copy.text()
        assert c.overlay.tip.previous_button.text() == "脚本有问题"
        assert c.overlay.tip.next_button.text() == "脚本没问题"
        assert c.overlay.tip.skip_button.text() == "跳过"
        e.execution.follow.setChecked(False)
        process_events_until(lambda: e.native_run_thread is None)
        c.script_guide.poll()
        assert c.detail == "seed:review"  # Completion never asserts the game is correct.
        assert c.overlay.tip.next_button.isEnabled()
        assert c.overlay.tip.previous_button.isEnabled()
        c.pause()
        c.begin_or_resume()
        assert c.detail == "seed:review"
        c.next()
        assert c.detail == "seed:capture_config"
        assert c.overlay.waiting_for_page
        w.tabs.setCurrentWidget(w.project_xs_tab)
        assert c.overlay.focus_target is w.config_combo
        assert path.read_text(encoding="utf-8") == "FOR 2\nWAIT 120\nNEXT\n"
    finally:
        e.stop_native_script()
        process_events_until(lambda: e.native_run_thread is None)
        backend.close()


@pytest.mark.parametrize("kind,next_kind", [("advance", "advance"), ("hit", "reverse"), ("reverse", "ocr")])
def test_each_script_requires_run_and_confirmation_then_returns(guided, tmp_path, kind, next_kind):
    w, p, c = guided
    open_for_guide(guided, tmp_path, kind)
    c.next()
    if kind == "advance":
        assert c.detail == "advance:edit_second"
        c.next()
        assert c.detail == "advance:advance_frames"
        c.next()
    assert c.detail == kind + ":run"
    c.next()
    assert c.detail == kind + ":run"
    fake_run(guided, "completed")
    assert c.detail == kind + ":review"
    assert c.overlay.tip.next_button.isEnabled()
    c.next()
    assert c.detail == next_kind + (":capture_start" if kind == "advance" else ":select")
    assert c.overlay.page_widget is (w.project_xs_tab if kind == "advance" else p)
    assert w.tabs.currentWidget() is w.easycon_tab
    c.pause()
    c.begin_or_resume()
    click_guided_tab(w, c, c.overlay.page_widget)


@pytest.mark.parametrize("state", ["failed", "stopped"])
def test_failure_does_not_count_and_retry_is_explicit(guided, tmp_path, state):
    w, p, c = guided
    open_for_guide(guided, tmp_path, "hit")
    c.next()
    fake_run(guided, state)
    assert c.detail == "hit:review"
    assert not c.overlay.tip.next_button.isEnabled()
    assert c.overlay.tip.previous_button.isEnabled()
    assert "修改并重新运行" in c.overlay.tip.status.text()
    c.previous()
    assert c.detail == "hit:retry"
    assert c.overlay.tip.next_button.text() == "修改完成"


def test_resume_does_not_reuse_unrelated_or_previous_process_execution(guided, tmp_path):
    w, p, c = guided
    open_for_guide(guided, tmp_path, "hit")
    c.next()
    trace = fake_run(guided, "completed")
    trace.begin("unrelated.txt")
    trace.finish("completed")
    c.script_guide.poll()
    assert not c.overlay.tip.next_button.isEnabled()
    c.pause()
    c.script_guide.attempt = None
    c.begin_or_resume()
    assert c.detail == "hit:edit"


def test_parameter_positions_and_red_copy(guided, tmp_path):
    w, p, c = guided
    for kind, name, line in [("seed", "BDSP测种.txt", 24), ("advance", "bdsp过帧.txt", 7), ("reverse", "捕捉反查脚本.txt", 4)]:
        path = tmp_path / name
        path.write_text("WAIT 1\n" * 140, encoding="utf-8")
        select(p, kind, path)
        w.tabs.setCurrentWidget(p)
        c._go("auto_script_config", kind + ":select")
        c.next()
        if kind == "reverse":
            c.next()
            c.next()
        assert w.easycon_tab.editor.textCursor().blockNumber() + 1 == line
        assert "#C62828" in c.overlay.copy.text()
        if kind == "advance":
            c.next()
            assert w.easycon_tab.editor.textCursor().blockNumber() + 1 == 127


def test_ocr_animation_uses_real_roi_and_isolated_settings(guided):
    w, p, c = guided
    w.open_ocr_settings()
    before = w._ocr_settings_dialog.region_config.to_settings_dict()
    w._ocr_settings_dialog.hide()
    d = OcrDemoDialog(w)
    d.show()
    try:
        d.timer.stop()
        d.advance_to(8700)
        assert d.preview.selection_enabled() and d.preview._drag_start is not None
        d.advance_to(11600)
        region = d.panel.region_config.get("nature")
        assert region.x == 137 and region.y == 193
        assert region.width == 189 and region.height == 58
        d.advance_to(20900)
        assert "固执" in d.panel.table.item(0, 4).text()
        assert w._ocr_settings_dialog.region_config.to_settings_dict() == before
        d.restart()
        assert d.panel.region_config.get("nature") is None
        assert w._ocr_settings_dialog.region_config.to_settings_dict() == before
    finally:
        d.reject()
        d.deleteLater()


def test_ocr_handoff_real_selection_resume_and_completion(guided):
    w, p, c = guided
    c._go("auto_script_config", "ocr:demo")
    o = c.script_guide.ocr
    assert o.demo.isVisible()
    o.demo.learned_button.click()
    QTest.qWait(50)
    assert c.detail == "ocr:notes" and o.dialog.isVisible()
    assert o.tip is not None
    assert o.dialog.layout().indexOf(o.tip) >= 0
    # Real preview selection and actual save signal return to the same settings step.
    w._latest_preview_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    o.dialog.table.cellWidget(0, 3).findChildren(QPushButton)[0].click()
    assert o.selecting and not o.dialog.isVisible()
    assert w.tabs.currentWidget() is w.project_xs_tab
    w.apply_selected_ocr_region((100, 100, 200, 40))
    QTest.qWait(50)
    assert not o.selecting and o.dialog.isVisible()
    assert o.dialog.region_config.get("nature").x == 100
    o.tip.next_button.click()
    assert c.detail == "ocr:stats"
    c.pause()
    assert o.tip is None
    c.begin_or_resume()
    assert c.detail == "ocr:stats" and o.tip.isVisible()
    o.tip.next_button.click()
    assert c.detail == "ocr:battle"
    o.tip.next_button.click()
    assert c.detail == "exit:select" and w.tabs.currentWidget() is w.project_xs_tab
    click_guided_tab(w, c, p)
    c.next()
    assert c.detail == "escape:select" and w.tabs.currentWidget() is p
    assert not c.overlay.waiting_for_page
    c.next()
    assert c.detail == "save:select"
    if p.save_scripts_button.isEnabled():
        p.save_scripts_button.click()
    c.script_guide.poll()
    c.next()
    assert not c.active


@pytest.mark.parametrize("size", [(1150, 760), (860, 600)])
def test_script_controls_remain_visible_and_clickable_after_layout(guided, tmp_path, size):
    w, p, c = guided
    w.resize(*size)
    c._go("auto_script_config", "seed:select")
    QTest.qWait(180)
    o = c.overlay
    assert o.tip.isVisible()
    for control in (p.seed_script_combo, p.script_edit_buttons[p.seed_script_combo]):
        center = o._target_rect(control).center().toPoint()
        assert o.hole.contains(center)
        assert not o.mask().contains(center)
    assert_body_text_is_readable(o.tip)
    open_for_guide(guided, tmp_path)
    c.next()
    fake_run(guided)
    QTest.qWait(150)
    for control in (w.easycon_tab.stop_button, w.easycon_tab.pause_button, w.easycon_tab.execution.follow, w.easycon_tab.editor):
        center = o._target_rect(control).center().toPoint()
        assert o.hole.contains(center)
        assert not o.mask().contains(center)
    # The run button and the gap above the status bar must stay shaded.
    run = o._target_rect(w.easycon_tab.run_button)
    bar = o._target_rect(w.easycon_tab.execution.bar)
    assert o.mask().contains(run.center().toPoint())
    gap = run.center().toPoint()
    gap.setY(int((run.bottom() + bar.top()) / 2))
    assert o.mask().contains(gap)
    assert len(o.holes) == 4
    assert not o.hole.intersects(o.tip.geometry())
    assert_body_text_is_readable(o.tip)


@pytest.fixture
def connected_guide(guided):
    e = guided[0].easycon_tab
    device = NintendoSwitchDevice(transport_factory=lambda port, baud: MemoryTransport(port, baud))
    backend = NativeEasyConBackend(device=device)
    backend.connect("mock")
    e.native_backend = backend
    e.port_combo.addItem("mock", "mock")
    e.port_combo.setCurrentIndex(e.port_combo.count() - 1)
    e._update_run_enabled()
    yield backend
    e.stop_native_script()
    process_events_until(lambda: e.native_run_thread is None)
    backend.close()


def test_problem_edit_save_cancel_and_rerun_new_script(guided, connected_guide, tmp_path, monkeypatch):
    w, p, c = guided
    path = open_for_guide(guided, tmp_path, text="WAIT 100\n")
    e = w.easycon_tab
    c.next()
    e.run_button.click()
    process_events_until(lambda: e.native_run_thread is None)
    c.script_guide.poll()
    first_id = c.script_guide.snapshot().run_id
    c.previous()
    assert c.detail == "seed:retry"
    assert not e.editor.isReadOnly()
    assert "玫瑰公园" in c.overlay.copy.text()
    e.editor.setPlainText("WAIT 160\nWAIT 80\n")
    c.script_guide.poll()
    with monkeypatch.context() as m:
        m.setattr(e, "save_script", lambda: None)
        c.next()
        assert c.detail == "seed:retry"
        assert connected_guide.execution_trace.snapshot().run_id == first_id
        assert path.read_text(encoding="utf-8") == "WAIT 100\n"
    c.next()
    assert c.detail == "seed:review"
    assert not c.overlay.tip.next_button.isEnabled()
    assert not c.overlay.tip.previous_button.isEnabled()
    process_events_until(lambda: e.native_run_thread is None)
    c.script_guide.poll()
    assert c.script_guide.snapshot().run_id == first_id + 1
    assert c.script_guide.completed()
    assert path.read_text(encoding="utf-8") == "WAIT 160\nWAIT 80\n"
    c.next()
    assert c.detail == "seed:capture_config" and c.overlay.page_widget is w.project_xs_tab


def test_running_result_buttons_disabled_and_skip_stops_worker_first(guided, connected_guide, tmp_path):
    w, p, c = guided
    open_for_guide(guided, tmp_path, text="WAIT 5000\n")
    e = w.easycon_tab
    c.next()
    e.run_button.click()
    process_events_until(lambda: c.script_guide.snapshot() is not None and c.script_guide.snapshot().point is not None)
    c.script_guide.poll()
    assert c.detail == "seed:review"
    assert not c.overlay.tip.next_button.isEnabled()
    assert not c.overlay.tip.previous_button.isEnabled()
    assert c.overlay.tip.skip_button.isEnabled()
    c.next()
    c.previous()
    assert c.detail == "seed:review"
    c.skip()
    assert c.detail == "seed:review"
    assert not c.overlay.tip.skip_button.isEnabled()
    process_events_until(lambda: e.native_run_thread is None)
    c.script_guide.poll()
    assert c.detail == "advance:select"
    assert connected_guide.execution_trace.snapshot().state == "stopped"


def test_preview_reused_visible_highlighted_and_restored(guided, tmp_path):
    w, p, c = guided
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    w._latest_preview_frame = frame
    w.show_picture_in_picture()
    preview = w._picture_in_picture
    original = (preview.geometry(), preview.frame_label.styleSheet(), preview.always_on_top())
    open_for_guide(guided, tmp_path)
    c.next()
    fake_run(guided, "completed")
    QTest.qWait(120)
    assert w._picture_in_picture is preview
    assert preview.isVisible() and preview.always_on_top()
    assert "#35C58D" in preview.frame_label.styleSheet()
    assert preview._raw_frame is frame
    assert all(not preview.frameGeometry().intersects(r) for r in c.script_guide.preview.protected_rects())
    preview.hide()
    c.script_guide.poll()
    assert preview.isVisible()
    c.pause()
    assert preview.windowTitle() == "独立预览"
    assert (preview.geometry(), preview.frame_label.styleSheet(), preview.always_on_top()) == original
    assert preview._raw_frame is frame


@pytest.mark.parametrize("legacy", ["follow", "locate", "wait", "confirm", "save"])
def test_legacy_running_progress_uses_single_confirmation(guided, tmp_path, legacy):
    w, p, c = guided
    open_for_guide(guided, tmp_path)
    c.next()
    fake_run(guided, "completed")
    c._go("auto_script_config", "seed:" + legacy)
    assert c.script_guide.pos == ("seed", "review")
    assert c.overlay.tip.next_button.text() == "脚本没问题"
    c.next()
    assert c.detail == "seed:capture_config"


def test_ocr_demo_coordinates_stay_on_real_controls_after_switching(guided):
    w, p, c = guided
    d = OcrDemoDialog(w)
    d.resize(780, 600)
    d.show()
    try:
        d.timer.stop()
        for t in (1800, 5800, 8800, 11000, 12000, 15000, 19900, 22000, 24000, 27500):
            d.advance_to(t)
            QTest.qWait(30)
            hole, cursor, click = d.visual_state()
            assert d.scene.sceneRect().contains(hole), t
            if t in (1800, 5800, 19900):
                button = d.panel.warmup_button if t == 1800 else d.action(0 if t == 5800 else 2)
                rect = button.rect()
                center = d.panel_proxy.mapToScene(button.mapTo(d.panel, rect.center()))
                assert hole.contains(center), t
            if t == 22000:
                row = d.panel.table.visualItemRect(d.panel.table.item(0, 4))
                center = d.panel_proxy.mapToScene(d.panel.table.viewport().mapTo(d.panel, row.center()))
                assert hole.contains(center)
                assert d.panel_proxy.pos().isNull()
            assert d.rect().contains(d.learned_button.geometry())
            assert d.learned_button.isVisible()
    finally:
        d.reject()
        d.deleteLater()


@pytest.mark.parametrize("step,detail", [("easycon_intro", ""), ("easycon_recording", "practice")])
def test_skipping_recording_continues_to_script_selection(guided, step, detail):
    w, p, c = guided
    w.tabs.setCurrentWidget(w.easycon_tab)
    c._go(step, detail)
    c.skip()
    assert c.step == "auto_script_config"
    assert c.overlay.page_widget is p
    assert c.overlay.waiting_for_page
    w.tabs.setCurrentWidget(p)
    assert c.overlay.focus_target is p.script_picker_widgets[p.seed_script_combo]
