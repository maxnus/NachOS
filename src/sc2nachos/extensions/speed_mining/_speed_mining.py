"""`SpeedMining`: the workers it holds, how they are handed over, and its turn, which orders each."""

from __future__ import annotations

import math
from collections import ChainMap, Counter
from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, cast, final

from sc2nachos.events import EventPriority, TurnEvent
from sc2nachos.extensions.speed_mining._trip_end_point_book import TripEndPointBook, finished_own_townhall_at_center
from sc2nachos.extensions.speed_mining._worker_attacks import WorkerAttacks
from sc2nachos.extensions.speed_mining._worker_trips import (
    WorkerTrips,
    carries_cargo,
    units_in_the_way_of_a_move,
    units_within_reach_of,
)
from sc2nachos.ids import AbilityId
from sc2nachos.units import OwnUnit, Unit, UnitDict, Units, UnitType

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, MutableMapping, Sequence

    from sc2nachos import Api
    from sc2nachos.extensions.speed_mining._trip_end_points import TripEndPoints
    from sc2nachos.gamemap import Expansion
    from sc2nachos.geometry import Point
    from sc2nachos.orders import IssuedOrder
    from sc2nachos.units import Order

# The most workers speed mining holds at one field: with three, it mined 19% less than plain gathers (in game).
_MOST_MINERS_AT_A_FIELD = 2
# The most workers speed mining holds at one gas building: the ideal a refinery reports (corpus).
_MOST_COLLECTORS_AT_A_GAS_BUILDING = 3
# How many collectors `collect`, picking a gas building itself, puts at each of every base's, nearest its townhall
# first, before a third at any, farthest first: two speed collected each collect as much as one alone, and a third adds
# the most where the trip is longest, its gas building taking one worker at a time (in game).
_COLLECTORS_AT_EACH_GAS_BUILDING_BEFORE_A_THIRD = 2


@final
class SpeedMining:
    """Speed mining: each worker handed over to mine minerals mines its field, and each worker handed over to collect
    vespene collects from its gas building, each trip ending in a move with the gather or the return queued behind,
    which keeps the worker's speed into it, where the gather or return alone slows the worker as it nears the field, the
    gas building or the townhall.

    Build it once the game has started, in the bot's own object an api is given as `extensions`:

    ```python
    from sc2nachos import Api
    from sc2nachos.extensions.speed_mining import SpeedMining


    class Extensions:
        def __init__(self, api: Api) -> None:
            self.speed_mining = SpeedMining(api)


    api = Api(extensions=Extensions)
    ```

    How many workers mine and how many collect stays the bot's, and which field or gas building each works may be:
    `mine` hands one over to mine, two at a field at most, at a field of an expansion with a finished townhall of this
    player's at its center, which it picks among the fields it is given or among all. `collect` hands one over to
    collect, three at a gas building at most, at a finished gas building of this player's with vespene left on a geyser
    of such an expansion, which it picks alike. Each sends the worker there at once unless it is at that work already,
    but takes none it cannot send, building, burrowed or out of reach of orders, and holds a worker at one field or gas
    building at a time. `release` takes one or several back, `release_miners` and `release_collectors` a number of
    them, those whose leaving costs the least, and `miners`, `collectors`, `workers` and `workers_at` read them. On
    each `TurnEvent`, at `EventPriority.AFTER`, after every handler of the bot's, speed mining orders each worker it
    holds:

    - A miner idle, gathering minerals, returning minerals, or on speed mining's own move, is kept mining its field:
      one gathering at another field, as the game sends a worker whose field is taken, or on its way to a field it was
      handed before, is sent to its own, and one carrying cargo but not returning it is given a return with the gather
      queued behind. When it comes within 0.75 to 2 of where its trip ends, it is given the move there, with the gather
      at the field queued behind, or a right-click on the townhall, which the worker shows as a return.
    - A collector idle, gathering vespene, returning cargo, or on speed mining's own move, is kept collecting from its
      gas building: one gathering at another gas building, or on its way to one it was handed before, is sent to its
      own, and one carrying cargo but not returning it is given a return with the gather queued behind. Near where its
      trip ends it is given the move there as a miner is, its trip read off its gather or return, since a worker
      carrying gas from a rich geyser shows no cargo (`Unit.is_carrying_gas`): the move of a trip to the gas building
      ends where a worker's circle touches the gas building's, toward the townhall's center.
    - A collector coming out of its gas building is given no move while a collector of the same gas building going in,
      on its move, given it this turn or sent it lately, has yet to pass it, the one going in farther from the gas
      building's center or the two touching, and is given its return back, with the gather queued behind, if it is on
      its move or speed mining's move is the last order it was sent. It passes through on its return.
    - No move is given while a unit stands in the worker's way, its circle within 0.5 of the line from the worker to
      where its trip ends, and a worker on the move that a unit comes in the way of is given its gather or return back.
      Units of both players count, on the ground and not burrowed, but structures, larvae, and the workers speed mining
      holds; a worker it lets go in the turn counts.
    - A worker attacks an enemy it can hit as it stands, unless speed mining was built with `attack_enemies=False`:
      its weapon cooldown at most a step, the enemy visible, on the ground, not a structure, in range
      (`Unit.has_in_range`), and no more than a step's turn away (`Unit.time_to_face`); the one with least life, among
      several. It goes back to mining or collecting once it has fired.
    - A worker's cargo goes back to the townhall standing at the center of the expansion of its field or of its gas
      building's geyser: one of this player's, finished.
      An orbital, a planetary fortress, a lair and a hive count as finished, since only a morph makes them: an orbital
      reads unfinished in the step before its morph finishes. A field or a geyser two expansions share counts as the
      first's, by `Api.expansions`, that has such a townhall.
    - A worker is let go, its orders left as they are, once the bot orders it: once an order issued to it this turn
      stands, no unqueued order coming after it, that is neither a return nor a gather at a mineral field, for a
      miner, or at a gas building, for a collector, as `mine` and `collect` hand a worker over with. Speed mining's
      turn comes after every handler of the bot's, so the worker is let go in the turn of the order, whether or not
      the game takes it, and `miners` and `collectors` hold it until then. A worker the bot sends to gather at another
      field is kept, and sent to its own, as one the game sends there is. So a miner the bot sends to build does not
      come back to its field, nor a drone it burrows: the bot hands it over again.
    - A worker given an order this turn, or sent one lately that may not show yet, as `OrderBook.issued_to` reads with
      `include_sent`, is left alone for the turn, and one burrowed, or doing anything but its work, as an SCV
      repairing by autocast, until it is unburrowed, idle or back at its work.
    - A worker is let go alike once it or its field is dead, and once no expansion holding its field has such a
      townhall any more, as when the townhall dies or lifts off. A collector is let go alike once its gas building is
      dead or has no vespene left, or no expansion holding its geyser has such a townhall.

    Raises `NotPlayingError` if built before the game starts.
    """

    __slots__ = (
        "_api",
        "_attack_enemies",
        "_attacks",
        "_field_by_worker",
        "_gas_building_by_worker",
        "_trip_end_points",
        "_trips",
    )

    def __init__(self, api: Api[object], *, attack_enemies: bool = True) -> None:
        """Speed mining for the workers of `api`'s game, from the next `TurnEvent`, its workers attacking enemies they
        can hit if `attack_enemies`."""
        self._api = api
        self._attack_enemies = attack_enemies
        self._attacks = WorkerAttacks(api)
        self._field_by_worker: UnitDict[OwnUnit[UnitType.Worker], Unit[UnitType.AnyMineralField]] = UnitDict()
        self._gas_building_by_worker: UnitDict[OwnUnit[UnitType.Worker], Unit[UnitType.GasBuilding]] = UnitDict()
        self._trip_end_points = TripEndPointBook(api.expansions)
        self._trips = WorkerTrips(api)
        api.on(TurnEvent, priority=EventPriority.AFTER)(self._order_the_workers)

    @property
    def miners(self) -> Mapping[OwnUnit[UnitType.Worker], Unit[UnitType.AnyMineralField]]:
        """Each worker speed mining holds to mine minerals, with the field it mines: a read-only view, so a worker
        handed over or let go shows at once. A dead worker is never among them."""
        return MappingProxyType(self._field_by_worker)

    @property
    def collectors(self) -> Mapping[OwnUnit[UnitType.Worker], Unit[UnitType.GasBuilding]]:
        """Each worker speed mining holds to collect vespene, with the gas building it collects from: a read-only view,
        as `miners` is. A worker inside its gas building is among them, and a dead one never."""
        return MappingProxyType(self._gas_building_by_worker)

    @property
    def workers(self) -> Mapping[OwnUnit[UnitType.Worker], Unit[UnitType.AnyResource]]:
        """Each worker speed mining holds, with the field it mines or the gas building it collects from: `miners` and
        `collectors` in one read-only view."""
        # Cast: a `ChainMap` takes mutable mappings, whose values a type checker cannot widen.
        miners = cast("MutableMapping[OwnUnit[UnitType.Worker], Unit[UnitType.AnyResource]]", self._field_by_worker)
        collectors = cast(
            "MutableMapping[OwnUnit[UnitType.Worker], Unit[UnitType.AnyResource]]", self._gas_building_by_worker
        )
        return MappingProxyType(ChainMap(miners, collectors))

    def workers_at(self, resource: Unit[UnitType.AnyResource]) -> Units[OwnUnit[UnitType.Worker]]:
        """The workers speed mining holds at `resource`, a mineral field or a gas building, in the order they were
        handed over."""
        if UnitType.AnyMineralField.includes(resource):
            return Units(worker for worker, field in self._field_by_worker.items() if field is resource)
        return Units(
            worker for worker, gas_building in self._gas_building_by_worker.items() if gas_building is resource
        )

    def mine(
        self,
        worker: OwnUnit[UnitType.Worker],
        fields: Unit[UnitType.AnyMineralField] | Iterable[Unit[UnitType.AnyMineralField]] | None = None,
    ) -> Unit[UnitType.AnyMineralField] | None:
        """Have `worker` mine a field until it is released, or let go as the class's docstring says, and return the
        field, or `None` where there is no room or `worker` cannot be sent there now. A worker held at another field,
        or at a gas building, moves to this one.

        Speed mining picks the field among those of the expansions with a finished townhall of this player's at their
        center, and only among `fields`, one or several, if given: at the expansion nearest `worker` with a field fewer
        than two other workers mine, a field of the nearer half of the expansion's fields before one of the farther
        half, and then the one nearest `worker`. Where there is none, as for a field of an expansion without such a
        townhall, `mine` returns `None` and leaves `worker` as it was, mining the field it had, if any.

        Handed over, `worker` is sent to the field at once: a gather at the field, behind a return of any cargo it
        carries, minerals or vespene. So a worker pulled into a fight, or collecting vespene, mines from this turn.
        Nothing is sent to a worker mining minerals already, which speed mining keeps at the field from the next turn,
        nor again to one sent there lately, whose orders may not show it yet, but one given another order this turn is
        sent there. A worker building, burrowed, or out of reach of orders (`OwnUnit.is_orderable`), as inside a
        refinery or a transport, cannot be sent: `mine` returns `None` for it and leaves it as it was, to be handed over
        once it can be.

        Raises `ValueError` for a unit that is not a worker, an SCV, a drone or a probe, and for one of `fields` that is
        not a mineral field.
        """
        _check_is_a_worker(worker)
        only_fields = _given(fields, UnitType.AnyMineralField, "speed mining mines mineral fields")
        field = self._nearest_field_with_room(worker, only_fields)
        if field is None or not self._send_to_gather_at(worker, field, mining=True):
            return None
        # Released first, so that it counts as handed over last wherever it was held.
        self.release(worker)
        self._field_by_worker[worker] = field
        return field

    def collect(
        self,
        worker: OwnUnit[UnitType.Worker],
        gas_buildings: Unit[UnitType.GasBuilding] | Iterable[Unit[UnitType.GasBuilding]] | None = None,
    ) -> Unit[UnitType.GasBuilding] | None:
        """Have `worker` collect vespene from a gas building until it is released, or let go as the class's docstring
        says, and return the gas building, or `None` where there is no room or `worker` cannot be sent there now. A
        worker held at another gas building, or at a field, moves to this one.

        Speed mining picks the gas building among the finished ones of this player's with vespene left, standing on a
        geyser of an expansion with a finished townhall of this player's at its center, and only among
        `gas_buildings`, one or several, if given: at the expansion nearest `worker` with a gas building fewer than two
        other workers collect from, the one of those nearest the expansion's center, before a third at any; where every
        one has two, at the expansion nearest `worker` with one fewer than three others collect from, the one of those
        farthest from its center. Where there is none, `collect` returns `None` and leaves `worker` as it was, held
        where it was, if anywhere.

        Handed over, `worker` is sent to the gas building at once, as `mine` sends a worker to its field: a gather at
        it, behind a return of any cargo it carries. Nothing is sent to a worker collecting vespene already, nor again
        to one sent there lately, but to one given another order this turn, and `collect` returns `None` for a worker
        building, burrowed, or out of reach of orders, as inside a gas building, and leaves it as it was.

        Raises `ValueError` for a unit that is not a worker, and for one of `gas_buildings` that is not a gas building.
        """
        _check_is_a_worker(worker)
        only_gas_buildings = _given(gas_buildings, UnitType.GasBuilding, "speed mining collects from gas buildings")
        gas_building = self._gas_building_with_room_to_pick(worker, only_gas_buildings)
        if gas_building is None:
            return None
        # So that a move to where one of its trips ends reads as speed mining's from the first.
        self._trip_end_points.work_out_gas_building(gas_building)
        if not self._send_to_gather_at(worker, gas_building, mining=False):
            return None
        # Released first, so that it counts as handed over last wherever it was held.
        self.release(worker)
        self._gas_building_by_worker[worker] = gas_building
        return gas_building

    def release(self, workers: OwnUnit[UnitType.Worker] | Iterable[OwnUnit[UnitType.Worker]]) -> None:
        """Stop ordering `workers`, one or several, leaving their orders as they are. Nothing happens for a worker not
        handed over."""
        for worker in _tuple_of_units(workers):
            self._field_by_worker.pop(worker, None)
            self._gas_building_by_worker.pop(worker, None)

    def release_miners(self, count: int) -> Units[OwnUnit[UnitType.Worker]]:
        """Release `count` of the miners speed mining holds, as `release` does, those whose leaving costs the least
        first, and return them in that order, or all of them where it holds fewer: the reverse of how `mine` picks a
        field. One speed mining would let go this turn, as one the bot has ordered, leaves first, then one at a field of
        the farther half of its expansion's fields, then one of the nearer half; of those alike, the one handed over
        last, the likeliest to have mined nothing yet.

        Raises `ValueError` for a `count` below zero."""
        return self._release_the_first(self._miners_cheapest_to_release_first(), count)

    def release_collectors(self, count: int) -> Units[OwnUnit[UnitType.Worker]]:
        """Release `count` of the collectors speed mining holds, as `release` does, those whose leaving costs the least
        vespene first, and return them in that order, or all of them where it holds fewer: the reverse of how
        `collect` fills gas buildings. One speed mining would let go this turn, as one the bot has ordered, leaves
        first, then a gas building's third collector of those it keeps, at the gas building nearest its expansion's
        center first, where a third adds the least, and then a gas building's others, at the one farthest from its
        expansion's center first; of those alike, the one handed over last, the likeliest to have collected nothing
        yet.

        Raises `ValueError` for a `count` below zero."""
        return self._release_the_first(self._collectors_cheapest_to_release_first(), count)

    def _release_the_first(
        self, workers_cheapest_first: list[OwnUnit[UnitType.Worker]], count: int
    ) -> Units[OwnUnit[UnitType.Worker]]:
        """Release the first `count` of `workers_cheapest_first`, or all of them where there are fewer, and return
        them. Raises `ValueError` for a `count` below zero."""
        if count < 0:
            raise ValueError(f"speed mining releases no fewer than none, not {count}")
        leaving = Units(workers_cheapest_first[:count])
        self.release(leaving)
        return leaving

    def _miners_cheapest_to_release_first(self) -> list[OwnUnit[UnitType.Worker]]:
        """The miners held, those whose leaving costs the least first, as `release_miners` says."""
        townhall_by_center: dict[Point, Unit[UnitType.Townhall] | None] = {}
        handed_over_place = {miner: place for place, miner in enumerate(self._field_by_worker)}
        cost_of_leaving: dict[OwnUnit[UnitType.Worker], tuple[int, int]] = {}
        for field, miners in _workers_by_resource(self._field_by_worker).items():
            trip_to_it = self._trip_to_a_field(field, townhall_by_center)
            # 0 for one let go this turn, 1 at a field of the farther half, 2 of the nearer.
            if trip_to_it is None:
                field_rank = 0
            else:
                alive_fields = [
                    expansion_field
                    for expansion_field in trip_to_it[0].expansion.mineral_fields
                    if expansion_field.is_alive
                ]
                field_rank = 1 if _is_of_the_farther_half(alive_fields.index(field), len(alive_fields)) else 2
            for miner in miners:
                leaving_rank = 0 if self._is_ordered_by_the_bot(miner, mining=True) else field_rank
                cost_of_leaving[miner] = (leaving_rank, -handed_over_place[miner])
        return sorted(cost_of_leaving, key=cost_of_leaving.__getitem__)

    def _collectors_cheapest_to_release_first(self) -> list[OwnUnit[UnitType.Worker]]:
        """The collectors held, those whose leaving costs the least first, as `release_collectors` says."""
        townhall_by_center: dict[Point, Unit[UnitType.Townhall] | None] = {}
        handed_over_place = {collector: place for place, collector in enumerate(self._gas_building_by_worker)}
        cost_of_leaving: dict[OwnUnit[UnitType.Worker], tuple[int, float, int]] = {}
        for gas_building, collectors in _workers_by_resource(self._gas_building_by_worker).items():
            trip_to_it = self._trip_to_a_gas_building(gas_building, townhall_by_center)
            from_its_center = 0.0 if trip_to_it is None else gas_building.distance_to(trip_to_it[0].expansion.center)
            place_at_the_gas_building = 0
            for collector in collectors:
                # 0 for one let go this turn, 1 for a third of those kept, the nearer its center first, 2 for the
                # others, the farther first.
                if trip_to_it is None or self._is_ordered_by_the_bot(collector, mining=False):
                    cost_of_leaving[collector] = (0, 0.0, -handed_over_place[collector])
                    continue
                place_at_the_gas_building += 1
                if place_at_the_gas_building > _COLLECTORS_AT_EACH_GAS_BUILDING_BEFORE_A_THIRD:
                    leaving_rank, distance_order = 1, from_its_center
                else:
                    leaving_rank, distance_order = 2, -from_its_center
                cost_of_leaving[collector] = (leaving_rank, distance_order, -handed_over_place[collector])
        return sorted(cost_of_leaving, key=cost_of_leaving.__getitem__)

    def _trip_to_a_field(
        self, field: Unit[UnitType.AnyMineralField], townhall_by_center: dict[Point, Unit[UnitType.Townhall] | None]
    ) -> tuple[TripEndPoints, Unit[UnitType.Townhall]] | None:
        """The end points of the trips to `field` with the townhall taking their cargo, or `None` where speed mining
        lets its miners go: once it is dead, or no expansion holding it has a finished townhall of this player's at its
        center. Each expansion's townhall is read once a turn, kept in `townhall_by_center`."""
        return None if field.is_dead else self._trip_end_points.of_field(field, townhall_by_center)

    def _trip_to_a_gas_building(
        self, gas_building: Unit[UnitType.GasBuilding], townhall_by_center: dict[Point, Unit[UnitType.Townhall] | None]
    ) -> tuple[TripEndPoints, Unit[UnitType.Townhall]] | None:
        """The end points of the trips to `gas_building` with the townhall taking their cargo, or `None` where speed
        mining lets its collectors go: once it is dead or has no vespene left, as `_trip_to_a_field` says otherwise."""
        if gas_building.is_dead or gas_building.vespene_contents == 0:
            return None
        return self._trip_end_points.of_gas_building(gas_building, townhall_by_center)

    def _send_to_gather_at(
        self, worker: OwnUnit[UnitType.Worker], resource: Unit[UnitType.AnyResource], *, mining: bool
    ) -> bool:
        """Send `worker`, being handed over at `resource`, a field to mine if `mining`, else a gas building to collect
        from, there at once, as `mine` and `collect` say, unless it is at that work already, and return whether it
        could be: not while it builds, is burrowed or is out of reach of orders. One the bot ordered elsewhere this turn
        is not at that work, whatever its orders show."""
        if not worker.is_orderable or worker.is_burrowed or worker.is_constructing:
            return False
        order_book = self._api.orders
        if not _has_a_standing_order_other_than_a_hand_overs(order_book.issued_to(worker), mining=mining):
            current_order = worker.current_order
            if current_order is not None and self._is_its_work(worker, current_order, mining=mining):
                return True
            if any(
                order.ability is AbilityId.GATHER and order.target is resource
                for order in order_book.issued_to(worker, include_sent=True)
            ):
                return True
        if carries_cargo(worker):
            self._trips.hand_in_then_gather(worker, resource)
        else:
            order_book.issue(worker, AbilityId.GATHER, target=resource)
        return True

    def _nearest_field_with_room(
        self, worker: OwnUnit[UnitType.Worker], only_fields: frozenset[Unit[UnitType.AnyMineralField]] | None
    ) -> Unit[UnitType.AnyMineralField] | None:
        """The field `mine` picks for `worker`, among `only_fields` unless `None`, as it says, or `None` where there is
        none."""
        miner_counts = _counts_of_the_others(self._field_by_worker, worker)
        for expansion in self._expansions_with_a_finished_townhall_nearest(worker):
            fields = [field for field in expansion.mineral_fields if field.is_alive]
            with_room = [
                (_is_of_the_farther_half(index, len(fields)), worker.distance_to(field), field)
                for index, field in enumerate(fields)
                if miner_counts[field] < _MOST_MINERS_AT_A_FIELD and (only_fields is None or field in only_fields)
            ]
            if with_room:
                return min(with_room, key=lambda candidate: candidate[:2])[2]
        return None

    def _gas_building_with_room_to_pick(
        self, worker: OwnUnit[UnitType.Worker], only_gas_buildings: frozenset[Unit[UnitType.GasBuilding]] | None
    ) -> Unit[UnitType.GasBuilding] | None:
        """The gas building `collect` picks for `worker`, among `only_gas_buildings` unless `None`, as it says, or
        `None` where there is none."""
        collector_counts = _counts_of_the_others(self._gas_building_by_worker, worker)
        with_room_by_position = {
            gas_building.position: gas_building
            for gas_building in self._api.units.own.gas_buildings
            if gas_building.is_complete
            and gas_building.vespene_contents > 0
            and collector_counts[gas_building] < _MOST_COLLECTORS_AT_A_GAS_BUILDING
            and (only_gas_buildings is None or gas_building in only_gas_buildings)
        }
        if not with_room_by_position:
            return None
        # Each expansion's, nearest its center first, as its geysers are, the expansion nearest `worker` first.
        with_room_at_each_expansion = [
            [
                with_room_by_position[geyser.position]
                for geyser in expansion.geysers
                if geyser.position in with_room_by_position
            ]
            for expansion in self._expansions_with_a_finished_townhall_nearest(worker)
        ]
        for with_room_at_the_expansion in with_room_at_each_expansion:
            for gas_building in with_room_at_the_expansion:
                if collector_counts[gas_building] < _COLLECTORS_AT_EACH_GAS_BUILDING_BEFORE_A_THIRD:
                    return gas_building
        return next(
            (
                with_room_at_the_expansion[-1]
                for with_room_at_the_expansion in with_room_at_each_expansion
                if with_room_at_the_expansion
            ),
            None,
        )

    def _expansions_with_a_finished_townhall_nearest(self, worker: OwnUnit[UnitType.Worker]) -> Iterator[Expansion]:
        """The expansions with a finished townhall of this player's at their center, nearest `worker` first."""
        for expansion in sorted(self._api.expansions, key=lambda expansion: worker.distance_to(expansion.center)):
            if finished_own_townhall_at_center(expansion) is not None:
                yield expansion

    def _order_the_workers(self, _: TurnEvent) -> None:
        """Order each worker held, as the class's docstring says: the miners field by field, then the collectors gas
        building by gas building, once the workers of the fields and gas buildings no longer mined or collected from
        are let go, and those the bot ordered, since they count among the units in a move's way."""
        self._let_go_of_the_workers_the_bot_ordered()
        townhall_by_center: dict[Point, Unit[UnitType.Townhall] | None] = {}
        mined_fields = _still_worked(self._field_by_worker, self._trip_to_a_field, townhall_by_center)
        collected_gas_buildings = _still_worked(
            self._gas_building_by_worker, self._trip_to_a_gas_building, townhall_by_center
        )
        units_in_the_way: Units[Unit[Any]] | None = None
        units_near_each_trip_end: dict[Point, Units[Unit[Any]]] = {}
        enemies_to_attack: Units[Unit[Any]] | None = None

        def units_near_the_trip_end(trip_end: Point) -> Units[Unit[Any]]:
            """The units that can stand in the way of a move to `trip_end`, worked out once a turn, and only for a trip
            end a worker comes near."""
            nonlocal units_in_the_way
            if (near := units_near_each_trip_end.get(trip_end)) is None:
                if units_in_the_way is None:
                    units_in_the_way = units_in_the_way_of_a_move(
                        self._api.units, self._field_by_worker, self._gas_building_by_worker
                    )
                near = units_near_each_trip_end[trip_end] = units_within_reach_of(trip_end, units_in_the_way)
            return near

        def workers_to_order(
            workers: list[OwnUnit[UnitType.Worker]], *, mining: bool
        ) -> list[OwnUnit[UnitType.Worker]]:
            """Those of `workers`, miners if `mining`, else collectors, that speed mining keeps at their work this turn:
            neither left alone nor attacking. The enemies to attack are read once a turn, and only once a worker could
            attack."""
            nonlocal enemies_to_attack
            free_workers = [worker for worker in workers if not self._is_left_alone(worker, mining=mining)]
            if not free_workers or not self._attack_enemies:
                return free_workers
            if enemies_to_attack is None:
                enemies_to_attack = self._api.units.enemy.visible.non_structures.ground
            attacks = self._attacks
            return [worker for worker in free_workers if not attacks.attacks(worker, enemies_to_attack)]

        trips = self._trips
        for field, workers, (field_trip_end_points, townhall) in mined_fields:
            trips.speed_mine(
                field, workers_to_order(workers, mining=True), townhall, field_trip_end_points, units_near_the_trip_end
            )
        for gas_building, workers, (gas_trip_end_points, townhall) in collected_gas_buildings:
            trips.speed_collect(
                gas_building,
                workers,
                workers_to_order(workers, mining=False),
                gas_trip_end_points,
                townhall,
                units_near_the_trip_end,
            )

    def _let_go_of_the_workers_the_bot_ordered(self) -> None:
        """Let go of each worker held that the bot has ordered, as `_is_ordered_by_the_bot` says, of those the turn's
        orders name."""
        for worker in {unit for order in self._api.orders.pending for unit in order.units}:
            if worker in self._field_by_worker and self._is_ordered_by_the_bot(worker, mining=True):
                del self._field_by_worker[worker]
            elif worker in self._gas_building_by_worker and self._is_ordered_by_the_bot(worker, mining=False):
                del self._gas_building_by_worker[worker]

    def _is_ordered_by_the_bot(self, worker: OwnUnit[UnitType.Worker], *, mining: bool) -> bool:
        """Whether the bot has ordered `worker`, a miner if `mining`, else a collector: an order issued to it this turn
        stands that is not a hand-over's. Speed mining's turn comes after every handler of the bot's, so the turn's
        orders are the bot's and the hand-overs'."""
        return _has_a_standing_order_other_than_a_hand_overs(self._api.orders.issued_to(worker), mining=mining)

    def _is_left_alone(self, worker: OwnUnit[UnitType.Worker], *, mining: bool) -> bool:
        """Whether speed mining leaves `worker`, a miner if `mining`, else a collector, alone this turn: not orderable,
        burrowed, given an order lately, or on an order that is not its work."""
        if not worker.is_orderable or worker.is_burrowed or self._api.orders.issued_to(worker, include_sent=True):
            return True
        current_order = worker.current_order
        return current_order is not None and not self._is_its_work(worker, current_order, mining=mining)

    def _is_its_work(self, worker: OwnUnit[UnitType.Worker], order: Order, *, mining: bool) -> bool:
        """Whether `order`, the current one of `worker`, is its work or speed mining's, for a worker mining minerals if
        `mining`, else collecting vespene: a gather at a mineral field, or at a gas building; a return of minerals, or
        of anything else, as gas from a rich geyser shows no cargo (in game); a move to where one of speed mining's
        moves ends; or speed mining's attack. A worker returning the other kind goes back to where it got it next, by
        itself."""
        match order.ability:
            case AbilityId.GATHER:
                target = order.known_target
                gathered_at = UnitType.AnyMineralField if mining else UnitType.GasBuilding
                return isinstance(target, Unit) and gathered_at.includes(target)
            case AbilityId.RETURN:
                return worker.is_carrying_minerals == mining
            case AbilityId.MOVE:
                return order.known_target in self._trip_end_points
            case AbilityId.ATTACK:
                return self._attacks.is_speed_minings_attack(worker, order)
            case _:
                return False


def _given[R: UnitType.AnyType](
    resources: Unit[R] | Iterable[Unit[R]] | None, group: type[R], refusal: str
) -> frozenset[Unit[R]] | None:
    """`resources`, one or several, as a set, or `None` for `None`. Raises `ValueError`, `refusal` first, for one not
    of `group`."""
    if resources is None:
        return None
    given = _tuple_of_units(resources)
    for resource in given:
        if not group.includes(resource):
            raise ValueError(f"{refusal}, not {resource!r}")
    return frozenset(given)


def _still_worked[R: Unit[Any], T](
    resource_by_worker: UnitDict[OwnUnit[UnitType.Worker], R],
    worked_as: Callable[[R, dict[Point, Unit[UnitType.Townhall] | None]], T | None],
    townhall_by_center: dict[Point, Unit[UnitType.Townhall] | None],
) -> list[tuple[R, list[OwnUnit[UnitType.Worker]], T]]:
    """Each value of `resource_by_worker` with its workers, in the order they were handed over, and what `worked_as`
    reads of it with `townhall_by_center`, once the workers of each it reads `None` for are let go. One call each, the
    townhalls passed along rather than closed over, as this runs every turn."""
    still_worked: list[tuple[R, list[OwnUnit[UnitType.Worker]], T]] = []
    for resource, workers in _workers_by_resource(resource_by_worker).items():
        if (worked := worked_as(resource, townhall_by_center)) is None:
            for worker in workers:
                del resource_by_worker[worker]
        else:
            still_worked.append((resource, workers, worked))
    return still_worked


def _workers_by_resource[R: Unit[Any]](
    resource_by_worker: UnitDict[OwnUnit[UnitType.Worker], R],
) -> dict[R, list[OwnUnit[UnitType.Worker]]]:
    """Each value of `resource_by_worker` with its workers, in the order they were handed over."""
    workers_by_resource: dict[R, list[OwnUnit[UnitType.Worker]]] = {}
    for worker, resource in resource_by_worker.items():
        workers_by_resource.setdefault(resource, []).append(worker)
    return workers_by_resource


def _has_a_standing_order_other_than_a_hand_overs(orders: Sequence[IssuedOrder], *, mining: bool) -> bool:
    """Whether one of `orders`, issued to a worker this turn, a miner if `mining`, else a collector, stands, no unqueued
    order coming after it, and is not a hand-over's: a return, or a gather at a mineral field, for a miner, or at a gas
    building, for a collector."""
    if not orders:
        return False
    standing_from = max((place for place, order in enumerate(orders) if not order.queued), default=0)
    gathered_at = UnitType.AnyMineralField if mining else UnitType.GasBuilding
    return not all(
        order.ability is AbilityId.RETURN
        or (order.ability is AbilityId.GATHER and isinstance(order.target, Unit) and gathered_at.includes(order.target))
        for order in orders[standing_from:]
    )


def _is_of_the_farther_half(place: int, field_count: int) -> bool:
    """Whether the field at `place`, counted from 0, of an expansion's `field_count` alive fields, nearest its center
    first, is of the farther half, as `mine` reads them: the farther holds one fewer of an odd count."""
    return place >= math.ceil(field_count / 2)


def _counts_of_the_others[R: Unit[Any]](
    resource_by_worker: UnitDict[OwnUnit[UnitType.Worker], R], worker: OwnUnit[UnitType.Worker]
) -> Counter[R]:
    """How many workers of `resource_by_worker` each resource has, `worker` left out."""
    return Counter(resource for other, resource in resource_by_worker.items() if other is not worker)


def _check_is_a_worker(unit: Unit[Any]) -> None:
    """Raise `ValueError` unless `unit` is a worker, an SCV, a drone or a probe."""
    if not UnitType.Worker.includes(unit):
        raise ValueError(f"speed mining orders workers, not {unit!r}")


def _tuple_of_units[U: Unit[Any]](units: U | Iterable[U]) -> tuple[U, ...]:
    """The units given, one unit or any number of them, as a tuple."""
    return (units,) if isinstance(units, Unit) else tuple(units)
