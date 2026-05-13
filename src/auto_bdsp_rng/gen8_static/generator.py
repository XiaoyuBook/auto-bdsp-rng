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
    hidden_power,
    is_shiny,
    validate_seed64,
)
from auto_bdsp_rng.rng_core.generators import BDSPXorshift, RNGList, XoroshiroBDSP
from auto_bdsp_rng.rng_core.seed import SeedPair64, U32_MAX

try:
    from auto_bdsp_rng.rng_core._native import generate_static as _native_generate
    _HAS_NATIVE = True
except ImportError:
    _HAS_NATIVE = False


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

    def _generate_native(self, seed0: int, seed1: int, roamer: bool) -> list[State8]:
        """使用 C++ 原生扩展生成（高速路径），输出与 Python 完全一致。"""
        sf = self.state_filter
        template = self.template
        profile = self.profile
        args = (
            seed0, seed1,
            self.initial_advances, self.max_advances, self.offset,
            int(self.lead), roamer,
            int(template.shiny), template.fateful,
            template.iv_count, template.ability,
            template.info.gender_ratio if not roamer else 0,
            template.info.ability_count,
            profile.tid, profile.sid,
            sf.skip, sf.ability, sf.gender, sf.shiny,
            sf.height_min, sf.height_max, sf.weight_min, sf.weight_max,
            tuple(sf.iv_min), tuple(sf.iv_max),
            tuple(sf.natures), tuple(sf.hidden_powers),
        )
        try:
            results_raw = _native_generate(*args[:15], template.level, *args[15:])
        except TypeError:
            results_raw = _native_generate(*args)
        return [
            State8(
                advances=r[0], ec=r[1], sidtid=r[2], pid=r[3],
                ivs=tuple(r[4]),
                ability=r[5], gender=r[6], level=template.level,
                nature=r[8], shiny=r[9], height=r[10], weight=r[11],
            )
            for r in results_raw
        ]

    def generate(self, seed0: int | SeedPair64, seed1: int | None = None) -> list[State8]:
        if isinstance(seed0, SeedPair64):
            if seed1 is not None:
                raise ValueError("seed1 must be omitted when seed0 is a SeedPair64")
            seed0, seed1 = seed0.seeds
        if seed1 is None:
            raise ValueError("seed1 is required")
        seed0 = validate_seed64(seed0, "seed0")
        seed1 = validate_seed64(seed1, "seed1")

        if _HAS_NATIVE:
            return self._generate_native(seed0, seed1, self.template.roamer)

        if self.template.roamer:
            return self.generate_roamer(seed0, seed1)
        return self.generate_non_roamer(seed0, seed1)

    def generate_non_roamer(self, seed0: int, seed1: int) -> list[State8]:
        rng = BDSPXorshift.from_seed_pair64(SeedPair64(seed0, seed1))
        rng.advance(self.initial_advances + self.offset)
        rng_list: RNGList[int] = RNGList(rng, size=32, generate=_gen_static_ec)
        sf = self.state_filter

        states: list[State8] = []
        for count in range(self.max_advances + 1):
            rng_list.advance_state()
            ec = rng_list.next()
            sidtid = rng_list.next()
            pid = rng_list.next()
            shiny, pid = _apply_non_roamer_shiny(self.template, self.profile, sidtid, pid)

            # IV
            ivs: list[int] = [255] * 6
            fixed = 0
            while fixed < self.template.iv_count:
                idx = _next_mod(rng_list, 6)
                if ivs[idx] == 255:
                    ivs[idx] = 31
                    fixed += 1
            for iv_idx in range(6):
                if ivs[iv_idx] == 255:
                    ivs[iv_idx] = _next_mod(rng_list, 32)

            ability = _generate_ability(rng_list, self.template)
            gender = _generate_gender(rng_list, self.template, self.lead)
            nature = _generate_nature(rng_list, self.lead)
            height, weight = _generate_height_weight(rng_list)

            # 统一过滤（与 PokeFinder 一致）
            if not sf.skip:
                if sf.ability != 255 and sf.ability != ability:
                    continue
                if sf.gender != 255 and sf.gender != gender:
                    continue
                if sf.shiny != 255 and not (sf.shiny & shiny):
                    continue
                if not sf.natures[nature]:
                    continue
                if not sf.hidden_powers[hidden_power(ivs)]:
                    continue
                if height < sf.height_min or height > sf.height_max:
                    continue
                if weight < sf.weight_min or weight > sf.weight_max:
                    continue
                if not all(sf.iv_min[i] <= ivs[i] <= sf.iv_max[i] for i in range(6)):
                    continue

            states.append(State8(
                advances=self.initial_advances + count,
                ec=ec, sidtid=sidtid, pid=pid, ivs=tuple(ivs),
                ability=ability, gender=gender, level=self.template.level,
                nature=nature, shiny=shiny, height=height, weight=weight,
            ))
        return states

    def generate_roamer(self, seed0: int, seed1: int) -> list[State8]:
        import time as _time
        gender = GENDER_FEMALE if self.template.species == 488 else GENDER_GENDERLESS
        roamer = BDSPXorshift.from_seed_pair64(SeedPair64(seed0, seed1))
        roamer.advance(self.initial_advances + self.offset)
        sf = self.state_filter
        need_shiny = sf.shiny != 255

        states: list[State8] = []
        _yield_every = 50000
        for count in range(self.max_advances + 1):
            if count % _yield_every == 0:
                _time.sleep(0)
            ec = _gen_static_ec(roamer)
            rng = XoroshiroBDSP(ec)
            sidtid = rng.next_uint(U32_MAX)
            pid = rng.next_uint(U32_MAX)
            shiny, pid = _apply_roamer_shiny(self.profile, sidtid, pid)

            if need_shiny and not (sf.shiny & shiny):
                continue

            ivs = [255] * 6
            fixed = 0
            while fixed < 3:
                idx = _next_mod(rng, 6)
                if ivs[idx] == 255:
                    ivs[idx] = 31
                    fixed += 1
            for iv_idx in range(6):
                if ivs[iv_idx] == 255:
                    ivs[iv_idx] = _next_mod(rng, 32)

            ability = rng.next_uint(2)
            nature = _generate_nature(rng, self.lead)
            height, weight = _generate_height_weight(rng)

            if sf.quick_reject(shiny, ability, gender, nature, height, weight):
                continue
            if not all(sf.iv_min[i] <= ivs[i] <= sf.iv_max[i] for i in range(6)):
                continue
            if not sf.hidden_powers[hidden_power(ivs)]:
                continue

            state = State8(
                advances=self.initial_advances + count,
                ec=ec, sidtid=sidtid, pid=pid, ivs=tuple(ivs),
                ability=ability, gender=gender, level=self.template.level,
                nature=nature, shiny=shiny, height=height, weight=weight,
            )
            states.append(state)
        return states
