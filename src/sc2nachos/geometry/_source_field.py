"""A field made of circles, each adding its value over the ground it covers."""

from __future__ import annotations

import math
import numbers
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, final

import numpy

from sc2nachos.geometry._edge import Edge
from sc2nachos.geometry._grid import Grid
from sc2nachos.geometry._point import Point, coordinate_arrays_of
from sc2nachos.geometry._shapes import Rectangle, Tile

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Sequence

    from numpy import ndarray

    from sc2nachos.geometry._area import Area
    from sc2nachos.geometry._point import PointLike


@final
@dataclass(frozen=True, slots=True)
class CircleSource:
    """A circle a `SourceField` holds, as `add_circle` took it: `value` held out to `edge_width` inside its edge, and
    falling to 0 at the edge as `edge` says."""

    center: Point
    radius: float
    value: float
    edge: Edge
    edge_width: float


@final
@dataclass(frozen=True, slots=True)
class _StoredCircle:
    """A circle added to a field, as its reads take it: its center as two floats, and the squares they compare
    against."""

    x: float
    y: float
    radius: float
    radius_squared: float
    edge: Edge
    edge_width: float
    full_within_squared: float
    """The square of how far from the center the circle holds its whole value, where its edge begins."""
    value: float

    def to_circle_source(self) -> CircleSource:
        """The circle as `add_circle` took it."""
        return CircleSource(Point((self.x, self.y)), self.radius, self.value, self.edge, self.edge_width)


@final
class SourceField:
    """A `Field` of floats made of circles, each adding its value over the ground it covers, its edge included, such as
    a threat around each enemy, and 0 beyond every circle. A circle with a sharp `Edge`, the default, holds its value
    throughout, so a mask is circles of 1.0; one with a linear or a smooth edge holds its value out to `edge_width`
    inside its edge, then falls to 0 at the edge, evenly or smoothly. Where circles overlap, their values add up. The
    field covers every point, so no read raises `IndexError`.

    `min`, `max`, `argmin` and `argmax` read the field at points at most `spacing` apart, along x and along y, over
    the area, and at the area's point nearest the center of each circle that reaches it. So where every circle's value
    is positive, whether `max` reads above 0 is exact, and so is the largest value where one circle reaches the area
    alone. Anything else may be off by as much as the field changes over the spacing, and a value held only on a patch
    narrower than that may be missed.

    A field is built from `CircleSource`s, or circle by circle with `add_circle`. `len` counts its circles, and
    iterating over it gives each as a `CircleSource`, in the order it was added. Fields add up and subtract, a sum
    holding the circles of both, and a number scales one, each a new field. Like a grid, a field has no truth value.
    """

    __slots__ = ("_circles", "_spacing")

    _circles: list[_StoredCircle]
    _spacing: float

    def __init__(self, sources: Iterable[CircleSource] = (), *, spacing: float = 0.25) -> None:
        """A field holding `sources`, each checked as `add_circle` checks it, whose reads over an area take points at
        most `spacing` apart: `SourceField(other)` holds another field's circles. Raises `ValueError` for a spacing that
        is not positive or a source `add_circle` refuses, and `TypeError` for a source that is not a `CircleSource`."""
        if spacing <= 0:
            raise ValueError(f"a source field reads points a positive spacing apart, got {spacing}")
        self._circles = []
        self._spacing = spacing
        for source in sources:
            if not isinstance(source, CircleSource):
                raise TypeError(f"a source field is built from CircleSources, not a {type(source).__name__}")
            self.add_circle(source.center, source.radius, source.value, edge=source.edge, edge_width=source.edge_width)

    @staticmethod
    def _holding(circles: list[_StoredCircle], *, spacing: float) -> SourceField:
        """A field holding `circles`, checked as they were added to another, and reading at `spacing`."""
        field = SourceField(spacing=spacing)
        field._circles = circles
        return field

    @property
    def spacing(self) -> float:
        """How far apart, at most, along x and along y, `min`, `max`, `argmin` and `argmax` read the field."""
        return self._spacing

    def add_circle(
        self, center: PointLike, radius: float, value: float, *, edge: Edge = Edge.SHARP, edge_width: float = 0.0
    ) -> None:
        """Add a circle of `radius` around `center`, holding `value`: throughout, with a sharp `edge`, or with a linear
        or a smooth one, out to `edge_width` inside its edge, falling from there to 0 at the edge, so that an edge as
        wide as the radius makes a cone, or a smooth one a bump. Raises `ValueError` for a radius not above 0, for a
        width given a sharp edge, and for a linear or smooth edge's width not above 0 or beyond the radius."""
        if not radius > 0:
            raise ValueError(f"a circle's radius lies above 0, got {radius}")
        if edge is Edge.SHARP:
            if edge_width:
                raise ValueError(f"a sharp edge takes no width, got {edge_width}")
        elif not 0 < edge_width <= radius:
            raise ValueError(
                f"a {edge.value} edge's width lies above 0 and within the radius of {radius}, got {edge_width}"
            )
        radius = float(radius)
        full_within = radius - edge_width
        self._circles.append(
            _StoredCircle(
                float(center[0]),
                float(center[1]),
                radius,
                radius * radius,
                edge,
                edge_width,
                full_within * full_within,
                float(value),
            )
        )

    def clear(self) -> None:
        """Remove every circle, keeping the spacing."""
        self._circles.clear()

    def copy(self) -> SourceField:
        """A new field holding the same circles and reading at the same spacing, which a circle added to either leaves
        out of the other."""
        return SourceField._holding(list(self._circles), spacing=self._spacing)

    def __len__(self) -> int:
        """The number of circles added."""
        return len(self._circles)

    def __iter__(self) -> Iterator[CircleSource]:
        """Each circle added, as a `CircleSource`, in the order it was added."""
        for circle in self._circles:
            yield circle.to_circle_source()

    def __bool__(self) -> bool:
        """Raises `TypeError`, as a grid's does: whether a field holds a circle and whether it reads anything differ,
        for circles of 0 or ones that cancel out. `len` says the one, and `max` within an area the other."""
        raise TypeError("a source field has no truth value; len() says whether it holds a circle")

    # --- Arithmetic

    def __add__(self, other: SourceField) -> SourceField:
        """A new field holding this field's circles and then `other`'s, so that its value is the sum of theirs
        everywhere, reading at the finer of their spacings."""
        if not isinstance(other, SourceField):
            return NotImplemented
        return SourceField._holding([*self._circles, *other._circles], spacing=min(self._spacing, other._spacing))

    def __sub__(self, other: SourceField) -> SourceField:
        """A new field holding this field's circles and then `other`'s with their values negated, so that its value is
        the difference of theirs everywhere, reading at the finer of their spacings."""
        if not isinstance(other, SourceField):
            return NotImplemented
        return self + -other

    def __neg__(self) -> SourceField:
        """A new field of the same circles, each with its value negated."""
        return self._with_values(lambda value: -value)

    def __mul__(self, factor: float) -> SourceField:
        """A new field of the same circles, each with its value multiplied by `factor`, a number."""
        if not isinstance(factor, numbers.Real):
            return NotImplemented
        return self._with_values(lambda value: value * factor)

    def __rmul__(self, factor: float) -> SourceField:
        """A new field of the same circles, each with its value multiplied by `factor`, a number."""
        return self.__mul__(factor)

    def __truediv__(self, divisor: float) -> SourceField:
        """A new field of the same circles, each with its value divided by `divisor`, a number. Raises
        `ZeroDivisionError` for 0."""
        if not isinstance(divisor, numbers.Real):
            return NotImplemented
        return self._with_values(lambda value: value / divisor)

    def _with_values(self, new_value_of: Callable[[float], float]) -> SourceField:
        """A new field of the same circles at the same spacing, each with the value `new_value_of` makes of its own."""
        return SourceField._holding(
            [replace(circle, value=float(new_value_of(circle.value))) for circle in self._circles],
            spacing=self._spacing,
        )

    # --- Reads

    def sources_reaching(self, area: Area) -> list[CircleSource]:
        """Each circle reaching `area`, as a `CircleSource`, in the order it was added: those whose values the reads
        over the area take, so not one touching only an edge the area leaves out. An empty area has none."""
        if not area.area:
            return []
        circles = self._circles_near(area.bounding_rectangle())
        return [circle.to_circle_source() for circle, _ in _circles_reaching(area, circles)]

    def values_at(self, points: Sequence[PointLike] | ndarray) -> ndarray:
        """The value at each of `points`, a sequence of points or an array of them, a point a row, as `value_at` reads
        it to the last bit, in a numpy pass a circle: an array of floats, a value a point, in order. It pays off from
        about a hundred points; for a few, `value_at` in a loop is quicker."""
        xs, ys = coordinate_arrays_of(points)
        if not len(xs):
            return numpy.zeros(0)
        left = xs.min().item()
        bottom = ys.min().item()
        bounds = Rectangle(left, bottom, xs.max().item() - left, ys.max().item() - bottom)
        return _values_at(xs, ys, self._circles_near(bounds))

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
                share_to_cross = (circle.radius - math.sqrt(distance_squared)) / circle.edge_width
                share_held = (
                    share_to_cross * share_to_cross * (3.0 - 2.0 * share_to_cross)
                    if circle.edge is Edge.SMOOTH
                    else share_to_cross
                )
                total += circle.value * share_held
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
        _check_not_empty(area)
        if not self._circles:
            return 0.0
        bounds = area.bounding_rectangle()
        circles = self._circles_near(bounds)
        reaching = _circles_reaching(area, circles)
        if not reaching:
            return 0.0
        if len(reaching) == 1 and largest == (reaching[0][0].value > 0):
            # A circle reaching the area alone holds its value farthest from 0 at the area's point nearest its center,
            # which is one of the points read, and the others near reach none of them.
            return self.value_at(reaching[0][1])
        xs, ys = self._points_to_read(area, bounds, [point for _, point in reaching])
        values = _values_at(xs, ys, circles)
        return (values.max() if largest else values.min()).item()

    def _extreme_point(self, area: Area, *, largest: bool) -> Point:
        """The point read in `area` with the largest value, or the smallest, the lowest x, then y, where several tie."""
        _check_not_empty(area)
        bounds = area.bounding_rectangle()
        circles = self._circles_near(bounds)
        xs, ys = self._points_to_read(area, bounds, [point for _, point in _circles_reaching(area, circles)])
        values = _values_at(xs, ys, circles)
        tied = numpy.flatnonzero(values == (values.max() if largest else values.min()))
        picked = tied[numpy.lexsort((ys[tied], xs[tied]))[0]]
        return Point((xs[picked].item(), ys[picked].item()))

    def _circles_near(self, bounds: Rectangle) -> list[_StoredCircle]:
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

    def _points_to_read(self, area: Area, bounds: Rectangle, nearest_points: list[Point]) -> tuple[ndarray, ndarray]:
        """The x and the y of the points to read in `area`, whose bounding rectangle is `bounds`: those `spacing` apart,
        by ascending x, then y, then `nearest_points`, the area's point nearest each reaching circle's center."""
        xs, ys = _points_spaced_over(area, bounds, self._spacing)
        if not nearest_points:
            return xs, ys
        nearest = numpy.array(nearest_points)
        return numpy.concatenate((xs, nearest[:, 0])), numpy.concatenate((ys, nearest[:, 1]))

    def gradient_at(self, point: PointLike) -> tuple[float, float]:
        """How fast the value rises at `point`, per map unit, along x and along y, together pointing the way it rises
        fastest. Exact: a circle slopes across its edge, beyond where that begins and out to the circle's edge, where a
        linear edge slopes too and a smooth one is flat, and is flat elsewhere."""
        x = point[0]
        y = point[1]
        slope_x = 0.0
        slope_y = 0.0
        for circle in self._circles:
            dx = x - circle.x
            dy = y - circle.y
            distance_squared = dx * dx + dy * dy
            if circle.full_within_squared < distance_squared <= circle.radius_squared:
                distance = math.sqrt(distance_squared)
                slope_per_offset = -circle.value / (circle.edge_width * distance)
                if circle.edge is Edge.SMOOTH:
                    share_to_cross = (circle.radius - distance) / circle.edge_width
                    slope_per_offset *= 6.0 * share_to_cross * (1.0 - share_to_cross)
                slope_x += slope_per_offset * dx
                slope_y += slope_per_offset * dy
        return slope_x, slope_y

    def to_grid(self, rectangle: Rectangle, *, outside: float | None = None) -> Grid[float]:
        """A grid over the tiles `rectangle` covers, as `Grid.cropped` takes them, each holding the field's value at
        its center, as `value_at` reads it, so that paths and a grid's arithmetic can take the field. A circle that
        covers no tile's center, as a small one may, leaves no mark on the grid. `outside` is what the grid reads
        beyond its edge, as `Grid` takes it."""
        x_range, y_range = rectangle.tile_range()
        values = numpy.zeros((len(x_range), len(y_range)))
        for circle in self._circles:
            # The tiles the circle's bounding box overlaps, of those the grid holds.
            left = max(x_range.start, math.floor(circle.x - circle.radius))
            right = min(x_range.stop, math.floor(circle.x + circle.radius) + 1)
            bottom = max(y_range.start, math.floor(circle.y - circle.radius))
            top = min(y_range.stop, math.floor(circle.y + circle.radius) + 1)
            if left >= right or bottom >= top:
                continue
            dx = (numpy.arange(left, right) + 0.5) - circle.x
            dy = (numpy.arange(bottom, top) + 0.5) - circle.y
            block = values[left - x_range.start : right - x_range.start, bottom - y_range.start : top - y_range.start]
            block += _circle_values(circle, (dx * dx)[:, None] + (dy * dy)[None, :])
        return Grid(values, origin=Tile(x_range.start, y_range.start), outside=outside)

    def __repr__(self) -> str:
        return f"SourceField({len(self._circles)} circles)"


def _values_at(xs: ndarray, ys: ndarray, circles: list[_StoredCircle]) -> ndarray:
    """The sum of the values of `circles` covering each point of `xs` and `ys`, worked out as `value_at` works out
    one."""
    totals = numpy.zeros(len(xs))
    for circle in circles:
        dx = xs - circle.x
        dy = ys - circle.y
        totals += _circle_values(circle, dx * dx + dy * dy)
    return totals


def _circle_values(circle: _StoredCircle, distances_squared: ndarray) -> ndarray:
    """The value `circle` holds at each of `distances_squared` from its center, worked out as `value_at` works out
    one, to the last bit."""
    if not circle.edge_width:
        return numpy.where(distances_squared <= circle.radius_squared, circle.value, 0.0)
    shares_to_cross = (circle.radius - numpy.sqrt(distances_squared)) / circle.edge_width
    shares_held = (
        shares_to_cross * shares_to_cross * (3.0 - 2.0 * shares_to_cross)
        if circle.edge is Edge.SMOOTH
        else shares_to_cross
    )
    falling = numpy.where(distances_squared <= circle.radius_squared, circle.value * shares_held, 0.0)
    return numpy.where(distances_squared <= circle.full_within_squared, circle.value, falling)


def _circles_reaching(area: Area, circles: list[_StoredCircle]) -> list[tuple[_StoredCircle, Point]]:
    """Each of `circles` that reaches `area`, with the area's point nearest its center."""
    reaching = []
    for circle in circles:
        point, distance = area.closest_point_to((circle.x, circle.y))
        if distance <= circle.radius:
            reaching.append((circle, point))
    return reaching


def _check_not_empty(area: Area) -> None:
    """Raise `ValueError` if `area` is empty, so that it has no smallest or largest value."""
    if not area.area:
        raise ValueError(f"{area!r} is empty, so it has no smallest or largest value")


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
