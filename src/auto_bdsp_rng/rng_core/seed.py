from __future__ import annotations

from dataclasses import dataclass
from string import hexdigits
from typing import Sequence


U32_MAX = 0xFFFFFFFF
U64_MAX = 0xFFFFFFFFFFFFFFFF


def _parse_hex(value: str, *, bits: int, label: str) -> int:
    width = bits // 4
    text = value.strip()
    if text.lower().startswith("0x"):
        text = text[2:]
    if not text:
        raise ValueError(f"{label} must not be empty")
    if len(text) > width:
        raise ValueError(f"{label} must fit in {width} hexadecimal digits")
    if any(char not in hexdigits for char in text):
        raise ValueError(f"{label} must be hexadecimal")
    return int(text, 16)


@dataclass(frozen=True)
class SeedPair64:
    """Two 64-bit seeds consumed by BDSP Gen 8 static generation."""

    seed0: int
    seed1: int

    def __post_init__(self) -> None:
        for name, value in zip(("seed0", "seed1"), self.seeds):
            if not 0 <= value <= U64_MAX:
                raise ValueError(f"{name} must be a 64-bit unsigned integer")

    @classmethod
    def from_seeds(cls, seeds: Sequence[int]) -> "SeedPair64":
        if len(seeds) != 2:
            raise ValueError("Seed[0-1] input must contain exactly two seeds")
        return cls(*(int(seed) for seed in seeds))

    @classmethod
    def from_hex_words(cls, words: Sequence[str]) -> "SeedPair64":
        if len(words) != 2:
            raise ValueError("Seed[0-1] input must contain exactly two hex words")
        return cls.from_seeds(
            [
                _parse_hex(word, bits=64, label=f"Seed[{index}]")
                for index, word in enumerate(words)
            ]
        )

    @classmethod
    def from_state32(cls, state: "SeedState32") -> "SeedPair64":
        return cls((state.s0 << 32) | state.s1, (state.s2 << 32) | state.s3)

    @property
    def seeds(self) -> tuple[int, int]:
        return (self.seed0, self.seed1)

    def to_state32(self) -> "SeedState32":
        return SeedState32(
            (self.seed0 >> 32) & U32_MAX,
            self.seed0 & U32_MAX,
            (self.seed1 >> 32) & U32_MAX,
            self.seed1 & U32_MAX,
        )

    def format_seeds(self) -> tuple[str, str]:
        return (f"{self.seed0:016X}", f"{self.seed1:016X}")

    def as_dict(self) -> dict[str, object]:
        return {
            "seed_0_1": list(self.format_seeds()),
            "seed64_pair": list(self.seeds),
        }


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
            raise ValueError("Seed[0-3] input must contain exactly four words")
        return cls(*(int(word) for word in words))

    @classmethod
    def from_hex_words(cls, words: Sequence[str]) -> "SeedState32":
        if len(words) != 4:
            raise ValueError("Seed[0-3] input must contain exactly four hex words")
        return cls.from_words(
            [
                _parse_hex(word, bits=32, label=f"S{index}")
                for index, word in enumerate(words)
            ]
        )

    @classmethod
    def from_seed_pair64(cls, seed_pair: SeedPair64) -> "SeedState32":
        return seed_pair.to_state32()

    @property
    def words(self) -> tuple[int, int, int, int]:
        return (self.s0, self.s1, self.s2, self.s3)

    @property
    def seed64_pair(self) -> tuple[int, int]:
        return self.to_seed_pair64().seeds

    def to_seed_pair64(self) -> SeedPair64:
        return SeedPair64.from_state32(self)

    def format_words(self) -> tuple[str, str, str, str]:
        return tuple(f"{word:08X}" for word in self.words)

    def format_seed64_pair(self) -> tuple[str, str]:
        return self.to_seed_pair64().format_seeds()

    def as_dict(self) -> dict[str, object]:
        return {
            "seed_0_3": list(self.format_words()),
            "state_words": list(self.words),
            **self.to_seed_pair64().as_dict(),
        }
