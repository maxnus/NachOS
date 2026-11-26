"""What a plan is worth: the reward a bot hands the planner."""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Self, final

from sc2nachos.gametime import GameTime
from sc2nachos.ids import UnitTypeId, UpgradeId


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class WeightSegment:
    """One piece of a concave curve over a count: each unit up to the count it ends at is worth `weight` a second."""

    weight: float
    """What each unit of the piece is worth a second."""
    end_count: float = math.inf
    """The count the piece ends at, where the next begins: above 0 and finite for every piece but the last, which
    runs on, whatever it says."""


@final
@dataclass(frozen=True, slots=True)
class WeightCurve:
    """What each further unit of a count is worth a second, less the more of them there are. It is made of pieces, each
    a weight for every unit up to the count the piece ends at: `WeightCurve([WeightSegment(weight=1.0, end_count=30),
    WeightSegment(weight=0.3)])` makes each of the first 30 units worth 1.0 a second and each one after worth 0.3.
    `constant` and `up_to` build the two usual ones.

    Raises `ValueError` for no segments, a weight above the one before it, a count not above the one before it, or a
    segment but the last that ends at no count above 0.
    """

    segments: Sequence[WeightSegment]
    """Its pieces in order, the first from a count of none. The last runs on, whatever count it says it ends at."""

    def __post_init__(self) -> None:
        self._validate()

    def _validate(self) -> None:
        """Raise `ValueError` for segments that are what the class's docstring refuses."""
        segments = self.segments
        if not segments:
            raise ValueError("a weight curve has no segments")
        for segment in segments[:-1]:
            if not 0 < segment.end_count < math.inf:
                raise ValueError(
                    f"every segment of a weight curve but the last must end at a count above 0, not {segment.end_count}"
                )
        for position, (earlier, later) in enumerate(itertools.pairwise(segments), start=1):
            if later.weight > earlier.weight:
                raise ValueError(f"a weight curve's weights must not rise: {later.weight} after {earlier.weight}")
            if position < len(segments) - 1 and later.end_count <= earlier.end_count:
                raise ValueError(f"a weight curve's counts must rise: {later.end_count} after {earlier.end_count}")

    @classmethod
    def constant(cls, weight: float) -> Self:
        """Every unit worth `weight` a second, however many there are."""
        return cls([WeightSegment(weight=weight)])

    @classmethod
    def up_to(cls, weight: float, end_count: float) -> Self:
        """Each unit up to `end_count` worth `weight` a second, and none past it anything: a count the bot wants,
        which a plan may fall short of. With an infinite `end_count` it is `constant`."""
        if math.isinf(end_count):
            return cls.constant(weight)
        return cls([WeightSegment(weight=weight, end_count=end_count), WeightSegment(weight=0.0)])


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class RewardTerm:
    """One part of what a plan is worth: the units of a group counted together, each for what the group says, are
    worth what a `WeightCurve`, `weights`, gives their count a second, from the game time `start` to `end`. Of several
    `groups` the curve is given the smallest count, so that the term is worth only what its scarcest group allows:
    units worth something in combination, as medivacs and the infantry they heal.

    Every part of a reward on units is one of these, and `weight`, `curve` and `combination` build the usual ones.
    The terms of a reward add up, each with its own curve and times, so a unit type may count in several: for what it
    is worth by itself, with others towards a count the bot wants of them together, and for something else from a game
    time on, where one term ends and another starts. A `time_factor` makes a term worth more or less as the game goes
    on, its whole curve times what the function gives for a game time, and a `count_factor` makes it want more or
    fewer units, the counts its curve's pieces end at times what that function gives.

    A term with `requirements` is worth nothing until each of those upgrades is done: what marines are worth more with
    combat shield than without is such a term, beside the one for what they are worth anyway. A plan starts such an
    upgrade whole over all its horizon, whatever the planner's `fluid_after` says, since a share of one would count for
    far more than that share of the term's units.

    Raises `ValueError` for no group, an `end` not later than `start`, or a negative weight in a term of several groups
    or with requirements. What `time_factor` and `count_factor` give is only seen where a plan is made: see
    `time_factor_at` and `count_factor_at`.
    """

    groups: Sequence[Mapping[UnitTypeId, float]]
    """The units counted, a group to a count: how much one unit of each type counts in it. Most terms have one."""
    weights: WeightCurve
    """What the count is worth a second: each further unit of it for less, the more there are."""
    start: GameTime = GameTime.ZERO
    """The game time it is worth something from: the game's start by default."""
    end: GameTime = GameTime.INFINITE
    """The game time it is worth something up to, and nothing at: none by default."""
    time_factor: Callable[[GameTime], float] | None = None
    """What the curve is multiplied by at a game time between `start` and `end`, never negative; `None` for 1
    throughout. A plan asks it at the end of each of its intervals, so it misses what happens within one. A
    `TimeDependent` passes as one, a `TimeCurve` for one."""
    count_factor: Callable[[GameTime], float] | None = None
    """What every `end_count` of the curve is multiplied by at a game time between `start` and `end`, never negative,
    each unit's weight staying what it is; `None` for 1 throughout. It changes nothing in a curve of one piece, which
    ends at no count. A plan asks it as it does `time_factor`."""
    requirements: frozenset[UpgradeId] = frozenset()
    """The upgrades that must be done for the term to be worth anything."""

    def __post_init__(self) -> None:
        self._validate()

    def _validate(self) -> None:
        """Raise `ValueError` for a field that holds what the class's docstring refuses."""
        if not self.groups:
            raise ValueError("a term must count a group of units at least")
        if self.end <= self.start:
            raise ValueError(f"a term must end later than it starts, not at {self.end} from {self.start}")
        segments = self.weights.segments
        if any(segment.weight < 0 for segment in segments):
            if len(self.groups) > 1:
                raise ValueError(f"a term of several groups may not hold a negative weight: {segments}")
            if self.requirements:
                raise ValueError(f"a term with requirements may not hold a negative weight: {segments}")

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
        count_factor: Callable[[GameTime], float] | None = None,
        requirements: Iterable[UpgradeId] = (),
    ) -> Self:
        """Each unit of `units` is worth `weight` a second, however many there are: of one unit type, or of several
        counted together, one unit of each for what the mapping says. With an `end_count`, each unit up to that count
        is, and none past it anything: a count the bot wants, which a plan may fall short of. From the game time
        `start`, the game's start by default, to `end`, and nothing before or from then, times what `time_factor`
        gives for a game time between them, and with `end_count` times what `count_factor` gives for it; and only once
        every upgrade of `requirements` is done."""
        return cls.curve(
            units,
            WeightCurve.up_to(weight, end_count),
            start=start,
            end=end,
            time_factor=time_factor,
            count_factor=count_factor,
            requirements=requirements,
        )

    @classmethod
    def curve(
        cls,
        units: UnitTypeId | Mapping[UnitTypeId, float],
        weights: WeightCurve,
        *,
        start: GameTime = GameTime.ZERO,
        end: GameTime = GameTime.INFINITE,
        time_factor: Callable[[GameTime], float] | None = None,
        count_factor: Callable[[GameTime], float] | None = None,
        requirements: Iterable[UpgradeId] = (),
    ) -> Self:
        """The units of `units` are worth what `weights` give their count a second, more of them less each: of one
        unit type, or of several counted together, one unit of each for what the mapping says. From the game time
        `start`, the game's start by default, to `end`, and nothing before or from then, times what `time_factor`
        gives for a game time between them, and with the counts its pieces end at times what `count_factor` gives for
        it; and only once every upgrade of `requirements` is done."""
        counted = {units: 1.0} if isinstance(units, UnitTypeId) else units
        return cls(
            groups=[counted],
            weights=weights,
            start=start,
            end=end,
            time_factor=time_factor,
            count_factor=count_factor,
            requirements=frozenset(requirements),
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
        count_factor: Callable[[GameTime], float] | None = None,
        requirements: Iterable[UpgradeId] = (),
    ) -> Self:
        """Units worth something in combination: each group of `groups` is counted, one unit of each type for what
        the group says, and each unit of the smallest of those counts is worth `weight` a second, so that the
        scarcest group limits what the others are worth. `[{MEDIVAC: 8}, {MARINE: 1, MARAUDER: 2}]` with 0.3 is 0.3
        for each marine a medivac heals, of which it heals 8, a marauder for two: 24 marines with 2 medivacs are
        worth 0.3 times 16, and a third medivac makes it 24. What each unit is worth without the others is another
        term's.

        With an `end_count`, no unit past that count is worth anything. From the game time `start`, the game's start
        by default, to `end`, and nothing before or from then, times what `time_factor` gives for a game time between
        them, and with `end_count` times what `count_factor` gives for it; and only once every upgrade of `requirements`
        is done. Raises `ValueError` for a negative `weight` with several groups."""
        return cls(
            groups=groups,
            weights=WeightCurve.up_to(weight, end_count),
            start=start,
            end=end,
            time_factor=time_factor,
            count_factor=count_factor,
            requirements=frozenset(requirements),
        )

    @property
    def unit_types(self) -> frozenset[UnitTypeId]:
        """Every unit type its groups count."""
        return frozenset(unit_type for group in self.groups for unit_type in group)

    def time_factor_at(self, time: GameTime) -> float:
        """What its curve is multiplied by at `time`: nothing before its `start` and from its `end`, and between them
        what its `time_factor` gives, 1 without one.

        Raises `ValueError` for a factor that is negative or not finite."""
        if not self.start <= time < self.end:
            return 0.0
        return _factor_at(self.time_factor, time, "time")

    def count_factor_at(self, time: GameTime) -> float:
        """What the counts its curve's pieces end at are multiplied by at `time`: what its `count_factor` gives, 1
        without one.

        Raises `ValueError` for a factor that is negative or not finite."""
        return _factor_at(self.count_factor, time, "count")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Reward:
    """What a plan is worth, summed over its horizon a second at a time: the units the plan expects alive, each second
    worth what its `terms` give their counts, and the upgrades it expects done, each second discounted by `half_life`.
    Only what is expected to survive counts, by the loss rates handed to `Planner.plan_now`.

    `terminal_time` counts the state at the horizon for that much longer, as if it lasted, discounted alike. The stock
    left at the horizon is worth `resource_weight` a mineral and a vespene besides.

    Raises `ValueError` for a `half_life` not longer than 0, or a negative `terminal_time` or `resource_weight`.
    """

    terms: Sequence[RewardTerm] = ()
    """What the units are worth, each term added to the others."""
    upgrade_weights: Mapping[UpgradeId, float] = field(default_factory=dict)
    """What each upgrade done is worth a second."""
    half_life: GameTime | None = GameTime(minutes=3)
    """The time over which a second's worth halves, from the plan's start: by default 3 minutes, so that a unit 3
    minutes into the plan counts half of what one counts now. `None` for no discount, every second of the horizon
    counting alike. A shorter one makes a plan build its army sooner and less economy first."""
    terminal_time: GameTime = GameTime.ZERO
    """How much longer the state at the horizon counts."""
    resource_weight: float = 1e-4
    """What each mineral and each vespene left in stock at the horizon is worth, once and not discounted: by default
    0.0001, far less than what it could have bought under weights around 1, so that it decides only between plans
    worth the same otherwise, and a reward of much smaller weights wants it smaller in proportion. With it, a plan's
    workers bring in what they can where it buys nothing more. With 0 they may stand idle there, and a plan may count
    starts of mining no worker makes, which cost it nothing, so that its stocks read less than its workers would bring
    in."""

    def __post_init__(self) -> None:
        self._validate()

    def _validate(self) -> None:
        """Raise `ValueError` for a field that holds what the class's docstring refuses."""
        if self.half_life is not None and self.half_life <= GameTime.ZERO:
            raise ValueError(f"a half-life must be longer than 0, not {self.half_life}")
        if self.terminal_time < GameTime.ZERO:
            raise ValueError(f"terminal_time must not be negative, not {self.terminal_time}")
        if self.resource_weight < 0:
            raise ValueError(f"resource_weight must not be negative, not {self.resource_weight}")

    @classmethod
    def weighted(
        cls,
        unit_weights: Mapping[UnitTypeId, float | WeightCurve],
        *,
        terms: Sequence[RewardTerm] = (),
        upgrade_weights: Mapping[UpgradeId, float] | None = None,
        half_life: GameTime | None = GameTime(minutes=3),
        terminal_time: GameTime = GameTime.ZERO,
        resource_weight: float = 1e-4,
    ) -> Self:
        """A reward of a term for each unit type of `unit_weights`, at any time: one number for what each unit of it is
        worth a second, or a `WeightCurve` where more of them are worth less each. `terms` come after them, and the rest
        is the reward's own."""
        terms_of_unit_weights = [
            RewardTerm.curve(unit_type, weight)
            if isinstance(weight, WeightCurve)
            else RewardTerm.weight(unit_type, float(weight))
            for unit_type, weight in unit_weights.items()
        ]
        return cls(
            terms=(*terms_of_unit_weights, *terms),
            upgrade_weights={} if upgrade_weights is None else upgrade_weights,
            half_life=half_life,
            terminal_time=terminal_time,
            resource_weight=resource_weight,
        )

    @property
    def unit_types(self) -> frozenset[UnitTypeId]:
        """Every unit type it values: those its terms count."""
        return frozenset[UnitTypeId]().union(*(term.unit_types for term in self.terms))

    @property
    def upgrades(self) -> frozenset[UpgradeId]:
        """Every upgrade it values: those weighted, and those its terms require."""
        return frozenset(self.upgrade_weights).union(*(term.requirements for term in self.terms))


def _factor_at(factor_of: Callable[[GameTime], float] | None, time: GameTime, name: str) -> float:
    """What `factor_of` gives for `time`, 1 if it is `None`; raises `ValueError` unless it is a number not below 0,
    naming the factor `name`."""
    if factor_of is None:
        return 1.0
    factor = factor_of(time)
    if not 0 <= factor < math.inf:
        raise ValueError(f"a term's {name} factor must be a number not below 0, not {factor} at {time}")
    return factor
