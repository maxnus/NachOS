"""A plan: what to make, in order, and what the planner expects of it."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, final

from sc2nachos.gamedata import Resources
from sc2nachos.gametime import GameTime
from sc2nachos.ids import UnitTypeId, UpgradeId

if TYPE_CHECKING:
    from sc2nachos.extensions.planner._game_state import GameState


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class PlannedItem:
    """One item of a plan: what to make or research, and when the planner expects it to start."""

    item: UnitTypeId | UpgradeId
    """The unit type to make, or the upgrade to research."""
    start: GameTime
    """When the planner expects it to start, the game time of the first second of an interval of its grid."""
    is_fluid: bool
    """Whether it starts where its item's starts are fluid, planned in fractions: it then stands for the fractions
    whose sum reached a whole number at `start`, rounded, and the plan pays for and counts the fractions, not it."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class PlanMoment:
    """What the planner expects at one game time, an edge of its grid: the first is the state it planned from, and the
    last the horizon."""

    time: GameTime
    """The game time."""
    resources: Resources
    """The minerals and vespene expected in stock, before what starts at `time` is paid."""
    collected: Resources
    """The minerals and vespene expected collected in the game by then: what the state the plan was made from had
    collected, and what the plan's workers and MULEs bring in from it."""
    units: Mapping[UnitTypeId, float]
    """The number of each unit type the planner considered expected alive, done and survived: every type the plan
    makes, and those of the state and of its production."""
    upgrades: frozenset[UpgradeId]
    """The upgrades expected done, one whose start is fluid from half of it done."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class PlanInterval:
    """One interval of a plan's grid: the workers the plan has mining and collecting in it, from the moment it starts at
    to the one it ends at, which is the next interval's `start`, the same object."""

    start: PlanMoment
    """What the planner expects when it starts: at the first interval's, the state the plan was made from."""
    end: PlanMoment
    """What the planner expects when it ends: at the last interval's, the plan's horizon."""
    miners: float
    """The workers the plan has at mineral fields in it."""
    collectors: float
    """The workers the plan has at refineries in it: what a bot holds its own at, the first interval's now."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class PlanDeviation:
    """How a game differs at one game time from what a plan expected of it then: the game's number less the plan's, so
    that the plan was optimistic where one is negative and pessimistic where one is positive.

    Read together, the three say where a plan was off. Less `collected` than expected is the economy's: the workers
    brought in less than `Economy` has them bring. As much collected and more in `resources` is the bot's, which
    started less than the plan had it start. As much of both and fewer `units` is units lost, which `loss_rates` are
    for.
    """

    time: GameTime
    """The game time compared at."""
    collected: Resources
    """The minerals and vespene collected by then, the game's less the plan's."""
    resources: Resources
    """The minerals and vespene in stock, the game's less the plan's."""
    units: Mapping[UnitTypeId, float]
    """The number of each unit type done and alive, the game's less the plan's: every type either counts."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Plan:
    """What to make, in order, with when each is expected to start, and each interval of the planner's grid with what it
    expects of it: the workers mining and collecting in it, and at its start and its end the minerals and vespene in
    stock, how many of each unit type are alive, and the upgrades done.

    A bot carries it out in order, each item as soon as it can be afforded, rather than to the second: the times are the
    model's, which rounds each start to an interval's first second and plans far starts in fractions, each item of those
    starting once their sum reaches it, rounded, and marked `is_fluid`.

    Of plans worth the same to within the solver's gap, which one it is is the solver's choice. So it may start an item
    worth nothing to the reward, a unit that would not be done by the horizon, say, where nothing else needs what it
    costs.
    """

    made_at: GameTime
    """The game time of the state it was planned from."""
    items: tuple[PlannedItem, ...]
    """What to make, in the order the planner expects each to start; of those expected at one time, in the order the
    game's data lists their types, then the upgrades."""
    intervals: tuple[PlanInterval, ...]
    """The intervals of its grid in order, the first from `made_at` and the last to the horizon."""
    objective: float
    """What the reward makes of the plan."""
    gap: float
    """The solver's bound on how far `objective` may fall short of the best plan's, relative to it."""
    is_optimal: bool
    """Whether the solver proved it the best plan, to a relative gap of 0.0001, rather than stopping at a limit."""
    solve_seconds: float
    """The wall-clock seconds the solve took."""

    def interval_at(self, time: GameTime) -> PlanInterval | None:
        """The interval `time` is in, from its start to before its end, or `None` for a time before `made_at` or not
        before the horizon. It holds the workers the plan has at `time`, and what the plan expects at its start and at
        its end; between the two the plan says nothing of the stock, the units or the upgrades."""
        position = bisect_right(self.intervals, time, key=_start_of) - 1
        if position < 0 or time >= self.intervals[position].end.time:
            return None
        return self.intervals[position]

    def moment_at(self, time: GameTime) -> PlanMoment | None:
        """What the plan expects at `time`, or `None` for a time before `made_at` or after the horizon. At an edge of
        the grid it is that interval's `start` or `end`. Within an interval it is what the planner's model has there,
        which jumps at the edges: the units and the upgrades of the interval's start, a unit counting as done at an
        edge; what is collected by then at the interval's income, which is even over it; and the stock with all that
        starts in the interval paid at its start, the end's stock less the income still to come. That stock can read
        below nothing just after an interval's start, where the model lets what starts be paid from the interval's
        income."""
        if not self.intervals or time > self.intervals[-1].end.time:
            return None
        interval = self.interval_at(time)
        if interval is None:
            return self.intervals[-1].end if time == self.intervals[-1].end.time else None
        start, end = interval.start, interval.end
        if time == start.time:
            return start
        share_to_come = (end.time - time).seconds / (end.time - start.time).seconds
        income_to_come = (end.collected - start.collected) * share_to_come
        return PlanMoment(
            time=time,
            resources=end.resources - income_to_come,
            collected=end.collected - income_to_come,
            units=start.units,
            upgrades=start.upgrades,
        )

    def deviation_of(self, state: GameState) -> PlanDeviation | None:
        """How `state`, a later state of the game the plan was made in, differs from what the plan expected at its
        time, by `moment_at`: the state's numbers less the plan's. `None` for a state from before `made_at` or after
        the horizon."""
        expected = self.moment_at(state.time)
        if expected is None:
            return None
        unit_types = sorted(expected.units.keys() | state.units.keys())
        return PlanDeviation(
            time=state.time,
            collected=state.collected - expected.collected,
            resources=state.resources - expected.resources,
            units={
                unit_type: state.units.get(unit_type, 0) - expected.units.get(unit_type, 0.0)
                for unit_type in unit_types
            },
        )


def _start_of(interval: PlanInterval) -> GameTime:
    """When `interval` starts."""
    return interval.start.time
