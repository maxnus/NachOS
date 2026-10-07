"""Orders as the protocol carries them: the actions in a turn's request."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from s2clientprotocol import common_pb2, raw_pb2, sc2api_pb2

from sc2nachos.gamedata._sent_as import Aim, SentAs
from sc2nachos.geometry import Point

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sc2nachos.ids import UnitTypeId
    from sc2nachos.orders._order import Order
    from sc2nachos.units import OwnUnit, Target


def create_unit_command_actions(
    order: Order[Any], units: Sequence[OwnUnit[Any]], sent_as: Mapping[UnitTypeId, SentAs]
) -> list[tuple[sc2api_pb2.Action, tuple[OwnUnit[Any], ...]]]:
    """The raw commands giving `order` to `units`, each with the units it names: one naming the units that take the
    order's own ability, then, for each ability `sent_as` sends their types instead, one naming its units, or one per
    unit where it is aimed at the unit itself."""
    own: list[OwnUnit[Any]] = []
    sent: dict[SentAs, list[OwnUnit[Any]]] = {}
    for unit in units:
        if (sending := sent_as.get(unit.type_id)) is None:
            own.append(unit)
        else:
            sent.setdefault(sending, []).append(unit)
    commands = [_naming(order.ability, own, order.target, queued=order.queued)] if own else []
    for sending, group in sent.items():
        match sending.aim:
            case Aim.ITSELF:
                commands += [_naming(sending.ability, [unit], unit, queued=order.queued) for unit in group]
            case Aim.TARGET:
                commands.append(_naming(sending.ability, group, order.target, queued=order.queued))
            case Aim.NOTHING:
                commands.append(_naming(sending.ability, group, None, queued=order.queued))
    return commands


def _naming(
    ability: int, units: Sequence[OwnUnit[Any]], target: Target | None, *, queued: bool
) -> tuple[sc2api_pb2.Action, tuple[OwnUnit[Any], ...]]:
    """One raw command giving `ability` to `units`, aimed at `target`, with the units it names."""
    return _unit_command_action(ability, [unit.tag for unit in units], target, queued=queued), tuple(units)


def _unit_command_action(ability: int, tags: list[int], target: Target | None, *, queued: bool) -> sc2api_pb2.Action:
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
