"""EasyCon / ezcon integration boundaries."""

from auto_bdsp_rng.automation.easycon.backend import EasyConBackend
from auto_bdsp_rng.automation.easycon.bridge_backend import BridgeEasyConBackend, BridgeProtocolError
from auto_bdsp_rng.automation.easycon.cli_backend import CliEasyConBackend
from auto_bdsp_rng.automation.easycon.discovery import discover_ezcon, list_ports, load_config, save_config
from auto_bdsp_rng.automation.easycon.models import (
    EasyConConfig,
    EasyConInstallation,
    EasyConLogEntry,
    EasyConRunResult,
    EasyConRunTask,
    EasyConStatus,
    ScriptParameter,
)
from auto_bdsp_rng.automation.easycon.scripts import (
    apply_parameter_values,
    generate_script_file,
    parse_script_parameters,
    scan_builtin_scripts,
)

__all__ = [
    "CliEasyConBackend",
    "BridgeEasyConBackend",
    "BridgeProtocolError",
    "EasyConBackend",
    "EasyConConfig",
    "EasyConInstallation",
    "EasyConLogEntry",
    "EasyConRunResult",
    "EasyConRunTask",
    "EasyConStatus",
    "ScriptParameter",
    "apply_parameter_values",
    "discover_ezcon",
    "generate_script_file",
    "list_ports",
    "load_config",
    "parse_script_parameters",
    "save_config",
    "scan_builtin_scripts",
]
