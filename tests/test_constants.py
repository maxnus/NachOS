import pytest

from sc2nachos.constants import SECONDS_PER_STEP, STEPS_PER_SECOND, seconds_to_steps, steps_to_seconds


class TestConversions:
    def test_a_second_is_the_step_rate(self):
        assert seconds_to_steps(1.0) == pytest.approx(STEPS_PER_SECOND)

    def test_a_step_is_the_step_duration(self):
        assert steps_to_seconds(1) == pytest.approx(SECONDS_PER_STEP)

    def test_round_trips_exactly(self):
        # Rounding inside the conversion once made this lossy: one second in, 0.982 back.
        for seconds in (0.5, 1.0, 7.3, 30.0, 600.0):
            assert steps_to_seconds(seconds_to_steps(seconds)) == pytest.approx(seconds)

    def test_is_not_rounded(self):
        assert seconds_to_steps(1.0) != int(seconds_to_steps(1.0))

    def test_additive(self):
        # Rounding per call once made the two sides differ by up to a step.
        assert seconds_to_steps(3.0) + seconds_to_steps(4.0) == pytest.approx(seconds_to_steps(7.0))

    def test_zero(self):
        assert seconds_to_steps(0) == 0
        assert steps_to_seconds(0) == 0
