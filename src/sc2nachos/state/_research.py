"""One research this player has running or queued."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, final

if TYPE_CHECKING:
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
    progress: float
    """How far along it is, from 0 to 1: 0 for one queued behind another."""
    research_steps: int
    """Steps the whole research takes, as the game's tables give it (in game)."""

    @property
    def steps_left(self) -> int:
        """Steps until it finishes, exactly at the normal rate (in game): a chrono boost makes it sooner, and one queued
        waits for the one ahead first."""
        return round((1 - self.progress) * self.research_steps)
