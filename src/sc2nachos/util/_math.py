"""Numeric helpers for scoring, interpolation and smoothing."""

from __future__ import annotations

import math


def clip[T: float](value: T, minimum: T = 0, maximum: T = 1) -> T:
    """`value` clamped to `[minimum, maximum]`, by default the unit interval."""
    return max(min(value, maximum), minimum)


def lerp(start: float, end: float, fraction: float) -> float:
    """Linear interpolation: `fraction` 0 gives `start`, 1 gives `end`, and values outside extrapolate."""
    return start + fraction * (end - start)


def remap(value: float, lower: float, upper: float, *, lower_value: float = 0.0, upper_value: float = 1.0) -> float:
    """`value` mapped from `[lower, upper]` onto `[lower_value, upper_value]`, clamped at both ends."""
    if lower >= upper:
        raise ValueError(f"lower bound {lower} is not below upper bound {upper}")
    if value <= lower:
        return lower_value
    if value >= upper:
        return upper_value
    fraction = (value - lower) / (upper - lower)
    return lower_value + fraction * (upper_value - lower_value)


def sticky_round(value: float, previous_value: int, *, tolerance: float = 1.0) -> int:
    """`value` rounded, a tie up, toward the larger integer, except that it stays at `previous_value` while within
    `tolerance` of it.

    Stops an estimate recomputed every step from flickering between neighboring integers.
    """
    if abs(value - previous_value) < tolerance:
        return previous_value
    return _rounded_half_up(value)


def _rounded_half_up(value: float) -> int:
    """`value` rounded to the nearest integer, a tie up, toward the larger one."""
    # Not `math.floor(value + 0.5)`: the addition itself rounds, taking 0.49999999999999994 to 1.0.
    floored = math.floor(value)
    return floored + (value - floored >= 0.5)


def damp(start: float, end: float, *, decay: float, seconds: float) -> float:
    """`start` moved toward `end` by exponential damping at rate `decay` per second, over `seconds`.

    Applied repeatedly, it converges on `end` at a rate independent of how often it is called. It never overshoots.
    """
    return lerp(start, end, 1 - math.exp(-decay * seconds))


def logistic(x: float, *, k: float = 1.0) -> float:
    """The logistic curve, rising from 0 to 1 about `x` = 0, with steepness `k`.

    Extreme inputs saturate rather than overflowing.
    """
    z = k * x
    if z >= 0:
        return 1 / (1 + math.exp(-z))
    e = math.exp(z)
    return e / (1 + e)


def s_curve(x: float, *, k: float = 1.0) -> float:
    """An S-shaped remap of the unit interval onto itself, with steepness `k`: the logistic curve about 0.5, scaled
    to pass through 0 and 1.

    0, 0.5 and 1 map to exactly themselves; inputs outside `[0, 1]` extrapolate. A `k` of 0 gives `x` itself, the
    limit as the curve flattens.
    """
    if not k:
        return x
    return 0.5 + 0.5 * math.tanh(k * (x - 0.5) / 2) / math.tanh(k / 4)
