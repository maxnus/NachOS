"""The unit counts the game's interface shows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import final


@final
@dataclass(frozen=True, slots=True)
class UiUnitCounts:
    """The counts of this player's units the game's interface shows beside the minimap, as the game counts them."""

    idle_workers: int
    """How many workers have nothing to do."""
    army: int
    """How many units other than workers there are."""
    warp_gates: int
    """How many warp gates there are."""
