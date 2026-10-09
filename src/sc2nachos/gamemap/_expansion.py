"""The places to build a townhall, and the steps that find them from the map and its resources at the start."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, final

import numpy
from numpy.lib.stride_tricks import sliding_window_view
from scipy.sparse import coo_array
from scipy.sparse.csgraph import connected_components

from sc2nachos.geometry import Grid, Point
from sc2nachos.units import Units
from sc2nachos.units._unit_type import UnitType

if TYPE_CHECKING:
    from numpy import ndarray

    from sc2nachos.geometry import Tile
    from sc2nachos.units import Unit

# Within a base each mineral field lies at most 3.16 from another, and a geyser at most 5.66 from another resource
# (the base's other geyser); two bases are at least 6.5 apart (corpus).
_MAX_RESOURCE_SPACING_WITHIN_A_BASE = 6.0
# Bases have 6, 7, 8 or 10 fields; the corpus's blockers have 2 and its walls 15.
_MIN_MINERAL_FIELDS_PER_EXPANSION = 6
_MAX_MINERAL_FIELDS_PER_EXPANSION = 10
# A base's townhall stands at most 5.86 from its resources' mean along an axis (corpus).
_MAX_TOWNHALL_OFFSET_FROM_RESOURCES = 8
# A base's own geysers stand 7.0 to 9.22 from its townhall, a far-side geyser 13.0 (corpus).
_MAX_GEYSER_DISTANCE_FROM_TOWNHALL = 11.0
_TOWNHALL_SIZE = 5
_MINERAL_FIELD_SIZE = (2, 1)
_GEYSER_SIZE = (3, 3)
_TOWNHALL_ZONE_MARGIN = 3


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Expansion:
    """A place to build a townhall, with the mineral fields and geysers it mines.

    Each group of resources lying within 6 of one another, through any chain of them, is an expansion if it has 6 to
    10 mineral fields; fewer fields are a blocker to mine out, more a wall. Where a group's fields can be mined from
    either side, with a geyser on each, each side is an expansion of its own, and the two share the fields.
    """

    location: Point
    """Where the townhall goes: of the tile centers the game takes a townhall on, the one with the least summed
    distance to the resources."""
    mineral_fields: Units[Unit[UnitType.AnyMineralField]]
    """Its mineral fields, nearest `location` first. The same units all game, though the game changes their tags as
    they pass in and out of sight."""
    geysers: Units[Unit[UnitType.AnyVespeneGeyser]]
    """Its geysers, nearest `location` first, the same units all game."""
    is_start_location: bool
    """Whether a player starts the game here: this player or an opponent."""
    walking_distance_from_start: float
    """How far a ground unit walks from this player's start to `location`, over the ground as the game began: `inf`
    where no walk reaches."""


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


def _where_townhall_allowed(
    placement: Grid[bool],
    mineral_fields: Units[Unit[UnitType.AnyMineralField]],
    geysers: Units[Unit[UnitType.AnyVespeneGeyser]],
    blockers: Units[Unit[Any]],
) -> ndarray:
    """Whether the game takes a townhall centered on each tile of `placement`, with `mineral_fields`, `geysers` and
    `blockers` on the map and nothing else: where the townhall's footprint covers only placeable tiles, none under a
    blocker and none in a resource's zone."""
    townhall_may_cover = numpy.array(placement.values)
    for unit in blockers:
        _mark_unplaceable_under(townhall_may_cover, placement.origin, unit.position, unit.radius)
    for unit in mineral_fields:
        _mark_townhall_zone(townhall_may_cover, placement.origin, unit.position, _MINERAL_FIELD_TOWNHALL_ZONE)
    for unit in geysers:
        _mark_townhall_zone(townhall_may_cover, placement.origin, unit.position, _GEYSER_TOWNHALL_ZONE)
    townhall_fits = sliding_window_view(townhall_may_cover, (_TOWNHALL_SIZE, _TOWNHALL_SIZE)).all(axis=(2, 3))
    townhall_allowed = numpy.zeros_like(townhall_may_cover)
    # A window's townhall is centered this many tiles in from its lower left corner.
    center_offset = _TOWNHALL_SIZE // 2
    width, height = townhall_fits.shape
    townhall_allowed[center_offset : center_offset + width, center_offset : center_offset + height] = townhall_fits
    return townhall_allowed


def _mark_unplaceable_under(placeable: ndarray, origin: Tile, position: Point, radius: float) -> None:
    """Mark false in `placeable` each tile whose center lies within the square a unit of `radius` at `position` spans,
    its sides rounded to whole tiles.

    The placement grid at the start leaves open the ground under Xel'Naga towers, unbuildable plates and bricks, and
    some destructible debris, though the game refuses a townhall on it (tool `sweep_townhall_placement`).
    """
    half_side = round(2 * radius) / 2
    if not half_side:
        return
    x0 = max(math.ceil(position[0] - half_side - 0.5) - origin[0], 0)
    x1 = min(math.floor(position[0] + half_side - 0.5) - origin[0] + 1, placeable.shape[0])
    y0 = max(math.ceil(position[1] - half_side - 0.5) - origin[1], 0)
    y1 = min(math.floor(position[1] + half_side - 0.5) - origin[1] + 1, placeable.shape[1])
    placeable[x0:x1, y0:y1] = False


def _mark_townhall_zone(townhall_may_cover: ndarray, origin: Tile, position: Point, zone: ndarray) -> None:
    """Mark false in `townhall_may_cover` each tile of `zone` around the resource at `position`."""
    zone_width, zone_height = zone.shape
    footprint_width, footprint_height = zone_width - 2 * _TOWNHALL_ZONE_MARGIN, zone_height - 2 * _TOWNHALL_ZONE_MARGIN
    # The zone's lower left tile, as an index into the grid; the footprint's corners lie on whole coordinates.
    x0 = round(position[0] - footprint_width / 2) - _TOWNHALL_ZONE_MARGIN - origin[0]
    y0 = round(position[1] - footprint_height / 2) - _TOWNHALL_ZONE_MARGIN - origin[1]
    grid_x0, grid_x1 = max(x0, 0), min(x0 + zone_width, townhall_may_cover.shape[0])
    grid_y0, grid_y1 = max(y0, 0), min(y0 + zone_height, townhall_may_cover.shape[1])
    if grid_x0 >= grid_x1 or grid_y0 >= grid_y1:
        return
    in_zone = zone[grid_x0 - x0 : grid_x1 - x0, grid_y0 - y0 : grid_y1 - y0]
    townhall_may_cover[grid_x0:grid_x1, grid_y0:grid_y1] &= ~in_zone


def _resources_of_each_base(
    mineral_fields: Units[Unit[UnitType.AnyMineralField]], geysers: Units[Unit[UnitType.AnyVespeneGeyser]]
) -> list[tuple[Units[Unit[UnitType.AnyMineralField]], Units[Unit[UnitType.AnyVespeneGeyser]]]]:
    """The mineral fields and geysers of each base: each group of resources lying within
    `_MAX_RESOURCE_SPACING_WITHIN_A_BASE` of one another, through any chain of them, with
    `_MIN_MINERAL_FIELDS_PER_EXPANSION` to `_MAX_MINERAL_FIELDS_PER_EXPANSION` mineral fields."""
    field_count = len(mineral_fields)
    bases = []
    for group_indices in _resource_groups(_resource_positions(mineral_fields, geysers)):
        group_fields = Units(mineral_fields[index] for index in group_indices if index < field_count)
        group_geysers = Units(geysers[index - field_count] for index in group_indices if index >= field_count)
        if _MIN_MINERAL_FIELDS_PER_EXPANSION <= len(group_fields) <= _MAX_MINERAL_FIELDS_PER_EXPANSION:
            bases.append((group_fields, group_geysers))
    return bases


def _resource_positions(
    mineral_fields: Units[Unit[UnitType.AnyMineralField]], geysers: Units[Unit[UnitType.AnyVespeneGeyser]]
) -> ndarray:
    """The positions of `mineral_fields` and then `geysers`, one row each."""
    return numpy.array([unit.position for unit in (*mineral_fields, *geysers)], dtype=float).reshape(-1, 2)


def _resource_groups(positions: ndarray) -> list[list[int]]:
    """The indices into `positions` of each group of resources lying within `_MAX_RESOURCE_SPACING_WITHIN_A_BASE` of
    one another, through any chain of them."""
    if not len(positions):
        return []
    distances = numpy.linalg.norm(positions[:, numpy.newaxis] - positions[numpy.newaxis], axis=-1)
    close_pairs = numpy.nonzero(distances <= _MAX_RESOURCE_SPACING_WITHIN_A_BASE)
    closeness = coo_array((numpy.ones(len(close_pairs[0])), close_pairs), shape=(len(positions), len(positions)))
    group_count, group_of_resource = connected_components(closeness, directed=False)
    return [numpy.flatnonzero(group_of_resource == group).tolist() for group in range(group_count)]


def _townhall_locations_of_base(
    townhall_allowed: ndarray,
    origin: Tile,
    fields: Units[Unit[UnitType.AnyMineralField]],
    geysers: Units[Unit[UnitType.AnyVespeneGeyser]],
) -> list[tuple[Point, Units[Unit[UnitType.AnyVespeneGeyser]]]]:
    """Where a townhall can mine one base's `fields` and `geysers`, each location with the geysers it mines: one, and
    one more for each geyser too far from it, on the far side of the fields. Every location mines all of `fields`."""
    location = _best_townhall_location(townhall_allowed, origin, _resource_positions(fields, geysers))
    if location is None:
        return []
    near_geysers: list[Unit[UnitType.AnyVespeneGeyser]] = []
    far_side_locations: list[tuple[Point, Units[Unit[UnitType.AnyVespeneGeyser]]]] = []
    for geyser in geysers:
        far_side_location = None
        if geyser.position.distance_to(location) > _MAX_GEYSER_DISTANCE_FROM_TOWNHALL:
            positions = _resource_positions(fields, Units([geyser]))
            far_side_location = _best_townhall_location(townhall_allowed, origin, positions)
        if far_side_location is None or far_side_location == location:
            near_geysers.append(geyser)
        else:
            far_side_locations.append((far_side_location, Units([geyser])))
    return [(location, Units(near_geysers)), *far_side_locations]


def _best_townhall_location(townhall_allowed: ndarray, origin: Tile, positions: ndarray) -> Point | None:
    """The center within `_MAX_TOWNHALL_OFFSET_FROM_RESOURCES` tiles of the mean of resources at `positions`, along
    each axis, where `townhall_allowed` takes a townhall, with the least summed distance to them; the first in tile
    order of those as near. `None` if there is none."""
    max_offset = _MAX_TOWNHALL_OFFSET_FROM_RESOURCES
    mean_x, mean_y = positions.mean(axis=0)
    column, row = math.floor(mean_x) - origin[0], math.floor(mean_y) - origin[1]
    x0, y0 = max(column - max_offset, 0), max(row - max_offset, 0)
    searched = townhall_allowed[x0 : column + max_offset + 1, y0 : row + max_offset + 1]
    xs, ys = numpy.nonzero(searched)
    if not len(xs):
        return None
    centers = numpy.stack((xs + x0 + origin[0] + 0.5, ys + y0 + origin[1] + 0.5), axis=-1)
    summed_distances = numpy.linalg.norm(centers[:, numpy.newaxis] - positions[numpy.newaxis], axis=-1).sum(axis=1)
    x, y = centers[int(numpy.argmin(summed_distances))].tolist()
    return Point((x, y))


def _walking_distance_from_start(pathing: Grid[bool], start: Point) -> Grid[float]:
    """How far a ground unit walks from `start` to each tile of `pathing`, where this player's first townhall stands
    and blocks the ground under it."""
    walkable = numpy.array(pathing.values)
    x, y = pathing.index_of(start)
    townhall_half_width = _TOWNHALL_SIZE // 2
    footprint_xs = slice(max(x - townhall_half_width, 0), x + townhall_half_width + 1)
    footprint_ys = slice(max(y - townhall_half_width, 0), y + townhall_half_width + 1)
    walkable[footprint_xs, footprint_ys] = True
    return Grid(walkable, origin=pathing.origin).path_distance_from(start)
