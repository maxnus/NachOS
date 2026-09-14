"""The supply a player's units take and its structures provide."""

from __future__ import annotations

from dataclasses import dataclass
from typing import final


@final
@dataclass(frozen=True, slots=True)
class Supply:
    """The supply this player's units take and its structures and units provide."""

    used: float
    """What this player's units take, those in production included. A zergling or a baneling takes half."""
    cap: int
    """What this player's structures and units provide, as far as the game allows."""
    army: float
    """What this player's units other than workers take."""
    workers: int
    """What this player's workers take, those in production left out."""

    @property
    def left(self) -> float:
        """What is still free under the cap, which is negative once units that provided supply are lost."""
        return self.cap - self.used
