from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QModelIndex, QPoint, QSortFilterProxyModel, Qt
from PySide6.QtWidgets import QAbstractItemView, QFrame, QLabel, QLineEdit, QListView, QVBoxLayout, QWidget

from auto_bdsp_rng.ui.combo_box import ChevronComboBox
from auto_bdsp_rng.ui.workspace_theme import ui_font, workspace_styles


class _ScriptSearchPopup(QFrame):
    """Filter a separate view so searching never changes the chosen script."""

    def __init__(self, combo: ScriptComboBox) -> None:
        super().__init__(combo, Qt.WindowType.Popup)
        self.combo = combo
        self.setObjectName("ScriptSearchPopup")
        self.setFont(ui_font(13))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        self.search = QLineEdit()
        self.search.setObjectName("ScriptSearchInput")
        self.search.setPlaceholderText("搜索脚本名称…")
        self.search.setAccessibleName("搜索脚本名称")
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.results = QListView()
        self.results.setObjectName("ScriptSearchResults")
        self.results.setAccessibleName("脚本搜索结果")
        self.results.setModel(self.proxy)
        self.results.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.results.setUniformItemSizes(True)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        layout.addWidget(self.results)
        self.empty_label = QLabel("没有匹配的脚本")
        self.empty_label.setObjectName("ScriptSearchEmpty")
        layout.addWidget(self.empty_label)
        self.search.textChanged.connect(self._filter)
        self.results.clicked.connect(self._choose)
        self.search.installEventFilter(self)
        self.results.installEventFilter(self)
        self.setStyleSheet(workspace_styles("""
            QFrame#ScriptSearchPopup {
                background: $surface; border: 1px solid $border; border-radius: 8px;
            }
            QLineEdit#ScriptSearchInput {
                background: $surface_muted; color: $text; border: 1px solid $border;
                border-radius: 6px; padding: 6px 10px; min-height: 0;
                font-size: 13px;
            }
            QLineEdit#ScriptSearchInput:focus { border-color: $accent; }
            QListView#ScriptSearchResults {
                background: $surface; color: $text; border: 0; padding: 0;
                outline: 0; font-size: 13px;
            }
            QListView#ScriptSearchResults::item { padding: 7px 10px; border-radius: 4px; }
            QListView#ScriptSearchResults::item:hover { background: $surface_muted; }
            QListView#ScriptSearchResults::item:selected { background: $accent_soft; color: $accent; }
            QLabel#ScriptSearchEmpty { color: $text_secondary; padding: 12px 8px; font-size: 13px; }
        """))

    def open(self) -> None:
        self.proxy.setSourceModel(self.combo.model())
        self.search.clear()
        self._filter("")
        self.show()
        self.search.setFocus(Qt.FocusReason.PopupFocusReason)

    def _filter(self, text: str) -> None:
        self.proxy.setFilterFixedString(text.strip())
        count = self.proxy.rowCount()
        self.results.setVisible(count > 0)
        self.empty_label.setVisible(count == 0)
        current = self.proxy.mapFromSource(self.combo.model().index(self.combo.currentIndex(), 0))
        if not current.isValid() and count:
            current = self.proxy.index(0, 0)
        self.results.setCurrentIndex(current)
        if current.isValid():
            self.results.scrollTo(current)
        row_height = max(32, self.results.sizeHintForRow(0))
        self.results.setFixedHeight(min(8, count) * row_height)
        self._position()

    def _position(self) -> None:
        available = self.combo.screen().availableGeometry()
        self.setFixedWidth(min(max(280, self.combo.width()), available.width()))
        self.layout().activate()
        # adjustSize caps top-level height and can squeeze the search field at high DPI.
        self.resize(self.width(), self.sizeHint().height())
        bottom = self.combo.mapToGlobal(QPoint(0, self.combo.height()))
        top = self.combo.mapToGlobal(QPoint())
        y = bottom.y()
        if y + self.height() > available.bottom() + 1:
            y = top.y() - self.height()
        x = min(max(bottom.x(), available.left()), available.right() + 1 - self.width())
        y = min(max(y, available.top()), max(available.top(), available.bottom() + 1 - self.height()))
        self.move(x, y)

    def _choose(self, index: QModelIndex) -> None:
        source = self.proxy.mapToSource(index)
        if not source.isValid() or not self.combo.isEnabled():
            return
        flags = source.flags()
        if not flags & Qt.ItemFlag.ItemIsEnabled or not flags & Qt.ItemFlag.ItemIsSelectable:
            return
        row = source.row()
        self.combo.hidePopup()
        self.combo.setCurrentIndex(row)
        self.combo.activated.emit(row)
        self.combo.textActivated.emit(self.combo.currentText())

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Escape:
                self.combo.hidePopup()
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._choose(self.results.currentIndex())
                return True
            if watched is self.search and key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                count = self.proxy.rowCount()
                if count:
                    row = self.results.currentIndex().row() + (1 if key == Qt.Key.Key_Down else -1)
                    current = self.proxy.index(max(0, min(row, count - 1)), 0)
                    self.results.setCurrentIndex(current)
                    self.results.scrollTo(current)
                return True
        return super().eventFilter(watched, event)


class ScriptComboBox(ChevronComboBox):
    """A script selector with a searchable popup and normal combo data/signals."""

    def __init__(self, before_popup: Callable[[], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._before_popup = before_popup
        self.search_popup: _ScriptSearchPopup | None = None

    def showPopup(self) -> None:  # noqa: N802
        self._before_popup()
        if not self.isEnabled():
            return
        if self.search_popup is None:
            self.search_popup = _ScriptSearchPopup(self)
        self.search_popup.open()

    def hidePopup(self) -> None:  # noqa: N802
        if self.search_popup is not None:
            self.search_popup.hide()
        super().hidePopup()

    def hideEvent(self, event) -> None:  # noqa: N802
        self.hidePopup()
        super().hideEvent(event)
