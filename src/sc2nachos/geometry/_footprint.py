"""The tiles a structure covers around its center."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, final

import numpy

from sc2nachos.geometry._point import Point, coordinates
from sc2nachos.geometry._shapes import Rectangle, Tile, TileSet

if TYPE_CHECKING:
    from numpy import ndarray

    from sc2nachos.geometry._point import PointLike


@final
class Footprint:
    """The tiles a structure covers, as a mask laid around its center.

    Along a side of odd length the mask centers on the tile holding the center, and along a side of even length on the
    tile corner nearest it, as the game snaps a structure: a 3x3 barracks stands on a tile center, a 2x2 depot on a
    tile corner, and a 2x1 mineral field on the edge between two tiles. A grid says where a footprint fits on it.
    """

    __slots__ = ("_mask",)

    _mask: ndarray

    def __init__(self, mask: ndarray) -> None:
        """A footprint covering the true entries of `mask`, indexed `[x, y]` from its lower left tile. A mask with no
        entries is the empty footprint, whatever its shape."""
        if mask.ndim != 2:
            raise ValueError(f"a footprint is two-dimensional, got {mask.ndim} dimensions")
        self._mask = numpy.array(mask, dtype=bool) if mask.size else numpy.zeros((0, 0), dtype=bool)
        self._mask.flags.writeable = False

    @classmethod
    def from_rows(cls, *rows: str) -> Footprint:
        """The footprint `rows` draw, the top row first: `#` covered, `.` not. No rows cover nothing. Raises
        `ValueError` for rows of different lengths, or with any other character."""
        if len({len(row) for row in rows}) > 1:
            raise ValueError(f"the rows of a footprint are all as long, got {rows}")
        if set("".join(rows)) - {"#", "."}:
            raise ValueError(f"a footprint's rows hold only '#' and '.', got {rows}")
        width = len(rows[0]) if rows else 0
        mask = numpy.array([[row[x] == "#" for row in reversed(rows)] for x in range(width)], dtype=bool)
        return cls(mask.reshape(width, len(rows)))

    @classmethod
    def empty(cls) -> Footprint:
        """A footprint covering no tile, as a Xel'Naga healing shrine's does."""
        return cls(numpy.zeros((0, 0), dtype=bool))

    @classmethod
    def rectangle(cls, width: int, height: int, *, cut_corners: int = 0) -> Footprint:
        """A footprint covering a rectangle `width` tiles across and `height` up, less, at each corner, the tiles
        fewer than `cut_corners` steps from it, counting each step across or up: the corner tile for 1, and the two
        beside it too for 2. Raises `ValueError` for a side shorter than 1 or a negative `cut_corners`."""
        if width < 1 or height < 1 or cut_corners < 0:
            raise ValueError(
                f"a rectangle is at least 1x1 with no negative corner cut, got {width}x{height}, {cut_corners}"
            )
        steps_from_side_x = numpy.minimum(numpy.arange(width), numpy.arange(width)[::-1])[:, numpy.newaxis]
        steps_from_side_y = numpy.minimum(numpy.arange(height), numpy.arange(height)[::-1])[numpy.newaxis, :]
        return cls(steps_from_side_x + steps_from_side_y >= cut_corners)

    @classmethod
    def square(cls, side: int, *, cut_corners: int = 0) -> Footprint:
        """A footprint covering a square `side` tiles across, less its corners as `rectangle` cuts them."""
        return cls.rectangle(side, side, cut_corners=cut_corners)

    @property
    def mask(self) -> ndarray:
        """Whether the footprint covers each tile of its bounding box, indexed `[x, y]` from its lower left tile."""
        return self._mask

    @property
    def width(self) -> int:
        """The number of tiles across its bounding box."""
        return self._mask.shape[0]

    @property
    def height(self) -> int:
        """The number of tiles up its bounding box."""
        return self._mask.shape[1]

    def bounding_box(self, *, at: PointLike) -> Rectangle:
        """The tiles `mask` spans, with the footprint placed at `at` and centered as the game snaps it. The footprint
        covers some of them, or all."""
        x, y = coordinates(at)[:2]
        left, bottom = math.floor(x - self.width / 2 + 0.5), math.floor(y - self.height / 2 + 0.5)
        return Rectangle(left, bottom, self.width, self.height)

    def snapped_center(self, *, at: PointLike) -> Point:
        """Where the footprint centers when placed at `at`, as the game snaps a structure."""
        return self.bounding_box(at=at).center

    def tiles(self, *, at: PointLike) -> TileSet:
        """The tiles the footprint covers, placed at `at` and centered as the game snaps it."""
        box = self.bounding_box(at=at)
        left, bottom = int(box.x), int(box.y)
        return TileSet(Tile(left + int(x), bottom + int(y)) for x, y in zip(*numpy.nonzero(self._mask), strict=True))

    def center_in_tile(self, tile: Tile) -> Point:
        """Where the footprint is centered when its center lies in `tile`: the tile's center along a side of odd
        length, and its lower left corner along a side of even length."""
        return Point((tile[0] + 0.5 * (self.width % 2), tile[1] + 0.5 * (self.height % 2)))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Footprint) and numpy.array_equal(self._mask, other._mask)

    def __hash__(self) -> int:
        return hash((self._mask.shape, self._mask.tobytes()))

    def _made_by(self) -> tuple[str, tuple[int, ...], int] | None:
        """The factory that makes this footprint, `empty`, `square` or `rectangle`, with the sides it takes and the
        corners it cuts; `None` if none does."""
        if not self._mask.size:
            return "empty", (), 0
        width, height = self.width, self.height
        # A cut deeper than the middle tile's steps from the corners leaves no tile.
        for cut_corners in range((width - 1) // 2 + (height - 1) // 2 + 1):
            if self == Footprint.rectangle(width, height, cut_corners=cut_corners):
                sides = (width,) if width == height else (width, height)
                return ("square" if width == height else "rectangle"), sides, cut_corners
        return None

    def __repr__(self) -> str:
        if (made_by := self._made_by()) is None:
            return f"Footprint({self.width}x{self.height}, {numpy.count_nonzero(self._mask)} tiles)"
        factory, sides, cut_corners = made_by
        arguments = [str(side) for side in sides] + ([f"cut_corners={cut_corners}"] if cut_corners else [])
        return f"Footprint.{factory}({', '.join(arguments)})"
