from __future__ import annotations

from auto_bdsp_rng.gen8_static import Lead, PersonalInfo8, Profile8, Shiny, State8, StateFilter, StaticGenerator8, StaticTemplate8


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
        {"advances": 0, "ec": 570639824, "pid": 2412930810, "ivs": [10, 4, 23, 15, 30, 19], "ability": 0, "gender": 0, "level": 5, "nature": 17, "shiny": 0, "height": 48, "weight": 96},
        {"advances": 1, "ec": 2412930810, "pid": 570642538, "ivs": [4, 23, 15, 30, 19, 26], "ability": 0, "gender": 0, "level": 5, "nature": 22, "shiny": 0, "height": 124, "weight": 99},
        {"advances": 2, "ec": 570642538, "pid": 2452903364, "ivs": [23, 15, 30, 19, 26, 18], "ability": 1, "gender": 0, "level": 5, "nature": 2, "shiny": 0, "height": 96, "weight": 69},
        {"advances": 3, "ec": 2452903364, "pid": 715243415, "ivs": [15, 30, 19, 26, 18, 25], "ability": 1, "gender": 0, "level": 5, "nature": 5, "shiny": 0, "height": 99, "weight": 96},
        {"advances": 4, "ec": 715243415, "pid": 3067672975, "ivs": [30, 19, 26, 18, 25, 27], "ability": 0, "gender": 0, "level": 5, "nature": 16, "shiny": 0, "height": 69, "weight": 147},
        {"advances": 5, "ec": 3067672975, "pid": 249593662, "ivs": [19, 26, 18, 25, 27, 16], "ability": 0, "gender": 0, "level": 5, "nature": 9, "shiny": 0, "height": 96, "weight": 103},
        {"advances": 6, "ec": 249593662, "pid": 3200942419, "ivs": [26, 18, 25, 27, 16, 12], "ability": 0, "gender": 0, "level": 5, "nature": 3, "shiny": 0, "height": 147, "weight": 67},
        {"advances": 7, "ec": 3200942419, "pid": 422632474, "ivs": [18, 25, 27, 16, 12, 0], "ability": 1, "gender": 0, "level": 5, "nature": 3, "shiny": 0, "height": 103, "weight": 115},
        {"advances": 8, "ec": 422632474, "pid": 3906296370, "ivs": [25, 27, 16, 12, 0, 15], "ability": 1, "gender": 1, "level": 5, "nature": 17, "shiny": 0, "height": 67, "weight": 129},
        {"advances": 9, "ec": 3906296370, "pid": 1698808217, "ivs": [27, 16, 12, 0, 15, 29], "ability": 0, "gender": 0, "level": 5, "nature": 1, "shiny": 0, "height": 115, "weight": 245},
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
        {"advances": 0, "ec": 570639824, "pid": 2412930810, "ivs": [15, 30, 31, 19, 31, 31], "ability": 0, "gender": 0, "level": 1, "nature": 17, "shiny": 0, "height": 48, "weight": 96},
        {"advances": 1, "ec": 2412930810, "pid": 570642538, "ivs": [30, 31, 31, 19, 26, 31], "ability": 0, "gender": 0, "level": 1, "nature": 22, "shiny": 0, "height": 124, "weight": 99},
    ]


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
