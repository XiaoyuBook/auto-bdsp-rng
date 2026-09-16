"""Loop the two note-page ROI operations on the user's verified game image."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic

from PySide6.QtCore import QEvent, QPointF, QRectF, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QApplication, QDialog, QFrame, QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from auto_bdsp_rng.resources import resource_path
from auto_bdsp_rng.automation.auto_rng.ocr_regions import OcrRegion, OCR_REGION_LABELS
from auto_bdsp_rng.ui.ocr_settings_dialog import OcrSettingsDialog


class OcrDemoView(QGraphicsView):
    def __init__(self, scene, lesson):
        super().__init__(scene)
        self.lesson = lesson
        self.setInteractive(False)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def drawForeground(self, painter, _bounds):
        hole, cursor, click = self.lesson.visual_state()
        mask = QPainterPath()
        mask.setFillRule(Qt.FillRule.OddEvenFill)
        mask.addRect(self.sceneRect())
        mask.addRoundedRect(hole, 7, 7)
        painter.fillPath(mask, QColor(0, 0, 0, 160))
        painter.setPen(QPen(QColor('#61D8AB'), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(hole, 7, 7)
        if cursor is None:
            return
        if click:
            painter.setPen(QPen(QColor('#04A878'), 3))
            painter.drawEllipse(cursor, 18, 18)
        painter.save()
        painter.translate(cursor)
        painter.setPen(QPen(QColor('#243D31'), 1.5))
        painter.setBrush(QColor('white'))
        painter.drawPolygon(QPolygonF([QPointF(0, 0), QPointF(3, 25), QPointF(10, 18), QPointF(16, 29),
                                       QPointF(21, 26), QPointF(15, 16), QPointF(26, 15)]))
        painter.restore()


class OcrDemoDialog(QDialog):
    learnedRequested = Signal()
    FIELD_DURATION = 21000
    DURATION = 44500

    def __init__(self, parent):
        super().__init__(parent)
        from auto_bdsp_rng.ui.main_window import PictureInPicturePreview
        import cv2
        import numpy as np

        self.setWindowTitle('5.3.5 · 性格与个性框选教学')
        self.setModal(True)
        area = parent.screen().availableGeometry()
        self.resize(min(1080, area.width() - 40), min(820, area.height() - 60))
        self.setMinimumSize(620, 490)
        self.setStyleSheet('QDialog {background:#F5F8F6;} QLabel {color:#34483D;} QPushButton {padding:7px 12px; background:white; border:1px solid #BDD5C7; border-radius:6px;}')
        self.temp = TemporaryDirectory(prefix='bdsp-ocr-lesson-')
        self.settings = QSettings(str(Path(self.temp.name) / 'demo.ini'), QSettings.Format.IniFormat)
        self.examples = json.loads(resource_path('docs', 'assets', 'guide-ocr', 'recognition.json').read_text(encoding='utf-8'))['fields']
        self.panel = OcrSettingsDialog(settings=self.settings)
        self.panel.setWindowFlags(Qt.WindowType.Widget)
        self.panel.setFixedSize(980, 600)
        self.pip = PictureInPicturePreview()
        self.pip.setWindowFlags(Qt.WindowType.Widget)
        self.pip.setFixedSize(900, 575)
        heading = QLabel('独立预览 · 在游戏画面中框选')
        heading.setStyleSheet('font-size:16px; color:#087C58; padding:4px;')
        self.pip.layout().insertWidget(0, heading)
        self.preview = self.pip.frame_label
        frame = cv2.imdecode(np.frombuffer(resource_path('docs', 'assets', 'guide-ocr', 'notes.jpg').read_bytes(), np.uint8), cv2.IMREAD_COLOR)
        self.pip.set_frames(frame)
        self.scene = QGraphicsScene(self)
        self.scene.setSceneRect(0, 0, 980, 600)
        self.panel_proxy = self.scene.addWidget(self.panel)
        self.preview_proxy = self.scene.addWidget(self.pip)
        self.preview_proxy.setPos(40, 10)
        self.confirmation = QFrame()
        self.confirmation.setFixedSize(340, 145)
        self.confirmation.setStyleSheet('QFrame {background:white; border:1px solid #CFE2D7; border-radius:10px;} QLabel {border:0; font-size:15px;} QPushButton {background:#087C58; color:white; border:0; border-radius:6px; padding:8px;}')
        confirmation_layout = QVBoxLayout(self.confirmation)
        confirmation_layout.addWidget(QLabel('确认 OCR 区域'))
        self.confirm_copy = QLabel()
        confirmation_layout.addWidget(self.confirm_copy)
        self.confirm_button = QPushButton('确认保存')
        self.confirm_button.clicked.connect(self._confirm)
        confirmation_layout.addWidget(self.confirm_button)
        self.confirm_proxy = self.scene.addWidget(self.confirmation)
        self.confirm_proxy.setPos(600, 415)
        self.view = OcrDemoView(self.scene, self)
        self.preview.roiSelected.connect(self._selected)
        self.panel.regionSelectionRequested.connect(self._select)
        self.panel.regionDisplayRequested.connect(self._display)
        self.panel.recognitionRequested.connect(lambda *_: None)
        self.title, self.copy = QLabel(), QLabel()
        self.title.setStyleSheet('font-size:18px; font-weight:600; color:#087C58;')
        self.copy.setWordWrap(True)
        self.copy.setMinimumHeight(46)
        layout = QVBoxLayout(self)
        layout.addWidget(self.view, 1)
        layout.addWidget(self.title)
        layout.addWidget(self.copy)
        actions = QHBoxLayout()
        actions.addWidget(QLabel('循环演示 · 示例游戏画面'), 1)
        self.pause_button = QPushButton('暂停演示')
        self.pause_button.clicked.connect(self.toggle_playing)
        self.replay_button = QPushButton('重新播放')
        self.replay_button.clicked.connect(self.restart)
        self.close_button = QPushButton('暂时收起')
        self.close_button.clicked.connect(self.reject)
        self.learned_button = QPushButton('我已学会')
        self.learned_button.setStyleSheet('background:#087C58; color:white;')
        self.learned_button.clicked.connect(self.learnedRequested)
        for button in (self.close_button, self.replay_button, self.pause_button, self.learned_button):
            button.setAutoDefault(False)
            actions.addWidget(button)
        layout.addLayout(actions)
        self.elapsed = self.applied = 0
        self.playing, self.closed = True, False
        self.events = []
        for i, field in enumerate(('nature', 'characteristic')):
            base = i * self.FIELD_DURATION
            self.events.extend((
                (base, lambda f=field: self._start_field(f)),
                (base + 2000, lambda: self.action(0).click()),
                (base + 2600, self._show_preview),
                (base + 4200, lambda: self.mouse(QEvent.Type.MouseButtonPress, self._drag_points()[0])),
                (base + 6600, lambda: self.mouse(QEvent.Type.MouseButtonRelease, self._drag_points()[1])),
                (base + 8400, self.confirm_button.click),
                (base + 9000, self._table),
                (base + 11000, lambda: self.action(1).click()),
                (base + 11600, self._show_preview),
                (base + 14300, self._table),
                (base + 16500, lambda: self.action(2).click()),
                (base + 17400, lambda: self.panel.finish_recognition(self.field, self.examples[self.field]['text'])),
            ))
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)
        self.restart()

    def action(self, index):
        row = 0 if self.field == 'nature' else 1
        return self.panel.table.cellWidget(row, 3).findChildren(QPushButton)[index]

    def _start_field(self, field):
        self.field = field
        self._table()
        self.panel.table.scrollToItem(self.panel.table.item(0 if field == 'nature' else 1, 0))

    def _region(self):
        return OcrRegion(*self.examples[self.field]['region'])

    def _drag_points(self):
        rect = self.preview._image_rect_to_widget_rect(self._region())
        return QPointF(rect.topLeft()), QPointF(rect.bottomRight())

    def mouse(self, event_type, point):
        button = Qt.MouseButton.NoButton if event_type == QEvent.Type.MouseMove else Qt.MouseButton.RightButton
        buttons = Qt.MouseButton.NoButton if event_type == QEvent.Type.MouseButtonRelease else Qt.MouseButton.RightButton
        QApplication.sendEvent(self.preview, QMouseEvent(event_type, point, point, button, buttons, Qt.KeyboardModifier.NoModifier))

    def _select(self, field):
        self.preview.clear_ocr_overlay()
        self.preview.set_selection_enabled(True)

    def _selected(self, region):
        self.pending_region = region
        self.preview.set_selection_enabled(False)
        self.preview.set_ocr_overlay(self.field, region)
        self.confirm_copy.setText(f'是否保存“{OCR_REGION_LABELS[self.field]}”区域？')
        self.confirmation.show()

    def _confirm(self):
        self.panel.set_region(self.field, self.pending_region)

    def _table(self):
        self.confirmation.hide()
        self.pip.hide()
        self.panel.show()
        self.panel_proxy.setPos(0, 0)

    def _show_preview(self):
        self.panel.hide()
        self.pip.show()
        self.preview_proxy.setPos(40, 10)
        self.pip._refresh_frame()

    def _display(self, field, region):
        self.preview.set_ocr_overlay(field, region)

    def restart(self):
        self.panel.finish_warmup(True, 'OCR 已就绪')
        for field in ('nature', 'characteristic'):
            self.panel.finish_recognition(field, '未测试')
            self.panel.reset_region(field)
        self.panel.table.scrollToTop()
        self.preview.clear_ocr_overlay()
        self.preview.set_selection_enabled(False)
        self.pending_region = None
        self.elapsed = self.applied = 0
        self.playing = True
        self.last_tick = monotonic()
        self.pause_button.setText('暂停演示')
        self.advance_to(0)
        self.timer.start()

    def advance_to(self, elapsed):
        if elapsed < self.elapsed:
            self.restart()
        self.elapsed = elapsed
        while self.applied < len(self.events) and self.events[self.applied][0] <= elapsed:
            self.events[self.applied][1]()
            self.applied += 1
        t = elapsed % self.FIELD_DURATION
        if 4200 <= t < 6600 and elapsed < 42000:
            start, end = self._drag_points()
            self.mouse(QEvent.Type.MouseMove, start + (end - start) * ((t - 4200) / 2400))
        self.present()

    def _tick(self):
        now = monotonic()
        delta = min(100, round((now - self.last_tick) * 1000))
        self.last_tick = now
        if self.playing:
            self.restart() if self.elapsed + delta >= self.DURATION else self.advance_to(self.elapsed + delta)

    def toggle_playing(self):
        self.playing = not self.playing
        self.pause_button.setText('暂停演示' if self.playing else '继续演示')

    def present(self):
        t = self.elapsed % self.FIELD_DURATION
        name = OCR_REGION_LABELS[self.field]
        prefix = '1' if self.field == 'nature' else '2'
        if self.elapsed >= 42000:
            title, copy = '接下来，在自己的画面上练习', '性格、个性完成后，向右切到能力页，逐项框选并识别六项数值；最后按 A（LEFT）回笔记页，再点击「测试全部」。'
        elif t < 2600:
            title, copy = f'{prefix}.1 · 点击{name}这一行的「框选」', f'游戏停留在笔记页，找到“{name}”这一行。接下来在独立预览小窗中右键框选。'
        elif t < 9000:
            title, copy = f'{prefix}.2 · 在独立预览中按住右键拖动', ('完整框住「固執的性格。」这一行，保留少量边缘。松开右键后确认保存。' if self.field == 'nature' else '完整框住「對聲音敏感。」这一行，不要包含下面的口味文字。松开右键后确认保存。')
        elif t < 14300:
            title, copy = f'{prefix}.3 · 点击「显示」，检查框的位置', '确认保存后区域自动生效。点击「显示」，独立预览中的绿色框应完整覆盖对应文字。'
        else:
            title, copy = f'{prefix}.4 · 点击「识别」，对照结果', ('这一整行会识别并清理为「固执」，不用只框红色的两个字。' if self.field == 'nature' else '这里应识别为「对声音敏感」。请对照游戏中的个性文字确认，不要误框成口味。')
        self.title.setText(title)
        self.copy.setText(copy)
        self.view.viewport().update()

    def _scene_rect(self, widget, parent, proxy):
        rect = QRectF(widget.rect())
        rect.moveTopLeft(QPointF(widget.mapTo(parent, widget.rect().topLeft())))
        return proxy.mapRectToScene(rect)

    @staticmethod
    def _move(start, end, amount):
        amount = max(0, min(1, amount))
        return start + (end - start) * (amount * amount * (3 - 2 * amount))

    def visual_state(self):
        t = self.elapsed % self.FIELD_DURATION
        if self.elapsed >= 42000:
            return self.panel_proxy.sceneBoundingRect(), None, False
        start, end = self._drag_points()
        origin = QPointF(self.preview.mapTo(self.pip, self.preview.rect().topLeft()))
        start, end = self.preview_proxy.mapToScene(origin + start), self.preview_proxy.mapToScene(origin + end)
        if self.confirmation.isVisible():
            button = self._scene_rect(self.confirm_button, self.confirmation, self.confirm_proxy)
            return self.confirm_proxy.sceneBoundingRect(), self._move(end, button.center(), (t - 6600) / 1800), 8400 <= t < 8900
        if self.pip.isVisible():
            region = self.preview._image_rect_to_widget_rect(self._region())
            hole = self.preview_proxy.mapRectToScene(QRectF(region).translated(origin)).adjusted(-10, -10, 10, 10)
            if t < 4200:
                previous = self._scene_rect(self.action(0), self.panel, self.panel_proxy).center()
                return hole, self._move(previous, start, (t - 2600) / 1600), False
            if t < 6600:
                return hole, start + (end - start) * ((t - 4200) / 2400), True
            return hole, None, False
        if t >= 17400:
            row = self.panel.table.visualItemRect(self.panel.table.item(0 if self.field == 'nature' else 1, 4))
            origin = self.panel.table.viewport().mapTo(self.panel, row.topLeft())
            return self.panel_proxy.mapRectToScene(QRectF(origin.x(), origin.y(), row.width(), row.height())), None, False
        index, at = (0, 2000) if t < 2600 else (1, 11000) if t < 14300 else (2, 16500)
        rect = self._scene_rect(self.action(index), self.panel, self.panel_proxy)
        previous = QPointF(490, 520) if index == 0 else self._scene_rect(self.confirm_button, self.confirmation, self.confirm_proxy).center() if index == 1 else self.preview_proxy.sceneBoundingRect().center()
        return rect.adjusted(-6, -5, 6, 5), self._move(previous, rect.center(), (t - at + 1500) / 1500), at <= t < at + 500

    def done(self, result):
        if not self.closed:
            self.closed = True
            self.timer.stop()
            self.settings.sync()
            self.temp.cleanup()
        super().done(result)
