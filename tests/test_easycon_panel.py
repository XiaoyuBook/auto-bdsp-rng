from __future__ import annotations

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


class FakeBridgeBackend:
    instances: list["FakeBridgeBackend"] = []

    def __init__(self, bridge_path=None):
        self.bridge_path = bridge_path
        self.connected_port = None
        self.script_runs: list[tuple[str, str | None]] = []
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
            stdout = "bridge stdout"
            stderr = ""

        return Result()

    def status(self):
        return EasyConStatus.BRIDGE_CONNECTED if self.connected_port else EasyConStatus.BRIDGE_DISCONNECTED


def test_easycon_panel_lists_builtin_scripts(easycon_panel):
    assert easycon_panel.script_list.count() == 1
    assert easycon_panel.script_list.item(0).text() == "玫瑰公园.txt"


def test_easycon_panel_loads_script_and_blocks_required_parameter(easycon_panel):
    item = easycon_panel.script_list.item(0)

    easycon_panel._load_script_item(item)

    assert easycon_panel.script_name_label.text() == "玫瑰公园.txt"
    assert "_闪帧 = 填入这里" in easycon_panel.editor.toPlainText()
    assert isinstance(easycon_panel.parameter_widgets["_闪帧"], QLineEdit)
    assert isinstance(easycon_panel.parameter_widgets["_等待时间"], QSpinBox)
    assert easycon_panel.run_button.isEnabled() is False


def test_easycon_panel_syncs_parameters_and_saves_generated_script(easycon_panel):
    easycon_panel._load_script_item(easycon_panel.script_list.item(0))
    blink_input = easycon_panel.parameter_widgets["_闪帧"]
    assert isinstance(blink_input, QLineEdit)

    blink_input.setText("123")
    generated = easycon_panel.save_generated_script()

    assert generated is not None
    assert generated.parent.name == ".generated"
    assert "_闪帧 = 123  # 目标差值" in easycon_panel.editor.toPlainText()
    assert generated.read_text(encoding="utf-8").startswith("_闪帧 = 123")


def test_easycon_panel_bridge_mode_requires_connection(easycon_panel):
    easycon_panel._load_script_item(easycon_panel.script_list.item(0))
    blink_input = easycon_panel.parameter_widgets["_闪帧"]
    assert isinstance(blink_input, QLineEdit)
    blink_input.setText("123")

    assert easycon_panel.backend_mode.currentData() == "bridge"
    assert easycon_panel.run_button.isEnabled() is False
    assert easycon_panel.backend_label.text() == "单片机: 未连接"


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

    backend = FakeBridgeBackend.instances[-1]
    assert easycon_panel.bridge_status == EasyConStatus.BRIDGE_CONNECTED
    assert easycon_panel.backend_label.text() == "单片机: 已长期连接"
    assert backend.connected_port == "COM7"
    assert len(backend.script_runs) == 1
    assert backend.script_runs[0][1] == "玫瑰公园.txt"
    assert "_闪帧 = 123" in backend.script_runs[0][0]
    assert "bridge stdout" in easycon_panel.log_view.toPlainText()


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
