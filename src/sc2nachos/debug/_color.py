"""The standard colors `api.debug` draws in."""

from enum import Enum

from s2clientprotocol import debug_pb2

from sc2nachos._checks import is_whole_number

type _Rgb = tuple[int, int, int]


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


def _rgb_of(color: Color | _Rgb) -> _Rgb:
    """`color`'s red, green and blue, checked to be whole numbers from 0 to 255."""
    rgb = color.value if isinstance(color, Color) else color
    if len(rgb) != 3 or not all(is_whole_number(part) and 0 <= part <= 255 for part in rgb):
        raise ValueError(f"a color is red, green and blue, each a whole number from 0 to 255, not {color!r}")
    return rgb


def _rgb_if_given(color: Color | _Rgb | None) -> _Rgb | None:
    """`color`'s red, green and blue, or `None` if no color is given. Raises `ValueError` as `_rgb_of` does."""
    return None if color is None else _rgb_of(color)


def _proto_color(rgb: _Rgb) -> debug_pb2.Color:
    """`rgb` as the protocol carries a color."""
    return debug_pb2.Color(r=rgb[0], g=rgb[1], b=rgb[2])
