"""Tiles held as a mask, as a grid reads an area through one."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, final

if TYPE_CHECKING:
    from numpy import ndarray

    from sc2nachos.geometry._shapes import Tile


@final
@dataclass(frozen=True, slots=True, eq=False)
class TileMask:
    """Tiles held as a boolean mask indexed `[x, y]`: entry `[i, j]` is the tile `i` across and `j` up from `origin`,
    as a grid has its `origin`. A mask with no true entry holds no tile."""

    origin: Tile
    """The tile at the mask's `[0, 0]` entry."""
    mask: ndarray
    """Whether each tile is held."""
