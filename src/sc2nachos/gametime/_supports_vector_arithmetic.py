"""The arithmetic of a vector, which a time curve reads between two of its values by."""

from typing import Protocol, Self


class SupportsVectorArithmetic(Protocol):
    """A value with the arithmetic of a vector: it adds to another of its kind, subtracts from it and multiplies by
    a number, as a float, `Resources`, a `Cost`, a `Point` and a `GameTime` do. A `TimeCurve` reads between two of
    its times by these three, so its values are numbers or of such a kind."""

    def __add__(self, other: Self, /) -> Self: ...

    def __sub__(self, other: Self, /) -> Self: ...

    def __mul__(self, factor: float, /) -> Self: ...
