"""The standard colors `api.debug` draws in."""

from enum import Enum


class Color(Enum):
    """A standard color, by its red, green and blue, each from 0 to 255. Any other is drawn by giving a tuple of the
    three."""

    WHITE = (255, 255, 255)
    BLACK = (0, 0, 0)
    GRAY = (128, 128, 128)
    RED = (255, 0, 0)
    GREEN = (0, 255, 0)
    BLUE = (0, 0, 255)
    YELLOW = (255, 255, 0)
    CYAN = (0, 255, 255)
    MAGENTA = (255, 0, 255)
    ORANGE = (255, 165, 0)
    PURPLE = (128, 0, 128)
