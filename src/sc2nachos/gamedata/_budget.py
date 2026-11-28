"""What a player has left to spend."""

from __future__ import annotations

from dataclasses import dataclass
from typing import final

from sc2nachos.gamedata._cost import Cost
from sc2nachos.gamedata._resources import Resources


@final
@dataclass(frozen=True, slots=True)
class Budget:
    """What a player has left to spend: minerals, vespene and the supply free under its cap. `api.budget` is what the
    game last reported, less what the turn's orders will cost once they go out, and a bot giving several orders in a
    turn takes each one's cost off as it goes: `budget -= cost`.

    Not a `Resources`, which holds no supply, nor a `Cost`, which is a price: `covers` says whether it pays for one.
    """

    minerals: float
    """Minerals."""
    vespene: float
    """Vespene."""
    supply: float
    """The supply free under the cap: negative once units that provided supply are lost, or once more is taken off than
    was free."""

    @property
    def resources(self) -> Resources:
        """The minerals and vespene, without the supply."""
        return Resources(self.minerals, self.vespene)

    def covers(self, cost: Cost) -> bool:
        """Whether it pays for `cost`: at least its minerals and its vespene, and the supply it takes if it takes any. A
        cost that takes none, a structure's, is paid for however little supply is free, so that a player over its cap
        can still build what provides more."""
        return (
            self.minerals >= cost.minerals
            and self.vespene >= cost.vespene
            and (cost.supply <= 0 or self.supply >= cost.supply)
        )

    def __sub__(self, cost: Cost) -> Budget:
        """What is left once `cost` is paid: negative where it was more, and more supply free for a cost that gives
        supply back, a spawning pool's."""
        return Budget(self.minerals - cost.minerals, self.vespene - cost.vespene, self.supply - cost.supply)
