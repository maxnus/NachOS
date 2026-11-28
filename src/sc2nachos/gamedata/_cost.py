"""The cost of making something."""

from __future__ import annotations

from dataclasses import dataclass
from typing import final

from sc2nachos._shared.vector_arithmetic import _VectorArithmetic
from sc2nachos.gamedata._resources import Resources, ResourcesAndSupply
from sc2nachos.gametime import GameTime


@final
@dataclass(frozen=True, slots=True)
class Cost(_VectorArithmetic):
    """The cost of making something: minerals, vespene, supply and the time it takes.

    Not a `Resources`, which is an amount a player holds, earns or spends, nor a `ResourcesAndSupply`, which has no
    time: `api.budget.covers(cost)` says whether what is left pays for it. Costs added up add their times too, which
    makes the production time they take one after another, not how long they take side by side.
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

    def to_tuple(self) -> tuple[float, float, float, GameTime]:
        """Its minerals, vespene, supply and time."""
        return (self.minerals, self.vespene, self.supply, self.time)

    @property
    def resources(self) -> Resources:
        """The minerals and vespene, to compare against what the player has."""
        return Resources(self.minerals, self.vespene)

    @property
    def resources_and_supply(self) -> ResourcesAndSupply:
        """The minerals, vespene and supply, without the time."""
        return ResourcesAndSupply(self.minerals, self.vespene, self.supply)

    @property
    def is_free(self) -> bool:
        """Whether it takes no minerals, vespene or supply, whatever its time: a siege, a MULE's calldown."""
        return not (self.minerals or self.vespene or self.supply)
