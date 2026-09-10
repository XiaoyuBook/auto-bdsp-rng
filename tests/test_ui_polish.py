from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QSettings, QThread, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox

from auto_bdsp_rng.automation.auto_rng.models import AutoRngPhase, AutoRngProgress
from auto_bdsp_rng.automation.auto_tid_rng import AutoTidRngPhase, AutoTidRngProgress
from auto_bdsp_rng.gen8_id import IDState8
from auto_bdsp_rng.rng_core import SeedPair64
from auto_bdsp_rng.ui import main_window as mw
from auto_bdsp_rng.ui.auto_rng_panel import AutoRngPanel
from auto_bdsp_rng.ui.auto_tid_rng_panel import AutoTidRngPanel


def wait(app, condition):
    deadline = time.monotonic() + 5
    while not condition():
        assert time.monotonic() < deadline, "Qt state transition timed out"
        app.processEvents()
        QTest.qWait(5)


@pytest.fixture
def window(monkeypatch, tmp_path):
    # Explicit INI files remain inside pytest's isolated run directory.
    from tests.test_easycon_panel import FakeNativeBackend, UnsupportedKeyboardHookFactory
    original = mw.EasyConPanel
    monkeypatch.setattr(mw, 'EasyConPanel', lambda *a, **kw: original(
        *a, **kw, native_backend=FakeNativeBackend(['COM3']),
        keyboard_hook_factory=UnsupportedKeyboardHookFactory()))
    monkeypatch.setattr(mw.MainWindow, 'refresh_capture_devices', lambda self: None)
    monkeypatch.setattr(mw, 'should_show_startup_notice', lambda: False)
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, 'critical', lambda *a: QMessageBox.StandardButton.Ok)
    app = QApplication.instance() or QApplication([])
    widget = mw.MainWindow(profile_settings=QSettings(str(tmp_path / 'profile.ini'), QSettings.Format.IniFormat))
    widget.auto_tid_rng_tab.add_target_display_tid(1)
    widget.show()
    widget.easycon_tab._native_status_timer.stop()
    app.processEvents()
    yield widget, app
    for panel in (widget.auto_rng_tab, widget.auto_tid_rng_tab):
        worker = panel._runner_worker
        if worker is not None:
            worker.runner.release.set()
            wait(app, lambda: panel._runner_thread is None)
        panel.set_preparing(False)
    widget._ocr_warmup_thread = None
    widget._ocr_after_warmup = None
    widget.close()
    app.processEvents()


class HeldRunner:
    def __init__(self, tid=False, fail=False):
        self.release = threading.Event()
        self.stop_calls = 0
        self.stop_error = False
        self.fail = fail
        self.progress = AutoTidRngProgress(phase=AutoTidRngPhase.WAIT_NAME_TRIGGER) if tid else AutoRngProgress(phase=AutoRngPhase.FINAL_WAIT)

    def run(self):
        self.progress_callback(self.progress)
        assert self.release.wait(10)
        if self.fail:
            raise RuntimeError('controlled failure')
        return self.progress

    def stop(self, **kwargs):
        self.stop_calls += 1
        if self.stop_error:
            raise RuntimeError('controlled stop failure')


@pytest.mark.parametrize('tid', [False, True])
def test_lifecycle_waits_for_thread_and_stops_once(window, tid):
    w, app = window
    panel, other = (w.auto_tid_rng_tab, w.auto_rng_tab) if tid else (w.auto_rng_tab, w.auto_tid_rng_tab)
    assert panel.start_button.isEnabled()
    assert not panel.stop_button.isEnabled()
    signals = []
    panel.stopRequested.connect(lambda: signals.append('stop'))
    for iteration in range(2):
        runner = HeldRunner(tid)
        panel.run_with_runner(runner)
        assert not panel.start_button.isEnabled()
        assert not other.start_button.isEnabled()
        assert all(not a.isEnabled() for a in panel.start_button.menu().actions())
        assert panel.stop_button.isEnabled()
        panel.stop_button.click()
        panel.stop_button.click()
        panel._stop_clicked()
        assert runner.stop_calls == 1
        assert len(signals) == iteration + 1
        assert panel.stop_button.text() == '正在停止'
        assert not panel.stop_button.isEnabled()
        thread = panel._runner_thread
        panel._runner_finished(runner.progress)
        panel._clear_runner_thread()
        assert panel._runner_thread is thread
        assert not panel.start_button.isEnabled()
        assert not other.start_button.isEnabled()
        runner.release.set()
        wait(app, lambda: panel._runner_thread is None)
        assert panel.start_button.isEnabled() and other.start_button.isEnabled()
        assert panel.stop_button.text() == '停止'
        assert not panel.stop_button.isEnabled()


@pytest.mark.parametrize('tid', [False, True])
def test_stop_failure_is_retryable_and_worker_failure_recovers(window, tid):
    w, app = window
    panel = w.auto_tid_rng_tab if tid else w.auto_rng_tab
    runner = HeldRunner(tid, fail=True)
    runner.stop_error = True
    panel.run_with_runner(runner)
    panel.stop_button.click()
    assert panel.stop_button.isEnabled()
    assert panel.stop_button.text() == '停止'
    assert '停止请求失败，请重试' in panel.log_view.toPlainText()
    runner.stop_error = False
    panel.stop_button.click()
    assert runner.stop_calls == 2
    runner.release.set()
    wait(app, lambda: panel._runner_thread is None)
    assert panel.start_button.isEnabled()
    assert not panel.stop_button.isEnabled()
    assert '失败' in panel.status_badge.text()


def test_preparation_cancel_holds_resource_until_warmup_exit(window, monkeypatch):
    w, app = window
    panel = w.auto_rng_tab
    thread = QThread(w)
    w._ocr_warmup_thread = thread
    thread.finished.connect(w._handle_ocr_warmup_thread_finished)
    thread.start()
    try:
        w._start_auto_rng(panel.build_config())
        assert panel._preparing
        assert panel.stop_button.isEnabled()
        assert not w.auto_tid_rng_tab.start_button.isEnabled()
        panel.stop_button.click()
        w._handle_ocr_warmup_completed(True, 'controlled success after cancellation')
        assert panel._preparing and panel._stop_pending
        assert w._ocr_after_warmup is None
    finally:
        thread.quit()
        wait(app, lambda: w._ocr_warmup_thread is None)
    assert panel.start_button.isEnabled()
    assert w.auto_tid_rng_tab.start_button.isEnabled()
    assert panel._runner_thread is None
    thread.deleteLater()


@pytest.mark.parametrize('success', [True, False])
def test_preparation_failure_or_declined_prerequisite_recovers(window, monkeypatch, success):
    w, app = window
    monkeypatch.setattr(w, '_ensure_preview_for_auto_rng', lambda: False)
    w._start_auto_rng(w.auto_rng_tab.build_config())
    assert not w.auto_rng_tab.start_button.isEnabled()
    w._handle_ocr_warmup_completed(success, 'controlled warmup')
    wait(app, lambda: w.auto_rng_tab.start_button.isEnabled())
    assert not w.auto_rng_tab.stop_button.isEnabled()


def test_cleanup_does_not_unlock_while_script_owns_resource(window):
    w, app = window
    runner = HeldRunner()
    w.auto_rng_tab.run_with_runner(runner)
    w.easycon_tab._native_run_reserved = True
    runner.release.set()
    wait(app, lambda: w.auto_rng_tab._runner_thread is None)
    assert not w.auto_rng_tab.start_button.isEnabled()
    assert not w.auto_tid_rng_tab.start_button.isEnabled()
    w.easycon_tab._native_run_reserved = False
    w._refresh_automation_start_state()
    assert w.auto_rng_tab.start_button.isEnabled()


def test_refresh_six_scripts_preserves_selection_dirty_state_and_missing_paths(window, tmp_path):
    w, app = window
    p = w.auto_rng_tab
    p.script_dir = tmp_path
    paths = [tmp_path / f'脚本{i}.txt' for i in range(6)]
    for path in paths:
        path.write_text('A 100\n', encoding='utf-8')
    p.refresh_scripts()
    p.escape_continue_check.setChecked(True)
    for combo, path in zip(p._script_combos(), paths):
        combo.setCurrentIndex(combo.findData(str(path)))
    for saved in (True, False):
        p._set_config_saved(saved)
        p.refresh_scripts_button.click()
        assert p.config_saved_label.property('saved') == saved
        assert [p._selected_path(c) for c in p._script_combos()] == paths
        assert all(b.isEnabled() for b in p.script_edit_buttons.values())
    new = tmp_path / '新增.txt'
    new.write_text('B 100\n', encoding='utf-8')
    paths[0].unlink()
    p._set_config_saved(True)
    p.refresh_scripts_button.click()
    assert p.seed_script_combo.currentData() is None
    assert not p.script_edit_buttons[p.seed_script_combo].isEnabled()
    assert p.config_saved_label.property('saved')
    assert not p.script_save_state_label.property('saved')
    assert '脚本已不存在' in p.log_view.toPlainText()
    assert all(c.findData(str(new)) > 0 for c in p._script_combos())
    p.escape_continue_check.setChecked(False)
    assert not p.script_edit_buttons[p.escape_script_combo].isEnabled()
    selected = [p._selected_path(c) for c in p._script_combos()]
    runner = HeldRunner()
    p.run_with_runner(runner)
    p.refresh_scripts()
    assert [p._selected_path(c) for c in p._script_combos()] == selected
    runner.release.set()
    wait(app, lambda: p._runner_thread is None)


@pytest.mark.parametrize('count', [0, 3, 50])
def test_tid_targets_two_rows_alignment_scroll_and_delete(window, count):
    w, app = window
    p = w.auto_tid_rng_tab
    w.tabs.setCurrentWidget(p)
    p._clear_targets()
    p.target_input.setText(','.join(str(i) for i in range(count)))
    p._add_target_from_input()
    app.processEvents()
    assert p.target_list.count() == count
    assert p.target_count_label.text() == f'{count} 个目标'
    title = p.target_title_label.mapTo(w, QPoint())
    badge = p.target_count_label.mapTo(w, QPoint())
    pool = p.target_list.mapTo(w, QPoint())
    inputs = p.target_input.mapTo(w, QPoint())
    assert 6 <= inputs.y() - title.y() - p.target_title_label.height() <= 10
    if count:
        assert pool.y() >= inputs.y() + p.target_input.height()
        assert badge.y() >= pool.y() + p.target_list.height()
        assert badge.x() + p.target_count_label.width() <= pool.x() + p.target_list.width()
    else:
        assert p.target_list.isHidden()
        assert badge.y() >= inputs.y() + p.target_input.height()
    assert p.frame_threshold.mapTo(w, QPoint()).y() < p.delay.mapTo(w, QPoint()).y()
    assert not p.script_fields.isVisible()
    p.script_toggle.click()
    app.processEvents()
    assert p.script_fields.isVisible()
    assert p.seed_script_combo.mapTo(w, QPoint()).x() < p.name_script_combo.mapTo(w, QPoint()).x()
    if count == 50:
        rects = [p.target_list.visualItemRect(p.target_list.item(i)) for i in range(count)]
        complete_rows = {r.top() for r in rects if p.target_list.viewport().rect().contains(r)}
        assert len(complete_rows) >= 2
        last = p.target_list.item(count - 1)
        p.target_list.scrollToItem(last)
        app.processEvents()
        rect = p.target_list.visualItemRect(last)
        assert p.target_list.viewport().rect().contains(rect)
        QTest.mouseClick(p.target_list.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(rect.right()-8, rect.center().y()))
        assert p.target_list.count() == 49
        assert 49 not in p.target_display_tids()
    p._save_panel_state()
    assert '×' not in str(p._settings.value('target_tids'))


def menu_enabled(monkeypatch, call):
    states = []
    def inspect_menu():
        menu = QApplication.activePopupWidget()
        assert isinstance(menu, QMenu)
        states.extend(a.isEnabled() for a in menu.actions())
        menu.close()
    QTimer.singleShot(0, inspect_menu)
    call(QPoint())
    return states


def test_tid_empty_lifecycle_preserves_results_on_ordinary_progress(window, monkeypatch):
    w, app = window
    p = w.auto_tid_rng_tab
    w.tabs.setCurrentWidget(p)
    assert p.id_table.rowCount() == 0
    assert p.id_empty_state.title.text() == '尚未捕获 Seed'
    assert not p.copy_button.isEnabled() and not p.export_button.isEnabled()
    assert menu_enabled(monkeypatch, p._show_table_context_menu) == [False, False]
    p.apply_progress(AutoTidRngProgress(phase=AutoTidRngPhase.SEARCH_TARGET))
    assert p.id_empty_state.title.text() == '正在搜索'
    p.apply_progress(AutoTidRngProgress(phase=AutoTidRngPhase.SEARCH_TARGET, id_search_completed=True))
    assert p.id_empty_state.title.text() == '没有符合当前条件的结果'
    p._runner_failed('controlled failure')
    assert p.id_empty_state.title.text() == '搜索未完成'
    state = IDState8(advances=1, tid=2, sid=3, tsv=4, display_tid=5)
    p.set_id_states([state])
    assert p.id_empty_state.isHidden()
    assert p.copy_button.isEnabled() and p.export_button.isEnabled()
    assert menu_enabled(monkeypatch, p._show_table_context_menu) == [True, True]
    p.apply_progress(AutoTidRngProgress(phase=AutoTidRngPhase.WAIT_NAME_TRIGGER, current_advances=3))
    assert p._id_states == [state]
    p.set_tid_seed(SeedPair64(1, 2), generate=False)
    assert p._id_states == [state]
    assert p.id_empty_state.isHidden()
    p.apply_progress(AutoTidRngProgress(phase=AutoTidRngPhase.SEARCH_TARGET, id_search_completed=True))
    assert not p.id_empty_state.isHidden() and p.id_table.rowCount() == 0
    assert p.id_empty_state.parent() is p.id_table.viewport()
    assert p.id_empty_state.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)


def test_static_empty_search_success_and_failure(window, monkeypatch):
    w, app = window
    w.tabs.setCurrentWidget(w.bdsp_tab)
    assert w.static_empty_state.title.text() == '尚未生成结果'
    assert not w.copy_button.isEnabled()
    assert menu_enabled(monkeypatch, w._show_result_context_menu) == [False, False, False]
    for box, text in zip(w.bdsp_seed64_inputs, ('1234567890ABCDEF', '1111111122222222')):
        box.setText(text)
    w.initial_advances.setText('0')
    w.max_advances.setText('2')
    w.generate_results()
    assert w._states
    assert w.static_empty_state.isHidden()
    assert menu_enabled(monkeypatch, w._show_result_context_menu) == [True, True, True]
    monkeypatch.setattr(mw, 'generate_static_candidates', lambda criteria: [])
    w.generate_results()
    assert w.table.rowCount() == 0
    assert w.static_empty_state.title.text() == '没有符合当前条件的结果'
    assert not w.export_button.isEnabled()
    def fail(criteria):
        assert w.static_empty_state.title.text() == '正在搜索'
        raise RuntimeError('controlled failure')
    monkeypatch.setattr(mw, 'generate_static_candidates', fail)
    monkeypatch.setattr(w, '_show_error', lambda *args, **kw: None)
    w.generate_results()
    assert w.static_empty_state.title.text() == '搜索未完成'
    assert w.static_empty_state.parent() is w.table.viewport()


def test_auto_rng_seed_sync_uses_static_result_lifecycle(window, monkeypatch):
    w, _app = window
    monkeypatch.setattr(mw, 'generate_static_candidates', lambda _criteria: [])

    w._sync_bdsp_data_from_auto_rng(SeedPair64(1, 2))

    assert w._static_result_state == 'complete'
    assert w.static_empty_state.title.text() == '没有符合当前条件的结果'

    def fail(_criteria):
        raise RuntimeError('controlled failure')

    monkeypatch.setattr(mw, 'generate_static_candidates', fail)
    monkeypatch.setattr(w, '_show_error', lambda *args, **kwargs: None)
    w._sync_bdsp_data_from_auto_rng(SeedPair64(3, 4))

    assert w._static_result_state == 'failed'
    assert w.static_empty_state.title.text() == '搜索未完成'
