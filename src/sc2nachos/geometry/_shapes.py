"""The shapes a bot reasons about: tiles, rectangles and sets of tiles."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from functools import cached_property
from typing import TYPE_CHECKING, Self, final

import numpy
from scipy.spatial import KDTree

from sc2nachos.geometry._area import Area
from sc2nachos.geometry._point import Point, _PointND, coordinates

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

    from numpy import ndarray
    from s2clientprotocol import common_pb2

    from sc2nachos.geometry._point import PointLike


@final
class Tile(tuple[int, int], Area):
    """One square of the map, addressed by the integer coordinates of its lower left corner.

    The tuple is the address. Used as a point, a tile reads as its `center`.
    """

    __slots__ = ()

    def __new__(cls, x: int, y: int) -> Self:
        return super().__new__(cls, (x, y))

    @classmethod
    def containing(cls, point: PointLike | Tile) -> Self:
        """The tile a point falls in, or the tile itself."""
        # Skips the general dispatch; this runs once per unit per grid lookup.
        if isinstance(point, _PointND):
            return cls(math.floor(point[0]), math.floor(point[1]))
        if isinstance(point, Tile):
            return cls(point[0], point[1])
        position = coordinates(point)
        return cls(math.floor(position[0]), math.floor(position[1]))

    @property
    def x(self) -> int:
        """The left edge."""
        return self[0]

    @property
    def y(self) -> int:
        """The bottom edge."""
        return self[1]

    @property
    def center(self) -> Point:
        """The middle of the tile, where a unit on it stands."""
        return Point((self[0] + 0.5, self[1] + 0.5))

    @property
    def area(self) -> float:
        """The ground covered: one square game unit."""
        return 1.0

    def random_point(self) -> Point:
        """A point drawn uniformly from the tile."""
        return Point((self[0] + random.random(), self[1] + random.random()))

    @property
    def neighbors4(self) -> tuple[Tile, Tile, Tile, Tile]:
        """The four orthogonally adjacent tiles."""
        x, y = self[0], self[1]
        return (Tile(x, y + 1), Tile(x, y - 1), Tile(x + 1, y), Tile(x - 1, y))

    @property
    def neighbors8(self) -> tuple[Tile, Tile, Tile, Tile, Tile, Tile, Tile, Tile]:
        """The eight adjacent tiles, diagonals included."""
        x, y = self[0], self[1]
        return (
            *self.neighbors4,
            Tile(x + 1, y + 1),
            Tile(x + 1, y - 1),
            Tile(x - 1, y + 1),
            Tile(x - 1, y - 1),
        )

    def __contains__(self, point: object) -> bool:
        """Whether a point falls on this tile, on the lower edges but not the upper ones.

        Membership in the area, not in the coordinate pair: `3 in Tile(3, 4)` raises rather than returning True.
        """
        return Tile.containing(point) == self  # pyright: ignore[reportArgumentType]

    def translated(self, offset: PointLike) -> Tile:
        """The tile reached by shifting this one's center by `offset`."""
        shift = coordinates(offset)
        return Tile.containing((self[0] + 0.5 + shift[0], self[1] + 0.5 + shift[1]))

    def closest_point_to(self, point: PointLike) -> tuple[Point, float]:
        """The point of the tile nearest to `point`, and the distance to it."""
        position = coordinates(point)
        closest = Point(
            (
                max(self[0], min(position[0], self[0] + 1)),
                max(self[1], min(position[1], self[1] + 1)),
            )
        )
        return closest, closest.distance_to(position)

    def bounding_rectangle(self) -> Rectangle:
        """The tile as a unit rectangle."""
        return Rectangle(self[0], self[1], 1, 1)

    def tiles(self) -> TileSet:
        """A set of this tile alone."""
        return TileSet([self])

    def __repr__(self) -> str:
        return f"Tile({self[0]}, {self[1]})"


@final
@dataclass(frozen=True, slots=True)
class Rectangle(Area):
    """An axis-aligned rectangle, anchored at its lower left corner.

    `y` grows upward, as in the game, so `top` is the higher edge.
    """

    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        """Rejects a negative extent."""
        # Two negatives cancel in `area`, so such a rectangle would report a plausible size while covering nothing.
        if self.width < 0 or self.height < 0:
            raise ValueError(f"a rectangle cannot have a negative extent, got {self.width} x {self.height}")

    @classmethod
    def from_center(cls, center: PointLike, width: float, height: float) -> Rectangle:
        """The rectangle of the given size centered on a point."""
        position = coordinates(center)
        return cls(position[0] - width / 2, position[1] - height / 2, width, height)

    @classmethod
    def _from_proto(cls, data: common_pb2.RectangleI) -> Rectangle:
        """Read a rectangle from the game's `RectangleI`, which holds opposite corners rather than a size."""
        return cls(data.p0.x, data.p0.y, data.p1.x - data.p0.x, data.p1.y - data.p0.y)

    @property
    def left(self) -> float:
        """The lower x edge."""
        return self.x

    @property
    def bottom(self) -> float:
        """The lower y edge."""
        return self.y

    @property
    def right(self) -> float:
        """The upper x edge."""
        return self.x + self.width

    @property
    def top(self) -> float:
        """The upper y edge."""
        return self.y + self.height

    @property
    def center(self) -> Point:
        """The midpoint."""
        return Point((self.x + self.width / 2, self.y + self.height / 2))

    @property
    def size(self) -> tuple[float, float]:
        """The extent along each axis."""
        return self.width, self.height

    @property
    def area(self) -> float:
        """The ground covered, in square game units."""
        return self.width * self.height

    @property
    def perimeter(self) -> float:
        """The distance around the edge."""
        return 2 * (self.width + self.height)

    @property
    def corners(self) -> tuple[Point, Point, Point, Point]:
        """The four corners, counter-clockwise from the bottom left."""
        return (
            Point((self.left, self.bottom)),
            Point((self.right, self.bottom)),
            Point((self.right, self.top)),
            Point((self.left, self.top)),
        )

    def __contains__(self, point: PointLike) -> bool:
        """Whether a point lies inside, on the lower edges but not the upper ones."""
        position = coordinates(point)
        return self.left <= position[0] < self.right and self.bottom <= position[1] < self.top

    def encloses(self, other: Rectangle) -> bool:
        """Whether the whole of `other` lies inside."""
        return (
            self.left <= other.left
            and self.bottom <= other.bottom
            and self.right >= other.right
            and self.top >= other.top
        )

    def translated(self, offset: PointLike) -> Rectangle:
        """The rectangle shifted by `offset`, keeping its size."""
        shift = coordinates(offset)
        return Rectangle(self.x + shift[0], self.y + shift[1], self.width, self.height)

    def intersects(self, other: Rectangle) -> bool:
        """Whether the two rectangles share any ground."""
        return not (
            self.right <= other.left or self.left >= other.right or self.top <= other.bottom or self.bottom >= other.top
        )

    def intersection(self, other: Rectangle) -> Rectangle | None:
        """The ground both rectangles cover; `None` if they do not overlap."""
        left = max(self.left, other.left)
        bottom = max(self.bottom, other.bottom)
        right = min(self.right, other.right)
        top = min(self.top, other.top)
        if right <= left or top <= bottom:
            return None
        return Rectangle(left, bottom, right - left, top - bottom)

    def rounded_out(self) -> Rectangle:
        """The smallest rectangle on integer coordinates that contains this one."""
        left = math.floor(self.left)
        bottom = math.floor(self.bottom)
        return Rectangle(left, bottom, math.ceil(self.right) - left, math.ceil(self.top) - bottom)

    def rounded_in(self) -> Rectangle | None:
        """The largest rectangle on integer coordinates that fits inside this one, `None` if none fits."""
        left = math.ceil(self.left)
        bottom = math.ceil(self.bottom)
        right = math.floor(self.right)
        top = math.floor(self.top)
        if right <= left or top <= bottom:
            return None
        return Rectangle(left, bottom, right - left, top - bottom)

    def random_point(self) -> Point:
        """A point drawn uniformly from the rectangle."""
        return Point((self.left + random.uniform(0, self.width), self.bottom + random.uniform(0, self.height)))

    def closest_point_to(self, point: PointLike) -> tuple[Point, float]:
        """The point of the rectangle nearest to `point`, and the distance to it."""
        position = coordinates(point)
        closest = Point(
            (
                max(self.left, min(position[0], self.right)),
                max(self.bottom, min(position[1], self.top)),
            )
        )
        return closest, closest.distance_to(position)

    def bounding_rectangle(self) -> Rectangle:
        """The rectangle itself."""
        return self

    def tile_range(self) -> tuple[range, range]:
        """The x and y addresses of the tiles the rectangle covers."""
        return (
            range(math.ceil(self.left - 0.5), math.ceil(self.right - 0.5)),
            range(math.ceil(self.bottom - 0.5), math.ceil(self.top - 0.5)),
        )

    def tile_centers(self, *, offset: PointLike = (0.0, 0.0)) -> ndarray:
        """The center of every tile the rectangle covers, as an array of shape `(x, y, 2)`.

        Laid out like the tiles themselves, so a mask over the same region indexes it directly. `offset`
        shifts every point without changing which tiles are covered.
        """
        shift = coordinates(offset)
        x_range, y_range = self.tile_range()
        xs = numpy.arange(x_range.start, x_range.stop) + 0.5 + shift[0]
        ys = numpy.arange(y_range.start, y_range.stop) + 0.5 + shift[1]
        return numpy.stack(numpy.meshgrid(xs, ys, indexing="ij"), axis=-1)

    def tiles(self) -> TileSet:
        """Every tile whose center the rectangle contains."""
        x_range, y_range = self.tile_range()
        return TileSet(Tile(x, y) for x in x_range for y in y_range)


@final
class TileSet(Area):
    """An area of any shape, held as the set of map tiles it covers.

    Build one from positions with `Tile.containing`. An empty tile set is falsy.
    """

    _tiles: frozenset[Tile]

    def __init__(self, tiles: Iterable[Tile]) -> None:
        """A tile set covering `tiles`."""
        self._tiles = frozenset(tiles)

    @property
    def center(self) -> Point:
        """The mean of the covered tile centers."""
        return self._centroid

    @cached_property
    def _centroid(self) -> Point:
        """The mean position, computed on first use."""
        if not self._tiles:
            raise ValueError("an empty tile set has no center")
        x = y = 0
        for tile in self._tiles:
            x += tile[0]
            y += tile[1]
        count = len(self._tiles)
        return Point((x / count + 0.5, y / count + 0.5))

    @property
    def area(self) -> float:
        """The ground covered, in square game units: one per tile."""
        return float(len(self._tiles))

    def __len__(self) -> int:
        return len(self._tiles)

    @cached_property
    def _ordered(self) -> tuple[Tile, ...]:
        """The covered tiles by ascending x then y, sorted on first use."""
        # Keep the sort: a set built by difference iterates in a different order from one built directly, which
        # reaches `random_point` and makes a seeded game unreproducible.
        return tuple(sorted(self._tiles))

    def __iter__(self) -> Iterator[Tile]:
        yield from self._ordered

    def __contains__(self, point: PointLike) -> bool:
        """Whether the tile containing `point` is covered."""
        return Tile.containing(point) in self._tiles

    @cached_property
    def _spatial_index(self) -> tuple[KDTree, list[Tile]]:
        """A KD-tree over the tile centers, with the tiles in the order it indexes them, built on first use."""
        tiles = list(self._ordered)
        return KDTree(numpy.array([tile.center for tile in tiles])), tiles

    def closest_point_to(self, point: PointLike) -> tuple[Point, float]:
        """The point of the covered ground nearest to `point`, and the distance to it.

        The nearest tile is found by its center, so a tile whose edge is marginally closer can lose to one
        whose center is nearer.
        """
        if not self._tiles:
            raise ValueError("an empty tile set has no closest point")
        position = Point(coordinates(point)[:2])
        if Tile.containing(position) in self._tiles:
            return position, 0.0
        tree, tiles = self._spatial_index
        _, nearest = tree.query([position.x, position.y])
        return tiles[nearest].closest_point_to(position)

    def random_point(self) -> Point:
        """A point drawn uniformly from the covered ground."""
        if not self._tiles:
            raise ValueError("an empty tile set has no points to draw from")
        return random.choice(self._ordered).random_point()

    def bounding_rectangle(self) -> Rectangle:
        """The smallest rectangle containing every covered tile."""
        if not self._tiles:
            raise ValueError("an empty tile set has no bounding rectangle")
        left = min(tile.x for tile in self._tiles)
        bottom = min(tile.y for tile in self._tiles)
        right = max(tile.x for tile in self._tiles) + 1
        top = max(tile.y for tile in self._tiles) + 1
        return Rectangle(left, bottom, right - left, top - bottom)

    def tiles(self) -> TileSet:
        """The tile set itself."""
        return self

    def translated(self, offset: PointLike) -> TileSet:
        """The tiles shifted by `offset`, snapped back onto the tile grid."""
        return TileSet(tile.translated(offset) for tile in self._tiles)

    def filter(self, predicate: Callable[[Tile], bool]) -> TileSet:
        """The tiles satisfying `predicate`."""
        return TileSet(tile for tile in self._tiles if predicate(tile))

    def __add__(self, other: Area) -> TileSet:
        if not isinstance(other, Area):
            return NotImplemented
        return TileSet(self._tiles.union(other.tiles()))

    def __sub__(self, other: Area) -> TileSet:
        if not isinstance(other, Area):
            return NotImplemented
        return TileSet(self._tiles.difference(other.tiles()))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TileSet):
            return NotImplemented
        return self._tiles == other._tiles

    def __hash__(self) -> int:
        return hash(self._tiles)

    def __repr__(self) -> str:
        if not self._tiles:
            return "TileSet(empty)"
        return f"TileSet({len(self._tiles)} tiles around {self.center})"
