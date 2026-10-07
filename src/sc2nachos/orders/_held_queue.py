"""What NachOS holds for one unit until the unit can start it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, final

if TYPE_CHECKING:
    from sc2nachos.ids import AbilityId
    from sc2nachos.orders._order import Order
    from sc2nachos.units import OwnUnit


@final
class HeldQueue:
    """The orders held for one unit, head first; the lead-in sent for an order, a move to its site or a landing; and
    what went out of the queue lately.

    An order given to several units stands in the queue of each that held something when it was given. An observation
    is counted by its number, as the order book counts them.
    """

    __slots__ = ("_lead_in", "_released", "orders", "unit")

    def __init__(self, unit: OwnUnit[Any]) -> None:
        """An empty queue for `unit`."""
        self.unit = unit
        self.orders: list[Order[Any]] = []
        # The order the lead-in was sent for, its ability, and the observation it was sent in.
        self._lead_in: tuple[Order[Any], AbilityId, int] | None = None
        # Each order that went out of the queue, with the observation it went out in.
        self._released: list[tuple[Order[Any], int]] = []

    def holds_any(self) -> bool:
        """Whether the queue holds an order not withdrawn."""
        return any(not order._withdrawn for order in self.orders)

    def forget_withdrawn(self) -> None:
        """Take the withdrawn orders out of the queue."""
        self.orders = [order for order in self.orders if not order._withdrawn]

    def release(self, observation: int) -> Order[Any]:
        """Take the head out of the queue, as going out in `observation`, and return it."""
        order = self.orders.pop(0)
        self._released.append((order, observation))
        return order

    def drop(self) -> None:
        """Let go of every order held, and of the lead-in."""
        self.orders.clear()
        self._lead_in = None

    def note_lead_in(self, order: Order[Any], ability: AbilityId, observation: int) -> None:
        """Record a lead-in of `ability` sent for `order` in `observation`."""
        self._lead_in = (order, ability, observation)

    def lead_in_for(self, order: Order[Any]) -> tuple[AbilityId, int] | None:
        """The ability of the lead-in sent for `order` and the observation it was sent in, or `None` if none was."""
        if self._lead_in is None or self._lead_in[0] is not order:
            return None
        return self._lead_in[1], self._lead_in[2]

    def released_since(self, observation: int) -> list[Order[Any]]:
        """The orders that went out of the queue in `observation` or after."""
        return [order for order, released in self._released if released >= observation]

    def forget_released_before(self, observation: int) -> None:
        """Forget the orders that went out before `observation`."""
        self._released = [(order, released) for order, released in self._released if released >= observation]

    def is_empty(self) -> bool:
        """Whether the queue holds nothing and nothing went out of it in the observations remembered."""
        return not self.orders and not self._released
