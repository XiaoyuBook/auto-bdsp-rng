from __future__ import annotations

import threading

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from auto_bdsp_rng.automation.auto_rng.ocr_regions import (
    OCR_REGION_FIELDS,
    SHINY_DIALOG_REGION_FIELD,
    STARTER_BATTLE_REGION_FIELD,
    OcrRegion,
)
from auto_bdsp_rng.ui import MainWindow
from auto_bdsp_rng.ui.auto_rng_panel import AutoRngPanel
from auto_bdsp_rng.ui.ocr_settings_dialog import (
    DEFAULT_OCR_REGIONS,
    OcrSettingsDialog,
    load_ocr_region_config,
)


_ORIGINAL_START_OCR_WARMUP = MainWindow._start_ocr_warmup
_EXPECTED_DEFAULT_OCR_REGIONS = {
    "nature": OcrRegion(112, 203, 230, 64),
    "characteristic": OcrRegion(103, 569, 432, 64),
    "hp": OcrRegion(517, 197, 54, 42),
    "attack": OcrRegion(735, 315, 85, 64),
    "defense": OcrRegion(717, 478, 115, 54),
    "sp_attack": OcrRegion(224, 306, 63, 67),
    "sp_defense": OcrRegion(218, 487, 85, 42),
    "speed": OcrRegion(475, 596, 85, 39),
    SHINY_DIALOG_REGION_FIELD: OcrRegion(6, 895, 1914, 175),
    STARTER_BATTLE_REGION_FIELD: OcrRegion(1540, 620, 170, 95),
}


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(MainWindow, "_start_ocr_warmup", lambda self: None)
    application = QApplication.instance() or QApplication([])
    yield application
    for widget in application.topLevelWidgets():
        for timer in widget.findChildren(QTimer):
            timer.stop()
        widget.close()
        widget.deleteLater()
    application.processEvents()


@pytest.fixture(autouse=True)
def isolate_main_window_ocr_settings(monkeypatch, tmp_path):
    import auto_bdsp_rng.ui.main_window as main_window_module

    settings = QSettings(str(tmp_path / "main_window_ocr.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(
        main_window_module,
        "OcrSettingsDialog",
        lambda parent=None: OcrSettingsDialog(parent, settings=settings),
    )


def _settings(tmp_path) -> QSettings:
    return QSettings(str(tmp_path / "ocr.ini"), QSettings.Format.IniFormat)


def test_auto_rng_button_opens_ocr_settings_label(app):
    panel = AutoRngPanel()

    assert panel.capture_info_button.text() == "OCR设置"
    assert "OCR" in panel.capture_info_button.toolTip()


def test_ocr_settings_dialog_is_non_modal_and_lists_all_fields(app, tmp_path):
    dialog = OcrSettingsDialog(settings=_settings(tmp_path))

    assert not dialog.isModal()
    assert dialog.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert dialog.table.rowCount() == 10
    assert [dialog.table.item(row, 0).text() for row in range(dialog.table.rowCount())] == [
        "性格",
        "个性",
        "HP",
        "攻击",
        "防御",
        "特攻",
        "特防",
        "速度",
        "判闪对话区域",
        "御三家战斗区域",
    ]
    assert dialog.table.verticalHeader().defaultSectionSize() >= 52
    assert dialog.table.minimumHeight() >= 470


def test_ocr_settings_dialog_uses_default_regions_on_first_load(app, tmp_path):
    settings = _settings(tmp_path)

    config = load_ocr_region_config(settings)

    assert DEFAULT_OCR_REGIONS == _EXPECTED_DEFAULT_OCR_REGIONS
    assert dict(config.items()) == _EXPECTED_DEFAULT_OCR_REGIONS
    assert all(region.clip(1920, 1080) == region for region in DEFAULT_OCR_REGIONS.values())
    assert not any(settings.contains(f"regions/{field}") for field in DEFAULT_OCR_REGIONS)


def test_ocr_settings_dialog_shows_calibrated_shiny_dialog_default(app, tmp_path):
    dialog = OcrSettingsDialog(settings=_settings(tmp_path))
    row = dialog._field_rows[SHINY_DIALOG_REGION_FIELD]
    default_region = _EXPECTED_DEFAULT_OCR_REGIONS[SHINY_DIALOG_REGION_FIELD]

    assert dialog.region_config.get(SHINY_DIALOG_REGION_FIELD) == default_region
    assert dialog.table.item(row, 1).text() == "6, 895, 1914, 175"
    assert dialog.table.item(row, 2).text() == "已设置"

    dialog.set_preview_frame_shape((1080, 1920, 3))

    assert dialog.resolve_region(SHINY_DIALOG_REGION_FIELD, (1080, 1920, 3)) == default_region
    assert dialog.table.item(row, 1).text() == "有效: 6, 895, 1914, 175"

    assert dialog.resolve_region(SHINY_DIALOG_REGION_FIELD, (720, 1280, 3)) == OcrRegion(0, 360, 1280, 360)


def test_ocr_settings_dialog_marks_malformed_shiny_dialog_config(app, tmp_path):
    settings = _settings(tmp_path)
    settings.setValue(f"regions/{SHINY_DIALOG_REGION_FIELD}", "not-a-region")
    settings.sync()

    dialog = OcrSettingsDialog(settings=settings)
    row = dialog._field_rows[SHINY_DIALOG_REGION_FIELD]

    assert dialog.region_config.has_invalid_custom(SHINY_DIALOG_REGION_FIELD)
    assert dialog.resolve_region(SHINY_DIALOG_REGION_FIELD, (720, 1280, 3)) == OcrRegion(0, 360, 1280, 360)
    assert dialog.table.item(row, 2).text() == "配置无效，使用默认"


def test_starter_battle_region_uses_calibrated_default_and_dynamic_fallback(app, tmp_path):
    dialog = OcrSettingsDialog(settings=_settings(tmp_path))
    row = dialog._field_rows[STARTER_BATTLE_REGION_FIELD]
    default_region = _EXPECTED_DEFAULT_OCR_REGIONS[STARTER_BATTLE_REGION_FIELD]

    assert dialog.region_config.get(STARTER_BATTLE_REGION_FIELD) == default_region
    assert dialog.table.item(row, 1).text() == "1540, 620, 170, 95"
    assert dialog.table.item(row, 2).text() == "已设置"
    assert dialog.resolve_region(STARTER_BATTLE_REGION_FIELD, (1080, 1920, 3)) == default_region
    assert dialog.resolve_region(STARTER_BATTLE_REGION_FIELD, (720, 1280, 3)) == OcrRegion(1027, 413, 113, 64)


def test_starter_battle_actions_persist_and_reset_to_dynamic_default(app, tmp_path):
    settings = _settings(tmp_path)
    dialog = OcrSettingsDialog(settings=settings)
    custom_region = OcrRegion(1000, 420, 180, 90)
    selections = []
    displays = []
    recognitions = []
    dialog.regionSelectionRequested.connect(selections.append)
    dialog.regionDisplayRequested.connect(lambda field, region: displays.append((field, region)))
    dialog.recognitionRequested.connect(lambda field, region: recognitions.append((field, region)))

    dialog.set_region(STARTER_BATTLE_REGION_FIELD, custom_region)
    dialog.request_selection(STARTER_BATTLE_REGION_FIELD)
    dialog.show_region(STARTER_BATTLE_REGION_FIELD)
    dialog.recognize_field(STARTER_BATTLE_REGION_FIELD)

    assert selections == [STARTER_BATTLE_REGION_FIELD]
    assert displays == [(STARTER_BATTLE_REGION_FIELD, custom_region)]
    assert recognitions == [(STARTER_BATTLE_REGION_FIELD, custom_region)]
    assert settings.value(f"regions/{STARTER_BATTLE_REGION_FIELD}") == "[1000, 420, 180, 90]"
    dialog.finish_recognition(STARTER_BATTLE_REGION_FIELD, "战斗")

    restored = OcrSettingsDialog(settings=settings)
    assert restored.region_config.get(STARTER_BATTLE_REGION_FIELD) == custom_region
    restored.reset_region(STARTER_BATTLE_REGION_FIELD)

    row = restored._field_rows[STARTER_BATTLE_REGION_FIELD]
    assert restored.region_config.get(STARTER_BATTLE_REGION_FIELD) is None
    assert restored.resolve_region(STARTER_BATTLE_REGION_FIELD, (720, 1280, 3)) == OcrRegion(1027, 413, 113, 64)
    assert "按当前帧比例" in restored.table.item(row, 1).text()
    assert restored.table.item(row, 2).text() == "默认"
    assert settings.value(f"regions/{STARTER_BATTLE_REGION_FIELD}") == ""


def test_ocr_settings_dialog_preserves_legacy_partial_regions(app, tmp_path):
    settings = _settings(tmp_path)
    custom_region = OcrRegion(1, 2, 30, 40)
    settings.setValue("regions/nature", custom_region.to_settings_value())
    settings.sync()

    config = load_ocr_region_config(settings)

    assert config.get("nature") == custom_region
    assert config.get("speed") == DEFAULT_OCR_REGIONS["speed"]


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


def test_shiny_dialog_actions_emit_calibrated_default_for_async_controller(app, tmp_path):
    dialog = OcrSettingsDialog(settings=_settings(tmp_path))
    default_region = _EXPECTED_DEFAULT_OCR_REGIONS[SHINY_DIALOG_REGION_FIELD]
    selections = []
    displays = []
    recognitions = []
    dialog.regionSelectionRequested.connect(selections.append)
    dialog.regionDisplayRequested.connect(lambda field, region: displays.append((field, region)))
    dialog.recognitionRequested.connect(lambda field, region: recognitions.append((field, region)))

    dialog.request_selection(SHINY_DIALOG_REGION_FIELD)
    dialog.show_region(SHINY_DIALOG_REGION_FIELD)
    dialog.recognize_field(SHINY_DIALOG_REGION_FIELD)

    row = dialog._field_rows[SHINY_DIALOG_REGION_FIELD]
    assert selections == [SHINY_DIALOG_REGION_FIELD]
    assert displays == [(SHINY_DIALOG_REGION_FIELD, default_region)]
    assert recognitions == [(SHINY_DIALOG_REGION_FIELD, default_region)]
    assert dialog.table.item(row, 4).text() == "识别中…"
    assert dialog.interaction_busy
    assert all(not button.isEnabled() for button in dialog._row_action_buttons)

    dialog.finish_recognition(SHINY_DIALOG_REGION_FIELD, "出现了！")

    assert dialog.table.item(row, 4).text() == "出现了！"
    assert not dialog.interaction_busy
    assert all(button.isEnabled() for button in dialog._row_action_buttons)


def test_shiny_dialog_custom_region_persists_and_reset_restores_dynamic_default(app, tmp_path):
    settings = _settings(tmp_path)
    dialog = OcrSettingsDialog(settings=settings)
    custom_region = OcrRegion(10, 400, 800, 200)

    dialog.set_region(SHINY_DIALOG_REGION_FIELD, custom_region)

    assert dialog.region_config.get(SHINY_DIALOG_REGION_FIELD) == custom_region
    assert settings.value(f"regions/{SHINY_DIALOG_REGION_FIELD}") == "[10, 400, 800, 200]"
    restored = OcrSettingsDialog(settings=settings)
    assert restored.region_config.get(SHINY_DIALOG_REGION_FIELD) == custom_region

    restored.reset_region(SHINY_DIALOG_REGION_FIELD)

    row = restored._field_rows[SHINY_DIALOG_REGION_FIELD]
    assert restored.region_config.get(SHINY_DIALOG_REGION_FIELD) is None
    assert restored.resolve_region(SHINY_DIALOG_REGION_FIELD, (720, 1280, 3)) == OcrRegion(0, 360, 1280, 360)
    assert "当前帧下方50%" in restored.table.item(row, 1).text()
    assert restored.table.item(row, 2).text() == "默认"
    assert settings.value(f"regions/{SHINY_DIALOG_REGION_FIELD}") == ""


def test_ocr_settings_dialog_test_all_excludes_timing_regions(app, tmp_path):
    dialog = OcrSettingsDialog(settings=_settings(tmp_path))
    emitted = []
    timing_fields = (SHINY_DIALOG_REGION_FIELD, STARTER_BATTLE_REGION_FIELD)
    dialog.fullTestRequested.connect(lambda: emitted.append(True))
    for field in timing_fields:
        dialog.set_recognition_result(field, "保留结果")

    dialog.test_all()

    assert emitted == [True]
    assert all(dialog.table.item(dialog._field_rows[field], 4).text() == "保留结果" for field in timing_fields)
    dialog.finish_full_test(True, "测试全部完成")


def test_ocr_settings_dialog_automation_active_keeps_only_display_actions(app, tmp_path):
    dialog = OcrSettingsDialog(settings=_settings(tmp_path))
    default_region = _EXPECTED_DEFAULT_OCR_REGIONS[SHINY_DIALOG_REGION_FIELD]
    selections = []
    displays = []
    recognitions = []
    dialog.regionSelectionRequested.connect(selections.append)
    dialog.regionDisplayRequested.connect(lambda field, region: displays.append((field, region)))
    dialog.recognitionRequested.connect(lambda field, region: recognitions.append((field, region)))

    dialog.set_automation_active(True)

    display_buttons = [button for button in dialog._row_action_buttons if button.text() == "显示"]
    other_buttons = [button for button in dialog._row_action_buttons if button.text() != "显示"]
    assert all(button.isEnabled() for button in display_buttons)
    assert all(not button.isEnabled() for button in other_buttons)
    assert not dialog.warmup_button.isEnabled()
    assert not dialog.test_current_button.isEnabled()
    assert not dialog.test_all_button.isEnabled()
    assert not dialog.defaults_button.isEnabled()

    dialog.request_selection(SHINY_DIALOG_REGION_FIELD)
    dialog.show_region(SHINY_DIALOG_REGION_FIELD)
    dialog.recognize_field(SHINY_DIALOG_REGION_FIELD)
    dialog.set_region(SHINY_DIALOG_REGION_FIELD, OcrRegion(1, 2, 30, 40))

    assert selections == []
    assert displays == [(SHINY_DIALOG_REGION_FIELD, default_region)]
    assert recognitions == []
    assert dialog.region_config.get(SHINY_DIALOG_REGION_FIELD) == default_region

    dialog.set_automation_active(False)
    assert all(button.isEnabled() for button in dialog._row_action_buttons)


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


def test_ocr_settings_dialog_reset_marks_region_unset(app, tmp_path):
    settings = _settings(tmp_path)
    dialog = OcrSettingsDialog(settings=settings)
    dialog.set_region("speed", OcrRegion(10, 20, 30, 40))

    dialog.reset_region("speed")

    assert dialog.region_config.get("speed") is None
    assert dialog.table.item(7, 1).text() == "未设置"
    assert settings.contains("regions/speed")
    assert settings.value("regions/speed") == ""


def test_ocr_settings_dialog_reset_regions_stay_unset_after_reopen(app, tmp_path):
    settings = _settings(tmp_path)
    dialog = OcrSettingsDialog(settings=settings)
    dialog.reset_region("speed")

    restored = OcrSettingsDialog(settings=settings)

    assert restored.region_config.get("speed") is None
    assert restored.region_config.get("nature") == DEFAULT_OCR_REGIONS["nature"]

    for field in OCR_REGION_FIELDS:
        restored.reset_region(field)

    empty_restored = OcrSettingsDialog(settings=settings)
    assert empty_restored.region_config.is_empty()


def test_ocr_settings_dialog_can_reimport_default_regions(app, tmp_path):
    settings = _settings(tmp_path)
    dialog = OcrSettingsDialog(settings=settings)
    dialog.set_region("nature", OcrRegion(1, 2, 30, 40))
    dialog.reset_region("speed")

    dialog.import_default_regions()

    assert dict(dialog.region_config.items()) == DEFAULT_OCR_REGIONS
    restored = OcrSettingsDialog(settings=settings)
    assert dict(restored.region_config.items()) == DEFAULT_OCR_REGIONS


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

    assert window._ocr_warmup_thread is None
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


def test_main_window_manual_ocr_uses_fresh_frame_in_managed_task(app, monkeypatch):
    import auto_bdsp_rng.ui.main_window as main_window_module

    window = MainWindow()
    window.open_ocr_settings()
    window._ocr_warmup_result = (True, "OCR预热完成")
    dialog = window._ocr_settings_dialog
    assert dialog is not None
    dialog.set_region("nature", OcrRegion(10, 20, 30, 40))
    stale_frame = object()

    class FreshFrame:
        shape = (720, 1280, 3)

    fresh_frame = FreshFrame()
    window._latest_preview_frame = stale_frame
    task_labels = []
    recognized = []

    def run_immediately(label, task, completed):
        task_labels.append(label)
        try:
            payload = task(lambda: False)
        except BaseException as exc:
            completed(False, exc)
        else:
            completed(True, payload)
        return True

    monkeypatch.setattr(window, "_config_from_form", lambda: type("Config", (), {"capture": object()})())
    monkeypatch.setattr(window, "_capture_preview_frame_for_config", lambda _config: fresh_frame)
    monkeypatch.setattr(window, "_start_managed_ocr_task", run_immediately)
    monkeypatch.setattr(
        main_window_module,
        "recognize_ocr_field",
        lambda frame, field, region: recognized.append((frame, field, region)) or "胆小",
    )

    dialog.recognize_field("nature")

    assert task_labels == ["性格识别"]
    assert recognized == [(fresh_frame, "nature", OcrRegion(10, 20, 30, 40))]
    assert window._latest_preview_frame is fresh_frame
    assert dialog.table.item(dialog._field_rows["nature"], 4).text() == "胆小"


def test_main_window_full_ocr_test_uses_right_between_notes_and_stats(app, monkeypatch):
    import auto_bdsp_rng.ui.main_window as main_window_module

    window = MainWindow()
    window.open_ocr_settings()
    window._ocr_warmup_result = (True, "OCR预热完成")
    dialog = window._ocr_settings_dialog
    for field in tuple(dialog.region_config._regions):
        dialog.reset_region(field)
    dialog.set_region("nature", OcrRegion(1, 1, 10, 10))
    dialog.set_region("characteristic", OcrRegion(2, 2, 10, 10))
    dialog.set_region("hp", OcrRegion(3, 3, 10, 10))
    events = []
    frames = iter(["notes_frame", "stats_frame"])

    monkeypatch.setattr(window, "_capture_preview_frame_for_config", lambda _config: next(frames))
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


def test_main_window_full_ocr_task_cancels_before_page_switch(app, monkeypatch):
    import auto_bdsp_rng.ui.main_window as main_window_module

    window = MainWindow()
    window.open_ocr_settings()
    window._ocr_warmup_result = (True, "OCR预热完成")
    dialog = window._ocr_settings_dialog
    for field in tuple(dialog.region_config._regions):
        dialog.reset_region(field)
    dialog.set_region("nature", OcrRegion(1, 1, 10, 10))
    dialog.set_region("hp", OcrRegion(2, 2, 10, 10))
    tasks = []
    recognized = []
    page_switches = []

    monkeypatch.setattr(window, "_config_from_form", lambda: type("Config", (), {"capture": object()})())
    monkeypatch.setattr(window, "_capture_preview_frame_for_config", lambda _config: "notes_frame")
    monkeypatch.setattr(window, "_start_managed_ocr_task", lambda _label, task, _completed: tasks.append(task) or True)
    monkeypatch.setattr(window, "_send_easycon_right", lambda: page_switches.append(True))
    monkeypatch.setattr(
        main_window_module,
        "recognize_ocr_field",
        lambda _frame, field, _region: recognized.append(field) or "胆小",
    )

    dialog.test_all()

    assert len(tasks) == 1
    with pytest.raises(InterruptedError, match="OCR 测试已取消"):
        tasks[0](lambda: bool(recognized))
    assert recognized == ["nature"]
    assert page_switches == []
