"""This player, as the game describes it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, final

if TYPE_CHECKING:
    from sc2nachos.match import Race


@final
@dataclass(frozen=True, slots=True)
class Player:
    """This player: its id in the game, its race and the one it asked for, and its name. Each game has its own."""

    id: int
    """Its id in the game, which its units read as their `owner_id`."""
    race: Race
    """The race it plays, which the game gives from the start, where it asked for Random too."""
    requested_race: Race
    """The race it asked for, `RANDOM` included."""
    name: str | None
    """Its name as the game gives it, and `None` where the game gives none, as in every game seen so far, though it
    joined with one (tool `sweep_requests`)."""
