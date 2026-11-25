"""A growable series of values indexed by game time."""

from __future__ import annotations

import math
import numbers
from typing import TYPE_CHECKING, final, overload

import numpy
import scipy.ndimage

from sc2nachos.gametime._game_time import GameTime, check_game_time
from sc2nachos.gametime._time_dependent import NO_DEFAULT, default_of_many_times, steps_of_many_times

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from numpy import ndarray


INITIAL_BUFFER = 128


@final
class TimeSeries[T: float]:
    """Values recorded against the game time they were observed at, one a step: a `TimeDependent` that covers the
    steps from its `start` to its `end`.

    Calling it, indexing and slicing are by `GameTime`, not buffer position, so `series(time)` means the same thing for
    the whole life of the series. A time between two steps reads the step before it, whose value holds until the next:
    `series(api.time - GameTime(seconds=1))` reads 23 steps back, a second being 22.4. A slice is a copy, sharing no
    memory with the series. Steps are contiguous: a gap between appends is filled by linear interpolation, rounded to
    the nearest whole number in an integer series, a tie up.
    """

    # Makes a numpy scalar on the left, `numpy.float64(2) * series`, hand over to the reflected operator instead of
    # reading the series as an array of its (step, value) pairs.
    __array_ufunc__ = None

    _buffer: ndarray
    _start: int | None
    _size: int

    def __init__(self, buffer: ndarray, start: GameTime | None, size: int) -> None:
        """A series over `buffer`, holding the `size` values recorded one a step from `start` onward, a whole number
        of steps.

        `buffer` may be longer than `size`, leaving room to append. An empty series has no start; the first append
        sets it. Raises `TypeError` for a `start` that is not a `GameTime`, and `ValueError` for one that is not a
        whole number of steps.
        """
        self._set_up(buffer, None if start is None else _whole_step_of(start, "start"), size)

    @classmethod
    def _from_steps(cls, buffer: ndarray, start: int | None, size: int) -> TimeSeries[T]:
        """A series over `buffer` from step `start`, made without a `GameTime`, for the operations that make one."""
        series = cls.__new__(cls)
        series._set_up(buffer, start, size)
        return series

    def _set_up(self, buffer: ndarray, start: int | None, size: int) -> None:
        """Hold the `size` values of `buffer` from step `start`, once they are checked."""
        if not len(buffer):
            raise ValueError("buffer must not be empty; a series needs room to record at least one value")
        if size < 0:
            raise ValueError(f"size must not be negative, got {size}")
        if size > len(buffer):
            raise ValueError(f"buffer of {len(buffer)} is too small for {size} values")
        if size and start is None:
            raise ValueError(f"a series of {size} values needs a start time")
        self._buffer = buffer
        self._start = start if size else None
        self._size = size

    @classmethod
    def empty(cls, dtype: type[T], *, initial_size: int = INITIAL_BUFFER) -> TimeSeries[T]:
        """An empty series; the time of the first append becomes its start."""
        return cls._from_steps(numpy.zeros(initial_size, dtype=dtype), None, 0)

    def _empty_like(self) -> TimeSeries[T]:
        """An empty series of the same type, for an operation whose result holds no values."""
        return TimeSeries._from_steps(numpy.zeros(INITIAL_BUFFER, dtype=self.dtype), None, 0)

    @property
    def size(self) -> int:
        """The number of recorded steps."""
        return self._size

    @property
    def start(self) -> GameTime:
        """The time of the first recorded step. An empty series has none, and raises `ValueError`."""
        return GameTime(steps=self._first_step)

    @property
    def end(self) -> GameTime:
        """The time of the most recently recorded step, which the series holds a value at, where a slice leaves out
        the time it ends at. An empty series has none, and raises `ValueError`."""
        return GameTime(steps=self._last_step)

    @property
    def _first_step(self) -> int:
        """The first recorded step; an empty series has none."""
        if self._start is None:
            raise ValueError("an empty series has no start step")
        return self._start

    @property
    def _last_step(self) -> int:
        """The most recently recorded step; an empty series has none."""
        return self._first_step + self._size - 1

    @property
    def last_value(self) -> T:
        """The value at the most recently recorded step; an empty series has none."""
        if self._start is None:
            raise ValueError("an empty series has no last value")
        return self._buffer.item(self._size - 1)

    @property
    def values(self) -> ndarray:
        """The recorded values, as a view of the underlying array."""
        return self._buffer[: self._size]

    @property
    def dtype(self) -> numpy.dtype:
        """The type of the recorded values."""
        return self._buffer.dtype

    def __len__(self) -> int:
        return self._size

    def __iter__(self) -> Iterator[tuple[GameTime, T]]:
        """Each recorded step's time and value, in order."""
        for step, value in enumerate(self.values.tolist(), start=self._start or 0):
            yield GameTime(steps=step), value

    def append(self, time: GameTime, value: T) -> None:
        """Record `value` at `time`, interpolating linearly across any gap since the last recorded step. Raises
        `TypeError` for a `time` that is not a `GameTime`, and `ValueError` for one that is not a whole number of
        steps, or is not after the last recorded."""
        step = _whole_step_of(time, "time")
        if self._size == 0:
            self._start = step
            self._buffer[0] = value
            self._size = 1
            return
        if step <= self._last_step:
            raise ValueError(f"step {step} is not after the last recorded step {self._last_step}")

        index = step - self._first_step
        if index >= self._buffer.size:
            grown = numpy.zeros(2 * index, dtype=self._buffer.dtype)
            grown[: self._size] = self.values
            self._buffer = grown

        gap = step - self._last_step
        if gap == 1:
            self._buffer[index] = value
        else:
            interpolated = numpy.linspace(self._buffer[index - gap], value, num=gap + 1)
            if numpy.issubdtype(self._buffer.dtype, numpy.integer):
                # A tie up, as `GameTime.nearest_step` has it; `numpy.rint` would send it to the even value.
                floored = numpy.floor(interpolated)
                interpolated = floored + (interpolated - floored >= 0.5)
            self._buffer[index - gap + 1 : index + 1] = interpolated[1:]
        self._size += gap

    @overload
    def __call__(self, time: GameTime, /) -> T: ...

    @overload
    def __call__[D](self, time: GameTime, /, *, default: D) -> T | D: ...

    @overload
    def __call__(self, time: Sequence[GameTime] | ndarray, /, *, default: float = ...) -> ndarray: ...

    def __call__(self, time: GameTime | Sequence[GameTime] | ndarray, /, *, default: object = NO_DEFAULT) -> object:
        """The value at `time`, or an array of the values at each of many, a sequence of `GameTime`s or an array of
        steps, in its shape; each read at its step, or the step before a time between two.

        A step never recorded raises `IndexError`, or reads `default` where one is given: any value for one time,
        `None` among them, and a number for many, each step not recorded reading it. The array is of the series's
        type, or of what numpy makes of it and a default it reads. Raises `TypeError` as a `TimeDependent` does and
        for a default of many times that is no number, and `ValueError` for a time not finite, such as
        `GameTime.INFINITE`. @public"""
        if not isinstance(time, GameTime):
            return self._values_at_steps(steps_of_many_times(time), default_of_many_times(default))
        step = _step_reached_at(time, "time")
        if self._start is not None and 0 <= step - self._start < self._size:
            return self._buffer.item(step - self._start)
        if default is NO_DEFAULT:
            raise self._step_not_recorded_error(step)
        return default

    def _values_at_steps(self, steps: ndarray, default: float | None) -> ndarray:
        """The value at each of `steps`, or the step before one between two, a step never recorded reading `default`
        or raising `IndexError` where that is `None`; raises `ValueError` for a step not finite."""
        if not numpy.isfinite(steps).all():
            raise ValueError("a time not finite has no step, so a series has no value at it")
        # Compared as floats, since a step past what an integer holds has no index to compare.
        steps_from_start = numpy.floor(steps) - (self._start or 0)
        recorded = (steps_from_start >= 0) & (steps_from_start < self._size)
        if recorded.all():
            return numpy.asarray(self._buffer[steps_from_start.astype(numpy.intp)])
        if default is None:
            raise self._step_not_recorded_error(math.floor(steps[~recorded].item(0)))
        indices = numpy.where(recorded, steps_from_start, 0).astype(numpy.intp)
        return numpy.where(recorded, self._buffer[indices], default)

    def _index(self, step: int) -> int:
        """The buffer position of a recorded step."""
        if self._start is None or not 0 <= step - self._start < self._size:
            raise self._step_not_recorded_error(step)
        return step - self._start

    def _step_not_recorded_error(self, step: int) -> IndexError:
        """The error for reading `step`, which the series never recorded."""
        if self._start is None:
            return IndexError(f"step {step} is outside an empty series")
        return IndexError(f"step {step} outside the recorded range {self._start}-{self._last_step}")

    def _slice(self, item: slice) -> tuple[slice, int | None]:
        """A range of times as a range of buffer positions, none in an empty series, and the step it starts at, or
        `None` from the start."""
        if item.step is not None:
            raise NotImplementedError("TimeSeries does not support strided slicing")
        start_step = None if item.start is None else _step_reached_at(item.start, "a slice's start")
        stop_step = None if item.stop is None else _step_reached_at(item.stop, "a slice's stop")
        if self._start is None:
            return slice(0, 0), start_step
        start = 0 if start_step is None else start_step - self._start
        stop = self._size if stop_step is None else stop_step - self._start
        if start < 0 or stop < 0:
            raise IndexError(f"steps {start_step}:{stop_step} begin before the first recorded step {self._start}")
        return slice(start, stop), start_step

    @overload
    def __getitem__(self, item: GameTime) -> T: ...

    @overload
    def __getitem__(self, item: slice) -> TimeSeries[T]: ...

    def __getitem__(self, item: GameTime | slice) -> T | TimeSeries[T]:
        """The value at a time, or a copy of the series over a range of times, from its start up to its end and
        without it, each read at its step, or the step before a time between two. Raises `IndexError` for a step not
        recorded, or a range that begins before the first; `TypeError` for anything but a `GameTime` or a slice of
        them; and `ValueError` for a time not finite, such as `GameTime.INFINITE`."""
        if isinstance(item, slice):
            positions, start_step = self._slice(item)
            values = self.values[positions]
            if not len(values):
                return self._empty_like()
            start = self._start if start_step is None else start_step
            return TimeSeries._from_steps(values.copy(), start, len(values))
        return self._buffer.item(self._index(_step_reached_at(item, "an index")))

    def __setitem__(self, item: GameTime | slice, value: T | ndarray) -> None:
        if isinstance(item, slice):
            self.values[self._slice(item)[0]] = value
        else:
            self.values[self._index(_step_reached_at(item, "an index"))] = value

    def min(self) -> T:
        """The smallest recorded value."""
        return self.values.min().item()

    def max(self) -> T:
        """The largest recorded value."""
        return self.values.max().item()

    def sum(self) -> T:
        """The sum of the recorded values."""
        return self.values.sum().item()

    def mean(self) -> float:
        """The mean of the recorded values. A float even for an integer series."""
        return self.values.mean().item()

    def std(self) -> float:
        """The standard deviation of the recorded values. A float even for an integer series."""
        return self.values.std().item()

    def derivative_at(self, time: GameTime, *, window: GameTime = GameTime(steps=100)) -> float | None:
        """The rate of change per second over the `window` ending at `time`: `time` read at its step, or the step
        before a time between two, and `window` rounded to the nearest whole step, a tie up.

        `None` if the series does not cover the whole range. Raises `TypeError` for a `time` or a `window` that is not
        a `GameTime`, and `ValueError` for a window under one step once rounded, or either not finite.
        """
        step = _step_reached_at(time, "time")
        check_game_time(window, "window")
        window_steps = window.nearest_step
        if window_steps <= 0:
            raise ValueError(f"a window is at least one step, not {window!r}")
        if self._start is None:
            return None
        start = step - window_steps
        if not (self._start <= start and step <= self._last_step):
            return None
        low = self._buffer.item(start - self._start)
        high = self._buffer.item(step - self._start)
        return (high - low) / GameTime(steps=window_steps).seconds

    def gaussian_filter(self, *, sigma: GameTime, mode: str = "nearest") -> TimeSeries[float]:
        """A copy with a Gaussian filter of standard deviation `sigma` applied along the series. Raises `TypeError` for
        a `sigma` that is not a `GameTime`."""
        check_game_time(sigma, "sigma")
        if not self._size:
            return TimeSeries.empty(float)
        filtered = scipy.ndimage.gaussian_filter1d(self.values.astype(float), sigma=sigma.steps, mode=mode)
        return TimeSeries._from_steps(filtered, self._start, self._size)

    def uniform_filter(self, *, size: GameTime, mode: str = "nearest") -> TimeSeries[float]:
        """A copy with a moving average over `size` applied along the series. Raises `TypeError` for a `size` that is
        not a `GameTime`, and `ValueError` for one that is not a whole number of steps, at least 1."""
        check_game_time(size, "size")
        if not size.is_integer() or size.steps < 1:
            raise ValueError(f"a moving average is over a whole number of steps, at least 1, not {size!r}")
        if not self._size:
            return TimeSeries.empty(float)
        filtered = scipy.ndimage.uniform_filter(self.values.astype(float), size=int(size.steps), mode=mode)
        return TimeSeries._from_steps(filtered, self._start, self._size)

    def copy(self) -> TimeSeries[T]:
        """An independent copy, sharing no memory with this series."""
        if not self._size:
            return self._empty_like()
        return TimeSeries._from_steps(self.values.copy(), self._start, self._size)

    def _combine(self, other: TimeSeries[T] | T, op: Callable[[ndarray, ndarray | T], ndarray]) -> TimeSeries[T]:
        """`op` applied with a scalar, or with another series over the steps both record."""
        if not isinstance(other, TimeSeries):
            if self._start is None:
                return self._empty_like()
            return TimeSeries._from_steps(op(self.values, other), self._start, self._size)
        if self._start is None or other._start is None:
            return self._empty_like()
        start = max(self._start, other._start)
        size = min(self._last_step, other._last_step) - start + 1
        if size <= 0:
            return self._empty_like()
        mine = self._buffer[start - self._start : start - self._start + size]
        theirs = other._buffer[start - other._start : start - other._start + size]
        return TimeSeries._from_steps(op(mine, theirs), start, size)

    def __add__(self, other: TimeSeries[T] | T) -> TimeSeries[T]:
        if not isinstance(other, OPERAND_TYPES):
            return NotImplemented
        return self._combine(other, numpy.add)

    def __sub__(self, other: TimeSeries[T] | T) -> TimeSeries[T]:
        if not isinstance(other, OPERAND_TYPES):
            return NotImplemented
        return self._combine(other, numpy.subtract)

    def __mul__(self, other: TimeSeries[T] | T) -> TimeSeries[T]:
        if not isinstance(other, OPERAND_TYPES):
            return NotImplemented
        return self._combine(other, numpy.multiply)

    __radd__ = __add__
    __rmul__ = __mul__

    def __rsub__(self, other: T) -> TimeSeries[T]:
        if not isinstance(other, SCALAR_TYPES):
            return NotImplemented
        return self._combine(other, lambda values, scalar: numpy.subtract(scalar, values))

    def __repr__(self) -> str:
        if self._size == 0:
            return f"{type(self).__name__}(empty, dtype={self.dtype})"
        return f"{type(self).__name__}(steps {self._start}-{self._last_step}, dtype={self.dtype})"


def _whole_step_of(time: GameTime, argument_name: str) -> int:
    """The step `time`, given as `argument_name`, is: raises `TypeError` for anything but a `GameTime`, and
    `ValueError` for one that is not a whole number of steps."""
    check_game_time(time, argument_name)
    if not time.is_integer():
        raise ValueError(f"{argument_name} is a whole number of steps, not {time!r}")
    return int(time.steps)


def _step_reached_at(time: GameTime, argument_name: str) -> int:
    """The step the game has reached at `time`, given as `argument_name`: its own, or the step before a time between
    two. Raises `TypeError` for anything but a `GameTime`, and `ValueError` for one not finite."""
    check_game_time(time, argument_name)
    try:
        return math.floor(time.steps)
    except (OverflowError, ValueError):
        raise ValueError(f"{time!r} is not finite, so it has no step") from None


# The operands the arithmetic above accepts, as constants: an inline `a | b` union is rebuilt on every call. They
# sit below the class because `TimeSeries` is one of them.
# `numbers.Real` catches numpy scalars, which subclass neither `int` nor `float`; it comes last, being the slowest.
SCALAR_TYPES = (int, float, numbers.Real)
OPERAND_TYPES = (TimeSeries, *SCALAR_TYPES)
