"""The mixed-integer linear program of one plan: from the game's state, the economy and the reward, to the plan."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, final

import numpy as np
from numpy.typing import NDArray

from sc2nachos.extensions.planner._economy import rate_tiers, walk_seconds_of
from sc2nachos.extensions.planner._linear_program import FloatArray, LinearProgram, RowExpression, VariableBlock
from sc2nachos.extensions.planner._plan import Plan, PlanInterval, PlannedItem
from sc2nachos.extensions.planner._reward import count_at
from sc2nachos.extensions.planner._terran_model import SUPPLY_LIMIT
from sc2nachos.extensions.planner._time_grid import SECONDS_TOLERANCE
from sc2nachos.gametime import GameTime
from sc2nachos.ids import UnitTypeId, UpgradeId

if TYPE_CHECKING:
    from sc2nachos.extensions.planner._economy import Economy
    from sc2nachos.extensions.planner._game_state import GameState
    from sc2nachos.extensions.planner._reward import Coverage, CurveSegment, Floor, Reward
    from sc2nachos.extensions.planner._terran_model import Item, TerranModel

# The most an item without a tighter bound starts in one interval.
_MOST_STARTS_IN_AN_INTERVAL: Final = 100.0
# A start pays from the stock at its interval's start and this share of the interval's income.
_SHARE_OF_INCOME_SPENT: Final = 0.5


@final
@dataclass(frozen=True, slots=True)
class _WorkerTier:
    """One tier of the workers at mineral fields or refineries: how many work in it in each interval, how many of them a
    field or refinery takes, and what each brings in a second."""

    block: VariableBlock
    workers_per_site: int
    per_worker_second: float


@final
class PlanProgram:
    """The mixed-integer linear program of one plan over the intervals between `edges`, in seconds from `state`'s time.

    Each item's starts so far by each interval's end are a variable, and an interval's starts those so far less those of
    the interval before; both are whole where the interval starts before the item's time in `fluid_after_seconds`, one
    for each item in the order of `items`. A start pays from the stock at its interval's start plus half the interval's
    income, keeps its producer busy for its build time over the intervals it spans, its SCV from the walk there through
    the build to the walk back, takes its supply at once, and counts as done from the first edge at or after its end. A
    whole start, or a build under way, that runs into an interval by less than `max_ignored_overrun` of it leaves the
    interval free. Nothing starts before `unlock_fraction` of a structure granting each tech it needs, and of each
    upgrade, is done; with 0, in the share that the fractions done of them allow. Workers mine, collect or build, pooled
    over every field and refinery, in the tiers of `economy`'s rates, and each orbital command adds its MULEs' income. A
    worker more mining or collecting in a tier than the interval before, or than `state` has, brings in nothing for
    `economy`'s `reassignment_time` from the interval's start, but a builder back from its walk. A unit counts as it
    survives its loss rate in `loss_rates`, for income, supply, the reward and coverage. Of an item in `start_limits` at
    most that many start over the plan: a unit type's limit is that of the type it counts as, the lowest of those of one
    type, and a limit on no item here does nothing. Each item of `previous_plan` that the plan starts no later than that
    one expected, counting those of its kind before it, is worth `hysteresis_bonus` once, beside the reward.

    Raises `ValueError` unless `fluid_after_seconds` hold one time for each item, and for a negative walk from
    `economy`'s `walk_time`.
    """

    __slots__ = (
        "_builders_free_after",
        "_collecting",
        "_completion_edges",
        "_completions",
        "_discounts",
        "_economy",
        "_edges",
        "_hysteresis_bonus",
        "_index_of_unit_type",
        "_index_of_upgrade",
        "_items",
        "_loss_rates",
        "_max_ignored_overrun",
        "_minerals",
        "_mining",
        "_model",
        "_objective",
        "_previous_plan",
        "_program",
        "_reward",
        "_start_limits",
        "_started_collecting",
        "_started_mining",
        "_starts_so_far",
        "_state",
        "_vespene",
        "_walk_seconds",
        "_unlock_fraction",
        "_whole",
        "_widths",
    )

    def __init__(
        self,
        *,
        items: Sequence[Item],
        model: TerranModel,
        edges: Sequence[float],
        state: GameState,
        reward: Reward,
        economy: Economy,
        loss_rates: Mapping[UnitTypeId, float],
        start_limits: Mapping[UnitTypeId | UpgradeId, int],
        fluid_after_seconds: Sequence[float],
        max_ignored_overrun: float,
        unlock_fraction: float,
        previous_plan: Plan | None,
        hysteresis_bonus: float,
    ) -> None:
        if len(fluid_after_seconds) != len(items):
            raise ValueError(f"{len(fluid_after_seconds)} windows for {len(items)} items")
        self._items = tuple(items)
        self._model = model
        self._state = state
        self._reward = reward
        self._economy = economy
        self._loss_rates = {model.counted_as(unit_type): rate for unit_type, rate in loss_rates.items()}
        self._start_limits = _limits_by_counted_type(start_limits, model)
        self._max_ignored_overrun = max_ignored_overrun
        self._unlock_fraction = unlock_fraction
        self._previous_plan, self._hysteresis_bonus = previous_plan, hysteresis_bonus
        self._walk_seconds = walk_seconds_of(
            economy, (unit_type for unit_type, item in model.unit_items.items() if item.is_built_by_worker)
        )
        self._completions, self._builders_free_after = self._completions_and_builders()
        self._edges: FloatArray = np.asarray(edges, dtype=float)
        self._widths: FloatArray = np.diff(self._edges)
        self._index_of_unit_type = {
            item.unit_type: i for i, item in enumerate(self._items) if item.unit_type is not None
        }
        self._index_of_upgrade = {item.upgrade: i for i, item in enumerate(self._items) if item.upgrade is not None}
        self._program = LinearProgram()
        self._completion_edges = [self._edge_index(self._edges[:-1] + item.build_seconds) for item in self._items]
        self._whole = [self._edges[:-1] < fluid_after - SECONDS_TOLERANCE for fluid_after in fluid_after_seconds]
        self._starts_so_far: list[VariableBlock] = []
        for item, whole in zip(self._items, self._whole, strict=True):
            self._add_starts_so_far(item, whole)
        self._minerals, self._vespene = self._add_stock(state.minerals), self._add_stock(state.vespene)
        self._mining = self._add_worker_tiers(economy.minerals_per_miner_second)
        self._collecting = self._add_worker_tiers(economy.vespene_per_collector_second)
        self._started_mining, self._started_collecting = self._add_work_starts()
        self._discounts = self._discounts_of(reward)
        self._add_constraints()
        self._objective = self._reward_expression()

    @property
    def linear_program(self) -> LinearProgram:
        """The program, to count its variables and rows."""
        return self._program

    def solve(self, *, time_limit: float | None, node_limit: int | None) -> Plan | None:
        """The best plan the solver finds within `time_limit` seconds and `node_limit` nodes, or `None` if it finds
        none."""
        solution = self._program.maximize(self._objective, time_limit=time_limit, node_limit=node_limit)
        if solution.values is None:
            return None
        return Plan(
            made_at=self._state.time,
            items=self._planned_items(solution.values),
            intervals=self._planned_intervals(solution.values),
            objective=solution.objective,
            gap=solution.gap,
            is_optimal=solution.is_optimal,
            solve_seconds=solution.seconds,
        )

    @property
    def _interval_count(self) -> int:
        """How many intervals the grid has."""
        return len(self._widths)

    # The variables.

    def _completions_and_builders(self) -> tuple[tuple[tuple[UnitTypeId, float], ...], tuple[float, ...]]:
        """Each unit in production with the seconds until it is done, a structure an SCV builds after any walk there;
        and the seconds until each SCV building, or walking to build, is back at work after its walk back."""
        completions = [
            (completion.unit_type, completion.time_left.seconds) for completion in self._state.unit_completions
        ]
        builders_free_after: list[float] = []
        for build in self._state.worker_builds:
            walk = self._walk_seconds.get(build.unit_type, 0.0)
            done_after = build.time_left.seconds + (walk if build.builder_walks_there else 0.0)
            completions.append((build.unit_type, done_after))
            builders_free_after.append(done_after + walk)
        return tuple(completions), tuple(builders_free_after)

    def _add_starts_so_far(self, item: Item, whole: NDArray[np.bool_]) -> None:
        """Add the variables of the next item, `item`: its starts so far by each interval's end, whole in the intervals
        `whole` marks, which run from the first, so that each of those intervals' starts is whole too. An interval's
        starts are held between none and the most the interval takes: none before an SCV could walk there, and none of
        an upgrade done or being researched."""
        interval_starts = self._edges[:-1]
        most_in_interval = np.full(len(interval_starts), self._most_starts_in_an_interval(item))
        if item.unit_type is not None and item.is_built_by_worker:
            most_in_interval[interval_starts < self._walk_seconds.get(item.unit_type, 0.0) - SECONDS_TOLERANCE] = 0.0
        if item.upgrade is not None and (
            item.upgrade in self._state.upgrades or item.upgrade in self._state.upgrade_completions
        ):
            most_in_interval[:] = 0.0
        block = self._program.add_variables(len(interval_starts), upper=np.cumsum(most_in_interval), integral=whole)
        self._starts_so_far.append(block)
        self._program.constrain(
            self._starts_in_interval(len(self._starts_so_far) - 1), lower=0.0, upper=most_in_interval
        )

    def _add_stock(self, initial: float) -> VariableBlock:
        """The variables of a resource's stock at each edge, the first `initial`."""
        lower = np.zeros(self._interval_count + 1)
        upper = np.full(self._interval_count + 1, math.inf)
        lower[0] = upper[0] = initial
        return self._program.add_variables(self._interval_count + 1, lower=lower, upper=upper)

    def _add_worker_tiers(self, rates: Sequence[float]) -> tuple[_WorkerTier, ...]:
        """The variables of how many workers work in each tier of `rates`, each worker's at a field or refinery, in each
        interval."""
        return tuple(
            _WorkerTier(self._program.add_variables(self._interval_count), workers, rate)
            for workers, rate in rate_tiers(rates)
        )

    def _add_work_starts(self) -> tuple[tuple[VariableBlock, ...], tuple[VariableBlock, ...]]:
        """The variables of the workers that start to mine in each interval, a block for each mining tier, and of those
        that start to collect, one for each collecting tier; none where the economy's `reassignment_time` is 0."""
        if not self._economy.reassignment_time.seconds:
            return (), ()
        started_mining = tuple(self._program.add_variables(self._interval_count) for _ in self._mining)
        return started_mining, tuple(self._program.add_variables(self._interval_count) for _ in self._collecting)

    def _working_in(self, tiers: Sequence[_WorkerTier]) -> RowExpression:
        """The workers working in `tiers` in each interval."""
        working = RowExpression(self._interval_count)
        for tier in tiers:
            working.add(tier.block, np.eye(self._interval_count))
        return working

    def _most_starts_in_an_interval(self, item: Item) -> float:
        """The most of `item` that can start in one interval."""
        state = self._state
        new_townhalls = self._start_limits.get(UnitTypeId.COMMAND_CENTER, _MOST_STARTS_IN_AN_INTERVAL)
        townhalls_coming = new_townhalls + _count_of(self._completions, UnitTypeId.COMMAND_CENTER)
        if item.upgrade is not None:
            most = 1.0
        elif item.unit_type == UnitTypeId.REFINERY:
            most = float(state.free_geysers + self._economy.geysers_per_new_base * townhalls_coming)
        elif item.morphed_from is not None:
            most = float(state.counts.get(item.morphed_from, 0) + townhalls_coming)
        elif item.supply_used > 0:
            most = float(math.floor(SUPPLY_LIMIT / item.supply_used))
        else:
            most = _MOST_STARTS_IN_AN_INTERVAL
        return most if item.made is None else min(most, self._start_limits.get(item.made, math.inf))

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

    # Expressions over the intervals.

    def _edge_index(self, seconds: FloatArray) -> FloatArray:
        """The index of the first edge at or after each of `seconds`, past the last edge's index if after it."""
        return np.searchsorted(self._edges, seconds - SECONDS_TOLERANCE, side="left").astype(float)

    def _row_edges(self, *, at_end: bool) -> FloatArray:
        """The index of the edge each interval's row counts at: its start, or its end."""
        return np.arange(self._interval_count, dtype=float) + (1.0 if at_end else 0.0)

    def _add_starts_to(
        self, expression: RowExpression, index: int, per_start_in_interval: FloatArray | float
    ) -> RowExpression:
        """Add to `expression` the starts of item `index` in each interval, each times `per_start_in_interval`: a matrix
        of a row to each of the expression's rows and a column to each interval, or one number for every one of them;
        returns `expression`. An interval's starts being those so far less those of the interval before, the starts so
        far by an interval's end take the interval's column less the next one's."""
        per_start = np.broadcast_to(
            np.asarray(per_start_in_interval, dtype=float), (expression.rows, self._interval_count)
        )
        per_start_so_far = per_start.copy()
        per_start_so_far[:, :-1] -= per_start[:, 1:]
        return expression.add(self._starts_so_far[index], per_start_so_far)

    def _started_so_far(self, index: int) -> RowExpression:
        """The starts of item `index` in each interval and the intervals before."""
        return RowExpression(self._interval_count).add(self._starts_so_far[index], np.eye(self._interval_count))

    def _starts_in_interval(self, index: int) -> RowExpression:
        """The starts of item `index` in each interval."""
        return self._add_starts_to(RowExpression(self._interval_count), index, np.eye(self._interval_count))

    def _starts_over_the_plan(self, index: int) -> RowExpression:
        """One row: the starts of item `index` over the whole plan."""
        return self._add_starts_to(RowExpression(1), index, 1.0)

    def _starts_done(self, index: int, *, at_end: bool, rate: float = 0.0) -> RowExpression:
        """The starts of item `index` done by each interval's start, or its end, each as it survives `rate` a second."""
        rows = self._row_edges(at_end=at_end)
        done = (self._completion_edges[index][None, :] <= rows[:, None]).astype(float)
        if rate:
            completed = self._edges[:-1] + self._items[index].build_seconds
            since = np.maximum(self._edges[rows.astype(int)][:, None] - completed[None, :], 0.0)
            done *= np.exp(-rate * since)
        return self._add_starts_to(RowExpression(len(rows)), index, done)

    def _done_by(self, seconds: float, *, at_end: bool, rate: float = 0.0) -> FloatArray:
        """For each interval's start, or its end, whether something done after `seconds` is done, as it survives `rate`
        a second."""
        rows = self._row_edges(at_end=at_end)
        done = (self._edge_index(np.array([seconds]))[0] <= rows).astype(float)
        return done * np.exp(-rate * np.maximum(self._edges[rows.astype(int)] - seconds, 0.0))

    def _count_alive(self, unit_type: UnitTypeId, *, at_end: bool, survive: bool = True) -> RowExpression:
        """The units of `unit_type` alive at each interval's start, or its end: those alive now, those in production and
        those the plan starts, less those morphed into another type, each as it survives its loss rate if `survive`."""
        rate = self._loss_rates.get(unit_type, 0.0) if survive else 0.0
        rows = self._row_edges(at_end=at_end)
        count = RowExpression(len(rows))
        count.constant = self._state.counts.get(unit_type, 0) * np.exp(-rate * self._edges[rows.astype(int)])
        for completion_type, seconds in self._completions:
            if completion_type == unit_type:
                count.constant += self._done_by(seconds, at_end=at_end, rate=rate)
            elif self._model.unit_items[completion_type].morphed_from == unit_type:
                count.constant -= self._done_by(seconds, at_end=at_end)
        for index, item in enumerate(self._items):
            if item.unit_type == unit_type:
                count.add_expression(self._starts_done(index, at_end=at_end, rate=rate))
            elif item.morphed_from == unit_type:
                count.add_expression(self._starts_done(index, at_end=at_end), -1.0)
        return count

    def _made_since_start(self, unit_type: UnitTypeId) -> RowExpression:
        """The units of `unit_type` done since the plan's start by each interval's start, from production or the
        plan."""
        made = RowExpression(self._interval_count)
        for completion_type, seconds in self._completions:
            if completion_type == unit_type:
                made.constant += self._done_by(seconds, at_end=False)
        if (index := self._index_of_unit_type.get(unit_type)) is not None:
            made.add_expression(self._starts_done(index, at_end=False))
        return made

    def _upgrade_done(self, upgrade: UpgradeId, *, at_end: bool) -> RowExpression:
        """Whether `upgrade` is done by each interval's start, or its end."""
        done = RowExpression(self._interval_count)
        if upgrade in self._state.upgrades:
            done.constant += 1.0
        elif upgrade in self._state.upgrade_completions:
            done.constant += self._done_by(self._state.upgrade_completions[upgrade].seconds, at_end=at_end)
        elif (index := self._index_of_upgrade.get(upgrade)) is not None:
            done.add_expression(self._starts_done(index, at_end=at_end))
        return done

    def _busy_seconds(self, index: int, *, before: float = 0.0, after: float = 0.0) -> FloatArray:
        """The seconds of each interval, by row, that one start of item `index` in each interval, by column, keeps busy:
        from `before` seconds ahead of the start to `after` seconds past its end, less the overrun ignored of a whole
        start."""
        busy_from = self._edges[:-1][None, :] - before
        busy_until = self._edges[:-1][None, :] + self._items[index].build_seconds + after
        overlap_start = np.maximum(busy_from, self._edges[:-1][:, None])
        overlap_end = np.minimum(busy_until, self._edges[1:][:, None])
        busy = np.maximum(overlap_end - overlap_start, 0.0)
        began_before = self._edges[:-1][:, None] > busy_from + SECONDS_TOLERANCE
        ignored = self._is_overrun_ignored(busy, self._widths[:, None], began_before) & self._whole[index][None, :]
        return np.where(ignored, 0.0, busy)

    def _busy_seconds_until(self, end_seconds: float, *, start_seconds: float = 0.0) -> FloatArray:
        """The seconds of each interval within `start_seconds` to `end_seconds` from the plan's start, less the overrun
        ignored. What is busy from the plan's start began before it."""
        overlap_start = np.maximum(self._edges[:-1], start_seconds)
        busy = np.maximum(np.minimum(self._edges[1:], end_seconds) - overlap_start, 0.0)
        began_before = (self._edges[:-1] > start_seconds + SECONDS_TOLERANCE) | (start_seconds <= SECONDS_TOLERANCE)
        return np.where(self._is_overrun_ignored(busy, self._widths, began_before), 0.0, busy)

    def _is_overrun_ignored(
        self, busy: FloatArray, widths: FloatArray, began_before: NDArray[np.bool_]
    ) -> NDArray[np.bool_]:
        """Whether the `busy` seconds of each interval, `widths` long, are an overrun to ignore: of what `began_before`
        the interval and ends in it, less than `max_ignored_overrun` of the interval."""
        most_ignored = self._max_ignored_overrun * widths - SECONDS_TOLERANCE
        return began_before & (busy > SECONDS_TOLERANCE) & (busy < most_ignored)

    def _shorter_than_every_build_and_whole(self, indices: list[int]) -> NDArray[np.bool_]:
        """Whether each interval is no longer than every build of the items `indices`, and their starts in it are whole:
        where a build under way keeps its producer from a start in the interval, as a start sits on its first second."""
        shortest = min(self._items[index].build_seconds for index in indices)
        whole = np.logical_and.reduce([self._whole[index] for index in indices])
        return (self._widths <= shortest + SECONDS_TOLERANCE) & whole

    def _constrain_at_most(self, used: RowExpression, available: RowExpression) -> None:
        """Hold `used` at or below `available` in each row, and at or below none where what is now already uses more,
        so that a plan starting nothing more stays possible."""
        excess = RowExpression(used.rows).add_expression(used).add_expression(available, -1.0)
        limit = np.maximum(-excess.constant, 0.0)
        excess.constant = np.zeros(used.rows)
        self._program.constrain(excess, upper=limit)

    # The constraints.

    def _add_constraints(self) -> None:
        """Every constraint of the program."""
        self._constrain_resources()
        self._constrain_workers()
        self._constrain_supply()
        self._constrain_producers()
        self._constrain_counts()
        self._constrain_prerequisites()

    def _constrain_resources(self) -> None:
        """Each interval's stock of minerals and vespene: the stock before, plus what was mined, less what started; and
        what starts paid from the stock at the interval's start and half its income."""
        intervals = self._interval_count
        mineral_income = self._income_of(self._mining, self._started_mining)
        if mule_rate := self._economy.mule_minerals_per_orbital_second:
            mineral_income.add_expression(self._count_alive(UnitTypeId.ORBITAL_COMMAND, at_end=False), mule_rate)
        vespene_income = self._income_of(self._collecting, self._started_collecting)
        for stock, income, is_minerals in (
            (self._minerals, mineral_income, True),
            (self._vespene, vespene_income, False),
        ):
            spent = RowExpression(intervals)
            for index, item in enumerate(self._items):
                if cost := item.minerals if is_minerals else item.vespene:
                    spent.add_expression(self._starts_in_interval(index), cost)
            flow = RowExpression(intervals).add(
                stock, np.eye(intervals, intervals + 1, k=1) - np.eye(intervals, intervals + 1)
            )
            flow.add_expression(income, -self._widths).add_expression(spent)
            self._program.constrain(flow, lower=0.0, upper=0.0)
            paid = RowExpression(intervals).add(stock, np.eye(intervals, intervals + 1))
            paid.add_expression(income, _SHARE_OF_INCOME_SPENT * self._widths).add_expression(spent, -1.0)
            self._program.constrain(paid, lower=0.0)

    def _income_of(self, tiers: Sequence[_WorkerTier], started: Sequence[VariableBlock]) -> RowExpression:
        """What the workers in `tiers` bring in a second in each interval, less what those `started` in a tier do not
        yet: nothing for the economy's `reassignment_time` from the start of the interval they start in."""
        income = RowExpression(self._interval_count)
        for tier in tiers:
            income.add(tier.block, np.eye(self._interval_count) * tier.per_worker_second)
        for tier, started_in_tier in zip(tiers, started, strict=False):
            income.add(started_in_tier, -tier.per_worker_second * self._reassignment_shares())
        return income

    def _constrain_workers(self) -> None:
        """Miners, collectors and each builder's share of an interval at most the SCVs alive at its start; with no
        walks, the builders counted whole where an interval is shorter than every build; and the workers that start to
        mine or to collect held at what the miners and collectors rise by."""
        intervals = self._interval_count
        working = self._working_in((*self._mining, *self._collecting))
        builders = RowExpression(intervals)
        back_from_builds = RowExpression(intervals)
        built = [(index, item) for index, item in enumerate(self._items) if item.is_built_by_worker]
        for index, item in built:
            walk = self._walk_seconds_of(item)
            busy_share = self._busy_seconds(index, before=walk, after=walk) / self._widths[:, None]
            self._add_starts_to(builders, index, busy_share)
            self._add_starts_to(back_from_builds, index, _falls_of(busy_share, from_share=0.0))
        for free_after in self._builders_free_after:
            busy_share = self._busy_seconds_until(free_after) / self._widths
            builders.constant += busy_share
            back_from_builds.constant += _falls_of(busy_share[:, None], from_share=1.0)[:, 0]
        self._constrain_work_starts(back_from_builds)
        workers = self._count_alive(UnitTypeId.SCV, at_end=False)
        self._constrain_at_most(RowExpression(intervals).add_expression(working).add_expression(builders), workers)
        if not built or any(self._walk_seconds_of(item) for _, item in built):
            return
        short_and_whole = self._shorter_than_every_build_and_whole([index for index, _ in built])
        builders_whole = RowExpression(intervals).add_expression(working)
        for index, _ in built:
            is_busy = (self._busy_seconds(index) > SECONDS_TOLERANCE).astype(float)
            self._add_starts_to(builders_whole, index, is_busy)
        for free_after in self._builders_free_after:
            builders_whole.constant += self._busy_seconds_until(free_after) > SECONDS_TOLERANCE
        self._constrain_at_most(builders_whole.selected(short_and_whole), workers.selected(short_and_whole))

    def _constrain_work_starts(self, back_from_builds: RowExpression) -> None:
        """Hold the workers that start to mine or to collect in each interval at no fewer than those of each tier rise
        by: over what the interval before had left of them, as its SCVs survive, or over those of the state, where every
        SCV that neither collects nor builds mines. Of the miners, as many as are `back_from_builds` in the interval
        start for nothing: their walk back was the builder's."""
        if not self._started_collecting:
            return
        state = self._state
        collecting_now = float(state.vespene_collectors)
        building_now = len(self._builders_free_after)
        mining_now = max(state.counts.get(UnitTypeId.SCV, 0) - collecting_now - building_now, 0.0)
        self._hold_starts_at_rises(
            self._collecting, self._started_collecting, working_now=collecting_now, sites=state.refineries_with_vespene
        )
        self._hold_starts_at_rises(
            self._mining,
            self._started_mining,
            working_now=mining_now,
            sites=state.mineral_fields,
            back_from_builds=back_from_builds,
        )

    def _hold_starts_at_rises(
        self,
        tiers: Sequence[_WorkerTier],
        started: Sequence[VariableBlock],
        *,
        working_now: float,
        sites: int,
        back_from_builds: RowExpression | None = None,
    ) -> None:
        """Hold the workers `started` in each of `tiers` in each interval at no fewer than the tier rises by, from the
        `working_now` in them, as many in each tier as the `sites` now take, the better tiers first; less as many in
        all as are `back_from_builds`."""
        intervals = self._interval_count
        survival = np.exp(-self._loss_rates.get(UnitTypeId.SCV, 0.0) * self._widths)
        risen_by = np.eye(intervals) - np.eye(intervals, k=-1) * survival[None, :]
        started_for_nothing = RowExpression(intervals)
        for tier, started_in_tier in zip(tiers, started, strict=True):
            in_tier_now = min(working_now, float(tier.workers_per_site * sites))
            working_now -= in_tier_now
            risen = RowExpression(intervals).add(tier.block, risen_by).add(started_in_tier, -np.eye(intervals))
            risen.constant[0] = -in_tier_now
            if back_from_builds is not None:
                back_in_tier = self._program.add_variables(intervals)
                risen.add(back_in_tier, -np.eye(intervals))
                started_for_nothing.add(back_in_tier, np.eye(intervals))
            self._program.constrain(risen, upper=0.0)
        if back_from_builds is not None:
            self._program.constrain(started_for_nothing.add_expression(back_from_builds, -1.0), upper=0.0)

    def _reassignment_shares(self) -> FloatArray:
        """The share of each interval, by row, within the economy's `reassignment_time` of each interval's start, by
        column: what a worker that starts work at that start loses of it."""
        started_at = self._edges[:-1][None, :]
        overlap_start = np.maximum(started_at, self._edges[:-1][:, None])
        overlap_end = np.minimum(started_at + self._economy.reassignment_time.seconds, self._edges[1:][:, None])
        return np.maximum(overlap_end - overlap_start, 0.0) / self._widths[:, None]

    def _constrain_supply(self) -> None:
        """The supply taken at most the cap and the game's limit at each interval's start: taken as a unit starts and
        given back as it is lost, and the cap raised as a supply depot or a command center is done."""
        intervals = self._interval_count
        used = RowExpression(intervals)
        used.constant += self._state.supply_used
        cap = RowExpression(intervals)
        cap.constant += self._state.supply_cap
        for index, item in enumerate(self._items):
            if item.supply_used:
                used.add_expression(self._started_so_far(index), item.supply_used)
            if item.supply_provided:
                cap.add_expression(self._starts_done(index, at_end=False), item.supply_provided)
        for completion_type, seconds in self._completions:
            if provided := self._model.unit_items[completion_type].supply_provided:
                cap.constant += provided * self._done_by(seconds, at_end=False)
        for unit_type, rate in self._loss_rates.items():
            supply = self._model.game_data.units[unit_type].supply_used
            if rate and supply:
                used.add_expression(self._count_alive(unit_type, at_end=False, survive=False), -supply)
                used.add_expression(self._count_alive(unit_type, at_end=False), supply)
        self._constrain_at_most(used, cap)
        limit = RowExpression(intervals)
        limit.constant += SUPPLY_LIMIT
        self._constrain_at_most(used, limit)

    def _constrain_producers(self) -> None:
        """For each set of producers an item takes, what its items and every item taking fewer of them keep busy at
        most what those producers can make, and their tech labs for the items that need one."""
        producer_sets = {item.producers for item in self._items if not item.is_built_by_worker}
        for producers in sorted(producer_sets, key=sorted):
            members = [
                index
                for index, item in enumerate(self._items)
                if not item.is_built_by_worker and item.producers <= producers
            ]
            self._constrain_producer_time(producers, members, on_tech_labs=False)
            if needing_tech_lab := [index for index in members if self._items[index].needs_tech_lab]:
                self._constrain_producer_time(producers, needing_tech_lab, on_tech_labs=True)

    def _constrain_producer_time(
        self, producers: frozenset[UnitTypeId], members: list[int], *, on_tech_labs: bool
    ) -> None:
        """The seconds the `members` and what is made now keep `producers` busy, or their tech labs, at most the
        seconds those can make, a reactor making a second; and where an interval is shorter than every member's build,
        the builds under way at most how many make them."""
        intervals = self._interval_count
        lanes = RowExpression(intervals)
        for producer in producers:
            add_on = self._model.tech_lab_of(producer) if on_tech_labs else self._model.reactor_of(producer)
            lane_types = [add_on] if on_tech_labs else [producer, add_on]
            for lane_type in lane_types:
                if lane_type is not None:
                    lanes.add_expression(self._count_alive(lane_type, at_end=False))
        occupations = [
            occupation
            for occupation in self._state.occupations
            if occupation.producer in producers and (occupation.needs_tech_lab or not on_tech_labs)
        ]
        busy = RowExpression(intervals)
        running = RowExpression(intervals)
        for index in members:
            busy_seconds = self._busy_seconds(index)
            self._add_starts_to(busy, index, busy_seconds)
            self._add_starts_to(running, index, (busy_seconds > SECONDS_TOLERANCE).astype(float))
        for occupation in occupations:
            seconds = self._busy_seconds_until(
                occupation.time_left.seconds, start_seconds=occupation.time_until_start.seconds
            )
            busy.constant += seconds
            running.constant += seconds > SECONDS_TOLERANCE
        self._constrain_at_most(busy, RowExpression(intervals).add_expression(lanes, self._widths))
        short_and_whole = self._shorter_than_every_build_and_whole(members)
        if short_and_whole.any():
            self._constrain_at_most(running.selected(short_and_whole), lanes.selected(short_and_whole))

    def _constrain_counts(self) -> None:
        """What the game's counts bound: workers at the fields and refineries there are, each tier its share of each, an
        add-on a host, a morph a townhall, an item its limit of starts, a refinery a geyser, and an upgrade once."""
        intervals = self._interval_count
        economy = self._economy
        new_bases = self._made_since_start(UnitTypeId.COMMAND_CENTER)
        fields = RowExpression(intervals).add_expression(new_bases, economy.mineral_fields_per_new_base)
        fields.constant += self._state.mineral_fields
        refineries = self._made_since_start(UnitTypeId.REFINERY)
        refineries.constant += self._state.refineries_with_vespene
        for tiers, sites in ((self._mining, fields), (self._collecting, refineries)):
            for tier in tiers:
                working = RowExpression(intervals).add(tier.block, np.eye(intervals))
                self._constrain_at_most(working, RowExpression(intervals).add_expression(sites, tier.workers_per_site))
        self._constrain_add_ons_and_morphs()
        for index, item in enumerate(self._items):
            limit = None if item.made is None else self._start_limits.get(item.made)
            if limit is not None:
                self._program.constrain(self._starts_over_the_plan(index), upper=limit)
        if (refinery := self._index_of_unit_type.get(UnitTypeId.REFINERY)) is not None:
            geysers = RowExpression(intervals).add_expression(new_bases, economy.geysers_per_new_base)
            geysers.constant += self._state.free_geysers
            self._constrain_at_most(self._started_so_far(refinery), geysers)
        for index, item in enumerate(self._items):
            if item.upgrade is not None:
                self._program.constrain(self._starts_over_the_plan(index), upper=1.0)

    def _constrain_add_ons_and_morphs(self) -> None:
        """At most one add-on a host, those standing and those in production counted, and at most one morph a
        command center, those in production counted."""
        sources: dict[UnitTypeId, list[int]] = {}
        for index, item in enumerate(self._items):
            if item.unit_type is not None and self._model.is_add_on(item.unit_type):
                for host in item.producers:
                    sources.setdefault(host, []).append(index)
            elif item.morphed_from is not None:
                sources.setdefault(item.morphed_from, []).append(index)
        for source, indices in sorted(sources.items()):
            made_types = {self._items[index].unit_type for index in indices}
            started = RowExpression(self._interval_count)
            for index in indices:
                started.add_expression(self._started_so_far(index))
            started.constant += sum(unit_type in made_types for unit_type, _ in self._completions)
            if self._items[indices[0]].morphed_from is None:
                started.constant += sum(self._state.counts.get(made, 0) for made in made_types if made is not None)
            available = self._made_since_start(source)
            available.constant += self._state.counts.get(source, 0)
            self._constrain_at_most(started, available)

    def _constrain_prerequisites(self) -> None:
        """No item starts in an interval before the techs and upgrades it needs are done by the interval's start: before
        the unlock fraction of a structure granting each tech, and of each upgrade, is; with one of 0, in the share
        that the fractions done of them allow."""
        met: dict[frozenset[UnitTypeId] | UpgradeId, RowExpression] = {}
        for index, item in enumerate(self._items):
            needed: list[RowExpression] = []
            for tech in item.techs:
                granting = self._model.types_granting(tech)
                if any(self._state.counts.get(unit_type, 0) for unit_type in granting):
                    continue
                if granting not in met:
                    granted = RowExpression(self._interval_count)
                    for unit_type in sorted(granting):
                        granted.add_expression(self._count_alive(unit_type, at_end=False, survive=False))
                    counted = [
                        made
                        for made, other in enumerate(self._items)
                        if other.unit_type in granting or other.morphed_from in granting
                    ]
                    met[granting] = self._whether_unlocked(granted, counted)
                needed.append(met[granting])
            for upgrade in item.upgrades_needed:
                if upgrade in self._state.upgrades:
                    continue
                if upgrade not in met:
                    researched = self._index_of_upgrade.get(upgrade)
                    is_under_way = upgrade in self._state.upgrade_completions
                    counted = [] if researched is None or is_under_way else [researched]
                    met[upgrade] = self._whether_unlocked(self._upgrade_done(upgrade, at_end=False), counted)
                needed.append(met[upgrade])
            bound = self._most_starts_in_an_interval(item)
            for done in needed:
                available = RowExpression(self._interval_count).add_expression(done, bound)
                self._constrain_at_most(self._starts_in_interval(index), available)

    def _whether_unlocked(self, done: RowExpression, counted: Sequence[int]) -> RowExpression:
        """Whether what `done` counts by each interval's start unlocks what needs it, 0 or 1: 1 only where the unlock
        fraction of one is done. With an unlock fraction of 0 it is `done` itself. `done` counts the starts of the
        items `counted`, and so in whole numbers until a fluid start of one of them can be done: the 0 or 1 is held
        whole from that edge on alone, and `done` returned if there is no such edge."""
        intervals = self._interval_count
        fractional_from = min(
            (self._completion_edges[index][~self._whole[index]].min(initial=math.inf) for index in counted),
            default=math.inf,
        )
        if not self._unlock_fraction or fractional_from >= intervals:
            return done
        unlocked = self._program.add_variables(intervals, upper=1.0, integral=np.arange(intervals) >= fractional_from)
        needed = RowExpression(intervals).add(unlocked, np.eye(intervals) * self._unlock_fraction)
        self._constrain_at_most(needed, done)
        return RowExpression(intervals).add(unlocked, np.eye(intervals))

    # The reward.

    def _reward_expression(self) -> RowExpression:
        """The reward of a plan: each interval's worth, discounted, the stock left at the horizon, and the previous
        plan's items kept to."""
        reward = self._reward
        worth = RowExpression(self._interval_count)
        identity = np.eye(self._interval_count)
        for unit_type, weight in reward.weights.items():
            worth.add_expression(self._count_alive(self._model.counted_as(unit_type), at_end=True), weight)
        for upgrade, weight in reward.upgrade_weights.items():
            worth.add_expression(self._upgrade_done(upgrade, at_end=True), weight)
        for unit_type, segments in reward.curves.items():
            worth.add(self._curve_values(self._model.counted_as(unit_type), segments), identity)
        for coverage in reward.coverages:
            worth.add(self._enemy_units_covered(coverage), identity * coverage.value)
        for floor in reward.floors:
            worth.add(self._units_short_of(floor), identity * -floor.penalty)
        objective = worth.summed(self._discounts)
        if reward.stock_weight:
            at_the_horizon = np.eye(1, self._interval_count + 1, k=self._interval_count) * reward.stock_weight
            objective.add(self._minerals, at_the_horizon).add(self._vespene, at_the_horizon)
        if self._previous_plan is not None and self._hysteresis_bonus:
            objective.add(self._previous_items_kept(self._previous_plan), self._hysteresis_bonus)
        return objective

    def _curve_values(self, unit_type: UnitTypeId, segments: Sequence[CurveSegment]) -> VariableBlock:
        """What the count of `unit_type` is worth at each interval's end by `segments`: a variable under each segment's
        line, which the reward raises to the lowest."""
        values = self._program.add_variables(self._interval_count, lower=-math.inf)
        count = self._count_alive(unit_type, at_end=True)
        worth_before = 0.0
        count_before = 0.0
        for segment in segments:
            under_line = RowExpression(self._interval_count).add(values, np.eye(self._interval_count))
            under_line.add_expression(count, -segment.slope)
            self._program.constrain(under_line, upper=worth_before - segment.slope * count_before)
            if math.isfinite(segment.up_to):
                worth_before += segment.slope * (segment.up_to - count_before)
                count_before = segment.up_to
        return values

    def _enemy_units_covered(self, coverage: Coverage) -> VariableBlock:
        """How many enemy units of `coverage` are covered at each interval's end: at most how many are expected, and at
        most what the own units are worth against them."""
        expected = [
            count_at(coverage.enemy_counts, self._state.time + GameTime(seconds=edge)) for edge in self._edges[1:]
        ]
        covered = self._program.add_variables(self._interval_count, upper=np.maximum(np.asarray(expected), 0.0))
        worth = RowExpression(self._interval_count)
        for unit_type, effectiveness in coverage.effectiveness.items():
            worth.add_expression(self._count_alive(self._model.counted_as(unit_type), at_end=True), effectiveness)
        self._constrain_at_most(RowExpression(self._interval_count).add(covered, np.eye(self._interval_count)), worth)
        return covered

    def _units_short_of(self, floor: Floor) -> VariableBlock:
        """How many units of `floor`'s type fall short of it at each interval's end."""
        wanted = [count_at(floor.counts, self._state.time + GameTime(seconds=edge)) for edge in self._edges[1:]]
        shortfalls = self._program.add_variables(self._interval_count)
        short = RowExpression(self._interval_count).add(shortfalls, -np.eye(self._interval_count))
        short.add_expression(self._count_alive(self._model.counted_as(floor.unit_type), at_end=True), -1.0)
        self._program.constrain(short, upper=-np.asarray(wanted, dtype=float))
        return shortfalls

    def _previous_items_kept(self, previous: Plan) -> VariableBlock:
        """Whether each of `previous`'s items still ahead is kept to: as many of its kind started by its interval as it
        and the previous plan's items of its kind before it, still ahead."""
        kept_to: list[tuple[int, int, int]] = []
        ranks: dict[tuple[bool, int], int] = {}
        for planned in previous.items:
            index = self._index_of(planned)
            seconds = (planned.start - self._state.time).seconds
            if index is None or seconds < 0 or seconds >= self._edges[-1]:
                continue
            key = (isinstance(planned.item, UpgradeId), int(planned.item))
            ranks[key] = ranks.get(key, 0) + 1
            interval = int(np.searchsorted(self._edges, seconds + SECONDS_TOLERANCE, side="right")) - 1
            kept_to.append((index, interval, ranks[key]))
        kept = self._program.add_variables(len(kept_to), upper=1.0)
        for position, (index, interval, rank) in enumerate(kept_to):
            enough = RowExpression(1).add(kept, np.eye(1, len(kept_to), k=position) * rank)
            enough.add_expression(
                self._started_so_far(index).selected(np.arange(self._interval_count) == interval), -1.0
            )
            self._program.constrain(enough, upper=0.0)
        return kept

    # Reading the plan.

    def _planned_items(self, values: FloatArray) -> tuple[PlannedItem, ...]:
        """Each item started, in order of start, those of one interval in the order of the items: of fractional starts,
        one each time their sum reaches a whole number, rounded, and fluid where its interval's starts are."""
        planned: list[tuple[int, int, PlannedItem]] = []
        for index, item in enumerate(self._items):
            made = item.made
            if made is None:
                continue
            whole = np.floor(_values_of(self._starts_so_far[index], values) + 0.5)
            for interval, count in enumerate(np.diff(whole, prepend=0.0)):
                start = self._state.time + GameTime(seconds=float(self._edges[interval]))
                is_fluid = not self._whole[index][interval]
                planned += [(interval, index, PlannedItem(item=made, start=start, is_fluid=is_fluid))] * int(count)
        return tuple(entry for _, _, entry in sorted(planned, key=lambda entry: (entry[0], entry[1])))

    def _planned_intervals(self, values: FloatArray) -> tuple[PlanInterval, ...]:
        """Each interval of the grid with the workers the plan has in it and what it comes to at its end."""
        times = [self._state.time + GameTime(seconds=edge) for edge in self._edges.tolist()]
        minerals, vespene = _values_of(self._minerals, values), _values_of(self._vespene, values)
        miners = self._working_in(self._mining).value_of(values)
        collectors = self._working_in(self._collecting).value_of(values)
        counts_at_ends = {
            unit_type: self._count_alive(unit_type, at_end=True).value_of(values)
            for unit_type in self._index_of_unit_type
        }
        return tuple(
            PlanInterval(
                start=times[interval],
                end=times[interval + 1],
                miners=float(miners[interval]),
                collectors=float(collectors[interval]),
                minerals=float(minerals[interval + 1]),
                vespene=float(vespene[interval + 1]),
                counts={unit_type: float(counts[interval]) for unit_type, counts in counts_at_ends.items()},
            )
            for interval in range(self._interval_count)
        )

    def _index_of(self, planned: PlannedItem) -> int | None:
        """The index of the item `planned` makes, or `None` if this plan has none."""
        if isinstance(planned.item, UpgradeId):
            return self._index_of_upgrade.get(planned.item)
        return self._index_of_unit_type.get(planned.item)

    def _walk_seconds_of(self, item: Item) -> float:
        """The seconds an SCV walks to build `item`, and back."""
        return 0.0 if item.unit_type is None else self._walk_seconds.get(item.unit_type, 0.0)


def _limits_by_counted_type(
    start_limits: Mapping[UnitTypeId | UpgradeId, int], model: TerranModel
) -> dict[UnitTypeId | UpgradeId, float]:
    """`start_limits`, each unit type's under the type it counts as by `model`, the lowest of those of one type."""
    limits: dict[UnitTypeId | UpgradeId, float] = {}
    for made, limit in start_limits.items():
        counted = model.counted_as(made) if isinstance(made, UnitTypeId) else made
        limits[counted] = min(float(limit), limits.get(counted, math.inf))
    return limits


def _falls_of(shares: FloatArray, *, from_share: float) -> FloatArray:
    """How far each column of `shares` falls from the row before to each row, to the first row from `from_share`."""
    before = np.vstack([np.full((1, shares.shape[1]), from_share), shares[:-1]])
    return np.maximum(before - shares, 0.0)


def _values_of(block: VariableBlock, values: FloatArray) -> FloatArray:
    """The values of `block`'s variables."""
    return values[block.start : block.start + block.size]


def _count_of(completions: Sequence[tuple[UnitTypeId, float]], unit_type: UnitTypeId) -> int:
    """How many of `completions` make `unit_type`."""
    return sum(completion_type == unit_type for completion_type, _ in completions)
