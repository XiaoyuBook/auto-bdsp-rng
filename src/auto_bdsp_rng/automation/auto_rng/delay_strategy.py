from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from fractions import Fraction
from typing import Iterable


class DelayStrategy(str, Enum):
    FIXED = "fixed"
    LAST = "last"
    MODE = "mode"
    MEDIAN = "median"
    ROLLING_MEAN = "mean"
    EWMA = "ema"
    TRIMMED_MEAN = "trimmed_mean"
    DENSE_INTERVAL = "dense_interval"


class MultiCandidatePolicy(str, Enum):
    IGNORE = "ignore"
    WEIGHTED = "weighted"


_STRATEGY_ALIASES = {
    "rolling_mean": DelayStrategy.ROLLING_MEAN,
    "ewma": DelayStrategy.EWMA,
}


def normalize_delay_strategy(value: DelayStrategy | str) -> DelayStrategy:
    if isinstance(value, DelayStrategy):
        return value
    strategy_id = str(value).strip().lower()
    try:
        return DelayStrategy(strategy_id)
    except ValueError:
        try:
            return _STRATEGY_ALIASES[strategy_id]
        except KeyError as exc:
            raise ValueError(f"unsupported delay strategy: {value!r}") from exc


def normalize_multi_candidate_policy(
    value: MultiCandidatePolicy | str,
) -> MultiCandidatePolicy:
    if isinstance(value, MultiCandidatePolicy):
        return value
    policy_id = str(value).strip().lower()
    try:
        return MultiCandidatePolicy(policy_id)
    except ValueError as exc:
        raise ValueError(f"unsupported multi-candidate policy: {value!r}") from exc


def normalize_delay_candidates(candidates: Iterable[object] | object) -> tuple[int, ...]:
    """Return the distinct, sorted, non-negative delays from one round."""

    try:
        values = iter(candidates)  # type: ignore[arg-type]
    except TypeError:
        return ()

    normalized: set[int] = set()
    for raw_value in values:
        if isinstance(raw_value, bool):
            continue
        try:
            value = int(raw_value)
        except (TypeError, ValueError, OverflowError):
            continue
        if value >= 0:
            normalized.add(value)
    return tuple(sorted(normalized))


@dataclass(frozen=True)
class DelaySampleRound:
    """One reverse-lookup result, kept as an atomic equal-weight sample."""

    candidates: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidates", normalize_delay_candidates(self.candidates))

    @classmethod
    def from_candidates(cls, candidates: Iterable[object] | object) -> DelaySampleRound:
        return cls(normalize_delay_candidates(candidates))


@dataclass(frozen=True)
class DelayStrategyConfig:
    strategy: DelayStrategy | str = DelayStrategy.FIXED
    baseline_delay: int = 100
    multi_candidate_policy: MultiCandidatePolicy | str = MultiCandidatePolicy.IGNORE
    window_size: int = 5
    ewma_alpha: float = 0.5
    dense_interval_width: int = 2

    def __post_init__(self) -> None:
        strategy = normalize_delay_strategy(self.strategy)
        policy = normalize_multi_candidate_policy(self.multi_candidate_policy)
        try:
            baseline_delay = int(self.baseline_delay)
            window_size = int(self.window_size)
            dense_interval_width = int(self.dense_interval_width)
            alpha = _number_fraction(self.ewma_alpha)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("invalid delay strategy configuration") from exc
        if baseline_delay < 0:
            raise ValueError("baseline_delay must be non-negative")
        if window_size < 1:
            raise ValueError("window_size must be at least 1")
        if not 0 < alpha <= 1:
            raise ValueError("ewma_alpha must be greater than 0 and at most 1")
        if dense_interval_width < 0:
            raise ValueError("dense_interval_width must be non-negative")

        object.__setattr__(self, "strategy", strategy)
        object.__setattr__(self, "baseline_delay", baseline_delay)
        object.__setattr__(self, "multi_candidate_policy", policy)
        object.__setattr__(self, "window_size", window_size)
        object.__setattr__(self, "ewma_alpha", float(alpha))
        object.__setattr__(self, "dense_interval_width", dense_interval_width)


@dataclass(frozen=True)
class DelayEstimate:
    value: int
    strategy: DelayStrategy
    effective_strategy: DelayStrategy
    valid_round_count: int
    candidate_count: int
    used_fallback: bool = False


def estimate_delay(
    config: DelayStrategyConfig,
    rounds: Iterable[DelaySampleRound | Iterable[object] | object],
    *,
    reference_delay: int | None = None,
) -> DelayEstimate:
    """Estimate the next delay from raw per-round reverse-lookup candidates.

    Ambiguous rounds are either ignored or assigned total weight one, divided
    equally among their distinct candidates. The ``last`` strategy always
    ignores ambiguous rounds because they do not identify one actual delay.
    Statistical strategies use the most recent ``window_size`` eligible
    rounds. Fractional final results use round-half-away-from-zero, which means
    ``.5`` rounds up for valid delays.
    """

    strategy = normalize_delay_strategy(config.strategy)
    if strategy is DelayStrategy.FIXED:
        return DelayEstimate(
            value=config.baseline_delay,
            strategy=strategy,
            effective_strategy=strategy,
            valid_round_count=0,
            candidate_count=0,
        )

    normalized_rounds = [_coerce_round(round_value) for round_value in rounds]
    allow_ambiguous_rounds = (
        strategy is not DelayStrategy.LAST
        and config.multi_candidate_policy is MultiCandidatePolicy.WEIGHTED
    )
    eligible = [
        sample
        for sample in normalized_rounds
        if sample.candidates
        and (
            allow_ambiguous_rounds
            or len(sample.candidates) == 1
        )
    ]
    if strategy is DelayStrategy.LAST:
        used_rounds = eligible[-1:]
    else:
        used_rounds = eligible[-config.window_size :]

    if not used_rounds:
        return DelayEstimate(
            value=config.baseline_delay,
            strategy=strategy,
            effective_strategy=DelayStrategy.FIXED,
            valid_round_count=0,
            candidate_count=0,
            used_fallback=True,
        )

    reference = config.baseline_delay if reference_delay is None else max(0, int(reference_delay))
    points = _weighted_points(used_rounds)
    candidate_count = sum(len(sample.candidates) for sample in used_rounds)
    effective_strategy = strategy
    used_fallback = False

    if strategy is DelayStrategy.LAST:
        raw_estimate = Fraction(used_rounds[-1].candidates[0])
    elif strategy is DelayStrategy.MODE:
        raw_estimate = _weighted_mode(points, reference)
    elif strategy is DelayStrategy.MEDIAN:
        raw_estimate = _weighted_median(points)
    elif strategy is DelayStrategy.ROLLING_MEAN:
        raw_estimate = _weighted_mean(points)
    elif strategy is DelayStrategy.EWMA:
        raw_estimate = _ewma(config, used_rounds)
    elif strategy is DelayStrategy.TRIMMED_MEAN:
        if len(used_rounds) < 5:
            raw_estimate = _weighted_mean(points)
            effective_strategy = DelayStrategy.ROLLING_MEAN
            used_fallback = True
        else:
            raw_estimate = _trimmed_mean(points)
    elif strategy is DelayStrategy.DENSE_INTERVAL:
        raw_estimate = _dense_interval_median(
            points,
            width=config.dense_interval_width,
            reference=reference,
        )
    else:  # pragma: no cover - exhaustive guard for future enum additions
        raise ValueError(f"unsupported delay strategy: {strategy.value}")

    return DelayEstimate(
        value=_round_fraction(raw_estimate),
        strategy=strategy,
        effective_strategy=effective_strategy,
        valid_round_count=len(used_rounds),
        candidate_count=candidate_count,
        used_fallback=used_fallback,
    )


def calculate_delay(
    config: DelayStrategyConfig,
    rounds: Iterable[DelaySampleRound | Iterable[object] | object],
    *,
    reference_delay: int | None = None,
) -> int:
    return estimate_delay(config, rounds, reference_delay=reference_delay).value


def _coerce_round(value: DelaySampleRound | Iterable[object] | object) -> DelaySampleRound:
    if isinstance(value, DelaySampleRound):
        return value
    return DelaySampleRound.from_candidates(value)


def _number_fraction(value: object) -> Fraction:
    if isinstance(value, bool):
        raise ValueError("boolean is not a numeric setting")
    return Fraction(str(value))


def _round_fraction(value: Fraction) -> int:
    if value < 0:
        return -_round_fraction(-value)
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


def _round_mean(sample: DelaySampleRound) -> Fraction:
    return Fraction(sum(sample.candidates), len(sample.candidates))


def _weighted_points(rounds: list[DelaySampleRound]) -> dict[int, Fraction]:
    points: dict[int, Fraction] = {}
    for sample in rounds:
        candidate_weight = Fraction(1, len(sample.candidates))
        for value in sample.candidates:
            points[value] = points.get(value, Fraction()) + candidate_weight
    return points


def _weighted_mean(points: dict[int, Fraction]) -> Fraction:
    total_weight = sum(points.values(), Fraction())
    return sum((value * weight for value, weight in points.items()), Fraction()) / total_weight


def _weighted_median(points: dict[int, Fraction]) -> Fraction:
    ordered = sorted(points.items())
    total_weight = sum((weight for _value, weight in ordered), Fraction())
    cumulative = Fraction()
    for index, (value, weight) in enumerate(ordered):
        cumulative += weight
        doubled = cumulative * 2
        if doubled > total_weight:
            return Fraction(value)
        if doubled == total_weight:
            next_value = next(
                (candidate for candidate, candidate_weight in ordered[index + 1 :] if candidate_weight > 0),
                value,
            )
            return Fraction(value + next_value, 2)
    return Fraction(ordered[-1][0])


def _weighted_mode(points: dict[int, Fraction], reference: int) -> Fraction:
    best_weight = max(points.values())
    winners = [value for value, weight in points.items() if weight == best_weight]
    winner = min(winners, key=lambda value: (abs(value - reference), value))
    return Fraction(winner)


def _ewma(config: DelayStrategyConfig, rounds: list[DelaySampleRound]) -> Fraction:
    alpha = _number_fraction(config.ewma_alpha)
    estimate = Fraction(config.baseline_delay)
    for sample in rounds:
        estimate = (1 - alpha) * estimate + alpha * _round_mean(sample)
    return estimate


def _trimmed_mean(points: dict[int, Fraction]) -> Fraction:
    trimmed = dict(points)
    _trim_weight(trimmed, Fraction(1), reverse=False)
    _trim_weight(trimmed, Fraction(1), reverse=True)
    return _weighted_mean({value: weight for value, weight in trimmed.items() if weight > 0})


def _trim_weight(points: dict[int, Fraction], amount: Fraction, *, reverse: bool) -> None:
    remaining = amount
    for value in sorted(points, reverse=reverse):
        if remaining <= 0:
            return
        removed = min(points[value], remaining)
        points[value] -= removed
        remaining -= removed


def _dense_interval_median(
    points: dict[int, Fraction],
    *,
    width: int,
    reference: int,
) -> Fraction:
    values = sorted(points)
    right = 0
    window_weight = Fraction()
    best_key: tuple[Fraction, Fraction, Fraction, int] | None = None
    best_bounds = (0, 1)
    for left, start in enumerate(values):
        while right < len(values) and values[right] <= start + width:
            window_weight += points[values[right]]
            right += 1
        end = values[right - 1]
        center = Fraction(start + end, 2)
        key = (-window_weight, abs(center - reference), center, start)
        if best_key is None or key < best_key:
            best_key = key
            best_bounds = (left, right)
        window_weight -= points[start]

    left, right = best_bounds
    selected = {value: points[value] for value in values[left:right]}
    return _weighted_median(selected)


__all__ = [
    "DelayEstimate",
    "DelaySampleRound",
    "DelayStrategy",
    "DelayStrategyConfig",
    "MultiCandidatePolicy",
    "calculate_delay",
    "estimate_delay",
    "normalize_delay_candidates",
    "normalize_delay_strategy",
    "normalize_multi_candidate_policy",
]
