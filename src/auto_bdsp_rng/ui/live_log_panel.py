"""Compact session log for the persistent video sidebar."""

from PySide6.QtCore import QEvent, QTimer, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout,
)

from auto_bdsp_rng.ui.check_box import CheckmarkCheckBox as QCheckBox
from auto_bdsp_rng.ui.combo_box import ChevronComboBox as QComboBox
from auto_bdsp_rng.ui.run_log_panel import RunLogBuffer, RunLogEntry


class LiveLogPanel(QFrame):
    """A bounded live view; filters never modify the shared session buffer."""

    expandRequested = Signal(object)
    DISPLAY_LIMIT = 300

    def __init__(self, buffer: RunLogBuffer, parent=None):
        super().__init__(parent)
        self.setObjectName("LiveLogPanel")
        self._buffer = buffer
        self._last_seq = 0
        self._visible_count = 0
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.timeout.connect(self._flush)
        self.setMinimumSize(0, 130)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        heading = QHBoxLayout()
        title = QLabel("日志中心")
        title.setObjectName("SectionTitle")
        self.count_label = QLabel("0 条")
        self.count_label.setObjectName("WorkspaceHint")
        self.expand_button = QPushButton("展开")
        self.expand_button.setObjectName("InlineLinkButton")
        self.expand_button.setFixedHeight(24)
        self.expand_button.setStyleSheet("QPushButton { min-height: 22px; max-height: 22px; }")
        self.expand_button.setToolTip("打开完整日志，查看轮次、搜索或导出")
        heading.addWidget(title)
        heading.addWidget(self.count_label)
        heading.addStretch(1)
        heading.addWidget(self.expand_button)
        layout.addLayout(heading)

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
        layout.addLayout(filters)

        self.text = QPlainTextEdit()
        self.text.setObjectName("LiveLogText")
        self.text.setAccessibleName("实时运行日志")
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(self.DISPLAY_LIMIT)
        self.text.setPlaceholderText("等待运行日志…\n捕捉 Seed、运行任务或连接设备后，消息会显示在这里。")
        self.text.setFrameShape(QFrame.Shape.NoFrame)
        self.text.setMinimumSize(0, 0)
        self.text.setToolTip("显示最近 300 行；完整日志可通过“展开”查看。")
        self.text.viewport().installEventFilter(self)
        layout.addWidget(self.text, 1)
        self.expand_button.clicked.connect(lambda: self.expandRequested.emit(self.source_combo.currentData()))
        self.source_combo.currentIndexChanged.connect(self._refresh)
        self.follow_check.toggled.connect(self._follow)
        buffer.entryAdded.connect(self._receive, Qt.ConnectionType.QueuedConnection)
        self._refresh()

    @staticmethod
    def _line(entry: RunLogEntry) -> str:
        level = {"INFO": "信息", "WARNING": "警告", "ERROR": "错误", "CRITICAL": "严重", "DEBUG": "调试"}.get(entry.level, entry.level)
        return f"{entry.timestamp:%H:%M:%S}  {entry.source} · {level}\n{entry.message}"

    def _matches(self, entry: RunLogEntry) -> bool:
        source = self.source_combo.currentData()
        return source is None or source == entry.source

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

    def _flush(self) -> None:
        entries = self._buffer.snapshot()
        source = self.source_combo.currentData()
        selected = [entry for entry in entries if source is None or entry.source == source]
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
