from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class AutoRngPhase(str, Enum):
    IDLE = "空闲"
    CAPTURE_SEED = "捕获Seed"
    SEARCH_TARGET = "搜索目标"
    RUN_SEED_SCRIPT = "运行测种脚本"
    DECIDE_ADVANCE = "决策过帧"
    RUN_ADVANCE_SCRIPT = "运行过帧脚本"
    REIDENTIFY = "重新识别位置"
    FINAL_CALIBRATE = "最终校准"
    FINAL_WAIT = "等待触发"
    FINAL_ADJUST = "动态调整闪帧"
    RUN_HIT_SCRIPT = "运行撞闪脚本"
    REVERSE_LOOKUP = "反查个体"
    LOOP_CHECK = "循环检查"
    COMPLETED = "已完成"
    FAILED = "失败"


class AutoRngDecisionKind(str, Enum):
    RUN_SEED_SCRIPT = "run_seed_script"
    RUN_ADVANCE_SCRIPT = "run_advance_script"
    FINAL_CALIBRATE = "final_calibrate"
    FINAL_WAIT = "final_wait"
    FINAL_ADJUST = "final_adjust"
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
    reverse_script_path: Path | None = None
    auto_reverse: bool = False
    reverse_lookup_window: int = 500
    sync_mode: int = 0  # 0=关闭, 1=首位普通精灵, 2=首位同步精灵
    sync_nature: str = ""  # 同步时锁定的性格名称
    fixed_delay: int = 100
    fixed_flash_frames: int = 60
    max_wait_frames: int = 300
    reseed_threshold_frames: int = 990_000
    min_final_flash_frames: int = 5
    loop_mode: str = "single"
    loop_count: int = 1
    max_advances: int = 100_000
    shiny_threshold_seconds: float | None = None
    debug_output: bool = False


@dataclass(frozen=True)
class AutoRngSeedResult:
    seed: Any
    current_advances: int = 0
    npc: int = 0
    seed_text: str = ""
    measured_at: float | None = None
    expected_advances_hint: int | None = None


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
