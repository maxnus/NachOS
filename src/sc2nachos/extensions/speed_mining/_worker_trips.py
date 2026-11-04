"""Speed mining's trips: the orders that keep a worker it holds at its field or gas building each turn, each trip ending
in a move, a collector coming out yielding to one going in."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, final

from sc2nachos.extensions.speed_mining._trip_end_points import WORKER_RADIUS
from sc2nachos.ids import AbilityId
from sc2nachos.units import Units, UnitType

if TYPE_CHECKING:
    from collections.abc import Callable, Container, Iterable

    from sc2nachos import Api
    from sc2nachos.extensions.speed_mining._trip_end_points import TripEndPoints
    from sc2nachos.geometry import Point
    from sc2nachos.units import OwnUnit, Unit, UnitOrder

# The distances from the point a trip's move ends at between which a worker on a plain gather or return is given the
# move: AvocaDOS's values.
_NEAREST_DISTANCE_FOR_THE_MOVE = 0.75
_FARTHEST_DISTANCE_FOR_THE_MOVE = 2.0
# How near the line of a worker's move a unit's circle may come before it stands in the way: a margin for units that
# move while the move lasts, not tuned.
_CLEARANCE_OF_THE_MOVE = 0.5
# The types of units in no move's way, whoever owns them: structures, which a move paths around, and larvae.
_TYPES_NEVER_IN_THE_WAY = UnitType.Structure.type_ids | UnitType.Larva.type_ids


@final
class WorkerTrips:
    """The orders of each trip of the workers speed mining holds, each turn: the move at the end of the trip, with the
    gather or return queued behind, a collector coming out yielding to one going in, and plain gathers and returns off
    the move."""

    __slots__ = ("_api",)

    def __init__(self, api: Api[object]) -> None:
        """The trips of the workers of `api`'s game."""
        self._api = api

    def speed_mine(
        self,
        field: Unit[UnitType.AnyMineralField],
        miners_to_order: list[OwnUnit[UnitType.Worker]],
        townhall: Unit[UnitType.Townhall],
        trip_end_points: TripEndPoints,
        units_near_the_trip_end: Callable[[Point], Units[Unit[Any]]],
    ) -> None:
        """Keep `miners_to_order`, of `field`'s miners, mining it, with `townhall` taking their cargo, each on the trip
        back if it carries cargo, else on the trip to `field`, as `_speed_trip` says."""
        mine_plainly = self._mine_plainly
        for miner in miners_to_order:
            returning = miner.is_carrying_resource
            trip_end = trip_end_points.return_point if returning else trip_end_points.gather_point
            self._speed_trip(
                miner,
                field,
                townhall,
                trip_end,
                units_near_the_trip_end,
                returning=returning,
                work_plainly=mine_plainly,
            )

    def speed_collect(
        self,
        gas_building: Unit[UnitType.GasBuilding],
        collectors: list[OwnUnit[UnitType.Worker]],
        collectors_to_order: list[OwnUnit[UnitType.Worker]],
        trip_end_points: TripEndPoints,
        townhall: Unit[UnitType.Townhall],
        units_near_the_trip_end: Callable[[Point], Units[Unit[Any]]],
    ) -> None:
        """Keep `collectors_to_order`, of `gas_building`'s `collectors`, collecting from it, with `townhall` taking
        their cargo, as `_speed_trip` says: those going in first, and then those coming out, each yielding to any
        collector going in on its move, ordered this turn or not, that has yet to pass it. Two on their moves meet head
        on, and the one shoved back nearly stops, where one on its return passes through (in game)."""
        gather_point = trip_end_points.gather_point
        return_point = trip_end_points.return_point
        ordered = set(collectors_to_order)
        left_alone = [collector for collector in collectors if collector not in ordered]
        going_in_on_the_move = [
            collector for collector in left_alone if self._is_on_the_move_to(collector, gather_point)
        ]
        coming_out: list[OwnUnit[UnitType.Worker]] = []
        collect_plainly = self._collect_plainly
        for collector in collectors_to_order:
            if _is_on_the_trip_back(collector.current_order, return_point):
                coming_out.append(collector)
            elif self._speed_trip(
                collector,
                gas_building,
                townhall,
                gather_point,
                units_near_the_trip_end,
                returning=False,
                work_plainly=collect_plainly,
            ):
                going_in_on_the_move.append(collector)
        # Handed in rather than sent to gather when taken off the move: gas from a rich geyser shows no cargo, and a
        # gather sends a worker back with its cargo (in game).
        hand_in_then_gather = self.hand_in_then_gather
        for collector in coming_out:
            self._speed_trip(
                collector,
                gas_building,
                townhall,
                return_point,
                units_near_the_trip_end,
                returning=True,
                work_plainly=hand_in_then_gather,
                yields=_has_yet_to_pass_any(going_in_on_the_move, collector, gas_building),
            )
        for collector in left_alone:
            if self._was_sent_the_move_to(collector, return_point) and _has_yet_to_pass_any(
                going_in_on_the_move, collector, gas_building
            ):
                hand_in_then_gather(collector, gas_building)

    def hand_in_then_gather(self, worker: OwnUnit[UnitType.Worker], resource: Unit[UnitType.AnyResource]) -> None:
        """Have `worker` hand in its cargo and then gather at `resource`: a return with the gather queued behind it,
        which waits for the cargo to be handed in, so that a worker carrying vespene does not go back to its geyser (in
        game)."""
        order_book = self._api.orders
        # Forced: NachOS leaves out a return to a worker the game reports returning aimed at nothing, and a gather
        # queued behind the return the worker shows of itself takes its place, the worker carrying its cargo to the
        # field first (in game).
        order_book.issue(worker, AbilityId.RETURN, force=True)
        order_book.issue(worker, AbilityId.GATHER, target=resource, queued=True)

    def _speed_trip[R: UnitType.AnyResource](
        self,
        worker: OwnUnit[UnitType.Worker],
        resource: Unit[R],
        townhall: Unit[UnitType.Townhall],
        trip_end: Point,
        units_near_the_trip_end: Callable[[Point], Units[Unit[Any]]],
        *,
        returning: bool,
        work_plainly: Callable[[OwnUnit[UnitType.Worker], Unit[R]], None],
        yields: bool = False,
    ) -> bool:
        """Keep `worker` at work at `resource`, a field or a gas building, with `townhall` taking its cargo, on the trip
        back to `townhall` if `returning`, else on the trip to `resource`: give it the move to `trip_end`, where that
        trip ends, near the end of the trip, unless it `yields` or one of the units `units_near_the_trip_end` reads for
        it stands in its way, and take it off the move once it yields or one does. Off the move, `work_plainly` keeps it
        at work. Returns whether it is on the move afterward. The units are read only for a worker on the move or near
        enough to be given it."""
        current_order = worker.current_order
        if current_order is not None and current_order.ability is AbilityId.MOVE:
            # Speed mining's own move, to where this trip or another ends.
            if (
                yields
                or current_order.known_target != trip_end
                or _is_any_in_the_way(units_near_the_trip_end(trip_end), worker.position, trip_end)
            ):
                work_plainly(worker, resource)
                return False
            if len(worker.orders) == 2:
                return True
            # The gather or return queued behind the move is gone, so the two go out again.
        elif not (_is_returning(current_order) if returning else _is_gathering_at(current_order, resource)):
            work_plainly(worker, resource)
            return False
        elif (
            yields
            or not (_NEAREST_DISTANCE_FOR_THE_MOVE < worker.distance_to(trip_end) < _FARTHEST_DISTANCE_FOR_THE_MOVE)
            or _is_any_in_the_way(units_near_the_trip_end(trip_end), worker.position, trip_end)
        ):
            return False
        order_book = self._api.orders
        order_book.issue(worker, AbilityId.MOVE, target=trip_end, force=True)
        if returning:
            # Aimed at the townhall, so the worker goes on into it at speed; a return aimed at nothing stops it at the
            # end of the move first (in game).
            order_book.issue(worker, AbilityId.SMART, target=townhall, queued=True)
        else:
            order_book.issue(worker, AbilityId.GATHER, target=resource, queued=True)
        return True

    def _mine_plainly(self, worker: OwnUnit[UnitType.Worker], field: Unit[UnitType.AnyMineralField]) -> None:
        """Keep `worker` mining `field` with a plain gather, or returning its cargo with the gather queued behind."""
        current_order = worker.current_order
        if worker.is_carrying_resource:
            if current_order is None or current_order.ability is not AbilityId.RETURN:
                self.hand_in_then_gather(worker, field)
        elif not _is_gathering_at(current_order, field):
            self._api.orders.issue(worker, AbilityId.GATHER, target=field)

    def _collect_plainly(self, worker: OwnUnit[UnitType.Worker], gas_building: Unit[UnitType.GasBuilding]) -> None:
        """Keep `worker` collecting from `gas_building` with a plain gather, or returning its cargo with the gather
        queued behind: one returning is left to it."""
        current_order = worker.current_order
        if _is_returning(current_order) or _is_gathering_at(current_order, gas_building):
            return
        if worker.is_carrying_resource:
            self.hand_in_then_gather(worker, gas_building)
        else:
            self._api.orders.issue(worker, AbilityId.GATHER, target=gas_building)

    def _is_on_the_move_to(self, worker: OwnUnit[UnitType.Worker], trip_end: Point) -> bool:
        """Whether `worker` is on speed mining's move to `trip_end`, or was sent it lately, which its orders may not
        show yet."""
        if not worker.is_orderable:
            return False
        current_order = worker.current_order
        if (
            current_order is not None
            and current_order.ability is AbilityId.MOVE
            and current_order.known_target == trip_end
        ):
            return True
        return self._was_sent_the_move_to(worker, trip_end)

    def _was_sent_the_move_to(self, worker: OwnUnit[UnitType.Worker], trip_end: Point) -> bool:
        """Whether the latest order that replaced `worker`'s, sent lately or issued this turn, is speed mining's move to
        `trip_end`, so that no other order, the bot's included, has taken its place since."""
        if not worker.is_orderable:
            return False
        replacing = [order for order in self._api.orders.issued_to(worker, include_sent=True) if not order.queued]
        return bool(replacing) and replacing[-1].ability is AbilityId.MOVE and replacing[-1].target == trip_end


def units_in_the_way_of_a_move(
    units: Iterable[Unit[Any]], miners: Container[Unit[Any]], collectors: Container[Unit[Any]]
) -> Units[Unit[Any]]:
    """Those of `units`, every unit in the game, that can stand in the way of a worker on a move, as `SpeedMining`'s
    docstring counts them: neither structures, larvae, in the air, burrowed nor among the `miners` and `collectors`
    speed mining holds. A worker on a gather or a return passes through units (in game), and one on a move does not
    (stated)."""
    return Units(
        unit
        for unit in units
        if (unit.is_own or unit.is_enemy)
        and unit.type_id not in _TYPES_NEVER_IN_THE_WAY
        and not unit.is_flying
        and not unit.is_burrowed
        and unit not in miners
        and unit not in collectors
    )


def units_within_reach_of(trip_end: Point, units: Units[Unit[Any]]) -> Units[Unit[Any]]:
    """The units of `units` that can stand in the way of a move to `trip_end` from as far as speed mining gives it."""
    reach = _FARTHEST_DISTANCE_FOR_THE_MOVE + WORKER_RADIUS + _CLEARANCE_OF_THE_MOVE
    return units.filter(lambda unit: unit.distance_to(trip_end) < reach + unit.radius)


def carries_cargo(worker: OwnUnit[UnitType.Worker]) -> bool:
    """Whether `worker` carries cargo, as its buffs show, or as its return shows: a worker carrying gas from a rich
    geyser wears no buff, and leaves the gas building ordered to return it (in game)."""
    return worker.is_carrying_resource or _is_returning(worker.current_order)


def _is_any_in_the_way(units: Iterable[Unit[Any]], start: Point, end: Point) -> bool:
    """Whether any of `units` stands in the way of a worker moving from `start` to `end`."""
    clearance = WORKER_RADIUS + _CLEARANCE_OF_THE_MOVE
    return any(unit.position.distance_to_segment(start, end) < clearance + unit.radius for unit in units)


def _is_gathering_at(order: UnitOrder | None, resource: Unit[UnitType.AnyResource]) -> bool:
    """Whether `order` is a gather at `resource`."""
    return order is not None and order.ability is AbilityId.GATHER and order.known_target is resource


def _is_returning(order: UnitOrder | None) -> bool:
    """Whether `order` is a return of cargo."""
    return order is not None and order.ability is AbilityId.RETURN


def _is_on_the_trip_back(order: UnitOrder | None, return_point: Point) -> bool:
    """Whether `order` is a return of cargo or a move to `return_point`, where the move of the trip back ends: a
    collector's trip read off its order, since gas from a rich geyser shows no cargo (in game)."""
    if order is None:
        return False
    ability = order.ability
    return ability is AbilityId.RETURN or (ability is AbilityId.MOVE and order.known_target == return_point)


def _has_yet_to_pass_any(
    going_in: Iterable[OwnUnit[UnitType.Worker]],
    coming_out: OwnUnit[UnitType.Worker],
    gas_building: Unit[UnitType.GasBuilding],
) -> bool:
    """Whether `coming_out`, a collector on its way out of `gas_building`, has yet to pass any of `going_in`, those on
    their way in: one going in is the farther from the gas building's center, or the two touch."""
    distance_out = coming_out.distance_to(gas_building)
    return any(
        collector.distance_to(coming_out) < 2 * WORKER_RADIUS or collector.distance_to(gas_building) > distance_out
        for collector in going_in
    )
