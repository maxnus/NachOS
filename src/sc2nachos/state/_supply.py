"""The supply a player's units take and its structures provide."""

from __future__ import annotations

from dataclasses import dataclass
from typing import final


@final
@dataclass(frozen=True, slots=True)
class Supply:
    """The supply this player's units take and its structures and units provide."""

    used: float
    """The supply this player's units take, including those in production. A zergling or a baneling takes half."""
    cap: int
    """The supply this player's structures and units provide, up to the game's limit."""
    army: float
    """The supply this player's units other than workers take."""
    workers: int
    """The supply this player's workers take, not counting those in production."""

    @property
    def left(self) -> float:
        """The supply still free under the cap. Negative once units that provided supply are lost."""
        return self.cap - self.used
