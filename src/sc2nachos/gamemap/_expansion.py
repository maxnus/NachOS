"""The places to build a townhall, found from the map and its resources at the start of a game."""

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

    from sc2nachos.gamemap._game_map import GameMap
    from sc2nachos.geometry import Tile
    from sc2nachos.units import Unit

_MAX_DISTANCE_BETWEEN_GROUPED_RESOURCES = 6.0
# Fewer is a blocker to mine out, more a wall (corpus).
_MIN_RESOURCES_PER_EXPANSION = 5
_MAX_RESOURCES_PER_EXPANSION = 12
_TOWNHALL_SEARCH_RADIUS = 12
# A geyser farther away is mined from the other side of the fields (corpus).
_MAX_GEYSER_DISTANCE_FROM_TOWNHALL = 10.0
_TOWNHALL_SIZE = 5
_MINERAL_FIELD_SIZE = (2, 1)
_GEYSER_SIZE = (3, 3)
_MIN_GAP_FROM_TOWNHALL_TO_RESOURCE = 3


@final
@dataclass(frozen=True, slots=True)
class Expansion:
    """A place to build a townhall, with the mineral fields and geysers it mines.

    A group of 5 to 12 resources lying within 6 of one another is an expansion; fewer is a blocker to mine out, more a
    wall. Where a group's fields can be mined from either side, with a geyser on each, each side is an expansion of its
    own, and the two share the fields.
    """

    location: Point
    """Where the townhall goes: of the tile centers the game takes a townhall on, the one with the least summed
    distance to the resources."""
    mineral_fields: Units[Unit[UnitType.AnyMineralField]]
    """Its mineral fields, nearest `location` first. The same units all game, though the game changes their tags as
    they pass in and out of sight."""
    geysers: Units[Unit[UnitType.AnyVespeneGeyser]]
    """Its geysers, nearest `location` first, the same units all game."""


def _find_expansions(game_map: GameMap, neutral_units: Units[Unit[Any]]) -> tuple[Expansion, ...]:
    """The expansions of `game_map` with `neutral_units` on it at the start, nearest this player's start first by the
    ground a unit walks, so the start comes first. Those no walk reaches come last, nearest first in a line.

    The mineral fields and geysers make the expansions; every other neutral unit blocks the ground under it.
    """
    mineral_fields = neutral_units.of_type(UnitType.AnyMineralField)
    geysers = neutral_units.of_type(UnitType.AnyVespeneGeyser)
    blockers = neutral_units.excluding_type(UnitType.AnyMineralField, UnitType.AnyVespeneGeyser)
    townhall_allowed = _where_townhall_allowed(game_map.placement, mineral_fields, geysers, blockers)
    origin = game_map.placement.origin
    field_count = len(mineral_fields)
    expansions: list[Expansion] = []
    for group_indices in _resource_groups(_resource_positions(mineral_fields, geysers)):
        if _MIN_RESOURCES_PER_EXPANSION <= len(group_indices) <= _MAX_RESOURCES_PER_EXPANSION:
            group_fields = Units(mineral_fields[index] for index in group_indices if index < field_count)
            group_geysers = Units(geysers[index - field_count] for index in group_indices if index >= field_count)
            expansions += _expansions_of_resource_group(townhall_allowed, origin, group_fields, group_geysers)
    return _sorted_by_walking_distance(expansions, game_map)


def _resource_positions(
    mineral_fields: Units[Unit[UnitType.AnyMineralField]], geysers: Units[Unit[UnitType.AnyVespeneGeyser]]
) -> ndarray:
    """The positions of `mineral_fields` and then `geysers`, one row each."""
    return numpy.array([unit.position for unit in (*mineral_fields, *geysers)], dtype=float).reshape(-1, 2)


def _where_townhall_allowed(
    placement: Grid[bool],
    mineral_fields: Units[Unit[UnitType.AnyMineralField]],
    geysers: Units[Unit[UnitType.AnyVespeneGeyser]],
    blockers: Units[Unit[Any]],
) -> ndarray:
    """Whether the game takes a townhall centered on each tile of `placement`, with `mineral_fields`, `geysers` and
    `blockers` on the map and nothing else."""
    placeable = numpy.array(placement.values)
    for unit in blockers:
        _mark_unplaceable_under(placeable, placement.origin, unit.position, unit.radius)
    townhall_allowed = numpy.zeros_like(placeable)
    townhall_half_width = _TOWNHALL_SIZE // 2
    townhall_fits = sliding_window_view(placeable, (_TOWNHALL_SIZE, _TOWNHALL_SIZE)).all(axis=(2, 3))
    townhall_allowed[
        townhall_half_width : townhall_half_width + townhall_fits.shape[0],
        townhall_half_width : townhall_half_width + townhall_fits.shape[1],
    ] = townhall_fits
    for unit in mineral_fields:
        _disallow_townhalls_near_resource(townhall_allowed, placement.origin, unit.position, _MINERAL_FIELD_SIZE)
    for unit in geysers:
        _disallow_townhalls_near_resource(townhall_allowed, placement.origin, unit.position, _GEYSER_SIZE)
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


def _disallow_townhalls_near_resource(
    townhall_allowed: ndarray, origin: Tile, position: Point, size: tuple[int, int]
) -> None:
    """Mark false in `townhall_allowed` each townhall center a resource of footprint `size` at `position` refuses.

    Apart, the two footprints leave a gap along each axis. A mineral field refuses a townhall while both gaps are
    under 3 tiles, unless both are 2; a geyser, while the two gaps add up to under 3 (tool
    `sweep_townhall_placement`).
    """
    refusal_reach = _MIN_GAP_FROM_TOWNHALL_TO_RESOURCE + (_TOWNHALL_SIZE + max(size)) // 2 + 1
    column, row = math.floor(position[0]) - origin[0], math.floor(position[1]) - origin[1]
    xs = numpy.arange(max(column - refusal_reach, 0), min(column + refusal_reach + 1, townhall_allowed.shape[0]))
    ys = numpy.arange(max(row - refusal_reach, 0), min(row + refusal_reach + 1, townhall_allowed.shape[1]))
    if not len(xs) or not len(ys):
        return
    centers_x = (xs + origin[0] + 0.5)[:, numpy.newaxis]
    centers_y = (ys + origin[1] + 0.5)[numpy.newaxis, :]
    gap_x = numpy.maximum(numpy.abs(centers_x - position[0]) - (_TOWNHALL_SIZE + size[0]) / 2, 0.0)
    gap_y = numpy.maximum(numpy.abs(centers_y - position[1]) - (_TOWNHALL_SIZE + size[1]) / 2, 0.0)
    min_gap = _MIN_GAP_FROM_TOWNHALL_TO_RESOURCE
    if size == _GEYSER_SIZE:
        refused = gap_x + gap_y < min_gap
    else:
        corner = (gap_x == min_gap - 1) & (gap_y == min_gap - 1)
        refused = (gap_x < min_gap) & (gap_y < min_gap) & ~corner
    townhall_allowed[xs[0] : xs[-1] + 1, ys[0] : ys[-1] + 1] &= ~refused


def _resource_groups(positions: ndarray) -> list[list[int]]:
    """The indices into `positions` of each group of resources lying within
    `_MAX_DISTANCE_BETWEEN_GROUPED_RESOURCES` of one another, through any chain of them."""
    if not len(positions):
        return []
    distances = numpy.linalg.norm(positions[:, numpy.newaxis] - positions[numpy.newaxis], axis=-1)
    close_pairs = numpy.nonzero(distances <= _MAX_DISTANCE_BETWEEN_GROUPED_RESOURCES)
    closeness = coo_array((numpy.ones(len(close_pairs[0])), close_pairs), shape=(len(positions), len(positions)))
    group_count, group_of_resource = connected_components(closeness, directed=False)
    return [numpy.flatnonzero(group_of_resource == group).tolist() for group in range(group_count)]


def _expansions_of_resource_group(
    townhall_allowed: ndarray,
    origin: Tile,
    fields: Units[Unit[UnitType.AnyMineralField]],
    geysers: Units[Unit[UnitType.AnyVespeneGeyser]],
) -> list[Expansion]:
    """The expansions mining one group of resources: one, or one more for each geyser too far from it to share it."""
    location = _best_townhall_location(townhall_allowed, origin, _resource_positions(fields, geysers))
    if location is None:
        return []
    near_geysers: list[Unit[UnitType.AnyVespeneGeyser]] = []
    far_side_expansions: list[Expansion] = []
    for geyser in geysers:
        far_side_location = None
        if geyser.position.distance_to(location) > _MAX_GEYSER_DISTANCE_FROM_TOWNHALL:
            positions = _resource_positions(fields, Units([geyser]))
            far_side_location = _best_townhall_location(townhall_allowed, origin, positions)
        if far_side_location is None or far_side_location == location:
            near_geysers.append(geyser)
        else:
            far_side_expansions.append(_expansion_nearest_first(far_side_location, fields, Units([geyser])))
    return [_expansion_nearest_first(location, fields, Units(near_geysers)), *far_side_expansions]


def _best_townhall_location(townhall_allowed: ndarray, origin: Tile, positions: ndarray) -> Point | None:
    """The center within `_TOWNHALL_SEARCH_RADIUS` tiles of the mean of resources at `positions` where
    `townhall_allowed` takes a townhall, with the least summed distance to them; the first in tile order of those as
    near. `None` if there is none."""
    mean_x, mean_y = positions.mean(axis=0)
    column, row = math.floor(mean_x) - origin[0], math.floor(mean_y) - origin[1]
    x0, y0 = max(column - _TOWNHALL_SEARCH_RADIUS, 0), max(row - _TOWNHALL_SEARCH_RADIUS, 0)
    searched = townhall_allowed[x0 : column + _TOWNHALL_SEARCH_RADIUS + 1, y0 : row + _TOWNHALL_SEARCH_RADIUS + 1]
    xs, ys = numpy.nonzero(searched)
    if not len(xs):
        return None
    centers = numpy.stack((xs + x0 + origin[0] + 0.5, ys + y0 + origin[1] + 0.5), axis=-1)
    summed_distances = numpy.linalg.norm(centers[:, numpy.newaxis] - positions[numpy.newaxis], axis=-1).sum(axis=1)
    x, y = centers[int(numpy.argmin(summed_distances))].tolist()
    return Point((x, y))


def _expansion_nearest_first(
    location: Point,
    fields: Units[Unit[UnitType.AnyMineralField]],
    geysers: Units[Unit[UnitType.AnyVespeneGeyser]],
) -> Expansion:
    """The expansion at `location`, its resources nearest it first."""
    return Expansion(location, fields.sorted_by_distance_to(location), geysers.sorted_by_distance_to(location))


def _sorted_by_walking_distance(expansions: list[Expansion], game_map: GameMap) -> tuple[Expansion, ...]:
    """`expansions` nearest this player's start first by the ground a unit walks, then those no walk reaches, nearest
    first in a line."""
    start = game_map.start_location
    walkable = numpy.array(game_map.pathing.values)
    # The start's own townhall blocks the ground under it.
    x, y = game_map.pathing.index_of(start)
    townhall_half_width = _TOWNHALL_SIZE // 2
    walkable[
        max(x - townhall_half_width, 0) : x + townhall_half_width + 1,
        max(y - townhall_half_width, 0) : y + townhall_half_width + 1,
    ] = True
    walking_distance = Grid(walkable, origin=game_map.pathing.origin).path_distance_from(start)
    return tuple(
        sorted(
            expansions,
            key=lambda expansion: (walking_distance[expansion.location], start.distance_to(expansion.location)),
        )
    )
