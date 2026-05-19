from __future__ import annotations

from dataclasses import dataclass, field

from auto_bdsp_rng.gen8_id.models import IDFilter, IDState8
from auto_bdsp_rng.rng_core.generators import BDSPXorshift, RNGList
from auto_bdsp_rng.rng_core.seed import SeedPair64, U32_MAX


def _gen_sidtid(rng: BDSPXorshift) -> int:
    return ((rng.next() % U32_MAX) + 0x80000000) & U32_MAX


@dataclass(frozen=True)
class IDGenerator8:
    initial_advances: int = 0
    max_advances: int = 100_000
    state_filter: IDFilter = field(default_factory=IDFilter)

    def __post_init__(self) -> None:
        if self.initial_advances < 0:
            raise ValueError("initial_advances must be non-negative")
        if self.max_advances < 0:
            raise ValueError("max_advances must be non-negative")

    def generate(self, seed0: int | SeedPair64, seed1: int | None = None) -> list[IDState8]:
        if isinstance(seed0, SeedPair64):
            if seed1 is not None:
                raise ValueError("seed1 must be omitted when seed0 is a SeedPair64")
            seed0, seed1 = seed0.seeds
        if seed1 is None:
            raise ValueError("seed1 is required")

        rng = BDSPXorshift.from_seed_pair64(SeedPair64(seed0, seed1))
        rng.advance(self.initial_advances)
        rng_list: RNGList[int] = RNGList(rng, size=2, generate=_gen_sidtid)

        states: list[IDState8] = []
        for count in range(self.max_advances):
            sidtid = rng_list.next()
            while sidtid == 0:
                sidtid = rng_list.next()

            tid = sidtid & 0xFFFF
            sid = sidtid >> 16
            state = IDState8(
                advances=self.initial_advances + count,
                tid=tid,
                sid=sid,
                tsv=(tid ^ sid) >> 4,
                display_tid=sidtid % 1_000_000,
            )
            if self.state_filter.compare_state(state):
                states.append(state)
            rng_list.advance_state()
        return states


def generate_ids(
    seed0: int | SeedPair64,
    seed1: int | None = None,
    *,
    initial_advances: int = 0,
    max_advances: int = 100_000,
    state_filter: IDFilter | None = None,
) -> list[IDState8]:
    return IDGenerator8(
        initial_advances=initial_advances,
        max_advances=max_advances,
        state_filter=state_filter or IDFilter(),
    ).generate(seed0, seed1)
