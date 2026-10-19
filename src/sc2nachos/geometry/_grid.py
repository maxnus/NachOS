"""A grid of per-tile values laid over a rectangular patch of the map."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, cast, final, overload

import numpy
import scipy.ndimage
from scipy.sparse import coo_array
from scipy.sparse.csgraph import dijkstra

from sc2nachos.geometry._area import Area
from sc2nachos.geometry._footprint import _margin_sides
from sc2nachos.geometry._point import coordinates
from sc2nachos.geometry._shapes import Rectangle, Tile, tile_address_of

if TYPE_CHECKING:
    from collections.abc import Callable

    from numpy import ndarray

    from sc2nachos.geometry._footprint import Footprint, Margin
    from sc2nachos.geometry._point import PointLike

_ORIGIN = Tile(0, 0)


# Scalars a grid combines with. numpy scalars subclass neither `int` nor `bool`, and only `float64` subclasses
# `float`; without them listed, the operand round-trips through numpy and back at twice the cost. A module constant,
# because an inline `a | b` union is rebuilt on every call at seven times the cost.
SCALAR_TYPES = (int, float, numpy.number, numpy.bool_)


@final
class Grid[T: float]:
    """A grid of per-tile values, addressed by map position rather than by array index.

    `origin` is the tile at the grid's lower left corner, so a grid can cover the playable area alone. An area
    reaching past the edge is clipped to the tiles the grid holds; a point outside it raises `IndexError`.

    A grid the library shares, such as the map's, is `readonly` and refuses every write. `copy()` gives a writable
    one, as does every other grid derived from it.
    """

    __slots__ = ("_data", "_origin", "_outside", "_readonly")

    _data: ndarray
    _origin: Tile
    _outside: T | None
    _readonly: bool

    # --- Construction

    def __init__(
        self, data: ndarray, *, origin: Tile = _ORIGIN, outside: T | None = None, readonly: bool = False
    ) -> None:
        """A grid holding `data`, whose `[0, 0]` entry is the tile at `origin`.

        `outside` is what a read beyond the grid returns; without it such a read raises. A write beyond the grid
        always raises, and a `readonly` grid refuses every write.
        """
        if data.ndim != 2:
            raise ValueError(f"a grid is two-dimensional, got {data.ndim} dimensions")
        self._data = data
        self._origin = origin
        self._outside = outside
        self._readonly = readonly

    @classmethod
    def zeros(
        cls, width: int, height: int, *, origin: Tile = _ORIGIN, dtype: type[T] = float, outside: T | None = None
    ) -> Grid[T]:
        """A grid of `width` by `height` tiles, filled with zeros."""
        return Grid(numpy.zeros((width, height), dtype=dtype), origin=origin, outside=outside)

    @classmethod
    def like[U: float](cls, other: Grid[U], *, dtype: type[T] = float, outside: T | None = None) -> Grid[T]:
        """A grid of zeros covering the same tiles as `other`."""
        return Grid(numpy.zeros(other._data.shape, dtype=dtype), origin=other._origin, outside=outside)

    # --- The ground it covers

    @property
    def origin(self) -> Tile:
        """The tile at the grid's lower left corner."""
        return self._origin

    @property
    def width(self) -> int:
        """The number of tiles across."""
        return self._data.shape[0]

    @property
    def height(self) -> int:
        """The number of tiles up."""
        return self._data.shape[1]

    @property
    def bounds(self) -> Rectangle:
        """The ground the grid covers."""
        return Rectangle(self._origin.x, self._origin.y, self.width, self.height)

    @property
    def values(self) -> ndarray:
        """A read-only view of the grid, indexed `[x, y]` from `origin`."""
        view = self._data.view()
        view.flags.writeable = False
        return view

    def index_of(self, point: PointLike | Tile) -> tuple[int, int]:
        """The index into `values` of the tile holding `point`, a tile reading as itself.

        Raises `IndexError` for a point the grid does not cover, whatever `outside` is.
        """
        if (index := self._index_if_covered(point)) is None:
            raise IndexError(f"{Tile.containing(point)!r} lies outside {self.bounds!r}")
        return index

    def _index_if_covered(self, point: PointLike | Tile) -> tuple[int, int] | None:
        """The index into `values` of the tile holding `point`, or `None` for a point the grid does not cover."""
        tile_x, tile_y = tile_address_of(point)
        x, y = tile_x - self._origin.x, tile_y - self._origin.y
        if 0 <= x < self._data.shape[0] and 0 <= y < self._data.shape[1]:
            return x, y
        return None

    def tile_at(self, index: tuple[int, int]) -> Tile:
        """The tile an index into `values` addresses. The inverse of `index_of`."""
        x, y = index
        if not (0 <= x < self._data.shape[0] and 0 <= y < self._data.shape[1]):
            raise IndexError(f"{index} lies outside a {self.width} by {self.height} grid")
        return Tile(self._origin.x + x, self._origin.y + y)

    @property
    def outside(self) -> T | None:
        """What a read beyond the grid returns, or `None` if one raises."""
        return self._outside

    @property
    def readonly(self) -> bool:
        """Whether the grid refuses writes."""
        return self._readonly

    def with_outside(self, value: T | None) -> Grid[T]:
        """The same grid, reading `value` beyond its edge.

        It shares the data rather than copying it, and stays read-only if this grid is.
        """
        return Grid(self._data, origin=self._origin, outside=value, readonly=self._readonly)

    def _value_at(self, point: PointLike | Tile) -> T:
        """The value at `point`, or `outside` for a point the grid does not cover."""
        if (index := self._index_if_covered(point)) is not None:
            return self._data.item(index)
        if self._outside is None:
            raise IndexError(f"{Tile.containing(point)!r} lies outside {self.bounds!r}")
        return self._outside

    def _aligned(self, other: Grid) -> ndarray:
        """The values of `other`, which must cover the same tiles as this grid."""
        if not isinstance(other, Grid):
            raise TypeError(f"expected a grid, got {type(other).__name__}")
        if other._data.shape != self._data.shape or other._origin != self._origin:
            raise ValueError(f"{other!r} does not cover the same tiles as {self!r}")
        return other._data

    # --- Reading and writing

    # `Tile` is an `Area`, and the only one that addresses a single tile, so it must be matched first. The overlap
    # is real: a key typed `Area` that holds a `Tile` is annotated `ndarray` but returns a scalar.
    @overload
    def __getitem__(self, key: Tile) -> T: ...  # pyright: ignore[reportOverlappingOverload]

    @overload
    def __getitem__(self, key: Rectangle | Area | Grid[bool]) -> ndarray: ...
    @overload
    def __getitem__(self, key: PointLike) -> T: ...

    def __getitem__(self, key: PointLike | Area | Grid[bool]) -> T | ndarray:
        """The value at a point, the values over an area, or the values a mask selects. The values over a rectangle
        are a view, read-only when the grid is; the others are a copy, by ascending x, then y."""
        # Points first, checked as tuples, then a tile, which addresses a single tile like any point. A miss here is
        # cheap; a miss against an `Area` subclass is not.
        if isinstance(key, tuple) or type(key) is Tile:
            return self._value_at(key)
        if isinstance(key, Rectangle):
            view = self._data[self._slices(key)]
            if self._readonly:
                view.flags.writeable = False
            return view
        if isinstance(key, Area):
            grid_slices, area_mask_on_grid = self._slices_and_mask_for_area(key)
            return self._data[grid_slices][area_mask_on_grid]
        if isinstance(key, Grid):
            return self._data[self._mask(key)]
        return self._value_at(key)

    def __setitem__(self, key: PointLike | Area | Grid[bool], value: T | ndarray) -> None:
        """Writes a value at a point, over an area, or over the tiles a mask selects."""
        self._refuse_readonly()
        if isinstance(key, tuple) or type(key) is Tile:
            self._data[self.index_of(key)] = value
        elif isinstance(key, Rectangle):
            self._data[self._slices(key)] = value
        elif isinstance(key, Area):
            grid_slices, area_mask_on_grid = self._slices_and_mask_for_area(key)
            self._data[grid_slices][area_mask_on_grid] = value
        elif isinstance(key, Grid):
            self._data[self._mask(key)] = value
        else:
            self._data[self.index_of(key)] = value

    def fill(self, value: T) -> None:
        """Sets every tile to `value`."""
        self._refuse_readonly()
        self._data[:] = value

    def fill_footprint(self, footprint: Footprint, *, at: PointLike, value: T, margin: Margin = 0) -> None:
        """Sets every tile `footprint` covers, centered at `at`, to `value`, and with `margin`, every tile within it of
        one the footprint covers, as `fits_footprint` takes it, skipping those beyond the grid. Raises `ValueError` for
        a negative margin."""
        self._refuse_readonly()
        footprint, at = footprint._grown_by(margin, at=at)
        grid_slices, mask_slices = self._slices_for_footprint(footprint, at)
        self._data[grid_slices][footprint.mask[mask_slices]] = value

    def fits_footprint(self: Grid[bool], footprint: Footprint, *, at: PointLike, margin: Margin = 0) -> bool:
        """Whether every tile `footprint` covers, centered at `at`, is true, and with `margin`, every tile within it of
        one the footprint covers: as many on every side, or so many on its left, right, bottom and top. A tile beyond
        the grid reads `outside`, and raises `IndexError` if that is `None`. Raises `ValueError` for a negative
        margin."""
        footprint, at = footprint._grown_by(margin, at=at)
        grid_slices, mask_slices = self._slices_for_footprint(footprint, at)
        covered_on_grid = footprint.mask[mask_slices]
        if numpy.count_nonzero(covered_on_grid) < numpy.count_nonzero(footprint.mask):
            if self._outside is None:
                raise IndexError(f"{footprint!r} centered at {at} reaches past {self.bounds!r}")
            if not self._outside:
                return False
        return bool((self._data[grid_slices].astype(bool, copy=False) | ~covered_on_grid).all())

    def _refuse_readonly(self) -> None:
        """Raises unless this grid can be written to."""
        if self._readonly:
            raise TypeError(f"{self!r} is read-only, because it is shared; copy() it to change it")

    def _slices_for_footprint(
        self, footprint: Footprint, center: PointLike
    ) -> tuple[tuple[slice, slice], tuple[slice, slice]]:
        """The slices that line `footprint`, centered at `center`, up with the grid: first those of the grid's values
        under its bounding box, clipped to the grid, then those of its mask over the same tiles. A 3x3 footprint
        centered on the grid's lower left tile meets the values in `[0:2, 0:2]` with its mask's `[1:3, 1:3]`. Both are
        empty where it lies wholly beyond the grid."""
        left, bottom = footprint._lower_left_tile(at=center)
        return self._slices_for_mask(left, bottom, footprint.mask.shape)

    def _slices_and_mask_for_area(self, area: Area) -> tuple[tuple[slice, slice], ndarray]:
        """The slices of the grid's values over the tiles `area` covers, clipped to the grid, and the mask of those
        tiles over the same slices."""
        origin, area_mask = area._tile_mask(self.bounds)
        grid_slices, mask_slices = self._slices_for_mask(origin.x, origin.y, area_mask.shape)
        return grid_slices, area_mask[mask_slices]

    def _slices_for_mask(
        self, left: int, bottom: int, shape: tuple[int, ...]
    ) -> tuple[tuple[slice, slice], tuple[slice, slice]]:
        """The slices that line a mask of `shape`, whose `[0, 0]` entry is the tile at `left` and `bottom`, up with the
        grid: first those of the grid's values under it, clipped to the grid, then those of the mask over the same
        tiles. Both are empty where it lies wholly beyond the grid."""
        width, height = self._data.shape
        mask_x, mask_y = left - self._origin.x, bottom - self._origin.y
        x = min(max(mask_x, 0), width)
        y = min(max(mask_y, 0), height)
        stop_x = min(max(mask_x + shape[0], x), width)
        stop_y = min(max(mask_y + shape[1], y), height)
        grid_slices = (slice(x, stop_x), slice(y, stop_y))
        return grid_slices, (slice(x - mask_x, stop_x - mask_x), slice(y - mask_y, stop_y - mask_y))

    def _slices(self, rectangle: Rectangle) -> tuple[slice, slice]:
        """The array slices covering the tiles of `rectangle`, clipped to the grid."""
        x_range, y_range = rectangle.tile_range()
        grid_slices, _ = self._slices_for_mask(x_range.start, y_range.start, (len(x_range), len(y_range)))
        return grid_slices

    def _mask(self, mask: Grid[bool]) -> ndarray:
        """The values of `mask`, which must cover the same tiles and hold booleans; numpy would read any other dtype
        as row indices."""
        values = self._aligned(mask)
        if values.dtype != bool:
            raise TypeError(f"a grid used as a mask must hold booleans, got {values.dtype}")
        return values

    # --- Derived grids

    def copy(self) -> Grid[T]:
        """An independent grid holding the same values."""
        return Grid(self._data.copy(), origin=self._origin, outside=self._outside)

    def as_readonly(self) -> Grid[T]:
        """The same grid, refusing writes. It shares the values, so a write to this grid shows in it."""
        return Grid(self.values, origin=self._origin, outside=self._outside, readonly=True)

    def where_footprint_fits(self: Grid[bool], footprint: Footprint, *, margin: Margin = 0) -> Grid[bool]:
        """Where `footprint` fits, with `margin` as `fits_footprint` takes it, by the tile holding its center: true
        where every tile it covers, centered as `footprint.center_in_tile` says, is true, which is this grid eroded by
        the footprint. A tile beyond the grid reads `outside`, or false if that is `None`. The result covers the same
        tiles as this grid and reads false beyond them. Raises `ValueError` for a negative margin."""
        values = self._data.astype(bool, copy=False)
        if any(sides := _margin_sides(margin)):
            return Grid(self._where_grown_fits(values, footprint, sides), origin=self._origin, outside=False)
        if not footprint.mask.any():
            return Grid(numpy.ones_like(values), origin=self._origin, outside=False)
        # The structure's own center, index `side // 2` along each axis, is where `center_in_tile` centers a footprint.
        fitting = scipy.ndimage.binary_erosion(values, structure=footprint.mask, border_value=int(bool(self._outside)))
        return Grid(fitting, origin=self._origin, outside=False)

    def _where_grown_fits(self, values: ndarray, footprint: Footprint, sides: tuple[int, int, int, int]) -> ndarray:
        """Where `footprint` fits on `values` with `sides` free beside it, by the tile holding its center."""
        left, _, bottom, _ = sides
        grown = footprint._grown(sides)
        if not grown.mask.any():
            return numpy.ones_like(values)
        # Padded so that every center on the grid has its grown footprint's center on the padding or the grid.
        pad = max(sides)
        border = bool(self._outside)
        padded = numpy.pad(values, pad, constant_values=border)
        fitting = scipy.ndimage.binary_erosion(padded, structure=grown.mask, border_value=int(border))
        # The tile holding the footprint's center lies this far from the one holding the grown footprint's.
        dx = left + footprint.width // 2 - grown.width // 2
        dy = bottom + footprint.height // 2 - grown.height // 2
        width, height = values.shape
        return fitting[pad - dx : pad - dx + width, pad - dy : pad - dy + height]

    def where(self, condition: Grid[bool], other: T | Grid[T]) -> Grid[T]:
        """This grid where `condition` holds, and `other` everywhere else."""
        values = self._aligned(other) if isinstance(other, Grid) else other
        # `_aligned` runs before `_where_outside`: it rejects a condition that is not a grid, and `_where_outside`
        # reads `condition._outside`, which needs that check to have passed.
        chosen = numpy.where(self._aligned(condition), self._data, values)
        return Grid(chosen, origin=self._origin, outside=self._where_outside(condition, other))

    def _where_outside(self, condition: Grid[bool], other: T | Grid[T]) -> T | None:
        """The off-grid value of whichever operand the condition picks off-grid, or `None` if it has none."""
        if condition._outside is None:
            return None
        if condition._outside:
            return self._outside
        return other._outside if isinstance(other, Grid) else other

    def minimum(self, other: Grid[T]) -> Grid[T]:
        """The smaller of the two values on each tile."""
        return self._combine(other, numpy.minimum)

    def maximum(self, other: Grid[T]) -> Grid[T]:
        """The larger of the two values on each tile."""
        return self._combine(other, numpy.maximum)

    def cropped(self, rectangle: Rectangle) -> Grid[T]:
        """An independent grid over the tiles `rectangle` covers, clipped to the ones this grid holds."""
        x, y = self._slices(rectangle)
        origin = Tile(self._origin.x + x.start, self._origin.y + y.start)
        return Grid(self._data[x, y].copy(), origin=origin, outside=self._outside)

    def distance_from(self, point: PointLike, *, outside: float | None = None) -> Grid[float]:
        """The distance from each tile's center to `point`."""
        position = coordinates(point)
        xs = numpy.arange(self.width) + self._origin.x + 0.5 - position[0]
        ys = numpy.arange(self.height) + self._origin.y + 0.5 - position[1]
        return Grid(numpy.hypot(xs[:, None], ys[None, :]), origin=self._origin, outside=outside)

    def distance_transform(self: Grid[bool], *, outside: float | None = None) -> Grid[float]:
        """For each true tile, the distance from its center to the nearest false tile's; zero on a false tile. Where
        this grid reads false beyond its edge, the tiles beyond it count as false; where no false tile is found, every
        distance is `inf`."""
        if self._outside is not None and not self._outside:
            padded = numpy.pad(self._data, 1, constant_values=False)
            distances = numpy.asarray(scipy.ndimage.distance_transform_edt(padded, return_indices=False))[1:-1, 1:-1]
        elif self._data.all():
            distances = numpy.full(self._data.shape, math.inf)
        else:
            distances = numpy.asarray(scipy.ndimage.distance_transform_edt(self._data, return_indices=False))
        return Grid(distances, origin=self._origin, outside=outside)

    def path_distance_from(self: Grid[bool], point: PointLike, *, outside: float | None = None) -> Grid[float]:
        """The length of the shortest path from the tile holding `point` to each tile's center, over true tiles: `inf`
        where no path reaches. A nonzero value counts as true.

        A path steps to a tile sharing a side or a corner, and across a corner only where both tiles sharing a side
        with the two are true. The tile holding `point` reads 0 even when false, but no path leaves a false tile, so
        every other tile then reads `inf`. Raises `IndexError` for a point the grid does not cover.
        """
        # Each step a path can take to a neighboring tile, across, up or diagonally, with its length. The steps back the
        # other way are the same edges of an undirected graph.
        neighbor_steps = ((1, 0, 1.0), (0, 1, 1.0), (1, 1, math.sqrt(2)), (1, -1, math.sqrt(2)))
        start = self.index_of(point)
        passable = self._data.astype(bool, copy=False)
        width, height = passable.shape
        tiles = numpy.arange(width * height).reshape(width, height)
        sources, targets, lengths = [], [], []
        for dx, dy, length in neighbor_steps:
            # The tiles a step leaves from and lands on, as slices of the grid lined up with each other.
            from_xs, to_xs = slice(0, width - dx), slice(dx, width)
            from_ys, to_ys = (
                (slice(0, height - dy), slice(dy, height)) if dy >= 0 else (slice(-dy, height), slice(0, height + dy))
            )
            open_step = passable[from_xs, from_ys] & passable[to_xs, to_ys]
            if dx and dy:
                open_step &= passable[to_xs, from_ys] & passable[from_xs, to_ys]
            sources.append(tiles[from_xs, from_ys][open_step])
            targets.append(tiles[to_xs, to_ys][open_step])
            lengths.append(numpy.full(int(open_step.sum()), length))
        steps = coo_array(
            (numpy.concatenate(lengths), (numpy.concatenate(sources), numpy.concatenate(targets))),
            shape=(width * height, width * height),
        )
        distances = dijkstra(steps.tocsr(), directed=False, indices=int(tiles[start]))
        return Grid(numpy.asarray(distances).reshape(width, height), origin=self._origin, outside=outside)

    def smoothed(self, sigma: float, *, within: Grid[bool] | None = None, outside: float | None = None) -> Grid[float]:
        """A Gaussian blur of the values, `sigma` measured in tiles.

        Given `within`, only those tiles contribute and the result is renormalized, so values near the edge
        of the region are not dragged down by the tiles outside it. A tile no contributor reaches keeps its
        own value.
        """
        values = self._data.astype(float)
        if within is None:
            return Grid(scipy.ndimage.gaussian_filter(values, sigma=sigma), origin=self._origin, outside=outside)
        weights = self._mask(within).astype(float)
        numerator = scipy.ndimage.gaussian_filter(values * weights, sigma=sigma)
        denominator = scipy.ndimage.gaussian_filter(weights, sigma=sigma)
        blurred = values.copy()
        numpy.divide(numerator, denominator, out=blurred, where=denominator > 0)
        return Grid(blurred, origin=self._origin, outside=outside)

    # --- Summaries

    def min(self) -> T:
        """The smallest value."""
        return self._data.min().item()

    def max(self) -> T:
        """The largest value."""
        return self._data.max().item()

    @overload
    def sum(self: Grid[bool]) -> int: ...
    @overload
    def sum(self) -> T: ...

    def sum(self) -> T | int:
        """The total over every tile; for a boolean grid, the number of true ones."""
        return self._data.sum().item()

    def argmin(self) -> Tile:
        """The tile with the smallest value; the lowest address if several tie."""
        return self._tile_at(int(numpy.argmin(self._data)))

    def argmax(self) -> Tile:
        """The tile with the largest value; the lowest address if several tie."""
        return self._tile_at(int(numpy.argmax(self._data)))

    def _tile_at(self, flat_index: int) -> Tile:
        """The tile addressed by a flat index into the grid."""
        x, y = numpy.unravel_index(flat_index, self._data.shape)
        return self.tile_at((int(x), int(y)))

    # --- Operators

    def _combine(self, other: object, op: Callable[..., ndarray], *, flip: bool = False) -> Grid:
        """`op` applied against a scalar, or tilewise against a grid covering the same tiles."""
        if isinstance(other, Grid):
            values = self._aligned(other)
        elif isinstance(other, SCALAR_TYPES):
            values = other
        else:
            return NotImplemented
        left, right = (values, self._data) if flip else (self._data, values)
        return Grid(op(left, right), origin=self._origin, outside=self._combine_outside(other, op, flip=flip))

    def _combine_outside(self, other: object, op: Callable[..., ndarray], *, flip: bool) -> T | None:
        """`op` applied to both operands' off-grid values, or `None` if either has none."""
        if self._outside is None:
            return None
        if isinstance(other, Grid):
            if other._outside is None:
                return None
            value = other._outside
        else:
            value = other
        left, right = (value, self._outside) if flip else (self._outside, value)
        return op(left, right).item()

    def __add__(self, other: Grid[T] | T) -> Grid[T]:
        return self._combine(other, numpy.add)

    def __radd__(self, other: T) -> Grid[T]:
        return self._combine(other, numpy.add, flip=True)

    def __sub__(self, other: Grid[T] | T) -> Grid[T]:
        return self._combine(other, numpy.subtract)

    def __rsub__(self, other: T) -> Grid[T]:
        return self._combine(other, numpy.subtract, flip=True)

    def __mul__(self, other: Grid[T] | T) -> Grid[T]:
        return self._combine(other, numpy.multiply)

    def __rmul__(self, other: T) -> Grid[T]:
        return self._combine(other, numpy.multiply, flip=True)

    def __truediv__(self, other: Grid[T] | T) -> Grid[float]:
        return self._combine(other, numpy.divide)

    def __neg__(self) -> Grid[T]:
        # Negating a `T` bound to float widens to float; at runtime the type is unchanged.
        outside = None if self._outside is None else cast("T", -self._outside)
        return Grid(-self._data, origin=self._origin, outside=outside)

    def __abs__(self) -> Grid[T]:
        outside = None if self._outside is None else cast("T", abs(self._outside))
        return Grid(abs(self._data), origin=self._origin, outside=outside)

    def __invert__(self: Grid[bool]) -> Grid[bool]:
        return Grid(
            numpy.logical_not(self._data),
            origin=self._origin,
            outside=None if self._outside is None else not self._outside,
        )

    def __and__(self: Grid[bool], other: Grid[bool] | bool) -> Grid[bool]:
        return self._combine(other, numpy.logical_and)

    def __rand__(self: Grid[bool], other: bool) -> Grid[bool]:
        return self._combine(other, numpy.logical_and, flip=True)

    def __or__(self: Grid[bool], other: Grid[bool] | bool) -> Grid[bool]:
        return self._combine(other, numpy.logical_or)

    def __ror__(self: Grid[bool], other: bool) -> Grid[bool]:
        return self._combine(other, numpy.logical_or, flip=True)

    # --- Comparisons

    def equals(self, other: object) -> bool:
        """Whether `other` is a grid covering the same tiles and holding the same values."""
        return (
            isinstance(other, Grid)
            and self._origin == other._origin
            and self._data.shape == other._data.shape
            and numpy.array_equal(self._data, other._data)
        )

    def __eq__(self, other: Grid[T] | T) -> Grid[bool]:  # pyright: ignore[reportIncompatibleMethodOverride]
        """Whether each tile equals `other`; `equals` compares grids as wholes."""
        return self._combine(other, numpy.equal)

    def __ne__(self, other: Grid[T] | T) -> Grid[bool]:  # pyright: ignore[reportIncompatibleMethodOverride]
        return self._combine(other, numpy.not_equal)

    def __lt__(self, other: Grid[T] | T) -> Grid[bool]:
        return self._combine(other, numpy.less)

    def __le__(self, other: Grid[T] | T) -> Grid[bool]:
        return self._combine(other, numpy.less_equal)

    def __gt__(self, other: Grid[T] | T) -> Grid[bool]:
        return self._combine(other, numpy.greater)

    def __ge__(self, other: Grid[T] | T) -> Grid[bool]:
        return self._combine(other, numpy.greater_equal)

    def __bool__(self) -> bool:
        """Raises `TypeError`: a grid has no truth value. `any` and `all` say whether any tile is true, or every one."""
        raise TypeError("a grid has no truth value; use .any() or .all()")

    def any(self) -> bool:
        """Whether any tile is true."""
        return bool(self._data.any())

    def all(self) -> bool:
        """Whether every tile is true."""
        return bool(self._data.all())

    def __repr__(self) -> str:
        return f"Grid({self.width}x{self.height} of {self._data.dtype} at {self._origin!r})"
