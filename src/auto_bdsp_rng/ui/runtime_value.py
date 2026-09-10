"""Selectable runtime values with a smaller unit and an unchanged plain-text API."""

from html import escape

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel

from auto_bdsp_rng.ui.workspace_theme import ui_font_weight


class RuntimeValueLabel(QLabel):
    """Keep callers' numeric text intact while Qt lays out the unit separately."""

    def __init__(self, text="—", parent=None):
        super().__init__(parent)
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.setText(text)

    def setText(self, text):  # noqa: N802
        self._plain_text = str(text)
        value = escape(self._plain_text)
        if self._plain_text.endswith(" 帧"):
            weight = int(ui_font_weight(QFont.Weight.Normal))
            value = escape(self._plain_text[:-2]) + f' <span style="font-size:14px;font-weight:{weight}">帧</span>'
        super().setText(value)
        self.setAccessibleName(self._plain_text)

    def text(self):
        return self._plain_text
