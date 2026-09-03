from __future__ import annotations

import threading
import time
from concurrent.futures import Future
from datetime import datetime
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QProcess, Qt
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QSpinBox
from PySide6.QtGui import QKeyEvent, QTextCursor

import auto_bdsp_rng.ui.easycon_panel as panel_module
from auto_bdsp_rng.automation.easycon import (
    BridgeTransportTerminatedError,
    EasyConConfig,
    EasyConInstallation,
    EasyConStatus,
)
from auto_bdsp_rng.automation.easycon.native.device import SwitchButton, SwitchReport
from auto_bdsp_rng.ui.easycon_panel import DEFAULT_KEY_MAPPING, EasyConPanel, KeyMappingDialog


class UnsupportedKeyboardHookFactory:
    def __init__(self) -> None:
        self.support_checks = 0
        self.create_calls = 0

    def is_supported(self) -> bool:
        self.support_checks += 1
        return False

    def __call__(self, *_args, **_kwargs):
        self.create_calls += 1
        raise AssertionError("unsupported keyboard hook factory must not be called")


class FakeKeyboardHook:
    def __init__(
        self,
        key_event,
        hook_stopped,
        *,
        start_result: bool = True,
        stop_failures: int = 0,
        stop_failure_marks_stopped: bool = False,
    ) -> None:
        self.key_event = key_event
        self.hook_stopped = hook_stopped
        self.start_result = start_result
        self.stop_failures = stop_failures
        self.stop_failure_marks_stopped = stop_failure_marks_stopped
        self.started_with: tuple[int, ...] | None = None
        self.stop_calls = 0
        self.is_running = False
        self.mapped_keys_enabled = True
        self.mapped_keys_enabled_calls: list[bool] = []

    def start(self, mapped_qt_keys) -> bool:
        self.started_with = tuple(int(key) for key in mapped_qt_keys)
        self.is_running = True
        return self.start_result

    def stop(self) -> bool:
        self.stop_calls += 1
        if self.stop_failures:
            self.stop_failures -= 1
            if self.stop_failure_marks_stopped:
                self.is_running = False
            raise RuntimeError("fake keyboard hook stop failed")
        self.is_running = False
        return True

    def set_mapped_keys_enabled(self, enabled: bool) -> None:
        self.mapped_keys_enabled = bool(enabled)
        self.mapped_keys_enabled_calls.append(self.mapped_keys_enabled)

    def emit_key(self, key: int, down: bool, control_down: bool = False) -> None:
        self.key_event(int(key), bool(down), bool(control_down))

    def emit_stopped(self, error: BaseException | None = None) -> None:
        self.hook_stopped(error)


class FakeKeyboardHookFactory:
    def __init__(
        self,
        *,
        start_result: bool = True,
        stop_failures: int = 0,
        stop_failure_marks_stopped: bool = False,
    ) -> None:
        self.instances: list[FakeKeyboardHook] = []
        self.support_checks = 0
        self.start_result = start_result
        self.stop_failures = stop_failures
        self.stop_failure_marks_stopped = stop_failure_marks_stopped

    def is_supported(self) -> bool:
        self.support_checks += 1
        return True

    def __call__(self, key_event, *, hook_stopped=None) -> FakeKeyboardHook:
        hook = FakeKeyboardHook(
            key_event,
            hook_stopped,
            start_result=self.start_result,
            stop_failures=self.stop_failures,
            stop_failure_marks_stopped=self.stop_failure_marks_stopped,
        )
        self.instances.append(hook)
        return hook


class FakeNativeBackend:
    def __init__(self, ports: list[str] | None = None) -> None:
        self.ports = list(ports) if ports is not None else ["COM7"]
        self.connected_port: str | None = None
        self.report = SwitchReport()
        self.script_runs: list[tuple[str, str | None, Path | None]] = []
        self.presses: list[tuple[str, int]] = []
        self.sticks: list[tuple[str, str | int, int | None]] = []
        self.key_events: list[tuple[str, str]] = []
        self.stick_events: list[tuple[str, str, bool]] = []
        self.stopped = False
        self.closed = False

    def list_ports(self) -> list[str]:
        return list(self.ports)

    def status(self) -> EasyConStatus:
        return EasyConStatus.BRIDGE_CONNECTED if self.connected_port else EasyConStatus.BRIDGE_DISCONNECTED

    def connect(self, port: str) -> None:
        self.connected_port = port

    def disconnect(self) -> None:
        self.connected_port = None

    def close(self) -> None:
        self.closed = True
        self.connected_port = None

    def get_report(self) -> SwitchReport:
        return self.report.copy()

    def run_script_text(self, script_text, name=None, *, script_dir=None):
        source_dir = Path(script_dir) if script_dir is not None else None
        self.script_runs.append((script_text, name, source_dir))

        class Result:
            status = EasyConStatus.COMPLETED
            exit_code = 0
            started_at = datetime.now()
            ended_at = datetime.now()
            script_path = (source_dir or Path(".")) / (name or "<native-script>")
            port = "COM7"
            stdout = "native stdout"
            stderr = ""

        return Result()

    def stop_current_script(self) -> None:
        self.stopped = True

    def press(self, button, duration_ms, **_kwargs):
        self.presses.append((button, duration_ms))

    def stick(self, side, direction, duration_ms):
        self.sticks.append((side, direction, duration_ms))

    def key_down(self, button):
        self.key_events.append(("down", button))

    def key_up(self, button):
        self.key_events.append(("up", button))

    def stick_direction(self, side, direction, down):
        self.stick_events.append((side, direction, down))


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


@pytest.fixture
def easycon_panel_factory(monkeypatch, tmp_path, app):
    script_dir = tmp_path / "script"
    script_dir.mkdir()
    (script_dir / "玫瑰公园.txt").write_text(
        "_闪帧 = 填入这里  # 目标差值\n_等待时间 = 8\nA 100\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(panel_module, "SCRIPT_DIR", script_dir)
    monkeypatch.setattr(panel_module, "load_config", lambda: EasyConConfig(mock_enabled=True))
    monkeypatch.setattr(panel_module, "save_config", lambda _config: tmp_path / "config.json")
    monkeypatch.setattr(
        panel_module,
        "discover_ezcon",
        lambda _config: EasyConInstallation(path=Path("D:/EasyCon/ezcon.exe"), version="1.6.3", source="test"),
    )
    monkeypatch.setattr(panel_module, "list_ports", lambda _installation: ["COM7"])

    def create_panel(
        *,
        native_backend: object | None = None,
        keyboard_hook_factory=None,
    ) -> EasyConPanel:
        if native_backend is None:
            native_backend = FakeNativeBackend()
        if keyboard_hook_factory is None:
            keyboard_hook_factory = UnsupportedKeyboardHookFactory()
        return EasyConPanel(
            native_backend=native_backend,
            video_source_connected=lambda: True,
            keyboard_hook_factory=keyboard_hook_factory,
        )

    return create_panel


@pytest.fixture
def easycon_panel(easycon_panel_factory):
    return easycon_panel_factory()


def process_events_until(predicate, timeout_ms=1000):
    app = QApplication.instance()
    assert app is not None
    deadline = time.monotonic() + timeout_ms / 1000
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert predicate()


def test_key_mapping_dialog_uses_original_easycon_layout(app):
    dialog = KeyMappingDialog(DEFAULT_KEY_MAPPING)

    assert dialog.size().width() == 999
    assert dialog.size().height() == 830
    assert dialog._buttons["ZL"].geometry().getRect() == (280, 134, 62, 41)
    assert dialog._buttons["A"].geometry().getRect() == (758, 292, 62, 41)
    assert dialog._buttons["RSUp"].geometry().getRect() == (571, 357, 62, 41)
    assert dialog._buttons["ZL"].text() == "F"
    assert dialog._buttons["L"].text() == "G"
    assert dialog._buttons["A"].text() == "L"
    assert dialog._buttons["RSUp"].text() == "Up"


def test_key_mapping_dialog_updates_visible_key_text(app):
    dialog = KeyMappingDialog(DEFAULT_KEY_MAPPING)

    dialog._select_button("A")
    QApplication.sendEvent(
        dialog,
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_M, Qt.KeyboardModifier.NoModifier),
    )

    assert dialog.get_mapping()["A"] == Qt.Key.Key_M
    assert dialog._buttons["A"].text() == "M"


def test_key_mapping_dialog_right_click_action_clears_mapping(app):
    dialog = KeyMappingDialog(DEFAULT_KEY_MAPPING)
    button = dialog._buttons["A"]
    delete_action = dialog._delete_actions["A"]
    dialog._select_button("A")

    assert button.contextMenuPolicy() == Qt.ContextMenuPolicy.ActionsContextMenu
    assert delete_action in button.actions()
    assert delete_action.text() == "删除映射"
    assert delete_action.isEnabled()

    delete_action.trigger()

    assert dialog.get_mapping()["A"] == 0
    assert button.text() == ""
    assert button.toolTip() == "A: 未绑定"
    assert button.isChecked() is False
    assert dialog._active_name is None
    assert delete_action.isEnabled() is False


def test_key_mapping_dialog_unbound_mapping_cannot_be_deleted_again(app):
    dialog = KeyMappingDialog(DEFAULT_KEY_MAPPING)

    assert dialog.get_mapping()["Up"] == 0
    assert dialog._delete_actions["Up"].isEnabled() is False
    assert dialog._dirty is False


def test_key_mapping_dialog_close_accepts_changed_mapping(app):
    dialog = KeyMappingDialog(DEFAULT_KEY_MAPPING)

    dialog._select_button("A")
    QApplication.sendEvent(
        dialog,
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_M, Qt.KeyboardModifier.NoModifier),
    )
    dialog.close()

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.get_mapping()["A"] == Qt.Key.Key_M


def test_easycon_config_persists_key_mapping(tmp_path):
    config_path = tmp_path / "config.json"
    config = EasyConConfig(
        key_mapping={"A": int(Qt.Key.Key_M), "B": 0, "LSUp": int(Qt.Key.Key_U)},
    )

    panel_module.save_config(config, config_path)
    restored = panel_module.load_config(config_path)

    assert restored.key_mapping == {
        "A": int(Qt.Key.Key_M),
        "B": 0,
        "LSUp": int(Qt.Key.Key_U),
    }


def test_easycon_panel_restores_configured_key_mapping(monkeypatch, tmp_path, app):
    script_dir = tmp_path / "script"
    script_dir.mkdir()
    saved_configs: list[EasyConConfig] = []
    monkeypatch.setattr(panel_module, "SCRIPT_DIR", script_dir)
    monkeypatch.setattr(
        panel_module,
        "load_config",
        lambda: EasyConConfig(
            mock_enabled=True,
            key_mapping={"A": int(Qt.Key.Key_M), "B": 0},
        ),
    )
    monkeypatch.setattr(panel_module, "save_config", lambda config: saved_configs.append(config) or tmp_path / "config.json")
    monkeypatch.setattr(
        panel_module,
        "discover_ezcon",
        lambda _config: EasyConInstallation(path=Path("D:/EasyCon/ezcon.exe"), version="1.6.3", source="test"),
    )
    monkeypatch.setattr(panel_module, "list_ports", lambda _installation: ["COM7"])

    panel = EasyConPanel()

    assert panel.key_mapping["A"] == int(Qt.Key.Key_M)
    assert panel.key_mapping["B"] == 0
    assert panel.key_mapping["X"] == DEFAULT_KEY_MAPPING["X"]


def test_easycon_panel_saves_key_mapping_after_dialog_accept(monkeypatch, tmp_path, easycon_panel):
    saved_configs: list[EasyConConfig] = []

    class FakeDialog:
        def __init__(self, mapping, parent=None):
            self.mapping = dict(mapping)
            self.mapping["A"] = int(Qt.Key.Key_M)

        def exec(self):
            return QDialog.DialogCode.Accepted

        def get_mapping(self):
            return dict(self.mapping)

    monkeypatch.setattr(panel_module, "KeyMappingDialog", FakeDialog)
    monkeypatch.setattr(panel_module, "save_config", lambda config: saved_configs.append(config) or tmp_path / "config.json")

    easycon_panel.open_key_mapping()

    assert easycon_panel.key_mapping["A"] == int(Qt.Key.Key_M)
    assert saved_configs[-1].key_mapping["A"] == int(Qt.Key.Key_M)


def test_easycon_panel_saves_cleared_key_mapping_after_dialog_accept(
    monkeypatch,
    tmp_path,
    easycon_panel,
):
    saved_configs: list[EasyConConfig] = []

    class FakeDialog:
        def __init__(self, mapping, parent=None):
            self.mapping = dict(mapping)
            self.mapping["A"] = 0

        def exec(self):
            return QDialog.DialogCode.Accepted

        def get_mapping(self):
            return dict(self.mapping)

    monkeypatch.setattr(panel_module, "KeyMappingDialog", FakeDialog)
    monkeypatch.setattr(
        panel_module,
        "save_config",
        lambda config: saved_configs.append(config) or tmp_path / "config.json",
    )

    easycon_panel.open_key_mapping()

    assert easycon_panel.key_mapping["A"] == 0
    assert saved_configs[-1].key_mapping["A"] == 0


class FakeBridgeBackend:
    instances: list["FakeBridgeBackend"] = []

    def __init__(self, bridge_path=None, log_callback=None):
        self.bridge_path = bridge_path
        self.log_callback = log_callback
        self.connected_port = None
        self.script_runs: list[tuple[str, str | None]] = []
        self.presses: list[tuple[str, int]] = []
        self.sticks: list[tuple[str, str | int, int | None]] = []
        self.key_events: list[tuple[str, str]] = []
        self.stick_events: list[tuple[str, str, bool]] = []
        self.stopped = False
        self.disconnected = False
        self.closed = False
        FakeBridgeBackend.instances.append(self)

    def connect(self, port):
        self.connected_port = port

    def disconnect(self):
        self.disconnected = True
        self.connected_port = None

    def run_script_text(self, script_text, name=None):
        self.script_runs.append((script_text, name))

        class Result:
            status = EasyConStatus.COMPLETED
            exit_code = 0
            started_at = datetime.now()
            ended_at = datetime.now()
            script_path = Path(name or "<bridge-script>")
            port = "COM7"
            stdout = "bridge stdout"
            stderr = ""

        return Result()

    def status(self):
        return EasyConStatus.BRIDGE_CONNECTED if self.connected_port else EasyConStatus.BRIDGE_DISCONNECTED

    def stop_current_script(self):
        self.stopped = True

    def press(self, button, duration_ms, **_kwargs):
        self.presses.append((button, duration_ms))

    def stick(self, side, direction, duration_ms):
        self.sticks.append((side, direction, duration_ms))

    def key_down(self, button):
        self.key_events.append(("down", button))

    def key_up(self, button):
        self.key_events.append(("up", button))

    def stick_direction(self, side, direction, down):
        self.stick_events.append((side, direction, down))

    def close(self):
        self.closed = True


def test_easycon_panel_lists_builtin_scripts(easycon_panel):
    assert easycon_panel.script_list.count() == 1
    assert easycon_panel.script_list.item(0).text() == "玫瑰公园.txt"


def test_easycon_panel_exposes_only_native_product_mode(easycon_panel):
    assert easycon_panel.backend_mode.currentData() == "native"
    assert easycon_panel.backend_mode.isVisible() is False
    assert easycon_panel.ezcon_path.isVisible() is False
    assert easycon_panel.status_backend_label.text() == "后端: Python 原生"


def test_easycon_panel_native_script_requires_broker_and_preserves_source_dir(easycon_panel):
    backend = easycon_panel.native_backend
    assert isinstance(backend, FakeNativeBackend)
    easycon_panel._load_script_item(easycon_panel.script_list.item(0))
    blink_input = easycon_panel.parameter_widgets["_闪帧"]
    assert isinstance(blink_input, QLineEdit)
    blink_input.setText("123")
    easycon_panel.connect_native()

    easycon_panel._video_source_connected = lambda: False
    easycon_panel.run_script()
    assert backend.script_runs == []
    assert "请先在 Seed 捕捉页面连接视频源" in easycon_panel.log_view.toPlainText()

    easycon_panel._video_source_connected = lambda: True
    legacy_generated_dir = panel_module.SCRIPT_DIR / ".generated"
    assert legacy_generated_dir.exists() is False
    easycon_panel.run_script()
    process_events_until(lambda: easycon_panel.native_run_thread is None)

    assert len(backend.script_runs) == 1
    assert easycon_panel._native_run_reserved is False
    script_text, script_name, script_dir = backend.script_runs[0]
    assert script_name == "玫瑰公园.txt"
    assert script_dir == easycon_panel.current_script_path.parent
    assert "_闪帧 = 123" in script_text
    assert easycon_panel.task_state_label.text() == "任务: 已完成"
    assert easycon_panel.status_controller_label.text() == "单片机已连接"
    assert easycon_panel.connect_button.text() == "断开连接"
    assert easycon_panel.connect_button.isEnabled() is True
    assert legacy_generated_dir.exists() is False


def test_easycon_panel_native_controls_and_keep_awake_share_backend(easycon_panel):
    backend = easycon_panel.native_backend
    assert isinstance(backend, FakeNativeBackend)
    assert easycon_panel.connect_native()

    easycon_panel.send_controller_press("A", duration_ms=120)
    easycon_panel.send_controller_stick("left", "RESET")
    assert easycon_panel.request_capture_keep_awake(10, 40, duration_ms=100)
    process_events_until(lambda: easycon_panel._capture_keep_awake_future is None)

    assert backend.presses == [("A", 120), ("L", 100)]
    assert backend.sticks == [("left", "RESET", 100)]


def test_easycon_panel_keeps_connection_display_while_native_script_is_reserved(easycon_panel):
    assert easycon_panel.connect_native()
    easycon_panel.task_state_text = "执行中"

    assert easycon_panel.reserve_native_script_run()

    assert easycon_panel.connection_state_label.text() == "连接: 已长期连接"
    assert easycon_panel.task_state_label.text() == "任务: 执行中"
    assert easycon_panel.status_controller_label.text() == "单片机已连接"
    assert easycon_panel.backend_label.text() == "单片机: 已长期连接"
    assert easycon_panel.connect_button.text() == "断开连接"
    assert easycon_panel.connect_button.isEnabled() is False
    assert easycon_panel.port_combo.isEnabled() is False

    easycon_panel.task_state_text = "已完成"
    easycon_panel.release_native_script_run()

    assert easycon_panel.connection_state_label.text() == "连接: 已长期连接"
    assert easycon_panel.task_state_label.text() == "任务: 已完成"
    assert easycon_panel.status_controller_label.text() == "单片机已连接"
    assert easycon_panel.connect_button.text() == "断开连接"
    assert easycon_panel.connect_button.isEnabled() is True
    assert easycon_panel.port_combo.isEnabled() is False


def test_easycon_panel_persists_native_connection_failure(monkeypatch, easycon_panel):
    backend = easycon_panel.native_backend
    assert isinstance(backend, FakeNativeBackend)

    def fail_connect(_port):
        raise RuntimeError("serial unavailable")

    monkeypatch.setattr(backend, "connect", fail_connect)

    assert easycon_panel.connect_native() is False
    easycon_panel._poll_native_connection_status()

    assert easycon_panel.connection_state_label.text() == "连接: 连接失败"
    assert easycon_panel.task_state_label.text() == "任务: 连接失败"
    assert easycon_panel.status_controller_label.text() == "单片机连接失败"
    assert easycon_panel.connect_button.text() == "连接伊机控"


def test_easycon_panel_refreshes_native_state_after_disconnect_failure(monkeypatch, easycon_panel):
    backend = easycon_panel.native_backend
    assert isinstance(backend, FakeNativeBackend)
    assert easycon_panel.connect_native()

    def fail_disconnect():
        backend.connected_port = None
        raise RuntimeError("release failed")

    monkeypatch.setattr(backend, "disconnect", fail_disconnect)

    assert easycon_panel.disconnect_native() is False
    assert easycon_panel.connection_state_label.text() == "连接: 未连接"
    assert easycon_panel.task_state_label.text() == "任务: 断开失败"
    assert easycon_panel.status_controller_label.text() == "单片机未连接"
    assert easycon_panel.connect_button.text() == "连接伊机控"


def test_easycon_panel_disconnect_failure_preserves_actual_connection(monkeypatch, easycon_panel):
    backend = easycon_panel.native_backend
    assert isinstance(backend, FakeNativeBackend)
    assert easycon_panel.connect_native()

    def fail_disconnect():
        raise RuntimeError("device busy")

    monkeypatch.setattr(backend, "disconnect", fail_disconnect)

    assert easycon_panel.disconnect_native() is False
    assert easycon_panel.connection_state_label.text() == "连接: 已长期连接"
    assert easycon_panel.task_state_label.text() == "任务: 断开失败"
    assert easycon_panel.status_controller_label.text() == "单片机已连接"
    assert easycon_panel.connect_button.text() == "断开连接"


def test_easycon_panel_polls_native_physical_disconnect(easycon_panel):
    backend = easycon_panel.native_backend
    assert isinstance(backend, FakeNativeBackend)
    assert easycon_panel.connect_native()
    backend.connected_port = None

    easycon_panel._poll_native_connection_status()

    assert easycon_panel.connection_state_label.text() == "连接: 未连接"
    assert easycon_panel.status_controller_label.text() == "单片机未连接"
    assert easycon_panel.connect_button.text() == "连接伊机控"
    assert easycon_panel.port_combo.isEnabled() is True
    assert easycon_panel.easycon_status.currentMessage() == "连接已断开"


def test_easycon_panel_polls_native_disconnect_while_script_runs(easycon_panel):
    backend = easycon_panel.native_backend
    assert isinstance(backend, FakeNativeBackend)
    assert easycon_panel.connect_native()
    easycon_panel.task_state_text = "执行中"
    assert easycon_panel.reserve_native_script_run()
    backend.connected_port = None

    easycon_panel._poll_native_connection_status()

    assert easycon_panel.connection_state_label.text() == "连接: 未连接"
    assert easycon_panel.task_state_label.text() == "任务: 执行中"
    assert easycon_panel.status_controller_label.text() == "单片机未连接"
    assert easycon_panel.connect_button.text() == "连接伊机控"
    assert easycon_panel.connect_button.isEnabled() is False
    assert easycon_panel.port_combo.isEnabled() is False
    assert easycon_panel.easycon_status.currentMessage() == "连接已断开"

    easycon_panel.release_native_script_run()


def test_easycon_panel_releases_reservation_when_worker_setup_fails(easycon_panel, monkeypatch):
    backend = easycon_panel.native_backend
    assert isinstance(backend, FakeNativeBackend)
    easycon_panel._load_script_item(easycon_panel.script_list.item(0))
    blink_input = easycon_panel.parameter_widgets["_闪帧"]
    assert isinstance(blink_input, QLineEdit)
    blink_input.setText("123")
    easycon_panel.connect_native()
    monkeypatch.setattr(
        panel_module,
        "NativeScriptWorker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("worker setup failed")),
    )

    easycon_panel.run_script()

    assert easycon_panel._native_run_reserved is False
    assert easycon_panel.native_run_thread is None
    assert easycon_panel.native_run_worker is None
    assert not easycon_panel.run_timer.isActive()
    assert "worker setup failed" in easycon_panel.log_view.toPlainText()
    assert easycon_panel.task_state_label.text() == "任务: 失败"
    assert easycon_panel.status_controller_label.text() == "单片机已连接"
    assert easycon_panel.connect_button.text() == "断开连接"


def test_easycon_panel_native_mock_port_does_not_need_ezcon(monkeypatch, tmp_path, app):
    monkeypatch.setattr(panel_module, "SCRIPT_DIR", tmp_path)
    monkeypatch.setattr(panel_module, "load_config", lambda: EasyConConfig(mock_enabled=True))
    monkeypatch.setattr(panel_module, "save_config", lambda _config: tmp_path / "config.json")
    backend = FakeNativeBackend(["mock", "MOCK"])

    panel = EasyConPanel(native_backend=backend, video_source_connected=lambda: True)

    assert panel.mock_check.text() == "模拟串口（测试模式）"
    assert panel.port_combo.count() == 0
    assert panel._connection_port() == "mock"
    assert panel.connect_native()
    assert backend.connected_port == "mock"


def test_native_mock_config_does_not_override_a_visible_serial_port(monkeypatch, tmp_path, app):
    monkeypatch.setattr(panel_module, "SCRIPT_DIR", tmp_path)
    monkeypatch.setattr(panel_module, "load_config", lambda: EasyConConfig(mock_enabled=True))
    monkeypatch.setattr(panel_module, "save_config", lambda _config: tmp_path / "config.json")
    backend = FakeNativeBackend(["mock", "COM7", "MOCK"])

    panel = EasyConPanel(native_backend=backend, video_source_connected=lambda: True)

    assert [panel.port_combo.itemText(index) for index in range(panel.port_combo.count())] == ["COM7"]
    assert panel._connection_port() == "COM7"
    assert panel.connect_native()
    assert backend.connected_port == "COM7"


def test_easycon_panel_ignores_stale_bridge_config(monkeypatch, tmp_path, app):
    script_dir = tmp_path / "script"
    script_dir.mkdir()
    bundled_bridge = tmp_path / "bundle" / "bridge" / "EasyConBridge" / "EasyConBridge.exe"
    bundled_bridge.parent.mkdir(parents=True)
    bundled_bridge.write_text("", encoding="utf-8")
    stale_bridge = tmp_path / "old-release" / "bridge" / "EasyConBridge" / "EasyConBridge.exe"

    monkeypatch.setattr(panel_module, "SCRIPT_DIR", script_dir)
    monkeypatch.setattr(panel_module, "load_config", lambda: EasyConConfig(bridge_path=stale_bridge))
    monkeypatch.setattr(panel_module, "save_config", lambda _config: tmp_path / "config.json")
    monkeypatch.setattr(panel_module, "bundled_easycon_bridge_path", lambda: bundled_bridge)
    monkeypatch.setattr(
        panel_module,
        "discover_ezcon",
        lambda _config: EasyConInstallation(path=Path("D:/EasyCon/ezcon.exe"), version="1.6.3", source="test"),
    )
    monkeypatch.setattr(panel_module, "list_ports", lambda _installation: ["COM7"])

    panel = EasyConPanel()

    assert Path(panel.bridge_path.text()) == bundled_bridge


def test_key_mapping_dialog_layout_fits_fixed_window(app):
    dialog = KeyMappingDialog({})

    assert dialog.minimumSizeHint().height() <= dialog.height()


def test_easycon_panel_loads_script_and_blocks_required_parameter(easycon_panel):
    item = easycon_panel.script_list.item(0)

    easycon_panel._load_script_item(item)

    assert easycon_panel.script_name_label.text() == "玫瑰公园.txt"
    assert easycon_panel.template_mode_label.text() == "模板副本"
    assert "_闪帧 = 填入这里" in easycon_panel.editor.toPlainText()
    assert isinstance(easycon_panel.parameter_widgets["_闪帧"], QLineEdit)
    assert isinstance(easycon_panel.parameter_widgets["_等待时间"], QSpinBox)
    assert easycon_panel.parameter_defaults["_等待时间"] == "8"
    assert easycon_panel.editor.line_number_area_width() > 0
    assert easycon_panel.run_button.isEnabled() is False


def test_easycon_panel_syncs_parameters_without_touching_source_or_creating_snapshot(easycon_panel):
    easycon_panel._load_script_item(easycon_panel.script_list.item(0))
    source = easycon_panel.current_script_path
    blink_input = easycon_panel.parameter_widgets["_闪帧"]
    assert isinstance(blink_input, QLineEdit)

    blink_input.setText("123")

    assert "_闪帧 = 123  # 目标差值" in easycon_panel.editor.toPlainText()
    assert source is not None
    assert "_闪帧 = 填入这里" in source.read_text(encoding="utf-8")
    assert (panel_module.SCRIPT_DIR / ".generated").exists() is False


def test_easycon_panel_loads_external_script_without_adding_to_builtin_list(monkeypatch, tmp_path, easycon_panel):
    saved_configs: list[EasyConConfig] = []
    monkeypatch.setattr(panel_module, "save_config", lambda saved: saved_configs.append(saved) or tmp_path / "config.json")
    external = tmp_path / "外部脚本.ecs"
    external.write_text("_等待时间 = 9\nA 100\n", encoding="utf-8")

    easycon_panel.load_script(external)
    easycon_panel.load_script(external)

    assert easycon_panel.script_list.count() == 1
    assert easycon_panel.script_list.item(0).text() == "玫瑰公园.txt"
    assert easycon_panel.template_mode_label.text() == "普通脚本"
    assert saved_configs[-1].recent_scripts == (external.resolve(),)


def test_easycon_panel_loads_legacy_internal_path_from_outer_script(monkeypatch, tmp_path, easycon_panel):
    saved_configs: list[EasyConConfig] = []
    monkeypatch.setattr(
        panel_module,
        "save_config",
        lambda saved: saved_configs.append(saved) or tmp_path / "config.json",
    )
    legacy = tmp_path / "_internal" / "script" / "玫瑰公园.txt"

    easycon_panel.load_script(legacy)

    canonical = panel_module.SCRIPT_DIR / legacy.name
    assert easycon_panel.current_script_path == canonical
    assert "_闪帧 = 填入这里" in easycon_panel.editor.toPlainText()
    assert "_闪帧 = 1" not in easycon_panel.editor.toPlainText()
    assert saved_configs[-1].recent_scripts == (canonical.resolve(),)


def test_easycon_panel_preserves_existing_external_script_with_legacy_marker(
    monkeypatch,
    tmp_path,
    easycon_panel,
):
    saved_configs: list[EasyConConfig] = []
    monkeypatch.setattr(
        panel_module,
        "save_config",
        lambda saved: saved_configs.append(saved) or tmp_path / "config.json",
    )
    external = tmp_path / "workspace" / "_internal" / "script" / "custom.ecs"
    external.parent.mkdir(parents=True)
    external.write_text("_等待时间 = 9\nA 100\n", encoding="utf-8")

    easycon_panel.load_script(external)

    assert easycon_panel.current_script_path == external
    assert "_等待时间 = 9" in easycon_panel.editor.toPlainText()
    assert saved_configs[-1].recent_scripts == (external.resolve(),)


def test_easycon_panel_migrates_legacy_config_with_canonical_values_winning(
    monkeypatch, tmp_path, app
):
    script_dir = tmp_path / "script"
    script_dir.mkdir()
    canonical = script_dir / "阿尔宙斯.txt"
    canonical.write_text("_闪帧 = 60\n_等待时间 = 8\n", encoding="utf-8")
    legacy = tmp_path / "_internal" / "script" / canonical.name
    stable_key = f"script/{canonical.name}"
    config = EasyConConfig(
        mock_enabled=True,
        recent_scripts=(legacy, canonical),
        script_parameters={
            str(legacy): {"_闪帧": "1", "_旧参数": "legacy"},
            str(canonical.resolve()): {"_闪帧": "50", "_外层参数": "outer"},
            stable_key: {"_闪帧": "60", "_稳定参数": "canonical"},
        },
    )
    saved_configs: list[EasyConConfig] = []
    monkeypatch.setattr(panel_module, "SCRIPT_DIR", script_dir)
    monkeypatch.setattr(panel_module, "load_config", lambda: config)
    monkeypatch.setattr(
        panel_module,
        "save_config",
        lambda saved: saved_configs.append(saved) or tmp_path / "config.json",
    )

    panel = EasyConPanel(native_backend=FakeNativeBackend(), video_source_connected=lambda: True)

    assert panel.config.recent_scripts == (canonical,)
    assert panel.config.script_parameters == {
        stable_key: {
            "_闪帧": "60",
            "_稳定参数": "canonical",
            "_外层参数": "outer",
            "_旧参数": "legacy",
        }
    }
    assert saved_configs
    assert saved_configs[0].recent_scripts == (canonical,)
    assert saved_configs[0].script_parameters == panel.config.script_parameters


def test_easycon_panel_still_starts_when_migrated_config_cannot_be_saved(
    monkeypatch,
    tmp_path,
    app,
):
    script_dir = tmp_path / "script"
    script_dir.mkdir()
    canonical = script_dir / "阿尔宙斯.txt"
    canonical.write_text("_闪帧 = 60\n", encoding="utf-8")
    legacy = tmp_path / "_internal" / "script" / canonical.name
    config = EasyConConfig(mock_enabled=True, recent_scripts=(legacy,))
    run_log_events: list[tuple[str, str]] = []
    monkeypatch.setattr(panel_module, "SCRIPT_DIR", script_dir)
    monkeypatch.setattr(panel_module, "load_config", lambda: config)
    save_attempts: list[EasyConConfig] = []

    def fail_first_save(saved_config: EasyConConfig):
        save_attempts.append(saved_config)
        if len(save_attempts) == 1:
            raise OSError("read only")
        return tmp_path / "config.json"

    monkeypatch.setattr(panel_module, "save_config", fail_first_save)

    panel = EasyConPanel(
        run_log_sink=lambda level, message: run_log_events.append((level, message)),
        native_backend=FakeNativeBackend(),
        video_source_connected=lambda: True,
    )

    assert panel.config.recent_scripts == (canonical,)
    assert len(save_attempts) >= 2
    assert "无法保存配置：read only" in panel.log_view.toPlainText()
    assert any(level == "WARNING" and "无法保存配置" in message for level, message in run_log_events)


def test_easycon_panel_restores_and_persists_recent_script_parameters(monkeypatch, tmp_path, app):
    script_dir = tmp_path / "script"
    script_dir.mkdir()
    (script_dir / "玫瑰公园.txt").write_text(
        "_闪帧 = 填入这里  # 目标差值\n_等待时间 = 8\nA 100\n",
        encoding="utf-8",
    )
    saved_configs: list[EasyConConfig] = []
    config = EasyConConfig(
        mock_enabled=True,
        script_parameters={"script/玫瑰公园.txt": {"_闪帧": "456", "_等待时间": "9"}},
    )
    monkeypatch.setattr(panel_module, "SCRIPT_DIR", script_dir)
    monkeypatch.setattr(panel_module, "load_config", lambda: config)
    monkeypatch.setattr(panel_module, "save_config", lambda saved: saved_configs.append(saved) or tmp_path / "config.json")
    monkeypatch.setattr(
        panel_module,
        "discover_ezcon",
        lambda _config: EasyConInstallation(path=Path("D:/EasyCon/ezcon.exe"), version="1.6.3", source="test"),
    )
    monkeypatch.setattr(panel_module, "list_ports", lambda _installation: ["COM7"])
    panel = EasyConPanel()

    panel._load_script_item(panel.script_list.item(0))

    assert "_闪帧 = 456  # 目标差值" in panel.editor.toPlainText()
    assert "_等待时间 = 9" in panel.editor.toPlainText()

    blink_input = panel.parameter_widgets["_闪帧"]
    assert isinstance(blink_input, QLineEdit)
    blink_input.setText("789")

    assert saved_configs[-1].script_parameters["script/玫瑰公园.txt"]["_闪帧"] == "789"


def test_easycon_panel_restores_template_defaults_and_locates_invalid_line(easycon_panel):
    easycon_panel._load_script_item(easycon_panel.script_list.item(0))
    blink_input = easycon_panel.parameter_widgets["_闪帧"]
    wait_input = easycon_panel.parameter_widgets["_等待时间"]
    assert isinstance(blink_input, QLineEdit)
    assert isinstance(wait_input, QSpinBox)

    blink_input.setText("123")
    wait_input.setValue(12)
    easycon_panel.restore_template_defaults()

    assert "_闪帧 = 填入这里" in easycon_panel.editor.toPlainText()
    assert "_等待时间 = 8" in easycon_panel.editor.toPlainText()
    assert easycon_panel._validate_parameters_for_run(focus=True) is False
    assert easycon_panel.editor.textCursor().blockNumber() == 0
    assert "第 1 行" in easycon_panel.log_view.toPlainText()


def test_easycon_run_log_sink_maps_levels_and_cannot_break_ui(easycon_panel):
    events: list[tuple[str, str]] = []
    panel = EasyConPanel(run_log_sink=lambda level, message: events.append((level, message)))
    events.clear()

    panel._append_log("info", "普通")
    panel._append_log("warn", "警告")
    panel._append_log("error", "错误")
    panel._append_log("stdout", "标准输出")
    panel._append_log("stderr", "标准错误")

    assert events == [
        ("INFO", "普通"),
        ("WARNING", "警告"),
        ("ERROR", "错误"),
        ("INFO", "标准输出"),
        ("ERROR", "标准错误"),
    ]

    def broken_sink(_level: str, _message: str) -> None:
        raise OSError("disk full")

    panel._run_log_sink = broken_sink
    panel._append_log("info", "仍写入界面")

    assert "仍写入界面" in panel.log_view.toPlainText()


def test_easycon_nonzero_cli_exit_logs_failure_as_error(easycon_panel):
    events: list[tuple[str, str]] = []
    easycon_panel._run_log_sink = lambda level, message: events.append((level, message))

    easycon_panel._process_finished(7, None)

    assert ("ERROR", "失败，exit code: 7") in events


def select_bridge_mode(panel: EasyConPanel) -> None:
    index = panel.backend_mode.findData("bridge")
    assert index >= 0
    panel.backend_mode.setCurrentIndex(index)
    if panel.port_combo.findText("COM7") >= 0:
        panel.port_combo.setCurrentText("COM7")


def test_easycon_panel_bridge_mode_requires_connection(easycon_panel):
    select_bridge_mode(easycon_panel)
    easycon_panel._load_script_item(easycon_panel.script_list.item(0))
    blink_input = easycon_panel.parameter_widgets["_闪帧"]
    assert isinstance(blink_input, QLineEdit)
    blink_input.setText("123")

    assert easycon_panel.backend_mode.currentData() == "bridge"
    assert easycon_panel.run_button.isEnabled() is False
    assert easycon_panel.backend_label.text() == "单片机: 未连接"


def test_easycon_panel_keeps_connection_display_while_bridge_script_runs(easycon_panel):
    select_bridge_mode(easycon_panel)
    easycon_panel.bridge_status = EasyConStatus.RUNNING
    easycon_panel.task_state_text = "执行中"

    easycon_panel._update_bridge_controls()

    assert easycon_panel.connection_state_label.text() == "连接: 已长期连接"
    assert easycon_panel.task_state_label.text() == "任务: 执行中"
    assert easycon_panel.status_controller_label.text() == "单片机已连接"
    assert easycon_panel.backend_label.text() == "单片机: 已长期连接"
    assert easycon_panel.connect_button.text() == "断开连接"
    assert easycon_panel.connect_button.isEnabled() is False


def test_easycon_panel_cli_mode_is_not_reported_as_connected(easycon_panel):
    easycon_panel.backend_mode.setCurrentIndex(1)
    easycon_panel.detect_easycon()

    assert easycon_panel.backend_label.text() == "单片机: CLI 过渡后端可用（未长期连接）"
    assert easycon_panel._connection_state_text() == "CLI 可用（未长期连接）"
    assert "CLI 过渡" in easycon_panel.status_backend_label.text()
    assert easycon_panel.cli_test_button.isEnabled() is True


def test_easycon_panel_auto_selects_last_port(monkeypatch, tmp_path, app):
    script_dir = tmp_path / "script"
    script_dir.mkdir()
    monkeypatch.setattr(panel_module, "SCRIPT_DIR", script_dir)
    monkeypatch.setattr(panel_module, "load_config", lambda: EasyConConfig(last_port="COM9", mock_enabled=False))
    monkeypatch.setattr(panel_module, "save_config", lambda _config: tmp_path / "config.json")
    monkeypatch.setattr(
        panel_module,
        "discover_ezcon",
        lambda _config: EasyConInstallation(path=Path("D:/EasyCon/ezcon.exe"), version="1.6.3", source="test"),
    )
    monkeypatch.setattr(panel_module, "list_ports", lambda _installation: ["COM7", "COM9"])

    panel = EasyConPanel(native_backend=FakeNativeBackend(["COM7", "COM9"]))
    panel.port_combo.blockSignals(True)
    panel.port_combo.setCurrentText("COM7")
    panel.port_combo.blockSignals(False)

    panel.auto_select_port()

    assert panel.port_combo.currentText() == "COM9"
    assert "已自动选择串口: COM9" in panel.log_view.toPlainText()


def test_easycon_panel_stops_running_cli_process(monkeypatch, tmp_path, easycon_panel):
    ezcon = tmp_path / "slow_ezcon.cmd"
    ezcon.write_text(
        "\n".join(
            [
                "@echo off",
                "if \"%1\"==\"--version\" (echo fake-ezcon-1.0& exit /b 0)",
                "if \"%1\"==\"run\" (ping -n 6 127.0.0.1 >nul& echo done& exit /b 0)",
                "exit /b 0",
            ]
        ),
        encoding="utf-8",
        newline="\r\n",
    )
    monkeypatch.setattr(
        panel_module,
        "discover_ezcon",
        lambda _config: EasyConInstallation(path=ezcon, version="fake", source="test"),
    )
    easycon_panel.backend_mode.setCurrentIndex(1)
    easycon_panel.editor.setPlainText("WAIT 5000\n")

    easycon_panel.run_script()
    assert easycon_panel.process is not None
    assert easycon_panel.process.waitForStarted(1000)
    temporary_script = Path(easycon_panel.process.arguments()[1])
    assert temporary_script.suffix == ".txt"
    assert temporary_script.exists()

    easycon_panel.toggle_run()
    assert easycon_panel.process.waitForFinished(2000)
    app = QApplication.instance()
    assert app is not None
    app.processEvents()

    assert "已中止" in easycon_panel.log_view.toPlainText()
    assert temporary_script.exists() is False


def test_easycon_panel_runs_script_text_through_bridge(monkeypatch, tmp_path, easycon_panel):
    FakeBridgeBackend.instances.clear()
    monkeypatch.setattr(panel_module, "BridgeEasyConBackend", FakeBridgeBackend)
    select_bridge_mode(easycon_panel)
    bridge = tmp_path / "EasyConBridge.exe"
    bridge.write_text("", encoding="utf-8")
    easycon_panel.bridge_path.setText(str(bridge))
    easycon_panel._load_script_item(easycon_panel.script_list.item(0))
    blink_input = easycon_panel.parameter_widgets["_闪帧"]
    assert isinstance(blink_input, QLineEdit)
    blink_input.setText("123")

    easycon_panel.connect_bridge()
    easycon_panel.run_script()
    process_events_until(lambda: easycon_panel.bridge_run_thread is None)

    backend = FakeBridgeBackend.instances[-1]
    assert easycon_panel.bridge_status == EasyConStatus.BRIDGE_CONNECTED
    assert easycon_panel.backend_label.text() == "单片机: 已长期连接"
    assert backend.connected_port == "COM7"
    assert len(backend.script_runs) == 1
    assert backend.script_runs[0][1] == "玫瑰公园.txt"
    assert "_闪帧 = 123" in backend.script_runs[0][0]
    assert "bridge stdout" in easycon_panel.log_view.toPlainText()
    assert easycon_panel.connection_state_label.text() == "连接: 已长期连接"
    assert easycon_panel.task_state_label.text() == "任务: 已完成"


def test_easycon_panel_disconnect_is_explicit(monkeypatch, tmp_path, easycon_panel):
    FakeBridgeBackend.instances.clear()
    monkeypatch.setattr(panel_module, "BridgeEasyConBackend", FakeBridgeBackend)
    select_bridge_mode(easycon_panel)
    bridge = tmp_path / "EasyConBridge.exe"
    bridge.write_text("", encoding="utf-8")
    easycon_panel.bridge_path.setText(str(bridge))

    easycon_panel.connect_bridge()
    easycon_panel.disconnect_bridge()

    backend = FakeBridgeBackend.instances[-1]
    assert backend.disconnected is True
    assert easycon_panel.bridge_status == EasyConStatus.BRIDGE_DISCONNECTED


def test_easycon_panel_sends_controller_tests_through_bridge(monkeypatch, tmp_path, easycon_panel):
    FakeBridgeBackend.instances.clear()
    monkeypatch.setattr(panel_module, "BridgeEasyConBackend", FakeBridgeBackend)
    select_bridge_mode(easycon_panel)
    bridge = tmp_path / "EasyConBridge.exe"
    bridge.write_text("", encoding="utf-8")
    easycon_panel.bridge_path.setText(str(bridge))

    easycon_panel.connect_bridge()
    easycon_panel.send_controller_press("A")
    easycon_panel.send_controller_press("ZR", duration_ms=120, log_label="捕捉亮屏保活")
    easycon_panel.send_controller_stick("left", "RESET")

    backend = FakeBridgeBackend.instances[-1]
    assert backend.presses == [("A", 100), ("ZR", 120)]
    assert backend.sticks == [("left", "RESET", 100)]
    assert easycon_panel.task_state_label.text() == "任务: 已完成"


def test_capture_keep_awake_cli_uses_cached_installation_without_blocking_discovery(monkeypatch, easycon_panel):
    discovery_called = threading.Event()
    starts: list[tuple[str, int]] = []

    def delayed_discovery() -> None:
        discovery_called.set()
        time.sleep(0.5)

    def capture_start(log_label: str, duration_ms: int) -> bool:
        starts.append((log_label, duration_ms))
        return True

    monkeypatch.setattr(easycon_panel, "detect_easycon", delayed_discovery)
    monkeypatch.setattr(easycon_panel, "_start_capture_keep_awake_cli", capture_start)
    easycon_panel.backend_mode.setCurrentIndex(easycon_panel.backend_mode.findData("cli"))

    started_at = time.monotonic()
    accepted = easycon_panel.request_capture_keep_awake(10, 40, duration_ms=100)
    elapsed = time.monotonic() - started_at

    assert accepted is True
    assert elapsed < 0.1
    assert not discovery_called.is_set()
    assert starts == [("捕捉亮屏保活 10/40", 100)]


def test_stale_keep_awake_completion_does_not_override_current_or_running_task(easycon_panel):
    current_future: Future[None] = Future()
    easycon_panel._capture_keep_awake_task_id = 2
    easycon_panel._capture_keep_awake_future = current_future
    easycon_panel.bridge_status = EasyConStatus.RUNNING
    easycon_panel.task_state_text = "执行中"
    original_log = easycon_panel.log_view.toPlainText()

    easycon_panel._handle_capture_keep_awake_finished(1, "捕捉亮屏保活 10/40", "L", 100, "", False)

    assert easycon_panel._capture_keep_awake_future is current_future
    assert easycon_panel.task_state_text == "执行中"
    assert easycon_panel.log_view.toPlainText() == original_log

    easycon_panel._handle_capture_keep_awake_finished(2, "捕捉亮屏保活 20/40", "L", 100, "", False)

    assert easycon_panel._capture_keep_awake_future is None
    assert easycon_panel.task_state_text == "执行中"
    assert "捕捉亮屏保活 20/40: L 100ms" in easycon_panel.log_view.toPlainText()


def test_keep_awake_terminal_bridge_failure_clears_backend_and_allows_reconnect(
    monkeypatch,
    tmp_path,
    easycon_panel,
):
    instances = []

    class ReconnectableBridgeBackend(FakeBridgeBackend):
        def __init__(self, bridge_path=None, log_callback=None):
            super().__init__(bridge_path=bridge_path, log_callback=log_callback)
            self.fail_keep_awake = False
            instances.append(self)

        def press(
            self,
            button,
            duration_ms,
            *,
            timeout_seconds=None,
            terminate_on_timeout=False,
        ):
            if self.fail_keep_awake:
                raise BridgeTransportTerminatedError("Bridge request timed out after 2s: press")
            super().press(button, duration_ms)

    monkeypatch.setattr(panel_module, "BridgeEasyConBackend", ReconnectableBridgeBackend)
    monkeypatch.setattr(easycon_panel, "_show_connection_toast", lambda _port: None)
    bridge = tmp_path / "EasyConBridge.exe"
    bridge.write_text("", encoding="utf-8")
    select_bridge_mode(easycon_panel)
    easycon_panel.bridge_path.setText(str(bridge))
    easycon_panel.connect_bridge()
    first_backend = instances[-1]
    first_backend.fail_keep_awake = True

    assert easycon_panel.request_capture_keep_awake(10, 40, duration_ms=100) is True
    process_events_until(lambda: easycon_panel._capture_keep_awake_future is None)

    assert first_backend.closed is True
    assert easycon_panel.bridge_backend is None
    assert easycon_panel.bridge_status == EasyConStatus.FAILED
    assert easycon_panel.connection_state_label.text() == "连接: 连接失败"
    assert easycon_panel.connect_button.text() == "连接伊机控"

    easycon_panel.connect_bridge()

    assert len(instances) == 2
    assert easycon_panel.bridge_backend is instances[-1]
    assert easycon_panel.bridge_status == EasyConStatus.BRIDGE_CONNECTED
    assert easycon_panel.connection_state_label.text() == "连接: 已长期连接"


def test_capture_keep_awake_cli_timeout_preserves_normal_task_state(monkeypatch, tmp_path, easycon_panel):
    ezcon = tmp_path / "slow_keep_awake.cmd"
    ezcon.write_text(
        "\n".join(
            [
                "@echo off",
                'if "%1"=="run" (ping -n 6 127.0.0.1 >nul& exit /b 0)',
                "exit /b 0",
            ]
        ),
        encoding="utf-8",
        newline="\r\n",
    )
    easycon_panel.backend_mode.setCurrentIndex(easycon_panel.backend_mode.findData("cli"))
    easycon_panel.installation = EasyConInstallation(path=ezcon, version="test", source="test")
    easycon_panel.task_state_text = "执行中"
    easycon_panel.run_seconds = 37
    easycon_panel.current_run_stdout = ["normal stdout"]
    easycon_panel.current_run_stderr = ["normal stderr"]
    normal_started_at = datetime.now()
    normal_script_path = tmp_path / "normal.ecs"
    easycon_panel.current_run_started_at = normal_started_at
    easycon_panel.current_run_script_path = normal_script_path
    easycon_panel.current_run_port = "mock"
    easycon_panel.run_timer.start()
    toast_calls: list[str] = []
    monkeypatch.setattr(easycon_panel, "_show_failure_toast", toast_calls.append)

    assert easycon_panel.request_capture_keep_awake(10, 40, duration_ms=100) is True
    keep_awake_process = easycon_panel._capture_keep_awake_cli_process
    assert keep_awake_process is not None
    assert keep_awake_process.objectName() == "capture_keep_awake_cli"
    temporary_script_path = Path(keep_awake_process.arguments()[1])
    assert temporary_script_path.name.startswith("auto-bdsp-rng-easycon-")
    assert temporary_script_path.suffix == ".txt"
    assert temporary_script_path.read_text(encoding="utf-8") == "L 100\n"
    assert keep_awake_process.waitForStarted(1000)

    easycon_panel._capture_keep_awake_cli_timeout()
    process_events_until(lambda: easycon_panel._capture_keep_awake_cli_process is None, timeout_ms=2000)

    assert easycon_panel.process is None
    assert easycon_panel.task_state_text == "执行中"
    assert easycon_panel.run_timer.isActive() is True
    assert easycon_panel.run_seconds == 37
    assert easycon_panel.current_run_stdout == ["normal stdout"]
    assert easycon_panel.current_run_stderr == ["normal stderr"]
    assert easycon_panel.current_run_started_at is normal_started_at
    assert easycon_panel.current_run_script_path == normal_script_path
    assert easycon_panel.current_run_port == "mock"
    assert toast_calls == []
    assert "捕捉亮屏保活 CLI 超时" in easycon_panel.log_view.toPlainText()
    assert temporary_script_path.exists() is False
    easycon_panel.run_timer.stop()


def test_capture_keep_awake_cli_failure_only_logs_keep_awake_error(monkeypatch, tmp_path, easycon_panel):
    ezcon = tmp_path / "failed_keep_awake.cmd"
    ezcon.write_text(
        "\n".join(
            [
                "@echo off",
                'if "%1"=="run" (exit /b 7)',
                "exit /b 0",
            ]
        ),
        encoding="utf-8",
        newline="\r\n",
    )
    easycon_panel.backend_mode.setCurrentIndex(easycon_panel.backend_mode.findData("cli"))
    easycon_panel.installation = EasyConInstallation(path=ezcon, version="test", source="test")
    easycon_panel.task_state_text = "待命"
    toast_calls: list[str] = []
    monkeypatch.setattr(easycon_panel, "_show_failure_toast", toast_calls.append)

    assert easycon_panel.request_capture_keep_awake(10, 20, duration_ms=100) is True
    process_events_until(lambda: easycon_panel._capture_keep_awake_cli_process is None, timeout_ms=2000)

    assert easycon_panel.task_state_text == "待命"
    assert toast_calls == []
    assert "捕捉亮屏保活 CLI 失败，继续捕捉: exit code 7" in easycon_panel.log_view.toPlainText()


def test_normal_cli_script_stops_residual_keep_awake_before_start(monkeypatch, tmp_path, easycon_panel):
    ezcon = tmp_path / "slow_cli.cmd"
    ezcon.write_text(
        "\n".join(
            [
                "@echo off",
                'if "%1"=="run" (ping -n 6 127.0.0.1 >nul& exit /b 0)',
                "exit /b 0",
            ]
        ),
        encoding="utf-8",
        newline="\r\n",
    )
    easycon_panel.backend_mode.setCurrentIndex(easycon_panel.backend_mode.findData("cli"))
    easycon_panel.installation = EasyConInstallation(path=ezcon, version="test", source="test")
    easycon_panel.editor.setPlainText("WAIT 5000\n")
    monkeypatch.setattr(easycon_panel, "detect_easycon", lambda: None)

    assert easycon_panel.request_capture_keep_awake(10, 40, duration_ms=100) is True
    keep_awake_process = easycon_panel._capture_keep_awake_cli_process
    assert keep_awake_process is not None
    assert keep_awake_process.waitForStarted(1000)

    easycon_panel.run_script()

    assert easycon_panel._capture_keep_awake_cli_process is None
    assert easycon_panel.process is not None
    assert easycon_panel.process is not keep_awake_process
    assert easycon_panel.process.waitForStarted(1000)
    easycon_panel.toggle_run()
    assert easycon_panel.process.waitForFinished(2000)


def test_easycon_panel_virtual_controller_uses_supported_keyboard_hook(easycon_panel_factory):
    backend = FakeNativeBackend()
    hook_factory = FakeKeyboardHookFactory()
    panel = easycon_panel_factory(
        native_backend=backend,
        keyboard_hook_factory=hook_factory,
    )
    assert panel.connect_native()

    assert panel._activate_virtual_controller()

    assert hook_factory.support_checks == 1
    assert len(hook_factory.instances) == 1
    hook = hook_factory.instances[0]
    assert hook.is_running
    assert hook.started_with == tuple(int(key) for key in panel.key_mapping.values())
    assert panel._vpad_input_source == "hook"
    assert panel.keyboard_controller_check.isChecked()

    hook.emit_key(Qt.Key.Key_L, True)
    hook.emit_key(Qt.Key.Key_L, False)
    process_events_until(lambda: len(backend.key_events) == 2)

    assert backend.key_events == [("down", "A"), ("up", "A")]
    assert panel.shutdown()
    assert hook.stop_calls == 1


def test_easycon_panel_ignores_failed_hook_events_after_qt_fallback(
    easycon_panel_factory,
    app,
):
    backend = FakeNativeBackend()
    hook_factory = FakeKeyboardHookFactory(start_result=False)
    panel = easycon_panel_factory(
        native_backend=backend,
        keyboard_hook_factory=hook_factory,
    )
    assert panel.connect_native()

    assert panel._activate_virtual_controller()

    hook = hook_factory.instances[0]
    fallback_generation = panel._vpad_generation
    assert hook.stop_calls == 1
    assert not hook.is_running
    assert panel._keyboard_hook is None
    assert panel._vpad_input_source == "qt"

    hook.emit_key(Qt.Key.Key_L, True)
    hook.emit_stopped(RuntimeError("failed hook stopped late"))
    app.processEvents()

    assert panel._vpad_generation == fallback_generation
    assert panel.virtual_controller_enabled
    assert panel._vpad_input_source == "qt"
    assert backend.key_events == []
    assert "意外停止" not in panel.log_view.toPlainText()
    assert panel.shutdown()


def test_easycon_panel_virtual_controller_falls_back_to_qt_events(easycon_panel_factory):
    backend = FakeNativeBackend()
    hook_factory = UnsupportedKeyboardHookFactory()
    panel = easycon_panel_factory(
        native_backend=backend,
        keyboard_hook_factory=hook_factory,
    )
    assert panel.connect_native()

    assert panel._activate_virtual_controller()

    assert hook_factory.support_checks == 1
    assert hook_factory.create_calls == 0
    assert panel._vpad_input_source == "qt"
    QApplication.sendEvent(
        panel,
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_L, Qt.KeyboardModifier.NoModifier),
    )
    QApplication.sendEvent(
        panel,
        QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_L, Qt.KeyboardModifier.NoModifier),
    )

    assert backend.key_events == [("down", "A"), ("up", "A")]
    assert panel.shutdown()


def test_easycon_panel_qt_fallback_only_swallows_mapped_auto_repeat_keys(
    easycon_panel_factory,
):
    backend = FakeNativeBackend()
    panel = easycon_panel_factory(native_backend=backend)
    assert panel.connect_native()
    assert panel._activate_virtual_controller()
    assert panel._vpad_input_source == "qt"

    for event_type in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
        unmapped = QKeyEvent(
            event_type,
            Qt.Key.Key_V,
            Qt.KeyboardModifier.NoModifier,
            "",
            True,
            1,
        )
        mapped = QKeyEvent(
            event_type,
            Qt.Key.Key_L,
            Qt.KeyboardModifier.NoModifier,
            "",
            True,
            1,
        )

        assert panel.eventFilter(panel, unmapped) is False
        assert panel.eventFilter(panel, mapped) is True

    assert backend.key_events == []
    assert panel.shutdown()


def test_easycon_panel_discards_queued_events_from_stopped_hook(easycon_panel_factory, app):
    backend = FakeNativeBackend()
    hook_factory = FakeKeyboardHookFactory()
    panel = easycon_panel_factory(
        native_backend=backend,
        keyboard_hook_factory=hook_factory,
    )
    assert panel.connect_native()
    assert panel._activate_virtual_controller()
    hook = hook_factory.instances[0]
    active_generation = panel._vpad_generation

    hook.emit_key(Qt.Key.Key_L, True)
    hook.emit_stopped(RuntimeError("late hook exit"))
    assert panel._deactivate_virtual_controller(log=False)
    app.processEvents()

    assert panel._vpad_generation > active_generation
    assert backend.key_events == []
    assert panel.virtual_controller_keys == {}
    assert "意外停止" not in panel.log_view.toPlainText()
    assert panel.shutdown()


def test_easycon_panel_native_reservation_disables_hook_without_restoring(
    easycon_panel_factory,
):
    backend = FakeNativeBackend()
    hook_factory = FakeKeyboardHookFactory()
    panel = easycon_panel_factory(
        native_backend=backend,
        keyboard_hook_factory=hook_factory,
    )
    assert panel.connect_native()
    assert panel._activate_virtual_controller()
    hook = hook_factory.instances[0]
    hook.emit_key(Qt.Key.Key_L, True)
    process_events_until(lambda: backend.key_events == [("down", "A")])

    assert panel.reserve_native_script_run()

    assert hook.stop_calls == 1
    assert not hook.is_running
    assert backend.key_events == [("down", "A"), ("up", "A")]
    assert panel.virtual_controller_enabled is False
    assert panel.keyboard_controller_check.isChecked() is False
    assert panel._vpad_input_source is None

    panel.release_native_script_run()

    assert panel.virtual_controller_enabled is False
    assert panel.keyboard_controller_check.isChecked() is False
    assert len(hook_factory.instances) == 1
    assert panel.shutdown()


def test_easycon_panel_native_reservation_retries_retained_stopped_hook(
    easycon_panel_factory,
):
    backend = FakeNativeBackend()
    hook_factory = FakeKeyboardHookFactory(
        stop_failures=1,
        stop_failure_marks_stopped=True,
    )
    panel = easycon_panel_factory(
        native_backend=backend,
        keyboard_hook_factory=hook_factory,
    )
    assert panel.connect_native()
    assert panel._activate_virtual_controller()
    hook = hook_factory.instances[0]

    assert panel.reserve_native_script_run() is False

    assert hook.stop_calls == 1
    assert hook.is_running is False
    assert panel._keyboard_hook is hook
    assert panel._native_run_reserved is False
    assert panel.native_run_thread is None
    assert backend.script_runs == []
    assert "已取消脚本启动" in panel.log_view.toPlainText()

    assert panel.reserve_native_script_run() is True

    assert hook.stop_calls == 2
    assert panel._keyboard_hook is None
    assert panel._native_run_reserved is True
    panel.release_native_script_run()
    assert panel.shutdown()


def test_easycon_panel_bridge_script_disables_hook_without_restoring(
    monkeypatch,
    tmp_path,
    easycon_panel_factory,
):
    FakeBridgeBackend.instances.clear()
    monkeypatch.setattr(panel_module, "BridgeEasyConBackend", FakeBridgeBackend)
    hook_factory = FakeKeyboardHookFactory()
    panel = easycon_panel_factory(keyboard_hook_factory=hook_factory)
    select_bridge_mode(panel)
    bridge = tmp_path / "EasyConBridge.exe"
    bridge.write_text("", encoding="utf-8")
    panel.bridge_path.setText(str(bridge))
    panel.editor.setPlainText("WAIT 1\n")
    panel.connect_bridge()
    assert panel._activate_virtual_controller()
    hook = hook_factory.instances[0]

    panel.run_script_via_bridge()

    assert hook.stop_calls == 1
    assert panel.virtual_controller_enabled is False
    assert panel.keyboard_controller_check.isChecked() is False
    process_events_until(lambda: panel.bridge_run_thread is None)
    assert panel.bridge_status == EasyConStatus.BRIDGE_CONNECTED
    assert panel.virtual_controller_enabled is False
    assert len(hook_factory.instances) == 1
    assert panel.shutdown()


def test_easycon_panel_bridge_script_retries_retained_stopped_hook(
    monkeypatch,
    tmp_path,
    easycon_panel_factory,
):
    FakeBridgeBackend.instances.clear()
    monkeypatch.setattr(panel_module, "BridgeEasyConBackend", FakeBridgeBackend)
    hook_factory = FakeKeyboardHookFactory(
        stop_failures=1,
        stop_failure_marks_stopped=True,
    )
    panel = easycon_panel_factory(keyboard_hook_factory=hook_factory)
    select_bridge_mode(panel)
    bridge = tmp_path / "EasyConBridge.exe"
    bridge.write_text("", encoding="utf-8")
    panel.bridge_path.setText(str(bridge))
    panel.editor.setPlainText("WAIT 1\n")
    panel.connect_bridge()
    backend = FakeBridgeBackend.instances[-1]
    assert panel._activate_virtual_controller()
    hook = hook_factory.instances[0]

    panel.run_script_via_bridge()

    assert hook.stop_calls == 1
    assert hook.is_running is False
    assert panel._keyboard_hook is hook
    assert panel.bridge_run_thread is None
    assert panel.bridge_status == EasyConStatus.BRIDGE_CONNECTED
    assert backend.script_runs == []

    panel.run_script_via_bridge()
    process_events_until(lambda: panel.bridge_run_thread is None)

    assert hook.stop_calls == 2
    assert panel._keyboard_hook is None
    assert len(backend.script_runs) == 1
    assert panel.bridge_status == EasyConStatus.BRIDGE_CONNECTED
    assert panel.shutdown()


def test_easycon_panel_bridge_failure_retries_retained_stopped_hook(
    monkeypatch,
    tmp_path,
    easycon_panel_factory,
):
    FakeBridgeBackend.instances.clear()
    monkeypatch.setattr(panel_module, "BridgeEasyConBackend", FakeBridgeBackend)
    hook_factory = FakeKeyboardHookFactory(
        stop_failures=1,
        stop_failure_marks_stopped=True,
    )
    panel = easycon_panel_factory(keyboard_hook_factory=hook_factory)
    select_bridge_mode(panel)
    bridge = tmp_path / "EasyConBridge.exe"
    bridge.write_text("", encoding="utf-8")
    panel.bridge_path.setText(str(bridge))
    panel.connect_bridge()
    backend = FakeBridgeBackend.instances[-1]
    assert panel._activate_virtual_controller()
    hook = hook_factory.instances[0]

    def fail_key_down(_button):
        raise RuntimeError("bridge transport closed")

    monkeypatch.setattr(backend, "key_down", fail_key_down)
    hook.emit_key(Qt.Key.Key_L, True)
    process_events_until(lambda: panel.bridge_status == EasyConStatus.FAILED)

    assert hook.stop_calls == 1
    assert hook.is_running is False
    assert panel.virtual_controller_enabled is False
    assert panel._keyboard_hook is hook

    panel._poll_native_connection_status()

    assert hook.stop_calls == 2
    assert panel._keyboard_hook is None
    assert panel.bridge_status == EasyConStatus.FAILED
    assert panel.shutdown()


def test_easycon_panel_poll_keeps_hook_standby_until_physical_disconnect(
    easycon_panel_factory,
    app,
):
    backend = FakeNativeBackend()
    hook_factory = FakeKeyboardHookFactory()
    panel = easycon_panel_factory(
        native_backend=backend,
        keyboard_hook_factory=hook_factory,
    )
    assert panel.connect_native()
    assert panel._activate_virtual_controller()
    assert panel._set_virtual_controller_standby()
    hook = hook_factory.instances[0]
    overlay = panel._controller_overlay
    assert overlay is not None
    app.processEvents()

    panel._poll_native_connection_status()

    assert panel._keyboard_hook is hook
    assert panel._vpad_input_source == "hook"
    assert hook.stop_calls == 0
    assert overlay.isVisible()

    backend.connected_port = None
    panel._poll_native_connection_status()

    assert panel._keyboard_hook is None
    assert panel._vpad_input_source is None
    assert hook.stop_calls == 1
    assert not overlay.isVisible()
    assert panel.shutdown()


def test_easycon_panel_script_reservation_fully_stops_hook_from_standby(
    easycon_panel_factory,
    app,
):
    hook_factory = FakeKeyboardHookFactory()
    panel = easycon_panel_factory(keyboard_hook_factory=hook_factory)
    assert panel.connect_native()
    assert panel._activate_virtual_controller()
    assert panel._set_virtual_controller_standby()
    hook = hook_factory.instances[0]
    overlay = panel._controller_overlay
    assert overlay is not None
    app.processEvents()

    assert panel.reserve_native_script_run()

    assert panel._keyboard_hook is None
    assert panel._vpad_input_source is None
    assert hook.stop_calls == 1
    assert overlay.isVisible()
    panel.release_native_script_run()
    assert panel.shutdown()


def test_easycon_panel_physical_disconnect_stops_hook_and_hides_overlay(
    easycon_panel_factory,
    app,
):
    backend = FakeNativeBackend()
    hook_factory = FakeKeyboardHookFactory()
    panel = easycon_panel_factory(
        native_backend=backend,
        keyboard_hook_factory=hook_factory,
    )
    assert panel.connect_native()
    assert panel._activate_virtual_controller()
    hook = hook_factory.instances[0]
    overlay = panel._controller_overlay
    assert overlay is not None
    app.processEvents()
    assert overlay.isVisible()

    backend.connected_port = None
    panel._poll_native_connection_status()

    assert hook.stop_calls == 1
    assert panel.virtual_controller_enabled is False
    assert panel.keyboard_controller_check.isChecked() is False
    assert not overlay.isVisible()
    assert panel.shutdown()


def test_easycon_panel_physical_disconnect_retries_retained_stopped_hook(
    easycon_panel_factory,
    app,
):
    backend = FakeNativeBackend()
    hook_factory = FakeKeyboardHookFactory(
        stop_failures=1,
        stop_failure_marks_stopped=True,
    )
    panel = easycon_panel_factory(
        native_backend=backend,
        keyboard_hook_factory=hook_factory,
    )
    assert panel.connect_native()
    assert panel._activate_virtual_controller()
    hook = hook_factory.instances[0]
    overlay = panel._controller_overlay
    assert overlay is not None
    app.processEvents()
    assert overlay.isVisible()

    backend.connected_port = None
    panel._poll_native_connection_status()

    assert hook.stop_calls == 1
    assert hook.is_running is False
    assert panel.virtual_controller_enabled is False
    assert panel._keyboard_hook is hook
    assert overlay.isVisible()

    panel._poll_native_connection_status()

    assert hook.stop_calls == 2
    assert panel._keyboard_hook is None
    assert not overlay.isVisible()
    assert panel.shutdown()


def test_easycon_panel_controller_overlay_reads_native_report(easycon_panel_factory, app):
    backend = FakeNativeBackend()
    backend.report = SwitchReport(
        button=int(SwitchButton.A | SwitchButton.HOME),
        lx=255,
        ly=0,
        rx=64,
        ry=192,
    )
    panel = easycon_panel_factory(native_backend=backend)
    assert panel.connect_native()

    panel.show_controller_overlay()
    app.processEvents()
    overlay = panel._controller_overlay
    assert overlay is not None
    overlay.refresh_state()
    snapshot = overlay.report

    assert snapshot == backend.report
    assert snapshot is not backend.report
    backend.report.reset()
    assert snapshot.button == int(SwitchButton.A | SwitchButton.HOME)
    assert panel.shutdown()


def test_easycon_panel_shutdown_stops_keyboard_hook_and_overlay(
    easycon_panel_factory,
    app,
):
    hook_factory = FakeKeyboardHookFactory()
    panel = easycon_panel_factory(keyboard_hook_factory=hook_factory)
    assert panel.connect_native()
    assert panel._activate_virtual_controller()
    hook = hook_factory.instances[0]
    overlay = panel._controller_overlay
    assert overlay is not None
    app.processEvents()
    assert overlay._timer.isActive()

    assert panel.shutdown()

    assert hook.stop_calls == 1
    assert not hook.is_running
    assert panel._keyboard_hook is None
    assert panel.virtual_controller_enabled is False
    assert not overlay._timer.isActive()
    assert not overlay.isVisible()


def test_easycon_panel_keyboard_virtual_controller_uses_key_down_up(monkeypatch, tmp_path, easycon_panel):
    FakeBridgeBackend.instances.clear()
    monkeypatch.setattr(panel_module, "BridgeEasyConBackend", FakeBridgeBackend)
    select_bridge_mode(easycon_panel)
    bridge = tmp_path / "EasyConBridge.exe"
    bridge.write_text("", encoding="utf-8")
    easycon_panel.bridge_path.setText(str(bridge))
    easycon_panel.connect_bridge()

    easycon_panel.keyboard_controller_check.setChecked(True)
    QApplication.sendEvent(
        easycon_panel,
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_L, Qt.KeyboardModifier.NoModifier),
    )
    QApplication.sendEvent(
        easycon_panel,
        QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_L, Qt.KeyboardModifier.NoModifier),
    )
    QApplication.sendEvent(
        easycon_panel,
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_W, Qt.KeyboardModifier.NoModifier),
    )
    QApplication.sendEvent(
        easycon_panel,
        QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_W, Qt.KeyboardModifier.NoModifier),
    )

    backend = FakeBridgeBackend.instances[-1]
    assert backend.key_events == [("down", "A"), ("up", "A")]
    assert backend.stick_events == [("left", "Up", True), ("left", "Up", False)]
    assert "键盘虚拟手柄已启用" in easycon_panel.log_view.toPlainText()


def test_easycon_panel_records_uppercase_direction_press_release_and_reset(monkeypatch, easycon_panel):
    timestamps = iter((101.0, 103.0, 104.0, 107.0, 109.0, 110.0))
    monkeypatch.setattr(panel_module, "monotonic", lambda: next(timestamps))
    easycon_panel.key_mapping["Right"] = int(Qt.Key.Key_H)
    assert easycon_panel.connect_native()
    assert easycon_panel._activate_virtual_controller()
    easycon_panel.editor.clear()

    easycon_panel._start_recording()
    for key in (Qt.Key.Key_H, Qt.Key.Key_D, Qt.Key.Key_Right):
        easycon_panel._handle_virtual_controller_key(key, True)
        easycon_panel._handle_virtual_controller_key(key, False)
    easycon_panel._stop_recording()

    assert easycon_panel.editor.toPlainText() == (
        "RIGHT DOWN\n"
        "WAIT 2000\n"
        "RIGHT UP\n"
        "WAIT 1000\n"
        "LS RIGHT\n"
        "WAIT 3000\n"
        "LS RESET\n"
        "WAIT 2000\n"
        "RS RIGHT\n"
        "WAIT 1000\n"
        "RS RESET\n"
    )
    assert easycon_panel.shutdown()


def test_easycon_panel_records_composite_hat_and_stick_directions(monkeypatch, easycon_panel):
    timestamps = iter((101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0, 108.0))
    monkeypatch.setattr(panel_module, "monotonic", lambda: next(timestamps))
    easycon_panel.key_mapping["Up"] = int(Qt.Key.Key_U)
    easycon_panel.key_mapping["Right"] = int(Qt.Key.Key_H)
    assert easycon_panel.connect_native()
    assert easycon_panel._activate_virtual_controller()
    easycon_panel.editor.clear()

    easycon_panel._start_recording()
    easycon_panel._handle_virtual_controller_key(Qt.Key.Key_U, True)
    easycon_panel._handle_virtual_controller_key(Qt.Key.Key_H, True)
    easycon_panel._handle_virtual_controller_key(Qt.Key.Key_U, False)
    easycon_panel._handle_virtual_controller_key(Qt.Key.Key_H, False)
    easycon_panel._handle_virtual_controller_key(Qt.Key.Key_W, True)
    easycon_panel._handle_virtual_controller_key(Qt.Key.Key_D, True)
    easycon_panel._handle_virtual_controller_key(Qt.Key.Key_W, False)
    easycon_panel._handle_virtual_controller_key(Qt.Key.Key_D, False)
    easycon_panel._stop_recording()

    assert easycon_panel.editor.toPlainText() == (
        "UP DOWN\n"
        "WAIT 1000\n"
        "UP UP\n"
        "UPRIGHT DOWN\n"
        "WAIT 1000\n"
        "UPRIGHT UP\n"
        "RIGHT DOWN\n"
        "WAIT 1000\n"
        "RIGHT UP\n"
        "WAIT 1000\n"
        "LS UP\n"
        "WAIT 1000\n"
        "LS UPRIGHT\n"
        "WAIT 1000\n"
        "LS RIGHT\n"
        "WAIT 1000\n"
        "LS RESET\n"
    )
    assert easycon_panel.shutdown()


def test_easycon_panel_skips_unchanged_composite_hat_direction(monkeypatch, easycon_panel):
    timestamps = iter((101.0, 102.0, 103.0, 104.0, 105.0, 106.0))
    monkeypatch.setattr(panel_module, "monotonic", lambda: next(timestamps))
    easycon_panel.key_mapping["Up"] = int(Qt.Key.Key_U)
    easycon_panel.key_mapping["Right"] = int(Qt.Key.Key_H)
    easycon_panel.key_mapping["UpRight"] = int(Qt.Key.Key_Y)
    assert easycon_panel.connect_native()
    assert easycon_panel._activate_virtual_controller()
    easycon_panel.editor.clear()

    easycon_panel._start_recording()
    for key, down in (
        (Qt.Key.Key_U, True),
        (Qt.Key.Key_H, True),
        (Qt.Key.Key_Y, True),
        (Qt.Key.Key_Y, False),
        (Qt.Key.Key_U, False),
        (Qt.Key.Key_H, False),
    ):
        easycon_panel._handle_virtual_controller_key(key, down)
    easycon_panel._stop_recording()

    assert easycon_panel.editor.toPlainText() == (
        "UP DOWN\n"
        "WAIT 1000\n"
        "UP UP\n"
        "UPRIGHT DOWN\n"
        "WAIT 3000\n"
        "UPRIGHT UP\n"
        "RIGHT DOWN\n"
        "WAIT 1000\n"
        "RIGHT UP\n"
    )
    assert easycon_panel.shutdown()


def test_easycon_panel_recording_ignores_auto_repeat_events(monkeypatch, easycon_panel):
    timestamps = iter((101.0, 104.0))
    monkeypatch.setattr(panel_module, "monotonic", lambda: next(timestamps))
    backend = easycon_panel.native_backend
    assert isinstance(backend, FakeNativeBackend)
    assert easycon_panel.connect_native()
    easycon_panel.keyboard_controller_check.setChecked(True)
    easycon_panel.editor.clear()

    easycon_panel._start_recording()
    QApplication.sendEvent(
        easycon_panel,
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_D, Qt.KeyboardModifier.NoModifier),
    )
    QApplication.sendEvent(
        easycon_panel,
        QKeyEvent(
            QEvent.Type.KeyRelease,
            Qt.Key.Key_D,
            Qt.KeyboardModifier.NoModifier,
            "",
            True,
            1,
        ),
    )
    QApplication.sendEvent(
        easycon_panel,
        QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_D,
            Qt.KeyboardModifier.NoModifier,
            "",
            True,
            1,
        ),
    )
    QApplication.sendEvent(
        easycon_panel,
        QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_D, Qt.KeyboardModifier.NoModifier),
    )
    easycon_panel._stop_recording()

    assert backend.stick_events == [
        ("left", "Right", True),
        ("left", "Right", False),
    ]
    assert easycon_panel.editor.toPlainText() == "LS RIGHT\nWAIT 3000\nLS RESET\n"


def test_easycon_panel_records_opposite_directions_as_neutral(monkeypatch, easycon_panel):
    timestamps = iter((101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0, 108.0))
    monkeypatch.setattr(panel_module, "monotonic", lambda: next(timestamps))
    easycon_panel.key_mapping["Up"] = int(Qt.Key.Key_U)
    easycon_panel.key_mapping["Down"] = int(Qt.Key.Key_H)
    assert easycon_panel.connect_native()
    assert easycon_panel._activate_virtual_controller()
    easycon_panel.editor.clear()

    easycon_panel._start_recording()
    for key, down in (
        (Qt.Key.Key_U, True),
        (Qt.Key.Key_H, True),
        (Qt.Key.Key_U, False),
        (Qt.Key.Key_H, False),
        (Qt.Key.Key_W, True),
        (Qt.Key.Key_S, True),
        (Qt.Key.Key_W, False),
        (Qt.Key.Key_S, False),
    ):
        easycon_panel._handle_virtual_controller_key(key, down)
    easycon_panel._stop_recording()

    assert easycon_panel.editor.toPlainText() == (
        "UP DOWN\n"
        "WAIT 1000\n"
        "UP UP\n"
        "WAIT 1000\n"
        "DOWN DOWN\n"
        "WAIT 1000\n"
        "DOWN UP\n"
        "WAIT 1000\n"
        "LS UP\n"
        "WAIT 1000\n"
        "LS RESET\n"
        "WAIT 1000\n"
        "LS DOWN\n"
        "WAIT 1000\n"
        "LS RESET\n"
    )
    assert easycon_panel.shutdown()


def test_easycon_panel_resume_recording_excludes_paused_input_and_wait(monkeypatch, easycon_panel):
    timestamps = iter((101.0, 102.0, 1001.0, 1003.0))
    monkeypatch.setattr(panel_module, "monotonic", lambda: next(timestamps))
    assert easycon_panel.connect_native()
    assert easycon_panel._activate_virtual_controller()
    easycon_panel.editor.clear()

    easycon_panel._start_recording()
    easycon_panel._handle_virtual_controller_key(Qt.Key.Key_D, True)
    easycon_panel._toggle_pause_recording()
    easycon_panel._handle_virtual_controller_key(Qt.Key.Key_W, True)
    easycon_panel._resume_recording()

    assert easycon_panel.virtual_controller_keys == {}

    easycon_panel._handle_virtual_controller_key(Qt.Key.Key_Right, True)
    easycon_panel._handle_virtual_controller_key(Qt.Key.Key_Right, False)
    easycon_panel._stop_recording()

    assert easycon_panel.editor.toPlainText() == (
        "LS RIGHT\n"
        "WAIT 1000\n"
        "LS RESET\n"
        "RS RIGHT\n"
        "WAIT 2000\n"
        "RS RESET\n"
    )
    assert easycon_panel.shutdown()


def test_easycon_panel_stop_while_paused_preserves_closed_recording(monkeypatch, easycon_panel):
    timestamps = iter((101.0, 104.0))
    monkeypatch.setattr(panel_module, "monotonic", lambda: next(timestamps))
    assert easycon_panel.connect_native()
    assert easycon_panel._activate_virtual_controller()
    easycon_panel.editor.clear()

    easycon_panel._start_recording()
    easycon_panel._handle_virtual_controller_key(Qt.Key.Key_D, True)
    easycon_panel._toggle_pause_recording()

    assert easycon_panel._recording
    assert easycon_panel._recording_paused
    assert easycon_panel.virtual_controller_keys == {}

    easycon_panel._start_recording()

    assert not easycon_panel._recording
    assert not easycon_panel._recording_paused
    assert easycon_panel.editor.toPlainText() == "LS RIGHT\nWAIT 3000\nLS RESET\n"
    assert easycon_panel.shutdown()


def test_easycon_panel_escape_press_immediately_toggles_hook_controller(
    easycon_panel_factory,
    app,
):
    backend = FakeNativeBackend()
    hook_factory = FakeKeyboardHookFactory()
    panel = easycon_panel_factory(
        native_backend=backend,
        keyboard_hook_factory=hook_factory,
    )
    assert panel.connect_native()
    assert panel._activate_virtual_controller()
    hook = hook_factory.instances[0]
    overlay = panel._controller_overlay
    assert overlay is not None
    app.processEvents()

    hook.emit_key(Qt.Key.Key_Escape, True)
    process_events_until(lambda: not panel.virtual_controller_enabled)

    assert panel._keyboard_hook is hook
    assert panel._vpad_input_source == "hook"
    assert hook.is_running
    assert hook.stop_calls == 0
    assert hook.mapped_keys_enabled is False
    assert hook.mapped_keys_enabled_calls == [False]
    assert panel.keyboard_controller_check.isChecked() is False
    assert overlay.isVisible()
    assert overlay.active is False

    hook.emit_key(Qt.Key.Key_Escape, True)
    app.processEvents()
    assert panel.virtual_controller_enabled is False
    assert hook.mapped_keys_enabled_calls == [False]

    hook.emit_key(Qt.Key.Key_Escape, False)
    hook.emit_key(Qt.Key.Key_Escape, True)
    process_events_until(lambda: panel.virtual_controller_enabled)

    assert panel._keyboard_hook is hook
    assert panel._vpad_input_source == "hook"
    assert hook.stop_calls == 0
    assert hook.mapped_keys_enabled is True
    assert hook.mapped_keys_enabled_calls == [False, True]
    assert panel.keyboard_controller_check.isChecked()
    assert overlay.isVisible()
    assert overlay.active is True
    hook.emit_key(Qt.Key.Key_Escape, False)

    assert panel.shutdown()
    assert hook.stop_calls == 1
    assert not hook.is_running


def test_easycon_panel_escape_press_immediately_toggles_qt_fallback_controller(
    easycon_panel_factory,
    app,
):
    backend = FakeNativeBackend()
    panel = easycon_panel_factory(
        native_backend=backend,
        keyboard_hook_factory=UnsupportedKeyboardHookFactory(),
    )
    assert panel.connect_native()
    assert panel._activate_virtual_controller()
    assert panel._vpad_input_source == "qt"
    overlay = panel._controller_overlay
    assert overlay is not None
    app.processEvents()

    def send_escape(down: bool) -> None:
        QApplication.sendEvent(
            panel,
            QKeyEvent(
                QEvent.Type.KeyPress if down else QEvent.Type.KeyRelease,
                Qt.Key.Key_Escape,
                Qt.KeyboardModifier.NoModifier,
            ),
        )

    send_escape(True)
    assert panel.virtual_controller_enabled is False

    assert panel._vpad_input_source == "qt"
    assert panel.keyboard_controller_check.isChecked() is False
    assert overlay.isVisible()
    assert overlay.active is False
    mapped_press = QKeyEvent(
        QEvent.Type.KeyPress,
        Qt.Key.Key_L,
        Qt.KeyboardModifier.NoModifier,
    )
    mapped_release = QKeyEvent(
        QEvent.Type.KeyRelease,
        Qt.Key.Key_L,
        Qt.KeyboardModifier.NoModifier,
    )
    assert panel.eventFilter(panel, mapped_press) is False
    assert panel.eventFilter(panel, mapped_release) is False
    assert backend.key_events == []

    send_escape(True)
    assert panel.virtual_controller_enabled is False
    send_escape(False)
    send_escape(True)
    assert panel.virtual_controller_enabled is True

    assert panel._vpad_input_source == "qt"
    assert panel.keyboard_controller_check.isChecked()
    assert overlay.active is True
    send_escape(False)
    assert panel.shutdown()


@pytest.mark.parametrize(
    ("use_hook", "start_in_standby"),
    ((True, False), (True, True), (False, False), (False, True)),
)
def test_easycon_panel_ctrl_escape_fully_closes_from_active_or_standby(
    easycon_panel_factory,
    app,
    use_hook,
    start_in_standby,
):
    hook_factory = FakeKeyboardHookFactory() if use_hook else UnsupportedKeyboardHookFactory()
    panel = easycon_panel_factory(
        native_backend=FakeNativeBackend(),
        keyboard_hook_factory=hook_factory,
    )
    assert panel.connect_native()
    assert panel._activate_virtual_controller()
    overlay = panel._controller_overlay
    assert overlay is not None
    app.processEvents()
    if start_in_standby:
        assert panel._set_virtual_controller_standby()

    if use_hook:
        hook = hook_factory.instances[0]
        hook.emit_key(Qt.Key.Key_Escape, True, control_down=True)
    else:
        QApplication.sendEvent(
            panel,
            QKeyEvent(
                QEvent.Type.KeyPress,
                Qt.Key.Key_Escape,
                Qt.KeyboardModifier.ControlModifier,
            ),
        )
    process_events_until(lambda: panel._vpad_input_source is None)

    assert panel.virtual_controller_enabled is False
    assert panel.keyboard_controller_check.isChecked() is False
    assert panel._keyboard_hook is None
    assert not overlay.isVisible()
    if use_hook:
        assert hook.stop_calls == 1
        assert not hook.is_running
    assert panel.shutdown()


def test_easycon_panel_escape_auto_repeat_and_release_do_not_toggle_again(
    easycon_panel_factory,
    app,
):
    panel = easycon_panel_factory(
        native_backend=FakeNativeBackend(),
        keyboard_hook_factory=UnsupportedKeyboardHookFactory(),
    )
    assert panel.connect_native()
    assert panel._activate_virtual_controller()

    QApplication.sendEvent(
        panel,
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier),
    )
    assert panel.virtual_controller_enabled is False

    for event_type in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
        QApplication.sendEvent(
            panel,
            QKeyEvent(
                event_type,
                Qt.Key.Key_Escape,
                Qt.KeyboardModifier.NoModifier,
                "",
                True,
                1,
            ),
        )
    assert panel.virtual_controller_enabled is False

    QApplication.sendEvent(
        panel,
        QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier),
    )
    QApplication.sendEvent(
        panel,
        QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier),
    )
    assert panel.virtual_controller_enabled is False

    QApplication.sendEvent(
        panel,
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier),
    )
    assert panel.virtual_controller_enabled is True
    QApplication.sendEvent(
        panel,
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier),
    )
    app.processEvents()
    assert panel.virtual_controller_enabled is True
    assert panel.shutdown()


def test_easycon_panel_qt_fallback_keeps_standby_held_key_passthrough_after_resume(
    easycon_panel_factory,
):
    backend = FakeNativeBackend()
    panel = easycon_panel_factory(
        native_backend=backend,
        keyboard_hook_factory=UnsupportedKeyboardHookFactory(),
    )
    assert panel.connect_native()
    assert panel._activate_virtual_controller()
    assert panel._set_virtual_controller_standby()

    mapped_press = QKeyEvent(
        QEvent.Type.KeyPress,
        Qt.Key.Key_L,
        Qt.KeyboardModifier.NoModifier,
    )
    mapped_release = QKeyEvent(
        QEvent.Type.KeyRelease,
        Qt.Key.Key_L,
        Qt.KeyboardModifier.NoModifier,
    )
    assert panel.eventFilter(panel, mapped_press) is False
    assert Qt.Key.Key_L in panel._qt_passthrough_pressed_keys

    assert panel._activate_virtual_controller()
    assert panel.eventFilter(panel, mapped_release) is False
    assert panel._qt_passthrough_pressed_keys == set()
    assert backend.key_events == []

    assert panel.eventFilter(panel, mapped_press) is True
    assert panel.eventFilter(panel, mapped_release) is True
    assert backend.key_events == [("down", "A"), ("up", "A")]
    assert panel.shutdown()


def test_easycon_panel_qt_fallback_clears_standby_passthrough_on_app_deactivate(
    easycon_panel_factory,
):
    panel = easycon_panel_factory(
        native_backend=FakeNativeBackend(),
        keyboard_hook_factory=UnsupportedKeyboardHookFactory(),
    )
    assert panel.connect_native()
    assert panel._activate_virtual_controller()
    assert panel._set_virtual_controller_standby()
    mapped_press = QKeyEvent(
        QEvent.Type.KeyPress,
        Qt.Key.Key_L,
        Qt.KeyboardModifier.NoModifier,
    )
    assert panel.eventFilter(panel, mapped_press) is False
    assert panel._qt_passthrough_pressed_keys == {Qt.Key.Key_L}

    panel.eventFilter(QApplication.instance(), QEvent(QEvent.Type.ApplicationDeactivate))

    assert panel._qt_passthrough_pressed_keys == set()
    assert panel.shutdown()


def test_easycon_panel_keyboard_virtual_controller_releases_on_app_deactivate(monkeypatch, tmp_path, easycon_panel):
    FakeBridgeBackend.instances.clear()
    monkeypatch.setattr(panel_module, "BridgeEasyConBackend", FakeBridgeBackend)
    select_bridge_mode(easycon_panel)
    bridge = tmp_path / "EasyConBridge.exe"
    bridge.write_text("", encoding="utf-8")
    easycon_panel.bridge_path.setText(str(bridge))
    easycon_panel.connect_bridge()

    easycon_panel.keyboard_controller_check.setChecked(True)
    QApplication.sendEvent(
        easycon_panel,
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_L, Qt.KeyboardModifier.NoModifier),
    )
    QApplication.sendEvent(QApplication.instance(), QEvent(QEvent.Type.ApplicationDeactivate))
    QApplication.sendEvent(
        easycon_panel,
        QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_L, Qt.KeyboardModifier.NoModifier),
    )

    backend = FakeBridgeBackend.instances[-1]
    assert backend.key_events == [("down", "A"), ("up", "A")]
    assert easycon_panel.virtual_controller_keys == {}


def test_easycon_panel_runs_cli_smoke_test(monkeypatch, tmp_path, easycon_panel):
    ezcon = tmp_path / "ezcon.cmd"
    ezcon.write_text(
        "\n".join(
            [
                "@echo off",
                "if \"%1\"==\"--version\" (echo fake-ezcon-1.0& exit /b 0)",
                "if \"%1\"==\"run\" (echo cli smoke %2 %3 %4& exit /b 0)",
                "exit /b 1",
            ]
        ),
        encoding="utf-8",
        newline="\r\n",
    )
    monkeypatch.setattr(
        panel_module,
        "discover_ezcon",
        lambda _config: EasyConInstallation(path=ezcon, version="fake", source="test"),
    )
    easycon_panel.backend_mode.setCurrentIndex(1)

    easycon_panel.run_cli_smoke_test()
    assert easycon_panel.process is not None
    temporary_script = Path(easycon_panel.process.arguments()[1])
    assert temporary_script.name.startswith("auto-bdsp-rng-easycon-")
    assert temporary_script.suffix == ".txt"
    assert temporary_script.read_text(encoding="utf-8") == "WAIT 50\n"
    assert easycon_panel.process.waitForFinished(2000)
    app = QApplication.instance()
    assert app is not None
    app.processEvents()

    assert temporary_script.exists() is False
    assert easycon_panel._cli_script_path is None
    assert (panel_module.SCRIPT_DIR / ".generated").exists() is False
    log_text = easycon_panel.log_view.toPlainText()
    assert "测试 CLI 运行会触发一次 CLI 连接" in log_text
    assert "cli smoke" in log_text


def test_easycon_panel_cli_smoke_accepts_chinese_and_space_paths(monkeypatch, tmp_path, app):
    script_dir = tmp_path / "脚本 目录"
    script_dir.mkdir()
    ezcon_dir = tmp_path / "伊机控 CLI"
    ezcon_dir.mkdir()
    ezcon = ezcon_dir / "ezcon.cmd"
    ezcon.write_text(
        "\n".join(
            [
                "@echo off",
                "if \"%1\"==\"--version\" (echo fake-ezcon-1.0& exit /b 0)",
                "if \"%1\"==\"run\" (echo ok path %2& exit /b 0)",
                "exit /b 1",
            ]
        ),
        encoding="utf-8",
        newline="\r\n",
    )
    monkeypatch.setattr(panel_module, "SCRIPT_DIR", script_dir)
    monkeypatch.setattr(panel_module, "load_config", lambda: EasyConConfig(mock_enabled=True))
    monkeypatch.setattr(panel_module, "save_config", lambda _config: tmp_path / "config.json")
    monkeypatch.setattr(
        panel_module,
        "discover_ezcon",
        lambda _config: EasyConInstallation(path=ezcon, version="fake", source="test"),
    )
    monkeypatch.setattr(panel_module, "list_ports", lambda _installation: ["COM7"])
    panel = EasyConPanel()
    panel.detect_easycon()
    panel.backend_mode.setCurrentIndex(1)

    panel.run_cli_smoke_test()
    assert panel.process is not None
    temporary_script = Path(panel.process.arguments()[1])
    assert temporary_script.suffix == ".txt"
    assert script_dir not in temporary_script.parents
    assert panel.process.waitForFinished(2000)
    app = QApplication.instance()
    assert app is not None
    app.processEvents()

    assert temporary_script.exists() is False
    assert (script_dir / ".generated").exists() is False
    assert "ok path" in panel.log_view.toPlainText()


def test_easycon_panel_copies_and_saves_logs(monkeypatch, tmp_path, easycon_panel):
    easycon_panel._append_log("info", "第一行日志")
    easycon_panel.copy_all_logs()

    assert "第一行日志" in QApplication.clipboard().text()

    output = tmp_path / "easycon.log"
    monkeypatch.setattr(panel_module.QFileDialog, "getSaveFileName", lambda *_args: (str(output), ""))

    saved = easycon_panel.save_logs_dialog()

    assert saved == output
    assert "第一行日志" in output.read_text(encoding="utf-8")


def test_easycon_panel_persists_and_applies_log_retention(monkeypatch, tmp_path, easycon_panel):
    saved_configs: list[EasyConConfig] = []
    monkeypatch.setattr(panel_module, "save_config", lambda saved: saved_configs.append(saved) or tmp_path / "config.json")

    easycon_panel.log_keep_lines.setValue(3)
    for index in range(5):
        easycon_panel._append_log("info", f"日志 {index}")

    log_text = easycon_panel.log_view.toPlainText()
    assert "日志 0" not in log_text
    assert "日志 1" not in log_text
    assert "日志 2" in log_text
    assert "日志 4" in log_text
    assert saved_configs[-1].keep_log_lines == 3


def test_easycon_panel_error_log_scrolls_to_last_error(easycon_panel):
    easycon_panel._append_log("info", "前置日志")
    easycon_panel.log_view.moveCursor(QTextCursor.MoveOperation.Start)

    easycon_panel._append_log("error", "最后一条错误")

    cursor = easycon_panel.log_view.textCursor()
    assert cursor.position() == easycon_panel.log_view.document().characterCount() - 1
    assert easycon_panel.log_view.toPlainText().endswith("最后一条错误")


def test_easycon_panel_records_script_print_output_from_cli(monkeypatch, tmp_path, easycon_panel):
    ezcon = tmp_path / "ezcon.cmd"
    ezcon.write_text(
        "\n".join(
            [
                "@echo off",
                "if \"%1\"==\"--version\" (echo fake-ezcon-1.0& exit /b 0)",
                "if \"%1\"==\"run\" (echo PRINT hello& exit /b 0)",
                "exit /b 1",
            ]
        ),
        encoding="utf-8",
        newline="\r\n",
    )
    monkeypatch.setattr(
        panel_module,
        "discover_ezcon",
        lambda _config: EasyConInstallation(path=ezcon, version="fake", source="test"),
    )
    easycon_panel.backend_mode.setCurrentIndex(1)
    easycon_panel.run_cli_smoke_test()
    assert easycon_panel.process is not None
    assert easycon_panel.process.waitForFinished(2000)
    app = QApplication.instance()
    assert app is not None
    app.processEvents()

    log_text = easycon_panel.log_view.toPlainText()
    assert "PRINT hello" in log_text


def test_easycon_panel_reports_missing_ezcon_and_empty_ports(monkeypatch, tmp_path, app):
    script_dir = tmp_path / "script"
    script_dir.mkdir()
    monkeypatch.setattr(panel_module, "SCRIPT_DIR", script_dir)
    monkeypatch.setattr(panel_module, "load_config", lambda: EasyConConfig(mock_enabled=False))
    monkeypatch.setattr(panel_module, "save_config", lambda _config: tmp_path / "config.json")
    monkeypatch.setattr(
        panel_module,
        "discover_ezcon",
        lambda _config: EasyConInstallation(path=None, error="ezcon.exe not found"),
    )
    monkeypatch.setattr(panel_module, "list_ports", lambda _installation: [])

    panel = EasyConPanel()
    panel.detect_easycon()

    assert "请选择 ezcon.exe 或设置 EASYCON_ROOT" in panel.log_view.toPlainText()
    assert panel.run_button.isEnabled() is False


def test_easycon_panel_reports_invalid_ezcon_version(monkeypatch, tmp_path, app):
    script_dir = tmp_path / "script"
    script_dir.mkdir()
    monkeypatch.setattr(panel_module, "SCRIPT_DIR", script_dir)
    monkeypatch.setattr(panel_module, "load_config", lambda: EasyConConfig(ezcon_path=Path("D:/bad/ezcon.exe")))
    monkeypatch.setattr(panel_module, "save_config", lambda _config: tmp_path / "config.json")
    monkeypatch.setattr(
        panel_module,
        "discover_ezcon",
        lambda _config: EasyConInstallation(path=None, error="D:/bad/ezcon.exe --version failed"),
    )
    monkeypatch.setattr(panel_module, "list_ports", lambda _installation: [])

    panel = EasyConPanel()
    panel.detect_easycon()

    assert "ezcon 路径可能无效或文件损坏" in panel.log_view.toPlainText()


def test_easycon_panel_reports_empty_ports_when_mock_disabled(monkeypatch, tmp_path, app):
    script_dir = tmp_path / "script"
    script_dir.mkdir()
    monkeypatch.setattr(panel_module, "SCRIPT_DIR", script_dir)
    monkeypatch.setattr(panel_module, "load_config", lambda: EasyConConfig(mock_enabled=False))
    monkeypatch.setattr(panel_module, "save_config", lambda _config: tmp_path / "config.json")
    monkeypatch.setattr(
        panel_module,
        "discover_ezcon",
        lambda _config: EasyConInstallation(path=Path("D:/EasyCon/ezcon.exe"), version="1.6.3", source="test"),
    )
    monkeypatch.setattr(panel_module, "list_ports", lambda _installation: [])

    panel = EasyConPanel(native_backend=FakeNativeBackend([]))

    assert "未发现串口；请连接设备后刷新串口，运行按钮已禁用" in panel.log_view.toPlainText()
    assert panel.run_button.isEnabled() is False


def test_easycon_panel_shutdown_closes_persistent_bridge(easycon_panel):
    backend = FakeBridgeBackend()
    easycon_panel.bridge_backend = backend

    assert easycon_panel.shutdown() is True

    assert backend.closed is True
    assert easycon_panel.bridge_backend is None
    with pytest.raises(RuntimeError, match="正在关闭"):
        easycon_panel._ensure_bridge_backend()

    assert easycon_panel.shutdown() is True


def test_easycon_panel_shutdown_clears_pending_native_run_reservation(easycon_panel):
    backend = easycon_panel.native_backend
    assert isinstance(backend, FakeNativeBackend)
    assert easycon_panel.connect_native()
    assert easycon_panel.reserve_native_script_run()
    assert easycon_panel.native_run_thread is None

    assert easycon_panel.shutdown() is True

    assert easycon_panel._native_run_reserved is False
    assert backend.closed is True


def test_easycon_panel_shutdown_reports_process_or_thread_timeout(easycon_panel):
    events: list[object] = []

    class StuckProcess:
        def state(self):
            return QProcess.ProcessState.Running

        def kill(self) -> None:
            events.append("process-kill")

        def waitForFinished(self, wait_ms: int) -> bool:
            events.append(("process-wait", wait_ms))
            return False

    class StuckThread:
        def isRunning(self) -> bool:
            return True

        def requestInterruption(self) -> None:
            events.append("thread-interrupt")

        def quit(self) -> None:
            events.append("thread-quit")

        def wait(self, wait_ms: int) -> bool:
            events.append(("thread-wait", wait_ms))
            return False

    easycon_panel.process = StuckProcess()  # type: ignore[assignment]
    easycon_panel.bridge_run_thread = StuckThread()  # type: ignore[assignment]

    assert easycon_panel.shutdown(wait_ms=25) is False
    assert events == [
        "process-kill",
        ("process-wait", 25),
        "thread-interrupt",
        "thread-quit",
        ("thread-wait", 25),
    ]


def test_easycon_panel_shutdown_retries_failed_bridge_close(easycon_panel):
    class RetryBackend(FakeBridgeBackend):
        def __init__(self) -> None:
            super().__init__()
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1
            if self.close_calls == 1:
                raise OSError("bridge still running")
            self.closed = True

    backend = RetryBackend()
    easycon_panel.bridge_backend = backend

    assert easycon_panel.shutdown(wait_ms=25) is False
    assert easycon_panel.bridge_backend is backend
    assert easycon_panel.shutdown(wait_ms=25) is True
    assert easycon_panel.bridge_backend is None
    assert backend.closed is True


def test_easycon_panel_reports_unsaved_script_changes(easycon_panel):
    assert easycon_panel.has_unsaved_script_changes() is False

    easycon_panel.editor.setPlainText(easycon_panel.editor.toPlainText() + "\nA 100")

    assert easycon_panel.has_unsaved_script_changes() is True
