"""What every field shares: a value at every position on the map."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from sc2nachos.geometry._area import Area
    from sc2nachos.geometry._point import Point, PointLike


class Field[T: float](Protocol):
    """A value at every position on the map, such as a threat or a mask: read at a point, as the smallest or the
    largest over an area and where it lies, or as its slope. `Grid` holds one value per tile, and `SourceField` adds up
    circles and rectangles.

    A point or an area the field does not cover raises `IndexError`, and an empty area `ValueError`.
    """

    def value_at(self, point: PointLike) -> T:
        """The value at `point`."""
        ...

    def min(self, *, within: Area) -> T:
        """The smallest value anywhere in `within`."""
        ...

    def max(self, *, within: Area) -> T:
        """The largest value anywhere in `within`."""
        ...

    def argmin(self, *, within: Area) -> Point:
        """A point where the value within `within` is smallest. A grid answers the center of that tile, which
        `within` may not hold, and a source field one of the points it reads, which `within` holds."""
        ...

    def argmax(self, *, within: Area) -> Point:
        """A point where the value within `within` is largest, as `argmin` answers one."""
        ...

    def gradient_at(self, point: PointLike) -> tuple[float, float]:
        """How fast the value rises at `point`, per map unit, along x and along y, together pointing the way it rises
        fastest. Raises `TypeError` for a field of booleans."""
        ...
