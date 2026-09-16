"""Keep the existing live preview visible while a script is being checked."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QLabel
from shiboken6 import isValid


class ScriptGuidePreview:
    def __init__(self, controller):
        self.c = controller
        self.window = controller.window
        self.preview = None
        self.original = None
        self.label = None

    @staticmethod
    def global_rect(widget):
        return QRect(widget.mapToGlobal(QPoint()), widget.size())

    def protected_rects(self):
        e = self.window.easycon_tab
        widgets = (self.c.overlay.tip, e.execution.bar, e.run_button, e.pause_button, e.stop_button)
        if self.window.tabs.currentWidget() is self.window.project_xs_tab or self.c.overlay.spec.script_editing:
            widgets += self.c.overlay.spec.highlights
        if e._controller_overlay is not None:
            widgets += (e._controller_overlay,)
        return [self.global_rect(w).adjusted(-8, -8, 8, 8) for w in widgets if w.isVisible()]

    def show(self, kind):
        first = self.preview is None
        if first:
            self.window.show_picture_in_picture()
            self.preview = self.window._picture_in_picture
            p = self.preview
            self.original = (p.windowTitle(), p.frame_label.styleSheet(), p.geometry(),
                             p.always_on_top(), p.frame_label.text())
            self.label = QLabel(p)
            self.label.setWordWrap(True)
            self.label.setStyleSheet("color: #087C58; background: #EAF8F1; border: 1px solid #83E8BF; "
                                    "border-radius: 6px; padding: 7px; font-size: 13px;")
            p.layout().insertWidget(0, self.label)
            p.frame_label.setStyleSheet("QLabel#Preview { border: 3px solid #35C58D; border-radius: 6px; background: #111917; color: #D7EEE3; }")
            p.setWindowTitle("独立预览 · 观察脚本结果")
            p.set_always_on_top(True)
            if p._raw_frame is None:
                p.frame_label.setText("暂无视频画面\n连接视频源后，在这里观察游戏结果")
        p = self.preview
        labels = {"seed": "核对测种地点", "advance": "核对是否回到测种地点",
                  "hit": "核对是否正常遇到目标", "reverse": "核对捕捉结果与笔记页"}
        self.label.setText("观察游戏画面 · " + labels[kind])
        self.label.show()
        restored = not p.isVisible() or p.isMinimized()
        if restored:
            p.showNormal()
        p.layout().activate()
        protected = self.protected_rects()
        available = self.window.screen().availableGeometry()
        if first or restored or not available.contains(p.frameGeometry()) or any(p.frameGeometry().intersects(r) for r in protected):
            self._place(available, protected)
        if first or restored:
            p.raise_()

    def _place(self, available, protected):
        p = self.preview
        main = self.window.frameGeometry()
        bounds = available.adjusted(8, 34, -8, -8)
        # Prefer space beside the main window. On smaller screens use its lower
        # right area, keeping the guide, execution status and run controls clear.
        candidates = []
        for width, height in ((480, 322), (380, 270), (320, 258)):
            width = max(width, p.minimumSizeHint().width())
            height = max(height, p.minimumSizeHint().height())
            positions = ((main.right() + 14, main.top() + 32), (main.left() - width - 14, main.top() + 32),
                         (main.right() - width - 12, main.bottom() - height - 12),
                         (bounds.right() - width, bounds.bottom() - height),
                         (bounds.left(), bounds.bottom() - height), (bounds.right() - width, bounds.top()))
            for x, y in positions:
                rect = QRect(x, y, width, height)
                if not bounds.contains(rect):
                    continue
                frame = rect.adjusted(-8, -30, 8, 8)
                blocked = sum(self._area(frame.intersected(r)) for r in protected)
                covered = self._area(frame.intersected(main))
                candidates.append(((blocked, bool(covered), 480 - width, covered), rect))
        if candidates:
            p.setGeometry(min(candidates, key=lambda item: item[0])[1])

    @staticmethod
    def _area(rect):
        return max(0, rect.width()) * max(0, rect.height())

    def restore(self):
        p = self.preview
        if p is None or not isValid(p):
            return
        title, style, geometry, top, text = self.original
        p.layout().removeWidget(self.label)
        self.label.hide()
        self.label.deleteLater()
        p.setWindowTitle(title)
        p.frame_label.setStyleSheet(style)
        p.set_always_on_top(top)
        p.setGeometry(geometry)
        if p._raw_frame is None:
            p.frame_label.setText(text)
        self.preview = self.original = self.label = None
