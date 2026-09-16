from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from auto_bdsp_rng.resources import resource_path
from auto_bdsp_rng.ui.ocr_guide import FIELDS
from tests.test_guide_flow import guided, assert_body_text_is_readable
from tests.test_script_guide import connected_guide
from tests.test_ui import app, isolated_ui_qsettings


RESULTS = dict(zip(FIELDS, ('固执', '对声音敏感', '200', '120', '110', '100', '90', '130')))


@pytest.fixture
def ocr(guided, connected_guide, monkeypatch):
    w, p, c = guided
    e = w.easycon_tab
    monkeypatch.setattr(e, '_keyboard_hook_factory', SimpleNamespace(is_supported=lambda: False))
    e.controller_active_button.click()
    w._latest_preview_frame = cv2.imdecode(np.frombuffer(resource_path('docs', 'assets', 'guide-ocr', 'notes.jpg').read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    w.tabs.setCurrentWidget(p)
    c._go('auto_script_config', 'ocr:select')
    c.next()
    o = c.script_guide.ocr
    assert o.phase == 'initial_test' and o.demo is None
    d = o.dialog
    d.fullTestRequested.disconnect(w._start_ocr_full_test)
    d.recognitionRequested.disconnect(w._request_ocr_region_recognition)
    monkeypatch.setattr(QMessageBox, 'question', lambda *_a, **_k: QMessageBox.StandardButton.Ok)
    yield o
    c.pause()


def full_test(o, results=None, success=True):
    o.dialog.test_all_button.click()
    assert o.dialog.interaction_busy
    assert not o.tip.next_button.isEnabled()
    for field, text in (RESULTS if results is None else results).items():
        o.dialog.set_recognition_result(field, text)
    o.dialog.finish_full_test(success, 'done' if success else 'failed')
    QTest.qWait(20)


def learn(o):
    assert o.demo.isVisible()
    o.demo.learned_button.click()
    QTest.qWait(30)
    assert o.phase in ('back_notes', 'switch_stats')
    o.tip.next_button.click()
    assert o.phase == 'field_select'


def select_region(o):
    page = o.window.tabs.currentWidget()
    o.action(o.field, 0).click()
    assert o.selecting and not o.dialog.isVisible()
    assert o.window.tabs.currentWidget() is page
    pip = o.window._picture_in_picture
    assert pip is o.preview.preview and pip.isVisible()
    label = pip.frame_label
    rect = label._pixmap_rect
    start = rect.topLeft() + QPoint(25, 25)
    end = start + QPoint(120, 30)
    QTest.mousePress(label, Qt.MouseButton.RightButton, pos=start)
    QTest.mouseMove(label, end)
    QTest.mouseRelease(label, Qt.MouseButton.RightButton, pos=end)
    QTest.qWait(30)
    assert not o.selecting and o.phase == 'field_recognize'
    assert o.dialog.isVisible()
    assert not o.tip.next_button.isEnabled()
    assert o.dialog.region_config.get(o.field) is not None


def recognize(o, value=None):
    field = o.field
    o.action(field, 2).click()
    assert not o.tip.next_button.isEnabled()
    o.dialog.finish_recognition(field, RESULTS[field] if value is None else value)
    assert o.phase == 'field_result'


def test_ocr_first_test_can_complete_without_demo(ocr):
    o = ocr
    assert o.shade.isVisible()
    assert not o.tip.next_button.isEnabled()
    o.dialog.finish_full_test(True, 'old result')
    assert o.phase == 'initial_test'
    full_test(o)
    assert o.phase == 'initial_result'
    assert o.tip.next_button.isEnabled()
    assert o.tip.previous_button.text() == '部分有问题'
    o.tip.next_button.click()
    assert o.c.detail == 'exit:select' and o.demo is None
    assert not o.window.easycon_tab.virtual_controller_enabled


def test_ocr_partial_repairs_only_selected_fields_then_tests_again(ocr, connected_guide):
    o = ocr
    full_test(o, {**RESULTS, 'nature': '空', 'speed': '42'})
    assert not o.tip.next_button.isEnabled()
    o.tip.previous_button.click()
    assert o.phase == 'choose_fields'
    assert o.choices['nature'].isChecked()
    o.choices['speed'].setChecked(True)
    before = o.dialog.region_config.get('attack')
    o.tip.next_button.click()
    assert o.fields == ('nature', 'speed')
    learn(o)
    select_region(o)
    recognize(o, '')
    assert not o.tip.next_button.isEnabled()
    o.tip.previous_button.click()
    select_region(o)
    recognize(o)
    o.tip.next_button.click()
    assert o.phase == 'switch_stats'
    QTest.keyPress(o.dialog, Qt.Key.Key_D)
    assert connected_guide.get_report().lx == 255
    QTest.keyRelease(o.dialog, Qt.Key.Key_D)
    assert connected_guide.get_report().lx == 128 and o._direction_seen
    o.tip.next_button.click()
    assert o.field == 'speed'
    select_region(o)
    recognize(o)
    o.tip.next_button.click()
    assert o.phase == 'return_notes'
    QTest.keyPress(o.dialog, Qt.Key.Key_A)
    assert connected_guide.get_report().lx == 1
    QTest.keyRelease(o.dialog, Qt.Key.Key_A)
    assert o._direction_seen and connected_guide.get_report().lx == 128
    o.tip.next_button.click()
    assert o.phase == 'final_test'
    assert o.dialog.region_config.get('attack') == before
    full_test(o)
    assert o.phase == 'final_result'
    o.tip.next_button.click()
    assert o.c.detail == 'exit:select'


def test_ocr_all_wrong_guides_all_eight_fields_and_retries(ocr):
    o = ocr
    full_test(o, success=False)
    assert not o.tip.next_button.isEnabled()
    o.tip.skip_button.click()
    assert o.fields == FIELDS
    learn(o)
    for field in FIELDS:
        if o.phase == 'switch_stats':
            o.tip.next_button.click()
        assert o.field == field
        select_region(o)
        recognize(o)
        o.tip.next_button.click()
    assert o.phase == 'return_notes'
    o.tip.next_button.click()
    full_test(o, {**RESULTS, 'hp': '空'})
    assert not o.tip.next_button.isEnabled()
    o.tip.previous_button.click()
    assert o.choices['hp'].isChecked()
    o.tip.next_button.click()
    assert o.fields == ('hp',)
    learn(o)
    assert o.field == 'hp'


def test_ocr_cancel_selection_and_resume_keeps_plan_and_real_results(ocr):
    o = ocr
    o.go('field_select', fields=('characteristic', 'defense'), index=0)
    before = o.dialog.region_config.to_settings_dict()
    o.action(o.field, 0).click()
    o.preview.cancel.click()
    QTest.qWait(30)
    assert not o.selecting and o.dialog.isVisible()
    assert o.phase == 'field_select' and o.dialog.region_config.to_settings_dict() == before
    o.c.pause()
    o.c.begin_or_resume()
    assert o.needs_control and o.fields == ('characteristic', 'defense')
    e = o.window.easycon_tab
    o.window.tabs.setCurrentWidget(e)
    e.controller_active_button.click()
    o.c.script_guide.poll()
    assert o.phase == 'field_select' and o.dialog.isVisible()
    select_region(o)
    recognize(o)
    o.dialog.set_region(o.field, (5, 5, 15, 15))
    assert o.phase == 'field_recognize' and not o.tip.next_button.isEnabled()


def test_partial_choices_survive_pause_and_stale_full_test_cannot_complete(ocr):
    o = ocr
    full_test(o)
    o.tip.previous_button.click()
    assert not any(box.isChecked() for box in o.choices.values())
    o.choices['speed'].setChecked(True)
    o.c.pause()
    o.c.begin_or_resume()
    e = o.window.easycon_tab
    o.window.tabs.setCurrentWidget(e)
    e.controller_active_button.click()
    o.c.script_guide.poll()
    assert o.fields == ('speed',)
    assert o.choices['speed'].isChecked()
    assert not o.choices['nature'].isChecked()
    o.go('final_test')
    full_test(o)
    o.dialog.set_recognition_result('hp', '999')
    o.navigation()
    assert not o.tip.next_button.isEnabled()


@pytest.mark.parametrize('size', [(1150, 760), (860, 600)])
def test_ocr_cards_remain_readable_with_choices(ocr, size):
    o = ocr
    o.window.resize(*size)
    full_test(o)
    o.tip.previous_button.click()
    QTest.qWait(100)
    assert_body_text_is_readable(o.tip)
    assert all(b.isVisible() for b in o.choices.values())
