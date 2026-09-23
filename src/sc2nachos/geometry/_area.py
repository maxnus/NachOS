"""The interface every shape shares."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from sc2nachos.geometry._point import Point, PointLike
    from sc2nachos.geometry._shapes import Rectangle, TileSet


class Area(ABC):
    """A patch of ground: somewhere to attack, to scout, or to filter positions by.

    An area may be empty. `center`, `random_point`, `closest_point_to` and `bounding_rectangle` raise `ValueError` on
    an empty area; the rest work normally.
    """

    # Without this every subclass carries a __dict__, whatever slots it declares itself.
    __slots__ = ()

    @property
    @abstractmethod
    def center(self) -> Point:
        """The middle of the area."""

    @property
    @abstractmethod
    def area(self) -> float:
        """The ground covered, in square game units."""

    @abstractmethod
    def random_point(self) -> Point:
        """A point drawn uniformly from the area."""

    @abstractmethod
    def __contains__(self, point: PointLike) -> bool:
        """Whether `point` lies inside. Raises `TypeError` on anything but a point."""

    @abstractmethod
    def translated(self, offset: PointLike) -> Self:
        """The area shifted by `offset`, keeping its shape."""

    @abstractmethod
    def closest_point_to(self, point: PointLike) -> tuple[Point, float]:
        """The point of the area nearest to `point`, and the distance to it.

        May lie on an edge the area excludes, so it is not necessarily in the area.
        """

    @abstractmethod
    def bounding_rectangle(self) -> Rectangle:
        """The smallest rectangle containing the area."""

    @abstractmethod
    def tiles(self) -> TileSet:
        """Every tile whose center lies in the area."""
