from __future__ import annotations

import pytest

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from auto_bdsp_rng.ui.combo_box import ChevronComboBox


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    application = QApplication.instance() or QApplication([])
    yield application
    for widget in application.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    application.processEvents()


def test_chevron_combo_box_leaves_standalone_native_arrow_untouched(app):
    combo = ChevronComboBox()

    assert not combo._stylesheet_owns_arrow()


def test_chevron_combo_box_draws_arrow_when_qss_hides_native_arrow(app):
    combo = ChevronComboBox()
    combo.addItems(["一", "二"])
    combo.setFixedSize(180, 32)
    combo.setStyleSheet(
        "QComboBox { background: #ffffff; border: 1px solid #e2e8e4; "
        "padding: 0 8px; }"
        "QComboBox::drop-down { width: 28px; border: 0; }"
        "QComboBox::down-arrow { image: none; }"
    )
    combo.show()
    app.processEvents()

    image = combo.grab().toImage()
    scale = image.width() / combo.width()
    center_x = round((combo.width() - 17) * scale)
    center_y = round((combo.height() / 2) * scale)
    dark_pixels = 0
    for x in range(center_x - round(6 * scale), center_x + round(6 * scale) + 1):
        for y in range(center_y - round(5 * scale), center_y + round(5 * scale) + 1):
            if QColor.fromRgba(image.pixel(x, y)).lightness() < 120:
                dark_pixels += 1

    assert dark_pixels > 0
