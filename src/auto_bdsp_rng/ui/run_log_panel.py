"""Structured, filterable runtime log widgets.

The in-memory buffer is independent from optional on-disk logging: callers can
always publish entries here and let the save toggle decide only whether the
same messages are persisted elsewhere.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import threading

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QObject,
    QSortFilterProxyModel,
    Qt,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QColor, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QTableView,
    QVBoxLayout,
    QWidget,
)


MAX_LOG_ENTRIES = 10_000
ENTRY_ROLE = int(Qt.ItemDataRole.UserRole)


@dataclass(frozen=True)
class RunLogEntry:
    seq: int
    timestamp: datetime
    level: str
    source: str
    message: str
    run_id: str | None = None
    round_id: int | None = None


class RunLogBuffer(QObject):
    """Thread-safe, bounded storage for one application's live log entries."""

    entryAdded = Signal(object)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        max_entries: int = MAX_LOG_ENTRIES,
    ) -> None:
        super().__init__(parent)
        capacity = int(max_entries)
        if capacity <= 0:
            raise ValueError("max_entries must be greater than zero")
        self._max_entries = min(capacity, MAX_LOG_ENTRIES)
        self._entries: deque[RunLogEntry] = deque(maxlen=self._max_entries)
        self._lock = threading.RLock()
        self._next_seq = 1

    @property
    def max_entries(self) -> int:
        return self._max_entries

    def publish(
        self,
        source: str,
        message: str,
        level: str = "INFO",
        run_id: str | None = None,
        round_id: int | None = None,
    ) -> RunLogEntry:
        safe_source = " ".join(str(source).split()) or "应用"
        safe_level = str(level).strip().upper() or "INFO"
        entry_message = str(message)
        with self._lock:
            entry = RunLogEntry(
                seq=self._next_seq,
                timestamp=datetime.now(),
                level=safe_level,
                source=safe_source,
                message=entry_message,
                run_id=run_id,
                round_id=round_id,
            )
            self._next_seq += 1
            self._entries.append(entry)

        # Qt selects a queued connection when publish() runs outside the UI
        # thread, so subscribers never need to touch widgets from that thread.
        self.entryAdded.emit(entry)
        return entry

    def snapshot(self) -> tuple[RunLogEntry, ...]:
        with self._lock:
            return tuple(self._entries)


class _RunLogTableModel(QAbstractTableModel):
    HEADERS = ("时间", "级别", "来源", "消息")

    def __init__(self, max_entries: int, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._max_entries = max(1, min(int(max_entries), MAX_LOG_ENTRIES))
        self._entries: list[RunLogEntry] = []
        self._sequences: set[int] = set()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._entries)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.HEADERS)

    def data(self, index: QModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)) -> object:
        if not index.isValid() or not (0 <= index.row() < len(self._entries)):
            return None
        entry = self._entries[index.row()]
        column = index.column()

        if role == ENTRY_ROLE:
            return entry
        if role in (int(Qt.ItemDataRole.DisplayRole), int(Qt.ItemDataRole.EditRole)):
            if column == 0:
                return entry.timestamp.strftime("%H:%M:%S.%f")[:-3]
            if column == 1:
                return entry.level
            if column == 2:
                return entry.source
            if column == 3:
                return entry.message.replace("\r\n", "\n").replace("\r", "\n")
        if role == int(Qt.ItemDataRole.ToolTipRole):
            timestamp = entry.timestamp.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            return f"{timestamp} [{entry.level}] [{entry.source}] {entry.message}"
        if role == int(Qt.ItemDataRole.TextAlignmentRole):
            return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        if role == int(Qt.ItemDataRole.ForegroundRole):
            if column == 1:
                return {
                    "DEBUG": QColor("#6B7280"),
                    "INFO": QColor("#166534"),
                    "WARNING": QColor("#92400E"),
                    "ERROR": QColor("#B91C1C"),
                    "CRITICAL": QColor("#991B1B"),
                }.get(entry.level, QColor("#374151"))
            if column in (0, 2):
                return QColor("#6B7280")
        if role == int(Qt.ItemDataRole.BackgroundRole) and column == 1:
            return {
                "INFO": QColor("#ECFDF5"),
                "WARNING": QColor("#FFFBEB"),
                "ERROR": QColor("#FEF2F2"),
                "CRITICAL": QColor("#FEE2E2"),
            }.get(entry.level)
        if role == int(Qt.ItemDataRole.FontRole) and column == 1:
            font = QFont()
            font.setBold(True)
            return font
        return None

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> object:
        if (
            orientation == Qt.Orientation.Horizontal
            and role == int(Qt.ItemDataRole.DisplayRole)
            and 0 <= section < len(self.HEADERS)
        ):
            return self.HEADERS[section]
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def entries(self) -> tuple[RunLogEntry, ...]:
        return tuple(self._entries)

    def replace(self, entries: tuple[RunLogEntry, ...] | list[RunLogEntry]) -> None:
        unique = {entry.seq: entry for entry in entries}
        ordered = sorted(unique.values(), key=lambda entry: entry.seq)[-self._max_entries :]
        self.beginResetModel()
        self._entries = ordered
        self._sequences = set(unique_entry.seq for unique_entry in ordered)
        self.endResetModel()

    def add_entry(self, entry: RunLogEntry) -> bool:
        if entry.seq in self._sequences:
            return False
        position = bisect_left([item.seq for item in self._entries], entry.seq)
        self.beginInsertRows(QModelIndex(), position, position)
        self._entries.insert(position, entry)
        self._sequences.add(entry.seq)
        self.endInsertRows()

        if len(self._entries) > self._max_entries:
            self.beginRemoveRows(QModelIndex(), 0, 0)
            removed = self._entries.pop(0)
            self._sequences.discard(removed.seq)
            self.endRemoveRows()
        return True

    def clear(self) -> None:
        if not self._entries:
            return
        self.beginResetModel()
        self._entries.clear()
        self._sequences.clear()
        self.endResetModel()


class _RunLogFilterProxyModel(QSortFilterProxyModel):
    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._source_filter: str | None = None
        self._level_filter: str | None = None
        self._search_text = ""
        self._run_id: str | None = None
        self._round_id: int | None = None
        self.setDynamicSortFilter(True)

    def set_source_filter(self, source: str | None) -> None:
        self._source_filter = source or None
        self.invalidateFilter()

    def set_level_filter(self, level: str | None) -> None:
        self._level_filter = level.upper() if level else None
        self.invalidateFilter()

    def set_search_text(self, text: str) -> None:
        self._search_text = text.strip().casefold()
        self.invalidateFilter()

    def set_round_filter(self, run_id: str | None, round_id: int | None) -> None:
        self._run_id = run_id
        self._round_id = round_id
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:  # noqa: N802
        model = self.sourceModel()
        if model is None:
            return False
        entry = model.index(source_row, 0, source_parent).data(ENTRY_ROLE)
        if not isinstance(entry, RunLogEntry):
            return False
        if self._source_filter is not None and entry.source != self._source_filter:
            return False
        if self._level_filter is not None and entry.level != self._level_filter:
            return False
        if self._run_id is not None and entry.run_id != self._run_id:
            return False
        if self._round_id is not None and entry.round_id != self._round_id:
            return False
        if self._search_text:
            timestamp = entry.timestamp.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            searchable = "\n".join((timestamp, entry.level, entry.source, entry.message)).casefold()
            if self._search_text not in searchable:
                return False
        return True


class RunLogPanel(QWidget):
    """Live log viewer with structured filters and optional round correlation."""

    countsChanged = Signal(int, int)

    _LEVEL_LABELS = {
        "DEBUG": "调试",
        "INFO": "信息",
        "WARNING": "警告",
        "ERROR": "错误",
        "CRITICAL": "严重",
    }

    def __init__(
        self,
        buffer: RunLogBuffer,
        save_enabled: bool = False,
        set_save_enabled: Callable[[bool], bool] | None = None,
        open_log_dir: Callable[[], object] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("RunLogPanel")
        self._buffer = buffer
        self._set_save_enabled_callback = set_save_enabled
        self._open_log_dir_callback = open_log_dir
        self._save_enabled = bool(save_enabled)
        self._model = _RunLogTableModel(buffer.max_entries, self)
        self.log_model = self._model
        self.proxy_model = _RunLogFilterProxyModel(self)
        self.proxy_model.setSourceModel(self._model)
        self._follow_timer = QTimer(self)
        self._follow_timer.setSingleShot(True)
        self._follow_timer.timeout.connect(self._scroll_to_bottom)
        self._build_ui()
        self._connect_signals()

        # Connect before taking the snapshot. Any concurrent publish is either
        # present in the snapshot or arrives as a queued signal; seq de-duplication
        # makes both paths safe.
        self._buffer.entryAdded.connect(self.add_entry)
        self._model.replace(list(self._buffer.snapshot()))
        self._rebuild_filter_options()
        self._refresh_view_state()
        if self.follow_check.isChecked() and self.proxy_model.rowCount() > 0:
            self._follow_timer.start(0)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        filters = QHBoxLayout()
        filters.setContentsMargins(0, 0, 0, 0)
        filters.setSpacing(10)

        source_field, self.source_combo = self._combo_field("来源", "全部来源")
        self.source_combo.setObjectName("RunLogSourceFilter")
        self.source_combo.setMinimumWidth(140)
        filters.addLayout(source_field)

        level_field, self.level_combo = self._combo_field("级别", "全部级别")
        self.level_combo.setObjectName("RunLogLevelFilter")
        for level, label in self._LEVEL_LABELS.items():
            self.level_combo.addItem(label, level)
        self.level_combo.setMinimumWidth(112)
        filters.addLayout(level_field)

        search_field = QVBoxLayout()
        search_field.setSpacing(3)
        search_field.addWidget(self._field_label("搜索日志"))
        self.search_edit = QLineEdit(self)
        self.search_edit.setObjectName("RunLogSearch")
        self.search_edit.setPlaceholderText("输入关键词")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMinimumWidth(180)
        search_field.addWidget(self.search_edit)
        filters.addLayout(search_field, 1)

        toggle_row = QHBoxLayout()
        toggle_row.setContentsMargins(0, 20, 0, 0)
        toggle_row.setSpacing(12)
        self.follow_check = QCheckBox("自动跟随", self)
        self.follow_check.setObjectName("RunLogAutoFollow")
        self.follow_check.setChecked(True)
        self.save_check = QCheckBox("自动保存到文件（保留 7 天）", self)
        self.save_check.setObjectName("RunLogAutoSave")
        self.save_check.setChecked(self._save_enabled)
        toggle_row.addWidget(self.follow_check)
        toggle_row.addWidget(self.save_check)
        filters.addLayout(toggle_row)
        root.addLayout(filters)

        self.correlation_frame = QFrame(self)
        self.correlation_frame.setObjectName("RunLogCorrelation")
        correlation_layout = QHBoxLayout(self.correlation_frame)
        correlation_layout.setContentsMargins(10, 6, 8, 6)
        correlation_layout.setSpacing(8)
        self.correlation_label = QLabel(self.correlation_frame)
        self.correlation_label.setObjectName("RunLogCorrelationLabel")
        self.correlation_label.setTextFormat(Qt.TextFormat.PlainText)
        self.correlation_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.correlation_clear_button = QPushButton("取消轮次筛选", self.correlation_frame)
        self.correlation_clear_button.setObjectName("RunLogCorrelationClear")
        self.correlation_clear_button.setFixedHeight(28)
        correlation_layout.addWidget(self.correlation_label, 1)
        correlation_layout.addWidget(self.correlation_clear_button)
        self.correlation_frame.hide()
        root.addWidget(self.correlation_frame)

        self.table = QTableView(self)
        self.table.setObjectName("RunLogTable")
        self.table.setAccessibleName("详细运行日志")
        self.table.setModel(self.proxy_model)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        header = self.table.horizontalHeader()
        header.setFixedHeight(34)
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 110)
        self.table.setColumnWidth(1, 104)
        self.table.setColumnWidth(2, 140)
        root.addWidget(self.table, 1)

        self.footer_frame = QFrame(self)
        self.footer_frame.setObjectName("RunLogFooter")
        self.footer_frame.setFixedHeight(50)
        footer = QHBoxLayout(self.footer_frame)
        footer.setContentsMargins(0, 8, 0, 8)
        footer.setSpacing(8)
        self.count_label = QLabel("显示 0 条 · 当前会话共 0 条", self)
        self.count_label.setObjectName("RunLogCount")
        self.count_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        footer.addWidget(self.count_label, 0, Qt.AlignmentFlag.AlignVCenter)
        footer.addStretch()
        self.clear_button = QPushButton("清空显示", self)
        self.copy_button = QPushButton("复制", self)
        self.export_button = QPushButton("导出", self)
        self.open_dir_button = QPushButton("日志目录", self)
        for button in (
            self.clear_button,
            self.copy_button,
            self.export_button,
            self.open_dir_button,
        ):
            button.setFixedHeight(34)
            footer.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
        self.clear_button.setObjectName("RunLogClear")
        self.copy_button.setObjectName("RunLogCopy")
        self.export_button.setObjectName("RunLogExport")
        self.open_dir_button.setObjectName("RunLogOpenDirectory")
        self.open_dir_button.setEnabled(self._open_log_dir_callback is not None)
        root.addWidget(self.footer_frame)

        self.setStyleSheet(
            """
            QWidget#RunLogPanel {
                background: transparent;
            }
            QWidget#RunLogPanel QLabel#RunLogFieldLabel,
            QWidget#RunLogPanel QLabel#RunLogCount {
                color: #6B7280;
                font-size: 12px;
            }
            QFrame#RunLogFooter {
                background: transparent;
                border: 0;
            }
            QWidget#RunLogPanel QPushButton#RunLogClear,
            QWidget#RunLogPanel QPushButton#RunLogCopy,
            QWidget#RunLogPanel QPushButton#RunLogExport,
            QWidget#RunLogPanel QPushButton#RunLogOpenDirectory {
                min-height: 32px;
                max-height: 32px;
                padding: 0 12px;
            }
            QFrame#RunLogCorrelation {
                background: #ECFDF5;
                border: 1px solid #A7F3D0;
                border-radius: 6px;
            }
            QLabel#RunLogCorrelationLabel {
                color: #166534;
                font-weight: 600;
            }
            QPushButton#RunLogCorrelationClear {
                background: transparent;
                border: 0;
                color: #047857;
                font-weight: 600;
                padding: 0 6px;
            }
            QPushButton#RunLogCorrelationClear:hover {
                color: #065F46;
                text-decoration: underline;
            }
            QTableView#RunLogTable {
                background: #FFFFFF;
                alternate-background-color: #F9FAFB;
                border: 1px solid #E5E7EB;
                border-radius: 6px;
                gridline-color: #EEF0F3;
                color: #111827;
                selection-background-color: #0E8F70;
                selection-color: #FFFFFF;
            }
            QTableView#RunLogTable::item {
                border-bottom: 1px solid #F3F4F6;
                padding: 3px 6px;
            }
            QTableView#RunLogTable QHeaderView::section {
                background: #F9FAFB;
                color: #4B5563;
                border: 0;
                border-right: 1px solid #E5E7EB;
                border-bottom: 1px solid #D1D5DB;
                padding: 6px;
                font-weight: 700;
            }
            """
        )

    @staticmethod
    def _field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("RunLogFieldLabel")
        return label

    def _combo_field(self, label: str, all_text: str) -> tuple[QVBoxLayout, QComboBox]:
        layout = QVBoxLayout()
        layout.setSpacing(3)
        layout.addWidget(self._field_label(label))
        combo = QComboBox(self)
        combo.addItem(all_text, None)
        layout.addWidget(combo)
        return layout, combo

    def _connect_signals(self) -> None:
        self.source_combo.currentIndexChanged.connect(self._apply_source_filter)
        self.level_combo.currentIndexChanged.connect(self._apply_level_filter)
        self.search_edit.textChanged.connect(self._apply_search_filter)
        self.save_check.toggled.connect(self._request_save_enabled)
        self.correlation_clear_button.clicked.connect(self.clear_round_filter)
        self.clear_button.clicked.connect(self.clear_display)
        self.copy_button.clicked.connect(self.copy_filtered)
        self.export_button.clicked.connect(self.export_filtered)
        self.open_dir_button.clicked.connect(self._open_log_directory)
        self.proxy_model.rowsInserted.connect(self._on_proxy_rows_inserted)
        self.proxy_model.rowsRemoved.connect(self._refresh_view_state)
        self.proxy_model.modelReset.connect(self._refresh_view_state)
        self.proxy_model.layoutChanged.connect(self._refresh_view_state)

    @Slot(object)
    def add_entry(self, entry: object) -> None:
        if not isinstance(entry, RunLogEntry):
            return
        if not self._model.add_entry(entry):
            return
        self._ensure_source_option(entry.source)
        self._ensure_level_option(entry.level)
        self._refresh_view_state()

    def set_save_enabled(self, enabled: bool) -> None:
        """Synchronize the checkbox without invoking the persistence callback."""

        self._save_enabled = bool(enabled)
        blocked = self.save_check.blockSignals(True)
        self.save_check.setChecked(self._save_enabled)
        self.save_check.blockSignals(blocked)

    def set_source_filter(self, source: str | None) -> None:
        """Select a source filter, adding a not-yet-seen source if needed."""

        if source is None:
            index = 0
        else:
            value = str(source)
            self._ensure_source_option(value)
            index = self.source_combo.findData(value)
        if self.source_combo.currentIndex() != index:
            self.source_combo.setCurrentIndex(index)
        else:
            self.proxy_model.set_source_filter(str(source) if source is not None else None)
            self._refresh_view_state()
            self._follow_filtered_rows()

    def show_round_logs(
        self,
        run_id: str | None,
        round_id: int | None,
        label: str | None = None,
    ) -> None:
        if run_id is None and round_id is None:
            self.clear_round_filter()
            return
        self.proxy_model.set_round_filter(run_id, round_id)
        if label is not None:
            correlation_text = str(label)
        elif round_id is not None:
            correlation_text = f"正在查看第 {round_id} 轮相关日志"
        else:
            correlation_text = "正在查看所选运行的相关日志"
        self.correlation_label.setText(correlation_text)
        self.correlation_frame.show()
        self._refresh_view_state()
        self._follow_filtered_rows()

    @Slot()
    def clear_round_filter(self) -> None:
        self.proxy_model.set_round_filter(None, None)
        self.correlation_label.clear()
        self.correlation_frame.hide()
        self._refresh_view_state()
        self._follow_filtered_rows()

    @Slot()
    def clear_display(self) -> None:
        self._model.clear()
        self._refresh_view_state()

    def visible_entries(self) -> tuple[RunLogEntry, ...]:
        entries: list[RunLogEntry] = []
        for row in range(self.proxy_model.rowCount()):
            entry = self.proxy_model.index(row, 0).data(ENTRY_ROLE)
            if isinstance(entry, RunLogEntry):
                entries.append(entry)
        return tuple(entries)

    @Slot()
    def copy_filtered(self) -> None:
        text = self._filtered_text()
        if text:
            QGuiApplication.clipboard().setText(text)

    def copy_current_filter(self) -> None:
        self.copy_filtered()

    @Slot()
    def export_filtered(self) -> None:
        default_name = f"run_logs_{datetime.now():%Y%m%d_%H%M%S}.txt"
        path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "导出当前日志",
            default_name,
            "文本文件 (*.txt);;日志文件 (*.log);;所有文件 (*)",
        )
        if not path:
            return
        text = self._filtered_text()
        Path(path).write_text(f"{text}\n" if text else "", encoding="utf-8")

    def export_current_filter(self) -> None:
        self.export_filtered()

    def _filtered_text(self) -> str:
        lines: list[str] = []
        for entry in self.visible_entries():
            timestamp = entry.timestamp.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            messages = entry.message.replace("\r\n", "\n").replace("\r", "\n").split("\n")
            for message in messages or [""]:
                lines.append(f"{timestamp} [{entry.level}] [{entry.source}] {message}")
        return "\n".join(lines)

    def _rebuild_filter_options(self) -> None:
        for entry in self._model.entries():
            self._ensure_source_option(entry.source)
            self._ensure_level_option(entry.level)

    def _ensure_source_option(self, source: str) -> None:
        if self.source_combo.findData(source) < 0:
            self.source_combo.addItem(source, source)

    def _ensure_level_option(self, level: str) -> None:
        if self.level_combo.findData(level) < 0:
            self.level_combo.addItem(self._LEVEL_LABELS.get(level, level), level)

    @Slot(int)
    def _apply_source_filter(self, _index: int) -> None:
        value = self.source_combo.currentData()
        self.proxy_model.set_source_filter(str(value) if value is not None else None)
        self._refresh_view_state()
        self._follow_filtered_rows()

    @Slot(int)
    def _apply_level_filter(self, _index: int) -> None:
        value = self.level_combo.currentData()
        self.proxy_model.set_level_filter(str(value) if value is not None else None)
        self._refresh_view_state()
        self._follow_filtered_rows()

    @Slot(str)
    def _apply_search_filter(self, text: str) -> None:
        self.proxy_model.set_search_text(text)
        self._refresh_view_state()
        self._follow_filtered_rows()

    @Slot(bool)
    def _request_save_enabled(self, requested: bool) -> None:
        previous = self._save_enabled
        try:
            actual = requested
            if self._set_save_enabled_callback is not None:
                actual = bool(self._set_save_enabled_callback(requested))
        except Exception:
            actual = previous
        self.set_save_enabled(actual)

    @Slot()
    def _open_log_directory(self) -> None:
        if self._open_log_dir_callback is not None:
            self._open_log_dir_callback()

    @Slot(QModelIndex, int, int)
    def _on_proxy_rows_inserted(self, _parent: QModelIndex, _first: int, _last: int) -> None:
        self._refresh_view_state()
        self._follow_filtered_rows()

    @Slot()
    def _refresh_view_state(self, *_args: object) -> None:
        visible = self.proxy_model.rowCount()
        total = self._model.rowCount()
        self.count_label.setText(f"显示 {visible} 条 · 当前会话共 {total} 条")
        self.clear_button.setEnabled(total > 0)
        self.copy_button.setEnabled(visible > 0)
        self.export_button.setEnabled(visible > 0)
        self.countsChanged.emit(visible, total)

    def _follow_filtered_rows(self) -> None:
        if self.follow_check.isChecked() and self.proxy_model.rowCount() > 0:
            self._follow_timer.start(0)

    @Slot()
    def _scroll_to_bottom(self) -> None:
        if self.follow_check.isChecked() and self.proxy_model.rowCount() > 0:
            self.table.scrollToBottom()


__all__ = ["RunLogBuffer", "RunLogEntry", "RunLogPanel"]
