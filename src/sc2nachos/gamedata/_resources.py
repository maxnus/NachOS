"""An amount of minerals and vespene."""

from __future__ import annotations

from dataclasses import dataclass
from typing import final


@final
@dataclass(frozen=True, slots=True)
class Resources:
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

    def __add__(self, other: Resources) -> Resources:
        """The sum of the two amounts."""
        return Resources(self.minerals + other.minerals, self.vespene + other.vespene)

    def __sub__(self, other: Resources) -> Resources:
        """This amount less `other`; negative where `other` is more."""
        return Resources(self.minerals - other.minerals, self.vespene - other.vespene)

    def __neg__(self) -> Resources:
        """This amount negated: owed rather than held."""
        return Resources(-self.minerals, -self.vespene)

    def __mul__(self, factor: float) -> Resources:
        """This amount `factor` times over."""
        return Resources(self.minerals * factor, self.vespene * factor)

    def __rmul__(self, factor: float) -> Resources:
        """This amount `factor` times over."""
        return self * factor

    def __truediv__(self, divisor: float) -> Resources:
        """This amount divided by `divisor`."""
        return Resources(self.minerals / divisor, self.vespene / divisor)

    def covers(self, other: Resources) -> bool:
        """Whether this amount is at least `other` in both minerals and vespene."""
        return self.minerals >= other.minerals and self.vespene >= other.vespene

    def minimum(self, other: Resources) -> Resources:
        """The smaller of the two amounts of each resource, each on its own: 75/50 of 100/50 and 75/80."""
        return Resources(min(self.minerals, other.minerals), min(self.vespene, other.vespene))

    def maximum(self, other: Resources) -> Resources:
        """The larger of the two amounts of each resource; `maximum(Resources(0, 0))` drops what is owed."""
        return Resources(max(self.minerals, other.minerals), max(self.vespene, other.vespene))
