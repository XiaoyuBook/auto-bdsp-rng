"""A resumable guide entry and the first spotlight on the existing workspace."""
from __future__ import annotations

import math
from shiboken6 import isValid

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF, QRegion
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QMenu, QMessageBox, QPushButton, QScrollArea, QToolButton, QVBoxLayout, QWidget

from auto_bdsp_rng.app_settings import get_guide_progress, start_guide_progress


class GuideSpotlight(QWidget):
    paused = Signal()

    def __init__(self, window) -> None:
        super().__init__(window.centralWidget())
        self.main_window = window
        self.target_button = window.auto_rng_tab.target_button
        self.target_card = self.target_button.parentWidget()
        self.setObjectName("GuideSpotlight")
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.hole = QRectF()
        self.arrow_start = QPointF()
        self.arrow_end = QPointF()
        self.tip = QFrame(self)
        self.tip.setObjectName("GuideTip")
        self.tip.setFixedWidth(338)
        self.tip.setStyleSheet("""
            QFrame#GuideTip { background: white; border: 1px solid #DCE7E1; border-radius: 13px; }
            QFrame#GuideTip QLabel, QFrame#GuideTip QPushButton {
                font-family: 'Microsoft YaHei UI'; font-size: 13px; font-weight: 400; color: #52606D;
                background: transparent; border: 0;
            }
            QFrame#GuideTip QLabel#GuideStep { color: #087C58; font-size: 12px; }
            QFrame#GuideTip QLabel#GuideTitle { color: #202A33; font-size: 19px; font-weight: 500; }
            QFrame#GuideTip QPushButton { border: 1px solid #E0E5EB; border-radius: 6px; padding: 6px 12px; }
            QFrame#GuideTip QPushButton:disabled { color: #9AA7A0; background: #F7F9F8; }
            QFrame#GuideTip QPushButton#GuideClose { border: 0; padding: 0; font-size: 19px; }
            QFrame#GuideTip QPushButton#GuideClose:hover { background: #F0F8F4; color: #087C58; }
            QFrame#GuideTip QLabel#GuideNote { color: #78847D; font-size: 11px; }
        """)
        layout = QVBoxLayout(self.tip)
        layout.setContentsMargins(20, 16, 20, 18)
        layout.setSpacing(12)
        top = QHBoxLayout()
        step = QLabel("第 1 步 · 设置目标精灵")
        step.setObjectName("GuideStep")
        top.addWidget(step, 1)
        self.close_button = QPushButton("×")
        self.close_button.setObjectName("GuideClose")
        self.close_button.setFixedSize(28, 28)
        self.close_button.setAccessibleName("暂时收起引导")
        self.close_button.setToolTip("收起提示，保留进度")
        self.close_button.clicked.connect(self.paused)
        top.addWidget(self.close_button)
        layout.addLayout(top)
        title = QLabel("先选好这次的目标")
        title.setObjectName("GuideTitle")
        layout.addWidget(title)
        copy = QLabel("点击亮起区域里的「设置」，选择这次想乱的精灵，以及你希望得到的结果。")
        copy.setWordWrap(True)
        layout.addWidget(copy)
        controls = QHBoxLayout()
        # Later steps and their navigation rules are designed separately.
        for text in ("上一步", "下一步", "跳过"):
            if text == "跳过":
                controls.addStretch(1)
            button = QPushButton(text)
            button.setFixedSize(72 if text != "跳过" else 56, 34)
            button.setEnabled(False)
            controls.addWidget(button)
        layout.addLayout(controls)
        note = QLabel("可随时收起提示，稍后继续。")
        note.setObjectName("GuideNote")
        layout.addWidget(note)
        self.relayout = QTimer(self)
        self.relayout.setSingleShot(True)
        self.relayout.timeout.connect(self.reposition)
        ancestor = self.target_card
        while ancestor is not None:
            ancestor.installEventFilter(self)
            ancestor = ancestor.parentWidget()
        self.hide()

    def reveal(self) -> None:
        ancestor = self.target_card.parentWidget()
        while ancestor is not None:
            if isinstance(ancestor, QScrollArea):
                ancestor.ensureWidgetVisible(self.target_card)
            ancestor = ancestor.parentWidget()
        if not self.reposition():
            return
        self.show()
        self.raise_()
        self.target_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def eventFilter(self, obj, event):
        if self.isVisible() and event.type() in (QEvent.Type.Resize, QEvent.Type.Move, QEvent.Type.LayoutRequest, QEvent.Type.Show):
            self.relayout.start(0)
        return False

    def _target_rect(self, widget) -> QRectF:
        origin = self.mapFromGlobal(widget.mapToGlobal(QPoint()))
        rect = QRectF(QRect(origin, widget.size())).adjusted(-4, -4, 4, 4)
        ancestor = widget.parentWidget()
        while ancestor is not None:
            if isinstance(ancestor, QScrollArea):
                viewport = ancestor.viewport()
                viewport_origin = self.mapFromGlobal(viewport.mapToGlobal(QPoint()))
                rect = rect.intersected(QRectF(QRect(viewport_origin, viewport.size())))
            ancestor = ancestor.parentWidget()
        return rect

    def reposition(self) -> bool:
        self.setGeometry(self.parentWidget().rect())
        self.tip.setFixedHeight(self.tip.sizeHint().height())
        width, height = self.tip.width(), self.tip.height()
        bounds = self.rect().adjusted(12, 12, -12, -12)
        chosen = None
        # Use global-to-local mapping between sibling widgets, and clip the
        # opening to scroll viewports when the splitter reflows vertically.
        for target in (self.target_card, self.target_button):
            hole = self._target_rect(target).intersected(QRectF(self.rect()))
            if hole.isEmpty():
                continue
            candidates = (
                QRect(int(hole.right()) + 44, int(hole.top()), width, height),
                QRect(int(hole.left()) - width - 44, int(hole.top()), width, height),
                QRect(max(12, min(int(hole.center().x()) - width // 2, bounds.right() - width)), int(hole.bottom()) + 24, width, height),
                QRect(max(12, min(int(hole.center().x()) - width // 2, bounds.right() - width)), int(hole.top()) - height - 24, width, height),
            )
            chosen = next((r for r in candidates if bounds.contains(r)), None)
            if chosen is not None:
                self.hole = hole
                break
        if chosen is None:
            self.paused.emit()
            return False
        self.tip.setGeometry(chosen)
        target_center = self._target_rect(self.target_button).center()
        if chosen.left() > self.hole.right():
            y = target_center.y()
            self.arrow_start = QPointF(chosen.left(), y)
            self.arrow_end = QPointF(self.hole.right() + 7, y)
        elif chosen.right() < self.hole.left():
            y = target_center.y()
            self.arrow_start = QPointF(chosen.right(), y)
            self.arrow_end = QPointF(self.hole.left() - 7, y)
        else:
            x = max(chosen.left() + 20, min(target_center.x(), chosen.right() - 20))
            below = chosen.top() > self.hole.bottom()
            self.arrow_start = QPointF(x, chosen.top() if below else chosen.bottom())
            self.arrow_end = QPointF(target_center.x(), self.hole.bottom() + 7 if below else self.hole.top() - 7)
        opening = QPainterPath()
        opening.addRoundedRect(self.hole.adjusted(2, 2, -2, -2), 9, 9)
        self.setMask(QRegion(self.rect()).subtracted(QRegion(opening.toFillPolygon().toPolygon())))
        self.update()
        return True

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        shade = QPainterPath()
        shade.setFillRule(Qt.FillRule.OddEvenFill)
        shade.addRect(QRectF(self.rect()))
        shade.addRoundedRect(self.hole, 11, 11)
        painter.fillPath(shade, QColor(0, 0, 0, 168))
        painter.setPen(QPen(QColor("#83E8BF"), 2))
        painter.drawRoundedRect(self.hole, 11, 11)
        painter.setPen(QPen(QColor("#A2EBCD"), 2))
        painter.drawLine(self.arrow_start, self.arrow_end)
        delta = self.arrow_end - self.arrow_start
        length = max(1, math.hypot(delta.x(), delta.y()))
        unit = delta / length
        base = self.arrow_end - unit * 7
        cross = QPointF(-unit.y(), unit.x()) * 4
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#A2EBCD"))
        painter.drawPolygon(QPolygonF([self.arrow_end, base + cross, base - cross]))


class GuideController(QObject):
    def __init__(self, window, button: QToolButton) -> None:
        super().__init__(window)
        self.window = window
        self.button = button
        self.overlay: GuideSpotlight | None = None
        self._resume_after_dialog = False
        self.menu = QMenu(button)
        self.restart_action = self.menu.addAction("重新开始引导", self.restart)
        button.clicked.connect(self.begin_or_resume)
        window.auto_rng_tab.targetDialogOpened.connect(self._target_opened)
        window.auto_rng_tab.targetDialogClosed.connect(self._target_closed)
        self.refresh()

    def refresh(self) -> None:
        resumable = get_guide_progress() is not None
        self.button.setText("继续引导" if resumable else "开始引导")
        self.button.setMenu(self.menu if resumable else None)
        self.button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup if resumable else QToolButton.ToolButtonPopupMode.DelayedPopup)
        self.button.setProperty("resumable", resumable)
        self.button.setAccessibleName(self.button.text())
        self.button.setToolTip("接着上次的步骤继续；右侧箭头可重新开始" if resumable else "从设置目标精灵开始一次新的引导")
        self.button.style().unpolish(self.button)
        self.button.style().polish(self.button)
        self.button.update()

    def begin_or_resume(self) -> None:
        self._begin(restart=False)

    def restart(self) -> None:
        self._begin(restart=True)

    def _begin(self, *, restart: bool) -> None:
        if self.window._is_closing:
            return
        try:
            if restart or get_guide_progress() is None:
                start_guide_progress()
        except OSError:
            QMessageBox.warning(self.window, "无法开始引导", "无法保存引导进度，请检查设置目录是否可写后重试。")
            return
        self.refresh()
        self.window.tabs.setCurrentWidget(self.window.auto_rng_tab)
        if self.overlay is None:
            self.overlay = GuideSpotlight(self.window)
            self.overlay.paused.connect(self.pause)
        self.overlay.reveal()
        if self.overlay.isVisible():
            QApplication.instance().installEventFilter(self)

    def pause(self) -> None:
        QApplication.instance().removeEventFilter(self)
        self._resume_after_dialog = False
        if self.overlay is not None:
            self.overlay.hide()
            self.overlay.relayout.stop()
        self.button.setFocus(Qt.FocusReason.OtherFocusReason)

    def _target_opened(self) -> None:
        self._resume_after_dialog = self.overlay is not None and self.overlay.isVisible()
        if self._resume_after_dialog:
            QApplication.instance().removeEventFilter(self)
            self.overlay.hide()

    def _target_closed(self) -> None:
        if self._resume_after_dialog:
            self._resume_after_dialog = False
            QTimer.singleShot(0, self.begin_or_resume)

    def eventFilter(self, obj, event):
        if self.overlay is None or not isValid(self.overlay) or not self.overlay.isVisible():
            return False
        if event.type() == QEvent.Type.Shortcut and self.window.isActiveWindow():
            return True
        if isinstance(obj, QWidget) and obj.window() is self.window:
            if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
                self.pause()
                return True
            if event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
                target = self.overlay.close_button if obj is self.overlay.target_button else self.overlay.target_button
                target.setFocus(Qt.FocusReason.TabFocusReason)
                return True
            if event.type() in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease, QEvent.Type.ShortcutOverride, QEvent.Type.Wheel):
                if obj is self.overlay.target_button or obj is self.overlay.tip or self.overlay.tip.isAncestorOf(obj):
                    return False
                return True
        return False
