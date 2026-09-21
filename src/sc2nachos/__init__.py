"""NachOS — an event-driven StarCraft II bot API for Python."""

from sc2nachos.__about__ import __version__
from sc2nachos._errors import NachOSError
from sc2nachos.api import Api, NotPlayingError
from sc2nachos.enemy import UpgradeInference
from sc2nachos.match import AIBuild, Computer, Difficulty, Race, Result
from sc2nachos.run import ApiBot, run_ladder, run_local

__all__ = [
    "AIBuild",
    "Api",
    "ApiBot",
    "Computer",
    "Difficulty",
    "NachOSError",
    "NotPlayingError",
    "Race",
    "Result",
    "UpgradeInference",
    "__version__",
    "run_ladder",
    "run_local",
]
