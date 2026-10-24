from fractions import Fraction

import numpy
import pytest

from sc2nachos import GameTime
from sc2nachos.constants import SECONDS_PER_STEP, STEPS_PER_SECOND


class TestConversions:
    def test_a_second_is_the_step_rate(self):
        assert GameTime.from_seconds(1.0).steps == pytest.approx(STEPS_PER_SECOND)

    def test_a_step_is_the_step_duration(self):
        assert GameTime(steps=1).seconds == pytest.approx(SECONDS_PER_STEP)

    def test_round_trips_exactly(self):
        # Rounding inside the conversion once made this lossy: one second in, 0.982 back.
        for seconds in (0.5, 1.0, 7.3, 30.0, 600.0):
            assert GameTime.from_seconds(seconds).seconds == pytest.approx(seconds)

    def test_whole_seconds_are_exact(self):
        assert GameTime(steps=4480).seconds == 200.0
        assert GameTime(steps=336).seconds == 15.0

    def test_seconds_that_make_whole_steps_make_them_exactly(self):
        """Multiplying by the stored 22.4 took 45 seconds to 1007.9999999999999 steps."""
        assert GameTime.from_seconds(45) == GameTime(steps=1008)
        assert all(GameTime.from_seconds(seconds).steps == seconds * 224 // 10 for seconds in range(0, 100_000, 5))

    def test_is_not_rounded(self):
        assert GameTime.from_seconds(1.0).steps == 22.4

    def test_zero(self):
        assert GameTime.from_seconds(0).steps == 0
        assert GameTime(steps=0).seconds == 0

    def test_takes_its_steps_by_keyword_alone(self):
        with pytest.raises(TypeError):
            GameTime(96)  # type: ignore[misc]

    def test_is_frozen(self):
        time = GameTime(steps=96)
        with pytest.raises(AttributeError):
            time.steps = 1  # type: ignore[misc]


class TestTimeString:
    def test_reads_minutes_seconds_and_milliseconds(self):
        assert GameTime(steps=4480).time_string == "3:20.000"

    def test_rounds_the_milliseconds_down(self):
        # 96 steps are 4285.714 milliseconds.
        assert GameTime(steps=96).time_string == "0:04.285"

    def test_negative(self):
        assert GameTime(steps=-96).time_string == "-0:04.285"

    def test_past_an_hour_counts_on_in_minutes(self):
        assert GameTime.from_seconds(3725).time_string == "62:05.000"

    def test_is_the_true_milliseconds_rounded_down_and_tells_every_step_from_the_next(self):
        strings = set()
        for steps in range(0, 100_000, 7):
            milliseconds = Fraction(steps * 1000) / Fraction(224, 10)
            minutes, rest = divmod(int(milliseconds), 60_000)
            assert GameTime(steps=steps).time_string == f"{minutes}:{rest // 1000:02d}.{rest % 1000:03d}"
        for steps in range(100_000):
            strings.add(GameTime(steps=steps).time_string)
        assert len(strings) == 100_000


class TestArithmetic:
    def test_adds_and_subtracts(self):
        assert GameTime(steps=96) + GameTime(steps=4) == GameTime(steps=100)
        assert GameTime(steps=96) - GameTime(steps=100) == GameTime(steps=-4)

    def test_scales_by_a_plain_number(self):
        assert GameTime(steps=272) * 0.25 == GameTime(steps=68)
        assert 0.25 * GameTime(steps=272) == GameTime(steps=68)
        assert GameTime(steps=272) / 4 == GameTime(steps=68)

    def test_scales_by_a_numpy_scalar_on_either_side(self):
        assert GameTime(steps=272) * numpy.float32(0.25) == GameTime(steps=68)  # pyright: ignore[reportOperatorIssue]
        assert numpy.float64(0.25) * GameTime(steps=272) == GameTime(steps=68)
        assert GameTime(steps=272) / numpy.int64(4) == GameTime(steps=68)  # pyright: ignore[reportOperatorIssue]

    def test_divides_by_another_to_a_plain_number(self):
        assert GameTime(steps=68) / GameTime(steps=272) == 0.25
        assert GameTime(steps=100) // GameTime(steps=16) == 6
        assert isinstance(GameTime(steps=100) // GameTime(steps=16), int)

    def test_takes_the_remainder_of_another(self):
        assert GameTime(steps=100) % GameTime(steps=16) == GameTime(steps=4)

    def test_negates_and_takes_its_size(self):
        assert -GameTime(steps=96) == GameTime(steps=-96)
        assert abs(GameTime(steps=-96)) == GameTime(steps=96)

    @pytest.mark.parametrize(
        "operation",
        [
            lambda time: time + 1,
            lambda time: 1 + time,
            lambda time: time - 1,
            lambda time: time * GameTime(steps=2),
            lambda time: 2 / time,
            lambda time: time // 2,
            lambda time: time % 2,
        ],
    )
    def test_refuses_a_plain_number_where_the_unit_matters(self, operation):
        with pytest.raises(TypeError):
            operation(GameTime(steps=96))


class TestComparisons:
    def test_orders_against_another(self):
        assert GameTime(steps=95) < GameTime(steps=96) <= GameTime(steps=96)
        assert GameTime(steps=97) > GameTime(steps=96) >= GameTime(steps=96)
        assert max(GameTime(steps=3), GameTime(steps=7)) == GameTime(steps=7)

    @pytest.mark.parametrize(
        "comparison",
        [
            lambda time: time < 120,
            lambda time: time <= 120,
            lambda time: time > 120,
            lambda time: time >= 120,
            lambda time: time > 120,
        ],
    )
    def test_refuses_to_order_against_a_plain_number(self, comparison):
        with pytest.raises(TypeError):
            comparison(GameTime(steps=96))

    def test_equals_no_plain_number(self):
        assert GameTime(steps=96) != 96

    def test_equal_whole_and_fractional_steps_are_one_key(self):
        assert GameTime(steps=96) == GameTime(steps=96.0)
        assert len({GameTime(steps=96), GameTime(steps=96.0)}) == 1

    def test_zero_is_false(self):
        assert not GameTime(steps=0)
        assert GameTime(steps=0.5)
        assert GameTime(steps=-1)

    def test_repr_shows_the_steps(self):
        assert repr(GameTime(steps=4480)) == "GameTime(steps=4480)"
