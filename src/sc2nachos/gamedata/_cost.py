"""What making something takes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import final

from sc2nachos.gamedata._resources import Resources


@final
@dataclass(frozen=True, slots=True)
class Cost:
    """What making something takes: minerals, vespene and supply.

    Not `Resources`, which is an amount a player holds, earns or spends: supply is room under the cap, so hold
    `cost.resources` against `api.resources` and `cost.supply` against `api.supply.left`. It has no time, since build
    times overlap and adding them is wrong nearly everywhere; that is `build_steps` or `research_steps` on the row.
    """

    minerals: float
    """Minerals."""
    vespene: float
    """Vespene."""
    supply: float = 0.0
    """Supply taken of the cap, or given back where it is negative: -1 for a spawning pool, whose drone is used up."""

    @property
    def resources(self) -> Resources:
        """The minerals and vespene, to hold against what the player has."""
        return Resources(self.minerals, self.vespene)

    def __add__(self, other: Cost) -> Cost:
        """Both costs together."""
        return Cost(self.minerals + other.minerals, self.vespene + other.vespene, self.supply + other.supply)

    def __sub__(self, other: Cost) -> Cost:
        """What this costs beyond `other`."""
        return Cost(self.minerals - other.minerals, self.vespene - other.vespene, self.supply - other.supply)

    def __mul__(self, count: float) -> Cost:
        """What `count` of this cost."""
        return Cost(self.minerals * count, self.vespene * count, self.supply * count)

    def __rmul__(self, count: float) -> Cost:
        """What `count` of this cost."""
        return self * count
