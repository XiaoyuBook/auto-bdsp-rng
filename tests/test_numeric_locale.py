from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QLocale, QSettings
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import QApplication, QDoubleSpinBox, QLineEdit, QSpinBox

from auto_bdsp_rng.ui.auto_rng_panel import AutoRngPanel
from auto_bdsp_rng.ui.auto_tid_rng_panel import AutoTidRngPanel
from auto_bdsp_rng.ui.numeric_locale import set_c_locale


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def test_numeric_spinboxes_ignore_rtl_system_digits_and_separators(app):
    arabic = QLocale("ar_EG")

    integer = QSpinBox()
    integer.setLocale(arabic)
    integer.setRange(0, 1_000_000_000)
    integer.setValue(123456789)
    integer.setPrefix("±")
    integer.setSuffix(" 帧")

    decimal = QDoubleSpinBox()
    decimal.setLocale(arabic)
    decimal.setRange(0.0, 9999.0)
    decimal.setDecimals(2)
    decimal.setValue(1234.5)

    set_c_locale(integer)
    set_c_locale(decimal)

    assert integer.locale().name() == "C"
    assert integer.lineEdit().locale().name() == "C"
    assert integer.text() == "±123456789 帧"
    assert integer.isGroupSeparatorShown() is False
    assert decimal.locale().name() == "C"
    assert decimal.lineEdit().locale().name() == "C"
    assert decimal.text() == "1234.50"
    assert decimal.isGroupSeparatorShown() is False


def test_c_locale_is_applied_to_legacy_line_edit_validator(app):
    line_edit = QLineEdit("123456")
    validator = QIntValidator(0, 1_000_000)
    validator.setLocale(QLocale("fa_IR"))
    line_edit.setValidator(validator)

    set_c_locale(line_edit)

    assert line_edit.locale().name() == "C"
    assert validator.locale().name() == "C"
    assert line_edit.text() == "123456"


def test_rng_panels_keep_numeric_fields_ascii_under_rtl_default(app, tmp_path: Path):
    previous = QLocale()
    QLocale.setDefault(QLocale("ar_EG"))
    try:
        settings = QSettings(str(tmp_path / "auto-rng.ini"), QSettings.Format.IniFormat)
        settings.clear()
        auto_panel = AutoRngPanel(script_dir=tmp_path, settings=settings)
        tid_panel = AutoTidRngPanel(script_dir=tmp_path)

        numeric_fields = (
            auto_panel.loop_count,
            auto_panel.max_advances,
            auto_panel.fixed_delay,
            auto_panel.max_wait_frames,
            auto_panel.reseed_threshold_frames,
            auto_panel.reidentify_max_attempts,
            auto_panel.reidentify_seed_max_attempts,
            auto_panel.reseeding_threshold,
            auto_panel.shiny_threshold_seconds,
            auto_panel.reverse_lookup_window,
            tid_panel.frame_threshold,
            tid_panel.delay,
            tid_panel.reverse_lookup_window,
        )
        for field in numeric_fields:
            assert field.locale().name() == "C"
            assert field.lineEdit().locale().name() == "C"

        auto_panel.max_advances.setValue(123456789)
        auto_panel.shiny_threshold_seconds.setValue(123.456)
        tid_panel.frame_threshold.setValue(987654321)
        assert auto_panel.max_advances.text() == "123456789"
        assert auto_panel.shiny_threshold_seconds.text() == "123.456"
        assert tid_panel.frame_threshold.text() == "987654321"
    finally:
        QLocale.setDefault(previous)
