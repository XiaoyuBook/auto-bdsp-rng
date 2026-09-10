"""Switch IV/stat presentation without replacing result rows or their selection."""
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTableWidgetItem


@dataclass
class StatDisplayMode:
    show_stats: bool = False


class StatResultItem(QTableWidgetItem):
    def __init__(self, iv: int, stat: int, mode: StatDisplayMode) -> None:
        super().__init__()
        self._values = (iv, stat)
        self._texts = (str(iv), str(stat))
        self._mode = mode

    @property
    def sort_value(self) -> int:
        return self._values[self._mode.show_stats]

    def data(self, role: int):
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return self._texts[self._mode.show_stats]
        return super().data(role)

    def __lt__(self, other):
        if isinstance(other, StatResultItem):
            return self._values[self._mode.show_stats] < other._values[other._mode.show_stats]
        return super().__lt__(other)
