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
        "_pending",
        "_queued",
        "_target",
        "_units",
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
        """An order of `ability` to `units`, issued at `step`. Made by `api.orders.issue`, never by a bot."""
        self._ability = ability
        self._units = units
        self._target = target
        self._queued = queued
        self._data = data
        self._order_behavior = order_behavior
        self._issued_step = step
        # Until the turn is sent or the bot withdraws it.
        self._pending = True

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
        """Whether the order was queued behind each unit's current orders instead of replacing them."""
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
        """The step of the observation the order was issued in. It is also the step it was sent at, since a turn's
        orders go out before the game steps again."""
        return self._issued_step

    def withdraw(self) -> None:
        """Take the order back, so it is never sent. An order whose turn has been sent is left as it is."""
        self._pending = False
