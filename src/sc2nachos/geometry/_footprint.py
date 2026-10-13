"""The tiles a structure covers around its center."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, final

import numpy
from numpy.lib.stride_tricks import sliding_window_view

from sc2nachos.geometry._grid import Grid
from sc2nachos.geometry._point import Point, coordinates
from sc2nachos.geometry._shapes import Tile

if TYPE_CHECKING:
    from numpy import ndarray

    from sc2nachos.geometry._point import PointLike


@final
class Footprint:
    """The tiles a structure covers, as a mask laid around its center.

    Along a side of odd length the mask centers on the tile holding the center, and along a side of even length on the
    tile corner nearest it, as the game snaps a structure: a 3x3 barracks stands on a tile center, a 2x2 depot on a
    tile corner, and a 2x1 mineral field on the edge between two tiles.
    """

    __slots__ = ("_mask",)

    _mask: ndarray

    def __init__(self, mask: ndarray) -> None:
        """A footprint covering the true entries of `mask`, indexed `[x, y]` from its lower left tile."""
        if mask.ndim != 2:
            raise ValueError(f"a footprint is two-dimensional, got {mask.ndim} dimensions")
        self._mask = numpy.array(mask, dtype=bool)
        self._mask.flags.writeable = False

    @classmethod
    def from_rows(cls, *rows: str) -> Footprint:
        """The footprint `rows` draw, the top row first: `#` covered, `.` not. No rows cover nothing."""
        if len({len(row) for row in rows}) > 1:
            raise ValueError(f"the rows of a footprint are all as long, got {rows}")
        width = len(rows[0]) if rows else 0
        mask = numpy.array([[row[x] == "#" for row in reversed(rows)] for x in range(width)], dtype=bool)
        return cls(mask.reshape(width, len(rows)))

    @classmethod
    def square(cls, side: int) -> Footprint:
        """A footprint covering every tile of a square `side` tiles across."""
        return cls(numpy.ones((side, side), dtype=bool))

    @property
    def mask(self) -> ndarray:
        """Whether the footprint covers each tile of its bounding box, indexed `[x, y]` from its lower left tile."""
        return self._mask

    @property
    def width(self) -> int:
        """The number of tiles across."""
        return self._mask.shape[0]

    @property
    def height(self) -> int:
        """The number of tiles up."""
        return self._mask.shape[1]

    def lower_left_tile(self, center: PointLike) -> Tile:
        """The tile at the lower left corner of the bounding box, with the footprint centered at `center`."""
        x, y = coordinates(center)[:2]
        return Tile(math.floor(x - self.width / 2 + 0.5), math.floor(y - self.height / 2 + 0.5))

    def center_in(self, tile: Tile) -> Point:
        """Where the footprint is centered when its center lies in `tile`: the tile's center along a side of odd
        length, and its lower left corner along a side of even length."""
        return Point((tile[0] + 0.5 * (self.width % 2), tile[1] + 0.5 * (self.height % 2)))

    def fits(self, grid: Grid[bool], *, at: PointLike) -> bool:
        """Whether every tile the footprint covers, centered at `at`, is true on `grid`. A tile beyond the grid reads
        `grid.outside`, and raises `IndexError` if that is `None`."""
        grid_slices, mask_slices = self._overlap(grid, at)
        covered_on_grid = self._mask[mask_slices]
        if numpy.count_nonzero(covered_on_grid) < numpy.count_nonzero(self._mask):
            if grid.outside is None:
                raise IndexError(f"{self!r} centered at {at} reaches past {grid.bounds!r}")
            if not grid.outside:
                return False
        return bool((grid._data[grid_slices] | ~covered_on_grid).all())

    def centers_fitting(self, grid: Grid[bool]) -> Grid[bool]:
        """Where the footprint fits on `grid`, by the tile holding its center: true where every tile it covers,
        centered as `center_in` says, is true on `grid`. A tile beyond the grid reads `grid.outside`, or false if that
        is `None`. The result covers the same tiles as `grid` and reads false beyond them."""
        values = grid._data.astype(bool, copy=False)
        if not self._mask.any():
            return Grid(numpy.ones_like(values), origin=grid.origin, outside=False)
        before_x, before_y = self.width // 2, self.height // 2
        padded = numpy.pad(
            values,
            ((before_x, self.width - 1 - before_x), (before_y, self.height - 1 - before_y)),
            constant_values=bool(grid.outside),
        )
        windows = sliding_window_view(padded, self._mask.shape)
        return Grid((windows | ~self._mask).all(axis=(2, 3)), origin=grid.origin, outside=False)

    def fill(self, grid: Grid[bool], *, at: PointLike, value: bool) -> None:
        """Set every tile the footprint covers, centered at `at`, to `value`, skipping those beyond the grid. Raises
        `TypeError` on a read-only grid."""
        grid._refuse_readonly()
        grid_slices, mask_slices = self._overlap(grid, at)
        grid._data[grid_slices][self._mask[mask_slices]] = value

    def _overlap(self, grid: Grid[bool], center: PointLike) -> tuple[tuple[slice, slice], tuple[slice, slice]]:
        """The slices of `grid`'s values and of the mask that cover the same tiles, with the footprint centered at
        `center`: empty where the two do not meet."""
        tile = self.lower_left_tile(center)
        grid_slices, mask_slices = [], []
        for low, size, grid_size in (
            (tile[0] - grid.origin[0], self.width, grid.width),
            (tile[1] - grid.origin[1], self.height, grid.height),
        ):
            start = min(max(low, 0), grid_size)
            stop = max(min(low + size, grid_size), start)
            grid_slices.append(slice(start, stop))
            mask_slices.append(slice(start - low, stop - low))
        return (grid_slices[0], grid_slices[1]), (mask_slices[0], mask_slices[1])

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Footprint) and numpy.array_equal(self._mask, other._mask)

    def __hash__(self) -> int:
        return hash((self._mask.shape, self._mask.tobytes()))

    def __repr__(self) -> str:
        rows = (
            "".join("#" if self._mask[x, y] else "." for x in range(self.width)) for y in reversed(range(self.height))
        )
        return f"Footprint.from_rows({', '.join(map(repr, rows))})"
