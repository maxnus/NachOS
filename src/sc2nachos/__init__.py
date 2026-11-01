"""NachOS — an event-driven StarCraft II bot API for Python.

A bot makes one `Api`, subscribes handlers to the events a game hands out with `Api.on`, and plays through a runner:
`run_local` against the built-in computer, or `run_from_command_line` for the ladder and local games alike. Each turn
it reads its units from `Api.units` and orders them through `Api.orders`.

The guides are on GitHub: [events](https://github.com/maxnus/NachOS/blob/main/docs/events.md),
[orders](https://github.com/maxnus/NachOS/blob/main/docs/orders.md),
[debugging](https://github.com/maxnus/NachOS/blob/main/docs/debugging.md),
[playing on the ladder](https://github.com/maxnus/NachOS/blob/main/docs/ladder.md),
[running StarCraft II on GitHub Actions](https://github.com/maxnus/NachOS/blob/main/docs/github-actions.md),
[testing a bot](https://github.com/maxnus/NachOS/blob/main/docs/testing.md),
[playing back a replay](https://github.com/maxnus/NachOS/blob/main/docs/replays.md) and
[migrating from python-sc2](https://github.com/maxnus/NachOS/blob/main/docs/migrating-from-python-sc2.md).
[`examples/demo_bot.py`](https://github.com/maxnus/NachOS/blob/main/examples/demo_bot.py) is a whole bot in one file.
"""

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
from sc2nachos.run import run_from_command_line, run_ladder, run_local, run_recording, run_replay
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
    "Resources",
    "Result",
    "Unit",
    "UnitType",
    "UnitTypeId",
    "Units",
    "UpgradeId",
    "UpgradeInference",
    "__version__",
    "run_from_command_line",
    "run_ladder",
    "run_local",
    "run_recording",
    "run_replay",
]
