import numpy
import pytest

from sc2nachos import GameTime, Point
from sc2nachos.gamedata import Resources
from sc2nachos.gametime import TimeDependent, TimeFunction


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

    def test_a_default_is_never_read_whatever_it_is(self):
        losses = TimeFunction(_losses_from_five_minutes)
        assert losses(numpy.arange(2), default=None).tolist() == [0.0, 0.0]
        assert losses([GameTime.ZERO], default="none").tolist() == [0.0]

    def test_every_step_of_five_minutes(self):
        steps = GameTime(seconds=200).steps + numpy.arange(6720)
        rates = TimeFunction(_losses_from_five_minutes)(steps)
        assert rates.shape == (6720,)
        assert rates[2239:2241].tolist() == [0.0, 0.004]


def _income_of_a_mineral_a_step(time: GameTime) -> Resources:
    return Resources(float(time.steps), 0.0)


class TestValuesThatAreNoNumbers:
    """The owner, review of #261: a function gives a value of any kind."""

    def test_one_time_gives_what_the_function_gives(self):
        assert TimeFunction(_income_of_a_mineral_a_step)(GameTime(steps=7)) == Resources(7.0, 0.0)

    def test_many_times_give_an_array_of_objects_a_value_each(self):
        income = TimeFunction(_income_of_a_mineral_a_step)
        values = income(numpy.array([[0, 1], [2, 3]]))
        assert (values.dtype, values.shape) == (numpy.dtype(object), (2, 2))
        assert values[1, 0] == Resources(2.0, 0.0)
        assert income([GameTime(steps=4)]).tolist() == [Resources(4.0, 0.0)]
        assert income(numpy.arange(4)).sum() == Resources(6.0, 0.0)

    def test_a_value_that_is_a_sequence_stays_one_value(self):
        """A `Point` is a tuple, which numpy would read as a row of two numbers."""
        position = TimeFunction(lambda time: Point((time.steps, 2 * time.steps)))
        positions = position(numpy.array([[0, 1, 2], [3, 4, 5]]))
        assert (positions.dtype, positions.shape) == (numpy.dtype(object), (2, 3))
        assert positions[1, 2] == Point((5, 10))
        assert isinstance(positions[0, 0], Point)
        assert position(numpy.array(3)).shape == ()
        assert position([GameTime(steps=1), GameTime(steps=2)]).shape == (2,)

    def test_values_of_any_kind_are_held_as_objects(self):
        opening_until_five_minutes = TimeFunction(lambda time: "opening" if time < GameTime(seconds=300) else None)
        phases = opening_until_five_minutes(numpy.array([0, 6720]))
        assert (phases.dtype, phases.tolist()) == (numpy.dtype(object), ["opening", None])

    def test_whether_each_is_a_number_decides_and_not_the_first(self):
        mixed = TimeFunction(lambda time: 0.0 if time.steps else Resources(0, 0))
        assert mixed(numpy.arange(3)).dtype == object

    def test_numpy_booleans_stay_an_array_of_booleans(self):
        """A comparison of numpy's gives one, which Python does not count among its numbers; as objects they made no
        mask."""
        heights = numpy.arange(6.0)
        above_three = TimeFunction(lambda time: heights[int(time.steps)] > 3)(numpy.arange(6))
        assert above_three.dtype == bool
        assert heights[above_three].tolist() == [4.0, 5.0]

    def test_booleans_and_whole_numbers_stay_what_numpy_makes_of_them(self):
        assert TimeFunction(lambda time: time.steps > 1)(numpy.arange(3)).tolist() == [False, False, True]
        assert TimeFunction(lambda time: time.steps > 1)(numpy.arange(3)).dtype == bool
        assert TimeFunction(lambda time: int(time.steps))(numpy.arange(3)).dtype.kind == "i"

    def test_no_times_give_no_values(self):
        assert TimeFunction(_income_of_a_mineral_a_step)([]).shape == (0,)

    def test_is_a_time_dependent_of_its_values_to_the_type_checker(self):
        """Pyright checks this assignment."""
        income_function = TimeFunction(_income_of_a_mineral_a_step)
        income: TimeDependent[Resources] = income_function
        assert income(GameTime(steps=1), default=None) == Resources(1.0, 0.0)
        assert income_function(numpy.arange(2), default=Resources(0, 0)).shape == (2,)


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
