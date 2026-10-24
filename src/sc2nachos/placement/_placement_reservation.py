"""Ground this player keeps free for a structure it means to place."""

from __future__ import annotations

from typing import TYPE_CHECKING, final

if TYPE_CHECKING:
    from sc2nachos.game_time import GameTime
    from sc2nachos.geometry import Footprint, Margin, Point
    from sc2nachos.ids import UnitTypeId


@final
class PlacementReservation:
    """The ground a structure would cover at a site, and its margin, which `api.placement.reserve` keeps out of `free`,
    `free_for`, `is_free` and `nearest_free` from the time it was made at until it ends: once withdrawn, once
    `max_time` has passed, or at the first observation showing one of this player's structures standing on its
    site, whatever type it stands as. An ended reservation never comes back."""

    __slots__ = ("_at", "_footprint", "_margin", "_max_time", "_time", "_type_id", "_withdrawn")

    def __init__(
        self,
        type_id: UnitTypeId,
        footprint: Footprint,
        *,
        at: Point,
        margin: Margin,
        time: GameTime,
        max_time: GameTime | None,
    ) -> None:
        """A reservation for a structure of `type_id`, which covers `footprint`, centered at `at`, and the tiles within
        `margin` of it, made at `time`."""
        self._type_id = type_id
        self._footprint = footprint
        self._at = at
        self._margin = margin
        self._time = time
        self._max_time = max_time
        self._withdrawn = False

    @property
    def type_id(self) -> UnitTypeId:
        """The type of structure the ground is kept for."""
        return self._type_id

    @property
    def at(self) -> Point:
        """Where the structure would center, snapped as the game snaps one."""
        return self._at

    @property
    def margin(self) -> Margin:
        """The tiles kept free beside the footprint, as `Grid.fits_footprint` takes them."""
        return self._margin

    @property
    def time(self) -> GameTime:
        """When it was made."""
        return self._time

    @property
    def max_time(self) -> GameTime | None:
        """The longest it lasts, or `None` to last until it is withdrawn or construction starts on its site."""
        return self._max_time

    def withdraw(self) -> None:
        """End the reservation at once, for the rest of the turn too. Withdrawing it again does nothing."""
        self._withdrawn = True

    def __repr__(self) -> str:
        return (
            f"PlacementReservation({self._type_id.name} at {self._at}, margin {self._margin}, made {self._time},"
            f" max {self._max_time})"
        )
