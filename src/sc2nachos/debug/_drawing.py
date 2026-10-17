"""A drawing `api.debug` keeps on the game's screen until its steps have passed."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from s2clientprotocol import common_pb2, debug_pb2

from sc2nachos.units import Unit

if TYPE_CHECKING:
    from collections.abc import Sequence

# Where a drawing stands: a point fixed when it was drawn, or a unit, which the drawing follows.
type _Anchor = tuple[float, float, float] | Unit[Any]
type _Color = tuple[int, int, int]
type _Shape = Literal["text", "screen_text", "line", "box", "sphere"]
# A drawing as it is sent: its shape, its points, its color, its text and its size or radius.
type _Spec = tuple[_Shape, tuple[tuple[float, float, float], ...], _Color, str, float]


@dataclass(frozen=True, slots=True)
class _Drawing:
    """One drawing, kept on screen until the step `until`."""

    shape: _Shape
    anchors: tuple[_Anchor, ...]
    color: _Color
    until: int
    text: str = ""
    size: float = 0.0

    def is_at_a_dead_unit(self) -> bool:
        """Whether the drawing stands at a unit that is dead."""
        return any(isinstance(anchor, Unit) and anchor.is_dead for anchor in self.anchors)

    def spec(self) -> _Spec:
        """The drawing as it is sent now, a unit it follows read where it stands."""
        points = tuple(_where(anchor) for anchor in self.anchors)
        return (self.shape, points, self.color, self.text, self.size)


def _where(anchor: _Anchor) -> tuple[float, float, float]:
    """Where `anchor` stands now."""
    if isinstance(anchor, Unit):
        position = anchor.position
        return (position.x, position.y, anchor.height)
    return anchor


def _draw_command(specs: Sequence[_Spec]) -> debug_pb2.DebugCommand:
    """The command that shows `specs` on the screen, in place of whatever it showed; none clears it."""
    draw = debug_pb2.DebugDraw()
    for shape, points, (red, green, blue), text, size in specs:
        color = debug_pb2.Color(r=red, g=green, b=blue)
        match shape:
            case "text":
                draw.text.add(color=color, text=text, world_pos=_point(points[0]), size=int(size))
            case "screen_text":
                x, y, _ = points[0]
                draw.text.add(color=color, text=text, virtual_pos=common_pb2.Point(x=x, y=y), size=int(size))
            case "line":
                draw.lines.add(color=color, line=debug_pb2.Line(p0=_point(points[0]), p1=_point(points[1])))
            case "box":
                draw.boxes.add(color=color, min=_point(points[0]), max=_point(points[1]))
            case "sphere":
                draw.spheres.add(color=color, p=_point(points[0]), r=size)
    return debug_pb2.DebugCommand(draw=draw)


def _point(point: tuple[float, float, float]) -> common_pb2.Point:
    return common_pb2.Point(x=point[0], y=point[1], z=point[2])
