"""Where structures close a main's ramp at its top."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, final

from sc2nachos.gamedata._footprints import ADD_ON_OFFSET
from sc2nachos.geometry import Point

if TYPE_CHECKING:
    from sc2nachos.gamemap._ramp import Ramp
    from sc2nachos.geometry import PointLike

# Every site is an offset from the middle of the ramp's top, as for a ramp climbing north-east, which `Ramp` mirrors
# to the way it climbs. They are burnysc2's sites (corpus), and with the depots raised each wall closes every main ramp
# of the pool, `barracks_with_add_on` there once its add-on stands (in game).
_CORNER_DEPOT_OFFSETS = ((2.0, -1.0), (-1.0, 2.0))
_MIDDLE_DEPOT_OFFSET = (1.0, 1.0)
_BARRACKS_IN_WALL_OFFSET = (1.5, 1.5)
# A barracks's add-on stands on its right, so where the ramp climbs toward -x the barracks moves this far left to leave
# the add-on room beside the depot.
_BARRACKS_SHIFT_FOR_ITS_ADD_ON = -2.0
_PYLON_OFFSET = (4.0, 4.0)
# The Protoss wall with its gap at the second of the corners; where the first is the nearer the start, each offset's
# coordinates swap, mirroring the wall across the line the ramp climbs along.
_NEAR_BUILDING_OFFSET = (0.5, 2.5)
_FAR_BUILDING_OFFSET = (2.5, -0.5)
_GAP_OFFSET = (-1.5, 1.5)


@final
@dataclass(frozen=True, slots=True)
class TerranWall:
    """Where a Terran wall closes a main's ramp at its top: a supply depot at each corner, and between them a barracks
    or a third depot. Each site is a structure's center, as `PlacementGrids.is_free` takes it, and whether one may
    stand there now is for `is_free` to say.
    """

    near_depot: Point
    """The corner depot nearer the main's start; of two as near, the one with the lower x."""
    far_depot: Point
    barracks_with_add_on: Point
    """A barracks between the depots with room for its add-on, on its right. On a main left of its ramp, which climbs
    toward -x, no barracks between them both closes the wall and leaves that room (corpus), so this one closes it only
    once its add-on stands."""
    add_on: Point
    """The center of the add-on of the barracks at `barracks_with_add_on`."""
    barracks_in_wall: Point
    """A barracks between the depots that closes the wall by itself. On a main right of its ramp it stands where
    `barracks_with_add_on` does; on one left of it, it leaves no room for an add-on."""
    middle_depot: Point
    """A third depot, in the barracks's place."""

    @classmethod
    def from_ramp(cls, ramp: Ramp, *, start: PointLike) -> TerranWall | None:
        """The Terran wall at the top of `ramp` out of the main `start` stands in, or `None` if the ramp is not of the
        shape every main ramp of the pool is: two top tiles touching at a corner, and a climb along a diagonal."""
        if not _is_shaped_like_the_pools_main_ramps(ramp):
            return None
        near_corner, far_corner = _corners_nearer_first(ramp, start)
        barracks_in_wall = ramp._point_from_top(_BARRACKS_IN_WALL_OFFSET)
        barracks_with_add_on = barracks_in_wall
        if ramp.orientation.value[0] < 0:
            barracks_with_add_on = Point((barracks_in_wall.x + _BARRACKS_SHIFT_FOR_ITS_ADD_ON, barracks_in_wall.y))
        return cls(
            near_depot=near_corner,
            far_depot=far_corner,
            barracks_with_add_on=barracks_with_add_on,
            add_on=Point((barracks_with_add_on.x + ADD_ON_OFFSET[0], barracks_with_add_on.y + ADD_ON_OFFSET[1])),
            barracks_in_wall=barracks_in_wall,
            middle_depot=ramp._point_from_top(_MIDDLE_DEPOT_OFFSET),
        )


@final
@dataclass(frozen=True, slots=True)
class ProtossWall:
    """Where a Protoss wall closes a main's ramp at its top but for a gap one unit closes: a pylon that powers two 3x3
    buildings, as a gateway, a forge or a cybernetics core is. Each site is a structure's center, as
    `PlacementGrids.is_free` takes it, and whether one may stand there now is for `is_free` to say.
    """

    pylon: Point
    near_building: Point
    """The building beside the gap, at the corner nearer the main's start; of two corners as near, the one with the
    lower x."""
    far_building: Point
    """The building at the corner farther from the main's start."""
    gap: Point
    """Where a unit stands to close the wall, at the corner nearer the main's start."""

    @classmethod
    def from_ramp(cls, ramp: Ramp, *, start: PointLike) -> ProtossWall | None:
        """The Protoss wall at the top of `ramp` out of the main `start` stands in, or `None` if the ramp is not of the
        shape every main ramp of the pool is: two top tiles touching at a corner, and a climb along a diagonal."""
        if not _is_shaped_like_the_pools_main_ramps(ramp):
            return None
        near_corner, _ = _corners_nearer_first(ramp, start)
        gap_at_the_second_corner = near_corner == ramp._point_from_top(_CORNER_DEPOT_OFFSETS[1])

        def site_by_the_near_corner(offset: tuple[float, float]) -> Point:
            return ramp._point_from_top(offset if gap_at_the_second_corner else (offset[1], offset[0]))

        return cls(
            pylon=ramp._point_from_top(_PYLON_OFFSET),
            near_building=site_by_the_near_corner(_NEAR_BUILDING_OFFSET),
            far_building=site_by_the_near_corner(_FAR_BUILDING_OFFSET),
            gap=site_by_the_near_corner(_GAP_OFFSET),
        )


def _is_shaped_like_the_pools_main_ramps(ramp: Ramp) -> bool:
    """Whether `ramp` has the two top tiles touching at a corner and the diagonal climb every main ramp of the pool
    has, which the walls' offsets are for: they are measured from that corner."""
    if len(ramp.top) != 2:
        return False
    first_top_tile, second_top_tile = sorted(ramp.top)
    touching_at_a_corner = second_top_tile.x - first_top_tile.x == abs(second_top_tile.y - first_top_tile.y) == 1
    step_x, step_y = ramp.orientation.value
    return touching_at_a_corner and step_x != 0 and step_y != 0


def _corners_nearer_first(ramp: Ramp, start: PointLike) -> tuple[Point, Point]:
    """The two corners of `ramp`'s top where a wall's depots stand, the one nearer `start` first; of two as near, the
    one with the lower x."""
    first_corner, second_corner = sorted(
        (ramp._point_from_top(offset) for offset in _CORNER_DEPOT_OFFSETS),
        key=lambda corner: (corner.distance_to(start), corner.x),
    )
    return first_corner, second_corner
