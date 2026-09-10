"""Light row separators that preserve native item state and model backgrounds."""

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QStyledItemDelegate

from auto_bdsp_rng.ui.workspace_theme import SEPARATOR


class RowSeparatorDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index) -> None:
        super().paint(painter, option, index)
        painter.save()
        painter.setPen(QColor(SEPARATOR))
        painter.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())
        painter.restore()
