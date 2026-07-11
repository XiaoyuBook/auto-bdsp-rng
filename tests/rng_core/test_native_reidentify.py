from __future__ import annotations

import sys
from pathlib import Path

import pytest

from auto_bdsp_rng.rng_core import SeedState32


PROJECT_XS_SRC = Path(__file__).resolve().parents[2] / "third_party" / "Project_Xs_CHN" / "src"
if str(PROJECT_XS_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_XS_SRC))


def _seed_pair(state: SeedState32) -> tuple[int, int]:
    return state.seed64_pair


def _rng_seed_pair(rng: object) -> tuple[int, int]:
    words = rng.get_state()
    return ((int(words[0]) << 32) | int(words[1]), (int(words[2]) << 32) | int(words[3]))


def _player_blink_intervals(seed: SeedState32, *, start: int, blink_count: int) -> list[int]:
    from xorshift import Xorshift

    rng = Xorshift(*seed.words)
    rng.get_next_rand_sequence(start)
    blink_positions: list[int] = []
    index = 0
    while len(blink_positions) < blink_count:
        if (rng.next() & 0b1110) == 0:
            blink_positions.append(index)
        index += 1
    return [0] + [current - previous for previous, current in zip(blink_positions, blink_positions[1:])]


def test_native_reidentify_by_intervals_matches_project_xs_python() -> None:
    from auto_bdsp_rng.rng_core import _native
    from xorshift import Xorshift
    import rngtool

    seed = SeedState32(0x12345678, 0x9ABCDEF0, 0x11111111, 0x22222222)
    intervals = _player_blink_intervals(seed, start=1200, blink_count=7)
    py_rng, py_advances = rngtool.reidentiy_by_intervals(
        Xorshift(*seed.words),
        intervals,
        npc=0,
        search_min=0,
        search_max=10_000,
        return_advance=True,
    )

    native_seed0, native_seed1, native_advances = _native.reidentify_by_intervals(
        *_seed_pair(seed),
        intervals,
        0,
        0,
        10_000,
    )

    assert native_advances == py_advances
    assert (native_seed0, native_seed1) == _rng_seed_pair(py_rng)


def test_native_reidentify_by_intervals_noisy_matches_project_xs_python_tie_breaking() -> None:
    from auto_bdsp_rng.rng_core import _native
    from xorshift import Xorshift
    import rngtool

    seed = SeedState32(0x12345678, 0x9ABCDEF0, 0x11111111, 0x22222222)
    intervals = [0, 1, 1, 1]
    py_rng, py_advances = rngtool.reidentiy_by_intervals_noisy(
        Xorshift(*seed.words),
        intervals,
        search_min=0,
        search_max=1_000,
    )

    native_seed0, native_seed1, native_advances = _native.reidentify_by_intervals_noisy(
        *_seed_pair(seed),
        intervals,
        0,
        1_000,
    )

    assert py_advances == 453
    assert native_advances == py_advances
    assert (native_seed0, native_seed1) == _rng_seed_pair(py_rng)


def test_native_reidentify_by_intervals_returns_none_when_not_found() -> None:
    from auto_bdsp_rng.rng_core import _native

    seed = SeedState32(0x12345678, 0x9ABCDEF0, 0x11111111, 0x22222222)

    assert _native.reidentify_by_intervals(*_seed_pair(seed), [0, 999, 999], 0, 0, 10) is None
    assert _native.reidentify_by_intervals_noisy(*_seed_pair(seed), [0, 999, 999], 0, 10) is None
