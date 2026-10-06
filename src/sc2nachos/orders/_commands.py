"""Orders as the protocol carries them: the actions in a turn's request."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from s2clientprotocol import common_pb2, raw_pb2, sc2api_pb2

from sc2nachos.geometry import Point

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sc2nachos.ids import AbilityId
    from sc2nachos.orders._order import Order
    from sc2nachos.units import OwnUnit, Target


def create_unit_command_actions(
    order: Order[Any], units: Sequence[OwnUnit[Any]], sent_as: AbilityId | None
) -> list[sc2api_pb2.Action]:
    """The raw commands giving `order` to `units`: one, or, for a custom ability, one per unit giving it `sent_as`
    aimed at itself."""
    if sent_as is None:
        return [_unit_command_action(order.ability, [unit.tag for unit in units], order.target, queued=order.queued)]
    return [_unit_command_action(sent_as, [unit.tag], unit, queued=order.queued) for unit in units]


def _unit_command_action(
    ability: AbilityId, tags: list[int], target: Target | None, *, queued: bool
) -> sc2api_pb2.Action:
    """One raw command giving `ability` to the units of `tags`, aimed at `target`."""
    if target is None:
        command = raw_pb2.ActionRawUnitCommand(ability_id=ability, unit_tags=tags, queue_command=queued)
    elif isinstance(target, Point):
        command = raw_pb2.ActionRawUnitCommand(
            ability_id=ability,
            unit_tags=tags,
            queue_command=queued,
            target_world_space_pos=common_pb2.Point2D(x=target[0], y=target[1]),
        )
    else:
        command = raw_pb2.ActionRawUnitCommand(
            ability_id=ability, unit_tags=tags, queue_command=queued, target_unit_tag=target.tag
        )
    return sc2api_pb2.Action(action_raw=raw_pb2.ActionRaw(unit_command=command))


def create_camera_move_action(at: Point) -> sc2api_pb2.Action:
    """The action that moves this player's camera."""
    center = common_pb2.Point(x=at[0], y=at[1])
    move = raw_pb2.ActionRawCameraMove(center_world_space=center)
    return sc2api_pb2.Action(action_raw=raw_pb2.ActionRaw(camera_move=move))
