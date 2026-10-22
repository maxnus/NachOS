"""Ramps, found from the map's grids."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast, final

import numpy
import scipy.ndimage

from sc2nachos.geometry import Direction, Point, TileSet

if TYPE_CHECKING:
    from numpy import ndarray

    from sc2nachos.geometry import Grid

# The neighbors `scipy.ndimage.label` joins into one patch, a tile's `neighbors8`: ground touching at a corner is one
# patch, so a ramp that runs diagonally is one ramp. Read-only, since every api in the process shares it.
_NEIGHBORS8 = numpy.ones((3, 3), dtype=bool)
_NEIGHBORS8.flags.writeable = False

# Levels are two apart in height, so ground spanning this much climbs from one to the next.
_HALF_A_LEVEL = 1.0

# One byte of the height the game sends: a row straight across a ramp is not quite level, its tiles differing by up to
# half a byte, while the next row up is 0.1875 away, so a byte cannot reach it (in game).
_RAMP_END_HEIGHT_TOLERANCE = 0.125


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

    @property
    def orientation(self) -> Direction:
        """The way the ramp climbs: of the eight directions, the one nearest the line from its bottom's center to its
        top's. Every main ramp of the pool climbs along a diagonal (corpus)."""
        return Direction.closest(self.top.center - self.bottom.center)

    @property
    def width(self) -> int:
        """How many tiles wide the ramp is at its top, across the way it climbs: from one end of the top to the other,
        any gap between them included. Every main ramp of the pool is 2 wide (corpus)."""
        step_x, step_y = self.orientation.value
        # Across the climb, a tile's place is its coordinates turned a quarter: the next tile across along a diagonal
        # is 2 further, along an axis 1.
        places_across = [step_x * tile.y - step_y * tile.x for tile in self.top]
        return (max(places_across) - min(places_across)) // (abs(step_x) + abs(step_y)) + 1

    def _point_at_offset_from_the_top(self, offset: tuple[float, float]) -> Point:
        """The point `offset` from the middle of the top, `offset` as for a ramp climbing north-east, mirrored to this
        one's diagonal. Raises `ValueError` for a ramp climbing along an axis, which has no diagonal to mirror to."""
        step_x, step_y = self.orientation.value
        if step_x == 0 or step_y == 0:
            raise ValueError(f"a ramp climbing {self.orientation.name} has no diagonal to mirror an offset to")
        top_middle = self.top.center
        return Point((top_middle.x + step_x * offset[0], top_middle.y + step_y * offset[1]))


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
                    top=TileSet._from_mask(origin, patch & (heights >= high - _RAMP_END_HEIGHT_TOLERANCE)),
                    bottom=TileSet._from_mask(origin, patch & (heights <= low + _RAMP_END_HEIGHT_TOLERANCE)),
                )
            )
    return tuple(sorted(ramps, key=lambda ramp: min(ramp.tiles)))
