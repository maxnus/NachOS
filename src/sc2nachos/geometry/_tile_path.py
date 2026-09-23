"""The route a pathfinder returns: an ordered walk of tiles and the distance along it."""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence
from typing import TYPE_CHECKING, cast, final, overload

from sc2nachos.geometry._shapes import Tile

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sc2nachos.geometry._point import PointLike


@final
class TilePath(Sequence[Tile]):
    """An ordered walk of tiles.

    A path may be empty. An empty path is falsy, and `start` and `end` raise `ValueError` on it.
    """

    __slots__ = ("_distance", "_tiles")

    _tiles: tuple[Tile, ...]
    _distance: float | None

    def __init__(self, tiles: Iterable[Tile], distance: float | None = None) -> None:
        """A path along `tiles`, `distance` game units end to end, measured on first use if not given."""
        self._tiles = tuple(tiles)
        if distance is not None and (not math.isfinite(distance) or distance < 0):
            raise ValueError(f"a path cannot be {distance} game units long")
        self._distance = distance

    @property
    def distance(self) -> float:
        """The length from the first tile to the last, center to center."""
        if self._distance is None:
            self._distance = math.fsum(
                one.center.distance_to(following.center) for one, following in itertools.pairwise(self._tiles)
            )
        return self._distance

    @property
    def start(self) -> Tile:
        """The first tile."""
        if not self._tiles:
            raise ValueError("an empty path has no start")
        return self._tiles[0]

    @property
    def end(self) -> Tile:
        """The last tile."""
        if not self._tiles:
            raise ValueError("an empty path has no end")
        return self._tiles[-1]

    @overload
    def __getitem__(self, index: int) -> Tile: ...

    @overload
    def __getitem__(self, index: slice) -> TilePath: ...

    def __getitem__(self, index: int | slice) -> Tile | TilePath:
        """The tile at `index`, or the stretch a slice selects.

        A stretch's distance is measured afresh, as straight lines between the tiles it keeps.
        """
        if isinstance(index, slice):
            return TilePath(self._tiles[index])
        return self._tiles[index]

    def __len__(self) -> int:
        return len(self._tiles)

    def __contains__(self, point: object) -> bool:
        """Whether the path covers the tile holding `point`. Raises `TypeError` on anything but a point.

        This scans the path; build a `TileSet` from it to test many points.
        """
        return Tile.containing(cast("PointLike | Tile", point)) in self._tiles

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TilePath):
            return NotImplemented
        return self._tiles == other._tiles

    def __hash__(self) -> int:
        return hash(self._tiles)

    def __repr__(self) -> str:
        if not self._tiles:
            return "TilePath(empty)"
        return f"TilePath({self.start!r} to {self.end!r}, {len(self._tiles)} tiles, {self.distance:.2f} long)"
