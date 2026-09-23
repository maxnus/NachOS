"""The map a game is played on."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, final

import numpy

from sc2nachos.gamemap._image_data import image_array, image_tiles
from sc2nachos.gamemap._ramp import Ramp, find_ramps
from sc2nachos.geometry import Grid, Point, Rectangle, Tile
from sc2nachos.geometry._point import coordinates

if TYPE_CHECKING:
    from numpy import ndarray
    from s2clientprotocol import common_pb2, sc2api_pb2

    from sc2nachos.geometry import PointLike

# Corners of one tile at least this far apart in height are on opposite sides of a cliff: across a ramp they differ
# by at most 1.25, across a cliff by at least 2.
_CLIFF = 1.0


@final
class GameMap:
    """The map, as the game describes it at the start. None of it changes.

    The grids cover the playable area only, so a grid's `values` are indexed from the playable area's lower left
    corner. Beyond it, pathing and placement read `False` and height raises. The grids are shared by everything that
    reads the map, so they refuse writes: `copy()` one to change it.
    """

    __slots__ = (
        "_corners",
        "_height",
        "_name",
        "_opponent_start_locations",
        "_pathing",
        "_placement",
        "_playable",
        "_ramps",
    )

    def __init__(self, info: sc2api_pb2.ResponseGameInfo) -> None:
        """Read the map from the game's answer to `RequestGameInfo`."""
        start = info.start_raw
        playable = Rectangle._from_proto(start.playable_area)
        origin = Tile(start.playable_area.p0.x, start.playable_area.p0.y)
        self._name = info.map_name
        self._playable = playable
        pathing = image_tiles(start.pathing_grid, playable) != 0
        placement = image_tiles(start.placement_grid, playable) != 0
        self._pathing = Grid(pathing, origin=origin, outside=False, readonly=True)
        self._placement = Grid(placement, origin=origin, outside=False, readonly=True)
        self._corners = _tile_corners(_corner_heights(start.terrain_height, playable))
        self._height = Grid(self._corners.mean(axis=-1), origin=origin, readonly=True)
        self._opponent_start_locations = tuple(Point._from_proto(location) for location in start.start_locations)
        self._ramps = find_ramps(self._pathing, self._placement, self._height)

    @property
    def name(self) -> str:
        """The map's display name, such as `Pylon AIE`."""
        return self._name

    @property
    def playable_area(self) -> Rectangle:
        """The part of the map a unit can be in."""
        return self._playable

    @property
    def pathing(self) -> Grid[bool]:
        """Where a ground unit can walk at the start of the game.

        Rocks block it, as do this player's own starting townhall, mineral fields and geysers, but no other resources.
        """
        return self._pathing

    @property
    def placement(self) -> Grid[bool]:
        """Where a structure can be placed at the start of the game. Rocks block it; no resource or townhall does."""
        return self._placement

    @property
    def height(self) -> Grid[float]:
        """The ground height at each tile's center, in the units of a unit's `z`.

        A unit at a tile's center stands within 0.05 of it nine times in ten; `height_at` is closer for any other
        point. Some maps raise their ramps above the heights the game sends, by as much as 0.76.
        """
        return self._height

    def height_at(self, point: PointLike) -> float:
        """The ground height at `point`, interpolated between the corners of its tile.

        A unit standing at `point` is within 0.03 of it nine times in ten, but as far off as `height` is on a map that
        raises its ramps. Raises `IndexError` outside the playable area.
        """
        position = coordinates(point)
        x, y = position[0], position[1]
        column, row = math.floor(x), math.floor(y)
        origin = self._height.origin
        i, j = column - origin[0], row - origin[1]
        if not (0 <= i < self._corners.shape[0] and 0 <= j < self._corners.shape[1]):
            raise IndexError(f"{Tile(column, row)!r} lies outside {self._playable!r}")
        low_left, low_right, up_left, up_right = self._corners[i, j].tolist()
        dx, dy = x - column, y - row
        return (
            low_left
            + (low_right - low_left) * dx
            + (up_left - low_left) * dy
            + (low_left - low_right - up_left + up_right) * dx * dy
        )

    @property
    def ramps(self) -> tuple[Ramp, ...]:
        """Every ramp on the map, ordered by lower left tile."""
        return self._ramps

    @property
    def opponent_start_locations(self) -> tuple[Point, ...]:
        """Every start location on the map but this player's own.

        Each is a townhall's position, which is a tile's center, so `Tile.containing` reads it exactly.
        """
        return self._opponent_start_locations


def _corner_heights(image: common_pb2.ImageData, area: Rectangle) -> ndarray:
    """The height at every tile corner of `area`: one more across and up than there are tiles.

    The game gives the height at each tile's lower left corner, not its center, an eighth of a unit to a byte with
    127 at zero.
    """
    xs, ys = area.tile_range()
    corners = image_array(image, area)[xs.start : xs.stop + 1, ys.start : ys.stop + 1]
    # An area reaching the far edge of the image has no corners beyond it; the edge's own corners stand in.
    missing = (len(xs) + 1 - corners.shape[0], len(ys) + 1 - corners.shape[1])
    corners = numpy.pad(corners, ((0, missing[0]), (0, missing[1])), mode="edge")
    return (corners.astype(float) - 127) / 8


def _tile_corners(heights: ndarray) -> ndarray:
    """The heights of each tile's lower left, lower right, upper left and upper right corners, at the tile's own level.

    A corner on a cliff line has one height, which is the wrong level for the tiles on the other side. A tile stands
    on the side most of its corners are on, and on the upper side when they split two and two (in game). Its corners
    on the far side take the mean height of those on its own side.
    """
    corners = numpy.stack((heights[:-1, :-1], heights[1:, :-1], heights[:-1, 1:], heights[1:, 1:]), axis=-1)
    ordered = numpy.sort(corners, axis=-1)
    cliff = ordered[..., 3] - ordered[..., 0] >= _CLIFF
    # The widest step between the sorted corner heights separates the two levels.
    step = numpy.argmax(numpy.diff(ordered, axis=-1), axis=-1)
    below = numpy.take_along_axis(ordered, step[..., numpy.newaxis], axis=-1)
    # A split after the first or second corner puts the tile on the upper side; after the third, on the lower.
    upper = (step < 2)[..., numpy.newaxis]
    own_side = numpy.where(upper, corners > below, corners <= below) | ~cliff[..., numpy.newaxis]
    side_height = (corners * own_side).sum(axis=-1) / own_side.sum(axis=-1)
    return numpy.where(own_side, corners, side_height[..., numpy.newaxis])
