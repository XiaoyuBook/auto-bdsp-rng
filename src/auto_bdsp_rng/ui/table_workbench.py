"""Table presentation tools independent of domain result ordering and exports."""
from __future__ import annotations

import json
import re
from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QMenu, QPushButton, QTableView, QTableWidgetItem, QToolButton

IDENTITY_ROLE = Qt.ItemDataRole.UserRole + 42
TABLE_TOOL_STYLE = "QToolButton { border: 1px solid #E0E5EB; border-radius: 7px; padding: 4px 10px; background: white; color: #52606D; } QToolButton:hover { background: #F2F4F7; }"


class ResultItem(QTableWidgetItem):
    def __init__(self, text, *, sort_value=None):
        super().__init__(str(text))
        clean = str(text).replace(",", "")
        self.sort_value = sort_value if sort_value is not None else (int(clean) if re.fullmatch(r"-?\d+", clean) else str(text))

    def __lt__(self, other):
        a, b = self.sort_value, getattr(other, "sort_value", other.text())
        return (0, a) < (0, b) if isinstance(a, (float, int)) and isinstance(b, (float, int)) else (str(a).casefold() < str(b).casefold())


class TableWorkbench(QObject):
    def __init__(self, table, toolbar, settings, key, *, pinned_columns=1, column_settings=True):
        super().__init__(table)
        self.table, self.settings, self.key = table, settings, "table_workbench/" + key
        self.pinned_columns = pinned_columns
        self.column_settings = column_settings
        self.pin_enabled = False
        self._updating = False
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self.refresh_frozen)
        self.copy_button = QPushButton("复制选中行")
        self.copy_button.setToolTip("复制所选单元格所在的完整行（含隐藏列），按当前表格顺序。Ctrl / Shift 可多选。")
        self.copy_button.clicked.connect(self.copy_selected)
        self.copy_button.setEnabled(False)
        self.copy_button.setFixedHeight(32)
        self.tools_button = QToolButton(table)
        self.tools_button.setText("表格设置")
        self.tools_button.setFixedHeight(32)
        self.tools_button.setStyleSheet(TABLE_TOOL_STYLE)
        self.tools_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.menu = QMenu(self.tools_button)
        self.tools_button.setMenu(self.menu)
        self.menu.aboutToShow.connect(self._build_menu)
        toolbar.addWidget(self.copy_button)
        if column_settings:
            toolbar.addWidget(self.tools_button)
        else:
            self.tools_button.hide()
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        table.itemSelectionChanged.connect(lambda: self.copy_button.setEnabled(bool(table.selectedIndexes())))
        table.horizontalHeader().setSectionsClickable(True)
        table.horizontalHeader().sectionClicked.connect(self._sort_clicked)
        table.horizontalHeader().sortIndicatorChanged.connect(self._save_sort)
        self.frozen = QTableView(table)
        self.frozen.setObjectName(table.objectName())
        self.frozen.setModel(table.model())
        self.frozen.setSelectionModel(table.selectionModel())
        self.frozen.setSelectionMode(table.selectionMode())
        self.frozen.setSelectionBehavior(table.selectionBehavior())
        self.frozen.setItemDelegate(table.itemDelegate())
        self.frozen.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.frozen.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.frozen.setShowGrid(table.showGrid())
        self.frozen.setAlternatingRowColors(table.alternatingRowColors())
        self.frozen.verticalHeader().hide()
        self.frozen.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.frozen.horizontalHeader().sectionClicked.connect(self._sort_frozen)
        self.frozen.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.frozen.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.verticalScrollBar().valueChanged.connect(self.frozen.verticalScrollBar().setValue)
        self.frozen.verticalScrollBar().valueChanged.connect(table.verticalScrollBar().setValue)
        table.horizontalHeader().sectionResized.connect(self.refresh_frozen)
        table.verticalHeader().sectionResized.connect(self.refresh_frozen)
        table.model().rowsInserted.connect(self.schedule_refresh)
        table.model().rowsRemoved.connect(self.schedule_refresh)
        table.model().modelReset.connect(self.schedule_refresh)
        table.installEventFilter(self)
        self.frozen.hide()
        self.restore()

    def schedule_refresh(self, *_args):
        self._refresh_timer.start(0)

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.schedule_refresh()
        return super().eventFilter(obj, event)

    def _sort_clicked(self, column):
        header = self.table.horizontalHeader()
        order = header.sortIndicatorOrder() if header.sortIndicatorSection() == column else Qt.SortOrder.AscendingOrder
        if not self.table.isSortingEnabled():
            header.setSortIndicator(column, Qt.SortOrder.AscendingOrder)
            self.table.setSortingEnabled(True)
        else:
            self.table.sortItems(column, order)
        self._save_sort(column, self.table.horizontalHeader().sortIndicatorOrder())

    def _sort_frozen(self, column):
        header = self.table.horizontalHeader()
        order = Qt.SortOrder.DescendingOrder if self.table.isSortingEnabled() and header.sortIndicatorSection() == column and header.sortIndicatorOrder() == Qt.SortOrder.AscendingOrder else Qt.SortOrder.AscendingOrder
        header.setSortIndicator(column, order)
        self.table.setSortingEnabled(True)
        self.table.sortItems(column, order)
        self._save_sort(column, order)

    def _save_sort(self, column, order):
        if self.table.isSortingEnabled():
            self.settings.setValue(self.key + "/sort", json.dumps([column, order.value]))

    def restore(self):
        try:
            hidden = json.loads(str(self.settings.value(self.key + "/hidden", "[]")))
            if self.column_settings and isinstance(hidden, list):
                for col in hidden:
                    if type(col) is int and self.pinned_columns <= col < self.table.columnCount():
                        self.table.setColumnHidden(col, True)
            sort = json.loads(str(self.settings.value(self.key + "/sort", "null")))
            if isinstance(sort, list) and len(sort) == 2 and type(sort[0]) is int and 0 <= sort[0] < self.table.columnCount() and sort[1] in (0, 1):
                self.table.horizontalHeader().setSortIndicator(sort[0], Qt.SortOrder(sort[1]))
                self.table.setSortingEnabled(True)
        except (ValueError, TypeError):
            pass
        self.pin_enabled = self.column_settings and str(self.settings.value(self.key + "/pin", "false")).lower() == "true"
        if not self.column_settings:
            for col in range(self.table.columnCount()):
                self.table.setColumnHidden(col, False)
        self.schedule_refresh()

    def _build_menu(self):
        self.menu.clear()
        hint = self.menu.addAction("点击表头排序 · Ctrl / Shift 多选")
        hint.setEnabled(False)
        pin = self.menu.addAction("固定左侧关键列")
        pin.setCheckable(True)
        pin.setChecked(self.pin_enabled)
        pin.toggled.connect(self.set_pinned)
        columns = self.menu.addMenu("显示列")
        for col in range(self.table.columnCount()):
            header = self.table.horizontalHeaderItem(col)
            action = columns.addAction(header.text() if header else str(col + 1))
            action.setCheckable(True)
            action.setChecked(not self.table.isColumnHidden(col))
            action.setEnabled(col >= self.pinned_columns)
            action.toggled.connect(lambda checked, c=col: self.set_column_visible(c, checked))
        self.menu.addAction("显示全部列", self.show_all_columns)

    def show_all_columns(self):
        for col in range(self.table.columnCount()):
            self.table.setColumnHidden(col, False)
        self.settings.setValue(self.key + "/hidden", "[]")
        self.refresh_frozen()

    def set_column_visible(self, column, visible):
        if column < self.pinned_columns:
            return
        self.table.setColumnHidden(column, not visible)
        hidden = [c for c in range(self.table.columnCount()) if self.table.isColumnHidden(c)]
        self.settings.setValue(self.key + "/hidden", json.dumps(hidden))
        self.refresh_frozen()

    def set_pinned(self, checked):
        self.pin_enabled = checked
        self.settings.setValue(self.key + "/pin", checked)
        self.refresh_frozen()

    def refresh_frozen(self, *_args):
        if self._updating:
            return
        self._updating = True
        try:
            table = self.table
            self.frozen.setVisible(self.pin_enabled and table.rowCount() > 0)
            if not self.pin_enabled:
                return
            for col in range(table.columnCount()):
                self.frozen.setColumnHidden(col, col >= self.pinned_columns)
                if col < self.pinned_columns:
                    self.frozen.setColumnWidth(col, table.columnWidth(col))
            self.frozen.verticalHeader().setDefaultSectionSize(table.verticalHeader().defaultSectionSize())
            self.frozen.horizontalHeader().setFixedHeight(table.horizontalHeader().height())
            width = sum(table.columnWidth(c) for c in range(self.pinned_columns))
            self.frozen.setGeometry(table.frameWidth(), table.frameWidth(), width,
                                    table.viewport().height() + table.horizontalHeader().height())
            self.frozen.raise_()
        finally:
            self._updating = False

    def selected_text(self):
        rows = sorted({index.row() for index in self.table.selectedIndexes() if not self.table.isRowHidden(index.row())})
        if not rows:
            return ""
        headers = [self.table.horizontalHeaderItem(c).text() for c in range(self.table.columnCount())]
        lines = ["\t".join(headers)]
        for row in rows:
            lines.append("\t".join(self.table.item(row, c).text() if self.table.item(row, c) else "" for c in range(self.table.columnCount())))
        return "\n".join(lines)

    def copy_selected(self):
        text = self.selected_text()
        if text:
            QGuiApplication.clipboard().setText(text)
