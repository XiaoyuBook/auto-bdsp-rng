"""Looping OCR lesson using the real settings table and ROI preview widget."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QApplication, QDialog, QFrame, QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

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
        d = self.lesson
        hole, cursor, click = d.visual_state()
        mask = QPainterPath()
        mask.setFillRule(Qt.FillRule.OddEvenFill)
        mask.addRect(self.sceneRect())
        mask.addRoundedRect(hole, 7, 7)
        painter.fillPath(mask, QColor(0, 0, 0, 165))
        painter.setPen(QPen(QColor('#61D8AB'), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(hole, 7, 7)
        if cursor is None:
            return
        if click:
            painter.setPen(QPen(QColor('#04A878'), 3))
            painter.drawEllipse(cursor, 19, 19)
        painter.save()
        painter.translate(cursor)
        painter.setPen(QPen(QColor('#243D31'), 1.5))
        painter.setBrush(QColor('white'))
        painter.drawPolygon(QPolygonF([QPointF(0, 0), QPointF(3, 25), QPointF(10, 18),
                                       QPointF(16, 29), QPointF(21, 26), QPointF(15, 16), QPointF(26, 15)]))
        painter.restore()


class OcrDemoDialog(QDialog):
    learnedRequested = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        from auto_bdsp_rng.ui.main_window import RoiPreviewLabel
        self.setWindowTitle('5.3.5 · OCR 框选与识别演示')
        self.setModal(True)
        self.resize(min(1080, parent.screen().availableGeometry().width() - 40),
                    min(820, parent.screen().availableGeometry().height() - 60))
        self.setMinimumSize(620, 490)
        self.setStyleSheet('QDialog {background:#F5F8F6;} QLabel {color:#34483D;} QPushButton {padding:7px 12px; background:white; border:1px solid #BDD5C7; border-radius:6px;}')
        self.temp = TemporaryDirectory(prefix='bdsp-ocr-lesson-')
        self.settings = QSettings(str(Path(self.temp.name) / 'demo.ini'), QSettings.Format.IniFormat)
        self.panel = OcrSettingsDialog(settings=self.settings)
        self.panel.setWindowFlags(Qt.WindowType.Widget)
        self.panel.setFixedSize(980, 570)
        self.preview = RoiPreviewLabel()
        self.preview.setFixedSize(980, 570)
        pixmap = QPixmap(980, 570)
        pixmap.fill(QColor('#EDF3F8'))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('white'))
        painter.drawRoundedRect(QRectF(90, 75, 800, 420), 16, 16)
        font = painter.font()
        font.setPixelSize(23)
        painter.setFont(font)
        painter.setPen(QColor('#47596C'))
        painter.drawText(QRect(130, 100, 700, 45), '精灵笔记页 · 示意文字画面')
        font.setPixelSize(26)
        painter.setFont(font)
        painter.drawText(QRect(145, 201, 690, 43), '性格：固执')
        painter.drawText(QRect(145, 345, 690, 43), '个性：喜欢胡闹')
        painter.end()
        self.preview.setPixmap(pixmap)
        self.preview.set_image_geometry(980, 570, QRect(0, 0, 980, 570))
        self.scene = QGraphicsScene(self)
        self.scene.setSceneRect(0, 0, 980, 570)
        self.panel_proxy = self.scene.addWidget(self.panel)
        self.preview_proxy = self.scene.addWidget(self.preview)
        self.confirmation = QFrame()
        self.confirmation.setFixedSize(370, 150)
        self.confirmation.setStyleSheet('QFrame {background:white; border:1px solid #CFE2D7; border-radius:10px;} QLabel {border:0; font-size:15px; color:#34483D;} QPushButton {background:#087C58; color:white; border:0; border-radius:6px; padding:8px; font-size:14px;}')
        confirm_layout = QVBoxLayout(self.confirmation)
        confirm_layout.addWidget(QLabel('确认 OCR 区域'))
        confirm_layout.addWidget(QLabel('是否保存“性格”区域？'))
        self.confirm_button = QPushButton('确认保存')
        self.confirm_button.clicked.connect(self._confirm)
        confirm_layout.addWidget(self.confirm_button)
        self.confirm_proxy = self.scene.addWidget(self.confirmation)
        self.confirm_proxy.setPos(305, 325)
        self.view = OcrDemoView(self.scene, self)
        self.preview.roiSelected.connect(self._selected)
        self.panel.regionSelectionRequested.connect(self._select)
        self.panel.regionDisplayRequested.connect(self._display)
        self.panel.recognitionRequested.connect(lambda *_: None)  # Simulated result; never starts OCR.
        self.title = QLabel()
        self.title.setStyleSheet('font-size:18px; font-weight:600; color:#087C58;')
        self.copy = QLabel()
        self.copy.setWordWrap(True)
        self.copy.setMinimumHeight(44)
        layout = QVBoxLayout(self)
        layout.addWidget(self.view, 1)
        layout.addWidget(self.title)
        layout.addWidget(self.copy)
        actions = QHBoxLayout()
        actions.addWidget(QLabel('循环演示 · 使用示意画面'), 1)
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
        self.playing = True
        self.closed = False
        self.events = (
            (1900, self.panel.warmup_button.click),
            (2900, lambda: self.panel.finish_warmup(True, '演示：OCR 已就绪')),
            (5900, lambda: self.action(0).click()),
            (7900, lambda: self.mouse(QEvent.Type.MouseButtonPress, QPointF(137, 193))),
            (10100, lambda: self.mouse(QEvent.Type.MouseButtonRelease, QPointF(325, 250))),
            (11600, self.confirm_button.click),
            (14900, lambda: self.action(1).click()),
            (17400, self._table),
            (19900, lambda: self.action(2).click()),
            (20900, lambda: self.panel.finish_recognition('nature', '固执（演示结果）')),
            (23500, lambda: self.panel.table.scrollToItem(self.panel.table.item(7, 0))),
            (27000, lambda: self.panel.table.scrollToItem(self.panel.table.item(9, 0))),
        )
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)
        self.restart()

    def action(self, index):
        return self.panel.table.cellWidget(0, 3).findChildren(QPushButton)[index]

    def mouse(self, event_type, point):
        button = Qt.MouseButton.NoButton if event_type == QEvent.Type.MouseMove else Qt.MouseButton.RightButton
        buttons = Qt.MouseButton.NoButton if event_type == QEvent.Type.MouseButtonRelease else Qt.MouseButton.RightButton
        QApplication.sendEvent(self.preview, QMouseEvent(event_type, point, point, button, buttons, Qt.KeyboardModifier.NoModifier))

    def _select(self, _field):
        self.panel.hide()
        self.preview.show()
        self.preview.set_selection_enabled(True)

    def _selected(self, region):
        self.pending_region = region
        self.preview.set_selection_enabled(False)
        self.preview.set_ocr_overlay('nature', region)
        self.confirmation.show()

    def _confirm(self):
        self.panel.set_region('nature', self.pending_region)
        self.confirmation.hide()
        self._table()

    def _table(self):
        self.preview.hide()
        self.panel.show()
        # QDialog may center itself when shown again, even as a scene widget.
        self.panel_proxy.setPos(0, 0)

    def _display(self, field, region):
        self.panel.hide()
        self.preview.show()
        self.preview.set_ocr_overlay(field, region)

    def restart(self):
        self.panel.finish_warmup(False, '未预热')
        self.panel.finish_recognition('nature', '未测试')
        self.panel.reset_region('nature')
        self.panel.table.scrollToTop()
        self.preview.clear_ocr_overlay()
        self.preview.set_selection_enabled(False)
        self.confirmation.hide()
        self._table()
        self.pending_region = None
        self.elapsed = self.applied = 0
        self.playing = True
        self.last_tick = monotonic()
        self.pause_button.setText('暂停演示')
        self.present()
        self.timer.start()

    def advance_to(self, elapsed):
        if elapsed < self.elapsed:
            self.restart()
        self.elapsed = elapsed
        while self.applied < len(self.events) and self.events[self.applied][0] <= elapsed:
            self.events[self.applied][1]()
            self.applied += 1
        if 7900 <= elapsed < 10100:
            amount = (elapsed - 7900) / 2200
            self.mouse(QEvent.Type.MouseMove, QPointF(137 + 188 * amount, 193 + 57 * amount))
        self.present()

    def _tick(self):
        now = monotonic()
        delta = min(100, round((now - self.last_tick) * 1000))
        self.last_tick = now
        if self.playing:
            if self.elapsed + delta >= 30000:
                self.restart()
            else:
                self.advance_to(self.elapsed + delta)

    def toggle_playing(self):
        self.playing = not self.playing
        self.pause_button.setText('暂停演示' if self.playing else '继续演示')

    def present(self):
        t = self.elapsed
        if t < 4000:
            title, copy = '1 · 预热 OCR', '先点击「预热OCR」，等待初始化完成。实际识别会读取你连接的视频画面。'
        elif t < 6400:
            title, copy = '2 · 选择要框选的项目', '以性格为例：游戏停留在笔记页，然后点击「性格」这一行的「框选」。'
        elif t < 11600:
            title, copy = '3 · 按住鼠标右键框选', '在 Seed 捕捉预览中，按住右键拖动，完整框住目标文字；松开后，在确认窗口中确认保存。'
        elif t < 17400:
            title, copy = '4 · 保存后，显示区域核对', '确认后区域会自动保存，不需要另找保存按钮。点击「显示」，检查识别框是否完整覆盖文字。'
        elif t < 23500:
            title, copy = '5 · 点击识别，核对文字', '回到 OCR 设置，点击该行「识别」。这里的“固执”是演示结果；实际请与游戏画面逐字核对，空白或错误时重新框选。'
        elif t < 27000:
            title, copy = '6 · 按画面逐组检查', '性格和个性在笔记页，六项能力值在能力页；判闪区域要切到战斗画面检查。「测试全部」会读取笔记页并控制翻页，运行前先准备好画面和伊机控。'
        else:
            title, copy = '7 · 核对判闪使用的区域', '将游戏切到对应战斗画面，检查判闪对话区域和御三家战斗区域。每个项目都可按刚才的方法显示、框选和识别。'
        self.title.setText(title)
        self.copy.setText(copy)
        self.view.viewport().update()

    def visual_state(self):
        t = self.elapsed
        if self.confirmation.isVisible():
            hole = self.confirm_proxy.sceneBoundingRect()
            rect = QRectF(self.confirm_button.rect())
            rect.moveTopLeft(QPointF(self.confirm_button.mapTo(self.confirmation, self.confirm_button.rect().topLeft())))
            end = self.confirm_proxy.mapToScene(rect.center())
            amount = min(1, max(0, (t - 10100) / 1500))
            start = QPointF(325, 250)
            return hole, start + (end - start) * amount, False
        if self.preview.isVisible():
            hole = QRectF(112, 164, 735, 110)
            if 6400 <= t < 7900:
                amount = max(0, (t - 6400) / 1500)
                return hole, QPointF(490 - 353 * amount, 420 - 227 * amount), False
            if 7900 <= t < 10100:
                amount = (t - 7900) / 2200
                return hole, QPointF(137 + 188 * amount, 193 + 57 * amount), True
            return hole, None, False
        if t >= 23500:
            rows = (2, 7) if t < 27000 else (8, 9)
            table = self.panel.table
            rect = table.visualItemRect(table.item(rows[0], 0)).united(table.visualItemRect(table.item(rows[1], 4)))
            rect = rect.intersected(table.viewport().rect())
            origin = table.viewport().mapTo(self.panel, rect.topLeft())
            return self.panel_proxy.mapRectToScene(QRectF(origin.x(), origin.y(), rect.width(), rect.height())), None, False
        button = self.panel.warmup_button if t < 4000 else self.action(0 if t < 11600 else 1 if t < 17400 else 2)
        rect = QRectF(button.rect())
        rect.moveTopLeft(QPointF(button.mapTo(self.panel, button.rect().topLeft())))
        rect = self.panel_proxy.mapRectToScene(rect)
        event_at = 1900 if t < 4000 else 5900 if t < 11600 else 14900 if t < 17400 else 19900
        amount = min(1, max(0, (t - (event_at - 1500)) / 1500))
        start = QPointF(490, 410)
        cursor = start + (rect.center() - start) * (amount * amount * (3 - 2 * amount))
        hole = rect.adjusted(-8, -6, 8, 6)
        if 20900 <= t:
            row = self.panel.table.visualItemRect(self.panel.table.item(0, 4))
            origin = self.panel.table.viewport().mapTo(self.panel, row.topLeft())
            hole = self.panel_proxy.mapRectToScene(QRectF(origin.x(), origin.y(), row.width(), row.height()))
        return hole, cursor if t < event_at + 600 else None, event_at <= t < event_at + 500

    def done(self, result):
        if not self.closed:
            self.closed = True
            self.timer.stop()
            self.settings.sync()
            self.temp.cleanup()
        super().done(result)
