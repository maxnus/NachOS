"""This player, as the game describes it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Self, final

from sc2nachos.match import Race

if TYPE_CHECKING:
    from s2clientprotocol import sc2api_pb2


@final
@dataclass(frozen=True, slots=True)
class Player:
    """This player: its id in the game, its race and the one it asked for, and its name. Each game has its own."""

    id: int
    """Its id in the game, which its units read as their `owner_id`."""
    race: Race
    """The race it plays, which the game gives from the start, where it asked for Random too. A computer's, which the
    game never gives, as when a replay is played back as one, is the race it asked for, `RANDOM` included (in game)."""
    requested_race: Race
    """The race it asked for, `RANDOM` included."""
    name: str | None
    """Its name as the game gives it, and `None` where the game gives none, as in every game seen so far, though it
    joined with one (tool `sweep_requests`)."""

    @classmethod
    def _from_game(cls, info: sc2api_pb2.ResponseGameInfo, player_id: int) -> Self:
        """The player `player_id` of the game `info` describes. Raises `ValueError` if `info` lists no such player."""
        entry = next((entry for entry in info.player_info if entry.player_id == player_id), None)
        if entry is None:
            raise ValueError(f"the game lists no player {player_id}, the one this client joined as")
        requested_race = Race(entry.race_requested)
        actual_race = Race(entry.race_actual)
        return cls(
            id=player_id,
            race=requested_race if actual_race is Race.NONE else actual_race,
            requested_race=requested_race,
            name=entry.player_name or None,
        )
