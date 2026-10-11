"""Fixed configuration and bundled scripts for starter automation."""
from dataclasses import replace
from pathlib import Path

from auto_bdsp_rng.blink_detection import ProjectXsTrackingConfig
from auto_bdsp_rng.resources import resource_path

from .models import AutoRngConfig, AutoRngPhase
from .scripts import AutoScriptError
from .starter_flow import STARTER_SLOTS, STARTER_TIMING, validate_starter_delay


def starter_seed_config_path() -> Path:
    return resource_path("third_party", "Project_Xs_CHN", "configs", "config_starter.json")


def bind_starter_tracking_config(config: ProjectXsTrackingConfig) -> ProjectXsTrackingConfig:
    """Fix the clock parameters while retaining the calibrated eye ROI/threshold."""
    return replace(
        config, white_delay=STARTER_TIMING["timeDelay"],
        advance_delay=STARTER_TIMING["advanceDelay"],
        advance_delay_2=STARTER_TIMING["advanceDelay2"],
        npc=STARTER_TIMING["npc"], timeline_npc=STARTER_TIMING["timelineNpc"],
        pokemon_npc=STARTER_TIMING["pokemonNpc"], reidentify_1_pk_npc=False,
    )


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
    result = replace(config, starter_automation=True, seed_config_path=str(starter_seed_config_path()), seed_script_path=seed,
                     reverse_script_path=reverse, advance_script_path=None,
                     hit_script_path=None, exit_script_path=None, escape_script_path=None,
                     auto_reverse=True, escape_continue=False, sync_mode=0, sync_nature="")
    validate_starter_config(result)
    return result
