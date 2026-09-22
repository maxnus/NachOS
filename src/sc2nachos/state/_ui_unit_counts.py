"""The unit counts the game's interface shows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import final


@final
@dataclass(frozen=True, slots=True)
class UiUnitCounts:
    """The counts of this player's units the game's interface shows beside the minimap, as the game counts them."""

    idle_workers: int
    """The workers with nothing to do."""
    army: int
    """The units other than workers."""
    warp_gates: int
    """The warp gates."""
