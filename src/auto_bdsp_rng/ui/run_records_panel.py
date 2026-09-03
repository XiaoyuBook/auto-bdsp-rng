"""Unified round history and live diagnostic log workspace."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from auto_bdsp_rng.ui.history_panel import HistoryPanel
from auto_bdsp_rng.ui.run_log_panel import RunLogBuffer, RunLogPanel


_UNREAD_LEVELS = {"WARNING", "ERROR", "CRITICAL"}


class RunRecordsPanel(QWidget):
    """Top-level workspace that keeps round summaries and logs together."""

    ROUND_TAB = 0
    LOG_TAB = 1

    def __init__(
        self,
        history_panel: HistoryPanel,
        log_buffer: RunLogBuffer,
        *,
        save_enabled: bool = False,
        set_save_enabled: Callable[[bool], bool] | None = None,
        open_log_dir: Callable[[], object] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.history_panel = history_panel
        self._unread_problem_count = sum(
            entry.level in _UNREAD_LEVELS for entry in log_buffer.snapshot()
        )
        self._build_ui(
            log_buffer,
            save_enabled=save_enabled,
            set_save_enabled=set_save_enabled,
            open_log_dir=open_log_dir,
        )
        self.history_panel.related_logs_requested.connect(self.show_round_logs)
        log_buffer.entryAdded.connect(
            self._on_log_entry_added,
            Qt.ConnectionType.QueuedConnection,
        )

    def _build_ui(
        self,
        log_buffer: RunLogBuffer,
        *,
        save_enabled: bool,
        set_save_enabled: Callable[[bool], bool] | None,
        open_log_dir: Callable[[], object] | None,
    ) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        heading = QFrame(self)
        heading.setObjectName("RunRecordsHeading")
        self.heading = heading
        heading.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        heading.setFixedHeight(42)
        heading_layout = QHBoxLayout(heading)
        heading_layout.setContentsMargins(2, 0, 2, 0)
        heading_layout.setSpacing(10)

        self.title_label = QLabel("日志区", heading)
        self.title_label.setObjectName("RunRecordsTitle")
        heading_layout.addWidget(self.title_label, 0, Qt.AlignmentFlag.AlignVCenter)
        divider = QFrame(heading)
        divider.setObjectName("RunRecordsDivider")
        divider.setFixedSize(1, 18)
        heading_layout.addWidget(divider, 0, Qt.AlignmentFlag.AlignVCenter)
        self.subtitle_label = QLabel("本次会话", heading)
        self.subtitle_label.setObjectName("RunRecordsSubtitle")
        heading_layout.addWidget(self.subtitle_label, 0, Qt.AlignmentFlag.AlignVCenter)
        heading_layout.addStretch(1)

        self.live_status_label = QLabel("等待任务", heading)
        self.live_status_label.setObjectName("RunRecordsLiveStatus")
        self.live_status_label.setFixedHeight(28)
        self.live_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        heading_layout.addWidget(self.live_status_label, 0, Qt.AlignmentFlag.AlignVCenter)
        root.addWidget(heading)

        self.view_tabs = QTabWidget(self)
        self.view_tabs.setObjectName("RunRecordsTabs")
        self.view_tabs.setDocumentMode(True)
        self.view_tabs.setUsesScrollButtons(False)
        self.log_panel = RunLogPanel(
            log_buffer,
            save_enabled=save_enabled,
            set_save_enabled=set_save_enabled,
            open_log_dir=open_log_dir,
            parent=self.view_tabs,
        )
        self.view_tabs.addTab(self.history_panel, "轮次记录")
        self.view_tabs.addTab(self.log_panel, "详细日志")
        self.view_tabs.currentChanged.connect(self._on_view_changed)
        root.addWidget(self.view_tabs, 1)

        # The application-wide tab rules target every QTabWidget. Keep this
        # nested switch visually lighter so it reads as a view selector.
        self.setStyleSheet(
            """
            QFrame#RunRecordsHeading {
                background: transparent;
                border: 0;
            }
            QLabel#RunRecordsTitle {
                color: #202225;
                font-size: 18px;
                font-weight: 600;
            }
            QFrame#RunRecordsDivider {
                background: #D9DEE4;
                border: 0;
            }
            QLabel#RunRecordsSubtitle {
                color: #6B7280;
                font-size: 12px;
            }
            QLabel#RunRecordsLiveStatus {
                color: #176F5C;
                background: #ECFDF5;
                border: 1px solid #A7F3D0;
                border-radius: 4px;
                padding: 0 9px;
                font-size: 12px;
                font-weight: 600;
            }
            QTabWidget#RunRecordsTabs::pane {
                border: 0;
                border-top: 1px solid #DFE3E8;
                background: transparent;
                top: -1px;
            }
            QTabWidget#RunRecordsTabs > QTabBar::tab {
                min-width: 96px;
                min-height: 34px;
                margin: 0 18px 0 0;
                padding: 0 2px 6px 2px;
                border: 0;
                border-bottom: 2px solid transparent;
                border-radius: 0;
                color: #66707D;
                background: transparent;
                font-weight: 500;
            }
            QTabWidget#RunRecordsTabs > QTabBar::tab:selected {
                color: #202225;
                border-bottom-color: #0E8F70;
                background: transparent;
            }
            QTabWidget#RunRecordsTabs > QTabBar::tab:hover:!selected {
                color: #176F5C;
                background: transparent;
            }
            """
        )
        self._refresh_log_tab_text()

    def set_session_context(self, source: str, target: str = "") -> None:
        details = [str(source).strip()]
        if str(target).strip():
            details.append(str(target).strip())
        self.subtitle_label.setText(" · ".join(part for part in details if part) or "本次会话")

    def set_active_round(self, round_id: int | None) -> None:
        if round_id is None:
            self.live_status_label.setText("准备中")
        else:
            self.live_status_label.setText(f"第 {int(round_id)} 轮进行中")

    def set_run_finished(self, text: str = "运行已结束") -> None:
        self.live_status_label.setText(str(text) or "运行已结束")

    def show_logs(self, source: str | None = None) -> None:
        self.log_panel.clear_round_filter()
        self.log_panel.set_source_filter(source)
        self.view_tabs.setCurrentIndex(self.LOG_TAB)

    @Slot(object, object)
    def show_round_logs(self, run_id: object, round_id: object) -> None:
        self.log_panel.set_source_filter(None)
        self.log_panel.show_round_logs(run_id, round_id)
        self.view_tabs.setCurrentIndex(self.LOG_TAB)

    def set_save_enabled(self, enabled: bool) -> None:
        self.log_panel.set_save_enabled(enabled)

    @Slot(int)
    def _on_view_changed(self, index: int) -> None:
        if index == self.LOG_TAB:
            self._mark_logs_read_if_visible()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self.view_tabs.currentIndex() == self.LOG_TAB:
            self._mark_logs_read_if_visible()

    def _mark_logs_read_if_visible(self) -> None:
        if not self.log_panel.isVisible() or self._unread_problem_count == 0:
            return
        self._unread_problem_count = 0
        self._refresh_log_tab_text()

    @Slot(object)
    def _on_log_entry_added(self, entry: object) -> None:
        if (
            not self.log_panel.isVisible()
            and str(getattr(entry, "level", "INFO")).upper() in _UNREAD_LEVELS
        ):
            self._unread_problem_count += 1
            self._refresh_log_tab_text()

    def _refresh_log_tab_text(self) -> None:
        text = "详细日志"
        if self._unread_problem_count:
            text += f" ({self._unread_problem_count})"
        self.view_tabs.setTabText(self.LOG_TAB, text)
