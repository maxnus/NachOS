"""An order as the protocol carries it: the actions a turn's request is made of."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from s2clientprotocol import common_pb2, raw_pb2, sc2api_pb2

from sc2nachos.geometry import Point

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sc2nachos.orders._order import Order
    from sc2nachos.units import OwnUnit


def create_unit_command_action(order: Order[Any], units: Sequence[OwnUnit[Any]]) -> sc2api_pb2.Action:
    """One raw command, giving `order` to `units`."""
    target = order.target
    tags = [unit.tag for unit in units]
    if target is None:
        command = raw_pb2.ActionRawUnitCommand(ability_id=order.ability, unit_tags=tags, queue_command=order.queued)
    elif isinstance(target, Point):
        command = raw_pb2.ActionRawUnitCommand(
            ability_id=order.ability,
            unit_tags=tags,
            queue_command=order.queued,
            target_world_space_pos=common_pb2.Point2D(x=target[0], y=target[1]),
        )
    else:
        command = raw_pb2.ActionRawUnitCommand(
            ability_id=order.ability, unit_tags=tags, queue_command=order.queued, target_unit_tag=target.tag
        )
    return sc2api_pb2.Action(action_raw=raw_pb2.ActionRaw(unit_command=command))


def create_camera_move_action(at: Point) -> sc2api_pb2.Action:
    """The action that moves this player's camera."""
    center = common_pb2.Point(x=at[0], y=at[1])
    move = raw_pb2.ActionRawCameraMove(center_world_space=center)
    return sc2api_pb2.Action(action_raw=raw_pb2.ActionRaw(camera_move=move))
