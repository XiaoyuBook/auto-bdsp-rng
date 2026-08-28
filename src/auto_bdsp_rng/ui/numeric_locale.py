"""Locale helpers for numeric input widgets.

The application's values are frame counts, IDs, and decimal timing values.
They should use stable ASCII digits and a dot regardless of the host's
regional settings, while still inheriting the host's UI language/font.
"""

from __future__ import annotations

from PySide6.QtCore import QLocale, Qt
from PySide6.QtWidgets import QWidget


def set_c_locale(widget: QWidget) -> None:
    """Use the C locale on a numeric widget and its editable internals.

    ``QSpinBox`` and ``QDoubleSpinBox`` own a child ``QLineEdit``.  Setting
    both sides avoids a platform style or later editor replacement restoring
    the system locale.  Plain ``QLineEdit`` fields are supported as well so
    validators attached to legacy numeric fields get the same locale.
    """

    locale = QLocale.c()
    widget.setLocale(locale)

    # Frame counts, IDs and timing values are technical values rather than
    # prose.  Do not let a user's Windows region turn them into localized
    # digits, decimal punctuation or grouped values that are hard to copy.
    group_separator_setter = getattr(widget, "setGroupSeparatorShown", None)
    if callable(group_separator_setter):
        group_separator_setter(False)
    if hasattr(widget, "setLayoutDirection"):
        widget.setLayoutDirection(Qt.LayoutDirection.LeftToRight)

    validator_getter = getattr(widget, "validator", None)
    if callable(validator_getter):
        validator = validator_getter()
        validator_setter = getattr(validator, "setLocale", None)
        if callable(validator_setter):
            validator_setter(locale)

    line_edit_getter = getattr(widget, "lineEdit", None)
    if not callable(line_edit_getter):
        return
    line_edit = line_edit_getter()
    if line_edit is None:
        return
    line_edit.setLocale(locale)
    if hasattr(line_edit, "setLayoutDirection"):
        line_edit.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
    editor_validator_getter = getattr(line_edit, "validator", None)
    if callable(editor_validator_getter):
        editor_validator = editor_validator_getter()
        editor_validator_setter = getattr(editor_validator, "setLocale", None)
        if callable(editor_validator_setter):
            editor_validator_setter(locale)
