"""Speed mining: workers mine minerals faster, each trip ending in a move that keeps their speed into the gather or
return."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, final

from sc2nachos.constants import POINT_PRECISION
from sc2nachos.events import EventPriority, TurnEvent
from sc2nachos.game_time import GameTime
from sc2nachos.geometry import Circle, Point
from sc2nachos.ids import AbilityId
from sc2nachos.units import OwnUnit, Unit, UnitDict, Units, UnitType

if TYPE_CHECKING:
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
# The most weapon cooldown, and the most turn toward an enemy, with which a worker attacks it: the first step of a turn
# costs nothing (in game).
_MOST_COOLDOWN_TO_ATTACK = GameTime(steps=1)
_MOST_TURN_TO_ATTACK = GameTime(steps=1)


@final
class SpeedMining:
    """Speed mining: each worker handed over mines its mineral field, each trip ending in a move with the gather or the
    return queued behind, which keeps the worker's speed into it, where the gather or return alone slows the worker as
    it nears the field or the townhall.

    Build it once the game has started, in the bot's own object an api is given as `extensions`:

    ```python
    from sc2nachos import Api
    from sc2nachos.extensions.speed_mining import SpeedMining


    class Extensions:
        def __init__(self, api: Api) -> None:
            self.speed_mining = SpeedMining(api)


    api = Api(extensions=Extensions)
    ```

    Which worker mines which field stays the bot's: `mine` hands one over, two at a field at most, at a field it picks
    unless given one, `release` takes it back, and `miners` and `miners_at` read them. On each `TurnEvent`, at
    `EventPriority.LOW`, speed mining orders each worker it holds:

    - A worker idle, gathering minerals, returning cargo, or on speed mining's own move, is kept mining its field: one
      gathering at another field, as the game sends a worker whose field is taken, or on its way to a field it was
      handed before, is sent to its own. When it comes within 0.75 to 2 of where its trip ends, it is given the move
      there, with the gather at the field queued behind, or a right-click on the townhall, which the worker shows as a
      return.
    - No move is given while a unit stands in the worker's way, its circle within 0.5 of the line from the worker to
      where its trip ends, and a worker on the move that a unit comes in the way of is given its gather or return back.
      Units of both players count, on the ground and not burrowed, but structures, larvae, and the workers speed mining
      holds.
    - A worker attacks an enemy it can hit as it stands, unless speed mining was built with `attack_enemies=False`:
      its weapon cooldown at most a step, the enemy visible, on the ground, not a structure, in range
      (`Unit.has_in_range`), and no more than a step's turn away (`Unit.time_to_face`); the one with least life, among
      several. It goes back to mining once it has fired.
    - Cargo goes back to the townhall standing at the center of the field's expansion: one of this player's, finished.
      Where there is none, the worker mines with plain gathers and returns. A field two expansions share counts as the
      first's, by `Api.expansions`, that has such a townhall.
    - A worker doing anything else, building, attacking on the bot's order or collecting vespene among them, is left
      alone until it is idle or mining minerals again, and so is a burrowed drone, and one given an order this turn,
      or sent one lately that may not show yet, as `OrderBook.issued_to` reads with `include_sent`. So a miner sent to
      build comes back to its field by itself, and one handed over while it collects vespene stays there until it is
      ordered to gather minerals.
    - A worker is let go once it or its field is dead.

    Raises `NotPlayingError` if built before the game starts.
    """

    __slots__ = (
        "_api",
        "_every_trip_end_point",
        "_field_by_worker",
        "_attack_enemies",
        "_miners",
        "_target_by_attacker",
        "_trip_end_points_by_field",
    )

    def __init__(self, api: Api[object], *, attack_enemies: bool = True) -> None:
        """Speed mining for the workers of `api`'s game, from the next `TurnEvent`, its workers attacking enemies they
        can hit if `attack_enemies`."""
        self._api = api
        self._attack_enemies = attack_enemies
        self._field_by_worker: UnitDict[OwnUnit[Any], Unit[UnitType.AnyMineralField]] = UnitDict()
        self._miners = MappingProxyType(self._field_by_worker)
        # The workers speed mining has given an attack, with its target, until they have fired.
        self._target_by_attacker: UnitDict[OwnUnit[Any], Unit[Any]] = UnitDict()
        self._trip_end_points_by_field = _trip_end_points_of_each_field(api.expansions)
        # Where the move of any trip ends, so a move toward a field a worker was handed before reads as speed mining's.
        self._every_trip_end_point = frozenset(
            point
            for every_trip_end_points in self._trip_end_points_by_field.values()
            for trip_end_points in every_trip_end_points
            for point in (trip_end_points.gather_point, trip_end_points.return_point)
        )
        api.on(TurnEvent, priority=EventPriority.LOW)(self._order_the_miners)

    @property
    def miners(self) -> Mapping[OwnUnit[Any], Unit[UnitType.AnyMineralField]]:
        """Each worker speed mining holds, with the field it mines: a read-only view, so a worker handed over or let go
        shows at once. A dead worker is never among them."""
        return self._miners

    def miners_at(self, field: Unit[UnitType.AnyMineralField]) -> tuple[OwnUnit[Any], ...]:
        """The workers speed mining holds at `field`, in the order they were handed over."""
        return tuple(worker for worker, mined in self._field_by_worker.items() if mined is field)

    def mine(
        self, worker: OwnUnit[Any], field: Unit[UnitType.AnyMineralField] | None = None
    ) -> Unit[UnitType.AnyMineralField]:
        """Have `worker` mine `field` until it is released, or either is dead, and return the field. A worker mining
        another field moves to this one.

        With no `field`, speed mining picks one, among the expansions with a finished townhall of this player's at their
        center: at the one nearest `worker` with a field fewer than two other workers mine, a field of the nearer half
        of its fields before one of the farther half, and then the one nearest `worker`.

        Raises `ValueError` for a unit that is not a worker, an SCV, a drone or a probe, for one that is not a mineral
        field, and for a field two other workers mine already: three at a field mine less speed mined than plain. With
        no `field`, it raises `ValueError` where those expansions have no field with room.
        """
        if not UnitType.Worker.includes(worker):
            raise ValueError(f"speed mining orders workers, not {worker!r}")
        if field is None:
            field = self._nearest_field_with_room(worker)
        elif not UnitType.AnyMineralField.includes(field):
            raise ValueError(f"speed mining mines mineral fields, not {field!r}")
        elif sum(miner is not worker for miner in self.miners_at(field)) >= _MOST_MINERS_AT_A_FIELD:
            raise ValueError(
                f"speed mining mines a field with {_MOST_MINERS_AT_A_FIELD} workers at most, and {field!r} has as many"
            )
        self._field_by_worker[worker] = field
        return field

    def release(self, worker: OwnUnit[Any]) -> None:
        """Stop ordering `worker`, leaving its orders as they are. Nothing happens for a worker not handed over."""
        self._field_by_worker.pop(worker, None)

    def _nearest_field_with_room(self, worker: OwnUnit[Any]) -> Unit[UnitType.AnyMineralField]:
        """The field `mine` picks for `worker` given none, as it says. Raises `ValueError` where there is none."""
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
                if miner_counts[field] < _MOST_MINERS_AT_A_FIELD
            ]
            if with_room:
                return min(with_room, key=lambda candidate: candidate[:2])[2]
        raise ValueError(
            f"speed mining has no field for {worker!r}: no expansion with a finished townhall of this player's has a "
            f"field fewer than {_MOST_MINERS_AT_A_FIELD} workers mine"
        )

    def _order_the_miners(self, _: TurnEvent) -> None:
        """Order each worker held, field by field, as the class's docstring says."""
        workers_by_field: dict[Unit[Any], list[OwnUnit[Any]]] = {}
        for worker, field in self._field_by_worker.items():
            workers_by_field.setdefault(field, []).append(worker)
        townhall_by_center: dict[Point, Unit[Any] | None] = {}
        units_in_the_way: Units[Unit[Any]] | None = None
        enemies_to_attack: Units[Unit[Any]] | None = None
        for field, workers in workers_by_field.items():
            if field.is_dead:
                for worker in workers:
                    del self._field_by_worker[worker]
                continue
            if not (free_workers := [worker for worker in workers if not self._is_left_alone(worker)]):
                continue
            if self._attack_enemies:
                if enemies_to_attack is None:
                    enemies_to_attack = self._api.units.enemy.visible.non_structures.ground
                if not (miners := [worker for worker in free_workers if not self._attacks(worker, enemies_to_attack)]):
                    continue
            else:
                miners = free_workers
            if (speed_mined := self._speed_mined_trip_end_points(field, townhall_by_center)) is None:
                for worker in miners:
                    self._mine_plainly(worker, field)
                continue
            trip_end_points, townhall = speed_mined
            if units_in_the_way is None:
                units_in_the_way = self._units_in_the_way_of_a_move()
            near_the_gather_point = _units_within_reach_of(trip_end_points.gather_point, units_in_the_way)
            near_the_return_point = _units_within_reach_of(trip_end_points.return_point, units_in_the_way)
            for worker in miners:
                if worker.is_carrying_resource:
                    self._speed_mine(worker, field, townhall, trip_end_points.return_point, near_the_return_point)
                else:
                    self._speed_mine(worker, field, townhall, trip_end_points.gather_point, near_the_gather_point)

    def _is_left_alone(self, worker: OwnUnit[Any]) -> bool:
        """Whether speed mining leaves `worker` alone this turn: not orderable, burrowed, given an order lately, or
        doing anything but mining or attacking on speed mining's order."""
        if not worker.is_orderable or worker.is_burrowed or self._api.orders.issued_to(worker, include_sent=True):
            return True
        current_order = worker.current_order
        return current_order is not None and not self._is_mining(worker, current_order)

    def _is_mining(self, worker: OwnUnit[Any], order: UnitOrder) -> bool:
        """Whether `order`, the current one of `worker`, is mining or speed mining's: a gather of a mineral field, a
        return of cargo, a move to where one of speed mining's moves ends, or speed mining's attack."""
        match order.ability:
            case AbilityId.GATHER:
                target = order.known_target
                return isinstance(target, Unit) and UnitType.AnyMineralField.includes(target)
            case AbilityId.RETURN:
                return True
            case AbilityId.MOVE:
                return order.known_target in self._every_trip_end_point
            case AbilityId.ATTACK:
                target = self._target_by_attacker.get(worker)
                return target is not None and order.known_target is target
            case _:
                return False

    def _attacks(self, worker: OwnUnit[Any], enemies: Units[Unit[Any]]) -> bool:
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
        reachable = [enemy for enemy in enemies if _can_attack(worker, enemy)]
        if not reachable:
            return False
        target = min(reachable, key=lambda enemy: enemy.life)
        self._api.orders.issue(worker, AbilityId.ATTACK, target=target)
        self._target_by_attacker[worker] = target
        return True

    def _speed_mined_trip_end_points(
        self, field: Unit[Any], townhall_by_center: dict[Point, Unit[Any] | None]
    ) -> tuple[_TripEndPoints, Unit[Any]] | None:
        """The end points of the trips to `field` from the first expansion holding it with a finished townhall of this
        player's at its center, with that townhall, or `None`. Each expansion's townhall is read once a turn, kept in
        `townhall_by_center`."""
        for trip_end_points in self._trip_end_points_by_field.get(field, ()):
            center = trip_end_points.expansion.center
            if center not in townhall_by_center:
                townhall_by_center[center] = _finished_own_townhall_at_center(trip_end_points.expansion)
            if (townhall := townhall_by_center[center]) is not None:
                return trip_end_points, townhall
        return None

    def _units_in_the_way_of_a_move(self) -> Units[Unit[Any]]:
        """The units that can stand in the way of a worker on a move, as the class's docstring counts them. A worker on
        a gather or a return passes through units (in game), and one on a move does not (stated)."""
        field_by_worker = self._field_by_worker
        return Units(
            unit
            for unit in self._api.units
            if (unit.is_own or unit.is_enemy)
            and unit.type_id not in _TYPES_NEVER_IN_THE_WAY
            and not unit.is_flying
            and not unit.is_burrowed
            and unit not in field_by_worker
        )

    def _mine_plainly(self, worker: OwnUnit[Any], field: Unit[Any]) -> None:
        """Keep `worker` mining `field` with a plain gather, or returning its cargo."""
        order_book = self._api.orders
        current_order = worker.current_order
        if worker.is_carrying_resource:
            if current_order is None or current_order.ability is not AbilityId.RETURN:
                order_book.issue(worker, AbilityId.RETURN)
        elif not _is_gathering_at(current_order, field):
            order_book.issue(worker, AbilityId.GATHER, target=field)

    def _speed_mine(
        self,
        worker: OwnUnit[Any],
        field: Unit[Any],
        townhall: Unit[Any],
        trip_end: Point,
        units_near_the_trip_end: Units[Unit[Any]],
    ) -> None:
        """Keep `worker` mining `field`, with `townhall` taking its cargo: give it the move to `trip_end`, where the
        trip it is on ends, near the end of the trip while none of `units_near_the_trip_end` stands in its way, and take
        it off the move once one does."""
        current_order = worker.current_order
        carrying = worker.is_carrying_resource
        in_the_way = _is_any_in_the_way(units_near_the_trip_end, worker.position, trip_end)
        if current_order is not None and current_order.ability is AbilityId.MOVE:
            # Speed mining's own move, to where this trip or the other ends.
            if current_order.known_target != trip_end or in_the_way:
                self._mine_plainly(worker, field)
                return
            if len(worker.orders) == 2:
                return
            # The gather or return queued behind the move is gone, so the two go out again.
        elif not (_is_returning(current_order) if carrying else _is_gathering_at(current_order, field)):
            self._mine_plainly(worker, field)
            return
        elif in_the_way or not (
            _NEAREST_DISTANCE_FOR_THE_MOVE < worker.distance_to(trip_end) < _FARTHEST_DISTANCE_FOR_THE_MOVE
        ):
            return
        order_book = self._api.orders
        order_book.issue(worker, AbilityId.MOVE, target=trip_end, force=True)
        if carrying:
            # Aimed at the townhall, so the worker goes on into it at speed; a return aimed at nothing stops it at the
            # end of the move first (in game).
            order_book.issue(worker, AbilityId.SMART, target=townhall, queued=True)
        else:
            order_book.issue(worker, AbilityId.GATHER, target=field, queued=True)


@final
class _TripEndPoints:
    """Where the moves of the trips between a field and an expansion's townhall end, as the game keeps a point."""

    __slots__ = ("expansion", "gather_point", "return_point")

    def __init__(self, expansion: Expansion, field: Unit[Any]) -> None:
        center = expansion.center
        gather_point = self._gather_point(field, expansion.mineral_fields, center)
        self.expansion = expansion
        self.gather_point = gather_point.rounded_down(step=POINT_PRECISION)
        """Where the move of a trip to the field ends."""
        self.return_point = center.towards(gather_point, _RETURN_POINT_DISTANCE).rounded_down(step=POINT_PRECISION)
        """Where the move of a trip back to the townhall ends."""

    @staticmethod
    def _gather_point(field: Unit[Any], fields: Units[Unit[Any]], townhall_center: Point) -> Point:
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


def _trip_end_points_of_each_field(expansions: Iterable[Expansion]) -> dict[Unit[Any], tuple[_TripEndPoints, ...]]:
    """The end points of the trips to each field of `expansions`, from each expansion holding it, in their order."""
    trip_end_points_by_field: dict[Unit[Any], list[_TripEndPoints]] = {}
    for expansion in expansions:
        for field in expansion.mineral_fields:
            trip_end_points_by_field.setdefault(field, []).append(_TripEndPoints(expansion, field))
    return {field: tuple(every_trip_end_points) for field, every_trip_end_points in trip_end_points_by_field.items()}


def _finished_own_townhall_at_center(expansion: Expansion) -> Unit[Any] | None:
    """The townhall standing at the center of `expansion` if it is this player's and finished, else `None`."""
    townhall = expansion.townhall
    if townhall is None or not townhall.is_own or not townhall.is_complete or townhall.position != expansion.center:
        return None
    return townhall


def _can_attack(worker: OwnUnit[Any], enemy: Unit[Any]) -> bool:
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


def _is_gathering_at(order: UnitOrder | None, field: Unit[Any]) -> bool:
    """Whether `order` is a gather at `field`."""
    return order is not None and order.ability is AbilityId.GATHER and order.known_target is field


def _is_returning(order: UnitOrder | None) -> bool:
    """Whether `order` is a return of cargo."""
    return order is not None and order.ability is AbilityId.RETURN
