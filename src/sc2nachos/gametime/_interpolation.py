"""How a time curve reads between the times it was given values at, and the values it reads so."""

from enum import Enum
from typing import Protocol, Self


class Interpolation(Enum):
    """How a `TimeCurve` reads a time between two of the times it was given values at."""

    LINEAR = "linear"
    """Along the straight line from the value before to the value after."""
    STEP = "step"
    """The value before, held from its time until the next, which the curve jumps to."""


class SupportsInterpolation(Protocol):
    """A value a `TimeCurve` reads between two of its times: one that adds to another of its kind, subtracts from it
    and multiplies by a number, as a float, `Resources`, a `Cost`, a `Point` and a `GameTime` do."""

    def __add__(self, other: Self, /) -> Self: ...

    def __sub__(self, other: Self, /) -> Self: ...

    def __mul__(self, factor: float, /) -> Self: ...
