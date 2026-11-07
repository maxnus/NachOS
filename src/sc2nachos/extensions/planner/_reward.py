"""What a plan is worth: the reward a bot hands the planner."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, final

from sc2nachos.gametime import GameTime
from sc2nachos.ids import UnitTypeId, UpgradeId

if TYPE_CHECKING:
    from sc2nachos.extensions.planner._plan import Plan


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class CurveSegment:
    """One piece of a concave reward on a unit type's count: each unit up to `up_to` is worth `slope` a second."""

    slope: float
    """What each unit of the piece is worth a second."""
    up_to: float = math.inf
    """The count the piece runs to; the last piece runs on, whatever it says."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Coverage:
    """Covering one kind of enemy unit: each enemy unit counted, up to how many the bot expects, is worth `value` a
    second while the bot's own units can cover it, each own unit of a type covering `effectiveness` of one, summed.

    Raises `ValueError` for `enemy_counts` not in order of time.
    """

    enemy_counts: Sequence[tuple[GameTime, float]]
    """How many the bot expects, from each game time on, in order of time; none before the first."""
    effectiveness: Mapping[UnitTypeId, float]
    """How much of one enemy unit one own unit of each type covers."""
    value: float
    """What each enemy unit covered is worth a second."""

    def __post_init__(self) -> None:
        check_in_order_of_time(self.enemy_counts, "enemy_counts")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Floor:
    """A count of a unit type the bot wants at least, from each game time on: each unit short costs `penalty` a second.

    Raises `ValueError` for `counts` not in order of time.
    """

    unit_type: UnitTypeId
    """The unit type counted."""
    counts: Sequence[tuple[GameTime, float]]
    """The count wanted, from each game time on, in order of time; none before the first."""
    penalty: float
    """What each unit short costs a second."""

    def __post_init__(self) -> None:
        check_in_order_of_time(self.counts, "counts")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Reward:
    """What a plan is worth, summed over its horizon a second at a time: the units the plan expects alive, each second
    worth what `weights` or `curves` give their count, the upgrades it expects done, the enemy units it covers, less
    what it falls short of the floors, each second discounted by `half_life`. Only what is expected to survive counts,
    by the loss rates handed to `Planner.plan_now`.

    `terminal_time` counts the state at the horizon for that much longer, as if it lasted, discounted alike. With a
    `previous_plan`, each of its items the new plan starts no later than the previous one expected, counting those of
    its type before it, is worth `hysteresis_bonus` once, so that a new plan changes the old only for more than that.

    Raises `ValueError` for a unit type both weighted and given a curve, a curve that is not concave, its slopes
    falling and its counts rising, a `half_life` not longer than 0, a negative `terminal_time` or `hysteresis_bonus`,
    or a `hysteresis_bonus` with no `previous_plan`.
    """

    weights: Mapping[UnitTypeId, float] = field(default_factory=dict)
    """What each unit of a type is worth a second."""
    curves: Mapping[UnitTypeId, Sequence[CurveSegment]] = field(default_factory=dict)
    """What a type's units are worth a second, piece by piece of their count, the first piece from none."""
    upgrade_weights: Mapping[UpgradeId, float] = field(default_factory=dict)
    """What each upgrade done is worth a second."""
    coverages: Sequence[Coverage] = ()
    """The kinds of enemy unit worth covering."""
    floors: Sequence[Floor] = ()
    """The counts wanted at least."""
    half_life: GameTime | None = None
    """The time over which a second's worth halves, from the plan's start; `None` for no discount."""
    terminal_time: GameTime = GameTime.ZERO
    """How much longer the state at the horizon counts."""
    previous_plan: Plan | None = None
    """The plan a new one is to keep to."""
    hysteresis_bonus: float = 0.0
    """What each item of `previous_plan` the new plan keeps to is worth."""

    def __post_init__(self) -> None:
        if both := self.weights.keys() & self.curves.keys():
            raise ValueError(f"a unit type is weighted or given a curve, not both: {sorted(t.name for t in both)}")
        for unit_type, segments in self.curves.items():
            _check_concave(unit_type, segments)
        if self.half_life is not None and self.half_life <= GameTime.ZERO:
            raise ValueError(f"a half-life must be longer than 0, not {self.half_life}")
        if self.terminal_time < GameTime.ZERO:
            raise ValueError(f"terminal_time must not be negative, not {self.terminal_time}")
        if self.hysteresis_bonus < 0:
            raise ValueError(f"hysteresis_bonus must not be negative, not {self.hysteresis_bonus}")
        if self.hysteresis_bonus and self.previous_plan is None:
            raise ValueError("a hysteresis bonus needs the previous plan it favors")

    @property
    def unit_types(self) -> frozenset[UnitTypeId]:
        """Every unit type it values: weighted, given a curve, covering an enemy or held to a floor."""
        covering = {unit_type for coverage in self.coverages for unit_type in coverage.effectiveness}
        floored = {floor.unit_type for floor in self.floors}
        return frozenset(self.weights.keys() | self.curves.keys() | covering | floored)


def count_at(counts: Sequence[tuple[GameTime, float]], time: GameTime) -> float:
    """The count `counts` gives at `time`: the one from the latest time not after it, 0 before the first."""
    found = 0.0
    for since, count in counts:
        if since > time:
            break
        found = count
    return found


def check_in_order_of_time(counts: Sequence[tuple[GameTime, float]], name: str) -> None:
    """Raise `ValueError` unless the times of `counts` rise."""
    times = [time for time, _ in counts]
    if any(later <= earlier for earlier, later in zip(times, times[1:], strict=False)):
        raise ValueError(f"{name} must be in order of time, each later than the one before: {times}")


def _check_concave(unit_type: UnitTypeId, segments: Sequence[CurveSegment]) -> None:
    """Raise `ValueError` unless `segments` is concave: slopes not rising, and counts rising."""
    if not segments:
        raise ValueError(f"the curve of {unit_type.name} has no segments")
    for earlier, later in zip(segments, segments[1:], strict=False):
        if later.slope > earlier.slope:
            raise ValueError(
                f"the curve of {unit_type.name} is not concave: a slope of {later.slope} after {earlier.slope}"
            )
        if later.up_to <= earlier.up_to:
            raise ValueError(f"the curve of {unit_type.name} must count up: {later.up_to} after {earlier.up_to}")
