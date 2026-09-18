"""The alerts the game raises for this player, one event for each."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import final

from s2clientprotocol import sc2api_pb2

from sc2nachos.events._event import Event

# An alert names no unit and no position. Where a docstring says what raised one, a game test saw it.


@final
@dataclass(frozen=True, slots=True)
class NuclearLaunchDetectedAlertEvent(Event):
    """The game has alerted this player to a nuke launched at it."""


@final
@dataclass(frozen=True, slots=True)
class NydusWormDetectedAlertEvent(Event):
    """The game has alerted this player to a nydus worm."""


@final
@dataclass(frozen=True, slots=True)
class ErrorAlertEvent(Event):
    """The game has raised its alert named `AlertError`."""


@final
@dataclass(frozen=True, slots=True)
class AddOnCompleteAlertEvent(Event):
    """The game has alerted this player to an add-on finished (in game)."""


@final
@dataclass(frozen=True, slots=True)
class BuildingCompleteAlertEvent(Event):
    """The game has alerted this player to a structure finished: a supply depot, a spawning pool or an extractor
    (in game)."""


@final
@dataclass(frozen=True, slots=True)
class BuildingUnderAttackAlertEvent(Event):
    """The game has alerted this player to a structure of its under attack."""


@final
@dataclass(frozen=True, slots=True)
class LarvaHatchedAlertEvent(Event):
    """The game has alerted this player to larvae hatched."""


@final
@dataclass(frozen=True, slots=True)
class MergeCompleteAlertEvent(Event):
    """The game has alerted this player to a merge finished."""


@final
@dataclass(frozen=True, slots=True)
class MineralsExhaustedAlertEvent(Event):
    """The game has alerted this player to a mineral field mined out (corpus)."""


@final
@dataclass(frozen=True, slots=True)
class MorphCompleteAlertEvent(Event):
    """The game has alerted this player to a morph finished: a lair, a baneling or an overseer (in game)."""


@final
@dataclass(frozen=True, slots=True)
class MothershipCompleteAlertEvent(Event):
    """The game has alerted this player to a mothership finished."""


@final
@dataclass(frozen=True, slots=True)
class MuleExpiredAlertEvent(Event):
    """The game has alerted this player to a MULE expired, in the observation that reports it dead (in game)."""


@final
@dataclass(frozen=True, slots=True)
class NukeCompleteAlertEvent(Event):
    """The game has alerted this player to a nuke armed."""


@final
@dataclass(frozen=True, slots=True)
class ResearchCompleteAlertEvent(Event):
    """The game has alerted this player to research finished."""


@final
@dataclass(frozen=True, slots=True)
class TrainErrorAlertEvent(Event):
    """The game has raised its alert named `TrainError`."""


@final
@dataclass(frozen=True, slots=True)
class TrainUnitCompleteAlertEvent(Event):
    """The game has alerted this player to a unit finished, once for each zergling hatched (in game)."""


@final
@dataclass(frozen=True, slots=True)
class TrainWorkerCompleteAlertEvent(Event):
    """The game has alerted this player to a worker finished: a drone hatched or an SCV trained (in game)."""


@final
@dataclass(frozen=True, slots=True)
class TransformationCompleteAlertEvent(Event):
    """The game has alerted this player to a transformation finished."""


@final
@dataclass(frozen=True, slots=True)
class UnitUnderAttackAlertEvent(Event):
    """The game has alerted this player to a unit of its under attack."""


@final
@dataclass(frozen=True, slots=True)
class UpgradeCompleteAlertEvent(Event):
    """The game has alerted this player to an upgrade finished: a level of weapons researched, or a command center
    become an orbital command (in game)."""


@final
@dataclass(frozen=True, slots=True)
class VespeneExhaustedAlertEvent(Event):
    """The game has alerted this player to a geyser mined out."""


@final
@dataclass(frozen=True, slots=True)
class WarpInCompleteAlertEvent(Event):
    """The game has alerted this player to a warp-in finished (in game)."""


_Alert = sc2api_pb2.Alert
_ALERT_EVENTS: Mapping[sc2api_pb2.Alert.ValueType, type[Event]] = MappingProxyType(
    {
        _Alert.NuclearLaunchDetected: NuclearLaunchDetectedAlertEvent,
        _Alert.NydusWormDetected: NydusWormDetectedAlertEvent,
        _Alert.AlertError: ErrorAlertEvent,
        _Alert.AddOnComplete: AddOnCompleteAlertEvent,
        _Alert.BuildingComplete: BuildingCompleteAlertEvent,
        _Alert.BuildingUnderAttack: BuildingUnderAttackAlertEvent,
        _Alert.LarvaHatched: LarvaHatchedAlertEvent,
        _Alert.MergeComplete: MergeCompleteAlertEvent,
        _Alert.MineralsExhausted: MineralsExhaustedAlertEvent,
        _Alert.MorphComplete: MorphCompleteAlertEvent,
        _Alert.MothershipComplete: MothershipCompleteAlertEvent,
        _Alert.MULEExpired: MuleExpiredAlertEvent,
        _Alert.NukeComplete: NukeCompleteAlertEvent,
        _Alert.ResearchComplete: ResearchCompleteAlertEvent,
        _Alert.TrainError: TrainErrorAlertEvent,
        _Alert.TrainUnitComplete: TrainUnitCompleteAlertEvent,
        _Alert.TrainWorkerComplete: TrainWorkerCompleteAlertEvent,
        _Alert.TransformationComplete: TransformationCompleteAlertEvent,
        _Alert.UnitUnderAttack: UnitUnderAttackAlertEvent,
        _Alert.UpgradeComplete: UpgradeCompleteAlertEvent,
        _Alert.VespeneExhausted: VespeneExhaustedAlertEvent,
        _Alert.WarpInComplete: WarpInCompleteAlertEvent,
    }
)
"""The event for each alert the protocol names, by its value."""
