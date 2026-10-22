"""Ramps, found from the map's grids."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast, final

import numpy
import scipy.ndimage

from sc2nachos.geometry import Grid, TileSet

if TYPE_CHECKING:
    from collections.abc import Sequence

    from numpy import ndarray

    from sc2nachos.geometry import PointLike

# The neighbors `scipy.ndimage.label` joins into one patch, a tile's `neighbors8`: ground touching at a corner is one
# patch, so a ramp that runs diagonally is one ramp. Read-only, since every api in the process shares it.
_NEIGHBORS8 = numpy.ones((3, 3), dtype=bool)
_NEIGHBORS8.flags.writeable = False
# The neighbors across a side alone, a tile's `neighbors4`: a unit walks from one tile to the next across a side, and
# across a corner only where both tiles beside it are open, which joins nothing a side does not.
_NEIGHBORS4 = numpy.array([[False, True, False], [True, True, True], [False, True, False]])
_NEIGHBORS4.flags.writeable = False

# Levels are two apart in height, so ground spanning this much climbs from one to the next.
_HALF_A_LEVEL = 1.0

# How close in height to a ramp's end a tile must be to count as part of it: one byte of the height the game sends.
# A row straight across a ramp is not quite level, its tiles differing by up to half a byte, while the next row up is
# 0.1875 away, so a byte cannot reach it (in game).
_A_BYTE = 0.125

# How many tiles a townhall reaches from the tile at its center to its edge, being 5 across.
_TOWNHALL_REACH_FROM_ITS_CENTER = 2


@final
@dataclass(frozen=True, slots=True)
class Ramp:
    """A slope a ground unit walks up to the level above.

    `top` and `bottom` are the rows it is entered by, unless something stands in one: the tiles within a byte of its
    highest and its lowest height. Its middle is `tiles.center`.
    """

    tiles: TileSet
    top: TileSet
    bottom: TileSet


def find_ramps(pathing: Grid[bool], placement: Grid[bool], height: Grid[float]) -> tuple[Ramp, ...]:
    """The map's ramps, ordered by lower left tile.

    A ramp is a patch of ground that is pathable but not buildable and climbs from one level to the next. The level
    patches are bridges, stands of trees and the ground under an indestructible doodad, which the grids cannot tell
    apart.
    """
    unbuildable = pathing.values & ~placement.values
    # `label` is unannotated, and pyright reads the return type off an early-return branch of its body.
    patches, count = cast("tuple[ndarray, int]", scipy.ndimage.label(unbuildable, structure=_NEIGHBORS8))
    heights = height.values
    origin = pathing.origin
    ramps: list[Ramp] = []
    for label in range(1, count + 1):
        patch = patches == label
        levels = heights[patch]
        low, high = float(levels.min()), float(levels.max())
        if high - low >= _HALF_A_LEVEL:
            ramps.append(
                Ramp(
                    tiles=TileSet._from_mask(origin, patch),
                    # A ramp climbs at least half a level, more than the two bytes its ends reach into it, so top
                    # and bottom cannot overlap.
                    top=TileSet._from_mask(origin, patch & (heights >= high - _A_BYTE)),
                    bottom=TileSet._from_mask(origin, patch & (heights <= low + _A_BYTE)),
                )
            )
    return tuple(sorted(ramps, key=lambda ramp: min(ramp.tiles)))


def find_main_ramp(
    start: PointLike, ramps: Sequence[Ramp], pathing: Grid[bool], placement: Grid[bool], height: Grid[float]
) -> Ramp | None:
    """The ramp out of the main `start` stands in: the one of `ramps` whose top touches the level ground `start` stands
    on across a side, the nearest by ground path from `start` if several do. `None` if none does, or if `start` stands
    on a ramp or on ground a unit can neither walk nor build on. Raises `IndexError` outside the grids.

    The level ground is every tile a unit can walk on, and the buildable ground a townhall at `start` covers, which the
    start's own townhall keeps out of the pathing grid: off the ramps, within half a level of `start`'s height, and
    joined to it across sides. Buildable ground elsewhere does not count, since the ground under rocks is buildable.
    """
    start_index = height.index_of(start)
    on_a_ramp = Grid.like(height, dtype=bool, outside=False)
    for ramp in ramps:
        on_a_ramp[ramp.tiles] = True
    walkable_or_under_the_townhall = pathing.values.copy()
    townhall_columns = slice(
        max(start_index[0] - _TOWNHALL_REACH_FROM_ITS_CENTER, 0), start_index[0] + _TOWNHALL_REACH_FROM_ITS_CENTER + 1
    )
    townhall_rows = slice(
        max(start_index[1] - _TOWNHALL_REACH_FROM_ITS_CENTER, 0), start_index[1] + _TOWNHALL_REACH_FROM_ITS_CENTER + 1
    )
    walkable_or_under_the_townhall[townhall_columns, townhall_rows] |= placement.values[townhall_columns, townhall_rows]
    tile_heights = height.values
    level_ground = (
        walkable_or_under_the_townhall
        & ~on_a_ramp.values
        & (numpy.abs(tile_heights - tile_heights[start_index]) < _HALF_A_LEVEL)
    )
    level_patches, _ = cast("tuple[ndarray, int]", scipy.ndimage.label(level_ground, structure=_NEIGHBORS4))
    start_patch = level_patches[start_index]
    if start_patch == 0:
        return None
    main_ground = level_patches == start_patch
    beside_the_main = Grid(
        scipy.ndimage.binary_dilation(main_ground, structure=_NEIGHBORS4), origin=height.origin, outside=False
    )
    ramps_leading_out = [ramp for ramp in ramps if any(beside_the_main[tile] for tile in ramp.top)]
    if len(ramps_leading_out) <= 1:
        return ramps_leading_out[0] if ramps_leading_out else None
    walkable_ground = Grid(main_ground | on_a_ramp.values, origin=height.origin, outside=False)
    walking_distances = walkable_ground.cheapest_paths_from(start).distances
    return min(ramps_leading_out, key=lambda ramp: min(walking_distances[tile] for tile in ramp.top))
