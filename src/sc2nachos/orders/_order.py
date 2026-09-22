"""One order a bot gave, and what became of it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, final

from sc2nachos.orders._order_state import OrderState

if TYPE_CHECKING:
    from sc2nachos.gamedata import OrderBehavior
    from sc2nachos.ids import AbilityId
    from sc2nachos.state import ActionFailure, ActionResult
    from sc2nachos.units import OwnUnit, Target


@final
class Order[T]:
    """An order a bot gave. Keep it to find out what became of the order.

    `T` is whatever the bot attached as `data`. NachOS carries it and never reads it.
    """

    __slots__ = (
        "_ability",
        "_order_behavior",
        "_data",
        "_failure",
        "_forced",
        "_given_step",
        "_queued",
        "_seen_carrying",
        "_sent_to",
        "_state",
        "_taken_by",
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
        forced: bool = False,
    ) -> None:
        """An order of `ability` to `units`, given at `step`. Made by `api.orders.issue`, never by a bot."""
        self._ability = ability
        self._units = units
        self._target = target
        self._queued = queued
        self._data = data
        self._order_behavior = order_behavior
        self._given_step = step
        # Sent even if the unit is already carrying it out; this is how a queue is cleared.
        self._forced = forced
        self._state = OrderState.GIVEN
        self._sent_to: tuple[OwnUnit[Any], ...] = ()
        self._action_result: ActionResult | None = None
        self._failure: ActionFailure | None = None
        self._taken_by: tuple[OwnUnit[Any], ...] = ()
        # The units seen carrying the order out in the first observation that showed any doing so. With one command
        # to a group, that is not always all of them.
        self._seen_carrying: frozenset[OwnUnit[Any]] = frozenset()

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
        """What the ability does to a unit's current orders. This decides which orders it competes with."""
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
    def failure(self) -> ActionFailure | None:
        """The failure the game gave the order up with, or `None`."""
        return self._failure

    @property
    def given_step(self) -> int:
        """The step of the observation the order was given in. It is also the step it was sent at, since a turn's
        orders go out before the game steps again."""
        return self._given_step

    @property
    def _acting_units(self) -> tuple[OwnUnit[Any], ...]:
        """The units the order was sent to. Until the turn resolves overrides, that is every unit it names."""
        return self._sent_to or self._units

    @property
    def taken_by(self) -> tuple[OwnUnit[Any], ...]:
        """The units the game reported carrying the order out. A group order need not reach all of its units."""
        return self._taken_by

    def withdraw(self) -> None:
        """Take the order back. An order not yet sent is never sent. An order already sent is only forgotten: the
        unit carries on with it. An order the game is already done with keeps its final state."""
        if not self._state.is_final:
            self._state = OrderState.WITHDRAWN

    def _settle(
        self,
        state: OrderState | None = None,
        *,
        sent_to: tuple[OwnUnit[Any], ...] | None = None,
        taken_by: tuple[OwnUnit[Any], ...] | None = None,
        seen_carrying: frozenset[OwnUnit[Any]] | None = None,
        action_result: ActionResult | None = None,
        failure: ActionFailure | None = None,
    ) -> None:
        """Record what the order book found out: the order's new state, if `state` is given, and anything else it
        learned. Only the order book calls this. Besides `withdraw`, it is the one way an order changes after it is
        given."""
        if state is not None:
            self._state = state
        if sent_to is not None:
            self._sent_to = sent_to
        if taken_by is not None:
            self._taken_by = taken_by
        if seen_carrying is not None:
            self._seen_carrying = seen_carrying
        if action_result is not None:
            self._action_result = action_result
        if failure is not None:
            self._failure = failure
