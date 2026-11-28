"""How many of a unit type's units survive its loss rate from one time of a plan to another."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import TYPE_CHECKING, final

import numpy as np

from sc2nachos.extensions.planner._milp import FloatArray
from sc2nachos.extensions.planner._time_grid import SECONDS_TOLERANCE
from sc2nachos.gametime import GameTime, TimeFunction
from sc2nachos.ids import UnitTypeId

if TYPE_CHECKING:
    from sc2nachos.extensions.planner._terran_model import TerranModel
    from sc2nachos.gametime import TimeDependent


@final
class SurvivalShares:
    """The share of each unit type's units that survives its loss rate from one time of a plan to another, by
    `loss_rates`: each unit type's under the type it counts as by `model`, the highest of those of one type. A number
    is a rate as it is, and a `TimeDependent` is read at each game step from `start`, the plan's start, to
    `horizon_seconds` after it, asked for all of them by one call and summed from the start to each step. Of a number
    and a `TimeDependent` for one type, the higher at each step.

    Raises `ValueError` for a rate a `TimeDependent` gives that is negative or not finite, `TypeError` for one that
    does not give a number for each step, as a function of one game time does not, and `IndexError` from one that
    does not cover a step.
    """

    __slots__ = ("_loss_rate_seconds", "_loss_rates", "_summed_loss_rates")

    def __init__(
        self,
        loss_rates: Mapping[UnitTypeId, float | TimeDependent[float]],
        *,
        model: TerranModel,
        start: GameTime,
        horizon_seconds: float,
    ) -> None:
        step_count = math.ceil(GameTime(seconds=horizon_seconds - SECONDS_TOLERANCE).steps)
        seconds_of_steps = np.arange(step_count + 1) * GameTime(steps=1).seconds
        self._loss_rate_seconds: FloatArray = np.minimum(seconds_of_steps, horizon_seconds)
        self._loss_rates: dict[UnitTypeId, float] = {}
        by_step: dict[UnitTypeId, FloatArray] = {}
        for unit_type, rate in loss_rates.items():
            counted = model.counted_as(unit_type)
            if callable(rate):
                rates = _loss_rates_from(rate, start, step_count, unit_type)
                by_step[counted] = np.maximum(rates, by_step.get(counted, 0.0))
            else:
                self._loss_rates[counted] = max(float(rate), self._loss_rates.get(counted, 0.0))
        self._summed_loss_rates: dict[UnitTypeId, FloatArray] = {}
        for counted, rates in by_step.items():
            highest = np.maximum(rates, self._loss_rates.pop(counted, 0.0))
            summed = np.cumsum(highest * np.diff(self._loss_rate_seconds))
            self._summed_loss_rates[counted] = np.concatenate([[0.0], summed])

    @property
    def unit_types_lost(self) -> tuple[UnitTypeId, ...]:
        """The unit types that have a loss rate, each as the type it counts as: a number above 0, or a
        `TimeDependent`."""
        lost_at_one_rate = (unit_type for unit_type, rate in self._loss_rates.items() if rate)
        return (*lost_at_one_rate, *self._summed_loss_rates)

    def share_surviving(
        self, unit_type: UnitTypeId, from_seconds: FloatArray | float, to_seconds: FloatArray | float
    ) -> FloatArray:
        """The share of the units of `unit_type` alive `from_seconds` after the plan's start that are alive `to_seconds`
        after it by its loss rate, all of them where that is no later."""
        summed = self._summed_loss_rates.get(unit_type)
        if summed is None:
            since = np.maximum(np.asarray(to_seconds, dtype=float) - from_seconds, 0.0)
            return np.exp(-self._loss_rates.get(unit_type, 0.0) * since)
        lost_by_then = np.interp(np.maximum(to_seconds, from_seconds), self._loss_rate_seconds, summed)
        return np.exp(np.interp(from_seconds, self._loss_rate_seconds, summed) - lost_by_then)


def _loss_rates_from(rate: TimeDependent[float], start: GameTime, step_count: int, unit_type: UnitTypeId) -> FloatArray:
    """What `rate` gives for each of `step_count` game steps from `start`, asked for all of them by one call, the steps
    whole numbers where `start`'s are. Raises `TypeError` unless that is a number for each, which a function of one
    game time does not give, and `ValueError` for a number below 0 or not finite, each naming `unit_type`; an
    `IndexError` or a `TypeError` of the call's own is noted with it."""
    steps = start.steps + np.arange(step_count)
    asked_as = f"the loss rate of {unit_type.name} is asked for every game step of the plan by one call"
    # What a function handed over as it is needs, which one in a `TimeFunction` already has.
    advice_for_a_function = (
        "" if isinstance(rate, TimeFunction) else "; a function of one game time goes in a TimeFunction"
    )
    try:
        rates: object = rate(steps)
    except IndexError as error:
        error.add_note(asked_as)
        raise
    except TypeError as error:
        error.add_note(asked_as + advice_for_a_function)
        raise
    if not (isinstance(rates, np.ndarray) and rates.shape == steps.shape and rates.dtype.kind in "iuf"):
        given = f"an array of {rates.dtype}, {rates.shape}" if isinstance(rates, np.ndarray) else repr(rates)
        raise TypeError(f"{asked_as}, and gave {given} for {step_count} steps{advice_for_a_function}")
    is_refused = ~((rates >= 0) & (rates < math.inf))
    if is_refused.any():
        first_refused = int(np.argmax(is_refused))
        raise ValueError(
            f"a loss rate must be a number not below 0, not {float(rates[first_refused])} for {unit_type.name} at "
            f"{start + GameTime(steps=first_refused)}"
        )
    return rates.astype(float, copy=False)
