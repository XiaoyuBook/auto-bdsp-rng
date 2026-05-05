from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from auto_bdsp_rng.rng_core import SeedState32


@dataclass(frozen=True)
class BlinkObservation:
    """Raw blink types and rounded intervals captured from Project_Xs logic."""

    blinks: tuple[int, ...]
    intervals: tuple[int, ...]
    offset_time: float = 0.0

    @classmethod
    def from_sequences(
        cls,
        blinks: Sequence[int],
        intervals: Sequence[int],
        offset_time: float = 0.0,
    ) -> "BlinkObservation":
        return cls(tuple(int(blink) for blink in blinks), tuple(int(i) for i in intervals), float(offset_time))


@dataclass(frozen=True)
class PokemonBlinkObservation:
    """Pokemon blink intervals captured for TID/SID style recovery."""

    intervals: tuple[float, ...]

    @classmethod
    def from_sequence(cls, intervals: Sequence[float]) -> "PokemonBlinkObservation":
        return cls(tuple(float(interval) for interval in intervals))


@dataclass(frozen=True)
class BlinkCaptureConfig:
    """Configuration needed to call Project_Xs blink tracking without its Tk UI."""

    eye_image_path: Path
    roi: tuple[int, int, int, int]
    threshold: float = 0.9
    blink_count: int = 40
    monitor_window: bool = True
    window_prefix: str = "SysDVR-Client [PID "
    crop: tuple[int, int, int, int] | None = None
    camera: int = 0


@dataclass(frozen=True)
class EyePreviewResult:
    """Eye template match result for one preview frame."""

    roi: tuple[int, int, int, int]
    match_score: float
    match_location: tuple[int, int]
    template_size: tuple[int, int]
    threshold: float

    @property
    def matched(self) -> bool:
        return self.match_score >= self.threshold

    def as_dict(self) -> dict[str, object]:
        return {
            "roi": list(self.roi),
            "match_score": self.match_score,
            "match_location": list(self.match_location),
            "template_size": list(self.template_size),
            "threshold": self.threshold,
            "matched": self.matched,
        }


@dataclass(frozen=True)
class AdvanceEvent:
    """One Project_Xs advance tracking event."""

    advance: int
    rand: int

    @property
    def blink_value(self) -> int:
        return self.rand & 0xF

    @property
    def is_blink(self) -> bool:
        return (self.rand & 0b1110) == 0

    def as_dict(self) -> dict[str, object]:
        return {
            "advance": self.advance,
            "rand": f"{self.rand:08X}",
            "blink_value": f"{self.blink_value:X}",
            "is_blink": self.is_blink,
        }


@dataclass(frozen=True)
class TimelineEvent:
    """One planned Project_Xs timeline event."""

    advance: int
    event_type: str
    scheduled_time: float
    rand: int | None = None
    next_interval: float | None = None

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "advance": self.advance,
            "event_type": self.event_type,
            "scheduled_time": self.scheduled_time,
        }
        if self.rand is not None:
            payload["rand"] = f"{self.rand:08X}"
            payload["blink_value"] = f"{self.rand & 0xF:X}"
        if self.next_interval is not None:
            payload["next_interval"] = self.next_interval
        return payload


@dataclass(frozen=True)
class ProjectXsSeedResult:
    """Normalized seed output from Project_Xs recovery."""

    state: SeedState32
    observation: BlinkObservation

    def as_dict(self) -> dict[str, object]:
        return {
            "seed_0_3": list(self.state.format_words()),
            "seed_0_1": list(self.state.format_seed64_pair()),
            "state_words": list(self.state.words),
            "seed64_pair": list(self.state.seed64_pair),
            "blinks": list(self.observation.blinks),
            "intervals": list(self.observation.intervals),
            "offset_time": self.observation.offset_time,
        }


@dataclass(frozen=True)
class ProjectXsReidentifyResult:
    """Project_Xs reidentify output with the matched advance count."""

    state: SeedState32
    observation: BlinkObservation
    advances: int

    def as_dict(self) -> dict[str, object]:
        return {
            "seed_0_3": list(self.state.format_words()),
            "seed_0_1": list(self.state.format_seed64_pair()),
            "state_words": list(self.state.words),
            "seed64_pair": list(self.state.seed64_pair),
            "blinks": list(self.observation.blinks),
            "intervals": list(self.observation.intervals),
            "offset_time": self.observation.offset_time,
            "advances": self.advances,
        }


@dataclass(frozen=True)
class ProjectXsAdvanceResult:
    """Project_Xs Xorshift state after manual advances."""

    state: SeedState32
    advances: int

    def as_dict(self) -> dict[str, object]:
        return {
            "seed_0_3": list(self.state.format_words()),
            "seed_0_1": list(self.state.format_seed64_pair()),
            "state_words": list(self.state.words),
            "seed64_pair": list(self.state.seed64_pair),
            "advances": self.advances,
        }


@dataclass(frozen=True)
class ProjectXsTidSidResult:
    """Project_Xs TID/SID recovery output."""

    state: SeedState32
    observation: PokemonBlinkObservation

    def as_dict(self) -> dict[str, object]:
        return {
            "seed_0_3": list(self.state.format_words()),
            "seed_0_1": list(self.state.format_seed64_pair()),
            "state_words": list(self.state.words),
            "seed64_pair": list(self.state.seed64_pair),
            "pokemon_intervals": list(self.observation.intervals),
        }


@dataclass(frozen=True)
class ProjectXsTrackingConfig:
    """Project_Xs config normalized for this application."""

    source_path: Path
    capture: BlinkCaptureConfig
    white_delay: float = 0.0
    advance_delay: int = 0
    advance_delay_2: int = 0
    npc: int = 0
    pokemon_npc: int = 0
    timeline_npc: int = 0
    display_percent: int = 100

    def as_dict(self) -> dict[str, object]:
        return {
            "source_path": str(self.source_path),
            "eye_image_path": str(self.capture.eye_image_path),
            "roi": list(self.capture.roi),
            "threshold": self.capture.threshold,
            "blink_count": self.capture.blink_count,
            "monitor_window": self.capture.monitor_window,
            "window_prefix": self.capture.window_prefix,
            "crop": None if self.capture.crop is None else list(self.capture.crop),
            "camera": self.capture.camera,
            "white_delay": self.white_delay,
            "advance_delay": self.advance_delay,
            "advance_delay_2": self.advance_delay_2,
            "npc": self.npc,
            "pokemon_npc": self.pokemon_npc,
            "timeline_npc": self.timeline_npc,
            "display_percent": self.display_percent,
        }
