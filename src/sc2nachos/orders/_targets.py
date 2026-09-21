"""What an order is aimed at: reading it, checking it, and keeping it as the game will."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy

from sc2nachos.constants import POINT_PRECISION
from sc2nachos.gamedata import TargetType
from sc2nachos.geometry import Point
from sc2nachos.geometry._point import coordinates
from sc2nachos.units import Unit

if TYPE_CHECKING:
    from s2clientprotocol import raw_pb2

    from sc2nachos.gamedata import AbilityData
    from sc2nachos.geometry import PointLike
    from sc2nachos.ids import AbilityId
    from sc2nachos.units import OwnUnit, Target

# What each target type takes, for the message an order aimed at the wrong thing is refused with.
_TARGETS_WANTED = {
    TargetType.NOTHING: "no target",
    TargetType.POINT: "a point",
    TargetType.UNIT: "a unit",
    TargetType.POINT_OR_UNIT: "a point or a unit",
    TargetType.POINT_OR_NOTHING: "a point or no target",
}


def aimed_at(target: PointLike | Unit[Any] | None) -> Target | None:
    """An order's target: the unit itself, or the ground point it is aimed at, as the game will read it.

    A height is left behind, since the game takes a target on the ground and reports a height of zero for every one.
    What is left is cut to the 32 bits the protocol carries a coordinate in, so that the point an order holds is the
    one the game is given, and the one it reports back.
    """
    if target is None or isinstance(target, Unit):
        return target
    point = coordinates(target)
    return Point((as_sent(point[0]), as_sent(point[1])))


def as_sent(coordinate: float) -> float:
    """A coordinate as the protocol carries it, which is a 32-bit float.

    Everything the game reports is one already, widened to a Python float, so only a point going out is ever cut
    this way: what comes back from a point sent unrounded is not the number that was sent.
    """
    return float(numpy.float32(coordinate))


def order_target(unit: OwnUnit[Any], order: raw_pb2.UnitOrder) -> Target | None:
    """What an order a unit reports is aimed at, naming a unit through the tracker that holds `unit`."""
    match order.WhichOneof("target"):
        case "target_world_space_pos":
            point = order.target_world_space_pos
            return Point((point.x, point.y))
        case "target_unit_tag":
            return unit._tracker.unit_tracker.by_tag(order.target_unit_tag)
        case _:
            return None


def check_target(ability: AbilityId, target: Target | None, row: AbilityData | None) -> None:
    """Raise `TypeError` where `ability` cannot be aimed at `target`. An ability with no row is left to the game."""
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
    """Whether a point the game reports is the one ordered, which it keeps to `POINT_PRECISION`, cut down."""
    return target.rounded_down(step=POINT_PRECISION) == Point((x, y)).rounded_down(step=POINT_PRECISION)
