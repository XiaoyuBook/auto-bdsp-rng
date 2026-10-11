"""Round history workspace; diagnostic logs use a separate window."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from auto_bdsp_rng.ui.history_panel import HistoryPanel
from auto_bdsp_rng.ui.workspace_theme import ui_styles


class RunRecordsPanel(QWidget):
    """Present existing round results without nested log navigation."""

    def __init__(self, history_panel: HistoryPanel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("RunRecordsPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.history_panel = history_panel
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(10)
        self.heading = QFrame(self)
        self.heading.setObjectName("RunRecordsHeading")
        self.heading.setFixedHeight(34)
        heading = QHBoxLayout(self.heading)
        heading.setContentsMargins(0, 0, 0, 0)
        heading.setSpacing(10)
        self.title_label = QLabel("轮次记录", self.heading)
        self.title_label.setObjectName("RunRecordsTitle")
        self.subtitle_label = QLabel("本次会话", self.heading)
        self.subtitle_label.setObjectName("RunRecordsSubtitle")
        self.live_status_label = QLabel("等待任务", self.heading)
        self.live_status_label.setObjectName("RunRecordsLiveStatus")
        heading.addWidget(self.title_label)
        heading.addWidget(self.subtitle_label)
        heading.addStretch(1)
        heading.addWidget(self.live_status_label)
        root.addWidget(self.heading)
        root.addWidget(self.history_panel, 1)
        self.setStyleSheet(ui_styles("""
            QWidget#RunRecordsPanel { background: #F2F4F7; }
            QFrame#RunRecordsHeading { background: transparent; border: 0; }
            QLabel#RunRecordsTitle { color: #202A33; font-size: 15px; font-weight: 500; }
            QLabel#RunRecordsSubtitle { color: #687480; font-size: 12px; }
            QLabel#RunRecordsLiveStatus { color: #087C58; background: transparent; font-size: 12px; }
        """))

    def set_session_context(self, source: str, target: str = "") -> None:
        details = [str(source).strip(), str(target).strip()]
        self.subtitle_label.setText(" · ".join(part for part in details if part) or "本次会话")
        self.live_status_label.setToolTip(self.subtitle_label.text())

    def set_active_round(self, round_id: int | None) -> None:
        self.live_status_label.setText("准备中" if round_id is None else f"第 {int(round_id)} 轮进行中")

    def set_run_finished(self, text: str = "运行已结束") -> None:
        self.live_status_label.setText(str(text) or "运行已结束")

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.subtitle_label.setVisible(self.width() >= 560)
