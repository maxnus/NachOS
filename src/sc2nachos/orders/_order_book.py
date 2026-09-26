"""The orders a bot gives in a turn, sent after its handlers have run."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, final, overload

from s2clientprotocol import raw_pb2, sc2api_pb2

from sc2nachos.gamedata import OrderBehavior
from sc2nachos.geometry import Point
from sc2nachos.geometry._point import coordinates
from sc2nachos.ids import AbilityId, UnitTypeId
from sc2nachos.orders._commands import create_camera_move_action, create_unit_command_action
from sc2nachos.orders._order import Order
from sc2nachos.orders._order_state import OrderState
from sc2nachos.orders._targets import aimed_at, as_sent, check_target, order_target, same_point, same_target
from sc2nachos.protocol import ProtocolError
from sc2nachos.state import ActionResult, UnitCommand
from sc2nachos.units import Unit

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence

    from sc2nachos.gamedata import AbilityData, GameData
    from sc2nachos.geometry import PointLike
    from sc2nachos.protocol import Client
    from sc2nachos.state import ActionFailure
    from sc2nachos.state._state import _State
    from sc2nachos.units import OwnUnit, Target


@final
class OrderBook:
    """This player's orders for the turn, and what became of every order still being followed.

    A bot gives orders through `api.orders` while its handlers run. They are sent in one request after the turn's
    last handler returns. A unit carries out only the last order it is given in a turn, plus any abilities it
    carries out at once.
    """

    __slots__ = (
        "_camera_location",
        "_game_data",
        "_given_by_unit",
        "_given_orders",
        "_last_sent",
        "_running_orders",
        "_step",
    )

    def __init__(self, game_data: GameData) -> None:
        """A book for one game. `game_data` says what each ability does and what it must be aimed at."""
        self._game_data = game_data
        # The turn's orders in the order they were given, and those of each unit by its id. A repeat handed back
        # twice counts where it was last given.
        self._given_orders: dict[Order[Any], None] = {}
        self._given_by_unit: dict[int, dict[Order[Any], None]] = {}
        # By unit id, the last unqueued order the game took that replaced the unit's orders.
        self._last_sent: dict[int, Order[Any]] = {}
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
        """Order `units` to use `ability`, aimed at a point, a unit or nothing, and return the `Order`.

        The order goes out as one command, so units ordered together keep their spacing where the game spreads them.
        `queued` puts the order behind each unit's current orders. `data` is the bot's own; NachOS carries it and
        never reads it. Nothing is sent until the turn's handlers have all run.

        An unqueued order is a repeat when the last order sent to replace the orders of `units` was given to those
        very units, with the same ability and target, and each of them is still carrying it out. A repeat hands back
        that order, with `data` in place of what it carried, and sends nothing, so whatever is queued behind it stays.

        Raises `TypeError` for a target the ability cannot be aimed at.
        """
        given = (units,) if isinstance(units, Unit) else tuple(units)
        if not given:
            raise ValueError("an order needs a unit to give it to")
        row = self._game_data.abilities.get(ability)
        aimed = aimed_at(target)
        check_target(ability, aimed, row)
        if not queued and (repeated := self._repeated(given, ability, aimed, row)) is not None:
            repeated._replace_data(data)
            self._add(repeated)
            return repeated
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
        behavior = _order_behavior(self._game_data.abilities.get(ability), (unit,))
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
            forced=True,
        )
        self._add(order)
        return order

    def issued_to(self, unit: OwnUnit[Any]) -> tuple[Order[Any], ...]:
        """The orders given to `unit` so far this turn, in the order they were given.

        A handler late in a turn reads this to leave alone a unit an earlier handler has ordered, since only the last
        order a unit is given goes out. A repeat counts, though it is not sent again. Withdrawn orders are left out.
        """
        orders = self._given_by_unit.get(unit.id)
        if not orders:
            return ()
        return tuple(order for order in orders if not order.state.is_final)

    @property
    def pending(self) -> tuple[Order[Any], ...]:
        """Every order given this turn and still to be sent. Withdrawn orders and repeats are left out."""
        return tuple(order for order in self._given_orders if order.state is OrderState.GIVEN)

    @property
    def running(self) -> tuple[Order[Any], ...]:
        """Every order sent and not yet final: those the game has yet to report on, and those it is carrying out.
        An order withdrawn since it was sent is left out."""
        return tuple(order for order in self._running_orders if not order.state.is_final)

    def camera(self, at: PointLike) -> None:
        """Move this player's camera to `at`, along with the turn's orders. Only the last move of a turn is sent."""
        aimed = coordinates(at)
        self._camera_location = Point((as_sent(aimed[0]), as_sent(aimed[1])))

    def _send(self, client: Client) -> None:
        """Send the turn's orders and record the game's answer on each. Sends nothing if there is nothing to send."""
        given, self._given_orders = tuple(self._given_orders), {}
        self._given_by_unit = {}
        actions: list[sc2api_pb2.Action] = []
        sent: list[tuple[Order[Any], tuple[OwnUnit[Any], ...]]] = []
        for order, units in self._orders_to_send(given):
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
        # The orders running before this turn's went out. Only orders the game accepts supersede them.
        running = tuple(self._running_orders)
        replaced: set[int] = set()
        # A camera move, if any, is answered last and belongs to no order.
        for (order, units), result in zip(sent, results[: len(sent)], strict=True):
            action_result = ActionResult.read(result)
            taken = action_result is ActionResult.SUCCESS
            order._settle(OrderState.SENT if taken else OrderState.REFUSED, action_result=action_result)
            if not taken:
                continue
            self._running_orders.append(order)
            if not order.queued:
                self._remember_sent(order, units)
            if not order.queued and not order._forced and order.order_behavior is OrderBehavior.REPLACES:
                replaced.update(unit.id for unit in units)
        self._supersede_running_orders(replaced, running)

    def _take_in(self, state: _State, step: int, dead: Iterable[Unit[Any]]) -> None:
        """Read from the observation at `step` what became of the orders sent before it. `dead` are the units it
        found dead."""
        self._step = step
        for unit in dead:
            self._last_sent.pop(unit.id, None)
        if not self._running_orders:
            return
        commands = [action for action in state.actions if isinstance(action, UnitCommand)]
        failures = state.action_failures
        running: list[Order[Any]] = []
        for order in self._running_orders:
            if not order.state.is_final:
                self._take_in_order(order, commands, failures)
            if not order.state.is_final:
                running.append(order)
        self._running_orders = running

    def _orders_to_send(self, given: Sequence[Order[Any]]) -> Iterator[tuple[Order[Any], tuple[OwnUnit[Any], ...]]]:
        """Each order of the turn that goes out, with the units it goes out to.

        An order that acts at once, or that is queued behind a unit's current orders, competes with nothing. Of the
        rest, a unit keeps only the last it was given, and an order left with no unit is overridden. A repeat takes
        its units like any other order, and is not sent again.

        An order that replaces a unit's orders is not sent to a unit already carrying it out. Re-sending costs
        nothing, but an unqueued order equal to a unit's first drops everything queued behind it, which is what
        `clear_queue` is for. A train or a research is sent regardless, since the game queues a second of the same
        behind the first (in game).
        """
        holder: dict[int, Order[Any]] = {}
        for order in given:
            if self._holds_units(order):
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
                    order._settle(OrderState.OVERRIDDEN)
                    continue
            fresh = units if self._sent_whatever_a_unit_is_at(order) else self._units_not_doing_it(units, order)
            if not fresh:
                order._settle(OrderState.RUNNING, sent_to=units, taken_by=units)
                self._running_orders.append(order)
                continue
            order._settle(sent_to=fresh)
            yield order, fresh

    def _holds_units(self, order: Order[Any]) -> bool:
        """Whether `order` takes its units from the turn's earlier orders: an order to send that competes for them,
        or a repeat."""
        if order.state is OrderState.GIVEN:
            return self._competes(order)
        return not order.state.is_final

    def _competes(self, order: Order[Any]) -> bool:
        """Whether `order` competes with the turn's other orders for its units.

        Every unqueued order does, whatever it would do to the unit. A unit takes the last order it was given, and so
        does a structure: the game would queue a second train behind the first instead of replacing it, and pay for
        it from the step it was ordered, so NachOS sends only the last thing the turn asked a structure to make.

        A queued order competes with nothing, since the bot is asking for a place in the queue. This is how a
        structure with a reactor is told to make two at once. An ability carried out at once competes with nothing
        either: the unit does both (in game).
        """
        return not order.queued and order.order_behavior is not OrderBehavior.KEEPS_ORDERS

    def _sent_whatever_a_unit_is_at(self, order: Order[Any]) -> bool:
        """Whether `order` goes out to every unit it names, whatever each is already doing.

        Only an order that replaces a unit's orders is held back as a duplicate. A train or a research queues behind
        what a structure is making, and a morph or an add-on is the game's to refuse (in game).
        """
        return order._forced or order.order_behavior is not OrderBehavior.REPLACES

    def _units_not_doing_it(self, units: Sequence[OwnUnit[Any]], order: Order[Any]) -> tuple[OwnUnit[Any], ...]:
        """The units of `order` that are not already carrying it out, unqueued."""
        if order.queued:
            return tuple(units)
        general = self._general_ability(order.ability)
        return tuple(unit for unit in units if not self._unit_is_at(unit, general, order.target))

    def _add(self, order: Order[Any]) -> None:
        """Count `order` among the turn's, last. A repeat already given this turn moves to the end."""
        self._given_orders.pop(order, None)
        self._given_orders[order] = None
        for unit in order.units:
            orders = self._given_by_unit.setdefault(unit.id, {})
            orders.pop(order, None)
            orders[order] = None

    def _repeated(
        self, units: Sequence[OwnUnit[Any]], ability: AbilityId, target: Target | None, row: AbilityData | None
    ) -> Order[Any] | None:
        """The order already sent that an unqueued order of `ability` at `target` to `units` repeats, or `None`."""
        last = self._last_sent.get(units[0].id)
        if last is None or last.state.is_final or set(last.units) != set(units):
            return None
        general = self._general_ability(ability)
        if self._general_ability(last.ability) is not general or not same_target(last.target, target):
            return None
        for unit in units:
            if self._last_sent.get(unit.id) is not last or not self._unit_is_at(unit, general, target):
                return None
            if _order_behavior(row, (unit,)) is not OrderBehavior.REPLACES:
                return None
        return last

    def _remember_sent(self, order: Order[Any], units: Sequence[OwnUnit[Any]]) -> None:
        """Record `order` as the last sent to each of `units` whose orders it replaced."""
        row = self._game_data.abilities.get(order.ability)
        for unit in units:
            if _order_behavior(row, (unit,)) is OrderBehavior.REPLACES:
                self._last_sent[unit.id] = order

    def _supersede_running_orders(self, replaced: set[int], running: Sequence[Order[Any]]) -> None:
        """Mark overridden every order in `running` whose units were all taken by this turn's orders, since the game
        drops what an unqueued order replaces. An order already final, withdrawn or otherwise, is left as it is."""
        if not replaced:
            return
        for order in running:
            if order.order_behavior is OrderBehavior.KEEPS_ORDERS or order.state.is_final:
                continue
            if all(unit.id in replaced for unit in order._acting_units):
                order._settle(OrderState.OVERRIDDEN)

    def _take_in_order(
        self, order: Order[Any], commands: Sequence[UnitCommand], failures: Sequence[ActionFailure]
    ) -> None:
        """Update one order's state from what the observation reported."""
        general = self._general_ability(order.ability)
        units = order._acting_units
        failed = tuple(failure for failure in failures if self._failure_is_of(failure, order, units, general))
        if failed:
            order._settle(failure=failed[0])
        carrying_out = any(self._unit_is_carrying_out_ability(unit, general) for unit in units)
        # The report can arrive an observation after the unit is seen carrying the order out, so a running order
        # reads it too. Only what it says about this order's own units is kept.
        reported = tuple(command for command in commands if self._command_reports_ability(command, general))
        taken_by = tuple(unit for unit in units if any(unit in command.units for command in reported))
        if taken_by:
            order._settle(taken_by=taken_by)
        done_with = {failure.unit for failure in failed} | {unit for unit in units if unit.is_dead}
        if failed and not carrying_out and not set(units) - done_with:
            # The game gave up on every unit the order went to that has not died since. If only some of a group
            # fail, the rest settle as they are, and the failure is left on the order to read.
            order._settle(OrderState.FAILED)
            return
        if carrying_out:
            if not order._seen_carrying:
                order._settle(
                    seen_carrying=frozenset(unit for unit in units if self._unit_is_carrying_out_ability(unit, general))
                )
        elif all(
            unit.is_dead and unit.type_id is not UnitTypeId.EGG
            for unit in order._seen_carrying or order._taken_by or units
        ):
            # Every unit seen carrying the order out is dead: those seen in the first observation that showed any,
            # or failing that those the game reported taking it, or failing that every unit it went out to. The game
            # reports only that a producer died, so this is the one case where a unit carrying nothing out does not
            # mean the order is done. One unit of a group that got where it was sent keeps the order `DONE`, and so
            # does an egg, which is reported dead when what it makes hatches (in game).
            order._settle(OrderState.LOST)
            return
        if order.state is OrderState.RUNNING:
            if not carrying_out:
                order._settle(OrderState.DONE)
            return
        if taken_by:
            # An ability carried out at once is done as soon as it is reported: it never shows in a unit's orders.
            order._settle(OrderState.RUNNING if carrying_out else OrderState.DONE)
        elif carrying_out:
            order._settle(OrderState.RUNNING)
        else:
            order._settle(OrderState.DROPPED)

    def _failure_is_of(
        self, failure: ActionFailure, order: Order[Any], units: Sequence[OwnUnit[Any]], general: AbilityId
    ) -> bool:
        """Whether `failure` names one of `units` and `order`'s ability."""
        return (
            failure.unit in units and failure.ability is not None and self._general_ability(failure.ability) is general
        )

    def _unit_is_at(self, unit: OwnUnit[Any], general: AbilityId, target: Target | None) -> bool:
        """Whether `unit`'s first order runs `general` at `target`."""
        orders = unit._latest_report.orders
        if not orders:
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

    def _unit_is_carrying_out_ability(self, unit: OwnUnit[Any], general: AbilityId) -> bool:
        """Whether any of `unit`'s orders runs `general`.

        A unit that is dead or gone from the observation is carrying out nothing, whatever it was last seen doing.
        The target is not compared: the game snaps a build's target, and reports a spell out of reach under the id
        it was ordered by (in game).
        """
        if unit.is_dead or unit.is_stale:
            return False
        return any(
            self._general_ability(_ability_of_unit_order(order)) is general for order in unit._latest_report.orders
        )

    def _command_reports_ability(self, command: UnitCommand, general: AbilityId) -> bool:
        """Whether a reported command runs `general`."""
        return self._general_ability(command.ability) is general

    def _general_ability(self, ability: AbilityId) -> AbilityId:
        """The general ability `ability` remaps to. That is what a unit reports, and what the game reports carrying
        out."""
        row = self._game_data.abilities.get(ability)
        return ability if row is None or row.remaps_to is None else row.remaps_to


def _order_behavior(row: AbilityData | None, units: Sequence[OwnUnit[Any]]) -> OrderBehavior:
    """What an ability does to the current orders of `units`: the behavior their types share, or `REPLACES`."""
    if row is None:
        return OrderBehavior.REPLACES
    behaviors = {row.order_behavior_for(unit.type_id) for unit in units}
    return behaviors.pop() if len(behaviors) == 1 else OrderBehavior.REPLACES


def _ability_of_unit_order(order: raw_pb2.UnitOrder) -> AbilityId:
    """The ability a raw order runs, or `NULL` if the curated ids leave it out. No order of ours runs such an
    ability."""
    return AbilityId.get(order.ability_id) or AbilityId.NULL
