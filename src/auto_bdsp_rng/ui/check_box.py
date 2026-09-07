from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QCheckBox, QStyle, QStyleOptionButton


class CheckmarkCheckBox(QCheckBox):
    """Draw a stable green checked indicator across Qt platform styles."""

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if self.checkState() == Qt.CheckState.Unchecked:
            return

        option = QStyleOptionButton()
        self.initStyleOption(option)
        indicator = self.style().subElementRect(
            QStyle.SubElement.SE_CheckBoxIndicator,
            option,
            self,
        )
        if not indicator.isValid():
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        fill = QColor("#087C58" if self.isEnabled() else "#94B8AA")
        painter.setPen(QPen(fill, 1))
        painter.setBrush(fill)
        painter.drawRoundedRect(indicator.adjusted(0, 0, -1, -1), 2, 2)

        left = float(indicator.left())
        top = float(indicator.top())
        width = max(1.0, float(indicator.width() - 1))
        height = max(1.0, float(indicator.height() - 1))
        painter.setPen(
            QPen(
                QColor("#FFFFFF"),
                1.6,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        if self.checkState() == Qt.CheckState.PartiallyChecked:
            y = top + height * 0.5
            painter.drawLine(
                QPointF(left + width * 0.27, y),
                QPointF(left + width * 0.73, y),
            )
        else:
            path = QPainterPath(QPointF(left + width * 0.23, top + height * 0.52))
            path.lineTo(left + width * 0.43, top + height * 0.72)
            path.lineTo(left + width * 0.78, top + height * 0.30)
            painter.drawPath(path)
        painter.end()


__all__ = ["CheckmarkCheckBox"]
