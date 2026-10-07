"""The orders a bot gives in a turn, sent after its handlers have run, and those held until a unit can start them."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Any, NamedTuple, final, overload

from s2clientprotocol import raw_pb2, sc2api_pb2

from sc2nachos.gamedata import OrderBehavior
from sc2nachos.gamedata._sent_as import Aim
from sc2nachos.gamedata._techtree import ABILITIES_SENT_AS_ANOTHER
from sc2nachos.geometry import Point
from sc2nachos.geometry._point import coordinates
from sc2nachos.ids import AbilityId
from sc2nachos.orders._commands import create_camera_move_action, create_unit_command_actions
from sc2nachos.orders._held_queue import HeldQueue
from sc2nachos.orders._order import Order
from sc2nachos.orders._starting import product_steps, site_of, slots, steps_until_free, travel_steps, within_reach
from sc2nachos.orders._targets import aimed_at, as_sent, check_target, same_point, same_target
from sc2nachos.protocol import ProtocolError
from sc2nachos.state import ActionFailure, ActionResult
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
# `UNLOAD`.
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
# What a structure's making does to its orders: a train or a research queues, an add-on or a morph needs it idle.
_MAKING = frozenset({OrderBehavior.QUEUES, OrderBehavior.NEEDS_IDLE})
# What an unqueued order does that drops what is held for a unit.
_DROPPING_HELD = frozenset({OrderBehavior.REPLACES, OrderBehavior.NEEDS_IDLE})


class _Outgoing(NamedTuple):
    """An order going out in the turn's request: to which units, aimed where, whether queued, and the queue it left,
    if it was held."""

    order: Order[Any]
    units: tuple[OwnUnit[Any], ...]
    target: Target | None
    queued: bool
    queue: HeldQueue | None


@final
class OrderBook:
    """This player's orders for the turn, and those held until a unit can start them.

    A bot gives orders through `api.orders` while its handlers run. They are sent in one request after the turn's
    last handler returns. A unit carries out only the last order it is given in a turn, plus any abilities it
    carries out at once.
    """

    __slots__ = (
        "_build_reach",
        "_camera_location",
        "_forced",
        "_game_data",
        "_held",
        "_issued",
        "_issued_by_unit",
        "_last_sent",
        "_observations",
        "_refusals",
        "_step",
    )

    def __init__(self, game_data: GameData, *, build_reach: float) -> None:
        """A book for one game. `game_data` says what each ability does and what it must be aimed at, and a held build
        goes out once its worker is within `build_reach` of the site."""
        self._game_data = game_data
        self._build_reach = build_reach
        # The turn's orders in the order they were issued, and those of each unit by its id.
        self._issued: list[Order[Any]] = []
        self._issued_by_unit: dict[int, list[Order[Any]]] = {}
        # The turn's orders issued with `force`, sent even to units already carrying them out.
        self._forced: set[Order[Any]] = set()
        # By unit id, what is held for the unit until it can start it.
        self._held: dict[int, HeldQueue] = {}
        # By unit id, the last unqueued order the game took that replaces the unit's orders, and the number of the
        # observation its turn read.
        self._last_sent: dict[int, tuple[Order[Any], int]] = {}
        self._camera_location: Point | None = None
        # What the game refused of the last turn's orders, one entry per unit a refused command named.
        self._refusals: tuple[ActionFailure, ...] = ()
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
        `queued` puts the order behind each unit's current orders, those held for it included. `data` is the bot's
        own; NachOS carries it and never reads it. Nothing is sent until the turn's handlers have all run.

        A worker's build, and a structure's train, research, add-on or morph, that costs is held until its unit can
        start it, and so is whatever is queued behind a held order. A worker is sent to the site and given the build
        once within the api's `build_reach`; a structure is given a train or a research once it has a slot free, and
        an add-on or a morph once it is idle on the ground, a flying one given a point being sent to land there. Given
        to several units, such an order is given to the one that can start it soonest, and goes behind what that one
        already has: a structure by the steps until it has a slot free, a worker by the steps it walks to the site,
        one already busy only if all are, the lowest id among equals. An unqueued order that replaces a unit's orders
        or needs it idle drops what is held for it; a train or a research goes behind what is held, queued or not.

        An unqueued order that replaces a unit's orders is not sent to a unit already doing it: sending it would only
        drop what the unit has queued behind it. What a unit is doing is the last such order sent to it, while the
        observation after its turn may not show it yet, and otherwise its first reported order. `force` sends it to
        those units too: that is how a unit is made to drop its queue and go on with its current order. It still
        competes with the turn's other orders like any other.

        An ability that goes out as another for some types (`AbilityData.sent_as`) is sent as one command per ability
        it goes out as. A command the game refuses is listed in `api.action_failures` once for each unit it names, and
        when it was held, what is held behind it for that unit is dropped.

        Raises `TypeError` for a target the ability cannot be aimed at, for an order without one to a type that goes
        out aimed at it, as a liberator's siege does, or to a flying structure given an add-on, and for a transport's
        unload at a point aimed at one of `units` itself, which is `UNLOAD` for that one: a group given an unload at
        one of its own transports is two orders. Raises `ValueError` for a custom ability given to a type it has
        nothing to be sent as for, or which this game's tables lack an ability it is sent as for.
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
            _check_placed_where_flying(ability, row, given)
        if isinstance(aimed, Unit):
            _check_not_aimed_at_itself(ability, aimed, given)
        if len(given) > 1 and row is not None and all(_is_held_kind(row, unit) for unit in given):
            picked = min(given, key=lambda unit: (*self._start_key(row, aimed, unit), unit.id))
            queued = queued or self._is_busy(picked)
            given = (picked,)
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
        """What `unit` was ordered that has not gone out: what is held for it, head first, then the turn's orders to
        it so far, in the order they were issued. Withdrawn orders are left out.

        A handler late in a turn reads this to leave alone a unit an earlier handler has ordered, since only the last
        order a unit is given goes out, or that has a build or a train waiting.
        """
        queue = self._held.get(unit.id)
        held = () if queue is None else queue.orders
        turns = self._issued_by_unit.get(unit.id, ())
        return tuple(order for order in (*held, *turns) if not order._withdrawn)

    @property
    def pending(self) -> tuple[Order[Any], ...]:
        """Every order issued this turn and still to be sent. Withdrawn orders are left out."""
        return tuple(order for order in self._issued if not order._withdrawn)

    def camera(self, at: PointLike) -> None:
        """Move this player's camera to `at`, along with the turn's orders. Only the last move of a turn is sent."""
        aimed = coordinates(at)
        self._camera_location = Point((as_sent(aimed[0]), as_sent(aimed[1])))

    def _send(self, client: Client) -> None:
        """Send the turn's orders and what is held that can start now, and keep what the game refused of them. Sends
        nothing if there is nothing to send."""
        issued, forced = self._issued, self._forced
        self._issued, self._issued_by_unit, self._forced = [], {}, set()
        self._refusals = ()
        outgoing = list(self._turns_orders(issued, forced))
        for queue in self._held.values():
            outgoing += self._release(queue)
        commands = [(out, *command) for out in outgoing for command in _commands(out)]
        actions = [action for _, action, _ in commands]
        if self._camera_location is not None:
            actions.append(create_camera_move_action(self._camera_location))
            self._camera_location = None
        if not actions:
            return
        results = client.act(actions).result
        if len(results) < len(commands):
            raise ProtocolError(f"the game answered {len(results)} of the {len(commands)} commands it was sent")
        # A camera move, if any, is answered last and belongs to no order.
        refusals: list[ActionFailure] = []
        for (out, _, units), result in zip(commands, results, strict=False):
            action_result = ActionResult.read(result)
            if action_result is not ActionResult.SUCCESS:
                refusals += [ActionFailure(self._step, unit, out.order.ability, action_result) for unit in units]
                if out.queue is not None:
                    out.queue.drop()
            elif not out.queued:
                self._remember_sent(out.order, units)
        self._refusals = tuple(refusals)

    def _observe(self, step: int, lost: Iterable[Unit[Any]]) -> None:
        """Take in the observation at `step`, which found `lost` dead or no longer this player's. What was held for
        them goes."""
        self._step = step
        self._observations += 1
        for unit in lost:
            self._last_sent.pop(unit.id, None)
            self._held.pop(unit.id, None)
        recent = self._observations - _SHOWN_WITHIN + 1
        for unit_id, queue in list(self._held.items()):
            queue.forget_released_before(recent)
            if queue.is_empty():
                del self._held[unit_id]

    def _turns_orders(self, issued: Sequence[Order[Any]], forced: Collection[Order[Any]]) -> Iterator[_Outgoing]:
        """What of the turn's orders goes out now: each to the units it is not held for and that are not carrying it
        out already, unless it is in `forced`. An order that drops what is held for a unit does so first."""
        for order, units in self._orders_given(issued):
            self._drop_held(order, units)
            going = tuple(unit for unit in units if not self._hold(order, unit))
            if order not in forced:
                going = self._units_not_carrying_it_out(order, going)
            if going:
                yield _Outgoing(order, going, order.target, order.queued, None)

    def _orders_given(self, issued: Sequence[Order[Any]]) -> Iterator[tuple[Order[Any], tuple[OwnUnit[Any], ...]]]:
        """Each of the turn's orders not withdrawn, with the units it keeps.

        An order that acts at once, or that is queued behind a unit's current orders, competes with nothing. Of the
        rest, a unit keeps only the last it was given.
        """
        holder: dict[int, Order[Any]] = {}
        for order in issued:
            if not order._withdrawn:
                for unit in order.units:
                    if self._competes_for(order, unit):
                        holder[unit.id] = order
        for order in issued:
            if order._withdrawn:
                continue
            units = tuple(
                unit for unit in order.units if holder.get(unit.id) is order or not self._competes_for(order, unit)
            )
            if units:
                yield order, units

    def _competes_for(self, order: Order[Any], unit: OwnUnit[Any]) -> bool:
        """Whether `order` competes with the turn's other orders for `unit`.

        An unqueued order that replaces the unit's orders or needs it idle does: the unit takes the last of them it
        was given. A queued order competes with nothing, since the bot is asking for a place in the queue, and nor
        does a train or a research, which goes behind the structure's other items, or an ability the unit's type
        carries out at once: the unit does both (in game). Each unit of a group is judged by its own type.
        """
        if order.queued:
            return False
        row = self._game_data.abilities.get(order.ability)
        return _behavior_for(row, unit) in _DROPPING_HELD

    def _add(self, order: Order[Any]) -> None:
        """Count `order` among the turn's, last."""
        self._issued.append(order)
        for unit_id in {unit.id for unit in order.units}:
            self._issued_by_unit.setdefault(unit_id, []).append(order)

    def _drop_held(self, order: Order[Any], units: Sequence[OwnUnit[Any]]) -> None:
        """Let go of what is held for each of `units` whose orders `order`, unqueued, replaces or needs idle."""
        if order.queued:
            return
        row = self._game_data.abilities.get(order.ability)
        for unit in units:
            queue = self._held.get(unit.id)
            if queue is not None and _behavior_for(row, unit) in _DROPPING_HELD:
                queue.drop()

    def _hold(self, order: Order[Any], unit: OwnUnit[Any]) -> bool:
        """Hold `order` for `unit`, and return `True`, if it waits there: behind what is held for the unit, or as a
        build, a train, a research, an add-on or a morph that costs. An ability the unit carries out at once never
        waits."""
        row = self._game_data.abilities.get(order.ability)
        if _behavior_for(row, unit) is OrderBehavior.KEEPS_ORDERS:
            return False
        queue = self._held.get(unit.id)
        if not ((queue is not None and queue.holds_any()) or _is_held_kind(row, unit)):
            return False
        if queue is None:
            queue = self._held[unit.id] = HeldQueue(unit)
        queue.orders.append(order)
        return True

    def _release(self, queue: HeldQueue) -> list[_Outgoing]:
        """What goes out of `queue` this turn: each order from its head that its unit can start, then the lead-in the
        next one needs, a move to its site or a landing. An order the unit is already carrying out leaves the queue
        unsent. A queue whose lead-in failed is let go of. Nothing goes to a unit the last observation left out, as
        one in a transport or a gas building."""
        if queue.unit.is_stale:
            return []
        queue.forget_withdrawn()
        outgoing: list[_Outgoing] = []
        while queue.orders:
            order = queue.orders[0]
            if self._can_start(queue, order):
                released = self._released(queue)
                if not self._is_carrying_out(released):
                    outgoing.append(released)
            elif self._lead_in_failed(queue, order):
                queue.drop()
            else:
                if (lead_in := self._lead_in(queue, order)) is not None:
                    outgoing.append(lead_in)
                break
        return outgoing

    def _released(self, queue: HeldQueue) -> _Outgoing:
        """The head of `queue`, taken out to go now: in place of its lead-in, and with no point once it has landed."""
        lead_in = queue.lead_in_for(queue.orders[0])
        order = queue.release(self._observations)
        if lead_in is None:
            return _Outgoing(order, (queue.unit,), order.target, order.queued, queue)
        target = None if lead_in[0] is AbilityId.LAND else order.target
        return _Outgoing(order, (queue.unit,), target, False, queue)

    def _is_carrying_out(self, out: _Outgoing) -> bool:
        """Whether `out` is an unqueued order replacing its unit's orders that the unit is already doing."""
        (unit,) = out.units
        row = self._game_data.abilities.get(out.order.ability)
        if out.queued or _behavior_for(row, unit) is not OrderBehavior.REPLACES:
            return False
        return self._is_doing(unit, out.order.ability, out.target)

    def _can_start(self, queue: HeldQueue, order: Order[Any]) -> bool:
        """Whether `queue`'s unit can start `order` now: a train or a research once the structure has a slot free, an
        add-on or a morph once it is idle and, given a point, on the ground, and a build once the worker is within
        reach. Anything else queued behind those can start once it is at the head."""
        unit, target = queue.unit, order.target
        row = self._game_data.abilities.get(order.ability)
        match _behavior_for(row, unit):
            case OrderBehavior.QUEUES:
                return self._free_slots(queue) > 0
            case OrderBehavior.NEEDS_IDLE:
                return self._is_idle(queue) and not (unit.is_flying and target is not None)
            case OrderBehavior.REPLACES if target is not None and _is_held_kind(row, unit):
                reached = within_reach(unit, target, self._build_reach)
                return reached and (not order.queued or self._may_start_queued(queue, order, target))
            case _:
                return True

    def _may_start_queued(self, queue: HeldQueue, order: Order[Any], site: Target) -> bool:
        """Whether a build queued behind a worker's other orders may start: never in the turn it was given in, and
        then once the worker is idle or on its way to `site`."""
        if order.issued_step >= self._step:
            return False
        return self._is_idle(queue) or self._is_doing(queue.unit, AbilityId.MOVE, site_of(site))

    def _free_slots(self, queue: HeldQueue) -> int:
        """The slots `queue`'s structure has free: those it runs at once, less the trains and researches it shows or
        was just sent, and none while it shows or was just sent an add-on or a morph."""
        unit = queue.unit
        abilities = [_ability_of_unit_order(order) for order in unit._latest_report.orders]
        abilities += [order.ability for order in self._in_flight(queue)]
        taken = 0
        for ability in abilities:
            match _behavior_for(self._game_data.abilities.get(ability), unit):
                case OrderBehavior.NEEDS_IDLE:
                    return 0
                case OrderBehavior.QUEUES:
                    taken += 1
                case _:
                    pass
        return slots(unit, self._game_data) - taken

    def _is_idle(self, queue: HeldQueue) -> bool:
        """Whether `queue`'s unit is doing nothing: it shows no order, and nothing sent to it may be on its way."""
        unit = queue.unit
        return not unit._latest_report.orders and self._sent_and_not_shown(unit) is None and not self._in_flight(queue)

    def _in_flight(self, queue: HeldQueue) -> list[Order[Any]]:
        """What went out of `queue` that the observations may not show yet."""
        return queue.released_since(self._observations - _SHOWN_WITHIN + 1)

    def _lead_in_failed(self, queue: HeldQueue, order: Order[Any]) -> bool:
        """Whether the lead-in sent for `order` has had time to show and left the unit idle where it cannot start it."""
        lead_in = queue.lead_in_for(order)
        return lead_in is not None and self._observations - lead_in[1] >= _SHOWN_WITHIN and self._is_idle(queue)

    def _lead_in(self, queue: HeldQueue, order: Order[Any]) -> _Outgoing | None:
        """The lead-in `order`, the head of `queue`, needs and has not been sent: a move to a build's site, or a
        landing at the point a flying structure was given. It is recorded and not sent where the unit is doing it
        already."""
        unit, target = queue.unit, order.target
        if target is None or queue.lead_in_for(order) is not None:
            return None
        row = self._game_data.abilities.get(order.ability)
        behavior = _behavior_for(row, unit)
        if behavior is OrderBehavior.NEEDS_IDLE and unit.is_flying:
            ability = AbilityId.LAND
        elif behavior is OrderBehavior.REPLACES and _is_held_kind(row, unit):
            ability = AbilityId.MOVE
        else:
            return None
        site = site_of(target)
        queue.note_lead_in(order, ability, self._observations)
        if self._is_doing(unit, ability, site):
            return None
        lead_in = Order(
            ability,
            (unit,),
            site,
            queued=order.queued,
            data=None,
            order_behavior=OrderBehavior.REPLACES,
            step=self._step,
        )
        return _Outgoing(lead_in, (unit,), site, order.queued, queue)

    def _start_key(self, row: AbilityData, target: Target | None, unit: OwnUnit[Any]) -> tuple[bool, float]:
        """How soon `unit` could start an order of `row`'s ability at `target`: a worker, or a flying structure, by
        whether it is busy and then by the steps it goes to the site; a structure by the steps until it has a slot
        free, or is idle for an add-on or a morph."""
        if target is not None and (unit.is_flying or _behavior_for(row, unit) is OrderBehavior.REPLACES):
            return self._is_busy(unit), travel_steps(unit, target)
        return False, self._steps_until_free(row, unit)

    def _steps_until_free(self, row: AbilityData, unit: OwnUnit[Any]) -> float:
        """The steps until structure `unit` has a slot free for an order of `row`'s ability, or is idle for one that
        needs it: what it shows, from its progress, then what is held or issued for it this turn, in full."""
        rows, game_data = self._game_data.abilities, self._game_data
        durations: list[float] = []
        for shown in unit._latest_report.orders:
            making = rows.get(_ability_of_unit_order(shown))
            if _behavior_for(making, unit) in _MAKING:
                durations.append((1.0 - shown.progress) * product_steps(making, unit.type_id, game_data))
        for waiting in self._waiting(unit):
            durations.append(product_steps(rows.get(waiting.ability), unit.type_id, game_data))
        every_slot = _behavior_for(row, unit) is OrderBehavior.NEEDS_IDLE
        return steps_until_free(durations, slots(unit, game_data), every_slot=every_slot)

    def _waiting(self, unit: OwnUnit[Any]) -> list[Order[Any]]:
        """What is held for `unit`, and the turn's orders to it that may be held."""
        queue = self._held.get(unit.id)
        held = [] if queue is None else [order for order in queue.orders if not order._withdrawn]
        turns = self._issued_by_unit.get(unit.id, ())
        rows = self._game_data.abilities
        return held + [
            order for order in turns if not order._withdrawn and _is_held_kind(rows.get(order.ability), unit)
        ]

    def _is_busy(self, unit: OwnUnit[Any]) -> bool:
        """Whether `unit` has something held for it or about to be, or is on its way to build or building."""
        if self._waiting(unit):
            return True
        orders = unit._latest_report.orders
        first = None if not orders else self._game_data.abilities.get(_ability_of_unit_order(orders[0]))
        return first is not None and first.needs_placement

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


def _commands(out: _Outgoing) -> list[tuple[sc2api_pb2.Action, tuple[OwnUnit[Any], ...]]]:
    """The raw commands `out` goes out as, each with the units it names."""
    ability = out.order.ability
    sent_as = ABILITIES_SENT_AS_ANOTHER.get(ability, _SENT_UNCHANGED)
    return create_unit_command_actions(ability, out.units, out.target, sent_as, queued=out.queued)


def _is_held_kind(row: AbilityData | None, unit: OwnUnit[Any]) -> bool:
    """Whether an order of `row`'s ability is held for `unit` until the unit can start it: one that costs, and that a
    structure makes, or that a worker places."""
    if row is None or not (row.cost.minerals or row.cost.vespene):
        return False
    behavior = row.order_behavior_for(unit.type_id)
    return behavior in _MAKING or (behavior is OrderBehavior.REPLACES and row.needs_placement)


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


def _check_placed_where_flying(ability: AbilityId, row: AbilityData | None, units: Sequence[OwnUnit[Any]]) -> None:
    """Raise `TypeError` if `ability`, given no target, places something and one of `units` is flying."""
    if row is None or not row.needs_placement:
        return
    if flying := sorted({unit.type_id.name for unit in units if unit.is_flying}):
        raise TypeError(f"{ability.name} takes a point for a flying {', '.join(flying)}, and was given no target")


def _check_not_aimed_at_itself(ability: AbilityId, target: Unit[Any], units: Sequence[OwnUnit[Any]]) -> None:
    """Raise `TypeError` if `ability` is aimed at one of `units` and another id is that."""
    custom = _IS_AIMED_AT_ITSELF.get(ability)
    if custom is not None and any(unit.id == target.id for unit in units):
        raise TypeError(f"{ability.name} aimed at the unit itself is {custom.name}")
