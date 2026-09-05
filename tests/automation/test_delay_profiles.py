from __future__ import annotations

import json

import pytest

from auto_bdsp_rng.automation.auto_rng.delay_profiles import (
    DELAY_PROFILES_SCHEMA_VERSION,
    DelayProfile,
    decode_delay_profiles,
    encode_delay_profiles,
)
from auto_bdsp_rng.automation.auto_rng.delay_strategy import (
    DelaySampleRound,
    DelayStrategyConfig,
)


def test_profiles_round_trip_all_sample_metadata_and_canonical_species_keys():
    profiles = {
        484: DelayProfile(
            config=DelayStrategyConfig(strategy="last", baseline_delay=1400),
            samples=[],
            next_round_number=1,
        ),
        483: DelayProfile(
            config=DelayStrategyConfig(
                strategy="median",
                baseline_delay=1450,
                multi_candidate_policy="weighted",
                window_size=8,
                ewma_alpha=0.25,
                dense_interval_width=3,
            ),
            samples=[
                DelaySampleRound.from_candidates(
                    [1453, 1451, 1453],
                    round_number=4,
                    observed_at="2026-09-05T19:42:18+08:00",
                ),
                DelaySampleRound.from_candidates(
                    [1678],
                    round_number=5,
                    observed_at=None,
                    excluded=True,
                ),
            ],
            next_round_number=6,
        ),
    }

    encoded = encode_delay_profiles(profiles)
    document = json.loads(encoded)

    assert document["schema_version"] == DELAY_PROFILES_SCHEMA_VERSION
    assert list(document["profiles"]) == ["483", "484"]
    assert document["profiles"]["483"]["samples"][0] == {
        "candidates": [1451, 1453],
        "round_number": 4,
        "observed_at": "2026-09-05T19:42:18+08:00",
        "excluded": False,
    }
    assert decode_delay_profiles(encoded) == profiles


def test_bad_profile_is_skipped_without_losing_other_species():
    document = {
        "schema_version": DELAY_PROFILES_SCHEMA_VERSION,
        "profiles": {
            "483": {
                "config": {"strategy": "median", "baseline_delay": 1450},
                "samples": [
                    {
                        "candidates": [1451],
                        "round_number": 1,
                        "observed_at": None,
                        "excluded": False,
                    }
                ],
                "next_round_number": 2,
            },
            "484": {
                "config": {"strategy": "unsupported"},
                "samples": [],
                "next_round_number": 1,
            },
            "0485": {
                "config": {"strategy": "fixed"},
                "samples": [],
                "next_round_number": 1,
            },
            "486": {
                "config": {"strategy": "fixed"},
                "samples": [
                    {
                        "candidates": [1451],
                        "round_number": 1,
                        "observed_at": "not-a-datetime",
                        "excluded": False,
                    }
                ],
                "next_round_number": 2,
            },
        },
    }

    profiles = decode_delay_profiles(json.dumps(document))

    assert set(profiles) == {483}
    assert profiles[483].samples[0].candidates == (1451,)


def test_next_round_number_is_raised_above_existing_rounds_on_create_and_decode():
    samples = [
        DelaySampleRound.from_candidates([1450], round_number=3),
        DelaySampleRound.from_candidates([1451], round_number=9),
    ]

    profile = DelayProfile(samples=samples, next_round_number=2)
    assert profile.next_round_number == 10

    document = json.loads(encode_delay_profiles({483: profile}))
    document["profiles"]["483"]["next_round_number"] = 4
    restored = decode_delay_profiles(document)
    assert restored[483].next_round_number == 10


@pytest.mark.parametrize(
    "round_numbers",
    [
        [0],
        [1, 1],
        [2, 1],
    ],
)
def test_profile_rejects_nonpositive_duplicate_or_out_of_order_round_numbers(
    round_numbers: list[int],
):
    samples = [
        DelaySampleRound.from_candidates([1450 + index], round_number=round_number)
        for index, round_number in enumerate(round_numbers)
    ]

    with pytest.raises(ValueError, match="positive and strictly increasing"):
        DelayProfile(samples=samples)


def test_encode_revalidates_round_numbers_after_profile_samples_are_mutated():
    profile = DelayProfile()
    profile.samples.extend(
        [
            DelaySampleRound.from_candidates([1450], round_number=1),
            DelaySampleRound.from_candidates([1451], round_number=1),
        ]
    )

    with pytest.raises(ValueError, match="positive and strictly increasing"):
        encode_delay_profiles({483: profile})


@pytest.mark.parametrize("bad_round_number", [0, 1.5, "1", True])
def test_bad_persisted_round_number_skips_only_its_species(bad_round_number: object):
    document = {
        "schema_version": DELAY_PROFILES_SCHEMA_VERSION,
        "profiles": {
            "483": {
                "config": {"strategy": "median", "baseline_delay": 1450},
                "samples": [
                    {
                        "candidates": [1451],
                        "round_number": 1,
                        "observed_at": None,
                        "excluded": False,
                    }
                ],
                "next_round_number": 2,
            },
            "484": {
                "config": {"strategy": "median", "baseline_delay": 1400},
                "samples": [
                    {
                        "candidates": [1401],
                        "round_number": bad_round_number,
                        "observed_at": None,
                        "excluded": False,
                    }
                ],
                "next_round_number": 2,
            },
        },
    }

    profiles = decode_delay_profiles(document)

    assert set(profiles) == {483}


@pytest.mark.parametrize("bad_round_numbers", [[1, 1], [2, 1]])
def test_duplicate_or_out_of_order_persisted_rounds_skip_only_their_species(
    bad_round_numbers: list[int],
):
    document = json.loads(
        encode_delay_profiles(
            {
                483: DelayProfile(
                    samples=[DelaySampleRound.from_candidates([1451], round_number=1)],
                    next_round_number=2,
                )
            }
        )
    )
    document["profiles"]["484"] = {
        "config": {"strategy": "median", "baseline_delay": 1400},
        "samples": [
            {
                "candidates": [1400 + index],
                "round_number": round_number,
                "observed_at": None,
                "excluded": False,
            }
            for index, round_number in enumerate(bad_round_numbers)
        ],
        "next_round_number": 3,
    }

    profiles = decode_delay_profiles(document)

    assert set(profiles) == {483}


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        "[]",
        '{"schema_version":999,"profiles":{}}',
        '{"schema_version":1,"profiles":[]}',
    ],
)
def test_invalid_document_returns_no_profiles(payload: str):
    assert decode_delay_profiles(payload) == {}


@pytest.mark.parametrize("species_id", [0, -1, True, "483"])
def test_encode_rejects_noncanonical_species_ids(species_id: object):
    with pytest.raises(ValueError, match="species IDs"):
        encode_delay_profiles({species_id: DelayProfile()})  # type: ignore[dict-item]
