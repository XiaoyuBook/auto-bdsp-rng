from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


U32_MAX = 0xFFFFFFFF


@dataclass(frozen=True)
class SeedState32:
    """Four 32-bit Xorshift state words produced by Project_Xs."""

    s0: int
    s1: int
    s2: int
    s3: int

    def __post_init__(self) -> None:
        for name, value in zip(("s0", "s1", "s2", "s3"), self.words):
            if not 0 <= value <= U32_MAX:
                raise ValueError(f"{name} must be a 32-bit unsigned integer")

    @classmethod
    def from_words(cls, words: Sequence[int]) -> "SeedState32":
        if len(words) != 4:
            raise ValueError("Project_Xs seed state must contain exactly four words")
        return cls(*(int(word) for word in words))

    @classmethod
    def from_hex_words(cls, words: Sequence[str]) -> "SeedState32":
        if len(words) != 4:
            raise ValueError("Seed[0-3] input must contain exactly four hex words")
        try:
            return cls.from_words([int(word, 16) for word in words])
        except ValueError as exc:
            raise ValueError("Seed[0-3] input must be hexadecimal") from exc

    @property
    def words(self) -> tuple[int, int, int, int]:
        return (self.s0, self.s1, self.s2, self.s3)

    @property
    def seed64_pair(self) -> tuple[int, int]:
        return ((self.s0 << 32) | self.s1, (self.s2 << 32) | self.s3)

    def format_words(self) -> tuple[str, str, str, str]:
        return tuple(f"{word:08X}" for word in self.words)

    def format_seed64_pair(self) -> tuple[str, str]:
        seed0, seed1 = self.seed64_pair
        return (f"{seed0:016X}", f"{seed1:016X}")


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
            "npc": self.npc,
            "pokemon_npc": self.pokemon_npc,
            "timeline_npc": self.timeline_npc,
            "display_percent": self.display_percent,
        }
