"""Non-interactive empty guidance confined to a real table's viewport."""

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from auto_bdsp_rng.ui.workspace_controls import workspace_icon


class TableEmptyState(QWidget):
    def __init__(self, table) -> None:
        super().__init__(table.viewport())
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet("background: transparent; border: 0;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addStretch()
        self.illustration = QLabel()
        self.illustration.setFixedSize(32, 32)
        self.illustration.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.illustration.setPixmap(workspace_icon("empty", "#94A5B2").pixmap(QSize(32, 32), self.devicePixelRatioF()))
        layout.addWidget(self.illustration, 0, Qt.AlignmentFlag.AlignHCenter)
        self.title = QLabel()
        self.detail = QLabel()
        for label, size in ((self.title, 14), (self.detail, 12)):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setWordWrap(True)
            label.setStyleSheet(f"color: #687480; font-size: {size}px; background: transparent; border: 0;")
            layout.addWidget(label)
        layout.addStretch()
        table.viewport().installEventFilter(self)
        self.setGeometry(table.viewport().rect())

    def show_message(self, title: str, detail: str, *, has_results: bool) -> None:
        self.title.setText(title)
        self.detail.setText(detail)
        self.setVisible(not has_results)
        self.raise_()

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.Resize:
            self.setGeometry(watched.rect())
        return False
