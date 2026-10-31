"""Game time, counted in steps."""

from __future__ import annotations

import math
import numbers
from dataclasses import dataclass
from typing import ClassVar, overload

from sc2nachos.constants import SECONDS_PER_STEP, STEPS_PER_NORMAL_SECOND

# 22.4 steps a second as whole numbers, 112 steps in 5 seconds. Multiplying by the one and dividing by the other is
# exact up to the division, which rounds once, so a whole number of steps comes out whole; multiplying by the stored
# 22.4, a little under it, takes 45 seconds to 1007.9999999999999 steps.
_STEPS_PER_FIVE_SECONDS = 112
_MOST_DECIMALS = 3


@dataclass(frozen=True, slots=True, init=False, eq=False, match_args=False)
class GameTime:
    """A moment of a game, counted from its start, or a length of game time, in steps of the game: 22.4 make a
    second at the Faster speed games are played at.

    Made from one of `steps`; `seconds` at the Faster speed; or `normal_seconds` at the Normal speed, 16 steps a
    second, in which the game gives some times, such as a weapon's cooldown. `GameTime(seconds=120)` is 2688 steps.
    Steps are kept as given. Seconds are multiplied out unrounded, exactly where they make a whole number of steps, so
    `GameTime(seconds=45) == GameTime(steps=1008)`; normal seconds exactly, 16 being a power of two. Any other
    keyword, none, or more than one raises `TypeError`.

    A moment is a whole number of steps; a length may be fractional, as a weapon's cooldown is. Two add, subtract and
    take `%` to a `GameTime`, and divide, `/` or `//`, to a plain number; one multiplies by a plain number and divides
    by one to a `GameTime`. It orders against another `GameTime` alone: compared with a plain number it raises
    `TypeError`, and it equals none. Zero is false, and is `GameTime.ZERO`; `GameTime.INFINITE` is later and longer
    than any other.
    """

    # Makes a numpy scalar on the left, `numpy.float64(2) * time`, hand over to `__rmul__` instead of reading the time
    # as an array.
    __array_ufunc__ = None

    ZERO: ClassVar[GameTime]
    """No time: the game's start, or a length of none."""

    INFINITE: ClassVar[GameTime]
    """The time of what never comes, later and longer than any other, as `math.inf` is a number larger than any. It
    orders above every finite time and stays infinite when one is added or subtracted, and `seconds` reads
    `math.inf`; `nearest_step`, `to_string` and dividing it by `//` raise `ValueError`. Subtracting it from itself,
    multiplying it by zero or taking its remainder gives a time of NaN steps, which equals none."""

    steps: float
    """The steps of the game it counts."""

    @overload
    def __init__(self, *, steps: float) -> None: ...

    @overload
    def __init__(self, *, seconds: float) -> None: ...

    @overload
    def __init__(self, *, normal_seconds: float) -> None: ...

    def __init__(
        self, *, steps: float | None = None, seconds: float | None = None, normal_seconds: float | None = None
    ) -> None:
        if steps is not None:
            if seconds is not None or normal_seconds is not None:
                raise _not_one_unit_error(steps=steps, seconds=seconds, normal_seconds=normal_seconds)
            counted_steps = steps
        elif seconds is not None:
            if normal_seconds is not None:
                raise _not_one_unit_error(steps=steps, seconds=seconds, normal_seconds=normal_seconds)
            counted_steps = seconds * _STEPS_PER_FIVE_SECONDS / 5
        elif normal_seconds is not None:
            counted_steps = normal_seconds * STEPS_PER_NORMAL_SECOND
        else:
            raise _not_one_unit_error(steps=steps, seconds=seconds, normal_seconds=normal_seconds)
        object.__setattr__(self, "steps", counted_steps)

    @classmethod
    def _from_steps(cls, steps: float) -> GameTime:
        """A time of `steps`, made without the constructor's check of its keywords."""
        new_time = object.__new__(cls)
        object.__setattr__(new_time, "steps", steps)
        return new_time

    @property
    def seconds(self) -> float:
        """The seconds it counts at the Faster speed, unrounded."""
        return self.steps * SECONDS_PER_STEP

    @property
    def normal_seconds(self) -> float:
        """The seconds it counts at the Normal speed, 16 steps a second. Exact, 16 being a power of two."""
        return self.steps / STEPS_PER_NORMAL_SECOND

    @property
    def nearest_step(self) -> int:
        """The step nearest it, a tie the later, where Python's `round` takes the even one. Raises `ValueError` for
        `INFINITE`, or any time not finite."""
        # As `util._math._rounded_half_up`, which this module cannot import: `sc2nachos.util` imports the series,
        # which imports `GameTime`. Not `math.floor(steps + 0.5)`: the addition rounds, taking 0.49999999999999994 to 1.
        try:
            floored = math.floor(self.steps)
        except (OverflowError, ValueError):
            raise ValueError(f"{self!r} is not finite, so it has no nearest step") from None
        return floored + (self.steps - floored >= 0.5)

    def is_integer(self) -> bool:
        """Whether it is a whole number of steps."""
        return float(self.steps).is_integer()

    def to_string(self, decimals: int = 3) -> str:
        """Minutes and seconds, the seconds to `decimals` places from 0 to 3, rounded down: `"3:20.000"` at step
        4480, and `"3:20"` to 0 places. From 2 places up every step reads apart from the next, since a step is 44.6
        milliseconds. A negative length reads `"-0:04.285"`. Raises `ValueError` for `decimals` outside 0 to 3, for
        a time not finite, and for one of about 3.6e307 steps or more, a tenth as many for each decimal place."""
        if (
            not isinstance(decimals, numbers.Integral)
            or isinstance(decimals, bool)
            or not 0 <= decimals <= _MOST_DECIMALS
        ):
            raise ValueError(f"decimals is a whole number from 0 to {_MOST_DECIMALS}, not {decimals!r}")
        parts_per_second = 10**decimals
        # One division, so a whole part of a second comes out whole and nothing is rounded up past one.
        try:
            parts = math.floor(abs(self.steps) * 5 * parts_per_second / _STEPS_PER_FIVE_SECONDS)
        except (OverflowError, ValueError):
            if math.isfinite(self.steps):
                raise ValueError(f"{self!r} is too long to count in {decimals} decimals of a second") from None
            raise ValueError(f"{self!r} is not finite, so it reads as no minutes and seconds") from None
        minutes, parts_in_minute = divmod(parts, 60 * parts_per_second)
        seconds, parts_in_second = divmod(parts_in_minute, parts_per_second)
        sign = "-" if self.steps < 0 else ""
        fraction = f".{parts_in_second:0{decimals}d}" if decimals else ""
        return f"{sign}{minutes}:{seconds:02d}{fraction}"

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
        return GameTime._from_steps(self.steps + other.steps)

    def __sub__(self, other: GameTime) -> GameTime:
        if not isinstance(other, GameTime):
            return NotImplemented
        return GameTime._from_steps(self.steps - other.steps)

    def __mul__(self, factor: float) -> GameTime:
        if not isinstance(factor, _SCALAR_TYPES):
            return NotImplemented
        return GameTime._from_steps(self.steps * factor)

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
        return GameTime._from_steps(self.steps / divisor)

    def __floordiv__(self, divisor: GameTime) -> int:
        if not isinstance(divisor, GameTime):
            return NotImplemented
        try:
            return int(self.steps // divisor.steps)
        except (OverflowError, ValueError):
            raise ValueError(f"{self!r} // {divisor!r} is not a finite whole number") from None

    def __mod__(self, divisor: GameTime) -> GameTime:
        if not isinstance(divisor, GameTime):
            return NotImplemented
        return GameTime._from_steps(self.steps % divisor.steps)

    def __neg__(self) -> GameTime:
        return GameTime._from_steps(-self.steps)

    def __abs__(self) -> GameTime:
        return GameTime._from_steps(abs(self.steps))


def _not_one_unit_error(**given: float | None) -> TypeError:
    """The error for a `GameTime` given other than exactly one of its keywords."""
    given_names = [name for name, value in given.items() if value is not None]
    return TypeError(
        f"GameTime takes exactly one of steps, seconds and normal_seconds, not {' and '.join(given_names) or 'none'}"
    )


GameTime.ZERO = GameTime(steps=0)
GameTime.INFINITE = GameTime(steps=math.inf)

# The plain numbers a game time multiplies and divides by, as a constant: an inline `a | b` union is rebuilt on every
# call. `numbers.Real` catches numpy scalars, which subclass neither `int` nor `float`; it comes last, being the
# slowest.
_SCALAR_TYPES = (int, float, numbers.Real)
