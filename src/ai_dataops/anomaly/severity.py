"""Severity model: magnitude band, then explicit modifiers.

Bands follow ``docs/anomaly-detection.md`` § 6. The mapping is intentionally
coarse and documented so a reviewer can reproduce any decision.
"""

from __future__ import annotations

from ai_dataops.domain.enums import Severity

_ORDER: tuple[Severity, ...] = (
    Severity.INFO,
    Severity.LOW,
    Severity.MEDIUM,
    Severity.HIGH,
    Severity.CRITICAL,
)


def bump(level: Severity, steps: int = 1) -> Severity:
    index = min(len(_ORDER) - 1, max(0, _ORDER.index(level) + steps))
    return _ORDER[index]


def max_level(*levels: Severity) -> Severity:
    return max(levels, key=_ORDER.index)


def from_z(score: float) -> Severity:
    magnitude = abs(score)
    if magnitude >= 8.0:
        return Severity.CRITICAL
    if magnitude >= 6.0:
        return Severity.HIGH
    if magnitude >= 4.5:
        return Severity.MEDIUM
    if magnitude >= 3.5:
        return Severity.LOW
    return Severity.INFO


def from_ratio(ratio: float) -> Severity:
    if ratio < 0.50 or ratio > 1.50:
        return Severity.CRITICAL
    if ratio < 0.70 or ratio > 1.30:
        return Severity.HIGH
    if ratio < 0.85 or ratio > 1.15:
        return Severity.MEDIUM
    if ratio < 0.90 or ratio > 1.10:
        return Severity.LOW
    return Severity.INFO


def from_pp(delta: float) -> Severity:
    magnitude = abs(delta)
    if magnitude >= 0.10:
        return Severity.CRITICAL
    if magnitude >= 0.03:
        return Severity.HIGH
    if magnitude >= 0.02:
        return Severity.MEDIUM
    if magnitude >= 0.01:
        return Severity.LOW
    return Severity.INFO


def from_psi(value: float) -> Severity:
    if value >= 0.40:
        return Severity.CRITICAL
    if value >= 0.25:
        return Severity.HIGH
    if value >= 0.15:
        return Severity.MEDIUM
    if value >= 0.10:
        return Severity.LOW
    return Severity.INFO


def from_streak(streak: int) -> Severity:
    if streak >= 4:
        return Severity.CRITICAL
    if streak == 3:
        return Severity.HIGH
    if streak == 2:
        return Severity.MEDIUM
    if streak == 1:
        return Severity.LOW
    return Severity.INFO


def apply_modifiers(
    level: Severity,
    *,
    criticality: str,
    persistent: bool = False,
) -> Severity:
    steps = 0
    if criticality == "tier_1":
        steps += 1
    elif criticality == "tier_3":
        steps -= 1
    if persistent:
        steps += 1
    return bump(level, steps)


__all__ = [
    "apply_modifiers",
    "bump",
    "from_pp",
    "from_psi",
    "from_ratio",
    "from_streak",
    "from_z",
    "max_level",
]
