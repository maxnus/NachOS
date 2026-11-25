"""What a value does for a time curve to read between two of them."""

from typing import Protocol, Self


class SupportsInterpolation(Protocol):
    """A value a `TimeCurve` reads between two of its times: one that adds to another of its kind, subtracts from it
    and multiplies by a number, as a float, `Resources`, a `Cost`, a `Point` and a `GameTime` do."""

    def __add__(self, other: Self, /) -> Self: ...

    def __sub__(self, other: Self, /) -> Self: ...

    def __mul__(self, factor: float, /) -> Self: ...
