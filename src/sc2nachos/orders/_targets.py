"""An order's target: reading it, checking it, and storing it as the game will."""

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
    point = coordinates(target)
    return Point((as_sent(point[0]), as_sent(point[1])))


def as_sent(coordinate: float) -> float:
    """`coordinate` as the protocol carries it: a 32-bit float.

    Everything the game reports is already a 32-bit float widened to a Python float, so only outgoing points are
    ever cut. A point sent unrounded does not come back as the number that was sent.
    """
    return float(numpy.float32(coordinate))


def order_target(unit: OwnUnit[Any], order: raw_pb2.UnitOrder) -> Target | None:
    """The target of an order `unit` reports. A unit target is looked up in the tracker that holds `unit`."""
    match order.WhichOneof("target"):
        case "target_world_space_pos":
            point = order.target_world_space_pos
            return Point((point.x, point.y))
        case "target_unit_tag":
            return unit._tracker.unit_tracker.by_tag(order.target_unit_tag)
        case _:
            return None


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
