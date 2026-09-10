"""Resizable native page surfaces; no changes to automation state or UI font scale."""
import json
from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer
from PySide6.QtWidgets import QBoxLayout, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QSplitter
from auto_bdsp_rng.ui.terminology import show_terminology


class _LayoutMemory:
    def __init__(self):
        self.values = {}

    def value(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value


def scroll_surface(widget):
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setMinimumSize(0, 0)
    area.setWidget(widget)
    return area


class WorkspaceSplit(QSplitter):
    def __init__(self, settings, key, *, breakpoint=1000, horizontal=(326, 800), vertical=(260, 500), orientation=Qt.Orientation.Horizontal):
        super().__init__(orientation)
        self.settings, self.key = settings if settings is not None else _LayoutMemory(), "workspace_layout/" + key
        self.breakpoint = breakpoint
        self.horizontal_sizes = horizontal
        self.vertical_sizes = vertical
        self._adapting = False
        self.setChildrenCollapsible(False)
        self.setHandleWidth(6)
        self.setStyleSheet("QSplitter::handle { background: #F0F2F5; } QSplitter::handle:hover { background: #C8E5D9; }")
        self.splitterMoved.connect(self._save_sizes)

    def minimumSizeHint(self):
        return QSize(0, 0)

    def _size_key(self):
        return self.key + ("/vertical" if self.orientation() == Qt.Orientation.Vertical else "/horizontal")

    def _save_sizes(self, *_args):
        if not self._adapting and all(self.sizes()):
            self.settings.setValue(self._size_key(), json.dumps(self.sizes()))

    def restore_sizes(self):
        default = self.horizontal_sizes if self.orientation() == Qt.Orientation.Horizontal else self.vertical_sizes
        try:
            values = json.loads(str(self.settings.value(self._size_key(), json.dumps(default))))
            if len(values) != self.count() or any(type(v) is not int or v <= 0 for v in values):
                values = default
        except (ValueError, TypeError):
            values = default
        self.setSizes(list(values))

    def adapt(self):
        if not self.breakpoint:
            return
        orientation = Qt.Orientation.Vertical if self.width() < self.breakpoint else Qt.Orientation.Horizontal
        if orientation != self.orientation():
            self._adapting = True
            self.setOrientation(orientation)
            self.restore_sizes()
            self._adapting = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.adapt()

    def showEvent(self, event):
        super().showEvent(event)
        self.adapt()


class ColumnReflow(QObject):
    def __init__(self, area, layout, breakpoint=1040):
        super().__init__(area)
        self.area, self.layout, self.breakpoint = area, layout, breakpoint
        area.viewport().installEventFilter(self)
        self.refresh()

    def refresh(self):
        self.layout.setDirection(QBoxLayout.Direction.TopToBottom if self.area.viewport().width() < self.breakpoint else QBoxLayout.Direction.LeftToRight)

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.refresh()
        return False


class PageHeader(QFrame):
    def __init__(self, title, page, *, log_callback=None, configuration=None, settings=None, key=""):
        super().__init__(page)
        self.configuration, self.settings, self.key = configuration, settings, "workspace_layout/" + key + "/folded"
        self.setObjectName("WorkspacePageHeader")
        self.setStyleSheet("QFrame#WorkspacePageHeader { background: white; border-bottom: 1px solid #F0F2F5; } QPushButton { padding: 4px 8px; border: 0; color: #52606D; } QPushButton:hover { color: #087C58; background: #F2F4F7; }")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 6, 16, 6)
        layout.setSpacing(8)
        self.title = QLabel(title)
        self.title.setStyleSheet("font-size: 16px; font-weight: 500; color: #202A33;")
        layout.addWidget(self.title)
        self.state = QLabel()
        self.state.setStyleSheet("font-size: 12px; color: #626D79;")
        self.state.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._status = ""
        layout.addWidget(self.state, 1)
        self.fold_button = QPushButton("收起配置")
        self.fold_button.setCheckable(True)
        self.fold_button.toggled.connect(self.set_folded)
        self.fold_button.setVisible(configuration is not None)
        layout.addWidget(self.fold_button)
        if log_callback is not None:
            self.logs_button = QPushButton("日志中心")
            self.logs_button.clicked.connect(lambda _checked=False: log_callback())
            layout.addWidget(self.logs_button)
        self.help_button = QPushButton("帮助")
        self.help_button.clicked.connect(lambda: show_terminology(page))
        layout.addWidget(self.help_button)
        if settings is not None and str(settings.value(self.key, "false")).lower() == "true":
            self.fold_button.setChecked(True)

    def set_folded(self, folded):
        if self.configuration is not None:
            self.configuration.setVisible(not folded)
        self.fold_button.setText("展开配置" if folded else "收起配置")
        if self.settings is not None:
            self.settings.setValue(self.key, folded)

    def reveal_configuration(self):
        self.fold_button.setChecked(False)

    def set_status(self, status):
        self._status = str(status)
        self.state.setToolTip(self._status)
        self.state.setText(self.state.fontMetrics().elidedText(self._status, Qt.TextElideMode.ElideRight, self.state.width()))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.set_status(self._status)
