"""EasyCon / ezcon integration boundaries."""

from auto_bdsp_rng.automation.easycon.backend import EasyConBackend
from auto_bdsp_rng.automation.easycon.bridge_backend import BridgeEasyConBackend, BridgeProtocolError
from auto_bdsp_rng.automation.easycon.cli_backend import (
    CLI_NOT_FINAL_NOTICE,
    CLI_RESET_NOTICE,
    CLI_TRANSITION_NOTICE,
    CliEasyConBackend,
    classify_cli_failure,
    cli_connection_notice,
    extract_compile_error_line,
)
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
    detect_newline_style,
    generate_script_file,
    parse_script_parameters,
    prune_generated_scripts,
    scan_builtin_scripts,
)

__all__ = [
    "CliEasyConBackend",
    "BridgeEasyConBackend",
    "BridgeProtocolError",
    "CLI_NOT_FINAL_NOTICE",
    "CLI_RESET_NOTICE",
    "CLI_TRANSITION_NOTICE",
    "EasyConBackend",
    "EasyConConfig",
    "EasyConInstallation",
    "EasyConLogEntry",
    "EasyConRunResult",
    "EasyConRunTask",
    "EasyConStatus",
    "ScriptParameter",
    "apply_parameter_values",
    "classify_cli_failure",
    "cli_connection_notice",
    "detect_newline_style",
    "discover_ezcon",
    "extract_compile_error_line",
    "generate_script_file",
    "list_ports",
    "load_config",
    "parse_script_parameters",
    "prune_generated_scripts",
    "save_config",
    "scan_builtin_scripts",
]
