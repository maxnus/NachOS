import math

import numpy
import pytest

from sc2nachos import GameTime
from sc2nachos.gametime import STEPS_PER_SECOND, TimeSeries


@pytest.fixture
def ramp() -> TimeSeries[float]:
    """0.0 through 9.0, recorded at steps 0 through 9."""
    series = TimeSeries.empty(float)
    for step in range(10):
        series.append(GameTime(steps=step), float(step))
    return series


class TestEmpty:
    def test_has_no_values(self):
        series = TimeSeries.empty(float)
        assert len(series) == 0
        assert series.size == 0
        assert series(GameTime(steps=0), default=None) is None

    def test_honors_the_requested_buffer(self):
        series = TimeSeries.empty(float, initial_size=64)
        for step in range(64):
            series.append(GameTime(steps=step), float(step))
        assert series.size == 64

    def test_keeps_the_dtype(self):
        assert TimeSeries.empty(int).dtype == numpy.dtype(int)

    def test_has_no_start_until_the_first_append(self):
        series = TimeSeries.empty(float)
        with pytest.raises(ValueError, match="no start step"):
            _ = series.start.steps
        with pytest.raises(ValueError, match="no start step"):
            _ = series.end.steps

    def test_the_first_append_sets_the_start(self):
        series = TimeSeries.empty(float)
        series.append(GameTime(steps=500), 1.0)
        assert series.start.steps == 500
        assert series.end.steps == 500

    def test_the_constructor_requires_a_start(self):
        with pytest.raises(TypeError):
            TimeSeries(numpy.zeros(3))  # pyright: ignore[reportCallIssue]

    def test_indexing_an_empty_series_raises(self):
        with pytest.raises(IndexError, match="empty series"):
            TimeSeries.empty(float)[GameTime(steps=0)]

    def test_slicing_an_empty_series_gives_an_empty_series(self):
        """It raised `TypeError`, doing arithmetic on the missing start."""
        series = TimeSeries.empty(float)
        assert series[GameTime(steps=5) : GameTime(steps=10)].size == 0
        series[GameTime(steps=5) : GameTime(steps=10)] = 3.0
        assert series.size == 0

    def test_derivative_of_an_empty_series_is_none(self):
        assert TimeSeries.empty(float).derivative_at(GameTime(steps=0)) is None

    def test_combining_with_an_empty_series_gives_an_empty_series(self):
        series = TimeSeries.empty(float)
        series.append(GameTime(steps=3), 1.0)
        assert (series + TimeSeries.empty(float)).size == 0
        assert (TimeSeries.empty(float) + series).size == 0


class TestAppend:
    def test_single(self, ramp):
        assert ramp.start.steps == 0
        assert ramp.end.steps == 9
        assert ramp.last_value == 9.0

    def test_consecutive(self, ramp):
        assert [ramp[GameTime(steps=step)] for step in range(10)] == [float(step) for step in range(10)]

    def test_gap_is_interpolated(self):
        series = TimeSeries.empty(float)
        series.append(GameTime(steps=0), 0.0)
        series.append(GameTime(steps=4), 4.0)
        assert len(series) == 5
        assert series[GameTime(steps=2)] == pytest.approx(2.0)
        assert series[GameTime(steps=4)] == pytest.approx(4.0)

    def test_a_gap_in_an_integer_series_is_rounded(self):
        """It was truncated: 0 to 2 over 3 steps gave 0, 0, 1, 2."""
        series = TimeSeries.empty(int)
        series.append(GameTime(steps=0), 0)
        series.append(GameTime(steps=3), 2)
        assert series.values.tolist() == [0, 1, 1, 2]

    def test_a_tie_in_an_integer_gap_rounds_up(self):
        """As `GameTime.nearest_step` has it, where `numpy.rint` gave 0 to 1 over 2 steps as 0, 0, 1."""
        series = TimeSeries.empty(int)
        series.append(GameTime(steps=0), 0)
        series.append(GameTime(steps=2), 1)
        series.append(GameTime(steps=4), -1)
        assert series.values.tolist() == [0, 1, 1, 0, -1]

    def test_grows_a_sliced_series(self):
        source = TimeSeries.empty(float, initial_size=8)
        for step in range(8):
            source.append(GameTime(steps=step), float(step))
        series = source[GameTime(steps=2) : GameTime(steps=6)]
        for step in range(8, 40):
            series.append(GameTime(steps=step), 1.0)
        assert series.size == 38
        assert series[GameTime(steps=39)] == 1.0

    def test_grows_while_a_values_view_is_held(self):
        # ndarray.resize refuses while another object references the buffer.
        series = TimeSeries.empty(float, initial_size=4)
        for step in range(4):
            series.append(GameTime(steps=step), float(step))
        held = series.values
        for step in range(4, 20):
            series.append(GameTime(steps=step), 1.0)
        assert series.size == 20
        assert list(held) == [0.0, 1.0, 2.0, 3.0]

    def test_grows_the_buffer(self):
        series = TimeSeries.empty(float, initial_size=4)
        for step in range(10):
            series.append(GameTime(steps=step), float(step))
        assert series[GameTime(steps=9)] == 9.0

    def test_first_append_sets_the_start(self):
        series = TimeSeries.empty(float)
        series.append(GameTime(steps=100), 1.0)
        assert series.start.steps == 100
        assert series.end.steps == 100

    def test_out_of_order_raises(self, ramp):
        # An `assert` here would vanish under `python -O`, corrupting the series instead.
        with pytest.raises(ValueError, match="not after the last recorded step"):
            ramp.append(GameTime(steps=5), 0.0)

    def test_repeated_step_raises(self, ramp):
        with pytest.raises(ValueError, match="not after the last recorded step"):
            ramp.append(GameTime(steps=9), 0.0)

    def test_at_a_fraction_of_a_step_raises(self, ramp):
        with pytest.raises(ValueError, match="whole number of steps"):
            ramp.append(GameTime(steps=10.5), 0.0)

    def test_at_a_plain_step_raises(self, ramp):
        with pytest.raises(TypeError, match="time is a GameTime"):
            ramp.append(10, 0.0)  # pyright: ignore[reportArgumentType]


class TestIndexing:
    def test_getitem_is_by_time_not_position(self):
        series = TimeSeries.empty(float)
        series.append(GameTime(steps=1000), 7.0)
        series.append(GameTime(steps=1001), 8.0)
        assert series[GameTime(steps=1000)] == 7.0
        assert series[GameTime(steps=1001)] == 8.0

    def test_getitem_below_the_start_raises(self):
        series = TimeSeries.empty(float)
        series.append(GameTime(steps=1000), 7.0)
        # Without a bounds check this wraps to the last value, silently.
        with pytest.raises(IndexError):
            series[GameTime(steps=999)]

    def test_getitem_above_the_end_raises(self, ramp):
        with pytest.raises(IndexError):
            ramp[GameTime(steps=10)]

    def test_setitem(self, ramp):
        ramp[GameTime(steps=3)] = 30.0
        assert ramp[GameTime(steps=3)] == 30.0

    def test_slice_is_by_time(self, ramp):
        window = ramp[GameTime(steps=2) : GameTime(steps=5)]
        assert window.start.steps == 2
        assert window.size == 3
        assert list(window.values) == [2.0, 3.0, 4.0]

    def test_open_slice_takes_the_whole_series(self, ramp):
        window = ramp[:]
        assert window.start.steps == 0
        assert window.size == 10

    def test_slice_past_the_end_clamps(self, ramp):
        window = ramp[GameTime(steps=8) : GameTime(steps=100)]
        assert window.size == 2

    def test_slice_before_the_start_raises(self):
        series = TimeSeries.empty(float)
        series.append(GameTime(steps=1000), 7.0)
        with pytest.raises(IndexError):
            series[GameTime(steps=900) : GameTime(steps=1001)]

    def test_strided_slice_raises(self, ramp):
        with pytest.raises(NotImplementedError):
            ramp[GameTime(steps=0) : GameTime(steps=10) : 2]

    def test_a_slice_is_a_copy(self, ramp):
        """A slice shared its source's memory, so writing to one changed the other."""
        window = ramp[GameTime(steps=2) : GameTime(steps=4)]
        window[GameTime(steps=3)] = 99.0
        assert ramp[GameTime(steps=3)] == 3.0

    def test_setitem_slice(self, ramp):
        ramp[GameTime(steps=2) : GameTime(steps=5)] = 0.0
        assert list(ramp.values[2:5]) == [0.0, 0.0, 0.0]

    def test_iter_yields_time_and_value(self):
        series = TimeSeries.empty(float)
        series.append(GameTime(steps=50), 1.0)
        series.append(GameTime(steps=51), 2.0)
        assert list(series) == [(GameTime(steps=50), 1.0), (GameTime(steps=51), 2.0)]

    def test_a_time_between_two_steps_indexes_the_step_before(self, ramp):
        """It read the nearest step, so 3.5 and 3.9 read step 4, which the game had not reached."""
        assert ramp[GameTime(steps=3.4)] == 3.0
        assert ramp[GameTime(steps=3.5)] == 3.0
        assert ramp[GameTime(steps=3.9)] == 3.0
        assert ramp[GameTime(seconds=4 / 22.4)] == 4.0

    def test_a_time_between_two_steps_writes_the_step_before(self, ramp):
        ramp[GameTime(steps=3.9)] = 30.0
        assert ramp.values.tolist()[3:5] == [30.0, 4.0]

    def test_a_slice_between_steps_starts_and_ends_at_the_step_before(self, ramp):
        """A fifth of a second is 4.48 steps, so it starts at 4.52, read as step 4, and ends at 8.9, read as 8."""
        window = ramp[ramp.end - GameTime(seconds=0.2) : GameTime(steps=8.9)]
        assert (window.start, window.values.tolist()) == (GameTime(steps=4), [4.0, 5.0, 6.0, 7.0])

    def test_a_slice_from_start_to_end_leaves_out_the_last_value(self, ramp):
        assert ramp[ramp.start : ramp.end].values.tolist() == [float(step) for step in range(9)]
        assert ramp(ramp.end) == ramp.last_value

    def test_a_plain_index_raises(self, ramp):
        with pytest.raises(TypeError, match="an index is a GameTime"):
            ramp[3]  # pyright: ignore[reportCallIssue, reportArgumentType]
        with pytest.raises(TypeError, match="an index is a GameTime"):
            ramp[3] = 0.0  # pyright: ignore[reportArgumentType]

    def test_a_slice_of_plain_steps_raises(self, ramp):
        with pytest.raises(TypeError, match="a slice's start is a GameTime"):
            ramp[2:]
        with pytest.raises(TypeError, match="a slice's stop is a GameTime"):
            ramp[:5]


class TestValue:
    def test_last_value(self, ramp):
        assert ramp.last_value == 9.0

    def test_last_value_of_an_empty_series_raises(self):
        with pytest.raises(ValueError, match="no last value"):
            _ = TimeSeries.empty(float).last_value

    def test_an_infinite_time_raises(self, ramp):
        with pytest.raises(ValueError, match="not finite"):
            ramp[GameTime.INFINITE]
        with pytest.raises(ValueError, match="not finite"):
            ramp[ramp.start : GameTime.INFINITE]


class TestCall:
    def test_reads_the_value_at_a_time(self, ramp):
        assert ramp(GameTime(steps=3)) == 3.0
        assert ramp(GameTime(steps=3)) == ramp[GameTime(steps=3)]

    def test_a_step_never_recorded_raises(self, ramp):
        with pytest.raises(IndexError, match="step -1 outside the recorded range 0-9"):
            ramp(GameTime(steps=-1))
        with pytest.raises(IndexError, match="step 10 outside the recorded range 0-9"):
            ramp(GameTime(steps=10))
        with pytest.raises(IndexError, match="empty series"):
            TimeSeries.empty(float)(GameTime(steps=0))

    def test_a_step_never_recorded_reads_the_default(self, ramp):
        assert ramp(GameTime(steps=-1), default=None) is None
        assert ramp(GameTime(seconds=100.0), default=0.5) == 0.5
        assert ramp(GameTime(steps=3), default=None) == 3.0
        assert TimeSeries.empty(float)(GameTime(steps=0), default=7.0) == 7.0

    def test_a_time_between_two_steps_reads_the_step_before(self, ramp):
        """A second back from step 1000 is step 977.6, whose value is step 977's: the game reaches 978 later."""
        assert ramp(GameTime(steps=3.5)) == 3.0
        assert ramp(GameTime(steps=3.999)) == 3.0
        assert ramp(GameTime(seconds=4 / STEPS_PER_SECOND)) == 4.0
        assert ramp(GameTime(steps=9.9)) == 9.0
        assert ramp(GameTime(steps=-0.5), default=None) is None

    def test_reads_many_times_as_an_array(self, ramp):
        values = ramp([GameTime(steps=2), GameTime(steps=3.7), GameTime(steps=9)])
        assert isinstance(values, numpy.ndarray)
        assert values.tolist() == [2.0, 3.0, 9.0]

    def test_reads_an_array_of_steps_in_its_shape(self, ramp):
        values = ramp(numpy.array([[0, 1, 2], [7.5, 8.5, 9.5]]))
        assert values.tolist() == [[0.0, 1.0, 2.0], [7.0, 8.0, 9.0]]

    def test_many_times_of_an_integer_series_stay_integers(self):
        series = TimeSeries(numpy.arange(5), GameTime(steps=10), 5)
        assert series(numpy.arange(10, 15)).dtype == series.dtype
        assert series(numpy.arange(8, 13), default=-1).tolist() == [-1, -1, 0, 1, 2]
        assert series(numpy.arange(8, 13), default=-1).dtype == series.dtype

    def test_many_times_are_a_copy(self, ramp):
        values = ramp(numpy.arange(3))
        values[:] = 99.0
        assert ramp.values.tolist()[:3] == [0.0, 1.0, 2.0]

    def test_many_times_with_one_never_recorded_raise(self, ramp):
        with pytest.raises(IndexError, match="step 12 outside the recorded range 0-9"):
            ramp(numpy.array([3, 12, 40]))
        with pytest.raises(IndexError, match="empty series"):
            TimeSeries.empty(float)(numpy.array([3]))

    def test_many_times_read_the_default_where_never_recorded(self, ramp):
        assert ramp(numpy.array([-1, 0, 9, 10]), default=0.5).tolist() == [0.5, 0.0, 9.0, 0.5]
        gaps_marked = ramp(numpy.array([-1, 4]), default=math.nan)
        assert math.isnan(gaps_marked[0])
        assert gaps_marked[1] == 4.0
        assert TimeSeries.empty(float)(numpy.array([3, 4]), default=1.0).tolist() == [1.0, 1.0]

    def test_many_times_with_a_step_past_any_integer_name_it(self, ramp):
        """It was cast to an integer, which numpy warned of, and the error named the smallest integer."""
        with pytest.raises(IndexError, match=f"step {math.floor(1e300)} outside the recorded range 0-9"):
            ramp(numpy.array([3.0, 1e300]))
        assert ramp(numpy.array([-1e300, 3.0, 1e300]), default=-1.0).tolist() == [-1.0, 3.0, -1.0]

    def test_many_times_ignore_the_unwritten_buffer(self):
        series = TimeSeries.empty(float, initial_size=16)
        series.append(GameTime(steps=0), 1.0)
        assert series(numpy.array([0, 1, 2]), default=-1.0).tolist() == [1.0, -1.0, -1.0]

    def test_no_times_give_an_empty_array(self, ramp):
        assert ramp([]).shape == (0,)

    def test_a_time_not_finite_raises(self, ramp):
        with pytest.raises(ValueError, match="not finite"):
            ramp(GameTime.INFINITE)
        with pytest.raises(ValueError, match="not finite"):
            ramp(GameTime.INFINITE, default=0.0)
        with pytest.raises(ValueError, match="not finite"):
            ramp(numpy.array([1.0, math.inf]), default=0.0)

    def test_plain_seconds_raise(self, ramp):
        with pytest.raises(TypeError, match="a time is a GameTime"):
            ramp(4.0)  # pyright: ignore[reportCallIssue, reportArgumentType]


class TestStatistics:
    def test_aggregates(self, ramp):
        assert ramp.min() == 0.0
        assert ramp.max() == 9.0
        assert ramp.sum() == 45.0
        assert ramp.mean() == 4.5
        assert ramp.std() == pytest.approx(numpy.std(numpy.arange(10.0)))

    def test_ignores_the_unwritten_buffer(self):
        series = TimeSeries.empty(float, initial_size=1024)
        series.append(GameTime(steps=0), 5.0)
        assert series.max() == 5.0
        assert series.mean() == 5.0


class TestDerivativeAt:
    def test_constant_series_is_flat(self):
        series = TimeSeries.empty(float)
        for step in range(200):
            series.append(GameTime(steps=step), 1.0)
        assert series.derivative_at(GameTime(steps=199)) == pytest.approx(0.0)

    def test_linear_series_rises_by_a_step_per_step(self):
        series = TimeSeries.empty(float)
        for step in range(200):
            series.append(GameTime(steps=step), float(step))
        assert series.derivative_at(GameTime(steps=199), window=GameTime(steps=100)) == pytest.approx(STEPS_PER_SECOND)

    def test_window_does_not_change_the_rate(self):
        series = TimeSeries.empty(float)
        for step in range(200):
            series.append(GameTime(steps=step), float(step))
        assert series.derivative_at(GameTime(steps=199), window=GameTime(steps=10)) == pytest.approx(
            series.derivative_at(GameTime(steps=199), window=GameTime(steps=150))
        )

    def test_insufficient_history_is_none(self, ramp):
        assert ramp.derivative_at(GameTime(steps=9), window=GameTime(steps=100)) is None

    @pytest.mark.parametrize("window", [0, -10])
    def test_a_window_of_no_steps_raises(self, ramp, window):
        """A negative window read the unrecorded buffer and answered -11.2; no window divided by zero."""
        with pytest.raises(ValueError, match="at least one step"):
            ramp.derivative_at(GameTime(steps=5), window=GameTime(steps=window))

    def test_a_window_of_seconds_is_rounded_to_the_nearest_step(self):
        """A second is 22.4 steps, read over 22: the square of the step rises by 2 * 100 - 22 a step over them."""
        series = TimeSeries.empty(float)
        for step in range(200):
            series.append(GameTime(steps=step), float(step**2))
        rate = series.derivative_at(GameTime(steps=100), window=GameTime(seconds=1))
        assert rate == pytest.approx((2 * 100 - 22) * STEPS_PER_SECOND)

    def test_a_window_halfway_between_two_steps_is_the_longer(self):
        series = TimeSeries.empty(float)
        for step in range(200):
            series.append(GameTime(steps=step), float(step**2))
        assert series.derivative_at(GameTime(steps=100), window=GameTime(steps=22.5)) == pytest.approx(
            (2 * 100 - 23) * STEPS_PER_SECOND
        )

    def test_a_window_in_plain_steps_raises(self, ramp):
        with pytest.raises(TypeError, match="window is a GameTime"):
            ramp.derivative_at(GameTime(steps=5), window=5)  # pyright: ignore[reportArgumentType]

    def test_a_plain_step_raises(self, ramp):
        with pytest.raises(TypeError, match="time is a GameTime"):
            ramp.derivative_at(5)  # pyright: ignore[reportArgumentType]

    def test_a_time_between_two_steps_ends_at_the_step_before(self):
        series = TimeSeries.empty(float)
        for step in range(20):
            series.append(GameTime(steps=step), float(step**2))
        assert series.derivative_at(GameTime(steps=10.9), window=GameTime(steps=5)) == series.derivative_at(
            GameTime(steps=10), window=GameTime(steps=5)
        )

    def test_measures_before_the_last_step(self, ramp):
        assert ramp.derivative_at(GameTime(steps=5), window=GameTime(steps=5)) == pytest.approx(STEPS_PER_SECOND)

    def test_handles_a_negative_start_step(self):
        series = TimeSeries.empty(float)
        for step in range(-10, 10):
            series.append(GameTime(steps=step), float(step))
        assert series.derivative_at(GameTime(steps=0), window=GameTime(steps=5)) == pytest.approx(STEPS_PER_SECOND)

    def test_smoothing_is_done_by_composing_with_a_filter(self):
        series = TimeSeries.empty(float)
        for step in range(200):
            series.append(GameTime(steps=step), float(step))
        assert series.gaussian_filter(sigma=GameTime(steps=2.0)).derivative_at(
            GameTime(steps=150), window=GameTime(steps=100)
        ) == pytest.approx(STEPS_PER_SECOND)


class TestFilters:
    def test_gaussian_filter_keeps_the_geometry(self, ramp):
        filtered = ramp.gaussian_filter(sigma=GameTime(steps=1.0))
        assert filtered.start.steps == ramp.start.steps
        assert filtered.size == ramp.size

    def test_gaussian_filter_with_a_plain_sigma_raises(self, ramp):
        with pytest.raises(TypeError, match="sigma is a GameTime"):
            ramp.gaussian_filter(sigma=1.0)  # pyright: ignore[reportArgumentType]

    def test_uniform_filter_keeps_the_geometry(self, ramp):
        filtered = ramp.uniform_filter(size=GameTime(steps=3))
        assert filtered.start.steps == ramp.start.steps
        assert filtered.size == ramp.size

    @pytest.mark.parametrize("size", [GameTime(steps=0), GameTime(steps=2.5), GameTime(seconds=1)])
    def test_uniform_filter_over_anything_but_a_whole_number_of_steps_raises(self, ramp, size):
        with pytest.raises(ValueError, match="whole number of steps"):
            ramp.uniform_filter(size=size)

    def test_uniform_filter_over_a_plain_number_raises(self, ramp):
        with pytest.raises(TypeError, match="GameTime"):
            ramp.uniform_filter(size=3)  # pyright: ignore[reportArgumentType]


class TestCopyAndAdd:
    def test_copy_is_independent(self, ramp):
        duplicate = ramp.copy()
        duplicate[GameTime(steps=0)] = 99.0
        assert ramp[GameTime(steps=0)] == 0.0

    def test_add_overlapping(self, ramp):
        combined = ramp + ramp
        assert combined.start.steps == 0
        assert combined.size == 10
        assert combined[GameTime(steps=5)] == 10.0

    def test_combining_covers_only_the_steps_both_record(self):
        first = TimeSeries.empty(float)
        for step in range(5):
            first.append(GameTime(steps=step), 1.0)
        second = TimeSeries.empty(float)
        for step in range(3, 8):
            second.append(GameTime(steps=step), 10.0)
        combined = first + second
        assert combined.start.steps == 3
        assert combined.end.steps == 4
        assert list(combined.values) == [11.0, 11.0]

    def test_combining_disjoint_series_gives_an_empty_series(self):
        first = TimeSeries.empty(float)
        for step in range(5):
            first.append(GameTime(steps=step), 1.0)
        second = TimeSeries.empty(float)
        for step in range(20, 25):
            second.append(GameTime(steps=step), 1.0)
        assert (first * second).size == 0

    def test_subtracting_a_series_is_reversible(self, ramp):
        other = TimeSeries.empty(float)
        for step in range(3, 20):
            other.append(GameTime(steps=step), 2.0 * step)
        assert list(((ramp - other) + other).values) == list(ramp[GameTime(steps=3) : GameTime(steps=10)].values)

    def test_multiply_series(self, ramp):
        squared = ramp * ramp
        assert squared[GameTime(steps=4)] == 16.0

    def test_scalar_arithmetic(self, ramp):
        assert (ramp + 1)[GameTime(steps=4)] == 5.0
        assert (ramp - 1)[GameTime(steps=4)] == 3.0
        assert (ramp * 2)[GameTime(steps=4)] == 8.0

    def test_scalar_arithmetic_keeps_the_step_range(self, ramp):
        scaled = ramp * 2
        assert scaled.start.steps == ramp.start.steps
        assert scaled.end.steps == ramp.end.steps

    def test_scalar_arithmetic_reflects(self, ramp):
        assert (1 + ramp)[GameTime(steps=4)] == 5.0
        assert (2 * ramp)[GameTime(steps=4)] == 8.0
        assert (10 - ramp)[GameTime(steps=4)] == 6.0

    @pytest.mark.parametrize("dtype", ["float64", "float32", "int64"])
    def test_scalar_arithmetic_takes_numpy_scalars_either_side(self, ramp, dtype):
        """A value read out of an array, such as `series.values.max()`, is a numpy scalar."""
        scalar = getattr(numpy, dtype)(2)
        for result in (ramp * scalar, scalar * ramp, ramp + scalar, scalar + ramp, ramp - scalar, scalar - ramp):
            assert isinstance(result, TimeSeries)
            assert (result.start.steps, result.end.steps) == (0, 9)
        assert (scalar * ramp)[GameTime(steps=4)] == 8.0
        assert (scalar - ramp)[GameTime(steps=4)] == -2.0

    def test_scalar_arithmetic_on_an_empty_series(self):
        assert (TimeSeries.empty(float) * 2).size == 0

    def test_an_integer_series_stays_integral(self):
        series = TimeSeries.empty(int)
        for step in range(5):
            series.append(GameTime(steps=step), step)
        assert (series * 2).dtype == numpy.dtype(int)
        assert isinstance((series * 2)[GameTime(steps=3)], int)

    def test_combining_with_an_unsupported_type_is_not_implemented(self, ramp):
        with pytest.raises(TypeError):
            _ = ramp + "x"  # pyright: ignore[reportOperatorIssue]


class TestConstruction:
    def test_wraps_an_existing_array(self):
        series = TimeSeries(numpy.array([1.0, 2.0, 3.0]), GameTime(steps=10), 3)
        assert series.start.steps == 10
        assert series.end.steps == 12
        assert series[GameTime(steps=11)] == 2.0

    def test_buffer_may_be_longer_than_the_recorded_size(self):
        series = TimeSeries(numpy.arange(10.0), GameTime(steps=0), 3)
        assert series.size == 3
        assert series.end.steps == 2
        assert list(series.values) == [0.0, 1.0, 2.0]

    def test_size_beyond_the_buffer_raises(self):
        with pytest.raises(ValueError, match="too small"):
            TimeSeries(numpy.zeros(4), GameTime(steps=0), 5)

    def test_negative_size_raises(self):
        with pytest.raises(ValueError, match="negative"):
            TimeSeries(numpy.zeros(4), GameTime(steps=0), -1)

    def test_a_non_empty_series_needs_a_start(self):
        with pytest.raises(ValueError, match="needs a start time"):
            TimeSeries(numpy.zeros(4), None, 3)

    def test_an_empty_series_drops_a_pointless_start(self):
        assert TimeSeries(numpy.zeros(4), GameTime(steps=99), 0)._start is None

    def test_a_start_at_a_fraction_of_a_step_raises(self):
        with pytest.raises(ValueError, match="start is a whole number of steps"):
            TimeSeries(numpy.zeros(4), GameTime(steps=0.5), 3)

    def test_a_plain_start_raises(self):
        with pytest.raises(TypeError, match="start is a GameTime"):
            TimeSeries(numpy.zeros(4), 0, 3)  # pyright: ignore[reportArgumentType]

    def test_an_empty_buffer_is_rejected(self):
        # It would make a series with no room to append, so nothing could ever be recorded.
        with pytest.raises(ValueError, match="must not be empty"):
            TimeSeries(numpy.zeros(0), None, 0)

    def test_operations_that_yield_nothing_still_give_an_appendable_series(self, ramp):
        for series in (ramp[GameTime(steps=5) : GameTime(steps=5)], TimeSeries.empty(float).copy()):
            assert series.size == 0
            series.append(GameTime(steps=7), 1.0)
            assert series.start.steps == 7
            assert series.last_value == 1.0

    def test_repr_names_the_step_range(self, ramp):
        assert "0-9" in repr(ramp)
        assert "empty" in repr(TimeSeries.empty(float))
