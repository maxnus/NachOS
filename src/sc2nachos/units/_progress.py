"""How far the making of one unit or upgrade has got."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sc2nachos.ids import UnitTypeId, UpgradeId

if TYPE_CHECKING:
    from sc2nachos.gametime import GameTime


@dataclass(frozen=True, slots=True)
class Progress[T: UnitTypeId | UpgradeId]:
    """How far the making of one unit or upgrade has got: what it makes, how much of it is done and how long it has
    left. `InProduction` and `Research` are each one, with what makes it."""

    product: T
    """What is being made."""
    fraction: float
    """How far along it is, from 0 to 1."""
    total_time: GameTime
    """The time the whole of it takes, a whole number of steps."""
    time_left: GameTime
    """The time until it is done, a whole number of steps."""
