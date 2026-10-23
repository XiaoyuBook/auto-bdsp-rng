"""Compact session log for the persistent video sidebar."""

from PySide6.QtCore import QEvent, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QColor, QFont, QTextBlockFormat, QTextCharFormat, QTextCursor, QTextFormat, QTextOption
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QTextEdit, QToolButton, QVBoxLayout, QWidget,
)

from auto_bdsp_rng.ui.check_box import CheckmarkCheckBox as QCheckBox
from auto_bdsp_rng.ui.combo_box import ChevronComboBox as QComboBox
from auto_bdsp_rng.ui.run_log_panel import RunLogBuffer, RunLogEntry
from auto_bdsp_rng.ui.workspace_controls import configure_disclosure_button, set_disclosure_state, workspace_icon
from auto_bdsp_rng.ui.workspace_theme import ui_font, ui_styles


class _LiveLogText(QTextEdit):
    """Selectable native text, with a quiet metadata line above each summary."""

    _META_ROLE = int(QTextFormat.Property.UserProperty) + 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setAcceptRichText(False)
        self.setUndoRedoEnabled(False)
        self.setFont(ui_font(13))
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.document().setDocumentMargin(0)
        option = self.document().defaultTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.document().setDefaultTextOption(option)
        self._layout_timer = QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.timeout.connect(self._align_timestamps)

    def schedule_layout(self) -> None:
        if not self._layout_timer.isActive():
            self._layout_timer.start(0)

    def _metadata_format(self, *, first: bool = False) -> QTextBlockFormat:
        block = QTextBlockFormat()
        block.setProperty(self._META_ROLE, True)
        block.setLeftMargin(4)
        block.setRightMargin(4)
        block.setTopMargin(3 if first else 8)
        block.setBottomMargin(3)
        tab = QTextOption.Tab()
        tab.position = max(80, self.viewport().width() - 12)
        tab.type = QTextOption.TabType.RightTab
        block.setTabPositions([tab])
        return block

    @staticmethod
    def _text_format(size: int, color: str, *, medium: bool = False) -> QTextCharFormat:
        result = QTextCharFormat()
        result.setFont(ui_font(size, QFont.Weight.Medium if medium else QFont.Weight.Normal))
        result.setForeground(QColor(color))
        return result

    def set_entries(self, entries: list[RunLogEntry]) -> None:
        self.clear()
        self.append_entries(entries)

    def append_entries(self, entries: list[RunLogEntry]) -> None:
        if not entries:
            return
        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.beginEditBlock()
        for entry in entries:
            first = self.document().isEmpty()
            if not first:
                cursor.insertBlock()
            cursor.setBlockFormat(self._metadata_format(first=first))
            tone = {"WARNING": "#A57126", "ERROR": "#B55349", "CRITICAL": "#B55349"}.get(entry.level, "#A5B1AB")
            cursor.insertText("●  ", self._text_format(8, tone))
            cursor.insertText(entry.source_label, self._text_format(11, "#7A858F"))
            problem = {"WARNING": "警告", "ERROR": "错误", "CRITICAL": "严重错误"}.get(entry.level)
            if problem:
                cursor.insertText(f"  ·  {problem}", self._text_format(11, tone, medium=True))
            timestamp = entry.last_timestamp or entry.timestamp
            cursor.insertText(f"\t{timestamp:%H:%M:%S}", self._text_format(10, "#929BA4"))
            message_block = QTextBlockFormat()
            message_block.setLeftMargin(14)
            message_block.setRightMargin(6)
            message_block.setBottomMargin(10)
            cursor.insertBlock(message_block)
            cursor.insertText(entry.display_message, self._text_format(
                13, "#954A44" if entry.level in {"ERROR", "CRITICAL"} else "#293640",
            ))
        cursor.endEditBlock()

    def _align_timestamps(self) -> None:
        cursor = QTextCursor(self.document())
        cursor.beginEditBlock()
        block = self.document().begin()
        while block.isValid():
            if block.blockFormat().property(self._META_ROLE):
                cursor.setPosition(block.position())
                cursor.setBlockFormat(self._metadata_format(first=block.blockNumber() == 0))
            block = block.next()
        cursor.endEditBlock()


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
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)
        heading = QHBoxLayout()
        heading.setSpacing(7)
        self.collapse_button = QToolButton()
        self.collapse_button.setText("实时日志")
        self.collapse_button.setCheckable(True)
        self.collapse_button.setChecked(True)
        configure_disclosure_button(self.collapse_button, compact=True)
        self.collapse_button.setStyleSheet(ui_styles("""
            QToolButton {
                background: transparent; color: #35434D; border: 0;
                border-radius: 5px; min-height: 24px; padding: 0 2px;
                font-size: 13px; font-weight: 500;
            }
            QToolButton:hover { background: #F1F6F3; }
            QToolButton:focus { background: #EAF4EE; }
        """))
        set_disclosure_state(self.collapse_button, True)
        self.collapse_button.setAccessibleName("折叠实时日志")
        self.collapse_button.setToolTip("折叠实时日志，消息继续收集")
        self.count_label = QLabel("0 条")
        self.count_label.setObjectName("LiveLogCount")
        self.expand_button = QPushButton("查看完整日志")
        self.expand_button.setObjectName("InlineLinkButton")
        self.expand_button.setFixedHeight(24)
        self.expand_button.setStyleSheet(ui_styles("""
            QPushButton {
                background: transparent; color: #748278; border: 0; border-radius: 4px;
                min-height: 22px; max-height: 22px; padding: 0 3px; font-size: 11px;
            }
            QPushButton:hover { background: #F0F6F2; color: #087C58; }
            QPushButton:focus { color: #087C58; background: #EAF4EE; }
        """))
        self.expand_button.setToolTip("打开完整日志，查看轮次、搜索或导出")
        heading.addWidget(self.collapse_button)
        heading.addWidget(self.count_label)
        self.problem_label = QLabel()
        self.problem_label.setObjectName("LiveLogProblems")
        self.problem_label.setToolTip("完整日志尚未查看的警告或错误")
        self.problem_label.hide()
        heading.addWidget(self.problem_label)
        heading.addStretch(1)
        heading.addWidget(self.expand_button)
        layout.addLayout(heading)

        self.body = QWidget()
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(8)
        filters = QHBoxLayout()
        self.source_combo = QComboBox()
        self.source_combo.setAccessibleName("监看日志来源")
        self.source_combo.addItem("全部来源", None)
        self.source_combo.setObjectName("LiveLogSource")
        self.source_combo.setMinimumWidth(0)
        self.source_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.source_combo.setMinimumContentsLength(5)
        self.source_combo.setMaximumWidth(154)
        self.source_combo.setFixedHeight(28)
        self.follow_check = QCheckBox("自动跟随")
        self.follow_check.setChecked(True)
        filters.addWidget(self.source_combo)
        filters.addStretch(1)
        filters.addWidget(self.follow_check)
        body_layout.addLayout(filters)

        self.text = _LiveLogText(self)
        self.text.setObjectName("LiveLogText")
        self.text.setAccessibleName("实时运行日志")
        # Exactly two paragraphs per event keep whole messages within the
        # retention limit, including when one paragraph wraps to several lines.
        self.text.document().setMaximumBlockCount(self.DISPLAY_LIMIT * 2)
        self.text.setMinimumSize(0, 0)
        self.text.setToolTip("显示最近 300 条运行摘要；可通过“查看完整日志”搜索、查看详情或导出。")
        self.empty_state = QWidget(self.text.viewport())
        self.empty_state.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        empty_layout = QVBoxLayout(self.empty_state)
        empty_layout.setContentsMargins(8, 0, 8, 0)
        empty_layout.setSpacing(7)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_icon = QLabel()
        ratio = self.devicePixelRatioF()
        pixmap = workspace_icon("journal", "#BBC6C0").pixmap(round(28 * ratio), round(28 * ratio))
        pixmap.setDevicePixelRatio(ratio)
        self.empty_icon.setPixmap(pixmap)
        self.empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_title = QLabel("等待运行消息")
        self.empty_title.setObjectName("LiveLogEmptyTitle")
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_hint = QLabel("任务进展和需要处理的情况会显示在这里")
        self.empty_hint.setObjectName("LiveLogEmptyHint")
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_hint.setWordWrap(True)
        for widget in (self.empty_icon, self.empty_title, self.empty_hint):
            empty_layout.addWidget(widget)
        self.text.viewport().installEventFilter(self)
        body_layout.addWidget(self.text, 1)
        layout.addWidget(self.body, 1)
        self.collapse_button.toggled.connect(lambda expanded: self.set_collapsed(not expanded))
        self.expand_button.clicked.connect(lambda: self.expandRequested.emit(self.source_combo.currentData()))
        self.source_combo.currentIndexChanged.connect(self._refresh)
        self.follow_check.toggled.connect(self._follow)
        buffer.entryAdded.connect(self._receive, Qt.ConnectionType.QueuedConnection)
        buffer.entryUpdated.connect(self._update_entry, Qt.ConnectionType.QueuedConnection)
        self.setStyleSheet(ui_styles("""
            QFrame#LiveLogPanel {
                background: #FFFFFF; border: 1px solid #E3E9E6; border-radius: 9px;
            }
            QLabel#LiveLogCount { color: #9AA39F; font-size: 11px; }
            QLabel#LiveLogProblems {
                color: #A56C32; background: #FAF3E9; border-radius: 4px;
                font-size: 10px; padding: 2px 5px;
            }
            QComboBox#LiveLogSource {
                background: #F5F7F6; color: #657269; border: 1px solid transparent;
                border-radius: 5px; min-height: 26px; max-height: 26px;
                padding: 0 28px 0 9px; font-size: 11px;
            }
            QComboBox#LiveLogSource:hover { background: #EEF3F0; border-color: #E0E8E3; }
            QComboBox#LiveLogSource:focus { border-color: #A6C7B6; }
            QComboBox#LiveLogSource::drop-down { border: 0; width: 26px; }
            QComboBox#LiveLogSource::down-arrow { image: none; width: 0; height: 0; }
            QComboBox#LiveLogSource QAbstractItemView {
                background: #FFFFFF; color: #35434D; border: 1px solid #E1E8E3;
                selection-background-color: #EAF4EE; selection-color: #087C58;
                padding: 4px; outline: 0;
            }
            QCheckBox { color: #7D8981; font-size: 11px; spacing: 6px; }
            QTextEdit#LiveLogText {
                background: #FFFFFF; color: #293640; border: 0; border-radius: 0;
                padding: 0; selection-background-color: #DCEFE7; selection-color: #202A33;
            }
            QTextEdit#LiveLogText QScrollBar:vertical {
                background: transparent; width: 5px; margin: 0;
            }
            QTextEdit#LiveLogText QScrollBar::handle:vertical {
                background: #D9E2DD; border-radius: 2px; min-height: 22px;
            }
            QTextEdit#LiveLogText QScrollBar::handle:vertical:hover { background: #BFCFC5; }
            QTextEdit#LiveLogText QScrollBar::add-line:vertical,
            QTextEdit#LiveLogText QScrollBar::sub-line:vertical { height: 0; }
            QTextEdit#LiveLogText QScrollBar::add-page:vertical,
            QTextEdit#LiveLogText QScrollBar::sub-page:vertical { background: transparent; }
            QLabel#LiveLogEmptyTitle { color: #87978D; font-size: 12px; }
            QLabel#LiveLogEmptyHint { color: #A1ADA6; font-size: 11px; }
        """))
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
        self.problem_label.setText(f"{count} 条待查看")
        self.problem_label.setVisible(count > 0)

    def _sync_empty_state(self) -> None:
        self.empty_state.setGeometry(self.text.viewport().rect())
        roomy = self.text.viewport().height() >= 100
        self.empty_icon.setVisible(roomy)
        self.empty_hint.setVisible(roomy)
        self.empty_title.setText("暂无该来源的消息" if self.source_combo.currentData() else "等待运行消息")
        self.empty_state.setVisible(self._visible_count == 0)

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
        self.text.set_entries(selected[-self.DISPLAY_LIMIT:])
        self.count_label.setText(f"{self._visible_count} 条")
        self._sync_empty_state()
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
        self.text.append_entries(new_entries[-self.DISPLAY_LIMIT:])
        self._sync_empty_state()
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
            self.text.schedule_layout()
            self._sync_empty_state()
            QTimer.singleShot(0, self, self._follow)
        return super().eventFilter(obj, event)
