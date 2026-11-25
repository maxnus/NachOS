"""What every value that depends on the game time shares: a value at a time."""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Final, Protocol, cast, overload

import numpy

from sc2nachos.gametime._game_time import _SCALAR_TYPES, GameTime, check_game_time

if TYPE_CHECKING:
    from collections.abc import Sequence

    from numpy import ndarray


class _NoDefault(Enum):
    """What a call's `default` holds where it was given none, `None` being a default a bot gives."""

    NO_DEFAULT = "no default"

    def __repr__(self) -> str:
        """How a call's signature shows a default not given."""
        return self.name


NO_DEFAULT: Final = _NoDefault.NO_DEFAULT


class TimeDependent[T](Protocol):
    """A value that depends on the game time, such as a supply, a rate of losses or the `Resources` a bot expects to
    have earned: called with a `GameTime` it gives the value at that time, and with many times an array of the values
    at them, through numpy. `TimeSeries` records a number a step, `TimeCurve` joins a few values given at their times,
    and `TimeFunction` asks a function.

    Many times are a sequence of `GameTime`s or an array of steps, and their values an array of the same shape: of
    numbers where the values are numbers, and of objects, a value each, where they are anything else. A time it does
    not cover raises `IndexError`, or reads `default` where the call is given one: any value for one time, and a
    number for many, each time not covered reading it. Raises `TypeError` for a time that is not a `GameTime`, and for
    many that are neither a sequence of them nor an array of numbers.

    Being called with a time, it passes wherever a `Callable[[GameTime], T]` is asked for, as a plain function of the
    game time does.
    """

    @overload
    def __call__(self, time: GameTime, /) -> T: ...

    @overload
    def __call__[D](self, time: GameTime, /, *, default: D) -> T | D: ...

    # The default of many times is a number and not a `T`, which would make `T` a type of what it takes as well as
    # of what it gives, and a series of integers no `TimeDependent[float]`. The kinds of other values cover every time.
    @overload
    def __call__(self, time: Sequence[GameTime] | ndarray, /, *, default: float = ...) -> ndarray: ...

    def __call__(self, time: GameTime | Sequence[GameTime] | ndarray, /, *, default: object = NO_DEFAULT) -> object:
        """The value at `time`, or an array of the values at each of many, a sequence of `GameTime`s or an array of
        steps, in its shape; a time not covered raises `IndexError`, or reads `default` where one is given.
        @public"""
        ...


def steps_of_many_times(times: Sequence[GameTime] | ndarray) -> ndarray:
    """The steps of `times`, the many a `TimeDependent` is called with, as an array of floats: an array of steps in its
    own shape, and a sequence of `GameTime`s a step each. Raises `TypeError` for an array of anything but numbers, a
    sequence of anything but `GameTime`s, and anything else, a plain number among them."""
    if isinstance(times, numpy.ndarray):
        check_array_holds_steps(times)
        return times.astype(float)
    return numpy.array([time.steps for time in game_times_of_sequence(times)], dtype=float)


def check_array_holds_steps(times: ndarray) -> None:
    """Raise `TypeError` unless `times`, an array given as many times, holds numbers."""
    if times.dtype.kind not in "iuf":
        raise TypeError(f"an array of times holds steps, whole or not, not {times.dtype}")


def game_times_of_sequence(times: Sequence[GameTime]) -> list[GameTime]:
    """`times`, given as many times and no array, as a list: raises `TypeError` for anything but a sequence of
    `GameTime`s, a plain number among them."""
    try:
        times_given = list(times)
    except TypeError:
        raise TypeError(
            f"a time is a GameTime, and many are a sequence of them or an array of steps, not {times!r}"
        ) from None
    for time in times_given:
        check_game_time(time, "each of many times")
    return times_given


# What a numpy array holds as a number. A numpy boolean, which a comparison of numpy's gives, is none of Python's
# numbers, though Python's own boolean is.
_NUMBER_TYPES = (*_SCALAR_TYPES, numpy.bool_)


def is_number(value: object) -> bool:
    """Whether `value` is a number, which a numpy array holds as one, and not a value it holds as an object."""
    return isinstance(value, _NUMBER_TYPES)


def array_of_values(values: Sequence[object]) -> ndarray:
    """`values`, those of a `TimeDependent` at many times, as an array of their length: of numbers where all are
    numbers, and else of objects, a value each. Those are handed to numpy a value at a time, since it would read a
    list of values that are sequences, as a `Point` is, as rows of numbers."""
    if all(issubclass(value_type, _NUMBER_TYPES) for value_type in set(map(type, values))):
        return numpy.array(values)
    return numpy.fromiter(values, dtype=object, count=len(values))


def default_of_many_times(default: object) -> float | None:
    """`default`, given with many times of numbers, or `None` where none was given: raises `TypeError` unless it is a
    number."""
    if default is NO_DEFAULT:
        return None
    if not is_number(default):
        raise TypeError(
            f"the default of many times is a number, which an array can hold, not {default!r}; "
            "math.nan marks a time not covered"
        )
    return cast("float", default)
