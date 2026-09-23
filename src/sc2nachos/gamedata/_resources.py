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
