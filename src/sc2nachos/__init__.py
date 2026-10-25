"""NachOS — an event-driven StarCraft II bot API for Python."""

from sc2nachos.__about__ import __version__
from sc2nachos._errors import NachOSError
from sc2nachos.api import Api, FogOnError, NoEnemyError, NotPlayingError
from sc2nachos.enemy import UpgradeInference
from sc2nachos.game_time import GameTime
from sc2nachos.gamedata import Cost, Resources
from sc2nachos.gamemap import Expansion
from sc2nachos.geometry import Circle, Footprint, Point, Rectangle
from sc2nachos.ids import AbilityId, BuffId, UnitTypeId, UpgradeId
from sc2nachos.match import AIBuild, Computer, Difficulty, Race, Result
from sc2nachos.run import (
    ReplayPlayback,
    open_replay,
    open_replay_recording,
    run_from_command_line,
    run_ladder,
    run_local,
    run_recording,
    run_replay,
)
from sc2nachos.units import OwnUnit, Unit, Units, UnitType

__all__ = [
    "AIBuild",
    "AbilityId",
    "Api",
    "BuffId",
    "Circle",
    "Computer",
    "Cost",
    "Difficulty",
    "Expansion",
    "FogOnError",
    "Footprint",
    "GameTime",
    "NachOSError",
    "NoEnemyError",
    "NotPlayingError",
    "OwnUnit",
    "Point",
    "Race",
    "Rectangle",
    "ReplayPlayback",
    "Resources",
    "Result",
    "Unit",
    "UnitType",
    "UnitTypeId",
    "Units",
    "UpgradeId",
    "UpgradeInference",
    "__version__",
    "open_replay",
    "open_replay_recording",
    "run_from_command_line",
    "run_ladder",
    "run_local",
    "run_recording",
    "run_replay",
]
