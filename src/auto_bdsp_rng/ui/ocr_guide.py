"""Test first, repair selected OCR fields, then validate the complete capture."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QRect, QTimer
from PySide6.QtGui import QBrush, QColor, QKeySequence
from PySide6.QtWidgets import QDialog, QGridLayout, QPushButton, QWidget

from auto_bdsp_rng.automation.auto_rng.ocr_regions import NOTE_REGION_FIELDS, STAT_REGION_FIELDS, OCR_REGION_LABELS
from auto_bdsp_rng.automation.auto_rng.pokemon_info_ocr import _NATURES_ZH, match_characteristic_text
from auto_bdsp_rng.ui.check_box import CheckmarkCheckBox
from auto_bdsp_rng.ui.guide_steps import GuideStep
from auto_bdsp_rng.ui.guide_tip import GuideTip
from auto_bdsp_rng.ui.ocr_demo_dialog import OcrDemoDialog
from auto_bdsp_rng.ui.ocr_guide_widgets import OcrFocusShade, OcrGuidePreview


FIELDS = (*NOTE_REGION_FIELDS, *STAT_REGION_FIELDS)
TEST_PHASES = ('initial_test', 'final_test')
RESULT_PHASES = ('initial_result', 'final_result')
DIRECTION_PHASES = ('back_notes', 'switch_stats', 'return_notes')


def valid_result(field, text):
    if field == 'nature':
        return text in _NATURES_ZH
    if field == 'characteristic':
        return match_characteristic_text(text) is not None
    return text.isdecimal() and int(text) > 0


class OcrGuide(QObject):
    def __init__(self, controller):
        super().__init__(controller)
        self.c, self.window = controller, controller.window
        self.demo = self.dialog = self.tip = self.shade = None
        self.layout_state = None
        self.selecting = self._opening = self._waiting_for_control = False
        self.preview = OcrGuidePreview(self)
        self.choices = {}
        self._connected_dialog = None
        self._test_started = self._test_done = self._test_ok = False
        self._test_message = ''
        self._test_signature = self._recognizing = self._recognized = None
        self._test_values = None
        self._direction_seen = False
        self.window.auto_rng_tab.captureInfoRequested.connect(self._opened)
        self.window.captureSelectionStarted.connect(self._selection_started)
        self.window.captureSelectionFinished.connect(self._selection_finished)
        self.window.ocrRegionSelected.connect(self._region_selected)
        self.window.easycon_tab.virtualControllerAction.connect(self._controller_action)

    @property
    def active(self):
        return self.c.active and self.c.step == 'auto_script_config' and self.c.detail.startswith('ocr:')

    @property
    def phase(self):
        return self.c.detail.split(':')[1]

    @property
    def fields(self):
        parts = self.c.detail.split(':')
        selected = parts[2].split(',') if len(parts) > 2 else FIELDS
        return tuple(f for f in FIELDS if f in selected)

    @property
    def index(self):
        parts = self.c.detail.split(':')
        return min(int(parts[3]), max(0, len(self.fields) - 1)) if len(parts) > 3 and parts[3].isdigit() else 0

    @property
    def field(self):
        return self.fields[self.index] if self.fields else FIELDS[0]

    def go(self, phase, *, fields=None, index=None):
        selected = self.fields if fields is None else fields
        index = self.index if index is None else index
        self._direction_seen = False
        self.c._go('auto_script_config', f'ocr:{phase}:' + ','.join(selected) + f':{index}')

    @property
    def needs_control(self):
        return (self.active and not self.window.easycon_tab.virtual_controller_enabled
                and self.demo is None and self.tip is None and not self.selecting)

    def control_step(self):
        e = self.window.easycon_tab
        connected = e._controller_connection_active()
        target = e.controller_active_button if connected else self.window.easycon_header_button
        title = '先开启虚拟手柄' if connected else '先连接伊机控'
        copy = ('点击亮起的「控制」，开启虚拟手柄后继续 OCR 设置。接下来需要在笔记页和能力页之间切换。'
                '<br><br><b>手柄显示着但处于「待机」时，也需要切回「控制」。</b>' if connected else
                'OCR 核对需要用虚拟手柄切换游戏页面。先连接伊机控，再开启「控制」。开发测试可选择 Mock 串口。')
        return GuideStep('auto_script_config', '5.3.5 · OCR 设置准备', title, copy, target, (target,))

    def control_navigation(self):
        if not self.needs_control:
            return False
        self.c.overlay.tip.next_button.setText('等待开启虚拟手柄')
        self.c.overlay.tip.next_button.setEnabled(False)
        self.c.overlay.tip.skip_button.setEnabled(False)
        return True

    def prepare(self):
        if not self.active:
            return False
        self._waiting_for_control = self.needs_control
        if self._waiting_for_control:
            return False
        phase = self.phase
        if phase in ('notes', 'stats', 'battle'):
            self.go('initial_test')
            return True
        if phase in RESULT_PHASES and not self._test_current():
            self.go('final_test' if phase == 'final_result' else 'initial_test')
            return True
        if phase == 'field_result' and not self._recognition_current():
            self.go('field_recognize')
            return True
        if phase == 'demo':
            self.c.overlay.shade_all()
            if self.demo is None:
                self.demo = OcrDemoDialog(self.window)
                self.demo.learnedRequested.connect(self._learned)
                self.demo.finished.connect(self._demo_closed)
                self.demo.open()
            return True
        if phase != 'select':
            if self.selecting:
                self._show_selection()
            else:
                self._show_settings()
            return True
        return False

    def _learned(self):
        phase = 'back_notes' if self.fields[0] in NOTE_REGION_FIELDS else 'switch_stats'
        detail = f'ocr:{phase}:' + ','.join(self.fields) + ':0'
        if self.c._persist('auto_script_config', detail):
            self.demo.accept()

    def _demo_closed(self, result):
        demo, self.demo = self.demo, None
        if demo is not None:
            demo.deleteLater()
        if not self.active:
            return
        if result == QDialog.DialogCode.Accepted:
            QTimer.singleShot(0, self.c._show_workspace)
        else:
            self.c.pause()

    def _opened(self):
        if not self._opening and self.active and self.phase == 'select':
            self.go('initial_test')

    def _show_settings(self):
        self.c.overlay.shade_all()
        if self.tip is None:
            self._opening = True
            try:
                self.window.open_ocr_settings()
            finally:
                self._opening = False
            self.dialog = d = self.window._ocr_settings_dialog
            self.layout_state = (d.size(), d.minimumSize(), d.table.minimumHeight(), d.geometry())
            if self._connected_dialog is not d:
                d.fullTestStarted.connect(self._test_start)
                d.fullTestFinished.connect(self._test_finish)
                d.recognitionStarted.connect(self._recognition_start)
                d.recognitionFinished.connect(self._recognition_finish)
                d.regionChanged.connect(self._region_changed)
                self._connected_dialog = d
            self.tip = GuideTip(d)
            self.tip.setMinimumWidth(0)
            self.tip.setMaximumWidth(16777215)
            self.tip.close_button.clicked.connect(self.c.pause)
            self.tip.previous_button.clicked.connect(self.previous)
            self.tip.next_button.clicked.connect(self.next)
            self.tip.skip_button.clicked.connect(self.all_wrong)
            self.choice_widget = QWidget(self.tip.body)
            grid = QGridLayout(self.choice_widget)
            grid.setContentsMargins(0, 0, 0, 0)
            self.choices = {}
            for i, field in enumerate(FIELDS):
                box = CheckmarkCheckBox(OCR_REGION_LABELS[field])
                box.setChecked(self.phase == 'choose_fields' and field in self.fields)
                box.toggled.connect(self._choice_changed)
                grid.addWidget(box, i // 4, i % 4)
                self.choices[field] = box
            self.tip.body_layout.addWidget(self.choice_widget)
            d.layout().addWidget(self.tip)
            d.table.setMinimumHeight(160)
            d.installEventFilter(self)
            self.shade = OcrFocusShade(d, self.tip)
            area = d.screen().availableGeometry()
            d.resize(min(980, area.width() - 40), min(760, area.height() - 60))
            d.move(area.left() + 16, area.top() + 24)
        self.dialog.show()
        self._present()

    def action(self, field, action):
        row = self.dialog._field_rows[field]
        return self.dialog.table.cellWidget(row, 3).findChildren(QPushButton)[action]

    def _key_name(self, direction):
        from auto_bdsp_rng.ui.easycon_panel import _resolve_vpad_button
        mapping = self.window.easycon_tab.key_mapping
        key = next((k for k in mapping.values() if _resolve_vpad_button(k, mapping) == ('stick', 'left', direction.title())), None)
        return QKeySequence(key).toString() if isinstance(key, int) else ('A' if direction == 'LEFT' else 'D')

    def _present(self):
        if self.tip is None or not self.active or self.selecting:
            return
        phase, field = self.phase, self.field
        name = OCR_REGION_LABELS[field]
        self.choice_widget.setVisible(phase == 'choose_fields')
        target = self.dialog.table
        if phase in TEST_PHASES:
            title = '先测试现有 OCR 配置' if phase == 'initial_test' else '重新测试全部'
            copy = ('请先确认游戏停在<b>精灵笔记页</b>，再点击亮起的「测试全部」。软件先读取性格、个性，再自动向右切到能力页读取六项数值。'
                    '<br>完成后请对照游戏画面，确认结果是否正确。')
            target = self.dialog.test_all_button
        elif phase in RESULT_PHASES:
            title, copy = '核对本次测试结果', ('对照游戏中的性格、个性和六项能力值。<b>测试完成不代表每项识别都正确。</b><br>'
                    '八项都有有效结果后，才能点击「全部正确，完成」。部分有问题只重框勾选项；全部有问题则逐项重新设置。'
                    '<br>需要重试时，请先用虚拟手柄回到<b>笔记页</b>，再点击「测试全部」。')
        elif phase == 'choose_fields':
            title, copy = '选择需要重新框选的项目', '勾选识别为空或与游戏不一致的项目。先观看性格、个性的示范，然后只修改这些项目。'
        elif phase in DIRECTION_PHASES:
            direction = 'RIGHT' if phase == 'switch_stats' else 'LEFT'
            key = self._key_name(direction)
            title = '切到能力页' if direction == 'RIGHT' else '回到笔记页'
            copy = (f'用虚拟手柄向{"右" if direction == "RIGHT" else "左"}轻推一下（当前键盘映射 <b>{key}</b>），松开后核对游戏页面。'
                    '<br>已经在目标页面时无需再推。请以独立预览中的实际画面为准，确认后继续。')
            if phase == 'return_notes':
                copy += '<br><b>测试全部必须从笔记页开始，请不要停在能力页直接测试。</b>'
        elif phase == 'field_select':
            title, copy = f'框选{name}区域', (f'点击「{name}」这一行亮起的「框选」，然后在<b>独立预览小窗按住鼠标右键拖动</b>，完整框住对应文字或数值。松开后确认保存。'
                    + ('<br>性格可以框住整句「固執的性格。」；软件会清理为「固执」。' if field == 'nature' else
                       '<br>只框个性这一行，不要包含下一行口味文字。' if field == 'characteristic' else '<br>框住这一项的实际能力值，不是努力值或等级。'))
            target = self.action(field, 0)
        elif phase == 'field_recognize':
            title, copy = f'识别并核对{name}', f'区域已保存。点击亮起的「识别」，等待本次结果，再与独立预览中的{name}核对。需要检查框的位置时，也可以点击「显示」。'
            target = self.action(field, 2)
        else:
            title, copy = f'{name}识别正确吗？', '对照本次识别结果与游戏画面。正确后进入下一项；有问题就重新框选本项。'
        self.tip.show_step(GuideStep('ocr', '5.3.5 · OCR 设置', title, copy, target, (target,)))
        self.tip.setFixedWidth(max(280, self.dialog.width() - 36))
        self.navigation()
        self.tip.fit_height(min(270, max(215, self.dialog.height() // 3)))
        self.tip.show()
        self._clear_highlights()
        if phase.startswith('field_'):
            row = self.dialog._field_rows[field]
            self.dialog.table.scrollToItem(self.dialog.table.item(row, 0))
            if isinstance(target, QPushButton):
                target.setStyleSheet('border:2px solid #28B886; border-radius:5px; background:#EAF8F1;')
            for col in (0, 1, 2, 4):
                self.dialog.table.item(row, col).setBackground(QColor('#EAF8F1'))
            actions = (self.action(field, 0),) if phase == 'field_select' else (self.action(field, 1), self.action(field, 2))
            self.shade.focus(*actions, self.dialog.table.verticalScrollBar(), self.dialog.table.horizontalScrollBar(), extra_rects=self._row_rects)
        elif phase in RESULT_PHASES:
            self.shade.focus(self.dialog.table, self.dialog.test_all_button)
        else:
            self.shade.focus(target)
        preview_copy = '对照实际游戏画面核对本次 OCR 结果'
        if phase in DIRECTION_PHASES:
            preview_copy = f'{title} · 轻按 {self._key_name("RIGHT" if phase == "switch_stats" else "LEFT")} 后松开，再确认页面'
        self.preview.show_message(preview_copy)

    def _row_rects(self):
        table = self.dialog.table
        row = self.dialog._field_rows[self.field]
        rects = []
        for col in (0, 1, 2, 4):
            rect = table.visualItemRect(table.item(row, col)).intersected(table.viewport().rect())
            if not rect.isEmpty():
                rects.append(QRect(table.viewport().mapTo(self.dialog, rect.topLeft()), rect.size()))
        return rects

    def navigation(self, *_):
        if self.tip is None or not self.active:
            return
        t, phase = self.tip, self.phase
        busy = self.dialog.interaction_busy or self.selecting
        t.status.setVisible(phase in TEST_PHASES + RESULT_PHASES + DIRECTION_PHASES or phase == 'field_result')
        t.status.setStyleSheet('')
        t.next_button.setToolTip('')
        for button in (t.next_button, t.previous_button, t.skip_button):
            button.setEnabled(not busy)
        t.previous_button.setVisible(phase in RESULT_PHASES or phase.startswith('field_'))
        t.skip_button.setVisible(phase in RESULT_PHASES)
        t.previous_button.setText('部分有问题' if phase in RESULT_PHASES else '重新框选')
        t.skip_button.setText('全部有问题')
        if phase in TEST_PHASES:
            t.next_button.setText('等待测试完成')
            t.next_button.setEnabled(False)
            t.status.setText('正在测试，请等待识别完成。' if busy else '请从笔记页开始，点击上方亮起的「测试全部」。')
        elif phase in RESULT_PHASES:
            reason = self._completion_block_reason()
            t.next_button.setText('全部正确，完成')
            t.next_button.setEnabled(not busy and not reason)
            t.next_button.setToolTip(reason)
            t.status.setText(reason or '八项均已识别。请对照游戏画面，全部正确后点击完成。')
            if reason and not busy:
                t.status.setStyleSheet('color: #B64032;')
        elif phase == 'choose_fields':
            t.next_button.setText('观看框选教学')
            t.next_button.setEnabled(not busy and any(b.isChecked() for b in self.choices.values()))
        elif phase in DIRECTION_PHASES:
            t.next_button.setText('已到能力页' if phase == 'switch_stats' else '已到笔记页')
            t.status.setText('已收到方向操作，请确认游戏画面后继续。' if self._direction_seen else '已在对应页面时，可以直接确认。')
        elif phase in ('field_select', 'field_recognize'):
            t.next_button.setText('等待框选保存' if phase == 'field_select' else '等待识别完成')
            t.next_button.setEnabled(False)
        elif phase == 'field_result':
            t.next_button.setText('识别正确，继续')
            t.next_button.setEnabled(not busy and self._recognition_current() and valid_result(self.field, self._text(self.field)))
            t.status.setText('本次识别：' + self._text(self.field))

    def next(self):
        if self.tip is None or not self.tip.next_button.isEnabled() or self.dialog.interaction_busy:
            return
        phase = self.phase
        if phase in RESULT_PHASES:
            if not self._all_correct():
                return
            self.c.script_guide.go('exit')
            if not self.active:
                self._detach()
                self.preview.restore()
                self.window.statusBar().showMessage('OCR 配置已完成')
        elif phase == 'choose_fields':
            selected = tuple(f for f, b in self.choices.items() if b.isChecked())
            if selected:
                self._detach()
                self.preview.restore()
                self.go('demo', fields=selected, index=0)
        elif phase in DIRECTION_PHASES:
            self._recognized = None
            self.go('final_test' if phase == 'return_notes' else 'field_select')
        elif phase == 'field_result' and self._recognition_current():
            if self.index + 1 == len(self.fields):
                self.go('return_notes')
            else:
                next_field = self.fields[self.index + 1]
                phase = 'switch_stats' if self.field in NOTE_REGION_FIELDS and next_field in STAT_REGION_FIELDS else 'field_select'
                self._recognized = None
                self.go(phase, index=self.index + 1)

    def previous(self):
        if self.dialog is None or self.dialog.interaction_busy:
            return
        if self.phase in RESULT_PHASES:
            suggested = tuple(f for f in FIELDS if not valid_result(f, self._text(f)))
            self.go('choose_fields', fields=suggested, index=0)
            for field, box in self.choices.items():
                box.blockSignals(True)
                box.setChecked(field in suggested)
                box.blockSignals(False)
            self.navigation()
        elif self.phase.startswith('field_'):
            self._recognized = None
            self.go('field_select')

    def all_wrong(self):
        if self.phase in RESULT_PHASES and not self.dialog.interaction_busy:
            self._detach()
            self.preview.restore()
            self.go('demo', fields=FIELDS, index=0)

    def _choice_changed(self, *_):
        if self.active and self.phase == 'choose_fields':
            fields = tuple(f for f, box in self.choices.items() if box.isChecked())
            self.c._persist('auto_script_config', 'ocr:choose_fields:' + ','.join(fields) + ':0')
        self.navigation()

    def _signature(self):
        return (repr(self.dialog.region_config.to_settings_dict()), self.window._video_source_generation)

    def _text(self, field):
        return self.dialog.table.item(self.dialog._field_rows[field], 4).text().strip()

    def _test_current(self):
        return (self.dialog is not None and self._test_done and self._test_signature == self._signature()
                and self._test_values == tuple(self._text(f) for f in FIELDS))

    def _all_correct(self):
        return not self._completion_block_reason()

    def _completion_block_reason(self):
        if self.dialog is None:
            return '请先打开 OCR 设置并完成一次「测试全部」。'
        if self.dialog.interaction_busy or self.selecting:
            return '正在测试或识别，请等待完成后再核对结果。'
        if not self._test_current():
            return '尚无本次完整测试结果，或配置、视频源、识别结果已改变。请回到笔记页，重新点击「测试全部」。'
        if not self._test_ok:
            return (self._test_message or '本次测试失败。') + '\n请检查视频源与游戏页面，回到笔记页后重试「测试全部」。'
        invalid = [OCR_REGION_LABELS[f] for f in FIELDS
                   if self.dialog.region_config.get(f) is None or not valid_result(f, self._text(f))]
        if invalid:
            return ('暂不能完成：' + '、'.join(invalid) + '缺少有效识别结果。\n'
                    '可选择「部分有问题」修复这些项目，或回到笔记页后重新「测试全部」。')
        return ''

    def _test_start(self):
        if not self.active:
            return
        self._test_started, self._test_done, self._test_ok = True, False, False
        self._test_message = ''
        self._test_signature = self._signature()
        self.navigation()

    def _test_finish(self, success, message):
        if not self.active or not self._test_started:
            return
        self._test_started, self._test_done, self._test_ok = False, True, success
        self._test_message = message
        self._test_values = tuple(self._text(f) for f in FIELDS)
        self.go('final_result' if self.phase in ('final_test', 'final_result') else 'initial_result')

    def _recognition_start(self, field, region):
        if self.active and self.phase in ('field_recognize', 'field_result') and field == self.field:
            self._recognizing = (field, self._signature())
            self._recognized = None
            self.navigation()

    def _recognition_finish(self, field, text):
        if not self.active or self._recognizing != (field, self._signature()) or field != self.field:
            return
        self._recognized = (field, self._signature(), self._text(field))
        self._recognizing = None
        self.go('field_result')

    def _recognition_current(self):
        return self.dialog is not None and self._recognized == (self.field, self._signature(), self._text(self.field))

    def _region_changed(self, _field):
        self._test_done = False
        self._recognized = None
        if self.active and self.phase == 'field_result':
            self.go('field_recognize')

    def _selection_started(self, mode):
        if not self.active or self.tip is None or mode != 'ocr_region':
            return
        if self.window._ocr_selection_field != self.field:
            self.window._cancel_preview_selection()
            return
        self._recognized = None
        self.selecting = True
        self.dialog.hide()
        self._show_selection()

    def _show_selection(self):
        self.c.overlay.shade_all()
        self.preview.show_message(f'框选{OCR_REGION_LABELS[self.field]} · 按住鼠标右键拖动，完整框住文字或数值；松开后确认保存', selecting=True)

    def _region_selected(self, field, _region):
        if self.active and self.selecting and field == self.field:
            self.selecting = False
            QTimer.singleShot(0, lambda: self.go('field_recognize') if self.active else None)

    def _selection_finished(self, mode, _ok):
        if self.active and mode == 'ocr_region':
            self.selecting = False
            QTimer.singleShot(0, self.c._show_workspace)

    def _controller_action(self, action, down):
        if self.active and self.phase in DIRECTION_PHASES and not down:
            direction = 'RIGHT' if self.phase == 'switch_stats' else 'LEFT'
            if action == ('stick', 'left', direction.title()) or action == ('stick', 'hat', direction.title()):
                self._direction_seen = True
                self.navigation()

    def poll(self):
        if not self.active:
            return
        overlay = self.c.overlay
        if overlay.isVisible() and not overlay.suspended:
            needs_control = self.needs_control
            if (needs_control != self._waiting_for_control or (needs_control and not overlay.waiting_for_page
                    and self.control_step().target not in overlay.spec.highlights)):
                self.c._show_workspace()
        if self.tip is not None:
            self.navigation()

    def eventFilter(self, obj, event):
        if self.active and event.type() == QEvent.Type.Close:
            if obj is self.dialog or obj is self.preview.preview:
                QTimer.singleShot(0, self.c.pause)
        elif obj is self.dialog and event.type() == QEvent.Type.Resize and self.tip is not None:
            QTimer.singleShot(0, self._present)
        return False

    def _clear_highlights(self):
        if self.dialog is None:
            return
        for button in self.dialog._row_action_buttons:
            button.setStyleSheet('')
        for row in range(self.dialog.table.rowCount()):
            for col in (0, 1, 2, 4):
                self.dialog.table.item(row, col).setBackground(QBrush())

    def _detach(self):
        if self.tip is None:
            return
        dialog, tip = self.dialog, self.tip
        self.tip = None
        self._clear_highlights()
        self.shade.hide()
        self.shade.deleteLater()
        self.shade = None
        dialog.removeEventFilter(self)
        dialog.layout().removeWidget(tip)
        tip.hide()
        tip.deleteLater()
        size, minimum, table_height, geometry = self.layout_state
        dialog.table.setMinimumHeight(table_height)
        dialog.setMinimumSize(minimum)
        dialog.resize(size)
        dialog.setGeometry(geometry)
        dialog.hide()
        self.layout_state = None

    def pause(self):
        if self.selecting:
            self.selecting = False
            if self.window._selection_mode == 'ocr_region':
                self.window._cancel_preview_selection()
        if self.demo is not None:
            self.demo.reject()
        self._detach()
        self.preview.restore()
