"""A field made of circles, each adding its value over the ground it covers."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, final

import numpy

from sc2nachos.geometry._circle import Circle
from sc2nachos.geometry._point import Point

if TYPE_CHECKING:
    from numpy import ndarray

    from sc2nachos.geometry._area import Area
    from sc2nachos.geometry._point import PointLike
    from sc2nachos.geometry._shapes import Rectangle


@final
@dataclass(frozen=True, slots=True)
class _CircleSource:
    """A circle added to a field, with the squares its reads compare against."""

    x: float
    y: float
    radius: float
    radius_squared: float
    falloff: float
    full_within_squared: float
    """The square of how far from the center the circle holds its whole value."""
    value: float


@final
class SourceField:
    """A `Field` of floats made of circles, each adding its value over the ground it covers, its edge included, such as
    a threat around each enemy, and 0 beyond every circle. A circle given a `falloff` holds its value out to that
    distance inside its edge, then falls linearly to 0 at the edge; a circle without one holds its value throughout, so
    a mask is circles of 1.0. Where circles overlap, their values add up. The field covers every point, so no read
    raises `IndexError`.

    `min`, `max`, `argmin` and `argmax` read the field at points at most `spacing` apart, along x and along y, over
    the area, and at the area's point nearest the center of each circle that reaches it. So where every circle's value
    is positive, whether `max` reads above 0 is exact, and so is the largest value where one circle reaches the area
    alone. Anything else may be off by as much as the field changes over the spacing, and a value held only on a patch
    narrower than that may be missed.
    """

    __slots__ = ("_circles", "_spacing")

    _circles: list[_CircleSource]
    _spacing: float

    def __init__(self, *, spacing: float = 0.25) -> None:
        """An empty field, whose reads over an area take points at most `spacing` apart. Raises `ValueError` for a
        spacing that is not positive."""
        if spacing <= 0:
            raise ValueError(f"a source field reads points a positive spacing apart, got {spacing}")
        self._circles = []
        self._spacing = spacing

    @property
    def spacing(self) -> float:
        """How far apart, at most, along x and along y, `min`, `max`, `argmin` and `argmax` read the field."""
        return self._spacing

    def add_circle(self, circle: Circle, value: float, *, falloff: float = 0.0) -> None:
        """Add `circle`, holding `value`: throughout, or with a `falloff`, out to that distance inside its edge, falling
        linearly from there to 0 at the edge, so that a falloff of the radius makes a cone. Raises `TypeError` for
        anything but a `Circle`, and `ValueError` for a falloff below 0 or beyond the radius."""
        if not isinstance(circle, Circle):
            raise TypeError(f"a source field adds circles, not a {type(circle).__name__}")
        radius = circle.radius
        if not 0 <= falloff <= radius:
            raise ValueError(f"a falloff lies between 0 and the radius of {circle!r}, got {falloff}")
        x, y = circle.center
        full_within = radius - falloff
        self._circles.append(
            _CircleSource(x, y, radius, radius * radius, falloff, full_within * full_within, float(value))
        )

    # --- Reads

    def value_at(self, point: PointLike) -> float:
        """The value at `point`: the sum of the values of the circles covering it."""
        x = point[0]
        y = point[1]
        total = 0.0
        for circle in self._circles:
            dx = x - circle.x
            dy = y - circle.y
            distance_squared = dx * dx + dy * dy
            if distance_squared <= circle.full_within_squared:
                total += circle.value
            elif distance_squared <= circle.radius_squared:
                total += circle.value * ((circle.radius - math.sqrt(distance_squared)) / circle.falloff)
        return total

    def min(self, *, within: Area) -> float:
        """The smallest value read in `within`, as the class says. Raises `ValueError` for an empty area."""
        return self._extreme_value(within, largest=False)

    def max(self, *, within: Area) -> float:
        """The largest value read in `within`, as the class says. Raises `ValueError` for an empty area."""
        return self._extreme_value(within, largest=True)

    def argmin(self, *, within: Area) -> Point:
        """The point, of those read in `within` as the class says, with the smallest value, the lowest x, then y, where
        several tie: always one `within` holds. Raises `ValueError` for an empty area."""
        return self._extreme_point(within, largest=False)

    def argmax(self, *, within: Area) -> Point:
        """The point, of those read in `within` as the class says, with the largest value, as `argmin` picks one."""
        return self._extreme_point(within, largest=True)

    def _extreme_value(self, area: Area, *, largest: bool) -> float:
        """The largest value read in `area`, or the smallest."""
        bounds = _bounding_rectangle_of_nonempty(area)
        circles = self._circles_near(bounds)
        if not circles:
            return 0.0
        xs, ys = self._points_read(area, bounds, circles)
        values = _values_at(xs, ys, circles)
        return (values.max() if largest else values.min()).item()

    def _extreme_point(self, area: Area, *, largest: bool) -> Point:
        """The point read in `area` with the largest value, or the smallest, the lowest x, then y, where several tie."""
        bounds = _bounding_rectangle_of_nonempty(area)
        circles = self._circles_near(bounds)
        xs, ys = self._points_read(area, bounds, circles)
        values = _values_at(xs, ys, circles)
        tied = numpy.flatnonzero(values == (values.max() if largest else values.min()))
        picked = tied[numpy.lexsort((ys[tied], xs[tied]))[0]]
        return Point((xs[picked].item(), ys[picked].item()))

    def _circles_near(self, bounds: Rectangle) -> list[_CircleSource]:
        """The circles that may reach into `bounds`, an area's bounding rectangle: every one that does, and some that
        only come near."""
        left, bottom = bounds.x, bounds.y
        right, top = bounds.right, bounds.top
        return [
            circle
            for circle in self._circles
            if circle.x - circle.radius <= right
            and circle.x + circle.radius >= left
            and circle.y - circle.radius <= top
            and circle.y + circle.radius >= bottom
        ]

    def _points_read(self, area: Area, bounds: Rectangle, circles: list[_CircleSource]) -> tuple[ndarray, ndarray]:
        """The x and the y of the points read in `area`, whose bounding rectangle is `bounds`: those `spacing` apart,
        by ascending x, then y, then the area's point nearest the center of each of `circles` that reaches it."""
        xs, ys = _points_spaced_over(area, bounds, self._spacing)
        nearest_points: list[Point] = []
        for circle in circles:
            point, distance = area.closest_point_to((circle.x, circle.y))
            if distance <= circle.radius:
                nearest_points.append(point)
        if not nearest_points:
            return xs, ys
        nearest = numpy.array(nearest_points)
        return numpy.concatenate((xs, nearest[:, 0])), numpy.concatenate((ys, nearest[:, 1]))

    def gradient_at(self, point: PointLike) -> tuple[float, float]:
        """How fast the value rises at `point`, per map unit, along x and along y, together pointing the way it rises
        fastest. Exact: a circle slopes over its falloff, beyond where that begins and out to its edge, which slopes
        too, and is flat elsewhere."""
        x = point[0]
        y = point[1]
        slope_x = 0.0
        slope_y = 0.0
        for circle in self._circles:
            dx = x - circle.x
            dy = y - circle.y
            distance_squared = dx * dx + dy * dy
            if circle.full_within_squared < distance_squared <= circle.radius_squared:
                slope_per_offset = -circle.value / (circle.falloff * math.sqrt(distance_squared))
                slope_x += slope_per_offset * dx
                slope_y += slope_per_offset * dy
        return slope_x, slope_y

    def __repr__(self) -> str:
        return f"SourceField({len(self._circles)} circles)"


def _values_at(xs: ndarray, ys: ndarray, circles: list[_CircleSource]) -> ndarray:
    """The sum of the values of `circles` covering each point of `xs` and `ys`, worked out as `value_at` works out
    one."""
    totals = numpy.zeros(len(xs))
    for circle in circles:
        dx = xs - circle.x
        dy = ys - circle.y
        distances_squared = dx * dx + dy * dy
        if circle.falloff:
            falling = circle.value * ((circle.radius - numpy.sqrt(distances_squared)) / circle.falloff)
            values = numpy.where(distances_squared <= circle.radius_squared, falling, 0.0)
            totals += numpy.where(distances_squared <= circle.full_within_squared, circle.value, values)
        else:
            totals += numpy.where(distances_squared <= circle.radius_squared, circle.value, 0.0)
    return totals


def _bounding_rectangle_of_nonempty(area: Area) -> Rectangle:
    """The bounding rectangle of `area`. Raises `ValueError` if the area is empty."""
    if not area.area:
        raise ValueError(f"{area!r} is empty, so it has no smallest or largest value")
    return area.bounding_rectangle()


def _points_spaced_over(area: Area, bounds: Rectangle, spacing: float) -> tuple[ndarray, ndarray]:
    """The x and the y of the centers of the cells, at most `spacing` wide and high, that split `bounds`, the area's
    bounding rectangle, of those in `area`, by ascending x, then y. If none is, the area's point nearest the middle
    of `bounds`."""
    columns = max(1, math.ceil(bounds.width / spacing))
    rows = max(1, math.ceil(bounds.height / spacing))
    xs = bounds.x + (numpy.arange(columns) + 0.5) * (bounds.width / columns)
    ys = bounds.y + (numpy.arange(rows) + 0.5) * (bounds.height / rows)
    column_indices, row_indices = numpy.nonzero(area._contains_each(xs[:, None], ys[None, :]))
    if len(column_indices):
        return xs[column_indices], ys[row_indices]
    point, _ = area.closest_point_to(bounds.center)
    return numpy.array([point[0]]), numpy.array([point[1]])
