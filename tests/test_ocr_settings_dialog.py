from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from auto_bdsp_rng.automation.auto_rng.ocr_regions import OcrRegion
from auto_bdsp_rng.ui import MainWindow
from auto_bdsp_rng.ui.auto_rng_panel import AutoRngPanel
from auto_bdsp_rng.ui.ocr_settings_dialog import OcrSettingsDialog


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def _settings(tmp_path) -> QSettings:
    return QSettings(str(tmp_path / "ocr.ini"), QSettings.Format.IniFormat)


def test_auto_rng_button_opens_ocr_settings_label(app):
    panel = AutoRngPanel()

    assert panel.capture_info_button.text() == "OCR设置"
    assert "OCR" in panel.capture_info_button.toolTip()


def test_ocr_settings_dialog_is_non_modal_and_lists_eight_fields(app, tmp_path):
    dialog = OcrSettingsDialog(settings=_settings(tmp_path))

    assert not dialog.isModal()
    assert dialog.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert dialog.table.rowCount() == 8
    assert [dialog.table.item(row, 0).text() for row in range(dialog.table.rowCount())] == [
        "性格",
        "个性",
        "HP",
        "攻击",
        "防御",
        "特攻",
        "特防",
        "速度",
    ]
    assert dialog.table.verticalHeader().defaultSectionSize() >= 52
    assert dialog.table.minimumHeight() >= 470


def test_ocr_settings_dialog_saves_region_immediately(app, tmp_path):
    settings = _settings(tmp_path)
    dialog = OcrSettingsDialog(settings=settings)

    dialog.set_region("nature", OcrRegion(1, 2, 30, 40))

    assert dialog.region_config.get("nature") == OcrRegion(1, 2, 30, 40)
    assert dialog.table.item(0, 1).text() == "1, 2, 30, 40"

    restored = OcrSettingsDialog(settings=settings)
    assert restored.region_config.get("nature") == OcrRegion(1, 2, 30, 40)


def test_ocr_settings_dialog_row_actions_are_on_demand(app, tmp_path):
    recognized = []

    def recognizer(field, region):
        recognized.append((field, region))
        return "胆小"

    dialog = OcrSettingsDialog(settings=_settings(tmp_path), recognizer=recognizer)
    selections = []
    displays = []
    dialog.regionSelectionRequested.connect(selections.append)
    dialog.regionDisplayRequested.connect(lambda field, region: displays.append((field, region)))
    dialog.set_region("nature", OcrRegion(1, 2, 30, 40))

    dialog.request_selection("nature")
    dialog.show_region("nature")
    dialog.recognize_field("nature")

    assert selections == ["nature"]
    assert displays == [("nature", OcrRegion(1, 2, 30, 40))]
    assert recognized == [("nature", OcrRegion(1, 2, 30, 40))]
    assert dialog.table.item(0, 4).text() == "胆小"


def test_ocr_settings_dialog_warmup_button_is_on_demand(app, tmp_path):
    dialog = OcrSettingsDialog(settings=_settings(tmp_path))
    emitted = []
    dialog.warmupRequested.connect(lambda: emitted.append(True))

    dialog.warmup_button.click()

    assert emitted == [True]
    assert not dialog.warmup_button.isEnabled()
    assert dialog.warmup_button.text() == "预热中…"

    dialog.finish_warmup(True, "OCR预热完成")

    assert dialog.warmup_button.isEnabled()
    assert dialog.warmup_status.text() == "OCR预热完成"


def test_ocr_settings_dialog_reset_removes_region(app, tmp_path):
    settings = _settings(tmp_path)
    dialog = OcrSettingsDialog(settings=settings)
    dialog.set_region("speed", OcrRegion(10, 20, 30, 40))

    dialog.reset_region("speed")

    assert dialog.region_config.get("speed") is None
    assert dialog.table.item(7, 1).text() == "未设置"


def test_main_window_opens_ocr_settings_and_saves_selected_region(app):
    window = MainWindow()

    window.open_ocr_settings()
    dialog = window._ocr_settings_dialog
    window.ocrRegionSelected.emit("nature", (10, 20, 30, 40))

    assert dialog is not None
    assert dialog.isVisible()
    assert dialog.region_config.get("nature") == OcrRegion(10, 20, 30, 40)


def test_main_window_warms_up_ocr_in_background(app, monkeypatch):
    import auto_bdsp_rng.ui.main_window as main_window_module

    window = MainWindow()
    window.open_ocr_settings()
    dialog = window._ocr_settings_dialog
    calls = []

    def fake_read_paddle_ocr_text(_frame):
        calls.append(True)
        return ""

    monkeypatch.setattr(main_window_module, "read_paddle_ocr_text", fake_read_paddle_ocr_text)

    dialog.start_warmup()
    deadline = 100
    while not dialog.warmup_button.isEnabled() and deadline > 0:
        app.processEvents()
        QTest.qWait(10)
        deadline -= 1

    assert calls == [True]
    assert dialog.warmup_button.isEnabled()
    assert "完成" in dialog.warmup_status.text()
