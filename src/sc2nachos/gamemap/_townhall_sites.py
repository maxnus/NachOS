"""Where the game takes a townhall on a map at the start of a game."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, final

import numpy
from numpy.lib.stride_tricks import sliding_window_view

from sc2nachos.geometry import Grid, Point

if TYPE_CHECKING:
    from collections.abc import Iterable

    from numpy import ndarray

    from sc2nachos.geometry import PointLike
    from sc2nachos.units import Unit, Units
    from sc2nachos.units._unit_type import UnitType

_TOWNHALL_SIZE = 5
_MINERAL_FIELD_SIZE = (2, 1)
_GEYSER_SIZE = (3, 3)
_TOWNHALL_ZONE_MARGIN = 3
# A base's townhall stands at most 5.86 from its resources' mean along an axis (corpus).
_MAX_TOWNHALL_OFFSET_FROM_RESOURCES = 8


def _townhall_zone(size: tuple[int, int], corner_cut: int) -> ndarray:
    """The tiles a resource of footprint `size` keeps townhalls off, indexed from the zone's lower left tile: its
    footprint grown by `_TOWNHALL_ZONE_MARGIN` tiles on every side, less the tiles fewer than `corner_cut` steps along
    the edges from a corner (tool `sweep_townhall_placement`)."""
    width, height = size[0] + 2 * _TOWNHALL_ZONE_MARGIN, size[1] + 2 * _TOWNHALL_ZONE_MARGIN
    steps_from_side_x = numpy.minimum(numpy.arange(width), numpy.arange(width)[::-1])[:, numpy.newaxis]
    steps_from_side_y = numpy.minimum(numpy.arange(height), numpy.arange(height)[::-1])[numpy.newaxis, :]
    zone = steps_from_side_x + steps_from_side_y >= corner_cut
    zone.flags.writeable = False
    return zone


# A mineral field's zone loses one tile at each corner, and a geyser's three: the corner and the tile beside it on each
# side.
_MINERAL_FIELD_TOWNHALL_ZONE = _townhall_zone(_MINERAL_FIELD_SIZE, corner_cut=1)
_GEYSER_TOWNHALL_ZONE = _townhall_zone(_GEYSER_SIZE, corner_cut=2)


@final
class _TownhallSites:
    """Where the game takes a townhall's center on a map at the start, with its resources and its other neutral units
    on it.

    A townhall may stand wherever its footprint covers only placeable tiles, none under a neutral unit and none in a
    resource's zone. A mineral field or a geyser keeps townhalls off its footprint grown by 3 tiles on every side, with
    each corner cut, by one tile at a field and by three at a geyser (tool `sweep_townhall_placement`).
    """

    __slots__ = ("_allowed",)

    def __init__(
        self,
        placement: Grid[bool],
        *,
        mineral_fields: Units[Unit[UnitType.AnyMineralField]],
        geysers: Units[Unit[UnitType.AnyVespeneGeyser]],
        blockers: Units[Unit[Any]],
    ) -> None:
        """The sites on the ground `placement` leaves open, with `mineral_fields`, `geysers` and `blockers` on it."""
        townhall_may_cover = numpy.array(placement.values)
        for unit in blockers:
            self._mark_unplaceable_under(townhall_may_cover, placement, unit.position, unit.radius)
        for unit in mineral_fields:
            self._mark_zone(townhall_may_cover, placement, unit.position, _MINERAL_FIELD_TOWNHALL_ZONE)
        for unit in geysers:
            self._mark_zone(townhall_may_cover, placement, unit.position, _GEYSER_TOWNHALL_ZONE)
        townhall_fits = sliding_window_view(townhall_may_cover, (_TOWNHALL_SIZE, _TOWNHALL_SIZE)).all(axis=(2, 3))
        allowed = numpy.zeros_like(townhall_may_cover)
        # A window's townhall is centered this many tiles in from its lower left corner.
        center_offset = _TOWNHALL_SIZE // 2
        width, height = townhall_fits.shape
        allowed[center_offset : center_offset + width, center_offset : center_offset + height] = townhall_fits
        self._allowed = Grid(allowed, origin=placement.origin, outside=False, readonly=True)

    def allows(self, center: PointLike) -> bool:
        """Whether the game takes a townhall centered on the tile holding `center`."""
        return bool(self._allowed[center])

    def best_center_for(self, resources: Iterable[Unit[Any]]) -> Point | None:
        """The center the game takes a townhall on with the least summed distance to `resources`, within
        `_MAX_TOWNHALL_OFFSET_FROM_RESOURCES` tiles of their mean along each axis; the first in tile order of those as
        near. `None` if there is none."""
        positions = numpy.array([unit.position for unit in resources], dtype=float).reshape(-1, 2)
        max_offset = _MAX_TOWNHALL_OFFSET_FROM_RESOURCES
        origin = self._allowed.origin
        mean_x, mean_y = positions.mean(axis=0)
        column, row = math.floor(mean_x) - origin[0], math.floor(mean_y) - origin[1]
        x0, y0 = max(column - max_offset, 0), max(row - max_offset, 0)
        searched = self._allowed.values[x0 : column + max_offset + 1, y0 : row + max_offset + 1]
        xs, ys = numpy.nonzero(searched)
        if not len(xs):
            return None
        centers = numpy.stack((xs + x0 + origin[0] + 0.5, ys + y0 + origin[1] + 0.5), axis=-1)
        summed_distances = numpy.linalg.norm(centers[:, numpy.newaxis] - positions[numpy.newaxis], axis=-1).sum(axis=1)
        x, y = centers[int(numpy.argmin(summed_distances))].tolist()
        return Point((x, y))

    @staticmethod
    def _mark_unplaceable_under(
        townhall_may_cover: ndarray, placement: Grid[bool], position: Point, radius: float
    ) -> None:
        """Mark false in `townhall_may_cover`, laid over `placement`, each tile whose center lies within the square a
        unit of `radius` at `position` spans, its sides rounded to whole tiles.

        The placement grid at the start leaves open the ground under Xel'Naga towers, unbuildable plates and bricks,
        and some destructible debris, though the game refuses a townhall on it (tool `sweep_townhall_placement`).
        """
        half_side = round(2 * radius) / 2
        if not half_side:
            return
        origin = placement.origin
        x0 = max(math.ceil(position[0] - half_side - 0.5) - origin[0], 0)
        x1 = min(math.floor(position[0] + half_side - 0.5) - origin[0] + 1, townhall_may_cover.shape[0])
        y0 = max(math.ceil(position[1] - half_side - 0.5) - origin[1], 0)
        y1 = min(math.floor(position[1] + half_side - 0.5) - origin[1] + 1, townhall_may_cover.shape[1])
        townhall_may_cover[x0:x1, y0:y1] = False

    @staticmethod
    def _mark_zone(townhall_may_cover: ndarray, placement: Grid[bool], position: Point, zone: ndarray) -> None:
        """Mark false in `townhall_may_cover`, laid over `placement`, each tile of `zone` around the resource at
        `position`."""
        zone_width, zone_height = zone.shape
        footprint_width = zone_width - 2 * _TOWNHALL_ZONE_MARGIN
        footprint_height = zone_height - 2 * _TOWNHALL_ZONE_MARGIN
        origin = placement.origin
        # The zone's lower left tile, as an index into the grid; the footprint's corners lie on whole coordinates.
        x0 = round(position[0] - footprint_width / 2) - _TOWNHALL_ZONE_MARGIN - origin[0]
        y0 = round(position[1] - footprint_height / 2) - _TOWNHALL_ZONE_MARGIN - origin[1]
        grid_x0, grid_x1 = max(x0, 0), min(x0 + zone_width, townhall_may_cover.shape[0])
        grid_y0, grid_y1 = max(y0, 0), min(y0 + zone_height, townhall_may_cover.shape[1])
        if grid_x0 >= grid_x1 or grid_y0 >= grid_y1:
            return
        in_zone = zone[grid_x0 - x0 : grid_x1 - x0, grid_y0 - y0 : grid_y1 - y0]
        townhall_may_cover[grid_x0:grid_x1, grid_y0:grid_y1] &= ~in_zone
