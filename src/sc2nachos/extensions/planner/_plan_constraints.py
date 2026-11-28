"""The constraints of one plan's program: what the game's rules hold its variables to."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import TYPE_CHECKING

import numpy as np

from sc2nachos.extensions.planner._milp import FloatArray, RowExpression, VariableBlock
from sc2nachos.extensions.planner._plan_variables import NewBase, PlanVariables, WorkerTier
from sc2nachos.extensions.planner._terran_model import SUPPLY_LIMIT
from sc2nachos.extensions.planner._time_grid import SECONDS_TOLERANCE
from sc2nachos.ids import UnitTypeId, UpgradeId

if TYPE_CHECKING:
    pass


class PlanConstraints(PlanVariables):
    """The variables of one plan's program with the constraints the game's rules hold them to, which `_add_constraints`
    adds: what starts is paid for, workers mine, collect or build, supply is there for what takes it, a producer makes
    one thing at a time, an add-on has its host and a morph its townhall, and nothing starts before what it needs."""

    __slots__ = ()

    def _constrain_at_most(
        self, used: RowExpression, available: RowExpression, *, most_used: float | None = None
    ) -> None:
        """Hold `used` at or below `available` in each row. Where what is now already uses more, that would leave no
        plan, so the row holds what the plan uses at no more than it makes available, which forgives the shortfall for
        good. With `most_used`, the most `used` can come to, it is not forgiven: nothing more is used in such a row
        unless `used` is at or below `available` there, at the cost of a whole variable for the row."""
        excess = RowExpression(used.rows).add_expression(used).add_expression(available, -1.0)
        shortfall = np.maximum(excess.constant, 0.0)
        limit = np.maximum(-excess.constant, 0.0)
        excess.constant = np.zeros(used.rows)
        is_short = shortfall > 0.0
        if most_used is not None and is_short.any():
            shortfall_made_up = self._program.add_variables(int(is_short.sum()), upper=1.0, integral=True)
            excess.add(shortfall_made_up, np.diag(shortfall)[:, is_short])
            more_used = used.selected(is_short)
            most_more_used = np.maximum(most_used - more_used.constant, 0.0)
            more_used.constant = np.zeros(more_used.rows)
            self._program.constrain(more_used.add(shortfall_made_up, -np.diag(most_more_used)), upper=0.0)
        self._program.constrain(excess, upper=limit)

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
        what starts paid from the stock at the interval's start and the spendable fraction of its income."""
        intervals = self._interval_count
        mineral_income, vespene_income = self._incomes_a_second()
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
            paid.add_expression(income, self._spendable_income_fraction * self._widths).add_expression(spent, -1.0)
            self._program.constrain(paid, lower=0.0)

    def _incomes_a_second(self) -> tuple[RowExpression, RowExpression]:
        """The minerals and the vespene that come in a second in each interval: what the workers bring in, and for
        minerals the MULEs of the orbital commands alive at the interval's start."""
        mineral_income = self._income_of(self._mining, self._started_mining)
        if mule_rate := self._economy.mule_minerals_per_orbital_second:
            mineral_income.add_expression(self._count_alive(UnitTypeId.ORBITAL_COMMAND, at_end=False), mule_rate)
        return mineral_income, self._income_of(self._collecting, self._started_collecting)

    def _income_of(self, tiers: Sequence[WorkerTier], started: Sequence[VariableBlock]) -> RowExpression:
        """What the workers in `tiers` bring in a second in each interval, less what those `started` in a tier do not
        yet: nothing for the economy's `initial_harvest_delay` from the start of the interval they start in."""
        income = RowExpression(self._interval_count)
        for tier in tiers:
            income.add(tier.block, np.eye(self._interval_count) * tier.per_worker_second)
        for tier, started_in_tier in zip(tiers, started, strict=False):
            income.add(started_in_tier, -tier.per_worker_second * self._initial_harvest_delay_shares())
        return income

    def _constrain_workers(self) -> None:
        """Miners, collectors and each builder's share of an interval at most the SCVs alive at its start; with no
        walks, the builders counted whole where an interval is shorter than every build; and the workers that start to
        mine or to collect held at what the miners and collectors rise by."""
        intervals = self._interval_count
        working = self._working_in((*self._mining, *self._collecting))
        builders = RowExpression(intervals)
        built = [(index, item) for index, item in enumerate(self._items) if item.is_built_by_worker]
        for index, item in built:
            walk_there, walk_back = self._walk_seconds_of(item), self._walk_back_seconds_of(item)
            busy_share = self._busy_seconds(index, before=walk_there, after=walk_back) / self._widths[:, None]
            self._add_starts_to(builders, index, busy_share)
        for free_after in self._builders_free_after:
            builders.constant += self._busy_seconds_until(free_after) / self._widths
        self._constrain_work_starts()
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

    def _constrain_work_starts(self) -> None:
        """Hold the workers that start to mine or to collect in each interval at no fewer than those of each tier rise
        by: over what the interval before had left of them, as its SCVs survive, or over those of the state, where every
        SCV that neither collects nor builds mines. A builder back from its structure is one of them, at minerals or
        vespene alike."""
        if not self._started_mining and not self._started_collecting:
            return
        state = self._state
        collecting_now = float(state.vespene_collectors)
        building_now = len(self._builders_free_after)
        mining_now = max(state.units.get(UnitTypeId.SCV, 0) - collecting_now - building_now, 0.0)
        self._hold_starts_at_rises(
            self._collecting,
            self._started_collecting,
            working_now=collecting_now,
            sites=state.gas_buildings_with_vespene,
        )
        self._hold_starts_at_rises(
            self._mining,
            self._started_mining,
            working_now=mining_now,
            sites=state.mineral_fields,
        )

    def _hold_starts_at_rises(
        self,
        tiers: Sequence[WorkerTier],
        started: Sequence[VariableBlock],
        *,
        working_now: float,
        sites: int,
    ) -> None:
        """Hold the workers `started` in each of `tiers` in each interval at no fewer than the tier rises by, from the
        `working_now` in them, as many in each tier as the `sites` now take, the better tiers first."""
        intervals = self._interval_count
        survival = self._survival.share_surviving(UnitTypeId.SCV, self._edges[:-1], self._edges[1:])
        risen_by = np.eye(intervals) - np.eye(intervals, k=-1) * survival[None, :]
        for tier, started_in_tier in zip(tiers, started, strict=True):
            in_tier_now = min(working_now, float(tier.workers_per_site * sites))
            working_now -= in_tier_now
            risen = RowExpression(intervals).add(tier.block, risen_by).add(started_in_tier, -np.eye(intervals))
            risen.constant[0] = -in_tier_now
            self._program.constrain(risen, upper=0.0)

    def _initial_harvest_delay_shares(self) -> FloatArray:
        """The share of each interval, by row, within the economy's `initial_harvest_delay` of each interval's start, by
        column: what a worker that starts work at that start loses of it."""
        started_at = self._edges[:-1][None, :]
        overlap_start = np.maximum(started_at, self._edges[:-1][:, None])
        overlap_end = np.minimum(started_at + self._economy.initial_harvest_delay.seconds, self._edges[1:][:, None])
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
        for unit_type in self._survival.unit_types_lost:
            supply = self._model.game_data.units[unit_type].supply_used
            if supply:
                used.add_expression(self._count_alive(unit_type, at_end=False, survive=False), -supply)
                used.add_expression(self._count_alive(unit_type, at_end=False), supply)
        self._constrain_at_most(used, cap, most_used=SUPPLY_LIMIT)
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
        for producer in sorted(producers):
            add_on = self._model.tech_lab_of(producer) if on_tech_labs else self._model.reactor_of(producer)
            lane_types = [add_on] if on_tech_labs else [producer, add_on]
            for lane_type in lane_types:
                if lane_type is not None:
                    lanes.add_expression(self._count_alive(lane_type, at_end=False))
        producer_activities = [
            occupation
            for occupation in self._state.producer_activities
            if occupation.producer in producers and (occupation.needs_tech_lab or not on_tech_labs)
        ]
        busy = RowExpression(intervals)
        running = RowExpression(intervals)
        for index in members:
            busy_seconds = self._busy_seconds(index)
            self._add_starts_to(busy, index, busy_seconds)
            self._add_starts_to(running, index, (busy_seconds > SECONDS_TOLERANCE).astype(float))
        for occupation in producer_activities:
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
        fields, geysers = self._fields_and_geysers_of_new_bases()
        fields.constant += self._state.mineral_fields
        refineries = self._made_since_start(UnitTypeId.REFINERY)
        refineries.constant += self._state.gas_buildings_with_vespene
        for tiers, sites in ((self._mining, fields), (self._collecting, refineries)):
            for tier in tiers:
                working = RowExpression(intervals).add(tier.block, np.eye(intervals))
                self._constrain_at_most(working, RowExpression(intervals).add_expression(sites, tier.workers_per_site))
        self._constrain_add_ons_and_morphs()
        for index, item in enumerate(self._items):
            limit = None if item.made is None else self._start_limits.get(item.made)
            if limit is not None:
                self._program.constrain(self._starts_over_the_plan(index), upper=limit)
        if (command_center := self._index_of_unit_type.get(UnitTypeId.COMMAND_CENTER)) is not None:
            self._program.constrain(self._starts_over_the_plan(command_center), upper=float(len(self._new_bases)))
        if (refinery := self._index_of_unit_type.get(UnitTypeId.REFINERY)) is not None:
            geysers.constant += self._state.free_geysers
            self._constrain_at_most(self._started_so_far(refinery), geysers)
        for index, item in enumerate(self._items):
            if item.upgrade is not None:
                self._program.constrain(self._starts_over_the_plan(index), upper=1.0)

    def _fields_and_geysers_of_new_bases(self) -> tuple[RowExpression, RowExpression]:
        """The mineral fields and the geysers that the townhalls done since the plan's start bring by each interval's
        start. One in production brings what the state says of it, from when it is done. Of those the plan starts,
        the first done brings the first new base's, and so on. A townhall's fields count only from the economy's
        `new_base_walk_time` after it is done: of new bases that bring different fields, the fewest any brings.

        New bases that all bring the same take no variable. Otherwise each has a variable an interval, how much of it
        is taken: all of them together as many as the plan's townhalls done, and each base no more than the one
        before it."""
        intervals = self._interval_count
        walk = self._economy.new_base_walk_time.seconds
        fields = RowExpression(intervals)
        geysers = RowExpression(intervals)
        for build in self._state.worker_builds:
            done_after = self._seconds_until_built(build)
            fields.constant += build.mineral_fields * self._done_by(done_after + walk, at_end=False)
            geysers.constant += build.geysers * self._done_by(done_after, at_end=False)
        command_center = self._index_of_unit_type.get(UnitTypeId.COMMAND_CENTER)
        if command_center is None:
            return fields, geysers
        done = self._starts_done(command_center, at_end=False)
        bases = self._new_bases
        if bases and walk > 0:
            walked_to = self._starts_done(command_center, at_end=False, later_by=walk)
            still_walking = RowExpression(intervals).add_expression(done).add_expression(walked_to, -1.0)
            fields.add_expression(still_walking, -min(base.mineral_fields for base in bases))
        if len(set(bases)) <= 1:
            for base in bases[:1]:
                fields.add_expression(done, base.mineral_fields)
                geysers.add_expression(done, base.geysers)
            return fields, geysers
        each_interval = np.eye(intervals)
        taken_in_all = RowExpression(intervals)
        taken = [self._program.add_variables(intervals, upper=1.0) for _ in bases]
        for base, taken_of_it in zip(bases, taken, strict=True):
            fields.add(taken_of_it, each_interval * base.mineral_fields)
            geysers.add(taken_of_it, each_interval * base.geysers)
            taken_in_all.add(taken_of_it, each_interval)
        self._program.constrain(taken_in_all.add_expression(done, -1.0), lower=0.0, upper=0.0)
        for position in range(1, len(bases)):
            self._take_a_base_after_the_one_before(
                bases[position], taken[position], base_before=bases[position - 1], taken_before=taken[position - 1]
            )
        return fields, geysers

    def _take_a_base_after_the_one_before(
        self, base: NewBase, taken: VariableBlock, *, base_before: NewBase, taken_before: VariableBlock
    ) -> None:
        """Hold `base`, of which `taken` is taken in each interval, no more taken than `base_before`, of which
        `taken_before` is. Where it brings more fields or geysers than the one before, the solver would rather take a
        share of each than the earlier whole: a whole variable an interval then holds that none of it is taken before
        all of the one before."""
        intervals = self._interval_count
        each_interval = np.eye(intervals)
        if base.mineral_fields <= base_before.mineral_fields and base.geysers <= base_before.geysers:
            more_of_it = RowExpression(intervals).add(taken, each_interval).add(taken_before, -each_interval)
            self._program.constrain(more_of_it, upper=0.0)
            return
        is_before_whole = self._program.add_variables(intervals, upper=1.0, integral=True)
        any_of_it = RowExpression(intervals).add(taken, each_interval).add(is_before_whole, -each_interval)
        self._program.constrain(any_of_it, upper=0.0)
        not_whole = RowExpression(intervals).add(is_before_whole, each_interval).add(taken_before, -each_interval)
        self._program.constrain(not_whole, upper=0.0)

    def _constrain_add_ons_and_morphs(self) -> None:
        """At most one add-on a host, those standing and those in production counted, and at most one morph a
        command center, those in production counted: of every type that takes the host, the plan's items or not."""
        sources: dict[UnitTypeId, list[int]] = {}
        for index, item in enumerate(self._items):
            if item.unit_type is not None and self._model.is_add_on(item.unit_type):
                for host in sorted(item.producers):
                    sources.setdefault(host, []).append(index)
            elif item.morphed_from is not None:
                sources.setdefault(item.morphed_from, []).append(index)
        for source, indices in sorted(sources.items()):
            is_host = self._items[indices[0]].morphed_from is None
            taking_it = self._add_ons_of(source) if is_host else self._types_morphed_from(source)
            started = RowExpression(self._interval_count)
            for index in indices:
                started.add_expression(self._started_so_far(index))
            started.constant += sum(unit_type in taking_it for unit_type, _ in self._completions)
            if is_host:
                started.constant += sum(self._state.units.get(add_on, 0) for add_on in taking_it)
            available = self._made_since_start(source)
            available.constant += self._state.units.get(source, 0)
            self._constrain_at_most(started, available)

    def _add_ons_of(self, host: UnitTypeId) -> frozenset[UnitTypeId]:
        """The add-on types `host` builds: its tech lab and its reactor."""
        add_ons = (self._model.tech_lab_of(host), self._model.reactor_of(host))
        return frozenset(add_on for add_on in add_ons if add_on is not None)

    def _types_morphed_from(self, unit_type: UnitTypeId) -> frozenset[UnitTypeId]:
        """The types a unit of `unit_type` morphs into."""
        unit_items = self._model.unit_items.items()
        return frozenset(morphed for morphed, item in unit_items if item.morphed_from is unit_type)

    def _constrain_prerequisites(self) -> None:
        """No item starts in an interval before the techs and upgrades it needs are done by the interval's start: before
        the unlock fraction of a structure granting each tech, and of each upgrade, is; with one of 0, in the share
        that the fractions done of them allow."""
        met: dict[frozenset[UnitTypeId] | UpgradeId, RowExpression] = {}
        for index, item in enumerate(self._items):
            needed: list[RowExpression] = []
            for tech in sorted(item.techs, key=lambda tech: tech.name):
                granting = self._model.types_granting(tech)
                if any(self._state.units.get(unit_type, 0) for unit_type in granting):
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
            for upgrade in sorted(item.upgrades_needed):
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
