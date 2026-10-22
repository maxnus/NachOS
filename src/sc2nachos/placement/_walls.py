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
# +x and +y; a ramp climbing another way mirrors them. They are burnysc2's sites, which close every main ramp of the
# pool (in game).
_CORNER_DEPOTS = ((2.0, -1.0), (-1.0, 2.0))
_MIDDLE_DEPOT = (1.0, 1.0)
_BARRACKS = (1.5, 1.5)
# A barracks's add-on stands on its right, so where the ramp climbs toward -x the barracks moves this far left to leave
# the add-on room beside the depot.
_BARRACKS_SHIFT_FOR_ITS_ADD_ON = -2.0
_PYLON = (4.0, 4.0)


@final
@dataclass(frozen=True, slots=True)
class TerranWall:
    """Where a Terran wall closes a ramp at its top: a supply depot at each corner, and between them a barracks or a
    third depot. Each site is a structure's center, as `PlacementGrids.is_free` takes it.

    `barracks` leaves room for the barracks's add-on, on its right. On a main left of its ramp, where the ramp climbs
    toward -x, no barracks between the depots both closes the wall and leaves that room, so this one closes it only once
    its add-on stands. `barracks_without_add_on` closes it by itself, leaving no room for an add-on on such a main and
    standing where `barracks` does on any other. `middle_depot` stands in the barracks's place.
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
    climb = _climb(ramp)
    if climb is None:
        return None
    first_corner, second_corner = sorted(_site(ramp, climb, offset) for offset in _CORNER_DEPOTS)
    barracks_without_add_on = _site(ramp, climb, _BARRACKS)
    barracks = barracks_without_add_on
    if climb[0] < 0:
        barracks = Point((barracks_without_add_on.x + _BARRACKS_SHIFT_FOR_ITS_ADD_ON, barracks_without_add_on.y))
    wall = TerranWall(
        depots=(first_corner, second_corner),
        barracks=barracks,
        barracks_without_add_on=barracks_without_add_on,
        middle_depot=_site(ramp, climb, _MIDDLE_DEPOT),
    )
    add_on = Point((barracks.x + _ADD_ON_OFFSET[0], barracks.y + _ADD_ON_OFFSET[1]))
    depot, barracks_footprint = footprint_of(UnitTypeId.SUPPLY_DEPOT), footprint_of(UnitTypeId.BARRACKS)
    sites = (
        (depot, first_corner),
        (depot, second_corner),
        (depot, wall.middle_depot),
        (barracks_footprint, barracks),
        (barracks_footprint, barracks_without_add_on),
        (footprint_of(UnitTypeId.REACTOR_BARRACKS), add_on),
    )
    return wall if all(ground.fits_footprint(footprint, at=center) for footprint, center in sites) else None


def find_protoss_wall(
    ramp: Ramp,
    ground: Grid[bool],
    footprint_of: Callable[[UnitTypeId], Footprint],
    start_locations: Sequence[Point],
) -> ProtossWall | None:
    """The Protoss wall at the top of `ramp`, its gap at the corner nearer the one of `start_locations` nearest the
    ramp's top, or `None` if the ramp is not of the shape every main ramp of the pool is, if there are no start
    locations, or if a site would not stand on `ground`, whose footprints `footprint_of` gives."""
    climb = _climb(ramp)
    if climb is None or not start_locations:
        return None
    start = min(start_locations, key=lambda location: location.distance_to(ramp.top.center))
    near_corner, far_corner = sorted(
        (_site(ramp, climb, offset) for offset in _CORNER_DEPOTS), key=lambda corner: corner.distance_to(start)
    )
    middle_depot = _site(ramp, climb, _MIDDLE_DEPOT)
    half_a_step_up = (0.5 * climb[0], 0.5 * climb[1])
    at_the_corner = Point((far_corner.x + half_a_step_up[0], far_corner.y + half_a_step_up[1]))
    barracks = _site(ramp, climb, _BARRACKS)
    beside_the_gap = Point(
        (
            barracks.x + (middle_depot.x - at_the_corner.x) / 1.5,
            barracks.y + (middle_depot.y - at_the_corner.y) / 1.5,
        )
    )
    wall = ProtossWall(
        pylon=_site(ramp, climb, _PYLON),
        buildings=(at_the_corner, beside_the_gap),
        gap=Point((near_corner.x - half_a_step_up[0], near_corner.y - half_a_step_up[1])),
    )
    building = footprint_of(UnitTypeId.GATEWAY)
    sites = ((footprint_of(UnitTypeId.PYLON), wall.pylon), (building, at_the_corner), (building, beside_the_gap))
    return wall if all(ground.fits_footprint(footprint, at=center) for footprint, center in sites) else None


def _climb(ramp: Ramp) -> tuple[int, int] | None:
    """Which way `ramp` climbs, as the signs of x and y, if it is of the shape every main ramp of the pool is: two top
    tiles touching at a corner, the ramp climbing diagonally away from the line through them. `None` otherwise."""
    if len(ramp.top) != 2:
        return None
    first, second = sorted(ramp.top)
    across = (second.x - first.x, second.y - first.y)
    if abs(across[0]) != 1 or abs(across[1]) != 1:
        return None
    climb_x, climb_y = ramp.top.center.x - ramp.bottom.center.x, ramp.top.center.y - ramp.bottom.center.y
    if climb_x == 0 or climb_y == 0:
        return None
    climb = (1 if climb_x > 0 else -1, 1 if climb_y > 0 else -1)
    if across[0] * climb[0] + across[1] * climb[1] != 0:
        return None
    return climb


def _site(ramp: Ramp, climb: tuple[int, int], offset: tuple[float, float]) -> Point:
    """The point `offset` from the middle of `ramp`'s top, mirrored to the way it climbs."""
    middle = ramp.top.center
    return Point((middle.x + climb[0] * offset[0], middle.y + climb[1] * offset[1]))
