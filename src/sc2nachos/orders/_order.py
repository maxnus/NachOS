"""One order a bot gave."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, final

if TYPE_CHECKING:
    from sc2nachos.game_time import GameTime
    from sc2nachos.ids import AbilityId
    from sc2nachos.units import OwnUnit, Target


@final
class Order[T]:
    """An order a bot gave. A refusal by the game shows in `api.action_failures`.

    `T` is whatever the bot attached as `data`. NachOS carries it and never reads it.
    """

    __slots__ = (
        "_ability",
        "_data",
        "_issued_time",
        "_queued",
        "_target",
        "_units",
        "_withdrawn",
    )

    def __init__(
        self,
        ability: AbilityId,
        units: tuple[OwnUnit[Any], ...],
        target: Target | None,
        *,
        queued: bool,
        data: T,
        issued_time: GameTime,
    ) -> None:
        """An order of `ability` to `units`, issued at `issued_time`. Made by the order book, never by a bot."""
        self._ability = ability
        self._units = units
        self._target = target
        self._queued = queued
        self._data = data
        self._issued_time = issued_time
        self._withdrawn = False

    def __repr__(self) -> str:
        units = self._units[0] if len(self._units) == 1 else f"{len(self._units)} units"
        return f"Order({self._ability.name}, {units})"

    @property
    def ability(self) -> AbilityId:
        """The ability ordered, as it was ordered. A unit carrying it out reads as running the same one."""
        return self._ability

    @property
    def units(self) -> tuple[OwnUnit[Any], ...]:
        """The units the order was given to."""
        return self._units

    @property
    def target(self) -> Target | None:
        """The point or unit the order is aimed at, or `None`."""
        return self._target

    @property
    def queued(self) -> bool:
        """Whether the order goes behind each unit's current orders instead of replacing them."""
        return self._queued

    @property
    def data(self) -> T:
        """Whatever the bot attached when it gave the order."""
        return self._data

    @property
    def issued_time(self) -> GameTime:
        """The time of the observation the order was issued in."""
        return self._issued_time

    def withdraw(self) -> None:
        """Take the order back while its turn's handlers run, so it never goes out. After its turn it does nothing."""
        self._withdrawn = True
