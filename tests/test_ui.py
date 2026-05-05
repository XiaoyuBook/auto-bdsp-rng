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

    assert [window.tabs.tabText(index) for index in range(window.tabs.count())] == ["Project_Xs", "BDSP / PokeFinder"]
    assert window.tabs.tabText(window.tabs.currentIndex()) == "Project_Xs"

    window.tabs.setCurrentWidget(window.bdsp_tab)
    window.max_advances.setValue(2)
    window.generate_results()

    assert window.table.rowCount() == 3
    assert window.result_count.text() == "3 条结果"
    assert window.table.item(0, 0).text() == "0"
    assert window.table.item(0, 1).text()


def test_main_window_syncs_seed64_display(app):
    window = MainWindow()

    assert window.seed64_outputs[0].text() == "123456789ABCDEF0"
    assert window.seed64_outputs[1].text() == "1111111122222222"


def test_main_window_loads_project_xs_config_fields(app):
    window = MainWindow()
    index = window.config_combo.findText("config_bebe.json")
    window.config_combo.setCurrentIndex(index)

    assert window.window_prefix.text() == "PotPlayer"
    assert window.monitor_window.isChecked() is True
    assert window.x.value() == 516
    assert window.y.value() == 377
    assert window.w.value() == 38
    assert window.h.value() == 53
    assert window.threshold.value() == 0.7


def test_main_window_can_switch_language(app):
    window = MainWindow()

    window.language_combo.setCurrentIndex(window.language_combo.findData("en"))

    assert window.capture_group.title() == "Blink capture"
    assert window.capture_button.text() == "Capture Seed"


def test_main_window_applies_selected_roi(app, monkeypatch):
    window = MainWindow()

    class FakeImage:
        shape = (10, 12)

    fake_cv2 = type("FakeCv2", (), {"IMREAD_GRAYSCALE": 0, "imread": staticmethod(lambda *_args: FakeImage())})
    monkeypatch.setitem(__import__("sys").modules, "cv2", fake_cv2)

    window.apply_selected_roi((20, 30, 40, 50))

    assert window.x.value() == 20
    assert window.y.value() == 30
    assert window.w.value() == 40
    assert window.h.value() == 50
