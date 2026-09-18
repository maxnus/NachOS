"""The ramps the map's grids describe."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast, final

import numpy
import scipy.ndimage

from sc2nachos.geometry import Tile, TileSet

if TYPE_CHECKING:
    from numpy import ndarray

    from sc2nachos.geometry import Grid

# Ground touching at a corner is one patch, so a ramp that runs diagonally is one ramp.
_TOUCHING = numpy.ones((3, 3), dtype=bool)

# The levels a unit stands on are two apart, so ground spanning this much runs from one of them to another.
_HALF_A_LEVEL = 1.0

# How near the end of a ramp a tile counts as part of it, which is one byte of the height the game sends. A row
# straight across a ramp is not quite level, its tiles differing by up to half a byte, while the next row up is
# 0.1875 away, so a byte cannot reach it (in game).
_A_BYTE = 0.125


@final
@dataclass(frozen=True, slots=True)
class Ramp:
    """The slope a ground unit walks up to reach the level above.

    `top` and `bottom` are the rows it is entered by, unless something stands in one of them: the tiles within
    a byte of its highest and of its lowest. Its middle is `tiles.center`.
    """

    tiles: TileSet
    top: TileSet
    bottom: TileSet


def find_ramps(pathing: Grid[bool], placement: Grid[bool], height: Grid[float]) -> tuple[Ramp, ...]:
    """The map's ramps, ordered by their lower left tile.

    A ramp is ground a unit can walk over but cannot build on that climbs from one level to the next, so the
    patches of such ground are read whole and the ones that climb are the ramps. The level patches are bridges,
    stands of trees, and the ground under an indestructible doodad, which the map's grids cannot tell apart.
    """
    unbuildable = pathing.values & ~placement.values
    # `label` is unannotated, and pyright reads the return type off an early-return branch of its body.
    patches, count = cast("tuple[ndarray, int]", scipy.ndimage.label(unbuildable, structure=_TOUCHING))
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
                    tiles=TileSet(_tiles(patch, origin)),
                    # A ramp climbs a level and an end reaches a byte into it, so the two cannot meet.
                    top=TileSet(_tiles(patch & (heights >= high - _A_BYTE), origin)),
                    bottom=TileSet(_tiles(patch & (heights <= low + _A_BYTE), origin)),
                )
            )
    return tuple(sorted(ramps, key=lambda ramp: min(ramp.tiles)))


def _tiles(mask: ndarray, origin: Tile) -> list[Tile]:
    """The tiles a mask over a grid holds true, addressed from the grid's `origin`."""
    xs, ys = numpy.nonzero(mask)
    return [Tile(origin[0] + x, origin[1] + y) for x, y in zip(xs.tolist(), ys.tolist(), strict=True)]
