"""A growable series of values indexed by step."""

from __future__ import annotations

import numbers
from typing import TYPE_CHECKING, Self, final, overload

import numpy
import scipy.ndimage

from sc2nachos._checks import whole_steps_of
from sc2nachos.game_time import GameTime

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from numpy import ndarray


INITIAL_BUFFER = 128
_DEFAULT_DERIVATIVE_WINDOW = GameTime(steps=100)


@final
class TimeSeries[T: float]:
    """Values recorded against the step they were observed at.

    Indexing and slicing are by step, not buffer position, so `series[step]` means the same thing for the whole life
    of the series, and `series[series.last_time.steps]` is the newest value. A slice is a copy, sharing no memory with
    the series. Steps are contiguous: a gap between appends is filled by linear interpolation, rounded to the nearest
    whole number in an integer series, a tie up.
    """

    # Makes a numpy scalar on the left, `numpy.float64(2) * series`, hand over to the reflected operator instead of
    # reading the series as an array of its (step, value) pairs.
    __array_ufunc__ = None

    _buffer: ndarray
    _start: int | None
    _size: int

    def __init__(self, buffer: ndarray, start: int | None, size: int) -> None:
        """A series over `buffer`, holding the `size` values recorded from step `start` onward.

        `buffer` may be longer than `size`, leaving room to append. An empty series has no start; the first append
        sets it.
        """
        if not len(buffer):
            raise ValueError("buffer must not be empty; a series needs room to record at least one value")
        if size < 0:
            raise ValueError(f"size must not be negative, got {size}")
        if size > len(buffer):
            raise ValueError(f"buffer of {len(buffer)} is too small for {size} values")
        if size and start is None:
            raise ValueError(f"a series of {size} values needs a start step")
        self._buffer = buffer
        self._start = start if size else None
        self._size = size

    @classmethod
    def empty(cls, dtype: type[T], *, initial_size: int = INITIAL_BUFFER) -> TimeSeries[T]:
        """An empty series; the step of the first append becomes its start."""
        return cls(numpy.zeros(initial_size, dtype=dtype), None, 0)

    def _empty_like(self) -> Self:
        """An empty series of the same type, for an operation whose result holds no values."""
        return type(self)(numpy.zeros(INITIAL_BUFFER, dtype=self.dtype), None, 0)

    @property
    def size(self) -> int:
        """The number of recorded steps."""
        return self._size

    @property
    def start_time(self) -> GameTime:
        """The time of the first recorded step. An empty series has none, and raises `ValueError`."""
        return GameTime(steps=self._first_step)

    @property
    def last_time(self) -> GameTime:
        """The time of the most recently recorded step. An empty series has none, and raises `ValueError`."""
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

    def __iter__(self) -> Iterator[tuple[int, T]]:
        yield from enumerate(self.values.tolist(), start=self._start or 0)

    def append(self, step: int, value: T) -> None:
        """Record `value` at `step`, interpolating linearly across any gap since the last recorded step."""
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
                # A tie up, as `sticky_round` has it; `numpy.rint` would send it to the even value.
                floored = numpy.floor(interpolated)
                interpolated = floored + (interpolated - floored >= 0.5)
            self._buffer[index - gap + 1 : index + 1] = interpolated[1:]
        self._size += gap

    def at(self, step: int) -> T | None:
        """The value at `step`, or `None` if that step was never recorded."""
        if self._start is None:
            return None
        index = step - self._start
        if not 0 <= index < self._size:
            return None
        return self._buffer.item(index)

    def at_time(self, time: GameTime) -> T | None:
        """The value at `time`, taken from the nearest step, or `None` if that step was never recorded."""
        return self.at(round(time.steps))

    def _index(self, step: int) -> int:
        """The buffer position of a recorded step."""
        if self._start is None:
            raise IndexError(f"step {step} is outside an empty series")
        index = step - self._start
        if not 0 <= index < self._size:
            raise IndexError(f"step {step} outside the recorded range {self._start}-{self._last_step}")
        return index

    def _slice(self, item: slice) -> slice:
        """A range of steps as a range of buffer positions: none in an empty series."""
        if item.step is not None:
            raise NotImplementedError("TimeSeries does not support strided slicing")
        if self._start is None:
            return slice(0, 0)
        start = 0 if item.start is None else item.start - self._start
        stop = self._size if item.stop is None else item.stop - self._start
        if start < 0 or stop < 0:
            raise IndexError(f"slice {item.start}:{item.stop} begins before the first recorded step {self._start}")
        return slice(start, stop)

    @overload
    def __getitem__(self, item: int) -> T: ...

    @overload
    def __getitem__(self, item: slice) -> TimeSeries[T]: ...

    def __getitem__(self, item: int | slice) -> T | TimeSeries[T]:
        """The value at a step, or a copy of the series over a range of steps. Raises `IndexError` for a step not
        recorded, or a range that begins before the first."""
        if isinstance(item, slice):
            values = self.values[self._slice(item)]
            if not len(values):
                return self._empty_like()
            start = self._start if item.start is None else item.start
            return TimeSeries(values.copy(), start, len(values))
        return self._buffer.item(self._index(item))

    def __setitem__(self, item: int | slice, value: T | ndarray) -> None:
        if isinstance(item, slice):
            self.values[self._slice(item)] = value
        else:
            self.values[self._index(item)] = value

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

    def derivative_at(self, step: int, *, window: GameTime = _DEFAULT_DERIVATIVE_WINDOW) -> float | None:
        """The rate of change per second over the `window` ending at `step`, the window rounded to the nearest whole
        step.

        `None` if the series does not cover the whole range. Raises `ValueError` for a `window` under one step once
        rounded.
        """
        window_steps = round(window.steps)
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
        """A copy with a Gaussian filter of standard deviation `sigma` applied along the series."""
        if not self._size:
            return TimeSeries.empty(float)
        filtered = scipy.ndimage.gaussian_filter1d(self.values.astype(float), sigma=sigma.steps, mode=mode)
        return TimeSeries(filtered, self._start, self._size)

    def uniform_filter(self, *, size: GameTime, mode: str = "nearest") -> TimeSeries[float]:
        """A copy with a moving average over `size` applied along the series. Raises `ValueError` for a `size` that is
        not a whole number of steps, at least 1."""
        size_steps = whole_steps_of(size)
        if size_steps is None or size_steps < 1:
            raise ValueError(f"a moving average is over a whole number of steps, at least 1, not {size!r}")
        if not self._size:
            return TimeSeries.empty(float)
        filtered = scipy.ndimage.uniform_filter(self.values.astype(float), size=size_steps, mode=mode)
        return TimeSeries(filtered, self._start, self._size)

    def copy(self) -> Self:
        """An independent copy, sharing no memory with this series."""
        if not self._size:
            return self._empty_like()
        return type(self)(self.values.copy(), self._start, self._size)

    def _combine(self, other: TimeSeries[T] | T, op: Callable[[ndarray, ndarray | T], ndarray]) -> TimeSeries[T]:
        """`op` applied with a scalar, or with another series over the steps both record."""
        if not isinstance(other, TimeSeries):
            if self._start is None:
                return self._empty_like()
            return TimeSeries(op(self.values, other), self._start, self._size)
        if self._start is None or other._start is None:
            return self._empty_like()
        start = max(self._start, other._start)
        size = min(self._last_step, other._last_step) - start + 1
        if size <= 0:
            return self._empty_like()
        mine = self._buffer[start - self._start : start - self._start + size]
        theirs = other._buffer[start - other._start : start - other._start + size]
        return TimeSeries(op(mine, theirs), start, size)

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


# The operands the arithmetic above accepts, as constants: an inline `a | b` union is rebuilt on every call. They
# sit below the class because `TimeSeries` is one of them.
# `numbers.Real` catches numpy scalars, which subclass neither `int` nor `float`; it comes last, being the slowest.
SCALAR_TYPES = (int, float, numbers.Real)
OPERAND_TYPES = (TimeSeries, *SCALAR_TYPES)
