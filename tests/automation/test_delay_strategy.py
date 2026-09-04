from __future__ import annotations

import pytest

from auto_bdsp_rng.automation.auto_rng.delay_strategy import (
    DelaySampleRound,
    DelayStrategy,
    DelayStrategyConfig,
    calculate_delay,
    estimate_delay,
    normalize_delay_candidates,
)


def _config(strategy: str, **updates: object) -> DelayStrategyConfig:
    values = {
        "strategy": strategy,
        "baseline_delay": 1452,
        "multi_candidate_policy": "weighted",
        "window_size": 5,
        "ewma_alpha": 0.5,
        "dense_interval_width": 2,
    }
    values.update(updates)
    return DelayStrategyConfig(**values)


def test_normalizes_each_round_to_distinct_sorted_non_negative_integers():
    assert normalize_delay_candidates(["1452", 1451, 1452, -1, None, True, 1453.9]) == (
        1451,
        1452,
        1453,
    )
    assert DelaySampleRound.from_candidates(None).candidates == ()


def test_fixed_strategy_ignores_history():
    result = estimate_delay(_config("fixed"), [[1400], [1500, 1501]])

    assert result.value == 1452
    assert result.valid_round_count == 0
    assert result.candidate_count == 0
    assert result.used_fallback is False


@pytest.mark.parametrize(
    "strategy",
    ["last", "mode", "median", "mean", "ema", "trimmed_mean", "dense_interval"],
)
def test_dynamic_strategies_fall_back_to_baseline_without_valid_rounds(strategy: str):
    config = _config(strategy, multi_candidate_policy="ignore")

    result = estimate_delay(config, [[], [-3], [1451, 1452]])

    assert result.value == 1452
    assert result.effective_strategy is DelayStrategy.FIXED
    assert result.used_fallback is True


def test_ignore_policy_skips_ambiguous_rounds_without_consuming_the_valid_window():
    config = _config("mean", multi_candidate_policy="ignore", window_size=2)

    result = estimate_delay(config, [[1400], [1500, 1501], [1410]])

    assert result.value == 1405
    assert result.valid_round_count == 2
    assert result.candidate_count == 2


def test_weighted_policy_gives_each_round_total_weight_one():
    config = _config("mean")

    result = estimate_delay(config, [[1400, 1500], [1460]])

    assert result.value == 1455
    assert result.valid_round_count == 2
    assert result.candidate_count == 3


@pytest.mark.parametrize("policy", ["ignore", "weighted"])
def test_last_always_skips_multi_candidate_rounds(policy: str):
    config = _config(
        "last",
        baseline_delay=1500,
        multi_candidate_policy=policy,
        window_size=1,
    )

    result = estimate_delay(config, [[1400], [1451, 1452], [1453, 1454]])

    assert result.value == 1400
    assert result.valid_round_count == 1
    assert result.candidate_count == 1
    assert result.used_fallback is False
    assert calculate_delay(config, [[1452, 1452]]) == 1452


def test_last_falls_back_to_baseline_when_only_multi_candidate_rounds_exist():
    result = estimate_delay(
        _config("last", baseline_delay=1500, multi_candidate_policy="weighted"),
        [[1451, 1452], [1453, 1454]],
    )

    assert result.value == 1500
    assert result.valid_round_count == 0
    assert result.candidate_count == 0
    assert result.used_fallback is True


def test_mode_accumulates_fractional_candidate_weights_by_round():
    rounds = [[1451, 1452, 1453], [1452]]

    assert calculate_delay(_config("mode"), rounds) == 1452


def test_mode_tie_prefers_reference_then_lower_value():
    config = _config("mode", baseline_delay=102)
    assert calculate_delay(config, [[100], [102]]) == 102
    assert calculate_delay(config, [[100], [102]], reference_delay=101) == 100


def test_weighted_median_averages_middle_gap_and_rounds_half_up():
    assert calculate_delay(_config("median"), [[1451], [1452]]) == 1452


def test_rolling_mean_uses_only_the_most_recent_valid_rounds():
    config = _config("rolling_mean", window_size=2)

    result = estimate_delay(config, [[1300], [1400], [1450]])

    assert result.value == 1425
    assert result.strategy is DelayStrategy.ROLLING_MEAN


def test_ewma_alias_starts_at_baseline_and_processes_rounds_in_order():
    config = _config("ewma", baseline_delay=100, ewma_alpha=0.5)

    result = estimate_delay(config, [[120], [140]])

    assert result.value == 125
    assert result.strategy is DelayStrategy.EWMA


def test_trimmed_mean_removes_total_weight_one_from_each_tail():
    config = _config("trimmed_mean")

    result = estimate_delay(config, [[0, 20], [10], [11], [12], [100]])

    assert result.value == 13
    assert result.effective_strategy is DelayStrategy.TRIMMED_MEAN
    assert result.used_fallback is False


def test_trimmed_mean_with_fewer_than_five_rounds_falls_back_to_mean():
    result = estimate_delay(_config("trimmed_mean"), [[1400], [1450], [1500]])

    assert result.value == 1450
    assert result.effective_strategy is DelayStrategy.ROLLING_MEAN
    assert result.used_fallback is True


def test_dense_interval_selects_maximum_weight_then_uses_interval_median():
    config = _config("dense_interval", baseline_delay=1452, dense_interval_width=2)
    rounds = [[1451], [1452], [1453], [1500], [1501]]

    assert calculate_delay(config, rounds) == 1452


def test_dense_interval_tie_prefers_interval_nearest_reference():
    config = _config("dense_interval", baseline_delay=103, dense_interval_width=2)
    rounds = [[100], [102], [104]]

    assert calculate_delay(config, rounds) == 103
    assert calculate_delay(config, rounds, reference_delay=101) == 101


def test_dense_interval_exact_tie_prefers_smaller_center():
    config = _config("dense_interval", baseline_delay=102, dense_interval_width=2)

    assert calculate_delay(config, [[100], [102], [104]]) == 101


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"baseline_delay": -1}, "baseline_delay"),
        ({"window_size": 0}, "window_size"),
        ({"ewma_alpha": 0}, "ewma_alpha"),
        ({"ewma_alpha": 1.1}, "ewma_alpha"),
        ({"dense_interval_width": -1}, "dense_interval_width"),
        ({"strategy": "unknown"}, "unsupported delay strategy"),
        ({"multi_candidate_policy": "unknown"}, "unsupported multi-candidate policy"),
    ],
)
def test_rejects_invalid_configuration(updates: dict[str, object], message: str):
    values = {
        "strategy": "fixed",
        "baseline_delay": 100,
        "multi_candidate_policy": "ignore",
        "window_size": 5,
        "ewma_alpha": 0.5,
        "dense_interval_width": 2,
    }
    values.update(updates)

    with pytest.raises(ValueError, match=message):
        DelayStrategyConfig(**values)
