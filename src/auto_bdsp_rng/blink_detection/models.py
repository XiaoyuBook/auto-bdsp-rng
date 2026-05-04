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
class ProjectXsSeedResult:
    """Normalized seed output from Project_Xs recovery."""

    state: SeedState32
    observation: BlinkObservation

