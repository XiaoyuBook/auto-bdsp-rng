from __future__ import annotations

import subprocess
from pathlib import Path

from auto_bdsp_rng.automation.easycon.discovery import (
    EZCON_DISCOVERY_TIMEOUT_SECONDS,
    EZCON_PORT_LIST_TIMEOUT_SECONDS,
    discover_ezcon,
    list_ports,
    load_config,
    parse_port_list,
    save_config,
)
from auto_bdsp_rng.automation.easycon.models import EasyConConfig, EasyConInstallation


def test_discover_ezcon_uses_saved_path(monkeypatch, tmp_path):
    ezcon = tmp_path / "ezcon.exe"
    ezcon.write_text("", encoding="utf-8")

    def fake_run(args, **kwargs):
        assert args == [str(ezcon), "--version"]
        assert kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW
        assert kwargs["timeout"] == EZCON_DISCOVERY_TIMEOUT_SECONDS
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

    def fake_run(args, **kwargs):
        assert args == [str(ezcon), "port", "-l"]
        assert kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW
        assert kwargs["timeout"] == EZCON_PORT_LIST_TIMEOUT_SECONDS
        return subprocess.CompletedProcess(args, 0, stdout="COM7\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert list_ports(EasyConInstallation(path=ezcon)) == ["COM7"]


def test_discover_ezcon_reports_version_probe_timeout(monkeypatch, tmp_path):
    ezcon = tmp_path / "ezcon.exe"
    ezcon.write_text("", encoding="utf-8")
    monkeypatch.delenv("EASYCON_ROOT", raising=False)
    monkeypatch.setattr("auto_bdsp_rng.automation.easycon.discovery.shutil.which", lambda _name: None)

    def timeout_run(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", timeout_run)

    installation = discover_ezcon(EasyConConfig(ezcon_path=ezcon))

    assert installation.path is None
    assert "timed out" in str(installation.error)


def test_list_ports_returns_empty_after_probe_timeout(monkeypatch, tmp_path):
    ezcon = tmp_path / "ezcon.exe"
    ezcon.write_text("", encoding="utf-8")

    def timeout_run(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", timeout_run)

    assert list_ports(EasyConInstallation(path=ezcon)) == []


def test_config_round_trip(tmp_path):
    config_path = tmp_path / "config.json"
    config = EasyConConfig(
        ezcon_path=Path("D:/app/EasyCon/ezcon.exe"),
        bridge_path=Path("D:/app/EasyCon/EasyConBridge.exe"),
        last_port="COM9",
        mock_enabled=True,
        recent_scripts=(Path("script/BDSP测种.txt"),),
        script_parameters={"script/玫瑰公园.txt": {"_闪帧": "123", "_等待时间": "8"}},
        keep_log_lines=300,
    )

    save_config(config, config_path)

    assert load_config(config_path) == config
