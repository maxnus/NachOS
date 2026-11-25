"""What a `TimeSeries`, a `TimeCurve` and a `TimeFunction` share as a `TimeDependent`, each probed the same way."""

from collections.abc import Callable

import numpy
import pytest

from sc2nachos import GameTime
from sc2nachos.gametime import TimeCurve, TimeDependent, TimeFunction, TimeSeries


def _series_of_its_step() -> TimeSeries[float]:
    return TimeSeries(numpy.arange(10.0), GameTime(steps=0), 10)


def _curve_of_its_step() -> TimeCurve[float]:
    return TimeCurve([(GameTime(steps=0), 0.0), (GameTime(steps=9), 9.0)])


def _function_of_its_step() -> TimeFunction[float]:
    return TimeFunction(lambda time: float(time.steps))


@pytest.fixture(
    params=[_series_of_its_step, _curve_of_its_step, _function_of_its_step], ids=["series", "curve", "function"]
)
def its_step(request: pytest.FixtureRequest) -> TimeDependent[float]:
    """A time-dependent value of each kind that reads, at each whole step from 0 to 9, that step."""
    return request.param()


def _value_read_as_a_function(function_of_time: Callable[[GameTime], float], time: GameTime) -> float:
    """What `function_of_time` gives for `time`, taken as a consumer that reads a value at a time takes it."""
    return function_of_time(time)


class TestOneTime:
    def test_reads_the_value_at_a_time(self, its_step):
        assert [its_step(GameTime(steps=step)) for step in range(10)] == [float(step) for step in range(10)]

    def test_a_default_changes_no_read_it_covers(self, its_step):
        assert its_step(GameTime(steps=4), default=None) == 4.0
        assert its_step(GameTime(steps=4), default=-1.0) == 4.0

    def test_passes_as_a_function_of_the_game_time(self, its_step):
        assert _value_read_as_a_function(its_step, GameTime(steps=6)) == 6.0
        assert _value_read_as_a_function(lambda time: float(time.steps), GameTime(steps=6)) == 6.0

    def test_plain_steps_raise(self, its_step):
        with pytest.raises(TypeError, match="a time is a GameTime"):
            its_step(4)


class TestManyTimes:
    def test_a_sequence_reads_as_each_of_its_times_does(self, its_step):
        times = [GameTime(steps=step) for step in (9, 0, 4, 4)]
        values = its_step(times)
        assert isinstance(values, numpy.ndarray)
        assert values.tolist() == [its_step(time) for time in times] == [9.0, 0.0, 4.0, 4.0]

    def test_an_array_of_steps_reads_as_a_sequence_of_those_times_does(self, its_step):
        steps = numpy.array([9, 0, 4, 4])
        assert its_step(steps).tolist() == its_step([GameTime(steps=step) for step in steps.tolist()]).tolist()
        assert its_step(steps.astype(float)).tolist() == [9.0, 0.0, 4.0, 4.0]

    def test_an_array_keeps_its_shape(self, its_step):
        values = its_step(numpy.array([[0, 1, 2], [7, 8, 9]]))
        assert values.tolist() == [[0.0, 1.0, 2.0], [7.0, 8.0, 9.0]]

    def test_an_array_with_no_dimension_gives_one_too(self, its_step):
        """A series and a curve gave a numpy number there, and a function an array."""
        value = its_step(numpy.array(4))
        assert isinstance(value, numpy.ndarray)
        assert (value.shape, value.item()) == ((), 4.0)

    def test_no_times_give_no_values(self, its_step):
        assert its_step([]).shape == (0,)
        assert its_step(numpy.zeros((0, 3))).shape == (0, 3)

    def test_a_default_changes_no_read_it_covers(self, its_step):
        assert its_step(numpy.arange(10), default=-1.0).tolist() == [float(step) for step in range(10)]

    @pytest.mark.parametrize("default", [None, "none", [0.0]])
    def test_a_default_that_is_no_number_raises_for_a_series(self, default):
        """An array of numbers holds no `None`, whether or not the default would be read. A curve and a function,
        whose values may be of any kind, never read one, so they take any."""
        with pytest.raises(TypeError, match="the default of many times is a number"):
            _series_of_its_step()(numpy.arange(3), default=default)
        assert _curve_of_its_step()(numpy.arange(3), default=default).tolist() == [0.0, 1.0, 2.0]
        assert _function_of_its_step()(numpy.arange(3), default=default).tolist() == [0.0, 1.0, 2.0]

    def test_a_default_of_a_numpy_number_is_taken(self, its_step):
        assert its_step(numpy.arange(3), default=numpy.float32(0.5)).tolist() == [0.0, 1.0, 2.0]

    def test_a_sequence_of_plain_steps_raises(self, its_step):
        with pytest.raises(TypeError, match="each of many times is a GameTime"):
            its_step([1, 2, 3])

    @pytest.mark.parametrize("times", [numpy.array([True, False]), numpy.array(["1", "2"]), numpy.array([None])])
    def test_an_array_of_anything_but_numbers_raises(self, its_step, times):
        with pytest.raises(TypeError, match="an array of times holds steps"):
            its_step(times)


def test_each_kind_is_a_time_dependent_to_the_type_checker():
    """Pyright checks these assignments; a series of integers passes where floats are asked for."""
    time_dependents: list[TimeDependent[float]] = [
        _series_of_its_step(),
        _curve_of_its_step(),
        _function_of_its_step(),
        TimeSeries.empty(int),
    ]
    assert len(time_dependents) == 4
