"""The orders a bot gives in a turn, sent after its handlers have run."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, final, overload

from s2clientprotocol import raw_pb2, sc2api_pb2

from sc2nachos.gamedata import OrderBehavior
from sc2nachos.geometry import Point
from sc2nachos.geometry._point import coordinates
from sc2nachos.ids import AbilityId
from sc2nachos.orders._commands import create_camera_move_action, create_unit_command_action
from sc2nachos.orders._order import Order
from sc2nachos.orders._order_state import OrderState
from sc2nachos.orders._targets import aimed_at, as_sent, check_target, order_target, same_point, same_target
from sc2nachos.protocol import ProtocolError
from sc2nachos.state import ActionResult
from sc2nachos.units import Unit

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Iterator, Sequence

    from sc2nachos.gamedata import AbilityData, GameData
    from sc2nachos.geometry import PointLike
    from sc2nachos.protocol import Client
    from sc2nachos.units import OwnUnit, Target

# The observations after the one a turn read by which its orders show in a unit's orders: the next in a stepped game,
# and the one after that on the ladder or in realtime (docs/game-behavior.md).
_SHOWN_WITHIN = 2


@final
class OrderBook:
    """This player's orders for the turn.

    A bot gives orders through `api.orders` while its handlers run. They are sent in one request after the turn's
    last handler returns. A unit carries out only the last order it is given in a turn, plus any abilities it
    carries out at once.
    """

    __slots__ = (
        "_camera_location",
        "_clearing",
        "_game_data",
        "_issued",
        "_issued_by_unit",
        "_last_sent",
        "_observations",
        "_step",
    )

    def __init__(self, game_data: GameData) -> None:
        """A book for one game. `game_data` says what each ability does and what it must be aimed at."""
        self._game_data = game_data
        # The turn's orders in the order they were issued, and those of each unit by its id.
        self._issued: list[Order[Any]] = []
        self._issued_by_unit: dict[int, list[Order[Any]]] = {}
        # The turn's orders from `clear_queue`, sent to a unit already carrying them out.
        self._clearing: set[Order[Any]] = set()
        # By unit id, the last unqueued order the game took that replaces the unit's orders, and the number of the
        # observation its turn read.
        self._last_sent: dict[int, tuple[Order[Any], int]] = {}
        self._camera_location: Point | None = None
        self._observations = 0
        self._step = 0

    @overload
    def issue(
        self,
        units: OwnUnit[Any] | Iterable[OwnUnit[Any]],
        ability: AbilityId,
        *,
        target: PointLike | Unit[Any] | None = None,
        queued: bool = False,
    ) -> Order[None]: ...

    @overload
    def issue[T](
        self,
        units: OwnUnit[Any] | Iterable[OwnUnit[Any]],
        ability: AbilityId,
        *,
        target: PointLike | Unit[Any] | None = None,
        queued: bool = False,
        data: T,
    ) -> Order[T]: ...

    def issue(
        self,
        units: OwnUnit[Any] | Iterable[OwnUnit[Any]],
        ability: AbilityId,
        *,
        target: PointLike | Unit[Any] | None = None,
        queued: bool = False,
        data: Any = None,
    ) -> Order[Any]:
        """Order `units` to use `ability`, aimed at a point, a unit or nothing, and return the `Order`.

        The order goes out as one command, so units ordered together keep their spacing where the game spreads them.
        `queued` puts the order behind each unit's current orders. `data` is the bot's own; NachOS carries it and
        never reads it. Nothing is sent until the turn's handlers have all run.

        An unqueued order that replaces a unit's orders is not sent to a unit already doing it: sending it would only
        drop what the unit has queued behind it. What a unit is doing is the last such order sent to it, while the
        observation after its turn may not show it yet, and otherwise its first reported order. An order left with no
        unit to send to reads `REDUNDANT`.

        Raises `TypeError` for a target the ability cannot be aimed at.
        """
        given = (units,) if isinstance(units, Unit) else tuple(units)
        if not given:
            raise ValueError("an order needs a unit to give it to")
        row = self._game_data.abilities.get(ability)
        aimed = aimed_at(target)
        check_target(ability, aimed, row)
        order = Order(
            ability,
            given,
            aimed,
            queued=queued,
            data=data,
            order_behavior=_order_behavior(row, given),
            step=self._step,
        )
        self._add(order)
        return order

    def clear_queue(self, unit: OwnUnit[Any]) -> Order[None] | None:
        """Drop `unit`'s queued orders, leaving the one it is carrying out, or return `None` if there is nothing to
        drop.

        The unit's current order is sent again, unqueued. The game answers `SUCCESS`, carries nothing out for it, and
        drops everything queued behind it (in game). An order given to the unit this turn overrides it, like any
        other.

        Returns `None` if the unit has nothing queued, if its current order is an ability NachOS cannot name, or if
        that ability is a train, a research or a morph: the game would queue a second of those behind the first
        instead of dropping anything, so a structure's queue is cancelled from its end instead.
        """
        orders = unit._latest_report.orders
        if len(orders) < 2:
            return None
        ability = self._general_ability(_ability_of_unit_order(orders[0]))
        behavior = _behavior_for(self._game_data.abilities.get(ability), unit)
        if ability is AbilityId.NULL or behavior is not OrderBehavior.REPLACES:
            return None
        order: Order[None] = Order(
            ability,
            (unit,),
            order_target(unit, orders[0]),
            queued=False,
            data=None,
            order_behavior=behavior,
            step=self._step,
        )
        self._add(order)
        self._clearing.add(order)
        return order

    def issued_to(self, unit: OwnUnit[Any]) -> tuple[Order[Any], ...]:
        """The orders issued to `unit` so far this turn, in the order they were issued.

        A handler late in a turn reads this to leave alone a unit an earlier handler has ordered, since only the last
        order a unit is given goes out. Withdrawn orders are left out.
        """
        orders = self._issued_by_unit.get(unit.id)
        if not orders:
            return ()
        return tuple(order for order in orders if order.state is OrderState.PENDING)

    @property
    def pending(self) -> tuple[Order[Any], ...]:
        """Every order issued this turn and still to be sent. Withdrawn orders are left out."""
        return tuple(order for order in self._issued if order.state is OrderState.PENDING)

    def camera(self, at: PointLike) -> None:
        """Move this player's camera to `at`, along with the turn's orders. Only the last move of a turn is sent."""
        aimed = coordinates(at)
        self._camera_location = Point((as_sent(aimed[0]), as_sent(aimed[1])))

    def _send(self, client: Client) -> None:
        """Send the turn's orders and record the game's answer on each. Sends nothing if there is nothing to send."""
        issued, clearing = self._issued, self._clearing
        self._issued, self._issued_by_unit, self._clearing = [], {}, set()
        actions: list[sc2api_pb2.Action] = []
        sent: list[tuple[Order[Any], tuple[OwnUnit[Any], ...]]] = []
        for order, units in self._orders_to_send(issued, clearing):
            actions.append(create_unit_command_action(order, units))
            sent.append((order, units))
        if self._camera_location is not None:
            actions.append(create_camera_move_action(self._camera_location))
            self._camera_location = None
        if not actions:
            return
        results = client.act(actions).result
        if len(results) < len(sent):
            raise ProtocolError(f"the game answered {len(results)} of the {len(sent)} orders it was sent")
        # A camera move, if any, is answered last and belongs to no order.
        for (order, units), result in zip(sent, results[: len(sent)], strict=True):
            action_result = ActionResult.read(result)
            taken = action_result is ActionResult.SUCCESS
            order._settle(OrderState.SENT if taken else OrderState.REFUSED, action_result=action_result)
            if taken and not order.queued:
                self._remember_sent(order, units)

    def _observe(self, step: int, dead: Iterable[Unit[Any]]) -> None:
        """Take in the observation at `step`, which found `dead` dead."""
        self._step = step
        self._observations += 1
        for unit in dead:
            self._last_sent.pop(unit.id, None)

    def _orders_to_send(
        self, issued: Sequence[Order[Any]], clearing: Collection[Order[Any]]
    ) -> Iterator[tuple[Order[Any], tuple[OwnUnit[Any], ...]]]:
        """Each order of the turn that goes out, with the units it goes out to.

        An order that acts at once, or that is queued behind a unit's current orders, competes with nothing. Of the
        rest, a unit keeps only the last it was given, and an order left with no unit is overridden. An order is then
        left out for the units already carrying it out, and one left with no unit is redundant. An order in `clearing`
        goes out regardless.
        """
        holder: dict[int, Order[Any]] = {}
        for order in issued:
            if order.state is OrderState.PENDING:
                for unit in order.units:
                    if self._competes_for(order, unit):
                        holder[unit.id] = order
        for order in issued:
            if order.state is not OrderState.PENDING:
                continue
            units = tuple(
                unit for unit in order.units if holder.get(unit.id) is order or not self._competes_for(order, unit)
            )
            if not units:
                order._settle(OrderState.OVERRIDDEN)
                continue
            if order not in clearing and not (units := self._units_not_carrying_it_out(order, units)):
                order._settle(OrderState.REDUNDANT)
                continue
            yield order, units

    def _competes_for(self, order: Order[Any], unit: OwnUnit[Any]) -> bool:
        """Whether `order` competes with the turn's other orders for `unit`.

        Every unqueued order does, whatever it would do to the unit. A unit takes the last order it was given, and so
        does a structure: the game would queue a second train behind the first instead of replacing it, and pay for
        it from the step it was ordered, so NachOS sends only the last thing the turn asked a structure to make.

        A queued order competes with nothing, since the bot is asking for a place in the queue. This is how a
        structure with a reactor is told to make two at once. An ability the unit's type carries out at once competes
        with nothing either: the unit does both (in game). Each unit of a group is judged by its own type.
        """
        if order.queued:
            return False
        row = self._game_data.abilities.get(order.ability)
        return _behavior_for(row, unit) is not OrderBehavior.KEEPS_ORDERS

    def _add(self, order: Order[Any]) -> None:
        """Count `order` among the turn's, last."""
        self._issued.append(order)
        for unit_id in {unit.id for unit in order.units}:
            self._issued_by_unit.setdefault(unit_id, []).append(order)

    def _remember_sent(self, order: Order[Any], units: Sequence[OwnUnit[Any]]) -> None:
        """Record `order` as the last sent to each of `units` whose orders it replaces."""
        row = self._game_data.abilities.get(order.ability)
        for unit in units:
            if _behavior_for(row, unit) is OrderBehavior.REPLACES:
                self._last_sent[unit.id] = (order, self._observations)

    def _units_not_carrying_it_out(
        self, order: Order[Any], units: tuple[OwnUnit[Any], ...]
    ) -> tuple[OwnUnit[Any], ...]:
        """The units of `units` that `order` would change anything for: all of them for a queued order, else all but
        those whose orders it replaces and which are already doing it."""
        if order.queued:
            return units
        row = self._game_data.abilities.get(order.ability)
        general = self._general_ability(order.ability)
        return tuple(
            unit
            for unit in units
            if _behavior_for(row, unit) is not OrderBehavior.REPLACES or not self._is_doing(unit, general, order.target)
        )

    def _is_doing(self, unit: OwnUnit[Any], general: AbilityId, target: Target | None) -> bool:
        """Whether `unit` is doing `general` at `target`: the last order sent to it says, while it may not show yet,
        and otherwise its first reported order."""
        last = self._last_sent.get(unit.id)
        if last is not None and self._observations - last[1] < _SHOWN_WITHIN:
            sent = last[0]
            return self._general_ability(sent.ability) is general and same_target(sent.target, target)
        return self._unit_is_at(unit, general, target)

    def _unit_is_at(self, unit: OwnUnit[Any], general: AbilityId, target: Target | None) -> bool:
        """Whether `unit`'s first order runs `general` at `target`. A unit the last observation left out, in a
        transport for one, is at nothing."""
        orders = unit._latest_report.orders
        if unit.is_stale or not orders:
            return False
        first = orders[0]
        if self._general_ability(_ability_of_unit_order(first)) is not general:
            return False
        match first.WhichOneof("target"):
            case "target_world_space_pos":
                point = first.target_world_space_pos
                return isinstance(target, Point) and same_point(target, point.x, point.y)
            case "target_unit_tag":
                return isinstance(target, Unit) and target.tag == first.target_unit_tag
            case _:
                return target is None

    def _general_ability(self, ability: AbilityId) -> AbilityId:
        """The general ability `ability` remaps to. That is what a unit reports, and what the game reports carrying
        out."""
        row = self._game_data.abilities.get(ability)
        return ability if row is None or row.remaps_to is None else row.remaps_to


def _order_behavior(row: AbilityData | None, units: Sequence[OwnUnit[Any]]) -> OrderBehavior:
    """What an ability does to the current orders of `units`: the behavior their types share, or `REPLACES`."""
    behaviors = {_behavior_for(row, unit) for unit in units}
    return behaviors.pop() if len(behaviors) == 1 else OrderBehavior.REPLACES


def _behavior_for(row: AbilityData | None, unit: OwnUnit[Any]) -> OrderBehavior:
    """What an ability does to the current orders of `unit`, a unit of its type. An ability with no row replaces
    them."""
    return OrderBehavior.REPLACES if row is None else row.order_behavior_for(unit.type_id)


def _ability_of_unit_order(order: raw_pb2.UnitOrder) -> AbilityId:
    """The ability a raw order runs, or `NULL` if the curated ids leave it out. No order of ours runs such an
    ability."""
    return AbilityId.get(order.ability_id) or AbilityId.NULL
