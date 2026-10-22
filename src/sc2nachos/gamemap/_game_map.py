"""The map a game is played on."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Self, final

import numpy
from s2clientprotocol import raw_pb2

from sc2nachos.gamedata._footprints import FOOTPRINTS
from sc2nachos.gamemap._image_data import image_array, image_tiles
from sc2nachos.gamemap._ramp import _HALF_A_LEVEL, Ramp, find_ramps
from sc2nachos.geometry import Grid, Point, Rectangle, Tile
from sc2nachos.ids import UnitTypeId
from sc2nachos.units._unit_type import UnitType

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
        "_all_start_locations",
        "_corners",
        "_height",
        "_main_ramps_by_start_tile",
        "_name",
        "_pathing",
        "_placement",
        "_playable",
        "_ramps",
    )

    def __init__(self, info: sc2api_pb2.ResponseGameInfo, *, start_location: Point) -> None:
        """Read the map from the game's answer to `RequestGameInfo`, with this player's start at `start_location`."""
        start = info.start_raw
        playable = Rectangle._from_proto(start.playable_area)
        origin = Tile(start.playable_area.p0.x, start.playable_area.p0.y)
        self._name = info.map_name
        self._playable = playable
        placement = image_tiles(start.placement_grid, playable) != 0
        self._pathing = _create_pathing_grid(start)
        self._placement = Grid(placement, origin=origin, outside=False, readonly=True)
        self._corners = _tile_corners(_corner_heights(start.terrain_height, playable))
        self._height = Grid(self._corners.mean(axis=-1), origin=origin, readonly=True)
        opponent_start_locations = (Point._from_proto(location) for location in start.start_locations)
        self._all_start_locations = (start_location, *opponent_start_locations)
        self._ramps = find_ramps(self._pathing, self._placement, self._height)
        self._main_ramps_by_start_tile = {
            Tile.at(start): self._find_main_ramp(start) for start in self._all_start_locations
        }

    @classmethod
    def _from_game(cls, info: sc2api_pb2.ResponseGameInfo, observation: sc2api_pb2.ResponseObservation) -> Self:
        """The map of a game whose first observation is `observation`: this player starts where its townhall stands
        in it, or in the middle of the playable area on a map that gives it none."""
        townhalls = (
            unit
            for unit in observation.observation.raw_data.units
            if unit.alliance == raw_pb2.Alliance.Self and unit.unit_type in UnitType.Townhall._type_ids
        )
        townhall = next(townhalls, None)
        if townhall is None:
            start = Rectangle._from_proto(info.start_raw.playable_area).center
        else:
            start = Point((townhall.pos.x, townhall.pos.y))
        return cls(info, start_location=start)

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
        """Where the game's placement grid lets a structure stand at the start of the game.

        The game's grid reads the ground beneath every unit, so it blocks no resource, townhall, rock or tower, though
        no structure can stand on one.
        """
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
        x, y = point[0], point[1]
        column, row = math.floor(x), math.floor(y)
        origin = self._height.origin
        i, j = column - origin.x, row - origin.y
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
    def main_ramp(self) -> Ramp | None:
        """The ramp out of this player's main, as `main_ramp_of(start_location)` says."""
        return self._main_ramps_by_start_tile[Tile.at(self.start_location)]

    def main_ramp_of(self, start: PointLike) -> Ramp | None:
        """The ramp out of the main at the start location `start`, such as an opponent's: of the ramps whose top is at
        `start`'s level, within half a level of the height at its tile, the one whose center is the shortest walk from
        `start`, and of two as short, the first in tile order. The walk is over the pathing grid with the ground a
        townhall at `start` covers opened, since the start's own townhall closes it. `None` if no such ramp can be
        walked to. Raises `ValueError` for a point on no start location's tile.
        """
        start_tile = Tile.at(start)
        if start_tile not in self._main_ramps_by_start_tile:
            raise ValueError(f"{start} is on no start location's tile: {self._all_start_locations}")
        return self._main_ramps_by_start_tile[start_tile]

    def _find_main_ramp(self, start: Point) -> Ramp | None:
        """The ramp out of the main at the start location `start`, as `main_ramp_of` says; `None` for a start beyond the
        playable area, which only a map drawn for a test has."""
        if start not in self._playable:
            return None
        walkable_ground = self._pathing.copy()
        # Every townhall's footprint is the command center's.
        walkable_ground.fill_footprint(FOOTPRINTS[UnitTypeId.COMMAND_CENTER], at=start, value=True)
        walking_distances = walkable_ground.cheapest_paths_from(start).distances
        start_level = self._height[start]
        at_its_level = [ramp for ramp in self._ramps if abs(self._height[min(ramp.top)] - start_level) < _HALF_A_LEVEL]
        nearest_ramp = min(at_its_level, key=lambda ramp: walking_distances[ramp.tiles.center], default=None)
        if nearest_ramp is None or walking_distances[nearest_ramp.tiles.center] == math.inf:
            return None
        return nearest_ramp

    @property
    def start_location(self) -> Point:
        """Where this player's first townhall stood when the game began, a tile's center. It stays the same all game."""
        return self._all_start_locations[0]

    @property
    def all_start_locations(self) -> tuple[Point, ...]:
        """Every start location on the map: this player's first, then the opponents' in the order the game names
        them."""
        return self._all_start_locations

    @property
    def opponent_start_locations(self) -> tuple[Point, ...]:
        """Every start location on the map but this player's own."""
        return self._all_start_locations[1:]


def _create_pathing_grid(start: raw_pb2.StartRaw) -> Grid[bool]:
    """Where `start`'s pathing grid lets a ground unit walk over the playable area, as a read-only grid that reads false
    beyond it."""
    playable = Rectangle._from_proto(start.playable_area)
    origin = Tile(start.playable_area.p0.x, start.playable_area.p0.y)
    return Grid(image_tiles(start.pathing_grid, playable) != 0, origin=origin, outside=False, readonly=True)


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
