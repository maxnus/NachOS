"""Rounding that several modules share."""

import math


def rounded_half_up(value: float) -> int:
    """`value` rounded to the nearest integer, a tie up, toward the larger one, where Python's `round` takes the even
    one. Raises `OverflowError` for an infinity and `ValueError` for a NaN, as `math.floor` does."""
    # Not `math.floor(value + 0.5)`: the addition itself rounds, taking 0.49999999999999994 to 1.0.
    floored = math.floor(value)
    return floored + (value - floored >= 0.5)
