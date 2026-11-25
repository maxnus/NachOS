"""A function of the game time, read as every value that depends on it is."""

from __future__ import annotations

from typing import TYPE_CHECKING, final, overload

import numpy

from sc2nachos.gametime._game_time import GameTime
from sc2nachos.gametime._time_dependent import (
    NO_DEFAULT,
    array_of_values,
    check_array_holds_steps,
    game_times_of_sequence,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from numpy import ndarray


@final
class TimeFunction[T]:
    """A `TimeDependent` that asks a function of the game time, such as a rate of losses from 5:00 on:
    `TimeFunction(lambda time: 0.004 if time >= GameTime(seconds=300) else 0.0)`. It decorates a function too, which
    is still called with a time as it was. The function gives a value of any kind.

    Called with a time it hands the function that time, as given, and with many times each of them in turn, a step of
    an array as a `GameTime`: an array of numbers where the function gave numbers, and of objects where it gave
    anything else. It covers every time, so no read raises `IndexError` or reads a default; what the function raises
    reaches the caller.
    """

    __slots__ = ("_function",)

    _function: Callable[[GameTime], T]

    def __init__(self, function: Callable[[GameTime], T]) -> None:
        """A time-dependent value that `function` gives for a game time. Raises `TypeError` for a `function` that
        cannot be called."""
        if not callable(function):
            raise TypeError(f"a time function wraps a function of the game time, not {function!r}")
        self._function = function

    @property
    def function(self) -> Callable[[GameTime], T]:
        """The function it asks."""
        return self._function

    @overload
    def __call__(self, time: GameTime, /) -> T: ...

    @overload
    def __call__[D](self, time: GameTime, /, *, default: D) -> T | D: ...

    @overload
    def __call__(self, time: Sequence[GameTime] | ndarray, /, *, default: object = ...) -> ndarray: ...

    def __call__(self, time: GameTime | Sequence[GameTime] | ndarray, /, *, default: object = NO_DEFAULT) -> object:
        """What the function gives for `time`, or an array of what it gives for each of many, a sequence of
        `GameTime`s or an array of steps, in its shape. `default` is never read, whatever it is. Raises `TypeError`
        as a `TimeDependent` does. @public"""
        if isinstance(time, GameTime):
            return self._function(time)
        if isinstance(time, numpy.ndarray):
            check_array_holds_steps(time)
            values = [self._function(GameTime._from_steps(step)) for step in time.ravel().tolist()]
            return array_of_values(values).reshape(time.shape)
        return array_of_values([self._function(time_given) for time_given in game_times_of_sequence(time)])

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._function!r})"
