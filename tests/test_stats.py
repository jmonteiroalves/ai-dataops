"""Statistical primitives, including edge cases."""

from __future__ import annotations

import pytest

from ai_dataops.anomaly.stats import (
    iqr_bounds,
    mad,
    median,
    psi,
    robust_z,
    scale_from_mad,
    two_proportion_z,
)


def test_median_and_mad_for_even_and_odd_sizes() -> None:
    assert median([1, 2, 3]) == 2
    assert median([1, 2, 3, 4]) == 2.5
    assert mad([1, 2, 3, 4, 5]) == 1.0
    with pytest.raises(ValueError):
        median([])


def test_iqr_falls_back_for_tiny_samples() -> None:
    assert iqr_bounds([1, 5]) == (1, 5)
    lower, upper = iqr_bounds([1, 2, 3, 4, 5, 6, 7, 8])
    assert lower < 1 or lower <= 1
    assert upper >= 8


def test_robust_z_handles_zero_dispersion() -> None:
    assert robust_z(10.0, [5.0, 5.0, 5.0]) == 0.0
    assert scale_from_mad([5.0, 5.0, 5.0]) == 0.0


def test_two_proportion_z_edge_cases() -> None:
    assert two_proportion_z(0, 0, 1, 10) == (0.0, 0.0, 0.0)
    z, pooled, error = two_proportion_z(0, 10, 0, 10)
    assert (z, pooled, error) == (0.0, 0.0, 0.0)


def test_psi_edge_cases() -> None:
    assert psi([0, 0], [0, 0]) == 0.0
    with pytest.raises(ValueError):
        psi([1], [1, 2])
