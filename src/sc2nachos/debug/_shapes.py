"""What `api.debug.draw` places at a point or a unit: shapes, as values, each offset from where it is drawn."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, final

from s2clientprotocol import common_pb2, debug_pb2

from sc2nachos._checks import is_whole_number
from sc2nachos.debug._color import Color, _Rgb, _rgb_of
from sc2nachos.debug._marker import Marker

if TYPE_CHECKING:
    from sc2nachos.geometry import PointLike
    from sc2nachos.units import Unit

type _Vector = tuple[float, float, float]

# A cross's arms run along the diagonals, so each end lies this far along both axes per unit of its radius.
_DIAGONAL = math.sqrt(0.5)
_SIDES = {Marker.TRIANGLE: 3, Marker.SQUARE: 4, Marker.PENTAGON: 5, Marker.HEXAGON: 6}


@final
@dataclass(frozen=True, slots=True)
class DebugText:
    """Text written in the world, `size` pixels high, or `api.debug.default_text_size` high if `None`."""

    text: str
    size: int | None = None
    color: Color | tuple[int, int, int] | None = None
    offset: tuple[float, float, float] = (0.0, 0.0, 0.0)
    # The red, green and blue of `color`, if it is given.
    _rgb: _Rgb | None = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Raises `ValueError` for a size that is not a whole number of pixels, at least 1, and as every shape does."""
        if self.size is not None and (not is_whole_number(self.size) or self.size < 1):
            raise ValueError(f"text is a whole number of pixels high, at least 1, not {self.size!r}")
        object.__setattr__(self, "_rgb", _rgb_if_given(self.color))
        _check_offset(self.offset)

    def _draw(self, draw: debug_pb2.DebugDraw, at: _Vector, rgb: _Rgb, size: int, end: _Vector | None) -> None:
        draw.text.add(color=_proto_color(rgb), text=self.text, world_pos=_point(at), size=size)


@final
@dataclass(frozen=True, slots=True)
class DebugBox:
    """A box `width` across, `depth` deep and `height` high, centered on where it is placed and standing on it."""

    width: float
    depth: float
    height: float = 1.0
    color: Color | tuple[int, int, int] | None = None
    offset: tuple[float, float, float] = (0.0, 0.0, 0.0)
    # The red, green and blue of `color`, if it is given.
    _rgb: _Rgb | None = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Raises `ValueError` for a side that is not a finite number above 0, and as every shape does."""
        for side in (self.width, self.depth, self.height):
            _check_length(side, "a box's side")
        object.__setattr__(self, "_rgb", _rgb_if_given(self.color))
        _check_offset(self.offset)

    def _draw(self, draw: debug_pb2.DebugDraw, at: _Vector, rgb: _Rgb, size: int, end: _Vector | None) -> None:
        x, y, z = at
        low = common_pb2.Point(x=x - self.width / 2, y=y - self.depth / 2, z=z)
        high = common_pb2.Point(x=x + self.width / 2, y=y + self.depth / 2, z=z + self.height)
        draw.boxes.add(color=_proto_color(rgb), min=low, max=high)


@final
@dataclass(frozen=True, slots=True)
class DebugSphere:
    """A sphere of `radius` around where it is placed."""

    radius: float
    color: Color | tuple[int, int, int] | None = None
    offset: tuple[float, float, float] = (0.0, 0.0, 0.0)
    # The red, green and blue of `color`, if it is given.
    _rgb: _Rgb | None = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Raises `ValueError` for a radius that is not a finite number above 0, and as every shape does."""
        _check_length(self.radius, "a radius")
        object.__setattr__(self, "_rgb", _rgb_if_given(self.color))
        _check_offset(self.offset)

    def _draw(self, draw: debug_pb2.DebugDraw, at: _Vector, rgb: _Rgb, size: int, end: _Vector | None) -> None:
        draw.spheres.add(color=_proto_color(rgb), p=_point(at), r=self.radius)


@final
@dataclass(frozen=True, slots=True)
class DebugMark:
    """A `Marker` around where it is placed, its arms or its corners `radius` from it, level with it."""

    marker: Marker
    radius: float = 0.5
    color: Color | tuple[int, int, int] | None = None
    offset: tuple[float, float, float] = (0.0, 0.0, 0.0)
    # The red, green and blue of `color`, if it is given.
    _rgb: _Rgb | None = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Raises `ValueError` for a radius that is not a finite number above 0, and as every shape does."""
        _check_length(self.radius, "a radius")
        object.__setattr__(self, "_rgb", _rgb_if_given(self.color))
        _check_offset(self.offset)

    def _draw(self, draw: debug_pb2.DebugDraw, at: _Vector, rgb: _Rgb, size: int, end: _Vector | None) -> None:
        x, y, z = at
        color = _proto_color(rgb)
        for (x0, y0), (x1, y1) in _segments_of_marker(self.marker, self.radius):
            p0, p1 = common_pb2.Point(x=x + x0, y=y + y0, z=z), common_pb2.Point(x=x + x1, y=y + y1, z=z)
            draw.lines.add(color=color, line=debug_pb2.Line(p0=p0, p1=p1))


@final
@dataclass(frozen=True, slots=True)
class DebugLine:
    """A line from where it is placed to `to`, a point placed as `api.debug.draw` places its own, or a unit, which it
    follows, offset by `to_offset`."""

    to: PointLike | Unit[Any]
    color: Color | tuple[int, int, int] | None = None
    offset: tuple[float, float, float] = (0.0, 0.0, 0.0)
    to_offset: tuple[float, float, float] = (0.0, 0.0, 0.0)
    # The red, green and blue of `color`, if it is given.
    _rgb: _Rgb | None = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Raises `ValueError` as every shape does, for `to_offset` too."""
        object.__setattr__(self, "_rgb", _rgb_if_given(self.color))
        _check_offset(self.offset)
        _check_offset(self.to_offset)

    def _draw(self, draw: debug_pb2.DebugDraw, at: _Vector, rgb: _Rgb, size: int, end: _Vector | None) -> None:
        if end is None:
            return
        x, y, z = end
        dx, dy, dz = self.to_offset
        reached = common_pb2.Point(x=x + dx, y=y + dy, z=z + dz)
        draw.lines.add(color=_proto_color(rgb), line=debug_pb2.Line(p0=_point(at), p1=reached))


@final
class DebugGroup:
    """Shapes drawn together, each offset from the group, and the group offset from where it is placed. A shape with no
    color of its own takes the group's. Groups compare equal by what they hold, as every shape does."""

    __slots__ = ("_color", "_offset", "_rgb", "_shapes")

    def __init__(
        self,
        *shapes: DebugShape,
        color: Color | tuple[int, int, int] | None = None,
        offset: tuple[float, float, float] = (0.0, 0.0, 0.0),
    ) -> None:
        """Raises `ValueError` for no shapes, and as every shape does."""
        if not shapes:
            raise ValueError("a group holds at least one shape")
        self._rgb = _rgb_if_given(color)
        _check_offset(offset)
        self._shapes = shapes
        self._color = color
        self._offset = offset

    @property
    def shapes(self) -> tuple[DebugShape, ...]:
        """The shapes drawn together."""
        return self._shapes

    @property
    def color(self) -> Color | tuple[int, int, int] | None:
        """The color of each shape with none of its own, or `None` for the drawing's."""
        return self._color

    @property
    def offset(self) -> tuple[float, float, float]:
        """How far the group is drawn from where it is placed, across, up the map and up."""
        return self._offset

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, DebugGroup):
            return NotImplemented
        return (self._shapes, self._color, self._offset) == (other._shapes, other._color, other._offset)

    def __hash__(self) -> int:
        return hash((self._shapes, self._color, self._offset))

    def __repr__(self) -> str:
        return f"DebugGroup({', '.join(map(repr, self._shapes))}, color={self._color!r}, offset={self._offset!r})"


type DebugShape = DebugText | DebugBox | DebugSphere | DebugMark | DebugLine | DebugGroup


@final
@dataclass(frozen=True, slots=True)
class _ScreenText:
    """Text written on the screen at `at`, from 0 to 1 across and down."""

    text: str
    at: tuple[float, float]

    def _draw(self, draw: debug_pb2.DebugDraw, at: _Vector, rgb: _Rgb, size: int, end: _Vector | None) -> None:
        position = common_pb2.Point(x=self.at[0], y=self.at[1])
        draw.text.add(color=_proto_color(rgb), text=self.text, virtual_pos=position, size=size)


def _segments_of_marker(marker: Marker, radius: float) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """The lines of `marker` of `radius`, each between two offsets from its center."""
    arm = radius * _DIAGONAL
    cross = [((-arm, -arm), (arm, arm)), ((-arm, arm), (arm, -arm))]
    plus = [((-radius, 0.0), (radius, 0.0)), ((0.0, -radius), (0.0, radius))]
    match marker:
        case Marker.CROSS:
            return cross
        case Marker.PLUS:
            return plus
        case Marker.STAR:
            return cross + plus
        case _:
            sides = _SIDES[marker]
            # Starting half a side before straight down puts a side level at the bottom.
            first = -math.pi / 2 - math.pi / sides
            angles = [first + math.tau * k / sides for k in range(sides)]
            corners = [(radius * math.cos(angle), radius * math.sin(angle)) for angle in angles]
            return list(zip(corners, corners[1:] + corners[:1], strict=True))


def _rgb_if_given(color: Color | _Rgb | None) -> _Rgb | None:
    """`color`'s red, green and blue, or `None` if no color is given. Raises `ValueError` for what is not a `Color` or
    red, green and blue, each a whole number from 0 to 255."""
    return None if color is None else _rgb_of(color)


def _check_offset(offset: _Vector) -> None:
    """Raises `ValueError` for an offset that is not three finite numbers."""
    if len(offset) != 3 or not all(math.isfinite(part) for part in offset):
        raise ValueError(f"an offset is three finite numbers, across, up the map and up, not {offset!r}")


def _check_length(length: float, what: str) -> None:
    if not 0 < length < math.inf:
        raise ValueError(f"{what} is a finite number above 0, not {length!r}")


def _proto_color(rgb: _Rgb) -> debug_pb2.Color:
    return debug_pb2.Color(r=rgb[0], g=rgb[1], b=rgb[2])


def _point(point: _Vector) -> common_pb2.Point:
    return common_pb2.Point(x=point[0], y=point[1], z=point[2])
