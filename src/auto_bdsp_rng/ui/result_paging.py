"""Bound Qt item allocation while retaining complete domain result sets."""
from __future__ import annotations

import heapq
import math
import re

from PySide6.QtCore import QObject, QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QHBoxLayout, QLabel, QPushButton, QTableWidget, QWidget

from auto_bdsp_rng.ui.background_search import BackgroundSearch

SOURCE_ROLE = Qt.ItemDataRole.UserRole + 80
RESULT_PAGE_SIZE = 200
BACKGROUND_RESULT_THRESHOLD = 10_000


def _ordered_indices(indices, sort_key, column, reverse, cancellation):
    """Bound C-level sort time as well as the Python key calculation time."""
    chunks = []
    for start in range(0, len(indices), 2048):
        cancellation.check()
        chunk = [(sort_key(source, column), source) for source in indices[start:start + 2048]]
        chunk.sort(key=lambda entry: entry[0], reverse=reverse)
        chunks.append(chunk)
    ordered = []
    for position, entry in enumerate(heapq.merge(*chunks, key=lambda entry: entry[0], reverse=reverse)):
        if position % 1024 == 0:
            cancellation.check()
        ordered.append(entry[1])
    return ordered


def numeric_sort_key(value):
    if isinstance(value, (int, float)):
        return (0, value)
    clean = str(value).replace(",", "")
    if re.fullmatch(r"-?\d+", clean):
        return (0, int(clean))
    return (1, str(value).casefold())


class PagedResultTable(QTableWidget):
    """Ordinary item table for small results, globally sorted pages for large ones."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.pager = ResultPager(self)
        self._virtual_sorting = False

    def isSortingEnabled(self):
        return self._virtual_sorting if self.pager.is_paged else super().isSortingEnabled()

    def setSortingEnabled(self, enabled):
        if not self.pager.is_paged:
            super().setSortingEnabled(enabled)
            return
        self._virtual_sorting = enabled
        if super().isSortingEnabled():
            super().setSortingEnabled(False)
        header = self.horizontalHeader()
        if header.isSortIndicatorShown() != enabled:
            header.setSortIndicatorShown(enabled)
        if enabled and not self.pager.populating:
            self.sortItems(self.horizontalHeader().sortIndicatorSection(), self.horizontalHeader().sortIndicatorOrder())

    def sortItems(self, column, order=Qt.SortOrder.AscendingOrder):
        if self.pager.is_paged:
            self.pager.sort(column, order)
        else:
            super().sortItems(column, order)


class ResultPager(QObject):
    pageChanged = Signal()

    def __init__(self, table: PagedResultTable):
        super().__init__(table)
        self.table = table
        self.page_size = RESULT_PAGE_SIZE
        self.total = 0
        self.page = 0
        self.indices = []
        self.is_paged = False
        self.populating = False
        self._item_factory = self._sort_key = self._text = self._identity = None
        self._snapshot = None
        self._filter_key = None
        self._predicate = None
        self._view_pending = False
        self._filter_pending = False
        self._prefix_pending = False
        self._pending_location = None
        self._search = BackgroundSearch(self)
        table.destroyed.connect(self._search.close)
        table.currentCellChanged.connect(self._selection_changed)
        table.model().dataChanged.connect(self._display_changed)
        self.controls = QWidget(table.parentWidget())
        layout = QHBoxLayout(self.controls)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addStretch(1)
        self.previous_button = QPushButton("上一页")
        self.page_label = QLabel()
        self.next_button = QPushButton("下一页")
        for button in (self.previous_button, self.next_button):
            button.setFixedHeight(28)
        layout.addWidget(self.previous_button)
        layout.addWidget(self.page_label)
        layout.addWidget(self.next_button)
        self.previous_button.clicked.connect(lambda: self.set_page(self.page - 1))
        self.next_button.clicked.connect(lambda: self.set_page(self.page + 1))
        self.controls.hide()

    @property
    def page_count(self):
        return max(1, math.ceil(len(self.indices) / self.page_size))

    @property
    def page_indices(self):
        start = self.page * self.page_size
        return self.indices[start:start + self.page_size]

    def set_source(self, count, item_factory, sort_key, text, identity, *, snapshot=None):
        self._search.cancel()
        self._view_pending = False
        self._filter_pending = False
        self._prefix_pending = False
        self._pending_location = None
        sorting = self.table.isSortingEnabled()
        QTableWidget.setSortingEnabled(self.table, False)
        self.total = count
        self.indices = list(range(count))
        self.page = 0
        self._filter_key = None
        self._predicate = None
        self._item_factory, self._sort_key, self._text, self._identity = item_factory, sort_key, text, identity
        # Factories run on the GUI thread and capture the current display mode,
        # locale and domain data. Their returned callables must never access Qt.
        self._snapshot = snapshot
        self.is_paged = count > self.page_size
        self.table._virtual_sorting = sorting
        if self.is_paged and sorting and count <= BACKGROUND_RESULT_THRESHOLD:
            header = self.table.horizontalHeader()
            self.indices.sort(key=lambda i: self._sort_key(i, header.sortIndicatorSection()),
                              reverse=header.sortIndicatorOrder() == Qt.SortOrder.DescendingOrder)
        self.render()
        if self.is_paged and sorting and count > BACKGROUND_RESULT_THRESHOLD:
            header = self.table.horizontalHeader()
            self.sort(header.sortIndicatorSection(), header.sortIndicatorOrder())
        if not self.is_paged:
            self.table.setSortingEnabled(sorting)

    def render(self, *, selection=(), current=None):
        self.populating = True
        self.table.setUpdatesEnabled(False)
        try:
            rows = self.page_indices if self.is_paged else self.indices
            self.table.clearContents()
            self.table.setRowCount(len(rows))
            model = self.table.model()
            # One notification avoids ResizeToContents/frozen-view work for
            # every inserted cell. Row count/reset notifications remain intact.
            with QSignalBlocker(model):
                for row, source in enumerate(rows):
                    for column, item in enumerate(self._item_factory(source)):
                        item.setData(SOURCE_ROLE, source)
                        self.table.setItem(row, column, item)
            if rows:
                model.dataChanged.emit(model.index(0, 0), model.index(len(rows) - 1, self.table.columnCount() - 1), [])
            if current is not None and current[0] in rows:
                self.table.setCurrentCell(rows.index(current[0]), current[1])
            for source, column in selection:
                if source in rows:
                    self.table.item(rows.index(source), column).setSelected(True)
        finally:
            self.table.setUpdatesEnabled(True)
            self.populating = False
        self.controls.setVisible(self.is_paged and self.page_count > 1)
        self._refresh_controls()
        self.table.horizontalHeader().setSortIndicatorShown(self.table.isSortingEnabled())
        self.pageChanged.emit()

    def _refresh_controls(self):
        status = " · 正在整理结果" if self._view_pending else ""
        self.page_label.setText(f"第 {self.page + 1} / {self.page_count} 页 · 每页 {self.page_size} 条{status}")
        self.previous_button.setEnabled(not self._view_pending and self.page > 0)
        self.next_button.setEnabled(not self._view_pending and self.page + 1 < self.page_count)

    def _selection(self):
        item = self.table.currentItem()
        current = (item.data(SOURCE_ROLE), self.table.currentColumn()) if item is not None else None
        selected = [(self.table.item(index.row(), index.column()).data(SOURCE_ROLE), index.column())
                    for index in self.table.selectedIndexes()]
        return current, selected

    def _callbacks(self):
        return self._snapshot() if self._snapshot is not None else (self._sort_key, self._text, self._identity)

    def _selection_changed(self, *_args):
        if self.populating:
            return
        self._pending_location = None
        self.invalidate_prefix()

    def _display_changed(self, *_args):
        if not self.populating:
            self.invalidate_prefix()

    def invalidate_prefix(self):
        """Discard a text lookup without cancelling a pending sort or filter."""
        if self._prefix_pending:
            self._search.cancel()
            self._prefix_pending = self._view_pending = False
            self._refresh_controls()

    def _submit_view(self, column, order, *, filter_changed=False):
        sort_key, _, _ = self._callbacks()
        self._prefix_pending = False
        predicate = self._predicate
        # A superseding sort must include the requested filter even if that
        # filter's result has not reached the GUI yet.
        rebuild_filter = filter_changed or self._filter_pending
        self._filter_pending = rebuild_filter
        base = list(self.indices) if not rebuild_filter else None
        total = self.total
        sorting = self.table.isSortingEnabled()
        reverse = order == Qt.SortOrder.DescendingOrder

        def work(token):
            indices = base
            if indices is None:
                indices = []
                for source in range(total):
                    if source % 1024 == 0:
                        token.check()
                    if predicate is None or predicate(source):
                        indices.append(source)
            if sorting:
                indices = _ordered_indices(indices, sort_key, column, reverse, token)
            return indices

        def done(indices):
            current, selection = self._selection()
            self._view_pending = False
            self._filter_pending = False
            self.indices = indices
            self.page = 0 if filter_changed else min(self.page, self.page_count - 1)
            if current is not None and current[0] in indices:
                self.page = indices.index(current[0]) // self.page_size
            self.page_label.setToolTip("")
            self.render(selection=selection, current=current)
            if self._pending_location is not None and not self._view_pending:
                location = self._pending_location
                self._pending_location = None
                self.locate(*location)

        def failed(error):
            self._view_pending = False
            self._filter_key = None
            self._refresh_controls()
            self.page_label.setText("结果整理失败")
            self.page_label.setToolTip(str(error))

        self._view_pending = True
        self._refresh_controls()
        self._search.submit(work, done, failed)

    def set_page(self, page):
        page = max(0, min(page, self.page_count - 1))
        if page != self.page:
            self.page = page
            self.render()

    def sort(self, column, order):
        if self.populating or not 0 <= column < self.table.columnCount():
            return
        if self.total > BACKGROUND_RESULT_THRESHOLD:
            self._submit_view(column, order)
            return
        current, selection = self._selection()
        sort_key, _, _ = self._callbacks()
        self.indices.sort(key=lambda i: sort_key(i, column), reverse=order == Qt.SortOrder.DescendingOrder)
        if current is not None and current[0] in self.indices:
            self.page = self.indices.index(current[0]) // self.page_size
        self.render(selection=selection, current=current)

    def set_filter(self, predicate, *, key=None):
        if not self.is_paged:
            return
        if key is not None and key == self._filter_key:
            return
        self._filter_key = key
        self._predicate = predicate
        if self.total > BACKGROUND_RESULT_THRESHOLD:
            header = self.table.horizontalHeader()
            self._submit_view(header.sortIndicatorSection(), header.sortIndicatorOrder(), filter_changed=True)
            return
        filtered = [i for i in range(self.total) if predicate(i)]
        if self.table.isSortingEnabled():
            header = self.table.horizontalHeader()
            sort_key, _, _ = self._callbacks()
            filtered.sort(key=lambda i: sort_key(i, header.sortIndicatorSection()),
                          reverse=header.sortIndicatorOrder() == Qt.SortOrder.DescendingOrder)
        if set(filtered) == set(self.indices):
            return
        self.indices = filtered
        self.page = 0
        self.render()

    def locate(self, identity, column):
        source = next((i for i in self.indices if self._identity(i) == identity), None)
        if source is None:
            return False
        position = self.indices.index(source)
        self.set_page(position // self.page_size)
        row = position % self.page_size
        self.table.setCurrentCell(row, column)
        self.table.scrollToItem(self.table.item(row, column), QAbstractItemView.ScrollHint.PositionAtCenter)
        return True

    def locate_when_ready(self, identity, column):
        if self._view_pending:
            self._pending_location = (identity, column)
            return True
        return self.locate(identity, column)

    def select_prefix(self, prefix, column):
        # A sort/filter owns the worker until its new view is ready. A prefix
        # lookup can be replaced by the latest typed text in the same worker.
        if self._view_pending and not self._prefix_pending:
            return False
        if len(self.indices) > BACKGROUND_RESULT_THRESHOLD:
            # Capture the displayed domain snapshot before starting the worker.
            _, text, _ = self._callbacks()
            current = self.table.currentItem()
            source = current.data(SOURCE_ROLE) if current is not None else None
            indices = list(self.indices)
            start = indices.index(source) if source in indices else -1
            folded = prefix.casefold()

            def work(token):
                for offset in range(1, len(indices) + 1):
                    if offset % 1024 == 0:
                        token.check()
                    position = (start + offset) % len(indices)
                    if text(indices[position], column).casefold().startswith(folded):
                        return position
                return -1

            def done(position):
                self._prefix_pending = self._view_pending = False
                self._refresh_controls()
                if position >= 0:
                    self.set_page(position // self.page_size)
                    self.table.setCurrentCell(position % self.page_size, column)
                    self.table.scrollToItem(self.table.currentItem(), QAbstractItemView.ScrollHint.PositionAtCenter)

            self._view_pending = True
            self._prefix_pending = True
            self._refresh_controls()
            self._search.submit(work, done, lambda error: done(-1))
            return True
        current = self.table.currentItem()
        source = current.data(SOURCE_ROLE) if current is not None else None
        start = self.indices.index(source) if source in self.indices else -1
        _, text, _ = self._callbacks()
        for offset in range(1, len(self.indices) + 1):
            position = (start + offset) % len(self.indices)
            source = self.indices[position]
            if text(source, column).casefold().startswith(prefix.casefold()):
                self.set_page(position // self.page_size)
                self.table.setCurrentCell(position % self.page_size, column)
                self.table.scrollToItem(self.table.currentItem(), QAbstractItemView.ScrollHint.PositionAtCenter)
                return True
        return False

    def close(self):
        self._search.close()
        self._view_pending = False
        self._pending_location = None
        # Factories and snapshot providers may capture their UI owner and the
        # complete result set. Release them at close instead of keeping that
        # cycle alive until a later search triggers Python's garbage collector.
        self._item_factory = self._sort_key = self._text = self._identity = None
        self._snapshot = self._predicate = None
        self.indices = []
