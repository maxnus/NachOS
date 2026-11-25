"""A few values given at game times, joined between them."""

from __future__ import annotations

import math
from bisect import bisect_right
from typing import TYPE_CHECKING, Any, final, overload

import numpy

from sc2nachos.gametime._game_time import GameTime, check_game_time
from sc2nachos.gametime._interpolation import Interpolation
from sc2nachos.gametime._time_dependent import NO_DEFAULT, array_of_values, is_number, steps_of_many_times

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence

    from numpy import ndarray

    from sc2nachos.gametime._supports_interpolation import SupportsInterpolation


# A curve of a value a step would fill a screen with its repr, so one of more pairs shows all but one of this many
# from its start, then "..." and its last.
_MOST_PAIRS_A_REPR_SHOWS = 6

# What a value that is no number has to do for a curve to read between two of them, as `SupportsInterpolation` says.
_OPERATIONS_OF_A_VALUE = ("__add__", "__sub__", "__mul__")


@final
class TimeCurve[T]:
    """A `TimeDependent` made of a few values, each given at a game time, such as a rate of losses that rises from
    5:00 to 6:00: `TimeCurve([(GameTime(seconds=300), 0.0), (GameTime(seconds=360), 0.004)])`. Between two of its
    times it reads by its `Interpolation`: along the line from the value before to the value after, by default, or
    with `Interpolation.STEP` the value before, held from its time until the next. Before its first time it holds its
    first value, and from its last time on its last, so it covers every time, `GameTime.INFINITE` too, and no read
    raises `IndexError` or reads a default.

    Its values are numbers, which it holds as floats, or all of another kind that adds, subtracts and multiplies by a
    number, as `Resources` and a `Point` do: `TimeCurve([(GameTime.ZERO, Resources(0, 0)), (GameTime(seconds=300),
    Resources(900, 300))])`. Many times of numbers are read through numpy at once. Those of another kind are read by
    the values' own arithmetic, a value at a time, into an array of objects, which along a line is far slower.

    A time is read as it is given, between two steps too. A curve is not changed once made. `len` counts its values,
    and iterating over it hands out each time with its value, in order.
    """

    __slots__ = (
        "_interpolation",
        "_step_array",
        "_steps",
        "_times",
        "_value_array",
        "_values",
        "_values_are_numbers",
    )

    _interpolation: Interpolation
    _times: list[GameTime]
    # A read of one time goes through these lists, which is several times quicker than numpy for one value, and a read
    # of many through the arrays below.
    _steps: list[float]
    _values: list[T]
    _step_array: ndarray
    # Of floats where the values are numbers, and else of objects, a value each.
    _value_array: ndarray
    _values_are_numbers: bool

    # Numbers come first, so that a curve given whole numbers is one of floats, which it holds them as.
    @overload
    def __init__(
        self: TimeCurve[float],
        time_value_pairs: Iterable[tuple[GameTime, float]],
        *,
        interpolation: Interpolation = ...,
    ) -> None: ...

    @overload
    def __init__[V: SupportsInterpolation](
        self: TimeCurve[V], time_value_pairs: Iterable[tuple[GameTime, V]], *, interpolation: Interpolation = ...
    ) -> None: ...

    def __init__(
        self, time_value_pairs: Iterable[tuple[GameTime, Any]], *, interpolation: Interpolation = Interpolation.LINEAR
    ) -> None:
        """A curve through `time_value_pairs`, each a game time and the value at it, in order of time. Raises
        `TypeError` for anything but pairs, a time that is not a `GameTime`, a value that is neither a number nor adds,
        subtracts and multiplies by one, two values no line can be worked out between, as a number and one that is
        none are, or an `interpolation` that is not an `Interpolation`; and `ValueError` for no pair, a time or a
        number not finite, a time not after the one before it, or a number further from the one before it than a float
        holds."""
        if not isinstance(interpolation, Interpolation):
            raise TypeError(f"interpolation is an Interpolation, not {interpolation!r}")
        self._interpolation = interpolation
        self._times = []
        self._steps = []
        self._values = []
        for time_value_pair in time_value_pairs:
            time, value = _time_and_value_of(time_value_pair)
            self._steps.append(_finite_step_after(time, self._steps))
            self._values.append(_value_after(value, time, self._values))
            self._times.append(time)
        if not self._steps:
            raise ValueError("a time curve needs at least one value")
        self._values_are_numbers = is_number(self._values[0])
        self._step_array = numpy.array(self._steps, dtype=float)
        self._value_array = array_of_values(self._values)

    @property
    def interpolation(self) -> Interpolation:
        """How it reads a time between two of its own."""
        return self._interpolation

    def __len__(self) -> int:
        return len(self._steps)

    def __iter__(self) -> Iterator[tuple[GameTime, T]]:
        """Each of its times with the value at it, in order."""
        yield from zip(self._times, self._values, strict=True)

    @overload
    def __call__(self, time: GameTime, /) -> T: ...

    @overload
    def __call__[D](self, time: GameTime, /, *, default: D) -> T | D: ...

    @overload
    def __call__(self, time: Sequence[GameTime] | ndarray, /, *, default: object = ...) -> ndarray: ...

    def __call__(self, time: GameTime | Sequence[GameTime] | ndarray, /, *, default: object = NO_DEFAULT) -> object:
        """The value at `time`, or an array of the values at each of many, a sequence of `GameTime`s or an array of
        steps, in its shape: of floats, or of objects where its values are no numbers. A curve covers every time, so
        `default` is never read, whatever it is. Raises `TypeError` as a `TimeDependent` does, and `ValueError` for a
        time of NaN steps. @public"""
        if not isinstance(time, GameTime):
            return self._values_at_steps(steps_of_many_times(time))
        step = time.steps
        if step != step:
            raise ValueError(f"{time!r} is no time, so a curve has no value at it")
        pairs_reached = bisect_right(self._steps, step)
        if pairs_reached == 0:
            return self._values[0]
        value_before: Any = self._values[pairs_reached - 1]
        if pairs_reached == len(self._steps) or self._interpolation is Interpolation.STEP:
            return value_before
        step_before = self._steps[pairs_reached - 1]
        step_after = self._steps[pairs_reached]
        value_after: Any = self._values[pairs_reached]
        if self._values_are_numbers:
            # In the order `numpy.interp` works it out, so that one time and many read the same to the last bit.
            slope = (value_after - value_before) / (step_after - step_before)
            return slope * (step - step_before) + value_before
        # A value that is no number is not asked to divide, so the share of the way is worked out first.
        return value_before + (value_after - value_before) * ((step - step_before) / (step_after - step_before))

    def _values_at_steps(self, steps: ndarray) -> ndarray:
        """The value at each of `steps`, worked out as a call with one time works it out."""
        if numpy.isnan(steps).any():
            raise ValueError("a time of NaN steps is no time, so a curve has no value at it")
        if self._values_are_numbers and self._interpolation is Interpolation.LINEAR:
            return numpy.asarray(numpy.interp(steps, self._step_array, self._value_array))
        # In one dimension, since an array of no dimension would pick a single value out, and numpy make an array of
        # numbers of one that is a sequence.
        steps_in_a_row = steps.ravel()
        pairs_reached = numpy.searchsorted(self._step_array, steps_in_a_row, side="right")
        values = self._value_array[numpy.maximum(pairs_reached - 1, 0)]
        if self._interpolation is Interpolation.LINEAR:
            between_two = (pairs_reached > 0) & (pairs_reached < len(self._steps))
            pairs_after = pairs_reached[between_two]
            steps_before = self._step_array[pairs_after - 1]
            shares_of_the_way = (steps_in_a_row[between_two] - steps_before) / (
                self._step_array[pairs_after] - steps_before
            )
            values_before = values[between_two]
            values[between_two] = values_before + (self._value_array[pairs_after] - values_before) * shares_of_the_way
        return values.reshape(steps.shape)

    def __repr__(self) -> str:
        """Its pairs and its interpolation; of more pairs than `_MOST_PAIRS_A_REPR_SHOWS`, the first ones, `...` and
        the last."""
        pairs = list(self)
        if len(pairs) > _MOST_PAIRS_A_REPR_SHOWS:
            pairs_shown = [*map(repr, pairs[: _MOST_PAIRS_A_REPR_SHOWS - 1]), "...", repr(pairs[-1])]
        else:
            pairs_shown = [*map(repr, pairs)]
        return f"{type(self).__name__}([{', '.join(pairs_shown)}], interpolation={self._interpolation})"


def _time_and_value_of(time_value_pair: tuple[GameTime, Any]) -> tuple[GameTime, Any]:
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


def _value_after(value: Any, time: GameTime, values_before: list[Any]) -> Any:
    """`value`, a curve's at `time` and its next after `values_before`, a number as a float: raises `TypeError` for a
    number among values that are none or the other way around, for a value that is neither a number nor adds,
    subtracts and multiplies by one, and as `_check_a_line_joins` does; and `ValueError` as `_finite_number_after`
    does."""
    if values_before and is_number(value) != is_number(values_before[-1]):
        raise TypeError(
            f"a curve's values are all numbers or none of them is, and {value!r} at {time!r} "
            f"comes after {values_before[-1]!r}"
        )
    if is_number(value):
        return _finite_number_after(value, time, values_before)
    if not all(hasattr(type(value), operation) for operation in _OPERATIONS_OF_A_VALUE):
        raise TypeError(
            f"a curve's value is a number, or adds, subtracts and multiplies by one, not {value!r} at {time!r}"
        )
    if values_before:
        _check_a_line_joins(values_before[-1], value, time)
    return value


def _check_a_line_joins(value_before: Any, value: Any, time: GameTime) -> None:
    """Raise `TypeError` unless the value halfway from `value_before` to `value`, a curve's at `time`, can be worked
    out as a read works it out: two values of different kinds cannot, nor can ones that have the operations and raise
    in them. Found as the curve is made, and not at the first read between the two, which may be a consumer's."""
    try:
        _ = value_before + (value - value_before) * 0.5
    except Exception as error:
        raise TypeError(
            f"a curve's values add, subtract and multiply by a number, which {value!r} at {time!r} "
            f"and {value_before!r} before it do not: {error}"
        ) from error


def _finite_number_after(number: float, time: GameTime, numbers_before: list[float]) -> float:
    """`number`, a curve's at `time` and its next after `numbers_before`, as a float: raises `ValueError` for one not
    finite, or further from the last of them than a float holds, between which no line has a slope."""
    if not math.isfinite(number):
        raise ValueError(f"a curve's value is finite, not {number!r} at {time!r}")
    if numbers_before and not math.isfinite(number - numbers_before[-1]):
        raise ValueError(f"a curve's value lies within a float of the one before, not {number!r} at {time!r}")
    return float(number)
