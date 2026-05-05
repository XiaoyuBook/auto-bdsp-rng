from __future__ import annotations

import pytest

from auto_bdsp_rng.rng_core import BDSPXorshift, RNGList, SeedPair64, SeedState32, Xoroshiro, XoroshiroBDSP


@pytest.mark.parametrize(
    ("seed", "result"),
    [
        (0x4000000000000000, 4196352),
        (0x8000000000000000, 8392704),
        (0xC000000000000000, 12589056),
        (0xFFFFFFFFFFFFFFFF, 2040),
    ],
)
def test_bdsp_xorshift_matches_pokefinder_next_samples(seed, result):
    rng = BDSPXorshift.from_pokefinder_seed(seed)

    assert rng.next() == result


@pytest.mark.parametrize(
    ("seed", "advances", "result"),
    [
        (0x8000000000000000, 5, 8392704),
        (0x8000000000000000, 10, 2222986240),
        (0xFFFFFFFFFFFFFFFF, 5, 4194247),
        (0xFFFFFFFFFFFFFFFF, 10, 4184095),
    ],
)
def test_bdsp_xorshift_matches_pokefinder_advance_samples(seed, advances, result):
    rng = BDSPXorshift.from_pokefinder_seed(seed)

    rng.advance(advances - 1)

    assert rng.next() == result


@pytest.mark.parametrize(
    ("seed", "advances", "result"),
    [
        (0x8000000000000000, 1073741824, 2876701647),
        (0x8000000000000000, 2147483648, 990808563),
        (0x8000000000000000, 3221225472, 2455714441),
        (0x8000000000000000, 4294967295, 4072477285),
    ],
)
def test_bdsp_xorshift_matches_pokefinder_jump_samples(seed, advances, result):
    rng = BDSPXorshift.from_pokefinder_seed(seed)

    rng.jump(advances - 1)

    assert rng.next() == result


def test_bdsp_xorshift_accepts_seed_models_and_generates_ranges():
    rng = BDSPXorshift(SeedState32(1, 2, 3, 4))

    assert rng.next() == 2061
    assert rng.words == (2, 3, 4, 2061)
    assert BDSPXorshift.from_seed_pair64(SeedPair64(0x0000000100000002, 0x0000000300000004)).words == (
        1,
        2,
        3,
        4,
    )
    assert 10 <= rng.next_range(10, 20) < 20


@pytest.mark.parametrize(
    ("seed", "result"),
    [
        (0, 9413281287807789659),
        (0x4000000000000000, 14024967306235177563),
        (0x8000000000000000, 189909250953013851),
        (0xC000000000000000, 4801595269380401755),
    ],
)
def test_xoroshiro_matches_pokefinder_next_samples(seed, result):
    rng = Xoroshiro(seed)

    assert rng.next() == result


@pytest.mark.parametrize(
    ("seed", "result"),
    [
        (0, 5807750865143411619),
        (0x4000000000000000, 13695927684520067560),
        (0x8000000000000000, 904773664738280429),
        (0xC000000000000000, 9852954266555844231),
    ],
)
def test_xoroshiro_bdsp_matches_pokefinder_next_samples(seed, result):
    rng = XoroshiroBDSP(seed)

    assert rng.next() == result


@pytest.mark.parametrize(
    ("seed", "advances", "result"),
    [
        (0, 5, 12308290697538785981),
        (0, 10, 614725201967582109),
        (0x8000000000000000, 5, 15092744403755812888),
        (0x8000000000000000, 10, 873521186264857422),
    ],
)
def test_xoroshiro_bdsp_matches_pokefinder_advance_samples(seed, advances, result):
    rng = XoroshiroBDSP(seed)

    rng.advance(advances - 1)

    assert rng.next() == result


@pytest.mark.parametrize(
    ("seed", "advances", "result"),
    [
        (0, 1073741824, 1816436583070306696),
        (0, 2147483648, 10735789180167930541),
        (0, 3221225472, 18384946360177730019),
        (0, 4294967295, 2069475519164550003),
    ],
)
def test_xoroshiro_bdsp_matches_pokefinder_jump_samples(seed, advances, result):
    rng = XoroshiroBDSP(seed)

    rng.jump(advances - 1)

    assert rng.next() == result


def test_xoroshiro_bdsp_next_uint_uses_upper_word_modulo():
    rng = XoroshiroBDSP(0)
    value = rng.next()

    assert XoroshiroBDSP(0).next_uint(100) == ((value >> 32) % 100)


def test_rng_list_reuses_buffer_and_can_refresh_states():
    rng = BDSPXorshift(SeedState32(1, 2, 3, 4))
    rng_list = RNGList(rng, size=4)

    assert rng_list.buffer == (2061, 6175, 4, 8224)
    assert rng_list.next() == 2061
    rng_list.advance(2)
    assert rng_list.next() == 8224

    rng_list.advance_state()

    assert rng_list.head == 1
    assert rng_list.pointer == 1
    assert rng_list.buffer[0] == 4194381
    assert rng_list.next_mod(10) == 5


def test_rng_list_wraps_256_entry_buffers_like_pokefinder_uint8_indices():
    rng = BDSPXorshift(SeedState32(1, 2, 3, 4))
    rng_list = RNGList(rng, size=256)

    rng_list.advance(256)

    assert rng_list.pointer == 0
