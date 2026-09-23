"""The cost of making something."""

from __future__ import annotations

from dataclasses import dataclass
from typing import final

from sc2nachos.gamedata._resources import Resources


@final
@dataclass(frozen=True, slots=True)
class Cost:
    """The cost of making something: minerals, vespene and supply.

    Not a `Resources`, which is an amount a player holds, earns or spends. Supply is room under the cap, so compare
    `cost.resources` against `api.resources` and `cost.supply` against `api.supply.left`. A cost has no time; that is
    `build_steps` or `research_steps` on the row.
    """

    minerals: float
    """Minerals."""
    vespene: float
    """Vespene."""
    supply: float = 0.0
    """Supply taken, or given back when negative: -1 for a spawning pool, which uses up a drone."""

    @property
    def resources(self) -> Resources:
        """The minerals and vespene, to compare against what the player has."""
        return Resources(self.minerals, self.vespene)

    def __add__(self, other: Cost) -> Cost:
        """The sum of the two costs."""
        return Cost(self.minerals + other.minerals, self.vespene + other.vespene, self.supply + other.supply)

    def __sub__(self, other: Cost) -> Cost:
        """This cost less `other`."""
        return Cost(self.minerals - other.minerals, self.vespene - other.vespene, self.supply - other.supply)

    def __mul__(self, count: float) -> Cost:
        """This cost `count` times over."""
        return Cost(self.minerals * count, self.vespene * count, self.supply * count)

    def __rmul__(self, count: float) -> Cost:
        """This cost `count` times over."""
        return self * count

    def __truediv__(self, divisor: float) -> Cost:
        """This cost divided by `divisor`."""
        return Cost(self.minerals / divisor, self.vespene / divisor, self.supply / divisor)

    def __neg__(self) -> Cost:
        """This cost negated: given back rather than paid."""
        return Cost(-self.minerals, -self.vespene, -self.supply)
