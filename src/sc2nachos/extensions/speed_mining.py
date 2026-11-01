"""Speed mining: workers mine minerals faster, each trip ending in a move that keeps their speed into the gather or
return."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Any, final

from sc2nachos.constants import POINT_PRECISION
from sc2nachos.events import EventPriority, TurnEvent
from sc2nachos.geometry import Circle, Point, Rectangle
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
# The types of this player's units that leave a mineral line clear to speed mine: structures, workers, larvae and eggs.
_TYPES_CLEAR_OF_THE_MINERAL_LINE = (
    UnitType.Structure.type_ids | UnitType.Worker.type_ids | UnitType.Larva.type_ids | UnitType.Egg.type_ids
)


@final
class SpeedMining:
    """Speed mining: each worker handed over mines its mineral field, each trip ending in a move with the gather or the
    return queued behind, which keeps the worker's speed into it, where the gather or return alone slows the worker as
    it nears the field or the townhall. Twelve SCVs on the eight fields of Pylon's main mine some 12% more (in game).

    Build it once the game has started, as the function an api is given as `extensions` does:

    ```python
    from sc2nachos import Api
    from sc2nachos.extensions.speed_mining import SpeedMining

    api = Api(extensions=SpeedMining)
    ```

    Which worker mines which field stays the bot's: `mine` hands one over, and `release` takes it back. On each
    `TurnEvent`, at `EventPriority.LOW`, speed mining orders each worker it holds:

    - A worker idle, gathering minerals, returning cargo, or on speed mining's own move, is kept mining its field: one
      gathering at another field, as the game sends a worker whose field is taken, is sent back. When it comes within
      0.75 to 2 of where its trip ends, it is given the move there, with the gather at the field queued behind, or a
      right-click on the townhall, which the worker shows as a return.
    - Cargo goes back to the townhall standing at the center of the field's expansion: one of this player's, finished.
      Where there is none, or where this player's ground units other than workers, larvae and eggs stand in the
      expansion's mineral line, the worker mines with plain gathers and returns. A field two expansions share counts
      as the first's, by `Api.expansions`, that has such a townhall.
    - A worker doing anything else, building, attacking or collecting vespene among them, is left alone until it is
      idle or mining minerals again, and so is one given an order this turn, or sent one lately that may not show yet,
      as `OrderBook.issued_to` reads with `include_sent`. So a miner sent to build comes back to its field by itself,
      and one handed over while it collects vespene stays there until it is ordered to gather minerals.
    - A worker is let go once it or its field is dead.

    Raises `NotPlayingError` if built before the game starts.
    """

    __slots__ = ("_api", "_field_of_worker", "_mineral_lines_of_field")

    def __init__(self, api: Api[object]) -> None:
        """Speed mining for the workers of `api`'s game, from the next `TurnEvent`."""
        self._api = api
        self._field_of_worker: UnitDict[OwnUnit[Any], Unit[UnitType.AnyMineralField]] = UnitDict()
        self._mineral_lines_of_field = _mineral_lines_by_field(api.expansions)
        api.on(TurnEvent, priority=EventPriority.LOW)(self._order_the_miners)

    def mine(self, worker: OwnUnit[Any], field: Unit[UnitType.AnyMineralField]) -> None:
        """Have `worker` mine `field` until it is released, or either is dead. A worker mining another field moves to
        this one.

        Raises `ValueError` for a unit that is not a worker, an SCV, a drone or a probe, and for one that is not a
        mineral field.
        """
        if not UnitType.Worker.includes(worker):
            raise ValueError(f"speed mining orders workers, not {worker!r}")
        if not UnitType.AnyMineralField.includes(field):
            raise ValueError(f"speed mining mines mineral fields, not {field!r}")
        self._field_of_worker[worker] = field

    def release(self, worker: OwnUnit[Any]) -> None:
        """Stop ordering `worker`, leaving its orders as they are. Nothing happens for a worker not handed over."""
        self._field_of_worker.pop(worker, None)

    def _order_the_miners(self, _: TurnEvent) -> None:
        """Order each worker held, as the class's docstring says."""
        api = self._api
        order_book = api.orders
        turn = _MiningTurn(api)
        mined_out: list[OwnUnit[Any]] = []
        for worker, field in self._field_of_worker.items():
            if field.is_dead:
                mined_out.append(worker)
                continue
            if not worker.is_orderable or order_book.issued_to(worker, include_sent=True):
                continue
            mineral_lines = self._mineral_lines_of_field.get(field, ())
            if (current_order := worker.current_order) is not None and not _is_mining(
                current_order, field, mineral_lines
            ):
                continue
            speed_mined = turn.speed_mined_line(mineral_lines)
            if speed_mined is None:
                self._mine_plainly(worker, field, current_order)
            else:
                self._speed_mine(worker, field, current_order, *speed_mined)
        for worker in mined_out:
            del self._field_of_worker[worker]

    def _mine_plainly(self, worker: OwnUnit[Any], field: Unit[Any], current_order: UnitOrder | None) -> None:
        """Keep `worker` mining `field` with a plain gather, or returning its cargo."""
        order_book = self._api.orders
        if worker.is_carrying_resource:
            if current_order is None or current_order.ability is not AbilityId.RETURN:
                order_book.issue(worker, AbilityId.RETURN)
        elif not _is_gathering_at(current_order, field):
            order_book.issue(worker, AbilityId.GATHER, target=field)

    def _speed_mine(
        self,
        worker: OwnUnit[Any],
        field: Unit[Any],
        current_order: UnitOrder | None,
        mineral_line: _MineralLine,
        townhall: Unit[Any],
    ) -> None:
        """Keep `worker` mining `field` from `mineral_line`, whose townhall is `townhall`, giving it the move near the
        end of each trip."""
        order_book = self._api.orders
        carrying = worker.is_carrying_resource
        if carrying:
            trip_end = mineral_line.return_points[field]
            on_its_trip = current_order is not None and current_order.ability is AbilityId.RETURN
        else:
            trip_end = mineral_line.gather_points[field]
            on_its_trip = _is_gathering_at(current_order, field)
        if current_order is not None and current_order.ability is AbilityId.MOVE:
            # Speed mining's own move, to where this trip or the other ends.
            if current_order.known_target != trip_end:
                self._mine_plainly(worker, field, current_order)
                return
            if len(worker.orders) == 2:
                return
            # The gather or return queued behind the move is gone, so the two go out again.
        elif not on_its_trip:
            self._mine_plainly(worker, field, current_order)
            return
        elif not _NEAREST_DISTANCE_FOR_THE_MOVE < worker.distance_to(trip_end) < _FARTHEST_DISTANCE_FOR_THE_MOVE:
            return
        order_book.issue(worker, AbilityId.MOVE, target=trip_end, force=True)
        if carrying:
            # Aimed at the townhall, so the worker goes on into it at speed; a return aimed at nothing stops it at the
            # end of the move first (in game).
            order_book.issue(worker, AbilityId.SMART, target=townhall, queued=True)
        else:
            order_book.issue(worker, AbilityId.GATHER, target=field, queued=True)


@final
class _MineralLine:
    """An expansion's mineral line as speed mining works it: where the move of each trip ends, and the ground this
    player's units must leave clear."""

    __slots__ = ("area", "expansion", "gather_points", "return_points")

    def __init__(self, expansion: Expansion) -> None:
        center = expansion.center
        fields = expansion.mineral_fields
        self.expansion = expansion
        gather_points = {field: _gather_point(field, fields, center) for field in fields}
        self.gather_points = {field: point.rounded_down(step=POINT_PRECISION) for field, point in gather_points.items()}
        """Where the move of a trip to each field ends, as the game keeps a point."""
        self.return_points = {
            field: center.towards(point, _RETURN_POINT_DISTANCE).rounded_down(step=POINT_PRECISION)
            for field, point in gather_points.items()
        }
        """Where the move of a trip from each field back to the townhall ends, as the game keeps a point."""
        self.area = _bounding_rectangle([center, *(field.position for field in fields)])
        """The ground between the townhall's center and the fields."""

    def trip_ends(self, field: Unit[Any]) -> tuple[Point, Point]:
        """Where the moves of the trips to `field` and back end."""
        return self.gather_points[field], self.return_points[field]


@final
class _MiningTurn:
    """What speed mining reads once a turn: which mineral lines it speed mines from, each read when first asked."""

    __slots__ = ("_api", "_blocker_positions", "_townhall_of_line")

    def __init__(self, api: Api[object]) -> None:
        self._api = api
        self._blocker_positions: list[Point] | None = None
        self._townhall_of_line: dict[_MineralLine, Unit[Any] | None] = {}

    def speed_mined_line(self, mineral_lines: Iterable[_MineralLine]) -> tuple[_MineralLine, Unit[Any]] | None:
        """The first of `mineral_lines` with a finished townhall of this player's at its center and none of this
        player's ground units other than workers, larvae and eggs in it, with that townhall; or `None`."""
        townhall_of_line = self._townhall_of_line
        for mineral_line in mineral_lines:
            if mineral_line not in townhall_of_line:
                townhall_of_line[mineral_line] = self._speed_mined_townhall(mineral_line)
            if (townhall := townhall_of_line[mineral_line]) is not None:
                return mineral_line, townhall
        return None

    def _speed_mined_townhall(self, mineral_line: _MineralLine) -> Unit[Any] | None:
        """The finished townhall of this player's standing at the center of `mineral_line`, if there is one and none of
        this player's ground units other than workers, larvae and eggs stand in it; else `None`."""
        expansion = mineral_line.expansion
        townhall = expansion.townhall
        if townhall is None or not townhall.is_own or not townhall.is_complete or townhall.position != expansion.center:
            return None
        if (blocker_positions := self._blocker_positions) is None:
            # One pass over the units rather than a collection built for each filter.
            blocker_positions = self._blocker_positions = [
                unit.position
                for unit in self._api.units
                if unit.is_own and unit.type_id not in _TYPES_CLEAR_OF_THE_MINERAL_LINE and not unit.is_flying
            ]
        area = mineral_line.area
        return None if any(position in area for position in blocker_positions) else townhall


def _is_mining(order: UnitOrder, field: Unit[Any], mineral_lines: Iterable[_MineralLine]) -> bool:
    """Whether `order`, a worker's current one, is mining: a gather of a mineral field, a return of cargo, or a move to
    where a trip to `field` or back ends."""
    match order.ability:
        case AbilityId.GATHER:
            target = order.known_target
            return isinstance(target, Unit) and UnitType.AnyMineralField.includes(target)
        case AbilityId.RETURN:
            return True
        case AbilityId.MOVE:
            target = order.known_target
            return any(target in mineral_line.trip_ends(field) for mineral_line in mineral_lines)
        case _:
            return False


def _is_gathering_at(order: UnitOrder | None, field: Unit[Any]) -> bool:
    """Whether `order` is a gather at `field`."""
    return order is not None and order.ability is AbilityId.GATHER and order.known_target is field


def _gather_point(field: Unit[Any], fields: Units[Unit[Any]], townhall_center: Point) -> Point:
    """Where the move of a trip to `field` ends: 1.325 from it toward the townhall, or, where that is nearer another of
    `fields`, where circles of 1.325 around the two cross, the crossing nearer the townhall, for the last such field.
    AvocaDOS's rule: it leaves a point for a field behind two others in the gap between them."""
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


def _bounding_rectangle(points: Sequence[Point]) -> Rectangle:
    """The smallest rectangle holding `points`."""
    left = min(point.x for point in points)
    bottom = min(point.y for point in points)
    return Rectangle(left, bottom, max(point.x for point in points) - left, max(point.y for point in points) - bottom)


def _mineral_lines_by_field(expansions: Iterable[Expansion]) -> dict[Unit[Any], tuple[_MineralLine, ...]]:
    """The mineral lines of `expansions` each field is mined from, in the order of `expansions`."""
    lines_of_field: dict[Unit[Any], list[_MineralLine]] = {}
    for expansion in expansions:
        mineral_line = _MineralLine(expansion)
        for field in expansion.mineral_fields:
            lines_of_field.setdefault(field, []).append(mineral_line)
    return {field: tuple(mineral_lines) for field, mineral_lines in lines_of_field.items()}
