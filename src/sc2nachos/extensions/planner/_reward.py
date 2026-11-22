"""What a plan is worth: the reward a bot hands the planner."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
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
    """One part of what a plan is worth: the units of a group counted together, each for what the group says, are
    worth what a concave curve, `segments`, gives their count a second, from the game time `start` to `end`. Of several
    `groups` the curve is given the smallest count, so that the term is worth only what its scarcest group allows:
    units worth something in combination, as medivacs and the infantry they heal.

    Every part of a reward on units is one of these, and `weight`, `curve` and `combination` build the usual ones.
    The terms of a reward add up, each with its own curve and times, so a unit type may count in several: for what it
    is worth by itself, with others towards a count the bot wants of them together, and for something else from a game
    time on, where one term ends and another starts. A `time_factor` makes a term worth more or less as the game goes
    on, its whole curve times what the function gives for a game time.

    A term that `requires` upgrades is worth nothing until each is done: what marines are worth more with combat
    shield than without is such a term, beside the one for what they are worth anyway. A plan starts such an upgrade
    whole over all its horizon, whatever the planner's `fluid_after` says, since a share of one would count for far
    more than that share of the term's units.

    Raises `ValueError` for no group, an `end` not later than `start`, a curve that is not concave, its weights falling
    and its counts rising, or a negative weight in a term of several groups or that requires an upgrade. What
    `time_factor` gives is only seen where a plan is made: see `factor_at`.
    """

    groups: Sequence[Mapping[UnitTypeId, float]]
    """The units counted, a group to a count: how much one unit of each type counts in it. Most terms have one."""
    segments: Sequence[CurveSegment]
    """What the count is worth a second, piece by piece of the count, the first piece from none."""
    start: GameTime = GameTime.ZERO
    """The game time it is worth something from: the game's start by default."""
    end: GameTime = GameTime.INFINITE
    """The game time it is worth something up to, and nothing at: none by default."""
    time_factor: Callable[[GameTime], float] | None = None
    """What the curve is multiplied by at a game time between `start` and `end`, never negative; `None` for 1
    throughout. A plan asks it at the end of each of its intervals, so it misses what happens within one."""
    requires: frozenset[UpgradeId] = frozenset()
    """The upgrades that must be done for the term to be worth anything."""

    def __post_init__(self) -> None:
        if not self.groups:
            raise ValueError("a term must count a group of units at least")
        if self.end <= self.start:
            raise ValueError(f"a term must end later than it starts, not at {self.end} from {self.start}")
        _check_concave(self.unit_types, self.segments)
        if any(segment.weight < 0 for segment in self.segments):
            if len(self.groups) > 1:
                raise ValueError(f"a term of several groups may not hold a negative weight: {self.segments}")
            if self.requires:
                raise ValueError(f"a term that requires an upgrade may not hold a negative weight: {self.segments}")

    @classmethod
    def weight(
        cls,
        units: UnitTypeId | Mapping[UnitTypeId, float],
        weight: float,
        *,
        end_count: float = math.inf,
        start: GameTime = GameTime.ZERO,
        end: GameTime = GameTime.INFINITE,
        time_factor: Callable[[GameTime], float] | None = None,
        requires: Iterable[UpgradeId] = (),
    ) -> Self:
        """Each unit of `units` is worth `weight` a second, however many there are: of one unit type, or of several
        counted together, one unit of each for what the mapping says. With an `end_count`, each unit up to that count
        is, and none past it anything: a count the bot wants, which a plan may fall short of. From the game time
        `start`, the game's start by default, to `end`, and nothing before or from then, times what `time_factor`
        gives for a game time between them; and only once every upgrade of `requires` is done."""
        segments = _segments_of(weight, end_count)
        return cls.curve(units, segments, start=start, end=end, time_factor=time_factor, requires=requires)

    @classmethod
    def curve(
        cls,
        units: UnitTypeId | Mapping[UnitTypeId, float],
        segments: Sequence[CurveSegment],
        *,
        start: GameTime = GameTime.ZERO,
        end: GameTime = GameTime.INFINITE,
        time_factor: Callable[[GameTime], float] | None = None,
        requires: Iterable[UpgradeId] = (),
    ) -> Self:
        """The units of `units` are worth what `segments` give their count a second, more of them less each: of one
        unit type, or of several counted together, one unit of each for what the mapping says. From the game time
        `start`, the game's start by default, to `end`, and nothing before or from then, times what `time_factor`
        gives for a game time between them; and only once every upgrade of `requires` is done."""
        counted = {units: 1.0} if isinstance(units, UnitTypeId) else units
        return cls(
            groups=[counted],
            segments=segments,
            start=start,
            end=end,
            time_factor=time_factor,
            requires=frozenset(requires),
        )

    @classmethod
    def combination(
        cls,
        groups: Sequence[Mapping[UnitTypeId, float]],
        weight: float,
        *,
        end_count: float = math.inf,
        start: GameTime = GameTime.ZERO,
        end: GameTime = GameTime.INFINITE,
        time_factor: Callable[[GameTime], float] | None = None,
        requires: Iterable[UpgradeId] = (),
    ) -> Self:
        """Units worth something in combination: each group of `groups` is counted, one unit of each type for what
        the group says, and each unit of the smallest of those counts is worth `weight` a second, so that the
        scarcest group limits what the others are worth. `[{MEDIVAC: 8}, {MARINE: 1, MARAUDER: 2}]` with 0.3 is 0.3
        for each marine a medivac heals, of which it heals 8, a marauder for two: 24 marines with 2 medivacs are
        worth 0.3 times 16, and a third medivac makes it 24. What each unit is worth without the others is another
        term's.

        With an `end_count`, no unit past that count is worth anything. From the game time `start`, the game's start
        by default, to `end`, and nothing before or from then, times what `time_factor` gives for a game time between
        them; and only once every upgrade of `requires` is done. Raises `ValueError` for a negative `weight`."""
        return cls(
            groups=groups,
            segments=_segments_of(weight, end_count),
            start=start,
            end=end,
            time_factor=time_factor,
            requires=frozenset(requires),
        )

    @property
    def unit_types(self) -> frozenset[UnitTypeId]:
        """Every unit type its groups count."""
        return frozenset(unit_type for group in self.groups for unit_type in group)

    def factor_at(self, time: GameTime) -> float:
        """What its curve is multiplied by at `time`: nothing before its `start` and from its `end`, and between them
        what its `time_factor` gives, 1 without one.

        Raises `ValueError` for a factor that is negative or not finite."""
        if not self.start <= time < self.end:
            return 0.0
        if self.time_factor is None:
            return 1.0
        factor = self.time_factor(time)
        if not 0 <= factor < math.inf:
            raise ValueError(f"a term's time factor must be a number not below 0, not {factor} at {time}")
        return factor


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
        """A reward of a term for each unit type of `weights`, at any time: one number for what each unit of it is worth
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
        return frozenset[UnitTypeId]().union(*(term.unit_types for term in self.terms))

    @property
    def upgrades(self) -> frozenset[UpgradeId]:
        """Every upgrade it values: those weighted, and those its terms require."""
        return frozenset(self.upgrade_weights).union(*(term.requires for term in self.terms))


def _segments_of(weight: float, end_count: float) -> Sequence[CurveSegment]:
    """The curve of `weight` for each unit up to `end_count`, and nothing for any past it."""
    if math.isinf(end_count):
        return [CurveSegment(weight=weight)]
    return [CurveSegment(weight=weight, end_count=end_count), CurveSegment(weight=0.0)]


def _check_concave(unit_types: Iterable[UnitTypeId], segments: Sequence[CurveSegment]) -> None:
    """Raise `ValueError` unless `segments` is concave: weights not rising, and counts rising but the last's."""
    names = ", ".join(sorted(unit_type.name for unit_type in unit_types))
    if not segments:
        raise ValueError(f"a curve of {names} has no segments")
    for position, (earlier, later) in enumerate(zip(segments, segments[1:], strict=False), start=1):
        if later.weight > earlier.weight:
            raise ValueError(f"a curve of {names} is not concave: a weight of {later.weight} after {earlier.weight}")
        if position < len(segments) - 1 and later.end_count <= earlier.end_count:
            raise ValueError(f"a curve of {names} must count up: {later.end_count} after {earlier.end_count}")
