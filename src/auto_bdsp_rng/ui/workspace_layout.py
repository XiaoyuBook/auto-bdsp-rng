"""Resizable native page surfaces; no changes to automation state or UI font scale."""
import json
from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import QBoxLayout, QFrame, QHBoxLayout, QScrollArea, QSizePolicy, QSplitter, QStackedWidget, QTabBar, QTabWidget, QWidget


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
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setWidget(widget)
    return area


class WorkspacePages(QStackedWidget):
    """One page stack with navigation mounted above the whole workspace."""

    def __init__(self):
        super().__init__()
        self.navigation = QWidget()
        self.navigation.setObjectName("WorkspaceNavigation")
        self.navigation.setFixedHeight(40)
        self.navigation_layout = QHBoxLayout(self.navigation)
        self.navigation_layout.setContentsMargins(8, 0, 8, 0)
        self.navigation_layout.setSpacing(8)
        self._bar = QTabBar()
        self._bar.setObjectName("WorkspaceNavigationBar")
        self._bar.setUsesScrollButtons(False)
        self._bar.setExpanding(False)
        self._bar.setDrawBase(False)
        self.navigation_layout.addWidget(self._bar)
        self.navigation_layout.addStretch(1)
        self._bar.currentChanged.connect(self.setCurrentIndex)
        self.currentChanged.connect(self._bar.setCurrentIndex)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)

    def minimumSizeHint(self):
        return QSize(0, 0)

    def addTab(self, widget, text):
        index = self.addWidget(widget)
        self._bar.addTab(text)
        return index

    def tabBar(self):
        return self._bar

    def setTabText(self, index, text):
        self._bar.setTabText(index, text)

    def tabText(self, index):
        return self._bar.tabText(index)

    def setCornerWidget(self, widget, _corner=None):
        self.navigation_layout.addWidget(widget)


class LocalViews(QTabWidget):
    """Switch views without recreating controls, drafts, tables or scrollbars."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("LocalWorkspaceViews")
        self.setDocumentMode(True)
        self.setUsesScrollButtons(False)
        self.tabBar().setExpanding(False)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setStyleSheet("""
            QTabWidget#LocalWorkspaceViews::pane { border: 0; }
            QTabWidget#LocalWorkspaceViews > QTabBar::tab {
                background: transparent; color: #687480; padding: 8px 14px;
                border: 0; border-bottom: 2px solid transparent; font-size: 13px;
            }
            QTabWidget#LocalWorkspaceViews > QTabBar::tab:selected {
                color: #087C58; border-bottom-color: #087C58; background: #EAF7F1;
            }
        """)

    def minimumSizeHint(self):
        return QSize(0, 0)


class MonitorWorkspaceSplit(QSplitter):
    """Responsive defaults until the user drags; v2 preserves custom layouts."""

    def __init__(self, settings):
        super().__init__(Qt.Orientation.Horizontal)
        self.settings = settings
        self.key = "workspace_layout/monitor-v2/horizontal"
        self.setChildrenCollapsible(False)
        self.setHandleWidth(6)
        self._applying = False
        self._custom = False
        self.setStyleSheet("QSplitter::handle { background: #F0F2F5; } QSplitter::handle:hover { background: #C8E5D9; }")
        self.splitterMoved.connect(self._save_sizes)

    def minimumSizeHint(self):
        return QSize(0, 0)

    def restore_sizes(self):
        raw = self.settings.value(self.key)
        if raw is None:
            # Only migrate genuinely wider old preferences. The old defaults
            # and narrow right columns must receive the new responsive layout.
            raw = self.settings.value("workspace_layout/monitor/horizontal")
            try:
                old = json.loads(str(raw))
                raw = raw if len(old) == 2 and old[1] >= 390 else None
            except (ValueError, TypeError):
                raw = None
        try:
            sizes = json.loads(str(raw))
            self._custom = len(sizes) == 2 and all(type(v) is int and v > 0 for v in sizes)
        except (ValueError, TypeError):
            self._custom = False
        if self._custom:
            self.setSizes(sizes)
            self.settings.setValue(self.key, json.dumps(sizes))
        else:
            self._apply_default()
        self.settings.setValue("workspace_layout/version", 2)

    def _apply_default(self):
        if self.count() != 2 or self._custom:
            return
        available = self.width() - self.handleWidth()
        right = min(720, max(400, round(available * .445)))
        self._applying = True
        self.setSizes([max(0, available - right), right])
        self._applying = False

    def _save_sizes(self, *_args):
        if not self._applying and all(self.sizes()):
            self._custom = True
            self.settings.setValue(self.key, json.dumps(self.sizes()))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_default()

    def showEvent(self, event):
        super().showEvent(event)
        self._apply_default()


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
    reflowed = Signal()

    def __init__(self, area, layout, breakpoint=1040):
        super().__init__(area)
        self.area, self.layout, self.breakpoint = area, layout, breakpoint
        area.viewport().installEventFilter(self)
        self.refresh()

    def refresh(self):
        direction = QBoxLayout.Direction.TopToBottom if self.area.viewport().width() < self.breakpoint else QBoxLayout.Direction.LeftToRight
        if self.layout.direction() != direction:
            self.layout.setDirection(direction)
            self.reflowed.emit()

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.refresh()
        return False


class ToolbarReflow(QObject):
    """Wrap toolbar groups when a persistent sidebar reduces page width."""

    def __init__(self, toolbar, layout):
        super().__init__(toolbar)
        self.toolbar, self.layout = toolbar, layout
        self._pending = False
        toolbar.installEventFilter(self)
        self.refresh()

    def refresh(self):
        self._pending = False
        margins = self.layout.contentsMargins()
        required = sum(self.layout.itemAt(i).sizeHint().width() for i in range(self.layout.count()))
        required += margins.left() + margins.right() + max(0, self.layout.count() - 1) * self.layout.spacing()
        stacked = self.toolbar.width() < required
        direction = QBoxLayout.Direction.TopToBottom if stacked else QBoxLayout.Direction.LeftToRight
        if self.layout.direction() != direction:
            self.layout.setDirection(direction)
        height = max(56, self.layout.sizeHint().height() + 16) if stacked else 56
        if self.toolbar.height() != height:
            self.toolbar.setFixedHeight(height)

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.LayoutRequest) and not self._pending:
            self._pending = True
            QTimer.singleShot(0, self.toolbar, self.refresh)
        return False
