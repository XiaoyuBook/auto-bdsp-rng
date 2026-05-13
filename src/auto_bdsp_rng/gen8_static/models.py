from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Sequence

from auto_bdsp_rng.rng_core.seed import U32_MAX, U64_MAX


class Lead(IntEnum):
    SYNCHRONIZE_START = 0
    SYNCHRONIZE_END = 24
    CUTE_CHARM_F = 25
    CUTE_CHARM_M = 26
    NONE = 255


class Shiny(IntEnum):
    RANDOM = 0
    NEVER = 1
    ALWAYS = 2
    STAR = 3
    SQUARE = 4
    STATIC = 5


GENDER_MALE = 0
GENDER_FEMALE = 1
GENDER_GENDERLESS = 2
GENDER_RATIO_MALE_ONLY = 0
GENDER_RATIO_FEMALE_ONLY = 254
GENDER_RATIO_GENDERLESS = 255


@dataclass(frozen=True)
class PersonalInfo8:
    """Minimal species metadata needed by the Gen 8 static generator."""

    gender_ratio: int = GENDER_RATIO_GENDERLESS
    ability_count: int = 2

    def __post_init__(self) -> None:
        if not 0 <= self.gender_ratio <= 255:
            raise ValueError("gender_ratio must be an unsigned 8-bit value")
        if not 1 <= self.ability_count <= 3:
            raise ValueError("ability_count must be between 1 and 3")


@dataclass(frozen=True)
class StaticTemplate8:
    """BDSP static encounter template."""

    species: int
    form: int = 0
    shiny: Shiny = Shiny.RANDOM
    ability: int = 255
    gender: int = 255
    iv_count: int = 0
    level: int = 1
    fateful: bool = False
    roamer: bool = False
    info: PersonalInfo8 = field(default_factory=PersonalInfo8)
    version: str = "BDSP"

    def __post_init__(self) -> None:
        if self.shiny == Shiny.ALWAYS:
            raise ValueError("BDSP static generation expects STAR, SQUARE, STATIC, RANDOM, or NEVER shiny modes")
        if not 0 <= self.species <= 0xFFFF:
            raise ValueError("species must be an unsigned 16-bit value")
        if not 0 <= self.form <= 0xFF:
            raise ValueError("form must be an unsigned 8-bit value")
        if not 0 <= self.ability <= 0xFF:
            raise ValueError("ability must be an unsigned 8-bit value")
        if not 0 <= self.gender <= 0xFF:
            raise ValueError("gender must be an unsigned 8-bit value")
        if not 0 <= self.iv_count <= 6:
            raise ValueError("iv_count must be between 0 and 6")
        if not 1 <= self.level <= 100:
            raise ValueError("level must be between 1 and 100")


@dataclass(frozen=True)
class Profile8:
    """Player profile values used to derive the trainer shiny value."""

    name: str = "-"
    version: str = "BDSP"
    tid: int = 0
    sid: int = 0
    national_dex: bool = False
    shiny_charm: bool = False
    oval_charm: bool = False

    def __post_init__(self) -> None:
        if not 0 <= self.tid <= 0xFFFF:
            raise ValueError("tid must be an unsigned 16-bit value")
        if not 0 <= self.sid <= 0xFFFF:
            raise ValueError("sid must be an unsigned 16-bit value")

    @property
    def tsv(self) -> int:
        return self.tid ^ self.sid


@dataclass(frozen=True)
class State8:
    """Generated BDSP static encounter result."""

    advances: int
    ec: int
    sidtid: int
    pid: int
    ivs: tuple[int, int, int, int, int, int]
    ability: int
    gender: int
    level: int
    nature: int
    shiny: int
    height: int
    weight: int

    def __post_init__(self) -> None:
        if self.advances < 0:
            raise ValueError("advances must be non-negative")
        for name in ("ec", "sidtid", "pid"):
            if not 0 <= getattr(self, name) <= U32_MAX:
                raise ValueError(f"{name} must be an unsigned 32-bit value")
        if len(self.ivs) != 6:
            raise ValueError("ivs must contain exactly six values")
        if any(iv < 0 or iv > 31 for iv in self.ivs):
            raise ValueError("ivs must be between 0 and 31")
        for name in ("ability", "gender", "level", "nature", "shiny", "height", "weight"):
            if not 0 <= getattr(self, name) <= 0xFF:
                raise ValueError(f"{name} must be an unsigned 8-bit value")

    def as_dict(self) -> dict[str, object]:
        return {
            "advances": self.advances,
            "ec": self.ec,
            "sidtid": self.sidtid,
            "pid": self.pid,
            "ivs": list(self.ivs),
            "ability": self.ability,
            "gender": self.gender,
            "level": self.level,
            "nature": self.nature,
            "shiny": self.shiny,
            "height": self.height,
            "weight": self.weight,
        }


@dataclass(frozen=True)
class StateFilter:
    """PokeFinder-style state filter for Gen 8 static results."""

    gender: int = 255
    ability: int = 255
    shiny: int = 255
    height_min: int = 0
    height_max: int = 255
    weight_min: int = 0
    weight_max: int = 255
    skip: bool = False
    iv_min: tuple[int, int, int, int, int, int] = (0, 0, 0, 0, 0, 0)
    iv_max: tuple[int, int, int, int, int, int] = (31, 31, 31, 31, 31, 31)
    natures: tuple[bool, ...] = (True,) * 25
    hidden_powers: tuple[bool, ...] = (True,) * 16

    @classmethod
    def from_iv_ranges(
        cls,
        iv_min: Sequence[int],
        iv_max: Sequence[int],
        **kwargs: object,
    ) -> "StateFilter":
        return cls(iv_min=tuple(iv_min), iv_max=tuple(iv_max), **kwargs)

    def __post_init__(self) -> None:
        if len(self.iv_min) != 6 or len(self.iv_max) != 6:
            raise ValueError("iv filters must contain exactly six values")
        if any(iv < 0 or iv > 31 for iv in (*self.iv_min, *self.iv_max)):
            raise ValueError("iv filters must be between 0 and 31")
        if len(self.natures) != 25:
            raise ValueError("natures filter must contain exactly 25 flags")
        if len(self.hidden_powers) != 16:
            raise ValueError("hidden_powers filter must contain exactly 16 flags")

    def compare_state(self, state: State8) -> bool:
        if self.skip:
            return True
        if self.ability != 255 and self.ability != state.ability:
            return False
        if self.gender != 255 and self.gender != state.gender:
            return False
        if self.shiny == 0 and state.shiny != 0:
            return False
        if self.shiny not in (0, 255) and not (self.shiny & state.shiny):
            return False
        if not self.natures[state.nature]:
            return False
        if not self.hidden_powers[hidden_power(state.ivs)]:
            return False
        if state.height < self.height_min or state.height > self.height_max:
            return False
        if state.weight < self.weight_min or state.weight > self.weight_max:
            return False
        return all(min_iv <= iv <= max_iv for iv, min_iv, max_iv in zip(state.ivs, self.iv_min, self.iv_max))

    def quick_reject(self, shiny: int, ability: int, gender: int, nature: int, height: int, weight: int) -> bool:
        """快速预筛选：True=不匹配直接跳过，避免创建 State8 对象。"""
        if self.skip:
            return False
        if self.ability != 255 and self.ability != ability:
            return True
        if self.gender != 255 and self.gender != gender:
            return True
        if self.shiny == 0 and shiny != 0:
            return True
        if self.shiny not in (0, 255) and not (self.shiny & shiny):
            return True
        if not self.natures[nature]:
            return True
        if height < self.height_min or height > self.height_max:
            return True
        if weight < self.weight_min or weight > self.weight_max:
            return True
        return False


def hidden_power(ivs: Sequence[int]) -> int:
    if len(ivs) != 6:
        raise ValueError("ivs must contain exactly six values")
    bits = sum((iv & 1) << index for index, iv in enumerate(ivs))
    return (bits * 15) // 63


def get_shiny(pid: int, tsv: int) -> int:
    pid &= U32_MAX
    tsv &= 0xFFFF
    psv = ((pid >> 16) ^ (pid & 0xFFFF)) & 0xFFFF
    if tsv == psv:
        return 2
    if (tsv ^ psv) < 16:
        return 1
    return 0


def is_shiny(pid: int, tsv: int) -> bool:
    return bool(get_shiny(pid, tsv))


def validate_seed64(seed: int, label: str) -> int:
    if not 0 <= seed <= U64_MAX:
        raise ValueError(f"{label} must be an unsigned 64-bit value")
    return seed
