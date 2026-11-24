import numpy
import pytest

from sc2nachos import GameTime
from sc2nachos.gametime import TimeFunction


def _losses_from_five_minutes(time: GameTime) -> float:
    return 0.004 if time >= GameTime(seconds=300) else 0.0


class TestOneTime:
    def test_asks_the_function(self):
        losses = TimeFunction(_losses_from_five_minutes)
        assert losses(GameTime(seconds=299)) == 0.0
        assert losses(GameTime(seconds=300)) == 0.004

    def test_hands_the_function_the_time_as_given(self):
        times_asked: list[GameTime] = []
        TimeFunction(lambda time: float(times_asked.append(time) or 0.0))(GameTime(steps=3.7))
        assert times_asked == [GameTime(steps=3.7)]
        assert TimeFunction(lambda time: time.seconds)(GameTime.INFINITE) == float("inf")

    def test_a_default_is_never_read(self):
        assert TimeFunction(_losses_from_five_minutes)(GameTime(seconds=9000), default=None) == 0.004

    def test_what_the_function_raises_reaches_the_caller(self):
        def losses_of_no_game(time: GameTime) -> float:
            raise LookupError(f"nothing is known of {time!r}")

        with pytest.raises(LookupError, match="nothing is known"):
            TimeFunction(losses_of_no_game)(GameTime.ZERO)
        with pytest.raises(LookupError, match="nothing is known"):
            TimeFunction(losses_of_no_game)(numpy.arange(3))


class TestManyTimes:
    def test_asks_the_function_each_time_in_turn(self):
        times_asked: list[GameTime] = []
        time_function = TimeFunction(lambda time: float(times_asked.append(time) or len(times_asked)))
        times = [GameTime(steps=5), GameTime(steps=1.5)]
        assert time_function(times).tolist() == [1.0, 2.0]
        assert times_asked == times

    def test_hands_the_function_each_step_of_an_array_as_a_game_time(self):
        times_asked: list[GameTime] = []
        time_function = TimeFunction(lambda time: float(times_asked.append(time) or time.steps))
        assert time_function(numpy.array([[4, 5], [6, 7]])).tolist() == [[4.0, 5.0], [6.0, 7.0]]
        assert times_asked == [GameTime(steps=step) for step in (4, 5, 6, 7)]
        assert all(isinstance(time.steps, int) for time in times_asked)

    def test_every_step_of_five_minutes(self):
        steps = GameTime(seconds=200).steps + numpy.arange(6720)
        rates = TimeFunction(_losses_from_five_minutes)(steps)
        assert rates.shape == (6720,)
        assert rates[2239:2241].tolist() == [0.0, 0.004]


class TestMaking:
    def test_decorates_a_function_which_is_still_called_with_a_time(self):
        @TimeFunction
        def losses(time: GameTime) -> float:
            return 0.004 if time >= GameTime(seconds=300) else 0.0

        assert isinstance(losses, TimeFunction)
        assert losses(GameTime(seconds=300)) == 0.004
        assert losses([GameTime.ZERO, GameTime(seconds=300)]).tolist() == [0.0, 0.004]

    def test_holds_its_function(self):
        assert TimeFunction(_losses_from_five_minutes).function is _losses_from_five_minutes
        assert "_losses_from_five_minutes" in repr(TimeFunction(_losses_from_five_minutes))

    def test_anything_that_cannot_be_called_raises(self):
        with pytest.raises(TypeError, match="a time function wraps a function"):
            TimeFunction(0.004)  # pyright: ignore[reportArgumentType]
