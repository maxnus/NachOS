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
from sc2nachos.extensions.planner._plan import Plan, PlannedItem
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

# The most an item without a tighter bound starts in one bucket.
_MOST_STARTS_IN_A_BUCKET: Final = 100.0
# A start pays from the stock at its bucket's start and this share of the bucket's income.
_SHARE_OF_INCOME_SPENT: Final = 0.5


@final
@dataclass(frozen=True, slots=True)
class _WorkerTier:
    """One tier of the workers at mineral fields or refineries: how many work in it in each bucket, how many of them a
    field or refinery takes, and what each brings in a second."""

    block: VariableBlock
    workers_per_site: int
    per_worker_second: float


@final
class PlanProgram:
    """The mixed-integer linear program of one plan over the buckets between `edges`, in seconds from `state`'s time.

    Each item's starts in each bucket are a variable, whole where the bucket starts before the item's time in
    `whole_until_seconds`, one for each item in the order of `items`. A start pays from the stock at its bucket's start
    plus half the bucket's income, keeps its producer busy for its build time over the buckets it spans, its SCV from
    the walk there through the build to the walk back, takes its supply at once, and counts as done from the first edge
    at or after its end. Workers mine, collect or build, pooled over every field and refinery, in the tiers of
    `economy`'s rates, and each orbital command adds its MULEs' income. A unit counts as it survives its loss rate in
    `loss_rates`, for income, supply, the reward and coverage. At most `new_command_center_limit` command centers
    start, any number if `None`.

    Raises `ValueError` unless `whole_until_seconds` hold one time for each item, and for a negative walk from
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
        "_new_command_center_limit",
        "_index_of_unit_type",
        "_index_of_upgrade",
        "_items",
        "_loss_rates",
        "_minerals",
        "_mining",
        "_model",
        "_objective",
        "_program",
        "_reward",
        "_starts",
        "_state",
        "_vespene",
        "_walk_seconds",
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
        new_command_center_limit: int | None,
        whole_until_seconds: Sequence[float],
    ) -> None:
        if len(whole_until_seconds) != len(items):
            raise ValueError(f"{len(whole_until_seconds)} windows for {len(items)} items")
        self._items = tuple(items)
        self._model = model
        self._state = state
        self._reward = reward
        self._economy = economy
        self._loss_rates = {model.counted_as(unit_type): rate for unit_type, rate in loss_rates.items()}
        self._new_command_center_limit = new_command_center_limit
        built_by_worker = (unit_type for unit_type, item in model.unit_items.items() if item.is_built_by_worker)
        self._walk_seconds = walk_seconds_of(economy, built_by_worker)
        self._completions, self._builders_free_after = self._completions_and_builders()
        self._edges: FloatArray = np.asarray(edges, dtype=float)
        self._widths: FloatArray = np.diff(self._edges)
        self._index_of_unit_type = {
            item.unit_type: i for i, item in enumerate(self._items) if item.unit_type is not None
        }
        self._index_of_upgrade = {item.upgrade: i for i, item in enumerate(self._items) if item.upgrade is not None}
        self._program = LinearProgram()
        self._completion_edges = [self._edge_index(self._edges[:-1] + item.build_seconds) for item in self._items]
        self._whole = [self._edges[:-1] < whole_until - SECONDS_TOLERANCE for whole_until in whole_until_seconds]
        self._starts = [self._add_starts(item, whole) for item, whole in zip(self._items, self._whole, strict=True)]
        self._minerals = self._add_stock(state.minerals)
        self._vespene = self._add_stock(state.vespene)
        self._mining = self._add_worker_tiers(economy.minerals_per_miner_second)
        self._collecting = self._add_worker_tiers(economy.vespene_per_collector_second)
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
        values = solution.values
        counts: dict[UnitTypeId, tuple[float, ...]] = {}
        for unit_type in self._index_of_unit_type:
            at_starts = self._count_alive(unit_type, at_end=False).value_of(values)
            at_end = self._count_alive(unit_type, at_end=True).value_of(values)[-1]
            counts[unit_type] = (*(float(count) for count in at_starts), float(at_end))
        return Plan(
            made_at=self._state.time,
            items=self._planned_items(values),
            times=tuple(self._state.time + GameTime(seconds=edge) for edge in self._edges),
            minerals=tuple(float(stock) for stock in _values_of(self._minerals, values)),
            vespene=tuple(float(stock) for stock in _values_of(self._vespene, values)),
            counts=counts,
            objective=solution.objective,
            gap=solution.gap,
            is_optimal=solution.is_optimal,
            solve_seconds=solution.seconds,
        )

    @property
    def _bucket_count(self) -> int:
        """How many buckets the grid has."""
        return len(self._widths)

    # The variables.

    def _completions_and_builders(self) -> tuple[tuple[tuple[UnitTypeId, float], ...], tuple[float, ...]]:
        """Each unit in production with the seconds until it is done, a structure an SCV builds after any walk there;
        and the seconds until each SCV building, or walking to build, is back at work after its walk back."""
        completions = [(unit_type, time_left.seconds) for unit_type, time_left in self._state.unit_completions]
        builders_free_after: list[float] = []
        for build in self._state.worker_builds:
            walk = self._walk_seconds.get(build.unit_type, 0.0)
            done_after = build.time_left.seconds + (walk if build.builder_walks_there else 0.0)
            completions.append((build.unit_type, done_after))
            builders_free_after.append(done_after + walk)
        return tuple(completions), tuple(builders_free_after)

    def _add_starts(self, item: Item, whole: NDArray[np.bool_]) -> VariableBlock:
        """The variables of `item`'s starts in each bucket: none before an SCV could walk there, and none of an upgrade
        done or being researched."""
        bucket_starts = self._edges[:-1]
        upper = np.full(len(bucket_starts), self._most_starts_in_a_bucket(item))
        if item.unit_type is not None and item.is_built_by_worker:
            upper[bucket_starts < self._walk_seconds.get(item.unit_type, 0.0) - SECONDS_TOLERANCE] = 0.0
        if item.upgrade is not None and (
            item.upgrade in self._state.upgrades or item.upgrade in self._state.upgrade_completions
        ):
            upper[:] = 0.0
        return self._program.add_variables(len(bucket_starts), upper=upper, integral=whole)

    def _add_stock(self, initial: float) -> VariableBlock:
        """The variables of a resource's stock at each edge, the first `initial`."""
        lower = np.zeros(self._bucket_count + 1)
        upper = np.full(self._bucket_count + 1, math.inf)
        lower[0] = upper[0] = initial
        return self._program.add_variables(self._bucket_count + 1, lower=lower, upper=upper)

    def _add_worker_tiers(self, rates: Sequence[float]) -> tuple[_WorkerTier, ...]:
        """The variables of how many workers work in each tier of `rates`, each worker's at a field or refinery, in each
        bucket."""
        return tuple(
            _WorkerTier(self._program.add_variables(self._bucket_count), workers, rate)
            for workers, rate in rate_tiers(rates)
        )

    def _most_starts_in_a_bucket(self, item: Item) -> float:
        """The most of `item` that can start in one bucket."""
        state = self._state
        new_townhalls = (
            _MOST_STARTS_IN_A_BUCKET
            if self._new_command_center_limit is None
            else float(self._new_command_center_limit)
        )
        townhalls_coming = new_townhalls + _count_of(self._completions, UnitTypeId.COMMAND_CENTER)
        if item.upgrade is not None:
            return 1.0
        if item.unit_type == UnitTypeId.COMMAND_CENTER:
            return new_townhalls
        if item.unit_type == UnitTypeId.REFINERY:
            return float(state.free_geysers + self._economy.geysers_per_new_base * townhalls_coming)
        if item.morphed_from is not None:
            return float(state.counts.get(item.morphed_from, 0) + townhalls_coming)
        if item.supply_used > 0:
            return float(math.floor(SUPPLY_LIMIT / item.supply_used))
        return _MOST_STARTS_IN_A_BUCKET

    def _discounts_of(self, reward: Reward) -> FloatArray:
        """What each bucket's seconds count for, discounted from the plan's start, the last bucket's with what the state
        at the horizon counts for after it."""
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

    # Expressions over the buckets.

    def _edge_index(self, seconds: FloatArray) -> FloatArray:
        """The index of the first edge at or after each of `seconds`, past the last edge's index if after it."""
        return np.searchsorted(self._edges, seconds - SECONDS_TOLERANCE, side="left").astype(float)

    def _row_edges(self, *, at_end: bool) -> FloatArray:
        """The index of the edge each bucket's row counts at: its start, or its end."""
        return np.arange(self._bucket_count, dtype=float) + (1.0 if at_end else 0.0)

    def _started_so_far(self, index: int) -> RowExpression:
        """The starts of item `index` in each bucket and the buckets before."""
        buckets = self._bucket_count
        return RowExpression(buckets).add(self._starts[index], np.tril(np.ones((buckets, buckets))))

    def _starts_in_bucket(self, index: int) -> RowExpression:
        """The starts of item `index` in each bucket."""
        return RowExpression(self._bucket_count).add(self._starts[index], np.eye(self._bucket_count))

    def _starts_done(self, index: int, *, at_end: bool, rate: float = 0.0) -> RowExpression:
        """The starts of item `index` done by each bucket's start, or its end, each as it survives `rate` a second."""
        rows = self._row_edges(at_end=at_end)
        done = (self._completion_edges[index][None, :] <= rows[:, None]).astype(float)
        if rate:
            completed = self._edges[:-1] + self._items[index].build_seconds
            since = np.maximum(self._edges[rows.astype(int)][:, None] - completed[None, :], 0.0)
            done *= np.exp(-rate * since)
        return RowExpression(len(rows)).add(self._starts[index], done)

    def _done_by(self, seconds: float, *, at_end: bool, rate: float = 0.0) -> FloatArray:
        """For each bucket's start, or its end, whether something done after `seconds` is done, as it survives `rate`
        a second."""
        rows = self._row_edges(at_end=at_end)
        done = (self._edge_index(np.array([seconds]))[0] <= rows).astype(float)
        return done * np.exp(-rate * np.maximum(self._edges[rows.astype(int)] - seconds, 0.0))

    def _count_alive(self, unit_type: UnitTypeId, *, at_end: bool, survive: bool = True) -> RowExpression:
        """The units of `unit_type` alive at each bucket's start, or its end: those alive now, those in production and
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
        """The units of `unit_type` done since the plan's start by each bucket's start, from production or the plan."""
        made = RowExpression(self._bucket_count)
        for completion_type, seconds in self._completions:
            if completion_type == unit_type:
                made.constant += self._done_by(seconds, at_end=False)
        if (index := self._index_of_unit_type.get(unit_type)) is not None:
            made.add_expression(self._starts_done(index, at_end=False))
        return made

    def _upgrade_done(self, upgrade: UpgradeId, *, at_end: bool) -> RowExpression:
        """Whether `upgrade` is done by each bucket's start, or its end."""
        done = RowExpression(self._bucket_count)
        if upgrade in self._state.upgrades:
            done.constant += 1.0
        elif upgrade in self._state.upgrade_completions:
            done.constant += self._done_by(self._state.upgrade_completions[upgrade].seconds, at_end=at_end)
        elif (index := self._index_of_upgrade.get(upgrade)) is not None:
            done.add_expression(self._starts_done(index, at_end=at_end))
        return done

    def _busy_seconds(self, item: Item, *, before: float = 0.0, after: float = 0.0) -> FloatArray:
        """The seconds of each bucket, by row, that one start of `item` in each bucket, by column, keeps busy: from
        `before` seconds ahead of the start to `after` seconds past its end."""
        starts = self._edges[:-1][None, :]
        overlap_start = np.maximum(starts - before, self._edges[:-1][:, None])
        overlap_end = np.minimum(starts + item.build_seconds + after, self._edges[1:][:, None])
        return np.maximum(overlap_end - overlap_start, 0.0)

    def _busy_seconds_until(self, end_seconds: float, *, start_seconds: float = 0.0) -> FloatArray:
        """The seconds of each bucket within `start_seconds` to `end_seconds` from the plan's start."""
        overlap_start = np.maximum(self._edges[:-1], start_seconds)
        return np.maximum(np.minimum(self._edges[1:], end_seconds) - overlap_start, 0.0)

    def _shorter_than_every_build_and_whole(self, indices: list[int]) -> NDArray[np.bool_]:
        """Whether each bucket is no longer than every build of the items `indices`, and their starts in it are whole:
        where a build under way keeps its producer from a start in the bucket, as a start sits on its first second."""
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
        """Each bucket's stock of minerals and vespene: the stock before, plus what was mined, less what started; and
        what starts paid from the stock at the bucket's start and half its income."""
        buckets = self._bucket_count
        mineral_income = RowExpression(buckets)
        for tier in self._mining:
            mineral_income.add(tier.block, np.eye(buckets) * tier.per_worker_second)
        if mule_rate := self._economy.mule_minerals_per_orbital_second:
            mineral_income.add_expression(self._count_alive(UnitTypeId.ORBITAL_COMMAND, at_end=False), mule_rate)
        vespene_income = RowExpression(buckets)
        for tier in self._collecting:
            vespene_income.add(tier.block, np.eye(buckets) * tier.per_worker_second)
        for stock, income, is_minerals in (
            (self._minerals, mineral_income, True),
            (self._vespene, vespene_income, False),
        ):
            spent = RowExpression(buckets)
            for index, item in enumerate(self._items):
                if cost := item.minerals if is_minerals else item.vespene:
                    spent.add_expression(self._starts_in_bucket(index), cost)
            flow = RowExpression(buckets).add(stock, np.eye(buckets, buckets + 1, k=1) - np.eye(buckets, buckets + 1))
            flow.add_expression(income, -self._widths).add_expression(spent)
            self._program.constrain(flow, lower=0.0, upper=0.0)
            paid = RowExpression(buckets).add(stock, np.eye(buckets, buckets + 1))
            paid.add_expression(income, _SHARE_OF_INCOME_SPENT * self._widths).add_expression(spent, -1.0)
            self._program.constrain(paid, lower=0.0)

    def _constrain_workers(self) -> None:
        """Miners, collectors and each builder's share of a bucket at most the SCVs alive at its start; and, with no
        walks, the builders counted whole where a bucket is shorter than every build."""
        buckets = self._bucket_count
        working = RowExpression(buckets)
        for tier in (*self._mining, *self._collecting):
            working.add(tier.block, np.eye(buckets))
        builders = RowExpression(buckets)
        built = [(index, item) for index, item in enumerate(self._items) if item.is_built_by_worker]
        for index, item in built:
            walk = self._walk_seconds_of(item)
            builders.add(self._starts[index], self._busy_seconds(item, before=walk, after=walk) / self._widths[:, None])
        for free_after in self._builders_free_after:
            builders.constant += self._busy_seconds_until(free_after) / self._widths
        workers = self._count_alive(UnitTypeId.SCV, at_end=False)
        self._constrain_at_most(RowExpression(buckets).add_expression(working).add_expression(builders), workers)
        if not built or any(self._walk_seconds_of(item) for _, item in built):
            return
        short_and_whole = self._shorter_than_every_build_and_whole([index for index, _ in built])
        builders_whole = RowExpression(buckets).add_expression(working)
        for index, item in built:
            builders_whole.add(self._starts[index], (self._busy_seconds(item) > SECONDS_TOLERANCE).astype(float))
        for free_after in self._builders_free_after:
            builders_whole.constant += self._busy_seconds_until(free_after) > SECONDS_TOLERANCE
        self._constrain_at_most(builders_whole.selected(short_and_whole), workers.selected(short_and_whole))

    def _constrain_supply(self) -> None:
        """The supply taken at most the cap and the game's limit at each bucket's start: taken as a unit starts and
        given back as it is lost, and the cap raised as a supply depot or a command center is done."""
        buckets = self._bucket_count
        used = RowExpression(buckets)
        used.constant += self._state.supply_used
        cap = RowExpression(buckets)
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
        limit = RowExpression(buckets)
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
        seconds those can make, a reactor making a second; and where a bucket is shorter than every member's build,
        the builds under way at most how many make them."""
        buckets = self._bucket_count
        lanes = RowExpression(buckets)
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
        busy = RowExpression(buckets)
        running = RowExpression(buckets)
        for index in members:
            busy_seconds = self._busy_seconds(self._items[index])
            busy.add(self._starts[index], busy_seconds)
            running.add(self._starts[index], (busy_seconds > SECONDS_TOLERANCE).astype(float))
        for occupation in occupations:
            seconds = self._busy_seconds_until(
                occupation.time_left.seconds, start_seconds=occupation.time_until_start.seconds
            )
            busy.constant += seconds
            running.constant += seconds > SECONDS_TOLERANCE
        self._constrain_at_most(busy, RowExpression(buckets).add_expression(lanes, self._widths))
        short_and_whole = self._shorter_than_every_build_and_whole(members)
        if short_and_whole.any():
            self._constrain_at_most(running.selected(short_and_whole), lanes.selected(short_and_whole))

    def _constrain_counts(self) -> None:
        """What the game's counts bound: workers at the fields and refineries there are, each tier its share of each, an
        add-on a host, a morph a townhall, command centers their limit, a refinery a geyser, and an upgrade
        once."""
        buckets = self._bucket_count
        economy = self._economy
        new_bases = self._made_since_start(UnitTypeId.COMMAND_CENTER)
        fields = RowExpression(buckets).add_expression(new_bases, economy.mineral_fields_per_new_base)
        fields.constant += self._state.mineral_fields
        refineries = self._made_since_start(UnitTypeId.REFINERY)
        refineries.constant += self._state.refineries_with_vespene
        for tiers, sites in ((self._mining, fields), (self._collecting, refineries)):
            for tier in tiers:
                working = RowExpression(buckets).add(tier.block, np.eye(buckets))
                self._constrain_at_most(working, RowExpression(buckets).add_expression(sites, tier.workers_per_site))
        self._constrain_add_ons_and_morphs()
        command_center = self._index_of_unit_type.get(UnitTypeId.COMMAND_CENTER)
        if command_center is not None and self._new_command_center_limit is not None:
            expansions = RowExpression(1).add(self._starts[command_center], 1.0)
            self._program.constrain(expansions, upper=float(self._new_command_center_limit))
        if (refinery := self._index_of_unit_type.get(UnitTypeId.REFINERY)) is not None:
            geysers = RowExpression(buckets).add_expression(new_bases, economy.geysers_per_new_base)
            geysers.constant += self._state.free_geysers
            self._constrain_at_most(self._started_so_far(refinery), geysers)
        for index, item in enumerate(self._items):
            if item.upgrade is not None:
                self._program.constrain(RowExpression(1).add(self._starts[index], 1.0), upper=1.0)

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
            started = RowExpression(self._bucket_count)
            for index in indices:
                started.add_expression(self._started_so_far(index))
            started.constant += sum(unit_type in made_types for unit_type, _ in self._completions)
            if self._items[indices[0]].morphed_from is None:
                started.constant += sum(self._state.counts.get(made, 0) for made in made_types if made is not None)
            available = self._made_since_start(source)
            available.constant += self._state.counts.get(source, 0)
            self._constrain_at_most(started, available)

    def _constrain_prerequisites(self) -> None:
        """No item starts in a bucket before the techs and upgrades it needs are done by the bucket's start."""
        for index, item in enumerate(self._items):
            needed: list[RowExpression] = []
            for tech in item.techs:
                granting = self._model.types_granting(tech)
                if not any(self._state.counts.get(unit_type, 0) for unit_type in granting):
                    granted = RowExpression(self._bucket_count)
                    for unit_type in sorted(granting):
                        granted.add_expression(self._count_alive(unit_type, at_end=False, survive=False))
                    needed.append(granted)
            needed += [
                self._upgrade_done(upgrade, at_end=False)
                for upgrade in item.upgrades_needed
                if upgrade not in self._state.upgrades
            ]
            bound = self._most_starts_in_a_bucket(item)
            for done in needed:
                available = RowExpression(self._bucket_count).add_expression(done, bound)
                self._constrain_at_most(self._starts_in_bucket(index), available)

    # The reward.

    def _reward_expression(self) -> RowExpression:
        """The reward of a plan: each bucket's worth, discounted, and the previous plan's items kept to."""
        reward = self._reward
        worth = RowExpression(self._bucket_count)
        identity = np.eye(self._bucket_count)
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
        if reward.previous_plan is not None and reward.hysteresis_bonus:
            objective.add(self._previous_items_kept(reward.previous_plan), reward.hysteresis_bonus)
        return objective

    def _curve_values(self, unit_type: UnitTypeId, segments: Sequence[CurveSegment]) -> VariableBlock:
        """What the count of `unit_type` is worth at each bucket's end by `segments`: a variable under each segment's
        line, which the reward raises to the lowest."""
        values = self._program.add_variables(self._bucket_count, lower=-math.inf)
        count = self._count_alive(unit_type, at_end=True)
        worth_before = 0.0
        count_before = 0.0
        for segment in segments:
            under_line = RowExpression(self._bucket_count).add(values, np.eye(self._bucket_count))
            under_line.add_expression(count, -segment.slope)
            self._program.constrain(under_line, upper=worth_before - segment.slope * count_before)
            if math.isfinite(segment.up_to):
                worth_before += segment.slope * (segment.up_to - count_before)
                count_before = segment.up_to
        return values

    def _enemy_units_covered(self, coverage: Coverage) -> VariableBlock:
        """How many enemy units of `coverage` are covered at each bucket's end: at most how many are expected, and at
        most what the own units are worth against them."""
        expected = [
            count_at(coverage.enemy_counts, self._state.time + GameTime(seconds=edge)) for edge in self._edges[1:]
        ]
        covered = self._program.add_variables(self._bucket_count, upper=np.maximum(np.asarray(expected), 0.0))
        worth = RowExpression(self._bucket_count)
        for unit_type, effectiveness in coverage.effectiveness.items():
            worth.add_expression(self._count_alive(self._model.counted_as(unit_type), at_end=True), effectiveness)
        self._constrain_at_most(RowExpression(self._bucket_count).add(covered, np.eye(self._bucket_count)), worth)
        return covered

    def _units_short_of(self, floor: Floor) -> VariableBlock:
        """How many units of `floor`'s type fall short of it at each bucket's end."""
        wanted = [count_at(floor.counts, self._state.time + GameTime(seconds=edge)) for edge in self._edges[1:]]
        shortfalls = self._program.add_variables(self._bucket_count)
        short = RowExpression(self._bucket_count).add(shortfalls, -np.eye(self._bucket_count))
        short.add_expression(self._count_alive(self._model.counted_as(floor.unit_type), at_end=True), -1.0)
        self._program.constrain(short, upper=-np.asarray(wanted, dtype=float))
        return shortfalls

    def _previous_items_kept(self, previous: Plan) -> VariableBlock:
        """Whether each of `previous`'s items still ahead is kept to: as many of its kind started by its bucket as it
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
            bucket = int(np.searchsorted(self._edges, seconds + SECONDS_TOLERANCE, side="right")) - 1
            kept_to.append((index, bucket, ranks[key]))
        kept = self._program.add_variables(len(kept_to), upper=1.0)
        for position, (index, bucket, rank) in enumerate(kept_to):
            enough = RowExpression(1).add(kept, np.eye(1, len(kept_to), k=position) * rank)
            enough.add_expression(self._started_so_far(index).selected(np.arange(self._bucket_count) == bucket), -1.0)
            self._program.constrain(enough, upper=0.0)
        return kept

    # Reading the plan.

    def _planned_items(self, values: FloatArray) -> tuple[PlannedItem, ...]:
        """Each item started, in order of start, those of one bucket in the order of the items: of fractional starts,
        one each time their sum reaches a whole number, rounded."""
        planned: list[tuple[int, int, PlannedItem]] = []
        for index, item in enumerate(self._items):
            made = item.unit_type if item.unit_type is not None else item.upgrade
            if made is None:
                continue
            whole = np.floor(np.cumsum(_values_of(self._starts[index], values)) + 0.5)
            for bucket, count in enumerate(np.diff(whole, prepend=0.0)):
                start = self._state.time + GameTime(seconds=float(self._edges[bucket]))
                planned += [(bucket, index, PlannedItem(item=made, start=start))] * int(count)
        return tuple(entry for _, _, entry in sorted(planned, key=lambda entry: (entry[0], entry[1])))

    def _index_of(self, planned: PlannedItem) -> int | None:
        """The index of the item `planned` makes, or `None` if this plan has none."""
        if isinstance(planned.item, UpgradeId):
            return self._index_of_upgrade.get(planned.item)
        return self._index_of_unit_type.get(planned.item)

    def _walk_seconds_of(self, item: Item) -> float:
        """The seconds an SCV walks to build `item`, and back."""
        return 0.0 if item.unit_type is None else self._walk_seconds.get(item.unit_type, 0.0)


def _values_of(block: VariableBlock, values: FloatArray) -> FloatArray:
    """The values of `block`'s variables."""
    return values[block.start : block.start + block.size]


def _count_of(completions: Sequence[tuple[UnitTypeId, float]], unit_type: UnitTypeId) -> int:
    """How many of `completions` make `unit_type`."""
    return sum(completion_type == unit_type for completion_type, _ in completions)
