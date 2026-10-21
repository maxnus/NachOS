"""The places to build a townhall, and the steps that find them from the map and its resources at the start."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Self, final

import numpy
from scipy.sparse import coo_array
from scipy.sparse.csgraph import connected_components

from sc2nachos.gamemap._expansion_locations import _ExpansionLocations
from sc2nachos.geometry import Grid, Point
from sc2nachos.units import Units
from sc2nachos.units._unit_type import UnitType

if TYPE_CHECKING:
    from numpy import ndarray

    from sc2nachos.gamemap._game_map import GameMap
    from sc2nachos.units import Unit
    from sc2nachos.units._tracking._unit_tracker import _UnitTracker

# Within a base each mineral field lies at most 3.16 from another, and a geyser at most 5.66 from another resource
# (the base's other geyser); two bases are at least 6.5 apart (corpus).
_MAX_RESOURCE_SPACING_WITHIN_A_BASE = 6.0
# Bases have 6, 7, 8 or 10 fields; the corpus's blockers have 2 and its walls 15.
_MIN_MINERAL_FIELDS_PER_EXPANSION = 6
_MAX_MINERAL_FIELDS_PER_EXPANSION = 10
# A base's own geysers stand 7.0 to 9.22 from its townhall, a far-side geyser 13.0 (corpus).
_MAX_GEYSER_DISTANCE_FROM_TOWNHALL = 11.0
_TOWNHALL_SIZE = 5


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Expansion:
    """A place to build a townhall, with the mineral fields and geysers it mines.

    Each group of resources lying within 6 of one another, through any chain of them, is an expansion if it has 6 to
    10 mineral fields; fewer fields are a blocker to mine out, more a wall. Where a group's fields can be mined from
    either side, with a geyser on each, each side is an expansion of its own, and the two share the fields.
    """

    center: Point
    """Where the townhall's center goes: of the tile centers the game takes a townhall on, the one with the least summed
    distance to the resources."""
    mineral_fields: Units[Unit[UnitType.AnyMineralField]]
    """Its mineral fields, nearest `center` first. The same units all game, though the game changes their tags as
    they pass in and out of sight."""
    geysers: Units[Unit[UnitType.AnyVespeneGeyser]]
    """Its geysers, nearest `center` first, the same units all game."""
    is_start_location: bool
    """Whether a player starts the game here: this player or an opponent."""
    pathing_distance_from_start: float
    """The pathing distance from this player's start to `center`, over the ground as the game began: `inf` where no
    path reaches."""
    _unit_tracker: _UnitTracker = field(repr=False, compare=False)

    @property
    def townhall(self) -> Unit[UnitType.Townhall] | None:
        """The townhall standing on it now, this player's or another's, in sight or remembered in the fog: the one whose
        footprint's bounding box holds `center`. `None` if there is none; a townhall in the air stands nowhere."""
        for townhall in self._unit_tracker.present_townhalls:
            footprint = townhall.footprint
            if footprint is not None and self.center in footprint.bounding_box(at=townhall.position):
                return townhall
        return None

    @classmethod
    def _find_all(cls, game_map: GameMap, unit_tracker: _UnitTracker) -> tuple[Self, ...]:
        """The expansions of `game_map` with the neutral units `unit_tracker` holds on it at the start, by pathing
        distance from this player's start, nearest first. Those no path reaches come last, sorted by increasing
        straight-line distance from this player's start. Each finds its townhall among the units `unit_tracker` holds
        then.

        The mineral fields and geysers make the expansions; every other neutral unit blocks the ground under it.
        """
        neutral_units = unit_tracker.present.neutral
        mineral_fields = neutral_units.of_type(UnitType.AnyMineralField)
        geysers = neutral_units.of_type(UnitType.AnyVespeneGeyser)
        blockers = neutral_units.excluding_type(UnitType.AnyMineralField, UnitType.AnyVespeneGeyser)
        expansion_locations = _ExpansionLocations(
            game_map.placement, mineral_fields=mineral_fields, geysers=geysers, blockers=blockers
        )
        start = game_map.start_location
        pathing_distance = _pathing_distance_from_start(game_map.pathing, start)
        start_locations = {start, *game_map.opponent_start_locations}
        expansions = [
            cls(
                center=center,
                mineral_fields=base_fields.sorted_by_distance_to(center),
                geysers=center_geysers.sorted_by_distance_to(center),
                is_start_location=center in start_locations,
                pathing_distance_from_start=pathing_distance[center],
                _unit_tracker=unit_tracker,
            )
            for base_fields, base_geysers in _resources_of_each_base(mineral_fields, geysers)
            for center, center_geysers in _townhall_centers_of_base(expansion_locations, base_fields, base_geysers)
        ]
        return tuple(
            sorted(expansions, key=lambda exp: (exp.pathing_distance_from_start, start.distance_to(exp.center)))
        )


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


def _townhall_centers_of_base(
    expansion_locations: _ExpansionLocations,
    fields: Units[Unit[UnitType.AnyMineralField]],
    geysers: Units[Unit[UnitType.AnyVespeneGeyser]],
) -> list[tuple[Point, Units[Unit[UnitType.AnyVespeneGeyser]]]]:
    """Where a townhall's center can go to mine one base's `fields` and `geysers`, each center with the geysers it
    mines: one, and one more for each geyser too far from it, on the far side of the fields. Every center mines all
    of `fields`."""
    center = expansion_locations.best_center_for([*fields, *geysers])
    if center is None:
        return []
    near_geysers: list[Unit[UnitType.AnyVespeneGeyser]] = []
    far_side_centers: list[tuple[Point, Units[Unit[UnitType.AnyVespeneGeyser]]]] = []
    for geyser in geysers:
        far_side_center = None
        if geyser.position.distance_to(center) > _MAX_GEYSER_DISTANCE_FROM_TOWNHALL:
            far_side_center = expansion_locations.best_center_for([*fields, geyser])
        if far_side_center is None or far_side_center == center:
            near_geysers.append(geyser)
        else:
            far_side_centers.append((far_side_center, Units([geyser])))
    return [(center, Units(near_geysers)), *far_side_centers]


def _pathing_distance_from_start(pathing: Grid[bool], start: Point) -> Grid[float]:
    """The pathing distance from `start` to each tile of `pathing`, where this player's first townhall stands and
    blocks the ground under it."""
    walkable = numpy.array(pathing.values)
    x, y = pathing.index_of(start)
    townhall_half_width = _TOWNHALL_SIZE // 2
    footprint_xs = slice(max(x - townhall_half_width, 0), x + townhall_half_width + 1)
    footprint_ys = slice(max(y - townhall_half_width, 0), y + townhall_half_width + 1)
    walkable[footprint_xs, footprint_ys] = True
    return Grid(walkable, origin=pathing.origin).cheapest_paths_from(start).distances
