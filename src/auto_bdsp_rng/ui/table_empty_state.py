"""Empty guidance with an optional, independently clickable next action."""

from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtWidgets import QLabel, QLayout, QPushButton, QVBoxLayout, QWidget

from auto_bdsp_rng.ui.workspace_controls import EmptyIllustration


class TableEmptyState(QWidget):
    def __init__(self, table, *, symbol: str = "empty") -> None:
        super().__init__(table.viewport())
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet("background: transparent; border: 0;")
        layout = QVBoxLayout(self)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addStretch()
        self.illustration = EmptyIllustration(symbol)
        layout.addWidget(self.illustration, 0, Qt.AlignmentFlag.AlignHCenter)
        self.title = QLabel()
        self.detail = QLabel()
        for label, size in ((self.title, 14), (self.detail, 12)):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setWordWrap(True)
            label.setStyleSheet(f"color: #687480; font-size: {size}px; background: transparent; border: 0;")
            layout.addWidget(label)
        self._action_space = QWidget()
        self._action_space.setFixedHeight(36)
        self._action_space.hide()
        layout.addWidget(self._action_space)
        # A sibling leaves the decorative overlay mouse-transparent, including
        # table selection and wheel events outside the actual button.
        self.action_button = QPushButton(table.viewport())
        self.action_button.setObjectName("EmptyStateAction")
        self.action_button.setStyleSheet(
            "QPushButton { background: #FFFFFF; color: #087C58; border: 1px solid #DCE5E0;"
            " border-radius: 7px; padding: 0 14px; min-height: 32px; }"
            "QPushButton:hover { background: #EAF7F1; }"
        )
        self.action_button.hide()
        self._action_callback = None
        self._has_results = False
        self.action_button.clicked.connect(self._trigger_action)
        layout.addStretch()
        table.viewport().installEventFilter(self)
        self.setGeometry(table.viewport().rect())

    def show_message(self, title: str, detail: str, *, has_results: bool) -> None:
        self.title.setText(title)
        self.detail.setText(detail)
        self._has_results = has_results
        self.setVisible(not has_results)
        self.raise_()
        self._sync_action_geometry()

    def set_action(self, text: str = "", callback=None) -> None:
        self._action_callback = callback
        self.action_button.setText(text)
        self._action_space.setVisible(bool(text and callback))
        self.layout().activate()
        self._sync_action_geometry()

    def _trigger_action(self) -> None:
        if self._action_callback is not None:
            self._action_callback()

    def _sync_action_geometry(self) -> None:
        show = bool(self.action_button.text() and self._action_callback and not self._has_results)
        self.setGeometry(self.parentWidget().rect())
        self.illustration.setVisible(self.height() >= (200 if show else 140))
        self.action_button.setVisible(show)
        if show:
            self.layout().activate()
            size = self.action_button.sizeHint()
            self.action_button.resize(size.width(), 34)
            self.action_button.move((self.width() - size.width()) // 2, self._action_space.geometry().top())
            self.action_button.raise_()

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.Resize:
            self.setGeometry(watched.rect())
            QTimer.singleShot(0, self._sync_action_geometry)
        return False
