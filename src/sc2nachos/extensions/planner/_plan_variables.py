"""The variables of one plan's program, and what is counted from them."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, final

import numpy as np
from numpy.typing import NDArray

from sc2nachos.extensions.planner._economy_model import rate_tiers, walk_seconds_of
from sc2nachos.extensions.planner._milp import FloatArray, LinearProgram, RowExpression, VariableBlock
from sc2nachos.extensions.planner._survival import SurvivalShares
from sc2nachos.extensions.planner._terran_model import SUPPLY_LIMIT
from sc2nachos.extensions.planner._time_grid import SECONDS_TOLERANCE
from sc2nachos.ids import UnitTypeId, UpgradeId

if TYPE_CHECKING:
    from sc2nachos.extensions.planner._economy_model import EconomyModel
    from sc2nachos.extensions.planner._planner_state import PlannerState, WorkerBuild
    from sc2nachos.extensions.planner._reward import Reward
    from sc2nachos.extensions.planner._terran_model import ProductionItem, TerranModel
    from sc2nachos.gametime import TimeDependent


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class NewBase:
    """What a townhall a plan may start brings once done: the mineral fields and the geysers of the expansion it goes
    to.

    Raises `ValueError` for a negative count.
    """

    mineral_fields: int
    """The mineral fields it brings."""
    geysers: int
    """The geysers it brings, each free for a refinery."""

    def __post_init__(self) -> None:
        self._validate()

    def _validate(self) -> None:
        """Raise `ValueError` for a field that holds what the class's docstring refuses."""
        if self.mineral_fields < 0 or self.geysers < 0:
            raise ValueError(
                f"a new base's fields and geysers must not be negative, not {self.mineral_fields} and {self.geysers}"
            )


@final
@dataclass(frozen=True, slots=True)
class WorkerTier:
    """One tier of the workers at mineral fields or refineries: how many work in it in each interval, how many of them a
    field or refinery takes, and what each brings in a second."""

    block: VariableBlock
    workers_per_site: int
    per_worker_second: float


class PlanVariables:
    """The variables of the program of one plan, and what is counted from them: each item's starts so far by each
    interval's end, each resource's stock at each edge, and the workers in each tier of the economy's rates in each
    interval; and from them, as expressions for constraints and the reward to use, the starts in an interval, those
    done by one, the units alive, the upgrades done and how long a start keeps its producer busy. `PlanProgram` says
    what its arguments are and what it raises.
    """

    __slots__ = (
        "_builders_free_after",
        "_collecting",
        "_completion_edges",
        "_completions",
        "_economy",
        "_edges",
        "_index_of_unit_type",
        "_index_of_upgrade",
        "_items",
        "_max_ignored_overrun",
        "_minerals",
        "_mining",
        "_model",
        "_new_bases",
        "_program",
        "_reward",
        "_spendable_income_fraction",
        "_start_limits",
        "_started_collecting",
        "_started_mining",
        "_starts_so_far",
        "_state",
        "_survival",
        "_unlock_fraction",
        "_vespene",
        "_walk_seconds",
        "_whole",
        "_widths",
    )

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
        if len(fluid_after_seconds) != len(items):
            raise ValueError(f"{len(fluid_after_seconds)} windows for {len(items)} items")
        self._items = tuple(items)
        self._model = model
        self._state = state
        self._reward = reward
        self._economy = economy
        self._start_limits = _limits_by_counted_type(start_limits, model)
        self._max_ignored_overrun = max_ignored_overrun
        self._unlock_fraction = unlock_fraction
        self._spendable_income_fraction = spendable_income_fraction
        self._walk_seconds = walk_seconds_of(
            economy, (unit_type for unit_type, item in model.unit_items.items() if item.is_built_by_worker)
        )
        self._completions, self._builders_free_after = self._completions_and_builders()
        self._new_bases = tuple(new_bases)
        self._edges: FloatArray = np.asarray(edges, dtype=float)
        self._widths: FloatArray = np.diff(self._edges)
        self._survival = SurvivalShares(
            loss_rates, model=model, start=state.time, horizon_seconds=float(self._edges[-1])
        )
        self._index_of_unit_type = {
            item.unit_type: i for i, item in enumerate(self._items) if item.unit_type is not None
        }
        self._index_of_upgrade = {item.upgrade: i for i, item in enumerate(self._items) if item.upgrade is not None}
        self._program = LinearProgram()
        self._completion_edges = [self._edge_index(self._edges[:-1] + item.build_seconds) for item in self._items]
        self._whole = self._whole_intervals_of(fluid_after_seconds)
        self._add_variables()

    def _whole_intervals_of(self, fluid_after_seconds: Sequence[float]) -> list[NDArray[np.bool_]]:
        """For each item, the intervals its starts are whole in: those that start before its time in
        `fluid_after_seconds`, and every one for an upgrade a term of the reward requires, a share of which would
        unlock that share of all the term could ever be worth, far more than of what its units are."""
        required = frozenset[UpgradeId]().union(*(term.requirements for term in self._reward.terms))
        return [
            np.ones(len(self._edges) - 1, dtype=bool)
            if item.upgrade in required
            else self._edges[:-1] < fluid_after - SECONDS_TOLERANCE
            for item, fluid_after in zip(self._items, fluid_after_seconds, strict=True)
        ]

    @property
    def _interval_count(self) -> int:
        """How many intervals the grid has."""
        return len(self._widths)

    def _add_variables(self) -> None:
        """Add the variables every plan has: each item's starts so far, the stock of minerals and of vespene at each
        edge, the workers in each tier, and those that start to mine or to collect."""
        self._starts_so_far: list[VariableBlock] = []
        for item, whole in zip(self._items, self._whole, strict=True):
            self._add_starts_so_far(item, whole)
        self._minerals = self._add_stock(self._state.resources.minerals)
        self._vespene = self._add_stock(self._state.resources.vespene)
        self._mining = self._add_worker_tiers(self._economy.minerals_per_miner_second)
        self._collecting = self._add_worker_tiers(self._economy.vespene_per_collector_second)
        self._started_mining, self._started_collecting = self._add_work_starts()

    def _completions_and_builders(self) -> tuple[tuple[tuple[UnitTypeId, float], ...], tuple[float, ...]]:
        """Each unit in production with the seconds until it is done, a structure an SCV builds after any walk there;
        and the seconds until each SCV building, or walking to build, is back at work after its walk back.

        Raises `ValueError` for a unit in production of a type that is no item of the model's."""
        for in_production in (*self._state.unit_completions, *self._state.worker_builds):
            if in_production.unit_type not in self._model.unit_items:
                raise ValueError(
                    f"the state holds a {in_production.unit_type.name} in production, which the planner cannot make "
                    "for Terran"
                )
        completions = [
            (completion.unit_type, completion.time_left.seconds) for completion in self._state.unit_completions
        ]
        builders_free_after: list[float] = []
        for build in self._state.worker_builds:
            done_after = self._seconds_until_built(build)
            completions.append((build.unit_type, done_after))
            builders_free_after.append(done_after + self._walk_seconds.get(build.unit_type, 0.0))
        return tuple(completions), tuple(builders_free_after)

    def _seconds_until_built(self, build: WorkerBuild) -> float:
        """The seconds until a structure an SCV builds is done, after the SCV's walk there if it has yet to walk."""
        walk = self._walk_seconds.get(build.unit_type, 0.0)
        return build.time_left.seconds + (walk if build.builder_walks_there else 0.0)

    def _add_starts_so_far(self, item: ProductionItem, whole: NDArray[np.bool_]) -> None:
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

    def _add_worker_tiers(self, rates: Sequence[float]) -> tuple[WorkerTier, ...]:
        """The variables of how many workers work in each tier of `rates`, each worker's at a field or refinery, in each
        interval."""
        return tuple(
            WorkerTier(self._program.add_variables(self._interval_count), workers, rate)
            for workers, rate in rate_tiers(rates)
        )

    def _add_work_starts(self) -> tuple[tuple[VariableBlock, ...], tuple[VariableBlock, ...]]:
        """The variables of the workers that start to mine in each interval, a block for each mining tier, and of those
        that start to collect, one for each collecting tier; none where the economy's `initial_harvest_delay` is 0."""
        if not self._economy.initial_harvest_delay.seconds:
            return (), ()
        started_mining = tuple(self._program.add_variables(self._interval_count) for _ in self._mining)
        return started_mining, tuple(self._program.add_variables(self._interval_count) for _ in self._collecting)

    def _working_in(self, tiers: Sequence[WorkerTier]) -> RowExpression:
        """The workers working in `tiers` in each interval."""
        working = RowExpression(self._interval_count)
        for tier in tiers:
            working.add(tier.block, np.eye(self._interval_count))
        return working

    def _most_starts_in_an_interval(self, item: ProductionItem) -> float:
        """The most of `item` that can start in one interval."""
        state = self._state
        if item.upgrade is not None:
            most = 1.0
        elif item.unit_type is UnitTypeId.COMMAND_CENTER:
            most = float(len(self._new_bases))
        elif item.unit_type is UnitTypeId.REFINERY:
            geysers_of_new_bases = sum(base.geysers for base in self._new_bases)
            geysers_coming = geysers_of_new_bases + sum(build.geysers for build in state.worker_builds)
            most = float(state.free_geysers + geysers_coming)
        elif item.morphed_from is not None:
            in_production = sum(build.unit_type is UnitTypeId.COMMAND_CENTER for build in state.worker_builds)
            townhalls_coming = len(self._new_bases) + in_production
            most = float(state.units.get(item.morphed_from, 0) + townhalls_coming)
        elif item.supply_used > 0:
            most = float(math.floor(SUPPLY_LIMIT / item.supply_used))
        else:
            # No tighter bound: more than any plan starts of one item in one interval.
            most = 100.0
        return most if item.made is None else min(most, self._start_limits.get(item.made, math.inf))

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

    def _starts_done(self, index: int, *, at_end: bool, survive: bool = False) -> RowExpression:
        """The starts of item `index` done by each interval's start, or its end, each as it survives its loss rate from
        when it is done if `survive`."""
        rows = self._row_edges(at_end=at_end)
        done = (self._completion_edges[index][None, :] <= rows[:, None]).astype(float)
        unit_type = self._items[index].unit_type
        if survive and unit_type is not None:
            completed = self._edges[:-1] + self._items[index].build_seconds
            done *= self._survival.share_surviving(
                unit_type, completed[None, :], self._edges[rows.astype(int)][:, None]
            )
        return self._add_starts_to(RowExpression(len(rows)), index, done)

    def _done_by(self, seconds: float, *, at_end: bool, surviving_as: UnitTypeId | None = None) -> FloatArray:
        """For each interval's start, or its end, whether something done after `seconds` is done, as a unit of the type
        `surviving_as` survives its loss rate from then."""
        rows = self._row_edges(at_end=at_end)
        done = (self._edge_index(np.array([seconds]))[0] <= rows).astype(float)
        if surviving_as is None:
            return done
        return done * self._survival.share_surviving(surviving_as, seconds, self._edges[rows.astype(int)])

    def _count_alive(self, unit_type: UnitTypeId, *, at_end: bool, survive: bool = True) -> RowExpression:
        """The units of `unit_type` alive at each interval's start, or its end: those alive now, those in production and
        those the plan starts, less those morphed into another type, each as it survives its loss rate if `survive`."""
        rows = self._row_edges(at_end=at_end)
        count = RowExpression(len(rows))
        count.constant = np.full(len(rows), float(self._state.units.get(unit_type, 0)))
        if survive:
            count.constant *= self._survival.share_surviving(unit_type, 0.0, self._edges[rows.astype(int)])
        for completion_type, seconds in self._completions:
            if completion_type == unit_type:
                count.constant += self._done_by(seconds, at_end=at_end, surviving_as=unit_type if survive else None)
            elif self._model.unit_items[completion_type].morphed_from == unit_type:
                count.constant -= self._done_by(seconds, at_end=at_end)
        for index, item in enumerate(self._items):
            if item.unit_type == unit_type:
                count.add_expression(self._starts_done(index, at_end=at_end, survive=survive))
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

    def _walk_seconds_of(self, item: ProductionItem) -> float:
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
