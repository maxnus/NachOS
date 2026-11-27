"""Commands as the protocol carries them: the actions in a turn's request."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, final

from s2clientprotocol import common_pb2, raw_pb2, sc2api_pb2

from sc2nachos.commands._targets import target_as_kept
from sc2nachos.gamedata._sent_as import SENT_UNCHANGED, SentAs
from sc2nachos.gamedata._techtree import ABILITIES_JOINING_OTHER_ORDERS, ABILITIES_SENT_AS_ANOTHER, MULTI_UNIT_ABILITIES
from sc2nachos.geometry import Point
from sc2nachos.units import Unit

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping, Sequence

    from sc2nachos.commands._command import Command
    from sc2nachos.ids import AbilityId, UnitTypeId
    from sc2nachos.units import OwnUnit, Target


@final
@dataclass(slots=True)
class ActionToSend:
    """One action of a turn's request: the game's `ability`, aimed at `target`, queued or not, given to the units of
    each command of `parts`."""

    ability: int
    target: Target | None
    queued: bool
    parts: list[tuple[Command, tuple[OwnUnit[Any], ...]]]
    """Each command the action carries, with the units it names for that command, in the order they joined it."""

    def to_action(self) -> sc2api_pb2.Action:
        """The action as the protocol carries it, naming the units of every part in turn."""
        tags = [unit.tag for _, units in self.parts for unit in units]
        return _unit_command_action(self.ability, tags, self.target, queued=self.queued)


def actions_giving(commands: Iterable[tuple[Command, tuple[OwnUnit[Any], ...]]]) -> list[ActionToSend]:
    """The actions giving each of `commands` to the units it goes out to, in the order they go out.

    A command of an ability every unit of a command carries out goes out as one action naming its units, or one for
    each ability it is sent as, and any other command as an action to each unit. An action of a command of an ability
    that joins other orders (`ABILITIES_JOINING_OTHER_ORDERS`) joins the last action before it of the same game
    ability, target as the game keeps it and queue flag, unless one of its units is named in that action or in one after
    it. So a unit's commands go out in the order it was given them, though another unit's may go out ahead of commands
    given before them, and no action names a unit twice. An action aimed at one of its own units joins nothing, and
    nothing joins it.
    """
    actions_to_send: list[ActionToSend] = []
    # By game ability, target as the game keeps it and queue flag, the index of the last action a command may join.
    last_joinable: dict[tuple[int, Target | None, bool], int] = {}
    # By unit id, the index of the last action naming the unit.
    last_naming: dict[int, int] = {}
    for command, ability, target, units in _actions_of_each(commands):
        key = _join_key(command, ability, target, units)
        at = -1 if key is None else last_joinable.get(key, -1)
        if at < 0 or any(last_naming.get(unit.id, -1) >= at for unit in units):
            at = len(actions_to_send)
            actions_to_send.append(ActionToSend(ability, target, command.queued, []))
            if key is not None:
                last_joinable[key] = at
        actions_to_send[at].parts.append((command, units))
        for unit in units:
            last_naming[unit.id] = at
    return actions_to_send


def _join_key(
    command: Command, ability: int, target: Target | None, units: tuple[OwnUnit[Any], ...]
) -> tuple[int, Target | None, bool] | None:
    """What an action of `command`, giving the game's `ability` at `target` to `units`, shares with each action it may
    join: the ability, the target as the game keeps it and the queue flag. `None` for an action that joins none and that
    none joins: of an ability that joins no other order, or aimed at one of its own units, as a transport's unload where
    it stands is."""
    if command.ability not in ABILITIES_JOINING_OTHER_ORDERS:
        return None
    if isinstance(target, Unit) and any(unit.id == target.id for unit in units):
        return None
    return ability, target_as_kept(target), command.queued


def _actions_of_each(
    commands: Iterable[tuple[Command, tuple[OwnUnit[Any], ...]]],
) -> Iterator[tuple[Command, int, Target | None, tuple[OwnUnit[Any], ...]]]:
    """Each action each of `commands` goes out as on its own, with its command: one naming its units for an ability
    every unit of a command carries out, or one for each ability it is sent as, and one to each unit for any other."""
    for command, units in commands:
        sent_as = ABILITIES_SENT_AS_ANOTHER.get(command.ability, SENT_UNCHANGED)
        groups = (units,) if command.ability in MULTI_UNIT_ABILITIES else tuple((unit,) for unit in units)
        for group in groups:
            for ability, target, named in _actions_of_ability(command.ability, group, command.target, sent_as):
                yield command, ability, target, named


def _actions_of_ability(
    ability: AbilityId,
    units: Sequence[OwnUnit[Any]],
    target: Target | None,
    sent_as: Mapping[UnitTypeId, SentAs],
) -> list[tuple[int, Target | None, tuple[OwnUnit[Any], ...]]]:
    """The actions giving `ability` at `target` to `units`, each as the game's ability, its target and the units
    it names: one naming the units that take `ability` itself, then, for each ability `sent_as` sends their types
    instead, one naming its units, or one per unit where it is aimed at the unit itself."""
    own: list[OwnUnit[Any]] = []
    sent: dict[SentAs, list[OwnUnit[Any]]] = {}
    for unit in units:
        if (sending := sent_as.get(unit.type_id)) is None:
            own.append(unit)
        else:
            sent.setdefault(sending, []).append(unit)
    actions: list[tuple[int, Target | None, tuple[OwnUnit[Any], ...]]] = [(ability, target, tuple(own))] if own else []
    for sending, group in sent.items():
        if sending.aimed_at_itself:
            actions += [(sending.ability, unit, (unit,)) for unit in group]
        else:
            actions.append((sending.ability, None, tuple(group)))
    return actions


def _unit_command_action(ability: int, tags: list[int], target: Target | None, *, queued: bool) -> sc2api_pb2.Action:
    """One action giving `ability` to the units of `tags`, aimed at `target`."""
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
