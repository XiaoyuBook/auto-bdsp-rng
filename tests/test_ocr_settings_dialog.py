from __future__ import annotations

import threading

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from auto_bdsp_rng.automation.auto_rng.ocr_regions import OcrRegion
from auto_bdsp_rng.ui import MainWindow
from auto_bdsp_rng.ui.auto_rng_panel import AutoRngPanel
from auto_bdsp_rng.ui.ocr_settings_dialog import OcrSettingsDialog


_ORIGINAL_START_OCR_WARMUP = MainWindow._start_ocr_warmup


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(MainWindow, "_start_ocr_warmup", lambda self: None)
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


def test_ocr_settings_dialog_blocks_synchronous_recognition_while_warming(app, tmp_path):
    recognized = []
    dialog = OcrSettingsDialog(
        settings=_settings(tmp_path),
        recognizer=lambda field, region: recognized.append((field, region)) or "胆小",
    )
    dialog.set_region("nature", OcrRegion(1, 2, 30, 40))

    dialog.show_warmup_running()
    dialog.recognize_field("nature")
    dialog.test_all()

    assert not dialog.warmup_button.isEnabled()
    assert not dialog.test_current_button.isEnabled()
    assert not dialog.test_all_button.isEnabled()
    assert all(not button.isEnabled() for button in dialog._recognition_buttons)
    assert recognized == []
    assert dialog.table.item(0, 4).text() == "等待预热完成"

    dialog.finish_warmup(True, "OCR预热完成")

    assert dialog.test_current_button.isEnabled()
    assert dialog.test_all_button.isEnabled()
    assert all(button.isEnabled() for button in dialog._recognition_buttons)


def test_ocr_settings_dialog_test_all_requests_full_controller_flow(app, tmp_path):
    dialog = OcrSettingsDialog(settings=_settings(tmp_path))
    emitted = []
    dialog.fullTestRequested.connect(lambda: emitted.append(True))

    dialog.test_all()

    assert emitted == [True]
    assert not dialog.test_all_button.isEnabled()
    assert dialog.test_all_button.text() == "测试中…"

    dialog.set_recognition_result("hp", "108")
    dialog.finish_full_test(True, "测试全部完成")

    assert dialog.table.item(2, 4).text() == "108"
    assert dialog.test_all_button.isEnabled()
    assert dialog.test_all_button.text() == "测试全部"


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


def test_main_window_opening_ocr_settings_replays_running_warmup_state(app):
    window = MainWindow()
    window._ocr_warmup_running = True

    window.open_ocr_settings()

    dialog = window._ocr_settings_dialog
    assert dialog is not None
    assert dialog.warmup_button.text() == "预热中…"
    assert dialog.warmup_status.text() == "正在初始化 OCR"
    assert not dialog.test_current_button.isEnabled()
    assert not dialog.test_all_button.isEnabled()


def test_main_window_warms_up_ocr_in_background(app, monkeypatch):
    import auto_bdsp_rng.ui.main_window as main_window_module

    window = MainWindow()
    calls = []

    def fake_warm_up_pokemon_info_ocr():
        calls.append(True)

    monkeypatch.setattr(main_window_module, "warm_up_pokemon_info_ocr", fake_warm_up_pokemon_info_ocr)
    monkeypatch.setattr(
        window,
        "_start_ocr_warmup",
        _ORIGINAL_START_OCR_WARMUP.__get__(window, MainWindow),
    )
    window.open_ocr_settings()
    dialog = window._ocr_settings_dialog

    dialog.start_warmup()
    deadline = 400
    while not dialog.warmup_button.isEnabled() and deadline > 0:
        app.processEvents()
        QTest.qWait(10)
        deadline -= 1

    assert calls == [True]
    assert dialog.warmup_button.isEnabled()
    assert "完成" in dialog.warmup_status.text()


def test_main_window_does_not_emit_ocr_warmup_result_after_closing(app, monkeypatch):
    import auto_bdsp_rng.ui.main_window as main_window_module

    window = MainWindow()
    started = threading.Event()
    release = threading.Event()
    results = []

    def slow_warm_up() -> None:
        started.set()
        release.wait(timeout=1.0)

    monkeypatch.setattr(main_window_module, "warm_up_pokemon_info_ocr", slow_warm_up)
    monkeypatch.setattr(
        window,
        "_start_ocr_warmup",
        _ORIGINAL_START_OCR_WARMUP.__get__(window, MainWindow),
    )
    window.ocrWarmupFinished.connect(lambda success, message: results.append((success, message)))
    window._start_ocr_warmup()

    deadline = 100
    while not started.is_set() and deadline > 0:
        QTest.qWait(10)
        deadline -= 1
    assert started.is_set()

    release_timer = threading.Timer(0.05, release.set)
    release_timer.start()
    window.close()
    release_timer.join(timeout=1.0)
    app.processEvents()

    assert window._ocr_warmup_thread is not None
    assert not window._ocr_warmup_thread.isRunning()
    assert results == []


@pytest.mark.parametrize(
    ("result", "expected_button", "expected_status"),
    [
        ((True, "OCR预热完成"), "重新预热", "OCR预热完成"),
        ((False, "OCR预热失败: 模型不可用"), "预热OCR", "OCR预热失败: 模型不可用"),
    ],
)
def test_main_window_replays_completed_ocr_warmup_result_when_settings_open_late(
    app,
    result,
    expected_button,
    expected_status,
):
    window = MainWindow()
    window._ocr_warmup_result = result

    window.open_ocr_settings()

    dialog = window._ocr_settings_dialog
    assert dialog is not None
    assert dialog.warmup_button.text() == expected_button
    assert dialog.warmup_status.text() == expected_status


def test_main_window_full_ocr_test_uses_right_between_notes_and_stats(app, monkeypatch):
    import auto_bdsp_rng.ui.main_window as main_window_module

    window = MainWindow()
    window.open_ocr_settings()
    dialog = window._ocr_settings_dialog
    for field in tuple(dialog.region_config._regions):
        dialog.reset_region(field)
    dialog.set_region("nature", OcrRegion(1, 1, 10, 10))
    dialog.set_region("characteristic", OcrRegion(2, 2, 10, 10))
    dialog.set_region("hp", OcrRegion(3, 3, 10, 10))
    events = []
    frames = iter(["notes_frame", "stats_frame"])

    monkeypatch.setattr(window, "_current_preview_frame_for_ocr", lambda: next(frames))
    monkeypatch.setattr(main_window_module, "capture_preview_frame", lambda _config: next(frames))
    monkeypatch.setattr(window, "_config_from_form", lambda: type("Config", (), {"capture": object()})())
    monkeypatch.setattr(window, "_send_easycon_right", lambda: events.append("right"))

    def fake_recognize(frame, field, _region):
        events.append((frame, field))
        return {"nature": "胆小", "characteristic": "喜欢胡闹", "hp": "108"}[field]

    monkeypatch.setattr(main_window_module, "recognize_ocr_field", fake_recognize)

    dialog.test_all()
    deadline = 400
    while not dialog.test_all_button.isEnabled() and deadline > 0:
        app.processEvents()
        QTest.qWait(10)
        deadline -= 1

    assert events == [
        ("notes_frame", "nature"),
        ("notes_frame", "characteristic"),
        "right",
        ("stats_frame", "hp"),
    ]
    assert dialog.table.item(0, 4).text() == "胆小"
    assert dialog.table.item(1, 4).text() == "喜欢胡闹"
    assert dialog.table.item(2, 4).text() == "108"
