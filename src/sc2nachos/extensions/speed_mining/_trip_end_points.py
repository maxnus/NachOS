"""Where speed mining's moves end: the end points of the trips between each field or gas building and the townhall of
an expansion holding it, and the townhall that takes a worker's cargo."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

from sc2nachos.geometry import POINT_PRECISION, Circle, Point
from sc2nachos.ids import UnitTypeId
from sc2nachos.units import Unit, Units, UnitType

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sc2nachos.gamemap import Expansion

# How far from a field's center the move of a trip to it ends, toward the townhall: AvocaDOS's value, inside the 1.5
# at which a worker's circle touches a field's (corpus).
_GATHER_POINT_DISTANCE = 1.325
# How far from a townhall's center the move of a trip back to it ends: its radius and a worker's, 2.75 and 0.375
# (corpus).
_RETURN_POINT_DISTANCE = 3.125
# An SCV's, a drone's and a probe's radius (corpus).
WORKER_RADIUS = 0.375
# The townhalls only a morph makes, of a finished command center or hatchery: one that reads unfinished is finishing its
# morph, as an orbital reads at build progress 0.998 the step before it does (in game).
_TOWNHALLS_ONLY_A_MORPH_MAKES = frozenset(
    {UnitTypeId.ORBITAL_COMMAND, UnitTypeId.PLANETARY_FORTRESS, UnitTypeId.LAIR, UnitTypeId.HIVE}
)


@final
class TripEndPoints:
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
        return cls(expansion, gas_building.position.towards(expansion.center, gas_building.radius + WORKER_RADIUS))

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


@final
class TripEndPointBook:
    """The end points of every trip speed mining gives a move: for each field and each gas building, from each
    expansion holding it, in `Api.expansions`' order. A gas building's are worked out from the first seen on its
    geyser, since a gas building's radius is not its geyser's: a refinery's is 1.6875 (in game), a geyser's 1.5
    (corpus)."""

    __slots__ = ("_by_field", "_by_geyser_position", "_every_trip_end_point", "_expansions_by_geyser_position")

    def __init__(self, expansions: Iterable[Expansion]) -> None:
        """The book of the trips at `expansions`, its gas buildings' to be worked out."""
        expansions = tuple(expansions)
        self._by_field = _trip_end_points_of_each_field(expansions)
        self._by_geyser_position: dict[Point, tuple[TripEndPoints, ...]] = {}
        # Where a gas building on a geyser stands (in game).
        self._expansions_by_geyser_position = _expansions_of_each_geyser_position(expansions)
        self._every_trip_end_point = {
            point
            for every_trip_end_points in self._by_field.values()
            for trip_end_points in every_trip_end_points
            for point in (trip_end_points.gather_point, trip_end_points.return_point)
        }

    def __contains__(self, point: object) -> bool:
        """Whether `point` is where one of the trips' moves ends: of a field, or of a gas building worked out."""
        return point in self._every_trip_end_point

    def of_field(
        self, field: Unit[UnitType.AnyMineralField], townhall_by_center: dict[Point, Unit[UnitType.Townhall] | None]
    ) -> tuple[TripEndPoints, Unit[UnitType.Townhall]] | None:
        """The end points of the trips to `field` from the first expansion holding it with a finished townhall of this
        player's at its center, with that townhall, or `None`. Each expansion's townhall is read once a turn, kept in
        `townhall_by_center`."""
        return _first_with_a_finished_townhall(self._by_field.get(field, ()), townhall_by_center)

    def of_gas_building(
        self, gas_building: Unit[UnitType.GasBuilding], townhall_by_center: dict[Point, Unit[UnitType.Townhall] | None]
    ) -> tuple[TripEndPoints, Unit[UnitType.Townhall]] | None:
        """The end points of the trips to `gas_building` as `of_field` gives a field's, worked out if they were not."""
        return _first_with_a_finished_townhall(self.work_out_gas_building(gas_building), townhall_by_center)

    def work_out_gas_building(self, gas_building: Unit[UnitType.GasBuilding]) -> tuple[TripEndPoints, ...]:
        """The end points of the trips to `gas_building` from each expansion holding its geyser, in their order: worked
        out from the first gas building seen on that geyser, and then counted among the end points of every trip."""
        position = gas_building.position
        if (every_trip_end_points := self._by_geyser_position.get(position)) is None:
            every_trip_end_points = tuple(
                TripEndPoints.from_gas_building(expansion, gas_building)
                for expansion in self._expansions_by_geyser_position.get(position, ())
            )
            self._by_geyser_position[position] = every_trip_end_points
            for trip_end_points in every_trip_end_points:
                self._every_trip_end_point.update((trip_end_points.gather_point, trip_end_points.return_point))
        return every_trip_end_points


def finished_own_townhall_at_center(expansion: Expansion) -> Unit[UnitType.Townhall] | None:
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


def _first_with_a_finished_townhall(
    every_trip_end_points: Iterable[TripEndPoints], townhall_by_center: dict[Point, Unit[UnitType.Townhall] | None]
) -> tuple[TripEndPoints, Unit[UnitType.Townhall]] | None:
    """The first of `every_trip_end_points` whose expansion has a finished townhall of this player's at its center,
    with that townhall, or `None`. Each expansion's townhall is read once a turn, kept in `townhall_by_center`."""
    for trip_end_points in every_trip_end_points:
        center = trip_end_points.expansion.center
        if center not in townhall_by_center:
            townhall_by_center[center] = finished_own_townhall_at_center(trip_end_points.expansion)
        if (townhall := townhall_by_center[center]) is not None:
            return trip_end_points, townhall
    return None


def _trip_end_points_of_each_field(
    expansions: Iterable[Expansion],
) -> dict[Unit[UnitType.AnyMineralField], tuple[TripEndPoints, ...]]:
    """The end points of the trips to each field of `expansions`, from each expansion holding it, in their order."""
    trip_end_points_by_field: dict[Unit[UnitType.AnyMineralField], list[TripEndPoints]] = {}
    for expansion in expansions:
        for field in expansion.mineral_fields:
            trip_end_points_by_field.setdefault(field, []).append(TripEndPoints.from_field(expansion, field))
    return {field: tuple(every_trip_end_points) for field, every_trip_end_points in trip_end_points_by_field.items()}


def _expansions_of_each_geyser_position(expansions: Iterable[Expansion]) -> dict[Point, tuple[Expansion, ...]]:
    """The expansions of `expansions` holding each geyser, by its position, in their order."""
    expansions_by_geyser_position: dict[Point, list[Expansion]] = {}
    for expansion in expansions:
        for geyser in expansion.geysers:
            expansions_by_geyser_position.setdefault(geyser.position, []).append(expansion)
    return {position: tuple(holders) for position, holders in expansions_by_geyser_position.items()}
