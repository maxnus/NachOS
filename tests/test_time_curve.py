import math

import numpy
import pytest

from sc2nachos import GameTime
from sc2nachos.gametime import Interpolation, TimeCurve


@pytest.fixture
def rising_losses() -> TimeCurve:
    """No losses until 5:00, rising to 0.004 by 6:00."""
    return TimeCurve([(GameTime(seconds=300), 0.0), (GameTime(seconds=360), 0.004)])


@pytest.fixture
def losses_from_five_minutes() -> TimeCurve:
    """No losses until 5:00, and 0.004 from then on."""
    return TimeCurve([(GameTime.ZERO, 0.0), (GameTime(seconds=300), 0.004)], interpolation=Interpolation.STEP)


class TestLinear:
    def test_reads_its_values_at_its_times(self, rising_losses):
        assert rising_losses(GameTime(seconds=300)) == 0.0
        assert rising_losses(GameTime(seconds=360)) == 0.004

    def test_reads_along_the_line_between_two_times(self, rising_losses):
        assert rising_losses(GameTime(seconds=330)) == pytest.approx(0.002)
        assert rising_losses(GameTime(seconds=315)) == pytest.approx(0.001)

    def test_reads_a_time_between_two_steps_as_given(self):
        curve = TimeCurve([(GameTime(steps=0), 0.0), (GameTime(steps=10), 10.0)])
        assert curve(GameTime(steps=3.5)) == pytest.approx(3.5)

    def test_holds_its_first_value_before_its_first_time(self, rising_losses):
        assert rising_losses(GameTime(seconds=200)) == 0.0
        assert rising_losses(GameTime.ZERO) == 0.0
        assert rising_losses(-GameTime.INFINITE) == 0.0

    def test_holds_its_last_value_after_its_last_time(self, rising_losses):
        assert rising_losses(GameTime(seconds=900)) == 0.004
        assert rising_losses(GameTime.INFINITE) == 0.004

    def test_is_linear_by_default(self, rising_losses):
        assert rising_losses.interpolation is Interpolation.LINEAR

    def test_a_peak_is_three_values(self):
        """30% lost within a second at 2:00."""
        peak = TimeCurve([(GameTime(seconds=119.5), 0.0), (GameTime(seconds=120), 0.7), (GameTime(seconds=120.5), 0.0)])
        assert peak(GameTime(seconds=119)) == 0.0
        assert peak(GameTime(seconds=120)) == 0.7
        assert peak(GameTime(seconds=120.25)) == pytest.approx(0.35)
        assert peak(GameTime(seconds=121)) == 0.0

    def test_a_ramp_then_a_jump_is_two_values_a_step_apart(self):
        jump_at = GameTime(steps=100)
        curve = TimeCurve([(GameTime(steps=0), 0.0), (jump_at - GameTime(steps=1), 1.0), (jump_at, 5.0)])
        assert curve(GameTime(steps=99)) == 1.0
        assert curve(GameTime(steps=100)) == 5.0


class TestStep:
    def test_holds_each_value_from_its_time_until_the_next(self, losses_from_five_minutes):
        assert losses_from_five_minutes(GameTime(seconds=299)) == 0.0
        assert losses_from_five_minutes(GameTime(seconds=300) - GameTime(steps=0.5)) == 0.0
        assert losses_from_five_minutes(GameTime(seconds=300)) == 0.004
        assert losses_from_five_minutes(GameTime(seconds=301)) == 0.004

    def test_holds_its_ends_as_a_linear_curve_does(self):
        curve = TimeCurve(
            [(GameTime(steps=10), 1.0), (GameTime(steps=20), 2.0), (GameTime(steps=30), 3.0)],
            interpolation=Interpolation.STEP,
        )
        assert [curve(GameTime(steps=step)) for step in (0, 10, 19.9, 20, 29, 30, 99)] == [1, 1, 1, 2, 2, 3, 3]
        assert curve(GameTime.INFINITE) == 3.0

    def test_many_times_read_as_each_does(self):
        curve = TimeCurve(
            [(GameTime(steps=10), 1.0), (GameTime(steps=20), 2.0), (GameTime(steps=30), 3.0)],
            interpolation=Interpolation.STEP,
        )
        steps = numpy.array([0, 10, 19.9, 20, 29, 30, 99, math.inf, -math.inf])
        assert curve(steps).tolist() == [curve(GameTime(steps=step)) for step in steps.tolist()]


class TestManyTimes:
    @pytest.mark.parametrize("interpolation", list(Interpolation))
    def test_read_to_the_last_bit_as_each_time_does(self, interpolation):
        """A call with one time works its value out in Python and one with many through numpy."""
        generator = numpy.random.default_rng(7)
        curve_steps = numpy.cumsum(generator.uniform(0.5, 400.0, size=12))
        pairs = [
            (GameTime(steps=step), value) for step, value in zip(curve_steps, generator.normal(size=12), strict=True)
        ]
        curve = TimeCurve(pairs, interpolation=interpolation)
        steps = numpy.concatenate([generator.uniform(-100.0, curve_steps[-1] + 100.0, size=2000), curve_steps])
        assert curve(steps).tolist() == [curve(GameTime(steps=step)) for step in steps.tolist()]

    def test_every_step_of_five_minutes(self, rising_losses):
        start = GameTime(seconds=200)
        steps = start.steps + numpy.arange(6720)
        rates = rising_losses(steps)
        assert rates.shape == (6720,)
        assert rates.min() == 0.0
        assert rates.max() == 0.004
        assert rates[int(GameTime(seconds=130).steps)] == pytest.approx(0.002)

    def test_a_default_is_never_read(self, rising_losses):
        assert rising_losses(numpy.array([-1e9, 1e9]), default=7.0).tolist() == [0.0, 0.004]
        assert rising_losses(GameTime(seconds=9000), default=None) == 0.004


class TestMaking:
    def test_one_value_is_held_at_every_time(self):
        curve = TimeCurve([(GameTime(seconds=60), 0.002)])
        assert curve(GameTime.ZERO) == curve(GameTime(seconds=60)) == curve(GameTime(seconds=600)) == 0.002
        assert curve(numpy.array([0, 5000])).tolist() == [0.002, 0.002]

    def test_whole_numbers_read_as_floats(self):
        curve = TimeCurve([(GameTime(steps=0), 1), (GameTime(steps=10), numpy.int64(3))])  # pyright: ignore[reportArgumentType]
        assert isinstance(curve(GameTime(steps=0)), float)
        assert curve(GameTime(steps=5)) == 2.0

    def test_takes_any_iterable_of_pairs(self):
        curve = TimeCurve((GameTime(steps=step), float(step)) for step in range(3))
        assert len(curve) == 3

    def test_iterating_hands_out_its_times_and_values(self, rising_losses):
        assert list(rising_losses) == [(GameTime(seconds=300), 0.0), (GameTime(seconds=360), 0.004)]
        longer = TimeCurve([*rising_losses, (GameTime(seconds=420), 0.0)])
        assert longer(GameTime(seconds=390)) == pytest.approx(0.002)

    def test_is_not_changed_by_the_pairs_it_was_made_of(self):
        pairs = [(GameTime(steps=0), 0.0), (GameTime(steps=10), 10.0)]
        curve = TimeCurve(pairs)
        pairs.append((GameTime(steps=20), 0.0))
        assert len(curve) == 2
        assert curve(GameTime(steps=20)) == 10.0

    def test_repr_shows_its_pairs_and_any_other_interpolation(self, rising_losses, losses_from_five_minutes):
        assert (
            repr(rising_losses) == f"TimeCurve([({GameTime(seconds=300)!r}, 0.0), ({GameTime(seconds=360)!r}, 0.004)])"
        )
        assert repr(losses_from_five_minutes).endswith("interpolation=Interpolation.STEP)")

    def test_no_value_raises(self):
        with pytest.raises(ValueError, match="at least one value"):
            TimeCurve([])

    @pytest.mark.parametrize("second_step", [10, 9])
    def test_a_time_not_after_the_one_before_raises(self, second_step):
        with pytest.raises(ValueError, match="a curve's times rise"):
            TimeCurve([(GameTime(steps=10), 0.0), (GameTime(steps=second_step), 1.0)])

    def test_a_time_not_finite_raises(self):
        with pytest.raises(ValueError, match="a curve's time is finite"):
            TimeCurve([(GameTime.ZERO, 0.0), (GameTime.INFINITE, 1.0)])

    def test_a_plain_time_raises(self):
        with pytest.raises(TypeError, match="a curve's time is a GameTime"):
            TimeCurve([(300, 0.0)])  # pyright: ignore[reportArgumentType]

    @pytest.mark.parametrize("value", [math.inf, math.nan])
    def test_a_value_not_finite_raises(self, value):
        with pytest.raises(ValueError, match="a curve's value is finite"):
            TimeCurve([(GameTime.ZERO, value)])

    def test_a_value_that_is_no_number_raises(self):
        with pytest.raises(TypeError, match="a curve's value is a number"):
            TimeCurve([(GameTime.ZERO, "0.004")])  # pyright: ignore[reportArgumentType]

    def test_an_interpolation_by_its_name_raises(self):
        with pytest.raises(TypeError, match="interpolation is an Interpolation"):
            TimeCurve([(GameTime.ZERO, 0.0)], interpolation="step")  # pyright: ignore[reportArgumentType]


class TestNoTime:
    def test_a_time_of_nan_steps_raises(self, rising_losses):
        no_time = GameTime.INFINITE - GameTime.INFINITE
        with pytest.raises(ValueError, match="no time"):
            rising_losses(no_time)
        with pytest.raises(ValueError, match="no time"):
            rising_losses(numpy.array([0.0, math.nan]))
