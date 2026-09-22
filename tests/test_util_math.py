import math

import pytest

from sc2nachos.constants import SECONDS_PER_STEP
from sc2nachos.util import clip, damp, lerp, logistic, remap, s_curve, sticky_round


class TestClip:
    def test_within_range(self):
        assert clip(0.5) == 0.5

    def test_below_and_above(self):
        assert clip(-1.0) == 0
        assert clip(2.0) == 1

    def test_explicit_bounds(self):
        assert clip(7, 2, 5) == 5
        assert clip(1, 2, 5) == 2

    def test_preserves_int(self):
        assert isinstance(clip(7, 2, 5), int)


class TestLerp:
    def test_endpoints(self):
        assert lerp(10, 20, 0.0) == 10
        assert lerp(10, 20, 1.0) == 20

    def test_midpoint(self):
        assert lerp(10, 20, 0.5) == 15

    def test_extrapolates(self):
        assert lerp(10, 20, 2.0) == 30
        assert lerp(10, 20, -1.0) == 0

    def test_descending_range(self):
        assert lerp(20, 10, 0.25) == 17.5


class TestRemap:
    def test_endpoints(self):
        assert remap(0, 0, 10) == 0.0
        assert remap(10, 0, 10) == 1.0

    def test_midpoint(self):
        assert remap(5, 0, 10) == 0.5

    def test_clamps_outside(self):
        assert remap(-5, 0, 10) == 0.0
        assert remap(15, 0, 10) == 1.0

    def test_custom_output_range(self):
        assert remap(5, 0, 10, lower_value=2, upper_value=4) == 3.0

    def test_descending_output_range(self):
        assert remap(0, 0, 10, lower_value=1, upper_value=0) == 1.0
        assert remap(10, 0, 10, lower_value=1, upper_value=0) == 0.0

    def test_degenerate_range_raises(self):
        # A zero-width input range has no ramp to map onto; a step function is the caller's job.
        with pytest.raises(ValueError, match="not below upper bound"):
            remap(5, 5, 5, lower_value=2, upper_value=4)

    def test_inverted_range_raises(self):
        # An `assert` here would vanish under `python -O`, silently returning nonsense.
        with pytest.raises(ValueError, match="not below upper bound"):
            remap(5, 10, 0)


class TestStickyRound:
    def test_holds_within_tolerance(self):
        assert sticky_round(4.4, 4) == 4

    def test_rounds_outside_tolerance(self):
        assert sticky_round(5.6, 4) == 6

    def test_custom_tolerance(self):
        assert sticky_round(4.4, 4, tolerance=0.1) == 4


class TestDamp:
    def test_moves_from_start_toward_end(self):
        assert damp(0.0, 1.0, decay=1.0, seconds=SECONDS_PER_STEP) == pytest.approx(1 - math.exp(-SECONDS_PER_STEP))

    def test_longer_interval_moves_further(self):
        assert damp(0.0, 1.0, decay=1.0, seconds=1.0) > damp(0.0, 1.0, decay=1.0, seconds=SECONDS_PER_STEP)

    def test_zero_decay_holds(self):
        assert damp(0.5, 1.0, decay=0.0, seconds=1.0) == 0.5

    def test_never_overshoots(self):
        # A linear blend would overshoot at decay * seconds > 1 and diverge beyond 2. A long interval converges on
        # the end instead.
        for seconds in (1.0, 10.0, 1000.0):
            assert 0.0 <= damp(0.0, 1.0, decay=5.0, seconds=seconds) <= 1.0

    def test_converges_on_the_end(self):
        assert damp(0.0, 1.0, decay=1.0, seconds=100.0) == pytest.approx(1.0)

    def test_repeated_application_is_step_rate_independent(self):
        """Damping every step lands where damping every eighth step does, over the same ten seconds."""
        fine = coarse = 0.0
        for _ in range(224):
            fine = damp(fine, 1.0, decay=0.5, seconds=SECONDS_PER_STEP)
        for _ in range(28):
            coarse = damp(coarse, 1.0, decay=0.5, seconds=8 * SECONDS_PER_STEP)
        assert fine == pytest.approx(coarse, rel=1e-9)


class TestLogistic:
    def test_centered_on_zero(self):
        assert logistic(0.0) == pytest.approx(0.5)

    def test_saturates(self):
        assert logistic(50.0) == pytest.approx(1.0)
        assert logistic(-50.0) == pytest.approx(0.0)

    def test_monotonic(self):
        values = [logistic(x) for x in (-4.0, -1.0, 0.0, 1.0, 4.0)]
        assert values == sorted(values)

    def test_symmetric_about_the_center(self):
        assert logistic(1.3) == pytest.approx(1 - logistic(-1.3))

    def test_steepness(self):
        assert logistic(0.5, k=10) > logistic(0.5, k=1)

    def test_extreme_inputs_do_not_overflow(self):
        # `1 / (1 + exp(-k * x))` alone raises OverflowError here, for either sign of k.
        for x in (-1e6, 1e6):
            for k in (-3.0, 3.0):
                assert 0.0 <= logistic(x, k=k) <= 1.0


class TestSCurve:
    def test_fixes_the_endpoints_and_the_middle(self):
        assert s_curve(0.0) == pytest.approx(0.0)
        assert s_curve(0.5) == pytest.approx(0.5)
        assert s_curve(1.0) == pytest.approx(1.0)

    def test_monotonic(self):
        values = [s_curve(x) for x in (0.0, 0.25, 0.5, 0.75, 1.0)]
        assert values == sorted(values)

    def test_steepness_bends_the_curve(self):
        assert s_curve(0.25, k=10) < s_curve(0.25, k=1)

    def test_is_odd_about_the_midpoint(self):
        assert s_curve(0.25, k=7) == pytest.approx(1 - s_curve(0.75, k=7))

    def test_extrapolates_rather_than_clamping(self):
        # The old name, clipped_sigmoid, promised a clamp it never applied.
        assert s_curve(2.0, k=7) > 1.0
        assert s_curve(-1.0, k=7) < 0.0
