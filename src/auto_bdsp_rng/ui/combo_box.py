from __future__ import annotations

import re

from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QComboBox, QWidget


_COMBO_SUBCONTROL_PATTERN = re.compile(
    r"QComboBox[^,{]*::(?:drop-down|down-arrow)"
)


class ChevronComboBox(QComboBox):
    """QComboBox with a stable chevron when a stylesheet owns the drop-down."""

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if not self._stylesheet_owns_arrow():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = "#24312D" if self.isEnabled() else "#A5AEA9"
        painter.setPen(QPen(QColor(color), 1.5))
        center_x = self.width() - 17
        center_y = self.height() // 2
        painter.drawLine(center_x - 4, center_y - 2, center_x, center_y + 2)
        painter.drawLine(center_x, center_y + 2, center_x + 4, center_y - 2)
        painter.end()

    def _stylesheet_owns_arrow(self) -> bool:
        widget: QWidget | None = self
        while widget is not None:
            if _COMBO_SUBCONTROL_PATTERN.search(widget.styleSheet()):
                return True
            widget = widget.parentWidget()
        app = QApplication.instance()
        return bool(app and _COMBO_SUBCONTROL_PATTERN.search(app.styleSheet()))


__all__ = ["ChevronComboBox"]
