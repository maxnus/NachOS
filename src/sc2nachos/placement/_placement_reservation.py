"""Ground this player keeps free for a structure it means to place."""

from __future__ import annotations

from typing import TYPE_CHECKING, final

if TYPE_CHECKING:
    from sc2nachos.geometry import Footprint, Point
    from sc2nachos.ids import UnitTypeId


@final
class PlacementReservation:
    """The ground a structure would cover at a site, which `api.placement.reserve` keeps out of `free`, `free_for`,
    `is_free` and `nearest_free` from the step it was made at until it ends: once withdrawn, once `max_steps` steps
    have passed, or at the first observation showing one of this player's structures standing on its site, whatever
    type it stands as. An ended reservation never comes back."""

    __slots__ = ("_at", "_footprint", "_max_steps", "_step", "_type_id", "_withdrawn")

    def __init__(
        self, type_id: UnitTypeId, footprint: Footprint, *, at: Point, step: int, max_steps: int | None
    ) -> None:
        """A reservation for a structure of `type_id`, which covers `footprint`, centered at `at`, made at `step`."""
        self._type_id = type_id
        self._footprint = footprint
        self._at = at
        self._step = step
        self._max_steps = max_steps
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
    def step(self) -> int:
        """The step it was made at."""
        return self._step

    @property
    def max_steps(self) -> int | None:
        """The most steps it lasts, or `None` to last until it is withdrawn or construction starts on its site."""
        return self._max_steps

    def withdraw(self) -> None:
        """End the reservation at once, for the rest of the turn too. Withdrawing it again does nothing."""
        self._withdrawn = True

    def __repr__(self) -> str:
        return f"PlacementReservation({self._type_id.name} at {self._at}, step {self._step}, max {self._max_steps})"
