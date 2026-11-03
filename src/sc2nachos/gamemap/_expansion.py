"""The places to build a townhall, and the steps that find them from the map and its resources at the start."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast, final

import numpy
import scipy.ndimage
from scipy.sparse import coo_array
from scipy.sparse.csgraph import connected_components

from sc2nachos.gamedata._footprints import FOOTPRINTS
from sc2nachos.gamemap._errors import NoMainRampError, NoNaturalError
from sc2nachos.gamemap._expansion_locations import _ExpansionLocations
from sc2nachos.gamemap._ramp import _HALF_A_LEVEL
from sc2nachos.geometry import Point, TileSet
from sc2nachos.ids import UnitTypeId
from sc2nachos.units import Units
from sc2nachos.units._unit_type import UnitType

if TYPE_CHECKING:
    from collections.abc import Iterable

    from numpy import ndarray

    from sc2nachos.gamemap._game_map import GameMap
    from sc2nachos.gamemap._ramp import Ramp
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


@dataclass(frozen=True, slots=True, kw_only=True)
class Expansion:
    """A place to build a townhall, with the mineral fields and geysers it mines.

    Each group of resources lying within 6 of one another, through any chain of them, is an expansion if it has 6 to
    10 mineral fields; fewer fields are a blocker to mine out, more a wall. Where a group's fields can be mined from
    either side, with a geyser on each, each side is an expansion of its own, and the two share the fields. Where a
    player starts the game, it is a `StartLocation`.
    """

    center: Point
    """Where the townhall's center goes: of the tile centers the game takes a townhall on, the one with the least summed
    distance to the resources."""
    mineral_fields: Units[Unit[UnitType.AnyMineralField]]
    """Its mineral fields, nearest `center` first. The same units all game, though the game changes their tags as
    they pass in and out of sight."""
    geysers: Units[Unit[UnitType.AnyVespeneGeyser]]
    """Its geysers, nearest `center` first, the same units all game."""
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

    @staticmethod
    def _find_all(game_map: GameMap, unit_tracker: _UnitTracker) -> tuple[Expansion, ...]:
        """The expansions of `game_map` with the neutral units `unit_tracker` holds on it at the start, by pathing
        distance from this player's start, nearest first, each start location a `StartLocation`. Those no path reaches
        come last, sorted by increasing straight-line distance from this player's start. Each finds its townhall among
        the units `unit_tracker` holds then.

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
        pathing_distance = game_map._walking_distances_from(start)
        start_locations = set(game_map.all_start_locations)
        expansions = [
            Expansion(
                center=center,
                mineral_fields=base_fields.sorted_by_distance_to(center),
                geysers=center_geysers.sorted_by_distance_to(center),
                pathing_distance_from_start=pathing_distance[center],
                _unit_tracker=unit_tracker,
            )
            for base_fields, base_geysers in _resources_of_each_base(mineral_fields, geysers)
            for center, center_geysers in _townhall_centers_of_base(expansion_locations, base_fields, base_geysers)
        ]
        no_ones_start = [expansion for expansion in expansions if expansion.center not in start_locations]
        found = [
            expansion
            if expansion.center not in start_locations
            else StartLocation._from_expansion(expansion, game_map, naturals=no_ones_start)
            for expansion in expansions
        ]
        return tuple(sorted(found, key=lambda exp: (exp.pathing_distance_from_start, start.distance_to(exp.center))))


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class StartLocation(Expansion):
    """An expansion a player starts the game at, with the ground of its main, the ramp out of it and its natural.

    It is found once, as the game begins, and none of it changes.
    """

    main_area: TileSet
    """The ground of the main: every tile within half a level of the height at `center`, walkable as the game began,
    joined side by side to `center` without crossing a ramp. The ground under its townhall, mineral fields and geysers
    counts at every start, though the game's pathing grid blocks only this player's (in game)."""
    _main_ramp: Ramp | None = field(repr=False, compare=False)
    _natural: Expansion | None = field(repr=False, compare=False)

    @property
    def main_ramp(self) -> Ramp:
        """The ramp out of the main: of the ramps whose top is at the main's level, within half a level of the height
        at `center`'s tile, the one whose center is the shortest walk from `center`, and of two as short, the first in
        tile order. The walk is over the pathing grid as the game began, with the ground the start's townhall covers
        opened. Raises `NoMainRampError` where no such ramp can be walked to."""
        if self._main_ramp is None:
            raise NoMainRampError(f"no ramp out of the main at {self.center} can be walked to")
        return self._main_ramp

    @property
    def natural(self) -> Expansion:
        """The expansion nearest `center` by pathing distance where no player starts, over the ground as the game
        began with the ground the start's townhall covers opened, and of two as near, the nearer in a straight line.
        Raises `NoNaturalError` where no such expansion can be walked to, as on a map drawn for a test."""
        if self._natural is None:
            raise NoNaturalError(f"no expansion where no player starts can be walked to from {self.center}")
        return self._natural

    @classmethod
    def _from_expansion(
        cls, expansion: Expansion, game_map: GameMap, *, naturals: Iterable[Expansion]
    ) -> StartLocation:
        """The start location `expansion` of `game_map` is, its natural the nearest of `naturals` to walk to."""
        center = expansion.center
        walking_distances = game_map._walking_distances_from(center)
        natural = min(
            naturals,
            key=lambda candidate: (walking_distances[candidate.center], center.distance_to(candidate.center)),
            default=None,
        )
        if natural is not None and walking_distances[natural.center] == math.inf:
            natural = None
        return cls(
            center=center,
            mineral_fields=expansion.mineral_fields,
            geysers=expansion.geysers,
            pathing_distance_from_start=expansion.pathing_distance_from_start,
            main_area=_ground_of_main(game_map, expansion),
            _main_ramp=game_map._main_ramp_of(center),
            _natural=natural,
            _unit_tracker=expansion._unit_tracker,
        )


def _start_location_at(expansions: Iterable[Expansion], point: Point) -> StartLocation | None:
    """The start location of `expansions` whose townhall goes at `point`, or `None` if there is none."""
    return next(
        (expansion for expansion in expansions if isinstance(expansion, StartLocation) and expansion.center == point),
        None,
    )


def _opponent_start_locations(game_map: GameMap, expansions: Iterable[Expansion]) -> tuple[StartLocation | None, ...]:
    """The start location of `expansions` at each of `game_map`'s start locations but this player's, in the map's
    order: `None` for one no expansion's townhall goes at."""
    return tuple(_start_location_at(expansions, start) for start in game_map.opponent_start_locations)


def _ground_of_main(game_map: GameMap, start: Expansion) -> TileSet:
    """The ground of the main at `start`, as `StartLocation.main_area` says: none if `center` itself is not on it, as
    for a start on a ramp, which only a map drawn for a test has."""
    walkable = game_map.pathing.copy()
    # Every townhall's footprint is the command center's.
    walkable.fill_footprint(FOOTPRINTS[UnitTypeId.COMMAND_CENTER], at=start.center, value=True)
    for resource in (*start.mineral_fields, *start.geysers):
        if (footprint := resource.footprint) is not None:
            walkable.fill_footprint(footprint, at=resource.position, value=True)
    for ramp in game_map.ramps:
        walkable[ramp.tiles] = False
    at_its_level = numpy.abs(game_map.height.values - game_map.height[start.center]) < _HALF_A_LEVEL
    # `label` is unannotated, and pyright reads the return type off an early-return branch of its body.
    patches, _ = cast("tuple[ndarray, int]", scipy.ndimage.label(walkable.values & at_its_level))
    patch_at_start = patches[walkable.index_of(start.center)]
    if not patch_at_start:
        return TileSet(())
    return TileSet._from_mask(walkable.origin, patches == patch_at_start)


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
