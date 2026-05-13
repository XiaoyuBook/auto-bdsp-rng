from __future__ import annotations

from auto_bdsp_rng.automation.auto_rng.search import (
    StaticSearchCriteria,
    StaticSearchTarget,
    generate_static_candidates,
    generate_static_candidates_multi,
)
from auto_bdsp_rng.data import get_static_encounters
from auto_bdsp_rng.gen8_static import Lead, Profile8, State8, StateFilter
from auto_bdsp_rng.rng_core import SeedPair64


def _criteria(max_advances: int = 20) -> StaticSearchCriteria:
    record = next(r for r in get_static_encounters() if r.description == "Shaymin")
    return StaticSearchCriteria(
        seed=SeedPair64(0x123456789ABCDEF0, 0x1111111122222222),
        profile=Profile8(name="test", tid=12345, sid=54321),
        record=record,
        state_filter=StateFilter(),
        initial_advances=0,
        max_advances=max_advances,
        offset=0,
        lead=Lead.NONE,
        shiny_mode="any",
    )


def test_generate_static_candidates_multi_matches_any_target_and_sorts_lowest_frame(monkeypatch):
    base = _criteria(max_advances=9)
    captured_filters: list[StateFilter] = []

    def fake_generate_matching_any(self, filters, seed):
        captured_filters.extend(filters)
        return [
            State8(advances=8, ec=1, sidtid=1, pid=1, ivs=(0, 0, 0, 0, 0, 0), ability=0, gender=2, level=30, nature=0, shiny=2, height=0, weight=10),
            State8(advances=3, ec=2, sidtid=2, pid=2, ivs=(0, 0, 0, 0, 0, 0), ability=0, gender=2, level=30, nature=0, shiny=2, height=255, weight=10),
        ]

    monkeypatch.setattr(
        "auto_bdsp_rng.automation.auto_rng.search.StaticGenerator8.generate_matching_any",
        fake_generate_matching_any,
    )

    states = generate_static_candidates_multi(
        base,
        [
            StaticSearchTarget(StateFilter(height_min=0, height_max=0, shiny=2), "square"),
            StaticSearchTarget(StateFilter(height_min=255, height_max=255, shiny=2), "square"),
        ],
    )

    assert [state.advances for state in states] == [3, 8]
    assert [(sf.height_min, sf.height_max, sf.shiny) for sf in captured_filters] == [(0, 0, 2), (255, 255, 2)]


def test_generate_static_candidates_multi_uses_single_target_compatibility():
    base = _criteria(max_advances=2)

    multi = generate_static_candidates_multi(
        base,
        [StaticSearchTarget(base.state_filter, base.shiny_mode)],
    )
    single = generate_static_candidates(base)

    assert multi == single
