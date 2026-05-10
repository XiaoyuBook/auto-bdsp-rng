from __future__ import annotations

from dataclasses import dataclass

from auto_bdsp_rng.gen8_static.models import (
    GENDER_FEMALE,
    GENDER_GENDERLESS,
    GENDER_MALE,
    GENDER_RATIO_FEMALE_ONLY,
    GENDER_RATIO_GENDERLESS,
    GENDER_RATIO_MALE_ONLY,
    Lead,
    Profile8,
    Shiny,
    State8,
    StateFilter,
    StaticTemplate8,
    get_shiny,
    is_shiny,
    validate_seed64,
)
from auto_bdsp_rng.rng_core.generators import BDSPXorshift, RNGList, XoroshiroBDSP
from auto_bdsp_rng.rng_core.seed import SeedPair64, U32_MAX


def _gen_static_ec(rng: BDSPXorshift) -> int:
    return ((rng.next() % U32_MAX) + 0x80000000) & U32_MAX


def _is_synchronize(lead: Lead | int) -> bool:
    value = int(lead)
    return int(Lead.SYNCHRONIZE_START) <= value <= int(Lead.SYNCHRONIZE_END)


def _force_shiny(pid: int, target_shiny: int, tsv: int) -> int:
    if get_shiny(pid, tsv) == target_shiny:
        return pid & U32_MAX
    high = (pid & 0xFFFF) ^ (tsv & 0xFFFF) ^ (2 - target_shiny)
    return ((high << 16) | (pid & 0xFFFF)) & U32_MAX


def _force_non_shiny(pid: int, tsv: int) -> int:
    if is_shiny(pid, tsv):
        return (pid ^ 0x10000000) & U32_MAX
    return pid & U32_MAX


def _apply_non_roamer_shiny(template: StaticTemplate8, profile: Profile8, sidtid: int, pid: int) -> tuple[int, int]:
    if template.shiny == Shiny.NEVER:
        return 0, _force_non_shiny(pid, profile.tsv)

    shiny = get_shiny(pid, ((sidtid >> 16) ^ (sidtid & 0xFFFF)) & 0xFFFF)
    if shiny:
        if template.fateful:
            shiny = 2
        return shiny, _force_shiny(pid, shiny, profile.tsv)
    return 0, _force_non_shiny(pid, profile.tsv)


def _apply_roamer_shiny(profile: Profile8, sidtid: int, pid: int) -> tuple[int, int]:
    shiny = get_shiny(pid, ((sidtid >> 16) ^ (sidtid & 0xFFFF)) & 0xFFFF)
    if shiny:
        return shiny, _force_shiny(pid, shiny, profile.tsv)
    return 0, _force_non_shiny(pid, profile.tsv)


def _next_mod(rng: object, maximum: int) -> int:
    if isinstance(rng, RNGList):
        return rng.next_mod(maximum)
    return rng.next_uint(maximum)  # type: ignore[attr-defined, no-any-return]


def _next_unique_fixed_iv_indices(rng: object, fixed_count: int) -> list[int]:
    ivs = [255] * 6
    count = 0
    while count < fixed_count:
        index = _next_mod(rng, 6)
        if ivs[index] == 255:
            ivs[index] = 31
            count += 1
    return ivs


def _fill_random_ivs(rng: object, ivs: list[int]) -> tuple[int, int, int, int, int, int]:
    for index, iv in enumerate(ivs):
        if iv == 255:
            ivs[index] = _next_mod(rng, 32)
    return tuple(ivs)  # type: ignore[return-value]


def _generate_ability(rng: object, template: StaticTemplate8) -> int:
    match template.ability:
        case 0 | 1:
            return template.ability
        case 2:
            _next_mod(rng, U32_MAX + 1)
            return 2
        case _:
            return _next_mod(rng, min(template.info.ability_count, 2))


def _generate_gender(rng: object, template: StaticTemplate8, lead: Lead | int) -> int:
    ratio = template.info.gender_ratio
    match ratio:
        case value if value == GENDER_RATIO_GENDERLESS:
            return GENDER_GENDERLESS
        case value if value == GENDER_RATIO_FEMALE_ONLY:
            return GENDER_FEMALE
        case value if value == GENDER_RATIO_MALE_ONLY:
            return GENDER_MALE
        case _:
            if int(lead) in (int(Lead.CUTE_CHARM_F), int(Lead.CUTE_CHARM_M)) and _next_mod(rng, 3) > 0:
                return GENDER_MALE if int(lead) == int(Lead.CUTE_CHARM_F) else GENDER_FEMALE
            return int(_next_mod(rng, 253) + 1 < ratio)


def _generate_nature(rng: object, lead: Lead | int) -> int:
    if _is_synchronize(lead):
        return int(lead)
    return _next_mod(rng, 25)


def _generate_height_weight(rng: object) -> tuple[int, int]:
    height = _next_mod(rng, 129) + _next_mod(rng, 128)
    weight = _next_mod(rng, 129) + _next_mod(rng, 128)
    return height, weight


@dataclass(frozen=True)
class StaticGenerator8:
    """BDSP Gen 8 static encounter generator."""

    initial_advances: int
    max_advances: int
    offset: int
    lead: Lead | int
    template: StaticTemplate8
    profile: Profile8
    state_filter: StateFilter = StateFilter()

    def __post_init__(self) -> None:
        if self.initial_advances < 0:
            raise ValueError("initial_advances must be non-negative")
        if self.max_advances < 0:
            raise ValueError("max_advances must be non-negative")
        if self.offset < 0:
            raise ValueError("offset must be non-negative")

    def generate(self, seed0: int | SeedPair64, seed1: int | None = None) -> list[State8]:
        if isinstance(seed0, SeedPair64):
            if seed1 is not None:
                raise ValueError("seed1 must be omitted when seed0 is a SeedPair64")
            seed0, seed1 = seed0.seeds
        if seed1 is None:
            raise ValueError("seed1 is required")
        seed0 = validate_seed64(seed0, "seed0")
        seed1 = validate_seed64(seed1, "seed1")
        if self.template.roamer:
            return self.generate_roamer(seed0, seed1)
        return self.generate_non_roamer(seed0, seed1)

    def generate_non_roamer(self, seed0: int, seed1: int) -> list[State8]:
        rng = BDSPXorshift.from_seed_pair64(SeedPair64(seed0, seed1))
        rng.advance(self.initial_advances + self.offset)
        rng_list: RNGList[int] = RNGList(rng, size=32, generate=_gen_static_ec)

        states: list[State8] = []
        for count in range(self.max_advances + 1):
            ec = rng_list.next()
            # sidtid 来自训练师 Profile，不消耗 RNG（对齐 PokeFinder）
            sidtid = (self.profile.sid << 16) | (self.profile.tid & 0xFFFF)
            pid = rng_list.next()
            shiny, pid = _apply_non_roamer_shiny(self.template, self.profile, sidtid, pid)
            ivs = _fill_random_ivs(rng_list, _next_unique_fixed_iv_indices(rng_list, self.template.iv_count))
            ability = _generate_ability(rng_list, self.template)
            gender = _generate_gender(rng_list, self.template, self.lead)
            nature = _generate_nature(rng_list, self.lead)
            height, weight = _generate_height_weight(rng_list)
            state = State8(
                advances=self.initial_advances + count,
                ec=ec,
                sidtid=sidtid,
                pid=pid,
                ivs=ivs,
                ability=ability,
                gender=gender,
                level=self.template.level,
                nature=nature,
                shiny=shiny,
                height=height,
                weight=weight,
            )
            if self.state_filter.compare_state(state):
                states.append(state)
            rng_list.advance_state()
        return states

    def generate_roamer(self, seed0: int, seed1: int) -> list[State8]:
        gender = GENDER_FEMALE if self.template.species == 488 else GENDER_GENDERLESS
        roamer = BDSPXorshift.from_seed_pair64(SeedPair64(seed0, seed1))
        roamer.advance(self.initial_advances + self.offset)

        states: list[State8] = []
        for count in range(self.max_advances + 1):
            ec = _gen_static_ec(roamer)
            rng = XoroshiroBDSP(ec)
            # sidtid 来自训练师 Profile，不消耗 RNG（对齐 PokeFinder）
            sidtid = (self.profile.sid << 16) | (self.profile.tid & 0xFFFF)
            pid = rng.next_uint(U32_MAX)
            shiny, pid = _apply_roamer_shiny(self.profile, sidtid, pid)
            ivs = _fill_random_ivs(rng, _next_unique_fixed_iv_indices(rng, 3))
            ability = rng.next_uint(2)
            nature = _generate_nature(rng, self.lead)
            height, weight = _generate_height_weight(rng)
            state = State8(
                advances=self.initial_advances + count,
                ec=ec,
                sidtid=sidtid,
                pid=pid,
                ivs=ivs,
                ability=ability,
                gender=gender,
                level=self.template.level,
                nature=nature,
                shiny=shiny,
                height=height,
                weight=weight,
            )
            if self.state_filter.compare_state(state):
                states.append(state)
        return states
