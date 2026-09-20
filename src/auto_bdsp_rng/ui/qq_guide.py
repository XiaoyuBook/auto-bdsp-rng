"""Native, offline screenshot tutorial for QQ bot registration."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QDesktopServices, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QListWidget, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from auto_bdsp_rng.resources import resource_path


def load_qq_steps():
    path = resource_path("docs", "assets", "guide-qq", "steps.js")
    text = path.read_text(encoding="utf-8")
    steps = json.loads(text[text.index("["):text.rindex("]") + 1])
    for step in steps:
        step["path"] = path.parent / step["image"]
        for key in ("title", "detail", "result"):
            step[key] = step[key].replace("QQ 通知测试工具", "QQ 通知设置").replace("测试工具", "软件的 QQ 通知设置")
        step["actions"] = [action.replace("QQ 通知测试工具", "QQ 通知设置").replace("测试工具", "软件的 QQ 通知设置") for action in step["actions"]]
    steps[-1]["detail"] = "AppSecret 是机器人鉴权密钥。接收通知的人不需要拿到这份密钥。继续下一阶段，在教程内填写凭据并绑定接收方。"
    return steps


class TutorialImage(QWidget):
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.image = QImage()
        self.crop = QRectF()
        self.focus = []
        self.full = False
        self.highlight = True
        self.setMinimumSize(100, 240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("点击放大原图")

    def set_step(self, step):
        self.image = QImage(str(step["path"]))
        self.crop = QRectF(*step["crop"])
        self.focus = step["focus"]
        self.setAccessibleName(step["title"] + "原始截图")
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#F8F9FB"))
        if self.image.isNull():
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "截图资源不可用")
            return
        source = QRectF(self.image.rect()) if self.full else self.crop
        area = QRectF(self.rect()).adjusted(8, 8, -8, -8)
        ratio = min(area.width() / source.width(), area.height() / source.height())
        target = QRectF(0, 0, source.width() * ratio, source.height() * ratio)
        target.moveCenter(area.center())
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(target, self.image, source)
        if self.highlight:
            painter.setClipRect(target)
            painter.setPen(QPen(QColor("#DD7414"), 2))
            painter.setBrush(QColor(239, 171, 55, 30))
            for x, y, w, h in self.focus:
                rect = QRectF(target.x() + (x - source.x()) * ratio,
                              target.y() + (y - source.y()) * ratio, w * ratio, h * ratio)
                painter.drawRoundedRect(rect, 3, 3)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class QQRegistrationGuide(QWidget):
    step_changed = Signal(int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.steps = load_qq_steps()
        self.index = 0
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(20)
        self.list = QListWidget()
        self.list.setObjectName("QQStepList")
        self.list.setFixedWidth(178)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setTextElideMode(Qt.TextElideMode.ElideRight)
        for index, step in enumerate(self.steps):
            self.list.addItem(f"{index + 1:02d}  {step['title']}")
            self.list.item(index).setToolTip(step["title"])
        layout.addWidget(self.list)
        content = QVBoxLayout()
        self.title = QLabel()
        self.title.setObjectName("SectionTitle")
        self.title.setWordWrap(True)
        content.addWidget(self.title)
        tools = QHBoxLayout()
        self.highlight = QCheckBox("点击提示")
        self.highlight.setChecked(True)
        tools.addWidget(self.highlight)
        tools.addStretch()
        self.full_button = QPushButton("查看完整截图")
        tools.addWidget(self.full_button)
        zoom = QPushButton("放大")
        tools.addWidget(zoom)
        content.addLayout(tools)
        self.picture = TutorialImage()
        content.addWidget(self.picture, 1)
        self.action = QLabel()
        self.action.setWordWrap(True)
        self.action.setTextFormat(Qt.TextFormat.PlainText)
        content.addWidget(self.action)
        self.detail = QLabel()
        self.detail.setObjectName("Muted")
        self.detail.setWordWrap(True)
        self.detail.setTextFormat(Qt.TextFormat.PlainText)
        content.addWidget(self.detail)
        layout.addLayout(content, 1)
        self.list.currentRowChanged.connect(self.set_step)
        self.highlight.toggled.connect(self._highlight)
        self.full_button.clicked.connect(self._full)
        zoom.clicked.connect(self.show_zoom)
        self.picture.clicked.connect(self.show_zoom)
        self.list.setCurrentRow(0)

    def set_step(self, index):
        self.index = max(0, min(len(self.steps) - 1, index))
        if self.list.currentRow() != self.index:
            self.list.setCurrentRow(self.index)
            return
        step = self.steps[self.index]
        self.title.setText(f"{self.index + 1:02d} / {len(self.steps)}   {step['title']}")
        self.action.setText("\n".join(step["actions"]))
        self.detail.setText(step["detail"])
        self.picture.full = False
        self.full_button.setText("查看完整截图")
        self.picture.set_step(step)
        self.step_changed.emit(self.index, step["result"])

    def _highlight(self, enabled):
        self.picture.highlight = enabled
        self.picture.update()

    def _full(self):
        self.picture.full = not self.picture.full
        self.full_button.setText("返回重点区域" if self.picture.full else "查看完整截图")
        self.picture.update()

    def show_zoom(self):
        dialog = QDialog(self.window())
        dialog.setWindowTitle(self.steps[self.index]["title"] + " · 原图")
        screen = dialog.screen().availableGeometry()
        dialog.resize(min(1150, screen.width() - 60), min(850, screen.height() - 80))
        layout = QVBoxLayout(dialog)
        bar = QHBoxLayout()
        minus, plus, fit = QPushButton("缩小"), QPushButton("放大"), QPushButton("适应窗口")
        ratio_label = QLabel()
        for widget in (minus, ratio_label, plus, fit):
            bar.addWidget(widget)
        bar.addStretch()
        layout.addLayout(bar)
        area = QScrollArea()
        image = TutorialImage()
        image.set_step(self.steps[self.index])
        image.full = True
        image.highlight = self.highlight.isChecked()
        area.setWidget(image)
        layout.addWidget(area)
        scale = [1.0]

        def resize_image(multiplier=None):
            if multiplier is None:
                scale[0] = min((area.viewport().width() - 20) / image.image.width(),
                               (area.viewport().height() - 20) / image.image.height())
            else:
                scale[0] = min(3.0, max(.15, scale[0] * multiplier))
            image.setFixedSize(image.image.size() * scale[0] + QSize(16, 16))
            ratio_label.setText(f"{scale[0]:.0%}")
        if image.image.isNull():
            return
        minus.clicked.connect(lambda: resize_image(.8))
        plus.clicked.connect(lambda: resize_image(1.25))
        fit.clicked.connect(lambda: resize_image())
        dialog.show()
        resize_image()
        dialog.exec()
