"""Shared presentation for automation parameters and script assignments."""
from PySide6.QtCore import QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QAbstractButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetItem,
)

from auto_bdsp_rng.ui.workspace_theme import workspace_styles
from auto_bdsp_rng.ui.combo_box import ChevronComboBox
from auto_bdsp_rng.ui.workspace_controls import workspace_icon


class TaskFieldGrid(QWidget):
    """Compact fields that wrap to the available automation sidebar width."""

    def __init__(self, parent=None, *, columns=2):
        super().__init__(parent)
        self._fields = []
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self._max_columns = columns
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(12)
        self.grid.setVerticalSpacing(12)
        self._columns = 0

    def add_field(self, field):
        self._fields.append(field)
        self._reflow(force=True)

    def _reflow(self, *, force=False):
        columns = max(1, min(self._max_columns, (self.width() + 12) // 200))
        if columns == self._columns and not force:
            return
        self._columns = columns
        for field in self._fields:
            self.grid.removeWidget(field)
        for index, field in enumerate(self._fields):
            self.grid.addWidget(field, index // columns, index % columns)
        for column in range(self._max_columns):
            self.grid.setColumnStretch(column, 1 if column < columns else 0)
        self.updateGeometry()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow()

    def minimumSizeHint(self):  # noqa: N802
        hint = super().minimumSizeHint()
        hint.setWidth(0)
        return hint


class _TaskPageStack(QWidget):
    """Keep each group's controls while sizing only the visible page."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current = None
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

    def addWidget(self, page):  # noqa: N802
        self._layout.addWidget(page)
        page.hide()

    def currentWidget(self):  # noqa: N802
        return self._current

    def setCurrentWidget(self, page):  # noqa: N802
        if self._current is not None:
            self._current.hide()
        self._current = page
        page.show()
        self.updateGeometry()


class TaskConfigurationGroups(QWidget):
    """Fixed configuration categories with one editable page at a time."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TaskConfigurationGroups")
        self.pages = {}
        self.buttons = {}
        self.navigation = QWidget()
        self.navigation.setObjectName("TaskGroupNavigation")
        self.navigation_layout = QGridLayout(self.navigation)
        self.navigation_layout.setContentsMargins(0, 0, 0, 0)
        self.navigation_layout.setSpacing(6)
        self.navigation.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.stack = _TaskPageStack()
        self.stack.setObjectName("TaskGroupStack")
        self.stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self.navigation)
        layout.addWidget(self.stack)

    def add_group(self, key, title):
        page = QWidget()
        page.setObjectName("TaskGroupPage")
        body = QVBoxLayout(page)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(14)
        body.setAlignment(Qt.AlignmentFlag.AlignTop)
        button = QToolButton()
        button.setObjectName("TaskGroupButton")
        button.setText(title)
        button.setCheckable(True)
        button.setMinimumHeight(32)
        button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setAccessibleName(title)
        button.clicked.connect(lambda _checked=False: self.select_group(key))
        self.pages[key] = page
        self.buttons[key] = button
        self.stack.addWidget(page)
        self._reflow()
        if len(self.pages) == 1:
            self.select_group(key)
        return body

    def select_group(self, key):
        if key not in self.pages:
            return
        self.stack.setCurrentWidget(self.pages[key])
        for name, button in self.buttons.items():
            button.setChecked(name == key)
        self.stack.updateGeometry()
        self.updateGeometry()

    def reveal(self, widget):
        for key, page in self.pages.items():
            if page is widget or page.isAncestorOf(widget):
                self.select_group(key)
                return

    def _reflow(self):
        columns = max(1, min(len(self.buttons), (self.width() + 6) // 114))
        if len(self.buttons) == 4 and columns == 3:
            columns = 2
        for button in self.buttons.values():
            self.navigation_layout.removeWidget(button)
        for index, button in enumerate(self.buttons.values()):
            self.navigation_layout.addWidget(button, index // columns, index % columns)
        for column in range(len(self.buttons)):
            self.navigation_layout.setColumnStretch(column, 1 if column < columns else 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow()


class TaskConfigBinding(QWidget):
    """A task-local view of the existing shared Project_Xs file selection."""

    editRequested = Signal(str)

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self._source = None
        self._refresh_pending = False
        self.combo = ChevronComboBox()
        self.combo.setAccessibleName(title)
        self.combo.setMinimumWidth(0)
        self.combo.setSizeAdjustPolicy(ChevronComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.combo.setMinimumContentsLength(8)
        self.combo.setFixedHeight(32)
        self.combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.combo.setEnabled(False)
        self.combo.currentIndexChanged.connect(self._selection_changed)
        self.edit_button = QToolButton()
        self.edit_button.setObjectName("TaskConfigEditButton")
        self.edit_button.setIcon(workspace_icon("square-pen", "#64707D"))
        self.edit_button.setFixedSize(32, 32)
        self.edit_button.setAccessibleName("编辑" + title)
        self.edit_button.setToolTip("在 Seed 捕捉页编辑同一份配置")
        self.edit_button.setEnabled(False)
        self.edit_button.clicked.connect(lambda: self.editRequested.emit(str(self.combo.currentData() or "")))
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(5)
        row.addWidget(self.combo, 1)
        row.addWidget(self.edit_button)

    def bind_source(self, source):
        self._source = source
        source.currentIndexChanged.connect(self.refresh)
        for signal in (source.model().modelReset, source.model().rowsInserted,
                       source.model().rowsRemoved, source.model().dataChanged):
            signal.connect(self._schedule_refresh)
        self.refresh()

    def _schedule_refresh(self, *_args):
        if self._refresh_pending:
            return
        self._refresh_pending = True
        QTimer.singleShot(0, self._finish_refresh)

    def _finish_refresh(self):
        self._refresh_pending = False
        self.refresh()

    def refresh(self, *_args):
        source = self._source
        if source is None:
            return
        with QSignalBlocker(self.combo):
            self.combo.clear()
            for index in range(source.count()):
                self.combo.addItem(source.itemText(index), source.itemData(index))
            self.combo.setCurrentIndex(source.currentIndex())
        self.combo.setEnabled(source.count() > 0)
        self.edit_button.setEnabled(bool(self.combo.currentData()))
        self.combo.setToolTip(str(self.combo.currentData() or source.toolTip()))

    def _selection_changed(self, index):
        if self._source is not None and index >= 0:
            self._source.setCurrentIndex(self._source.findData(self.combo.currentData()))
            self.refresh()


class TaskStrategyLayout(QVBoxLayout):
    """Modern stacked layout with the small QFormLayout compatibility surface."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._fields = {}
        self.setSpacing(8)

    def register(self, field, label, row=None):
        self._fields[field] = (label, row if row is not None else len(self._fields))

    def labelForField(self, field):  # noqa: N802
        label = self._fields.get(field, (None, None))[0]
        # The task cards apply the explanatory copy after the fields are
        # registered.  Keep the visible label and its control in sync when a
        # tooltip is assigned later in the build sequence.
        tooltip = field.toolTip()
        # A composite field such as the delay strategy exposes its live
        # estimate on the button tooltip.  Mirror that current explanation on
        # the visible field label as the old form API did.
        if isinstance(field, QWidget):
            button = field.findChild(QAbstractButton)
            if button is not None and button.toolTip():
                tooltip = button.toolTip()
        if label is not None and tooltip != label.toolTip():
            label.setToolTip(tooltip)
        return label

    def verticalSpacing(self):  # noqa: N802
        """Keep the small QFormLayout inspection API used by the UI tests."""
        return self.spacing()

    def getWidgetPosition(self, field):  # noqa: N802
        row = self._fields.get(field, (None, -1))[1]
        return row, QFormLayout.ItemRole.FieldRole

    def indexOf(self, widget):  # noqa: N802
        if widget in self._fields:
            return self._fields[widget][1]
        return super().indexOf(widget)

    def getItemPosition(self, index):  # noqa: N802
        for field, (_label, row) in self._fields.items():
            if row == index:
                return row, QFormLayout.ItemRole.FieldRole
        return index, QFormLayout.ItemRole.LabelRole

    def setRowVisible(self, field, visible):  # noqa: N802
        field.setVisible(bool(visible))
        label = self.labelForField(field)
        if label is not None:
            label.setVisible(bool(visible))


class TaskScriptLayout(QVBoxLayout):
    """Stacked script card layout with the former grid lookup surface.

    The script editor is intentionally a vertical card on narrow sidebars,
    while a few integrations still locate a script row by its old grid row.
    The compatibility items below are lightweight wrappers and do not take
    part in geometry management, so they cannot distort the new layout.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._legacy_items = {}
        self._legacy_indexes = {}
        self._legacy_widgets = []

    def register_legacy_position(self, row, column, widget, *, text=None, alignment=None):
        if text is not None:
            proxy = QLabel(text, self._compat_parent())
            proxy.setObjectName("ScriptLegacyTitle")
            proxy.hide()
            self._legacy_widgets.append(proxy)
            widget = proxy
        item = QWidgetItem(widget)
        if alignment is not None:
            item.setAlignment(alignment)
        self._legacy_items[(row, column)] = item
        self._legacy_indexes.setdefault(widget, 1000 + len(self._legacy_indexes))

    def _compat_parent(self):
        parent = self.parent()
        return parent if isinstance(parent, QWidget) else None

    def itemAtPosition(self, row, column):  # noqa: N802
        return self._legacy_items.get((row, column))

    def indexOf(self, widget):  # noqa: N802
        if widget in self._legacy_indexes:
            return self._legacy_indexes[widget]
        return super().indexOf(widget)


def section_header(title, state, save, toggle=None):
    header = QWidget()
    header.setObjectName("TaskSectionHeader")
    row = QHBoxLayout(header)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)
    label = QLabel(title)
    label.setObjectName("TaskSectionTitle")
    row.addWidget(label)
    row.addStretch(1)
    row.addWidget(state)
    row.addWidget(save)
    if toggle is not None:
        row.addWidget(toggle)
    return header


def parameter_field(title, control, hint):
    field = QWidget()
    field.setObjectName("TaskParameter")
    field.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
    layout = QVBoxLayout(field)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    layout.setAlignment(Qt.AlignmentFlag.AlignTop)
    label = QLabel(title)
    label.setObjectName("TaskFieldTitle")
    label.setBuddy(control)
    layout.addWidget(label)
    layout.addWidget(control)
    note = QLabel(hint)
    note.setObjectName("TaskFieldHint")
    note.setWordWrap(True)
    note.setVisible(bool(hint))
    layout.addWidget(note)
    return field


class ScriptAssignmentRow(QFrame):
    """A compact script field with its readiness and edit action together."""

    def __init__(self, number, title, description, picker):
        super().__init__()
        self.setObjectName("ScriptAssignmentRow")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.number = QLabel(number)
        self.number.setObjectName("ScriptStepNumber")
        self.number.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.number.setFixedSize(26, 26)
        self.title = QLabel(title)
        self.title.setObjectName("TaskFieldTitle")
        self.detail = QLabel(description)
        self.detail.setObjectName("TaskFieldHint")
        self.detail.setWordWrap(True)
        self.detail.setMinimumWidth(0)
        self.detail.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.requirement = QLabel()
        self.requirement.setObjectName("ScriptRequirement")
        self.heading = QWidget()
        labels = QHBoxLayout(self.heading)
        labels.setContentsMargins(0, 0, 0, 0)
        labels.setSpacing(6)
        labels.addWidget(self.title)
        labels.addWidget(self.requirement)
        labels.addStretch(1)
        self.picker = picker
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(10)
        self.grid.setVerticalSpacing(5)
        self.number.hide()
        self.detail.hide()
        self.grid.addWidget(self.heading, 0, 0)
        self.grid.addWidget(picker, 1, 0)
        self.setToolTip(description)

    def set_requirement(self, text, missing=False):
        self.requirement.setText(text)
        for widget in (self, self.requirement):
            if widget.property("missing") != missing:
                widget.setProperty("missing", missing)
                widget.style().unpolish(widget)
                widget.style().polish(widget)

def task_settings_styles():
    return workspace_styles('''
        QFrame[taskCard="true"] {
            background: $surface; border: 1px solid $card_border; border-radius: 10px;
        }
        QWidget#TaskSectionHeader, QWidget#TaskParameter { background: transparent; }
        QLabel#TaskSectionTitle { color: $text; font-size: 16px; font-weight: 500; }
        QLabel#TaskFieldTitle { color: $text; font-size: 13px; font-weight: 500; }
        QLabel#TaskFieldHint, QLabel#TaskSectionNote {
            color: $text_secondary; background: transparent; font-size: 11px; font-weight: 400;
        }
        QLabel#TaskSectionNote { padding-top: 2px; }
        QFrame#ScriptAssignmentRow {
            background: transparent; border: 0;
        }
        QWidget#TaskGroupNavigation, QWidget#TaskGroupPage, QWidget#TaskGroupStack {
            background: transparent; border: 0;
        }
        QToolButton#TaskGroupButton {
            background: $surface; color: $text_secondary; border: 1px solid $border;
            border-radius: 6px; padding: 0 8px; font-size: 12px; font-weight: 400;
        }
        QToolButton#TaskGroupButton:hover { background: $accent_soft; color: $accent; }
        QToolButton#TaskGroupButton:checked {
            background: $accent_soft; color: $accent; border-color: #CEE6DB;
        }
        QToolButton#TaskGroupButton:focus { border-color: $accent; }
        QToolButton#TaskConfigEditButton {
            background: $surface; border: 1px solid $border; border-radius: 6px;
        }
        QToolButton#TaskConfigEditButton:hover, QToolButton#TaskConfigEditButton:focus {
            border-color: $accent; background: $accent_soft;
        }
        QLabel#TaskSaveState { color: $text_secondary; font-size: 11px; }
        QLabel#TaskSaveState[dirty="true"] { color: $warning; }
        QLabel#ScriptStepNumber {
            color: $accent; background: $accent_soft; border-radius: 8px;
            font-size: 11px; font-weight: 500;
        }
        QLabel#ScriptRequirement { color: #7B8790; font-size: 10px; }
        QLabel#ScriptRequirement[missing="true"] { color: $warning; }
        QFrame#ScriptAssignmentRow[missing="true"] QLabel#ScriptStepNumber {
            color: $warning; background: $warning_soft;
        }
        QFrame[taskCard="true"] QComboBox,
        QFrame[taskCard="true"] QSpinBox,
        QFrame[taskCard="true"] QLineEdit {
            background: #F8FAFB; border: 1px solid $border; border-radius: 7px;
            min-height: 32px; padding: 0 10px; color: $text;
        }
        QFrame[taskCard="true"] QComboBox:hover,
        QFrame[taskCard="true"] QSpinBox:hover,
        QFrame[taskCard="true"] QLineEdit:hover { border-color: #B8C4CE; }
        QFrame[taskCard="true"] QComboBox:focus,
        QFrame[taskCard="true"] QSpinBox:focus,
        QFrame[taskCard="true"] QLineEdit:focus { background: $surface; border-color: $accent; }
        QFrame[taskCard="true"] QComboBox[missing="true"] { background: $warning_soft; border-color: #D8B77F; }
        QFrame[taskCard="true"] QComboBox:disabled,
        QFrame[taskCard="true"] QSpinBox:disabled,
        QFrame[taskCard="true"] QLineEdit:disabled { color: #97A1AB; background: #F5F7F8; }
        QFrame[taskCard="true"] QSpinBox QLineEdit {
            background: transparent; border: 0; padding: 0; min-height: 0;
        }
        QFrame[taskCard="true"] QComboBox { padding-right: 30px; }
        QLabel#ConfigSavedLabel, QLabel#ScriptSaveStateLabel,
        QLabel#AutoTidSaveState, QLabel#AutoTidScriptSaveState {
            background: transparent; border: 0; padding: 0; color: $text_secondary; font-size: 11px;
        }
        QLabel#ConfigSavedLabel[saved="false"], QLabel#ScriptSaveStateLabel[saved="false"],
        QLabel#AutoTidSaveState[dirty="true"], QLabel#AutoTidScriptSaveState[dirty="true"] {
            background: transparent; border: 0; color: $warning;
        }
        QPushButton#TaskSaveButton {
            background: $accent_soft; color: $accent; border: 1px solid #CEE6DB;
            border-radius: 6px; min-height: 28px; padding: 0 9px; font-size: 12px;
        }
        QPushButton#TaskSaveButton:hover { background: #DCEFE5; border-color: $accent; }
        QPushButton#TaskSaveButton:disabled { background: #F6F8F7; color: #98A59F; border-color: #E7ECE9; }
        QPushButton#TaskSaveButton:focus { border-color: $accent; }
    ''')
