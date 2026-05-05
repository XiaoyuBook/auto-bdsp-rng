from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from auto_bdsp_rng.ui import MainWindow


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def test_main_window_generates_static_results(app):
    window = MainWindow()

    window.max_advances.setValue(2)
    window.generate_results()

    assert window.table.rowCount() == 3
    assert window.result_count.text() == "3 results"
    assert window.table.item(0, 0).text() == "0"
    assert window.table.item(0, 1).text()


def test_main_window_syncs_seed64_display(app):
    window = MainWindow()

    assert window.seed64_outputs[0].text() == "123456789ABCDEF0"
    assert window.seed64_outputs[1].text() == "1111111122222222"
