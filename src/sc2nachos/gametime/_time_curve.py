"""A few values given at game times, joined between them."""

from __future__ import annotations

import math
from bisect import bisect_right
from typing import TYPE_CHECKING, final, overload

import numpy

from sc2nachos.gametime._game_time import _SCALAR_TYPES, GameTime, check_game_time
from sc2nachos.gametime._interpolation import Interpolation
from sc2nachos.gametime._time_dependent import NO_DEFAULT, default_of_many_times, steps_of_many_times

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence

    from numpy import ndarray


@final
class TimeCurve:
    """A `TimeDependent` of floats made of a few values, each given at a game time, such as a rate of losses that
    rises from 5:00 to 6:00: `TimeCurve([(GameTime(seconds=300), 0.0), (GameTime(seconds=360), 0.004)])`. Between two
    of its times it reads by its `Interpolation`: along the line from the value before to the value after, by default,
    or with `Interpolation.STEP` the value before, held from its time until the next. Before its first time it holds
    its first value, and from its last time on its last, so it covers every time, `GameTime.INFINITE` too, and no read
    raises `IndexError` or reads a default.

    A time is read as it is given, between two steps too. A curve is not changed once made. `len` counts its values,
    and iterating over it hands out each time with its value, in order.
    """

    __slots__ = ("_interpolation", "_step_array", "_steps", "_times", "_value_array", "_values")

    _interpolation: Interpolation
    _times: list[GameTime]
    # A read of one time goes through these lists, which is several times quicker than numpy for one value, and a read
    # of many through the arrays below.
    _steps: list[float]
    _values: list[float]
    _step_array: ndarray
    _value_array: ndarray

    def __init__(
        self, time_value_pairs: Iterable[tuple[GameTime, float]], *, interpolation: Interpolation = Interpolation.LINEAR
    ) -> None:
        """A curve through `time_value_pairs`, each a game time and the value at it, in order of time. Raises
        `TypeError` for anything but pairs, a time that is not a `GameTime`, a value that is not a number, or an
        `interpolation` that is not an `Interpolation`; and `ValueError` for no pair, a time or a value not finite, a
        time not after the one before it, or a value further from the one before it than a float holds."""
        if not isinstance(interpolation, Interpolation):
            raise TypeError(f"interpolation is an Interpolation, not {interpolation!r}")
        self._interpolation = interpolation
        self._times = []
        self._steps = []
        self._values = []
        for time_value_pair in time_value_pairs:
            time, value = _time_and_value_of(time_value_pair)
            self._steps.append(_finite_step_after(time, self._steps))
            self._values.append(_finite_value_after(value, time, self._values))
            self._times.append(time)
        if not self._steps:
            raise ValueError("a time curve needs at least one value")
        self._step_array = numpy.array(self._steps, dtype=float)
        self._value_array = numpy.array(self._values, dtype=float)

    @property
    def interpolation(self) -> Interpolation:
        """How it reads a time between two of its own."""
        return self._interpolation

    def __len__(self) -> int:
        return len(self._steps)

    def __iter__(self) -> Iterator[tuple[GameTime, float]]:
        """Each of its times with the value at it, in order."""
        yield from zip(self._times, self._values, strict=True)

    @overload
    def __call__(self, time: GameTime, /) -> float: ...

    @overload
    def __call__[D](self, time: GameTime, /, *, default: D) -> float | D: ...

    @overload
    def __call__(self, time: Sequence[GameTime] | ndarray, /, *, default: float = ...) -> ndarray: ...

    def __call__(self, time: GameTime | Sequence[GameTime] | ndarray, /, *, default: object = NO_DEFAULT) -> object:
        """The value at `time`, or an array of the values at each of many, a sequence of `GameTime`s or an array of
        steps, in its shape. A curve covers every time, so `default` is never read. Raises `TypeError` as a
        `TimeDependent` does, and `ValueError` for a time of NaN steps. @public"""
        if not isinstance(time, GameTime):
            default_of_many_times(default)
            return self._values_at_steps(steps_of_many_times(time))
        step = time.steps
        if step != step:
            raise ValueError(f"{time!r} is no time, so a curve has no value at it")
        pairs_reached = bisect_right(self._steps, step)
        if pairs_reached == 0:
            return self._values[0]
        value_before = self._values[pairs_reached - 1]
        if pairs_reached == len(self._steps) or self._interpolation is Interpolation.STEP:
            return value_before
        step_before = self._steps[pairs_reached - 1]
        slope = (self._values[pairs_reached] - value_before) / (self._steps[pairs_reached] - step_before)
        return slope * (step - step_before) + value_before

    def _values_at_steps(self, steps: ndarray) -> ndarray:
        """The value at each of `steps`, worked out as a call with one time works it out."""
        if numpy.isnan(steps).any():
            raise ValueError("a time of NaN steps is no time, so a curve has no value at it")
        if self._interpolation is Interpolation.STEP:
            pairs_reached = numpy.searchsorted(self._step_array, steps, side="right")
            return numpy.asarray(self._value_array[numpy.maximum(pairs_reached - 1, 0)])
        return numpy.asarray(numpy.interp(steps, self._step_array, self._value_array))

    def __repr__(self) -> str:
        pairs = ", ".join(f"({time!r}, {value!r})" for time, value in self)
        if self._interpolation is Interpolation.LINEAR:
            return f"{type(self).__name__}([{pairs}])"
        return f"{type(self).__name__}([{pairs}], interpolation={self._interpolation})"


def _time_and_value_of(time_value_pair: tuple[GameTime, float]) -> tuple[GameTime, float]:
    """The time and the value of `time_value_pair`, one of those a curve is made of: raises `TypeError` for anything
    but a pair, such as a time alone, which a mapping of times to values hands over."""
    try:
        time, value = time_value_pair
    except (TypeError, ValueError):
        raise TypeError(f"a curve is made of pairs of a GameTime and a value, not {time_value_pair!r}") from None
    return time, value


def _finite_step_after(time: GameTime, steps_before: list[float]) -> float:
    """The step of `time`, a curve's next after `steps_before`: raises `TypeError` for anything but a `GameTime`, and
    `ValueError` for one not finite, or not after the last of them."""
    check_game_time(time, "a curve's time")
    step = float(time.steps)
    if not math.isfinite(step):
        raise ValueError(f"a curve's time is finite, not {time!r}")
    if steps_before and step <= steps_before[-1]:
        raise ValueError(f"a curve's times rise, and {time!r} is not after {GameTime(steps=steps_before[-1])!r}")
    return step


def _finite_value_after(value: float, time: GameTime, values_before: list[float]) -> float:
    """`value`, a curve's at `time` and its next after `values_before`, as a float: raises `TypeError` for anything
    but a number, and `ValueError` for one not finite, or further from the last of them than a float holds, between
    which no line has a slope."""
    if not isinstance(value, _SCALAR_TYPES):
        raise TypeError(f"a curve's value is a number, not {value!r} at {time!r}")
    if not math.isfinite(value):
        raise ValueError(f"a curve's value is finite, not {value!r} at {time!r}")
    if values_before and not math.isfinite(value - values_before[-1]):
        raise ValueError(f"a curve's value lies within a float of the one before, not {value!r} at {time!r}")
    return float(value)
