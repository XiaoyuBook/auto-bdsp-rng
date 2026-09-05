from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from auto_bdsp_rng.automation.auto_rng.delay_strategy import (
    DelaySampleRound,
    DelayStrategyConfig,
)


DELAY_PROFILES_SCHEMA_VERSION = 1


@dataclass
class DelayProfile:
    config: DelayStrategyConfig = field(default_factory=DelayStrategyConfig)
    samples: list[DelaySampleRound] = field(default_factory=list)
    next_round_number: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.config, DelayStrategyConfig):
            raise TypeError("config must be a DelayStrategyConfig")
        self.samples = list(self.samples)
        if not all(isinstance(sample, DelaySampleRound) for sample in self.samples):
            raise TypeError("samples must contain DelaySampleRound values")
        _validate_sample_round_numbers(self.samples)
        if isinstance(self.next_round_number, bool):
            raise ValueError("next_round_number must be a positive integer")
        try:
            next_round_number = int(self.next_round_number)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("next_round_number must be a positive integer") from exc
        if next_round_number < 1:
            raise ValueError("next_round_number must be a positive integer")
        minimum_next = max(
            (sample.round_number + 1 for sample in self.samples),
            default=1,
        )
        self.next_round_number = max(next_round_number, minimum_next)


def encode_delay_profiles(profiles: Mapping[int, DelayProfile]) -> str:
    """Encode species-keyed delay profiles into canonical versioned JSON."""

    encoded_profiles: dict[str, dict[str, Any]] = {}
    normalized: list[tuple[int, DelayProfile]] = []
    for raw_species_id, profile in profiles.items():
        species_id = _validate_species_id(raw_species_id)
        if not isinstance(profile, DelayProfile):
            raise TypeError("profiles must contain DelayProfile values")
        normalized.append((species_id, profile))

    for species_id, profile in sorted(normalized, key=lambda item: item[0]):
        encoded_profiles[str(species_id)] = _encode_profile(profile)

    return json.dumps(
        {
            "schema_version": DELAY_PROFILES_SCHEMA_VERSION,
            "profiles": encoded_profiles,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def decode_delay_profiles(payload: object) -> dict[int, DelayProfile]:
    """Decode profiles while isolating corruption to the affected species."""

    if isinstance(payload, Mapping):
        document: object = payload
    else:
        try:
            document = json.loads(payload)  # type: ignore[arg-type]
        except (TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
            return {}
    if not isinstance(document, Mapping):
        return {}
    if document.get("schema_version") != DELAY_PROFILES_SCHEMA_VERSION:
        return {}
    raw_profiles = document.get("profiles")
    if not isinstance(raw_profiles, Mapping):
        return {}

    profiles: dict[int, DelayProfile] = {}
    for raw_species_id, raw_profile in raw_profiles.items():
        try:
            species_id = _decode_species_id(raw_species_id)
            profile = _decode_profile(raw_profile)
        except (TypeError, ValueError, OverflowError):
            continue
        profiles[species_id] = profile
    return profiles


def _encode_profile(profile: DelayProfile) -> dict[str, Any]:
    config = profile.config
    if not all(isinstance(sample, DelaySampleRound) for sample in profile.samples):
        raise TypeError("samples must contain DelaySampleRound values")
    _validate_sample_round_numbers(profile.samples)
    minimum_next = max(
        (sample.round_number + 1 for sample in profile.samples),
        default=1,
    )
    if isinstance(profile.next_round_number, bool):
        raise ValueError("next_round_number must be a positive integer")
    try:
        next_round_number = int(profile.next_round_number)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("next_round_number must be a positive integer") from exc
    if next_round_number < 1:
        raise ValueError("next_round_number must be a positive integer")
    return {
        "config": {
            "strategy": config.strategy.value,
            "baseline_delay": config.baseline_delay,
            "multi_candidate_policy": config.multi_candidate_policy.value,
            "window_size": config.window_size,
            "ewma_alpha": config.ewma_alpha,
            "dense_interval_width": config.dense_interval_width,
        },
        "samples": [
            {
                "candidates": list(sample.candidates),
                "round_number": sample.round_number,
                "observed_at": sample.observed_at,
                "excluded": sample.excluded,
            }
            for sample in profile.samples
        ],
        "next_round_number": max(next_round_number, minimum_next),
    }


def _decode_profile(raw_profile: object) -> DelayProfile:
    if not isinstance(raw_profile, Mapping):
        raise TypeError("profile must be an object")
    raw_config = raw_profile.get("config")
    raw_samples = raw_profile.get("samples")
    if not isinstance(raw_config, Mapping) or not isinstance(raw_samples, list):
        raise TypeError("profile config and samples are required")

    config = DelayStrategyConfig(
        strategy=raw_config.get("strategy", "fixed"),
        baseline_delay=raw_config.get("baseline_delay", 100),
        multi_candidate_policy=raw_config.get("multi_candidate_policy", "ignore"),
        window_size=raw_config.get("window_size", 5),
        ewma_alpha=raw_config.get("ewma_alpha", 0.5),
        dense_interval_width=raw_config.get("dense_interval_width", 2),
    )
    samples = [_decode_sample(raw_sample) for raw_sample in raw_samples]

    raw_next_round_number = raw_profile.get("next_round_number", 1)
    if isinstance(raw_next_round_number, bool):
        raise ValueError("next_round_number must be a positive integer")
    try:
        next_round_number = int(raw_next_round_number)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("next_round_number must be a positive integer") from exc
    if next_round_number < 1:
        raise ValueError("next_round_number must be a positive integer")
    return DelayProfile(
        config=config,
        samples=samples,
        next_round_number=next_round_number,
    )


def _decode_sample(raw_sample: object) -> DelaySampleRound:
    if not isinstance(raw_sample, Mapping):
        raise TypeError("sample must be an object")
    candidates = raw_sample.get("candidates")
    if not isinstance(candidates, list):
        raise TypeError("sample candidates must be an array")
    if "round_number" not in raw_sample or "excluded" not in raw_sample:
        raise ValueError("sample metadata is incomplete")
    round_number = raw_sample["round_number"]
    if isinstance(round_number, bool) or not isinstance(round_number, int):
        raise ValueError("sample round_number must be an integer")
    return DelaySampleRound.from_candidates(
        candidates,
        round_number=round_number,
        observed_at=raw_sample.get("observed_at"),
        excluded=raw_sample["excluded"],
    )


def _validate_sample_round_numbers(samples: list[DelaySampleRound]) -> None:
    previous_round_number = 0
    for sample in samples:
        if sample.round_number <= previous_round_number:
            raise ValueError(
                "profile sample round_number values must be positive and strictly increasing"
            )
        previous_round_number = sample.round_number


def _validate_species_id(raw_species_id: object) -> int:
    if isinstance(raw_species_id, bool) or not isinstance(raw_species_id, int):
        raise ValueError("species IDs must be positive integers")
    if raw_species_id < 1:
        raise ValueError("species IDs must be positive integers")
    return raw_species_id


def _decode_species_id(raw_species_id: object) -> int:
    if not isinstance(raw_species_id, str):
        raise ValueError("profile keys must be decimal species IDs")
    if not raw_species_id.isascii() or not raw_species_id.isdecimal():
        raise ValueError("profile keys must be decimal species IDs")
    if raw_species_id.startswith("0"):
        raise ValueError("profile keys must be canonical decimal species IDs")
    return _validate_species_id(int(raw_species_id, 10))


__all__ = [
    "DELAY_PROFILES_SCHEMA_VERSION",
    "DelayProfile",
    "decode_delay_profiles",
    "encode_delay_profiles",
]
