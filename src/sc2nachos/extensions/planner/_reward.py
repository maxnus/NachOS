"""What a plan is worth: the reward a bot hands the planner."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import final

from sc2nachos.gametime import GameTime
from sc2nachos.ids import UnitTypeId, UpgradeId


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class CurveSegment:
    """One piece of a concave reward on a unit type's count: each unit up to the count it ends at is worth `weight` a
    second."""

    weight: float
    """What each unit of the piece is worth a second."""
    end: float = math.inf
    """The count the piece ends at, where the next begins; the last piece runs on, whatever it says."""


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
    worth what `weights` gives their count, the upgrades it expects done, the enemy units it covers, less
    what it falls short of the floors, each second discounted by `half_life`. Only what is expected to survive counts,
    by the loss rates handed to `Planner.plan_now`.

    `terminal_time` counts the state at the horizon for that much longer, as if it lasted, discounted alike. The stock
    left at the horizon is worth `stock_weight` a mineral and a vespene besides.

    Raises `ValueError` for a curve that is not concave, its weights falling and its counts rising, a `half_life` not
    longer than 0, or a negative `terminal_time` or `stock_weight`.
    """

    weights: Mapping[UnitTypeId, float | Sequence[CurveSegment]] = field(default_factory=dict)
    """What the units of a type are worth a second: one number for each of them, or a concave curve where more of them
    are worth less each, piece by piece of their count, the first piece from none."""
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
    stock_weight: float = 1e-4
    """What each mineral and each vespene left in stock at the horizon is worth, once and not discounted: by default
    0.0001, far less than what it could have bought under weights around 1, so that it decides only between plans
    worth the same otherwise, and a reward of much smaller weights wants it smaller in proportion. With it, a plan's
    workers bring in what they can where it buys nothing more. With 0 they may stand idle there, and a plan may count
    starts of mining no worker makes, which cost it nothing, so that its stocks read less than its workers would bring
    in."""

    def __post_init__(self) -> None:
        for unit_type, weight in self.weights.items():
            if not isinstance(weight, (int, float)):
                _check_concave(unit_type, weight)
        if self.half_life is not None and self.half_life <= GameTime.ZERO:
            raise ValueError(f"a half-life must be longer than 0, not {self.half_life}")
        if self.terminal_time < GameTime.ZERO:
            raise ValueError(f"terminal_time must not be negative, not {self.terminal_time}")
        if self.stock_weight < 0:
            raise ValueError(f"stock_weight must not be negative, not {self.stock_weight}")

    @property
    def unit_types(self) -> frozenset[UnitTypeId]:
        """Every unit type it values: weighted, covering an enemy or held to a floor."""
        covering = {unit_type for coverage in self.coverages for unit_type in coverage.effectiveness}
        floored = {floor.unit_type for floor in self.floors}
        return frozenset(self.weights.keys() | covering | floored)


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
    """Raise `ValueError` unless `segments` is concave: weights not rising, and counts rising but the last's."""
    if not segments:
        raise ValueError(f"the curve of {unit_type.name} has no segments")
    for position, (earlier, later) in enumerate(zip(segments, segments[1:], strict=False), start=1):
        if later.weight > earlier.weight:
            raise ValueError(
                f"the curve of {unit_type.name} is not concave: a weight of {later.weight} after {earlier.weight}"
            )
        if position < len(segments) - 1 and later.end <= earlier.end:
            raise ValueError(f"the curve of {unit_type.name} must count up: {later.end} after {earlier.end}")
