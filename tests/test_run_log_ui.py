from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from auto_bdsp_rng.rng_core import SeedState32
from auto_bdsp_rng.run_log import RunLogManager
from auto_bdsp_rng.ui import MainWindow
import auto_bdsp_rng.ui.main_window as main_window_module


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(MainWindow, "_start_ocr_warmup", lambda self: None)
    monkeypatch.setattr(main_window_module, "should_show_startup_notice", lambda: False)
    application = QApplication.instance() or QApplication([])
    yield application
    for widget in application.topLevelWidgets():
        for timer in widget.findChildren(QTimer):
            timer.stop()
        widget.close()
        widget.deleteLater()
    application.processEvents()


def test_main_window_run_log_menu_toggles_and_collects_sources(app, tmp_path, monkeypatch):
    saved_states: list[bool] = []
    monkeypatch.setattr(
        main_window_module,
        "set_run_log_enabled",
        lambda enabled: saved_states.append(bool(enabled)) or bool(enabled),
    )
    monkeypatch.setattr(QMessageBox, "critical", lambda *args, **kwargs: None)
    manager = RunLogManager(
        tmp_path / "logs",
        now=lambda: datetime(2026, 8, 22, 15, 31, 8, 125000),
        pid=123,
    )
    window = MainWindow(run_log_manager=manager)

    action = window.help_menu_controller.run_log_save_action
    assert action.isChecked() is False
    action.trigger()
    path = manager.current_path

    assert action.isChecked() is True
    assert path is not None
    window.auto_rng_tab.add_log("定点事件")
    window.auto_tid_rng_tab.add_log("TID 事件", level="WARNING")
    window.easycon_tab._append_log("stderr", "EasyCon 错误")
    window._handle_auto_history_event("cycle_start", (3,))
    window._show_error("示例错误", RuntimeError("失败"))

    action.trigger()
    window.auto_rng_tab.add_log("关闭后不写入")
    manager.close()

    assert action.isChecked() is False
    assert saved_states == [True, False]
    text = path.read_text(encoding="utf-8")
    assert "[INFO] [自动定点] 定点事件" in text
    assert "[WARNING] [自动 TID] TID 事件" in text
    assert "[ERROR] [伊机控] EasyCon 错误" in text
    assert "[INFO] [历史记录] 自动定点第 3 轮开始" in text
    assert "[ERROR] [应用] 示例错误: 失败" in text
    assert "自动保存运行日志已关闭" in text
    assert "关闭后不写入" not in text


def test_manual_tid_seed_capture_writes_start_result_and_seed(app, tmp_path, monkeypatch):
    manager = RunLogManager(
        tmp_path / "logs",
        now=lambda: datetime(2026, 8, 22, 16, 0),
        pid=456,
    )
    path = manager.enable()
    window = MainWindow(run_log_manager=manager)
    seed = SeedState32(0xAAAAAAAA, 0xBBBBBBBB, 0xCCCCCCCC, 0xDDDDDDDD)
    monkeypatch.setattr(
        main_window_module,
        "capture_pokemon_blinks",
        lambda config, **kwargs: SimpleNamespace(intervals=[]),
    )
    monkeypatch.setattr(
        main_window_module,
        "recover_tidsid_seed_from_observation",
        lambda observation: SimpleNamespace(state=seed, observation=observation),
    )
    window._latest_preview_frame = object()

    window.capture_tidsid_seed()
    window._capture_thread.join(timeout=2)
    window._poll_capture_thread()
    manager.close()

    text = path.read_text(encoding="utf-8")
    assert "[INFO] [Seed 捕捉] 开始 TID/SID Seed 捕捉；眨眼数 64" in text
    assert "[INFO] [Seed 捕捉] TID/SID Seed 捕捉完成；Seed AAAAAAAA BBBBBBBB CCCCCCCC DDDDDDDD" in text


def test_main_window_opens_run_log_directory(app, tmp_path, monkeypatch):
    manager = RunLogManager(tmp_path / "logs")
    opened = []
    monkeypatch.setattr(
        main_window_module,
        "QDesktopServices",
        SimpleNamespace(openUrl=lambda url: opened.append(url) or True),
    )
    window = MainWindow(run_log_manager=manager)

    window.help_menu_controller.open_run_log_dir_action.trigger()

    assert manager.directory.is_dir()
    assert len(opened) == 1
    assert Path(opened[0].toLocalFile()).resolve() == manager.directory.resolve()


def test_main_window_reports_when_system_cannot_open_log_directory(app, tmp_path, monkeypatch):
    manager = RunLogManager(tmp_path / "logs")
    warnings: list[str] = []
    monkeypatch.setattr(main_window_module, "QDesktopServices", SimpleNamespace(openUrl=lambda _url: False))
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, _title, message: warnings.append(message),
    )
    window = MainWindow(run_log_manager=manager)

    window.help_menu_controller.open_run_log_dir_action.trigger()

    assert manager.directory.is_dir()
    assert any("无法打开日志目录" in message for message in warnings)


def test_run_restores_enabled_setting_and_reports_startup_write_failure(monkeypatch):
    class FakeApp:
        def exec(self) -> int:
            return 0

    class FakeManager:
        def __init__(self) -> None:
            self.enabled = False
            self.callback = None
            self.cleanup_called = False
            self.enable_called = False
            self.closed = False

        def set_error_callback(self, callback) -> None:
            self.callback = callback

        def cleanup(self) -> tuple[Path, ...]:
            self.cleanup_called = True
            return ()

        def enable(self) -> Path:
            self.enable_called = True
            self.enabled = True
            return Path("logs/run.log")

        def write(self, source, message, level="INFO") -> None:
            _ = source, message, level
            if self.enabled:
                self.enabled = False
                assert self.callback is not None
                self.callback("写入运行日志失败，已自动停用: disk full")

        def write_exception(self, *_args) -> None:
            return

        def close(self) -> None:
            self.closed = True
            self.enabled = False

    class FakeGuard:
        def __init__(self) -> None:
            self.restored = False

        def install(self):
            return self

        def restore(self) -> None:
            self.restored = True

    class FakeWindow:
        def __init__(self) -> None:
            self.shown = False
            self.startup_errors: list[str] = []

        def show(self) -> None:
            self.shown = True

        def show_run_log_startup_error(self, message: str) -> None:
            self.startup_errors.append(message)

    app = FakeApp()
    manager = FakeManager()
    guard = FakeGuard()
    window = FakeWindow()
    saved_states: list[bool] = []
    monkeypatch.setattr(main_window_module, "QApplication", SimpleNamespace(instance=lambda: app))
    monkeypatch.setattr(main_window_module, "configure_application_identity", lambda _app: None)
    monkeypatch.setattr(main_window_module, "RunLogManager", lambda: manager)
    monkeypatch.setattr(main_window_module, "ExceptionHookGuard", lambda _manager: guard)
    monkeypatch.setattr(main_window_module, "is_run_log_enabled", lambda: True)
    monkeypatch.setattr(
        main_window_module,
        "set_run_log_enabled",
        lambda enabled: saved_states.append(bool(enabled)) or bool(enabled),
    )
    monkeypatch.setattr(main_window_module, "create_window", lambda **_kwargs: window)
    monkeypatch.setattr(
        main_window_module,
        "QTimer",
        SimpleNamespace(singleShot=lambda _delay, callback: callback()),
    )

    assert main_window_module.run() == 0

    assert manager.cleanup_called is True
    assert manager.enable_called is True
    assert saved_states == [False]
    assert window.shown is True
    assert window.startup_errors == ["写入运行日志失败，已自动停用: disk full"]
    assert manager.closed is True
    assert guard.restored is True


@pytest.mark.parametrize("failure_phase", ["exit_write", "close"])
def test_run_persists_disabled_setting_when_shutdown_logging_fails(monkeypatch, failure_phase):
    class FakeApp:
        def exec(self) -> int:
            return 7

    class FakeManager:
        def __init__(self) -> None:
            self.enabled = False
            self.callback = None
            self.write_calls: list[tuple[object, object, object]] = []
            self.closed = False

        def set_error_callback(self, callback) -> None:
            self.callback = callback

        def cleanup(self) -> tuple[Path, ...]:
            return ()

        def enable(self) -> Path:
            self.enabled = True
            return Path("logs/run.log")

        def write(self, source, message, level="INFO") -> None:
            self.write_calls.append((source, message, level))
            if failure_phase == "exit_write" and len(self.write_calls) == 2:
                self.enabled = False
                assert self.callback is not None
                self.callback("写入运行日志失败，已自动停用: disk full on exit")

        def write_exception(self, *_args) -> None:
            return

        def close(self) -> None:
            self.closed = True
            self.enabled = False
            if failure_phase == "close":
                assert self.callback is not None
                self.callback("关闭运行日志文件失败: disk full on close")

    class FakeGuard:
        def __init__(self) -> None:
            self.restored = False

        def install(self):
            return self

        def restore(self) -> None:
            self.restored = True

    class FakeWindow:
        def __init__(self) -> None:
            self.shown = False

        def show(self) -> None:
            self.shown = True

    app = FakeApp()
    manager = FakeManager()
    guard = FakeGuard()
    window = FakeWindow()
    saved_states: list[bool] = []
    monkeypatch.setattr(main_window_module, "QApplication", SimpleNamespace(instance=lambda: app))
    monkeypatch.setattr(main_window_module, "configure_application_identity", lambda _app: None)
    monkeypatch.setattr(main_window_module, "RunLogManager", lambda: manager)
    monkeypatch.setattr(main_window_module, "ExceptionHookGuard", lambda _manager: guard)
    monkeypatch.setattr(main_window_module, "is_run_log_enabled", lambda: True)
    monkeypatch.setattr(
        main_window_module,
        "set_run_log_enabled",
        lambda enabled: saved_states.append(bool(enabled)) or bool(enabled),
    )
    monkeypatch.setattr(main_window_module, "create_window", lambda **_kwargs: window)

    assert main_window_module.run() == 7

    assert saved_states == [False]
    assert window.shown is True
    assert manager.closed is True
    assert guard.restored is True


@pytest.mark.parametrize(
    ("method_name", "expected_field", "args", "result", "expected_message"),
    [
        (
            "_recognize_ocr_region",
            "nature",
            ("nature", main_window_module.OcrRegion(1, 2, 3, 4)),
            "胆小",
            "性格识别结果: 胆小",
        ),
        (
            "_recognize_tid_ocr_region",
            "tid",
            (main_window_module.OcrRegion(1, 2, 3, 4),),
            "123456",
            "TID识别结果: 123456",
        ),
    ],
)
def test_manual_ocr_success_writes_info_log(
    app,
    monkeypatch,
    method_name,
    expected_field,
    args,
    result,
    expected_message,
):
    window = MainWindow()
    frame = object()
    records: list[tuple[str, str, str]] = []
    window._latest_preview_frame = frame
    monkeypatch.setattr(
        window,
        "_write_run_log",
        lambda source, message, *, level="INFO": records.append((source, str(message), level)),
    )

    def recognize(actual_frame, field, _region):
        assert actual_frame is frame
        assert field == expected_field
        return result

    monkeypatch.setattr(main_window_module, "recognize_ocr_field", recognize)

    assert getattr(window, method_name)(*args) == result
    assert records == [("OCR", expected_message, "INFO")]


@pytest.mark.parametrize(
    ("method_name", "args", "expected_message"),
    [
        (
            "_recognize_ocr_region",
            ("nature", main_window_module.OcrRegion(1, 2, 3, 4)),
            "性格识别失败: OCR boom",
        ),
        (
            "_recognize_tid_ocr_region",
            (main_window_module.OcrRegion(1, 2, 3, 4),),
            "TID识别失败: OCR boom",
        ),
    ],
)
def test_manual_ocr_failure_writes_error_and_reraises_same_exception(
    app,
    monkeypatch,
    method_name,
    args,
    expected_message,
):
    window = MainWindow()
    error = RuntimeError("OCR boom")
    records: list[tuple[str, str, str]] = []
    window._latest_preview_frame = object()
    monkeypatch.setattr(
        window,
        "_write_run_log",
        lambda source, message, *, level="INFO": records.append((source, str(message), level)),
    )

    def fail_recognition(*_args):
        raise error

    monkeypatch.setattr(main_window_module, "recognize_ocr_field", fail_recognition)

    with pytest.raises(RuntimeError) as caught:
        getattr(window, method_name)(*args)

    assert caught.value is error
    assert records == [("OCR", expected_message, "ERROR")]


def test_full_ocr_success_logs_each_result_and_completion(app, monkeypatch):
    window = MainWindow()
    window._ocr_warmup_result = (True, "OCR预热完成")
    records: list[tuple[str, str, str]] = []
    outcomes: list[tuple[bool, str]] = []
    regions = {
        "nature": main_window_module.OcrRegion(1, 2, 3, 4),
        "hp": main_window_module.OcrRegion(5, 6, 7, 8),
    }

    def run_immediately(_label, task, completed):
        try:
            payload = task(lambda: False)
        except BaseException as exc:
            completed(False, exc)
        else:
            completed(True, payload)
        return True

    monkeypatch.setattr(
        window,
        "_write_run_log",
        lambda source, message, *, level="INFO": records.append((source, str(message), level)),
    )
    monkeypatch.setattr(window, "_ocr_region_config", lambda: regions)
    monkeypatch.setattr(window, "_config_from_form", lambda: SimpleNamespace(capture=object()))
    frames = iter(("notes-frame", "stats-frame"))
    monkeypatch.setattr(window, "_capture_preview_frame_for_config", lambda _config: next(frames))
    monkeypatch.setattr(window, "_start_managed_ocr_task", run_immediately)
    monkeypatch.setattr(window, "_send_easycon_right", lambda: None)
    monkeypatch.setattr(main_window_module, "NOTE_REGION_FIELDS", ("nature",))
    monkeypatch.setattr(main_window_module, "STAT_REGION_FIELDS", ("hp",))
    monkeypatch.setattr(main_window_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        main_window_module,
        "recognize_ocr_field",
        lambda frame, field, _region: {("notes-frame", "nature"): "胆小", ("stats-frame", "hp"): "108"}[
            (frame, field)
        ],
    )
    window.ocrFullTestFinished.connect(lambda success, message: outcomes.append((success, message)))

    window._start_ocr_full_test()

    assert outcomes == [(True, "测试全部完成")]
    assert window._ocr_full_test_running is False
    assert records == [
        ("OCR", "测试全部开始", "INFO"),
        ("OCR", "测试全部/性格识别结果: 胆小", "INFO"),
        ("OCR", "测试全部/HP识别结果: 108", "INFO"),
        ("OCR", "测试全部完成", "INFO"),
    ]


def test_full_ocr_failure_logs_error_and_preserves_finished_signal_semantics(app, monkeypatch):
    window = MainWindow()
    window._ocr_warmup_result = (True, "OCR预热完成")
    records: list[tuple[str, str, str]] = []
    outcomes: list[tuple[bool, str]] = []

    def run_immediately(_label, task, completed):
        try:
            payload = task(lambda: False)
        except BaseException as exc:
            completed(False, exc)
        else:
            completed(True, payload)
        return True

    monkeypatch.setattr(
        window,
        "_write_run_log",
        lambda source, message, *, level="INFO": records.append((source, str(message), level)),
    )
    monkeypatch.setattr(
        window,
        "_ocr_region_config",
        lambda: {"nature": main_window_module.OcrRegion(1, 2, 3, 4)},
    )
    monkeypatch.setattr(window, "_config_from_form", lambda: SimpleNamespace(capture=object()))
    monkeypatch.setattr(window, "_capture_preview_frame_for_config", lambda _config: "notes-frame")
    monkeypatch.setattr(window, "_start_managed_ocr_task", run_immediately)
    monkeypatch.setattr(main_window_module, "NOTE_REGION_FIELDS", ("nature",))
    monkeypatch.setattr(main_window_module, "STAT_REGION_FIELDS", ())
    monkeypatch.setattr(
        main_window_module,
        "recognize_ocr_field",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("full OCR boom")),
    )
    window.ocrFullTestFinished.connect(lambda success, message: outcomes.append((success, message)))

    window._start_ocr_full_test()

    assert outcomes == [(False, "测试全部失败: full OCR boom")]
    assert window._ocr_full_test_running is False
    assert records == [
        ("OCR", "测试全部开始", "INFO"),
        ("OCR", "测试全部失败: full OCR boom", "ERROR"),
    ]


def test_shiny_calibration_failure_is_logged_once_as_error(app, monkeypatch):
    window = MainWindow()
    records: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        window,
        "_write_run_log",
        lambda source, message, *, level="INFO": records.append((source, str(message), level)),
    )
    monkeypatch.setattr(QMessageBox, "critical", lambda *_args, **_kwargs: None)

    window._shiny_threshold_calibration_failed("camera unavailable")

    matching = [record for record in records if "camera unavailable" in record[1]]
    assert len(matching) == 1
    assert matching[0][2] == "ERROR"
    assert "[闪光判定校准] 失败: camera unavailable" in window.auto_rng_tab.log_view.toPlainText()


def test_manual_pokemon_info_explicit_failures_are_logged_as_errors(app, monkeypatch):
    window = MainWindow()
    records: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        window,
        "_write_run_log",
        lambda source, message, *, level="INFO": records.append((source, str(message), level)),
    )

    def fail_config():
        raise RuntimeError("bad config")

    monkeypatch.setattr(window, "_config_from_form", fail_config)
    window._capture_pokemon_info()

    monkeypatch.setattr(window, "_config_from_form", lambda: SimpleNamespace(capture=object()))
    monkeypatch.setattr(
        main_window_module,
        "capture_preview_frame",
        lambda _config: (_ for _ in ()).throw(RuntimeError("notes camera")),
    )
    window._capture_pokemon_info()

    monkeypatch.setattr(main_window_module, "extract_pokemon_info", lambda **_kwargs: {})
    monkeypatch.setattr(
        window,
        "_pause_ocr_and_turn_to_stats_page",
        lambda: (_ for _ in ()).throw(RuntimeError("right command")),
    )
    window._do_capture_pokemon_info(object())

    window._capture_config = object()
    monkeypatch.setattr(
        main_window_module,
        "capture_preview_frame",
        lambda _config: (_ for _ in ()).throw(RuntimeError("stats camera")),
    )
    window._on_request_stats_capture(None, None)

    expected_messages = {
        "[捕获精灵信息] 获取截图配置失败: bad config",
        "[捕获精灵信息] 截图笔记页失败: notes camera",
        "[捕获精灵信息] 发送 RIGHT 指令失败: right command",
        "[捕获精灵信息] 截图能力页失败: stats camera",
    }
    matching = [record for record in records if record[1] in expected_messages]
    assert {record[1] for record in matching} == expected_messages
    assert len(matching) == len(expected_messages)
    assert all(level == "ERROR" for _source, _message, level in matching)
