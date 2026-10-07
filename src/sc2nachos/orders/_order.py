"""One order a bot gave."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, final

if TYPE_CHECKING:
    from sc2nachos.gamedata import OrderBehavior
    from sc2nachos.ids import AbilityId
    from sc2nachos.units import OwnUnit, Target


@final
class Order[T]:
    """An order a bot gave. A refusal by the game shows in `api.action_failures`.

    `T` is whatever the bot attached as `data`. NachOS carries it and never reads it.
    """

    __slots__ = (
        "_ability",
        "_order_behavior",
        "_data",
        "_issued_step",
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
        order_behavior: OrderBehavior,
        step: int,
    ) -> None:
        """An order of `ability` to `units`, issued at `step`. Made by the order book, never by a bot."""
        self._ability = ability
        self._units = units
        self._target = target
        self._queued = queued
        self._data = data
        self._order_behavior = order_behavior
        self._issued_step = step
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
        """The units the order was given to. An order NachOS holds until its unit can start it, given to several, is
        given to the one NachOS picked: the one that can start it soonest."""
        return self._units

    @property
    def target(self) -> Target | None:
        """The point or unit the order is aimed at, or `None`."""
        return self._target

    @property
    def queued(self) -> bool:
        """Whether the order goes behind each unit's current orders, those NachOS holds included, instead of replacing
        them. An order NachOS picked a unit for reads `True` where that unit had something to do already."""
        return self._queued

    @property
    def order_behavior(self) -> OrderBehavior:
        """What the ability does to the current orders of the units it was given to: the behavior their types share,
        or `REPLACES` where they differ."""
        return self._order_behavior

    @property
    def data(self) -> T:
        """Whatever the bot attached when it gave the order."""
        return self._data

    @property
    def issued_step(self) -> int:
        """The step of the observation the order was issued in."""
        return self._issued_step

    def withdraw(self) -> None:
        """Take back whatever of the order has not gone out: all of it while its turn's handlers run, and what NachOS
        still holds of it after."""
        self._withdrawn = True
