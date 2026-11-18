"""Severity bands are a documented contract; exercise every boundary."""

from __future__ import annotations

import pytest

from ai_dataops.anomaly.severity import (
    apply_modifiers,
    bump,
    from_pp,
    from_psi,
    from_ratio,
    from_streak,
    from_z,
    max_level,
)
from ai_dataops.domain.enums import Severity


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (1.0, Severity.INFO),
        (3.5, Severity.LOW),
        (4.5, Severity.MEDIUM),
        (6.0, Severity.HIGH),
        (8.0, Severity.CRITICAL),
        (-9.0, Severity.CRITICAL),
    ],
)
def test_z_bands(score: float, expected: Severity) -> None:
    assert from_z(score) is expected


@pytest.mark.parametrize(
    ("ratio", "expected"),
    [
        (1.0, Severity.INFO),
        (0.88, Severity.LOW),
        (0.80, Severity.MEDIUM),
        (0.60, Severity.HIGH),
        (0.40, Severity.CRITICAL),
        (1.60, Severity.CRITICAL),
        (1.20, Severity.MEDIUM),
    ],
)
def test_ratio_bands(ratio: float, expected: Severity) -> None:
    assert from_ratio(ratio) is expected


@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        (0.001, Severity.INFO),
        (0.015, Severity.LOW),
        (0.025, Severity.MEDIUM),
        (0.05, Severity.HIGH),
        (0.20, Severity.CRITICAL),
    ],
)
def test_pp_bands(delta: float, expected: Severity) -> None:
    assert from_pp(delta) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.05, Severity.INFO),
        (0.12, Severity.LOW),
        (0.20, Severity.MEDIUM),
        (0.30, Severity.HIGH),
        (0.50, Severity.CRITICAL),
    ],
)
def test_psi_bands(value: float, expected: Severity) -> None:
    assert from_psi(value) is expected


@pytest.mark.parametrize(
    ("streak", "expected"),
    [
        (0, Severity.INFO),
        (1, Severity.LOW),
        (2, Severity.MEDIUM),
        (3, Severity.HIGH),
        (5, Severity.CRITICAL),
    ],
)
def test_streak_bands(streak: int, expected: Severity) -> None:
    assert from_streak(streak) is expected


def test_bump_is_clamped() -> None:
    assert bump(Severity.CRITICAL, 3) is Severity.CRITICAL
    assert bump(Severity.INFO, -2) is Severity.INFO
    assert bump(Severity.LOW) is Severity.MEDIUM


def test_max_level_and_modifiers() -> None:
    assert max_level(Severity.LOW, Severity.CRITICAL, Severity.MEDIUM) is Severity.CRITICAL
    assert (
        apply_modifiers(Severity.MEDIUM, criticality="tier_1", persistent=True) is Severity.CRITICAL
    )
    assert apply_modifiers(Severity.INFO, criticality="tier_3") is Severity.INFO
    assert apply_modifiers(Severity.HIGH, criticality="tier_2") is Severity.HIGH
