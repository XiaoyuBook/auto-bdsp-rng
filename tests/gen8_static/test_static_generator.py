from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from auto_bdsp_rng.gen8_static import Lead, PersonalInfo8, Profile8, Shiny, State8, StateFilter, StaticGenerator8, StaticTemplate8
from auto_bdsp_rng.gen8_static import generator as generator_module
from auto_bdsp_rng.rng_core.cancellation import SearchCancellation, SearchCancelled


SEED0 = 1311768467139281697
SEED1 = 9756277977086449272
PROFILE = Profile8("-", "BDSP", tid=12345, sid=54321)


def _core_fields(states):
    return [
        {
            "advances": state.advances,
            "ec": state.ec,
            "pid": state.pid,
            "ivs": list(state.ivs),
            "ability": state.ability,
            "gender": state.gender,
            "level": state.level,
            "nature": state.nature,
            "shiny": state.shiny,
            "height": state.height,
            "weight": state.weight,
        }
        for state in states
    ]


def test_non_roamer_matches_pokefinder_turtwig_sample():
    template = StaticTemplate8(
        species=387,
        shiny=Shiny.NEVER,
        ability=255,
        gender=255,
        iv_count=0,
        level=5,
        info=PersonalInfo8(gender_ratio=31, ability_count=2),
    )
    generator = StaticGenerator8(0, 9, 0, Lead.NONE, template, PROFILE, StateFilter())

    assert _core_fields(generator.generate(SEED0, SEED1)) == [
        {"advances": 0, "ec": 570639824, "pid": 570642538, "ivs": [4, 23, 15, 30, 19, 26], "ability": 0, "gender": 0, "level": 5, "nature": 22, "shiny": 0, "height": 124, "weight": 99},
        {"advances": 1, "ec": 2412930810, "pid": 2452903364, "ivs": [23, 15, 30, 19, 26, 18], "ability": 1, "gender": 0, "level": 5, "nature": 2, "shiny": 0, "height": 96, "weight": 69},
        {"advances": 2, "ec": 570642538, "pid": 715243415, "ivs": [15, 30, 19, 26, 18, 25], "ability": 1, "gender": 0, "level": 5, "nature": 5, "shiny": 0, "height": 99, "weight": 96},
        {"advances": 3, "ec": 2452903364, "pid": 3067672975, "ivs": [30, 19, 26, 18, 25, 27], "ability": 0, "gender": 0, "level": 5, "nature": 16, "shiny": 0, "height": 69, "weight": 147},
        {"advances": 4, "ec": 715243415, "pid": 249593662, "ivs": [19, 26, 18, 25, 27, 16], "ability": 0, "gender": 0, "level": 5, "nature": 9, "shiny": 0, "height": 96, "weight": 103},
        {"advances": 5, "ec": 3067672975, "pid": 3200942419, "ivs": [26, 18, 25, 27, 16, 12], "ability": 0, "gender": 0, "level": 5, "nature": 3, "shiny": 0, "height": 147, "weight": 67},
        {"advances": 6, "ec": 249593662, "pid": 422632474, "ivs": [18, 25, 27, 16, 12, 0], "ability": 1, "gender": 0, "level": 5, "nature": 3, "shiny": 0, "height": 103, "weight": 115},
        {"advances": 7, "ec": 3200942419, "pid": 3906296370, "ivs": [25, 27, 16, 12, 0, 15], "ability": 1, "gender": 1, "level": 5, "nature": 17, "shiny": 0, "height": 67, "weight": 129},
        {"advances": 8, "ec": 422632474, "pid": 1698808217, "ivs": [27, 16, 12, 0, 15, 29], "ability": 0, "gender": 0, "level": 5, "nature": 1, "shiny": 0, "height": 115, "weight": 245},
        {"advances": 9, "ec": 3906296370, "pid": 267060347, "ivs": [16, 12, 0, 15, 29, 20], "ability": 0, "gender": 0, "level": 5, "nature": 11, "shiny": 0, "height": 129, "weight": 95},
    ]


def test_non_roamer_fixed_ivs_match_pokefinder_omanyte_sample():
    template = StaticTemplate8(
        species=138,
        shiny=Shiny.NEVER,
        ability=255,
        gender=255,
        iv_count=3,
        level=1,
        info=PersonalInfo8(gender_ratio=31, ability_count=2),
    )
    generator = StaticGenerator8(0, 1, 0, Lead.NONE, template, PROFILE, StateFilter())

    assert _core_fields(generator.generate(SEED0, SEED1)) == [
        {"advances": 0, "ec": 570639824, "pid": 570642538, "ivs": [30, 31, 31, 19, 26, 31], "ability": 0, "gender": 0, "level": 1, "nature": 22, "shiny": 0, "height": 124, "weight": 99},
        {"advances": 1, "ec": 2412930810, "pid": 2452903364, "ivs": [19, 31, 26, 18, 31, 31], "ability": 1, "gender": 0, "level": 1, "nature": 2, "shiny": 0, "height": 96, "weight": 69},
    ]


@pytest.mark.parametrize("use_native", [False, True])
def test_non_roamer_shaymin_square_sample_matches_pokefinder_ui(monkeypatch, use_native):
    import auto_bdsp_rng.gen8_static.generator as generator_module

    if use_native and not generator_module._HAS_NATIVE:
        pytest.skip("C++ native extension not installed")
    monkeypatch.setattr(generator_module, "_HAS_NATIVE", use_native)

    template = StaticTemplate8(
        species=492,
        shiny=Shiny.RANDOM,
        ability=255,
        gender=255,
        iv_count=3,
        level=30,
        fateful=True,
        info=PersonalInfo8(gender_ratio=255, ability_count=2),
    )
    state_filter = StateFilter(shiny=2)
    generator = StaticGenerator8(0, 14113, 0, Lead.NONE, template, PROFILE, state_filter)

    states_by_advances = {state.advances: state for state in generator.generate(0xF60D851240A516EB, 0xF478CC49CA821A1)}

    state = states_by_advances[14113]
    assert state.ec == 0x38458EDC
    assert state.pid == 0xE4B100B9
    assert state.ivs == (31, 31, 31, 9, 23, 31)
    assert state.ability == 1
    assert state.gender == 2
    assert state.nature == 16
    assert state.shiny == 2
    assert state.height == 165
    assert state.weight == 135


def test_roamer_matches_pokefinder_mesprit_sample():
    template = StaticTemplate8(
        species=481,
        shiny=Shiny.RANDOM,
        ability=255,
        gender=255,
        iv_count=3,
        level=50,
        roamer=True,
        info=PersonalInfo8(gender_ratio=255, ability_count=1),
    )
    generator = StaticGenerator8(0, 2, 0, Lead.NONE, template, PROFILE, StateFilter())

    assert _core_fields(generator.generate(SEED0, SEED1)) == [
        {"advances": 0, "ec": 570639824, "pid": 408037119, "ivs": [13, 31, 7, 3, 31, 31], "ability": 1, "gender": 2, "level": 50, "nature": 10, "shiny": 0, "height": 194, "weight": 73},
        {"advances": 1, "ec": 2412930810, "pid": 2142114103, "ivs": [31, 16, 23, 31, 12, 31], "ability": 1, "gender": 2, "level": 50, "nature": 14, "shiny": 0, "height": 116, "weight": 91},
        {"advances": 2, "ec": 570642538, "pid": 2187529905, "ivs": [19, 31, 31, 31, 29, 1], "ability": 0, "gender": 2, "level": 50, "nature": 11, "shiny": 0, "height": 185, "weight": 166},
    ]


def test_filter_can_constrain_state_fields():
    template = StaticTemplate8(
        species=387,
        shiny=Shiny.NEVER,
        ability=255,
        iv_count=0,
        level=5,
        info=PersonalInfo8(gender_ratio=31, ability_count=2),
    )
    state_filter = StateFilter(ability=1, iv_min=(20, 0, 0, 0, 0, 0), natures=tuple(index == 2 for index in range(25)))
    generator = StaticGenerator8(0, 9, 0, Lead.NONE, template, PROFILE, state_filter)

    states = generator.generate(SEED0, SEED1)

    assert len(states) == 1
    assert states[0].advances == 1
    assert states[0].ability == 1
    assert states[0].nature == 2


def test_shiny_filter_matches_pokefinder_star_square_semantics():
    base = dict(
        advances=0,
        ec=0,
        sidtid=0,
        pid=0,
        ivs=(0, 0, 0, 0, 0, 0),
        ability=0,
        gender=0,
        level=5,
        nature=0,
        height=0,
        weight=0,
    )
    non_shiny = State8(**base, shiny=0)
    star = State8(**base, shiny=1)
    square = State8(**base, shiny=2)

    assert StateFilter(shiny=1).compare_state(star) is True
    assert StateFilter(shiny=1).compare_state(square) is False
    assert StateFilter(shiny=2).compare_state(square) is True
    assert StateFilter(shiny=2).compare_state(star) is False
    assert StateFilter(shiny=1 | 2).compare_state(star) is True
    assert StateFilter(shiny=1 | 2).compare_state(square) is True
    assert StateFilter(shiny=1 | 2).compare_state(non_shiny) is False


def _legacy_generator(max_advances=130):
    template = StaticTemplate8(
        species=387, shiny=Shiny.NEVER, ability=255, iv_count=0, level=5,
        info=PersonalInfo8(gender_ratio=31, ability_count=2),
    )
    return StaticGenerator8(7, max_advances, 3, Lead.NONE, template, PROFILE, StateFilter(ability=1))


@pytest.mark.parametrize("legacy", ["missing_token_type", "reject_cancel_keyword"])
@pytest.mark.parametrize("multi", [False, True])
def test_cancellable_legacy_native_uses_interruptible_python_without_partial_results(monkeypatch, legacy, multi):
    generator = _legacy_generator()
    filters = [generator.state_filter, StateFilter(height_min=100, height_max=130)]
    all_states = replace(generator, state_filter=StateFilter(skip=True)).generate_non_roamer(SEED0, SEED1)
    expected = [state for state in all_states if any(sf.compare_state(state) for sf in filters)] if multi else [
        state for state in all_states if generator.state_filter.compare_state(state)
    ]
    native_calls = []

    def legacy_native(role, kwargs):
        native_calls.append((role, kwargs))
        if "cancel_token" in kwargs:
            raise TypeError("unexpected keyword argument 'cancel_token'")
        raise AssertionError("uncancellable legacy native generator was entered")

    monkeypatch.setattr(generator_module, "_HAS_NATIVE", True)
    monkeypatch.setattr(generator_module, "_native_generate", lambda *args, **kwargs: legacy_native("single", kwargs), raising=False)
    monkeypatch.setattr(generator_module, "_native_generate_multi", lambda *args, **kwargs: legacy_native("multi", kwargs))

    def make_token():
        token = SearchCancellation()
        token.native = None if legacy == "missing_token_type" else SimpleNamespace(cancel=lambda: None)
        return token

    def generate(token):
        if multi:
            return generator.generate_matching_any(filters, SEED0, SEED1, cancellation=token)
        return generator.generate(SEED0, SEED1, cancellation=token)

    cancellation = make_token()
    assert generate(cancellation) == expected
    expected_calls = [] if legacy == "missing_token_type" else [("multi" if multi else "single", {"cancel_token": cancellation.native})]
    assert native_calls == expected_calls

    # Cancel after real Python encounters have been generated, before the
    # next cooperative check; the public call must not return that prefix.
    native_calls.clear()
    cancellation = make_token()
    original_advance = generator_module.RNGList.advance_state
    advances = 0

    def advance(rng):
        nonlocal advances
        result = original_advance(rng)
        advances += 1
        if advances == 64:
            cancellation.cancel()
        return result

    monkeypatch.setattr(generator_module.RNGList, "advance_state", advance)
    with pytest.raises(SearchCancelled):
        generate(cancellation)
    assert advances == 64
    expected_calls = [] if legacy == "missing_token_type" else [("multi" if multi else "single", {"cancel_token": cancellation.native})]
    assert native_calls == expected_calls

    native_calls.clear()
    cancellation = make_token()
    cancellation.cancel()
    with pytest.raises(SearchCancelled):
        generate(cancellation)
    assert not native_calls and advances == 64


@pytest.mark.parametrize("accept_level", [False, True])
def test_uncancellable_legacy_level_api_remains_compatible(monkeypatch, accept_level):
    generator = replace(_legacy_generator(max_advances=0), state_filter=StateFilter())
    expected = generator.generate_non_roamer(SEED0, SEED1)[0]
    raw_state = (
        expected.advances, expected.ec, expected.sidtid, expected.pid, expected.ivs,
        expected.ability, expected.gender, 100, expected.nature, expected.shiny,
        expected.height, expected.weight,
    )
    calls = []

    def native(*args, **kwargs):
        calls.append((len(args), kwargs))
        if not accept_level and len(args) == 28:
            raise TypeError("legacy API has no level argument")
        assert len(args) == (28 if accept_level else 27) and not kwargs
        return [raw_state]

    monkeypatch.setattr(generator_module, "_HAS_NATIVE", True)
    monkeypatch.setattr(generator_module, "_native_generate", native, raising=False)
    monkeypatch.setattr(generator_module, "_native_generate_multi", None)
    assert generator.generate(SEED0, SEED1) == [expected]
    assert generator.generate_matching_any([StateFilter()], SEED0, SEED1) == [expected]
    assert [count for count, kwargs in calls] == ([28, 28] if accept_level else [28, 27, 28, 27])


@pytest.mark.parametrize("roamer,skip", [(False, False), (True, False), (True, True)])
def test_cancellable_native_single_and_multi_keep_all_python_fields(monkeypatch, roamer, skip):
    from auto_bdsp_rng.data import get_static_encounters

    cancellation = SearchCancellation()
    if not generator_module._HAS_NATIVE or generator_module._native_generate_multi is None or cancellation.native is None:
        pytest.skip("cancellable native extension required")
    template = next(record.template for record in get_static_encounters()
                    if record.template.roamer == roamer and (not roamer or record.template.species == 481))
    filters = [StateFilter(shiny=0, natures=tuple(index % 2 == 0 for index in range(25))),
               StateFilter(height_min=100, height_max=130)]
    if skip:
        filters[0] = StateFilter(skip=True, iv_max=(0,) * 6, hidden_powers=(False,) * 16)
    generator = StaticGenerator8(31, 100, 3, Lead.NONE, template, PROFILE, filters[0])
    generate_python = StaticGenerator8.generate_roamer if roamer else StaticGenerator8.generate_non_roamer
    expected_single = generate_python(generator, SEED0, SEED1)
    all_states = generate_python(replace(generator, state_filter=StateFilter(skip=True)), SEED0, SEED1)
    expected_multi = [state for state in all_states if any(sf.compare_state(state) for sf in filters)]
    native_single, native_multi = generator_module._native_generate, generator_module._native_generate_multi
    calls = []

    def single(*args, **kwargs):
        calls.append(("single", kwargs.get("cancel_token")))
        return native_single(*args, **kwargs)

    def multi(*args, **kwargs):
        calls.append(("multi", kwargs.get("cancel_token")))
        return native_multi(*args, **kwargs)

    monkeypatch.setattr(generator_module, "_native_generate", single)
    monkeypatch.setattr(generator_module, "_native_generate_multi", multi)
    assert generator.generate(SEED0, SEED1, cancellation=cancellation) == expected_single
    multi_cancellation = SearchCancellation()
    assert generator.generate_matching_any(filters, SEED0, SEED1, cancellation=multi_cancellation) == expected_multi
    assert calls == [("single", cancellation.native), ("multi", multi_cancellation.native)]
