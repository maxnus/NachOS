"""Where structures close a main's ramp at its top."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, final

from sc2nachos.geometry import Point
from sc2nachos.ids import UnitTypeId
from sc2nachos.placement._build_sites import _ADD_ON_OFFSET

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from sc2nachos.gamemap import Ramp
    from sc2nachos.geometry import Footprint, Grid

# Every site is an offset from the middle of the ramp's top, the corner its two tiles share, for a ramp climbing toward
# +x and +y; a ramp climbing another way mirrors them. They are burnysc2's sites (corpus), and with the depots raised
# each wall closes every main ramp of the pool, `barracks` there once its add-on stands (in game).
_CORNER_DEPOT_OFFSETS = ((2.0, -1.0), (-1.0, 2.0))
_MIDDLE_DEPOT_OFFSET = (1.0, 1.0)
_BARRACKS_OFFSET = (1.5, 1.5)
# A barracks's add-on stands on its right, so where the ramp climbs toward -x the barracks moves this far left to leave
# the add-on room beside the depot.
_BARRACKS_SHIFT_FOR_ITS_ADD_ON = -2.0
_PYLON_OFFSET = (4.0, 4.0)
# The Protoss buildings and the gap where the corner farther from the start is the first of `_CORNER_DEPOT_OFFSETS`;
# where it is the second, each offset's coordinates swap, mirroring the wall across the way the ramp climbs.
_PROTOSS_BUILDING_OFFSETS = ((2.5, -0.5), (0.5, 2.5))
_PROTOSS_GAP_OFFSET = (-1.5, 1.5)


@final
@dataclass(frozen=True, slots=True)
class TerranWall:
    """Where a Terran wall closes a ramp at its top: a supply depot at each corner, and between them a barracks or a
    third depot. Each site is a structure's center, as `PlacementGrids.is_free` takes it.

    `barracks` leaves room for the barracks's add-on, on its right. On a main left of its ramp, where the ramp climbs
    toward -x, no barracks between the depots both closes the wall and leaves that room (corpus), so this one closes it
    only once its add-on stands. `barracks_without_add_on` closes it by itself, leaving no room for an add-on on such a
    main and standing where `barracks` does on any other. `middle_depot` stands in the barracks's place.
    """

    depots: tuple[Point, Point]
    """The two at the corners, ordered by x."""
    barracks: Point
    barracks_without_add_on: Point
    middle_depot: Point


@final
@dataclass(frozen=True, slots=True)
class ProtossWall:
    """Where a Protoss wall closes a ramp at its top but for a gap one unit closes. Each site is a structure's center,
    as `PlacementGrids.is_free` takes it.

    `pylon` powers both `buildings`, each 3 by 3, as a gateway, a forge or a cybernetics core is: first the one at the
    corner farther from the main's start, then the one beside the gap. `gap` is where a unit stands to close the wall,
    at the corner nearer the start.
    """

    pylon: Point
    buildings: tuple[Point, Point]
    gap: Point


def find_terran_wall(
    ramp: Ramp, ground: Grid[bool], footprint_of: Callable[[UnitTypeId], Footprint]
) -> TerranWall | None:
    """The Terran wall at the top of `ramp`, or `None` if the ramp is not of the shape every main ramp of the pool is,
    or if a site or the barracks's add-on would not stand on `ground`, whose footprints `footprint_of` gives."""
    climbing_signs = _climbing_signs(ramp)
    if climbing_signs is None:
        return None
    first_corner, second_corner = sorted(
        _site_at_offset(ramp, climbing_signs, offset) for offset in _CORNER_DEPOT_OFFSETS
    )
    barracks_without_add_on = _site_at_offset(ramp, climbing_signs, _BARRACKS_OFFSET)
    barracks_site = barracks_without_add_on
    if climbing_signs[0] < 0:
        barracks_site = Point((barracks_without_add_on.x + _BARRACKS_SHIFT_FOR_ITS_ADD_ON, barracks_without_add_on.y))
    terran_wall = TerranWall(
        depots=(first_corner, second_corner),
        barracks=barracks_site,
        barracks_without_add_on=barracks_without_add_on,
        middle_depot=_site_at_offset(ramp, climbing_signs, _MIDDLE_DEPOT_OFFSET),
    )
    add_on_site = Point((barracks_site.x + _ADD_ON_OFFSET[0], barracks_site.y + _ADD_ON_OFFSET[1]))
    depot_footprint = footprint_of(UnitTypeId.SUPPLY_DEPOT)
    barracks_footprint = footprint_of(UnitTypeId.BARRACKS)
    footprints_at_sites = (
        (depot_footprint, first_corner),
        (depot_footprint, second_corner),
        (depot_footprint, terran_wall.middle_depot),
        (barracks_footprint, barracks_site),
        (barracks_footprint, barracks_without_add_on),
        (footprint_of(UnitTypeId.REACTOR_BARRACKS), add_on_site),
    )
    if all(ground.fits_footprint(footprint, at=site) for footprint, site in footprints_at_sites):
        return terran_wall
    return None


def find_protoss_wall(
    ramp: Ramp,
    ground: Grid[bool],
    footprint_of: Callable[[UnitTypeId], Footprint],
    start_locations: Sequence[Point],
) -> ProtossWall | None:
    """The Protoss wall at the top of `ramp`, its gap at the corner nearer the one of `start_locations` nearest the
    ramp's top, or `None` if the ramp is not of the shape every main ramp of the pool is, if there are no start
    locations, or if a site would not stand on `ground`, whose footprints `footprint_of` gives."""
    climbing_signs = _climbing_signs(ramp)
    if climbing_signs is None or not start_locations:
        return None
    nearest_start = min(start_locations, key=lambda location: location.distance_to(ramp.top.center))
    first_corner, second_corner = (_site_at_offset(ramp, climbing_signs, offset) for offset in _CORNER_DEPOT_OFFSETS)
    first_corner_is_farther = first_corner.distance_to(nearest_start) > second_corner.distance_to(nearest_start)

    def site_mirrored_to_the_far_corner(offset: tuple[float, float]) -> Point:
        mirrored_offset = offset if first_corner_is_farther else (offset[1], offset[0])
        return _site_at_offset(ramp, climbing_signs, mirrored_offset)

    at_the_corner, beside_the_gap = (site_mirrored_to_the_far_corner(offset) for offset in _PROTOSS_BUILDING_OFFSETS)
    protoss_wall = ProtossWall(
        pylon=_site_at_offset(ramp, climbing_signs, _PYLON_OFFSET),
        buildings=(at_the_corner, beside_the_gap),
        gap=site_mirrored_to_the_far_corner(_PROTOSS_GAP_OFFSET),
    )
    building_footprint = footprint_of(UnitTypeId.GATEWAY)
    footprints_at_sites = (
        (footprint_of(UnitTypeId.PYLON), protoss_wall.pylon),
        (building_footprint, at_the_corner),
        (building_footprint, beside_the_gap),
    )
    if all(ground.fits_footprint(footprint, at=site) for footprint, site in footprints_at_sites):
        return protoss_wall
    return None


def _climbing_signs(ramp: Ramp) -> tuple[int, int] | None:
    """Which way `ramp` climbs, as the signs of x and y, if it is of the shape every main ramp of the pool is: two top
    tiles touching at a corner, the ramp climbing diagonally away from the line through them. `None` otherwise."""
    if len(ramp.top) != 2:
        return None
    first_top_tile, second_top_tile = sorted(ramp.top)
    across_the_top = (second_top_tile.x - first_top_tile.x, second_top_tile.y - first_top_tile.y)
    if abs(across_the_top[0]) != 1 or abs(across_the_top[1]) != 1:
        return None
    climb_x, climb_y = ramp.top.center.x - ramp.bottom.center.x, ramp.top.center.y - ramp.bottom.center.y
    if climb_x == 0 or climb_y == 0:
        return None
    climbing_signs = (1 if climb_x > 0 else -1, 1 if climb_y > 0 else -1)
    if across_the_top[0] * climbing_signs[0] + across_the_top[1] * climbing_signs[1] != 0:
        return None
    return climbing_signs


def _site_at_offset(ramp: Ramp, climbing_signs: tuple[int, int], offset: tuple[float, float]) -> Point:
    """The point `offset` from the middle of `ramp`'s top, mirrored to the way it climbs, as `climbing_signs` says."""
    top_middle = ramp.top.center
    return Point((top_middle.x + climbing_signs[0] * offset[0], top_middle.y + climbing_signs[1] * offset[1]))
