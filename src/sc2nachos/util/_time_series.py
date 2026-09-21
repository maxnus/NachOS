"""A growable series of values indexed by game step."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, overload

import numpy
import scipy.ndimage

from sc2nachos.constants import SECONDS_PER_STEP, seconds_to_steps

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from numpy import ndarray


INITIAL_BUFFER = 128


class TimeSeries[T: float]:
    """Values recorded against the game step they were observed at.

    Indexing and slicing are in game steps, not buffer positions, so `series[step]` means the same thing for the
    whole life of the series. Steps are contiguous: a gap between appends is filled by linear interpolation.
    """

    _buffer: ndarray
    _start: int | None
    _size: int

    def __init__(self, buffer: ndarray, start: int | None, size: int) -> None:
        """A series over `buffer`, holding the `size` values recorded from step `start` onward.

        `buffer` may be longer than `size`, leaving room to append. An empty series has no start; the first
        append sets it.
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
        """An empty series of this one's type, for an operation whose result holds no values."""
        return type(self)(numpy.zeros(INITIAL_BUFFER, dtype=self.dtype), None, 0)

    @property
    def size(self) -> int:
        """The number of recorded steps."""
        return self._size

    @property
    def start(self) -> int:
        """The first recorded step; an empty series has none."""
        if self._start is None:
            raise ValueError("an empty series has no start step")
        return self._start

    @property
    def last_step(self) -> int:
        """The most recently recorded step; an empty series has none."""
        return self.start + self._size - 1

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
        if step <= self.last_step:
            raise ValueError(f"step {step} is not after the last recorded step {self.last_step}")

        index = step - self.start
        if index >= self._buffer.size:
            grown = numpy.zeros(2 * index, dtype=self._buffer.dtype)
            grown[: self._size] = self.values
            self._buffer = grown

        gap = step - self.last_step
        if gap == 1:
            self._buffer[index] = value
        else:
            interpolated = numpy.linspace(self._buffer[index - gap], value, num=gap + 1)
            self._buffer[index - gap + 1 : index + 1] = interpolated[1:]
        self._size += gap

    def at(self, step: int) -> T | None:
        """The value at `step`; `None` if that step was never recorded."""
        if self._start is None:
            return None
        index = step - self._start
        if not 0 <= index < self._size:
            return None
        return self._buffer.item(index)

    def at_time(self, time: float) -> T | None:
        """The value at `time` seconds of game time, taken from the nearest step."""
        return self.at(round(seconds_to_steps(time)))

    def _index(self, step: int) -> int:
        """The buffer position of a recorded step."""
        if self._start is None:
            raise IndexError(f"step {step} is outside an empty series")
        index = step - self._start
        if not 0 <= index < self._size:
            raise IndexError(f"step {step} outside the recorded range {self._start}-{self.last_step}")
        return index

    def _slice(self, item: slice) -> slice:
        """A range of steps as a range of buffer positions."""
        if item.step is not None:
            raise NotImplementedError("TimeSeries does not support strided slicing")
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
        if isinstance(item, slice):
            values = self.values[self._slice(item)]
            if not len(values):
                return self._empty_like()
            start = self._start if item.start is None else item.start
            return TimeSeries(values, start, len(values))
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
        """The mean of the recorded values, which is a float even for an integer series."""
        return self.values.mean().item()

    def std(self) -> float:
        """The standard deviation of the recorded values, which is a float even for an integer series."""
        return self.values.std().item()

    def derivative_at(self, step: int, *, window: int = 100) -> float | None:
        """The rate of change per second across the `window` steps ending at `step`.

        `None` if the series does not cover that whole range.
        """
        if self._start is None:
            return None
        start = step - window
        if not (self._start <= start and step <= self.last_step):
            return None
        low = self._buffer.item(start - self._start)
        high = self._buffer.item(step - self._start)
        return (high - low) / (window * SECONDS_PER_STEP)

    def gaussian_filter(self, *, sigma: float, mode: str = "nearest") -> TimeSeries[float]:
        """A copy with a Gaussian filter applied along the series."""
        if not self._size:
            return TimeSeries.empty(float)
        filtered = scipy.ndimage.gaussian_filter1d(self.values.astype(float), sigma=sigma, mode=mode)
        return TimeSeries(filtered, self._start, self._size)

    def uniform_filter(self, *, size: int, mode: str = "nearest") -> TimeSeries[float]:
        """A copy with a moving average applied along the series."""
        if not self._size:
            return TimeSeries.empty(float)
        filtered = scipy.ndimage.uniform_filter(self.values.astype(float), size=size, mode=mode)
        return TimeSeries(filtered, self._start, self._size)

    def copy(self) -> Self:
        """An independent copy, sharing no memory with this series."""
        if not self._size:
            return self._empty_like()
        return type(self)(self.values.copy(), self._start, self._size)

    def _combine(self, other: TimeSeries[T] | T, op: Callable[[ndarray, ndarray | T], ndarray]) -> TimeSeries[T]:
        """`op` applied against a scalar, or against another series over the steps both record."""
        if not isinstance(other, TimeSeries):
            if self._start is None:
                return self._empty_like()
            return TimeSeries(op(self.values, other), self._start, self._size)
        if self._start is None or other._start is None:
            return self._empty_like()
        start = max(self._start, other._start)
        size = min(self.last_step, other.last_step) - start + 1
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
        return f"{type(self).__name__}(steps {self._start}-{self.last_step}, dtype={self.dtype})"


# The operands the arithmetic above accepts, as constants: an inline `a | b` union is rebuilt on every
# call. They sit below the class because `TimeSeries` is one of them.
SCALAR_TYPES = (int, float)
OPERAND_TYPES = (TimeSeries, *SCALAR_TYPES)
