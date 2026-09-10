"""Resizable native page surfaces; no changes to automation state or UI font scale."""
import json
from PySide6.QtCore import QEvent, QObject, QSize, Qt
from PySide6.QtWidgets import QBoxLayout, QFrame, QScrollArea, QSplitter


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
