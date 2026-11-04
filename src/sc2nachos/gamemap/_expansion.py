"""The places to build a townhall, and the steps that find them from the map and its resources at the start."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Self, cast, final

import numpy
import scipy.ndimage
from scipy.sparse import coo_array
from scipy.sparse.csgraph import connected_components

from sc2nachos.gamedata._footprints import FOOTPRINTS
from sc2nachos.gamemap._errors import NoExpansionError, NoMainRampError
from sc2nachos.gamemap._expansion_locations import _ExpansionLocations
from sc2nachos.gamemap._ramp import _HALF_A_LEVEL
from sc2nachos.geometry import Point, TileSet
from sc2nachos.ids import UnitTypeId
from sc2nachos.units import Units
from sc2nachos.units._unit_type import UnitType

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from numpy import ndarray

    from sc2nachos.gamemap._game_map import GameMap
    from sc2nachos.gamemap._ramp import Ramp
    from sc2nachos.geometry import Grid, Rectangle
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
# A full base, which a third is, has 8 fields and 2 geysers, as every main and natural of the pool has; the pool's other
# bases have 6 or 7 fields, as LeyLines' near the center and Torches' gold bases do, or fewer than 2 geysers (corpus).
_MIN_MINERAL_FIELDS_OF_A_FULL_BASE = 8
_GEYSERS_OF_A_FULL_BASE = 2


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
    """The pathing distance from this player's start to `center`, over the ground as the game began with every mineral
    field and geyser on it: `inf` where no path reaches."""
    _game_map: GameMap = field(repr=False, compare=False)
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

    def plateau(self, *, max_distance: float = math.inf, height_tolerance: float = _HALF_A_LEVEL) -> TileSet:
        """The ground at the expansion's level: every tile less than `height_tolerance` from the height at `center`,
        half a level by default, walkable as the game began, and joined side by side to `center` within `max_distance`
        of it in a straight line, without crossing a ramp. The ground under its townhall, mineral fields and geysers
        counts, though the game's pathing grid blocks this player's own at its start (in game). Empty where `center`
        stands on no such ground. Worked out at each call."""
        game_map = self._game_map
        walkable = game_map.pathing.copy()
        # Every townhall's footprint is the command center's.
        walkable.fill_footprint(FOOTPRINTS[UnitTypeId.COMMAND_CENTER], at=self.center, value=True)
        for resource in (*self.mineral_fields, *self.geysers):
            if (footprint := resource.footprint) is not None:
                walkable.fill_footprint(footprint, at=resource.position, value=True)
        for ramp in game_map.ramps:
            walkable[ramp.tiles] = False
        ground = walkable.values & (numpy.abs(game_map.height.values - game_map.height[self.center]) < height_tolerance)
        if max_distance < math.inf:
            ground &= walkable.distance_from(self.center).values <= max_distance
        # `label` is unannotated, and pyright reads the return type off an early-return branch of its body.
        patches, _ = cast("tuple[ndarray, int]", scipy.ndimage.label(ground))
        patch_at_center = patches[walkable.index_of(self.center)]
        if not patch_at_center:
            return TileSet(())
        return TileSet._from_mask(walkable.origin, patches == patch_at_center)

    @property
    def _is_full_base(self) -> bool:
        """Whether it has the fields and geysers of a full base."""
        return (
            len(self.mineral_fields) >= _MIN_MINERAL_FIELDS_OF_A_FULL_BASE
            and len(self.geysers) == _GEYSERS_OF_A_FULL_BASE
        )

    @staticmethod
    def _find_all(game_map: GameMap, unit_tracker: _UnitTracker) -> tuple[Expansion, ...]:
        """The expansions of `game_map` with the neutral units `unit_tracker` holds on it at the start, by pathing
        distance from this player's start, nearest first, each start location a `StartLocation`. Those no path reaches
        come last, sorted by increasing straight-line distance from this player's start. Each finds its townhall among
        the units `unit_tracker` holds then.

        The mineral fields and geysers make the expansions; every other neutral unit blocks the ground under it. Every
        walk is over the ground as the game began with every mineral field and geyser on it.
        """
        neutral_units = unit_tracker.present.neutral
        mineral_fields = neutral_units.of_type(UnitType.AnyMineralField)
        geysers = neutral_units.of_type(UnitType.AnyVespeneGeyser)
        blockers = neutral_units.excluding_type(UnitType.AnyMineralField, UnitType.AnyVespeneGeyser)
        expansion_locations = _ExpansionLocations(
            game_map.placement, mineral_fields=mineral_fields, geysers=geysers, blockers=blockers
        )
        ground = _ground_as_the_game_began(game_map, (*mineral_fields, *geysers))
        start = game_map.start_location
        from_start = _walking_distances(ground, start)
        start_locations = set(game_map.all_start_locations)
        expansions = [
            Expansion(
                center=center,
                mineral_fields=base_fields.sorted_by_distance_to(center),
                geysers=center_geysers.sorted_by_distance_to(center),
                pathing_distance_from_start=from_start[center],
                _game_map=game_map,
                _unit_tracker=unit_tracker,
            )
            for base_fields, base_geysers in _resources_of_each_base(mineral_fields, geysers)
            for center, center_geysers in _townhall_centers_of_base(expansion_locations, base_fields, base_geysers)
        ]
        candidates = [expansion for expansion in expansions if expansion.center not in start_locations]
        found = [
            expansion
            if expansion.center not in start_locations
            else StartLocation._from_expansion(
                expansion,
                ground=ground,
                candidates=candidates,
                from_center=from_start if expansion.center == start else _walking_distances(ground, expansion.center),
            )
            for expansion in expansions
        ]
        return tuple(sorted(found, key=lambda exp: (exp.pathing_distance_from_start, start.distance_to(exp.center))))


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class StartLocation(Expansion):
    """An expansion a player starts the game at, with the ground of its main, the ramp out of it, its natural and its
    two thirds.

    It is found once, as the game begins, and none of it changes. Its natural and thirds are walked to over the ground
    as the game began with every mineral field and geyser on it, which the game's pathing grid leaves out but for this
    player's own (in game), and never where a player starts.
    """

    main_area: TileSet
    """The ground of the main, its `plateau()`: every tile within half a level of the height at `center`, walkable as
    the game began, joined side by side to `center` without crossing a ramp."""
    _main_ramp: Ramp | None = field(repr=False, compare=False)
    _natural: Expansion | None = field(repr=False, compare=False)
    _line_third: Expansion | None = field(repr=False, compare=False)
    _triangle_third: Expansion | None = field(repr=False, compare=False)

    @property
    def main_ramp(self) -> Ramp:
        """The ramp out of the main: of the ramps whose top is at the main's level, within half a level of the height
        at `center`'s tile, the one whose center is the shortest walk from `center`, and of two as short, the first in
        tile order. The walk is over the game's pathing grid as the game began, with the ground the start's townhall
        covers opened. Raises `NoMainRampError` where no such ramp can be walked to."""
        if self._main_ramp is None:
            raise NoMainRampError(f"no ramp out of the main at {self.center} can be walked to")
        return self._main_ramp

    @property
    def natural(self) -> Expansion:
        """The expansion nearest `center` by pathing distance where no player starts, and of two as near, the nearer in
        a straight line. Raises `NoExpansionError` where no such expansion can be walked to, as on a map drawn for a
        test."""
        if self._natural is None:
            raise NoExpansionError(f"no expansion where no player starts can be walked to from {self.center}")
        return self._natural

    @property
    def line_third(self) -> Expansion:
        """The third along the way from the main through the natural: of the two full bases nearest the natural by
        pathing distance where no player starts, the one whose straight-line distance to `center` is the larger against
        its straight-line distance to the playable area's nearest edge. A full base has 8 mineral fields or more and 2
        geysers. Raises `NoExpansionError` where fewer than two such bases can be walked to from the natural."""
        if self._line_third is None:
            raise NoExpansionError(f"fewer than two full bases can be walked to from the natural of {self.center}")
        return self._line_third

    @property
    def triangle_third(self) -> Expansion:
        """The third beside the main and the natural: of the two `line_third` is chosen from, the other. Raises
        `NoExpansionError` as `line_third` does."""
        if self._triangle_third is None:
            raise NoExpansionError(f"fewer than two full bases can be walked to from the natural of {self.center}")
        return self._triangle_third

    @classmethod
    def _from_expansion(
        cls, expansion: Expansion, *, ground: Grid[bool], candidates: Sequence[Expansion], from_center: Grid[float]
    ) -> Self:
        """The start location `expansion` is, walked from over `ground`: its natural and thirds the nearest of
        `candidates`, the expansions where no player starts, by the pathing distances `from_center` holds."""
        center = expansion.center
        line_third = triangle_third = None
        natural = next(iter(_nearest_by_walking(candidates, center, from_center)), None)
        if natural is not None:
            from_natural = _walking_distances(ground, natural.center)
            full_bases = [candidate for candidate in candidates if candidate is not natural and candidate._is_full_base]
            thirds = _nearest_by_walking(full_bases, natural.center, from_natural)[:2]
            if len(thirds) == 2:
                playable_area = expansion._game_map.playable_area
                line_third, triangle_third = sorted(
                    thirds, key=lambda third: -_how_far_out_along_the_edge(third.center, center, playable_area)
                )
        return cls(
            center=center,
            mineral_fields=expansion.mineral_fields,
            geysers=expansion.geysers,
            pathing_distance_from_start=expansion.pathing_distance_from_start,
            main_area=expansion.plateau(),
            _main_ramp=expansion._game_map._main_ramp_of(center),
            _natural=natural,
            _line_third=line_third,
            _triangle_third=triangle_third,
            _game_map=expansion._game_map,
            _unit_tracker=expansion._unit_tracker,
        )

    @classmethod
    def _find_at(cls, expansions: Iterable[Expansion], point: Point) -> Self | None:
        """The start location of `expansions` whose townhall goes at `point`, or `None` if there is none."""
        return next(
            (expansion for expansion in expansions if isinstance(expansion, cls) and expansion.center == point), None
        )

    @classmethod
    def _find_at_opponent_starts(cls, expansions: Iterable[Expansion], game_map: GameMap) -> tuple[Self | None, ...]:
        """The start location of `expansions` at each of `game_map`'s start locations but this player's, in the map's
        order: `None` for one no expansion's townhall goes at."""
        return tuple(cls._find_at(expansions, start) for start in game_map.opponent_start_locations)


def _ground_as_the_game_began(game_map: GameMap, resources: Iterable[Unit]) -> Grid[bool]:
    """Where a ground unit can walk as the game began: the game's pathing grid, which blocks this player's own
    mineral fields and geysers but no other (in game), with every one of `resources` closed under its footprint."""
    ground = game_map.pathing.copy()
    for resource in resources:
        if (footprint := resource.footprint) is not None:
            ground.fill_footprint(footprint, at=resource.position, value=False)
    return ground


def _walking_distances(ground: Grid[bool], origin: Point) -> Grid[float]:
    """The pathing distance from `origin` to every tile over `ground`, `inf` where no path reaches, with the ground a
    townhall at `origin` covers opened, since this player's own starting townhall closes it."""
    walkable = ground.copy()
    # Every townhall's footprint is the command center's.
    walkable.fill_footprint(FOOTPRINTS[UnitTypeId.COMMAND_CENTER], at=origin, value=True)
    return walkable.cheapest_paths_from(origin).distances


def _nearest_by_walking(expansions: Iterable[Expansion], origin: Point, from_origin: Grid[float]) -> list[Expansion]:
    """Those of `expansions` a path from `origin` reaches, nearest first by the pathing distances `from_origin` holds,
    and of two as near, the nearer in a straight line."""
    reachable = [expansion for expansion in expansions if from_origin[expansion.center] < math.inf]
    return sorted(
        reachable, key=lambda expansion: (from_origin[expansion.center], origin.distance_to(expansion.center))
    )


def _how_far_out_along_the_edge(point: Point, main: Point, playable_area: Rectangle) -> float:
    """The straight-line distance from `main` to `point` over the straight-line distance from `point` to the nearest
    edge of `playable_area`: large for a base out along the edge, as a line third is."""
    distance_to_the_edge = min(
        point.x - playable_area.x, playable_area.right - point.x, point.y - playable_area.y, playable_area.top - point.y
    )
    return main.distance_to(point) / distance_to_the_edge


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
