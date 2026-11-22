"""What a plan is worth: the reward a bot hands the planner."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Self, final

from sc2nachos.gametime import GameTime
from sc2nachos.ids import UnitTypeId, UpgradeId


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class CurveSegment:
    """One piece of a concave curve over a count: each unit up to the count it ends at is worth `weight` a second."""

    weight: float
    """What each unit of the piece is worth a second."""
    end_count: float = math.inf
    """The count the piece ends at, where the next begins; the last piece runs on, whatever it says."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class RewardTerm:
    """One part of what a plan is worth: its units counted together, each for what `units` says, are worth what a
    concave curve gives their count a second, and which curve may change at game times.

    Every part of a reward on units is one of these, and `weight` and `curve` build the usual ones. The terms of a
    reward add up, each with its own curve, so a unit type may count in several: for what it is worth by itself, and
    with others towards a count the bot wants of them together.

    A term that `requires` upgrades is worth nothing until each is done: what marines are worth more with combat
    shield than without is such a term, beside the one for what they are worth anyway. A plan starts such an upgrade
    whole over all its horizon, whatever the planner's `fluid_after` says, since a share of one would count for far
    more than that share of the term's units.

    Raises `ValueError` for `curves` not in order of time, a curve that is not concave, its weights falling and its
    counts rising, or a negative weight in a term that requires an upgrade.
    """

    units: Mapping[UnitTypeId, float]
    """How much one unit of each type counts."""
    curves: Sequence[tuple[GameTime, Sequence[CurveSegment]]]
    """What the count is worth a second from each game time on, in order of time, piece by piece of the count, the
    first piece from none; nothing before the first time."""
    requires: frozenset[UpgradeId] = frozenset()
    """The upgrades that must be done for the term to be worth anything."""

    def __post_init__(self) -> None:
        check_in_order_of_time(self.curves, "curves")
        for _, segments in self.curves:
            _check_concave(self.units, segments)
            if self.requires and any(segment.weight < 0 for segment in segments):
                raise ValueError(f"a term that requires an upgrade may not hold a negative weight: {segments}")

    @classmethod
    def weight(
        cls,
        units: UnitTypeId | Mapping[UnitTypeId, float],
        weight: float,
        *,
        end_count: float = math.inf,
        since: GameTime = GameTime.ZERO,
        requires: Iterable[UpgradeId] = (),
    ) -> Self:
        """Each unit of `units` is worth `weight` a second, however many there are: of one unit type, or of several
        counted together, one unit of each for what the mapping says. With an `end_count`, each unit up to that count
        is, and none past it anything: a count the bot wants, which a plan may fall short of. From the game time
        `since` on, the game's start by default, and nothing before it; and only once every upgrade of `requires` is
        done."""
        if math.isinf(end_count):
            segments = [CurveSegment(weight=weight)]
        else:
            segments = [CurveSegment(weight=weight, end_count=end_count), CurveSegment(weight=0.0)]
        return cls.curve(units, segments, since=since, requires=requires)

    @classmethod
    def curve(
        cls,
        units: UnitTypeId | Mapping[UnitTypeId, float],
        segments: Sequence[CurveSegment],
        *,
        since: GameTime = GameTime.ZERO,
        requires: Iterable[UpgradeId] = (),
    ) -> Self:
        """The units of `units` are worth what `segments` give their count a second, more of them less each: of one
        unit type, or of several counted together, one unit of each for what the mapping says. From the game time
        `since` on, the game's start by default, and nothing before it; and only once every upgrade of `requires` is
        done."""
        counted = {units: 1.0} if isinstance(units, UnitTypeId) else units
        return cls(units=counted, curves=[(since, segments)], requires=frozenset(requires))

    def segments_at(self, time: GameTime) -> Sequence[CurveSegment]:
        """The curve at `time`: the one from the latest time not after it, none before the first."""
        found: Sequence[CurveSegment] = ()
        for since, segments in self.curves:
            if since > time:
                break
            found = segments
        return found


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Reward:
    """What a plan is worth, summed over its horizon a second at a time: the units the plan expects alive, each second
    worth what its `terms` give their counts, and the upgrades it expects done, each second discounted by `half_life`.
    Only what is expected to survive counts, by the loss rates handed to `Planner.plan_now`.

    `terminal_time` counts the state at the horizon for that much longer, as if it lasted, discounted alike. The stock
    left at the horizon is worth `stock_weight` a mineral and a vespene besides.

    Raises `ValueError` for a `half_life` not longer than 0, or a negative `terminal_time` or `stock_weight`.
    """

    terms: Sequence[RewardTerm] = ()
    """What the units are worth, each term added to the others."""
    upgrade_weights: Mapping[UpgradeId, float] = field(default_factory=dict)
    """What each upgrade done is worth a second."""
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
        if self.half_life is not None and self.half_life <= GameTime.ZERO:
            raise ValueError(f"a half-life must be longer than 0, not {self.half_life}")
        if self.terminal_time < GameTime.ZERO:
            raise ValueError(f"terminal_time must not be negative, not {self.terminal_time}")
        if self.stock_weight < 0:
            raise ValueError(f"stock_weight must not be negative, not {self.stock_weight}")

    @classmethod
    def weighted(
        cls,
        weights: Mapping[UnitTypeId, float | Sequence[CurveSegment]],
        *,
        terms: Sequence[RewardTerm] = (),
        upgrade_weights: Mapping[UpgradeId, float] | None = None,
        half_life: GameTime | None = None,
        terminal_time: GameTime = GameTime.ZERO,
        stock_weight: float = 1e-4,
    ) -> Self:
        """A reward of a term for each unit type of `weights`, whenever: one number for what each unit of it is worth
        a second, or a curve where more of them are worth less each. `terms` come after them, and the rest is the
        reward's own."""
        of_weights = [
            RewardTerm.weight(unit_type, weight)
            if isinstance(weight, (int, float))
            else RewardTerm.curve(unit_type, weight)
            for unit_type, weight in weights.items()
        ]
        return cls(
            terms=(*of_weights, *terms),
            upgrade_weights={} if upgrade_weights is None else upgrade_weights,
            half_life=half_life,
            terminal_time=terminal_time,
            stock_weight=stock_weight,
        )

    @property
    def unit_types(self) -> frozenset[UnitTypeId]:
        """Every unit type it values: those its terms count."""
        return frozenset(unit_type for term in self.terms for unit_type in term.units)

    @property
    def upgrades(self) -> frozenset[UpgradeId]:
        """Every upgrade it values: those weighted, and those its terms require."""
        return frozenset(self.upgrade_weights).union(*(term.requires for term in self.terms))


def check_in_order_of_time(by_time: Sequence[tuple[GameTime, object]], name: str) -> None:
    """Raise `ValueError` unless the times of `by_time` rise."""
    times = [time for time, _ in by_time]
    if any(later <= earlier for earlier, later in zip(times, times[1:], strict=False)):
        raise ValueError(f"{name} must be in order of time, each later than the one before: {times}")


def _check_concave(units: Mapping[UnitTypeId, float], segments: Sequence[CurveSegment]) -> None:
    """Raise `ValueError` unless `segments` is concave: weights not rising, and counts rising but the last's."""
    names = ", ".join(sorted(unit_type.name for unit_type in units))
    if not segments:
        raise ValueError(f"a curve of {names} has no segments")
    for position, (earlier, later) in enumerate(zip(segments, segments[1:], strict=False), start=1):
        if later.weight > earlier.weight:
            raise ValueError(f"a curve of {names} is not concave: a weight of {later.weight} after {earlier.weight}")
        if position < len(segments) - 1 and later.end_count <= earlier.end_count:
            raise ValueError(f"a curve of {names} must count up: {later.end_count} after {earlier.end_count}")
