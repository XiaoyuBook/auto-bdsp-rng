from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, QSettings, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QListView, QToolButton, QWidget

from auto_bdsp_rng.blink_detection import BlinkCaptureConfig, ProjectXsTrackingConfig
from auto_bdsp_rng.automation.auto_rng.ocr_regions import OcrRegion
from auto_bdsp_rng.automation.auto_tid_rng import AutoTidRngConfig, AutoTidRngPhase, AutoTidRngProgress
from auto_bdsp_rng.gen8_id import IDState8
from auto_bdsp_rng.rng_core import SeedPair64, SeedState32
from auto_bdsp_rng.ui import MainWindow
import auto_bdsp_rng.ui.main_window as main_window_module
from auto_bdsp_rng.ui.auto_tid_rng_panel import AutoTidRngPanel
from auto_bdsp_rng.ui.tid_ocr_dialog import TidOcrDialog


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    application = QApplication.instance() or QApplication([])
    yield application
    for widget in application.topLevelWidgets():
        for timer in widget.findChildren(QTimer):
            timer.stop()
        widget.close()
        widget.deleteLater()
    application.processEvents()


def _settings(tmp_path: Path) -> QSettings:
    return QSettings(str(tmp_path / "tid_ocr.ini"), QSettings.Format.IniFormat)


def test_auto_tid_panel_builds_config_with_target_list(app, tmp_path: Path) -> None:
    seed_script = tmp_path / "BDSP测种.txt"
    name_script = tmp_path / "取名.txt"
    for path in (seed_script, name_script):
        path.write_text("A 100\n", encoding="utf-8")
    panel = AutoTidRngPanel(script_dir=tmp_path)
    panel.target_list.clear()
    panel.frame_threshold.setValue(300)
    panel.delay.setValue(20)
    panel.add_target_display_tid(1)
    panel.add_target_display_tid(222222)
    panel.seed_script_combo.setCurrentIndex(panel.seed_script_combo.findText(seed_script.name))
    panel.name_script_combo.setCurrentIndex(panel.name_script_combo.findText(name_script.name))

    config = panel.build_config()

    assert isinstance(config, AutoTidRngConfig)
    assert config.frame_threshold == 300
    assert config.delay == 20
    assert config.target_display_tids == (1, 222222)
    assert config.seed_script_path == seed_script
    assert config.name_script_path == name_script


def test_auto_tid_panel_migrates_legacy_internal_script_settings(app, tmp_path: Path) -> None:
    script_dir = tmp_path / "script"
    script_dir.mkdir()
    seed_script = script_dir / "自定义测种.txt"
    name_script = script_dir / "自定义取名.txt"
    for path in (seed_script, name_script):
        path.write_text("A 100\n", encoding="utf-8")
    settings = _settings(tmp_path)
    settings.setValue(
        "seed_script",
        str(tmp_path / "_internal" / "script" / seed_script.name),
    )
    settings.setValue(
        "name_script",
        str(tmp_path / "_internal" / "script" / name_script.name),
    )

    panel = AutoTidRngPanel(script_dir=script_dir, settings=settings)
    panel.add_target_display_tid(123456)

    assert panel.seed_script_combo.currentData() == str(seed_script)
    assert panel.name_script_combo.currentData() == str(name_script)
    assert panel.build_config().seed_script_path == seed_script
    assert panel.build_config().name_script_path == name_script
    assert settings.value("seed_script") == str(seed_script)
    assert settings.value("name_script") == str(name_script)


def test_auto_tid_panel_can_start_from_capture_seed_via_menu(app, tmp_path: Path) -> None:
    seed_script = tmp_path / "BDSP娴嬬.txt"
    name_script = tmp_path / "鍙栧悕.txt"
    for path in (seed_script, name_script):
        path.write_text("A 100\n", encoding="utf-8")
    panel = AutoTidRngPanel(script_dir=tmp_path)
    panel.add_target_display_tid(123456)
    panel.seed_script_combo.setCurrentIndex(panel.seed_script_combo.findText(seed_script.name))
    panel.name_script_combo.setCurrentIndex(panel.name_script_combo.findText(name_script.name))
    emitted: list[AutoTidRngConfig] = []
    panel.startRequested.connect(emitted.append)

    panel.start_from_capture_action.trigger()

    assert emitted
    assert emitted[-1].start_phase == AutoTidRngPhase.CAPTURE_TIDSID


def test_auto_tid_start_button_uses_split_menu_for_capture_seed(app, tmp_path: Path) -> None:
    panel = AutoTidRngPanel(script_dir=tmp_path)

    assert panel.start_button.menu() is panel.start_menu
    assert panel.start_button.popupMode() == QToolButton.ToolButtonPopupMode.MenuButtonPopup
    assert panel.start_from_capture_action in panel.start_menu.actions()


def test_auto_tid_panel_replaces_log_with_operable_id_table(app, tmp_path: Path) -> None:
    panel = AutoTidRngPanel(script_dir=tmp_path)

    panel.set_id_states([
        IDState8(advances=5, tid=10, sid=20, tsv=1, display_tid=1),
        IDState8(advances=42, tid=30, sid=40, tsv=2, display_tid=123456),
    ])

    assert panel.id_table.rowCount() == 2
    assert panel.id_table.item(1, 4).text() == "123456"
    assert panel.log_view.isVisible() is False
    assert panel.copy_button.isEnabled()
    assert panel.export_button.isEnabled()


def test_auto_tid_log_sink_receives_failure_once_and_cannot_break_ui(app, tmp_path: Path) -> None:
    events: list[tuple[str, str]] = []
    panel = AutoTidRngPanel(
        script_dir=tmp_path,
        run_log_sink=lambda level, message: events.append((level, message)),
    )
    progress = AutoTidRngProgress(phase=AutoTidRngPhase.FAILED, log_message="TID 流程失败")

    panel.add_log("普通事件")
    panel.apply_progress(progress)
    panel._runner_finished(progress)

    assert events == [
        ("INFO", "普通事件"),
        ("ERROR", "TID 流程失败"),
    ]

    def broken_sink(_level: str, _message: str) -> None:
        raise OSError("disk full")

    panel._run_log_sink = broken_sink
    panel.add_log("仍写入界面")

    assert "仍写入界面" in panel.log_view.toPlainText()


def test_auto_tid_stop_button_passes_user_reason_to_worker(app, tmp_path: Path) -> None:
    panel = AutoTidRngPanel(script_dir=tmp_path)
    reasons: list[str] = []
    emitted: list[bool] = []
    panel._runner_worker = SimpleNamespace(request_stop=lambda reason: reasons.append(reason))  # type: ignore[assignment]
    panel.stopRequested.connect(lambda: emitted.append(True))

    panel._stop_clicked()

    assert reasons == ["用户点击停止按钮"]
    assert emitted == [True]


def test_preview_failure_logs_broker_diagnostics_and_tid_stop_reason(app, monkeypatch, tmp_path: Path, request) -> None:
    class Process:
        status = "failed"
        failure = "共享视频源连续无新帧超过 1 秒"
        process = SimpleNamespace(poll=lambda: 2)

        @staticmethod
        def stop() -> bool:
            return True

    window = MainWindow(capture_broker_process=Process())
    window._video_source_connected = True
    window._preview_timer.start()
    window.auto_tid_rng_tab._runner_thread = object()  # type: ignore[assignment]
    reasons: list[str] = []
    window.auto_tid_rng_tab._runner_worker = SimpleNamespace(  # type: ignore[assignment]
        request_stop=lambda reason: reasons.append(reason),
    )

    def cleanup() -> None:
        window.auto_tid_rng_tab._runner_worker = None
        window.auto_tid_rng_tab._runner_thread = None
        window.close()
        app.processEvents()

    request.addfinalizer(cleanup)
    logs: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        window,
        "_write_run_log",
        lambda source, message, **kwargs: logs.append((str(source), str(message), str(kwargs.get("level")))),
    )
    monkeypatch.setattr(window, "_show_error", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(window, "_config_from_form", lambda: SimpleNamespace(capture=object()))

    def read_failure(_config):
        try:
            raise ValueError("no new frame")
        except ValueError as cause:
            raise RuntimeError("read wrapper") from cause

    monkeypatch.setattr(window, "_read_live_preview_frame", read_failure)

    window._update_preview_frame()

    assert reasons and reasons[0].startswith("视频源读帧失败")
    assert len(logs) == 1
    source, message, level = logs[0]
    assert source == "视频源"
    assert level == "ERROR"
    assert "异常链=RuntimeError: read wrapper <- ValueError: no new frame" in message
    assert "broker_state=failed" in message
    assert "broker_failure=共享视频源连续无新帧超过 1 秒" in message
    assert "broker_exit=2" in message


def test_video_source_diagnostic_rejects_replaced_manifest_session(app, monkeypatch, tmp_path: Path) -> None:
    class Process:
        process = SimpleNamespace(pid=1111, poll=lambda: 2)
        manifest_path = tmp_path / "capture-broker.json"
        _session_id = "owned-session"
        device_index = 3
        capture_api = 1400

        @staticmethod
        def status():
            return "failed"

        @staticmethod
        def failure():
            return "旧会话已失败"

    replaced = SimpleNamespace(
        pid=2222,
        session_id="replacement-session",
        state="running",
        failure_message="新会话的状态",
        frame_timeout_seconds=1.0,
        capture={"device_index": 9, "api": 700},
    )
    monkeypatch.setattr(
        "auto_bdsp_rng.capture_broker.BrokerManifest.load",
        lambda _path: replaced,
    )
    window = MainWindow(capture_broker_process=Process())

    diagnostic = window._video_source_diagnostic_snapshot()

    assert "broker_pid=1111" in diagnostic
    assert "broker_session=owned-session" in diagnostic
    assert "manifest_path=" in diagnostic
    assert "manifest_identity=mismatch/" in diagnostic
    assert "manifest_pid=2222" in diagnostic
    assert "manifest_session=replacement-session" in diagnostic
    assert "manifest_failure=" not in diagnostic
    assert "capture_device_index=3" in diagnostic
    assert "capture_api=1400" in diagnostic


def test_auto_tid_panel_keeps_targets_compact_and_gives_id_table_space(app, tmp_path: Path) -> None:
    panel = AutoTidRngPanel(script_dir=tmp_path)

    assert panel.target_list.maximumHeight() <= 140
    assert panel.target_list.viewMode() == QListView.ViewMode.IconMode
    assert panel.target_list.flow() == QListView.Flow.LeftToRight
    assert panel.target_list.isWrapping()
    assert panel.id_table.minimumHeight() >= 320
    assert panel.id_table.horizontalHeader().stretchLastSection()


def test_auto_tid_content_is_added_directly_below_toolbar(app, tmp_path: Path) -> None:
    panel = AutoTidRngPanel(script_dir=tmp_path)

    content = panel.layout().itemAt(1).widget()
    toolbar = panel.layout().itemAt(0).widget()

    assert content is not None
    assert content.objectName() == "AutoTidContent"
    assert content.parentWidget() is panel
    assert not hasattr(panel, "content_scroll")
    assert toolbar is not None


def test_auto_tid_top_controls_put_params_and_scripts_in_one_row(app, tmp_path: Path) -> None:
    panel = AutoTidRngPanel(script_dir=tmp_path)

    top_controls = panel.findChild(QWidget, "AutoTidTopControls")

    assert top_controls is not None
    assert panel.frame_threshold.parentWidget() is top_controls
    assert panel.delay.parentWidget() is top_controls
    assert panel.seed_script_combo.parentWidget() is top_controls
    assert panel.name_script_combo.parentWidget() is top_controls
    assert panel.refresh_scripts_button.parentWidget() is top_controls
    assert panel.frame_threshold.maximumWidth() <= 140
    assert panel.delay.maximumWidth() <= 120
    assert panel.seed_script_combo.maximumWidth() <= 220
    assert panel.name_script_combo.maximumWidth() <= 220


def test_auto_tid_panel_shows_target_count_in_wrapped_target_list(app, tmp_path: Path) -> None:
    panel = AutoTidRngPanel(script_dir=tmp_path)
    panel.target_list.clear()

    for value in range(12):
        panel.add_target_display_tid(value)

    assert panel.target_count_label.text() == "12 个目标"
    assert panel.target_list.gridSize().width() >= 80
    assert panel.target_list.gridSize().height() <= 36


def test_auto_tid_target_chips_show_close_marker_but_store_plain_tids(app, tmp_path: Path) -> None:
    panel = AutoTidRngPanel(script_dir=tmp_path)
    panel.target_list.clear()

    panel.add_target_display_tid(1)
    panel.add_target_display_tid(123456)

    assert panel.target_list.item(0).text() == "000001 ×"
    assert panel.target_list.item(1).text() == "123456 ×"
    assert panel.target_display_tids() == (1, 123456)


def test_auto_tid_target_chip_close_marker_deletes_that_target(app, tmp_path: Path) -> None:
    panel = AutoTidRngPanel(script_dir=tmp_path)
    panel.target_list.clear()
    panel.add_target_display_tid(1)
    panel.add_target_display_tid(123456)
    panel.show()
    app.processEvents()

    item = panel.target_list.item(0)
    rect = panel.target_list.visualItemRect(item)
    close_pos = rect.topRight() + QPoint(-8, rect.height() // 2)

    QTest.mouseClick(panel.target_list.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, close_pos)

    assert panel.target_display_tids() == (123456,)
    assert panel.target_count_label.text() == "1 个目标"


def test_auto_tid_target_pool_has_multiline_space_and_bulk_add(app, tmp_path: Path) -> None:
    panel = AutoTidRngPanel(script_dir=tmp_path)
    panel.target_list.clear()

    panel.target_input.setText("000001, 123456 654321\n999999")
    panel._add_target_from_input()

    assert panel.target_display_tids() == (1, 123456, 654321, 999999)
    assert panel.target_count_label.text() == "4 个目标"
    assert panel.target_list.objectName() == "TargetPool"
    assert panel.target_list.minimumHeight() >= panel.target_list.gridSize().height() * 3
    assert panel.target_list.maximumHeight() <= 150
    target_actions = panel.findChild(QWidget, "TargetPoolActions")
    assert target_actions is not None
    assert panel.target_input.parentWidget() is target_actions
    assert panel.add_target_button.parentWidget() is target_actions
    assert panel.clear_targets_button.parentWidget() is target_actions
    assert panel.target_input.maximumWidth() <= 360
    assert target_actions.minimumHeight() >= panel.target_list.gridSize().height() * 3
    assert target_actions.maximumHeight() <= panel.target_list.maximumHeight()
    assert panel.delete_target_button.isVisible() is False

    panel.clear_targets_button.click()

    assert panel.target_display_tids() == ()
    assert panel.target_count_label.text() == "0 个目标"
    assert panel.findChildren(type(panel.target_count_label), "SectionTitle") == []


def test_auto_tid_panel_seed_display_generates_id_table_from_threshold(app, tmp_path: Path) -> None:
    panel = AutoTidRngPanel(script_dir=tmp_path)
    seed_pair = SeedPair64(0x0123456789ABCDEF, 0x0FEDCBA987654321)
    panel.frame_threshold.setValue(4)

    panel.set_tid_seed(seed_pair)

    assert [box.text() for box in panel.tid_seed_inputs] == list(seed_pair.format_seeds())
    assert panel.id_table.rowCount() == 5
    assert panel.id_table.item(0, 0).text() == "0"
    assert panel.id_result_count.text() == "5 条结果"


def test_tid_ocr_dialog_is_non_modal_and_has_two_primary_actions(app, tmp_path: Path) -> None:
    dialog = TidOcrDialog(settings=_settings(tmp_path))

    assert not dialog.isModal()
    assert dialog.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert dialog.recognize_button.text() == "识别当前内容"
    assert dialog.select_button.text() == "框选范围"


def test_tid_ocr_dialog_saves_region_and_runs_on_demand_recognizer(app, tmp_path: Path) -> None:
    dialog = TidOcrDialog(settings=_settings(tmp_path), recognizer=lambda region: f"{region.x:05d}")
    dialog.set_region(OcrRegion(123, 20, 30, 40))

    dialog.recognize_current()

    assert dialog.region == OcrRegion(123, 20, 30, 40)
    assert dialog.result_value.text() == "00123"
    restored = TidOcrDialog(settings=_settings(tmp_path))
    assert restored.region == OcrRegion(123, 20, 30, 40)


def test_main_window_starts_auto_tid_runner_from_panel_signal(app, tmp_path: Path, monkeypatch) -> None:
    window = MainWindow()
    seed_script = tmp_path / "BDSP测种.txt"
    name_script = tmp_path / "取名.txt"
    reverse_script = tmp_path / "反查ID.txt"
    for path in (seed_script, name_script, reverse_script):
        path.write_text("A 100\n", encoding="utf-8")
    config = AutoTidRngConfig(
        script_dir=tmp_path,
        seed_script_path=seed_script,
        name_script_path=name_script,
        frame_threshold=300,
        target_display_tids=(1,),
        delay=20,
    )
    started = []
    window._latest_preview_frame = object()
    monkeypatch.setattr(window, "_ensure_bridge_connected", lambda: True)
    monkeypatch.setattr(window.auto_tid_rng_tab, "run_with_runner", started.append)

    window._start_auto_tid_rng(config)

    assert len(started) == 1
    assert started[0].config == config


def test_main_window_styles_keep_auto_tid_target_pool_multiline(app) -> None:
    window = MainWindow()
    panel = window.auto_tid_rng_tab
    panel.target_list.clear()
    for value in (1, 123456, 654321, 777777, 888888, 999999, 135790, 246800, 314159, 271828, 424242, 515151):
        panel.add_target_display_tid(value)

    window.show()
    app.processEvents()

    assert panel.target_list.minimumHeight() >= panel.target_list.gridSize().height() * 3
    assert panel.target_list.verticalScrollBar().isVisible() is False


def test_main_window_auto_tid_capture_uses_64_munchlax_blinks(app, tmp_path: Path, monkeypatch) -> None:
    window = MainWindow()
    loaded: list[tuple[str, int]] = []
    captured: list[int] = []
    warmup_windows: list[float | None] = []
    seed_state = SeedState32(0xAAAAAAAA, 0xBBBBBBBB, 0xCCCCCCCC, 0xDDDDDDDD)

    def fake_load_config(path, blink_count):
        loaded.append((str(path), blink_count))
        return ProjectXsTrackingConfig(
            source_path=tmp_path / Path(str(path)).name,
            capture=BlinkCaptureConfig(
                eye_image_path=tmp_path / "eye.png",
                roi=(0, 0, 1, 1),
                blink_count=blink_count,
            ),
        )

    def fake_capture(config, **kwargs):
        captured.append(config.blink_count)
        warmup_windows.append(kwargs.get("discard_first_blink_within_seconds"))
        kwargs["progress_callback"](config.blink_count, config.blink_count)
        return SimpleNamespace(intervals=[])

    monkeypatch.setattr(main_window_module, "load_project_xs_config", fake_load_config)
    monkeypatch.setattr(main_window_module, "capture_pokemon_blinks", fake_capture)
    monkeypatch.setattr(
        main_window_module,
        "recover_tidsid_seed_from_observation",
        lambda observation: SimpleNamespace(state=seed_state, observation=observation),
    )
    services = window._build_auto_tid_rng_services(AutoTidRngConfig(script_dir=tmp_path))

    result = services.capture_seed()

    assert loaded[-1][1] == 64
    assert captured == [64]
    assert warmup_windows == [1.0]
    assert result.seed == seed_state.to_seed_pair64()
    assert [box.text() for box in window.auto_tid_rng_tab.tid_seed_inputs] == list(seed_state.format_seed64_pair())
    assert window.auto_tid_rng_tab.id_table.rowCount() == window.auto_tid_rng_tab.frame_threshold.value() + 1


def test_main_window_tid_ocr_region_selection_confirm_emits_region(app, monkeypatch) -> None:
    window = MainWindow()
    emitted = []
    window.tidOcrRegionSelected.connect(emitted.append)
    window._selection_mode = "tid_ocr_region"
    window.preview_label.set_selection_enabled(True)
    monkeypatch.setattr(window, "_confirm_preview_selection", lambda _roi: True)

    window._handle_preview_selection((10, 20, 30, 40))

    assert emitted == [(10, 20, 30, 40)]
    assert window._selection_mode is None
    assert window.preview_label._ocr_overlay_field == "tid"
