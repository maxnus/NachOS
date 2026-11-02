"""Game time, counted in steps: `GameTime`, the game's speeds, and `TimeSeries`, values a bot records against the
game time."""

from sc2nachos.gametime._game_speed import (
    FASTER_PER_NORMAL_SPEED,
    SECONDS_PER_STEP,
    STEPS_PER_NORMAL_SECOND,
    STEPS_PER_SECOND,
)
from sc2nachos.gametime._game_time import GameTime
from sc2nachos.gametime._time_series import TimeSeries

__all__ = [
    "FASTER_PER_NORMAL_SPEED",
    "SECONDS_PER_STEP",
    "STEPS_PER_NORMAL_SECOND",
    "STEPS_PER_SECOND",
    "GameTime",
    "TimeSeries",
]
