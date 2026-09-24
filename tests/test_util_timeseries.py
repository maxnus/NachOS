import numpy
import pytest

from sc2nachos.constants import STEPS_PER_SECOND
from sc2nachos.util import TimeSeries


@pytest.fixture
def ramp() -> TimeSeries[float]:
    """0.0 through 9.0, recorded at steps 0 through 9."""
    series = TimeSeries.empty(float)
    for step in range(10):
        series.append(step, float(step))
    return series


class TestEmpty:
    def test_has_no_values(self):
        series = TimeSeries.empty(float)
        assert len(series) == 0
        assert series.size == 0
        assert series.at(0) is None

    def test_honors_the_requested_buffer(self):
        series = TimeSeries.empty(float, initial_size=64)
        for step in range(64):
            series.append(step, float(step))
        assert series.size == 64

    def test_keeps_the_dtype(self):
        assert TimeSeries.empty(int).dtype == numpy.dtype(int)

    def test_has_no_start_until_the_first_append(self):
        series = TimeSeries.empty(float)
        with pytest.raises(ValueError, match="no start step"):
            _ = series.start
        with pytest.raises(ValueError, match="no start step"):
            _ = series.last_step

    def test_the_first_append_sets_the_start(self):
        series = TimeSeries.empty(float)
        series.append(500, 1.0)
        assert series.start == 500
        assert series.last_step == 500

    def test_the_constructor_requires_a_start(self):
        with pytest.raises(TypeError):
            TimeSeries(numpy.zeros(3))  # pyright: ignore[reportCallIssue]

    def test_indexing_an_empty_series_raises(self):
        with pytest.raises(IndexError, match="empty series"):
            TimeSeries.empty(float)[0]

    def test_derivative_of_an_empty_series_is_none(self):
        assert TimeSeries.empty(float).derivative_at(0) is None

    def test_combining_with_an_empty_series_gives_an_empty_series(self):
        series = TimeSeries.empty(float)
        series.append(3, 1.0)
        assert (series + TimeSeries.empty(float)).size == 0
        assert (TimeSeries.empty(float) + series).size == 0


class TestAppend:
    def test_single(self, ramp):
        assert ramp.start == 0
        assert ramp.last_step == 9
        assert ramp.last_value == 9.0

    def test_consecutive(self, ramp):
        assert [ramp[step] for step in range(10)] == [float(step) for step in range(10)]

    def test_gap_is_interpolated(self):
        series = TimeSeries.empty(float)
        series.append(0, 0.0)
        series.append(4, 4.0)
        assert len(series) == 5
        assert series[2] == pytest.approx(2.0)
        assert series[4] == pytest.approx(4.0)

    def test_grows_a_sliced_series(self):
        # A slice's buffer is a view, which ndarray.resize refuses to grow.
        source = TimeSeries.empty(float, initial_size=8)
        for step in range(8):
            source.append(step, float(step))
        series = source[2:6]
        for step in range(8, 40):
            series.append(step, 1.0)
        assert series.size == 38
        assert series[39] == 1.0

    def test_grows_while_a_values_view_is_held(self):
        # ndarray.resize refuses while another object references the buffer.
        series = TimeSeries.empty(float, initial_size=4)
        for step in range(4):
            series.append(step, float(step))
        held = series.values
        for step in range(4, 20):
            series.append(step, 1.0)
        assert series.size == 20
        assert list(held) == [0.0, 1.0, 2.0, 3.0]

    def test_grows_the_buffer(self):
        series = TimeSeries.empty(float, initial_size=4)
        for step in range(10):
            series.append(step, float(step))
        assert series[9] == 9.0

    def test_first_append_sets_the_start(self):
        series = TimeSeries.empty(float)
        series.append(100, 1.0)
        assert series.start == 100
        assert series.last_step == 100

    def test_out_of_order_raises(self, ramp):
        # An `assert` here would vanish under `python -O`, corrupting the series instead.
        with pytest.raises(ValueError, match="not after the last recorded step"):
            ramp.append(5, 0.0)

    def test_repeated_step_raises(self, ramp):
        with pytest.raises(ValueError, match="not after the last recorded step"):
            ramp.append(9, 0.0)


class TestIndexing:
    def test_getitem_is_by_step_not_position(self):
        series = TimeSeries.empty(float)
        series.append(1000, 7.0)
        series.append(1001, 8.0)
        assert series[1000] == 7.0
        assert series[1001] == 8.0

    def test_getitem_below_the_start_raises(self):
        series = TimeSeries.empty(float)
        series.append(1000, 7.0)
        # Without a bounds check this wraps to the last value, silently.
        with pytest.raises(IndexError):
            series[999]

    def test_getitem_above_the_end_raises(self, ramp):
        with pytest.raises(IndexError):
            ramp[10]

    def test_setitem(self, ramp):
        ramp[3] = 30.0
        assert ramp[3] == 30.0

    def test_slice_is_by_step(self, ramp):
        window = ramp[2:5]
        assert window.start == 2
        assert window.size == 3
        assert list(window.values) == [2.0, 3.0, 4.0]

    def test_open_slice_takes_the_whole_series(self, ramp):
        window = ramp[:]
        assert window.start == 0
        assert window.size == 10

    def test_slice_past_the_end_clamps(self, ramp):
        window = ramp[8:100]
        assert window.size == 2

    def test_slice_before_the_start_raises(self):
        series = TimeSeries.empty(float)
        series.append(1000, 7.0)
        with pytest.raises(IndexError):
            series[900:1001]

    def test_strided_slice_raises(self, ramp):
        with pytest.raises(NotImplementedError):
            ramp[0:10:2]

    def test_setitem_slice(self, ramp):
        ramp[2:5] = 0.0
        assert list(ramp.values[2:5]) == [0.0, 0.0, 0.0]

    def test_iter_yields_step_and_value(self):
        series = TimeSeries.empty(float)
        series.append(50, 1.0)
        series.append(51, 2.0)
        assert list(series) == [(50, 1.0), (51, 2.0)]


class TestValue:
    def test_last_value(self, ramp):
        assert ramp.last_value == 9.0

    def test_last_value_of_an_empty_series_raises(self):
        with pytest.raises(ValueError, match="no last value"):
            _ = TimeSeries.empty(float).last_value

    def test_at_a_given_step(self, ramp):
        assert ramp.at(3) == 3.0

    def test_outside_the_range_is_none(self, ramp):
        assert ramp.at(-1) is None
        assert ramp.at(10) is None

    def test_at_time_converts_seconds_to_steps(self, ramp):
        assert ramp.at_time(4 / STEPS_PER_SECOND) == 4.0

    def test_at_time_outside_the_range_is_none(self, ramp):
        assert ramp.at_time(100.0) is None


class TestStatistics:
    def test_aggregates(self, ramp):
        assert ramp.min() == 0.0
        assert ramp.max() == 9.0
        assert ramp.sum() == 45.0
        assert ramp.mean() == 4.5
        assert ramp.std() == pytest.approx(numpy.std(numpy.arange(10.0)))

    def test_ignores_the_unwritten_buffer(self):
        series = TimeSeries.empty(float, initial_size=1024)
        series.append(0, 5.0)
        assert series.max() == 5.0
        assert series.mean() == 5.0


class TestDerivativeAt:
    def test_constant_series_is_flat(self):
        series = TimeSeries.empty(float)
        for step in range(200):
            series.append(step, 1.0)
        assert series.derivative_at(199) == pytest.approx(0.0)

    def test_linear_series_rises_by_a_step_per_step(self):
        series = TimeSeries.empty(float)
        for step in range(200):
            series.append(step, float(step))
        assert series.derivative_at(199, window=100) == pytest.approx(STEPS_PER_SECOND)

    def test_window_does_not_change_the_rate(self):
        series = TimeSeries.empty(float)
        for step in range(200):
            series.append(step, float(step))
        assert series.derivative_at(199, window=10) == pytest.approx(series.derivative_at(199, window=150))

    def test_insufficient_history_is_none(self, ramp):
        assert ramp.derivative_at(9, window=100) is None

    def test_measures_before_the_last_step(self, ramp):
        assert ramp.derivative_at(5, window=5) == pytest.approx(STEPS_PER_SECOND)

    def test_handles_a_negative_start_step(self):
        series = TimeSeries.empty(float)
        for step in range(-10, 10):
            series.append(step, float(step))
        assert series.derivative_at(0, window=5) == pytest.approx(STEPS_PER_SECOND)

    def test_smoothing_is_done_by_composing_with_a_filter(self):
        series = TimeSeries.empty(float)
        for step in range(200):
            series.append(step, float(step))
        assert series.gaussian_filter(sigma=2.0).derivative_at(150, window=100) == pytest.approx(STEPS_PER_SECOND)


class TestFilters:
    def test_gaussian_filter_keeps_the_geometry(self, ramp):
        filtered = ramp.gaussian_filter(sigma=1.0)
        assert filtered.start == ramp.start
        assert filtered.size == ramp.size

    def test_uniform_filter_keeps_the_geometry(self, ramp):
        filtered = ramp.uniform_filter(size=3)
        assert filtered.start == ramp.start
        assert filtered.size == ramp.size


class TestCopyAndAdd:
    def test_copy_is_independent(self, ramp):
        duplicate = ramp.copy()
        duplicate[0] = 99.0
        assert ramp[0] == 0.0

    def test_add_overlapping(self, ramp):
        combined = ramp + ramp
        assert combined.start == 0
        assert combined.size == 10
        assert combined[5] == 10.0

    def test_combining_covers_only_the_steps_both_record(self):
        first = TimeSeries.empty(float)
        for step in range(5):
            first.append(step, 1.0)
        second = TimeSeries.empty(float)
        for step in range(3, 8):
            second.append(step, 10.0)
        combined = first + second
        assert combined.start == 3
        assert combined.last_step == 4
        assert list(combined.values) == [11.0, 11.0]

    def test_combining_disjoint_series_gives_an_empty_series(self):
        first = TimeSeries.empty(float)
        for step in range(5):
            first.append(step, 1.0)
        second = TimeSeries.empty(float)
        for step in range(20, 25):
            second.append(step, 1.0)
        assert (first * second).size == 0

    def test_subtracting_a_series_is_reversible(self, ramp):
        other = TimeSeries.empty(float)
        for step in range(3, 20):
            other.append(step, 2.0 * step)
        assert list(((ramp - other) + other).values) == list(ramp[3:10].values)

    def test_multiply_series(self, ramp):
        squared = ramp * ramp
        assert squared[4] == 16.0

    def test_scalar_arithmetic(self, ramp):
        assert (ramp + 1)[4] == 5.0
        assert (ramp - 1)[4] == 3.0
        assert (ramp * 2)[4] == 8.0

    def test_scalar_arithmetic_keeps_the_step_range(self, ramp):
        scaled = ramp * 2
        assert scaled.start == ramp.start
        assert scaled.last_step == ramp.last_step

    def test_scalar_arithmetic_reflects(self, ramp):
        assert (1 + ramp)[4] == 5.0
        assert (2 * ramp)[4] == 8.0
        assert (10 - ramp)[4] == 6.0

    @pytest.mark.parametrize("dtype", ["float64", "float32", "int64"])
    def test_scalar_arithmetic_takes_numpy_scalars_either_side(self, ramp, dtype):
        """A value read out of an array, such as `series.values.max()`, is a numpy scalar."""
        scalar = getattr(numpy, dtype)(2)
        for result in (ramp * scalar, scalar * ramp, ramp + scalar, scalar + ramp, ramp - scalar, scalar - ramp):
            assert isinstance(result, TimeSeries)
            assert (result.start, result.last_step) == (0, 9)
        assert (scalar * ramp)[4] == 8.0
        assert (scalar - ramp)[4] == -2.0

    def test_scalar_arithmetic_on_an_empty_series(self):
        assert (TimeSeries.empty(float) * 2).size == 0

    def test_an_integer_series_stays_integral(self):
        series = TimeSeries.empty(int)
        for step in range(5):
            series.append(step, step)
        assert (series * 2).dtype == numpy.dtype(int)
        assert isinstance((series * 2)[3], int)

    def test_combining_with_an_unsupported_type_is_not_implemented(self, ramp):
        with pytest.raises(TypeError):
            _ = ramp + "x"  # pyright: ignore[reportOperatorIssue]


class TestConstruction:
    def test_wraps_an_existing_array(self):
        series = TimeSeries(numpy.array([1.0, 2.0, 3.0]), 10, 3)
        assert series.start == 10
        assert series.last_step == 12
        assert series[11] == 2.0

    def test_buffer_may_be_longer_than_the_recorded_size(self):
        series = TimeSeries(numpy.arange(10.0), 0, 3)
        assert series.size == 3
        assert series.last_step == 2
        assert list(series.values) == [0.0, 1.0, 2.0]

    def test_size_beyond_the_buffer_raises(self):
        with pytest.raises(ValueError, match="too small"):
            TimeSeries(numpy.zeros(4), 0, 5)

    def test_negative_size_raises(self):
        with pytest.raises(ValueError, match="negative"):
            TimeSeries(numpy.zeros(4), 0, -1)

    def test_a_non_empty_series_needs_a_start(self):
        with pytest.raises(ValueError, match="needs a start step"):
            TimeSeries(numpy.zeros(4), None, 3)

    def test_an_empty_series_drops_a_pointless_start(self):
        assert TimeSeries(numpy.zeros(4), 99, 0)._start is None

    def test_an_empty_buffer_is_rejected(self):
        # It would make a series with no room to append, so nothing could ever be recorded.
        with pytest.raises(ValueError, match="must not be empty"):
            TimeSeries(numpy.zeros(0), None, 0)

    def test_operations_that_yield_nothing_still_give_an_appendable_series(self, ramp):
        for series in (ramp[5:5], TimeSeries.empty(float).copy()):
            assert series.size == 0
            series.append(7, 1.0)
            assert series.start == 7
            assert series.last_value == 1.0

    def test_repr_names_the_step_range(self, ramp):
        assert "0-9" in repr(ramp)
        assert "empty" in repr(TimeSeries.empty(float))
