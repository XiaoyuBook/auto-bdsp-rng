from __future__ import annotations

import re

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QWheelEvent
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QDoubleSpinBox,
    QSpinBox,
    QStyle,
    QStyleOptionSpinBox,
    QWidget,
)


_SPIN_SUBCONTROL_PATTERN = re.compile(
    r"Q(?:Double)?SpinBox[^,{]*::(?:up-arrow|down-arrow)"
)


class _ChevronSpinMixin:
    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        # Scroll the surrounding page, even when this input has focus.
        event.ignore()

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if (
            self.buttonSymbols() == QAbstractSpinBox.ButtonSymbols.NoButtons
            or not self._stylesheet_owns_arrows()
        ):
            return

        option = QStyleOptionSpinBox()
        self.initStyleOption(option)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor("#5F6C66" if self.isEnabled() else "#A5AEA9")
        pen = QPen(color, 1.15)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        for sub_control, points in (
            (
                QStyle.SubControl.SC_SpinBoxUp,
                ((-3.0, 1.0), (0.0, -2.0), (3.0, 1.0)),
            ),
            (
                QStyle.SubControl.SC_SpinBoxDown,
                ((-3.0, -1.0), (0.0, 2.0), (3.0, -1.0)),
            ),
        ):
            rect = self.style().subControlRect(
                QStyle.ComplexControl.CC_SpinBox,
                option,
                sub_control,
                self,
            )
            if not rect.isValid():
                continue
            center = rect.center()
            first, middle, last = points
            painter.drawLine(
                QPointF(center.x() + first[0], center.y() + first[1]),
                QPointF(center.x() + middle[0], center.y() + middle[1]),
            )
            painter.drawLine(
                QPointF(center.x() + middle[0], center.y() + middle[1]),
                QPointF(center.x() + last[0], center.y() + last[1]),
            )
        painter.end()

    def _stylesheet_owns_arrows(self) -> bool:
        widget: QWidget | None = self
        while widget is not None:
            if _SPIN_SUBCONTROL_PATTERN.search(widget.styleSheet()):
                return True
            widget = widget.parentWidget()
        app = QApplication.instance()
        return bool(app and _SPIN_SUBCONTROL_PATTERN.search(app.styleSheet()))


class ChevronSpinBox(_ChevronSpinMixin, QSpinBox):
    pass


class ChevronDoubleSpinBox(_ChevronSpinMixin, QDoubleSpinBox):
    pass


__all__ = ["ChevronDoubleSpinBox", "ChevronSpinBox"]
