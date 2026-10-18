"""A drawing `api.debug` keeps on the game's screen."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, final

from s2clientprotocol import debug_pb2

from sc2nachos.units import Unit

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sc2nachos.debug._color import _Rgb
    from sc2nachos.debug._shapes import DebugBox, DebugLine, DebugMark, DebugSphere, DebugText, _ScreenText, _Vector

# Where a drawing stands: a point fixed when it was drawn, or a unit, which the drawing follows.
type _Anchor = _Vector | Unit[Any]


@final
@dataclass(frozen=True, slots=True)
class _PlacedShape:
    """One shape of a drawing, as it is drawn: its offset from the drawing's place, groups' included, its color and
    text size, and for a line, where it ends."""

    shape: DebugText | DebugBox | DebugSphere | DebugMark | DebugLine | _ScreenText
    offset: _Vector
    color: _Rgb
    size: int
    to: _Anchor | None = None


@final
class Drawing:
    """A drawing on the game's screen, shown until its steps have passed, a unit it stands at is dead, or it is
    removed. `api.debug.draw` and `draw_text_on_screen` return it."""

    __slots__ = ("_anchor", "_parts", "_removed", "_until")

    def __init__(self, anchor: _Anchor, parts: tuple[_PlacedShape, ...], *, until: int | None) -> None:
        """A drawing of `parts` at `anchor`, shown while the step is before `until`, or until removed if `None`."""
        self._anchor = anchor
        self._parts = parts
        self._until = until
        self._removed = False

    def remove(self) -> None:
        """Take the drawing off the screen at the next refresh. Removing it again does nothing."""
        self._removed = True

    def _shown_at(self, step: int) -> bool:
        """Whether the drawing is still shown at `step`."""
        if self._removed or (self._until is not None and step >= self._until):
            return False
        anchors = (self._anchor, *(part.to for part in self._parts))
        return not any(isinstance(anchor, Unit) and anchor.is_dead for anchor in anchors)

    def _draw(self, draw: debug_pb2.DebugDraw) -> None:
        """Add the drawing to `draw`, a unit it follows read where it stands now."""
        x, y, z = _where(self._anchor)
        for part in self._parts:
            dx, dy, dz = part.offset
            end = None if part.to is None else _where(part.to)
            part.shape._draw(draw, (x + dx, y + dy, z + dz), part.color, part.size, end)


def _where(anchor: _Anchor) -> tuple[float, float, float]:
    """Where `anchor` stands now."""
    if isinstance(anchor, Unit):
        position = anchor.position
        return (position.x, position.y, anchor.height)
    return anchor


def _draw_all(drawings: Sequence[Drawing]) -> debug_pb2.DebugDraw:
    """What shows `drawings` on the screen, each unit they follow read where it stands now."""
    draw = debug_pb2.DebugDraw()
    for drawing in drawings:
        drawing._draw(draw)
    return draw
