from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Sequence

from auto_bdsp_rng.data import StaticEncounterRecord
from auto_bdsp_rng.gen8_static import Lead, Profile8, State8, StateFilter, StaticGenerator8
from auto_bdsp_rng.rng_core import SeedPair64


@dataclass(frozen=True)
class StaticSearchCriteria:
    seed: SeedPair64
    profile: Profile8
    record: StaticEncounterRecord
    state_filter: StateFilter
    initial_advances: int
    max_advances: int
    offset: int
    lead: Lead | int
    shiny_mode: str = "any"


@dataclass(frozen=True)
class StaticSearchTarget:
    state_filter: StateFilter
    shiny_mode: str = "any"


def _filter_for_shiny_mode(state_filter: StateFilter, shiny_mode: str) -> StateFilter:
    if shiny_mode != "none":
        return state_filter
    return replace(state_filter, shiny=0)


def generate_static_candidates(criteria: StaticSearchCriteria) -> list[State8]:
    template = replace(criteria.record.template, version=criteria.profile.version)
    generator = StaticGenerator8(
        criteria.initial_advances,
        criteria.max_advances,
        criteria.offset,
        criteria.lead,
        template,
        criteria.profile,
        criteria.state_filter,
    )
    states = generator.generate(criteria.seed)
    if criteria.shiny_mode == "none":
        states = [state for state in states if state.shiny == 0]
    return sorted(states, key=lambda state: state.advances)


def generate_static_candidates_multi(
    criteria: StaticSearchCriteria,
    targets: Sequence[StaticSearchTarget],
) -> list[State8]:
    if not targets:
        return []
    if len(targets) == 1:
        target = targets[0]
        return generate_static_candidates(
            replace(criteria, state_filter=target.state_filter, shiny_mode=target.shiny_mode)
        )

    template = replace(criteria.record.template, version=criteria.profile.version)
    filters = [
        _filter_for_shiny_mode(target.state_filter, target.shiny_mode)
        for target in targets
    ]
    generator = StaticGenerator8(
        criteria.initial_advances,
        criteria.max_advances,
        criteria.offset,
        criteria.lead,
        template,
        criteria.profile,
        StateFilter(skip=True),
    )
    states = generator.generate_matching_any(filters, criteria.seed)
    unique: dict[int, State8] = {}
    for state in states:
        unique.setdefault(state.advances, state)
    return sorted(unique.values(), key=lambda state: state.advances)
