from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


@pytest.mark.parametrize("field_kind", ["integer", "decimal"])
@pytest.mark.parametrize("platform", ["offscreen", "windows"])
def test_numeric_editor_recovers_from_clipped_style_geometry(field_kind, platform):
    if platform == "windows" and sys.platform != "win32":
        pytest.skip("Windows platform plugin is only available on Windows")
    code = textwrap.dedent("""
        import sys
        from PySide6.QtCore import QRect, Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication, QAbstractSpinBox, QProxyStyle, QStyle
        from auto_bdsp_rng.ui.spin_box import ChevronSpinBox, ChevronDoubleSpinBox
        from auto_bdsp_rng.ui.workspace_theme import ui_font

        class ClippedEditorStyle(QProxyStyle):
            def __init__(self):
                super().__init__('Fusion')

            def subControlRect(self, control, option, sub_control, widget=None):
                if (control == QStyle.ComplexControl.CC_SpinBox and
                        sub_control == QStyle.SubControl.SC_SpinBoxEditField):
                    # A visible outer frame can coexist with a one-pixel text
                    # area. The model still holds 31 but Qt clips every digit.
                    return QRect(0, 1, 1, max(1, option.rect.height() - 2))
                return super().subControlRect(control, option, sub_control, widget)

        app = QApplication([])
        app.setFont(ui_font())
        style = ClippedEditorStyle()
        app.setStyle(style)
        kind = ChevronSpinBox if sys.argv[1] == 'integer' else ChevronDoubleSpinBox
        field = kind()
        field.setRange(0, 99)
        if sys.argv[1] == 'decimal':
            field.setDecimals(1)
        field.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        field.setAlignment(Qt.AlignmentFlag.AlignRight)
        field.setValue(31)
        field.setFixedSize(160, 34)
        field.show()
        app.processEvents()
        editor = field.lineEdit()
        assert field.value() == 31
        assert editor.width() > editor.fontMetrics().horizontalAdvance(editor.text()) + 4
        assert field.rect().contains(editor.geometry())
        before = field.grab().toImage()
        field.setValue(42)
        app.processEvents()
        after = field.grab().toImage()
        # Two different values must produce different visible pixels, not
        # merely different text()/value() while the input remains blank.
        assert before != after
        field.setFocus()
        field.selectAll()
        QTest.keyClicks(field, '12')
        QTest.keyClick(field, Qt.Key.Key_Return)
        assert field.value() == 12
        field.resize(120, 36)
        field.setFixedSize(120, 36)
        app.processEvents()
        assert field.rect().contains(editor.geometry())
        assert editor.width() > editor.fontMetrics().horizontalAdvance(editor.text()) + 4
    """)
    env = dict(os.environ, QT_QPA_PLATFORM=platform, PYTHONIOENCODING="utf-8")
    result = subprocess.run(
        [sys.executable, "-c", code, field_kind],
        cwd=Path(__file__).resolve().parents[1], env=env,
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("platform_style", ["Windows", "windowsvista", "windows11", "Fusion"])
@pytest.mark.parametrize("scale", ["0.75", "1", "1.25", "1.5", "2"])
def test_numeric_editors_keep_values_inside_small_fields(platform_style, scale):
    # Fresh processes exercise real font/layout metrics at each DPI without
    # leaking QApplication style or scale state into unrelated UI tests.
    code = textwrap.dedent("""
        import sys
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QFontDatabase, QFontInfo, QPalette
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import (
            QApplication, QAbstractSpinBox, QStyleFactory, QVBoxLayout, QWidget,
        )
        from auto_bdsp_rng.ui.spin_box import ChevronSpinBox, ChevronDoubleSpinBox

        app = QApplication([])
        requested = sys.argv[1]
        if requested.casefold() not in {name.casefold() for name in QStyleFactory.keys()}:
            sys.exit(77)
        app.setStyle(requested)
        parent = QWidget()
        layout = QVBoxLayout(parent)
        fields = []
        for field_type in (ChevronSpinBox, ChevronDoubleSpinBox):
            for buttons in (QAbstractSpinBox.ButtonSymbols.NoButtons,
                            QAbstractSpinBox.ButtonSymbols.UpDownArrows):
                field = field_type(parent)
                field.setRange(0, 99)
                if isinstance(field, ChevronDoubleSpinBox):
                    field.setDecimals(1)
                field.setValue(31)
                field.setAlignment(Qt.AlignmentFlag.AlignRight)
                field.setButtonSymbols(buttons)
                field.setStyleSheet('QSpinBox, QDoubleSpinBox { background: white; }')
                field.setFixedSize(110 if isinstance(field, ChevronDoubleSpinBox) else 80, 34)
                field.setEnabled(False)
                layout.addWidget(field)
                fields.append(field)
        # Outer input styles may change after construction. Editor constraints
        # must stay independent, otherwise a right-aligned value is clipped.
        parent.setStyleSheet('''
            QWidget { background: white; color: #202A33; font-family: Times New Roman; }
            QSpinBox, QDoubleSpinBox {
                border: 1px solid #E0E5EB; border-radius: 7px; padding: 0 8px;
            }
            QSpinBox:disabled, QDoubleSpinBox:disabled { color: #687480; }
            QLineEdit {
                min-width: 220px; max-width: 240px;
                min-height: 70px; max-height: 90px;
                border: 2px solid red; padding: 12px; margin: 5px;
            }
        ''')
        parent.show()
        app.processEvents()
        parent.setFocus()
        available_fonts = set(QFontDatabase.families())
        for field in fields:
            editor = field.lineEdit()
            assert editor.palette().color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text) == \
                field.palette().color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text)
            field.setEnabled(True)
            app.processEvents()
            assert field.rect().contains(editor.geometry()), editor.geometry()
            assert editor.minimumWidth() == editor.minimumHeight() == 0
            assert editor.fontMetrics().horizontalAdvance(editor.text()) + 4 <= editor.width()
            assert QFontInfo(editor.font()).family() != 'Times New Roman'
            if 'Consolas' in available_fonts:
                assert QFontInfo(editor.font()).family() == 'Consolas'
            image = field.grab().toImage()
            ratio = image.devicePixelRatio()
            area = editor.geometry().adjusted(2, 2, -2, -2)
            ink = [image.pixelColor(x, y)
                   for y in range(round(area.top() * ratio), round((area.bottom() + 1) * ratio))
                   for x in range(round(area.left() * ratio), round((area.right() + 1) * ratio))]
            assert sum(color.alpha() > 0 and color.lightness() < 150 for color in ink) > 5
            field.setFocus()
            field.selectAll()
            QTest.keyClicks(field, '42')
            QTest.keyClick(field, Qt.Key.Key_Return)
            assert field.value() == 42
            QTest.keyClick(field, Qt.Key.Key_Up)
            assert field.value() == 43
            field.setReadOnly(True)
            assert field.rect().contains(editor.geometry())
            field.setEnabled(False)
            app.processEvents()
            assert field.rect().contains(editor.geometry())
            assert editor.palette().color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text) == \
                field.palette().color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text)
            parent.setStyleSheet(parent.styleSheet().replace('#687480', '#986D80'))
            app.processEvents()
            assert editor.palette().color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text) == \
                field.palette().color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text)
            field.setEnabled(True)
            app.processEvents()
            assert editor.palette().color(QPalette.ColorGroup.Active, QPalette.ColorRole.Text) == \
                field.palette().color(QPalette.ColorGroup.Active, QPalette.ColorRole.Text)
    """)
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_SCALE_FACTOR=scale, PYTHONIOENCODING="utf-8")
    result = subprocess.run(
        [sys.executable, "-c", code, platform_style],
        cwd=Path(__file__).resolve().parents[1], env=env,
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    if result.returncode == 77:
        pytest.skip(f"Qt style {platform_style} is not available")
    assert result.returncode == 0, result.stdout + result.stderr


def test_target_and_task_parameter_values_remain_visible(tmp_path):
    code = textwrap.dedent("""
        import sys
        from pathlib import Path
        from PySide6.QtCore import QSettings
        from PySide6.QtGui import QFontDatabase, QFontInfo, QPalette
        from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget
        from auto_bdsp_rng.ui.auto_rng_panel import AutoRngPanel
        from auto_bdsp_rng.ui.main_window import MainWindow
        from auto_bdsp_rng.ui.target_dialog import TargetDialog
        from auto_bdsp_rng.ui.workspace_style import configure_workspace_style

        app = QApplication([])
        configure_workspace_style(app)
        parent = QWidget()
        MainWindow._apply_theme(parent)
        panel = AutoRngPanel(parent=parent, script_dir=Path(sys.argv[1]), settings=QSettings(
            sys.argv[1] + '/display.ini', QSettings.Format.IniFormat))
        QVBoxLayout(parent).addWidget(panel)
        parent.resize(1050, 900)
        parent.show()
        dialog = TargetDialog(parent)
        dialog.show()
        app.processEvents()
        form = dialog.target_form
        for field in (panel.max_advances, panel.max_wait_frames, form.level_display,
                      form.iv_count_display, *form.iv_min, *form.iv_max,
                      form.height_min, form.height_max, form.weight_min, form.weight_max):
            editor = field.lineEdit()
            assert editor.text()
            assert field.rect().contains(editor.geometry()), editor.geometry()
            assert editor.fontMetrics().horizontalAdvance(editor.text()) + 4 <= editor.width()
            if 'Consolas' in QFontDatabase.families():
                assert QFontInfo(editor.font()).family() == 'Consolas'
            field.setEnabled(False)
            app.processEvents()
            assert editor.palette().color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text) == \
                field.palette().color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text)
    """)
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8")
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1], env=env,
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
