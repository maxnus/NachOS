"""The eight ways from a tile to a neighbor."""

from __future__ import annotations

import math
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sc2nachos.geometry._point import PointLike


class Direction(Enum):
    """One of the eight ways from a tile to a neighbor, valued as its step in x and y. North is +y, as the game's y
    grows upward."""

    EAST = (1, 0)
    NORTH_EAST = (1, 1)
    NORTH = (0, 1)
    NORTH_WEST = (-1, 1)
    WEST = (-1, 0)
    SOUTH_WEST = (-1, -1)
    SOUTH = (0, -1)
    SOUTH_EAST = (1, -1)

    @classmethod
    def closest(cls, vector: PointLike) -> Direction:
        """The direction whose line lies nearest by angle to the line along `vector`. Raises `ValueError` for a vector
        of no length, which points nowhere."""
        vector_x, vector_y = vector[0], vector[1]
        if vector_x == 0 and vector_y == 0:
            raise ValueError("a vector of no length has no direction")
        # The members run counterclockwise from east, an eighth of a turn apart; a half rounds up, as NachOS rounds.
        eighths_counterclockwise_of_east = math.floor(math.atan2(vector_y, vector_x) / (math.pi / 4) + 0.5) % 8
        return tuple(cls)[eighths_counterclockwise_of_east]
