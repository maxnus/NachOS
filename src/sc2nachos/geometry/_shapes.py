"""The shapes a bot reasons about: tiles, rectangles and sets of tiles."""

from __future__ import annotations

import math
import operator
import random
from dataclasses import dataclass
from functools import cached_property
from typing import TYPE_CHECKING, Self, final

import numpy
from scipy.spatial import KDTree

from sc2nachos.geometry._area import Area
from sc2nachos.geometry._point import Point, checked_ground_offset, not_a_point_error

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

    from numpy import ndarray
    from s2clientprotocol import common_pb2

    from sc2nachos.geometry._point import PointLike


@final
@dataclass(frozen=True, slots=True, order=True, init=False)
class Tile(Area):
    """One square of the map, addressed by the integer coordinates of its lower left corner. An area's `in`, a grid's
    index and `Tile.containing` take a tile where they take a point; anything else that wants a point takes its
    `center`. Tiles sort by `x`, then `y`."""

    x: int
    """The left edge."""
    y: int
    """The bottom edge."""

    def __init__(self, x: int, y: int) -> None:
        """The tile whose lower left corner is at `x` and `y`, each an integer, a numpy one included, which becomes an
        `int`. Raises `TypeError` for any other coordinate, a float among them."""
        object.__setattr__(self, "x", operator.index(x))
        object.__setattr__(self, "y", operator.index(y))

    @classmethod
    def containing(cls, point: PointLike | Tile) -> Self:
        """The tile a point falls in, or the tile itself."""
        x, y = Tile._address_containing(point)
        return cls(x, y)

    @staticmethod
    def _address_containing(point: PointLike | Tile) -> tuple[int, int]:
        """The x and y of the tile `point` falls in, or of the tile itself, without a tile built for them. Raises
        `TypeError` on anything but a point or a tile."""
        # Points first: this runs for every lookup of a tile set and a grid.
        if isinstance(point, tuple):
            return math.floor(point[0]), math.floor(point[1])
        if type(point) is Tile:
            return point.x, point.y
        raise not_a_point_error(point)

    @property
    def center(self) -> Point:
        """The middle of the tile, where a unit on it stands."""
        return Point((self.x + 0.5, self.y + 0.5))

    @property
    def area(self) -> float:
        """The ground covered: one square game unit."""
        return 1.0

    def random_point(self, *, rng: random.Random | None = None) -> Point:
        """A point drawn uniformly from the tile, by `rng` or the `random` module's own generator."""
        random_fraction = random.random if rng is None else rng.random
        return Point((self.x + random_fraction(), self.y + random_fraction()))

    @property
    def neighbors4(self) -> tuple[Tile, Tile, Tile, Tile]:
        """The four orthogonally adjacent tiles."""
        x, y = self.x, self.y
        return (Tile(x, y + 1), Tile(x, y - 1), Tile(x + 1, y), Tile(x - 1, y))

    @property
    def neighbors8(self) -> tuple[Tile, Tile, Tile, Tile, Tile, Tile, Tile, Tile]:
        """The eight adjacent tiles, diagonals included."""
        x, y = self.x, self.y
        return (
            *self.neighbors4,
            Tile(x + 1, y + 1),
            Tile(x + 1, y - 1),
            Tile(x - 1, y + 1),
            Tile(x - 1, y - 1),
        )

    def __contains__(self, point: PointLike | Tile) -> bool:
        """Whether a point falls on this tile, on the lower edges but not the upper ones, a tile reading as itself.
        Raises `TypeError` on anything but a point or a tile."""
        return Tile._address_containing(point) == (self.x, self.y)

    def translated(self, offset: PointLike) -> Tile:
        """The tile reached by shifting this one's center by `offset`. Raises `ValueError` for an offset with a
        height."""
        ground_offset = checked_ground_offset(offset)
        return Tile.containing((self.x + 0.5 + ground_offset[0], self.y + 0.5 + ground_offset[1]))

    def closest_point_to(self, point: PointLike) -> tuple[Point, float]:
        """The point of the tile nearest to `point`, and the distance to it."""
        closest = Point(
            (
                max(self.x, min(point[0], self.x + 1)),
                max(self.y, min(point[1], self.y + 1)),
            )
        )
        return closest, closest.distance_to(point)

    def bounding_rectangle(self) -> Rectangle:
        """The tile as a unit rectangle."""
        return Rectangle(self.x, self.y, 1, 1)

    def tiles(self) -> TileSet:
        """A set of this tile alone."""
        return TileSet._of_addresses([(self.x, self.y)])

    def __repr__(self) -> str:
        return f"Tile({self.x}, {self.y})"


def coordinates_or_tile_center(value: PointLike | Tile) -> PointLike:
    """`value`, a point, or a `Tile` read as its center: what `in` takes on every area. Raises `TypeError` for anything
    else."""
    # Points first, so a point pays nothing for the tile check.
    if isinstance(value, tuple):
        return value
    if type(value) is Tile:
        return value.center
    raise not_a_point_error(value)


@final
@dataclass(frozen=True, slots=True)
class Rectangle(Area):
    """An axis-aligned rectangle, anchored at its lower left corner.

    `y` grows upward, as in the game, so `top` is the higher edge. A rectangle with no width or no height covers
    nothing: it is empty, as `Area` has it.
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
        return cls(center[0] - width / 2, center[1] - height / 2, width, height)

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
        self._raise_if_empty("center")
        return Point((self.x + self.width / 2, self.y + self.height / 2))

    @property
    def _is_empty(self) -> bool:
        """Whether the rectangle has no width or no height, and so covers nothing."""
        return not self.width or not self.height

    def _raise_if_empty(self, what: str) -> None:
        """Raises `ValueError`, naming `what` it lacks, if the rectangle is empty."""
        if self._is_empty:
            raise ValueError(f"{self!r} covers no ground, so it has no {what}")

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

    def __contains__(self, point: PointLike | Tile) -> bool:
        """Whether a point lies inside, on the lower edges but not the upper ones."""
        position = coordinates_or_tile_center(point)
        # The fields rather than the edge properties, a call each; the arithmetic is the same as `right` and `top`.
        x, y = self.x, self.y
        return x <= position[0] < x + self.width and y <= position[1] < y + self.height

    def encloses(self, other: Rectangle) -> bool:
        """Whether the whole of `other` lies inside."""
        return (
            self.left <= other.left
            and self.bottom <= other.bottom
            and self.right >= other.right
            and self.top >= other.top
        )

    def translated(self, offset: PointLike) -> Rectangle:
        """The rectangle shifted by `offset`, keeping its size. Raises `ValueError` for an offset with a height."""
        ground_offset = checked_ground_offset(offset)
        return Rectangle(self.x + ground_offset[0], self.y + ground_offset[1], self.width, self.height)

    def intersects(self, other: Rectangle) -> bool:
        """Whether the two rectangles share any ground: never when either is empty."""
        if self._is_empty or other._is_empty:
            return False
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

    def random_point(self, *, rng: random.Random | None = None) -> Point:
        """A point drawn uniformly from the rectangle, by `rng` or the `random` module's own generator."""
        self._raise_if_empty("points to draw from")
        uniform = random.uniform if rng is None else rng.uniform
        return Point((self.left + uniform(0, self.width), self.bottom + uniform(0, self.height)))

    def closest_point_to(self, point: PointLike) -> tuple[Point, float]:
        """The point of the rectangle nearest to `point`, and the distance to it."""
        self._raise_if_empty("closest point")
        closest = Point(
            (
                max(self.left, min(point[0], self.right)),
                max(self.bottom, min(point[1], self.top)),
            )
        )
        return closest, closest.distance_to(point)

    def bounding_rectangle(self) -> Rectangle:
        """The rectangle itself."""
        self._raise_if_empty("bounding rectangle")
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
        x_range, y_range = self.tile_range()
        xs = numpy.arange(x_range.start, x_range.stop) + 0.5 + offset[0]
        ys = numpy.arange(y_range.start, y_range.stop) + 0.5 + offset[1]
        return numpy.stack(numpy.meshgrid(xs, ys, indexing="ij"), axis=-1)

    def tiles(self) -> TileSet:
        """Every tile whose center the rectangle contains."""
        x_range, y_range = self.tile_range()
        return TileSet._of_addresses((x, y) for x in x_range for y in y_range)


def _mask_of_addresses(xs: ndarray, ys: ndarray) -> tuple[Tile, ndarray]:
    """The tiles at `xs` and `ys` as a mask indexed `[x, y]` over their bounding rectangle, and first the tile at its
    `[0, 0]` entry: an empty mask at `Tile(0, 0)` for none."""
    if not len(xs):
        return Tile(0, 0), numpy.zeros((0, 0), dtype=bool)
    lowest_x, lowest_y = int(xs.min()), int(ys.min())
    covered_tiles = numpy.zeros((int(xs.max()) - lowest_x + 1, int(ys.max()) - lowest_y + 1), dtype=bool)
    covered_tiles[xs - lowest_x, ys - lowest_y] = True
    return Tile(lowest_x, lowest_y), covered_tiles


@final
class TileSet(Area):
    """An area of any shape, held as the set of map tiles it covers.

    Build one from positions with `Tile.containing`. An empty tile set is falsy.
    """

    _tile_addresses: frozenset[tuple[int, int]]

    def __init__(self, tiles: Iterable[Tile]) -> None:
        """A tile set covering `tiles`. Raises `TypeError` for anything else among them."""
        try:
            self._tile_addresses = frozenset((tile.x, tile.y) for tile in tiles)
        except AttributeError:
            raise TypeError("a tile set is built of tiles; Tile.containing gives the one a point falls in") from None

    @classmethod
    def _of_addresses(cls, addresses: Iterable[tuple[int, int]]) -> TileSet:
        """A tile set covering the tiles at `addresses`, each an x and a y."""
        tile_set = cls.__new__(cls)
        tile_set._tile_addresses = frozenset(addresses)
        return tile_set

    @classmethod
    def _of_mask(cls, origin: Tile, mask: ndarray) -> TileSet:
        """A tile set covering the true entries of `mask`, indexed `[x, y]`, whose `[0, 0]` entry is the tile at
        `origin`."""
        xs, ys = numpy.nonzero(mask)
        return cls._of_addresses(zip((xs + origin.x).tolist(), (ys + origin.y).tolist(), strict=True))

    @property
    def center(self) -> Point:
        """The mean of the covered tile centers."""
        return self._centroid

    @cached_property
    def _centroid(self) -> Point:
        """The mean position, computed on first use."""
        if not self._tile_addresses:
            raise ValueError("an empty tile set has no center")
        sum_x = sum_y = 0
        for x, y in self._tile_addresses:
            sum_x += x
            sum_y += y
        count = len(self._tile_addresses)
        return Point((sum_x / count + 0.5, sum_y / count + 0.5))

    @property
    def area(self) -> float:
        """The ground covered, in square game units: one per tile."""
        return float(len(self._tile_addresses))

    def __len__(self) -> int:
        return len(self._tile_addresses)

    @cached_property
    def _ordered(self) -> tuple[Tile, ...]:
        """The covered tiles by ascending x then y, sorted on first use."""
        # Keep the sort: a set built by difference iterates in a different order from one built directly, which
        # reaches `random_point` and makes a seeded game unreproducible.
        return tuple(Tile(x, y) for x, y in sorted(self._tile_addresses))

    def __iter__(self) -> Iterator[Tile]:
        yield from self._ordered

    def __contains__(self, point: PointLike | Tile) -> bool:
        """Whether the tile containing `point` is covered."""
        return Tile._address_containing(point) in self._tile_addresses

    @cached_property
    def _spatial_index(self) -> tuple[KDTree, list[Tile]]:
        """A KD-tree over the tile centers, with the tiles in the order it indexes them, built on first use."""
        tiles = list(self._ordered)
        return KDTree(numpy.array([(tile.x + 0.5, tile.y + 0.5) for tile in tiles])), tiles

    def closest_point_to(self, point: PointLike) -> tuple[Point, float]:
        """The point of the covered ground nearest to `point`, and the distance to it."""
        if not self._tile_addresses:
            raise ValueError("an empty tile set has no closest point")
        position = Point(point[:2])
        if Tile._address_containing(position) in self._tile_addresses:
            return position, 0.0
        tree, tiles = self._spatial_index
        nearest_center_distance, _ = tree.query(position)
        # A tile's nearest point lies between 1/2 and sqrt(1/2) nearer than its center, so a tile can beat the one
        # whose center is nearest only if its own center lies within this distance.
        farthest_center_that_can_be_nearer = nearest_center_distance + math.sqrt(0.5) - 0.5
        candidates = tree.query_ball_point(position, farthest_center_that_can_be_nearer, return_sorted=True)
        return min((tiles[index].closest_point_to(position) for index in candidates), key=operator.itemgetter(1))

    def random_point(self, *, rng: random.Random | None = None) -> Point:
        """A point drawn uniformly from the covered ground, by `rng` or the `random` module's own generator."""
        if not self._tile_addresses:
            raise ValueError("an empty tile set has no points to draw from")
        choice = random.choice if rng is None else rng.choice
        return choice(self._ordered).random_point(rng=rng)

    def bounding_rectangle(self) -> Rectangle:
        """The smallest rectangle containing every covered tile."""
        if not self._tile_addresses:
            raise ValueError("an empty tile set has no bounding rectangle")
        lowest_x, lowest_y, highest_x, highest_y = self._address_bounds
        return Rectangle(lowest_x, lowest_y, highest_x - lowest_x + 1, highest_y - lowest_y + 1)

    def tiles(self) -> TileSet:
        """The tile set itself."""
        return self

    def _tile_mask(self, within: Rectangle | None = None) -> tuple[Tile, ndarray]:
        """The covered tiles that lie `within` a rectangle on whole tiles, if one is given, as a mask indexed `[x, y]`
        over their bounding rectangle, and first the tile at its `[0, 0]` entry: an empty mask at `Tile(0, 0)` for
        none."""
        xs, ys = self._address_arrays
        if within is None or not len(xs) or self._lies_within(within):
            # Kept once built: its size is at most that of `within`, or of the set's own bounds if none was given.
            return self._mask_over_bounds
        left, bottom = int(within.x), int(within.y)
        right, top = left + int(within.width), bottom + int(within.height)
        kept = (xs >= left) & (xs < right) & (ys >= bottom) & (ys < top)
        return _mask_of_addresses(xs[kept], ys[kept])

    def _lies_within(self, within: Rectangle) -> bool:
        """Whether every covered tile lies within `within`, a rectangle on whole tiles. The set is not empty."""
        lowest_x, lowest_y, highest_x, highest_y = self._address_bounds
        return (
            within.x <= lowest_x
            and highest_x < within.x + within.width
            and within.y <= lowest_y
            and highest_y < within.y + within.height
        )

    @cached_property
    def _address_bounds(self) -> tuple[int, int, int, int]:
        """The lowest x and y and the highest x and y of the covered tiles, found on first use. The set is not
        empty."""
        xs, ys = self._address_arrays
        return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())

    @cached_property
    def _address_arrays(self) -> tuple[ndarray, ndarray]:
        """The x and the y of every covered tile, as two arrays, built on first use."""
        address_rows = numpy.array(tuple(self._tile_addresses), dtype=int).reshape(-1, 2)
        address_rows.flags.writeable = False
        return address_rows[:, 0], address_rows[:, 1]

    @cached_property
    def _mask_over_bounds(self) -> tuple[Tile, ndarray]:
        """What `_tile_mask` returns for every covered tile, built on first use."""
        origin, covered_tiles = _mask_of_addresses(*self._address_arrays)
        covered_tiles.flags.writeable = False
        return origin, covered_tiles

    def translated(self, offset: PointLike) -> TileSet:
        """The tiles shifted by `offset`, snapped back onto the tile grid, as `Tile.translated` shifts each. Raises
        `ValueError` for an offset with a height."""
        ground_offset = checked_ground_offset(offset)
        offset_x, offset_y = ground_offset[0], ground_offset[1]
        return TileSet._of_addresses(
            (math.floor(x + 0.5 + offset_x), math.floor(y + 0.5 + offset_y)) for x, y in self._tile_addresses
        )

    def filter(self, predicate: Callable[[Tile], bool]) -> TileSet:
        """The tiles satisfying `predicate`."""
        return TileSet(tile for tile in self._ordered if predicate(tile))

    def __add__(self, other: Area) -> TileSet:
        if not isinstance(other, Area):
            return NotImplemented
        return TileSet._of_addresses(self._tile_addresses | other.tiles()._tile_addresses)

    def __sub__(self, other: Area) -> TileSet:
        if not isinstance(other, Area):
            return NotImplemented
        return TileSet._of_addresses(self._tile_addresses - other.tiles()._tile_addresses)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TileSet):
            return NotImplemented
        return self._tile_addresses == other._tile_addresses

    def __hash__(self) -> int:
        return hash(self._tile_addresses)

    def __repr__(self) -> str:
        if not self._tile_addresses:
            return "TileSet(empty)"
        return f"TileSet({len(self._tile_addresses)} tiles around {self.center})"
