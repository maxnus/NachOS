"""The orders a bot gives in a turn, sent after its handlers have run."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Any, final, overload

from s2clientprotocol import raw_pb2, sc2api_pb2

from sc2nachos.gamedata import OrderBehavior
from sc2nachos.gamedata._sent_as import Aim
from sc2nachos.gamedata._techtree import ABILITIES_SENT_AS_ANOTHER
from sc2nachos.geometry import Point
from sc2nachos.geometry._point import coordinates
from sc2nachos.ids import AbilityId
from sc2nachos.orders._commands import create_camera_move_action, create_unit_command_actions
from sc2nachos.orders._order import Order
from sc2nachos.orders._order_state import OrderState
from sc2nachos.orders._targets import aimed_at, as_sent, check_target, same_point, same_target
from sc2nachos.protocol import ProtocolError
from sc2nachos.state import ActionResult
from sc2nachos.units import Unit

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence

    from sc2nachos.gamedata import AbilityData, GameData
    from sc2nachos.gamedata._sent_as import SentAs
    from sc2nachos.geometry import PointLike
    from sc2nachos.ids import UnitTypeId
    from sc2nachos.protocol import Client
    from sc2nachos.units import OwnUnit, Target

# The observations after the one a turn read by which its orders show in a unit's orders: the next in a stepped game,
# and the one after that on the ladder or in realtime (docs/game-behavior.md).
_SHOWN_WITHIN = 2
# The ability each game ability is, aimed at the unit given it: an unload at a point aimed at the transport itself is
# `GENERAL_UNLOAD`.
_IS_AIMED_AT_ITSELF = MappingProxyType(
    {
        sending.ability: ability
        for ability, sent_as in ABILITIES_SENT_AS_ANOTHER.items()
        for sending in sent_as.values()
        if sending.aim is Aim.ITSELF
    }
)
# What an ability goes out as where no type is sent another.
_SENT_UNCHANGED: Mapping[UnitTypeId, SentAs] = MappingProxyType({})


@final
class OrderBook:
    """This player's orders for the turn.

    A bot gives orders through `api.orders` while its handlers run. They are sent in one request after the turn's
    last handler returns. A unit carries out only the last order it is given in a turn, plus any abilities it
    carries out at once.
    """

    __slots__ = (
        "_camera_location",
        "_forced",
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
        # The turn's orders issued with `force`, sent even to units already carrying them out.
        self._forced: set[Order[Any]] = set()
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
        force: bool = False,
    ) -> Order[None]: ...

    @overload
    def issue[T](
        self,
        units: OwnUnit[Any] | Iterable[OwnUnit[Any]],
        ability: AbilityId,
        *,
        target: PointLike | Unit[Any] | None = None,
        queued: bool = False,
        force: bool = False,
        data: T,
    ) -> Order[T]: ...

    def issue(
        self,
        units: OwnUnit[Any] | Iterable[OwnUnit[Any]],
        ability: AbilityId,
        *,
        target: PointLike | Unit[Any] | None = None,
        queued: bool = False,
        force: bool = False,
        data: Any = None,
    ) -> Order[Any]:
        """Order `units` to use `ability`, aimed at a point, a unit or nothing, and return the `Order`.

        The order goes out as one command, so units ordered together keep their spacing where the game spreads them.
        `queued` puts the order behind each unit's current orders. `data` is the bot's own; NachOS carries it and
        never reads it. Nothing is sent until the turn's handlers have all run.

        An unqueued order that replaces a unit's orders is not sent to a unit already doing it: sending it would only
        drop what the unit has queued behind it. What a unit is doing is the last such order sent to it, while the
        observation after its turn may not show it yet, and otherwise its first reported order. An order left with no
        unit to send to reads `REDUNDANT`. `force` sends it to those units too, so it is never `REDUNDANT`: that is
        how a unit is made to drop its queue and go on with its current order. It still competes with the turn's other
        orders like any other.

        An ability that goes out as another for some types (`AbilityData.sent_as`) is sent as one command per ability
        it goes out as, and answered `SUCCESS` if any of them was, as the game answers one command naming several
        units.

        Raises `TypeError` for a target the ability cannot be aimed at, for an order without one to a type that goes
        out aimed at it, as a liberator's siege does, and for a transport's unload at a point aimed at one of `units`
        itself, which is `GENERAL_UNLOAD` for that one: a group given an unload at one of its own transports is two
        orders. Raises `ValueError` for a custom ability given to a type it has nothing to be sent as for, or which this
        game's tables lack an ability it is sent as for.
        """
        given = (units,) if isinstance(units, Unit) else tuple(units)
        if not given:
            raise ValueError("an order needs a unit to give it to")
        row = self._game_data.abilities.get(ability)
        sent_as = ABILITIES_SENT_AS_ANOTHER.get(ability, _SENT_UNCHANGED)
        if ability.is_custom:
            if row is None:
                raise ValueError(f"{ability.name} has nothing in this game's tables to be sent as")
            _check_sent_as(ability, sent_as, given)
        aimed = aimed_at(target)
        check_target(ability, aimed, row)
        if aimed is None:
            _check_aimed_where_needed(ability, sent_as, given)
        if isinstance(aimed, Unit):
            _check_not_aimed_at_itself(ability, aimed, given)
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
        if force:
            self._forced.add(order)
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
        issued, forced = self._issued, self._forced
        self._issued, self._issued_by_unit, self._forced = [], {}, set()
        actions: list[sc2api_pb2.Action] = []
        sent: list[tuple[Order[Any], tuple[OwnUnit[Any], ...], int]] = []
        for order, units in self._orders_to_send(issued, forced):
            order_actions = create_unit_command_actions(
                order, units, ABILITIES_SENT_AS_ANOTHER.get(order.ability, _SENT_UNCHANGED)
            )
            actions += order_actions
            sent.append((order, units, len(order_actions)))
        if self._camera_location is not None:
            actions.append(create_camera_move_action(self._camera_location))
            self._camera_location = None
        if not actions:
            return
        results = client.act(actions).result
        unit_commands = sum(count for _, _, count in sent)
        if len(results) < unit_commands:
            raise ProtocolError(f"the game answered {len(results)} of the {unit_commands} commands it was sent")
        # A camera move, if any, is answered last and belongs to no order.
        answered = 0
        for order, units, count in sent:
            action_result = _answer(results[answered : answered + count])
            answered += count
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
        self, issued: Sequence[Order[Any]], forced: Collection[Order[Any]]
    ) -> Iterator[tuple[Order[Any], tuple[OwnUnit[Any], ...]]]:
        """Each order of the turn that goes out, with the units it goes out to.

        An order that acts at once, or that is queued behind a unit's current orders, competes with nothing. Of the
        rest, a unit keeps only the last it was given, and an order left with no unit is overridden. An order is then
        left out for the units already carrying it out, and one left with no unit is redundant. An order in `forced`
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
            if order not in forced and not (units := self._units_not_carrying_it_out(order, units)):
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
        return tuple(
            unit
            for unit in units
            if _behavior_for(row, unit) is not OrderBehavior.REPLACES
            or not self._is_doing(unit, order.ability, order.target)
        )

    def _is_doing(self, unit: OwnUnit[Any], ability: AbilityId, target: Target | None) -> bool:
        """Whether `unit` is doing `ability` at `target`: the last order sent to it says, while it may not show yet,
        and otherwise its first reported order."""
        sent = self._sent_and_not_shown(unit)
        if sent is not None:
            return sent.ability is ability and same_target(sent.target, target)
        return self._unit_is_at(unit, ability, target)

    def _sent_and_not_shown(self, unit: OwnUnit[Any]) -> Order[Any] | None:
        """The last order sent to `unit` that replaced its orders, while the observations may not show it yet."""
        last = self._last_sent.get(unit.id)
        if last is not None and self._observations - last[1] < _SHOWN_WITHIN:
            return last[0]
        return None

    def _unit_is_at(self, unit: OwnUnit[Any], ability: AbilityId, target: Target | None) -> bool:
        """Whether `unit`'s first order runs `ability` at `target`. A unit the last observation left out, in a
        transport for one, is at nothing."""
        orders = unit._latest_report.orders
        if unit.is_stale or not orders:
            return False
        first = orders[0]
        if _ability_of_unit_order(first) is not ability:
            return False
        match first.WhichOneof("target"):
            case "target_world_space_pos":
                point = first.target_world_space_pos
                return isinstance(target, Point) and same_point(target, point.x, point.y)
            case "target_unit_tag":
                return isinstance(target, Unit) and target.tag == first.target_unit_tag
            case _:
                return target is None


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


def _answer(results: Sequence[int]) -> ActionResult:
    """The game's answer to an order sent as `results`' commands, read as it answers one command naming several units:
    `SUCCESS` if any unit took it, and otherwise the first refusal (in game)."""
    answers = [ActionResult.read(result) for result in results]
    return ActionResult.SUCCESS if ActionResult.SUCCESS in answers else answers[0]


def _check_sent_as(ability: AbilityId, sent_as: Mapping[UnitTypeId, SentAs], units: Sequence[OwnUnit[Any]]) -> None:
    """Raise `ValueError` if the custom `ability` has nothing to be sent as for one of `units`."""
    if missing := sorted({unit.type_id.name for unit in units if unit.type_id not in sent_as}):
        raise ValueError(f"{ability.name} has nothing to be sent as for {', '.join(missing)}")


def _check_aimed_where_needed(
    ability: AbilityId, sent_as: Mapping[UnitTypeId, SentAs], units: Sequence[OwnUnit[Any]]
) -> None:
    """Raise `TypeError` if `ability`, given no target, goes out at the order's target for one of `units`."""
    aiming = {unit.type_id: sending for unit in units if (sending := sent_as.get(unit.type_id)) is not None}
    if needing := sorted(unit_type.name for unit_type, sending in aiming.items() if sending.aim is Aim.TARGET):
        raise TypeError(f"{ability.name} takes a point for {', '.join(needing)}, and was given no target")


def _check_not_aimed_at_itself(ability: AbilityId, target: Unit[Any], units: Sequence[OwnUnit[Any]]) -> None:
    """Raise `TypeError` if `ability` is aimed at one of `units` and another id is that."""
    custom = _IS_AIMED_AT_ITSELF.get(ability)
    if custom is not None and any(unit.id == target.id for unit in units):
        raise TypeError(f"{ability.name} aimed at the unit itself is {custom.name}")
