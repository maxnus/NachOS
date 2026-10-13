"""Where an expansion's townhall may stand on a map at the start of a game."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, final

import numpy

from sc2nachos.geometry import Footprint, Grid, Point

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sc2nachos.geometry import PointLike
    from sc2nachos.units import Unit, Units
    from sc2nachos.units._unit_type import UnitType

_TOWNHALL_FOOTPRINT = Footprint.square(5)
_MINERAL_FIELD_SIZE = (2, 1)
_GEYSER_SIZE = (3, 3)
_TOWNHALL_ZONE_MARGIN = 3
# A base's townhall stands at most 5.86 from its resources' mean along an axis (corpus).
_MAX_TOWNHALL_OFFSET_FROM_RESOURCES = 8


# The tiles a resource keeps townhalls off, laid around its center: its footprint grown by `_TOWNHALL_ZONE_MARGIN`
# tiles on every side, less one tile at each corner of a mineral field's and three at a geyser's, the corner and the
# tile beside it on each side (tool `sweep_townhall_placement`).
_MINERAL_FIELD_TOWNHALL_ZONE = Footprint.rectangle(
    _MINERAL_FIELD_SIZE[0] + 2 * _TOWNHALL_ZONE_MARGIN,
    _MINERAL_FIELD_SIZE[1] + 2 * _TOWNHALL_ZONE_MARGIN,
    cut_corners=1,
)
_GEYSER_TOWNHALL_ZONE = Footprint.rectangle(
    _GEYSER_SIZE[0] + 2 * _TOWNHALL_ZONE_MARGIN, _GEYSER_SIZE[1] + 2 * _TOWNHALL_ZONE_MARGIN, cut_corners=2
)


@final
class _ExpansionLocations:
    """Where an expansion's townhall may be centered on a map at the start, with its resources and its other neutral
    units on it.

    A townhall may stand wherever its footprint covers only placeable tiles, none under another neutral unit's
    footprint and none in a resource's zone. A mineral field or a geyser keeps townhalls off its footprint grown by 3
    tiles on every side, with each corner cut, by one tile at a field and by three at a geyser (tool
    `sweep_townhall_placement`).
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
        """The locations on the ground `placement` leaves open, with `mineral_fields`, `geysers` and `blockers` on
        it."""
        townhall_may_cover = placement.copy()
        for unit in blockers:
            if (footprint := unit.footprint) is not None:
                townhall_may_cover.fill_footprint(footprint, at=unit.position, value=False)
        for unit in mineral_fields:
            townhall_may_cover.fill_footprint(_MINERAL_FIELD_TOWNHALL_ZONE, at=unit.position, value=False)
        for unit in geysers:
            townhall_may_cover.fill_footprint(_GEYSER_TOWNHALL_ZONE, at=unit.position, value=False)
        self._allowed = townhall_may_cover.centers_fitting(_TOWNHALL_FOOTPRINT)

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
