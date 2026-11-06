"""What every field shares: a value at every position on the map."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from sc2nachos.geometry._area import Area
    from sc2nachos.geometry._point import Point, PointLike


class Field[T: float](Protocol):
    """A value at every position on the map, such as a threat or a mask: read at a point, as the largest or the
    smallest over an area, or as its slope. `Grid` holds one value per tile.

    A point or an area the field does not cover raises `IndexError`.
    """

    def __getitem__(self, point: PointLike, /) -> T:
        """The value at `point`."""
        ...

    def max(self, *, within: Area) -> T:
        """The largest value anywhere in `within`. Raises `ValueError` for an empty area."""
        ...

    def min(self, *, within: Area) -> T:
        """The smallest value anywhere in `within`. Raises `ValueError` for an empty area."""
        ...

    def gradient_at(self, point: PointLike) -> Point:
        """How fast the value rises at `point`, per map unit, pointing the way it rises fastest. Raises `TypeError` for
        a field of booleans."""
        ...
