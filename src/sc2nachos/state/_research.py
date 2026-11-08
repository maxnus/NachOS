"""One research this player has running or queued."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, final

from sc2nachos.ids import UpgradeId
from sc2nachos.units import Progress

if TYPE_CHECKING:
    from sc2nachos.units import OwnUnit


@final
@dataclass(frozen=True, slots=True)
class Research(Progress[UpgradeId]):
    """One research this player has running, or queued behind another, as a structure's orders show it, and how far it
    has got.

    `fraction` is the game's, 0 for one queued behind another. `total_time` is the tables' research time, a whole number
    of steps (in game), and `time_left` is exact at the normal rate (in game): a chrono boost makes it sooner, and one
    queued behind another has all its time left and waits for the one ahead first.
    """

    unit: OwnUnit[Any]
    """The structure researching it."""
