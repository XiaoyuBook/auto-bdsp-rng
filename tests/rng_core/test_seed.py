from __future__ import annotations

import pytest

from auto_bdsp_rng.rng_core import SeedPair64, SeedState32


def test_seed_state32_formats_fixed_width_uppercase_words_and_seed_pair():
    state = SeedState32(0x1, 0xABCDEF0, 0x11111111, 0x22222222)

    assert state.format_words() == ("00000001", "0ABCDEF0", "11111111", "22222222")
    assert state.format_seed64_pair() == ("000000010ABCDEF0", "1111111122222222")
    assert state.seed64_pair == (0x000000010ABCDEF0, 0x1111111122222222)


def test_seed_pair64_splits_back_to_state32():
    seed_pair = SeedPair64(0x123456789ABCDEF0, 0x1111111122222222)

    state = seed_pair.to_state32()

    assert state.words == (0x12345678, 0x9ABCDEF0, 0x11111111, 0x22222222)
    assert state.to_seed_pair64() == seed_pair


def test_seed_state32_parses_hex_words_with_optional_prefixes():
    state = SeedState32.from_hex_words(["0x12345678", "9abcdef0", "00000001", "2"])

    assert state.words == (0x12345678, 0x9ABCDEF0, 0x00000001, 0x00000002)
    assert state.format_words() == ("12345678", "9ABCDEF0", "00000001", "00000002")


def test_seed_pair64_parses_hex_words_with_fixed_output_width():
    seed_pair = SeedPair64.from_hex_words(["123456789abcdef0", "2"])

    assert seed_pair.seeds == (0x123456789ABCDEF0, 0x2)
    assert seed_pair.format_seeds() == ("123456789ABCDEF0", "0000000000000002")


@pytest.mark.parametrize(
    ("words", "message"),
    [
        (["123456789", "0", "0", "0"], "8 hexadecimal digits"),
        (["12345678", "nothex", "0", "0"], "hexadecimal"),
        (["12345678", "0", "0"], "exactly four"),
    ],
)
def test_seed_state32_rejects_invalid_manual_input(words, message):
    with pytest.raises(ValueError, match=message):
        SeedState32.from_hex_words(words)


@pytest.mark.parametrize(
    ("words", "message"),
    [
        (["123456789ABCDEF01", "0"], "16 hexadecimal digits"),
        (["123456789ABCDEF0"], "exactly two"),
    ],
)
def test_seed_pair64_rejects_invalid_manual_input(words, message):
    with pytest.raises(ValueError, match=message):
        SeedPair64.from_hex_words(words)
