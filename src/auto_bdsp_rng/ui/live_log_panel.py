"""Compact session log for the persistent video sidebar."""

from PySide6.QtCore import QEvent, QTimer, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from auto_bdsp_rng.ui.check_box import CheckmarkCheckBox as QCheckBox
from auto_bdsp_rng.ui.combo_box import ChevronComboBox as QComboBox
from auto_bdsp_rng.ui.run_log_panel import RunLogBuffer, RunLogEntry
from auto_bdsp_rng.ui.workspace_controls import configure_disclosure_button, set_disclosure_state


class LiveLogPanel(QFrame):
    """A bounded live view; filters never modify the shared session buffer."""

    expandRequested = Signal(object)
    collapsedChanged = Signal(bool)
    DISPLAY_LIMIT = 300

    def __init__(self, buffer: RunLogBuffer, parent=None):
        super().__init__(parent)
        self.setObjectName("LiveLogPanel")
        self._buffer = buffer
        self._last_seq = 0
        self._visible_count = 0
        self._collapsed = False
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.timeout.connect(self._flush)
        self.setMinimumSize(0, 110)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(6)
        heading = QHBoxLayout()
        self.collapse_button = QToolButton()
        self.collapse_button.setText("实时日志")
        self.collapse_button.setCheckable(True)
        self.collapse_button.setChecked(True)
        configure_disclosure_button(self.collapse_button, compact=True)
        set_disclosure_state(self.collapse_button, True)
        self.collapse_button.setAccessibleName("折叠实时日志")
        self.collapse_button.setToolTip("折叠实时日志，消息继续收集")
        self.count_label = QLabel("0 条")
        self.count_label.setObjectName("WorkspaceHint")
        self.expand_button = QPushButton("查看完整日志")
        self.expand_button.setObjectName("InlineLinkButton")
        self.expand_button.setFixedHeight(24)
        self.expand_button.setStyleSheet("QPushButton { min-height: 22px; max-height: 22px; }")
        self.expand_button.setToolTip("打开完整日志，查看轮次、搜索或导出")
        heading.addWidget(self.collapse_button)
        heading.addWidget(self.count_label)
        self.problem_label = QLabel()
        self.problem_label.setStyleSheet("color: #AC4B42; font-size: 12px;")
        self.problem_label.setToolTip("完整日志尚未查看的警告或错误")
        self.problem_label.hide()
        heading.addWidget(self.problem_label)
        heading.addStretch(1)
        heading.addWidget(self.expand_button)
        layout.addLayout(heading)

        self.body = QWidget()
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(6)
        filters = QHBoxLayout()
        self.source_combo = QComboBox()
        self.source_combo.setAccessibleName("监看日志来源")
        self.source_combo.addItem("全部来源", None)
        self.source_combo.setMinimumWidth(0)
        self.source_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.source_combo.setMinimumContentsLength(5)
        self.source_combo.setFixedHeight(28)
        self.source_combo.setStyleSheet("QComboBox { min-height: 26px; max-height: 26px; padding: 0 8px; }")
        self.follow_check = QCheckBox("自动跟随")
        self.follow_check.setChecked(True)
        filters.addWidget(self.source_combo, 1)
        filters.addWidget(self.follow_check)
        body_layout.addLayout(filters)

        self.text = QPlainTextEdit()
        self.text.setObjectName("LiveLogText")
        self.text.setAccessibleName("实时运行日志")
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(self.DISPLAY_LIMIT)
        self.text.setPlaceholderText("等待运行日志…\n捕捉 Seed、运行任务或连接设备后，消息会显示在这里。")
        self.text.setFrameShape(QFrame.Shape.NoFrame)
        self.text.setStyleSheet("QPlainTextEdit { padding: 2px 6px; }")
        self.text.setMinimumSize(0, 0)
        self.text.setToolTip("显示最近 300 行；可通过“查看完整日志”搜索或导出。")
        self.text.viewport().installEventFilter(self)
        body_layout.addWidget(self.text, 1)
        layout.addWidget(self.body, 1)
        self.collapse_button.toggled.connect(lambda expanded: self.set_collapsed(not expanded))
        self.expand_button.clicked.connect(lambda: self.expandRequested.emit(self.source_combo.currentData()))
        self.source_combo.currentIndexChanged.connect(self._refresh)
        self.follow_check.toggled.connect(self._follow)
        buffer.entryAdded.connect(self._receive, Qt.ConnectionType.QueuedConnection)
        buffer.entryUpdated.connect(self._update_entry, Qt.ConnectionType.QueuedConnection)
        self._refresh()

    @property
    def collapsed(self) -> bool:
        return self._collapsed

    def set_collapsed(self, collapsed: bool) -> None:
        collapsed = bool(collapsed)
        if collapsed == self._collapsed:
            return
        self._collapsed = collapsed
        self.body.setVisible(not collapsed)
        self.setMinimumHeight(40 if collapsed else 110)
        self.setMaximumHeight(40 if collapsed else 16777215)
        self.setSizePolicy(QSizePolicy.Policy.Preferred,
                           QSizePolicy.Policy.Fixed if collapsed else QSizePolicy.Policy.Expanding)
        blocked = self.collapse_button.blockSignals(True)
        self.collapse_button.setChecked(not collapsed)
        self.collapse_button.blockSignals(blocked)
        set_disclosure_state(self.collapse_button, not collapsed)
        self.collapse_button.setAccessibleName("恢复实时日志" if collapsed else "折叠实时日志")
        self.collapse_button.setToolTip("恢复实时日志" if collapsed else "折叠实时日志，消息继续收集")
        self.collapsedChanged.emit(collapsed)
        if not collapsed:
            self._follow()

    def set_unread_problem_count(self, count: int) -> None:
        self.problem_label.setText(f"异常 {count}")
        self.problem_label.setVisible(count > 0)

    @staticmethod
    def _line(entry: RunLogEntry) -> str:
        level = {"INFO": "信息", "WARNING": "警告", "ERROR": "错误", "CRITICAL": "严重", "DEBUG": "调试"}.get(entry.level, entry.level)
        timestamp = entry.last_timestamp or entry.timestamp
        return f"{timestamp:%H:%M:%S}  {entry.source_label} · {level}\n{entry.display_message}"

    def _matches(self, entry: RunLogEntry) -> bool:
        source = self.source_combo.currentData()
        return entry.user_visible and (source is None or source == entry.source)

    def _add_source(self, source: str) -> None:
        if self.source_combo.findData(source) < 0:
            self.source_combo.addItem(source, source)

    def _refresh(self, *_args) -> None:
        entries = self._buffer.snapshot()
        for entry in entries:
            self._add_source(entry.source)
        self._last_seq = max((entry.seq for entry in entries), default=0)
        selected = [entry for entry in entries if self._matches(entry)]
        self._visible_count = len(selected)
        self.text.setPlainText("\n".join(self._line(entry) for entry in selected[-self.DISPLAY_LIMIT:]))
        self.count_label.setText(f"{self._visible_count} 条")
        self._follow()

    @Slot(object)
    def _receive(self, entry: RunLogEntry) -> None:
        # Coalesce worker bursts into one UI update. The snapshot keeps entries
        # ordered even when publishers' queued signals arrive out of order.
        if entry.seq > self._last_seq and not self._flush_timer.isActive():
            self._flush_timer.start(0)

    @Slot(object)
    def _update_entry(self, entry: RunLogEntry) -> None:
        if not self._matches(entry):
            return
        position = self.text.verticalScrollBar().value()
        self._refresh()
        if not self.follow_check.isChecked():
            self.text.verticalScrollBar().setValue(position)

    def _flush(self) -> None:
        entries = self._buffer.snapshot()
        selected = [entry for entry in entries if self._matches(entry)]
        new_entries = [entry for entry in selected if entry.seq > self._last_seq]
        for entry in entries:
            if entry.seq > self._last_seq:
                self._add_source(entry.source)
        self._last_seq = max((entry.seq for entry in entries), default=self._last_seq)
        self._visible_count = len(selected)
        self.count_label.setText(f"{self._visible_count} 条")
        if not new_entries:
            return
        scrollbar = self.text.verticalScrollBar()
        position = scrollbar.value()
        self.text.appendPlainText("\n".join(self._line(entry) for entry in new_entries[-self.DISPLAY_LIMIT:]))
        if self.follow_check.isChecked():
            QTimer.singleShot(0, self, self._follow)
        else:
            scrollbar.setValue(position)

    def _follow(self, *_args) -> None:
        if self.follow_check.isChecked():
            scrollbar = self.text.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    def eventFilter(self, obj, event):
        if obj is self.text.viewport() and event.type() == QEvent.Type.Resize:
            QTimer.singleShot(0, self, self._follow)
        return super().eventFilter(obj, event)
