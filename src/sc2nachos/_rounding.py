"""Rounding that several modules share, kept apart from them so that any of them can import it."""

from __future__ import annotations

import math


def rounded_half_up(value: float) -> int:
    """`value` rounded to the nearest integer, a tie up, toward the larger one."""
    # Not `math.floor(value + 0.5)`: the addition itself rounds, taking 0.49999999999999994 to 1.0.
    floored = math.floor(value)
    return floored + (value - floored >= 0.5)
