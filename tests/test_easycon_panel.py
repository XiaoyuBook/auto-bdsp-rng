from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLineEdit, QSpinBox

import auto_bdsp_rng.ui.easycon_panel as panel_module
from auto_bdsp_rng.automation.easycon import EasyConConfig, EasyConInstallation, EasyConStatus
from auto_bdsp_rng.ui.easycon_panel import EasyConPanel


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
    return EasyConPanel()


def process_events_until(predicate, timeout_ms=1000):
    app = QApplication.instance()
    assert app is not None
    deadline = time.monotonic() + timeout_ms / 1000
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert predicate()


class FakeBridgeBackend:
    instances: list["FakeBridgeBackend"] = []

    def __init__(self, bridge_path=None, log_callback=None):
        self.bridge_path = bridge_path
        self.log_callback = log_callback
        self.connected_port = None
        self.script_runs: list[tuple[str, str | None]] = []
        self.presses: list[tuple[str, int]] = []
        self.sticks: list[tuple[str, str | int, int | None]] = []
        self.stopped = False
        self.disconnected = False
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

    def press(self, button, duration_ms):
        self.presses.append((button, duration_ms))

    def stick(self, side, direction, duration_ms):
        self.sticks.append((side, direction, duration_ms))


def test_easycon_panel_lists_builtin_scripts(easycon_panel):
    assert easycon_panel.script_list.count() == 1
    assert easycon_panel.script_list.item(0).text() == "玫瑰公园.txt"


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


def test_easycon_panel_bridge_mode_requires_connection(easycon_panel):
    easycon_panel._load_script_item(easycon_panel.script_list.item(0))
    blink_input = easycon_panel.parameter_widgets["_闪帧"]
    assert isinstance(blink_input, QLineEdit)
    blink_input.setText("123")

    assert easycon_panel.backend_mode.currentData() == "bridge"
    assert easycon_panel.run_button.isEnabled() is False
    assert easycon_panel.backend_label.text() == "单片机: 未连接"


def test_easycon_panel_cli_mode_is_not_reported_as_connected(easycon_panel):
    easycon_panel.backend_mode.setCurrentIndex(1)

    assert easycon_panel.backend_label.text() == "单片机: CLI 过渡后端可用（未长期连接）"
    assert easycon_panel._connection_state_text() == "CLI 可用（未长期连接）"
    assert "CLI 过渡" in easycon_panel.status_backend_label.text()


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
    bridge = tmp_path / "EasyConBridge.exe"
    bridge.write_text("", encoding="utf-8")
    easycon_panel.bridge_path.setText(str(bridge))

    easycon_panel.connect_bridge()
    easycon_panel.send_controller_press("A")
    easycon_panel.send_controller_stick("left", "RESET")

    backend = FakeBridgeBackend.instances[-1]
    assert backend.presses == [("A", 100)]
    assert backend.sticks == [("left", "RESET", 100)]
    assert easycon_panel.task_state_label.text() == "任务: 已完成"


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
