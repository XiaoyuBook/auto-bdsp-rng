from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import numpy as np

pytest.importorskip("PySide6")

from auto_bdsp_rng.blink_detection import (
    BlinkCaptureConfig,
    BlinkObservation,
    ProjectXsIntegrationError,
    ProjectXsReidentifyResult,
    ProjectXsTrackingConfig,
    SeedState32,
)
from PySide6.QtCore import QPoint, QPointF, QSettings, QSize, QThread, QTimer, Qt
from PySide6.QtGui import QPaintEvent, QPixmap, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractItemView, QAbstractSpinBox, QApplication, QFileDialog, QGridLayout, QGroupBox, QLabel, QMessageBox, QPushButton, QScrollArea, QSizePolicy

from auto_bdsp_rng.automation.auto_rng import AutoRngConfig, AutoRngPhase, AutoRngProgress, AutoRngSeedResult, AutoRngTarget
from auto_bdsp_rng.automation.auto_rng.dialog_timing import DialogTimingResult
from auto_bdsp_rng.automation.auto_rng.ocr_regions import OcrRegion, OcrRegionConfig
from auto_bdsp_rng.automation.auto_rng.runner import AutoRngRunner
from auto_bdsp_rng.automation.auto_tid_rng import AutoTidRngConfig, ProjectXsMunchlaxAdvanceCounter
from auto_bdsp_rng.automation.easycon import EasyConInstallation, EasyConRunResult, EasyConStatus
from auto_bdsp_rng.gen8_static import State8, StateFilter
from auto_bdsp_rng.rng_core import BDSPXorshift, SeedPair64
from auto_bdsp_rng.ui import MainWindow
import auto_bdsp_rng.ui.main_window as main_window_module
from auto_bdsp_rng.automation.auto_rng.runner import _NATURE_MAP
from auto_bdsp_rng.ui.main_window import (
    NATURES_ZH,
    PictureInPicturePreview,
    _draw_easycon_search_overlay,
    _normalize_iv_ranges,
    _reverse_lookup_search_span,
    _reverse_species_label,
)
from auto_bdsp_rng.ui.auto_rng_panel import AutoRngPanel, AutoRngWorker
from auto_bdsp_rng.ui.history_panel import HistoryPanel


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


def _set_bdsp_seed(window: MainWindow) -> None:
    window.bdsp_seed64_inputs[0].setText("123456789ABCDEF0")
    window.bdsp_seed64_inputs[1].setText("1111111122222222")


def _auto_rng_settings(tmp_path: Path) -> QSettings:
    settings = QSettings(str(tmp_path / "auto_rng.ini"), QSettings.Format.IniFormat)
    settings.clear()
    return settings


def _profile_settings(tmp_path: Path) -> QSettings:
    settings = QSettings(str(tmp_path / "profile.ini"), QSettings.Format.IniFormat)
    settings.clear()
    return settings


def _project_xs_munchlax_interval(state: SeedState32) -> float:
    rng = BDSPXorshift(state)
    temp = (rng.next() & 0x7FFFFF) / 8388607.0
    return temp * 3.0 + (1.0 - temp) * 12.0 + 0.285


def test_main_window_generates_static_results(app):
    window = MainWindow()

    assert [window.tabs.tabText(index) for index in range(window.tabs.count())] == [
        "自动定点乱数",
        "自动 TID 乱数",
        "Seed 捕捉",
        "定点数据区",
        "伊机控",
        "历史记录",
    ]
    assert window.tabs.tabText(window.tabs.currentIndex()) == "自动定点乱数"
    assert hasattr(window, "id_tab")

    window.tabs.setCurrentWidget(window.bdsp_tab)
    _set_bdsp_seed(window)
    window.max_advances.setText("2")
    window.generate_results()

    assert window.table.rowCount() == 3
    assert window.result_count.text() == "3 条结果"
    assert window.table.item(0, 0).text() == "0"
    assert window.table.item(0, 1).text()


def test_main_window_does_not_warm_ocr_until_requested(app, monkeypatch):
    started = []

    monkeypatch.setattr(MainWindow, "_start_ocr_warmup", lambda self: started.append(self))

    window = MainWindow()
    app.processEvents()

    assert started == []
    assert window._ocr_warmup_result is None


def test_static_generation_runs_in_background(app, monkeypatch):
    window = MainWindow()
    window.tabs.setCurrentWidget(window.bdsp_tab)
    _set_bdsp_seed(window)
    window.max_advances.setText("10000000")

    def slow_generate(_criteria):
        time.sleep(0.2)
        return []

    monkeypatch.setattr(main_window_module, "generate_static_candidates", slow_generate)

    started = time.perf_counter()
    window.generate_results()
    elapsed = time.perf_counter() - started

    assert elapsed < 0.1
    assert not window.generate_button.isEnabled()
    deadline = time.perf_counter() + 2
    while not window.generate_button.isEnabled() and time.perf_counter() < deadline:
        app.processEvents()
        QTest.qWait(10)
    assert window.generate_button.isEnabled()


def test_bdsp_max_advances_matches_pokefinder_limit(app):
    window = MainWindow()

    assert window.max_advances.validator().top() == 1_000_000_000
    assert window.max_advances.text() == "100000"


def test_bdsp_filter_tools_do_not_overlap_speed_row(app):
    window = MainWindow()
    window.tabs.setCurrentWidget(window.bdsp_tab)
    window.show()
    app.processEvents()
    speed_min = window.iv_min[5]
    show_stats = window.show_stats_check

    assert window.national_dex.text() == "全国图鉴"
    assert window.shiny_charm.text() == "闪耀护符"
    assert window.oval_charm.text() == "圆形护符"
    assert speed_min.geometry().bottom() < show_stats.geometry().top()


def test_project_xs_controls_use_commit_0940b1b_left_layout(app):
    window = MainWindow()
    window.tabs.setCurrentWidget(window.project_xs_tab)
    window.resize(1280, 760)
    window.show()
    app.processEvents()

    capture = window.capture_group.geometry()
    seed = window.seed_group.geometry()

    assert seed.x() == capture.x()
    assert seed.y() > capture.bottom()
    assert window.window_prefix.parent() is window.capture_group
    assert all(not box.isVisible() for box in window.seed32_inputs)
    assert [label.text() for label in window.seed_group.findChildren(QLabel)] == ["Seed0", "Seed1"]
    assert window.threshold.height() <= 32
    assert window.capture_button.height() <= 34


def test_project_xs_status_group_uses_seed_and_reidentify_config_selectors(app):
    window = MainWindow()
    layout = window.status_group.layout()

    assert hasattr(window, "seed_config_combo")
    assert hasattr(window, "reidentify_config_combo")
    assert window.seed_config_combo.findText("config_bebe.json") >= 0
    assert window.reidentify_config_combo.findText("config_bebe.json") >= 0
    assert layout.itemAtPosition(0, 0).widget() is window.progress_label
    assert layout.itemAtPosition(0, 1).widget() is window.progress_value
    assert layout.itemAtPosition(0, 3).widget() is window.seed_config_combo
    assert layout.itemAtPosition(1, 0).widget() is window.advances_label
    assert layout.itemAtPosition(1, 1).widget() is window.advances_value
    assert layout.itemAtPosition(1, 2).widget().text() == "校正配置"
    assert layout.itemAtPosition(1, 3).widget() is window.reidentify_config_combo
    assert window.reidentify_button.text() == "校正"
    assert window.reidentify_1_pk_npc.text() == "1 PK NPC 校正"
    assert window.status_group.maximumHeight() >= 148
    assert window.status_group.maximumWidth() <= 760
    assert window.refresh_seed_configs_button.isHidden()
    assert window.preview_label.minimumHeight() <= 270
    assert not window.progress_label.isHidden()
    assert not window.advances_label.isHidden()
    assert window.timer_label.isHidden()
    assert window.advance_button.isHidden()


def test_preview_scaling_uses_physical_pixels_on_high_dpi(app):
    source = QPixmap(780, 460)

    scaled, logical_size = main_window_module._scale_preview_pixmap(source, QSize(480, 260), 1.5)

    assert scaled.devicePixelRatio() == 1.5
    assert scaled.height() == 390
    assert scaled.width() > 600
    assert logical_size.width() <= 480
    assert logical_size.height() <= 260


def test_preview_scaling_does_not_enlarge_low_resolution_source(app):
    source = QPixmap(780, 460)

    scaled, logical_size = main_window_module._scale_preview_pixmap(source, QSize(800, 600), 1.5)

    assert (scaled.width(), scaled.height()) == (780, 460)
    assert scaled.devicePixelRatio() == 1.5
    assert logical_size == QSize(520, 307)


def test_shared_video_source_keeps_preview_running_and_injects_broker_capture(app):
    class Client:
        def close(self):
            return None

    class BrokerProcess:
        def __init__(self):
            self.started = None
            self.stopped = False

        def start(self, *, device_index, capture_api):
            self.started = (device_index, capture_api)
            return True

        def client(self):
            return Client()

        def stop(self):
            self.stopped = True

    process = BrokerProcess()
    window = MainWindow(capture_broker_process=process)

    assert window.connect_video_source()
    deadline = time.perf_counter() + 2
    while not window._video_source_connected and time.perf_counter() < deadline:
        app.processEvents()
        QTest.qWait(5)
    config = window._config_from_form().capture

    assert process.started == (0, 700)
    assert config.uses_shared_video_source
    assert callable(config.frame_source_factory)
    assert window._preview_timer.isActive()
    assert window.preview_button.text() == "预览常驻"

    window._pause_preview_for_capture()
    assert window._preview_timer.isActive()

    assert window.disconnect_video_source(force=True)
    assert process.stopped
    assert not window._preview_timer.isActive()


def test_shared_video_source_connection_does_not_block_ui(app):
    class Client:
        def close(self):
            return None

    class BrokerProcess:
        def start(self, *, device_index, capture_api):
            del device_index, capture_api
            time.sleep(0.2)
            return True

        def client(self):
            return Client()

        def stop(self):
            return None

    window = MainWindow(capture_broker_process=BrokerProcess())

    started_at = time.perf_counter()
    assert window.connect_video_source()
    assert time.perf_counter() - started_at < 0.1
    assert window._video_source_connecting

    deadline = time.perf_counter() + 2
    while not window._video_source_connected and time.perf_counter() < deadline:
        app.processEvents()
        QTest.qWait(5)
    assert window._video_source_connected
    assert window.disconnect_video_source(force=True)


@pytest.mark.parametrize("running", [True, False])
def test_capture_broker_controller_cannot_change_while_start_attempt_is_pending(app, running):
    class StartThread:
        def isRunning(self):
            return running

    window = MainWindow()
    window._capture_broker_start_thread = StartThread()  # type: ignore[assignment]

    assert window.connect_video_source() is False
    with pytest.raises(RuntimeError, match="请先断开"):
        window.set_capture_broker_process(object())
    window._capture_broker_start_thread = None


def test_non_running_broker_start_attempt_disconnect_rejects_late_success(app):
    class StartThread:
        def __init__(self):
            self.interruptions = 0

        def isRunning(self):
            return False

        def requestInterruption(self):
            self.interruptions += 1

    class BrokerProcess:
        def __init__(self):
            self.stop_calls = 0

        def stop(self):
            self.stop_calls += 1
            return True

    thread = StartThread()
    process = BrokerProcess()
    window = MainWindow(capture_broker_process=process)
    window._capture_broker_start_thread = thread  # type: ignore[assignment]
    window._capture_broker_attempt = 7
    window._video_source_connecting = True
    window.video_source_button.setEnabled(False)
    window._preview_timer.start()

    assert window.disconnect_video_source(force=True) is True
    generation = window._video_source_generation
    status = window.video_source_status.text()

    assert process.stop_calls == 2
    assert thread.interruptions == 1
    assert window._capture_broker_start_thread is None
    assert not window._video_source_connecting
    assert not window._video_source_cancel_requested
    assert not window._video_source_connected
    assert not window._preview_timer.isActive()
    assert window.video_source_button.isEnabled()
    assert status == "连接已取消"

    window._finish_video_source_connection(
        thread,  # type: ignore[arg-type]
        7,
        process,
        0,
        700,
        True,
        None,
    )
    window._capture_broker_start_finished(thread, 7)  # type: ignore[arg-type]

    assert window._video_source_generation == generation
    assert not window._video_source_connected
    assert not window._preview_timer.isActive()
    assert window.video_source_status.text() == status


def test_running_broker_start_cancel_finally_stops_owner_created_after_initial_stop(app):
    class BrokerProcess:
        def __init__(self):
            self.start_entered = threading.Event()
            self.allow_start = threading.Event()
            self.started = False
            self.stop_calls: list[bool] = []

        def start(self, *, device_index, capture_api):
            del device_index, capture_api
            self.start_entered.set()
            if not self.allow_start.wait(2):
                return False
            self.started = True
            return True

        def client(self):
            return SimpleNamespace(close=lambda: None)

        def stop(self):
            self.stop_calls.append(self.started)
            self.started = False
            return True

    process = BrokerProcess()
    window = MainWindow(capture_broker_process=process)

    assert window.connect_video_source()
    assert process.start_entered.wait(1)
    assert window.disconnect_video_source(force=True) is False
    assert process.stop_calls == [False]

    process.allow_start.set()
    deadline = time.perf_counter() + 2
    while window._capture_broker_start_thread is not None and time.perf_counter() < deadline:
        app.processEvents()
        QTest.qWait(5)

    assert window._capture_broker_start_thread is None
    assert process.stop_calls == [False, True]
    assert not process.started
    assert not window._video_source_connected
    assert not window._video_source_connecting
    assert not window._video_source_cancel_requested
    assert window.video_source_button.isEnabled()
    assert window.video_source_status.text() == "连接已取消"


def test_non_running_broker_start_stop_failure_requires_retry_and_rejects_late_success(app):
    class StartThread:
        def isRunning(self):
            return False

        def requestInterruption(self):
            return None

    class BrokerProcess:
        stopped = False

        def stop(self):
            return self.stopped

    thread = StartThread()
    process = BrokerProcess()
    window = MainWindow(capture_broker_process=process)
    window._capture_broker_start_thread = thread  # type: ignore[assignment]
    window._capture_broker_attempt = 10
    window._video_source_connecting = True
    window.video_source_button.setEnabled(False)

    assert window.disconnect_video_source(force=True) is False

    assert window._capture_broker_start_thread is None
    assert not window._video_source_connected
    assert not window._video_source_connecting
    assert window._video_source_stop_pending
    assert window.video_source_button.isEnabled()
    assert window.video_source_button.text() == "重试断开"
    assert window.connect_video_source() is False
    with pytest.raises(RuntimeError, match="请先断开"):
        window.set_capture_broker_process(object())

    window._finish_video_source_connection(
        thread,  # type: ignore[arg-type]
        10,
        process,
        0,
        700,
        True,
        None,
    )
    window._capture_broker_start_finished(thread, 10)  # type: ignore[arg-type]

    assert not window._video_source_connected
    assert window._video_source_stop_pending

    process.stopped = True
    assert window.disconnect_video_source(force=True) is True
    assert not window._video_source_stop_pending
    assert window.video_source_button.text() == "连接视频源"


def test_close_stops_non_running_broker_start_attempt_before_queued_success(app, monkeypatch):
    class StartThread:
        def __init__(self):
            self.interruptions = 0

        def isRunning(self):
            return False

        def requestInterruption(self):
            self.interruptions += 1

    class BrokerProcess:
        def __init__(self):
            self.stop_calls = 0

        def stop(self):
            self.stop_calls += 1
            return True

    thread = StartThread()
    process = BrokerProcess()
    window = MainWindow(capture_broker_process=process)
    window._capture_broker_start_thread = thread  # type: ignore[assignment]
    window._capture_broker_attempt = 8
    window._video_source_connecting = True
    window.video_source_button.setEnabled(False)
    monkeypatch.setattr(window.easycon_tab, "shutdown", lambda: True)
    window.show()

    assert window.close() is True
    generation = window._video_source_generation

    assert process.stop_calls == 2
    assert thread.interruptions == 1
    assert window._capture_broker_start_thread is None
    assert not window._video_source_connected
    assert window.video_source_button.isEnabled()
    assert window.video_source_status.text() == "连接已取消"

    window._finish_video_source_connection(
        thread,  # type: ignore[arg-type]
        8,
        process,
        0,
        700,
        True,
        None,
    )
    window._capture_broker_start_finished(thread, 8)  # type: ignore[arg-type]

    assert window._video_source_generation == generation
    assert not window._video_source_connected
    assert not window._preview_timer.isActive()


def test_broker_start_finish_while_closing_restores_controls_after_shutdown_timeout(app):
    class StartThread:
        def __init__(self):
            self.running = True

        def isRunning(self):
            return self.running

        def requestInterruption(self):
            return None

        def wait(self, _wait_ms):
            return False

    thread = StartThread()
    process = SimpleNamespace(stop=lambda: True)
    window = MainWindow(capture_broker_process=process)
    window._capture_broker_start_thread = thread  # type: ignore[assignment]
    window._capture_broker_attempt = 9
    window._video_source_connected = True
    window._video_source_connecting = False

    assert window._shutdown_capture_broker_start_thread(wait_ms=0) is False
    assert window._capture_broker_start_thread is thread
    assert window._video_source_cancel_requested
    assert window._video_source_connecting
    assert not window.video_source_button.isEnabled()
    assert window.video_source_button.text() == "正在断开..."

    window._is_closing = True
    thread.running = False
    window._capture_broker_start_finished(thread, 9)  # type: ignore[arg-type]
    window._is_closing = False

    assert window._capture_broker_start_thread is None
    assert not window._video_source_connecting
    assert not window._video_source_cancel_requested
    assert window.video_source_button.isEnabled()
    assert window.video_source_status.text() == "连接已取消"


def test_stale_capture_broker_completion_cannot_publish_a_new_connection(app):
    current_thread = object()
    old_thread = object()
    process = object()
    window = MainWindow(capture_broker_process=process)
    window._capture_broker_start_thread = current_thread  # type: ignore[assignment]
    window._capture_broker_attempt = 2
    window._video_source_connecting = True

    window._finish_video_source_connection(
        old_thread,  # type: ignore[arg-type]
        1,
        process,
        0,
        700,
        True,
        None,
    )
    window._finish_video_source_connection(
        current_thread,  # type: ignore[arg-type]
        1,
        process,
        0,
        700,
        True,
        None,
    )

    assert window._video_source_connecting
    assert not window._video_source_connected
    window._capture_broker_start_thread = None
    window._video_source_connecting = False


def test_late_capture_broker_thread_finish_restores_connection_controls(app):
    thread = object()
    window = MainWindow()
    window._capture_broker_start_thread = thread  # type: ignore[assignment]
    window._capture_broker_attempt = 1
    window._video_source_connecting = True
    window._video_source_pending_status = "连接已取消"
    window.video_source_button.setEnabled(False)

    window._capture_broker_start_finished(thread, 1)  # type: ignore[arg-type]

    assert window._capture_broker_start_thread is None
    assert not window._video_source_connecting
    assert window.video_source_button.isEnabled()
    assert window.video_source_status.text() == "连接已取消"


def test_video_source_stop_failure_requires_an_explicit_retry(app):
    class BrokerProcess:
        stopped = False

        def stop(self):
            return self.stopped

    process = BrokerProcess()
    window = MainWindow(capture_broker_process=process)
    window._video_source_connected = True
    window._preview_timer.start()

    assert window.disconnect_video_source(force=True) is False
    assert not window._video_source_connected
    assert window._video_source_stop_pending
    assert window.video_source_button.text() == "重试断开"
    assert window.video_source_status.text() == "停止失败，请重试"
    assert not window.capture_device_combo.isEnabled()

    process.stopped = True
    assert window.disconnect_video_source(force=True) is True
    assert not window._video_source_stop_pending
    assert window.video_source_button.text() == "连接视频源"
    assert window.capture_device_combo.isEnabled()


def test_video_source_stop_exception_keeps_retry_state(app):
    class BrokerProcess:
        def stop(self):
            raise OSError("device owner is still running")

    window = MainWindow(capture_broker_process=BrokerProcess())
    window._video_source_connected = True

    assert window.disconnect_video_source(force=True) is False
    assert window._video_source_stop_pending
    assert window._video_source_stop_error == "device owner is still running"
    assert window.video_source_button.text() == "重试断开"
    window._capture_broker_process = None
    window._video_source_stop_pending = False


def test_main_window_refuses_close_until_video_source_stop_is_confirmed(app, monkeypatch):
    class BrokerProcess:
        stopped = False

        def stop(self):
            return self.stopped

    process = BrokerProcess()
    window = MainWindow(capture_broker_process=process)
    window._video_source_connected = True
    window.show()
    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(window.easycon_tab, "shutdown", lambda: True)
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )

    assert window.close() is False
    assert window.isVisible()
    assert window._video_source_stop_pending
    assert warnings[-1][0] == "视频源未能停止"

    process.stopped = True
    assert window.close() is True


def test_capture_devices_use_selected_backend_and_real_indices(app, monkeypatch):
    calls: list[int] = []

    def enumerate_devices(capture_api: int) -> list[tuple[int, str]]:
        calls.append(capture_api)
        if capture_api == 0:
            return [(1400, "USB Video"), (700, "USB Video"), (701, "OBS Virtual Camera")]
        if capture_api == 1400:
            return [(0, "USB Video")]
        return [(0, "USB Video"), (1, "OBS Virtual Camera")]

    monkeypatch.setattr(
        main_window_module,
        "_enumerate_capture_devices",
        enumerate_devices,
    )
    window = MainWindow()
    window.capture_device_combo.setCurrentIndex(1)

    window.refresh_capture_devices()

    assert window.capture_device_combo.itemText(0) == "0 - USB Video"
    assert window.capture_device_combo.itemText(1) == "1 - OBS Virtual Camera"
    assert window._capture_device_index() == 1

    window.capture_api_combo.setCurrentIndex(window.capture_api_combo.findData(1400))

    assert window.capture_device_combo.count() == 1
    assert window.capture_device_combo.itemText(0) == "0 - USB Video"
    assert window._capture_device_index() == 0

    window.capture_api_combo.setCurrentIndex(window.capture_api_combo.findData(0))

    assert [window.capture_device_combo.itemData(row) for row in range(3)] == [1400, 700, 701]
    assert window._capture_device_index() == 1400
    assert calls[-3:] == [700, 1400, 0]


def test_enumerate_capture_devices_uses_library_indices(monkeypatch):
    cameras = [
        SimpleNamespace(index=1400, name=" USB Video "),
        SimpleNamespace(index=701, name="OBS Virtual Camera"),
    ]
    fake_module = SimpleNamespace(enumerate_cameras=lambda capture_api: cameras if capture_api == 0 else [])
    monkeypatch.setitem(__import__("sys").modules, "cv2_enumerate_cameras", fake_module)

    assert main_window_module._enumerate_capture_devices(0) == [
        (1400, "USB Video"),
        (701, "OBS Virtual Camera"),
    ]


def test_easycon_search_overlay_only_changes_display_copy():
    frame = np.zeros((24, 24, 3), dtype=np.uint8)
    result = SimpleNamespace(
        range_rect=(1, 1, 18, 18),
        match_rect=(6, 6, 5, 5),
    )

    annotated = _draw_easycon_search_overlay(frame, result)

    assert np.count_nonzero(frame) == 0
    assert annotated is not frame
    assert annotated[1, 1].tolist() == [0, 196, 255]
    assert annotated[6, 6].tolist() == [76, 210, 76]


def test_preview_annotation_failure_keeps_broker_and_raw_frame(app, monkeypatch):
    frame = np.zeros((24, 24, 3), dtype=np.uint8)
    stops: list[bool] = []
    process = SimpleNamespace(stop=lambda: stops.append(True) or True)
    window = MainWindow(capture_broker_process=process)
    window._video_source_connected = True
    warnings: list[str] = []
    monkeypatch.setattr(
        window,
        "_config_from_form",
        lambda: SimpleNamespace(capture=object()),
    )
    monkeypatch.setattr(window, "_read_live_preview_frame", lambda _config: frame)
    monkeypatch.setattr(
        main_window_module,
        "render_eye_preview",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("bad eye template")),
    )
    monkeypatch.setattr(
        window,
        "_write_run_log",
        lambda _source, message, **_kwargs: warnings.append(str(message)),
    )

    window._update_preview_frame()
    window._update_preview_frame()

    assert window._video_source_connected
    assert window._latest_preview_frame is not frame
    assert np.array_equal(window._latest_preview_frame, frame)
    assert np.array_equal(window._latest_annotated_preview_frame, frame)
    assert len(warnings) == 1
    assert "回退到原始画面" in warnings[0]
    assert stops == []
    window._video_source_connected = False


def test_invalid_preview_config_keeps_broker_raw_frames_updating(app, monkeypatch):
    frames = [
        np.zeros((24, 24, 3), dtype=np.uint8),
        np.full((24, 24, 3), 37, dtype=np.uint8),
    ]
    reads: list[object] = []
    stops: list[bool] = []
    warnings: list[str] = []
    process = SimpleNamespace(stop=lambda: stops.append(True) or True)
    window = MainWindow(capture_broker_process=process)
    picture_in_picture_frames: list[tuple[object, object]] = []
    window._picture_in_picture = SimpleNamespace(
        isVisible=lambda: True,
        set_frames=lambda raw, annotated: picture_in_picture_frames.append((raw, annotated)),
    )
    window._video_source_connected = True
    window._preview_timer.start()
    monkeypatch.setattr(
        window,
        "_config_from_form",
        lambda: (_ for _ in ()).throw(ValueError("editing ROI")),
    )

    def read_frame(_config):
        frame = frames[len(reads)]
        reads.append(frame)
        return frame

    monkeypatch.setattr(window, "_read_live_preview_frame", read_frame)
    monkeypatch.setattr(
        main_window_module,
        "render_eye_preview",
        lambda *_args: pytest.fail("invalid annotation config must not be rendered"),
    )
    monkeypatch.setattr(
        window,
        "_write_run_log",
        lambda _source, message, **_kwargs: warnings.append(str(message)),
    )

    window._update_preview_frame()
    window._update_preview_frame()

    assert len(reads) == 2
    assert np.array_equal(window._latest_preview_frame, frames[1])
    assert window._latest_preview_frame is not frames[1]
    assert np.array_equal(window._latest_annotated_preview_frame, frames[1])
    assert window._latest_annotated_preview_frame is not window._latest_preview_frame
    assert window._preview_timer.isActive()
    assert window._video_source_connected
    assert len(picture_in_picture_frames) == 2
    assert np.array_equal(picture_in_picture_frames[-1][0], frames[1])
    assert np.array_equal(picture_in_picture_frames[-1][1], frames[1])
    assert stops == []
    assert len(warnings) == 1
    assert "预览识别配置无效" in warnings[0]
    assert "显示原始画面" in window.statusBar().currentMessage()
    window._picture_in_picture = None
    window._video_source_connected = False


def test_preview_frame_read_failure_disconnects_broker(app, monkeypatch):
    stopped: list[bool] = []
    process = SimpleNamespace(stop=lambda: stopped.append(True) or True)
    window = MainWindow(capture_broker_process=process)
    window._video_source_connected = True
    window._preview_timer.start()
    monkeypatch.setattr(
        window,
        "_config_from_form",
        lambda: SimpleNamespace(capture=object()),
    )
    monkeypatch.setattr(
        window,
        "_read_live_preview_frame",
        lambda _config: (_ for _ in ()).throw(RuntimeError("no new frame")),
    )
    monkeypatch.setattr(window, "_show_error", lambda *_args, **_kwargs: None)

    window._update_preview_frame()

    assert stopped == [True]
    assert not window._video_source_connected
    assert not window._preview_timer.isActive()
    assert window.video_source_status.text() == "视频源故障"


def test_easycon_search_results_are_scoped_to_source_and_script_generations(app):
    window = MainWindow()
    current = object()
    stale = object()
    window._video_source_connected = True
    window._video_source_generation = 4
    window._easycon_run_generation = 6

    window._handle_easycon_image_search_result(3, 6, stale)
    assert window._latest_easycon_image_search_result is None

    window._handle_easycon_image_search_result(4, 5, stale)
    assert window._latest_easycon_image_search_result is None

    window._handle_easycon_image_search_result(4, 6, current)
    assert window._latest_easycon_image_search_result is current

    window._latest_easycon_image_search_result = None
    window._video_source_connected = False
    window._handle_easycon_image_search_result(4, 6, current)
    assert window._latest_easycon_image_search_result is None


def test_new_native_script_rejects_queued_results_from_previous_run(app):
    window = MainWindow()
    window._video_source_connected = True
    window._video_source_generation = 4
    window._install_easycon_image_result_callback(4, 0)
    backend = window.easycon_tab._ensure_native_backend()
    previous_callback = backend._image_result_callback
    previous = object()
    current = object()
    window._latest_easycon_image_search_result = object()

    previous_callback(previous)

    window.easycon_tab.nativeScriptStarted.emit()
    current_callback = backend._image_result_callback
    app.processEvents()

    assert window._latest_easycon_image_search_result is None
    assert window._easycon_run_generation == 1
    assert current_callback is not previous_callback

    current_callback(current)
    app.processEvents()

    assert window._latest_easycon_image_search_result is current
    window._video_source_connected = False


def test_page_script_reservation_blocks_auto_without_replacing_overlay_generation(app):
    window = MainWindow()

    class CallbackBackend:
        def __init__(self) -> None:
            self.image_callback = None

        def status(self) -> EasyConStatus:
            return EasyConStatus.BRIDGE_CONNECTED

        def set_image_result_callback(self, callback) -> None:
            self.image_callback = callback

        def close(self) -> None:
            return None

    backend = CallbackBackend()
    window.easycon_tab.native_backend = backend
    window._video_source_connected = True

    assert window.easycon_tab.reserve_native_script_run()
    window.easycon_tab.nativeScriptStarted.emit()
    generation = window._easycon_run_generation
    callback = backend.image_callback

    with pytest.raises(RuntimeError, match="已有伊机控脚本正在运行"):
        window._prepare_auto_easycon_script("auto.txt")

    assert window._easycon_run_generation == generation
    assert backend.image_callback is callback
    window.easycon_tab.release_native_script_run()
    window._video_source_connected = False


def test_auto_script_reservation_blocks_page_without_replacing_overlay_generation(app):
    window = MainWindow()

    class CallbackBackend:
        def __init__(self) -> None:
            self.image_callback = None

        def status(self) -> EasyConStatus:
            return EasyConStatus.BRIDGE_CONNECTED

        def set_image_result_callback(self, callback) -> None:
            self.image_callback = callback

        def close(self) -> None:
            return None

    backend = CallbackBackend()
    window.easycon_tab.native_backend = backend
    window._video_source_connected = True

    assert window._prepare_auto_easycon_script("auto.txt") is backend
    generation = window._easycon_run_generation
    callback = backend.image_callback

    page_reserved = window.easycon_tab.reserve_native_script_run()
    if page_reserved:
        window.easycon_tab.nativeScriptStarted.emit()

    assert page_reserved is False
    assert window._easycon_run_generation == generation
    assert backend.image_callback is callback
    window._fail_auto_script(RuntimeError("test cleanup"))
    window._video_source_connected = False


def test_auto_script_terminal_signal_holds_reservation_until_all_slots_return(app):
    window = MainWindow()

    class CallbackBackend:
        def __init__(self) -> None:
            self.image_callback = None

        def status(self) -> EasyConStatus:
            return EasyConStatus.BRIDGE_CONNECTED

        def set_image_result_callback(self, callback) -> None:
            self.image_callback = callback

        def close(self) -> None:
            return None

    backend = CallbackBackend()
    window.easycon_tab.native_backend = backend
    window._video_source_connected = True
    assert window._prepare_auto_easycon_script("auto.txt") is backend
    nested_reservations: list[bool] = []
    window.autoScriptFinished.connect(
        lambda _result: nested_reservations.append(window.easycon_tab.reserve_native_script_run())
    )
    result = EasyConRunResult(
        status=EasyConStatus.COMPLETED,
        exit_code=0,
        started_at=datetime.now(),
        ended_at=datetime.now(),
        script_path=Path("auto.txt"),
        port="COM1",
    )

    assert window._finalize_auto_script_result(result, "auto.txt") is result

    assert nested_reservations == [False]
    assert window.easycon_tab._native_run_reserved is False
    assert window.easycon_tab.reserve_native_script_run()
    window.easycon_tab.release_native_script_run()
    window._video_source_connected = False


def test_picture_in_picture_preview_controls_are_independent(app):
    window = MainWindow()
    picture_in_picture = PictureInPicturePreview(window)

    assert window.main_preview_overlay_check.isChecked()
    assert picture_in_picture.overlay_enabled()
    assert not picture_in_picture.always_on_top()

    picture_in_picture.set_overlay_enabled(False)
    picture_in_picture.set_always_on_top(True)

    assert window.main_preview_overlay_check.isChecked()
    assert not picture_in_picture.overlay_enabled()
    assert picture_in_picture.always_on_top()

    window.main_preview_overlay_check.setChecked(False)
    picture_in_picture.set_overlay_enabled(True)

    assert not window.main_preview_overlay_check.isChecked()
    assert picture_in_picture.overlay_enabled()


def test_tidsid_capture_updates_seed_inputs(app, monkeypatch):
    window = MainWindow()
    seed_state = SeedState32(0xAAAAAAAA, 0xBBBBBBBB, 0xCCCCCCCC, 0xDDDDDDDD)
    calls: list[tuple[int, bool, bool, bool]] = []

    def fake_capture(config, **kwargs):
        calls.append(
            (
                config.blink_count,
                kwargs.get("show_window"),
                callable(kwargs.get("frame_callback")),
                callable(kwargs.get("progress_callback")),
            )
        )
        kwargs["progress_callback"](config.blink_count, config.blink_count)
        return SimpleNamespace(intervals=[1, 2, 3])

    monkeypatch.setattr(main_window_module, "capture_pokemon_blinks", fake_capture)
    monkeypatch.setattr(
        main_window_module,
        "recover_tidsid_seed_from_observation",
        lambda observation: SimpleNamespace(state=seed_state, observation=observation),
    )
    window._latest_preview_frame = object()

    window.capture_tidsid_seed()
    window._capture_thread.join(timeout=2)
    window._poll_capture_thread()

    assert calls == [(64, False, True, True)]
    assert [box.text() for box in window.seed32_inputs] == ["AAAAAAAA", "BBBBBBBB", "CCCCCCCC", "DDDDDDDD"]
    assert window.seed64_outputs[0].text() == "AAAAAAAABBBBBBBB"
    assert [box.text() for box in window.auto_tid_rng_tab.tid_seed_inputs] == list(seed_state.format_seed64_pair())
    assert window.auto_tid_rng_tab.id_table.rowCount() == window.auto_tid_rng_tab.frame_threshold.value() + 1
    assert window.tidsid_button.isEnabled()


@pytest.mark.parametrize(
    ("total", "expected_milestones"),
    [
        (7, []),
        (20, [10]),
        (40, [10, 20, 30]),
        (64, [10, 20, 30, 40, 50, 60]),
    ],
)
def test_capture_progress_keep_awake_milestones(app, monkeypatch, total, expected_milestones):
    window = MainWindow()
    emitted: list[tuple[int, int]] = []
    monkeypatch.setattr(window.easycon_tab, "request_capture_keep_awake", lambda *_args, **_kwargs: None)
    window.captureKeepAwakeRequested.connect(lambda done, count: emitted.append((done, count)))
    progress_callback = window._wrap_capture_progress_with_keep_awake(lambda _done, _total: None)

    for done in range(1, total + 1):
        progress_callback(done, total)
        if done == 10:
            progress_callback(done, total)

    assert emitted == [(milestone, total) for milestone in expected_milestones]


def test_capture_keep_awake_sends_short_l_and_ignores_failure(app, monkeypatch):
    window = MainWindow()
    calls: list[tuple[int, int, dict[str, object]]] = []
    logs: list[tuple[str, str]] = []

    def capture_press(done: int, total: int, **kwargs) -> None:
        calls.append((done, total, kwargs))

    monkeypatch.setattr(window.easycon_tab, "request_capture_keep_awake", capture_press)
    window._handle_capture_keep_awake_requested(10, 40)

    assert calls == [
        (
            10,
            40,
            {
                "duration_ms": 100,
            },
        )
    ]

    monkeypatch.setattr(
        window.easycon_tab,
        "request_capture_keep_awake",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    monkeypatch.setattr(window.easycon_tab, "_append_log", lambda level, message: logs.append((level, message)))

    window._handle_capture_keep_awake_requested(20, 40)

    assert logs == [("warn", "捕捉亮屏保活发送 L 失败，继续捕捉: offline")]


def test_capture_keep_awake_cli_slot_does_not_run_delayed_discovery(app, monkeypatch, tmp_path):
    window = MainWindow()
    cli_index = window.easycon_tab.backend_mode.findData("cli")
    window.easycon_tab.backend_mode.setCurrentIndex(cli_index)
    window.easycon_tab.installation = EasyConInstallation(
        path=tmp_path / "ezcon.exe",
        version="test",
        source="test",
    )
    discovery_called = threading.Event()
    starts: list[tuple[str, int]] = []

    def delayed_discovery() -> None:
        discovery_called.set()
        time.sleep(0.5)

    def capture_start(log_label: str, duration_ms: int) -> bool:
        starts.append((log_label, duration_ms))
        return True

    monkeypatch.setattr(window.easycon_tab, "detect_easycon", delayed_discovery)
    monkeypatch.setattr(window.easycon_tab, "_start_capture_keep_awake_cli", capture_start)

    started_at = time.monotonic()
    window._handle_capture_keep_awake_requested(10, 40)
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.1
    assert not discovery_called.is_set()
    assert starts == [("捕捉亮屏保活 10/40", 100)]


def test_capture_keep_awake_bridge_does_not_block_gui_or_create_unbounded_tasks(app):
    window = MainWindow()
    backend_started = threading.Event()
    backend_release = threading.Event()
    calls: list[tuple[str, int, float | None, bool]] = []

    class BlockingBridgeBackend:
        def close(self) -> None:
            backend_release.set()

        def press(
            self,
            button: str,
            duration_ms: int,
            *,
            timeout_seconds: float | None = None,
            terminate_on_timeout: bool = False,
        ) -> None:
            calls.append((button, duration_ms, timeout_seconds, terminate_on_timeout))
            backend_started.set()
            backend_release.wait(timeout=1)
            raise RuntimeError("Bridge request timed out")

    bridge_index = window.easycon_tab.backend_mode.findData("bridge")
    window.easycon_tab.backend_mode.setCurrentIndex(bridge_index)
    window.easycon_tab.bridge_backend = BlockingBridgeBackend()
    window.easycon_tab.bridge_status = EasyConStatus.BRIDGE_CONNECTED

    started_at = time.monotonic()
    window._handle_capture_keep_awake_requested(10, 40)
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.1
    assert backend_started.wait(timeout=0.5)
    for milestone in (20, 30, 30, 30):
        window._handle_capture_keep_awake_requested(milestone, 40)
    assert calls == [("L", 100, 2.0, True)]

    class AliveCaptureThread:
        @staticmethod
        def is_alive() -> bool:
            return True

    window._capture_thread = AliveCaptureThread()
    window._capture_cancel.clear()
    window.capture_button.click()
    assert window._capture_cancel.is_set()
    window._capture_thread = None

    backend_release.set()
    deadline = time.monotonic() + 1
    while window.easycon_tab._capture_keep_awake_future is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()

    assert window.easycon_tab._capture_keep_awake_future is None
    assert "捕捉亮屏保活发送 L 失败，继续捕捉: Bridge request timed out" in window.easycon_tab.log_view.toPlainText()


def test_tidsid_capture_starts_project_xs_munchlax_tracking(app, monkeypatch):
    window = MainWindow()
    seed_state = SeedState32(0x01020304, 0x11121314, 0x21222324, 0x31323334)
    monkeypatch.setattr(main_window_module.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(
        main_window_module,
        "capture_pokemon_blinks",
        lambda config, **kwargs: SimpleNamespace(intervals=[]),
    )
    monkeypatch.setattr(
        main_window_module,
        "recover_tidsid_seed_from_observation",
        lambda observation: SimpleNamespace(state=seed_state, observation=observation),
    )
    window._latest_preview_frame = object()

    window.capture_tidsid_seed()
    window._capture_thread.join(timeout=2)
    window._poll_capture_thread()

    assert isinstance(window._advance_counter, ProjectXsMunchlaxAdvanceCounter)
    assert window._advance_counter.current_advances == 0
    assert window._advance_counter.next_tick_at == pytest.approx(100.0 + _project_xs_munchlax_interval(seed_state))


def test_tidsid_tracking_timer_schedules_next_munchlax_blink(app, monkeypatch):
    window = MainWindow()
    seed_state = SeedState32(0x01020304, 0x11121314, 0x21222324, 0x31323334)
    now_values = iter([100.0, 100.0, 100.0])
    monkeypatch.setattr(main_window_module.time, "monotonic", lambda: next(now_values))
    monkeypatch.setattr(
        main_window_module,
        "capture_pokemon_blinks",
        lambda config, **kwargs: SimpleNamespace(intervals=[]),
    )
    monkeypatch.setattr(
        main_window_module,
        "recover_tidsid_seed_from_observation",
        lambda observation: SimpleNamespace(state=seed_state, observation=observation),
    )
    window._latest_preview_frame = object()

    window.capture_tidsid_seed()
    window._capture_thread.join(timeout=2)
    window._poll_capture_thread()

    expected_ms = round(_project_xs_munchlax_interval(seed_state) * 1000)
    assert window._advance_timer.interval() == expected_ms


def test_munchlax_advance_tick_reschedules_to_following_blink(app, monkeypatch):
    window = MainWindow()
    seed_state = SeedState32(0x01020304, 0x11121314, 0x21222324, 0x31323334)
    counter = ProjectXsMunchlaxAdvanceCounter()
    counter.reset(current_advances=0, seed=seed_state, now=100.0)
    first_tick = counter.next_tick_at
    window._advance_counter = counter
    window._advance_timer.setInterval(1018)
    monkeypatch.setattr(main_window_module.time, "monotonic", lambda: first_tick)

    window._advance_tick()

    assert window._tracked_advances == 1
    assert window._advance_timer.interval() == round((counter.next_tick_at - first_tick) * 1000)


def test_blink_parameter_spinboxes_ignore_mouse_wheel(app):
    window = MainWindow()
    window.tabs.setCurrentWidget(window.project_xs_tab)
    window.show()
    app.processEvents()
    window.threshold.setValue(0.5)
    window.threshold.setFocus()

    wheel = QWheelEvent(
        QPointF(10, 10),
        QPointF(10, 10),
        QPoint(0, 0),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    QApplication.sendEvent(window.threshold, wheel)

    assert window.threshold.value() == 0.5


def test_bdsp_table_uses_pokefinder_cell_interactions(app):
    window = MainWindow()
    window.tabs.setCurrentWidget(window.bdsp_tab)
    _set_bdsp_seed(window)
    window.max_advances.setText("30")
    iv_header_count = len(window._result_headers())
    window.show_stats_check.setChecked(True)
    window.generate_results()

    height_column = window._result_headers().index("身高")
    window.table.item(5, height_column).setText("208")
    window.table.setCurrentCell(0, height_column)
    QTest.keyClicks(window.table, "208")

    assert window.table.selectionBehavior() == QAbstractItemView.SelectionBehavior.SelectItems
    assert window.table.currentColumn() == height_column
    assert window.table.currentItem().text().startswith("208")
    assert len(window._result_headers()) == iv_header_count
    assert "HP能力" in window._result_headers()
    assert "HP" not in window._result_headers()
    assert window._result_headers()[7:13] == ["HP能力", "攻击能力", "防御能力", "特攻能力", "特防能力", "速度能力"]


def test_main_window_tab_labels_are_copyable(app):
    window = MainWindow()
    for tab_index in range(window.tabs.count()):
        tab = window.tabs.widget(tab_index)
        labels = [label for label in tab.findChildren(QLabel) if label.text()]
        for label in labels:
            flags = label.textInteractionFlags()
            assert flags & Qt.TextInteractionFlag.TextSelectableByMouse
            assert flags & Qt.TextInteractionFlag.TextSelectableByKeyboard


def test_auto_rng_reverse_lookup_window_is_configurable(app, tmp_path):
    panel = AutoRngPanel(script_dir=tmp_path)

    panel.reverse_lookup_window.setValue(500)
    config = panel.build_config()

    assert config.reverse_lookup_window == 500
    assert panel.reverse_lookup_window.maximum() == 10_000
    assert panel.reverse_lookup_window.buttonSymbols() == QAbstractSpinBox.ButtonSymbols.NoButtons


def test_auto_rng_strategy_parameters_have_hover_explanations(app, tmp_path):
    panel = AutoRngPanel(script_dir=tmp_path, settings=_auto_rng_settings(tmp_path))
    form = panel.strategy_group.layout()

    explained_rows = (
        (panel.max_advances, "过 100 万帧大约需要 10 分钟"),
        (panel.fixed_delay, "delay 越大，撞闪脚本启动得越早"),
        (panel.max_wait_frames, "越依赖过帧脚本接近目标"),
        (panel.reseeding_threshold, "若重测 Seed 或校正后"),
        (panel.shiny_threshold_seconds, "判定为疑似闪光并停止自动流程"),
    )
    for field, expected_text in explained_rows:
        assert expected_text in field.toolTip()
        label = form.labelForField(field)
        assert label is not None
        assert label.toolTip() == field.toolTip()

    assert form.labelForField(panel.max_advances).text() == "搜索范围"
    assert form.labelForField(panel.shiny_threshold_seconds).text() == "闪光阈值（秒）"
    for control in (
        panel.sync_combo,
        panel.sync_nature_input,
        panel.auto_reverse_combo,
        panel.reverse_lookup_window,
    ):
        assert control.toolTip() == ""


def test_auto_rng_panel_persists_exit_script_and_reseeding_threshold(app, tmp_path):
    (tmp_path / "BDSP测种.txt").write_text("A 100\n", encoding="utf-8")
    (tmp_path / "bdsp过帧.txt").write_text("_目标帧数 = 100\n", encoding="utf-8")
    (tmp_path / "谢米.txt").write_text("_闪帧 = 100\n", encoding="utf-8")
    (tmp_path / "离开地下.txt").write_text("B 100\n", encoding="utf-8")
    panel = AutoRngPanel(script_dir=tmp_path)

    panel.exit_script_combo.setCurrentIndex(panel.exit_script_combo.findText("离开地下.txt"))
    panel.reseeding_threshold.setValue(12345)
    config = panel.build_config()

    assert config.exit_script_path == tmp_path / "离开地下.txt"
    assert config.reseeding_threshold == 12345
    assert not hasattr(panel, "refresh_scripts_button")


def test_auto_rng_script_group_uses_escape_continue_layout(app, tmp_path):
    (tmp_path / "逃跑.txt").write_text("B 100\n", encoding="utf-8")
    panel = AutoRngPanel(script_dir=tmp_path, settings=_auto_rng_settings(tmp_path))
    layout = panel.script_group.layout()

    assert layout.itemAtPosition(0, 0).widget().text() == "测种脚本"
    assert layout.itemAtPosition(0, 1).widget() is panel.seed_script_combo
    assert layout.itemAtPosition(0, 2).widget().text() == "过帧脚本"
    assert layout.itemAtPosition(0, 3).widget() is panel.advance_script_combo
    assert layout.itemAtPosition(1, 0).widget().text() == "撞闪脚本"
    assert layout.itemAtPosition(1, 1).widget() is panel.hit_script_combo
    assert layout.itemAtPosition(1, 2).widget() is panel.escape_continue_check
    assert layout.itemAtPosition(1, 3).widget() is panel.escape_script_combo
    assert layout.itemAtPosition(2, 0).widget().text() == "过场脚本"
    assert layout.itemAtPosition(2, 1).widget() is panel.exit_script_combo
    assert layout.itemAtPosition(2, 2).widget().text() == "反查脚本"
    assert layout.itemAtPosition(2, 3).widget() is panel.reverse_script_combo
    assert panel.escape_continue_check.text() == "逃跑续搜"
    assert panel.escape_continue_check.layoutDirection() == Qt.LayoutDirection.RightToLeft
    assert "background: transparent" in panel.escape_continue_check.styleSheet()
    assert layout.itemAtPosition(1, 2).alignment() == (
        Qt.AlignmentFlag.AlignLeft
        | Qt.AlignmentFlag.AlignVCenter
        | Qt.AlignmentFlag.AlignAbsolute
    )
    panel.resize(1000, 700)
    panel.show()
    app.processEvents()
    advance_label = layout.itemAtPosition(0, 2).widget()
    assert panel.escape_continue_check.geometry().left() == advance_label.geometry().left()
    assert not panel.escape_continue_check.isChecked()
    assert not panel.escape_script_combo.isEnabled()
    assert "每次未出闪都会重复" in panel.escape_continue_check.toolTip()
    assert "回到能够捕捉玩家眨眼" in panel.escape_script_combo.toolTip()
    assert not panel.build_config().escape_continue
    assert panel.build_config().escape_script_path is None

    panel.escape_continue_check.setChecked(True)
    panel.escape_script_combo.setCurrentIndex(panel.escape_script_combo.findText("逃跑.txt"))
    panel.escape_continue_check.setChecked(False)

    assert not panel.escape_script_combo.isEnabled()
    assert panel.escape_script_combo.currentText() == "逃跑.txt"


def test_auto_rng_script_combos_refresh_on_popup_and_preserve_each_selection(app, tmp_path, monkeypatch):
    selected_names = (
        "测种-A.txt",
        "过帧-A.txt",
        "撞闪-A.txt",
        "逃跑-A.txt",
        "过场-A.txt",
        "反查-A.txt",
    )
    for name in selected_names:
        (tmp_path / name).write_text("A 100\n", encoding="utf-8")
    panel = AutoRngPanel(script_dir=tmp_path, settings=_auto_rng_settings(tmp_path))
    combos = (
        panel.seed_script_combo,
        panel.advance_script_combo,
        panel.hit_script_combo,
        panel.escape_script_combo,
        panel.exit_script_combo,
        panel.reverse_script_combo,
    )
    for combo, name in zip(combos, selected_names, strict=True):
        combo.setCurrentIndex(combo.findText(name))

    (tmp_path / "新增脚本.txt").write_text("A 100\n", encoding="utf-8")
    (tmp_path / selected_names[3]).unlink()
    refresh_calls = []
    original_refresh = panel.refresh_scripts

    def track_refresh() -> None:
        refresh_calls.append(True)
        original_refresh()

    monkeypatch.setattr(panel, "refresh_scripts", track_refresh)
    for combo in combos:
        combo.showPopup()
        combo.hidePopup()

    assert len(refresh_calls) == len(combos)
    assert all(combo.findText("新增脚本.txt") >= 0 for combo in combos)
    assert [combo.currentText() for combo in combos] == [
        selected_names[0],
        selected_names[1],
        selected_names[2],
        "请选择",
        selected_names[4],
        selected_names[5],
    ]


def test_auto_rng_panel_persists_escape_continue_and_script(app, tmp_path):
    escape_script = tmp_path / "逃跑.txt"
    escape_script.write_text("B 100\n", encoding="utf-8")
    settings = _auto_rng_settings(tmp_path)
    panel = AutoRngPanel(script_dir=tmp_path, settings=settings)
    panel.escape_continue_check.setChecked(True)
    panel.escape_script_combo.setCurrentIndex(panel.escape_script_combo.findText(escape_script.name))

    panel._save_panel_state()
    settings.sync()
    restored = AutoRngPanel(script_dir=tmp_path, settings=settings)

    assert restored.escape_continue_check.isChecked()
    assert restored.escape_script_combo.isEnabled()
    assert restored.build_config().escape_continue
    assert restored.build_config().escape_script_path == escape_script


def test_auto_rng_panel_blocks_invalid_escape_continue_settings(app, tmp_path):
    (tmp_path / "BDSP测种.txt").write_text("A 100\n", encoding="utf-8")
    (tmp_path / "bdsp过帧.txt").write_text("_目标帧数 = 100\n", encoding="utf-8")
    (tmp_path / "谢米.txt").write_text("_闪帧 = 100\n", encoding="utf-8")
    (tmp_path / "逃跑.txt").write_text("B 100\n", encoding="utf-8")
    panel = AutoRngPanel(script_dir=tmp_path, settings=_auto_rng_settings(tmp_path))
    emitted = []
    panel.startRequested.connect(emitted.append)
    panel.hit_script_combo.setCurrentIndex(panel.hit_script_combo.findText("谢米.txt"))
    panel.escape_continue_check.setChecked(True)

    panel.start_button.click()

    assert emitted == []
    assert panel.status_badge.text() == "配置错误"
    assert "逃跑脚本" in panel.log_view.toPlainText()

    panel.log_view.clear()
    panel.escape_script_combo.setCurrentIndex(panel.escape_script_combo.findText("逃跑.txt"))
    panel.shiny_threshold_seconds.setValue(0)
    panel.start_button.click()

    assert emitted == []
    assert "闪光阈值" in panel.log_view.toPlainText()


def test_auto_rng_panel_defaults_reseeding_threshold_to_500000_without_saved_setting(app, tmp_path):
    panel = AutoRngPanel(script_dir=tmp_path, settings=_auto_rng_settings(tmp_path))

    assert panel.reseeding_threshold.value() == 500_000
    assert panel.build_config().reseeding_threshold == 500_000


def test_auto_rng_panel_restores_saved_reseeding_threshold(app, tmp_path):
    settings = _auto_rng_settings(tmp_path)
    settings.setValue("reseeding_threshold", 12345)

    panel = AutoRngPanel(script_dir=tmp_path, settings=settings)

    assert panel.reseeding_threshold.value() == 12345
    assert panel.build_config().reseeding_threshold == 12345


def test_reverse_lookup_search_span_uses_symmetric_window():
    assert _reverse_lookup_search_span(500, 500) == (0, 1000, 1000)
    assert _reverse_lookup_search_span(2000, 500) == (1500, 2500, 1000)
    assert _reverse_lookup_search_span(20_000, 20_000) == (10_000, 30_000, 20_000)


def test_reverse_species_label_uses_chinese_names():
    assert _reverse_species_label("Registeel") == "雷吉斯奇鲁"
    assert _reverse_species_label("Regirock") == "雷吉洛克"
    assert _reverse_species_label("Unknownmon") == "Unknownmon"


def test_reverse_lookup_groups_include_legendary_birds():
    assert main_window_module._reverse_lookup_group_descriptions("Articuno") == ("Articuno", "Zapdos", "Moltres")
    assert main_window_module._reverse_lookup_group_descriptions("Zapdos") == ("Articuno", "Zapdos", "Moltres")
    assert main_window_module._reverse_lookup_group_descriptions("Moltres") == ("Articuno", "Zapdos", "Moltres")
    assert main_window_module._reverse_lookup_group_descriptions("Unknownmon") == ("Unknownmon",)


def test_main_window_waits_around_right_after_notes_ocr(app, monkeypatch):
    window = MainWindow()
    events = []

    monkeypatch.setattr(main_window_module.time, "sleep", lambda seconds: events.append(("sleep", seconds)))
    monkeypatch.setattr(window, "_send_easycon_right", lambda log_details=True: events.append(("right", log_details)))

    window._pause_ocr_and_turn_to_stats_page()

    assert events == [("sleep", 0.1), ("right", True), ("sleep", 0.1)]


def test_main_window_can_silence_internal_right_logs(app, monkeypatch):
    window = MainWindow()
    events = []

    monkeypatch.setattr(main_window_module.time, "sleep", lambda seconds: events.append(("sleep", seconds)))
    monkeypatch.setattr(window, "_send_easycon_right", lambda log_details=True: events.append(("right", log_details)))

    window._pause_ocr_and_turn_to_stats_page(log_details=False)

    assert events == [("sleep", 0.1), ("right", False), ("sleep", 0.1)]


def test_auto_rng_nature_map_matches_ui_nature_order():
    assert [_NATURE_MAP[name] for name in NATURES_ZH] == list(range(25))
    assert _NATURE_MAP["温顺"] == 21


def test_normalize_iv_ranges_rejects_impossible_native_sentinel():
    assert _normalize_iv_ranges([(30, 31), (31, 0), (0, 3), (24, 26), (30, 31), (30, 31)]) is None
    assert _normalize_iv_ranges([(30, 31), (10, 13), (17, 23), (30, 31), (27, 29), (30, 31)]) == (
        [30, 10, 17, 30, 27, 30],
        [31, 13, 23, 31, 29, 31],
    )


def test_bdsp_characteristic_matches_pokefinder_tie_break_and_translation(app):
    window = MainWindow()
    state = State8(
        advances=14113,
        ec=0x38458EDC,
        sidtid=0,
        pid=0xE4B100B9,
        ivs=(31, 31, 31, 9, 23, 31),
        ability=1,
        gender=2,
        level=30,
        nature=16,
        shiny=2,
        height=165,
        weight=135,
    )

    assert window._characteristic_text(state) == "经常睡午觉"


def test_main_window_exports_txt_from_results(app, monkeypatch, tmp_path):
    window = MainWindow()
    window.tabs.setCurrentWidget(window.bdsp_tab)
    _set_bdsp_seed(window)
    window.max_advances.setText("2")
    window.generate_results()
    output = tmp_path / "results.txt"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_args, **_kwargs: (str(output), "Text files (*.txt)"))

    window.export_results_txt()

    text = output.read_text(encoding="utf-8")
    assert text.startswith("帧数\tEC\tPID")
    assert "\t个性\n" in text.splitlines()[0] + "\n"


def test_main_window_syncs_seed64_display(app):
    window = MainWindow()

    assert window.seed64_outputs[0].text() == ""
    assert window.seed64_outputs[1].text() == ""


def test_seed_update_auto_refreshes_existing_results(app):
    window = MainWindow()
    window.tabs.setCurrentWidget(window.bdsp_tab)
    _set_bdsp_seed(window)
    window.max_advances.setText("2")
    window.generate_results()
    first_ec = window.table.item(0, 1).text()

    window.bdsp_seed64_inputs[0].setText("0000000000000001")
    window.bdsp_seed64_inputs[1].setText("0000000000000002")
    window._sync_state32_from_bdsp_seed64()

    assert window.table.rowCount() == 3
    assert window.table.item(0, 1).text() != first_ec


def test_reidentify_updates_advances_and_keeps_seed(app, monkeypatch):
    window = MainWindow()
    window.tabs.setCurrentWidget(window.bdsp_tab)
    _set_bdsp_seed(window)
    window.max_advances.setText("2")
    window.generate_results()
    observation = BlinkObservation.from_sequences([0, 1, 0], [0, 12, 24])
    state = SeedState32(0xAAAAAAAA, 0xBBBBBBBB, 0xCCCCCCCC, 0xDDDDDDDD)

    capture_counts: list[int] = []
    run_logs: list[str] = []

    def fake_capture(config, *args, **kwargs):
        capture_counts.append(config.blink_count)
        return observation

    monkeypatch.setattr("auto_bdsp_rng.ui.main_window.capture_player_blinks", fake_capture)
    monkeypatch.setattr(
        "auto_bdsp_rng.ui.main_window.reidentify_seed_from_observation",
        lambda *_args, **_kwargs: ProjectXsReidentifyResult(state=state, observation=observation, advances=42),
    )
    monkeypatch.setattr(
        window,
        "_write_run_log",
        lambda _source, message, **_kwargs: run_logs.append(str(message)),
    )
    original_words = ["12345678", "9ABCDEF0", "11111111", "22222222"]
    for box, text in zip(window.seed32_inputs, original_words):
        box.setText(text)
    window._latest_preview_frame = object()

    window.reidentify_seed()
    window._capture_thread.join(timeout=2)
    window._poll_capture_thread()

    assert [box.text() for box in window.seed32_inputs] == original_words
    assert int(window.advances_value.text()) >= 42
    assert capture_counts == [7]
    assert window.result_count.text() == "3 条结果"
    assert window.statusBar().currentMessage() == "Seed 校正完成"
    assert any(message.startswith("开始校正；") for message in run_logs)
    assert any(message.startswith("校正完成；") for message in run_logs)


def test_reidentify_noisy_option_uses_20_blinks_and_noisy_reidentify(app, monkeypatch):
    window = MainWindow()
    window.tabs.setCurrentWidget(window.bdsp_tab)
    _set_bdsp_seed(window)
    observation = BlinkObservation.from_sequences([0, 1, 0], [0, 12, 24])
    state = SeedState32(0xAAAAAAAA, 0xBBBBBBBB, 0xCCCCCCCC, 0xDDDDDDDD)
    capture_counts: list[int] = []

    def fake_capture(config, *args, **kwargs):
        capture_counts.append(config.blink_count)
        return observation

    def fail_regular(*_args, **_kwargs):
        raise AssertionError("regular reidentify should not be used")

    monkeypatch.setattr("auto_bdsp_rng.ui.main_window.capture_player_blinks", fake_capture)
    monkeypatch.setattr("auto_bdsp_rng.ui.main_window.reidentify_seed_from_observation", fail_regular)
    monkeypatch.setattr(
        "auto_bdsp_rng.ui.main_window.reidentify_seed_from_observation_noisy",
        lambda *_args, **_kwargs: ProjectXsReidentifyResult(state=state, observation=observation, advances=43),
    )
    original_words = ["12345678", "9ABCDEF0", "11111111", "22222222"]
    for box, text in zip(window.seed32_inputs, original_words):
        box.setText(text)
    window.reidentify_1_pk_npc.setChecked(True)
    window._latest_preview_frame = object()

    window.reidentify_seed()
    window._capture_thread.join(timeout=2)
    window._poll_capture_thread()

    assert capture_counts == [20]
    assert [box.text() for box in window.seed32_inputs] == original_words
    assert window.advances_value.text() == "43"


def test_reidentify_noisy_uses_tracked_advances_before_stopping_tracking(app, monkeypatch):
    window = MainWindow()
    observation = BlinkObservation.from_sequences([], [0, 12, 24])
    window.reidentify_1_pk_npc.setChecked(True)
    window._tracked_advances = 120_000
    window.max_advances.setText("100000")
    window._latest_preview_frame = object()
    search_ranges: list[tuple[int, int]] = []

    def fake_capture(*_args, **_kwargs):
        return observation

    def fake_noisy(_state, _observation, **kwargs):
        search_ranges.append((kwargs["search_min"], kwargs["search_max"]))
        return ProjectXsReidentifyResult(
            state=SeedState32(0xAAAAAAAA, 0xBBBBBBBB, 0xCCCCCCCC, 0xDDDDDDDD),
            observation=observation,
            advances=110_000,
        )

    monkeypatch.setattr(main_window_module, "capture_player_blinks", fake_capture)
    monkeypatch.setattr(main_window_module, "reidentify_seed_from_observation_noisy", fake_noisy)
    for box, text in zip(window.seed32_inputs, ["12345678", "9ABCDEF0", "11111111", "22222222"]):
        box.setText(text)

    window.reidentify_seed()
    window._capture_thread.join(timeout=2)
    window._poll_capture_thread()

    assert search_ranges == [(110_000, 100_000)]


def test_capture_seed_restores_running_preview(app, monkeypatch):
    window = MainWindow()
    observation = SimpleNamespace(offset_time=100.0)
    seed_state = SeedState32(0xAAAAAAAA, 0xBBBBBBBB, 0xCCCCCCCC, 0xDDDDDDDD)
    monkeypatch.setattr(main_window_module.time, "perf_counter", lambda: 100.0)
    monkeypatch.setattr("auto_bdsp_rng.ui.main_window.capture_player_blinks", lambda *_args, **_kwargs: observation)
    monkeypatch.setattr(
        "auto_bdsp_rng.ui.main_window.recover_seed_from_observation",
        lambda *_args, **_kwargs: SimpleNamespace(state=seed_state),
    )
    window._preview_timer.start()
    window.preview_button.setText(window._text("stop_preview"))
    window._latest_preview_frame = object()

    window.capture_seed()
    window._capture_thread.join(timeout=2)
    window._poll_capture_thread()

    preview_active = window._preview_timer.isActive()
    preview_label = window.preview_button.text()
    window._preview_timer.stop()

    assert preview_active
    assert preview_label == window._text("stop_preview")


def test_capture_seed_initializes_preview_when_no_frame_was_seen(app, monkeypatch):
    window = MainWindow()
    observation = SimpleNamespace(offset_time=100.0)
    seed_state = SeedState32(0xAAAAAAAA, 0xBBBBBBBB, 0xCCCCCCCC, 0xDDDDDDDD)
    preview_updates: list[str] = []
    monkeypatch.setattr(main_window_module.time, "perf_counter", lambda: 100.0)
    monkeypatch.setattr("auto_bdsp_rng.ui.main_window.capture_player_blinks", lambda *_args, **_kwargs: observation)
    monkeypatch.setattr(
        "auto_bdsp_rng.ui.main_window.recover_seed_from_observation",
        lambda *_args, **_kwargs: SimpleNamespace(state=seed_state),
    )

    def fake_update_preview() -> None:
        preview_updates.append("updated")
        window._latest_preview_frame = object()

    monkeypatch.setattr(window, "_update_preview_frame", fake_update_preview)

    window.capture_seed()
    window._capture_thread.join(timeout=2)
    window._poll_capture_thread()

    assert preview_updates == ["updated"]
    assert window._latest_preview_frame is not None


def test_reidentify_restores_running_preview(app, monkeypatch):
    window = MainWindow()
    window.tabs.setCurrentWidget(window.bdsp_tab)
    _set_bdsp_seed(window)
    observation = BlinkObservation.from_sequences([0, 1, 0], [0, 12, 24])
    state = SeedState32(0xAAAAAAAA, 0xBBBBBBBB, 0xCCCCCCCC, 0xDDDDDDDD)
    monkeypatch.setattr("auto_bdsp_rng.ui.main_window.capture_player_blinks", lambda *_args, **_kwargs: observation)
    monkeypatch.setattr(
        "auto_bdsp_rng.ui.main_window.reidentify_seed_from_observation",
        lambda *_args, **_kwargs: ProjectXsReidentifyResult(state=state, observation=observation, advances=42),
    )
    for box, text in zip(window.seed32_inputs, ["12345678", "9ABCDEF0", "11111111", "22222222"]):
        box.setText(text)
    window._preview_timer.start()
    window.preview_button.setText(window._text("stop_preview"))
    window._latest_preview_frame = object()

    window.reidentify_seed()
    window._capture_thread.join(timeout=2)
    window._poll_capture_thread()

    preview_active = window._preview_timer.isActive()
    preview_label = window.preview_button.text()
    window._preview_timer.stop()

    assert preview_active
    assert preview_label == window._text("stop_preview")


def test_reidentify_initializes_preview_when_no_frame_was_seen(app, monkeypatch):
    window = MainWindow()
    window.tabs.setCurrentWidget(window.bdsp_tab)
    _set_bdsp_seed(window)
    observation = BlinkObservation.from_sequences([0, 1, 0], [0, 12, 24])
    state = SeedState32(0xAAAAAAAA, 0xBBBBBBBB, 0xCCCCCCCC, 0xDDDDDDDD)
    preview_updates: list[str] = []
    monkeypatch.setattr("auto_bdsp_rng.ui.main_window.capture_player_blinks", lambda *_args, **_kwargs: observation)
    monkeypatch.setattr(
        "auto_bdsp_rng.ui.main_window.reidentify_seed_from_observation",
        lambda *_args, **_kwargs: ProjectXsReidentifyResult(state=state, observation=observation, advances=42),
    )
    for box, text in zip(window.seed32_inputs, ["12345678", "9ABCDEF0", "11111111", "22222222"]):
        box.setText(text)

    def fake_update_preview() -> None:
        preview_updates.append("updated")
        window._latest_preview_frame = object()

    monkeypatch.setattr(window, "_update_preview_frame", fake_update_preview)

    window.reidentify_seed()
    window._capture_thread.join(timeout=2)
    window._poll_capture_thread()

    assert preview_updates == ["updated"]
    assert window._latest_preview_frame is not None


def test_main_window_loads_project_xs_config_fields(app, monkeypatch, tmp_path):
    config_dir = tmp_path / "project_xs_configs"
    config_dir.mkdir()
    (config_dir / "config_bebe.json").write_text(
        """{
    "MonitorWindow": true,
    "WindowPrefix": "PotPlayer",
    "image": "./images/bebe/eye2.png",
    "view": [516, 377, 38, 53],
    "thresh": 0.7,
    "white_delay": 0.0,
    "advance_delay": 0,
    "advance_delay_2": 0,
    "npc": 1,
    "pokemon_npc": 0,
    "timeline_npc": 0,
    "crop": [0, 0, 0, 0],
    "camera": 0,
    "display_percent": 80
}
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(main_window_module, "PROJECT_XS_CONFIGS", config_dir)
    window = MainWindow()
    index = window.config_combo.findText("config_bebe.json")
    assert index >= 0
    window.config_combo.setCurrentIndex(index)

    assert window.window_prefix.text() == "PotPlayer"
    assert window.monitor_window.isChecked() is True
    assert window.x.text() == "516"
    assert window.y.text() == "377"
    assert window.w.text() == "38"
    assert window.h.text() == "53"
    assert window.threshold.value() == 0.7


def test_main_window_header_shows_auto_rng_runtime_status(app):
    window = MainWindow()

    assert not hasattr(window, "language_combo")
    assert not hasattr(window, "language_label")
    assert not hasattr(window, "seed_badge")
    assert window.auto_loop_badge.text() == "循环 0"
    assert window.auto_phase_badge.text() == "阶段 空闲"
    assert window.auto_advance_badge.text() == "advance 0"

    window.auto_rng_tab.autoProgressChanged.emit(AutoRngProgress(phase=AutoRngPhase.REIDENTIFY))
    assert window.auto_phase_badge.text() == "阶段 校正位置"

    window.auto_rng_tab.autoProgressChanged.emit(
        AutoRngProgress(
            phase=AutoRngPhase.RUN_HIT_SCRIPT,
            loop_index=3,
            current_advances=4567,
        )
    )

    assert window.auto_loop_badge.text() == "循环 3"
    assert window.auto_phase_badge.text() == "阶段 运行撞闪脚本"
    assert window.auto_advance_badge.text() == "advance 4567"


def test_main_window_has_auto_rng_tab(app):
    window = MainWindow()

    assert window.tabs.count() == 6
    assert window.tabs.tabText(0) == "自动定点乱数"
    assert window.tabs.tabText(1) == "自动 TID 乱数"
    assert window.tabs.tabText(5) == "历史记录"
    assert hasattr(window, "id_tab")


def test_id_panel_syncs_seed_pair_and_generates_results(app):
    window = MainWindow()

    _set_bdsp_seed(window)
    window._sync_state32_from_bdsp_seed64()

    assert [box.text() for box in window.id_tab.seed_inputs] == ["123456789ABCDEF0", "1111111122222222"]

    window.id_tab.seed_inputs[0].setText("AAAAAAAA55555555")
    window.id_tab.seed_inputs[1].setText("33333333CCCCCCCC")
    window.id_tab._emit_seed_changed()

    assert [box.text() for box in window.bdsp_seed64_inputs] == ["AAAAAAAA55555555", "33333333CCCCCCCC"]

    window.id_tab.max_advances.setValue(3)
    window.id_tab.generate_results()

    assert window.id_tab.table.rowCount() == 3
    assert window.id_tab.table.columnCount() == 5


def test_auto_rng_panel_blocks_start_when_required_script_parameter_is_missing(app, tmp_path):
    (tmp_path / "BDSP测种.txt").write_text("A 100\n", encoding="utf-8")
    (tmp_path / "bdsp过帧.txt").write_text("A 100\n", encoding="utf-8")
    (tmp_path / "谢米.txt").write_text("_闪帧 = 100\n", encoding="utf-8")
    panel = AutoRngPanel(script_dir=tmp_path)
    emitted = []
    panel.startRequested.connect(lambda payload: emitted.append(payload))
    panel.hit_script_combo.setCurrentIndex(panel.hit_script_combo.findText("谢米.txt"))

    panel.start_button.click()

    assert emitted == []
    assert "缺少必需参数 _目标帧数" in panel.log_view.toPlainText()


def test_auto_rng_start_button_uses_primary_toolbutton_style(app):
    window = MainWindow()

    assert window.auto_rng_tab.start_button.isEnabled()
    assert window.auto_rng_tab.start_button.objectName() == "PrimaryButton"
    assert "QToolButton#PrimaryButton" in window.styleSheet()


def test_profile_version_syncs_auto_rng_target_choices(app):
    window = MainWindow()

    window._set_profile_version(main_window_module.GameVersion.SP)

    assert window.auto_rng_tab._target_version == main_window_module.GameVersion.SP
    form = window.auto_rng_tab.target_form
    form.category_combo.setCurrentIndex(form.category_combo.findData("ramanasParkPureSpace"))
    bird_descriptions = [
        form.encounter_combo.itemData(index).description
        for index in range(form.encounter_combo.count())
        if form.encounter_combo.itemData(index).description in {"Articuno", "Zapdos", "Moltres"}
    ]
    assert bird_descriptions == ["Articuno", "Zapdos", "Moltres"]


def test_main_window_restores_profile_settings(app, tmp_path):
    settings = _profile_settings(tmp_path)
    settings.setValue("name", "Pearl main")
    settings.setValue("tid", 24680)
    settings.setValue("sid", 13579)
    settings.setValue("version", main_window_module.GameVersion.SP.value)
    settings.setValue("national_dex", True)
    settings.setValue("shiny_charm", True)
    settings.setValue("oval_charm", True)

    window = MainWindow(profile_settings=settings)

    assert window.profile_name.text() == "Pearl main"
    assert window.tid.text() == "24680"
    assert window.sid.text() == "13579"
    assert window.tsv.text() == str(24680 ^ 13579)
    assert window._profile_version == main_window_module.GameVersion.SP
    assert window.profile_game_value.text() == "明亮珍珠"
    assert window.auto_rng_tab._target_version == main_window_module.GameVersion.SP
    assert window.national_dex.isChecked()
    assert window.shiny_charm.isChecked()
    assert window.oval_charm.isChecked()


def test_main_window_saves_profile_settings_on_close(app, tmp_path):
    settings = _profile_settings(tmp_path)
    window = MainWindow(profile_settings=settings)
    window.profile_name.setText("Diamond main")
    window.tid.setText("11111")
    window.sid.setText("22222")
    window.national_dex.setChecked(True)
    window.shiny_charm.setChecked(False)
    window.oval_charm.setChecked(True)
    window._set_profile_version(main_window_module.GameVersion.BD)

    window.close()

    assert settings.value("name") == "Diamond main"
    assert int(settings.value("tid")) == 11111
    assert int(settings.value("sid")) == 22222
    assert settings.value("version") == main_window_module.GameVersion.BD.value
    assert settings.value("national_dex", type=bool)
    assert not settings.value("shiny_charm", type=bool)
    assert settings.value("oval_charm", type=bool)


def test_auto_rng_panel_emits_config_when_starting_with_valid_scripts(app, tmp_path):
    (tmp_path / "BDSP测种.txt").write_text("A 100\n", encoding="utf-8")
    (tmp_path / "bdsp过帧.txt").write_text("_目标帧数 = 100\n", encoding="utf-8")
    (tmp_path / "谢米.txt").write_text("_闪帧 = 100\n", encoding="utf-8")
    panel = AutoRngPanel(script_dir=tmp_path)
    emitted: list[AutoRngConfig] = []
    panel.startRequested.connect(lambda config: emitted.append(config))
    panel.hit_script_combo.setCurrentIndex(panel.hit_script_combo.findText("谢米.txt"))
    panel.fixed_delay.setValue(1200)
    panel.max_wait_frames.setValue(300)

    panel.start_button.click()

    assert len(emitted) == 1
    config = emitted[0]
    assert isinstance(config, AutoRngConfig)
    assert config.script_dir == tmp_path
    assert config.seed_script_path == tmp_path / "BDSP测种.txt"
    assert config.advance_script_path == tmp_path / "bdsp过帧.txt"
    assert config.hit_script_path == tmp_path / "谢米.txt"
    assert config.fixed_delay == 1200
    assert config.max_wait_frames == 300
    assert config.start_phase == AutoRngPhase.RUN_SEED_SCRIPT
    assert config.reseed_threshold_frames == 900_000
    assert config.min_final_flash_frames == 5


def test_auto_rng_panel_can_start_from_capture_seed_via_menu(app, tmp_path):
    (tmp_path / "BDSP测种.txt").write_text("A 100\n", encoding="utf-8")
    (tmp_path / "bdsp过帧.txt").write_text("_目标帧数 = 100\n", encoding="utf-8")
    (tmp_path / "谢米.txt").write_text("_闪帧 = 100\n", encoding="utf-8")
    panel = AutoRngPanel(script_dir=tmp_path)
    emitted: list[AutoRngConfig] = []
    panel.startRequested.connect(lambda config: emitted.append(config))
    panel.hit_script_combo.setCurrentIndex(panel.hit_script_combo.findText("谢米.txt"))

    panel.start_from_capture_action.trigger()

    assert len(emitted) == 1
    assert emitted[0].start_phase == AutoRngPhase.CAPTURE_SEED
    assert emitted[0].seed_script_path == tmp_path / "BDSP测种.txt"


def test_auto_rng_panel_can_start_from_reidentify_via_menu(app, tmp_path):
    (tmp_path / "BDSP测种.txt").write_text("A 100\n", encoding="utf-8")
    (tmp_path / "bdsp过帧.txt").write_text("_目标帧数 = 100\n", encoding="utf-8")
    (tmp_path / "谢米.txt").write_text("_闪帧 = 100\n", encoding="utf-8")
    panel = AutoRngPanel(script_dir=tmp_path)
    emitted: list[AutoRngConfig] = []
    panel.startRequested.connect(lambda config: emitted.append(config))
    panel.hit_script_combo.setCurrentIndex(panel.hit_script_combo.findText("谢米.txt"))

    assert panel.start_from_reidentify_action.text() == "从校正开始"
    panel.start_from_reidentify_action.trigger()

    assert len(emitted) == 1
    assert emitted[0].start_phase == AutoRngPhase.REIDENTIFY


def test_main_window_reidentify_start_requires_existing_seed(app, tmp_path, monkeypatch):
    window = MainWindow()
    config = AutoRngConfig(script_dir=tmp_path, start_phase=AutoRngPhase.REIDENTIFY)
    started: list[object] = []
    warnings: list[str] = []
    monkeypatch.setattr(window.auto_rng_tab, "run_with_runner", started.append)
    monkeypatch.setattr(main_window_module.QMessageBox, "warning", lambda _parent, _title, text: warnings.append(text))
    for box in window.seed32_inputs:
        box.clear()

    window._start_auto_rng(config)

    assert not started
    assert warnings
    assert "从校正开始需要先填入有效 Seed" in warnings[0]
    assert "reidentify" not in warnings[0].lower()


def test_main_window_exposes_shiny_threshold_calibration_button_on_seed_capture_tab(app):
    window = MainWindow()

    assert window.calibrate_shiny_threshold_button.text() == "校准闪光判定"


def test_shiny_threshold_calibration_rejects_concurrent_ocr_activity(app, monkeypatch):
    window = MainWindow()
    warnings = []
    window._ocr_warmup_running = True
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )

    window.calibrate_shiny_threshold()

    assert window._shiny_calibration_worker is None
    assert warnings == [
        (
            "OCR 正在使用",
            "请等待当前 OCR 预热、识别或测试完成后再校准闪光判定。",
        )
    ]


def test_shiny_threshold_calibration_runs_in_background_without_wait_cursor(app, monkeypatch):
    window = MainWindow()
    cursor_states: list[bool] = []
    shown: list[float] = []

    def fake_measure_keyword_interval(*_args, **_kwargs):
        cursor_states.append(QApplication.overrideCursor() is not None)
        time.sleep(0.15)
        return SimpleNamespace(interval_seconds=2.5)

    monkeypatch.setattr(main_window_module, "measure_keyword_interval", fake_measure_keyword_interval)
    monkeypatch.setattr(window, "_show_shiny_threshold_dialog", lambda interval: shown.append(interval))

    started_at = time.monotonic()
    window.calibrate_shiny_threshold()
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.1
    assert "[闪光判定校准] 开始监控" in window.auto_rng_tab.log_view.toPlainText()
    assert window.calibrate_shiny_threshold_button.text() == "停止校准"
    for _ in range(20):
        if shown:
            break
        QTest.qWait(50)
    assert cursor_states == [False]
    assert shown == [2.5]
    assert window.calibrate_shiny_threshold_button.text() == "校准闪光判定"


def test_auto_rng_panel_includes_editable_shiny_threshold_in_config(app, tmp_path):
    (tmp_path / "BDSP测种.txt").write_text("A 100\n", encoding="utf-8")
    (tmp_path / "bdsp过帧.txt").write_text("_目标帧数 = 100\n", encoding="utf-8")
    (tmp_path / "谢米.txt").write_text("_闪帧 = 100\n", encoding="utf-8")
    panel = AutoRngPanel(script_dir=tmp_path)
    panel.hit_script_combo.setCurrentIndex(panel.hit_script_combo.findText("谢米.txt"))
    panel.shiny_threshold_seconds.setValue(2.8)

    config = panel.build_config()

    assert config.shiny_threshold_seconds == 2.8


def test_auto_rng_panel_defaults_shiny_threshold_to_four_seconds_without_saved_setting(app, tmp_path):
    panel = AutoRngPanel(script_dir=tmp_path, settings=_auto_rng_settings(tmp_path))

    assert panel.shiny_threshold_seconds.value() == 4.0
    assert panel.build_config().shiny_threshold_seconds == 4.0


def test_auto_rng_panel_restores_saved_shiny_threshold(app, tmp_path):
    settings = _auto_rng_settings(tmp_path)
    settings.setValue("shiny_threshold", 2.5)

    panel = AutoRngPanel(script_dir=tmp_path, settings=settings)

    assert panel.shiny_threshold_seconds.value() == 2.5
    assert panel.build_config().shiny_threshold_seconds == 2.5


def test_auto_rng_panel_has_target_button_and_no_old_main_regions(app):
    panel = AutoRngPanel()
    group_titles = {group.title() for group in panel.findChildren(QGroupBox)}

    assert "目标精灵设置" not in group_titles
    assert "运行摘要" not in group_titles
    assert "定点目标 / 存档信息 / 个体筛选" not in group_titles
    assert "候选结果" not in group_titles
    assert not hasattr(panel, "candidate_table")
    assert not hasattr(panel, "search_target_summary")
    assert hasattr(panel, "target_button")
    assert panel.target_button.text() == "目标精灵设置..."
    assert not hasattr(panel, "parameter_preview")
    assert not hasattr(panel, "preview_button")
    assert panel.log_view.isReadOnly() is True
    labels = {label.text() for label in panel.findChildren(QLabel)}
    assert "重新测 seed 阈值" not in labels
    assert "最小 final flash frames" not in labels


def test_auto_rng_panel_uses_full_width_log_without_summary_group(app):
    panel = AutoRngPanel()
    group_titles = {group.title() for group in panel.findChildren(QGroupBox)}
    visible_labels = {label.text() for label in panel.findChildren(QLabel)}

    assert "运行摘要" not in group_titles
    assert "Seed" not in visible_labels
    assert "触发帧" not in visible_labels
    assert "剩余" not in visible_labels
    assert "raw target" not in visible_labels
    assert "trigger advances" not in visible_labels
    assert "current advances" not in visible_labels
    assert "remaining_to_trigger" not in visible_labels
    assert "final flash_frames" not in visible_labels
    assert not ({"当前循环", "当前阶段", "原始目标帧", "当前帧", "最终闪帧"} & visible_labels)
    assert not hasattr(panel, "summary_seed")
    assert not hasattr(panel, "summary_group")
    assert not hasattr(panel, "summary_trigger")
    assert not hasattr(panel, "summary_remaining")
    assert not hasattr(panel, "summary_target")
    assert panel.log_group.maximumWidth() == 16777215
    assert len([group for group in panel.findChildren(QGroupBox) if group.title() == "日志"]) == 1
    assert panel.content_grid.itemAtPosition(1, 0).widget() is panel.log_group
    index = panel.content_grid.indexOf(panel.log_group)
    assert index >= 0
    row, column, row_span, column_span = panel.content_grid.getItemPosition(index)
    assert (row, column, row_span, column_span) == (1, 0, 1, 2)
    assert panel.content_grid.itemAtPosition(0, 0).widget() is panel.config_panel
    assert panel.content_grid.itemAtPosition(0, 1).widget() is panel.runtime_panel


def test_auto_rng_page_uses_compact_toolbar_and_fixed_left_sidebar(app):
    panel = AutoRngPanel()

    assert 56 <= panel.toolbar.maximumHeight() <= 64
    assert panel.mode_combo.width() == 120
    assert panel.loop_count.width() == 80
    assert panel.start_button.height() == 34
    assert panel.stop_button.height() == 34
    assert panel.config_panel.minimumWidth() >= 430
    assert panel.config_panel.minimumWidth() == panel.config_panel.maximumWidth()
    assert panel.strategy_group.minimumHeight() < 400
    assert panel.strategy_group.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Preferred
    assert panel.strategy_group.maximumHeight() == 16777215  # 未设固定高度
    assert panel.script_group.maximumHeight() == 16777215  # 未设固定高度
    assert panel.max_advances.width() >= 200
    assert panel.seed_script_combo.width() <= 170
    assert not hasattr(panel, "refresh_scripts_button")
    assert not any(button.text() == "刷新脚本列表" for button in panel.findChildren(QPushButton))
    assert not any(button.text() == "参数预览" for button in panel.findChildren(QPushButton))


def test_auto_rng_target_summary_uses_chinese_compact_rows_and_scroll(app):
    panel = AutoRngPanel()
    from auto_bdsp_rng.data import get_static_encounters

    record = next(r for r in get_static_encounters() if r.description == "Shaymin")
    panel.set_targets([
        (record, StateFilter(height_min=0, height_max=0, shiny=2), "square"),
        (record, StateFilter(height_min=255, height_max=255, shiny=2), "square"),
    ])

    assert panel.target_summary_title.text() == "精灵筛选列表：谢米"
    assert panel.target_summary_labels[0].text() == "1. 异色：方闪 | 身高：0"
    assert panel.target_summary_labels[1].text() == "2. 异色：方闪 | 身高：255"
    assert "任意" not in panel.target_summary_labels[0].text()
    assert "Height" not in panel.target_summary_labels[0].text()
    assert "Weight" not in panel.target_summary_labels[0].text()
    assert panel.target_summary_scroll.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    assert panel.target_summary_scroll.maximumHeight() <= 150


def test_auto_rng_target_summary_scrolls_when_many_conditions(app):
    panel = AutoRngPanel()
    from auto_bdsp_rng.data import get_static_encounters

    record = next(r for r in get_static_encounters() if r.description == "Shaymin")
    panel.set_targets([
        (record, StateFilter(height_min=i, height_max=i, shiny=2), "square")
        for i in range(12)
    ])

    assert len(panel.target_summary_labels) == 12
    assert panel.target_summary_scroll.maximumHeight() <= 150


def test_auto_rng_panel_restores_persisted_multi_targets(app, tmp_path):
    from auto_bdsp_rng.data import get_static_encounters

    first = AutoRngPanel(script_dir=tmp_path)
    first._settings.clear()
    record = next(r for r in get_static_encounters() if r.description == "Shaymin")
    first.set_targets([
        (record, StateFilter(height_min=0, height_max=0, weight_min=10, weight_max=20, shiny=2), "square"),
        (record, StateFilter(height_min=255, height_max=255, ability=1, gender=0, shiny=2), "square"),
    ])
    first.close()
    app.processEvents()

    restored = AutoRngPanel(script_dir=tmp_path)

    targets = restored.targets()
    assert len(targets) == 2
    assert [target[0].description for target in targets] == ["Shaymin", "Shaymin"]
    assert targets[0][1].height_min == 0
    assert targets[0][1].height_max == 0
    assert targets[0][1].weight_min == 10
    assert targets[0][1].weight_max == 20
    assert targets[1][1].height_min == 255
    assert targets[1][1].height_max == 255
    assert targets[1][1].ability == 1
    assert targets[1][1].gender == 0
    assert [target[2] for target in targets] == ["square", "square"]
    assert restored.target_summary_title.text() == "精灵筛选列表：谢米"
    restored._settings.clear()


def test_auto_rng_stop_button_requests_runner_stop_immediately(app):
    panel = AutoRngPanel()
    stops: list[str] = []
    emissions: list[str] = []
    panel.stopRequested.connect(lambda: emissions.append("emitted"))
    panel._runner_worker = SimpleNamespace(stop=lambda: stops.append("stopped"))

    panel.stop_button.click()

    assert stops == ["stopped"]
    assert emissions == ["emitted"]


def test_auto_rng_panel_apply_progress_updates_summary_and_log(app):
    panel = AutoRngPanel()

    panel.apply_progress(AutoRngProgress(phase=AutoRngPhase.REIDENTIFY))
    assert panel.status_badge.text() == "校正位置"

    panel.apply_progress(
        AutoRngProgress(
            phase=AutoRngPhase.RUN_HIT_SCRIPT,
            loop_index=2,
            seed_text="seed-1",
            locked_target=AutoRngTarget(raw_target_advances=1300, label="Shaymin"),
            raw_target_advances=1300,
            fixed_delay=1200,
            trigger_advances=100,
            current_advances=0,
            remaining_to_trigger=100,
            final_flash_frames=100,
            log_message="最终撞闪剩余 100 帧",
        )
    )

    assert panel.status_badge.text() == "运行撞闪脚本"
    assert "最终撞闪剩余 100 帧" in panel.log_view.toPlainText()


def test_manual_reidentify_invalid_seed_uses_chinese_error_title(app, monkeypatch):
    window = MainWindow()
    errors: list[str] = []
    monkeypatch.setattr(window, "_show_error", lambda title, _error, **_kwargs: errors.append(title))
    for box in window.seed32_inputs:
        box.clear()

    window.reidentify_seed()

    assert errors == ["校正失败"]
    window._capture_mode = "reidentify"
    assert window._capture_mode_label() == "校正捕捉"


def test_auto_rng_progress_current_advances_syncs_header_counter(app):
    window = MainWindow()
    window._advance_timer.stop()
    window._advance_step = 1
    window._tracked_advances = 990
    window._update_auto_rng_header(advances=990)

    window._apply_auto_rng_header_progress(
        AutoRngProgress(
            phase=AutoRngPhase.RUN_HIT_SCRIPT,
            loop_index=1,
            current_advances=1000,
        )
    )

    assert window._tracked_advances == 1000
    assert window.auto_advance_badge.text() == "advance 1000"
    assert not window._advance_timer.isActive()


def test_auto_seed_captured_does_not_start_local_advance_timer(app):
    window = MainWindow()
    window._advance_timer.stop()

    window._handle_auto_seed_captured(
        AutoRngSeedResult(
            seed=SeedPair64(0x1111111122222222, 0x3333333344444444),
            current_advances=1234,
            npc=0,
        )
    )

    assert window._tracked_advances == 1234
    assert window.auto_advance_badge.text() == "advance 1234"
    assert not window._advance_timer.isActive()


def test_advance_tick_catches_up_from_elapsed_time(app, monkeypatch):
    window = MainWindow()
    current_time = [100.0]
    monkeypatch.setattr(main_window_module.time, "monotonic", lambda: current_time[0])

    window._start_auto_advance_tracking(
        AutoRngSeedResult(
            seed=SeedPair64(0, 0),
            current_advances=990,
            npc=0,
            measured_at=100.0,
        )
    )
    current_time[0] = 100.0 + 10 * 1.018
    window._advance_tick()

    assert window._tracked_advances == 1000
    assert window.auto_advance_badge.text() == "advance 1000"


def test_history_panel_reverse_lookup_candidates_are_single_line(app):
    panel = HistoryPanel()
    state = SimpleNamespace(
        advances=1234,
        ec=0xAABBCCDD,
        pid=0x11223344,
        ivs=(31, 30, 29, 28, 27, 26),
        ability=1,
        gender=0,
        nature=0,
        shiny=2,
        height=255,
        weight=12,
    )

    panel.reverse_lookup_results([state], characteristic="喜欢吃东西", delays=[99])

    lines = [line for line in panel.text_view.toPlainText().splitlines() if line.strip()]
    reverse_lines = [line for line in lines if "反查候选" in line]
    assert len(reverse_lines) == 1
    assert "adv=1234" in reverse_lines[0]
    assert "delay=99" in reverse_lines[0]
    assert "EC=AABBCCDD" in reverse_lines[0]
    assert "PID=11223344" in reverse_lines[0]
    assert "HP=31" in reverse_lines[0]
    assert "身高=255" in reverse_lines[0]
    assert "体重=12" in reverse_lines[0]
    assert not any(line.strip().startswith("EC:") for line in lines)


def test_history_panel_candidates_use_configured_delay(app):
    panel = HistoryPanel()
    state = SimpleNamespace(
        advances=1234,
        ec=0xAABBCCDD,
        pid=0x11223344,
        ivs=(31, 30, 29, 28, 27, 26),
        ability=1,
        gender=0,
        nature=0,
        shiny=2,
        height=255,
        weight=12,
    )

    panel.candidates_found([state], locked_index=0, candidate_delay=321)

    text = panel.text_view.toPlainText()
    assert "adv=1234" in text
    assert "delay=321" in text
    assert "delay=1234" not in text


def test_auto_rng_escape_attempt_history_includes_loop_and_attempt(app, monkeypatch):
    window = MainWindow()
    run_logs: list[str] = []
    monkeypatch.setattr(
        window,
        "_write_run_log",
        lambda _source, message, **_kwargs: run_logs.append(str(message)),
    )

    window._handle_auto_history_event(
        "attempt_result",
        (2, 3, False, 2.3456, 1234, 100),
    )

    history_text = window.history_tab.text_view.toPlainText()
    assert "第 2 轮 / 第 3 次撞闪" in history_text
    assert "准备运行逃跑脚本，完成后继续搜索" in history_text
    assert "逃跑后继续" not in history_text
    assert run_logs == [
        "第 2 轮 / 第 3 次撞闪未出闪；间隔 2.3456；启动 Adv 1234；delay 100"
    ]


def test_auto_rng_log_adds_timestamp(app, monkeypatch):
    panel = AutoRngPanel()

    class FixedDatetime(datetime):
        @classmethod
        def now(cls):
            return cls(2026, 5, 14, 12, 34, 56)

    monkeypatch.setattr("auto_bdsp_rng.ui.auto_rng_panel.datetime", FixedDatetime)

    panel.add_log("第一行\n[01:02:03] 已有时间")

    lines = panel.log_view.toPlainText().splitlines()
    assert lines[0] == "[12:34:56] 第一行"
    assert lines[1] == "[01:02:03] 已有时间"


def test_auto_rng_log_sink_receives_levels_and_cannot_break_ui(app):
    events: list[tuple[str, str]] = []
    panel = AutoRngPanel(run_log_sink=lambda level, message: events.append((level, message)))

    panel.add_log("普通事件")
    panel.apply_progress(AutoRngProgress(phase=AutoRngPhase.FAILED, log_message="流程失败"))
    panel._runner_failed("worker 失败")

    assert events == [
        ("INFO", "普通事件"),
        ("ERROR", "流程失败"),
        ("ERROR", "worker 失败"),
    ]

    def broken_sink(_level: str, _message: str) -> None:
        raise OSError("disk full")

    panel._run_log_sink = broken_sink
    panel.add_log("仍写入界面")

    assert "仍写入界面" in panel.log_view.toPlainText()




def test_auto_rng_panel_logs_same_failed_progress_message_once(app, tmp_path):
    events: list[tuple[str, str]] = []
    panel = AutoRngPanel(
        script_dir=tmp_path,
        settings=_auto_rng_settings(tmp_path),
        run_log_sink=lambda level, message: events.append((level, message)),
    )
    progress = AutoRngProgress(
        phase=AutoRngPhase.FAILED,
        log_message="OCR 结果未知，请人工确认",
    )

    class FailedRunner:
        def run(self) -> AutoRngProgress:
            self.progress_callback(progress)
            return progress

    worker = AutoRngWorker(FailedRunner())
    worker.progressChanged.connect(panel.apply_progress)
    worker.failed.connect(panel._runner_failed)
    worker.run()

    assert panel.status_badge.text() == "失败"
    assert panel.log_view.toPlainText().count(progress.log_message) == 1
    assert events == [("ERROR", progress.log_message)]


def test_auto_rng_panel_reports_cancelled_idle_result_as_stopped(app, tmp_path):
    panel = AutoRngPanel(script_dir=tmp_path, settings=_auto_rng_settings(tmp_path))

    panel._runner_finished(AutoRngProgress(phase=AutoRngPhase.IDLE))

    assert panel.status_badge.text() == "已停止"


def test_auto_rng_worker_emits_progress_and_finished(app):
    progress = AutoRngProgress(phase=AutoRngPhase.COMPLETED, log_message="完成")

    class FakeRunner:
        def __init__(self) -> None:
            self.progress_callback = None
            self.log_callback = None
            self.stopped = False

        def run(self) -> AutoRngProgress:
            self.progress_callback(progress)
            if self.log_callback is not None:
                self.log_callback("完成")
            return progress

        def stop(self) -> None:
            self.stopped = True

    runner = FakeRunner()
    worker = AutoRngWorker(runner)
    progress_events: list[AutoRngProgress] = []
    logs: list[str] = []
    finished: list[AutoRngProgress] = []
    worker.progressChanged.connect(progress_events.append)
    worker.logEmitted.connect(logs.append)
    worker.finished.connect(finished.append)

    worker.run()
    worker.stop()

    assert progress_events == [progress]
    assert logs == []
    assert finished == [progress]
    assert runner.stopped is True


def test_auto_rng_worker_emits_failed_for_failed_result(app):
    progress = AutoRngProgress(phase=AutoRngPhase.FAILED, log_message="OCR 结果未知，请人工确认")

    class FakeRunner:
        def run(self) -> AutoRngProgress:
            return progress

    worker = AutoRngWorker(FakeRunner())
    finished = []
    failed = []
    worker.finished.connect(finished.append)
    worker.failed.connect(failed.append)

    worker.run()

    assert finished == []
    assert failed == ["OCR 结果未知，请人工确认"]


def test_main_window_starts_auto_rng_runner_from_panel_signal(app, tmp_path, monkeypatch):
    window = MainWindow()
    window._ocr_warmup_result = (True, "OCR预热完成")
    seed_script = tmp_path / "BDSP测种.txt"
    advance_script = tmp_path / "bdsp过帧.txt"
    hit_script = tmp_path / "谢米.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    advance_script.write_text("_目标帧数 = 100\n", encoding="utf-8")
    hit_script.write_text("_闪帧 = 100\n", encoding="utf-8")
    config = AutoRngConfig(
        script_dir=tmp_path,
        seed_script_path=seed_script,
        advance_script_path=advance_script,
        hit_script_path=hit_script,
    )
    started: list[AutoRngRunner] = []
    window._latest_preview_frame = object()
    monkeypatch.setattr(window, "_ensure_bridge_connected", lambda: True)
    monkeypatch.setattr(window.auto_rng_tab, "run_with_runner", started.append)

    window._start_auto_rng(config)

    assert len(started) == 1
    assert isinstance(started[0], AutoRngRunner)
    assert started[0].config == AutoRngConfig(
        script_dir=tmp_path,
        seed_script_path=seed_script,
        advance_script_path=advance_script,
        hit_script_path=hit_script,
        seed_config_path=window._selected_auto_seed_config_path(),
        reidentify_config_path=window._selected_auto_reidentify_config_path(),
    )


def test_main_window_auto_rng_start_prepares_preview_frame_when_inactive(app, tmp_path, monkeypatch):
    window = MainWindow()
    window._ocr_warmup_result = (True, "OCR预热完成")
    config = AutoRngConfig(script_dir=tmp_path)
    started: list[AutoRngRunner] = []
    preview_updates: list[str] = []

    def fake_update_preview() -> None:
        preview_updates.append("updated")
        window._latest_preview_frame = object()

    monkeypatch.setattr(window, "_update_preview_frame", fake_update_preview)
    monkeypatch.setattr(window, "_ensure_bridge_connected", lambda: True)
    monkeypatch.setattr(window.auto_rng_tab, "run_with_runner", started.append)

    window._start_auto_rng(config)

    assert not window._preview_timer.isActive()
    assert preview_updates == ["updated"]
    assert len(started) == 1


def test_main_window_auto_rng_waits_for_lazy_ocr_warmup_then_starts(app, tmp_path, monkeypatch):
    window = MainWindow()
    config = AutoRngConfig(script_dir=tmp_path)
    started: list[AutoRngRunner] = []
    warmups: list[bool] = []
    window._latest_preview_frame = object()
    monkeypatch.setattr(window, "_start_ocr_warmup", lambda: warmups.append(True))
    monkeypatch.setattr(window, "_ensure_bridge_connected", lambda: True)
    monkeypatch.setattr(window.auto_rng_tab, "run_with_runner", started.append)

    window._start_auto_rng(config)

    assert warmups == [True]
    assert started == []
    assert window._ocr_after_warmup is not None
    assert window._ocr_after_warmup[0] == "auto_rng"

    window._handle_ocr_warmup_completed(True, "OCR预热完成")
    app.processEvents()

    assert len(started) == 1


def test_live_preview_only_keeps_wgc_capture_open(app, monkeypatch):
    class FakePreviewCapture:
        instances = []

        def __init__(self, config):
            self.config = config
            self.keep_open_for_preview = config.monitor_window
            self.release_count = 0
            type(self).instances.append(self)

        def read(self):
            return f"frame-{len(type(self).instances)}"

        def release(self):
            self.release_count += 1

    monkeypatch.setattr(main_window_module, "PreviewFrameCapture", FakePreviewCapture)
    window = MainWindow()
    camera_config = BlinkCaptureConfig(Path("eye.png"), (0, 0, 1, 1), monitor_window=False)

    assert window._read_live_preview_frame(camera_config) == "frame-1"
    assert window._read_live_preview_frame(camera_config) == "frame-2"
    assert window._preview_capture is None
    assert [capture.release_count for capture in FakePreviewCapture.instances] == [1, 1]

    obs_config = BlinkCaptureConfig(
        Path("eye.png"),
        (0, 0, 1, 1),
        monitor_window=True,
        window_prefix="投影 - 源：窗口采集 2",
    )
    assert window._read_live_preview_frame(obs_config) == "frame-3"
    same_source_config = BlinkCaptureConfig(
        Path("different-eye.png"),
        (10, 20, 30, 40),
        blink_count=7,
        monitor_window=True,
        window_prefix="投影 - 源：窗口采集 2",
        crop=(0, 0, 0, 0),
    )
    assert window._read_live_preview_frame(same_source_config) == "frame-3"
    assert len(FakePreviewCapture.instances) == 3
    assert window._preview_capture is FakePreviewCapture.instances[2]

    window._release_preview_capture()

    assert FakePreviewCapture.instances[2].release_count == 1


def test_active_preview_frame_request_reuses_live_capture(app, monkeypatch):
    config = BlinkCaptureConfig(
        Path("eye.png"),
        (0, 0, 1, 1),
        monitor_window=True,
        window_prefix="投影 - 源：窗口采集 2",
    )

    class FakePreviewCapture:
        keep_open_for_preview = True

        def __init__(self, actual_config):
            self.config = actual_config
            self.read_count = 0

        def read(self):
            self.read_count += 1
            return "shared-frame"

        def release(self):
            pass

    monkeypatch.setattr(main_window_module, "PreviewFrameCapture", FakePreviewCapture)
    monkeypatch.setattr(
        main_window_module,
        "capture_preview_frame",
        lambda _config: pytest.fail("Active preview must reuse its capture source"),
    )
    window = MainWindow()
    monkeypatch.setattr(window, "_config_from_form", lambda: SimpleNamespace(capture=config))
    window._preview_timer.start()

    assert window._capture_preview_frame_for_config(config) == "shared-frame"
    assert window._capture_preview_frame_for_config(config) == "shared-frame"
    assert window._preview_capture.read_count == 2


def test_main_window_close_stops_manual_capture_thread(app):
    window = MainWindow()
    started = threading.Event()

    def capture_worker() -> None:
        started.set()
        window._capture_cancel.wait(2.0)

    thread = threading.Thread(target=capture_worker, daemon=True)
    window._capture_thread = thread
    thread.start()
    assert started.wait(1.0)

    window.close()

    assert window._capture_cancel.is_set()
    assert not thread.is_alive()


def test_main_window_cancelled_close_keeps_update_task_running(app, monkeypatch):
    window = MainWindow()
    window.show()
    shutdown_calls: list[bool] = []
    monkeypatch.setattr(window.easycon_tab, "has_unsaved_script_changes", lambda: True)
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Cancel,
    )
    monkeypatch.setattr(
        window.update_controller,
        "shutdown",
        lambda: shutdown_calls.append(True) or True,
    )

    assert window.close() is False
    assert window.isVisible()
    assert shutdown_calls == []

    monkeypatch.setattr(window.easycon_tab, "has_unsaved_script_changes", lambda: False)
    assert window.close() is True


def test_main_window_refuses_close_while_easycon_thread_is_still_running(app, monkeypatch):
    window = MainWindow()
    window.show()
    warnings: list[tuple[str, str]] = []
    shutdown_order: list[str] = []
    monkeypatch.setattr(
        window,
        "_request_automation_runner_stop",
        lambda _panel, label: shutdown_order.append(label),
    )
    monkeypatch.setattr(
        window,
        "_shutdown_automation_runner",
        lambda _panel, _label, **_kwargs: True,
    )
    monkeypatch.setattr(
        window.easycon_tab,
        "shutdown",
        lambda: shutdown_order.append("伊机控") or False,
    )
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )

    assert window.close() is False
    assert window.isVisible()
    assert window._is_closing is False
    assert warnings and warnings[-1][0] == "正在停止伊机控任务"
    assert shutdown_order == ["自动定点", "自动 TID", "伊机控"]

    monkeypatch.setattr(window.easycon_tab, "shutdown", lambda: True)
    assert window.close() is True


def test_shutdown_automation_runner_stops_and_waits(app):
    window = MainWindow()
    calls = []

    class FakeThread:
        def __init__(self):
            self.running = True

        def quit(self):
            calls.append("quit")

        def isRunning(self):
            return self.running

        def wait(self, milliseconds):
            calls.append(("wait", milliseconds))
            self.running = False
            return True

    panel = SimpleNamespace(
        _runner_worker=SimpleNamespace(stop=lambda: calls.append("stop")),
        _runner_thread=FakeThread(),
    )

    assert window._shutdown_automation_runner(panel, "测试流程") is True

    assert calls == ["stop", "quit", ("wait", 50)]


def test_shutdown_worker_thread_reports_timeout(app):
    window = MainWindow()
    calls = []

    class FakeThread:
        def quit(self):
            calls.append("quit")

        def isRunning(self):
            return True

        def wait(self, milliseconds):
            calls.append(("wait", milliseconds))
            return False

    worker = SimpleNamespace(stop=lambda: calls.append("stop"))

    assert window._shutdown_worker_thread(worker, FakeThread(), "测试流程", wait_ms=0) is False
    assert calls == ["stop", "quit"]


def test_shutdown_capture_thread_reports_timeout(app):
    window = MainWindow()

    class FakeCaptureThread:
        def is_alive(self):
            return True

        def join(self, *, timeout):
            pytest.fail(f"zero wait must not call join: {timeout}")

    window._capture_thread = FakeCaptureThread()  # type: ignore[assignment]
    assert window._shutdown_capture_thread(wait_ms=0) is False
    window._capture_thread = None


def test_shutdown_ocr_warmup_is_bounded_and_never_terminates_thread(app):
    window = MainWindow()
    calls = []

    class FakeSignal:
        def disconnect(self, _slot):
            calls.append("disconnect")

    class FakeWarmupThread:
        completed = FakeSignal()

        def __init__(self):
            self.running = True

        def requestInterruption(self):
            calls.append("interrupt")

        def isRunning(self):
            return self.running

        def wait(self, milliseconds):
            calls.append(("wait", milliseconds))
            self.running = False
            return True

    window._ocr_warmup_running = True
    window._ocr_warmup_thread = FakeWarmupThread()  # type: ignore[assignment]

    assert window._shutdown_ocr_warmup_thread() is True
    assert calls == ["interrupt", ("wait", 50), "disconnect"]
    assert window._ocr_warmup_running is False
    window._ocr_warmup_thread = None


def test_main_window_notifies_automation_before_easycon_and_refuses_live_runner(app, monkeypatch):
    window = MainWindow()
    window.show()
    shutdown_order: list[str] = []
    warnings: list[tuple[str, str]] = []

    monkeypatch.setattr(
        window,
        "_request_automation_runner_stop",
        lambda _panel, label: shutdown_order.append(f"notify:{label}"),
    )
    monkeypatch.setattr(
        window.easycon_tab,
        "shutdown",
        lambda: shutdown_order.append("easycon") or True,
    )

    def shutdown_runner(_panel, label, **_kwargs):
        shutdown_order.append(f"wait:{label}")
        return label != "自动定点"

    monkeypatch.setattr(window, "_shutdown_automation_runner", shutdown_runner)
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )

    assert window.close() is False
    assert window.isVisible()
    assert window._is_closing is False
    assert shutdown_order == [
        "notify:自动定点",
        "notify:自动 TID",
        "easycon",
        "wait:自动定点",
        "wait:自动 TID",
    ]
    assert warnings == [("正在停止后台任务", "自动定点尚未退出，请稍后再关闭程序。")]

    monkeypatch.setattr(
        window,
        "_shutdown_automation_runner",
        lambda _panel, _label, **_kwargs: True,
    )
    assert window.close() is True


def test_main_window_refuses_close_while_local_background_threads_are_running(app, monkeypatch):
    window = MainWindow()
    window.show()
    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(window, "_request_automation_runner_stop", lambda *_args: None)
    monkeypatch.setattr(
        window,
        "_shutdown_automation_runner",
        lambda _panel, _label, **_kwargs: True,
    )
    monkeypatch.setattr(window.easycon_tab, "shutdown", lambda: True)
    monkeypatch.setattr(window, "_shutdown_worker_thread", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(window, "_shutdown_capture_thread", lambda: False)
    monkeypatch.setattr(window, "_shutdown_ocr_warmup_thread", lambda: False)
    monkeypatch.setattr(window, "_shutdown_ocr_task_thread", lambda: False)
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )

    assert window.close() is False
    assert window.isVisible()
    assert window._is_closing is False
    assert warnings == [
        (
            "正在停止后台任务",
            "闪光判定校准、Seed 捕捉、OCR 预热、OCR 识别尚未退出，请稍后再关闭程序。",
        )
    ]

    monkeypatch.setattr(window, "_shutdown_worker_thread", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(window, "_shutdown_capture_thread", lambda: True)
    monkeypatch.setattr(window, "_shutdown_ocr_warmup_thread", lambda: True)
    monkeypatch.setattr(window, "_shutdown_ocr_task_thread", lambda: True)
    assert window.close() is True


def test_close_rejection_cancels_lazy_ocr_auto_start_and_restores_idle_status(app):
    window = MainWindow()
    window._ocr_shutdown_requested = True
    window._ocr_after_warmup = ("auto_rng", lambda: None)
    window._ocr_warmup_running = True
    window.auto_rng_tab.set_phase_text("初始化 OCR")

    window._restore_interrupted_ocr_state_if_idle()

    assert window._ocr_after_warmup is None
    assert window._ocr_warmup_running is False
    assert window.auto_rng_tab.status_badge.text() == AutoRngPhase.IDLE.value
    assert "自动流程未启动" in window.auto_rng_tab.log_view.toPlainText()


def test_main_window_auto_rng_services_search_with_bdsp_snapshot(app, tmp_path):
    window = MainWindow()
    window.tabs.setCurrentWidget(window.bdsp_tab)
    _set_bdsp_seed(window)
    window.max_advances.setText("2")
    record, _state_filter, _shiny_mode = window.auto_rng_tab.targets()[0]
    window.auto_rng_tab.set_targets([(record, StateFilter(), "any")])
    services = window._build_auto_rng_services(AutoRngConfig(script_dir=tmp_path, max_advances=2))

    candidates = services.search_candidates(AutoRngSeedResult(seed=window._current_seed_pair()))

    assert [state.advances for state in candidates] == [0, 1, 2]
    assert "搜索目标" in window.auto_rng_tab.log_view.toPlainText()


def test_zoom_recovery_pauses_preview_and_waits_before_capturing(app, monkeypatch):
    window = MainWindow()
    capture_config = BlinkCaptureConfig(Path("eye.png"), (0, 0, 1, 1), monitor_window=False)
    events = []
    window._preview_timer.start()

    monkeypatch.setattr(main_window_module.time, "sleep", lambda seconds: events.append(("wait", seconds)))
    monkeypatch.setattr(main_window_module, "capture_preview_frame", lambda _config: events.append(("capture",)) or object())

    def fake_recover(capture_frame, _run_script_text, *, should_stop):
        assert not window._preview_timer.isActive()
        assert should_stop() is False
        capture_frame()
        events.append(("recover",))
        return True

    monkeypatch.setattr(main_window_module, "recover_zoom_overlay", fake_recover)

    assert window._recover_zoom_mode_with_preview_paused(capture_config, lambda _text, _name: None) is True
    assert events == [("wait", 0.3), ("capture",), ("recover",)]
    assert window._preview_timer.isActive()


def test_auto_rng_zoom_recovery_service_uses_preview_safe_recovery(app, tmp_path, monkeypatch):
    window = MainWindow()
    capture_config = BlinkCaptureConfig(Path("eye.png"), (0, 0, 1, 1), monitor_window=False)
    tracking_config = ProjectXsTrackingConfig(Path("seed.json"), capture_config)
    calls = []
    monkeypatch.setattr(main_window_module, "load_project_xs_config", lambda *_args, **_kwargs: tracking_config)
    monkeypatch.setattr(
        window,
        "_recover_zoom_mode_with_preview_paused",
        lambda actual_capture, _run_script: calls.append(actual_capture) or True,
    )

    services = window._build_auto_rng_services(AutoRngConfig(script_dir=tmp_path))

    assert services.recover_zoom_mode() is True
    assert calls == [capture_config]


def test_auto_tid_zoom_recovery_service_uses_preview_safe_recovery(app, tmp_path, monkeypatch):
    window = MainWindow()
    capture_config = BlinkCaptureConfig(Path("eye.png"), (0, 0, 1, 1), monitor_window=False)
    tracking_config = ProjectXsTrackingConfig(Path("seed.json"), capture_config)
    calls = []
    monkeypatch.setattr(main_window_module, "load_project_xs_config", lambda *_args, **_kwargs: tracking_config)
    monkeypatch.setattr(
        window,
        "_recover_zoom_mode_with_preview_paused",
        lambda actual_capture, _run_script: calls.append(actual_capture) or True,
    )

    services = window._build_auto_tid_rng_services(AutoTidRngConfig(script_dir=tmp_path))

    assert services.recover_zoom_mode() is True
    assert calls == [capture_config]


def test_main_window_auto_rng_services_search_uses_multi_targets(app, tmp_path, monkeypatch):
    window = MainWindow()
    _set_bdsp_seed(window)
    window.max_advances.setText("2")
    window.auto_rng_tab.max_advances.setValue(9)
    # 直接设置 _targets 列表模拟已添加目标
    from auto_bdsp_rng.data import get_static_encounters
    from auto_bdsp_rng.gen8_static import StateFilter
    records = [r for r in get_static_encounters() if r.description == "Shaymin"]
    if records:
        sf = StateFilter(height_min=0, height_max=0)
        window.auto_rng_tab._targets = [(records[0], sf, "square")]
    captured = []

    def fake_generate(criteria, targets):
        captured.append((criteria, targets))
        return []

    monkeypatch.setattr(main_window_module, "generate_static_candidates_multi", fake_generate)
    services = window._build_auto_rng_services(AutoRngConfig(script_dir=tmp_path, max_advances=9))

    services.search_candidates(AutoRngSeedResult(seed=window._current_seed_pair()))

    assert len(captured) == 1
    criteria, targets = captured[0]
    assert criteria.record.description == "Shaymin"
    assert criteria.max_advances == 9
    assert targets[0].shiny_mode == "square"
    assert targets[0].state_filter.height_min == 0
    assert targets[0].state_filter.height_max == 0


def test_main_window_auto_rng_capture_service_uses_project_xs(app, tmp_path, monkeypatch):
    window = MainWindow()
    seed_state = SeedState32(0x11111111, 0x22222222, 0x33333333, 0x44444444)
    observation = BlinkObservation.from_sequences([1, 0], [12, 24], offset_time=100.0)
    captured_counts: list[int] = []
    warmup_windows: list[float | None] = []
    recovered_observations: list[BlinkObservation] = []

    def fake_capture(config, *_args, **kwargs):
        captured_counts.append(config.blink_count)
        warmup_windows.append(kwargs.get("discard_first_blink_within_seconds"))
        return observation

    def fake_recover(actual_observation, npc):
        recovered_observations.append(actual_observation)
        return SimpleNamespace(state=seed_state, advances=0)

    monkeypatch.setattr(main_window_module.time, "perf_counter", lambda: 100.0)
    monkeypatch.setattr(main_window_module, "capture_player_blinks", fake_capture)
    monkeypatch.setattr(main_window_module, "recover_seed_from_observation", fake_recover)
    services = window._build_auto_rng_services(AutoRngConfig(script_dir=tmp_path))

    result = services.capture_seed()

    assert result.seed == seed_state
    assert result.current_advances == 0
    assert result.seed_text == "1111111122222222 3333333344444444"
    assert captured_counts == [40]
    assert warmup_windows == [1.0]
    assert recovered_observations[0].blinks == (1, 0)
    assert recovered_observations[0].intervals == (12, 24)


def test_main_window_auto_rng_capture_syncs_seed_tab_and_bdsp_results(app, tmp_path, monkeypatch):
    window = MainWindow()
    seed_state = SeedState32(0x11111111, 0x22222222, 0x33333333, 0x44444444)
    observation = BlinkObservation.from_sequences([1, 0], [12, 24], offset_time=100.0)
    displayed_frames: list[object] = []
    generated = []

    def fake_capture(_config, **kwargs):
        kwargs["progress_callback"](3, 40)
        kwargs["frame_callback"]("frame-1")
        return observation

    def fake_generate(criteria):
        generated.append(criteria)
        return []

    monkeypatch.setattr(window, "_display_frame", lambda frame: displayed_frames.append(frame))
    monkeypatch.setattr(main_window_module.time, "perf_counter", lambda: 100.0)
    monkeypatch.setattr(main_window_module, "capture_player_blinks", fake_capture)
    monkeypatch.setattr(
        main_window_module,
        "recover_seed_from_observation",
        lambda actual_observation, npc: SimpleNamespace(state=seed_state, advances=0),
    )
    monkeypatch.setattr(main_window_module, "generate_static_candidates", fake_generate)
    services = window._build_auto_rng_services(AutoRngConfig(script_dir=tmp_path, max_advances=9))

    result = services.capture_seed()
    QApplication.processEvents()

    assert result.seed == seed_state
    assert displayed_frames == ["frame-1"]
    assert window.progress_value.text() == "3/40"
    assert [box.text() for box in window.seed32_inputs] == ["11111111", "22222222", "33333333", "44444444"]
    assert [box.text() for box in window.bdsp_seed64_inputs] == ["1111111122222222", "3333333344444444"]
    _, target_filter, _ = window.auto_rng_tab.targets()[0]
    assert window.iv_min[0].text() == str(target_filter.iv_min[0])
    assert window.iv_max[0].text() == str(target_filter.iv_max[0])
    assert window.height_max.text() == str(target_filter.height_max)
    assert len(generated) >= 1
    assert generated[-1].seed == seed_state.to_seed_pair64()
    assert generated[-1].state_filter.iv_min[0] == target_filter.iv_min[0]


def test_main_window_auto_rng_capture_stop_does_not_show_failure_dialog(app, tmp_path, monkeypatch):
    window = MainWindow()
    warnings: list[tuple[str, str]] = []

    def fake_capture(_config, **_kwargs):
        window._capture_cancel.set()
        raise ProjectXsIntegrationError("Blink capture stopped")

    monkeypatch.setattr(main_window_module, "capture_player_blinks", fake_capture)
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "warning",
        lambda _parent, title, text: warnings.append((title, text)),
    )
    services = window._build_auto_rng_services(AutoRngConfig(script_dir=tmp_path))

    with pytest.raises(ProjectXsIntegrationError, match="Blink capture stopped"):
        services.capture_seed()

    assert warnings == []


def test_main_window_auto_rng_capture_preview_controls_run_on_ui_thread(app, tmp_path, monkeypatch):
    window = MainWindow()
    seed_state = SeedState32(0x11111111, 0x22222222, 0x33333333, 0x44444444)
    observation = SimpleNamespace(offset_time=100.0)
    ui_thread = window.thread()
    touched: list[str] = []

    def assert_ui_thread(name: str) -> None:
        assert QThread.currentThread() == ui_thread
        touched.append(name)

    window._preview_timer.start()
    original_stop_preview = window._preview_timer.stop
    original_start_preview = window._preview_timer.start

    def stop_preview() -> None:
        assert_ui_thread("stop_preview")
        original_stop_preview()

    def start_preview() -> None:
        assert_ui_thread("start_preview")
        original_start_preview()

    monkeypatch.setattr(window._preview_timer, "stop", stop_preview)
    monkeypatch.setattr(window._preview_timer, "start", start_preview)
    monkeypatch.setattr(window.preview_label, "clear", lambda: assert_ui_thread("clear_preview"))
    monkeypatch.setattr(window.preview_button, "setText", lambda _text: assert_ui_thread("set_preview_text"))
    monkeypatch.setattr(window.preview_label, "setText", lambda _text: assert_ui_thread("set_preview_label"))
    monkeypatch.setattr(main_window_module.time, "perf_counter", lambda: 100.0)
    monkeypatch.setattr(main_window_module, "capture_player_blinks", lambda *_args, **_kwargs: observation)
    monkeypatch.setattr(
        main_window_module,
        "recover_seed_from_observation",
        lambda actual_observation, npc: SimpleNamespace(state=seed_state, advances=0),
    )
    monkeypatch.setattr(main_window_module, "generate_static_candidates", lambda _criteria: [])
    services = window._build_auto_rng_services(AutoRngConfig(script_dir=tmp_path))

    class CaptureRunner:
        def run(self):
            services.capture_seed()
            return AutoRngProgress(phase=AutoRngPhase.IDLE)

        def stop(self) -> None:
            pass

    window.auto_rng_tab.run_with_runner(CaptureRunner())
    deadline = time.monotonic() + 2.0
    while window.auto_rng_tab._runner_thread is not None and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    QApplication.processEvents()

    assert window.auto_rng_tab._runner_thread is None
    assert "stop_preview" in touched
    assert "start_preview" in touched
    window._preview_timer.stop()
    window.close()


def test_main_window_auto_rng_reidentify_service_uses_project_xs(app, tmp_path, monkeypatch):
    window = MainWindow()
    seed_state = SeedState32(0xAAAAAAAA, 0xBBBBBBBB, 0xCCCCCCCC, 0xDDDDDDDD)
    calls: list[SeedState32] = []
    capture_counts: list[int] = []
    warmup_windows: list[float | None] = []
    passed_observations: list[BlinkObservation] = []
    observation = BlinkObservation.from_sequences([1, 0], [12, 24], offset_time=100.0)

    def fake_capture(config, *_args, **kwargs):
        capture_counts.append(config.blink_count)
        warmup_windows.append(kwargs.get("discard_first_blink_within_seconds"))
        return observation

    monkeypatch.setattr(main_window_module.time, "perf_counter", lambda: 105.0)
    monkeypatch.setattr(main_window_module, "capture_player_blinks", fake_capture)

    def fake_reidentify(current_state, actual_observation, **_kwargs):
        calls.append(current_state)
        passed_observations.append(actual_observation)
        return SimpleNamespace(state=seed_state, advances=42)

    monkeypatch.setattr(main_window_module, "reidentify_seed_from_observation", fake_reidentify)
    services = window._build_auto_rng_services(AutoRngConfig(script_dir=tmp_path))

    result = services.reidentify(AutoRngSeedResult(seed=SeedPair64(0x1111111122222222, 0x3333333344444444)))
    QApplication.processEvents()

    assert calls == [SeedState32(0x11111111, 0x22222222, 0x33333333, 0x44444444)]
    assert result.seed == SeedPair64(0x1111111122222222, 0x3333333344444444)
    assert result.current_advances == 47
    assert capture_counts == [7]
    assert warmup_windows == [1.0]
    assert passed_observations[0].blinks == (1, 0)
    assert passed_observations[0].intervals == (12, 24)
    assert int(window.advances_value.text()) >= 47
    assert not window._advance_timer.isActive()


def test_main_window_auto_rng_reidentify_uses_hint_limited_regular_search_range(app, tmp_path, monkeypatch):
    window = MainWindow()
    seed_state = SeedState32(0xAAAAAAAA, 0xBBBBBBBB, 0xCCCCCCCC, 0xDDDDDDDD)
    observation = BlinkObservation.from_sequences([1, 0], [12, 24], offset_time=0.0)
    search_ranges: list[tuple[int, int, int]] = []

    def fake_load_config(path, blink_count):
        return ProjectXsTrackingConfig(
            source_path=tmp_path / Path(str(path)).name,
            capture=BlinkCaptureConfig(
                eye_image_path=tmp_path / "eye.png",
                roi=(0, 0, 1, 1),
                blink_count=blink_count,
            ),
            npc=2,
        )

    def fake_capture(config, *_args, **_kwargs):
        return observation

    def fake_reidentify(current_state, _observation, **kwargs):
        search_ranges.append((kwargs["search_min"], kwargs["search_max"], kwargs["npc"]))
        return ProjectXsReidentifyResult(state=seed_state, observation=observation, advances=50_100)

    monkeypatch.setattr(main_window_module, "load_project_xs_config", fake_load_config)
    monkeypatch.setattr(main_window_module, "capture_player_blinks", fake_capture)
    monkeypatch.setattr(main_window_module, "reidentify_seed_from_observation", fake_reidentify)

    services = window._build_auto_rng_services(
        AutoRngConfig(
            script_dir=tmp_path,
            seed_config_path=str(tmp_path / "seed.json"),
            reidentify_config_path=str(tmp_path / "exit.json"),
            max_advances=2_000_000,
        )
    )

    services.reidentify(
        AutoRngSeedResult(
            seed=SeedPair64(0x1111111122222222, 0x3333333344444444),
            expected_advances_hint=50_000,
        )
    )

    assert search_ranges == [(40_000, 70_000, 2)]


def test_main_window_auto_rng_reidentify_uses_hint_limited_noisy_search_window(app, tmp_path, monkeypatch):
    window = MainWindow()
    seed_state = SeedState32(0xAAAAAAAA, 0xBBBBBBBB, 0xCCCCCCCC, 0xDDDDDDDD)
    observation = BlinkObservation.from_sequences([1, 0], [12, 24], offset_time=0.0)
    search_ranges: list[tuple[int, int]] = []

    def fake_load_config(path, blink_count):
        return ProjectXsTrackingConfig(
            source_path=tmp_path / Path(str(path)).name,
            capture=BlinkCaptureConfig(
                eye_image_path=tmp_path / "eye.png",
                roi=(0, 0, 1, 1),
                blink_count=blink_count,
            ),
            npc=2,
            pokemon_npc=1,
        )

    def fake_capture(config, *_args, **_kwargs):
        return observation

    def fake_noisy(current_state, _observation, **kwargs):
        search_ranges.append((kwargs["search_min"], kwargs["search_max"]))
        return ProjectXsReidentifyResult(state=seed_state, observation=observation, advances=50_100)

    monkeypatch.setattr(main_window_module, "load_project_xs_config", fake_load_config)
    monkeypatch.setattr(main_window_module, "capture_player_blinks", fake_capture)
    monkeypatch.setattr(main_window_module, "reidentify_seed_from_observation_noisy", fake_noisy)

    services = window._build_auto_rng_services(
        AutoRngConfig(
            script_dir=tmp_path,
            seed_config_path=str(tmp_path / "seed.json"),
            reidentify_config_path=str(tmp_path / "exit.json"),
            max_advances=2_000_000,
        )
    )

    services.reidentify(
        AutoRngSeedResult(
            seed=SeedPair64(0x1111111122222222, 0x3333333344444444),
            expected_advances_hint=50_000,
            after_exit_reseed=True,
        )
    )

    assert search_ranges == [(40_000, 30_000)]


def test_main_window_auto_rng_exit_reidentify_caps_noisy_search_without_hint(app, tmp_path, monkeypatch):
    window = MainWindow()
    seed_state = SeedState32(0xAAAAAAAA, 0xBBBBBBBB, 0xCCCCCCCC, 0xDDDDDDDD)
    observation = BlinkObservation.from_sequences([1, 0], [12, 24], offset_time=0.0)
    search_ranges: list[tuple[int, int]] = []

    def fake_load_config(path, blink_count):
        return ProjectXsTrackingConfig(
            source_path=tmp_path / Path(str(path)).name,
            capture=BlinkCaptureConfig(
                eye_image_path=tmp_path / "eye.png",
                roi=(0, 0, 1, 1),
                blink_count=blink_count,
            ),
            pokemon_npc=1,
        )

    def fake_capture(config, *_args, **_kwargs):
        return observation

    def fake_noisy(current_state, _observation, **kwargs):
        search_ranges.append((kwargs["search_min"], kwargs["search_max"]))
        return ProjectXsReidentifyResult(state=seed_state, observation=observation, advances=42)

    monkeypatch.setattr(main_window_module, "load_project_xs_config", fake_load_config)
    monkeypatch.setattr(main_window_module, "capture_player_blinks", fake_capture)
    monkeypatch.setattr(main_window_module, "reidentify_seed_from_observation_noisy", fake_noisy)

    services = window._build_auto_rng_services(
        AutoRngConfig(
            script_dir=tmp_path,
            seed_config_path=str(tmp_path / "seed.json"),
            reidentify_config_path=str(tmp_path / "exit.json"),
            max_advances=2_000_000,
        )
    )

    services.reidentify_exit(AutoRngSeedResult(seed=SeedPair64(0x1111111122222222, 0x3333333344444444)))

    assert search_ranges == [(0, 100_000)]


def test_main_window_auto_rng_reidentify_after_exit_uses_reidentify_config(app, tmp_path, monkeypatch):
    window = MainWindow()
    observation = SimpleNamespace(offset_time=100.0)
    loaded: list[tuple[str, int]] = []
    capture_counts: list[int] = []
    normal_calls: list[SeedState32] = []
    noisy_calls: list[SeedState32] = []

    def fake_load_config(path, blink_count):
        loaded.append((str(path), blink_count))
        return ProjectXsTrackingConfig(
            source_path=tmp_path / Path(str(path)).name,
            capture=BlinkCaptureConfig(
                eye_image_path=tmp_path / "eye.png",
                roi=(0, 0, 1, 1),
                blink_count=blink_count,
            ),
            npc=7,
            pokemon_npc=2,
            timeline_npc=3,
            white_delay=0.5,
            advance_delay=11,
            advance_delay_2=13,
        )

    def fake_capture(config, *_args, **_kwargs):
        capture_counts.append(config.blink_count)
        return observation

    def fake_reidentify(current_state, _observation, **_kwargs):
        normal_calls.append(current_state)
        return ProjectXsReidentifyResult(state=current_state, observation=observation, advances=42)

    def fake_noisy(current_state, _observation, **_kwargs):
        noisy_calls.append(current_state)
        return ProjectXsReidentifyResult(state=current_state, observation=observation, advances=42)

    monkeypatch.setattr(main_window_module, "load_project_xs_config", fake_load_config)
    monkeypatch.setattr(main_window_module, "capture_player_blinks", fake_capture)
    monkeypatch.setattr(main_window_module, "reidentify_seed_from_observation", fake_reidentify)
    monkeypatch.setattr(main_window_module, "reidentify_seed_from_observation_noisy", fake_noisy)
    monkeypatch.setattr(main_window_module.time, "perf_counter", lambda: 105.0)

    services = window._build_auto_rng_services(
        AutoRngConfig(
            script_dir=tmp_path,
            seed_config_path=str(tmp_path / "seed.json"),
            reidentify_config_path=str(tmp_path / "exit.json"),
        )
    )

    result = services.reidentify(
        AutoRngSeedResult(
            seed=SeedPair64(0x1111111122222222, 0x3333333344444444),
            after_exit_reseed=True,
        )
    )

    assert loaded == [
        (str(tmp_path / "seed.json"), 40),
        (str(tmp_path / "exit.json"), 20),
    ]
    assert capture_counts == [20]
    assert normal_calls == []
    assert noisy_calls == [SeedState32(0x11111111, 0x22222222, 0x33333333, 0x44444444)]
    assert result.current_advances == 82
    assert result.npc == 7
    assert result.after_exit_reseed is True
    assert result.advance_mode == "timeline"
    assert result.pokemon_npc == 2
    assert result.timeline_npc == 3
    assert result.white_delay == 0.5
    assert result.advance_delay == 11
    assert result.advance_delay_2 == 13


def test_main_window_auto_rng_exit_reidentify_uses_reidentify_config(app, tmp_path, monkeypatch):
    window = MainWindow()
    observation = SimpleNamespace(offset_time=100.0)
    loaded: list[tuple[str, int]] = []
    capture_counts: list[int] = []
    noisy_calls: list[SeedState32] = []

    def fake_load_config(path, blink_count):
        loaded.append((str(path), blink_count))
        return ProjectXsTrackingConfig(
            source_path=tmp_path / Path(str(path)).name,
            capture=BlinkCaptureConfig(
                eye_image_path=tmp_path / "eye.png",
                roi=(0, 0, 1, 1),
                blink_count=blink_count,
            ),
            npc=0,
            pokemon_npc=2,
            timeline_npc=3,
            white_delay=0.5,
            advance_delay=11,
            advance_delay_2=13,
        )

    def fake_capture(config, *_args, **_kwargs):
        capture_counts.append(config.blink_count)
        return observation

    def fake_noisy(current_state, _observation, **_kwargs):
        noisy_calls.append(current_state)
        return ProjectXsReidentifyResult(state=current_state, observation=observation, advances=42)

    monkeypatch.setattr(main_window_module, "load_project_xs_config", fake_load_config)
    monkeypatch.setattr(main_window_module, "capture_player_blinks", fake_capture)
    monkeypatch.setattr(main_window_module, "reidentify_seed_from_observation_noisy", fake_noisy)
    monkeypatch.setattr(main_window_module.time, "perf_counter", lambda: 105.0)

    services = window._build_auto_rng_services(
        AutoRngConfig(
            script_dir=tmp_path,
            seed_config_path=str(tmp_path / "seed.json"),
            reidentify_config_path=str(tmp_path / "exit.json"),
        )
    )

    result = services.reidentify_exit(
        AutoRngSeedResult(seed=SeedPair64(0x1111111122222222, 0x3333333344444444))
    )
    QApplication.processEvents()

    assert loaded == [
        (str(tmp_path / "seed.json"), 40),
        (str(tmp_path / "exit.json"), 20),
    ]
    assert capture_counts == [20]
    assert noisy_calls == [SeedState32(0x11111111, 0x22222222, 0x33333333, 0x44444444)]
    assert result.current_advances == 47
    assert result.npc == 0
    assert result.advance_mode == "timeline"
    assert result.pokemon_npc == 2
    assert result.timeline_npc == 3
    assert result.white_delay == 0.5
    assert result.advance_delay == 11
    assert result.advance_delay_2 == 13


def _install_connected_native_backend(window, monkeypatch, backend) -> None:
    window._video_source_connected = True
    monkeypatch.setattr(window.easycon_tab, "_native_status", lambda: EasyConStatus.BRIDGE_CONNECTED)
    monkeypatch.setattr(window.easycon_tab, "_ensure_native_backend", lambda: backend)


def test_main_window_auto_rng_run_script_service_uses_native_backend(app, tmp_path, monkeypatch):
    window = MainWindow()
    calls: list[tuple[str, str, Path]] = []

    class FakeBackend:
        def run_script_text(self, script_text: str, name: str, *, script_dir: Path) -> str:
            calls.append((script_text, name, script_dir))
            return "ok"

    _install_connected_native_backend(window, monkeypatch, FakeBackend())
    services = window._build_auto_rng_services(AutoRngConfig(script_dir=tmp_path))

    assert services.run_script_text("A 100", "hit.txt") == "ok"
    assert calls == [("A 100", "hit.txt", tmp_path)]


def test_auto_rng_script_installs_overlay_generation_before_backend_runs(app, tmp_path, monkeypatch):
    window = MainWindow()
    image_result = object()
    observations: list[tuple[int, object | None]] = []

    class FakeBackend:
        def __init__(self):
            self.image_callback = None

        def set_image_result_callback(self, callback):
            self.image_callback = callback

        def run_script_text(self, _script_text: str, _name: str, *, script_dir: Path) -> str:
            assert script_dir == tmp_path
            observations.append((window._easycon_run_generation, self.image_callback))
            assert self.image_callback is not None
            self.image_callback(image_result)
            return "ok"

    backend = FakeBackend()
    _install_connected_native_backend(window, monkeypatch, backend)
    services = window._build_auto_rng_services(AutoRngConfig(script_dir=tmp_path))
    results: list[object] = []
    errors: list[BaseException] = []

    def run_service() -> None:
        try:
            results.append(services.run_script_text("A 100", "hit.txt"))
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=run_service)
    worker.start()
    deadline = time.perf_counter() + 2
    while worker.is_alive() and time.perf_counter() < deadline:
        app.processEvents()
        QTest.qWait(5)
    worker.join(timeout=0.1)
    app.processEvents()

    assert not worker.is_alive()
    assert errors == []
    assert results == ["ok"]
    assert len(observations) == 1
    assert observations[0][0] == 1
    assert observations[0][1] is not None
    assert window._latest_easycon_image_search_result is image_result
    window._video_source_connected = False


def test_main_window_auto_rng_shiny_timeout_returns_unknown_result(app, tmp_path, monkeypatch):
    window = MainWindow()
    stops: list[bool] = []
    logs: list[str] = []

    class FakeBackend:
        def run_script_text(self, _script_text: str, _name: str, *, script_dir: Path) -> str:
            return "ok"

        def stop_current_script(self) -> None:
            stops.append(True)

    backend = FakeBackend()
    window.auto_rng_tab.captureLog.connect(logs.append)
    _install_connected_native_backend(window, monkeypatch, backend)
    monkeypatch.setattr(window, "_call_on_ui_thread", lambda callback: callback())

    def raise_timeout(*_args, **_kwargs):
        event = main_window_module.DialogTimingEvent("timeout_before_first", 31.0, 31.0)
        callback = _kwargs.get("event_callback")
        if callback is not None:
            callback(event)
        raise main_window_module.DialogKeywordTimeoutError(
            "OCR timeout",
            stage="before_first",
            event=event,
            events=(event,),
        )

    monkeypatch.setattr(main_window_module, "measure_keyword_interval", raise_timeout)
    services = window._build_auto_rng_services(
        AutoRngConfig(script_dir=tmp_path, escape_continue=True, shiny_threshold_seconds=2.8)
    )

    result = services.run_hit_script_with_shiny_check("A 100", "hit.txt", 2.8)

    assert result.is_shiny is False
    assert result.interval_seconds is None
    assert stops == []
    assert any("阶段=等待" in message and "出现了" in message for message in logs)
    assert any("按未出闪继续自动流程" in message for message in logs)


def test_main_window_auto_rng_shiny_check_crops_default_roi_and_logs_event_times(app, tmp_path, monkeypatch):
    window = MainWindow()
    logs: list[str] = []
    slices: list[object] = []

    class FakeFrame:
        shape = (100, 200, 3)

        def __getitem__(self, key):
            slices.append(key)
            return "dialog-roi"

    class FakeBackend:
        def run_script_text(self, _script_text: str, _name: str, *, script_dir: Path) -> str:
            return "ok"

    def fake_measure(capture_frame, _read_text, **kwargs):
        assert capture_frame() == "dialog-roi"
        callback = kwargs["event_callback"]
        callback(main_window_module.DialogTimingEvent("monitor_started", 100.0, 0.0))
        first = main_window_module.DialogTimingEvent("first_seen", 101.25, 1.25, keyword="出现了！")
        second = main_window_module.DialogTimingEvent("second_seen", 104.75, 4.75, 3.5, "上吧")
        callback(first)
        callback(second)
        return DialogTimingResult(101.25, 104.75, 3.5, (first, second))

    window.auto_rng_tab.captureLog.connect(logs.append)
    _install_connected_native_backend(window, monkeypatch, FakeBackend())
    monkeypatch.setattr(window, "_call_on_ui_thread", lambda callback: callback())
    monkeypatch.setattr(window, "_ocr_region_config", OcrRegionConfig)
    monkeypatch.setattr(window, "_capture_preview_frame_for_config", lambda _config: FakeFrame())
    monkeypatch.setattr(main_window_module, "measure_keyword_interval", fake_measure)

    services = window._build_auto_rng_services(AutoRngConfig(script_dir=tmp_path, shiny_threshold_seconds=3.0))
    result = services.run_hit_script_with_shiny_check("A 100", "hit.txt", 3.0)

    assert result == main_window_module.ShinyCheckResult(is_shiny=True, interval_seconds=3.5)
    assert slices == [(slice(50, 100, None), slice(0, 200, None))]
    assert any("有效 ROI" in message and "Y=50" in message and "H=50" in message for message in logs)
    assert any("识别到「出现了! / 出现了！」" in message and "监控累计 1.250s" in message for message in logs)
    assert any("识别到「上吧」" in message and "关键词间隔 3.500s" in message for message in logs)


def test_main_window_auto_rng_shiny_check_propagates_script_error_before_ocr_result(app, tmp_path, monkeypatch):
    window = MainWindow()

    class FakeBackend:
        def run_script_text(self, _script_text: str, _name: str, *, script_dir: Path) -> str:
            raise RuntimeError("EasyCon failed")

        def stop_current_script(self) -> None:
            pass

    def fake_measure(_capture_frame, _read_text, **kwargs):
        deadline = time.monotonic() + 1.0
        while not kwargs["should_stop"]() and time.monotonic() < deadline:
            time.sleep(0.001)
        raise RuntimeError("monitor stopped")

    _install_connected_native_backend(window, monkeypatch, FakeBackend())
    monkeypatch.setattr(window, "_call_on_ui_thread", lambda callback: callback())
    monkeypatch.setattr(main_window_module, "measure_keyword_interval", fake_measure)
    services = window._build_auto_rng_services(AutoRngConfig(script_dir=tmp_path, shiny_threshold_seconds=3.0))

    with pytest.raises(RuntimeError, match="EasyCon failed"):
        services.run_hit_script_with_shiny_check("A 100", "hit.txt", 3.0)


def test_main_window_auto_rng_script_rejects_when_broker_is_disconnected(app, tmp_path, monkeypatch):
    window = MainWindow()
    calls: list[str] = []

    class FakeBackend:
        def run_script_text(self, script_text: str, name: str, *, script_dir: Path) -> str:
            calls.append(name)
            return "ok"

    backend = FakeBackend()
    monkeypatch.setattr(window.easycon_tab, "_native_status", lambda: EasyConStatus.BRIDGE_CONNECTED)
    monkeypatch.setattr(window.easycon_tab, "_ensure_native_backend", lambda: backend)
    services = window._build_auto_rng_services(AutoRngConfig(script_dir=tmp_path))

    with pytest.raises(RuntimeError, match="连接视频源"):
        services.run_script_text("A 100", "hit.txt")
    assert calls == []


def test_main_window_auto_rng_run_script_syncs_easycon_status_and_output(app, tmp_path, monkeypatch):
    window = MainWindow()
    started = datetime(2026, 5, 8, 12, 0, 0)
    ended = datetime(2026, 5, 8, 12, 0, 1)

    class FakeBackend:
        def run_script_text(self, script_text: str, name: str, *, script_dir: Path) -> EasyConRunResult:
            assert script_dir == tmp_path
            return EasyConRunResult(
                status=EasyConStatus.COMPLETED,
                exit_code=0,
                started_at=started,
                ended_at=ended,
                script_path=tmp_path / name,
                port="COM1",
                stdout="done\n",
            )

    _install_connected_native_backend(window, monkeypatch, FakeBackend())
    services = window._build_auto_rng_services(AutoRngConfig(script_dir=tmp_path))

    services.run_script_text("A 100", "hit.txt")
    QApplication.processEvents()

    assert window.easycon_tab.task_state_text == "已完成"
    assert "自动流程运行脚本: hit.txt" in window.easycon_tab.log_view.toPlainText()
    assert "done" in window.easycon_tab.log_view.toPlainText()
    assert window.easycon_tab.easycon_status.currentMessage() == "已完成，连接保持"


def test_main_window_auto_rng_run_script_raises_on_easycon_failure(app, tmp_path, monkeypatch):
    window = MainWindow()
    started = datetime(2026, 5, 24, 12, 0, 0)
    ended = datetime(2026, 5, 24, 12, 0, 1)
    finished_results: list[object] = []
    failed_messages: list[str] = []
    window.autoScriptFinished.connect(finished_results.append)
    window.autoScriptFailed.connect(failed_messages.append)

    class FakeBackend:
        def run_script_text(self, script_text: str, name: str, *, script_dir: Path) -> EasyConRunResult:
            assert script_dir == tmp_path
            return EasyConRunResult(
                status=EasyConStatus.FAILED,
                exit_code=1,
                started_at=started,
                ended_at=ended,
                script_path=tmp_path / name,
                port="COM7",
                stdout="CLI 模式[right_press]: 准备\n",
                stderr="串口连接失败: COM7\n",
            )

    _install_connected_native_backend(window, monkeypatch, FakeBackend())
    services = window._build_auto_rng_services(AutoRngConfig(script_dir=tmp_path))

    with pytest.raises(RuntimeError, match="串口连接失败"):
        services.run_script_text("A 100", "BDSP测种.txt")
    QApplication.processEvents()

    assert "串口连接失败" in window.easycon_tab.log_view.toPlainText()
    assert len(finished_results) == 1
    assert failed_messages == []


def test_main_window_auto_tid_run_script_uses_native_backend_and_script_dir(app, tmp_path, monkeypatch):
    window = MainWindow()
    calls: list[tuple[str, str, Path]] = []

    class FakeBackend:
        def run_script_text(self, script_text: str, name: str, *, script_dir: Path) -> str:
            calls.append((script_text, name, script_dir))
            return "ok"

    _install_connected_native_backend(window, monkeypatch, FakeBackend())
    services = window._build_auto_tid_rng_services(AutoTidRngConfig(script_dir=tmp_path))

    assert services.run_script_text("A 100", "tid.txt") == "ok"
    assert calls == [("A 100", "tid.txt", tmp_path)]
    assert window.easycon_tab._native_run_reserved is False


def test_main_window_auto_tid_releases_native_reservation_after_base_exception(app, tmp_path, monkeypatch):
    window = MainWindow()

    class FatalScriptError(BaseException):
        pass

    class FakeBackend:
        def run_script_text(self, _script_text: str, _name: str, *, script_dir: Path) -> str:
            assert script_dir == tmp_path
            raise FatalScriptError("fatal script failure")

    _install_connected_native_backend(window, monkeypatch, FakeBackend())
    services = window._build_auto_tid_rng_services(AutoTidRngConfig(script_dir=tmp_path))

    with pytest.raises(FatalScriptError, match="fatal script failure"):
        services.run_script_text("A 100", "tid.txt")

    assert window.easycon_tab._native_run_reserved is False
    assert window.easycon_tab.task_state_text == "失败"


def test_main_window_send_easycon_right_reuses_connected_native_backend(app, monkeypatch):
    window = MainWindow()
    presses: list[tuple[str, int]] = []

    class FakeBackend:
        def press(self, button: str, duration_ms: int) -> None:
            presses.append((button, duration_ms))

    backend = FakeBackend()
    _install_connected_native_backend(window, monkeypatch, backend)

    window._send_easycon_right(log_details=False)

    assert presses == [("RIGHT", 200)]


def test_main_window_applies_selected_roi(app, monkeypatch):
    window = MainWindow()

    class FakeImage:
        shape = (10, 12)

    fake_cv2 = type("FakeCv2", (), {"IMREAD_GRAYSCALE": 0, "imread": staticmethod(lambda *_args: FakeImage())})
    monkeypatch.setitem(__import__("sys").modules, "cv2", fake_cv2)

    window.apply_selected_roi((20, 30, 40, 50))

    assert window.x.text() == "20"
    assert window.y.text() == "30"
    assert window.w.text() == "40"
    assert window.h.text() == "50"


def test_preview_selection_cancel_keeps_previous_roi(app, monkeypatch):
    window = MainWindow()
    window.x.setText("1")
    window.y.setText("2")
    window.w.setText("30")
    window.h.setText("40")
    window._selection_mode = "roi"
    window._roi_before_selection = (1, 2, 30, 40)
    window.preview_label.set_selection_enabled(True)
    monkeypatch.setattr(window, "_confirm_preview_selection", lambda _roi: False)

    window._handle_preview_selection((10, 20, 50, 60))

    assert (window.x.text(), window.y.text(), window.w.text(), window.h.text()) == ("1", "2", "30", "40")
    assert window._selection_mode is None


def test_preview_selection_cancel_keeps_previous_eye_path(app, monkeypatch, tmp_path):
    window = MainWindow()
    old_eye = tmp_path / "old_eye.png"
    window._eye_image_path = old_eye
    window._selection_mode = "eye"
    window.preview_label.set_selection_enabled(True)
    called = []
    monkeypatch.setattr(window, "_confirm_preview_selection", lambda _roi: False)
    monkeypatch.setattr(window, "apply_selected_eye", lambda _roi: called.append(_roi))

    window._handle_preview_selection((10, 20, 30, 40))

    assert window._eye_image_path == old_eye
    assert called == []
    assert window._selection_mode is None


def test_preview_label_stores_ocr_overlay_region(app):
    window = MainWindow()

    window.preview_label.set_ocr_overlay("nature", OcrRegion(10, 20, 30, 40))

    assert window.preview_label._ocr_overlay_field == "nature"
    assert window.preview_label._ocr_overlay_region == OcrRegion(10, 20, 30, 40)


def test_preview_label_paints_drag_and_ocr_overlay_with_one_painter(app, monkeypatch):
    window = MainWindow()
    label = window.preview_label
    label.resize(200, 120)
    label.set_image_geometry(200, 120, label.rect())
    label.set_selection_enabled(True)
    label._drag_start = QPoint(10, 10)
    label._drag_current = QPoint(80, 50)
    label.set_ocr_overlay("nature", OcrRegion(20, 20, 40, 30))
    active = {"count": 0}

    class FakePainter:
        def __init__(self, _device):
            if active["count"]:
                raise RuntimeError("nested painter")
            active["count"] += 1

        def setPen(self, _pen):
            pass

        def drawRect(self, _rect):
            pass

        def end(self):
            active["count"] = 0

        def __del__(self):
            active["count"] = 0

    monkeypatch.setattr(main_window_module, "QPainter", FakePainter)

    label.paintEvent(QPaintEvent(label.rect()))


def test_ocr_region_selection_confirm_emits_field_and_roi(app, monkeypatch):
    window = MainWindow()
    emitted = []
    window.ocrRegionSelected.connect(lambda field, roi: emitted.append((field, roi)))
    window._selection_mode = "ocr_region"
    window._ocr_selection_field = "characteristic"
    window.preview_label.set_selection_enabled(True)
    monkeypatch.setattr(window, "_confirm_preview_selection", lambda _roi: True)

    window._handle_preview_selection((10, 20, 30, 40))

    assert emitted == [("characteristic", (10, 20, 30, 40))]
    assert window._selection_mode is None
    assert window._ocr_selection_field is None
    assert window.preview_label._ocr_overlay_region == OcrRegion(10, 20, 30, 40)


def test_ocr_region_selection_cancel_does_not_emit(app, monkeypatch):
    window = MainWindow()
    emitted = []
    window.ocrRegionSelected.connect(lambda field, roi: emitted.append((field, roi)))
    window._selection_mode = "ocr_region"
    window._ocr_selection_field = "nature"
    window.preview_label.set_selection_enabled(True)
    monkeypatch.setattr(window, "_confirm_preview_selection", lambda _roi: False)

    window._handle_preview_selection((10, 20, 30, 40))

    assert emitted == []
    assert window._selection_mode is None
    assert window._ocr_selection_field is None
