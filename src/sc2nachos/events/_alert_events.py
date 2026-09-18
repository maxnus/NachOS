"""The alerts the game raises for this player, one event for each it is known to raise when it should."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import final

from s2clientprotocol import sc2api_pb2

from sc2nachos.events._event import Event

# An alert names no unit and no position. What each docstring says raised it was seen in game, most of it by
# `tools/sweep_alerts.py`, which also counted one alert for each thing it made happen.


@final
@dataclass(frozen=True, slots=True)
class NuclearLaunchDetectedAlertEvent(Event):
    """The game has alerted this player to a nuke launched at it. Untested, since only an enemy launches one: this
    player's own nuke raises nothing (in game)."""


@final
@dataclass(frozen=True, slots=True)
class NydusWormDetectedAlertEvent(Event):
    """The game has alerted this player to an enemy's nydus worm. Untested, since only an enemy summons one: this
    player's own worm raises only a `BuildingCompleteAlertEvent` (in game)."""


@final
@dataclass(frozen=True, slots=True)
class AddOnCompleteAlertEvent(Event):
    """An add-on of this player's has finished, one alert for each (in game)."""


@final
@dataclass(frozen=True, slots=True)
class BuildingCompleteAlertEvent(Event):
    """A structure of this player's has finished, one alert for each: built, become by a drone, or a nydus worm, but
    not an add-on (in game)."""


@final
@dataclass(frozen=True, slots=True)
class BuildingUnderAttackAlertEvent(Event):
    """A structure of this player's out of sight of its camera has come under attack. See
    `UnitUnderAttackAlertEvent` for when the game holds one back (in game)."""


@final
@dataclass(frozen=True, slots=True)
class LarvaHatchedAlertEvent(Event):
    """A queen's inject has hatched its larva, one alert for each inject, a few steps before the larva are seen. Larva
    a hatchery makes by itself raise none (in game)."""


@final
@dataclass(frozen=True, slots=True)
class MergeCompleteAlertEvent(Event):
    """Two templar of this player's have merged into an archon, one alert for each, some 100 steps after the archon is
    first seen (in game)."""


@final
@dataclass(frozen=True, slots=True)
class MineralsExhaustedAlertEvent(Event):
    """A mineral field this player mined has run out, one alert for each, in the observation it is gone from (in
    game)."""


@final
@dataclass(frozen=True, slots=True)
class MorphCompleteAlertEvent(Event):
    """A unit or structure of this player's has finished morphing: a lair, a baneling, an overseer or a ravager, one
    alert for each. A hellion becoming a hellbat raises none (in game)."""


@final
@dataclass(frozen=True, slots=True)
class MothershipCompleteAlertEvent(Event):
    """A mothership of this player's has finished, without a `TrainUnitCompleteAlertEvent` (in game)."""


@final
@dataclass(frozen=True, slots=True)
class MuleExpiredAlertEvent(Event):
    """A MULE of this player's has expired, one alert for each, in the observation that reports it dead (in game)."""


@final
@dataclass(frozen=True, slots=True)
class NukeCompleteAlertEvent(Event):
    """A nuke has been armed at a ghost academy of this player's (in game)."""


@final
@dataclass(frozen=True, slots=True)
class ResearchCompleteAlertEvent(Event):
    """Research of this player's that is not a level has finished: stimpack, combat shield, zergling speed, warp gate
    or charge, one alert for each (in game)."""


@final
@dataclass(frozen=True, slots=True)
class TrainUnitCompleteAlertEvent(Event):
    """A unit of this player's that is not a worker has been trained or hatched, one alert for each, two for a pair
    of zerglings. A mothership, a warp-in and a morph raise alerts of their own (in game)."""


@final
@dataclass(frozen=True, slots=True)
class TrainWorkerCompleteAlertEvent(Event):
    """A worker of this player's has been trained or hatched, one alert for each (in game)."""


@final
@dataclass(frozen=True, slots=True)
class TransformationCompleteAlertEvent(Event):
    """A gateway of this player's has become a warp gate, or a warp gate a gateway, one alert for each, the gateways
    warp gate research turns by themselves included (in game)."""


@final
@dataclass(frozen=True, slots=True)
class UnitUnderAttackAlertEvent(Event):
    """A unit of this player's out of sight of its camera has come under attack.

    The game raises none for a unit its camera shows, and none again for a unit until it has gone some 6000 to 6500
    steps without being attacked: one attacked every 3000 steps raised one alert in 24000 steps. Each unit counts
    for itself, so two attacked at once raise two, and a new unit attacked where another just was raises one (in
    game).
    """


@final
@dataclass(frozen=True, slots=True)
class UpgradeCompleteAlertEvent(Event):
    """A level of this player's weapons or armor has finished, or a command center has become an orbital command or a
    planetary fortress, one alert for each (in game)."""


@final
@dataclass(frozen=True, slots=True)
class VespeneExhaustedAlertEvent(Event):
    """A geyser this player mined has run out, one alert for each (in game)."""


@final
@dataclass(frozen=True, slots=True)
class WarpInCompleteAlertEvent(Event):
    """A unit of this player's has finished warping in, one alert for each (in game)."""


_Alert = sc2api_pb2.Alert
_ALERT_EVENTS: Mapping[sc2api_pb2.Alert.ValueType, type[Event] | None] = MappingProxyType(
    {
        _Alert.NuclearLaunchDetected: NuclearLaunchDetectedAlertEvent,
        _Alert.NydusWormDetected: NydusWormDetectedAlertEvent,
        # Nothing the sweep did raised these two, and what would is not known, so they are passed over.
        _Alert.AlertError: None,
        _Alert.TrainError: None,
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
        _Alert.TrainUnitComplete: TrainUnitCompleteAlertEvent,
        _Alert.TrainWorkerComplete: TrainWorkerCompleteAlertEvent,
        _Alert.TransformationComplete: TransformationCompleteAlertEvent,
        _Alert.UnitUnderAttack: UnitUnderAttackAlertEvent,
        _Alert.UpgradeComplete: UpgradeCompleteAlertEvent,
        _Alert.VespeneExhausted: VespeneExhaustedAlertEvent,
        _Alert.WarpInComplete: WarpInCompleteAlertEvent,
    }
)
"""The event for each alert the protocol names, by its value, or `None` for one handed to no handler."""
