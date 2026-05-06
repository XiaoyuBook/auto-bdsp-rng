from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from auto_bdsp_rng.automation.easycon.models import EasyConConfig, EasyConInstallation


CONFIG_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "auto_bdsp_rng" / "easycon"
CONFIG_PATH = CONFIG_DIR / "config.json"


def load_config(path: Path = CONFIG_PATH) -> EasyConConfig:
    if not path.exists():
        return EasyConConfig()
    payload = json.loads(path.read_text(encoding="utf-8"))
    return EasyConConfig(
        ezcon_path=Path(payload["ezcon_path"]) if payload.get("ezcon_path") else None,
        bridge_path=Path(payload["bridge_path"]) if payload.get("bridge_path") else None,
        last_port=payload.get("last_port") or None,
        mock_enabled=bool(payload.get("mock_enabled", False)),
        recent_scripts=tuple(Path(item) for item in payload.get("recent_scripts", [])),
        script_parameters={
            str(script): {str(name): str(value) for name, value in values.items()}
            for script, values in payload.get("script_parameters", {}).items()
            if isinstance(values, dict)
        },
        keep_generated=int(payload.get("keep_generated", 20)),
    )


def save_config(config: EasyConConfig, path: Path = CONFIG_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ezcon_path": str(config.ezcon_path) if config.ezcon_path else None,
        "bridge_path": str(config.bridge_path) if config.bridge_path else None,
        "last_port": config.last_port,
        "mock_enabled": config.mock_enabled,
        "recent_scripts": [str(item) for item in config.recent_scripts],
        "script_parameters": config.script_parameters,
        "keep_generated": config.keep_generated,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def discover_ezcon(config: EasyConConfig | None = None) -> EasyConInstallation:
    config = config or load_config()
    candidates: list[tuple[str, Path]] = []
    if config.ezcon_path is not None:
        candidates.append(("config", config.ezcon_path))
    easycon_root = os.environ.get("EASYCON_ROOT")
    if easycon_root:
        candidates.append(("EASYCON_ROOT", Path(easycon_root) / "ezcon.exe"))
    path_candidate = shutil.which("ezcon") or shutil.which("ezcon.exe")
    if path_candidate:
        candidates.append(("PATH", Path(path_candidate)))

    first_error: str | None = None
    for source, candidate in candidates:
        if not candidate.exists():
            first_error = f"{candidate} does not exist"
            continue
        version = _read_version(candidate)
        if version.returncode == 0:
            return EasyConInstallation(path=candidate, version=version.stdout.strip(), source=source)
        first_error = version.stderr.strip() or version.stdout.strip() or f"{candidate} --version failed"
    return EasyConInstallation(path=None, source="missing", error=first_error or "ezcon.exe not found")


def list_ports(installation: EasyConInstallation) -> list[str]:
    if installation.path is None:
        return []
    result = subprocess.run(
        [str(installation.path), "port", "-l"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        return []
    return parse_port_list(result.stdout)


def parse_port_list(output: str) -> list[str]:
    ports: list[str] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        token = line.split()[0].rstrip(":,;")
        if token.upper().startswith("COM") and token[3:].isdigit() and token not in ports:
            ports.append(token)
    return ports


def _read_version(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(path), "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
