"""An order's target: reading it, checking it, and storing it as the game will."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sc2nachos.constants import POINT_PRECISION
from sc2nachos.gamedata import TargetType
from sc2nachos.geometry import Point
from sc2nachos.units import Unit

if TYPE_CHECKING:
    from sc2nachos.gamedata import AbilityData
    from sc2nachos.geometry import PointLike
    from sc2nachos.ids import AbilityId
    from sc2nachos.units import Target

# What each target type takes, for the error message when an order is aimed at the wrong thing.
_TARGETS_WANTED = {
    TargetType.NOTHING: "no target",
    TargetType.POINT: "a point",
    TargetType.UNIT: "a unit",
    TargetType.POINT_OR_UNIT: "a point or a unit",
    TargetType.POINT_OR_NOTHING: "a point or no target",
}


def aimed_at(target: PointLike | Unit[Any] | None) -> Target | None:
    """An order's target as the game will read it: the unit itself, or the ground point.

    Any height is dropped: the game takes a target on the ground and reports a height of zero for every one. The
    coordinates are cut to the 32-bit floats the protocol carries, so the point an order holds is the one the game
    is given and the one it reports back.
    """
    if target is None or isinstance(target, Unit):
        return target
    return Point._as_sent(target)


def check_target(ability: AbilityId, target: Target | None, row: AbilityData | None) -> None:
    """Raise `TypeError` if `ability` cannot be aimed at `target`. An ability with no row is left to the game."""
    if row is None:
        return
    wanted = row.target_type
    takes = {
        TargetType.NOTHING: target is None,
        TargetType.POINT: isinstance(target, Point),
        TargetType.UNIT: isinstance(target, Unit),
        TargetType.POINT_OR_UNIT: target is not None,
        TargetType.POINT_OR_NOTHING: not isinstance(target, Unit),
    }
    if not takes[wanted]:
        aimed = "no target" if target is None else "a point" if isinstance(target, Point) else "a unit"
        raise TypeError(f"{ability.name} takes {_TARGETS_WANTED[wanted]}, and was given {aimed}")


def same_point(target: Point, x: float, y: float) -> bool:
    """Whether a point the game reports is the one ordered. The game keeps a point to `POINT_PRECISION`, rounded
    down."""
    return target.rounded_down(step=POINT_PRECISION) == Point((x, y)).rounded_down(step=POINT_PRECISION)


def same_target(one: Target | None, other: Target | None) -> bool:
    """Whether two orders are aimed at the same thing: the same unit, the same point as the game keeps it, or
    nothing."""
    if isinstance(one, Point) and isinstance(other, Point):
        return same_point(one, other.x, other.y)
    return one is other
