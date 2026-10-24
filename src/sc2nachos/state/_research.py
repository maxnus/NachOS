"""One research this player has running or queued."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, final

if TYPE_CHECKING:
    from sc2nachos.game_time import GameTime
    from sc2nachos.ids import UpgradeId
    from sc2nachos.units import OwnUnit


@final
@dataclass(frozen=True, slots=True)
class Research:
    """One research this player has running, or queued behind another, as a structure's orders show it."""

    upgrade: UpgradeId
    """What is being researched."""
    unit: OwnUnit[Any]
    """The structure researching it."""
    total_time: GameTime
    """The time the whole research takes, a whole number of steps, as the game's tables give it (in game)."""
    time_left: GameTime
    """The time until it finishes, a whole number of steps, exactly at the normal rate (in game): a chrono boost makes
    it sooner, and one queued behind another has all its time left and waits for the one ahead first."""

    @property
    def progress(self) -> float:
        """How far along it is, from 0 to 1: 0 for one queued behind another."""
        return (self.total_time - self.time_left) / self.total_time
