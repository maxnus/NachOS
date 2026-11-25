"""Game time, counted in steps: `GameTime`, the game's speeds, and `TimeDependent`, a value that depends on the game
time: `TimeSeries`, values a bot records against it, `TimeCurve`, a few values joined between their times, and
`TimeFunction`, a function of it."""

from sc2nachos.gametime._game_speed import (
    FASTER_PER_NORMAL_SPEED,
    SECONDS_PER_STEP,
    STEPS_PER_NORMAL_SECOND,
    STEPS_PER_SECOND,
)
from sc2nachos.gametime._game_time import GameTime
from sc2nachos.gametime._interpolation import Interpolation
from sc2nachos.gametime._supports_vector_arithmetic import SupportsVectorArithmetic
from sc2nachos.gametime._time_curve import TimeCurve
from sc2nachos.gametime._time_dependent import TimeDependent
from sc2nachos.gametime._time_function import TimeFunction
from sc2nachos.gametime._time_series import TimeSeries

__all__ = [
    "FASTER_PER_NORMAL_SPEED",
    "SECONDS_PER_STEP",
    "STEPS_PER_NORMAL_SECOND",
    "STEPS_PER_SECOND",
    "GameTime",
    "Interpolation",
    "SupportsVectorArithmetic",
    "TimeCurve",
    "TimeDependent",
    "TimeFunction",
    "TimeSeries",
]
