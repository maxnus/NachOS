"""What this player did, as the game carried it out, and what it gave up on."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Self, final

from sc2nachos.geometry import Point
from sc2nachos.ids import AbilityId
from sc2nachos.state._action_result import ActionResult

if TYPE_CHECKING:
    from collections.abc import Callable

    from s2clientprotocol import raw_pb2, sc2api_pb2

    from sc2nachos.units import Target, Unit


@dataclass(frozen=True, slots=True)
class Action:
    """Something this player did, as the game carried it out: a `UnitCommand`, an `AutocastToggle` or a
    `CameraMove`."""

    step: int
    """The step the game carried it out at."""


@final
@dataclass(frozen=True, slots=True)
class UnitCommand(Action):
    """Units of this player's given an order."""

    ability: AbilityId
    """What they were ordered, as the game runs it: a move is `GENERAL_MOVE_EXACT`, and a research names its level
    (in game)."""
    units: tuple[Unit[Any], ...]
    target: Target | None
    """Where they were sent, the unit they were sent at, or `None` for an order that needs neither."""
    queued: bool
    """Whether the order went behind the ones they had, rather than replacing them."""

    @classmethod
    def from_proto(
        cls, step: int, command: raw_pb2.ActionRawUnitCommand, unit_by_tag: Callable[[int], Unit[Any]]
    ) -> Self:
        match command.WhichOneof("target"):
            case "target_world_space_pos":
                position = command.target_world_space_pos
                target: Target | None = Point((position.x, position.y))
            case "target_unit_tag":
                target = unit_by_tag(command.target_unit_tag)
            case _:
                target = None
        units = tuple(unit_by_tag(tag) for tag in command.unit_tags)
        return cls(step, AbilityId.read(command.ability_id), units, target, command.queue_command)


@final
@dataclass(frozen=True, slots=True)
class AutocastToggle(Action):
    """Units of this player's that started or stopped casting an ability by themselves."""

    ability: AbilityId
    units: tuple[Unit[Any], ...]

    @classmethod
    def from_proto(
        cls, step: int, toggle: raw_pb2.ActionRawToggleAutocast, unit_by_tag: Callable[[int], Unit[Any]]
    ) -> Self:
        return cls(step, AbilityId.read(toggle.ability_id), tuple(unit_by_tag(tag) for tag in toggle.unit_tags))


@final
@dataclass(frozen=True, slots=True)
class CameraMove(Action):
    """This player's camera moved, as the game moves it once as a game starts."""

    center: Point
    """Where the camera was centered."""


@final
@dataclass(frozen=True, slots=True)
class ActionError:
    """An order the game took and then gave up on, in the observation it gave up in (in game).

    It is not an `Action`: an action is something this player did, and this is something the game undid.
    """

    step: int
    """The step the game gave up at."""
    unit: Unit[Any] | None
    """The unit it gave the order up for, or `None` where it named none."""
    ability: AbilityId | None
    """The order it gave up, or `None` where it named none."""
    result: ActionResult
    """What it gave up for: `NOT_ENOUGH_FOOD` for a marine with no supply left, `CANT_BUILD_LOCATION_INVALID` for a
    site taken meanwhile."""

    @classmethod
    def from_proto(cls, error: sc2api_pb2.ActionError, unit_by_tag: Callable[[int], Unit[Any]], step: int) -> Self:
        """Read an action error the observation at `step` reports, naming a unit through `unit_by_tag`.

        Raises `UncuratedIdError` where it names an ability the curated ids leave out.
        """
        unit = unit_by_tag(error.unit_tag) if error.HasField("unit_tag") else None
        ability = AbilityId.read(error.ability_id) if error.HasField("ability_id") else None
        return cls(step, unit, ability, ActionResult.read(error.result))


def read_action(action: sc2api_pb2.Action, unit_by_tag: Callable[[int], Unit[Any]]) -> Action | None:
    """The action an observation reports, naming each unit through `unit_by_tag`, or `None` for one through an
    interface other than the raw one.

    Raises `UncuratedIdError` for an ability the curated ids leave out.
    """
    raw = action.action_raw
    step = action.game_loop
    match raw.WhichOneof("action"):
        case "unit_command":
            return UnitCommand.from_proto(step, raw.unit_command, unit_by_tag)
        case "toggle_autocast":
            return AutocastToggle.from_proto(step, raw.toggle_autocast, unit_by_tag)
        case "camera_move":
            center = raw.camera_move.center_world_space
            return CameraMove(step, Point((center.x, center.y)))
        case _:
            return None
