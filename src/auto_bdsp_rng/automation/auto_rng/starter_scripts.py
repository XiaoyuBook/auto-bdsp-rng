"""Bundled scripts for starter automation, independent of manual selections."""
from dataclasses import replace
from pathlib import Path

from .models import AutoRngConfig, AutoRngPhase
from .scripts import AutoScriptError
from .starter_flow import STARTER_SLOTS, validate_starter_delay


def starter_script_paths() -> tuple[Path, Path]:
    assets = Path(__file__).resolve().parent / "starter_assets"
    return assets / "seed.ecs", assets / "reverse.ecs"


def validate_starter_config(config: AutoRngConfig) -> None:
    if config.target_species not in STARTER_SLOTS:
        raise ValueError("御三家全自动仅支持草苗龟、小火焰猴或波加曼")
    if config.start_phase not in (AutoRngPhase.RUN_SEED_SCRIPT, AutoRngPhase.CAPTURE_SEED):
        raise ValueError("御三家全自动须从测种或捕获 Seed 开始，停留在“怎么回事？刚才那两人……”")
    if not config.shiny_threshold_seconds or config.shiny_threshold_seconds <= 0:
        raise ValueError("御三家全自动需要大于 0 的闪光阈值")
    if config.delay_strategy == "fixed":
        validate_starter_delay(config.fixed_delay)
    for path in (config.seed_script_path, config.reverse_script_path):
        if path is None:
            raise AutoScriptError("御三家内置脚本未配置")
        try:
            if not path.read_text(encoding="utf-8").strip():
                raise AutoScriptError(f"御三家内置脚本为空：{path.name}")
        except (OSError, UnicodeError) as error:
            raise AutoScriptError(f"无法读取御三家内置脚本：{path.name}") from error


def bind_starter_config(config: AutoRngConfig) -> AutoRngConfig:
    seed, reverse = starter_script_paths()
    result = replace(config, starter_automation=True, seed_script_path=seed,
                     reverse_script_path=reverse, advance_script_path=None,
                     hit_script_path=None, exit_script_path=None, escape_script_path=None,
                     auto_reverse=True, escape_continue=False, sync_mode=0, sync_nature="")
    validate_starter_config(result)
    return result
