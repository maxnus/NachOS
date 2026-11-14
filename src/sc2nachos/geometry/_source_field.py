"""A field made of circles and rectangles, each adding its value over the ground it covers."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, NamedTuple, cast, final

import numpy

from sc2nachos.geometry._circle import Circle
from sc2nachos.geometry._point import Point
from sc2nachos.geometry._shapes import Rectangle, Tile

if TYPE_CHECKING:
    from numpy import ndarray

    from sc2nachos.geometry._area import Area
    from sc2nachos.geometry._point import PointLike


class _CircleSource(NamedTuple):
    """A circle added to a field, with the squares its reads compare against."""

    x: float
    y: float
    radius: float
    radius_squared: float
    full_within: float
    full_within_squared: float
    value: float


class _RectangleSource(NamedTuple):
    """A rectangle added to a field, by its edges."""

    left: float
    bottom: float
    right: float
    top: float
    value: float


@final
class SourceField[T: float]:
    """A `Field` made of circles and rectangles, each adding its value over the ground it covers, such as a threat
    around each enemy: 0 beyond every shape, or false in a field of booleans. A circle covers its edge, and a rectangle
    its lower edges but not its upper ones, as `in` has them. A circle may fall off from its center, holding its value
    out to `full_within` and falling linearly to 0 at its radius; a rectangle, and every shape of a field of booleans,
    holds its value throughout. Where shapes overlap, their values add up, and booleans are or-ed. The field covers
    every point, so no read raises `IndexError`.

    `min`, `max`, `argmin` and `argmax` read the field at points at most `spacing` apart, along x and along y, over
    the area, and at the area's point nearest the center of each shape that reaches it, moved by the least a float can
    move where it lies on an edge the area leaves out. So where one circle of a positive value reaches the area alone,
    the largest value is exact, and so is whether a circle of a field of booleans reaches the area. Anything else may be
    off by as much as the field changes over the spacing, and a value held only on a patch narrower than that may be
    missed.
    """

    __slots__ = ("_circles", "_holds_booleans", "_rectangles", "_spacing")

    _circles: list[_CircleSource]
    _holds_booleans: bool
    _rectangles: list[_RectangleSource]
    _spacing: float

    def __init__(self, *, dtype: type[T] = float, spacing: float = 0.25) -> None:
        """An empty field of `dtype`, `float` or `bool`, whose reads over an area take points at most `spacing` apart.
        Raises `TypeError` for any other dtype, and `ValueError` for a spacing that is not positive."""
        if dtype is not float and dtype is not bool:
            raise TypeError(f"a source field holds floats or booleans, not {dtype.__name__}")
        if spacing <= 0:
            raise ValueError(f"a source field reads points a positive spacing apart, got {spacing}")
        self._circles = []
        self._holds_booleans = dtype is bool
        self._rectangles = []
        self._spacing = spacing

    @property
    def spacing(self) -> float:
        """How far apart, at most, along x and along y, `min`, `max`, `argmin` and `argmax` read the field."""
        return self._spacing

    def add(self, shape: Circle | Rectangle, value: T, *, full_within: float | None = None) -> None:
        """Add `shape`, a `Circle` or a `Rectangle`, holding `value`. A circle given `full_within`, a distance from its
        center no greater than its radius, holds `value` out to it and falls linearly to 0 at its radius. Raises
        `TypeError` for any other shape, and `ValueError` for `full_within` on a rectangle, in a field of booleans, or
        beyond the radius."""
        if type(shape) is Circle:
            self._add_circle(shape, value, full_within)
        elif type(shape) is Rectangle:
            if full_within is not None:
                raise ValueError(f"a rectangle holds its value throughout, so {shape!r} takes no full_within")
            if self._holds_booleans and not value:
                return
            self._rectangles.append(_RectangleSource(shape.x, shape.y, shape.right, shape.top, float(value)))
        else:
            hint = "; add a tile's bounding_rectangle()" if type(shape) is Tile else ""
            raise TypeError(f"a source field adds circles and rectangles, not a {type(shape).__name__}{hint}")

    def _add_circle(self, circle: Circle, value: T, full_within: float | None) -> None:
        """Add `circle`, holding `value` out to `full_within`, or throughout if that is `None`."""
        radius = circle.radius
        if full_within is None:
            full_within = radius
        elif self._holds_booleans:
            raise ValueError("a field of booleans holds each shape's value throughout, so it takes no full_within")
        elif not 0 <= full_within <= radius:
            raise ValueError(f"full_within lies between 0 and the radius of {circle!r}, got {full_within}")
        if self._holds_booleans and not value:
            return
        x, y = circle.center
        self._circles.append(
            _CircleSource(x, y, radius, radius * radius, full_within, full_within * full_within, float(value))
        )

    # --- Reads

    def value_at(self, point: PointLike) -> T:
        """The value at `point`: the sum of the values of the shapes covering it, or in a field of booleans whether
        any does."""
        x = point[0]
        y = point[1]
        if self._holds_booleans:
            return cast("T", self._covers(x, y))
        total = 0.0
        for circle_x, circle_y, radius, radius_squared, full_within, full_within_squared, value in self._circles:
            dx = x - circle_x
            dy = y - circle_y
            distance_squared = dx * dx + dy * dy
            if distance_squared <= full_within_squared:
                total += value
            elif distance_squared <= radius_squared:
                total += value * ((radius - math.sqrt(distance_squared)) / (radius - full_within))
        for left, bottom, right, top, value in self._rectangles:
            if left <= x < right and bottom <= y < top:
                total += value
        return cast("T", total)

    def _covers(self, x: float, y: float) -> bool:
        """Whether any shape covers the point at `x` and `y`."""
        for circle_x, circle_y, _, radius_squared, *_ in self._circles:
            dx = x - circle_x
            dy = y - circle_y
            if dx * dx + dy * dy <= radius_squared:
                return True
        return any(left <= x < right and bottom <= y < top for left, bottom, right, top, _ in self._rectangles)

    def min(self, *, within: Area) -> T:
        """The smallest value read in `within`, as the class says. Raises `ValueError` for an empty area."""
        return self._extreme_value(within, largest=False)

    def max(self, *, within: Area) -> T:
        """The largest value read in `within`, as the class says. Raises `ValueError` for an empty area."""
        return self._extreme_value(within, largest=True)

    def argmin(self, *, within: Area) -> Point:
        """The point, of those read in `within` as the class says, with the smallest value, the lowest x, then y, where
        several tie: always one `within` holds. Raises `ValueError` for an empty area."""
        return self._extreme_point(within, largest=False)

    def argmax(self, *, within: Area) -> Point:
        """The point, of those read in `within` as the class says, with the largest value, as `argmin` picks one."""
        return self._extreme_point(within, largest=True)

    def _extreme_value(self, area: Area, *, largest: bool) -> T:
        """The largest value read in `area`, or the smallest."""
        bounds = _bounding_rectangle_of_nonempty(area)
        circles, rectangles = self._shapes_reaching(bounds)
        if not circles and not rectangles:
            return cast("T", False if self._holds_booleans else 0.0)
        xs, ys = self._points_read(area, bounds, circles, rectangles)
        values = self._values_at(xs, ys, circles, rectangles)
        return (values.max() if largest else values.min()).item()

    def _extreme_point(self, area: Area, *, largest: bool) -> Point:
        """The point read in `area` with the largest value, or the smallest, the lowest x, then y, where several tie."""
        bounds = _bounding_rectangle_of_nonempty(area)
        circles, rectangles = self._shapes_reaching(bounds)
        xs, ys = self._points_read(area, bounds, circles, rectangles)
        values = self._values_at(xs, ys, circles, rectangles)
        tied = numpy.flatnonzero(values == (values.max() if largest else values.min()))
        picked = tied[numpy.lexsort((ys[tied], xs[tied]))[0]]
        return Point((xs[picked].item(), ys[picked].item()))

    def _shapes_reaching(self, bounds: Rectangle) -> tuple[list[_CircleSource], list[_RectangleSource]]:
        """The circles and the rectangles that may reach into `bounds`, an area's bounding rectangle: every one that
        does, and some that only come near."""
        left, bottom = bounds.x, bounds.y
        right, top = bounds.right, bounds.top
        circles = [
            circle
            for circle in self._circles
            if circle.x - circle.radius <= right
            and circle.x + circle.radius >= left
            and circle.y - circle.radius <= top
            and circle.y + circle.radius >= bottom
        ]
        rectangles = [
            rectangle
            for rectangle in self._rectangles
            if rectangle.left <= right
            and rectangle.right >= left
            and rectangle.bottom <= top
            and rectangle.top >= bottom
        ]
        return circles, rectangles

    def _points_read(
        self, area: Area, bounds: Rectangle, circles: list[_CircleSource], rectangles: list[_RectangleSource]
    ) -> tuple[ndarray, ndarray]:
        """The x and the y of the points read in `area`, whose bounding rectangle is `bounds`: those `spacing` apart,
        by ascending x, then y, then the area's point nearest the center of each of `circles` that reaches it and of
        each of `rectangles`, as `_held_point_nearest` gives it."""
        xs, ys = _points_spaced_over(area, bounds, self._spacing)
        nearest_points: list[Point] = []
        for circle in circles:
            point = _held_point_nearest((circle.x, circle.y), area, within=circle.radius)
            if point is not None:
                nearest_points.append(point)
        for rectangle in rectangles:
            center = ((rectangle.left + rectangle.right) / 2, (rectangle.bottom + rectangle.top) / 2)
            point = _held_point_nearest(center, area)
            if point is not None:
                nearest_points.append(point)
        if not nearest_points:
            return xs, ys
        nearest = numpy.array(nearest_points)
        return numpy.concatenate((xs, nearest[:, 0])), numpy.concatenate((ys, nearest[:, 1]))

    def _values_at(
        self, xs: ndarray, ys: ndarray, circles: list[_CircleSource], rectangles: list[_RectangleSource]
    ) -> ndarray:
        """The value at each point of `xs` and `ys` of `circles` and `rectangles` alone, worked out as `value_at`
        works out one."""
        if self._holds_booleans:
            return self._covered_at(xs, ys, circles, rectangles)
        totals = numpy.zeros(len(xs))
        for circle_x, circle_y, radius, radius_squared, full_within, full_within_squared, value in circles:
            dx = xs - circle_x
            dy = ys - circle_y
            distances_squared = dx * dx + dy * dy
            if full_within < radius:
                falloff = value * ((radius - numpy.sqrt(distances_squared)) / (radius - full_within))
                values = numpy.where(distances_squared <= radius_squared, falloff, 0.0)
                totals += numpy.where(distances_squared <= full_within_squared, value, values)
            else:
                totals += numpy.where(distances_squared <= radius_squared, value, 0.0)
        for left, bottom, right, top, value in rectangles:
            totals += numpy.where((left <= xs) & (xs < right) & (bottom <= ys) & (ys < top), value, 0.0)
        return totals

    @staticmethod
    def _covered_at(
        xs: ndarray, ys: ndarray, circles: list[_CircleSource], rectangles: list[_RectangleSource]
    ) -> ndarray:
        """Whether any of `circles` and `rectangles` covers each point of `xs` and `ys`."""
        covered = numpy.zeros(len(xs), dtype=bool)
        for circle_x, circle_y, _, radius_squared, *_ in circles:
            dx = xs - circle_x
            dy = ys - circle_y
            covered |= dx * dx + dy * dy <= radius_squared
        for left, bottom, right, top, _ in rectangles:
            covered |= (left <= xs) & (xs < right) & (bottom <= ys) & (ys < top)
        return covered

    def gradient_at(self, point: PointLike) -> tuple[float, float]:
        """How fast the value rises at `point`, per map unit, along x and along y, together pointing the way it rises
        fastest. Exact: a circle slopes from beyond its `full_within` out to its radius, its edge included, and is flat
        elsewhere, as a rectangle is everywhere, its edges included. Raises `TypeError` for a field of booleans."""
        if self._holds_booleans:
            raise TypeError(f"{self!r} holds booleans, which have no gradient")
        x = point[0]
        y = point[1]
        slope_x = 0.0
        slope_y = 0.0
        for circle_x, circle_y, radius, radius_squared, full_within, full_within_squared, value in self._circles:
            dx = x - circle_x
            dy = y - circle_y
            distance_squared = dx * dx + dy * dy
            if full_within_squared < distance_squared <= radius_squared:
                slope_per_offset = -value / ((radius - full_within) * math.sqrt(distance_squared))
                slope_x += slope_per_offset * dx
                slope_y += slope_per_offset * dy
        return slope_x, slope_y

    def __repr__(self) -> str:
        kind = "booleans" if self._holds_booleans else "floats"
        return f"SourceField({kind}, {len(self._circles)} circles, {len(self._rectangles)} rectangles)"


def _bounding_rectangle_of_nonempty(area: Area) -> Rectangle:
    """The bounding rectangle of `area`. Raises `ValueError` if the area is empty."""
    if not area.area:
        raise ValueError(f"{area!r} is empty, so it has no smallest or largest value")
    return area.bounding_rectangle()


def _points_spaced_over(area: Area, bounds: Rectangle, spacing: float) -> tuple[ndarray, ndarray]:
    """The x and the y of the centers of the cells, at most `spacing` wide and high, that split `bounds`, the area's
    bounding rectangle, of those in `area`, by ascending x, then y. If none is, the point `_held_point_nearest` gives
    for the middle of `bounds`, if any."""
    columns = max(1, math.ceil(bounds.width / spacing))
    rows = max(1, math.ceil(bounds.height / spacing))
    xs = bounds.x + (numpy.arange(columns) + 0.5) * (bounds.width / columns)
    ys = bounds.y + (numpy.arange(rows) + 0.5) * (bounds.height / rows)
    column_indices, row_indices = numpy.nonzero(area._contains_each(xs[:, None], ys[None, :]))
    if len(column_indices):
        return xs[column_indices], ys[row_indices]
    point = _held_point_nearest(bounds.center, area)
    if point is None:
        return numpy.empty(0), numpy.empty(0)
    return numpy.array([point[0]]), numpy.array([point[1]])


def _held_point_nearest(point: PointLike, area: Area, *, within: float = math.inf) -> Point | None:
    """The area's point nearest to `point`, if it lies `within` that distance, moved down, left or both by the least a
    float can move where the area leaves out the edge it lies on, so that the area holds it; `None` if none does."""
    nearest, distance = area.closest_point_to(point)
    if distance > within:
        return None
    if nearest in area:
        return nearest
    x, y = nearest
    below_x = math.nextafter(x, -math.inf)
    below_y = math.nextafter(y, -math.inf)
    for moved in ((below_x, y), (x, below_y), (below_x, below_y)):
        if moved in area:
            return Point(moved)
    return None
