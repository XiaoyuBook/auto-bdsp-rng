"""Reusable non-modal window for the shared session's complete log view."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable

from PySide6.QtCore import QEvent, Qt, Signal, Slot
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from auto_bdsp_rng.ui.run_log_panel import RunLogBuffer, RunLogEntry, RunLogPanel
from auto_bdsp_rng.ui.workspace_theme import ui_font, ui_styles


class RunLogWindow(QDialog):
    unreadProblemsChanged = Signal(int)

    def __init__(
        self,
        buffer: RunLogBuffer,
        *,
        save_enabled: bool = False,
        set_save_enabled: Callable[[bool], bool] | None = None,
        open_log_dir: Callable[[], object] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("RunLogWindow")
        self.setWindowTitle("完整日志")
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.setFont(ui_font())
        self.setMinimumSize(560, 380)
        self.resize(980, 640)
        self._position_initialized = False
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 12, 18, 12)
        root.setSpacing(0)
        heading = QHBoxLayout()
        title = QLabel("完整日志", self)
        title.setStyleSheet(ui_styles("font-size: 16px; font-weight: 500; color: #202A33;"))
        heading.addWidget(title)
        heading.addStretch(1)
        hint = QLabel("当前会话", self)
        hint.setStyleSheet("color: #687480; font-size: 12px;")
        heading.addWidget(hint)
        root.addLayout(heading)
        self.log_panel = RunLogPanel(
            buffer, save_enabled=save_enabled, set_save_enabled=set_save_enabled,
            open_log_dir=open_log_dir, parent=self,
        )
        root.addWidget(self.log_panel, 1)
        self.setStyleSheet("QDialog#RunLogWindow { background: #F2F4F7; }")
        buffer.entryAdded.connect(self._on_entry_added, Qt.ConnectionType.QueuedConnection)
        buffer.entryUpdated.connect(self._on_entry_added, Qt.ConnectionType.QueuedConnection)
        initial = buffer.snapshot()
        self._read_through_seq = max((entry.seq for entry in initial), default=0)
        self._problem_versions = OrderedDict(
            (entry.seq, entry.repeat_count) for entry in initial if entry.level in {"WARNING", "ERROR", "CRITICAL"}
        )
        self._unread_problem_sequences = set(self._problem_versions)
        self.unread_problem_count = sum(entry.level in {"WARNING", "ERROR", "CRITICAL"} for entry in initial)
        self._buffer = buffer

    def show_logs(
        self, source: str | None = None, *, run_id: str | None = None,
        round_id: int | None = None, label: str | None = None,
    ) -> None:
        self.log_panel.clear_filters()
        self.log_panel.set_source_filter(source)
        self.log_panel.show_round_logs(run_id, round_id, label)
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        if not self._position_initialized:
            screen = self.parentWidget().screen() if self.parentWidget() is not None else self.screen()
            if screen is not None:
                available = screen.availableGeometry()
                self.resize(min(self.width(), available.width()), min(self.height(), available.height() - 40))
                center = self.parentWidget().frameGeometry().center() if self.parentWidget() else available.center()
                self.move(
                    min(max(center.x() - self.width() // 2, available.left()), max(available.left(), available.right() - self.width())),
                    min(max(center.y() - self.height() // 2, available.top()), max(available.top(), available.bottom() - self.height() - 40)),
                )
            self._position_initialized = True
        self.raise_()
        self.activateWindow()
        self._mark_read()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._mark_read()

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and self.isVisible() and not self.isMinimized():
            self._mark_read()

    def _mark_read(self) -> None:
        self._unread_problem_sequences.clear()
        entries = self._buffer.snapshot()
        self._read_through_seq = max((entry.seq for entry in entries), default=self._read_through_seq)
        self._problem_versions = OrderedDict(
            (entry.seq, entry.repeat_count) for entry in entries
            if entry.level in {"WARNING", "ERROR", "CRITICAL"}
        )
        if getattr(self, "unread_problem_count", 0):
            self.unread_problem_count = 0
            self.unreadProblemsChanged.emit(0)

    @Slot(object)
    def _on_entry_added(self, entry: RunLogEntry) -> None:
        if entry.level in {"WARNING", "ERROR", "CRITICAL"}:
            if entry.seq <= self._read_through_seq and entry.seq not in self._problem_versions:
                return
            if entry.repeat_count <= self._problem_versions.get(entry.seq, 0):
                return
            self._problem_versions[entry.seq] = entry.repeat_count
            self._problem_versions.move_to_end(entry.seq)
            while len(self._problem_versions) > self._buffer.max_entries:
                expired, _version = self._problem_versions.popitem(last=False)
                self._unread_problem_sequences.discard(expired)
            if (not self.isVisible() or self.isMinimized()) and entry.seq not in self._unread_problem_sequences:
                self._unread_problem_sequences.add(entry.seq)
                self.unread_problem_count += 1
                self.unreadProblemsChanged.emit(self.unread_problem_count)
