from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLineEdit, QSpinBox

import auto_bdsp_rng.ui.easycon_panel as panel_module
from auto_bdsp_rng.automation.easycon import EasyConConfig, EasyConInstallation
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
