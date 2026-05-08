from __future__ import annotations

from dataclasses import dataclass, replace

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
