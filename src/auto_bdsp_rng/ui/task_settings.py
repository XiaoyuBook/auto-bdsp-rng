"""Shared presentation for automation parameters and script assignments."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QAbstractButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    QWidgetItem,
)

from auto_bdsp_rng.ui.workspace_theme import workspace_styles


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
    layout = QVBoxLayout(field)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    label = QLabel(title)
    label.setObjectName("TaskFieldTitle")
    label.setBuddy(control)
    layout.addWidget(label)
    layout.addWidget(control)
    note = QLabel(hint)
    note.setObjectName("TaskFieldHint")
    note.setWordWrap(True)
    layout.addWidget(note)
    return field


class ScriptAssignmentRow(QFrame):
    """A numbered script stage, with the same picker at every window width."""

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
        self.grid.setContentsMargins(0, 10, 0, 10)
        self.grid.setHorizontalSpacing(10)
        self.grid.setVerticalSpacing(5)
        self.grid.addWidget(self.number, 0, 0, 2, 1, Qt.AlignmentFlag.AlignTop)
        self.grid.addWidget(self.heading, 0, 1)
        self.grid.addWidget(self.detail, 1, 1)
        self.grid.addWidget(picker, 0, 2, 2, 1)
        self._wide = None
        self._reflow()

    def set_requirement(self, text, missing=False):
        self.requirement.setText(text)
        for widget in (self, self.requirement):
            if widget.property("missing") != missing:
                widget.setProperty("missing", missing)
                widget.style().unpolish(widget)
                widget.style().polish(widget)

    def _reflow(self):
        wide = self.width() >= 500
        if wide == self._wide:
            return
        self._wide = wide
        self.grid.removeWidget(self.picker)
        self.grid.addWidget(self.picker, 0 if wide else 2, 2 if wide else 1, 2 if wide else 1, 1)
        self.grid.setColumnStretch(1, 0 if wide else 1)
        self.grid.setColumnStretch(2, 1 if wide else 0)
        self.heading.setMinimumWidth(148 if wide else 0)
        self.picker.setMinimumWidth(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow()


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
            background: transparent; border: 0; border-bottom: 1px solid $separator;
        }
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
