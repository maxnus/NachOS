"""Checks that the arguments of several modules share."""

from numbers import Integral


def is_whole_number(value: object) -> bool:
    """Whether `value` is a whole number, and not a `bool`."""
    return isinstance(value, Integral) and not isinstance(value, bool)
