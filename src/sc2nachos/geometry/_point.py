"""Points in the game's coordinate space."""

from __future__ import annotations

import math
import numbers
import operator
from collections.abc import Iterable
from typing import TYPE_CHECKING, Self, Union, final

# Imported for the check in `coordinates`. Safe at runtime: `_area` only imports this module for annotations.
from sc2nachos.geometry._area import Area

if TYPE_CHECKING:
    from collections.abc import Callable

    from s2clientprotocol import common_pb2


# Coordinates on the ground plane, or on it with a height: anything a point *is*.
#
# Something that merely *has* a point is deliberately not one, and the caller names the point it means. An area
# has a `.center`; a unit has a `.position`, read at that moment — a shape built straight from a moving object
# would snapshot it and then silently stop following it.
PointLike = Union["Point", "Point3D", tuple[float, float], tuple[float, float, float]]


# Scalars accepted by point arithmetic. `numbers.Real` catches numpy scalars, which subclass neither `int` nor
# `float` — except `float64`. It goes last because the ABC check is far slower than the concrete ones.
SCALAR_TYPES = (int, float, numbers.Real)


def coordinates(value: PointLike) -> PointLike:
    """`value` itself, once it is known to be coordinates. A caller that is not type-checked fails here.

    A `Tile` is rejected despite being a coordinate pair: it is an address, not the point it stands for.
    """
    # Points first: the common case, and a concrete class is far quicker to test than an ABC.
    if isinstance(value, _PointND) or type(value) is tuple:
        return value
    if isinstance(value, Area):
        raise TypeError(f"a {type(value).__name__} is an area, not a point — pass its .center")
    if isinstance(value, tuple):
        return value
    raise TypeError(f"expected a point or a coordinate tuple, got {type(value).__name__}")


class _PointND(tuple[float, ...]):
    """A point of any dimensionality: the coordinate math shared by `Point` and `Point3D`.

    Operations preserve the operand's type, and reject operands of a different dimensionality. The arithmetic
    operators are vector operations, not tuple ones: `point * 3` scales rather than repeating the sequence.
    """

    __slots__ = ()

    @property
    def x(self) -> float:
        """The horizontal coordinate."""
        return self[0]

    @property
    def y(self) -> float:
        """The vertical coordinate."""
        return self[1]

    @property
    def position(self) -> Self:
        """The point itself, so points and units can be used interchangeably."""
        return self

    @property
    def length(self) -> float:
        """Distance from the origin, on the ground plane."""
        return math.hypot(self[0], self[1])

    @property
    def length_squared(self) -> float:
        """Squared distance from the origin, for when only the ordering matters."""
        return self[0] ** 2 + self[1] ** 2

    def dot(self, other: PointLike) -> float:
        """The dot product on the ground plane."""
        position = coordinates(other)
        return self[0] * position[0] + self[1] * position[1]

    @property
    def normalized(self) -> Self:
        """The point scaled to unit length on the ground plane."""
        length = self.length
        if not length:
            raise ZeroDivisionError("cannot normalize a zero-length point")
        return self / length

    def distance_to(self, other: PointLike) -> float:
        """Distance to another point, on the ground plane."""
        position = coordinates(other)
        return math.hypot(self[0] - position[0], self[1] - position[1])

    def distance_to_squared(self, other: PointLike) -> float:
        """Squared ground-plane distance, for when only the ordering matters."""
        position = coordinates(other)
        return (self[0] - position[0]) ** 2 + (self[1] - position[1]) ** 2

    def is_closer_than(self, distance: float, other: PointLike) -> bool:
        """Whether another point lies strictly within `distance`."""
        return self.distance_to_squared(other) < distance**2

    def towards(self, other: PointLike, distance: float = 1, *, limit: bool = False) -> Self:
        """The point moved `distance` toward another of the same dimensionality.

        `distance` is measured on the ground plane. With `limit`, never overshoots. A negative distance moves
        away.
        """
        position = coordinates(other)
        # Checked up front rather than left to the zip below, which the zero-separation early return would skip.
        if len(position) != len(self):
            raise self._dimension_error(position)
        separation = self.distance_to(position)
        if not separation:
            return self
        if limit:
            distance = min(distance, separation)
        fraction = distance / separation
        return type(self)(value + fraction * (target - value) for value, target in zip(self, position, strict=True))

    def direction_vector(self, other: PointLike) -> Point:
        """The unit vector on the ground plane pointing from this point to another."""
        position = coordinates(other)
        return Point((position[0] - self[0], position[1] - self[1])).normalized

    def angle_to(self, other: PointLike) -> float:
        """The angle in radians to another point, from the positive x-axis."""
        position = coordinates(other)
        return math.atan2(position[1] - self[1], position[0] - self[0])

    def rotated(self, angle: float, *, around: PointLike | None = None) -> Self:
        """The point rotated by `angle` radians about the vertical axis through `around`, or the origin.

        Only the pivot's x and y are read, so it may be of any dimensionality. Height is carried through.
        """
        pivot = (0.0, 0.0) if around is None else coordinates(around)
        sin, cos = math.sin(angle), math.cos(angle)
        dx, dy = self[0] - pivot[0], self[1] - pivot[1]
        rotated = (pivot[0] + cos * dx - sin * dy, pivot[1] + sin * dx + cos * dy)
        return type(self)(rotated + tuple(self[2:]))

    def snapped(self, *, step: float) -> Self:
        """The nearest point on a lattice of `step` tiles. Height is carried through.

        Each axis rounds on its own, so `step=0.5` gives the nearest of a tile's four corners, its four
        edge midpoints and its center, and `step=1` gives the nearest corner rather than the nearest center.
        """
        if step <= 0:
            raise ValueError(f"a lattice step must be positive, got {step}")
        snapped = (round(self[0] / step) * step, round(self[1] / step) * step)
        return type(self)(snapped + tuple(self[2:]))

    def rounded_down(self, *, step: float) -> Self:
        """The point on a lattice of `step` tiles at or below this one on each axis. Height is carried through.

        Where `snapped` rounds, this cuts down, which is what the game does to a point it is given: a move to
        x = 157.123456 is carried out at 157.123291, a whole `POINT_PRECISION` below (in game).
        """
        if step <= 0:
            raise ValueError(f"a lattice step must be positive, got {step}")
        cut = (math.floor(self[0] / step) * step, math.floor(self[1] / step) * step)
        return type(self)(cut + tuple(self[2:]))

    def closest[T: PointLike](self, points: Iterable[T]) -> T:
        """The nearest of the given points, returned as it was passed in."""
        candidates = list(points)
        if not candidates:
            raise ValueError("no points to choose from")
        return min(candidates, key=self.distance_to_squared)

    def furthest[T: PointLike](self, points: Iterable[T]) -> T:
        """The most distant of the given points, returned as it was passed in."""
        candidates = list(points)
        if not candidates:
            raise ValueError("no points to choose from")
        return max(candidates, key=self.distance_to_squared)

    def _dimension_error(self, position: tuple[float, ...]) -> ValueError:
        """The error for combining points of unequal dimensionality."""
        return ValueError(
            f"cannot combine {len(self)} coordinates with {len(position)}: "
            f"{type(self).__name__}{tuple(self)} and {tuple(position)}. "
            f"Convert explicitly with .ground or .with_height."
        )

    def _combine(self, other: PointLike | float, operation: Callable[[float, float], float]) -> Self:
        """Apply `operation` coordinate-wise against a point of equal dimensionality, or a scalar."""
        # Ordered so the common point-and-point case never reaches the expensive ABC check.
        if isinstance(other, tuple):
            position: tuple[float, ...] = other
        elif isinstance(other, SCALAR_TYPES):
            return type(self)([operation(value, other) for value in self])
        else:
            position = coordinates(other)
        try:
            return type(self)([operation(a, b) for a, b in zip(self, position, strict=True)])
        except ValueError:
            raise self._dimension_error(position) from None

    def __add__(self, other: PointLike | float) -> Self:  # pyright: ignore[reportIncompatibleMethodOverride]
        return self._combine(other, operator.add)

    def __sub__(self, other: PointLike | float) -> Self:
        return self._combine(other, operator.sub)

    def __mul__(self, other: PointLike | float) -> Self:  # pyright: ignore[reportIncompatibleMethodOverride]
        return self._combine(other, operator.mul)

    def __truediv__(self, other: PointLike | float) -> Self:
        return self._combine(other, operator.truediv)

    # Required, not conveniences: without them `(1, 2) + point` inherits tuple concatenation and `2 * point`
    # inherits sequence repetition, both silently returning a 4-tuple. Delegating to the forward operator keeps
    # `Point`'s fast path. Addition and multiplication are commutative, so no operand order is lost.

    def __radd__(self, other: PointLike | float) -> Self:
        return self.__add__(other)

    def __rmul__(self, other: PointLike | float) -> Self:  # pyright: ignore[reportIncompatibleMethodOverride]
        return self.__mul__(other)

    def __neg__(self) -> Self:
        return type(self)(-value for value in self)

    def __abs__(self) -> float:
        return self.length

    def __str__(self) -> str:
        return f"({', '.join(f'{value:.2f}' for value in self)})"

    def __repr__(self) -> str:
        return f"{type(self).__name__}({tuple(self)})"


@final
class Point(_PointND, tuple[float, float]):
    """An immutable 2D point, constructed from an iterable: `Point((3, 4))`.

    A tuple subclass, so it unpacks, hashes and compares like `(x, y)` and works as a dict key.
    """

    __slots__ = ()

    @classmethod
    def from_proto(cls, data: common_pb2.Point2D | common_pb2.PointI) -> Self:
        """Build from a protobuf 2D point message."""
        return cls((data.x, data.y))

    def with_height(self, z: float = 0.0) -> Point3D:
        """The point lifted to height `z`."""
        return Point3D((self[0], self[1], z))

    # Two-coordinate fast paths, ~2.5x quicker than the general implementation and used in per-unit loops.

    def __add__(self, other: PointLike | float) -> Point:  # pyright: ignore[reportIncompatibleMethodOverride]
        if isinstance(other, tuple) and len(other) == 2:
            return Point((self[0] + other[0], self[1] + other[1]))
        return super().__add__(other)

    def __sub__(self, other: PointLike | float) -> Point:
        if isinstance(other, tuple) and len(other) == 2:
            return Point((self[0] - other[0], self[1] - other[1]))
        return super().__sub__(other)


@final
class Point3D(_PointND, tuple[float, float, float]):
    """An immutable 3D point, used for terrain height and debug drawing."""

    __slots__ = ()

    @classmethod
    def from_proto(cls, data: common_pb2.Point) -> Self:
        """Build from a protobuf 3D point message."""
        return cls((data.x, data.y, data.z))

    @property
    def z(self) -> float:
        """The height coordinate."""
        return self[2]

    @property
    def ground(self) -> Point:
        """The point projected onto the ground plane, dropping its height."""
        return Point((self[0], self[1]))
