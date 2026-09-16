"""OCR animation followed by checks in the existing OCR settings window."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtWidgets import QDialog

from auto_bdsp_rng.ui.guide_steps import GuideStep
from auto_bdsp_rng.ui.guide_tip import GuideTip
from auto_bdsp_rng.ui.ocr_demo_dialog import OcrDemoDialog


CHECKS = {
    'notes': ('先核对笔记页', '反查脚本结束后保持在精灵笔记页。检查「性格」「个性」两项：点击「显示」核对区域，再点「识别」对照文字。框不准时点「框选」，在 Seed 捕捉画面按住右键重新框选，确认后会自动保存。'),
    'stats': ('再核对六项能力值', '在游戏中切到能力页，逐项「显示」「识别」HP、攻击、防御、特攻、特防、速度，确认结果与画面一致。分辨率或布局不同，请重新框选；不要在笔记页测试能力值。'),
    'battle': ('了解判闪区域与完整测试', '「判闪对话区域」用于读取战斗文本；「御三家战斗区域」用于识别战斗按钮，需要在对应战斗画面核对。导入默认区域会覆盖现有框选，请先核对分辨率。\n「测试全部」会从笔记页开始读取并通过伊机控翻到能力页；运行前先回到笔记页。确认配置无误后完成本步。'),
}


class OcrGuide(QObject):
    def __init__(self, controller):
        super().__init__(controller)
        self.c, self.window = controller, controller.window
        self.demo = None
        self.dialog = self.tip = None
        self.layout_state = None
        self.selecting = False
        self._opening = False
        self.window.auto_rng_tab.captureInfoRequested.connect(self._opened)
        self.window.captureSelectionStarted.connect(self._selection_started)
        self.window.captureSelectionFinished.connect(self._selection_finished)
        self.window.ocrRegionSelected.connect(lambda *_: self._selection_finished('ocr_region', True))

    @property
    def active(self):
        return self.c.active and self.c.step == 'auto_script_config' and self.c.detail.startswith('ocr:')

    def prepare(self):
        if not self.active:
            return False
        phase = self.c.detail.partition(':')[2]
        if phase == 'demo':
            self.c.overlay.shade_all()
            if self.demo is None:
                self.demo = OcrDemoDialog(self.window)
                self.demo.learnedRequested.connect(self._learned)
                self.demo.finished.connect(self._demo_closed)
                self.demo.open()
            return True
        if phase in CHECKS:
            if self.selecting:
                self._show_selection()
            else:
                self._show_settings()
            return True
        return False

    def _learned(self):
        if self.c._persist('auto_script_config', 'ocr:notes'):
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
        if self._opening or not self.active:
            return
        if self.c.detail == 'ocr:select':
            self.window._ocr_settings_dialog.hide()
            self.c._go('auto_script_config', 'ocr:demo')

    def _show_settings(self):
        self.c.overlay.shade_all()
        if self.tip is None:
            self._opening = True
            try:
                self.window.open_ocr_settings()
            finally:
                self._opening = False
            self.dialog = self.window._ocr_settings_dialog
            dialog = self.dialog
            self.layout_state = (dialog.size(), dialog.minimumSize(), dialog.table.minimumHeight())
            self.tip = GuideTip(dialog)
            self.tip.range_options.hide()
            self.tip.setMinimumWidth(0)
            self.tip.setMaximumWidth(16777215)
            self.tip.close_button.clicked.connect(self.c.pause)
            self.tip.previous_button.clicked.connect(self.previous)
            self.tip.next_button.clicked.connect(self.next)
            self.tip.skip_button.hide()
            dialog.layout().addWidget(self.tip)
            dialog.table.setMinimumHeight(140)
            dialog.installEventFilter(self)
            area = dialog.screen().availableGeometry()
            dialog.resize(min(980, area.width() - 40), min(820, area.height() - 60))
        self.dialog.show()
        self._present()

    def _present(self):
        if self.tip is None:
            return
        phase = self.c.detail.partition(':')[2]
        title, copy = CHECKS.get(phase, CHECKS['notes'])
        self.tip.show_step(GuideStep('ocr', '5.3.5 · OCR 设置', title, copy, self.dialog.table, (self.dialog.table,)))
        self.tip.setFixedWidth(max(280, self.dialog.width() - 36))
        self.tip.fit_height(min(235, max(180, self.dialog.height() // 3)))
        self.tip.previous_button.setText('返回演示' if phase == 'notes' else '上一步')
        self.tip.next_button.setText('已核对，继续' if phase != 'battle' else '完成 OCR 设置')
        self.tip.next_button.setEnabled(not self.dialog.interaction_busy)
        self.tip.show()
        row = {'notes': 0, 'stats': 2, 'battle': 8}.get(phase, 0)
        self.dialog.table.scrollToItem(self.dialog.table.item(row, 0))

    def next(self):
        if self.dialog is None or self.dialog.interaction_busy:
            return
        phase = self.c.detail.partition(':')[2]
        if phase == 'battle':
            self._detach()
            self.c.script_guide.go('exit')
        else:
            self.c._go('auto_script_config', 'ocr:' + ('stats' if phase == 'notes' else 'battle'))

    def previous(self):
        phase = self.c.detail.partition(':')[2]
        if phase == 'notes':
            self._detach()
            self.c._go('auto_script_config', 'ocr:demo')
        else:
            self.c._go('auto_script_config', 'ocr:' + ('notes' if phase == 'stats' else 'stats'))

    def _selection_started(self, mode):
        if not self.active or self.tip is None or mode != 'ocr_region':
            return
        self.selecting = True
        self.dialog.hide()
        self.window.tabs.setCurrentWidget(self.window.project_xs_tab)
        self._show_selection()

    def _show_selection(self):
        self.c.overlay.page_widget = self.window.project_xs_tab
        target = self.window.preview_label
        self.c.overlay.configure(GuideStep('ocr', '5.3.5 · 框选 OCR 区域', '在对应文字周围框选',
                                          '在画面中按住鼠标右键拖动，完整框住当前项目的文字。松开并确认保存后，将返回 OCR 设置；取消会保留原区域。', target, (target,)))
        self.c.overlay.reveal()
        self.c.overlay.tip.next_button.setEnabled(False)
        self.c.overlay.tip.previous_button.setEnabled(False)
        self.c.overlay.tip.skip_button.setEnabled(False)

    def _selection_finished(self, mode, _ok):
        if self.active and self.selecting and mode == 'ocr_region':
            self.selecting = False
            QTimer.singleShot(0, self.c._show_workspace)

    def poll(self):
        if self.tip is not None and self.active:
            self.tip.next_button.setEnabled(not self.dialog.interaction_busy)

    def eventFilter(self, obj, event):
        if obj is self.dialog:
            if event.type() == QEvent.Type.Close and self.active:
                QTimer.singleShot(0, self.c.pause)
            elif event.type() == QEvent.Type.Resize and self.tip is not None:
                QTimer.singleShot(0, self._present)
        return False

    def _detach(self):
        if self.tip is None:
            return
        dialog, tip = self.dialog, self.tip
        self.tip = None
        dialog.removeEventFilter(self)
        dialog.layout().removeWidget(tip)
        tip.hide()
        tip.deleteLater()
        size, minimum, table_height = self.layout_state
        dialog.table.setMinimumHeight(table_height)
        dialog.setMinimumSize(minimum)
        dialog.resize(size)
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
