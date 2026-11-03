"""The map a game is played on."""

from sc2nachos.gamemap._errors import NoMainRampError, NoNaturalError, NoStartLocationError
from sc2nachos.gamemap._expansion import Expansion, StartLocation
from sc2nachos.gamemap._game_map import GameMap
from sc2nachos.gamemap._ramp import Ramp

__all__ = [
    "Expansion",
    "GameMap",
    "NoMainRampError",
    "NoNaturalError",
    "NoStartLocationError",
    "Ramp",
    "StartLocation",
]
