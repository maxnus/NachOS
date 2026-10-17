"""The marks `api.debug.draw_marker` draws at a point."""

from enum import Enum


class Marker(Enum):
    """A mark drawn at a point, of lines reaching out from it."""

    CROSS = "cross"
    """An ×, its two arms on the diagonals."""
    PLUS = "plus"
    """A +, its two arms along the axes."""
