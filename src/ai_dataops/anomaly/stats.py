"""Statistical primitives used by the detectors (ADR-009, ADR-014)."""

from __future__ import annotations

import math
from collections.abc import Sequence

MAD_SCALE = 0.6745


def median(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("median requires at least one value")
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def mad(values: Sequence[float]) -> float:
    centre = median(values)
    return median([abs(value - centre) for value in values])


def robust_z(value: float, values: Sequence[float]) -> float:
    """Modified z-score; falls back to the IQR scale when MAD is zero."""
    centre = median(values)
    dispersion = mad(values)
    if dispersion == 0:
        lower, upper = iqr_bounds(values)
        dispersion = (upper - lower) / 1.349 if upper > lower else 0.0
    if dispersion == 0:
        return 0.0
    return MAD_SCALE * (value - centre) / dispersion


def iqr_bounds(values: Sequence[float], factor: float = 1.5) -> tuple[float, float]:
    ordered = sorted(values)
    if len(ordered) < 4:
        return (ordered[0], ordered[-1])
    q1 = _percentile(ordered, 0.25)
    q3 = _percentile(ordered, 0.75)
    spread = q3 - q1
    return (q1 - factor * spread, q3 + factor * spread)


def _percentile(ordered: Sequence[float], fraction: float) -> float:
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def scale_from_mad(values: Sequence[float]) -> float:
    dispersion = mad(values)
    if dispersion > 0:
        return dispersion / MAD_SCALE
    lower, upper = iqr_bounds(values)
    return (upper - lower) / 1.349 if upper > lower else 0.0


def two_proportion_z(
    successes_a: float,
    sample_a: float,
    successes_b: float,
    sample_b: float,
) -> tuple[float, float, float]:
    """Return (z, pooled_rate, standard_error) for a two-proportion test."""
    if sample_a <= 0 or sample_b <= 0:
        return (0.0, 0.0, 0.0)
    p_a = successes_a / sample_a
    p_b = successes_b / sample_b
    pooled = (successes_a + successes_b) / (sample_a + sample_b)
    variance = pooled * (1.0 - pooled) * (1.0 / sample_a + 1.0 / sample_b)
    if variance <= 0:
        return (0.0, pooled, 0.0)
    standard_error = math.sqrt(variance)
    return ((p_a - p_b) / standard_error, pooled, standard_error)


def psi(current: Sequence[float], baseline: Sequence[float]) -> float:
    """Population Stability Index between two count vectors."""
    if len(current) != len(baseline):
        raise ValueError("PSI requires vectors of the same length")
    total_current = sum(current)
    total_baseline = sum(baseline)
    if total_current <= 0 or total_baseline <= 0:
        return 0.0
    value = 0.0
    for current_count, baseline_count in zip(current, baseline, strict=True):
        current_share = current_count / total_current
        baseline_share = baseline_count / total_baseline
        if current_share == 0 and baseline_share == 0:
            continue
        current_share = max(current_share, 1e-6)
        baseline_share = max(baseline_share, 1e-6)
        value += (current_share - baseline_share) * math.log(current_share / baseline_share)
    return value


__all__ = [
    "MAD_SCALE",
    "iqr_bounds",
    "mad",
    "median",
    "psi",
    "robust_z",
    "scale_from_mad",
    "two_proportion_z",
]
