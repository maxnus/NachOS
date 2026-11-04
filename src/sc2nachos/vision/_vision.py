"""What this player sees, and when it last saw each tile."""

from __future__ import annotations

from typing import TYPE_CHECKING, final

import numpy

from sc2nachos.gametime import GameTime
from sc2nachos.geometry import Grid
from sc2nachos.protocol import ProtocolError

if TYPE_CHECKING:
    from sc2nachos.gamemap import GameMap
    from sc2nachos.geometry import Area, PointLike
    from sc2nachos.state._state import _State

_NEVER_IN_SIGHT = -1


@final
class Vision:
    """What this player sees, each grid over the playable area like the map's: `in_sight`, where it can see now;
    `explored`, where it has seen at any point in the game; and `last_in_sight`, when it last saw each tile.
    `time_unseen` says how long ago a tile or an area was last in sight."""

    __slots__ = ("_handed_out", "_last_in_sight", "_stamped", "_state", "_step")

    def __init__(self, last_in_sight: Grid[int], state: _State, step: int) -> None:
        """Vision as of `state`, the observation at `step`, which is yet to be stamped, with the steps stamped before it
        in `last_in_sight`, which it goes on stamping."""
        self._last_in_sight = last_in_sight
        self._state = state
        self._step = step
        self._stamped = False
        self._handed_out: Grid[int] | None = None

    @classmethod
    def _build_at_start(cls, game_map: GameMap, *, state: _State, step: int) -> Vision:
        """Vision at the start of a game on `game_map`, with no tile yet in sight, as of `state`, the first
        observation, at `step`."""
        never = Grid.like(game_map.pathing, dtype=int, outside=_NEVER_IN_SIGHT)
        never.fill(_NEVER_IN_SIGHT)
        return cls(never, state, step)

    @property
    def in_sight(self) -> Grid[bool]:
        """Where this player can see now."""
        return self._state.vision

    @property
    def explored(self) -> Grid[bool]:
        """Where this player has seen at any point in the game."""
        return self._state.explored

    @property
    def last_in_sight(self) -> Grid[int]:
        """The step of the last observation that showed each tile in sight: -1 for a tile never in sight, and off the
        grid. A grid read in one turn stays as it was in later ones. A tile in sight only between two observations
        is never stamped, though `explored` shows it.

        Raises `ProtocolError` if this observation reported no vision, as `in_sight` does.
        """
        if (handed_out := self._handed_out) is None:
            handed_out = self._handed_out = self._stamped_steps().copy().as_readonly()
        return handed_out

    def time_unseen(self, where: PointLike | Area) -> GameTime | None:
        """How long ago `where`, a tile or any tile of an area, was last in sight: zero while in sight, and `None` if
        never in sight, as for a point off the grid.

        Raises `ProtocolError` if this observation reported no vision, as `in_sight` does.
        """
        last = int(numpy.max(self._stamped_steps()[where], initial=_NEVER_IN_SIGHT))
        return None if last == _NEVER_IN_SIGHT else GameTime(steps=self._step - last)

    def _update(self, state: _State, step: int) -> None:
        """Take in `state`, the observation at `step`, stamping `step` on each tile it shows in sight. An observation
        whose vision cannot be read is not stamped, and the reads that need its stamp raise as `in_sight` does."""
        self._state = state
        self._step = step
        self._handed_out = None
        try:
            in_sight = state.vision
        except ProtocolError:
            self._stamped = False
            return
        self._last_in_sight._data[in_sight.values] = step
        self._stamped = True

    def _stamped_steps(self) -> Grid[int]:
        """The steps stamped up to this observation. Raises `ProtocolError` if this observation reported no vision."""
        if not self._stamped:
            # Reading the vision again raises why it could not be stamped.
            _ = self._state.vision
        return self._last_in_sight
