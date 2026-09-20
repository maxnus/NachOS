"""The orders a bot gives in a turn, sent once its handlers have run."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, final, overload

import numpy
from s2clientprotocol import common_pb2, raw_pb2, sc2api_pb2

from sc2nachos.constants import POINT_PRECISION
from sc2nachos.gamedata import OrderBehavior, TargetType
from sc2nachos.geometry import Point
from sc2nachos.geometry._point import coordinates
from sc2nachos.ids import AbilityId
from sc2nachos.orders._order import Order
from sc2nachos.orders._order_state import OrderState
from sc2nachos.protocol import ProtocolError
from sc2nachos.state import ActionResult, UnitCommand, read_result
from sc2nachos.units import Unit

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence

    from sc2nachos.gamedata import AbilityData, GameData
    from sc2nachos.geometry import PointLike
    from sc2nachos.protocol import Client
    from sc2nachos.state import ActionError
    from sc2nachos.state._state import _State
    from sc2nachos.units import OwnUnit, Target

# What each target type takes, for the message an order aimed at the wrong thing is refused with.
_TARGETS_WANTED = {
    TargetType.NOTHING: "no target",
    TargetType.POINT: "a point",
    TargetType.UNIT: "a unit",
    TargetType.POINT_OR_UNIT: "a point or a unit",
    TargetType.POINT_OR_NOTHING: "a point or no target",
}


@final
class OrderBook:
    """What this player orders in a turn, and what became of every order still worth following.

    A bot gives orders through `api.order` while its handlers run; they go out in one request once the last handler
    has returned. A unit takes one order a turn, the last it was given, besides the abilities it carries out at once.
    """

    __slots__ = ("_camera_location", "_game_data", "_given_orders", "_running_orders", "_step")

    def __init__(self, game_data: GameData) -> None:
        """A book for one game, reading `game_data` for what each ability does and what it must be aimed at."""
        self._game_data = game_data
        self._given_orders: list[Order[Any]] = []
        self._running_orders: list[Order[Any]] = []
        self._camera_location: Point | None = None
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
        """Order `ability` of `units`, aimed at a point, at a unit or at nothing, and answer the order to hold on to.

        One command goes out for the order, so units given one together keep their spacing where the game spreads
        them, and `queued` sends it to go behind what each unit already has. `data` is the bot's own, carried and
        never read. Nothing is sent before the turn's handlers have all run.

        Raises `TypeError` for a target the ability cannot be aimed at.
        """
        given = (units,) if isinstance(units, Unit) else tuple(units)
        if not given:
            raise ValueError("an order needs a unit to give it to")
        row = self._game_data.abilities.get(ability)
        aimed = _target(target)
        _check_target(ability, aimed, row)
        order = Order(
            ability,
            given,
            aimed,
            queued=queued,
            data=data,
            behavior=OrderBehavior.REPLACES if row is None else row.behavior,
            step=self._step,
        )
        self._given_orders.append(order)
        return order

    def clear_queue(self, unit: OwnUnit[Any]) -> Order[None] | None:
        """Drop what `unit` has queued, leaving it at the order it is carrying out, or `None` where there is nothing
        to drop.

        The order it is at is sent back unqueued, which the game answers `SUCCESS`, carries nothing out for, and
        drops everything behind (in game). An order given to the unit this turn overrides it, as it would any other.

        It answers `None` where the unit has nothing queued, where what it is at is an ability NachOS cannot name,
        and where that ability is a train, a research or a morph: the game would put a second of those behind the
        first rather than drop anything, so a structure's queue is cancelled from its end instead.
        """
        orders = unit._latest_data.orders
        if len(orders) < 2:
            return None
        ability = self._general_ability(_ability_of_unit_order(orders[0]))
        behavior = self._order_behavior_of_ability(ability)
        if ability is AbilityId.NULL or behavior is not OrderBehavior.REPLACES:
            return None
        order: Order[None] = Order(
            ability,
            (unit,),
            _order_target(unit, orders[0]),
            queued=False,
            data=None,
            behavior=behavior,
            step=self._step,
            forced=True,
        )
        self._given_orders.append(order)
        return order

    def issued(self, unit: OwnUnit[Any]) -> tuple[Order[Any], ...]:
        """What this turn has given `unit` so far, in the order it was given.

        A handler late in a turn reads this to leave a unit an earlier handler has spoken for, since the last order
        a unit is given is the one that goes out. An order withdrawn or already overridden is left out: it speaks
        for nothing.
        """
        return tuple(order for order in self._given_orders if unit in order.units and not order.state.is_final)

    @property
    def pending(self) -> tuple[Order[Any], ...]:
        """Every order given this turn and still to be sent, without those withdrawn or already overridden."""
        return tuple(order for order in self._given_orders if not order.state.is_final)

    @property
    def running(self) -> tuple[Order[Any], ...]:
        """Every order sent and not finished with: what the game has yet to answer for, and what it is carrying
        out. One withdrawn since it was sent is left out."""
        return tuple(order for order in self._running_orders if not order.state.is_final)

    def camera(self, at: PointLike) -> None:
        """Move this player's camera to `at`, with the turn's orders. Only the last of a turn is sent."""
        aimed = coordinates(at)
        self._camera_location = Point((_as_sent(aimed[0]), _as_sent(aimed[1])))

    def _send(self, client: Client) -> None:
        """Send the turn's orders, and read the game's verdict onto each. Sends nothing where there is nothing."""
        given, self._given_orders = self._given_orders, []
        actions: list[sc2api_pb2.Action] = []
        sent: list[tuple[Order[Any], tuple[OwnUnit[Any], ...]]] = []
        for order, units in self._orders_to_send(given):
            actions.append(_create_unit_command_action(order, units))
            sent.append((order, units))
        if self._camera_location is not None:
            actions.append(_create_camera_move_action(self._camera_location))
            self._camera_location = None
        if not actions:
            return
        results = client.act(actions).result
        if len(results) < len(sent):
            raise ProtocolError(f"the game answered {len(results)} of the {len(sent)} orders it was sent")
        # What was running before this turn's orders went out: only the orders the game takes supersede those.
        running = tuple(self._running_orders)
        replaced: set[int] = set()
        # A camera move, where there is one, is answered last and belongs to no order.
        for (order, units), result in zip(sent, results[: len(sent)], strict=True):
            verdict = read_result(result)
            order._verdict = verdict
            if verdict is not ActionResult.SUCCESS:
                order._state = OrderState.REFUSED
                continue
            order._state = OrderState.SENT
            self._running_orders.append(order)
            if not order.queued and not order._forced and order.behavior is OrderBehavior.REPLACES:
                replaced.update(unit.id for unit in units)
        self._supersede_running_orders(replaced, running)

    def _take_in(self, state: _State, step: int) -> None:
        """Read what the observation at `step` says became of the orders sent before it."""
        self._step = step
        if not self._running_orders:
            return
        commands = [action for action in state.actions if isinstance(action, UnitCommand)]
        errors = state.action_errors
        running: list[Order[Any]] = []
        for order in self._running_orders:
            if not order.state.is_final:
                self._take_in_order(order, commands, errors)
            if not order.state.is_final:
                running.append(order)
        self._running_orders = running

    def _orders_to_send(self, given: Sequence[Order[Any]]) -> Iterator[tuple[Order[Any], tuple[OwnUnit[Any], ...]]]:
        """Each order of the turn that goes out, with the units it goes out for.

        An order that acts at once, or that goes behind what a unit already has, competes with nothing. Of the
        rest, a unit keeps only the last it was given, and an order left with no unit was overridden.

        An order that replaces a unit's orders is not sent to a unit already carrying it out: re-sending costs
        nothing, but an unqueued order the same as a unit's first drops what is queued behind it, which is what
        `clear_queue` is for. A train or a research is sent all the same, since the game puts a second of the same
        behind the first (in game).
        """
        holder: dict[int, Order[Any]] = {}
        for order in given:
            if order.state is OrderState.GIVEN and self._competes(order):
                for unit in order.units:
                    holder[unit.id] = order
        for order in given:
            if order.state is not OrderState.GIVEN:
                continue
            if not self._competes(order):
                units = order.units
            else:
                units = tuple(unit for unit in order.units if holder[unit.id] is order)
                if not units:
                    order._state = OrderState.OVERRIDDEN
                    continue
            fresh = units if self._sent_whatever_a_unit_is_at(order) else self._units_not_doing_it(units, order)
            if not fresh:
                order._sent_to = units
                order._taken_by = units
                order._state = OrderState.RUNNING
                self._running_orders.append(order)
                continue
            order._sent_to = fresh
            yield order, fresh

    def _competes(self, order: Order[Any]) -> bool:
        """Whether `order` takes its units from the other orders of the turn.

        Every unqueued order does, whatever it would do to the unit. A unit takes the last it was given, and so does
        a structure: the game would put a second train behind the first rather than replace it, and pay for it from
        the step it was ordered, so NachOS sends only the last thing the turn asked a structure to make.

        A queued order competes with nothing, since it is the bot asking for a place in the queue: it is how a
        structure with a reactor is told to make two at once. An ability carried out at once competes with nothing
        either, because the unit does both (in game).
        """
        return not order.queued and order.behavior is not OrderBehavior.AT_ONCE

    def _sent_whatever_a_unit_is_at(self, order: Order[Any]) -> bool:
        """Whether `order` goes out to every unit it names, whatever each is already carrying out.

        Only an order that replaces a unit's orders is held back as a duplicate. A train or a research goes behind
        what a structure is making, and a morph or an add-on is the game's to refuse (in game).
        """
        return order._forced or order.behavior is not OrderBehavior.REPLACES

    def _units_not_doing_it(self, units: Sequence[OwnUnit[Any]], order: Order[Any]) -> tuple[OwnUnit[Any], ...]:
        """The units of `order` that are not already carrying it out."""
        return tuple(unit for unit in units if not self._unit_already_doing_order(unit, order))

    def _supersede_running_orders(self, replaced: set[int], running: Sequence[Order[Any]]) -> None:
        """End every order of `running` whose units this turn's orders all took: the game drops what an unqueued
        order replaces. An order the bot has taken back, or the game is otherwise done with, is left as it is."""
        if not replaced:
            return
        for order in running:
            if order.behavior is OrderBehavior.AT_ONCE or order.state.is_final:
                continue
            if all(unit.id in replaced for unit in order._acting_units):
                order._state = OrderState.OVERRIDDEN

    def _take_in_order(self, order: Order[Any], commands: Sequence[UnitCommand], errors: Sequence[ActionError]) -> None:
        """What one order's state becomes, from what the observation reported."""
        general = self._general_ability(order.ability)
        units = order._acting_units
        errored = tuple(error for error in errors if self._error_is_of(error, order, units, general))
        if errored:
            order._error = errored[0]
        carrying_out = any(self._unit_is_carrying_out_ability(unit, general) for unit in units)
        if errored and not carrying_out and not set(units) - {error.unit for error in errored}:
            # The game gave up on every unit it went out for. One of a group failing leaves the rest to settle as
            # they are, with the error on the order to read.
            order._state = OrderState.FAILED
            return
        # The report can come an observation after the unit is seen carrying the order out, so a running order reads
        # it too, and only what it says about this order's own units is kept.
        reported = tuple(command for command in commands if self._command_reports_ability(command, general))
        taken_by = tuple(unit for unit in units if any(unit in command.units for command in reported))
        if taken_by:
            order._taken_by = taken_by
        if order.state is OrderState.RUNNING:
            if not carrying_out:
                order._state = OrderState.DONE
            return
        if taken_by:
            # An ability carried out at once is over as soon as it is reported: it never shows in a unit's orders.
            order._state = OrderState.RUNNING if carrying_out else OrderState.DONE
        elif carrying_out:
            order._state = OrderState.RUNNING
        else:
            order._state = OrderState.DROPPED

    def _error_is_of(
        self, error: ActionError, order: Order[Any], units: Sequence[OwnUnit[Any]], general: AbilityId
    ) -> bool:
        """Whether an action error names one of `units` and the ability `order` was given."""
        return error.unit in units and error.ability is not None and self._general_ability(error.ability) is general

    def _unit_already_doing_order(self, unit: OwnUnit[Any], order: Order[Any]) -> bool:
        """Whether `unit`'s first order is the one `order` would send it unqueued."""
        if order.queued:
            return False
        orders = unit._latest_data.orders
        if not orders:
            return False
        first = orders[0]
        if self._general_ability(_ability_of_unit_order(first)) is not self._general_ability(order.ability):
            return False
        target = order.target
        match first.WhichOneof("target"):
            case "target_world_space_pos":
                point = first.target_world_space_pos
                return isinstance(target, Point) and _same_point(target, point.x, point.y)
            case "target_unit_tag":
                return isinstance(target, Unit) and target.tag == first.target_unit_tag
            case _:
                return target is None

    def _unit_is_carrying_out_ability(self, unit: OwnUnit[Any], general: AbilityId) -> bool:
        """Whether any order of `unit`'s runs `general`.

        A unit dead or gone from the observation is carrying out nothing, whatever it was last seen doing. The target
        is not compared: the game snaps a build's, and reports a spell's out of reach under the id it was ordered by
        (in game).
        """
        if unit.is_dead or unit.is_stale:
            return False
        return any(
            self._general_ability(_ability_of_unit_order(order)) is general for order in unit._latest_data.orders
        )

    def _command_reports_ability(self, command: UnitCommand, general: AbilityId) -> bool:
        """Whether a reported command is the one `general` was ordered by."""
        return self._general_ability(command.ability) is general

    def _general_ability(self, ability: AbilityId) -> AbilityId:
        """The ability an id stands for, which is what a unit reports and what the game reports carrying out."""
        row = self._game_data.abilities.get(ability)
        return ability if row is None or row.remaps_to is None else row.remaps_to

    def _order_behavior_of_ability(self, ability: AbilityId) -> OrderBehavior:
        """What ordering `ability` does to what a unit is already doing."""
        row = self._game_data.abilities.get(ability)
        return OrderBehavior.REPLACES if row is None else row.behavior


def _target(target: PointLike | Unit[Any] | None) -> Target | None:
    """An order's target: the unit itself, or the ground point it is aimed at, as the game will read it.

    A height is left behind, since the game takes a target on the ground and reports a height of zero for every one.
    What is left is cut to the 32 bits the protocol carries a coordinate in, so that the point an order holds is the
    one the game is given, and the one it reports back.
    """
    if target is None or isinstance(target, Unit):
        return target
    aimed = coordinates(target)
    return Point((_as_sent(aimed[0]), _as_sent(aimed[1])))


def _as_sent(coordinate: float) -> float:
    """A coordinate as the protocol carries it, which is a 32-bit float.

    Everything the game reports is one already, widened to a Python float, so only a point going out is ever cut
    this way: what comes back from a point sent unrounded is not the number that was sent.
    """
    return float(numpy.float32(coordinate))


def _order_target(unit: OwnUnit[Any], order: raw_pb2.UnitOrder) -> Target | None:
    """What an order a unit reports is aimed at, naming a unit through the tracker that holds `unit`."""
    match order.WhichOneof("target"):
        case "target_world_space_pos":
            point = order.target_world_space_pos
            return Point((point.x, point.y))
        case "target_unit_tag":
            return unit._tracker.units.by_tag(order.target_unit_tag)
        case _:
            return None


def _check_target(ability: AbilityId, target: Target | None, row: AbilityData | None) -> None:
    """Raise `TypeError` where `ability` cannot be aimed at `target`. An ability with no row is left to the game."""
    if row is None:
        return
    wanted = row.target_type
    takes = {
        TargetType.NOTHING: target is None,
        TargetType.POINT: isinstance(target, Point),
        TargetType.UNIT: isinstance(target, Unit),
        TargetType.POINT_OR_UNIT: target is not None,
        TargetType.POINT_OR_NOTHING: not isinstance(target, Unit),
    }
    if not takes[wanted]:
        aimed = "no target" if target is None else "a point" if isinstance(target, Point) else "a unit"
        raise TypeError(f"{ability.name} takes {_TARGETS_WANTED[wanted]}, and was given {aimed}")


def _ability_of_unit_order(order: raw_pb2.UnitOrder) -> AbilityId:
    """The ability a raw order runs, or `NULL` where the curated ids leave it out, which no order of ours runs."""
    return AbilityId.get(order.ability_id) or AbilityId.NULL


def _create_unit_command_action(order: Order[Any], units: Sequence[OwnUnit[Any]]) -> sc2api_pb2.Action:
    """One raw command, giving `order` to `units`."""
    target = order.target
    tags = [unit.tag for unit in units]
    if target is None:
        command = raw_pb2.ActionRawUnitCommand(ability_id=order.ability, unit_tags=tags, queue_command=order.queued)
    elif isinstance(target, Point):
        command = raw_pb2.ActionRawUnitCommand(
            ability_id=order.ability,
            unit_tags=tags,
            queue_command=order.queued,
            target_world_space_pos=common_pb2.Point2D(x=target[0], y=target[1]),
        )
    else:
        command = raw_pb2.ActionRawUnitCommand(
            ability_id=order.ability, unit_tags=tags, queue_command=order.queued, target_unit_tag=target.tag
        )
    return sc2api_pb2.Action(action_raw=raw_pb2.ActionRaw(unit_command=command))


def _create_camera_move_action(at: Point) -> sc2api_pb2.Action:
    """The action that moves this player's camera."""
    center = common_pb2.Point(x=at[0], y=at[1])
    move = raw_pb2.ActionRawCameraMove(center_world_space=center)
    return sc2api_pb2.Action(action_raw=raw_pb2.ActionRaw(camera_move=move))


def _same_point(target: Point, x: float, y: float) -> bool:
    """Whether a point the game reports is the one ordered, which it keeps to `POINT_PRECISION`, cut down."""
    return target.cut_down(step=POINT_PRECISION) == Point((x, y)).cut_down(step=POINT_PRECISION)
