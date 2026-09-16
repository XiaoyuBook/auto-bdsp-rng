"""Focus real OCR controls and keep the existing independent preview usable."""
from PySide6.QtCore import QEvent, QPoint, QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QRegion
from PySide6.QtWidgets import QPushButton, QWidget

from auto_bdsp_rng.ui.script_guide_preview import ScriptGuidePreview


class OcrFocusShade(QWidget):
    def __init__(self, dialog, tip):
        super().__init__(dialog)
        self.dialog, self.tip = dialog, tip
        self.targets = ()
        self.extra_rects = lambda: ()
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        dialog.installEventFilter(self)

    def focus(self, *targets, extra_rects=None):
        self.targets = targets
        self.extra_rects = extra_rects or (lambda: ())
        self.reposition()
        self.show()
        self.raise_()
        self.tip.raise_()

    def reposition(self):
        self.setGeometry(self.dialog.rect())
        self.holes = [QRect(w.mapTo(self.dialog, QPoint()), w.size()).adjusted(-3, -3, 3, 3)
                      for w in self.targets if w.isVisible()]
        self.holes.extend(self.extra_rects())
        self.holes.append(QRect(self.tip.mapTo(self.dialog, QPoint()), self.tip.size()))
        mask = QRegion(self.rect())
        for rect in self.holes:
            mask -= QRegion(rect)
        self.setMask(mask)
        self.update()

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.LayoutRequest):
            self.reposition()
        return False

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 140))
        painter.setPen(QPen(QColor('#35C58D'), 2))
        for rect in self.holes[:-1]:
            painter.drawRoundedRect(rect.adjusted(-1, -1, 1, 1), 5, 5)
        painter.end()


class OcrGuidePreview(ScriptGuidePreview):
    def __init__(self, guide):
        super().__init__(guide.c)
        self.guide = guide
        self.cancel = None
        self.overlay_enabled = None
        self.controller_geometry = None

    def protected_rects(self):
        dialog = self.guide.dialog
        widgets = [dialog] if dialog is not None and dialog.isVisible() else []
        e = self.window.easycon_tab
        if e._controller_overlay is not None and e._controller_overlay.isVisible():
            widgets.append(e._controller_overlay)
        return [self.global_rect(w).adjusted(-8, -8, 8, 8) for w in widgets]

    def show_message(self, text, *, selecting=False):
        first = self.preview is None
        super().show('reverse')
        p = self.preview
        if first:
            self.overlay_enabled = p.overlay_enabled()
            p.set_overlay_enabled(True)
            self.cancel = QPushButton('取消本次框选', p)
            self.cancel.clicked.connect(self.window._cancel_preview_selection)
            p.layout().addWidget(self.cancel)
            p.installEventFilter(self.guide)
        self.label.setText(text)
        p.setWindowTitle('独立预览 · OCR 框选与核对')
        self.cancel.setVisible(selecting)
        if selecting:
            # Enlarge the actual preview; source coordinates still come from
            # RoiPreviewLabel's letterboxed image rectangle.
            area = p.screen().availableGeometry().adjusted(12, 36, -12, -12)
            p.resize(min(800, area.width()), min(550, area.height()))
            p.move(area.center() - p.rect().center())
        p.showNormal()
        p.layout().activate()
        p._refresh_frame()
        controller = self.window.easycon_tab._controller_overlay
        if controller is not None and controller.isVisible():
            if self.controller_geometry is None:
                self.controller_geometry = controller.geometry()
            area = p.screen().availableGeometry().adjusted(8, 8, -8, -8)
            x = min(area.right() - controller.width(), p.x() + (p.width() - controller.width()) // 2)
            y = min(area.bottom() - controller.height(), p.frameGeometry().bottom() + 12)
            if selecting and QRect(x, y, controller.width(), controller.height()).intersects(p.frameGeometry()):
                x = area.right() - controller.width()
            controller.move(max(area.left(), x), max(area.top(), y))
        p.raise_()

    def _place(self, available, protected):
        dialog = self.guide.dialog
        if dialog is not None and dialog.isVisible():
            frame = dialog.frameGeometry()
            width = min(560, available.right() - frame.right() - 30)
            if width >= 320:
                self.preview.setGeometry(frame.right() + 16, available.top() + 36, width, round(width * 9 / 16) + 100)
                return
        super()._place(available, protected)

    def restore(self):
        controller = self.window.easycon_tab._controller_overlay
        if self.controller_geometry is not None and controller is not None:
            controller.setGeometry(self.controller_geometry)
        self.controller_geometry = None
        if self.preview is not None:
            self.preview.removeEventFilter(self.guide)
            self.preview.set_overlay_enabled(self.overlay_enabled)
            if self.cancel is not None:
                self.preview.layout().removeWidget(self.cancel)
                self.cancel.deleteLater()
                self.cancel = None
        super().restore()
