"""A drawing `api.debug` keeps on the game's screen, and the shapes it comes in."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from s2clientprotocol import common_pb2, debug_pb2

from sc2nachos.units import Unit

if TYPE_CHECKING:
    from collections.abc import Sequence

# Where a drawing stands: a point fixed when it was drawn, or a unit, which the drawing follows.
type _Anchor = tuple[float, float, float] | Unit[Any]
type _Rgb = tuple[int, int, int]


class Drawing(ABC):
    """A drawing on the game's screen, shown until its steps have passed, a unit it stands at is dead, or it is
    removed. `api.debug`'s draw methods return it."""

    __slots__ = ("_anchors", "_color", "_removed", "_until")

    def __init__(self, anchors: tuple[_Anchor, ...], color: _Rgb, *, until: int | None) -> None:
        """A drawing at `anchors` in `color`, shown while the step is before `until`, or until removed if `None`."""
        self._anchors = anchors
        self._color = color
        self._until = until
        self._removed = False

    def remove(self) -> None:
        """Take the drawing off the screen at the next refresh. Removing it again does nothing."""
        self._removed = True

    def _lasts_at(self, step: int) -> bool:
        """Whether the drawing is still shown at `step`."""
        if self._removed or (self._until is not None and step >= self._until):
            return False
        return not any(isinstance(anchor, Unit) and anchor.is_dead for anchor in self._anchors)

    @abstractmethod
    def _add_to(self, draw: debug_pb2.DebugDraw) -> None:
        """Add the drawing to `draw`, a unit it follows read where it stands now."""

    def _proto_color(self) -> debug_pb2.Color:
        red, green, blue = self._color
        return debug_pb2.Color(r=red, g=green, b=blue)


class _Text(Drawing):
    """Text written in the world."""

    __slots__ = ("_size", "_text")

    def __init__(self, text: str, at: _Anchor, color: _Rgb, *, size: int, until: int | None) -> None:
        super().__init__((at,), color, until=until)
        self._text = text
        self._size = size

    def _add_to(self, draw: debug_pb2.DebugDraw) -> None:
        position = _point(_where(self._anchors[0]))
        draw.text.add(color=self._proto_color(), text=self._text, world_pos=position, size=self._size)


class _ScreenText(Drawing):
    """Text written on the screen, at a point from 0 to 1 across and down."""

    __slots__ = ("_size", "_text")

    def __init__(self, text: str, at: tuple[float, float], color: _Rgb, *, size: int, until: int | None) -> None:
        super().__init__(((at[0], at[1], 0.0),), color, until=until)
        self._text = text
        self._size = size

    def _add_to(self, draw: debug_pb2.DebugDraw) -> None:
        x, y, _ = _where(self._anchors[0])
        position = common_pb2.Point(x=x, y=y)
        draw.text.add(color=self._proto_color(), text=self._text, virtual_pos=position, size=self._size)


class _Line(Drawing):
    """A line between two places."""

    __slots__ = ()

    def __init__(self, start: _Anchor, end: _Anchor, color: _Rgb, *, until: int | None) -> None:
        super().__init__((start, end), color, until=until)

    def _add_to(self, draw: debug_pb2.DebugDraw) -> None:
        start, end = (_point(_where(anchor)) for anchor in self._anchors)
        draw.lines.add(color=self._proto_color(), line=debug_pb2.Line(p0=start, p1=end))


class _Figure(Drawing):
    """Lines around a place, each between two offsets from it, level with the place."""

    __slots__ = ("_segments",)

    def __init__(
        self,
        center: _Anchor,
        segments: Sequence[tuple[tuple[float, float], tuple[float, float]]],
        color: _Rgb,
        *,
        until: int | None,
    ) -> None:
        super().__init__((center,), color, until=until)
        self._segments = tuple(segments)

    def _add_to(self, draw: debug_pb2.DebugDraw) -> None:
        x, y, z = _where(self._anchors[0])
        color = self._proto_color()
        for (x0, y0), (x1, y1) in self._segments:
            start, end = common_pb2.Point(x=x + x0, y=y + y0, z=z), common_pb2.Point(x=x + x1, y=y + y1, z=z)
            draw.lines.add(color=color, line=debug_pb2.Line(p0=start, p1=end))


class _Box(Drawing):
    """A box between two fixed corners."""

    __slots__ = ()

    def __init__(
        self, low: tuple[float, float, float], high: tuple[float, float, float], color: _Rgb, *, until: int | None
    ) -> None:
        super().__init__((low, high), color, until=until)

    def _add_to(self, draw: debug_pb2.DebugDraw) -> None:
        low, high = (_point(_where(anchor)) for anchor in self._anchors)
        draw.boxes.add(color=self._proto_color(), min=low, max=high)


class _Sphere(Drawing):
    """A sphere around a place."""

    __slots__ = ("_radius",)

    def __init__(self, center: _Anchor, radius: float, color: _Rgb, *, until: int | None) -> None:
        super().__init__((center,), color, until=until)
        self._radius = radius

    def _add_to(self, draw: debug_pb2.DebugDraw) -> None:
        draw.spheres.add(color=self._proto_color(), p=_point(_where(self._anchors[0])), r=self._radius)


def _where(anchor: _Anchor) -> tuple[float, float, float]:
    """Where `anchor` stands now."""
    if isinstance(anchor, Unit):
        position = anchor.position
        return (position.x, position.y, anchor.height)
    return anchor


def _draw(drawings: Sequence[Drawing]) -> debug_pb2.DebugDraw:
    """What shows `drawings` on the screen, each unit they follow read where it stands now."""
    draw = debug_pb2.DebugDraw()
    for drawing in drawings:
        drawing._add_to(draw)
    return draw


def _point(point: tuple[float, float, float]) -> common_pb2.Point:
    return common_pb2.Point(x=point[0], y=point[1], z=point[2])
