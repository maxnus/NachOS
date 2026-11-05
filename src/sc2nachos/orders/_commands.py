"""Orders as the protocol carries them: the actions in a turn's request."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, final

from s2clientprotocol import common_pb2, raw_pb2, sc2api_pb2

from sc2nachos.gamedata._sent_as import SentAs
from sc2nachos.gamedata._techtree import ABILITIES_SENT_AS_ANOTHER, MULTI_UNIT_ABILITIES
from sc2nachos.geometry import POINT_PRECISION, Point
from sc2nachos.ids import AbilityId

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping, Sequence

    from sc2nachos.ids import UnitTypeId
    from sc2nachos.orders._order import Order
    from sc2nachos.units import OwnUnit, Target

# The abilities whose orders join a command of another order. An archon's merge does not: it merges the two templar it
# is given to (in game), and what one command naming four does was never seen.
_JOINING_OTHER_ORDERS = MULTI_UNIT_ABILITIES - {AbilityId.MORPH_ARCHON}
# What an ability goes out as where no type is sent another.
SENT_UNCHANGED: Mapping[UnitTypeId, SentAs] = MappingProxyType({})


@final
@dataclass(slots=True)
class UnitCommand:
    """One raw command of a turn's request: the game's `ability`, aimed at `target`, queued or not, given to the units
    of each order of `parts`."""

    ability: int
    target: Target | None
    queued: bool
    parts: list[tuple[Order[Any], tuple[OwnUnit[Any], ...]]]
    """Each order the command carries, with the units it names for that order, in the order they joined it."""

    def to_action(self) -> sc2api_pb2.Action:
        """The command as the protocol carries it, naming the units of every part in turn."""
        tags = [unit.tag for _, units in self.parts for unit in units]
        return _unit_command_action(self.ability, tags, self.target, queued=self.queued)


def unit_commands(orders: Iterable[tuple[Order[Any], tuple[OwnUnit[Any], ...]]]) -> list[UnitCommand]:
    """The raw commands giving each of `orders` to the units it goes out to, in the order they go out.

    An order of an ability every unit of a command carries out goes out as one command naming its units, or one for
    each ability it is sent as, and any other order as a command to each unit. A command of an order joins the last
    command before it of the same game ability, target as the game keeps it and queue flag, unless one of its units is
    named in that command or in one after it. So a unit's commands go out in the order it was given them, and no
    command names a unit twice. An archon's merge joins no other order's command.
    """
    commands: list[UnitCommand] = []
    # By game ability, target as the game keeps it and queue flag, the index of the last command an order may join.
    last_joinable: dict[tuple[int, Target | None, bool], int] = {}
    # By unit id, the index of the last command naming the unit.
    last_naming: dict[int, int] = {}
    for order, ability, target, units in _commands_of_each(orders):
        joins = order.ability in _JOINING_OTHER_ORDERS
        key = (ability, _target_as_kept(target), order.queued)
        at = last_joinable.get(key, -1) if joins else -1
        if at < 0 or any(last_naming.get(unit.id, -1) >= at for unit in units):
            at = len(commands)
            commands.append(UnitCommand(ability, target, order.queued, []))
            if joins:
                last_joinable[key] = at
        commands[at].parts.append((order, units))
        for unit in units:
            last_naming[unit.id] = at
    return commands


def _commands_of_each(
    orders: Iterable[tuple[Order[Any], tuple[OwnUnit[Any], ...]]],
) -> Iterator[tuple[Order[Any], int, Target | None, tuple[OwnUnit[Any], ...]]]:
    """Each raw command each of `orders` goes out as on its own, with its order: one naming its units for an ability
    every unit of a command carries out, or one for each ability it is sent as, and one to each unit for any other."""
    for order, units in orders:
        sent_as = ABILITIES_SENT_AS_ANOTHER.get(order.ability, SENT_UNCHANGED)
        groups = (units,) if order.ability in MULTI_UNIT_ABILITIES else tuple((unit,) for unit in units)
        for group in groups:
            for ability, target, named in _commands_giving(order.ability, group, order.target, sent_as):
                yield order, ability, target, named


def _commands_giving(
    ability: AbilityId,
    units: Sequence[OwnUnit[Any]],
    target: Target | None,
    sent_as: Mapping[UnitTypeId, SentAs],
) -> list[tuple[int, Target | None, tuple[OwnUnit[Any], ...]]]:
    """The raw commands giving `ability` at `target` to `units`, each as the game's ability, its target and the units
    it names: one naming the units that take `ability` itself, then, for each ability `sent_as` sends their types
    instead, one naming its units, or one per unit where it is aimed at the unit itself."""
    own: list[OwnUnit[Any]] = []
    sent: dict[SentAs, list[OwnUnit[Any]]] = {}
    for unit in units:
        if (sending := sent_as.get(unit.type_id)) is None:
            own.append(unit)
        else:
            sent.setdefault(sending, []).append(unit)
    commands: list[tuple[int, Target | None, tuple[OwnUnit[Any], ...]]] = [(ability, target, tuple(own))] if own else []
    for sending, group in sent.items():
        if sending.aimed_at_itself:
            commands += [(sending.ability, unit, (unit,)) for unit in group]
        else:
            commands.append((sending.ability, None, tuple(group)))
    return commands


def _target_as_kept(target: Target | None) -> Target | None:
    """`target` as the game keeps it: a point cut down to `POINT_PRECISION`, so that two targets are `same_target`
    exactly when these are equal."""
    if isinstance(target, Point):
        return target.rounded_down(step=POINT_PRECISION)
    return target


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
