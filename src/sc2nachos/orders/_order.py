"""One order a bot gave, and the game's answer to it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, final

from sc2nachos.orders._order_state import OrderState

if TYPE_CHECKING:
    from sc2nachos.gamedata import OrderBehavior
    from sc2nachos.ids import AbilityId
    from sc2nachos.state import ActionResult
    from sc2nachos.units import OwnUnit, Target


@final
class Order[T]:
    """An order a bot gave: whether it was sent, and the game's answer.

    `T` is whatever the bot attached as `data`. NachOS carries it and never reads it.
    """

    __slots__ = (
        "_ability",
        "_order_behavior",
        "_data",
        "_given_step",
        "_queued",
        "_state",
        "_target",
        "_units",
        "_action_result",
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
        """An order of `ability` to `units`, given at `step`. Made by `api.orders.issue`, never by a bot."""
        self._ability = ability
        self._units = units
        self._target = target
        self._queued = queued
        self._data = data
        self._order_behavior = order_behavior
        self._given_step = step
        self._state = OrderState.GIVEN
        self._action_result: ActionResult | None = None

    def __repr__(self) -> str:
        units = self._units[0] if len(self._units) == 1 else f"{len(self._units)} units"
        return f"Order({self._ability.name}, {units}, {self._state.value})"

    @property
    def ability(self) -> AbilityId:
        """The ability ordered, as it was ordered. A unit carrying it out reports the exact id it runs."""
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
        or `REPLACES` where they differ. This decides which orders it competes with."""
        return self._order_behavior

    @property
    def data(self) -> T:
        """Whatever the bot attached when it gave the order."""
        return self._data

    @property
    def state(self) -> OrderState:
        """The order's current state."""
        return self._state

    @property
    def action_result(self) -> ActionResult | None:
        """The game's answer, or `None` until the order is sent."""
        return self._action_result

    @property
    def given_step(self) -> int:
        """The step of the observation the order was given in. It is also the step it was sent at, since a turn's
        orders go out before the game steps again. A repeat keeps the step it was first given at."""
        return self._given_step

    def withdraw(self) -> None:
        """Take the order back, so it is never sent. An order already sent, a repeat included, is left as it is, and
        so is one overridden."""
        if self._state is OrderState.GIVEN:
            self._state = OrderState.WITHDRAWN

    def _replace_data(self, data: T) -> None:
        """Carry `data` in place of what the order carried. Only the order book calls this, when the order is
        repeated."""
        self._data = data

    def _settle(self, state: OrderState, *, action_result: ActionResult | None = None) -> None:
        """Record the order's final state, and the game's answer if it was sent. Only the order book calls this."""
        self._state = state
        self._action_result = action_result
