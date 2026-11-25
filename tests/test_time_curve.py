import decimal
import math

import numpy
import pytest

from sc2nachos import GameTime, Point
from sc2nachos.gamedata import Cost, Resources
from sc2nachos.gametime import Interpolation, TimeCurve, TimeDependent


@pytest.fixture
def rising_losses() -> TimeCurve[float]:
    """No losses until 5:00, rising to 0.004 by 6:00."""
    return TimeCurve([(GameTime(seconds=300), 0.0), (GameTime(seconds=360), 0.004)])


@pytest.fixture
def losses_from_five_minutes() -> TimeCurve[float]:
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

    def test_covers_times_far_outside_its_own(self, rising_losses):
        assert rising_losses(numpy.array([-1e9, 1e9])).tolist() == [0.0, 0.004]
        assert rising_losses(GameTime(seconds=9000)) == 0.004


class TestMaking:
    def test_one_value_is_held_at_every_time(self):
        curve = TimeCurve([(GameTime(seconds=60), 0.002)])
        assert curve(GameTime.ZERO) == curve(GameTime(seconds=60)) == curve(GameTime(seconds=600)) == 0.002
        assert curve(numpy.array([0, 5000])).tolist() == [0.002, 0.002]

    def test_whole_numbers_read_as_floats(self):
        curve = TimeCurve([(GameTime(steps=0), 1), (GameTime(steps=10), numpy.int64(3))])  # pyright: ignore[reportCallIssue, reportArgumentType]
        assert isinstance(curve(GameTime(steps=0)), float)
        assert curve(GameTime(steps=5)) == 2.0

    def test_takes_any_iterable_of_pairs(self):
        curve = TimeCurve((GameTime(steps=step), float(step)) for step in range(3))
        assert len(curve) == 3

    def test_iterating_hands_out_its_times_and_values(self, rising_losses):
        assert list(rising_losses) == [(GameTime(seconds=300), 0.0), (GameTime(seconds=360), 0.004)]
        whole_step = GameTime(steps=300)
        assert next(iter(TimeCurve([(whole_step, 1)])))[0] is whole_step
        longer = TimeCurve([*rising_losses, (GameTime(seconds=420), 0.0)])
        assert longer(GameTime(seconds=390)) == pytest.approx(0.002)

    def test_is_not_changed_by_the_pairs_it_was_made_of(self):
        pairs = [(GameTime(steps=0), 0.0), (GameTime(steps=10), 10.0)]
        curve = TimeCurve(pairs)
        pairs.append((GameTime(steps=20), 0.0))
        assert len(curve) == 2
        assert curve(GameTime(steps=20)) == 10.0

    def test_repr_shows_its_pairs_and_its_interpolation(self, rising_losses, losses_from_five_minutes):
        """The owner, review of #261: a linear curve says so too."""
        assert repr(rising_losses) == (
            f"TimeCurve([({GameTime(seconds=300)!r}, 0.0), ({GameTime(seconds=360)!r}, 0.004)],"
            " interpolation=Interpolation.LINEAR)"
        )
        assert repr(losses_from_five_minutes).endswith("interpolation=Interpolation.STEP)")

    def test_repr_shows_its_times_as_given(self):
        assert repr(TimeCurve([(GameTime(steps=300), 1)])).startswith("TimeCurve([(GameTime(steps=300), 1.0)],")

    def test_repr_shows_six_pairs_whole(self):
        curve = TimeCurve([(GameTime(steps=step), float(step)) for step in range(6)])
        assert "..." not in repr(curve)
        assert repr(curve).count("GameTime") == 6

    def test_repr_of_more_pairs_shows_the_first_five_and_the_last(self):
        """The owner, review of #261: a curve of a value a step is not printed whole."""
        curve = TimeCurve([(GameTime(steps=step), float(step)) for step in range(6720)])
        assert repr(curve) == (
            "TimeCurve([(GameTime(steps=0), 0.0), (GameTime(steps=1), 1.0), (GameTime(steps=2), 2.0),"
            " (GameTime(steps=3), 3.0), (GameTime(steps=4), 4.0), ..., (GameTime(steps=6719), 6719.0)],"
            " interpolation=Interpolation.LINEAR)"
        )

    @pytest.mark.parametrize(
        "no_pairs",
        [{GameTime(steps=0): 0.0}, [GameTime(steps=0)], [(GameTime(steps=0), 0.0, 1.0)], [(GameTime(steps=0),)], [7]],
    )
    def test_anything_but_pairs_raises(self, no_pairs):
        """A mapping of times to values raised `cannot unpack non-iterable GameTime object`."""
        with pytest.raises(TypeError, match="a curve is made of pairs of a GameTime and a value"):
            TimeCurve(no_pairs)

    def test_a_value_further_from_the_one_before_than_a_float_holds_raises(self):
        """The line between them had an infinite slope, which read NaN at the first of the two times."""
        with pytest.raises(ValueError, match="within a float of the one before"):
            TimeCurve([(GameTime(steps=0), 1e308), (GameTime(steps=10), -1e308)])
        assert TimeCurve([(GameTime(steps=0), 8e307), (GameTime(steps=10), -8e307)])(GameTime(steps=0)) == 8e307

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
            TimeCurve([(300, 0.0)])  # pyright: ignore[reportCallIssue, reportArgumentType]

    @pytest.mark.parametrize("value", [math.inf, math.nan])
    def test_a_value_not_finite_raises(self, value):
        with pytest.raises(ValueError, match="a curve's value is finite"):
            TimeCurve([(GameTime.ZERO, value)])

    @pytest.mark.parametrize("value", ["0.004", None, (0.0, 0.004), {0.004}])
    def test_a_value_that_neither_is_a_number_nor_adds_subtracts_and_multiplies_by_one_raises(self, value):
        """In every interpolation, so that a curve that is made can be made with another."""
        for interpolation in Interpolation:
            with pytest.raises(TypeError, match="a curve's value is a number, or adds, subtracts and multiplies"):
                TimeCurve([(GameTime.ZERO, value)], interpolation=interpolation)

    def test_an_interpolation_by_its_name_raises(self):
        with pytest.raises(TypeError, match="interpolation is an Interpolation"):
            TimeCurve([(GameTime.ZERO, 0.0)], interpolation="step")  # pyright: ignore[reportCallIssue, reportArgumentType]


class TestNoTime:
    def test_a_time_of_nan_steps_raises(self, rising_losses):
        no_time = GameTime.INFINITE - GameTime.INFINITE
        with pytest.raises(ValueError, match="no time"):
            rising_losses(no_time)
        with pytest.raises(ValueError, match="no time"):
            rising_losses(numpy.array([0.0, math.nan]))


@pytest.fixture
def income() -> TimeCurve[Resources]:
    """Nothing earned at the start, 900 minerals and 300 vespene by 5:00, and 1200 and 600 by 10:00."""
    return TimeCurve(
        [
            (GameTime.ZERO, Resources(0, 0)),
            (GameTime(seconds=300), Resources(900, 300)),
            (GameTime(seconds=600), Resources(1200, 600)),
        ]
    )


def test_a_step_curve_of_numbers_keeps_the_shape_of_its_times(losses_from_five_minutes):
    """It reads many times in one dimension, as a curve of other values does."""
    assert losses_from_five_minutes(numpy.array([[0, 6719], [6720, 9000]])).tolist() == [[0.0, 0.0], [0.004, 0.004]]
    value = losses_from_five_minutes(numpy.array(6720))
    assert isinstance(value, numpy.ndarray)
    assert (value.shape, value.dtype, value.item()) == ((), numpy.dtype(float), 0.004)


class TestValuesThatAreNoNumbers:
    """The owner, review of #261: anything that adds, subtracts and multiplies by a number, such as `Resources`."""

    def test_reads_along_the_line_between_two_times(self, income):
        assert income(GameTime(seconds=100)) == Resources(300.0, 100.0)
        assert income(GameTime(seconds=450)) == Resources(1050.0, 450.0)

    def test_reads_its_values_at_its_times(self, income):
        assert income(GameTime.ZERO) == Resources(0, 0)
        assert income(GameTime(seconds=300)) == Resources(900, 300)
        assert income(GameTime(seconds=600)) == Resources(1200, 600)

    def test_holds_its_first_and_its_last_value_outside_its_times(self):
        curve = TimeCurve([(GameTime(seconds=60), Resources(50, 0)), (GameTime(seconds=120), Resources(400, 100))])
        assert curve(GameTime.ZERO) == Resources(50, 0)
        assert curve(GameTime(seconds=900)) == curve(GameTime.INFINITE) == Resources(400, 100)

    def test_many_times_give_an_array_of_objects_a_value_each(self, income):
        values = income([GameTime(seconds=100), GameTime.ZERO, GameTime(seconds=900)])
        assert (values.dtype, values.shape) == (numpy.dtype(object), (3,))
        assert values.tolist() == [Resources(300.0, 100.0), Resources(0, 0), Resources(1200, 600)]

    @pytest.mark.parametrize("interpolation", list(Interpolation))
    def test_many_times_read_as_each_of_them_does(self, interpolation):
        random = numpy.random.default_rng(7)
        pairs = [
            (GameTime(steps=step), Resources(minerals, vespene))
            for step, minerals, vespene in zip(
                numpy.cumsum(random.uniform(0.5, 400.0, 30)).tolist(),
                random.uniform(0.0, 2000.0, 30).tolist(),
                random.uniform(0.0, 900.0, 30).tolist(),
                strict=True,
            )
        ]
        curve = TimeCurve(pairs, interpolation=interpolation)
        last_step = pairs[-1][0].steps
        steps = numpy.concatenate(
            [random.uniform(-50.0, last_step + 50.0, 2000), [pair[0].steps for pair in pairs], [-1e9, 1e9]]
        )
        assert curve(steps).tolist() == [curve(GameTime(steps=step)) for step in steps.tolist()]

    def test_an_array_keeps_its_shape(self, income):
        steps = numpy.array([[0, 2240], [6720, 20000]])
        values = income(steps)
        assert values.shape == (2, 2)
        assert values[0, 1] == Resources(300.0, 100.0)
        assert values[1, 1] == Resources(1200, 600)

    def test_an_array_with_no_dimension_gives_one_too(self, income):
        value = income(numpy.array(2240))
        assert isinstance(value, numpy.ndarray)
        assert (value.shape, value.item()) == ((), Resources(300.0, 100.0))

    def test_no_times_give_no_values(self, income):
        assert income([]).shape == (0,)
        assert income(numpy.zeros((0, 3))).shape == (0, 3)

    def test_the_values_of_a_range_add_up(self, income):
        """What an array of objects is for: numpy adds them a value at a time."""
        assert income(numpy.array([0, 2240, 6720])).sum() == Resources(1200.0, 400.0)

    def test_each_value_is_held_until_the_next_with_step(self):
        curve = TimeCurve(
            [(GameTime.ZERO, Resources(0, 0)), (GameTime(seconds=300), Resources(900, 300))],
            interpolation=Interpolation.STEP,
        )
        assert curve(GameTime(seconds=299)) == Resources(0, 0)
        assert curve(GameTime(seconds=300)) == Resources(900, 300)
        assert curve(numpy.array([6719, 6720])).tolist() == [Resources(0, 0), Resources(900, 300)]

    @pytest.mark.parametrize("interpolation", list(Interpolation))
    def test_a_value_that_is_a_sequence_stays_one_value(self, interpolation):
        """A `Point` is a tuple, which numpy would read as a row of two numbers."""
        path = TimeCurve(
            [(GameTime(steps=0), Point((0, 0))), (GameTime(steps=10), Point((20, 10)))], interpolation=interpolation
        )
        positions = path(numpy.array([[0, 5], [10, 15]]))
        assert (positions.dtype, positions.shape) == (numpy.dtype(object), (2, 2))
        assert all(isinstance(position, Point) for position in positions.ravel().tolist())
        assert positions[1, 0] == Point((20, 10))
        halfway = Point((10, 5)) if interpolation is Interpolation.LINEAR else Point((0, 0))
        assert positions[0, 1] == path(GameTime(steps=5)) == halfway
        assert path(numpy.array(5)).shape == ()

    def test_a_game_time_is_a_value_too(self):
        arrival = TimeCurve([(GameTime(steps=0), GameTime(seconds=30)), (GameTime(steps=100), GameTime(seconds=10))])
        assert arrival(GameTime(steps=50)) == GameTime(seconds=20)

    def test_iterating_hands_its_values_back_as_given(self, income):
        values = [value for _, value in income]
        assert values == [Resources(0, 0), Resources(900, 300), Resources(1200, 600)]
        assert TimeCurve(income)(GameTime(seconds=100)) == Resources(300.0, 100.0)
        assert "Resources(minerals=900, vespene=300)" in repr(income)

    @pytest.mark.parametrize(
        "values", [(Resources(0, 0), 100.0), (100.0, Resources(0, 0)), (Resources(0, 0), Resources(5, 5), 1)]
    )
    def test_numbers_among_values_that_are_none_raise(self, values):
        pairs = [(GameTime(steps=step), value) for step, value in enumerate(values)]
        with pytest.raises(TypeError, match="a curve's values are all numbers or none of them is"):
            TimeCurve(pairs)

    @pytest.mark.parametrize(
        "values",
        [
            (Resources(0, 0), Cost(100, 0, 1)),
            (Cost(100, 0, 1), Resources(0, 0)),
            (decimal.Decimal(0), decimal.Decimal(1)),
            (Resources(0, 0), Resources(5, 5), Point((0, 0))),
        ],
    )
    def test_values_no_line_joins_raise_as_the_curve_is_made(self, values):
        """They have the operations and raise in them, which the first read between two would have met, in the
        planner or wherever the curve was handed to."""
        pairs = [(GameTime(steps=step), value) for step, value in enumerate(values)]
        for interpolation in Interpolation:
            with pytest.raises(TypeError, match="a curve's values add, subtract and multiply by a number, which"):
                TimeCurve(pairs, interpolation=interpolation)

    def test_one_value_alone_needs_no_line(self):
        """Its kind is not one a type checker lets by, and a curve of one value reads it at every time."""
        curve = TimeCurve([(GameTime.ZERO, decimal.Decimal(1))])  # pyright: ignore[reportCallIssue, reportArgumentType]
        assert curve(GameTime(seconds=5)) == decimal.Decimal(1)

    def test_a_numpy_boolean_is_a_number(self):
        """A comparison of numpy's gives one, which Python does not count among its numbers."""
        curve = TimeCurve([(GameTime(steps=0), numpy.bool_(False)), (GameTime(steps=10), numpy.bool_(True))])  # pyright: ignore[reportCallIssue, reportArgumentType]
        assert curve(GameTime(steps=5)) == 0.5
        assert curve(numpy.array([0, 5])).dtype == float

    def test_a_value_is_not_asked_to_be_finite(self):
        """Only a number is checked; what a value of another kind holds is its own."""
        curve = TimeCurve([(GameTime.ZERO, Resources(math.inf, 0))])
        assert curve(GameTime(seconds=5)) == Resources(math.inf, 0)

    def test_is_a_time_dependent_of_its_values_to_the_type_checker(self, income, rising_losses):
        """Pyright checks these assignments; whole numbers make a curve of floats."""
        of_resources: TimeDependent[Resources] = income
        of_floats: TimeDependent[float] = TimeCurve([(GameTime.ZERO, 0), (GameTime(seconds=5), 1)])
        of_points: TimeCurve[Point] = TimeCurve([(GameTime.ZERO, Point((0, 0)))])
        assert (of_resources, len(of_floats), len(of_points)) == (income, 2, 1)
