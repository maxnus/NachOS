"""Arithmetic on an amount made of several parts, a part at a time."""

from __future__ import annotations

import operator
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any, Self


class _VectorArithmetic(ABC):
    """Arithmetic on an amount made of several parts, each part on its own: `Resources`, `ResourcesAndSupply` and
    `Cost`. A subclass gives its parts in order with `to_tuple`, as its constructor takes them, and gets the sum and the
    difference of two of its own kind, a multiple, a share and the negation, each of its kind. One of another kind added
    or taken off raises `TypeError`, so that no part of it is silently dropped, unless the subclass takes it itself, as
    `ResourcesAndSupply` takes a `Cost` off.
    """

    __slots__ = ()

    @abstractmethod
    def to_tuple(self) -> tuple[Any, ...]:
        """Its parts in order, as its constructor takes them."""

    def __add__(self, other: Self) -> Self:
        """The sum of the two, part by part."""
        if type(other) is not type(self):
            return NotImplemented
        constructor: Callable[..., Self] = type(self)
        return constructor(*map(operator.add, self.to_tuple(), other.to_tuple()))

    def __sub__(self, other: Self) -> Self:
        """This less `other`, part by part: negative where `other` is more."""
        if type(other) is not type(self):
            return NotImplemented
        constructor: Callable[..., Self] = type(self)
        return constructor(*map(operator.sub, self.to_tuple(), other.to_tuple()))

    def __mul__(self, factor: float) -> Self:
        """This `factor` times over."""
        constructor: Callable[..., Self] = type(self)
        return constructor(*[part * factor for part in self.to_tuple()])

    def __rmul__(self, factor: float) -> Self:
        """This `factor` times over."""
        return self * factor

    def __truediv__(self, divisor: float) -> Self:
        """This divided by `divisor`."""
        constructor: Callable[..., Self] = type(self)
        return constructor(*[part / divisor for part in self.to_tuple()])

    def __neg__(self) -> Self:
        """This negated: owed rather than held, or given back rather than paid."""
        constructor: Callable[..., Self] = type(self)
        return constructor(*map(operator.neg, self.to_tuple()))
