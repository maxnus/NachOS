"""Where speed mining's moves end: the end points of the trips between a field or a gas building and the townhall of
an expansion holding it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

from sc2nachos.geometry import POINT_PRECISION, Circle, Point
from sc2nachos.units import Unit, Units, UnitType

if TYPE_CHECKING:
    from sc2nachos.gamemap import Expansion

# How far from a field's center the move of a trip to it ends, toward the townhall: AvocaDOS's value, inside the 1.5
# at which a worker's circle touches a field's (corpus).
_GATHER_POINT_DISTANCE = 1.325
# How far from a townhall's center the move of a trip back to it ends: its radius and a worker's, 2.75 and 0.375
# (corpus).
_RETURN_POINT_DISTANCE = 3.125
# An SCV's, a drone's and a probe's radius (corpus).
WORKER_RADIUS = 0.375


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
