"""The marks `DebugMark` draws at a point."""

from enum import Enum


class Marker(Enum):
    """A mark drawn at a point, of lines around it: a cross of arms reaching out from it, or a regular polygon whose
    corners lie around it, its bottom side level."""

    CROSS = "x"
    """An ×, its two arms on the diagonals."""
    PLUS = "+"
    """A +, its two arms along the axes."""
    STAR = "*"
    """The × and the + together."""
    TRIANGLE = "triangle"
    """A triangle, pointing up the map."""
    SQUARE = "square"
    """A square, its sides along the axes."""
    PENTAGON = "pentagon"
    """A pentagon, pointing up the map."""
    HEXAGON = "hexagon"
    """A hexagon, flat at the top and the bottom."""
