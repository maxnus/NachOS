"""One order a bot gave, from the turn it was given to what became of it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, final

from sc2nachos.orders._order_state import OrderState

if TYPE_CHECKING:
    from sc2nachos.gamedata import OrderBehavior
    from sc2nachos.ids import AbilityId
    from sc2nachos.state import ActionError, ActionResult
    from sc2nachos.units import OwnUnit, Target


@final
class Order[T]:
    """An order a bot gave, which it holds on to for as long as it cares what became of it.

    `T` is whatever the bot attached as `data`, which NachOS carries and never reads.
    """

    __slots__ = (
        "_ability",
        "_behavior",
        "_data",
        "_error",
        "_forced",
        "_given_step",
        "_queued",
        "_state",
        "_taken_by",
        "_target",
        "_units",
        "_verdict",
    )

    def __init__(
        self,
        ability: AbilityId,
        units: tuple[OwnUnit[Any], ...],
        target: Target | None,
        *,
        queued: bool,
        data: T,
        behavior: OrderBehavior,
        step: int,
        forced: bool = False,
    ) -> None:
        """An order of `ability` to `units`, given at `step`. `api.order.issue` makes these; a bot does not."""
        self._ability = ability
        self._units = units
        self._target = target
        self._queued = queued
        self._data = data
        self._behavior = behavior
        self._given_step = step
        # Sent even where the unit is already carrying it out, which is how a queue is cleared.
        self._forced = forced
        self._state = OrderState.GIVEN
        self._verdict: ActionResult | None = None
        self._error: ActionError | None = None
        self._taken_by: tuple[OwnUnit[Any], ...] = ()

    def __repr__(self) -> str:
        units = self._units[0] if len(self._units) == 1 else f"{len(self._units)} units"
        return f"Order({self._ability.name}, {units}, {self._state.value})"

    @property
    def ability(self) -> AbilityId:
        """What was ordered, as it was ordered: a unit carrying it out reports the exact id it runs."""
        return self._ability

    @property
    def units(self) -> tuple[OwnUnit[Any], ...]:
        """The units it was given to."""
        return self._units

    @property
    def target(self) -> Target | None:
        """Where they were sent, the unit they were sent at, or `None`."""
        return self._target

    @property
    def queued(self) -> bool:
        """Whether it was sent to go behind what each unit already has, rather than to replace it."""
        return self._queued

    @property
    def behavior(self) -> OrderBehavior:
        """What the ability does to what a unit is already doing, which decides what it competes with."""
        return self._behavior

    @property
    def data(self) -> T:
        """What the bot attached when it gave the order."""
        return self._data

    @property
    def state(self) -> OrderState:
        """How far it has got."""
        return self._state

    @property
    def verdict(self) -> ActionResult | None:
        """What the game answered it with, or `None` while it has not been sent."""
        return self._verdict

    @property
    def error(self) -> ActionError | None:
        """The action error the game gave it up with, or `None`."""
        return self._error

    @property
    def given_step(self) -> int:
        """The step of the observation the bot gave it in, which is the step it was sent at: a turn's orders go out
        before the game steps again."""
        return self._given_step

    @property
    def taken_by(self) -> tuple[OwnUnit[Any], ...]:
        """The units the game reported carrying it out, which a group order need not give to all of its units."""
        return self._taken_by

    def withdraw(self) -> None:
        """Take it back. One not sent yet never is; one already sent is only forgotten, and the unit goes on with
        it. One the game is already done with is left as it is, so how it ended is not lost."""
        if not self._state.is_final:
            self._state = OrderState.WITHDRAWN
