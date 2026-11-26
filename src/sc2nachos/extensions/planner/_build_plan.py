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
    from sc2nachos.extensions.planner._milp._solver import Solver
    from sc2nachos.extensions.planner._planner_state import PlannerState


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class PlanItem:
    """One item of a plan: what to make or research, when the planner expects it to start, and how long it takes."""

    product: UnitTypeId | UpgradeId
    """What to make: the unit type, or the upgrade to research."""
    start: GameTime
    """The game time the planner expects it to start at: the start of an interval of its grid."""
    duration: GameTime
    """How long it takes from its start: its build time or its research time, from the game's data."""
    is_fluid: bool
    """Whether the planner planned it in fractions rather than whole, as it does far ahead. The plan then spreads its
    cost and its count over several intervals, and lists it once at `start`, where those fractions add up to one more
    item, rounded."""

    @property
    def end(self) -> GameTime:
        """The game time the planner expects it done at: `start` and `duration`. The plan itself counts it from the
        first edge of its grid at or after that."""
        return self.start + self.duration

    def to_string(self) -> str:
        """Its start as minutes and seconds and what it makes, for a log or an overlay: `"3:12 SUPPLY_DEPOT"`, and
        `"5:40 MARINE (fluid)"` for one planned in fractions."""
        return f"{self.start.to_string(0)} {self.product.name}{' (fluid)' if self.is_fluid else ''}"


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class PlanMoment:
    """What the planner expects at one game time. A plan holds one for each edge of its grid: the first is the state
    it planned from, and the last is at the horizon."""

    time: GameTime
    """The game time."""
    resources: Resources
    """The minerals and vespene expected in the bank at `time`, before what starts at `time` is paid for."""
    collected: Resources
    """The minerals and vespene expected collected from the game's start to `time`, as the game's score counts them,
    whether spent or not. It is what the state the plan was made from had collected, plus what the plan expects its
    workers and MULEs to bring in from then to `time`."""
    units: Mapping[UnitTypeId, float]
    """How many finished units of each type are expected alive at `time`. A unit in production is not counted until it
    is done. A number is a fraction where the plan starts units in fractions or a loss rate applies. It holds every
    unit type the planner considered, and every type the state held or had in production, at 0 where none is
    expected."""
    upgrades: Mapping[UpgradeId, float]
    """How much of each upgrade is expected done at `time`: 1 for one that is done, 0 for one that is not, and a share
    between them where the plan researches an upgrade in fractions, as it does far ahead. An upgrade being researched
    counts as 0 until it is done. It holds every upgrade the planner considered, and every one the state had done or
    was researching."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class PlanInterval:
    """One interval of a plan's grid: what the planner expects at its start and at its end, and how many workers the
    plan has mining and collecting in between. Its `end` is the next interval's `start`, the same object."""

    start: PlanMoment
    """What the planner expects when it starts: at the first interval's, the state the plan was made from."""
    end: PlanMoment
    """What the planner expects when it ends: at the last interval's, the plan's horizon."""
    miners: float
    """The workers the plan has at mineral fields in it."""
    collectors: float
    """The workers the plan has at gas buildings in it. A bot keeps as many of its own collecting vespene: now, the
    first interval's."""

    @property
    def duration(self) -> GameTime:
        """How long the interval is."""
        return self.end.time - self.start.time


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class PlanDeviation:
    """How a game differs at one game time from what a plan expected of it then: the game's number less the plan's, so
    that the plan was optimistic where one is negative and pessimistic where one is positive.

    Read together, they say where a plan was off. Less `collected` than expected is the economy's: the workers
    brought in less than `EconomyModel` has them bring. As much collected and more in `resources` is the bot's, which
    started less than the plan had it start. As much of both and fewer `units` is units lost, which `loss_rates` are
    for. Less of an upgrade is a research the bot started late or not at all.
    """

    time: GameTime
    """The game time compared at."""
    resources: Resources
    """The minerals and vespene in the bank, the game's less the plan's."""
    collected: Resources
    """The minerals and vespene collected from the game's start to `time`, the game's less the plan's."""
    units: Mapping[UnitTypeId, float]
    """The number of each unit type done and alive, the game's less the plan's: every type either counts."""
    upgrades: Mapping[UpgradeId, float]
    """How much of each upgrade is done, the game's less the plan's: the game's is 1 for one done and 0 for any other,
    and the plan's the share it expected done. It holds every upgrade either counts."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class SolveReport:
    """What the solver says of the solve that found a plan."""

    gap: float
    """The solver's bound on how far the plan's `reward` may fall short of the best plan's, relative to it."""
    is_optimal: bool
    """Whether the solver proved the plan the best, to the `relative_gap` its `plan_now` was given, rather than stopping
    at a limit."""
    seconds: float
    """The wall-clock seconds the solve took."""
    solver: Solver
    """The solver that found the plan: `Solver.HIGHS` where the planner was built with `Solver.GUROBI` but Gurobi
    refused the program, as one larger than its licence allows."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class BuildPlan:
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

    time: GameTime
    """The game time of the state it was planned from, which is when it starts."""
    items: tuple[PlanItem, ...]
    """What to make, in the order the planner expects each to start; of those expected at one time, in the order the
    game's data lists their types, then the upgrades."""
    intervals: tuple[PlanInterval, ...]
    """The intervals of its grid in order, the first from `time` and the last to the horizon."""
    reward: float
    """What the plan is worth by the `Reward` it was planned for: what the solver made as large as it could."""
    solve_report: SolveReport
    """What the solver says of the solve: how near the best the plan is proven, and how long it took."""

    def to_string(self) -> str:
        """The plan as text, for a log: a line that says from when to when it runs, what it is worth and how the solve
        ended, a line for each item as `PlanItem.to_string` writes it, and a line with the units expected at the
        horizon, those of none left out.

        ```
        plan from 0:00 to 5:00, reward 2981.9, proven the best in 1.62 s
        0:00 SCV
        0:07 SUPPLY_DEPOT
        4:38 MARINE (fluid)
        at 5:00: 31.4 SCV, 3 SUPPLY_DEPOT, 22.5 MARINE
        ```

        A plan the solver did not prove the best reads `within 1.2% of the best after 3.00 s`, by the solver's gap.
        """
        report = self.solve_report
        solve = (
            f"proven the best in {report.seconds:.2f} s"
            if report.is_optimal
            else f"within {report.gap:.1%} of the best after {report.seconds:.2f} s"
        )
        if not self.intervals:
            return f"plan from {self.time.to_string(0)}, reward {self.reward:.1f}, {solve}"
        horizon = self.intervals[-1].end
        units = ", ".join(
            f"{count:.3g} {unit_type.name}" for unit_type, count in horizon.units.items() if count >= 0.05
        )
        return "\n".join(
            (
                f"plan from {self.time.to_string(0)} to {horizon.time.to_string(0)}, reward {self.reward:.1f}, {solve}",
                *(item.to_string() for item in self.items),
                f"at {horizon.time.to_string(0)}: {units}",
            )
        )

    def interval_at(self, time: GameTime) -> PlanInterval | None:
        """The interval `time` is in, from its start to before its end, or `None` for a time before the plan starts or
        not before the horizon. It holds the workers the plan has at `time`, and what the plan expects at its start and
        at its end; between the two the plan says nothing of the stock, the units or the upgrades."""
        position = bisect_right(self.intervals, time, key=lambda interval: interval.start.time) - 1
        if position < 0 or time >= self.intervals[position].end.time:
            return None
        return self.intervals[position]

    def moment_at(self, time: GameTime) -> PlanMoment | None:
        """What the plan expects at `time`, or `None` for a time before the plan starts or after the horizon. At an edge
        of the grid it is that interval's `start` or `end`. Within an interval it is what the planner's model has there,
        which jumps at the edges: the units and the upgrades of the interval's start, a unit counting as done at an
        edge; what is collected by then at the interval's income, which is even over it; and the stock with all that
        starts in the interval paid at its start, the end's stock less the income still to come. That stock is less than
        nothing just after an interval's start where what starts is paid from the interval's income, the planner's
        `spendable_income_fraction` of it: by as much as the plan has spent of what is yet to come in. A bot that starts
        each item as soon as it can pay still saves up then, and holds what this reads from that fraction of the
        interval on."""
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

    def deviation_of(self, state: PlannerState) -> PlanDeviation | None:
        """How `state`, a later state of the game the plan was made in, differs from what the plan expected at its
        time, by `moment_at`: the state's numbers less the plan's. `None` for a state from before the plan starts or
        after the horizon."""
        expected = self.moment_at(state.time)
        if expected is None:
            return None
        unit_types = sorted(expected.units.keys() | state.units.keys())
        upgrades = sorted(expected.upgrades.keys() | state.upgrades)
        return PlanDeviation(
            time=state.time,
            resources=state.resources - expected.resources,
            collected=state.collected - expected.collected,
            units={
                unit_type: state.units.get(unit_type, 0) - expected.units.get(unit_type, 0.0)
                for unit_type in unit_types
            },
            upgrades={
                upgrade: float(upgrade in state.upgrades) - expected.upgrades.get(upgrade, 0.0) for upgrade in upgrades
            },
        )
