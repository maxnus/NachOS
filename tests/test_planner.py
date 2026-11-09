"""The build planner: its grid, its model of Terran, plans on tiny games whose best plan is known, the state it reads
off a made-up game, and the extension itself."""

import dataclasses
import math
from collections.abc import Callable
from typing import Any

import pytest
from s2clientprotocol import common_pb2, raw_pb2, sc2api_pb2

from sc2nachos import Api, GameTime
from sc2nachos.events import TurnEvent
from sc2nachos.extensions.planner import (
    Coverage,
    CurveSegment,
    Economy,
    Floor,
    GameState,
    Occupation,
    Plan,
    PlannedItem,
    Planner,
    Reward,
    TimeGrid,
    WorkerBuild,
)
from sc2nachos.extensions.planner._economy import rate_tiers, walk_seconds_of
from sc2nachos.extensions.planner._plan_program import PlanProgram
from sc2nachos.extensions.planner._planner import whole_until_seconds_of
from sc2nachos.extensions.planner._terran_model import TerranModel
from sc2nachos.ids import UnitTypeId, UpgradeId
from sc2nachos.ids.raw import RawAbilityId
from sc2nachos.testing import load_game_data
from sc2nachos.units import Alliance
from support import ScriptedGame, make_game_info, make_observation, make_unit, shipped_game_data

_TERRAN_MODEL = TerranModel(load_game_data())
_TWO_SECOND_BUCKETS = TimeGrid.uniform(GameTime(seconds=2))
# Enough branching for every tiny game here to be proven best, the same on every machine.
_NODE_LIMIT = 20_000
# The economy the known plans are worked out with: the default, but an SCV walking 6 seconds to every structure.
_WALKING_6_SECONDS_TO_EVERY_STRUCTURE = Economy(walk_time=lambda _: GameTime(seconds=6))


def _game_start(**changes: Any) -> GameState:
    """The state a game starts in: twelve SCVs mining a main's eight fields beside its command center, its two geysers
    free, 50 minerals and 12 of 15 supply, then what `changes` says."""
    state = GameState(
        time=GameTime.ZERO,
        minerals=50.0,
        vespene=0.0,
        supply_used=12.0,
        supply_cap=15.0,
        counts={UnitTypeId.SCV: 12, UnitTypeId.COMMAND_CENTER: 1},
        mineral_fields=8,
        free_geysers=2,
    )
    return dataclasses.replace(state, **changes)


def _best_plan(
    state: GameState,
    reward: Reward,
    *,
    horizon_seconds: float,
    grid: TimeGrid = _TWO_SECOND_BUCKETS,
    whole_until: Callable[[UnitTypeId], GameTime | None] | None = None,
    economy: Economy = _WALKING_6_SECONDS_TO_EVERY_STRUCTURE,
    loss_rates: dict[UnitTypeId, float] | None = None,
    new_command_center_limit: int | None = None,
) -> Plan:
    """The best plan for `reward` from `state`, over `horizon_seconds` on `grid` with the windows of `whole_until`, by
    `economy`, with `loss_rates` and `new_command_center_limit`, proven best."""
    items = _TERRAN_MODEL.items_for(unit_types=reward.unit_types, upgrades=reward.upgrade_weights)
    program = PlanProgram(
        items=items,
        model=_TERRAN_MODEL,
        edges=grid.edges(GameTime(seconds=horizon_seconds)),
        state=state,
        reward=reward,
        economy=economy,
        loss_rates=loss_rates or {},
        new_command_center_limit=new_command_center_limit,
        whole_until_seconds=whole_until_seconds_of(items, whole_until=whole_until),
    )
    plan = program.solve(time_limit=None, node_limit=_NODE_LIMIT)
    assert plan is not None
    assert plan.is_optimal
    return plan


def _whole_over_any_horizon(_: UnitTypeId) -> GameTime:
    """Every unit type whole over any horizon."""
    return GameTime.INFINITE


def _start_seconds(plan: Plan, item: UnitTypeId | UpgradeId) -> list[float]:
    """The seconds after the plan's start at which it starts each `item`, to a millionth."""
    return [round((planned.start - plan.made_at).seconds, 6) for planned in plan.items if planned.item == item]


class TestTimeGrid:
    def test_the_doubling_grid_runs_2_second_buckets_to_0_45_doubling_to_32_seconds_63_to_10_minutes(self) -> None:
        edges = TimeGrid.doubling().edges(GameTime(seconds=600))
        widths = [later - earlier for earlier, later in zip(edges, edges[1:], strict=False)]

        assert len(widths) == 63
        assert edges[:3] == (0.0, 2.0, 4.0)
        assert edges[22:24] == (44.0, 48.0)
        assert edges[-3:] == (544.0, 576.0, 600.0)
        assert sorted(set(widths)) == [2.0, 4.0, 8.0, 16.0, 24.0, 32.0]

    def test_a_segments_buckets_follow_while_the_next_ends_by_its_time_and_the_last_is_cut_at_the_horizon(self) -> None:
        grid = TimeGrid(
            segments=[(GameTime(seconds=10), GameTime(seconds=25)), (GameTime(seconds=4), GameTime.INFINITE)]
        )
        assert grid.edges(GameTime(seconds=33)) == (0.0, 10.0, 20.0, 24.0, 28.0, 32.0, 33.0)

    def test_past_the_last_segment_one_bucket_runs_to_the_horizon(self) -> None:
        grid = TimeGrid(segments=[(GameTime(seconds=10), GameTime(seconds=20))])
        assert grid.edges(GameTime(seconds=50)) == (0.0, 10.0, 20.0, 50.0)

    @pytest.mark.parametrize(
        "segments",
        [
            [],
            [(GameTime.ZERO, GameTime.INFINITE)],
            [(GameTime(seconds=2), GameTime(seconds=10)), (GameTime(seconds=4), GameTime(seconds=10))],
        ],
        ids=["no segments", "a bucket of 0", "a segment not after the one before"],
    )
    def test_a_grid_that_cannot_be_laid_out_raises(self, segments: list[tuple[GameTime, GameTime]]) -> None:
        with pytest.raises(ValueError):
            TimeGrid(segments=segments)

    @pytest.mark.parametrize("horizon", [GameTime.ZERO, GameTime.INFINITE])
    def test_a_horizon_not_longer_than_0_or_infinite_raises(self, horizon: GameTime) -> None:
        with pytest.raises(ValueError, match="horizon"):
            TimeGrid.doubling().edges(horizon)


class TestTerranModel:
    def test_a_reward_on_marines_marauders_and_medivacs_needs_the_toys_items(self) -> None:
        items = _TERRAN_MODEL.items_for(
            unit_types=[UnitTypeId.MARINE, UnitTypeId.MARAUDER, UnitTypeId.MEDIVAC], upgrades=[]
        )
        assert {item.unit_type for item in items} == {
            UnitTypeId.SCV,
            UnitTypeId.SUPPLY_DEPOT,
            UnitTypeId.COMMAND_CENTER,
            UnitTypeId.ORBITAL_COMMAND,
            UnitTypeId.REFINERY,
            UnitTypeId.BARRACKS,
            UnitTypeId.TECH_LAB_BARRACKS,
            UnitTypeId.REACTOR_BARRACKS,
            UnitTypeId.FACTORY,
            UnitTypeId.STARPORT,
            UnitTypeId.REACTOR_STARPORT,
            UnitTypeId.MARINE,
            UnitTypeId.MARAUDER,
            UnitTypeId.MEDIVAC,
        }

    def test_an_upgrade_brings_its_researcher_and_the_upgrades_and_techs_it_needs(self) -> None:
        items = _TERRAN_MODEL.items_for(unit_types=[], upgrades=[UpgradeId.TERRAN_INFANTRY_WEAPONS_2])
        assert {item.upgrade for item in items if item.upgrade is not None} == {
            UpgradeId.TERRAN_INFANTRY_WEAPONS_1,
            UpgradeId.TERRAN_INFANTRY_WEAPONS_2,
        }
        assert {UnitTypeId.ENGINEERING_BAY, UnitTypeId.ARMORY} <= {item.unit_type for item in items}

    def test_a_form_counts_as_the_type_it_switches_from_at_no_cost(self) -> None:
        assert _TERRAN_MODEL.usual_form(UnitTypeId.BARRACKS_FLYING) == UnitTypeId.BARRACKS
        assert _TERRAN_MODEL.usual_form(UnitTypeId.SUPPLY_DEPOT_LOWERED) == UnitTypeId.SUPPLY_DEPOT
        assert _TERRAN_MODEL.usual_form(UnitTypeId.REFINERY_RICH) == UnitTypeId.REFINERY
        assert _TERRAN_MODEL.usual_form(UnitTypeId.ORBITAL_COMMAND) == UnitTypeId.ORBITAL_COMMAND

    def test_an_orbital_command_is_a_morph_of_a_command_center_that_adds_no_supply(self) -> None:
        orbital = _TERRAN_MODEL.unit_items[UnitTypeId.ORBITAL_COMMAND]
        assert orbital.morphed_from == UnitTypeId.COMMAND_CENTER
        assert orbital.producers == {UnitTypeId.COMMAND_CENTER}
        assert orbital.supply_provided == 0.0
        assert (orbital.minerals, orbital.build_seconds) == (150.0, 25.0)

    def test_a_unit_starts_whole_by_default_60_seconds_each_100_resources_at_least_30_and_an_upgrade_always(
        self,
    ) -> None:
        windows = {unit_type: item.default_whole_until for unit_type, item in _TERRAN_MODEL.unit_items.items()}
        assert windows[UnitTypeId.COMMAND_CENTER] == GameTime(seconds=240)
        assert windows[UnitTypeId.FACTORY] == GameTime(seconds=150)
        assert windows[UnitTypeId.ORBITAL_COMMAND] == GameTime(seconds=90)
        assert windows[UnitTypeId.SUPPLY_DEPOT] == GameTime(seconds=60)
        assert windows[UnitTypeId.SCV] == windows[UnitTypeId.MARINE] == GameTime(seconds=30)
        assert all(item.default_whole_until == GameTime.INFINITE for item in _TERRAN_MODEL.upgrade_items.values())

    @pytest.mark.parametrize("unit_type", [UnitTypeId.ZERGLING, UnitTypeId.MULE, UnitTypeId.AUTO_TURRET])
    def test_a_type_terran_does_not_make_raises(self, unit_type: UnitTypeId) -> None:
        with pytest.raises(ValueError, match=unit_type.name):
            _TERRAN_MODEL.items_for(unit_types=[unit_type], upgrades=[])


class TestKnownPlans:
    def test_from_50_minerals_and_twelve_scvs_the_first_depot_starts_at_8_seconds(self) -> None:
        """The first SCV takes the 50 minerals at once, and the depot's SCV, walking 6 seconds, leaves at 2; a depot
        worth a little gets built as soon as it can be paid."""
        reward = Reward(weights={UnitTypeId.SCV: 1.0, UnitTypeId.SUPPLY_DEPOT: 0.01})
        plan = _best_plan(_game_start(), reward, horizon_seconds=40)

        assert _start_seconds(plan, UnitTypeId.SCV)[:2] == [0.0, 14.0]
        assert _start_seconds(plan, UnitTypeId.SUPPLY_DEPOT)[0] == 8.0

    @pytest.mark.parametrize(
        ("reactors", "marine_starts"),
        [(0, [0.0, 18.0, 36.0]), (1, [0.0, 0.0, 18.0, 18.0, 36.0, 36.0])],
        ids=["one at a time", "two with a reactor"],
    )
    def test_one_barracks_trains_one_marine_at_a_time_and_two_with_a_reactor(
        self, reactors: int, marine_starts: list[float]
    ) -> None:
        """With no SCV to build more, and too little time for a reactor to pay, over a minute: a marine started later
        would not be done by its end, and so be worth nothing."""
        state = _game_start(
            minerals=1000.0,
            supply_used=0.0,
            supply_cap=100.0,
            counts={UnitTypeId.BARRACKS: 1, UnitTypeId.REACTOR_BARRACKS: reactors},
            mineral_fields=0,
        )
        plan = _best_plan(state, Reward(weights={UnitTypeId.MARINE: 1.0}), horizon_seconds=60)
        assert _start_seconds(plan, UnitTypeId.MARINE) == marine_starts

    def test_a_command_center_starts_at_48_seconds_after_uninterrupted_scv_production(self) -> None:
        """SCVs worth more than a command center, which is built as soon as it does not hold one up, on the doubling
        grid, every start whole. An SCV starts each 14 seconds, its 12.14 rounded up to whole 2-second buckets."""
        reward = Reward(weights={UnitTypeId.SCV: 2.0, UnitTypeId.COMMAND_CENTER: 1.0})
        plan = _best_plan(
            _game_start(), reward, horizon_seconds=150, grid=TimeGrid.doubling(), whole_until=_whole_over_any_horizon
        )

        assert _start_seconds(plan, UnitTypeId.COMMAND_CENTER)[0] == 48.0
        assert _start_seconds(plan, UnitTypeId.SCV)[:5] == [0.0, 14.0, 28.0, 42.0, 56.0]

    @pytest.mark.parametrize(
        ("walks_there", "depot_done", "scv_start"),
        [(False, 22.0, 74.0), (True, 28.0, 80.0)],
        ids=["its SCV building it", "its SCV walking there first"],
    )
    def test_a_structure_an_scv_builds_is_done_after_any_walk_there_and_keeps_it_until_its_walk_back(
        self, walks_there: bool, depot_done: float, scv_start: float
    ) -> None:
        """The one SCV, with 21.43 seconds of a depot to build and 6 to walk each way, mines from 27.43 seconds, or
        33.43 if it walks there first, and the next SCV's 50 minerals take it 45.65 seconds more, less half its
        bucket's."""
        depot = _TERRAN_MODEL.unit_items[UnitTypeId.SUPPLY_DEPOT].build_seconds
        build = WorkerBuild(
            unit_type=UnitTypeId.SUPPLY_DEPOT, time_left=GameTime(seconds=depot), builder_walks_there=walks_there
        )
        state = _game_start(
            minerals=0.0,
            supply_used=1.0,
            counts={UnitTypeId.SCV: 1, UnitTypeId.COMMAND_CENTER: 1},
            worker_builds=(build,),
        )
        plan = _best_plan(
            state, Reward(weights={UnitTypeId.SCV: 1.0}), horizon_seconds=100, whole_until=_whole_over_any_horizon
        )

        depot_counts = zip(plan.times, plan.counts[UnitTypeId.SUPPLY_DEPOT], strict=False)
        assert next(time for time, count in depot_counts if count >= 1) == GameTime(seconds=depot_done)
        assert _start_seconds(plan, UnitTypeId.SCV) == [scv_start]

    def test_the_plans_curves_start_from_the_state_and_its_times_at_the_grids_edges(self) -> None:
        state = _game_start(time=GameTime(seconds=30))
        plan = _best_plan(state, Reward(weights={UnitTypeId.SCV: 1.0}), horizon_seconds=14)

        assert plan.made_at == GameTime(seconds=30)
        assert plan.times == tuple(GameTime(seconds=seconds) for seconds in range(30, 45, 2))
        assert plan.minerals[0] == 50.0
        assert plan.vespene[0] == 0.0
        assert plan.counts[UnitTypeId.SCV][0] == 12.0
        assert plan.items[0] == PlannedItem(item=UnitTypeId.SCV, start=GameTime(seconds=30))

    def test_a_unit_counts_as_it_survives_its_loss_rate(self) -> None:
        """No barracks, so no marine is made."""
        state = _game_start(counts={UnitTypeId.MARINE: 10})
        plan = _best_plan(
            state, Reward(weights={UnitTypeId.MARINE: 1.0}), horizon_seconds=10, loss_rates={UnitTypeId.MARINE: 0.01}
        )
        assert plan.counts[UnitTypeId.MARINE] == pytest.approx([10 * math.exp(-0.01 * 2 * k) for k in range(6)])

    def test_covering_air_buys_marines_not_marauders(self) -> None:
        """Marauders, which cannot shoot up, cover none of it. Two barracks with tech labs make two at a time, and over
        40 seconds those started at 36 would not be done."""
        state = _game_start(
            minerals=2000.0,
            vespene=1000.0,
            supply_used=0.0,
            supply_cap=100.0,
            counts={UnitTypeId.BARRACKS: 2, UnitTypeId.TECH_LAB_BARRACKS: 2},
            mineral_fields=0,
        )
        air = Coverage(
            enemy_counts=[(GameTime.ZERO, 10.0)],
            effectiveness={UnitTypeId.MARINE: 1.0, UnitTypeId.MARAUDER: 0.0},
            value=1.0,
        )
        plan = _best_plan(state, Reward(coverages=[air], weights={UnitTypeId.MARAUDER: 0.01}), horizon_seconds=40)
        assert _start_seconds(plan, UnitTypeId.MARINE) == [0.0, 0.0, 18.0, 18.0]
        assert _start_seconds(plan, UnitTypeId.MARAUDER) == []

    def test_a_floor_buys_what_it_wants_by_its_time(self) -> None:
        state = _game_start(
            minerals=500.0, supply_used=0.0, supply_cap=100.0, counts={UnitTypeId.BARRACKS: 1}, mineral_fields=0
        )
        floor = Floor(unit_type=UnitTypeId.MARINE, counts=[(GameTime(seconds=36), 2.0)], penalty=10.0)

        without = _best_plan(state, Reward(weights={UnitTypeId.SUPPLY_DEPOT: 1.0}), horizon_seconds=40)
        held = _best_plan(state, Reward(weights={UnitTypeId.SUPPLY_DEPOT: 1.0}, floors=[floor]), horizon_seconds=40)

        assert _start_seconds(without, UnitTypeId.MARINE) == []
        assert _start_seconds(held, UnitTypeId.MARINE) == [0.0, 18.0]

    def test_a_concave_curve_buys_up_to_where_its_slope_falls_below_nothing(self) -> None:
        state = _game_start(
            minerals=1000.0,
            supply_used=0.0,
            supply_cap=100.0,
            counts={UnitTypeId.BARRACKS: 1, UnitTypeId.REACTOR_BARRACKS: 1},
            mineral_fields=0,
        )
        curve = [CurveSegment(slope=1.0, up_to=3), CurveSegment(slope=-0.1)]
        plan = _best_plan(state, Reward(curves={UnitTypeId.MARINE: curve}), horizon_seconds=40)
        assert len(_start_seconds(plan, UnitTypeId.MARINE)) == 3

    def test_a_hysteresis_bonus_keeps_to_the_previous_plans_items(self) -> None:
        """A barracks nothing else is worth gets built by when the previous plan had it, for a bonus above its cost in
        what an SCV's mining would have been worth."""
        previous = Plan(
            made_at=GameTime.ZERO,
            items=(PlannedItem(item=UnitTypeId.BARRACKS, start=GameTime(seconds=30)),),
            times=(),
            minerals=(),
            vespene=(),
            counts={},
            objective=0.0,
            gap=0.0,
            is_optimal=True,
            solve_seconds=0.0,
        )
        state = _game_start(minerals=500.0, supply_cap=23.0, counts={UnitTypeId.SCV: 12, UnitTypeId.SUPPLY_DEPOT: 1})
        reward = Reward(weights={UnitTypeId.SCV: 1.0})

        without = _best_plan(state, reward, horizon_seconds=40)
        kept = _best_plan(
            state, dataclasses.replace(reward, previous_plan=previous, hysteresis_bonus=100.0), horizon_seconds=40
        )

        assert _start_seconds(without, UnitTypeId.BARRACKS) == []
        assert len(_start_seconds(kept, UnitTypeId.BARRACKS)) == 1
        assert _start_seconds(kept, UnitTypeId.BARRACKS)[0] <= 30.0


class TestWholeUntil:
    def test_a_unit_type_has_what_whole_until_returns_or_its_default_for_none_and_an_upgrade_its_default(self) -> None:
        items = _TERRAN_MODEL.items_for(unit_types=[UnitTypeId.MARINE], upgrades=[UpgradeId.STIMPACK])
        asked: list[UnitTypeId] = []

        def whole_until(unit_type: UnitTypeId) -> GameTime | None:
            asked.append(unit_type)
            return GameTime(seconds=20) if unit_type == UnitTypeId.SUPPLY_DEPOT else None

        seconds = whole_until_seconds_of(items, whole_until=whole_until)

        seconds_by_name = {item.name: window for item, window in zip(items, seconds, strict=True)}
        assert seconds_by_name["SUPPLY_DEPOT"] == 20.0
        assert seconds_by_name["COMMAND_CENTER"] == 240.0
        assert seconds_by_name["STIMPACK"] == math.inf
        assert asked == [item.unit_type for item in items if item.unit_type is not None]
        assert whole_until_seconds_of(items, whole_until=None) == tuple(
            item.default_whole_until.seconds for item in items
        )

    def test_a_negative_time_raises(self) -> None:
        items = _TERRAN_MODEL.items_for(unit_types=[UnitTypeId.MARINE], upgrades=[])
        with pytest.raises(ValueError, match="SCV"):
            whole_until_seconds_of(
                items, whole_until=lambda unit_type: GameTime(seconds=-1) if unit_type == UnitTypeId.SCV else None
            )

    def test_starts_are_whole_in_the_buckets_that_start_before_their_items_time(self) -> None:
        reward = Reward(weights={UnitTypeId.MARINE: 1.0})
        items = _TERRAN_MODEL.items_for(unit_types=reward.unit_types, upgrades=reward.upgrade_weights)

        def whole_numbers(whole_until: GameTime) -> int:
            program = PlanProgram(
                items=items,
                model=_TERRAN_MODEL,
                edges=_TWO_SECOND_BUCKETS.edges(GameTime(seconds=60)),
                state=_game_start(),
                reward=reward,
                economy=Economy(),
                loss_rates={},
                new_command_center_limit=None,
                whole_until_seconds=[whole_until.seconds] * len(items),
            )
            return program.linear_program.integral_count

        assert whole_numbers(GameTime.ZERO) == 0
        assert whole_numbers(GameTime(seconds=10)) == 5 * len(items)
        assert whole_numbers(GameTime.INFINITE) == 30 * len(items)


class TestEconomy:
    @pytest.mark.parametrize(
        "arguments",
        [
            {"minerals_per_miner_second": (1.0, 1.1)},
            {"vespene_per_collector_second": (-0.1,)},
            {"mule_minerals_per_orbital_second": -1.0},
            {"mineral_fields_per_new_base": -1},
            {"geysers_per_new_base": -1},
        ],
        ids=[
            "a rate above the one before",
            "a negative rate",
            "a negative MULE rate",
            "negative fields",
            "negative geysers",
        ],
    )
    def test_a_rate_above_the_one_before_or_a_negative_number_raises(self, arguments: dict[str, Any]) -> None:
        with pytest.raises(ValueError):
            Economy(**arguments)

    def test_rates_become_tiers_of_equal_rates_above_0(self) -> None:
        assert rate_tiers((1.0, 1.0, 0.5, 0.0)) == ((2, 1.0), (1, 0.5))

    def test_an_scv_walks_10_seconds_to_a_command_center_and_6_to_any_other_structure_or_what_walk_time_returns(
        self,
    ) -> None:
        unit_types = (UnitTypeId.COMMAND_CENTER, UnitTypeId.SUPPLY_DEPOT, UnitTypeId.BARRACKS)
        assert walk_seconds_of(Economy(), unit_types) == {
            UnitTypeId.COMMAND_CENTER: 10.0,
            UnitTypeId.SUPPLY_DEPOT: 6.0,
            UnitTypeId.BARRACKS: 6.0,
        }
        depots_near = Economy(walk_time=lambda t: GameTime(seconds=3) if t == UnitTypeId.SUPPLY_DEPOT else None)
        assert walk_seconds_of(depots_near, unit_types)[UnitTypeId.SUPPLY_DEPOT] == 3.0
        assert walk_seconds_of(depots_near, unit_types)[UnitTypeId.COMMAND_CENTER] == 10.0

    def test_a_negative_walk_raises(self) -> None:
        with pytest.raises(ValueError, match="BARRACKS"):
            walk_seconds_of(Economy(walk_time=lambda _: GameTime(seconds=-1)), [UnitTypeId.BARRACKS])

    @pytest.mark.parametrize(
        ("rates", "marine_start"),
        [((1.0718, 1.0718, 0.5359), 2.0), ((1.0718, 1.0718), 3.0), ((1.0718,), 6.0)],
        ids=["three a field", "two a field", "one a field"],
    )
    def test_a_worker_past_the_mining_rates_mines_nothing(self, rates: tuple[float, ...], marine_start: float) -> None:
        """24 SCVs on 8 fields and no minerals, on 1-second buckets: a start pays from the stock at its bucket's start
        and half the bucket's income, so the marine's 50 minerals come at 2, 3 and 6 seconds, mined at 21.4, 17.1 and
        8.6 a second."""
        state = _game_start(
            minerals=0.0,
            supply_used=24.0,
            supply_cap=30.0,
            counts={UnitTypeId.SCV: 24, UnitTypeId.COMMAND_CENTER: 1, UnitTypeId.BARRACKS: 1},
        )
        plan = _best_plan(
            state,
            Reward(weights={UnitTypeId.MARINE: 1.0}),
            horizon_seconds=30,
            grid=TimeGrid.uniform(GameTime(seconds=1)),
            economy=Economy(minerals_per_miner_second=rates),
        )
        assert _start_seconds(plan, UnitTypeId.MARINE) == [marine_start]

    @pytest.mark.parametrize(
        ("rates", "marauder_start"),
        [((0.9083, 0.9083, 0.9083), 9.0), ((0.9083, 0.9083), 14.0)],
        ids=["three a refinery", "two a refinery"],
    )
    def test_a_worker_past_the_collecting_rates_collects_nothing(
        self, rates: tuple[float, ...], marauder_start: float
    ) -> None:
        """No vespene and one refinery, on 1-second buckets: the marauder's 25 vespene come at 9 and 14 seconds,
        collected at 2.7 and 1.8 a second."""
        state = _game_start(
            minerals=500.0,
            counts={
                UnitTypeId.SCV: 12,
                UnitTypeId.COMMAND_CENTER: 1,
                UnitTypeId.BARRACKS: 1,
                UnitTypeId.TECH_LAB_BARRACKS: 1,
                UnitTypeId.REFINERY: 1,
            },
            refineries_with_vespene=1,
            free_geysers=1,
        )
        plan = _best_plan(
            state,
            Reward(weights={UnitTypeId.MARAUDER: 1.0}),
            horizon_seconds=40,
            grid=TimeGrid.uniform(GameTime(seconds=1)),
            economy=Economy(vespene_per_collector_second=rates),
        )
        assert _start_seconds(plan, UnitTypeId.MARAUDER) == [marauder_start]

    @pytest.mark.parametrize(
        ("mule_rate", "marine_starts"), [(225 / (50 / 0.7875), [14.0]), (0.0, [])], ids=["MULEs", "no MULEs"]
    )
    def test_an_orbital_commands_mules_mine_what_the_economy_says(
        self, mule_rate: float, marine_starts: list[float]
    ) -> None:
        """No SCV and no minerals, on 1-second buckets: the MULEs' 3.5 minerals a second pay for a marine at 14 seconds,
        and with none, nothing pays for one."""
        state = _game_start(
            minerals=0.0,
            supply_used=0.0,
            counts={UnitTypeId.ORBITAL_COMMAND: 1, UnitTypeId.BARRACKS: 1},
        )
        plan = _best_plan(
            state,
            Reward(weights={UnitTypeId.MARINE: 1.0}),
            horizon_seconds=40,
            grid=TimeGrid.uniform(GameTime(seconds=1)),
            economy=dataclasses.replace(
                _WALKING_6_SECONDS_TO_EVERY_STRUCTURE, mule_minerals_per_orbital_second=mule_rate
            ),
        )
        assert _start_seconds(plan, UnitTypeId.MARINE) == marine_starts

    def test_with_a_limit_of_no_new_command_center_none_starts(self) -> None:
        """The known plan that starts one at 48 seconds."""
        reward = Reward(weights={UnitTypeId.SCV: 2.0, UnitTypeId.COMMAND_CENTER: 1.0})
        plan = _best_plan(
            _game_start(),
            reward,
            horizon_seconds=150,
            grid=TimeGrid.doubling(),
            whole_until=_whole_over_any_horizon,
            new_command_center_limit=0,
        )
        assert _start_seconds(plan, UnitTypeId.COMMAND_CENTER) == []


class TestReward:
    def test_a_type_weighted_and_given_a_curve_raises(self) -> None:
        with pytest.raises(ValueError, match="MARINE"):
            Reward(weights={UnitTypeId.MARINE: 1.0}, curves={UnitTypeId.MARINE: [CurveSegment(slope=1.0)]})

    @pytest.mark.parametrize(
        "segments",
        [
            [],
            [CurveSegment(slope=1.0, up_to=10), CurveSegment(slope=2.0)],
            [CurveSegment(slope=1.0, up_to=10), CurveSegment(slope=0.5, up_to=5), CurveSegment(slope=0.0)],
        ],
        ids=["no segments", "a slope rising", "a count falling"],
    )
    def test_a_curve_that_is_not_concave_raises(self, segments: list[CurveSegment]) -> None:
        with pytest.raises(ValueError, match="MARINE"):
            Reward(curves={UnitTypeId.MARINE: segments})

    def test_the_last_segments_count_is_ignored(self) -> None:
        Reward(curves={UnitTypeId.MARINE: [CurveSegment(slope=1.0, up_to=30), CurveSegment(slope=0.0, up_to=0)]})

    def test_counts_not_in_order_of_time_raise(self) -> None:
        later_first = [(GameTime(seconds=60), 2.0), (GameTime(seconds=30), 1.0)]
        with pytest.raises(ValueError, match="order of time"):
            Coverage(enemy_counts=later_first, effectiveness={}, value=1.0)
        with pytest.raises(ValueError, match="order of time"):
            Floor(unit_type=UnitTypeId.MARINE, counts=later_first, penalty=1.0)

    @pytest.mark.parametrize(
        "arguments",
        [{"half_life": GameTime.ZERO}, {"terminal_time": GameTime(seconds=-1)}, {"hysteresis_bonus": 1.0}],
        ids=["a half-life of 0", "a negative terminal time", "a bonus with no previous plan"],
    )
    def test_a_bad_discount_tail_or_bonus_raises(self, arguments: dict[str, Any]) -> None:
        with pytest.raises(ValueError):
            Reward(**arguments)

    def test_the_unit_types_it_values_are_those_weighted_curved_covering_and_floored(self) -> None:
        reward = Reward(
            weights={UnitTypeId.MARINE: 1.0},
            curves={UnitTypeId.MEDIVAC: [CurveSegment(slope=1.0)]},
            coverages=[Coverage(enemy_counts=[], effectiveness={UnitTypeId.VIKING: 1.0}, value=1.0)],
            floors=[Floor(unit_type=UnitTypeId.BUNKER, counts=[], penalty=1.0)],
        )
        assert reward.unit_types == {UnitTypeId.MARINE, UnitTypeId.MEDIVAC, UnitTypeId.VIKING, UnitTypeId.BUNKER}


# A main laid out around a command center at (24.5, 24.5) as Pylon's main is, by each resource's offset (corpus), and
# a second base like it at (24.5, 52.5).
_MAIN_CENTER = (24.5, 24.5)
_NATURAL_CENTER = (24.5, 52.5)
_FIELD_OFFSETS = (
    (-0.5, -6.0),
    (6.5, 0.0),
    (2.5, -6.0),
    (6.5, -2.0),
    (0.5, -7.0),
    (5.5, -5.0),
    (7.5, -1.0),
    (6.5, -4.0),
)
_GEYSER_OFFSETS = ((7.0, 3.0), (-4.0, -7.0))
_COMMAND_CENTER_TAG = 1
_BARRACKS_TAG = 2
_TECH_LAB_TAG = 3
_REFINERY_TAG = 4
_BUILDING_SCV_TAG = 10


def _base_resources(center: tuple[float, float], tags_from: int) -> list[raw_pb2.Unit]:
    """The mineral fields and geysers of a base around a townhall at `center`, tagged from `tags_from`."""
    fields = [
        make_unit(
            tags_from + i, UnitTypeId.MINERAL_FIELD, at=(center[0] + dx, center[1] + dy), alliance=Alliance.NEUTRAL
        )
        for i, (dx, dy) in enumerate(_FIELD_OFFSETS)
    ]
    geysers = [
        make_unit(
            tags_from + 10 + i,
            UnitTypeId.VESPENE_GEYSER,
            at=(center[0] + dx, center[1] + dy),
            alliance=Alliance.NEUTRAL,
        )
        for i, (dx, dy) in enumerate(_GEYSER_OFFSETS)
    ]
    return [*fields, *geysers]


def _unit_order(ability: int, *, progress: float = 0.0, at: tuple[float, float] | None = None) -> raw_pb2.UnitOrder:
    """An order a unit shows, `progress` along, aimed at the point `at` if given."""
    order = raw_pb2.UnitOrder(ability_id=ability, progress=progress)
    if at is not None:
        order.target_world_space_pos.x, order.target_world_space_pos.y = at
    return order


def _main_base_at(step: int, *, morphing: bool = False) -> sc2api_pb2.ResponseObservation:
    """This player's main at `step`: its command center training an SCV a quarter done, or morphing into an orbital
    command if `morphing`; a barracks with a tech lab training a marauder half done and a marine queued; the tech lab
    researching Stimpack a quarter done; a refinery on the first geyser; an SCV walking to build a supply depot; and
    eleven more SCVs. 200 minerals, 25 vespene, 15 of 23 supply."""
    main_x, main_y = _MAIN_CENTER
    geyser_x, geyser_y = _GEYSER_OFFSETS[0]
    if morphing:
        townhall_order = _unit_order(RawAbilityId.UpgradeToOrbital_OrbitalCommand)
    else:
        townhall_order = _unit_order(RawAbilityId.CommandCenterTrain_SCV, progress=0.25)
    units = [
        *_base_resources(_MAIN_CENTER, 100),
        *_base_resources(_NATURAL_CENTER, 200),
        make_unit(
            _COMMAND_CENTER_TAG, UnitTypeId.COMMAND_CENTER, at=_MAIN_CENTER, build_progress=1.0, orders=[townhall_order]
        ),
        make_unit(
            _BARRACKS_TAG,
            UnitTypeId.BARRACKS,
            at=(33.5, 33.5),
            build_progress=1.0,
            add_on_tag=_TECH_LAB_TAG,
            orders=[
                _unit_order(RawAbilityId.BarracksTrain_Marauder, progress=0.5),
                _unit_order(RawAbilityId.BarracksTrain_Marine),
            ],
        ),
        make_unit(
            _TECH_LAB_TAG,
            UnitTypeId.TECH_LAB_BARRACKS,
            at=(36.0, 33.0),
            build_progress=1.0,
            orders=[_unit_order(RawAbilityId.BarracksTechLabResearch_Stimpack, progress=0.25)],
        ),
        make_unit(
            _REFINERY_TAG,
            UnitTypeId.REFINERY,
            at=(main_x + geyser_x, main_y + geyser_y),
            build_progress=1.0,
            vespene_contents=2250,
        ),
        make_unit(
            _BUILDING_SCV_TAG,
            UnitTypeId.SCV,
            at=(20.0, 30.0),
            build_progress=1.0,
            orders=[_unit_order(RawAbilityId.TerranBuild_SupplyDepot, at=(14.0, 34.0))],
        ),
        *(make_unit(20 + i, UnitTypeId.SCV, at=(22.0, 22.0), build_progress=1.0) for i in range(11)),
    ]
    common = sc2api_pb2.PlayerCommon(minerals=200, vespene=25, food_used=15, food_cap=23)
    return make_observation(step, units=units, common=common)


def _states_of(*observations: sc2api_pb2.ResponseObservation) -> list[GameState]:
    """The state of each turn of a game of `observations` on a map of open ground, as its planner reads it."""
    game = ScriptedGame(
        [*observations, make_observation(observations[-1].observation.game_loop + 1)],
        game_info=make_game_info(*["#" * 48] * 64),
        data=shipped_game_data(),
    )
    api = Api(extensions=Planner)
    states: list[GameState] = []

    @api.on(TurnEvent)
    def read_state(_: TurnEvent) -> None:
        states.append(api.extensions.game_state())

    api.play(game.joined())
    return states[: len(observations)]


def _worker_builds_in_seconds(state: GameState) -> list[tuple[UnitTypeId, float, bool]]:
    """`state`'s worker builds, each as its type, its seconds left to a hundredth, and whether its SCV walks there."""
    return [
        (build.unit_type, round(build.time_left.seconds, 2), build.builder_walks_there) for build in state.worker_builds
    ]


def _occupations_in_seconds(state: GameState) -> list[tuple[UnitTypeId, float, float, bool]]:
    """`state`'s occupations, each as its producer, its seconds until it starts and until it is done, and whether it
    needs a tech lab, in order of start."""
    return sorted(
        (
            occupation.producer,
            occupation.time_until_start.seconds,
            occupation.time_left.seconds,
            occupation.needs_tech_lab,
        )
        for occupation in state.occupations
    )


class TestGameState:
    def test_it_reads_stock_supply_and_finished_units_by_their_usual_form(self) -> None:
        (state,) = _states_of(_main_base_at(0))

        assert (state.minerals, state.vespene, state.supply_used, state.supply_cap) == (200, 25, 15, 23)
        assert state.counts == {
            UnitTypeId.SCV: 12,
            UnitTypeId.COMMAND_CENTER: 1,
            UnitTypeId.BARRACKS: 1,
            UnitTypeId.TECH_LAB_BARRACKS: 1,
            UnitTypeId.REFINERY: 1,
        }

    def test_it_reads_what_is_in_production_with_the_seconds_until_done_and_what_keeps_each_producer_busy(self) -> None:
        """A marine queued behind a marauder waits for it; the depot an SCV walks to build waits for its walk, which the
        state leaves to the economy."""
        (state,) = _states_of(_main_base_at(0))
        scv = _TERRAN_MODEL.unit_items[UnitTypeId.SCV].build_seconds
        marauder = _TERRAN_MODEL.unit_items[UnitTypeId.MARAUDER].build_seconds
        marine = _TERRAN_MODEL.unit_items[UnitTypeId.MARINE].build_seconds
        depot = _TERRAN_MODEL.unit_items[UnitTypeId.SUPPLY_DEPOT].build_seconds
        stimpack = _TERRAN_MODEL.upgrade_items[UpgradeId.STIMPACK].build_seconds

        assert sorted((unit_type, time.seconds) for unit_type, time in state.unit_completions) == pytest.approx(
            sorted(
                [
                    (UnitTypeId.SCV, 0.75 * scv),
                    (UnitTypeId.MARAUDER, 0.5 * marauder),
                    (UnitTypeId.MARINE, 0.5 * marauder + marine),
                ]
            ),
            abs=0.05,
        )
        assert {upgrade: time.seconds for upgrade, time in state.upgrade_completions.items()} == {
            UpgradeId.STIMPACK: pytest.approx(0.75 * stimpack, abs=0.05)
        }
        assert _worker_builds_in_seconds(state) == [(UnitTypeId.SUPPLY_DEPOT, round(depot, 2), True)]
        assert _occupations_in_seconds(state) == pytest.approx(
            sorted(
                [
                    (UnitTypeId.COMMAND_CENTER, 0.0, 0.75 * scv, False),
                    (UnitTypeId.BARRACKS, 0.0, 0.5 * marauder, True),
                    (UnitTypeId.TECH_LAB_BARRACKS, 0.0, 0.75 * stimpack, False),
                    (UnitTypeId.BARRACKS, 0.5 * marauder, 0.5 * marauder + marine, False),
                ]
            ),
            abs=0.05,
        )

    def test_an_scv_inside_a_refinery_which_the_observation_leaves_out_counts(self) -> None:
        before = _main_base_at(0)
        before.observation.raw_data.units.append(make_unit(31, UnitTypeId.SCV, at=(31.0, 27.0), build_progress=1.0))
        _, inside = _states_of(before, _main_base_at(1))

        assert inside.counts[UnitTypeId.SCV] == 13

    def test_it_reads_the_fields_and_free_geysers_of_its_bases_but_not_of_a_free_expansion(self) -> None:
        (state,) = _states_of(_main_base_at(0))

        assert (state.mineral_fields, state.free_geysers, state.refineries_with_vespene) == (8, 1, 1)

    def test_a_refinery_an_scv_walks_to_build_takes_its_geyser(self) -> None:
        observation = _main_base_at(0)
        free_geyser_tag = 111
        order = raw_pb2.UnitOrder(ability_id=RawAbilityId.TerranBuild_Refinery, target_unit_tag=free_geyser_tag)
        observation.observation.raw_data.units.append(
            make_unit(11, UnitTypeId.SCV, at=(24.0, 30.0), build_progress=1.0, orders=[order])
        )
        (state,) = _states_of(observation)
        refinery = _TERRAN_MODEL.unit_items[UnitTypeId.REFINERY].build_seconds

        assert state.free_geysers == 0
        assert (UnitTypeId.REFINERY, round(refinery, 2), True) in _worker_builds_in_seconds(state)

    def test_a_command_center_an_scv_walks_to_build_waits_for_its_walk(self) -> None:
        observation = _main_base_at(0)
        observation.observation.raw_data.units.append(
            make_unit(
                11,
                UnitTypeId.SCV,
                at=(24.0, 40.0),
                build_progress=1.0,
                orders=[_unit_order(RawAbilityId.TerranBuild_CommandCenter, at=_NATURAL_CENTER)],
            )
        )
        (state,) = _states_of(observation)

        assert (UnitTypeId.COMMAND_CENTER, 71.43, True) in _worker_builds_in_seconds(state)

    def test_a_command_centers_morph_counts_down_from_the_observation_before_the_first_showing_it(self) -> None:
        """Given after the observation at 0 and seen at 10 and 20 seconds, it has 5 of its 25 left at the last."""
        states = _states_of(_main_base_at(0), _main_base_at(224, morphing=True), _main_base_at(448, morphing=True))[1:]
        orbital = _TERRAN_MODEL.unit_items[UnitTypeId.ORBITAL_COMMAND].build_seconds
        left = [
            time
            for state in states
            for unit_type, time in state.unit_completions
            if unit_type == UnitTypeId.ORBITAL_COMMAND
        ]
        assert [time.seconds for time in left] == pytest.approx([orbital - 10, orbital - 20])
        assert all(
            Occupation(
                producer=UnitTypeId.COMMAND_CENTER, time_until_start=GameTime.ZERO, time_left=time, needs_tech_lab=False
            )
            in state.occupations
            for state, time in zip(states, left, strict=True)
        )


class TestPlanner:
    def test_it_plans_from_the_game_when_asked_and_holds_the_plan(self) -> None:
        game = ScriptedGame(
            [_main_base_at(0), _main_base_at(1)], game_info=make_game_info(*["#" * 48] * 64), data=shipped_game_data()
        )
        api = Api(extensions=lambda api: Planner(api, time_limit=None, node_limit=_NODE_LIMIT))
        plans: list[tuple[Plan | None, Plan]] = []

        @api.on(TurnEvent)
        def plan_marines(_: TurnEvent) -> None:
            before = api.extensions.plan
            plans.append(
                (
                    before,
                    api.extensions.plan_now(Reward(weights={UnitTypeId.MARINE: 1.0}), horizon=GameTime(seconds=60)),
                )
            )

        api.play(game.joined())

        ((before, plan),) = plans
        assert before is None
        assert api.extensions.plan is plan
        assert plan.made_at == GameTime.ZERO
        assert plan.minerals[0] == 200.0
        assert UnitTypeId.MARINE in {planned.item for planned in plan.items}

    def test_it_plans_for_terran_alone(self) -> None:
        zerg = (
            sc2api_pb2.PlayerInfo(
                player_id=1,
                type=sc2api_pb2.PlayerType.Participant,
                race_requested=common_pb2.Race.Zerg,
                race_actual=common_pb2.Race.Zerg,
            ),
            sc2api_pb2.PlayerInfo(
                player_id=2, type=sc2api_pb2.PlayerType.Computer, race_requested=common_pb2.Race.Zerg
            ),
        )
        game = ScriptedGame([make_observation(0), make_observation(1)], game_info=make_game_info(players=zerg))
        with pytest.raises(ValueError, match="Terran alone"):
            Api(extensions=Planner).play(game.joined())

    @pytest.mark.parametrize(
        "arguments", [{"time_limit": 0.0}, {"node_limit": 0}], ids=["a time limit of 0", "a node limit of 0"]
    )
    def test_a_bad_limit_raises(self, arguments: dict[str, Any]) -> None:
        game = ScriptedGame([make_observation(0), make_observation(1)])
        with pytest.raises(ValueError):
            Api(extensions=lambda api: Planner(api, **arguments)).play(game.joined())

    def test_a_negative_loss_rate_raises(self) -> None:
        game = ScriptedGame([make_observation(0), make_observation(1)], data=shipped_game_data())
        api = Api(extensions=Planner)

        @api.on(TurnEvent)
        def plan_with_a_gain(_: TurnEvent) -> None:
            api.extensions.plan_now(Reward(), horizon=GameTime(seconds=10), loss_rates={UnitTypeId.MARINE: -0.1})

        with pytest.raises(ValueError, match="loss rates"):
            api.play(game.joined())

    def test_a_negative_time_from_whole_until_raises(self) -> None:
        game = ScriptedGame([make_observation(0), make_observation(1)], data=shipped_game_data())
        api = Api(extensions=lambda api: Planner(api, whole_until=lambda _: GameTime(seconds=-1)))

        @api.on(TurnEvent)
        def plan_marines(_: TurnEvent) -> None:
            api.extensions.plan_now(Reward(weights={UnitTypeId.MARINE: 1.0}), horizon=GameTime(seconds=10))

        with pytest.raises(ValueError, match="negative time"):
            api.play(game.joined())

    def test_it_plans_from_a_state_it_is_handed(self) -> None:
        game = ScriptedGame(
            [_main_base_at(0), _main_base_at(1)], game_info=make_game_info(*["#" * 48] * 64), data=shipped_game_data()
        )
        api = Api(extensions=lambda api: Planner(api, time_limit=None, node_limit=_NODE_LIMIT))
        plans: list[Plan] = []

        @api.on(TurnEvent)
        def plan_with_nothing_in_stock(_: TurnEvent) -> None:
            state = dataclasses.replace(api.extensions.game_state(), minerals=0.0)
            reward = Reward(weights={UnitTypeId.MARINE: 1.0})
            plans.append(api.extensions.plan_now(reward, horizon=GameTime(seconds=60), state=state))

        api.play(game.joined())

        ((plan),) = plans
        assert plan.minerals[0] == 0.0

    @pytest.mark.parametrize(
        "arguments",
        [{"new_command_center_limit": -1}, {"economy": Economy(walk_time=lambda _: GameTime(seconds=-1))}],
        ids=["a negative command center limit", "a negative walk"],
    )
    def test_a_negative_command_center_limit_or_walk_raises(self, arguments: dict[str, Any]) -> None:
        game = ScriptedGame([make_observation(0), make_observation(1)], data=shipped_game_data())
        api = Api(extensions=Planner)

        @api.on(TurnEvent)
        def plan_marines(_: TurnEvent) -> None:
            api.extensions.plan_now(Reward(weights={UnitTypeId.MARINE: 1.0}), horizon=GameTime(seconds=10), **arguments)

        with pytest.raises(ValueError):
            api.play(game.joined())
