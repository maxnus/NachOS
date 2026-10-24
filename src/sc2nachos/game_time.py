"""Game time, counted in steps."""

from __future__ import annotations

import math
import numbers
from dataclasses import dataclass
from typing import overload

from sc2nachos.constants import SECONDS_PER_STEP

_MILLISECONDS_PER_MINUTE = 60_000
# 22.4 steps a second as whole numbers, 112 steps in 5 seconds. Multiplying by the one and dividing by the other is
# exact up to the division, which rounds once, so a whole number of steps comes out whole; multiplying by the stored
# 22.4, a little under it, takes 45 seconds to 1007.9999999999999 steps.
_STEPS_PER_FIVE_SECONDS = 112
_MILLISECONDS_PER_FIVE_SECONDS = 5000


@dataclass(frozen=True, slots=True, kw_only=True, eq=False)
class GameTime:
    """A moment of a game, counted from its start, or a length of game time, in steps of the game: 22.4 make a
    second at the Faster speed games are played at.

    A moment is a whole number of steps; a length may be fractional, as a weapon's cooldown is. Two add, subtract and
    take `%` to a `GameTime`, and divide, `/` or `//`, to a plain number; one multiplies by a plain number and divides
    by one to a `GameTime`. It orders against another `GameTime` alone: compared with a plain number it raises
    `TypeError`, and it equals none. Zero is false.
    """

    # Makes a numpy scalar on the left, `numpy.float64(2) * time`, hand over to `__rmul__` instead of reading the time
    # as an array.
    __array_ufunc__ = None

    steps: float
    """The steps of the game it counts."""

    @classmethod
    def from_seconds(cls, seconds: float) -> GameTime:
        """The game time of `seconds` at the Faster speed, unrounded."""
        return cls(steps=seconds * _STEPS_PER_FIVE_SECONDS / 5)

    @property
    def seconds(self) -> float:
        """The seconds it counts at the Faster speed, unrounded."""
        return self.steps * SECONDS_PER_STEP

    @property
    def time_string(self) -> str:
        """Minutes, seconds and milliseconds, `"3:20.000"` at step 4480: the milliseconds rounded down, which tells
        every step from the next, since a step is 44.6 milliseconds. A negative length reads `"-0:04.285"`."""
        # One division, so a whole millisecond comes out whole and nothing is rounded up past one.
        milliseconds = math.floor(abs(self.steps) * _MILLISECONDS_PER_FIVE_SECONDS / _STEPS_PER_FIVE_SECONDS)
        minutes, milliseconds_in_minute = divmod(milliseconds, _MILLISECONDS_PER_MINUTE)
        seconds, milliseconds_in_second = divmod(milliseconds_in_minute, 1000)
        sign = "-" if self.steps < 0 else ""
        return f"{sign}{minutes}:{seconds:02d}.{milliseconds_in_second:03d}"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, GameTime):
            return NotImplemented
        return self.steps == other.steps

    def __hash__(self) -> int:
        return hash(self.steps)

    def __lt__(self, other: GameTime) -> bool:
        if not isinstance(other, GameTime):
            return NotImplemented
        return self.steps < other.steps

    def __le__(self, other: GameTime) -> bool:
        if not isinstance(other, GameTime):
            return NotImplemented
        return self.steps <= other.steps

    def __gt__(self, other: GameTime) -> bool:
        if not isinstance(other, GameTime):
            return NotImplemented
        return self.steps > other.steps

    def __ge__(self, other: GameTime) -> bool:
        if not isinstance(other, GameTime):
            return NotImplemented
        return self.steps >= other.steps

    def __bool__(self) -> bool:
        return self.steps != 0

    def __add__(self, other: GameTime) -> GameTime:
        if not isinstance(other, GameTime):
            return NotImplemented
        return GameTime(steps=self.steps + other.steps)

    def __sub__(self, other: GameTime) -> GameTime:
        if not isinstance(other, GameTime):
            return NotImplemented
        return GameTime(steps=self.steps - other.steps)

    def __mul__(self, factor: float) -> GameTime:
        if not isinstance(factor, _SCALAR_TYPES):
            return NotImplemented
        return GameTime(steps=self.steps * factor)

    __rmul__ = __mul__

    @overload
    def __truediv__(self, divisor: GameTime) -> float: ...

    @overload
    def __truediv__(self, divisor: float) -> GameTime: ...

    def __truediv__(self, divisor: GameTime | float) -> GameTime | float:
        if isinstance(divisor, GameTime):
            return self.steps / divisor.steps
        if not isinstance(divisor, _SCALAR_TYPES):
            return NotImplemented
        return GameTime(steps=self.steps / divisor)

    def __floordiv__(self, divisor: GameTime) -> int:
        if not isinstance(divisor, GameTime):
            return NotImplemented
        return int(self.steps // divisor.steps)

    def __mod__(self, divisor: GameTime) -> GameTime:
        if not isinstance(divisor, GameTime):
            return NotImplemented
        return GameTime(steps=self.steps % divisor.steps)

    def __neg__(self) -> GameTime:
        return GameTime(steps=-self.steps)

    def __abs__(self) -> GameTime:
        return GameTime(steps=abs(self.steps))


# The plain numbers a game time multiplies and divides by, as a constant: an inline `a | b` union is rebuilt on every
# call. `numbers.Real` catches numpy scalars, which subclass neither `int` nor `float`; it comes last, being the
# slowest.
_SCALAR_TYPES = (int, float, numbers.Real)
