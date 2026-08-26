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
from auto_bdsp_rng.ui.easycon_panel import DEFAULT_KEY_MAPPING, EasyConPanel, KeyMappingDialog


class FakeNativeBackend:
    def __init__(self, ports: list[str] | None = None) -> None:
        self.ports = list(ports) if ports is not None else ["COM7"]
        self.connected_port: str | None = None
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
def easycon_panel(monkeypatch, tmp_path, app):
    script_dir = tmp_path / "script"
    script_dir.mkdir()
    generated_dir = script_dir / ".generated"
    (script_dir / "玫瑰公园.txt").write_text(
        "_闪帧 = 填入这里  # 目标差值\n_等待时间 = 8\nA 100\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(panel_module, "SCRIPT_DIR", script_dir)
    monkeypatch.setattr(panel_module, "GENERATED_DIR", generated_dir)
    monkeypatch.setattr(panel_module, "load_config", lambda: EasyConConfig(mock_enabled=True))
    monkeypatch.setattr(panel_module, "save_config", lambda _config: tmp_path / "config.json")
    monkeypatch.setattr(
        panel_module,
        "discover_ezcon",
        lambda _config: EasyConInstallation(path=Path("D:/EasyCon/ezcon.exe"), version="1.6.3", source="test"),
    )
    monkeypatch.setattr(panel_module, "list_ports", lambda _installation: ["COM7"])
    return EasyConPanel(native_backend=FakeNativeBackend(), video_source_connected=lambda: True)


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
    config = EasyConConfig(key_mapping={"A": int(Qt.Key.Key_M), "LSUp": int(Qt.Key.Key_U)})

    panel_module.save_config(config, config_path)
    restored = panel_module.load_config(config_path)

    assert restored.key_mapping == {"A": int(Qt.Key.Key_M), "LSUp": int(Qt.Key.Key_U)}


def test_easycon_panel_restores_configured_key_mapping(monkeypatch, tmp_path, app):
    script_dir = tmp_path / "script"
    script_dir.mkdir()
    generated_dir = script_dir / ".generated"
    saved_configs: list[EasyConConfig] = []
    monkeypatch.setattr(panel_module, "SCRIPT_DIR", script_dir)
    monkeypatch.setattr(panel_module, "GENERATED_DIR", generated_dir)
    monkeypatch.setattr(
        panel_module,
        "load_config",
        lambda: EasyConConfig(mock_enabled=True, key_mapping={"A": int(Qt.Key.Key_M)}),
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
    assert panel.key_mapping["B"] == DEFAULT_KEY_MAPPING["B"]


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
    easycon_panel.run_script()
    process_events_until(lambda: easycon_panel.native_run_thread is None)

    assert len(backend.script_runs) == 1
    assert easycon_panel._native_run_reserved is False
    script_text, script_name, script_dir = backend.script_runs[0]
    assert script_name == "玫瑰公园.txt"
    assert script_dir == easycon_panel.current_script_path.parent
    assert "_闪帧 = 123" in script_text


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


def test_easycon_panel_native_mock_port_does_not_need_ezcon(monkeypatch, tmp_path, app):
    monkeypatch.setattr(panel_module, "SCRIPT_DIR", tmp_path)
    monkeypatch.setattr(panel_module, "GENERATED_DIR", tmp_path / ".generated")
    monkeypatch.setattr(panel_module, "load_config", lambda: EasyConConfig(mock_enabled=True))
    monkeypatch.setattr(panel_module, "save_config", lambda _config: tmp_path / "config.json")
    backend = FakeNativeBackend([])

    panel = EasyConPanel(native_backend=backend, video_source_connected=lambda: True)

    assert panel.port_combo.currentText() == "mock"
    assert panel.connect_native()
    assert backend.connected_port == "mock"


def test_easycon_panel_ignores_stale_bridge_config(monkeypatch, tmp_path, app):
    script_dir = tmp_path / "script"
    script_dir.mkdir()
    generated_dir = script_dir / ".generated"
    bundled_bridge = tmp_path / "bundle" / "bridge" / "EasyConBridge" / "EasyConBridge.exe"
    bundled_bridge.parent.mkdir(parents=True)
    bundled_bridge.write_text("", encoding="utf-8")
    stale_bridge = tmp_path / "old-release" / "bridge" / "EasyConBridge" / "EasyConBridge.exe"

    monkeypatch.setattr(panel_module, "SCRIPT_DIR", script_dir)
    monkeypatch.setattr(panel_module, "GENERATED_DIR", generated_dir)
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


def test_easycon_panel_syncs_parameters_and_saves_generated_script(easycon_panel):
    easycon_panel._load_script_item(easycon_panel.script_list.item(0))
    source = easycon_panel.current_script_path
    blink_input = easycon_panel.parameter_widgets["_闪帧"]
    assert isinstance(blink_input, QLineEdit)

    blink_input.setText("123")
    generated = easycon_panel.save_generated_script()

    assert generated is not None
    assert generated.parent.name == ".generated"
    assert "_闪帧 = 123  # 目标差值" in easycon_panel.editor.toPlainText()
    assert generated.read_text(encoding="utf-8").startswith("_闪帧 = 123")
    assert source is not None
    assert "_闪帧 = 填入这里" in source.read_text(encoding="utf-8")


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


def test_easycon_panel_restores_and_persists_recent_script_parameters(monkeypatch, tmp_path, app):
    script_dir = tmp_path / "script"
    script_dir.mkdir()
    generated_dir = script_dir / ".generated"
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
    monkeypatch.setattr(panel_module, "GENERATED_DIR", generated_dir)
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
    monkeypatch.setattr(panel_module, "GENERATED_DIR", script_dir / ".generated")
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

    easycon_panel.toggle_run()
    assert easycon_panel.process.waitForFinished(2000)
    app = QApplication.instance()
    assert app is not None
    app.processEvents()

    assert "已中止" in easycon_panel.log_view.toPlainText()


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
    generated_script_path = Path(keep_awake_process.arguments()[1])
    assert generated_script_path.name.startswith("capture_keep_awake_l_")
    assert generated_script_path.name.endswith("_controller.ecs")
    assert generated_script_path.read_text(encoding="utf-8") == "L 100\n"
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


def test_easycon_panel_escape_disables_keyboard_virtual_controller(monkeypatch, tmp_path, easycon_panel):
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
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier),
    )

    assert easycon_panel.keyboard_controller_check.isChecked() is False
    assert easycon_panel.virtual_controller_enabled is False


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
    assert easycon_panel.process.waitForFinished(2000)
    app = QApplication.instance()
    assert app is not None
    app.processEvents()

    generated = sorted((panel_module.GENERATED_DIR).glob("*cli_smoke*.ecs"))
    assert generated
    assert generated[-1].read_text(encoding="utf-8") == "WAIT 50\n"
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
    monkeypatch.setattr(panel_module, "GENERATED_DIR", script_dir / ".generated")
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
    assert panel.process.waitForFinished(2000)
    app = QApplication.instance()
    assert app is not None
    app.processEvents()

    generated = sorted((script_dir / ".generated").glob("*cli_smoke*.ecs"))
    assert generated
    assert "脚本 目录" in str(generated[-1])
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
    monkeypatch.setattr(panel_module, "GENERATED_DIR", script_dir / ".generated")
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
    monkeypatch.setattr(panel_module, "GENERATED_DIR", script_dir / ".generated")
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
    monkeypatch.setattr(panel_module, "GENERATED_DIR", script_dir / ".generated")
    monkeypatch.setattr(panel_module, "load_config", lambda: EasyConConfig(mock_enabled=False))
    monkeypatch.setattr(panel_module, "save_config", lambda _config: tmp_path / "config.json")
    monkeypatch.setattr(
        panel_module,
        "discover_ezcon",
        lambda _config: EasyConInstallation(path=Path("D:/EasyCon/ezcon.exe"), version="1.6.3", source="test"),
    )
    monkeypatch.setattr(panel_module, "list_ports", lambda _installation: [])

    panel = EasyConPanel(native_backend=FakeNativeBackend([]))

    assert "未发现串口；请选择串口或启用 mock 模式，运行按钮已禁用" in panel.log_view.toPlainText()
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
