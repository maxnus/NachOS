"""The build planner: its grid, its model of Terran, plans on tiny games whose best plan is known, the state it reads
off a made-up game, and the extension itself."""

import dataclasses
import itertools
import math
from collections.abc import Callable
from typing import Any

import pytest
from s2clientprotocol import common_pb2, raw_pb2, sc2api_pb2

from sc2nachos import Api, GameTime, Resources
from sc2nachos.events import TurnEvent
from sc2nachos.extensions.planner import (
    CurveSegment,
    Economy,
    GameState,
    Occupation,
    Plan,
    PlannedItem,
    Planner,
    Reward,
    RewardTerm,
    TimeGrid,
    UnitCompletion,
    WorkerBuild,
)
from sc2nachos.extensions.planner._economy import rate_tiers, walk_seconds_of
from sc2nachos.extensions.planner._plan_program import PlanProgram
from sc2nachos.extensions.planner._planner import fluid_after_seconds_of
from sc2nachos.extensions.planner._terran_model import TerranModel
from sc2nachos.ids import UnitTypeId, UpgradeId
from sc2nachos.ids.raw import RawAbilityId
from sc2nachos.match import Race
from sc2nachos.testing import load_game_data
from sc2nachos.units import Alliance
from support import ScriptedGame, make_game_info, make_observation, make_unit, shipped_game_data

_TERRAN_MODEL = TerranModel(load_game_data())
_TWO_SECOND_INTERVALS = TimeGrid.uniform(GameTime(seconds=2))
# Enough branching for every tiny game here to be proven best, the same on every machine.
_NODE_LIMIT = 20_000
# Limits that leave a plan its units alone: no structure an SCV builds and no morph.
_NOTHING_BUT_UNITS: dict[UnitTypeId | UpgradeId, int] = {
    UnitTypeId.COMMAND_CENTER: 0,
    UnitTypeId.ORBITAL_COMMAND: 0,
    UnitTypeId.SUPPLY_DEPOT: 0,
    UnitTypeId.REFINERY: 0,
}
# The economy the known plans are worked out with: the default, but an SCV walking 6 seconds to every structure.
_WALKING_6_SECONDS_TO_EVERY_STRUCTURE = Economy(walk_time=lambda _: GameTime(seconds=6))


def _game_start(*, minerals: float = 50.0, vespene: float = 0.0, **changes: Any) -> GameState:
    """The state a game starts in: twelve SCVs mining a main's eight fields beside its command center, its two geysers
    free, 50 minerals and 12 of 15 supply, with `minerals` and `vespene` in stock, then what `changes` says."""
    state = GameState(
        time=GameTime.ZERO,
        resources=Resources(minerals, vespene),
        supply_used=12.0,
        supply_cap=15.0,
        units={UnitTypeId.SCV: 12, UnitTypeId.COMMAND_CENTER: 1},
        mineral_fields=8,
        free_geysers=2,
    )
    return dataclasses.replace(state, **changes)


def _best_plan(
    state: GameState,
    reward: Reward,
    *,
    horizon_seconds: float,
    grid: TimeGrid = _TWO_SECOND_INTERVALS,
    fluid_after: Callable[[UnitTypeId | UpgradeId], GameTime | None] | None = None,
    economy: Economy = _WALKING_6_SECONDS_TO_EVERY_STRUCTURE,
    loss_rates: dict[UnitTypeId, float] | None = None,
    start_limits: dict[UnitTypeId | UpgradeId, int] | None = None,
    max_ignored_overrun: float = 0.25,
    unlock_fraction: float = 1.0,
    previous_plan: Plan | None = None,
    hysteresis_bonus: float = 0.0,
) -> Plan:
    """The best plan for `reward` from `state`, over `horizon_seconds` on `grid` with the windows of `fluid_after`, by
    `economy`, with `loss_rates`, `start_limits`, `max_ignored_overrun` and `unlock_fraction`, keeping to
    `previous_plan` for `hysteresis_bonus`, proven best."""
    items = _TERRAN_MODEL.items_for(unit_types=reward.unit_types, upgrades=reward.upgrades)
    program = PlanProgram(
        items=items,
        model=_TERRAN_MODEL,
        edges=grid.edges(GameTime(seconds=horizon_seconds)),
        state=state,
        reward=reward,
        economy=economy,
        loss_rates=loss_rates or {},
        start_limits=start_limits or {},
        fluid_after_seconds=fluid_after_seconds_of(items, fluid_after=fluid_after, state=state, economy=economy),
        max_ignored_overrun=max_ignored_overrun,
        unlock_fraction=unlock_fraction,
        previous_plan=previous_plan,
        hysteresis_bonus=hysteresis_bonus,
    )
    plan = program.solve(time_limit=None, node_limit=_NODE_LIMIT)
    assert plan is not None
    assert plan.is_optimal
    return plan


# A plan that starts one barracks, 30 seconds after it was made at the game's start.
_A_PLAN_OF_ONE_BARRACKS = Plan(
    made_at=GameTime.ZERO,
    items=(PlannedItem(item=UnitTypeId.BARRACKS, start=GameTime(seconds=30), is_fluid=False),),
    intervals=(),
    objective=0.0,
    gap=0.0,
    is_optimal=True,
    solve_seconds=0.0,
)


def _whole_over_any_horizon(_: UnitTypeId | UpgradeId) -> GameTime:
    """Every item whole over any horizon."""
    return GameTime.INFINITE


def _default_whole_seconds_by_name(state: GameState, economy: Economy) -> dict[str, float]:
    """The default seconds each item of a plan for marines and stimpack starts whole for, from `state` by `economy`, by
    the item's name, but those of add-ons, barracks and refineries."""
    items = _TERRAN_MODEL.items_for(unit_types=[UnitTypeId.MARINE, UnitTypeId.FACTORY], upgrades=[UpgradeId.STIMPACK])
    seconds = fluid_after_seconds_of(items, fluid_after=None, state=state, economy=economy)
    named = ("COMMAND_CENTER", "FACTORY", "STIMPACK", "ORBITAL_COMMAND", "SUPPLY_DEPOT", "SCV", "MARINE")
    return {item.name: window for item, window in zip(items, seconds, strict=True) if item.name in named}


def _plan_found(plan: Plan | None) -> Plan:
    """`plan`, which the solver found."""
    assert plan is not None
    return plan


def _start_seconds(plan: Plan, item: UnitTypeId | UpgradeId) -> list[float]:
    """The seconds after the plan's start at which it starts each `item`, to a millionth."""
    return [round((planned.start - plan.made_at).seconds, 6) for planned in plan.items if planned.item == item]


def _start_seconds_of_units_done_in_time(plan: Plan, unit_type: UnitTypeId) -> list[float]:
    """The seconds after the plan's start at which it starts each unit of `unit_type` that is done by its horizon. One
    started later is worth nothing to the plan and costs it nothing, so whether it starts is the solver's choice."""
    build_seconds = _TERRAN_MODEL.unit_items[unit_type].build_seconds
    horizon_seconds = (plan.intervals[-1].end.time - plan.made_at).seconds
    return [start for start in _start_seconds(plan, unit_type) if start + build_seconds <= horizon_seconds]


class TestTimeGrid:
    def test_the_doubling_grid_runs_34_step_intervals_to_544_steps_doubling_to_544_steps_57_to_10_minutes(self) -> None:
        edges = [GameTime(seconds=edge).steps for edge in TimeGrid.doubling().edges(GameTime(seconds=600))]
        widths = [later - earlier for earlier, later in itertools.pairwise(edges)]

        assert len(widths) == 57
        assert edges[:3] == pytest.approx([0, 34, 68])
        assert edges[15:18] == pytest.approx([510, 544, 612])
        assert [edges[index] for index in (24, 32, 40)] == pytest.approx([1088, 2176, 4352])
        assert edges[-3:] == pytest.approx([12512, 13056, 13440])
        assert sorted({round(width) for width in widths}) == [34, 68, 136, 272, 384, 544]

    def test_a_worker_takes_a_whole_number_of_the_doubling_grids_intervals_while_they_are_shorter_than_it(self) -> None:
        worker_steps = GameTime(seconds=_TERRAN_MODEL.unit_items[UnitTypeId.SCV].build_seconds).steps
        edges = [GameTime(seconds=edge).steps for edge in TimeGrid.doubling().edges(GameTime(seconds=600))]

        assert worker_steps == pytest.approx(272)
        for one_after_another in range(17):
            assert any(edge == pytest.approx(one_after_another * worker_steps) for edge in edges)

    def test_a_doubling_grid_takes_its_initial_intervals_length_and_count_and_its_most_doublings(self) -> None:
        grid = TimeGrid.doubling(
            initial_interval_length=GameTime(seconds=2), initial_interval_count=22, max_interval_doublings=4
        )
        edges = grid.edges(GameTime(seconds=600))
        widths = [later - earlier for earlier, later in itertools.pairwise(edges)]

        assert len(widths) == 63
        assert edges[:3] == (0.0, 2.0, 4.0)
        assert edges[22:24] == (44.0, 48.0)
        assert edges[-3:] == (544.0, 576.0, 600.0)
        assert sorted(set(widths)) == [2.0, 4.0, 8.0, 16.0, 24.0, 32.0]

    def test_a_doubling_grid_of_no_doublings_is_uniform(self) -> None:
        grid = TimeGrid.doubling(initial_interval_length=GameTime(seconds=5), max_interval_doublings=0)
        assert grid.edges(GameTime(seconds=22)) == TimeGrid.uniform(GameTime(seconds=5)).edges(GameTime(seconds=22))

    @pytest.mark.parametrize(
        "arguments",
        [
            {"initial_interval_length": GameTime.ZERO},
            {"initial_interval_count": 0},
            {"initial_interval_count": 15},
            {"max_interval_doublings": -1},
        ],
        ids=["a length of 0", "no initial intervals", "an odd count", "negative doublings"],
    )
    def test_a_doubling_grid_that_cannot_be_laid_out_raises(self, arguments: dict[str, Any]) -> None:
        with pytest.raises(ValueError):
            TimeGrid.doubling(**arguments)

    def test_a_segments_intervals_follow_while_the_next_ends_by_its_time_and_the_last_is_cut_at_the_horizon(
        self,
    ) -> None:
        grid = TimeGrid(
            segments=[(GameTime(seconds=10), GameTime(seconds=25)), (GameTime(seconds=4), GameTime.INFINITE)]
        )
        assert grid.edges(GameTime(seconds=33)) == (0.0, 10.0, 20.0, 24.0, 28.0, 32.0, 33.0)

    def test_past_the_last_segment_one_interval_runs_to_the_horizon(self) -> None:
        grid = TimeGrid(segments=[(GameTime(seconds=10), GameTime(seconds=20))])
        assert grid.edges(GameTime(seconds=50)) == (0.0, 10.0, 20.0, 50.0)

    @pytest.mark.parametrize(
        "segments",
        [
            [],
            [(GameTime.ZERO, GameTime.INFINITE)],
            [(GameTime(seconds=2), GameTime(seconds=10)), (GameTime(seconds=4), GameTime(seconds=10))],
        ],
        ids=["no segments", "an interval of 0", "a segment not after the one before"],
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

    def test_a_unit_counts_as_the_type_an_order_makes_of_its_equivalent_types(self) -> None:
        """Not one morphed from another of them, and of two an order makes, the one needing fewer techs: a hellion,
        which needs no armory. A rich refinery counts as the refinery its order makes."""
        expected = {
            UnitTypeId.BARRACKS_FLYING: UnitTypeId.BARRACKS,
            UnitTypeId.SUPPLY_DEPOT_LOWERED: UnitTypeId.SUPPLY_DEPOT,
            UnitTypeId.SIEGE_TANK_SIEGED: UnitTypeId.SIEGE_TANK,
            UnitTypeId.ORBITAL_COMMAND: UnitTypeId.ORBITAL_COMMAND,
            UnitTypeId.ORBITAL_COMMAND_FLYING: UnitTypeId.ORBITAL_COMMAND,
            UnitTypeId.HELLBAT: UnitTypeId.HELLION,
            UnitTypeId.HELLION: UnitTypeId.HELLION,
            UnitTypeId.REFINERY_RICH: UnitTypeId.REFINERY,
        }
        assert {unit_type: _TERRAN_MODEL.counted_as(unit_type) for unit_type in expected} == expected

    def test_every_terran_type_counts_as_one_of_its_equivalent_types_but_a_rich_refinery(self) -> None:
        game_data = _TERRAN_MODEL.game_data
        for unit_type, row in game_data.units.items():
            if row.race == Race.TERRAN and unit_type != UnitTypeId.REFINERY_RICH:
                assert _TERRAN_MODEL.counted_as(unit_type) in row.equivalent_types.type_ids, unit_type.name

    def test_a_hellbat_is_no_item_and_a_reward_on_it_brings_hellions_and_no_armory(self) -> None:
        items = _TERRAN_MODEL.items_for(unit_types=[UnitTypeId.HELLBAT], upgrades=[])
        unit_types = {item.unit_type for item in items}
        assert UnitTypeId.HELLBAT not in _TERRAN_MODEL.unit_items
        assert UnitTypeId.HELLION in unit_types
        assert UnitTypeId.ARMORY not in unit_types

    def test_an_orbital_command_is_a_morph_of_a_command_center_that_adds_no_supply(self) -> None:
        orbital = _TERRAN_MODEL.unit_items[UnitTypeId.ORBITAL_COMMAND]
        assert orbital.morphed_from == UnitTypeId.COMMAND_CENTER
        assert orbital.producers == {UnitTypeId.COMMAND_CENTER}
        assert orbital.supply_provided == 0.0
        assert (orbital.minerals, orbital.build_seconds) == (150.0, 25.0)

    @pytest.mark.parametrize("unit_type", [UnitTypeId.ZERGLING, UnitTypeId.MULE, UnitTypeId.AUTO_TURRET])
    def test_a_type_terran_does_not_make_raises(self, unit_type: UnitTypeId) -> None:
        with pytest.raises(ValueError, match=unit_type.name):
            _TERRAN_MODEL.items_for(unit_types=[unit_type], upgrades=[])


class TestKnownPlans:
    def test_from_50_minerals_and_twelve_scvs_the_first_depot_starts_at_8_seconds(self) -> None:
        """The first SCV takes the 50 minerals at once, and the depot's SCV, walking 6 seconds, leaves at 2; a depot
        worth a little gets built as soon as it can be paid."""
        reward = Reward.weighted({UnitTypeId.SCV: 1.0, UnitTypeId.SUPPLY_DEPOT: 0.01})
        plan = _best_plan(_game_start(), reward, horizon_seconds=40)

        assert _start_seconds(plan, UnitTypeId.SCV)[:2] == [0.0, 12.0]
        assert _start_seconds(plan, UnitTypeId.SUPPLY_DEPOT)[0] == 8.0

    @pytest.mark.parametrize(
        ("max_ignored_overrun", "scv_starts"),
        [(0.0, [0.0, 14.0, 28.0, 42.0]), (0.05, [0.0, 14.0, 28.0, 42.0]), (0.25, [0.0, 12.0, 24.0, 36.0])],
        ids=["none ignored", "less than it runs over", "a quarter of an interval"],
    )
    def test_a_whole_build_running_into_an_interval_by_less_than_the_overrun_ignored_leaves_it_free(
        self, max_ignored_overrun: float, scv_starts: list[float]
    ) -> None:
        """An SCV's 12.14 seconds run 0.14 into a seventh interval of 2 seconds, 0.07 of it: the next SCV starts in that
        interval if that is ignored, and in the one after if not. Of the SCVs done by the plan's end."""
        plan = _best_plan(
            _game_start(minerals=1000.0, supply_cap=100.0),
            Reward.weighted({UnitTypeId.SCV: 1.0}),
            horizon_seconds=60,
            fluid_after=lambda _: GameTime.INFINITE,
            start_limits=_NOTHING_BUT_UNITS,
            max_ignored_overrun=max_ignored_overrun,
        )
        assert _start_seconds_of_units_done_in_time(plan, UnitTypeId.SCV) == scv_starts

    def test_every_overrun_is_ignored_with_an_overrun_ignored_of_1(self) -> None:
        """A marine's 17.86 seconds run 1.86 into a ninth interval of 2 seconds: the next starts in it."""
        state = _game_start(
            minerals=1000.0, supply_used=0.0, supply_cap=100.0, units={UnitTypeId.BARRACKS: 1}, mineral_fields=0
        )
        plan = _best_plan(state, Reward.weighted({UnitTypeId.MARINE: 1.0}), horizon_seconds=60, max_ignored_overrun=1.0)
        assert _start_seconds_of_units_done_in_time(plan, UnitTypeId.MARINE) == [0.0, 16.0, 32.0]

    def test_a_fluid_start_keeps_its_producer_busy_for_its_build_time_whatever_overrun_is_ignored(self) -> None:
        worths = [
            _best_plan(
                _game_start(minerals=1000.0, supply_cap=100.0),
                Reward.weighted({UnitTypeId.SCV: 1.0}),
                horizon_seconds=60,
                fluid_after=lambda _: GameTime.ZERO,
                start_limits=_NOTHING_BUT_UNITS,
                max_ignored_overrun=max_ignored_overrun,
            ).objective
            for max_ignored_overrun in (0.0, 1.0)
        ]
        assert worths[0] == pytest.approx(worths[1])

    @pytest.mark.parametrize(
        ("max_ignored_overrun", "next_marine_start"), [(0.0, 6.0), (0.25, 4.0)], ids=["none ignored", "a quarter"]
    )
    def test_a_build_under_way_running_into_an_interval_by_less_than_the_overrun_ignored_leaves_it_free(
        self, max_ignored_overrun: float, next_marine_start: float
    ) -> None:
        """A marine 4.2 seconds from done runs 0.2 into the third interval of 2 seconds, a tenth of it."""
        time_left = GameTime(seconds=4.2)
        training = Occupation(
            producer=UnitTypeId.BARRACKS, time_until_start=GameTime.ZERO, time_left=time_left, needs_tech_lab=False
        )
        state = _game_start(
            minerals=1000.0,
            supply_used=1.0,
            supply_cap=100.0,
            units={UnitTypeId.BARRACKS: 1},
            mineral_fields=0,
            unit_completions=(UnitCompletion(unit_type=UnitTypeId.MARINE, time_left=time_left),),
            occupations=(training,),
        )
        plan = _best_plan(
            state,
            Reward.weighted({UnitTypeId.MARINE: 1.0}),
            horizon_seconds=40,
            max_ignored_overrun=max_ignored_overrun,
        )
        assert _start_seconds(plan, UnitTypeId.MARINE)[0] == next_marine_start

    @pytest.mark.parametrize(
        ("reactors", "marine_starts"),
        [(0, [0.0, 18.0, 36.0]), (1, [0.0, 0.0, 18.0, 18.0, 36.0, 36.0])],
        ids=["one at a time", "two with a reactor"],
    )
    def test_one_barracks_trains_one_marine_at_a_time_and_two_with_a_reactor(
        self, reactors: int, marine_starts: list[float]
    ) -> None:
        """With no SCV to build more, and too little time for a reactor to pay, over a minute, of the marines done by
        its end: one started later is worth nothing."""
        state = _game_start(
            minerals=1000.0,
            supply_used=0.0,
            supply_cap=100.0,
            units={UnitTypeId.BARRACKS: 1, UnitTypeId.REACTOR_BARRACKS: reactors},
            mineral_fields=0,
        )
        plan = _best_plan(state, Reward.weighted({UnitTypeId.MARINE: 1.0}), horizon_seconds=60)
        assert _start_seconds_of_units_done_in_time(plan, UnitTypeId.MARINE) == marine_starts

    def test_a_unit_in_production_counts_from_the_first_edge_after_it_is_done(self) -> None:
        """With nothing to spend, a marine 9 seconds from done is the plan's only one, alive from the edge at 10, the
        fifth interval's end."""
        marine = UnitCompletion(unit_type=UnitTypeId.MARINE, time_left=GameTime(seconds=9))
        state = _game_start(
            minerals=0.0,
            supply_used=1.0,
            supply_cap=100.0,
            units={UnitTypeId.BARRACKS: 1},
            mineral_fields=0,
            unit_completions=(marine,),
        )
        plan = _best_plan(state, Reward.weighted({UnitTypeId.MARINE: 1.0}), horizon_seconds=16)
        assert plan.items == ()
        assert [interval.end.units[UnitTypeId.MARINE] for interval in plan.intervals] == [0, 0, 0, 0, 1, 1, 1, 1]

    def test_a_reward_on_hellbats_plans_hellions(self) -> None:
        """A factory with no vespene for a reactor, over a minute: a hellion started at 44 seconds would not be done by
        its end."""
        state = _game_start(
            minerals=1000.0,
            supply_used=0.0,
            supply_cap=100.0,
            units={UnitTypeId.FACTORY: 1},
            mineral_fields=0,
        )
        plan = _best_plan(state, Reward.weighted({UnitTypeId.HELLBAT: 1.0}), horizon_seconds=60)
        assert _start_seconds(plan, UnitTypeId.HELLION) == [0.0, 22.0]
        assert UnitTypeId.HELLBAT not in plan.intervals[0].end.units

    def test_a_command_center_starts_with_the_fifth_scv_after_uninterrupted_scv_production(self) -> None:
        """SCVs worth more than a command center, which is built as soon as it does not hold one up, on the doubling
        grid, every start whole, a new SCV mining at once. An SCV starts each 272 steps, as in the game: eight of the
        grid's first intervals."""
        reward = Reward.weighted({UnitTypeId.SCV: 2.0, UnitTypeId.COMMAND_CENTER: 1.0})
        plan = _best_plan(
            _game_start(),
            reward,
            horizon_seconds=150,
            grid=TimeGrid.doubling(),
            fluid_after=_whole_over_any_horizon,
            economy=dataclasses.replace(_WALKING_6_SECONDS_TO_EVERY_STRUCTURE, reassignment_time=GameTime.ZERO),
        )

        scv_seconds = GameTime(steps=272).seconds
        assert _start_seconds(plan, UnitTypeId.COMMAND_CENTER)[0] == pytest.approx(4 * scv_seconds)
        assert _start_seconds(plan, UnitTypeId.SCV)[:5] == pytest.approx([scv_seconds * count for count in range(5)])

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
        interval's."""
        depot = _TERRAN_MODEL.unit_items[UnitTypeId.SUPPLY_DEPOT].build_seconds
        build = WorkerBuild(
            unit_type=UnitTypeId.SUPPLY_DEPOT, time_left=GameTime(seconds=depot), builder_walks_there=walks_there
        )
        state = _game_start(
            minerals=0.0,
            supply_used=1.0,
            units={UnitTypeId.SCV: 1, UnitTypeId.COMMAND_CENTER: 1},
            worker_builds=(build,),
        )
        plan = _best_plan(
            state, Reward.weighted({UnitTypeId.SCV: 1.0}), horizon_seconds=100, fluid_after=_whole_over_any_horizon
        )

        first_with_the_depot = next(
            interval for interval in plan.intervals if interval.end.units[UnitTypeId.SUPPLY_DEPOT] >= 1
        )
        assert first_with_the_depot.end.time == GameTime(seconds=depot_done)
        assert _start_seconds(plan, UnitTypeId.SCV) == [scv_start]

    def test_the_plans_intervals_are_its_grids_from_the_state_on(self) -> None:
        """With no field to mine, the state's 50 minerals pay the SCV that starts at once, done 12.1 seconds on: nothing
        is in stock at the first interval's end, and the SCV is alive at the last one's."""
        state = _game_start(time=GameTime(seconds=30), mineral_fields=0)
        plan = _best_plan(state, Reward.weighted({UnitTypeId.SCV: 1.0}), horizon_seconds=14)

        assert plan.made_at == GameTime(seconds=30)
        assert [interval.start.time for interval in plan.intervals] == [
            GameTime(seconds=seconds) for seconds in range(30, 44, 2)
        ]
        assert [interval.end.time for interval in plan.intervals] == [
            GameTime(seconds=seconds) for seconds in range(32, 46, 2)
        ]
        assert plan.items == (PlannedItem(item=UnitTypeId.SCV, start=GameTime(seconds=30), is_fluid=False),)
        assert [interval.end.resources.minerals for interval in plan.intervals] == pytest.approx([0.0] * 7, abs=1e-9)
        assert [interval.miners for interval in plan.intervals] == pytest.approx([0.0] * 7, abs=1e-9)
        assert [interval.end.units[UnitTypeId.SCV] for interval in plan.intervals] == [12, 12, 12, 12, 12, 12, 13]

    def test_a_unit_counts_as_it_survives_its_loss_rate(self) -> None:
        """No barracks, so no marine is made."""
        state = _game_start(units={UnitTypeId.MARINE: 10})
        plan = _best_plan(
            state, Reward.weighted({UnitTypeId.MARINE: 1.0}), horizon_seconds=10, loss_rates={UnitTypeId.MARINE: 0.01}
        )
        marines = [interval.end.units[UnitTypeId.MARINE] for interval in plan.intervals]
        assert marines == pytest.approx([10 * math.exp(-0.01 * 2 * k) for k in range(1, 6)])

    def test_a_weight_on_types_counted_together_buys_those_that_count(self) -> None:
        """Ten wanted against air, where a marine counts for one and a marauder, which cannot shoot up, for none. Two
        barracks with tech labs make two at a time, and over 40 seconds those started at 36 would not be done."""
        state = _game_start(
            minerals=2000.0,
            vespene=1000.0,
            supply_used=0.0,
            supply_cap=100.0,
            units={UnitTypeId.BARRACKS: 2, UnitTypeId.TECH_LAB_BARRACKS: 2},
            mineral_fields=0,
        )
        air = RewardTerm.weight({UnitTypeId.MARINE: 1.0, UnitTypeId.MARAUDER: 0.0}, 1.0, end_count=10.0)
        plan = _best_plan(state, Reward.weighted({UnitTypeId.MARAUDER: 0.01}, terms=[air]), horizon_seconds=40)
        assert _start_seconds(plan, UnitTypeId.MARINE) == [0.0, 0.0, 18.0, 18.0]
        assert _start_seconds(plan, UnitTypeId.MARAUDER) == []

    def test_a_weight_up_to_a_count_from_a_time_buys_the_count_by_that_time(self) -> None:
        state = _game_start(
            minerals=500.0, supply_used=0.0, supply_cap=100.0, units={UnitTypeId.BARRACKS: 1}, mineral_fields=0
        )
        floor = RewardTerm.weight(UnitTypeId.MARINE, 10.0, end_count=2.0, start=GameTime(seconds=36))

        without = _best_plan(state, Reward.weighted({UnitTypeId.SUPPLY_DEPOT: 1.0}), horizon_seconds=40)
        held = _best_plan(state, Reward.weighted({UnitTypeId.SUPPLY_DEPOT: 1.0}, terms=[floor]), horizon_seconds=40)

        assert _start_seconds(without, UnitTypeId.MARINE) == []
        assert _start_seconds(held, UnitTypeId.MARINE) == [0.0, 18.0]

    def test_a_concave_curve_buys_up_to_where_its_weight_falls_below_nothing(self) -> None:
        state = _game_start(
            minerals=1000.0,
            supply_used=0.0,
            supply_cap=100.0,
            units={UnitTypeId.BARRACKS: 1, UnitTypeId.REACTOR_BARRACKS: 1},
            mineral_fields=0,
        )
        curve = [CurveSegment(weight=1.0, end_count=3), CurveSegment(weight=-0.1)]
        plan = _best_plan(state, Reward.weighted({UnitTypeId.MARINE: curve}), horizon_seconds=40)
        assert len(_start_seconds_of_units_done_in_time(plan, UnitTypeId.MARINE)) == 3

    def test_a_curve_of_one_piece_is_a_weight_whatever_count_it_runs_to(self) -> None:
        """The piece runs on past its count, so its plan is worth what the weight's is, more than three marines."""
        state = _game_start(
            minerals=1000.0,
            supply_used=0.0,
            supply_cap=100.0,
            units={UnitTypeId.BARRACKS: 1, UnitTypeId.REACTOR_BARRACKS: 1},
            mineral_fields=0,
        )
        by_weight = Reward.weighted({UnitTypeId.MARINE: 1.0})
        by_curve = Reward.weighted({UnitTypeId.MARINE: [CurveSegment(weight=1.0, end_count=3)]})
        weighted, curved = (_best_plan(state, reward, horizon_seconds=40) for reward in (by_weight, by_curve))

        assert curved.objective == pytest.approx(weighted.objective)
        assert len(_start_seconds_of_units_done_in_time(curved, UnitTypeId.MARINE)) > 3

    def test_a_term_that_ends_where_another_starts_buys_for_that_time(self) -> None:
        """One marine to afford, which costs while it is alive before 20 seconds and is worth having from then: it
        starts at 2, to be done at 20, where one worth the same throughout starts at once."""
        state = _game_start(
            minerals=50.0, supply_used=0.0, supply_cap=100.0, units={UnitTypeId.BARRACKS: 1}, mineral_fields=0
        )
        from_20_seconds = [
            RewardTerm.weight(UnitTypeId.MARINE, -1.0, end=GameTime(seconds=20)),
            RewardTerm.weight(UnitTypeId.MARINE, 1.0, start=GameTime(seconds=20)),
        ]
        throughout = _best_plan(state, Reward.weighted({UnitTypeId.MARINE: 1.0}), horizon_seconds=40)
        late = _best_plan(state, Reward(terms=from_20_seconds), horizon_seconds=40)

        assert _start_seconds(throughout, UnitTypeId.MARINE) == [0.0]
        assert _start_seconds(late, UnitTypeId.MARINE) == [2.0]

    def test_a_term_is_worth_nothing_from_its_end(self) -> None:
        """One marine to afford, done at 18 and worth 1 a second up to 40: counted at the ends of the intervals of 2
        seconds from 18 to 38, eleven of them, and not at 40."""
        state = _game_start(
            minerals=50.0, supply_used=0.0, supply_cap=100.0, units={UnitTypeId.BARRACKS: 1}, mineral_fields=0
        )
        up_to_40_seconds = RewardTerm.weight(UnitTypeId.MARINE, 1.0, end=GameTime(seconds=40))
        plan = _best_plan(state, Reward(terms=[up_to_40_seconds]), horizon_seconds=80)

        assert _start_seconds(plan, UnitTypeId.MARINE) == [0.0]
        assert plan.objective == pytest.approx(11 * 2)

    def test_a_curve_between_two_times_is_worth_what_it_gives_between_them(self) -> None:
        """A reactor could make two marines at once and the curve counts one, so one is made, done by 20: worth 1 a
        second from 20 up to 30, at the ends of the five intervals from 20 to 28. The 450 minerals left are worth
        0.0001 each."""
        state = _game_start(
            minerals=500.0,
            supply_used=0.0,
            supply_cap=100.0,
            units={UnitTypeId.BARRACKS: 1, UnitTypeId.REACTOR_BARRACKS: 1},
            mineral_fields=0,
        )
        one_marine = RewardTerm.weight(
            UnitTypeId.MARINE, 1.0, end_count=1.0, start=GameTime(seconds=20), end=GameTime(seconds=30)
        )
        plan = _best_plan(state, Reward(terms=[one_marine]), horizon_seconds=40)

        assert len(_start_seconds(plan, UnitTypeId.MARINE)) == 1
        assert plan.objective == pytest.approx(5 * 2 + 450 * 1e-4)

    def test_a_term_that_starts_past_the_horizon_is_worth_nothing(self) -> None:
        state = _game_start(
            minerals=500.0, supply_used=0.0, supply_cap=100.0, units={UnitTypeId.BARRACKS: 1}, mineral_fields=0
        )
        too_late = RewardTerm.weight(UnitTypeId.MARINE, 1.0, end_count=4.0, start=GameTime(seconds=60))
        plan = _best_plan(state, Reward(terms=[too_late]), horizon_seconds=40)

        assert plan.items == ()
        assert plan.objective == pytest.approx(500 * 1e-4)

    def test_a_time_factor_multiplies_what_a_term_is_worth_at_each_game_time(self) -> None:
        """One marine to afford in a state at 100 seconds, done 18 seconds on, each second worth a tenth of the seconds
        since the state: counted at the ends of the intervals of 2 seconds from 18 to 40, which sum to 348, a tenth of
        each over its 2 seconds."""
        state = _game_start(
            time=GameTime(seconds=100),
            minerals=50.0,
            supply_used=0.0,
            supply_cap=100.0,
            units={UnitTypeId.BARRACKS: 1},
            mineral_fields=0,
        )
        rising = RewardTerm.weight(UnitTypeId.MARINE, 1.0, time_factor=lambda time: (time.seconds - 100) / 10)
        plan = _best_plan(state, Reward(terms=[rising]), horizon_seconds=40)

        assert _start_seconds(plan, UnitTypeId.MARINE) == [0.0]
        assert plan.objective == pytest.approx(348 / 10 * 2)

    def test_a_time_factor_multiplies_a_curve_between_two_times(self) -> None:
        """The marine of the curve between 20 and 30 seconds, worth three times its 5 intervals of 2 seconds."""
        state = _game_start(
            minerals=500.0,
            supply_used=0.0,
            supply_cap=100.0,
            units={UnitTypeId.BARRACKS: 1, UnitTypeId.REACTOR_BARRACKS: 1},
            mineral_fields=0,
        )
        one_marine = RewardTerm.weight(
            UnitTypeId.MARINE,
            1.0,
            end_count=1.0,
            start=GameTime(seconds=20),
            end=GameTime(seconds=30),
            time_factor=lambda _: 3.0,
        )
        plan = _best_plan(state, Reward(terms=[one_marine]), horizon_seconds=40)

        assert len(_start_seconds(plan, UnitTypeId.MARINE)) == 1
        assert plan.objective == pytest.approx(3 * 5 * 2 + 450 * 1e-4)

    def test_a_term_of_a_time_factor_of_0_is_worth_nothing(self) -> None:
        state = _game_start(
            minerals=500.0, supply_used=0.0, supply_cap=100.0, units={UnitTypeId.BARRACKS: 1}, mineral_fields=0
        )
        never = RewardTerm.weight(UnitTypeId.MARINE, 1.0, end_count=4.0, time_factor=lambda _: 0.0)
        plan = _best_plan(state, Reward(terms=[never]), horizon_seconds=40)

        assert plan.items == ()
        assert plan.objective == pytest.approx(500 * 1e-4)

    def test_a_negative_time_factor_raises_where_the_plan_is_made(self) -> None:
        state = _game_start(
            minerals=500.0, supply_used=0.0, supply_cap=100.0, units={UnitTypeId.BARRACKS: 1}, mineral_fields=0
        )
        falling = RewardTerm.weight(UnitTypeId.MARINE, 1.0, time_factor=lambda time: 1 - time.seconds / 20)
        with pytest.raises(ValueError, match="time factor"):
            _best_plan(state, Reward(terms=[falling]), horizon_seconds=40)

    def test_terms_that_count_the_same_units_add_up(self) -> None:
        """Two terms of half a weight each are worth what one of the whole weight is."""
        state = _game_start(
            minerals=1000.0, supply_used=0.0, supply_cap=100.0, units={UnitTypeId.BARRACKS: 1}, mineral_fields=0
        )
        halves = Reward(terms=[RewardTerm.weight(UnitTypeId.MARINE, 0.5), RewardTerm.weight(UnitTypeId.MARINE, 0.5)])
        in_two, in_one = (
            _best_plan(state, reward, horizon_seconds=40)
            for reward in (halves, Reward.weighted({UnitTypeId.MARINE: 1.0}))
        )

        assert in_two.objective == pytest.approx(in_one.objective)
        assert _start_seconds(in_two, UnitTypeId.MARINE) == _start_seconds(in_one, UnitTypeId.MARINE)

    def test_a_term_that_requires_an_upgrade_is_worth_nothing_until_it_is_done(self) -> None:
        """Marines worth something with combat shield alone, 300 minerals and 100 vespene with nothing coming in: the
        plan researches it at once, done 78.6 seconds on, and its four marines count from the edge at 80 to the horizon
        at 120, 21 intervals of 2 seconds."""
        state = _game_start(
            minerals=300.0,
            vespene=100.0,
            supply_used=0.0,
            supply_cap=100.0,
            units={UnitTypeId.BARRACKS: 1, UnitTypeId.TECH_LAB_BARRACKS: 1},
            mineral_fields=0,
        )
        with_the_shield = Reward(terms=[RewardTerm.weight(UnitTypeId.MARINE, 1.0, requires=[UpgradeId.COMBAT_SHIELD])])
        plan = _best_plan(state, with_the_shield, horizon_seconds=120, fluid_after=_whole_over_any_horizon)

        assert _start_seconds(plan, UpgradeId.COMBAT_SHIELD) == [0.0]
        assert len(_start_seconds_of_units_done_in_time(plan, UnitTypeId.MARINE)) == 4
        assert plan.objective == pytest.approx(4 * 21 * 2)

    def test_an_upgrade_a_term_requires_starts_whole_where_every_start_is_fluid(self) -> None:
        """A share of the upgrade would unlock that share of all the term could be worth, far more than of its
        marines."""
        state = _game_start(
            minerals=300.0,
            vespene=100.0,
            supply_used=0.0,
            supply_cap=100.0,
            units={UnitTypeId.BARRACKS: 1, UnitTypeId.TECH_LAB_BARRACKS: 1},
            mineral_fields=0,
        )
        with_the_shield = Reward(terms=[RewardTerm.weight(UnitTypeId.MARINE, 1.0, requires=[UpgradeId.COMBAT_SHIELD])])
        plan = _best_plan(state, with_the_shield, horizon_seconds=120, fluid_after=lambda _: GameTime.ZERO)

        shield = PlannedItem(item=UpgradeId.COMBAT_SHIELD, start=GameTime.ZERO, is_fluid=False)
        assert [planned for planned in plan.items if planned.item == UpgradeId.COMBAT_SHIELD] == [shield]
        assert all(planned.is_fluid for planned in plan.items if planned.item == UnitTypeId.MARINE)

    def test_a_term_that_requires_an_upgrade_the_state_has_is_worth_what_its_weight_is(self) -> None:
        state = _game_start(
            minerals=300.0,
            supply_used=0.0,
            supply_cap=100.0,
            units={UnitTypeId.BARRACKS: 1},
            mineral_fields=0,
            upgrades=frozenset({UpgradeId.COMBAT_SHIELD}),
        )
        with_the_shield = Reward(terms=[RewardTerm.weight(UnitTypeId.MARINE, 1.0, requires=[UpgradeId.COMBAT_SHIELD])])
        required, weighted = (
            _best_plan(state, reward, horizon_seconds=60)
            for reward in (with_the_shield, Reward.weighted({UnitTypeId.MARINE: 1.0}))
        )

        assert required.objective == pytest.approx(weighted.objective)
        assert _start_seconds(required, UnitTypeId.MARINE) == _start_seconds(weighted, UnitTypeId.MARINE)

    def test_a_combination_buys_no_more_of_a_group_than_its_scarcest_group_allows(self) -> None:
        """Two medivacs and no starport for more, each healing two marines: four marines are worth something and a
        fifth nothing, so one barracks makes four, done at 18, 36, 54 and 72. Counted at the ends of the intervals of 2
        seconds from then to the horizon at 80, that is 32, 23, 14 and 5 intervals, 74 of 2 seconds, and the 800
        minerals left are worth 0.0001 each."""
        state = _game_start(
            minerals=1000.0,
            supply_used=0.0,
            supply_cap=100.0,
            units={UnitTypeId.BARRACKS: 1, UnitTypeId.MEDIVAC: 2},
            mineral_fields=0,
        )
        healed = RewardTerm.combination([{UnitTypeId.MEDIVAC: 2.0}, {UnitTypeId.MARINE: 1.0}], 1.0)
        plan = _best_plan(state, Reward(terms=[healed]), horizon_seconds=80, fluid_after=_whole_over_any_horizon)

        assert _start_seconds(plan, UnitTypeId.MARINE) == [0.0, 18.0, 36.0, 54.0]
        assert plan.objective == pytest.approx(74 * 2 + 800 * 1e-4)

    def test_a_combination_buys_the_group_it_is_short_of(self) -> None:
        """Sixteen marines, no barracks for more, and a medivac heals eight: the starport makes two and no third."""
        state = _game_start(
            minerals=1000.0,
            vespene=1000.0,
            supply_used=0.0,
            supply_cap=100.0,
            units={UnitTypeId.STARPORT: 1, UnitTypeId.MARINE: 16},
            mineral_fields=0,
        )
        healed = RewardTerm.combination([{UnitTypeId.MEDIVAC: 8.0}, {UnitTypeId.MARINE: 1.0}], 1.0)
        plan = _best_plan(state, Reward(terms=[healed]), horizon_seconds=80)

        assert _start_seconds(plan, UnitTypeId.MEDIVAC) == [0.0, 30.0]

    def test_a_combination_up_to_a_count_buys_no_more_than_the_count(self) -> None:
        """Marines enough for three medivacs, of which the first two are worth something."""
        state = _game_start(
            minerals=1000.0,
            vespene=1000.0,
            supply_used=0.0,
            supply_cap=100.0,
            units={UnitTypeId.STARPORT: 1, UnitTypeId.MARINE: 24},
            mineral_fields=0,
        )
        healed = RewardTerm.combination([{UnitTypeId.MEDIVAC: 8.0}, {UnitTypeId.MARINE: 1.0}], 1.0, end_count=16.0)
        plan = _best_plan(state, Reward(terms=[healed]), horizon_seconds=100)

        assert _start_seconds(plan, UnitTypeId.MEDIVAC) == [0.0, 30.0]

    def test_a_combination_that_requires_an_upgrade_the_state_has_is_worth_what_it_is_without(self) -> None:
        state = _game_start(
            minerals=1000.0,
            supply_used=0.0,
            supply_cap=100.0,
            units={UnitTypeId.BARRACKS: 1, UnitTypeId.MEDIVAC: 2},
            mineral_fields=0,
            upgrades=frozenset({UpgradeId.COMBAT_SHIELD}),
        )
        groups = [{UnitTypeId.MEDIVAC: 2.0}, {UnitTypeId.MARINE: 1.0}]
        required, without = (
            _best_plan(state, Reward(terms=[healed]), horizon_seconds=80)
            for healed in (
                RewardTerm.combination(groups, 1.0, requires=[UpgradeId.COMBAT_SHIELD]),
                RewardTerm.combination(groups, 1.0),
            )
        )

        assert required.objective == pytest.approx(without.objective)
        assert _start_seconds(required, UnitTypeId.MARINE) == _start_seconds(without, UnitTypeId.MARINE)

    def test_a_hysteresis_bonus_keeps_to_the_previous_plans_items(self) -> None:
        """A barracks nothing else is worth gets built by when the previous plan had it, for a bonus above its cost in
        what an SCV's mining would have been worth."""
        previous = _A_PLAN_OF_ONE_BARRACKS
        state = _game_start(minerals=500.0, supply_cap=23.0, units={UnitTypeId.SCV: 12, UnitTypeId.SUPPLY_DEPOT: 1})
        reward = Reward.weighted({UnitTypeId.SCV: 1.0})

        without = _best_plan(state, reward, horizon_seconds=40)
        kept = _best_plan(state, reward, horizon_seconds=40, previous_plan=previous, hysteresis_bonus=100.0)

        assert _start_seconds(without, UnitTypeId.BARRACKS) == []
        assert len(_start_seconds(kept, UnitTypeId.BARRACKS)) == 1
        assert _start_seconds(kept, UnitTypeId.BARRACKS)[0] <= 30.0


class TestPlan:
    def test_the_interval_at_a_time_is_the_one_from_its_start_to_before_its_end(self) -> None:
        state = _game_start(time=GameTime(seconds=30))
        plan = _best_plan(state, Reward.weighted({UnitTypeId.SCV: 1.0}), horizon_seconds=14)

        assert plan.interval_at(GameTime(seconds=30)) is plan.intervals[0]
        assert plan.interval_at(GameTime(seconds=31.9)) is plan.intervals[0]
        assert plan.interval_at(GameTime(seconds=32)) is plan.intervals[1]
        assert plan.interval_at(GameTime(seconds=43.9)) is plan.intervals[-1]

    @pytest.mark.parametrize("seconds", [29.9, 44.0, 50.0], ids=["before it was made", "the horizon", "after it"])
    def test_no_interval_is_at_a_time_outside_the_plan(self, seconds: float) -> None:
        state = _game_start(time=GameTime(seconds=30))
        plan = _best_plan(state, Reward.weighted({UnitTypeId.SCV: 1.0}), horizon_seconds=14)

        assert plan.interval_at(GameTime(seconds=seconds)) is None

    def test_no_interval_is_at_any_time_of_a_plan_with_none(self) -> None:
        assert _A_PLAN_OF_ONE_BARRACKS.interval_at(GameTime.ZERO) is None

    def test_its_first_moment_is_the_state_and_each_intervals_end_is_the_next_ones_start(self) -> None:
        """Ten marines the plan cannot make more of count in every moment, as the state's own."""
        state = _game_start(
            time=GameTime(seconds=30),
            minerals=70.0,
            vespene=5.0,
            units={UnitTypeId.SCV: 12, UnitTypeId.COMMAND_CENTER: 1, UnitTypeId.MARINE: 10},
            upgrades=frozenset({UpgradeId.STIMPACK}),
        )
        plan = _best_plan(state, Reward.weighted({UnitTypeId.SCV: 1.0}), horizon_seconds=14)

        first = plan.intervals[0].start
        assert (first.time, first.resources, first.upgrades) == (state.time, state.resources, state.upgrades)
        assert {unit_type: first.units[unit_type] for unit_type in state.units} == state.units
        assert all(
            earlier.end is later.start for earlier, later in zip(plan.intervals, plan.intervals[1:], strict=False)
        )
        assert plan.intervals[-1].end.time == GameTime(seconds=44)
        assert plan.intervals[-1].end.units[UnitTypeId.MARINE] == 10.0

    def test_a_moment_holds_the_upgrades_done_by_it(self) -> None:
        """Stimpack researched from the plan's start reads as done from the first edge at or after its research time,
        the infantry armor 9 seconds from done from the edge at 10, and the state's infantry weapons all along."""
        stimpack_seconds = _TERRAN_MODEL.upgrade_items[UpgradeId.STIMPACK].build_seconds
        state = _game_start(
            minerals=100.0,
            vespene=100.0,
            units={UnitTypeId.BARRACKS: 1, UnitTypeId.TECH_LAB_BARRACKS: 1},
            mineral_fields=0,
            upgrades=frozenset({UpgradeId.TERRAN_INFANTRY_WEAPONS_1}),
            upgrade_completions={UpgradeId.TERRAN_INFANTRY_ARMOR_1: GameTime(seconds=9)},
        )
        plan = _best_plan(
            state,
            Reward(upgrade_weights={UpgradeId.STIMPACK: 1.0}),
            horizon_seconds=2 * math.ceil(stimpack_seconds / 2) + 4,
            fluid_after=_whole_over_any_horizon,
        )

        assert _start_seconds(plan, UpgradeId.STIMPACK) == [0.0]
        moments = [plan.intervals[0].start, *(interval.end for interval in plan.intervals)]
        assert all(UpgradeId.TERRAN_INFANTRY_WEAPONS_1 in moment.upgrades for moment in moments)
        seconds_done_from = {
            upgrade: next((moment.time - plan.made_at).seconds for moment in moments if upgrade in moment.upgrades)
            for upgrade in (UpgradeId.TERRAN_INFANTRY_ARMOR_1, UpgradeId.STIMPACK)
        }
        assert seconds_done_from == {
            UpgradeId.TERRAN_INFANTRY_ARMOR_1: 10.0,
            UpgradeId.STIMPACK: 2.0 * math.ceil(stimpack_seconds / 2),
        }


class TestFluidAfter:
    def test_an_item_has_what_fluid_after_returns_or_its_default_for_none(self) -> None:
        items = _TERRAN_MODEL.items_for(unit_types=[UnitTypeId.MARINE], upgrades=[UpgradeId.STIMPACK])
        asked: list[UnitTypeId | UpgradeId] = []

        def fluid_after(item: UnitTypeId | UpgradeId) -> GameTime | None:
            asked.append(item)
            if item == UpgradeId.STIMPACK:
                return GameTime.INFINITE
            return GameTime(seconds=20) if item == UnitTypeId.SUPPLY_DEPOT else None

        seconds = fluid_after_seconds_of(items, fluid_after=fluid_after, state=_game_start(), economy=Economy())

        seconds_by_name = {item.name: window for item, window in zip(items, seconds, strict=True)}
        assert seconds_by_name["SUPPLY_DEPOT"] == 20.0
        assert seconds_by_name["COMMAND_CENTER"] == 240.0
        assert seconds_by_name["STIMPACK"] == math.inf
        assert asked == [item.made for item in items]

    def test_at_a_games_start_it_is_by_default_60_seconds_each_100_resources_and_at_least_30(self) -> None:
        assert _default_whole_seconds_by_name(_game_start(), Economy()) == {
            "COMMAND_CENTER": 240.0,
            "FACTORY": 150.0,
            "STIMPACK": 120.0,
            "ORBITAL_COMMAND": 90.0,
            "SUPPLY_DEPOT": 60.0,
            "SCV": 30.0,
            "MARINE": 30.0,
        }

    def test_at_a_higher_income_it_is_by_default_shorter_in_proportion_and_at_least_30_seconds(self) -> None:
        # Thirty SCVs mine sixteen fields and an orbital command's MULEs add three SCVs' worth, against a game's twelve.
        state = _game_start(units={UnitTypeId.SCV: 30, UnitTypeId.ORBITAL_COMMAND: 1}, mineral_fields=16)
        economy = Economy(minerals_per_miner_second=(1.0, 1.0, 0.5), mule_minerals_per_orbital_second=3.0)

        seconds_by_name = _default_whole_seconds_by_name(state, economy)

        assert seconds_by_name["COMMAND_CENTER"] == pytest.approx(240 * 12 / 33)
        assert seconds_by_name["FACTORY"] == pytest.approx(seconds_by_name["COMMAND_CENTER"] * 250 / 400)
        assert seconds_by_name["STIMPACK"] == pytest.approx(seconds_by_name["COMMAND_CENTER"] * 200 / 400)
        assert seconds_by_name["ORBITAL_COMMAND"] == pytest.approx(seconds_by_name["COMMAND_CENTER"] * 150 / 400)
        assert seconds_by_name["SUPPLY_DEPOT"] == seconds_by_name["SCV"] == seconds_by_name["MARINE"] == 30.0

    def test_workers_past_what_the_fields_take_add_what_they_would_mine_there(self) -> None:
        saturated = _game_start(units={UnitTypeId.SCV: 16})
        with_a_third_worker_a_field = _game_start(units={UnitTypeId.SCV: 24})
        with_more_than_the_fields_take = _game_start(units={UnitTypeId.SCV: 40})

        command_center_seconds = [
            _default_whole_seconds_by_name(state, Economy())["COMMAND_CENTER"]
            for state in (saturated, with_a_third_worker_a_field, with_more_than_the_fields_take)
        ]

        assert command_center_seconds == pytest.approx([240 * 12 / 16, 240 * 12 / 20, 240 * 12 / 20])

    @pytest.mark.parametrize(
        "state",
        [
            _game_start(units={UnitTypeId.SCV: 6}),
            _game_start(units={}),
            _game_start(mineral_fields=0),
        ],
    )
    def test_at_a_lower_income_or_none_it_is_by_default_what_it_is_at_a_games_start(self, state: GameState) -> None:
        assert _default_whole_seconds_by_name(state, Economy()) == _default_whole_seconds_by_name(
            _game_start(), Economy()
        )

    def test_the_income_is_by_the_economy_it_is_handed(self) -> None:
        state = _game_start(units={UnitTypeId.SCV: 12, UnitTypeId.ORBITAL_COMMAND: 2})
        with_mules_of_three_workers = Economy(
            minerals_per_miner_second=(1.0, 1.0, 0.5), mule_minerals_per_orbital_second=3.0
        )
        mining_half_as_fast = Economy(minerals_per_miner_second=(0.5, 0.5, 0.25))
        with_no_mules = Economy(mule_minerals_per_orbital_second=0.0)
        mining_nothing = Economy(minerals_per_miner_second=(), mule_minerals_per_orbital_second=0.0)

        with_mules_seconds = _default_whole_seconds_by_name(state, with_mules_of_three_workers)["COMMAND_CENTER"]
        slower_seconds = _default_whole_seconds_by_name(_game_start(), mining_half_as_fast)["COMMAND_CENTER"]

        assert with_mules_seconds == pytest.approx(240 * 12 / 18)
        assert slower_seconds == 240.0
        assert _default_whole_seconds_by_name(state, with_no_mules)["COMMAND_CENTER"] == 240.0
        assert _default_whole_seconds_by_name(state, mining_nothing)["COMMAND_CENTER"] == 240.0

    def test_a_negative_time_raises(self) -> None:
        items = _TERRAN_MODEL.items_for(unit_types=[UnitTypeId.MARINE], upgrades=[])
        with pytest.raises(ValueError, match="SCV"):
            fluid_after_seconds_of(
                items,
                fluid_after=lambda item: GameTime(seconds=-1) if item == UnitTypeId.SCV else None,
                state=_game_start(),
                economy=Economy(),
            )

    def test_starts_are_whole_in_the_intervals_that_start_before_their_items_time(self) -> None:
        reward = Reward.weighted({UnitTypeId.MARINE: 1.0})
        items = _TERRAN_MODEL.items_for(unit_types=reward.unit_types, upgrades=reward.upgrades)

        def whole_numbers(fluid_after: GameTime) -> int:
            program = PlanProgram(
                items=items,
                model=_TERRAN_MODEL,
                edges=_TWO_SECOND_INTERVALS.edges(GameTime(seconds=60)),
                state=_game_start(),
                reward=reward,
                economy=Economy(),
                loss_rates={},
                start_limits={},
                fluid_after_seconds=[fluid_after.seconds] * len(items),
                max_ignored_overrun=0.25,
                unlock_fraction=0.0,
                previous_plan=None,
                hysteresis_bonus=0.0,
            )
            return program.linear_program.integral_count

        assert whole_numbers(GameTime.ZERO) == 0
        assert whole_numbers(GameTime(seconds=10)) == 5 * len(items)
        assert whole_numbers(GameTime.INFINITE) == 30 * len(items)

    def test_a_plans_item_is_fluid_if_it_starts_no_earlier_than_its_items_time(self) -> None:
        plan = _best_plan(
            _game_start(),
            Reward.weighted({UnitTypeId.SCV: 1.0}),
            horizon_seconds=60,
            fluid_after=lambda _: GameTime(seconds=20),
        )

        fluid_starts = [planned.start for planned in plan.items if planned.is_fluid]
        whole_starts = [planned.start for planned in plan.items if not planned.is_fluid]
        assert whole_starts
        assert max(whole_starts) < GameTime(seconds=20)
        assert fluid_starts
        assert min(fluid_starts) >= GameTime(seconds=20)


class TestUnlockFraction:
    @staticmethod
    def _plan_for_medivacs(*, unlock_fraction: float) -> Plan:
        """The best plan for medivacs from a barracks with the resources in hand, a factory's starts fluid throughout:
        a starport needs a factory."""
        state = _game_start(
            minerals=2000.0,
            vespene=1000.0,
            supply_cap=100.0,
            units={UnitTypeId.SCV: 12, UnitTypeId.COMMAND_CENTER: 1, UnitTypeId.BARRACKS: 1},
        )
        return _best_plan(
            state,
            Reward.weighted({UnitTypeId.MEDIVAC: 1.0}),
            horizon_seconds=240,
            grid=TimeGrid.uniform(GameTime(seconds=10)),
            fluid_after=lambda item: GameTime.ZERO if item == UnitTypeId.FACTORY else None,
            unlock_fraction=unlock_fraction,
        )

    def test_nothing_starts_before_a_whole_structure_granting_its_tech_is_done(self) -> None:
        plan = self._plan_for_medivacs(unlock_fraction=1.0)

        factory_start = _start_seconds(plan, UnitTypeId.FACTORY)[0]
        factory_seconds = _TERRAN_MODEL.unit_items[UnitTypeId.FACTORY].build_seconds
        assert _start_seconds(plan, UnitTypeId.STARPORT)[0] >= factory_start + factory_seconds
        assert _start_seconds(plan, UnitTypeId.MEDIVAC) != []

    def test_with_an_unlock_fraction_of_0_any_fraction_of_a_structure_grants_its_tech(self) -> None:
        """The plan makes its medivacs at a starport, on a fraction of a factory too small to be an item of it."""
        plan = self._plan_for_medivacs(unlock_fraction=0.0)

        assert _start_seconds(plan, UnitTypeId.FACTORY) == []
        assert _start_seconds(plan, UnitTypeId.STARPORT) != []
        assert _start_seconds(plan, UnitTypeId.MEDIVAC) != []

    @staticmethod
    def _plan_for_the_second_level(*, unlock_fraction: float) -> Plan:
        """The best plan for the second level of infantry weapons, which needs the first, both fluid: with what the
        second costs and a quarter of the first's, and nothing coming in."""
        first = _TERRAN_MODEL.upgrade_items[UpgradeId.TERRAN_INFANTRY_WEAPONS_1]
        second = _TERRAN_MODEL.upgrade_items[UpgradeId.TERRAN_INFANTRY_WEAPONS_2]
        state = _game_start(
            minerals=second.minerals + first.minerals / 4,
            vespene=second.vespene + first.vespene / 4,
            units={UnitTypeId.ENGINEERING_BAY: 1, UnitTypeId.ARMORY: 1},
            mineral_fields=0,
        )
        return _best_plan(
            state,
            Reward(upgrade_weights={UpgradeId.TERRAN_INFANTRY_WEAPONS_2: 1.0}),
            horizon_seconds=400,
            grid=TimeGrid.uniform(GameTime(seconds=20)),
            fluid_after=lambda _: GameTime.ZERO,
            unlock_fraction=unlock_fraction,
        )

    @pytest.mark.parametrize("unlock_fraction", [1.0, 0.0], ids=["the whole of it", "any fraction"])
    def test_an_upgrade_needs_the_unlock_fraction_of_the_upgrade_before_it(self, unlock_fraction: float) -> None:
        """A quarter of the first level lets a quarter of the second start in each interval, too little of the first to
        be an item of the plan. Needing the whole of the first, the plan pays for it, and for what is left of the
        second."""
        plan = self._plan_for_the_second_level(unlock_fraction=unlock_fraction)

        assert (_start_seconds(plan, UpgradeId.TERRAN_INFANTRY_WEAPONS_1) != []) == (unlock_fraction == 1.0)
        assert _start_seconds(plan, UpgradeId.TERRAN_INFANTRY_WEAPONS_2) != []

    def test_a_higher_unlock_fraction_leaves_less_for_what_it_unlocks(self) -> None:
        """A quarter of the first level, then a half, then the whole of it has to be paid before the second starts."""
        worths = [
            self._plan_for_the_second_level(unlock_fraction=unlock_fraction).objective
            for unlock_fraction in (0.25, 0.5, 1.0)
        ]
        assert worths[0] > worths[1] > worths[2]

    def test_it_adds_whole_numbers_only_where_what_is_needed_may_be_done_in_fractions(self) -> None:
        """A starport needs a factory: whether one is done is a whole number in each interval a fluid factory can be
        done by, and none where every factory starts whole."""
        reward = Reward.weighted({UnitTypeId.MEDIVAC: 1.0})
        items = _TERRAN_MODEL.items_for(unit_types=reward.unit_types, upgrades=reward.upgrades)
        state = _game_start(units={UnitTypeId.SCV: 12, UnitTypeId.COMMAND_CENTER: 1, UnitTypeId.BARRACKS: 1})

        def whole_numbers(factory_fluid_after: GameTime, *, unlock_fraction: float) -> int:
            program = PlanProgram(
                items=items,
                model=_TERRAN_MODEL,
                edges=TimeGrid.uniform(GameTime(seconds=10)).edges(GameTime(seconds=100)),
                state=state,
                reward=reward,
                economy=_WALKING_6_SECONDS_TO_EVERY_STRUCTURE,
                loss_rates={},
                start_limits={},
                fluid_after_seconds=[
                    factory_fluid_after.seconds if item.unit_type == UnitTypeId.FACTORY else math.inf for item in items
                ],
                max_ignored_overrun=0.25,
                unlock_fraction=unlock_fraction,
                previous_plan=None,
                hysteresis_bonus=0.0,
            )
            return program.linear_program.integral_count

        every_start_whole = whole_numbers(GameTime.INFINITE, unlock_fraction=0.0)
        assert whole_numbers(GameTime.INFINITE, unlock_fraction=1.0) == every_start_whole
        # A factory fluid from 30 seconds on: three of its ten intervals whole, and one started at 30 is done at 80.
        assert whole_numbers(GameTime(seconds=30), unlock_fraction=0.0) == every_start_whole - 7
        assert whole_numbers(GameTime(seconds=30), unlock_fraction=0.5) == every_start_whole - 7 + 2


class TestEconomy:
    @pytest.mark.parametrize(
        "arguments",
        [
            {"minerals_per_miner_second": (1.0, 1.1)},
            {"vespene_per_collector_second": (-0.1,)},
            {"mule_minerals_per_orbital_second": -1.0},
            {"mineral_fields_per_new_base": -1},
            {"geysers_per_new_base": -1},
            {"reassignment_time": GameTime(seconds=-1)},
        ],
        ids=[
            "a rate above the one before",
            "a negative rate",
            "a negative MULE rate",
            "negative fields",
            "negative geysers",
            "a negative reassignment time",
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
        """24 SCVs on 8 fields and no minerals, on 1-second intervals: a start pays from the stock at its interval's
        start and half the interval's income, so the marine's 50 minerals come at 2, 3 and 6 seconds, mined at 21.4,
        17.1 and 8.6 a second."""
        state = _game_start(
            minerals=0.0,
            supply_used=24.0,
            supply_cap=30.0,
            units={UnitTypeId.SCV: 24, UnitTypeId.COMMAND_CENTER: 1, UnitTypeId.BARRACKS: 1},
        )
        plan = _best_plan(
            state,
            Reward.weighted({UnitTypeId.MARINE: 1.0}),
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
        """No vespene and one refinery with three SCVs at it, on 1-second intervals: the marauder's 25 vespene come at 9
        and 14 seconds, collected at 2.7 and 1.8 a second."""
        state = _game_start(
            minerals=500.0,
            units={
                UnitTypeId.SCV: 12,
                UnitTypeId.COMMAND_CENTER: 1,
                UnitTypeId.BARRACKS: 1,
                UnitTypeId.TECH_LAB_BARRACKS: 1,
                UnitTypeId.REFINERY: 1,
            },
            refineries_with_vespene=1,
            vespene_collectors=3,
            free_geysers=1,
        )
        plan = _best_plan(
            state,
            Reward.weighted({UnitTypeId.MARAUDER: 1.0}),
            horizon_seconds=40,
            grid=TimeGrid.uniform(GameTime(seconds=1)),
            economy=Economy(vespene_per_collector_second=rates),
        )
        assert _start_seconds(plan, UnitTypeId.MARAUDER) == [marauder_start]

    @pytest.mark.parametrize(
        ("reassignment_seconds", "marauder_start"), [(3.0, 12.0), (2.0, 11.0), (0.0, 9.0)], ids=["3 s", "2 s", "none"]
    )
    def test_a_worker_moved_to_a_refinery_collects_nothing_for_the_reassignment_time(
        self, reassignment_seconds: float, marauder_start: float
    ) -> None:
        """No vespene and one refinery with no SCV at it, on 1-second intervals: the marauder's 25 vespene, 9 seconds'
        collecting of three SCVs, come that long after they got there."""
        state = _game_start(
            minerals=500.0,
            units={
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
            Reward.weighted({UnitTypeId.MARAUDER: 1.0}),
            horizon_seconds=40,
            grid=TimeGrid.uniform(GameTime(seconds=1)),
            economy=Economy(reassignment_time=GameTime(seconds=reassignment_seconds)),
        )
        assert _start_seconds(plan, UnitTypeId.MARAUDER) == [marauder_start]

    @pytest.mark.parametrize(("reassignment_seconds", "marine_start"), [(3.0, 19.0), (0.0, 16.0)], ids=["3 s", "none"])
    def test_a_worker_moved_from_a_refinery_mines_nothing_for_the_reassignment_time(
        self, reassignment_seconds: float, marine_start: float
    ) -> None:
        """No minerals and the only three SCVs at a refinery, on 1-second intervals: the marine's 50 minerals, 16
        seconds' mining of the three, come that long after they left it."""
        state = _game_start(
            minerals=0.0,
            supply_used=3.0,
            units={
                UnitTypeId.SCV: 3,
                UnitTypeId.COMMAND_CENTER: 1,
                UnitTypeId.BARRACKS: 1,
                UnitTypeId.REFINERY: 1,
            },
            refineries_with_vespene=1,
            vespene_collectors=3,
            free_geysers=1,
        )
        plan = _best_plan(
            state,
            Reward.weighted({UnitTypeId.MARINE: 1.0}),
            horizon_seconds=40,
            grid=TimeGrid.uniform(GameTime(seconds=1)),
            economy=Economy(reassignment_time=GameTime(seconds=reassignment_seconds)),
            start_limits={**_NOTHING_BUT_UNITS, UnitTypeId.SCV: 0},
        )
        assert _start_seconds_of_units_done_in_time(plan, UnitTypeId.MARINE) == [marine_start]

    @pytest.mark.parametrize(("reassignment_seconds", "marine_start"), [(3.0, 55.0), (0.0, 52.0)], ids=["3 s", "none"])
    def test_a_new_scv_mines_nothing_for_the_reassignment_time(
        self, reassignment_seconds: float, marine_start: float
    ) -> None:
        """No minerals and no SCV but one done in 5 seconds, on 1-second intervals: the marine's 50 minerals, 47
        seconds' mining of one, come that long after it is done."""
        state = _game_start(
            minerals=0.0,
            supply_used=1.0,
            units={UnitTypeId.COMMAND_CENTER: 1, UnitTypeId.BARRACKS: 1},
            unit_completions=(UnitCompletion(unit_type=UnitTypeId.SCV, time_left=GameTime(seconds=5)),),
        )
        plan = _best_plan(
            state,
            Reward.weighted({UnitTypeId.MARINE: 1.0}),
            horizon_seconds=80,
            grid=TimeGrid.uniform(GameTime(seconds=1)),
            fluid_after=_whole_over_any_horizon,
            economy=Economy(reassignment_time=GameTime(seconds=reassignment_seconds)),
            start_limits={**_NOTHING_BUT_UNITS, UnitTypeId.SCV: 0},
        )
        assert _start_seconds_of_units_done_in_time(plan, UnitTypeId.MARINE) == [marine_start]

    @pytest.mark.parametrize("reassignment_seconds", [3.0, 0.0], ids=["3 s", "none"])
    def test_a_builder_back_from_its_structure_mines_from_the_end_of_its_walk_back(
        self, reassignment_seconds: float
    ) -> None:
        """No minerals and the only SCV building a supply depot for 5 seconds more, 6 seconds' walk from the fields, on
        1-second intervals: the marine's 50 minerals, 47 seconds' mining, come that long after its walk back, whatever a
        reassignment takes."""
        state = _game_start(
            minerals=0.0,
            supply_used=1.0,
            units={UnitTypeId.SCV: 1, UnitTypeId.COMMAND_CENTER: 1, UnitTypeId.BARRACKS: 1},
            worker_builds=(
                WorkerBuild(
                    unit_type=UnitTypeId.SUPPLY_DEPOT, time_left=GameTime(seconds=5), builder_walks_there=False
                ),
            ),
        )
        plan = _best_plan(
            state,
            Reward.weighted({UnitTypeId.MARINE: 1.0}),
            horizon_seconds=80,
            grid=TimeGrid.uniform(GameTime(seconds=1)),
            fluid_after=_whole_over_any_horizon,
            economy=Economy(reassignment_time=GameTime(seconds=reassignment_seconds)),
            start_limits={**_NOTHING_BUT_UNITS, UnitTypeId.SCV: 0},
        )
        assert _start_seconds_of_units_done_in_time(plan, UnitTypeId.MARINE) == [58.0]

    def test_a_reassignment_costs_its_time_of_an_interval_however_long_the_interval(self) -> None:
        """No vespene and one refinery with no SCV at it, on 10-second intervals: each SCV sent there collects for 7 of
        the first 10 seconds, up to 19 vespene from three, which with half the next interval's pay for a marauder at 10
        seconds; nothing from the first interval would not."""
        state = _game_start(
            minerals=500.0,
            units={
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
            Reward.weighted({UnitTypeId.MARAUDER: 1.0}),
            horizon_seconds=60,
            grid=TimeGrid.uniform(GameTime(seconds=10)),
            economy=Economy(reassignment_time=GameTime(seconds=3)),
        )
        assert _start_seconds(plan, UnitTypeId.MARAUDER)[0] == 10.0
        first = plan.intervals[0]
        assert first.collectors > 0
        collected = first.collectors * 7 * Economy().vespene_per_collector_second[0]
        assert first.end.resources.vespene == pytest.approx(collected)

    def test_a_plan_holds_how_many_workers_mine_and_collect_in_each_interval(self) -> None:
        """One refinery and nothing to start but marauders, on 1-second intervals, with no reassignment time: until the
        first marauder starts, each stock rises by what the plan's miners and collectors bring in."""
        economy = Economy(reassignment_time=GameTime.ZERO)
        state = _game_start(
            minerals=0.0,
            units={
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
            Reward.weighted({UnitTypeId.MARAUDER: 1.0}),
            horizon_seconds=40,
            grid=TimeGrid.uniform(GameTime(seconds=1)),
            economy=economy,
            start_limits={**_NOTHING_BUT_UNITS, UnitTypeId.SCV: 0, UnitTypeId.BARRACKS: 0},
        )
        first_start = int(_start_seconds(plan, UnitTypeId.MARAUDER)[0])
        assert first_start > 0
        for interval in plan.intervals[:first_start]:
            brought_in = interval.end.resources - interval.start.resources
            assert brought_in.minerals == pytest.approx(interval.miners * economy.minerals_per_miner_second[0])
            assert brought_in.vespene == pytest.approx(interval.collectors * economy.vespene_per_collector_second[0])
            assert interval.miners + interval.collectors <= 12 + 1e-6

    def test_every_worker_mines_and_its_minerals_are_in_stock_where_they_buy_nothing(self) -> None:
        """One SCV to train over 14 seconds, done too late to mine, and nothing else to buy: for the stock left at the
        horizon alone, the state's twelve mine throughout, and each interval's stock rises by what they bring in."""
        plan = _best_plan(_game_start(), Reward.weighted({UnitTypeId.SCV: 1.0}), horizon_seconds=14)

        assert _start_seconds(plan, UnitTypeId.SCV) == [0.0]
        mined_in_an_interval = 12 * 2 * Economy().minerals_per_miner_second[0]
        assert [interval.miners for interval in plan.intervals] == pytest.approx([12.0] * 7)
        assert [interval.end.resources.minerals for interval in plan.intervals] == pytest.approx(
            [mined_in_an_interval * intervals for intervals in range(1, 8)]
        )

    def test_a_plan_is_found_where_fewer_scvs_are_left_than_the_state_has_collecting(self) -> None:
        """Six collecting by the state, of three SCVs that are being lost, one of them building a supply depot, and no
        field to mine: the collectors fall as they must."""
        state = _game_start(
            units={UnitTypeId.SCV: 3, UnitTypeId.COMMAND_CENTER: 1, UnitTypeId.BARRACKS: 1, UnitTypeId.REFINERY: 1},
            worker_builds=(
                WorkerBuild(
                    unit_type=UnitTypeId.SUPPLY_DEPOT, time_left=GameTime(seconds=20), builder_walks_there=False
                ),
            ),
            mineral_fields=0,
            refineries_with_vespene=1,
            vespene_collectors=6,
        )
        plan = _best_plan(
            state, Reward.weighted({UnitTypeId.MARINE: 1.0}), horizon_seconds=40, loss_rates={UnitTypeId.SCV: 0.05}
        )
        assert plan.intervals[0].collectors <= 2.0 + 1e-6

    @pytest.mark.parametrize(
        ("mule_rate", "marine_starts"), [(225 / (50 / 0.7875), [14.0]), (0.0, [])], ids=["MULEs", "no MULEs"]
    )
    def test_an_orbital_commands_mules_mine_what_the_economy_says(
        self, mule_rate: float, marine_starts: list[float]
    ) -> None:
        """No SCV and no minerals, on 1-second intervals: the MULEs' 3.5 minerals a second pay for a marine at 14
        seconds, and with none, nothing pays for one. Of the marines done by the plan's end."""
        state = _game_start(
            minerals=0.0,
            supply_used=0.0,
            units={UnitTypeId.ORBITAL_COMMAND: 1, UnitTypeId.BARRACKS: 1},
        )
        plan = _best_plan(
            state,
            Reward.weighted({UnitTypeId.MARINE: 1.0}),
            horizon_seconds=40,
            grid=TimeGrid.uniform(GameTime(seconds=1)),
            economy=dataclasses.replace(
                _WALKING_6_SECONDS_TO_EVERY_STRUCTURE, mule_minerals_per_orbital_second=mule_rate
            ),
        )
        assert _start_seconds_of_units_done_in_time(plan, UnitTypeId.MARINE) == marine_starts

    def test_with_a_limit_of_no_new_command_center_none_starts(self) -> None:
        """The known plan that starts one at 48 seconds."""
        reward = Reward.weighted({UnitTypeId.SCV: 2.0, UnitTypeId.COMMAND_CENTER: 1.0})
        plan = _best_plan(
            _game_start(),
            reward,
            horizon_seconds=150,
            grid=TimeGrid.doubling(),
            fluid_after=_whole_over_any_horizon,
            start_limits={UnitTypeId.COMMAND_CENTER: 0},
        )
        assert _start_seconds(plan, UnitTypeId.COMMAND_CENTER) == []


class TestStartLimits:
    @pytest.mark.parametrize(
        "fluid_after", [_whole_over_any_horizon, lambda _: GameTime.ZERO], ids=["whole starts", "fluid starts"]
    )
    def test_an_item_starts_at_most_its_limit_over_the_plan(
        self, fluid_after: Callable[[UnitTypeId | UpgradeId], GameTime | None]
    ) -> None:
        reward = Reward.weighted({UnitTypeId.SCV: 1.0})

        without = _best_plan(_game_start(), reward, horizon_seconds=60, fluid_after=fluid_after)
        limited = _best_plan(
            _game_start(), reward, horizon_seconds=60, fluid_after=fluid_after, start_limits={UnitTypeId.SCV: 2}
        )

        assert len(_start_seconds(without, UnitTypeId.SCV)) > 2
        assert len(_start_seconds(limited, UnitTypeId.SCV)) == 2

    def test_an_upgrade_with_a_limit_of_none_is_not_researched(self) -> None:
        state = _game_start(
            minerals=100.0,
            vespene=100.0,
            units={UnitTypeId.SCV: 12, UnitTypeId.BARRACKS: 1, UnitTypeId.TECH_LAB_BARRACKS: 1},
        )
        reward = Reward(upgrade_weights={UpgradeId.STIMPACK: 1.0})

        without = _best_plan(state, reward, horizon_seconds=120)
        limited = _best_plan(state, reward, horizon_seconds=120, start_limits={UpgradeId.STIMPACK: 0})

        assert _start_seconds(without, UpgradeId.STIMPACK) == [0.0]
        assert _start_seconds(limited, UpgradeId.STIMPACK) == []

    def test_a_unit_types_limit_is_that_of_the_type_it_counts_as_and_the_lowest_of_them(self) -> None:
        """Twelve SCVs of 15 supply: a supply depot lets a plan for SCVs train more than three."""
        reward = Reward.weighted({UnitTypeId.SCV: 1.0})

        without = _best_plan(_game_start(), reward, horizon_seconds=90)
        limited = _best_plan(
            _game_start(),
            reward,
            horizon_seconds=90,
            start_limits={UnitTypeId.SUPPLY_DEPOT: 5, UnitTypeId.SUPPLY_DEPOT_LOWERED: 0},
        )

        assert _start_seconds(without, UnitTypeId.SUPPLY_DEPOT) != []
        assert _start_seconds(limited, UnitTypeId.SUPPLY_DEPOT) == []
        assert len(_start_seconds(limited, UnitTypeId.SCV)) == 3

    def test_a_limit_on_what_the_plan_would_not_make_does_nothing(self) -> None:
        reward = Reward.weighted({UnitTypeId.SCV: 1.0})
        limits: dict[UnitTypeId | UpgradeId, int] = {
            UnitTypeId.BATTLECRUISER: 0,
            UnitTypeId.ZERGLING: 0,
            UpgradeId.STIMPACK: 0,
        }

        without = _best_plan(_game_start(), reward, horizon_seconds=60)
        limited = _best_plan(_game_start(), reward, horizon_seconds=60, start_limits=limits)

        assert limited.items == without.items
        assert limited.objective == without.objective


class TestReward:
    @pytest.mark.parametrize(
        "segments",
        [
            [],
            [CurveSegment(weight=1.0, end_count=10), CurveSegment(weight=2.0)],
            [
                CurveSegment(weight=1.0, end_count=10),
                CurveSegment(weight=0.5, end_count=5),
                CurveSegment(weight=0.0),
            ],
        ],
        ids=["no segments", "a weight rising", "a count falling"],
    )
    def test_a_curve_that_is_not_concave_raises(self, segments: list[CurveSegment]) -> None:
        with pytest.raises(ValueError, match="MARINE"):
            Reward.weighted({UnitTypeId.MARINE: segments})

    def test_the_last_segments_count_is_ignored(self) -> None:
        Reward.weighted(
            {UnitTypeId.MARINE: [CurveSegment(weight=1.0, end_count=30), CurveSegment(weight=0.0, end_count=0)]}
        )

    @pytest.mark.parametrize("end_seconds", [30, 60], ids=["before its start", "at its start"])
    def test_a_term_that_ends_no_later_than_it_starts_raises(self, end_seconds: int) -> None:
        with pytest.raises(ValueError, match="later than it starts"):
            RewardTerm.weight(UnitTypeId.MARINE, 1.0, start=GameTime(seconds=60), end=GameTime(seconds=end_seconds))

    def test_a_terms_factor_is_1_from_its_start_and_0_from_its_end(self) -> None:
        term = RewardTerm.weight(UnitTypeId.MARINE, 1.0, start=GameTime(seconds=30), end=GameTime(seconds=60))
        factors = [term.factor_at(GameTime(seconds=seconds)) for seconds in (28, 30, 58, 60)]
        assert factors == [0.0, 1.0, 1.0, 0.0]
        assert RewardTerm.weight(UnitTypeId.MARINE, 1.0).factor_at(GameTime(seconds=10_000)) == 1.0

    def test_a_terms_factor_is_what_its_time_factor_gives_between_its_start_and_end(self) -> None:
        """The function is not asked outside them, where it would give a negative factor here."""
        term = RewardTerm.weight(
            UnitTypeId.MARINE,
            1.0,
            start=GameTime(seconds=30),
            end=GameTime(seconds=60),
            time_factor=lambda time: (time.seconds - 30) / 10,
        )
        factors = [term.factor_at(GameTime(seconds=seconds)) for seconds in (20, 30, 50, 60)]
        assert factors == pytest.approx([0.0, 0.0, 2.0, 0.0])

    @pytest.mark.parametrize("factor", [-0.5, math.inf, math.nan], ids=["negative", "infinite", "not a number"])
    def test_a_time_factor_that_is_negative_or_not_finite_raises(self, factor: float) -> None:
        term = RewardTerm.weight(UnitTypeId.MARINE, 1.0, time_factor=lambda _: factor)
        with pytest.raises(ValueError, match="time factor"):
            term.factor_at(GameTime(seconds=10))

    @pytest.mark.parametrize(
        "arguments",
        [{"half_life": GameTime.ZERO}, {"terminal_time": GameTime(seconds=-1)}, {"stock_weight": -1e-4}],
        ids=["a half-life of 0", "a negative terminal time", "a negative weight on the stock"],
    )
    def test_a_bad_discount_tail_or_stock_weight_raises(self, arguments: dict[str, Any]) -> None:
        with pytest.raises(ValueError):
            Reward(**arguments)

    def test_the_unit_types_it_values_are_those_its_terms_count(self) -> None:
        reward = Reward.weighted(
            {UnitTypeId.MARINE: 1.0, UnitTypeId.MEDIVAC: [CurveSegment(weight=1.0)]},
            terms=[
                RewardTerm.weight({UnitTypeId.VIKING: 1.0, UnitTypeId.THOR: 2.0}, 1.0, end_count=8.0),
                RewardTerm.weight(UnitTypeId.BUNKER, 1.0, end_count=2.0),
            ],
        )
        assert reward.unit_types == {
            UnitTypeId.MARINE,
            UnitTypeId.MEDIVAC,
            UnitTypeId.VIKING,
            UnitTypeId.THOR,
            UnitTypeId.BUNKER,
        }

    def test_the_upgrades_it_values_are_those_weighted_and_those_its_terms_require(self) -> None:
        reward = Reward(
            terms=[RewardTerm.weight(UnitTypeId.MARINE, 0.3, requires=[UpgradeId.COMBAT_SHIELD])],
            upgrade_weights={UpgradeId.STIMPACK: 1.0},
        )
        assert reward.upgrades == {UpgradeId.COMBAT_SHIELD, UpgradeId.STIMPACK}

    def test_a_negative_weight_in_a_term_that_requires_an_upgrade_raises(self) -> None:
        with pytest.raises(ValueError, match="negative"):
            RewardTerm.weight(UnitTypeId.MARINE, -1.0, requires=[UpgradeId.COMBAT_SHIELD])

    def test_the_unit_types_a_combination_counts_are_those_of_all_its_groups(self) -> None:
        healed = RewardTerm.combination(
            [{UnitTypeId.MEDIVAC: 8.0}, {UnitTypeId.MARINE: 1.0, UnitTypeId.MARAUDER: 2.0}], 0.3
        )
        assert healed.unit_types == {UnitTypeId.MEDIVAC, UnitTypeId.MARINE, UnitTypeId.MARAUDER}
        assert Reward(terms=[healed]).unit_types == healed.unit_types

    def test_a_negative_weight_on_a_combination_raises(self) -> None:
        with pytest.raises(ValueError, match="negative"):
            RewardTerm.combination([{UnitTypeId.MEDIVAC: 8.0}, {UnitTypeId.MARINE: 1.0}], -0.3)

    def test_a_term_of_no_group_raises(self) -> None:
        with pytest.raises(ValueError, match="group"):
            RewardTerm.combination([], 0.3)


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
            assigned_harvesters=3,
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


def _unit_completions_in_seconds(state: GameState) -> list[tuple[UnitTypeId, float]]:
    """`state`'s unit completions, each as its type and its seconds left."""
    return [(completion.unit_type, completion.time_left.seconds) for completion in state.unit_completions]


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
    def test_it_reads_stock_supply_and_finished_units_by_the_type_each_counts_as(self) -> None:
        (state,) = _states_of(_main_base_at(0))

        assert (state.resources, state.supply_used, state.supply_cap) == (Resources(200, 25), 15, 23)
        assert state.units == {
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

        assert sorted(_unit_completions_in_seconds(state)) == pytest.approx(
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

        assert inside.units[UnitTypeId.SCV] == 13

    def test_it_reads_the_fields_and_free_geysers_of_its_bases_but_not_of_a_free_expansion(self) -> None:
        (state,) = _states_of(_main_base_at(0))

        assert (state.mineral_fields, state.free_geysers, state.refineries_with_vespene) == (8, 1, 1)

    def test_it_reads_the_workers_assigned_to_its_refineries_as_those_collecting(self) -> None:
        (state,) = _states_of(_main_base_at(0))

        assert state.vespene_collectors == 3

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
            completion.time_left
            for state in states
            for completion in state.unit_completions
            if completion.unit_type == UnitTypeId.ORBITAL_COMMAND
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
                    _plan_found(
                        api.extensions.plan_now(Reward.weighted({UnitTypeId.MARINE: 1.0}), horizon=GameTime(seconds=60))
                    ),
                )
            )

        api.play(game.joined())

        ((before, plan),) = plans
        assert before is None
        assert api.extensions.plan is plan
        assert plan.made_at == GameTime.ZERO
        assert plan.intervals[0].start.units[UnitTypeId.BARRACKS] == 1.0
        assert UnitTypeId.MARINE in {planned.item for planned in plan.items}

    def test_it_returns_none_and_holds_no_plan_if_the_solver_finds_none_within_its_limits(self) -> None:
        """A billionth of a second ends the solve before it has begun."""
        game = ScriptedGame(
            [_main_base_at(0), _main_base_at(1)], game_info=make_game_info(*["#" * 48] * 64), data=shipped_game_data()
        )
        api = Api(extensions=lambda api: Planner(api, time_limit=1e-9))
        plans: list[Plan | None] = []

        @api.on(TurnEvent)
        def plan_marines(_: TurnEvent) -> None:
            plans.append(
                api.extensions.plan_now(Reward.weighted({UnitTypeId.MARINE: 1.0}), horizon=GameTime(seconds=60))
            )

        api.play(game.joined())

        assert plans == [None]
        assert api.extensions.plan is None

    def test_it_holds_the_plan_before_if_the_solver_finds_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        game = ScriptedGame(
            [_main_base_at(0), _main_base_at(1)], game_info=make_game_info(*["#" * 48] * 64), data=shipped_game_data()
        )
        api = Api(extensions=lambda api: Planner(api, time_limit=None, node_limit=_NODE_LIMIT))
        plans: list[Plan | None] = []

        def find_no_plan(_: PlanProgram, *, time_limit: float | None, node_limit: int | None) -> None:
            return None

        @api.on(TurnEvent)
        def plan_marines_twice(_: TurnEvent) -> None:
            reward = Reward.weighted({UnitTypeId.MARINE: 1.0})
            plans.append(api.extensions.plan_now(reward, horizon=GameTime(seconds=60)))
            monkeypatch.setattr(PlanProgram, "solve", find_no_plan)
            plans.append(api.extensions.plan_now(reward, horizon=GameTime(seconds=60)))

        api.play(game.joined())

        found, not_found = plans
        assert found is not None
        assert not_found is None
        assert api.extensions.plan is found

    def test_it_plans_over_5_minutes_unless_handed_a_horizon(self) -> None:
        game = ScriptedGame(
            [_main_base_at(0), _main_base_at(1)], game_info=make_game_info(*["#" * 48] * 64), data=shipped_game_data()
        )
        api = Api(extensions=Planner)
        plans: list[Plan] = []

        @api.on(TurnEvent)
        def plan_twice(_: TurnEvent) -> None:
            plans.append(_plan_found(api.extensions.plan_now(Reward())))
            plans.append(_plan_found(api.extensions.plan_now(Reward(), horizon=GameTime(seconds=60))))

        api.play(game.joined())

        by_default, over_a_minute = plans
        assert by_default.intervals[-1].end.time - by_default.made_at == GameTime(seconds=300)
        assert over_a_minute.intervals[-1].end.time - over_a_minute.made_at == GameTime(seconds=60)

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
        "arguments",
        [
            {"time_limit": 0.0},
            {"node_limit": 0},
            {"max_ignored_overrun": -0.1},
            {"max_ignored_overrun": 1.1},
            {"unlock_fraction": -0.1},
            {"unlock_fraction": 1.1},
        ],
        ids=[
            "a time limit of 0",
            "a node limit of 0",
            "an overrun ignored below 0",
            "an overrun ignored above 1",
            "an unlock fraction below 0",
            "an unlock fraction above 1",
        ],
    )
    def test_a_limit_of_0_or_a_share_outside_0_to_1_raises(self, arguments: dict[str, Any]) -> None:
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

    def test_a_negative_time_from_fluid_after_raises(self) -> None:
        game = ScriptedGame([make_observation(0), make_observation(1)], data=shipped_game_data())
        api = Api(extensions=lambda api: Planner(api, fluid_after=lambda _: GameTime(seconds=-1)))

        @api.on(TurnEvent)
        def plan_marines(_: TurnEvent) -> None:
            api.extensions.plan_now(Reward.weighted({UnitTypeId.MARINE: 1.0}), horizon=GameTime(seconds=10))

        with pytest.raises(ValueError, match="negative time"):
            api.play(game.joined())

    def test_it_plans_from_a_state_it_is_handed(self) -> None:
        game = ScriptedGame(
            [_main_base_at(0), _main_base_at(1)], game_info=make_game_info(*["#" * 48] * 64), data=shipped_game_data()
        )
        api = Api(extensions=lambda api: Planner(api, time_limit=None, node_limit=_NODE_LIMIT))
        plans: list[Plan] = []

        @api.on(TurnEvent)
        def plan_from_a_later_time(_: TurnEvent) -> None:
            state = dataclasses.replace(api.extensions.game_state(), time=GameTime(seconds=90))
            reward = Reward.weighted({UnitTypeId.MARINE: 1.0})
            plans.append(_plan_found(api.extensions.plan_now(reward, horizon=GameTime(seconds=60), state=state)))

        api.play(game.joined())

        ((plan),) = plans
        assert plan.made_at == plan.intervals[0].start.time == GameTime(seconds=90)

    def test_it_keeps_to_a_previous_plan_it_is_handed_for_a_bonus(self) -> None:
        """With 150 minerals and none mined, a barracks costs the plan its SCVs, which the reward is for: it starts
        one for a bonus worth more. The barracks waits for the supply depot an SCV is walking to build, done 27.4
        seconds in, so the previous plan has it at 32."""
        game = ScriptedGame(
            [_main_base_at(0), _main_base_at(1)], game_info=make_game_info(*["#" * 48] * 64), data=shipped_game_data()
        )
        api = Api(extensions=lambda api: Planner(api, time_limit=None, node_limit=_NODE_LIMIT))
        plans: list[Plan] = []
        a_barracks_at_32_seconds = PlannedItem(item=UnitTypeId.BARRACKS, start=GameTime(seconds=32), is_fluid=False)
        previous_plan = dataclasses.replace(_A_PLAN_OF_ONE_BARRACKS, items=(a_barracks_at_32_seconds,))

        @api.on(TurnEvent)
        def plan_scvs_twice(_: TurnEvent) -> None:
            state = dataclasses.replace(api.extensions.game_state(), resources=Resources(150.0, 0.0), mineral_fields=0)
            reward = Reward.weighted({UnitTypeId.SCV: 1.0})
            horizon = GameTime(seconds=40)
            plans.append(_plan_found(api.extensions.plan_now(reward, horizon=horizon, state=state)))
            plans.append(
                _plan_found(
                    api.extensions.plan_now(
                        reward,
                        horizon=horizon,
                        state=state,
                        previous_plan=previous_plan,
                        hysteresis_bonus=100.0,
                    )
                )
            )

        api.play(game.joined())

        without, kept = plans
        assert _start_seconds(without, UnitTypeId.BARRACKS) == []
        assert _start_seconds(without, UnitTypeId.SCV) != []
        assert len(_start_seconds(kept, UnitTypeId.BARRACKS)) == 1
        assert _start_seconds(kept, UnitTypeId.SCV) == []

    def test_it_starts_no_more_of_an_item_than_the_limit_it_is_handed(self) -> None:
        game = ScriptedGame(
            [_main_base_at(0), _main_base_at(1)], game_info=make_game_info(*["#" * 48] * 64), data=shipped_game_data()
        )
        api = Api(extensions=lambda api: Planner(api, time_limit=None, node_limit=_NODE_LIMIT))
        plans: list[Plan] = []

        @api.on(TurnEvent)
        def plan_scvs_twice(_: TurnEvent) -> None:
            reward = Reward.weighted({UnitTypeId.SCV: 1.0})
            horizon = GameTime(seconds=40)
            plans.append(_plan_found(api.extensions.plan_now(reward, horizon=horizon)))
            limited_to_one_scv = api.extensions.plan_now(reward, horizon=horizon, start_limits={UnitTypeId.SCV: 1})
            plans.append(_plan_found(limited_to_one_scv))

        api.play(game.joined())

        without, limited = plans
        assert len(_start_seconds(without, UnitTypeId.SCV)) > 1
        assert len(_start_seconds(limited, UnitTypeId.SCV)) == 1

    @pytest.mark.parametrize(
        "arguments",
        [
            {"start_limits": {UnitTypeId.COMMAND_CENTER: -1}},
            {"economy": Economy(walk_time=lambda _: GameTime(seconds=-1))},
            {"hysteresis_bonus": 1.0},
            {"previous_plan": _A_PLAN_OF_ONE_BARRACKS, "hysteresis_bonus": -1.0},
        ],
        ids=[
            "a negative start limit",
            "a negative walk",
            "a bonus with no previous plan",
            "a negative bonus",
        ],
    )
    def test_a_negative_limit_walk_or_bonus_or_a_bonus_with_no_previous_plan_raises(
        self, arguments: dict[str, Any]
    ) -> None:
        game = ScriptedGame([make_observation(0), make_observation(1)], data=shipped_game_data())
        api = Api(extensions=Planner)

        @api.on(TurnEvent)
        def plan_marines(_: TurnEvent) -> None:
            api.extensions.plan_now(
                Reward.weighted({UnitTypeId.MARINE: 1.0}), horizon=GameTime(seconds=10), **arguments
            )

        with pytest.raises(ValueError):
            api.play(game.joined())
