"""The mixed-integer linear program of one plan: from the game's state, the economy and the reward, to the plan."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, final

import numpy as np

from sc2nachos.extensions.planner._build_plan import BuildPlan, PlanInterval, PlanItem, PlanMoment, SolveReport
from sc2nachos.extensions.planner._milp._linear_program import FloatArray, LinearProgram, RowExpression
from sc2nachos.extensions.planner._milp._solver import Solver
from sc2nachos.extensions.planner._plan_constraints import PlanConstraints
from sc2nachos.extensions.planner._plan_variables import NewBase
from sc2nachos.extensions.planner._terran_model import SUPPLY_LIMIT
from sc2nachos.extensions.planner._time_grid import on_a_whole_step
from sc2nachos.gamedata import Resources
from sc2nachos.gametime import GameTime
from sc2nachos.ids import UnitTypeId, UpgradeId

if TYPE_CHECKING:
    from sc2nachos.extensions.planner._economy_model import EconomyModel
    from sc2nachos.extensions.planner._planner_state import PlannerState
    from sc2nachos.extensions.planner._reward import Reward, RewardTerm, WeightSegment
    from sc2nachos.extensions.planner._terran_model import ProductionItem, TerranModel
    from sc2nachos.gametime import TimeDependent


@final
class PlanProgram(PlanConstraints):
    """The mixed-integer linear program of one plan over the intervals between `edges`, in seconds from `state`'s time.

    Each item's starts so far by each interval's end are a variable, and an interval's starts those so far less those of
    the interval before; both are whole where the interval starts before the item's time in `fluid_after_seconds`, one
    for each item in the order of `items`, and throughout for an upgrade a term of the reward requires. A start pays
    from the stock at its interval's start plus `spendable_income_fraction` of the interval's income, which rounds it to
    the nearer edge of the grid at a half, keeps its producer busy for its build time over the intervals it spans, its
    SCV from the walk there through the build to the walk back, takes its supply at once, and counts as done from the
    first edge at or after its end. A whole start, or a build under way, that runs into an interval by less than
    `max_ignored_overrun` of it leaves the interval free. From a `state` with more supply taken than its cap, nothing
    that takes supply starts until the cap is at least what is taken with it. Nothing starts before `unlock_fraction` of
    a structure granting each tech it needs, and of each upgrade, is done; with 0, in the share that the fractions done
    of them allow. Workers mine, collect or build, pooled over every field and refinery, in the tiers of `economy`'s
    rates, and each orbital command adds its MULEs' income. A worker more mining or collecting in a tier than the
    interval before, or than `state` has, brings in nothing for `economy`'s `initial_harvest_delay` from the interval's
    start, but a builder back from its walk. A unit counts as it survives its loss rate in `loss_rates`, for income,
    supply and the reward: a unit type's rate is that of the type it counts as, the highest of those of one type, and a
    number or a `TimeDependent`, read at each game step and summed, so that a peak in it loses its share whatever the
    grid. Of an item in `start_limits` at most that many start over the plan: a unit type's limit is that of the type it
    counts as, the lowest of those of one type, and a limit on no item here does nothing. The plan starts at most as
    many command centers as `new_bases` holds, each bringing the fields and geysers of the next of them once done, and
    one in production brings what `state` says of it.

    Raises `ValueError` unless `fluid_after_seconds` hold one time for each item, for a negative walk from `economy`'s
    `builder_walk_time`, for a loss rate from a `TimeDependent` that is negative or not finite, and for a unit in
    production in `state` of a type that is no item of `model`'s; `TypeError` for a loss rate that is neither a number
    nor gives one for each of many game steps, as a function of one game time does not; and `IndexError` from a loss
    rate that does not cover a step of the plan.
    """

    __slots__ = ("_discounts", "_objective")

    def __init__(
        self,
        *,
        items: Sequence[ProductionItem],
        model: TerranModel,
        edges: Sequence[float],
        state: PlannerState,
        reward: Reward,
        economy: EconomyModel,
        loss_rates: Mapping[UnitTypeId, float | TimeDependent[float]],
        start_limits: Mapping[UnitTypeId | UpgradeId, int],
        new_bases: Sequence[NewBase],
        fluid_after_seconds: Sequence[float],
        max_ignored_overrun: float,
        unlock_fraction: float,
        spendable_income_fraction: float,
    ) -> None:
        super().__init__(
            items=items,
            model=model,
            edges=edges,
            state=state,
            reward=reward,
            economy=economy,
            loss_rates=loss_rates,
            start_limits=start_limits,
            new_bases=new_bases,
            fluid_after_seconds=fluid_after_seconds,
            max_ignored_overrun=max_ignored_overrun,
            unlock_fraction=unlock_fraction,
            spendable_income_fraction=spendable_income_fraction,
        )
        self._discounts = self._discounts_of(reward)
        self._add_constraints()
        self._objective = self._reward_expression()

    @property
    def linear_program(self) -> LinearProgram:
        """The program, to count its variables and rows."""
        return self._program

    def solve(
        self, *, solver: Solver, time_limit: float | None, node_limit: int | None, relative_gap: float
    ) -> BuildPlan | None:
        """The best plan `solver` finds within `time_limit` seconds and `node_limit` nodes, proven within
        `relative_gap` of the best where it gets that far, or `None` if it finds none."""
        solution = self._program.maximize(
            self._objective, solver=solver, time_limit=time_limit, node_limit=node_limit, relative_gap=relative_gap
        )
        if solution.values is None:
            return None
        return BuildPlan(
            time=self._state.time,
            items=self._planned_items(solution.values),
            intervals=self._planned_intervals(solution.values),
            reward=solution.objective,
            solve_report=SolveReport(
                gap=solution.gap, is_optimal=solution.is_optimal, seconds=solution.seconds, solver=solution.solver
            ),
        )

    def _discounts_of(self, reward: Reward) -> FloatArray:
        """What each interval's seconds count for, discounted from the plan's start, the last interval's with what the
        state at the horizon counts for after it."""
        horizon = float(self._edges[-1])
        tail = reward.terminal_time.seconds
        if reward.half_life is None:
            discounts = self._widths.copy()
            discounts[-1] += tail
            return discounts
        decay = math.log(2) / reward.half_life.seconds
        discounts = (np.exp(-decay * self._edges[:-1]) - np.exp(-decay * self._edges[1:])) / decay
        discounts[-1] += (math.exp(-decay * horizon) - math.exp(-decay * (horizon + tail))) / decay
        return discounts

    def _reward_expression(self) -> RowExpression:
        """The reward of a plan: each interval's worth, discounted, and the stock left at the horizon."""
        reward = self._reward
        worth = RowExpression(self._interval_count)
        for term in reward.terms:
            worth.add_expression(self._worth_of(term))
        for upgrade, weight in reward.upgrade_weights.items():
            worth.add_expression(self._upgrade_done(upgrade, at_end=True), weight)
        objective = worth.summed(self._discounts)
        if reward.resource_weight:
            at_the_horizon = np.eye(1, self._interval_count + 1, k=self._interval_count) * reward.resource_weight
            objective.add(self._minerals, at_the_horizon).add(self._vespene, at_the_horizon)
        return objective

    def _worth_of(self, term: RewardTerm) -> RowExpression:
        """What `term`'s units are worth a second at each interval's end, its curve times the term's time factor
        then, its pieces ending at their counts times its count factor then, nothing where the time factor is 0: their
        count times the weight where it has one group and one piece, which runs on, and else a variable an interval of
        a time factor above 0 under each piece's line of each group's count, which the reward raises to the lowest.
        That is the curve of the smallest count, since no piece of a term of several groups falls."""
        intervals = self._interval_count
        ends = [self._time_after(edge) for edge in self._edges[1:].tolist()]
        factors = np.array([term.time_factor_at(time) for time in ends])
        counted = factors > 0
        worth = RowExpression(intervals)
        if not counted.any():
            return worth
        count_factors = np.array(
            [term.count_factor_at(time) if is_counted else 1.0 for time, is_counted in zip(ends, counted, strict=True)]
        )
        counts = [self._count_of(group) for group in term.groups]
        if len(counts) == 1 and len(term.weights.segments) == 1 and not term.requirements:
            return worth.add_expression(counts[0], factors * term.weights.segments[0].weight)
        values = self._program.add_variables(int(counted.sum()), lower=-math.inf)
        worth.add(values, np.eye(intervals)[:, counted])
        worth_before = np.zeros(intervals)
        count_before = np.zeros(intervals)
        for segment in term.weights.segments:
            for count in counts:
                under_line = RowExpression(intervals).add_expression(worth)
                under_line.add_expression(count, -segment.weight * factors)
                at_most = (worth_before - segment.weight * count_before) * factors
                self._program.constrain(under_line.selected(counted), upper=at_most[counted])
            if math.isfinite(segment.end_count):
                ends_at = segment.end_count * count_factors
                worth_before = worth_before + segment.weight * (ends_at - count_before)
                count_before = ends_at
        if term.requirements:
            most_counted = self._most_counted_by(term)
            most_worth = np.array(
                [
                    _worth_at(term.weights.segments, most_counted, count_factor=factor)
                    for factor in count_factors.tolist()
                ]
            )
            for upgrade in sorted(term.requirements):
                not_done = RowExpression(intervals).add_expression(worth)
                not_done.add_expression(self._upgrade_done(upgrade, at_end=True), -most_worth * factors)
                self._program.constrain(not_done.selected(counted), upper=0.0)
        return worth

    def _count_of(self, group: Mapping[UnitTypeId, float]) -> RowExpression:
        """How many units `group` counts alive at each interval's end, each for what the group says."""
        count = RowExpression(self._interval_count)
        for unit_type, counts_for in group.items():
            count.add_expression(self._count_alive(self._model.counted_as(unit_type), at_end=True), counts_for)
        return count

    def _most_counted_by(self, term: RewardTerm) -> float:
        """The highest count `term` can come to, the smallest of its groups': of each type a group counts for more
        than nothing, as many as the supply limit, or as the state has if those are more."""
        return min(
            sum(
                counts_for * max(SUPPLY_LIMIT, self._state.units.get(self._model.counted_as(unit_type), 0))
                for unit_type, counts_for in group.items()
                if counts_for > 0
            )
            for group in term.groups
        )

    def _time_after(self, seconds: float) -> GameTime:
        """The game time `seconds` after the plan's start, on a whole game step where it is meant for one."""
        return on_a_whole_step(self._state.time + GameTime(seconds=seconds))

    def _planned_items(self, values: FloatArray) -> tuple[PlanItem, ...]:
        """Each item started, in order of start, those of one interval in the order of the items: of fractional starts,
        one each time their sum reaches a whole number, rounded, and fluid where its interval's starts are."""
        planned: list[tuple[int, int, PlanItem]] = []
        for index, item in enumerate(self._items):
            made = item.made
            if made is None:
                continue
            duration = on_a_whole_step(GameTime(seconds=item.build_seconds))
            whole = np.floor(values[self._starts_so_far[index].to_slice()] + 0.5)
            for interval, count in enumerate(np.diff(whole, prepend=0.0)):
                start = self._time_after(float(self._edges[interval]))
                is_fluid = not self._whole[index][interval]
                entry = PlanItem(product=made, start=start, duration=duration, is_fluid=is_fluid)
                planned += [(interval, index, entry)] * int(count)
        return tuple(entry for _, _, entry in sorted(planned, key=lambda entry: (entry[0], entry[1])))

    def _planned_intervals(self, values: FloatArray) -> tuple[PlanInterval, ...]:
        """Each interval of the grid with the workers the plan has in it, from the moment at one edge to the moment at
        the next, which two intervals share."""
        moments = self._planned_moments(values)
        miners = self._working_in(self._mining).value_of(values)
        collectors = self._working_in(self._collecting).value_of(values)
        return tuple(
            PlanInterval(
                start=moments[interval],
                end=moments[interval + 1],
                miners=float(miners[interval]),
                collectors=float(collectors[interval]),
            )
            for interval in range(self._interval_count)
        )

    def _planned_moments(self, values: FloatArray) -> list[PlanMoment]:
        """What the plan expects at each edge of the grid: the state's at the first, and at each other what the
        interval before it comes to at its end."""
        minerals = values[self._minerals.to_slice()]
        vespene = values[self._vespene.to_slice()]
        state = self._state
        # What is collected by each edge: nothing more at the first, then each interval's income over its length.
        minerals_collected, vespene_collected = (
            np.concatenate([[0.0], np.cumsum(income.value_of(values) * self._widths)])
            for income in self._incomes_a_second()
        )
        in_production = {completion_type for completion_type, _ in self._completions}
        unit_types = sorted(self._index_of_unit_type.keys() | state.units.keys() | in_production)
        upgrades = sorted(state.upgrades | state.upgrade_completions.keys() | self._index_of_upgrade.keys())
        # A row to each edge: the first interval's start, then every interval's end.
        at_edges = {
            unit_type: np.concatenate(
                [
                    self._count_alive(unit_type, at_end=False).value_of(values)[:1],
                    self._count_alive(unit_type, at_end=True).value_of(values),
                ]
            )
            for unit_type in unit_types
        }
        done_at_edges = {
            upgrade: np.concatenate(
                [
                    self._upgrade_done(upgrade, at_end=False).value_of(values)[:1],
                    self._upgrade_done(upgrade, at_end=True).value_of(values),
                ]
            )
            for upgrade in upgrades
        }
        return [
            PlanMoment(
                time=self._time_after(seconds),
                resources=Resources(float(minerals[edge]), float(vespene[edge])),
                collected=state.collected + Resources(float(minerals_collected[edge]), float(vespene_collected[edge])),
                units={unit_type: float(counts[edge]) for unit_type, counts in at_edges.items()},
                upgrades={upgrade: float(done[edge]) for upgrade, done in done_at_edges.items()},
            )
            for edge, seconds in enumerate(self._edges.tolist())
        ]


def _worth_at(segments: Sequence[WeightSegment], count: float, *, count_factor: float) -> float:
    """What `segments` give `count` a second, each piece ending at its count times `count_factor`."""
    worth = 0.0
    count_before = 0.0
    for position, segment in enumerate(segments):
        is_last = position == len(segments) - 1
        ends_at = segment.end_count * count_factor if math.isfinite(segment.end_count) else math.inf
        count_within = count if is_last else min(count, ends_at)
        worth += segment.weight * max(count_within - count_before, 0.0)
        count_before = max(count_before, count_within)
    return worth
