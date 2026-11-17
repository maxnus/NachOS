"""A field made of circles, each adding its value over the ground it covers."""

from __future__ import annotations

import math
import numbers
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import TYPE_CHECKING, final

import numpy

from sc2nachos.geometry._circle import Circle
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
    """A circle a `SourceField` holds: `value` held throughout `circle` with a sharp `edge`; with a linear or a smooth
    one, held out to half of `edge_width` inside the circle's edge, falling across the band of that width centered on
    the edge, to half the value on it and 0 half the width beyond it, as `Edge` says. So the field reaches past `circle`
    by half the width. It is checked as it is made: raises `TypeError` for a `circle` that is not a `Circle`, and
    `ValueError` for a radius not above 0, a width given a sharp edge, and a linear or smooth edge's width not above 0.
    Being frozen, a changed one is made with `dataclasses.replace`, and checked again."""

    circle: Circle
    value: float
    edge: Edge = Edge.SHARP
    edge_width: float = 0.0
    _x: float = dataclass_field(init=False, repr=False, compare=False)
    _y: float = dataclass_field(init=False, repr=False, compare=False)
    _reach: float = dataclass_field(init=False, repr=False, compare=False)
    """How far from the center the circle's value reaches: the radius and half the edge's width."""
    _reach_squared: float = dataclass_field(init=False, repr=False, compare=False)
    _full_within_squared: float = dataclass_field(init=False, repr=False, compare=False)
    """The square of how far from the center the circle holds its whole value, where its edge begins, or -1 where the
    edge is wider than the circle and no point holds it."""

    def __post_init__(self) -> None:
        """Check the circle and its edge, and keep what the reads compare as floats of their own."""
        if not isinstance(self.circle, Circle):
            raise TypeError(f"a circle source holds a Circle, not a {type(self.circle).__name__}")
        radius = float(self.circle.radius)
        if not radius > 0:
            raise ValueError(f"a circle's radius lies above 0, got {radius}")
        if self.edge is Edge.SHARP:
            if self.edge_width:
                raise ValueError(f"a sharp edge takes no width, got {self.edge_width}")
        elif not self.edge_width > 0:
            raise ValueError(f"a {self.edge.value} edge's width lies above 0, got {self.edge_width}")
        half_width = self.edge_width / 2.0
        reach = radius + half_width
        full_within = radius - half_width
        center = self.circle.center
        set_field = object.__setattr__
        set_field(self, "value", float(self.value))
        set_field(self, "edge_width", float(self.edge_width))
        set_field(self, "_x", float(center[0]))
        set_field(self, "_y", float(center[1]))
        set_field(self, "_reach", reach)
        set_field(self, "_reach_squared", reach * reach)
        set_field(self, "_full_within_squared", full_within * full_within if full_within >= 0 else -1.0)

    def _with_value(self, value: float) -> CircleSource:
        """The circle holding `value` in place of its own, made as `dataclasses.replace` would make it, at about half
        the cost."""
        return CircleSource(self.circle, value, self.edge, self.edge_width)

    def _values_at_distances_squared(self, distances_squared: ndarray) -> ndarray:
        """The value the circle holds at each of `distances_squared` from its center, worked out as
        `SourceField.value_at` works out one, to the last bit."""
        if not self.edge_width:
            return numpy.where(distances_squared <= self._reach_squared, self.value, 0.0)
        depths = (self._reach - numpy.sqrt(distances_squared)) / self.edge_width
        weights = depths * depths * (3.0 - 2.0 * depths) if self.edge is Edge.SMOOTH else depths
        falling = numpy.where(distances_squared <= self._reach_squared, self.value * weights, 0.0)
        return numpy.where(distances_squared <= self._full_within_squared, self.value, falling)


@final
class SourceField:
    """A `Field` of floats made of circles, each adding its value over the ground it covers, its edge included, such as
    a threat around each enemy, and 0 beyond every circle's reach. A circle with a sharp `Edge`, the default, holds
    its value throughout, so a mask is circles of 1.0; one with a linear or a smooth edge falls from its value to 0,
    evenly or smoothly, across a band of `edge_width` centered on its edge, half the value on the edge, so it reaches
    half the width beyond the circle. Where circles overlap, their values add up. The field covers every point, so no
    read raises `IndexError`.

    `min`, `max`, `argmin` and `argmax` read the field at points at most `spacing` apart, along x and along y, over
    the area, 0.1 unless the read is given another, and at the area's point nearest the center of each circle that
    reaches it. So where every circle's value
    is positive, whether `max` reads above 0 is exact, and so is the largest value where one circle reaches the area
    alone. Anything else may be off by as much as the field changes over the spacing, and a value held only on a patch
    narrower than that may be missed.

    A field holds `CircleSource`s, built from them or circle by circle with `add_circle`. `len` counts them, and
    iterating over the field hands them out, in the order added. Fields add up and subtract, a sum holding the circles
    of both, a number scales one, and `transformed` remakes each circle, each a new field. Like a grid, a field has no
    truth value.
    """

    __slots__ = ("_sources",)

    _sources: list[CircleSource]

    def __init__(self, sources: Iterable[CircleSource] = ()) -> None:
        """A field holding `sources`: `SourceField(other)` holds another field's circles. Raises `TypeError` for a
        source that is not a `CircleSource`."""
        self._sources = []
        for source in sources:
            if not isinstance(source, CircleSource):
                raise TypeError(f"a source field holds CircleSources, not a {type(source).__name__}")
            self._sources.append(source)

    def add_circle(
        self, center: PointLike, radius: float, value: float, *, edge: Edge = Edge.SHARP, edge_width: float = 0.0
    ) -> None:
        """Add a circle of `radius` around `center`, holding `value`: throughout, with a sharp `edge`, or with a linear
        or a smooth one, falling across a band of `edge_width` centered on its edge, to half the value on it and 0 half
        the width beyond it, so that an edge as wide as the circle makes a cone, or a smooth one a bump, reaching twice
        the radius. Raises `ValueError` as a `Circle` and a `CircleSource` do: for a radius not above 0, for a width
        given a sharp edge, and for a linear or smooth edge's width not above 0."""
        self._sources.append(CircleSource(Circle(center, radius), value, edge, edge_width))

    def clear(self) -> None:
        """Remove every circle."""
        self._sources.clear()

    def copy(self) -> SourceField:
        """A new field holding the same circles, which a circle added to either leaves out of the other."""
        return SourceField(self._sources)

    def __len__(self) -> int:
        """The number of circles added."""
        return len(self._sources)

    def __iter__(self) -> Iterator[CircleSource]:
        """Each circle the field holds, in the order added."""
        return iter(self._sources)

    def __bool__(self) -> bool:
        """Raises `TypeError`, as a grid's does: whether a field holds a circle and whether it reads anything differ,
        for circles of 0 or ones that cancel out. `len` says the one, and `max` within an area the other."""
        raise TypeError("a source field has no truth value; len() says whether it holds a circle")

    # --- Arithmetic

    def __add__(self, other: SourceField) -> SourceField:
        """A new field holding this field's circles and then `other`'s, so that its value is the sum of theirs
        everywhere."""
        if not isinstance(other, SourceField):
            return NotImplemented
        return SourceField([*self._sources, *other._sources])

    def __sub__(self, other: SourceField) -> SourceField:
        """A new field holding this field's circles and then `other`'s with their values negated, so that its value is
        the difference of theirs everywhere."""
        if not isinstance(other, SourceField):
            return NotImplemented
        return self + -other

    def __neg__(self) -> SourceField:
        """A new field of the same circles, each with its value negated."""
        return self.transformed(lambda source: source._with_value(-source.value))

    def __mul__(self, factor: float) -> SourceField:
        """A new field of the same circles, each with its value multiplied by `factor`, a number."""
        if not isinstance(factor, numbers.Real):
            return NotImplemented
        return self.transformed(lambda source: source._with_value(source.value * factor))

    def __rmul__(self, factor: float) -> SourceField:
        """A new field of the same circles, each with its value multiplied by `factor`, a number."""
        return self.__mul__(factor)

    def __truediv__(self, divisor: float) -> SourceField:
        """A new field of the same circles, each with its value divided by `divisor`, a number. Raises
        `ZeroDivisionError` for 0."""
        if not isinstance(divisor, numbers.Real):
            return NotImplemented
        return self.transformed(lambda source: source._with_value(source.value / divisor))

    def transformed(self, transformation: Callable[[CircleSource], CircleSource]) -> SourceField:
        """A new field of each circle as `transformation` remakes it, a `CircleSource` from a `CircleSource`, in the
        same order: its value, its circle or its edge, through `dataclasses.replace`. Raises `TypeError` for anything
        but a `CircleSource` made, which was checked as it was made."""
        return SourceField(transformation(source) for source in self._sources)

    # --- Reads

    def sources_reaching(self, area: Area) -> list[CircleSource]:
        """Each circle reaching `area`, as a `CircleSource`, in the order it was added: those whose values the reads
        over the area take, those it reaches within its radius and half its edge's width, so not one touching only an
        edge the area leaves out. An empty area has none."""
        if not area.area:
            return []
        sources = self._sources_near(area.bounding_rectangle())
        return [source for source, _ in _sources_reaching(area, sources)]

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
        return _values_at(xs, ys, self._sources_near(bounds))

    def value_at(self, point: PointLike) -> float:
        """The value at `point`: the sum of the values of the circles covering it."""
        x = point[0]
        y = point[1]
        total = 0.0
        for source in self._sources:
            dx = x - source._x
            dy = y - source._y
            distance_squared = dx * dx + dy * dy
            if distance_squared <= source._full_within_squared:
                total += source.value
            elif distance_squared <= source._reach_squared:
                depth = (source._reach - math.sqrt(distance_squared)) / source.edge_width
                weight = depth * depth * (3.0 - 2.0 * depth) if source.edge is Edge.SMOOTH else depth
                total += source.value * weight
        return total

    def min(self, *, within: Area, spacing: float = 0.1) -> float:
        """The smallest value read in `within`, at points at most `spacing` apart, as the class says. Raises
        `ValueError` for an empty area and for a spacing not above 0."""
        return self._extreme_value(within, spacing, largest=False)

    def max(self, *, within: Area, spacing: float = 0.1) -> float:
        """The largest value read in `within`, at points at most `spacing` apart, as the class says. Raises
        `ValueError` for an empty area and for a spacing not above 0."""
        return self._extreme_value(within, spacing, largest=True)

    def argmin(self, *, within: Area, spacing: float = 0.1) -> Point:
        """The point, of those read in `within` at points at most `spacing` apart as the class says, with the smallest
        value, the lowest x, then y, where several tie: always one `within` holds. Raises `ValueError` for an empty
        area and for a spacing not above 0."""
        return self._extreme_point(within, spacing, largest=False)

    def argmax(self, *, within: Area, spacing: float = 0.1) -> Point:
        """The point, of those read in `within` at points at most `spacing` apart as the class says, with the largest
        value, as `argmin` picks one."""
        return self._extreme_point(within, spacing, largest=True)

    def _extreme_value(self, area: Area, spacing: float, *, largest: bool) -> float:
        """The largest value read in `area` at points at most `spacing` apart, or the smallest."""
        _check_not_empty(area)
        _check_spacing(spacing)
        if not self._sources:
            return 0.0
        bounds = area.bounding_rectangle()
        sources = self._sources_near(bounds)
        reaching = _sources_reaching(area, sources)
        if not reaching:
            return 0.0
        if len(reaching) == 1 and largest == (reaching[0][0].value > 0):
            # A circle reaching the area alone holds its value farthest from 0 at the area's point nearest its center,
            # which is one of the points read, and the others near reach none of them.
            return self.value_at(reaching[0][1])
        xs, ys = _points_to_read(area, bounds, spacing, [point for _, point in reaching])
        values = _values_at(xs, ys, sources)
        return (values.max() if largest else values.min()).item()

    def _extreme_point(self, area: Area, spacing: float, *, largest: bool) -> Point:
        """The point read in `area` at points at most `spacing` apart with the largest value, or the smallest, the
        lowest x, then y, where several tie."""
        _check_not_empty(area)
        _check_spacing(spacing)
        bounds = area.bounding_rectangle()
        sources = self._sources_near(bounds)
        xs, ys = _points_to_read(area, bounds, spacing, [point for _, point in _sources_reaching(area, sources)])
        values = _values_at(xs, ys, sources)
        tied = numpy.flatnonzero(values == (values.max() if largest else values.min()))
        picked = tied[numpy.lexsort((ys[tied], xs[tied]))[0]]
        return Point((xs[picked].item(), ys[picked].item()))

    def _sources_near(self, bounds: Rectangle) -> list[CircleSource]:
        """The circles that may reach into `bounds`, an area's bounding rectangle: every one that does, and some that
        only come near."""
        left, bottom = bounds.x, bounds.y
        right, top = bounds.right, bounds.top
        return [
            source
            for source in self._sources
            if source._x - source._reach <= right
            and source._x + source._reach >= left
            and source._y - source._reach <= top
            and source._y + source._reach >= bottom
        ]

    def gradient_at(self, point: PointLike) -> tuple[float, float]:
        """How fast the value rises at `point`, per map unit, along x and along y, together pointing the way it rises
        fastest. Exact: a circle slopes across its edge's band, beyond where that begins and out to where it ends, where
        a linear edge slopes too and a smooth one is flat, and is flat elsewhere, at its center too, where an edge wider
        than the circle meets itself in a peak."""
        x = point[0]
        y = point[1]
        slope_x = 0.0
        slope_y = 0.0
        for source in self._sources:
            dx = x - source._x
            dy = y - source._y
            distance_squared = dx * dx + dy * dy
            if source._full_within_squared < distance_squared <= source._reach_squared and distance_squared > 0.0:
                distance = math.sqrt(distance_squared)
                slope_per_offset = -source.value / (source.edge_width * distance)
                if source.edge is Edge.SMOOTH:
                    depth = (source._reach - distance) / source.edge_width
                    slope_per_offset *= 6.0 * depth * (1.0 - depth)
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
        for source in self._sources:
            # The tiles the circle's bounding box overlaps, of those the grid holds.
            left = max(x_range.start, math.floor(source._x - source._reach))
            right = min(x_range.stop, math.floor(source._x + source._reach) + 1)
            bottom = max(y_range.start, math.floor(source._y - source._reach))
            top = min(y_range.stop, math.floor(source._y + source._reach) + 1)
            if left >= right or bottom >= top:
                continue
            dx = (numpy.arange(left, right) + 0.5) - source._x
            dy = (numpy.arange(bottom, top) + 0.5) - source._y
            block = values[left - x_range.start : right - x_range.start, bottom - y_range.start : top - y_range.start]
            block += source._values_at_distances_squared((dx * dx)[:, None] + (dy * dy)[None, :])
        return Grid(values, origin=Tile(x_range.start, y_range.start), outside=outside)

    def __repr__(self) -> str:
        return f"SourceField({len(self._sources)} circles)"


def _values_at(xs: ndarray, ys: ndarray, sources: list[CircleSource]) -> ndarray:
    """The sum of the values of `sources` covering each point of `xs` and `ys`, worked out as `value_at` works out
    one."""
    totals = numpy.zeros(len(xs))
    for source in sources:
        dx = xs - source._x
        dy = ys - source._y
        totals += source._values_at_distances_squared(dx * dx + dy * dy)
    return totals


def _sources_reaching(area: Area, sources: list[CircleSource]) -> list[tuple[CircleSource, Point]]:
    """Each of `sources` that reaches `area`, with the area's point nearest its center."""
    reaching = []
    for source in sources:
        point, distance = area.closest_point_to((source._x, source._y))
        if distance <= source._reach:
            reaching.append((source, point))
    return reaching


def _points_to_read(
    area: Area, bounds: Rectangle, spacing: float, nearest_points: list[Point]
) -> tuple[ndarray, ndarray]:
    """The x and the y of the points to read in `area`, whose bounding rectangle is `bounds`: those at most `spacing`
    apart, by ascending x, then y, then `nearest_points`, the area's point nearest each reaching circle's center."""
    xs, ys = _points_spaced_over(area, bounds, spacing)
    if not nearest_points:
        return xs, ys
    nearest = numpy.array(nearest_points)
    return numpy.concatenate((xs, nearest[:, 0])), numpy.concatenate((ys, nearest[:, 1]))


def _check_spacing(spacing: float) -> None:
    """Raises `ValueError` for a spacing not above 0, NaN included."""
    if not spacing > 0:
        raise ValueError(f"a source field reads points a positive spacing apart, got {spacing}")


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
