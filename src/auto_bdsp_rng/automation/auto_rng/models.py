from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class AutoRngPhase(str, Enum):
    IDLE = "Idle"
    CAPTURE_SEED = "CaptureSeed"
    SEARCH_TARGET = "SearchTarget"
    RUN_SEED_SCRIPT = "RunSeedScript"
    DECIDE_ADVANCE = "DecideAdvance"
    RUN_ADVANCE_SCRIPT = "RunAdvanceScript"
    REIDENTIFY = "Reidentify"
    FINAL_CALIBRATE = "FinalCalibrate"
    RUN_HIT_SCRIPT = "RunHitScript"
    LOOP_CHECK = "LoopCheck"
    COMPLETED = "Completed"
    FAILED = "Failed"


class AutoRngDecisionKind(str, Enum):
    RUN_SEED_SCRIPT = "run_seed_script"
    RUN_ADVANCE_SCRIPT = "run_advance_script"
    FINAL_CALIBRATE = "final_calibrate"
    RUN_HIT_SCRIPT = "run_hit_script"
    REIDENTIFY = "reidentify"
    CAPTURE_SEED = "capture_seed"
    TARGET_MISSED = "target_missed"
    TARGET_TOO_CLOSE = "target_too_close"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass(frozen=True)
class AutoRngTarget:
    raw_target_advances: int
    state: Any | None = None
    label: str = ""

    @classmethod
    def from_state(cls, state: Any, label: str = "") -> "AutoRngTarget":
        return cls(raw_target_advances=int(getattr(state, "advances")), state=state, label=label)


@dataclass(frozen=True)
class AutoRngConfig:
    script_dir: Path
    seed_script_path: Path | None = None
    advance_script_path: Path | None = None
    hit_script_path: Path | None = None
    fixed_delay: int = 100
    fixed_flash_frames: int = 60
    max_wait_frames: int = 300
    reseed_threshold_frames: int = 990_000
    min_final_flash_frames: int = 5
    loop_mode: str = "single"
    loop_count: int = 1
    max_advances: int = 100_000
    shiny_threshold_seconds: float | None = None


@dataclass(frozen=True)
class AutoRngSeedResult:
    seed: Any
    current_advances: int = 0
    npc: int = 0
    seed_text: str = ""
    measured_at: float | None = None


@dataclass(frozen=True)
class AutoRngDecision:
    kind: AutoRngDecisionKind
    phase: AutoRngPhase
    target: AutoRngTarget | None = None
    raw_target_advances: int | None = None
    fixed_delay: int | None = None
    trigger_advances: int | None = None
    current_advances: int | None = None
    remaining_to_trigger: int | None = None
    requested_advances: int | None = None
    flash_frames: int | None = None
    message: str = ""


@dataclass(frozen=True)
class ShinyCheckResult:
    is_shiny: bool
    interval_seconds: float | None = None
    first_event_text: str = "出现了"
    second_event_text: str = "去吧"


@dataclass(frozen=True)
class AutoRngProgress:
    phase: AutoRngPhase = AutoRngPhase.IDLE
    loop_index: int = 0
    seed_text: str = ""
    locked_target: AutoRngTarget | None = None
    raw_target_advances: int | None = None
    fixed_delay: int | None = None
    trigger_advances: int | None = None
    current_advances: int | None = None
    remaining_to_trigger: int | None = None
    final_flash_frames: int | None = None
    last_script_path: Path | None = None
    log_message: str = ""
