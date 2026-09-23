"""The circle: everything within a radius of a point."""

from __future__ import annotations

import math
import random
from typing import TYPE_CHECKING, final

import numpy

from sc2nachos.geometry._area import Area
from sc2nachos.geometry._point import Point, coordinates
from sc2nachos.geometry._shapes import Rectangle, Tile, TileSet

if TYPE_CHECKING:
    from sc2nachos.geometry._point import PointLike


@final
class Circle(Area):
    """Every point within `radius` of a center, boundary included."""

    # Not a dataclass: `Area.center` is a property, and a field cannot override one.
    __slots__ = ("_center", "_radius")

    _center: Point
    _radius: float

    def __init__(self, center: PointLike, radius: float) -> None:
        """A circle of `radius` around `center`."""
        if radius <= 0:
            raise ValueError("a circle must have a positive radius")
        self._center = Point(coordinates(center)[:2])
        self._radius = radius

    @property
    def center(self) -> Point:
        """The point the circle is drawn around."""
        return self._center

    @property
    def radius(self) -> float:
        """The distance from the center to the boundary."""
        return self._radius

    @property
    def area(self) -> float:
        """The ground covered, in square game units."""
        return math.pi * self._radius**2

    @property
    def perimeter(self) -> float:
        """The length of the boundary."""
        return 2 * math.pi * self._radius

    def __contains__(self, point: PointLike) -> bool:
        """Whether `point` lies in the circle, boundary included."""
        position = coordinates(point)
        dx = position[0] - self._center[0]
        dy = position[1] - self._center[1]
        return dx * dx + dy * dy <= self._radius**2

    def translated(self, offset: PointLike) -> Circle:
        """The circle shifted by `offset`, keeping its radius."""
        return Circle(self._center + offset, self._radius)

    def random_point(self) -> Point:
        """A point drawn uniformly from the circle."""
        # sqrt, because a uniform radius would crowd the points toward the center.
        distance = self._radius * math.sqrt(random.random())
        angle = random.uniform(0, 2 * math.pi)
        return Point(
            (
                self._center[0] + distance * math.cos(angle),
                self._center[1] + distance * math.sin(angle),
            )
        )

    def closest_point_to(self, point: PointLike) -> tuple[Point, float]:
        """The point of the circle nearest to `point`, and the distance to it.

        A point inside the circle is its own nearest point, at distance zero.
        """
        position = Point(coordinates(point)[:2])
        offset = position - self._center
        distance = offset.length
        if distance <= self._radius:
            return position, 0.0
        return self._center + offset * (self._radius / distance), distance - self._radius

    def bounding_rectangle(self) -> Rectangle:
        """The smallest rectangle containing the circle."""
        diameter = 2 * self._radius
        return Rectangle(self._center[0] - self._radius, self._center[1] - self._radius, diameter, diameter)

    def tiles(self) -> TileSet:
        """Every tile whose center lies in the circle."""
        # Derived from the circle, not from its bounding rectangle: that rectangle is half-open, so it would
        # drop a tile center sitting exactly on the circle's rightmost or topmost point.
        center_x, center_y = self._center[0], self._center[1]
        first_x = math.ceil(center_x - self._radius - 0.5)
        first_y = math.ceil(center_y - self._radius - 0.5)
        last_x = math.floor(center_x + self._radius - 0.5)
        last_y = math.floor(center_y + self._radius - 0.5)
        offsets_x = numpy.arange(first_x, last_x + 1) + 0.5 - center_x
        offsets_y = numpy.arange(first_y, last_y + 1) + 0.5 - center_y
        inside = offsets_x[:, None] ** 2 + offsets_y[None, :] ** 2 <= self._radius**2
        rows, columns = numpy.nonzero(inside)
        return TileSet(
            Tile(first_x + row, first_y + column) for row, column in zip(rows.tolist(), columns.tolist(), strict=True)
        )

    def intersections_with(self, other: Circle) -> tuple[Point, ...]:
        """The points where the two boundaries cross: two, one where the circles touch, none otherwise.

        Concentric circles, the same circle included, report none.
        """
        offset = other.center - self._center
        distance_squared = offset[0] ** 2 + offset[1] ** 2
        if distance_squared > (self._radius + other.radius) ** 2:
            return ()
        if distance_squared < (self._radius - other.radius) ** 2 or distance_squared == 0:
            return ()
        distance = math.sqrt(distance_squared)
        along = (self._radius**2 - other.radius**2 + distance_squared) / (2 * distance)
        midpoint = self._center + offset * (along / distance)
        across_squared = self._radius**2 - along**2
        if across_squared <= 0:
            return (midpoint,)
        across = math.sqrt(across_squared) / distance
        perpendicular = Point((offset[1] * across, -offset[0] * across))
        return midpoint + perpendicular, midpoint - perpendicular

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Circle):
            return NotImplemented
        return self._center == other.center and self._radius == other.radius

    def __hash__(self) -> int:
        return hash((self._center, self._radius))

    def __repr__(self) -> str:
        return f"Circle({self._center!r}, {self._radius})"
