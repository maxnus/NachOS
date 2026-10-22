"""The eight ways from a tile to a neighbor."""

from __future__ import annotations

import math
from enum import Enum


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
    def _nearest_to(cls, step_x: float, step_y: float) -> Direction:
        """The direction whose line lies nearest by angle to the line toward `(step_x, step_y)`; of two as near, the
        one counterclockwise of it. Raises `ValueError` for a step of no length, which points nowhere."""
        if step_x == 0 and step_y == 0:
            raise ValueError("a step of no length has no direction")
        # A tie rounds up, counterclockwise, as every tie in NachOS does.
        eighths_counterclockwise_of_east = math.floor(math.atan2(step_y, step_x) / (math.pi / 4) + 0.5) % 8
        return _COUNTERCLOCKWISE_FROM_EAST[eighths_counterclockwise_of_east]


_COUNTERCLOCKWISE_FROM_EAST = tuple(Direction)
