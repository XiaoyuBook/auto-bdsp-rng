from __future__ import annotations

import subprocess
from pathlib import Path

from auto_bdsp_rng.automation.easycon.discovery import discover_ezcon, list_ports, load_config, parse_port_list, save_config
from auto_bdsp_rng.automation.easycon.models import EasyConConfig, EasyConInstallation


def test_discover_ezcon_uses_saved_path(monkeypatch, tmp_path):
    ezcon = tmp_path / "ezcon.exe"
    ezcon.write_text("", encoding="utf-8")

    def fake_run(args, **kwargs):
        assert args == [str(ezcon), "--version"]
        return subprocess.CompletedProcess(args, 0, stdout="1.6.1+test\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    installation = discover_ezcon(EasyConConfig(ezcon_path=ezcon))

    assert installation.path == ezcon
    assert installation.version == "1.6.1+test"
    assert installation.source == "config"


def test_discover_ezcon_falls_back_to_easycon_root(monkeypatch, tmp_path):
    root = tmp_path / "EasyCon"
    root.mkdir()
    ezcon = root / "ezcon.exe"
    ezcon.write_text("", encoding="utf-8")
    monkeypatch.setenv("EASYCON_ROOT", str(root))

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 0, stdout="1.6.1\n", stderr=""),
    )

    assert discover_ezcon(EasyConConfig()).path == ezcon


def test_parse_port_list_accepts_plain_com_tokens():
    assert parse_port_list("COM3\nCOM9 USB Serial\nnoise\nCOM3\n") == ["COM3", "COM9"]


def test_list_ports_calls_ezcon_port_list(monkeypatch, tmp_path):
    ezcon = tmp_path / "ezcon.exe"
    ezcon.write_text("", encoding="utf-8")

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 0, stdout="COM7\n", stderr=""),
    )

    assert list_ports(EasyConInstallation(path=ezcon)) == ["COM7"]


def test_config_round_trip(tmp_path):
    config_path = tmp_path / "config.json"
    config = EasyConConfig(
        ezcon_path=Path("D:/app/EasyCon/ezcon.exe"),
        bridge_path=Path("D:/app/EasyCon/EasyConBridge.exe"),
        last_port="COM9",
        mock_enabled=True,
        recent_scripts=(Path("script/BDSP测种.txt"),),
        script_parameters={"script/玫瑰公园.txt": {"_闪帧": "123", "_等待时间": "8"}},
        keep_generated=7,
    )

    save_config(config, config_path)

    assert load_config(config_path) == config
