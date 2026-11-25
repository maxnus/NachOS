"""How a time curve reads between the times it was given values at."""

from enum import Enum


class Interpolation(Enum):
    """How a `TimeCurve` reads a time between two of the times it was given values at."""

    LINEAR = "linear"
    """Along the straight line from the value before to the value after."""
    STEP = "step"
    """The value before, held from its time until the next, which the curve jumps to."""
