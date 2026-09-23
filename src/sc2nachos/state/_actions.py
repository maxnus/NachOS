"""This player's actions as the game carried them out, and the orders it gave up on."""

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
    """An action of this player's, as the game carried it out: a `UnitCommand`, an `AutocastToggle` or a
    `CameraMove`."""

    step: int
    """The step the game carried it out at."""


@final
@dataclass(frozen=True, slots=True)
class UnitCommand(Action):
    """An order given to units of this player's."""

    ability: AbilityId
    """The ability ordered, as the game runs it: a move is `GENERAL_MOVE_EXACT`, and a research names its level
    (in game)."""
    units: tuple[Unit[Any], ...]
    target: Target | None
    """The point or unit the order was aimed at, or `None` for an order that takes neither."""
    queued: bool
    """Whether the order was queued behind the units' current orders instead of replacing them."""

    @classmethod
    def _from_proto(
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
    """Units of this player's that had an ability's autocast turned on or off."""

    ability: AbilityId
    units: tuple[Unit[Any], ...]

    @classmethod
    def _from_proto(
        cls, step: int, toggle: raw_pb2.ActionRawToggleAutocast, unit_by_tag: Callable[[int], Unit[Any]]
    ) -> Self:
        return cls(step, AbilityId.read(toggle.ability_id), tuple(unit_by_tag(tag) for tag in toggle.unit_tags))


@final
@dataclass(frozen=True, slots=True)
class CameraMove(Action):
    """A move of this player's camera. The game moves it once itself, as a game starts."""

    center: Point
    """Where the camera was centered."""


@final
@dataclass(frozen=True, slots=True)
class ActionFailure:
    """An order the game accepted and then gave up on, reported in the observation it gave up in (in game).

    Not an `Action`: an action is something this player did, and this is something the game undid. Not an
    exception either: the game reports it, and nothing raises it.
    """

    step: int
    """The step the game gave up at."""
    unit: Unit[Any] | None
    """The unit whose order was given up, or `None` if the game named none."""
    ability: AbilityId | None
    """The ability given up, or `None` if the game named none."""
    action_result: ActionResult
    """Why the game gave up: `NOT_ENOUGH_FOOD` for a marine with no supply left, `CANT_BUILD_LOCATION_INVALID` for
    a site taken meanwhile."""

    @classmethod
    def _from_proto(cls, error: sc2api_pb2.ActionError, unit_by_tag: Callable[[int], Unit[Any]], step: int) -> Self:
        """Read an action error (the protocol's name for it) from the observation at `step`, looking up its unit
        through `unit_by_tag`.

        Raises `UncuratedIdError` if it names an ability the curated ids leave out.
        """
        unit = unit_by_tag(error.unit_tag) if error.HasField("unit_tag") else None
        ability = AbilityId.read(error.ability_id) if error.HasField("ability_id") else None
        return cls(step, unit, ability, ActionResult.read(error.result))


def read_action(action: sc2api_pb2.Action, unit_by_tag: Callable[[int], Unit[Any]]) -> Action | None:
    """Read an action an observation reports, looking up its units through `unit_by_tag`. Returns `None` for an
    action through an interface other than the raw one.

    Raises `UncuratedIdError` for an ability the curated ids leave out.
    """
    raw = action.action_raw
    step = action.game_loop
    match raw.WhichOneof("action"):
        case "unit_command":
            return UnitCommand._from_proto(step, raw.unit_command, unit_by_tag)
        case "toggle_autocast":
            return AutocastToggle._from_proto(step, raw.toggle_autocast, unit_by_tag)
        case "camera_move":
            center = raw.camera_move.center_world_space
            return CameraMove(step, Point((center.x, center.y)))
        case _:
            return None
