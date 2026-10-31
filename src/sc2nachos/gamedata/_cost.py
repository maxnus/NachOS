"""The cost of making something."""

from __future__ import annotations

from dataclasses import dataclass
from typing import final

from sc2nachos.game_time import GameTime
from sc2nachos.gamedata._resources import Resources


@final
@dataclass(frozen=True, slots=True)
class Cost:
    """The cost of making something: minerals, vespene, supply and the time it takes.

    Not a `Resources`, which is an amount a player holds, earns or spends. Supply is room under the cap, so compare
    `cost.resources` against `api.resources` and `cost.supply` against `api.supply.left`. Costs added up add their
    times too, which makes the production time they take one after another, not how long they take side by side.
    """

    minerals: float
    """Minerals."""
    vespene: float
    """Vespene."""
    supply: float = 0.0
    """Supply taken, or given back when negative: -1 for a spawning pool, which uses up a drone."""
    time: GameTime = GameTime.ZERO
    """The time production takes, at the normal rate: a whole number of steps from the tables, fractional once scaled
    or divided; zero for an order that makes nothing, for a switch that costs nothing, such as a siege, and where the
    tables give none, as for an interceptor."""

    @property
    def resources(self) -> Resources:
        """The minerals and vespene, to compare against what the player has."""
        return Resources(self.minerals, self.vespene)

    @property
    def is_free(self) -> bool:
        """Whether it takes no minerals, vespene or supply, whatever its time: a siege, a MULE's calldown."""
        return not (self.minerals or self.vespene or self.supply)

    def __add__(self, other: Cost) -> Cost:
        """The sum of the two costs."""
        return Cost(
            self.minerals + other.minerals,
            self.vespene + other.vespene,
            self.supply + other.supply,
            self.time + other.time,
        )

    def __sub__(self, other: Cost) -> Cost:
        """This cost less `other`."""
        return Cost(
            self.minerals - other.minerals,
            self.vespene - other.vespene,
            self.supply - other.supply,
            self.time - other.time,
        )

    def __mul__(self, count: float) -> Cost:
        """This cost `count` times over."""
        return Cost(self.minerals * count, self.vespene * count, self.supply * count, self.time * count)

    def __rmul__(self, count: float) -> Cost:
        """This cost `count` times over."""
        return self * count

    def __truediv__(self, divisor: float) -> Cost:
        """This cost divided by `divisor`."""
        return Cost(self.minerals / divisor, self.vespene / divisor, self.supply / divisor, self.time / divisor)

    def __neg__(self) -> Cost:
        """This cost negated: given back rather than paid."""
        return Cost(-self.minerals, -self.vespene, -self.supply, -self.time)
