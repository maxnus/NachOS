"""An amount of minerals and vespene."""

from __future__ import annotations

from dataclasses import dataclass
from typing import final

from sc2nachos.gamedata._vector_arithmetic import _VectorArithmetic


@final
@dataclass(frozen=True, slots=True)
class Resources(_VectorArithmetic):
    """An amount of minerals and vespene: spent, held, earned or averaged.

    Whole when it comes from the game or from adding such amounts up; fractional once scaled or divided.
    """

    minerals: float
    """Minerals."""
    vespene: float
    """Vespene."""

    @property
    def total(self) -> float:
        """Minerals plus vespene, counted alike."""
        return self.minerals + self.vespene

    def to_tuple(self) -> tuple[float, float]:
        """Its minerals and vespene."""
        return (self.minerals, self.vespene)

    def covers(self, other: Resources) -> bool:
        """Whether this amount is at least `other` in both minerals and vespene."""
        return self.minerals >= other.minerals and self.vespene >= other.vespene

    def minimum(self, other: Resources) -> Resources:
        """The smaller of the two amounts of each resource, each on its own: 75/50 of 100/50 and 75/80."""
        return Resources(min(self.minerals, other.minerals), min(self.vespene, other.vespene))

    def maximum(self, other: Resources) -> Resources:
        """The larger of the two amounts of each resource; `maximum(Resources(0, 0))` drops what is owed."""
        return Resources(max(self.minerals, other.minerals), max(self.vespene, other.vespene))
