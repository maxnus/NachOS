"""Speed mining: workers mine minerals and collect vespene faster, each trip ending in a move that keeps their speed
into the gather or return."""

from __future__ import annotations

import math
from collections import ChainMap, Counter
from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Self, cast, final

from sc2nachos.events import EventPriority, TurnEvent
from sc2nachos.gametime import GameTime
from sc2nachos.geometry import POINT_PRECISION, Circle, Point
from sc2nachos.ids import AbilityId, UnitTypeId
from sc2nachos.units import OwnUnit, Unit, UnitDict, Units, UnitType

if TYPE_CHECKING:
    from collections.abc import Callable, MutableMapping

    from sc2nachos import Api
    from sc2nachos.gamemap import Expansion
    from sc2nachos.units import UnitOrder

# How far from a field's center the move of a trip to it ends, toward the townhall: AvocaDOS's value, inside the 1.5
# at which a worker's circle touches a field's (corpus).
_GATHER_POINT_DISTANCE = 1.325
# How far from a townhall's center the move of a trip back to it ends: its radius and a worker's, 2.75 and 0.375
# (corpus).
_RETURN_POINT_DISTANCE = 3.125
# The distances from the point a trip's move ends at between which a worker on a plain gather or return is given the
# move: AvocaDOS's values.
_NEAREST_DISTANCE_FOR_THE_MOVE = 0.75
_FARTHEST_DISTANCE_FOR_THE_MOVE = 2.0
# An SCV's, a drone's and a probe's radius (corpus).
_WORKER_RADIUS = 0.375
# How near the line of a worker's move a unit's circle may come before it stands in the way: a margin for units that
# move while the move lasts, not tuned.
_CLEARANCE_OF_THE_MOVE = 0.5
# The types of units in no move's way, whoever owns them: structures, which a move paths around, and larvae.
_TYPES_NEVER_IN_THE_WAY = UnitType.Structure.type_ids | UnitType.Larva.type_ids
# The most workers speed mining holds at one field: with three, it mined 19% less than plain gathers (in game).
_MOST_MINERS_AT_A_FIELD = 2
# The most workers speed mining holds at one gas building: the ideal a refinery reports (corpus).
_MOST_COLLECTORS_AT_A_GAS_BUILDING = 3
# How many collectors `collect`, picking a gas building itself, puts at each of a base's, nearest its townhall first,
# before a third at any, farthest first: two speed collected each collect as much as one alone, and a third adds the
# most where the trip is longest, its gas building taking one worker at a time (in game).
_COLLECTORS_AT_EACH_GAS_BUILDING_BEFORE_A_THIRD = 2
# The most weapon cooldown, and the most turn toward an enemy, with which a worker attacks it: the first step of a turn
# costs nothing (in game).
_MOST_COOLDOWN_TO_ATTACK = GameTime(steps=1)
_MOST_TURN_TO_ATTACK = GameTime(steps=1)
# No worker's weapon reaches farther: an SCV's, a drone's and a probe's reach 0.19995, and no upgrade changes it
# (corpus). An enemy farther than this and both radii from a worker is out of its reach without the full check.
_MOST_WEAPON_RANGE_OF_A_WORKER = 0.2
# The townhalls only a morph makes, of a finished command center or hatchery: one that reads unfinished is finishing its
# morph, as an orbital reads at build progress 0.998 the step before it does (in game).
_TOWNHALLS_ONLY_A_MORPH_MAKES = frozenset(
    {UnitTypeId.ORBITAL_COMMAND, UnitTypeId.PLANETARY_FORTRESS, UnitTypeId.LAIR, UnitTypeId.HIVE}
)


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

    Which worker mines which field, and which collects from which gas building, stays the bot's: `mine` hands one over
    to mine, two at a field at most, at a field of an expansion with a finished townhall of this player's at its center,
    which it picks among the fields it is given or among all. `collect` hands one over to collect, three at a gas
    building at most, at a finished gas building of this player's with vespene left on a geyser of such an expansion,
    which it picks alike. Each sends the worker there at once unless it is at that work already, but takes none it
    cannot send, building, burrowed or out of reach of orders, and holds a worker at one field or gas building at a
    time. `release` takes one or several back, and `miners`, `collectors`, `workers` and `workers_at` read them. On
    each `TurnEvent`, at `EventPriority.LOW`, speed mining orders each worker it holds:

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
      on its move or given it this turn, has yet to pass it, the one going in farther from the gas building's center or
      the two touching, and is given its return back if it is on its move. It passes through on its return.
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
    - A worker doing anything else is left alone until it is idle or back at its work: a miner building, attacking on
      the bot's order or collecting vespene among them, and a collector mining minerals too. So is a burrowed drone,
      and one given an order this turn, or sent one lately that may not show yet, as `OrderBook.issued_to` reads with
      `include_sent`. So a miner sent to build comes back to its field by itself, and one the bot sends to collect
      vespene stays there until it is released, or handed over again; a collector alike.
    - A worker is let go, its orders left as they are, once it or its field is dead, and once no expansion holding its
      field has such a townhall any more, as when the townhall dies or lifts off. A collector is let go alike once its
      gas building is dead or has no vespene left, or no expansion holding its geyser has such a townhall.

    Raises `NotPlayingError` if built before the game starts.
    """

    __slots__ = (
        "_api",
        "_every_trip_end_point",
        "_expansions_by_geyser_position",
        "_field_by_worker",
        "_gas_building_by_worker",
        "_attack_enemies",
        "_target_by_attacker",
        "_trip_end_points_by_field",
        "_trip_end_points_by_geyser_position",
    )

    def __init__(self, api: Api[object], *, attack_enemies: bool = True) -> None:
        """Speed mining for the workers of `api`'s game, from the next `TurnEvent`, its workers attacking enemies they
        can hit if `attack_enemies`."""
        self._api = api
        self._attack_enemies = attack_enemies
        self._field_by_worker: UnitDict[OwnUnit[UnitType.Worker], Unit[UnitType.AnyMineralField]] = UnitDict()
        self._gas_building_by_worker: UnitDict[OwnUnit[UnitType.Worker], Unit[UnitType.GasBuilding]] = UnitDict()
        # The expansions holding each geyser, by its position, where a gas building on it stands (in game).
        self._expansions_by_geyser_position = _expansions_of_each_geyser_position(api.expansions)
        # The workers speed mining has given an attack, with its target, until they have fired.
        self._target_by_attacker: UnitDict[OwnUnit[UnitType.Worker], Unit[Any]] = UnitDict()
        self._trip_end_points_by_field = _trip_end_points_of_each_field(api.expansions)
        # The end points of the trips to the gas building on each geyser, by its position, worked out from the first
        # gas building seen there, since a gas building's radius is not its geyser's: a refinery's is 1.6875 (in game),
        # a geyser's 1.5 (corpus).
        self._trip_end_points_by_geyser_position: dict[Point, tuple[_TripEndPoints, ...]] = {}
        # Where the move of any trip ends, so a move toward a field or gas building a worker was handed before reads as
        # speed mining's. A gas building's are added once worked out.
        self._every_trip_end_point = {
            point
            for every_trip_end_points in self._trip_end_points_by_field.values()
            for trip_end_points in every_trip_end_points
            for point in (trip_end_points.gather_point, trip_end_points.return_point)
        }
        api.on(TurnEvent, priority=EventPriority.LOW)(self._order_the_workers)

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

    def workers_at(self, resource: Unit[UnitType.AnyResource]) -> tuple[OwnUnit[UnitType.Worker], ...]:
        """The workers speed mining holds at `resource`, a mineral field or a gas building, in the order they were
        handed over."""
        if UnitType.AnyMineralField.includes(resource):
            return tuple(worker for worker, field in self._field_by_worker.items() if field is resource)
        return tuple(
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
        nor again to one sent there lately, whose orders may not show it yet. A worker building, burrowed, or out of
        reach of orders (`OwnUnit.is_orderable`), as inside a refinery or a transport, cannot be sent: `mine` returns
        `None` for it and leaves it as it was, to be handed over once it can be.

        Raises `ValueError` for a unit that is not a worker, an SCV, a drone or a probe, and for one of `fields` that is
        not a mineral field.
        """
        if not UnitType.Worker.includes(worker):
            raise ValueError(f"speed mining orders workers, not {worker!r}")
        only_fields = _given(fields, UnitType.AnyMineralField, "speed mining mines mineral fields")
        field = self._nearest_field_with_room(worker, only_fields)
        if field is None or not self._send_to_gather_at(worker, field, mining=True):
            return None
        self._gas_building_by_worker.pop(worker, None)
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
        `gas_buildings`, one or several, if given: at the expansion nearest `worker` with a gas building fewer than
        three other workers collect from, the nearest the expansion's center of those fewer than two others collect
        from, or, where none is, the farthest from it. Where there is none, `collect` returns `None` and leaves
        `worker` as it was, held where it was, if anywhere.

        Handed over, `worker` is sent to the gas building at once, as `mine` sends a worker to its field: a gather at
        it, behind a return of any cargo it carries. Nothing is sent to a worker collecting vespene already, nor again
        to one sent there lately, and `collect` returns `None` for a worker building, burrowed, or out of reach of
        orders, as inside a gas building, and leaves it as it was.

        Raises `ValueError` for a unit that is not a worker, and for one of `gas_buildings` that is not a gas building.
        """
        if not UnitType.Worker.includes(worker):
            raise ValueError(f"speed mining orders workers, not {worker!r}")
        only_gas_buildings = _given(gas_buildings, UnitType.GasBuilding, "speed mining collects from gas buildings")
        gas_building = self._gas_building_with_room_to_pick(worker, only_gas_buildings)
        if gas_building is None:
            return None
        # So that a move to where one of its trips ends reads as speed mining's from the first.
        self._trip_end_points_of_gas_building(gas_building)
        if not self._send_to_gather_at(worker, gas_building, mining=False):
            return None
        self._field_by_worker.pop(worker, None)
        self._gas_building_by_worker[worker] = gas_building
        return gas_building

    def release(self, workers: OwnUnit[UnitType.Worker] | Iterable[OwnUnit[UnitType.Worker]]) -> None:
        """Stop ordering `workers`, one or several, leaving their orders as they are. Nothing happens for a worker not
        handed over."""
        for worker in (workers,) if isinstance(workers, Unit) else workers:
            self._field_by_worker.pop(worker, None)
            self._gas_building_by_worker.pop(worker, None)

    def _send_to_gather_at(
        self, worker: OwnUnit[UnitType.Worker], resource: Unit[UnitType.AnyResource], *, mining: bool
    ) -> bool:
        """Send `worker`, being handed over at `resource`, a field to mine if `mining`, else a gas building to collect
        from, there at once, as `mine` and `collect` say, unless its current order is its work already, and return
        whether it could be: not while it builds, is burrowed or is out of reach of orders."""
        if not worker.is_orderable or worker.is_burrowed or worker.is_constructing:
            return False
        current_order = worker.current_order
        if current_order is not None and self._is_its_work(worker, current_order, mining=mining):
            return True
        order_book = self._api.orders
        if any(
            order.ability is AbilityId.GATHER and order.target is resource
            for order in order_book.issued_to(worker, include_sent=True)
        ):
            return True
        if _carries_cargo(worker):
            self._hand_in_then_gather(worker, resource)
        else:
            order_book.issue(worker, AbilityId.GATHER, target=resource)
        return True

    def _hand_in_then_gather(self, worker: OwnUnit[UnitType.Worker], resource: Unit[UnitType.AnyResource]) -> None:
        """Have `worker` hand in its cargo and then gather at `resource`: a return with the gather queued behind it,
        which waits for the cargo to be handed in, so that a worker carrying vespene does not go back to its geyser (in
        game)."""
        order_book = self._api.orders
        # Forced: NachOS leaves out a return to a worker the game reports returning aimed at nothing, and a gather
        # queued behind the return the worker shows of itself takes its place, the worker carrying its cargo to the
        # field first (in game).
        order_book.issue(worker, AbilityId.RETURN, force=True)
        order_book.issue(worker, AbilityId.GATHER, target=resource, queued=True)

    def _nearest_field_with_room(
        self, worker: OwnUnit[UnitType.Worker], only_fields: frozenset[Unit[UnitType.AnyMineralField]] | None
    ) -> Unit[UnitType.AnyMineralField] | None:
        """The field `mine` picks for `worker`, among `only_fields` unless `None`, as it says, or `None` where there is
        none."""
        miner_counts = Counter(field for miner, field in self._field_by_worker.items() if miner is not worker)
        expansions = sorted(self._api.expansions, key=lambda expansion: worker.distance_to(expansion.center))
        for expansion in expansions:
            if _finished_own_townhall_at_center(expansion) is None:
                continue
            fields = [field for field in expansion.mineral_fields if field.is_alive]
            nearer_half = math.ceil(len(fields) / 2)
            with_room = [
                (index >= nearer_half, worker.distance_to(field), field)
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
        collector_counts = Counter(
            gas_building for collector, gas_building in self._gas_building_by_worker.items() if collector is not worker
        )
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
        expansions = sorted(self._api.expansions, key=lambda expansion: worker.distance_to(expansion.center))
        for expansion in expansions:
            if _finished_own_townhall_at_center(expansion) is None:
                continue
            # Nearest the expansion's center first, as its geysers are.
            at_the_expansion = [
                with_room_by_position[geyser.position]
                for geyser in expansion.geysers
                if geyser.position in with_room_by_position
            ]
            if at_the_expansion:
                return next(
                    (
                        gas_building
                        for gas_building in at_the_expansion
                        if collector_counts[gas_building] < _COLLECTORS_AT_EACH_GAS_BUILDING_BEFORE_A_THIRD
                    ),
                    at_the_expansion[-1],
                )
        return None

    def _order_the_workers(self, _: TurnEvent) -> None:
        """Order each worker held, as the class's docstring says: the miners field by field, then the collectors gas
        building by gas building, once the workers of the fields and gas buildings no longer mined or collected from
        are let go, since they count among the units in a move's way."""
        townhall_by_center: dict[Point, Unit[UnitType.Townhall] | None] = {}
        mined_fields = _still_worked(
            self._field_by_worker,
            lambda field: None if field.is_dead else self._speed_mined_trip_end_points(field, townhall_by_center),
        )
        collected_gas_buildings = _still_worked(
            self._gas_building_by_worker,
            lambda gas_building: self._speed_collected_trip_end_points(gas_building, townhall_by_center),
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
                    units_in_the_way = self._units_in_the_way_of_a_move()
                near = units_near_each_trip_end[trip_end] = _units_within_reach_of(trip_end, units_in_the_way)
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
            return [worker for worker in free_workers if not self._attacks(worker, enemies_to_attack)]

        mine_plainly = self._mine_plainly
        for field, workers, (trip_end_points, townhall) in mined_fields:
            for worker in workers_to_order(workers, mining=True):
                returning = worker.is_carrying_resource
                trip_end = trip_end_points.return_point if returning else trip_end_points.gather_point
                self._speed_trip(
                    worker,
                    field,
                    townhall,
                    trip_end,
                    units_near_the_trip_end,
                    returning=returning,
                    work_plainly=mine_plainly,
                )
        for gas_building, workers, (trip_end_points, townhall) in collected_gas_buildings:
            self._speed_collect(
                gas_building,
                workers,
                workers_to_order(workers, mining=False),
                trip_end_points,
                townhall,
                units_near_the_trip_end,
            )

    def _speed_collected_trip_end_points(
        self, gas_building: Unit[UnitType.GasBuilding], townhall_by_center: dict[Point, Unit[UnitType.Townhall] | None]
    ) -> tuple[_TripEndPoints, Unit[UnitType.Townhall]] | None:
        """The end points of the trips to `gas_building` from the first expansion holding its geyser with a finished
        townhall of this player's at its center, with that townhall, if `gas_building` is alive with vespene left, else
        `None`. Each expansion's townhall is read once a turn, kept in `townhall_by_center`."""
        if gas_building.is_dead or gas_building.vespene_contents == 0:
            return None
        for trip_end_points in self._trip_end_points_of_gas_building(gas_building):
            townhall = _finished_own_townhall_at_center_read_once(trip_end_points.expansion, townhall_by_center)
            if townhall is not None:
                return trip_end_points, townhall
        return None

    def _trip_end_points_of_gas_building(self, gas_building: Unit[UnitType.GasBuilding]) -> tuple[_TripEndPoints, ...]:
        """The end points of the trips to `gas_building` from each expansion holding its geyser, in their order: worked
        out from the first gas building seen on that geyser, and then counted among the end points of every trip."""
        position = gas_building.position
        if (every_trip_end_points := self._trip_end_points_by_geyser_position.get(position)) is None:
            every_trip_end_points = tuple(
                _TripEndPoints.from_gas_building(expansion, gas_building)
                for expansion in self._expansions_by_geyser_position.get(position, ())
            )
            self._trip_end_points_by_geyser_position[position] = every_trip_end_points
            for trip_end_points in every_trip_end_points:
                self._every_trip_end_point.update((trip_end_points.gather_point, trip_end_points.return_point))
        return every_trip_end_points

    def _is_left_alone(self, worker: OwnUnit[UnitType.Worker], *, mining: bool) -> bool:
        """Whether speed mining leaves `worker`, a miner if `mining`, else a collector, alone this turn: not orderable,
        burrowed, given an order lately, or on an order that is not its work."""
        if not worker.is_orderable or worker.is_burrowed or self._api.orders.issued_to(worker, include_sent=True):
            return True
        current_order = worker.current_order
        return current_order is not None and not self._is_its_work(worker, current_order, mining=mining)

    def _is_its_work(self, worker: OwnUnit[UnitType.Worker], order: UnitOrder, *, mining: bool) -> bool:
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
                return order.known_target in self._every_trip_end_point
            case AbilityId.ATTACK:
                return self._is_speed_minings_attack(worker, order)
            case _:
                return False

    def _is_speed_minings_attack(self, worker: OwnUnit[UnitType.Worker], order: UnitOrder) -> bool:
        """Whether `order`, the current one of `worker`, is the attack speed mining gave it."""
        target = self._target_by_attacker.get(worker)
        return target is not None and order.known_target is target

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
        return any(
            order.ability is AbilityId.MOVE and order.target == trip_end
            for order in self._api.orders.issued_to(worker, include_sent=True)
        )

    def _attacks(self, worker: OwnUnit[UnitType.Worker], enemies: Units[Unit[Any]]) -> bool:
        """Whether `worker` attacks this turn: it is on speed mining's attack and has yet to fire, or it is given one on
        the weakest of `enemies` it can hit as it stands. Speed mining forgets an attack once the worker has fired."""
        ready = worker.weapon_cooldown <= _MOST_COOLDOWN_TO_ATTACK
        if (target := self._target_by_attacker.get(worker)) is not None:
            current_order = worker.current_order
            if (
                ready
                and current_order is not None
                and current_order.ability is AbilityId.ATTACK
                and current_order.known_target is target
            ):
                return True
            del self._target_by_attacker[worker]
        if not ready or not enemies:
            return False
        position, reach = worker.position, _MOST_WEAPON_RANGE_OF_A_WORKER + worker.radius
        reachable = [
            enemy
            for enemy in enemies
            if position.distance_to(enemy.position) <= reach + enemy.radius and _can_attack(worker, enemy)
        ]
        if not reachable:
            return False
        target = min(reachable, key=lambda enemy: enemy.life)
        self._api.orders.issue(worker, AbilityId.ATTACK, target=target)
        self._target_by_attacker[worker] = target
        return True

    def _speed_mined_trip_end_points(
        self, field: Unit[UnitType.AnyMineralField], townhall_by_center: dict[Point, Unit[UnitType.Townhall] | None]
    ) -> tuple[_TripEndPoints, Unit[UnitType.Townhall]] | None:
        """The end points of the trips to `field` from the first expansion holding it with a finished townhall of this
        player's at its center, with that townhall, or `None`. Each expansion's townhall is read once a turn, kept in
        `townhall_by_center`."""
        for trip_end_points in self._trip_end_points_by_field.get(field, ()):
            townhall = _finished_own_townhall_at_center_read_once(trip_end_points.expansion, townhall_by_center)
            if townhall is not None:
                return trip_end_points, townhall
        return None

    def _units_in_the_way_of_a_move(self) -> Units[Unit[Any]]:
        """The units that can stand in the way of a worker on a move, as the class's docstring counts them. A worker on
        a gather or a return passes through units (in game), and one on a move does not (stated)."""
        field_by_worker = self._field_by_worker
        gas_building_by_worker = self._gas_building_by_worker
        return Units(
            unit
            for unit in self._api.units
            if (unit.is_own or unit.is_enemy)
            and unit.type_id not in _TYPES_NEVER_IN_THE_WAY
            and not unit.is_flying
            and not unit.is_burrowed
            and unit not in field_by_worker
            and unit not in gas_building_by_worker
        )

    def _collect_plainly(self, worker: OwnUnit[UnitType.Worker], gas_building: Unit[UnitType.GasBuilding]) -> None:
        """Keep `worker` collecting from `gas_building` with a plain gather, or returning its cargo with the gather
        queued behind: one returning is left to it."""
        current_order = worker.current_order
        if _is_returning(current_order) or _is_gathering_at(current_order, gas_building):
            return
        if worker.is_carrying_resource:
            self._hand_in_then_gather(worker, gas_building)
        else:
            self._api.orders.issue(worker, AbilityId.GATHER, target=gas_building)

    def _mine_plainly(self, worker: OwnUnit[UnitType.Worker], field: Unit[UnitType.AnyMineralField]) -> None:
        """Keep `worker` mining `field` with a plain gather, or returning its cargo with the gather queued behind."""
        current_order = worker.current_order
        if worker.is_carrying_resource:
            if current_order is None or current_order.ability is not AbilityId.RETURN:
                self._hand_in_then_gather(worker, field)
        elif not _is_gathering_at(current_order, field):
            self._api.orders.issue(worker, AbilityId.GATHER, target=field)

    def _speed_collect(
        self,
        gas_building: Unit[UnitType.GasBuilding],
        collectors: list[OwnUnit[UnitType.Worker]],
        collectors_to_order: list[OwnUnit[UnitType.Worker]],
        trip_end_points: _TripEndPoints,
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
        going_in_on_the_move = [
            collector
            for collector in collectors
            if collector not in ordered and self._is_on_the_move_to(collector, gather_point)
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
        for collector in coming_out:
            self._speed_trip(
                collector,
                gas_building,
                townhall,
                return_point,
                units_near_the_trip_end,
                returning=True,
                work_plainly=collect_plainly,
                yields=any(_have_yet_to_pass(going_in, collector, gas_building) for going_in in going_in_on_the_move),
            )

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


@final
class _TripEndPoints:
    """Where the moves of the trips between a field or a gas building and an expansion's townhall end, as the game keeps
    a point."""

    __slots__ = ("expansion", "gather_point", "return_point")

    def __init__(self, expansion: Expansion, gather_point: Point) -> None:
        """The end points of the trips between `expansion`'s townhall and a field or a gas building whose trip ends at
        `gather_point`, before the game keeps it: the trip back ends 3.125 from the townhall's center toward it."""
        self.expansion = expansion
        self.gather_point = gather_point.rounded_down(step=POINT_PRECISION)
        """Where the move of a trip to the field or the gas building ends."""
        self.return_point = expansion.center.towards(gather_point, _RETURN_POINT_DISTANCE).rounded_down(
            step=POINT_PRECISION
        )
        """Where the move of a trip back to the townhall ends."""

    @classmethod
    def from_field(cls, expansion: Expansion, field: Unit[UnitType.AnyMineralField]) -> Self:
        """The end points of the trips between `field`, one of `expansion`'s, and its townhall."""
        return cls(expansion, cls._gather_point_of_a_field(field, expansion.mineral_fields, expansion.center))

    @classmethod
    def from_gas_building(cls, expansion: Expansion, gas_building: Unit[UnitType.GasBuilding]) -> Self:
        """The end points of the trips between `gas_building`, on one of `expansion`'s geysers, and its townhall: the
        trip to it ends where a worker's circle touches the gas building's, toward the townhall's center."""
        return cls(expansion, gas_building.position.towards(expansion.center, gas_building.radius + _WORKER_RADIUS))

    @staticmethod
    def _gather_point_of_a_field(
        field: Unit[UnitType.AnyMineralField], fields: Units[Unit[UnitType.AnyMineralField]], townhall_center: Point
    ) -> Point:
        """Where the move of a trip to `field` ends: 1.325 from it toward the townhall, or, where that is nearer another
        of `fields`, where circles of 1.325 around the two cross, the crossing nearer the townhall, for the last such
        field. AvocaDOS's rule: it leaves a point for a field behind two others in the gap between them."""
        position = field.position
        gather_point = position.towards(townhall_center, _GATHER_POINT_DISTANCE)
        field_circle = Circle(position, _GATHER_POINT_DISTANCE)
        for other_field in fields.within(_GATHER_POINT_DISTANCE, of=gather_point):
            if other_field is field:
                continue
            crossings = field_circle.intersections_with(Circle(other_field.position, _GATHER_POINT_DISTANCE))
            if crossings:
                gather_point = townhall_center.closest(crossings)
        return gather_point


def _trip_end_points_of_each_field(
    expansions: Iterable[Expansion],
) -> dict[Unit[UnitType.AnyMineralField], tuple[_TripEndPoints, ...]]:
    """The end points of the trips to each field of `expansions`, from each expansion holding it, in their order."""
    trip_end_points_by_field: dict[Unit[UnitType.AnyMineralField], list[_TripEndPoints]] = {}
    for expansion in expansions:
        for field in expansion.mineral_fields:
            trip_end_points_by_field.setdefault(field, []).append(_TripEndPoints.from_field(expansion, field))
    return {field: tuple(every_trip_end_points) for field, every_trip_end_points in trip_end_points_by_field.items()}


def _given[R: UnitType.AnyType](
    resources: Unit[R] | Iterable[Unit[R]] | None, group: type[R], refusal: str
) -> frozenset[Unit[R]] | None:
    """`resources`, one or several, as a set, or `None` for `None`. Raises `ValueError`, `refusal` first, for one not
    of `group`."""
    if resources is None:
        return None
    given = (resources,) if isinstance(resources, Unit) else tuple(resources)
    for resource in given:
        if not group.includes(resource):
            raise ValueError(f"{refusal}, not {resource!r}")
    return frozenset(given)


def _still_worked[R: Unit[Any], T](
    resource_by_worker: UnitDict[OwnUnit[UnitType.Worker], R], worked_as: Callable[[R], T | None]
) -> list[tuple[R, list[OwnUnit[UnitType.Worker]], T]]:
    """Each value of `resource_by_worker` with its workers, in the order they were handed over, and what `worked_as`
    reads of it, once the workers of each it reads `None` for are let go."""
    workers_by_resource: dict[R, list[OwnUnit[UnitType.Worker]]] = {}
    for worker, resource in resource_by_worker.items():
        workers_by_resource.setdefault(resource, []).append(worker)
    still_worked: list[tuple[R, list[OwnUnit[UnitType.Worker]], T]] = []
    for resource, workers in workers_by_resource.items():
        if (worked := worked_as(resource)) is None:
            for worker in workers:
                del resource_by_worker[worker]
        else:
            still_worked.append((resource, workers, worked))
    return still_worked


def _expansions_of_each_geyser_position(expansions: Iterable[Expansion]) -> dict[Point, tuple[Expansion, ...]]:
    """The expansions of `expansions` holding each geyser, by its position, in their order."""
    expansions_by_geyser_position: dict[Point, list[Expansion]] = {}
    for expansion in expansions:
        for geyser in expansion.geysers:
            expansions_by_geyser_position.setdefault(geyser.position, []).append(expansion)
    return {position: tuple(holders) for position, holders in expansions_by_geyser_position.items()}


def _finished_own_townhall_at_center_read_once(
    expansion: Expansion, townhall_by_center: dict[Point, Unit[UnitType.Townhall] | None]
) -> Unit[UnitType.Townhall] | None:
    """`_finished_own_townhall_at_center` of `expansion`, read once a turn: kept in `townhall_by_center`."""
    center = expansion.center
    if center not in townhall_by_center:
        townhall_by_center[center] = _finished_own_townhall_at_center(expansion)
    return townhall_by_center[center]


def _finished_own_townhall_at_center(expansion: Expansion) -> Unit[UnitType.Townhall] | None:
    """The townhall standing at the center of `expansion` if it is this player's and finished, else `None`. One only a
    morph makes counts as finished as it reads."""
    townhall = expansion.townhall
    if (
        townhall is None
        or not townhall.is_own
        or not (townhall.is_complete or townhall.type_id in _TOWNHALLS_ONLY_A_MORPH_MAKES)
        or townhall.position != expansion.center
    ):
        return None
    return townhall


def _can_attack(worker: OwnUnit[UnitType.Worker], enemy: Unit[Any]) -> bool:
    """Whether `worker` can hit `enemy` as it stands: in range, and no more than a step's turn away."""
    if not worker.has_in_range(enemy):
        return False
    turn = worker.time_to_face(enemy)
    return turn is not None and turn <= _MOST_TURN_TO_ATTACK


def _units_within_reach_of(trip_end: Point, units: Units[Unit[Any]]) -> Units[Unit[Any]]:
    """The units of `units` that can stand in the way of a move to `trip_end` from as far as speed mining gives it."""
    reach = _FARTHEST_DISTANCE_FOR_THE_MOVE + _WORKER_RADIUS + _CLEARANCE_OF_THE_MOVE
    return units.filter(lambda unit: unit.distance_to(trip_end) < reach + unit.radius)


def _is_any_in_the_way(units: Iterable[Unit[Any]], start: Point, end: Point) -> bool:
    """Whether any of `units` stands in the way of a worker moving from `start` to `end`."""
    clearance = _WORKER_RADIUS + _CLEARANCE_OF_THE_MOVE
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


def _have_yet_to_pass(
    going_in: OwnUnit[UnitType.Worker], coming_out: OwnUnit[UnitType.Worker], gas_building: Unit[UnitType.GasBuilding]
) -> bool:
    """Whether `going_in`, a collector on its way into `gas_building`, and `coming_out`, one on its way out, have yet to
    pass each other: the one going in is the farther from the gas building's center, or the two touch."""
    return going_in.distance_to(coming_out) < 2 * _WORKER_RADIUS or going_in.distance_to(
        gas_building
    ) > coming_out.distance_to(gas_building)


def _carries_cargo(worker: OwnUnit[UnitType.Worker]) -> bool:
    """Whether `worker` carries cargo, as its buffs show, or as its return shows: a worker carrying gas from a rich
    geyser wears no buff, and leaves the gas building ordered to return it (in game)."""
    return worker.is_carrying_resource or _is_returning(worker.current_order)
