"""Benchmark the build planner on the cases of its toy, `tools/planner_toy/`, from the game's data rather than the toy's
14 hand-written items: each solve's time, objective, gap and counts, and the first part of its plan.

The toy's cases keep its grid, 2-second intervals doubling from 45 seconds, and its 10 minutes, and `reference`,
`replans` and `items` plan on the planner's defaults, its grid and its 5 minutes; `--horizon` sets another time for
any case, in seconds. Every case but `replans` and `items` plans from the start of a game, twelve SCVs on a main's
eight fields beside its command center, 50 minerals and 12 of 15 supply, and every case with at most three new command
centers. But for those two, each values marines, marauders and medivacs as the toy did, with no walks unless it says
so. Those three value the stock left at the horizon as the planner does by default, which is in their objective, and the
toy's cases do not, as the toy did not:

- `reference`: the reference configuration, the doubling grid, the planner's default windows of whole starts, and a
  discounted concave reward, at each half-life given by `--half-lives`, with walks: 25 seconds to the expansion and 6
  to anything else.
- `grids`: the toy's run7, its four grids with every start whole for 90 seconds and fluid after.
- `windows`: its run8, every start whole for 90 seconds against its lumpy items whole over the horizon, and both
  against the planner's default, with the linear reward.
- `rewards`: its run9, the linear reward against the concave one.
- `enemy`: its run10, covering an enemy armored, turning to air at 4:00, or air.
- `walks`: its run11, no walks against Terran's.
- `losses`: its run12, losing army units at 0.002 a second, or workers at 0.001.
- `discount`: its run2's discount, the linear reward at each half-life given by `--half-lives`.
- `replans`: a plan made in the middle of a game, as a bot asks for most of its plans: from two made-up states, two
  bases at 3:00 and at 6:00 with units in production and three SCVs at each refinery, with the reference's grid,
  windows and walks, at each half-life, for a reward on marines, marauders and medivacs that never saturates, each
  count still worth something.
- `items`: the item set a wide reward brings, 38 items where the toy's has 14: the reward of `replans` and ten more
  army types, and eight upgrades, from the game's start and from the states of `replans`, with the reference's grid,
  windows and walks, at each half-life.

    uv run python tools/benchmark_planner.py reference --time-limit 3
    uv run python tools/benchmark_planner.py reference --time-limit 3 --horizon 600
    uv run python tools/benchmark_planner.py grids --time-limit 10
"""

from __future__ import annotations

import argparse
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from types import MappingProxyType

from sc2nachos import GameTime, Resources
from sc2nachos.extensions.planner import (
    CurveSegment,
    Economy,
    GameState,
    Occupation,
    Plan,
    Reward,
    RewardTerm,
    TimeGrid,
    UnitCompletion,
    WorkerBuild,
)
from sc2nachos.extensions.planner._plan_program import PlanProgram
from sc2nachos.extensions.planner._planner import fluid_after_seconds_of
from sc2nachos.extensions.planner._terran_model import TerranModel
from sc2nachos.ids import UnitTypeId, UpgradeId
from sc2nachos.testing import load_game_data

# What the toy planned over, and the cases that keep to it.
_TOY_HORIZON = GameTime(seconds=600)
# What `plan_now` plans over by default, and the cases on the planner's defaults.
_PLANNER_HORIZON = GameTime(seconds=300)
_CASES_ON_THE_PLANNERS_DEFAULTS = frozenset({"reference", "replans", "items"})
_START_LIMITS: Mapping[UnitTypeId | UpgradeId, int] = {UnitTypeId.COMMAND_CENTER: 3}
# The planner's default: a whole build running into an interval by less than a quarter of it leaves the interval free.
_MAX_IGNORED_OVERRUN = 0.25
# The planner's default: nothing starts before a whole structure granting each tech it needs is done.
_UNLOCK_FRACTION = 1.0
_FIRST_SECONDS_SHOWN = 90.0
_TOY_WEIGHTS = {UnitTypeId.MARINE: 1.0, UnitTypeId.MARAUDER: 2.2, UnitTypeId.MEDIVAC: 2.5}
# The toy's run9: the first 30 marines at full weight, the next 30 at a third, then nothing; the others alike.
_TOY_CURVES = {
    UnitTypeId.MARINE: (
        CurveSegment(weight=1.0, end_count=30),
        CurveSegment(weight=0.33, end_count=60),
        CurveSegment(weight=0.0),
    ),
    UnitTypeId.MARAUDER: (
        CurveSegment(weight=2.2, end_count=12),
        CurveSegment(weight=0.7, end_count=30),
        CurveSegment(weight=0.0),
    ),
    UnitTypeId.MEDIVAC: (
        CurveSegment(weight=2.5, end_count=4),
        CurveSegment(weight=0.8, end_count=8),
        CurveSegment(weight=0.0),
    ),
}
# Like the toy's curves, but no count is worth nothing, so that the reward never saturates.
_GROWING_CURVES = {
    UnitTypeId.MARINE: (
        CurveSegment(weight=1.0, end_count=30),
        CurveSegment(weight=0.5, end_count=80),
        CurveSegment(weight=0.25),
    ),
    UnitTypeId.MARAUDER: (
        CurveSegment(weight=2.2, end_count=12),
        CurveSegment(weight=1.1, end_count=40),
        CurveSegment(weight=0.5),
    ),
    UnitTypeId.MEDIVAC: (
        CurveSegment(weight=2.5, end_count=4),
        CurveSegment(weight=1.2, end_count=10),
        CurveSegment(weight=0.4),
    ),
}
# The wide reward: what each of the first six units of ten more army types is worth a second, each one after them a
# third of it, and what each of eight upgrades is worth a second once done.
_FIRST_SIX_WEIGHTS_OF_THE_WIDE_REWARD = {
    UnitTypeId.SIEGE_TANK: 4.0,
    UnitTypeId.VIKING: 3.0,
    UnitTypeId.HELLION: 1.2,
    UnitTypeId.CYCLONE: 3.0,
    UnitTypeId.LIBERATOR: 3.5,
    UnitTypeId.WIDOW_MINE: 1.5,
    UnitTypeId.THOR: 7.0,
    UnitTypeId.GHOST: 4.0,
    UnitTypeId.RAVEN: 3.5,
    UnitTypeId.BANSHEE: 3.5,
}
_UPGRADE_WEIGHTS_OF_THE_WIDE_REWARD = {
    UpgradeId.STIMPACK: 20.0,
    UpgradeId.COMBAT_SHIELD: 10.0,
    UpgradeId.CONCUSSIVE_SHELLS: 6.0,
    UpgradeId.TERRAN_INFANTRY_WEAPONS_1: 12.0,
    UpgradeId.TERRAN_INFANTRY_ARMOR_1: 10.0,
    UpgradeId.TERRAN_INFANTRY_WEAPONS_2: 14.0,
    UpgradeId.TERRAN_INFANTRY_ARMOR_2: 12.0,
    UpgradeId.TERRAN_VEHICLE_WEAPONS_1: 8.0,
}
# The toy's run10: a small standing value a unit, and the rest from covering the enemy.
_STANDING_WEIGHTS = {UnitTypeId.MARINE: 0.1, UnitTypeId.MARAUDER: 0.2, UnitTypeId.MEDIVAC: 0.1}
_EFFECTIVENESS_AGAINST = {
    "air": {UnitTypeId.MARINE: 1.0, UnitTypeId.MARAUDER: 0.0},
    "armored": {UnitTypeId.MARINE: 0.3, UnitTypeId.MARAUDER: 1.0},
    "light": {UnitTypeId.MARINE: 0.8, UnitTypeId.MARAUDER: 0.3},
}
_ENEMY_SCENARIOS: Mapping[str, Mapping[str, Sequence[tuple[GameTime, float]]]] = {
    "armored": {"armored": [(GameTime.ZERO, 30.0)], "light": [(GameTime.ZERO, 10.0)], "air": []},
    "air-at-4:00": {
        "armored": [(GameTime.ZERO, 30.0)],
        "light": [(GameTime.ZERO, 10.0)],
        "air": [(GameTime(seconds=240), 25.0)],
    },
    "air": {"armored": [], "light": [(GameTime.ZERO, 10.0)], "air": [(GameTime.ZERO, 25.0)]},
}
# The toy's run11: the walk there, and back, to each structure.
_TOY_WALKS = {
    UnitTypeId.COMMAND_CENTER: 25.0,
    UnitTypeId.SUPPLY_DEPOT: 6.0,
    UnitTypeId.REFINERY: 5.0,
    UnitTypeId.BARRACKS: 8.0,
    UnitTypeId.FACTORY: 8.0,
    UnitTypeId.STARPORT: 8.0,
}
_REFERENCE_WALK_SECONDS = 6.0
_NO_WALKS: Mapping[UnitTypeId, float] = MappingProxyType({})
_NO_LOSSES: Mapping[UnitTypeId, float] = MappingProxyType({})
# The toy's run8: townhalls, their morphs and the structures that make or research something, whole over the horizon.
_WHOLE_OVER_THE_HORIZON_IN_THE_TOY = frozenset(
    {
        UnitTypeId.COMMAND_CENTER,
        UnitTypeId.ORBITAL_COMMAND,
        UnitTypeId.PLANETARY_FORTRESS,
        UnitTypeId.BARRACKS,
        UnitTypeId.FACTORY,
        UnitTypeId.STARPORT,
        UnitTypeId.ENGINEERING_BAY,
        UnitTypeId.ARMORY,
        UnitTypeId.GHOST_ACADEMY,
        UnitTypeId.FUSION_CORE,
    }
)
_TOY_GRIDS = {
    "step": TimeGrid(
        segments=[
            (GameTime(seconds=2), GameTime(seconds=90)),
            (GameTime(seconds=30), GameTime(seconds=300)),
            (GameTime(seconds=60), GameTime.INFINITE),
        ]
    ),
    "double60": TimeGrid.doubling(
        initial_interval_length=GameTime(seconds=2), initial_interval_count=30, max_interval_doublings=4
    ),
    "double90": TimeGrid(
        segments=[
            (GameTime(seconds=2), GameTime(seconds=90)),
            (GameTime(seconds=4), GameTime(seconds=180)),
            (GameTime(seconds=8), GameTime(seconds=360)),
            (GameTime(seconds=16), GameTime.INFINITE),
        ]
    ),
    "double45": TimeGrid.doubling(
        initial_interval_length=GameTime(seconds=2), initial_interval_count=22, max_interval_doublings=4
    ),
}
# The grid of the toy's later runs, which the planner's default was before it was cut to a worker's build time.
_TOY_DOUBLING_GRID = _TOY_GRIDS["double45"]


def main() -> None:
    """Run the case the command line names."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("case", choices=sorted(_CASES))
    parser.add_argument("--time-limit", type=float, default=10.0, help="seconds of wall-clock time for each solve")
    parser.add_argument("--half-lives", default="none,120,240,480", help="the half-lives in seconds, or none")
    parser.add_argument(
        "--horizon",
        type=float,
        help="the seconds each plan looks ahead: by default the planner's 300 for `reference`, `replans` and `items`, "
        "and the toy's 600 for the rest",
    )
    arguments = parser.parse_args()
    model = TerranModel(load_game_data())
    half_lives = [None if text == "none" else GameTime(seconds=float(text)) for text in arguments.half_lives.split(",")]
    default_horizon = _PLANNER_HORIZON if arguments.case in _CASES_ON_THE_PLANNERS_DEFAULTS else _TOY_HORIZON
    horizon = default_horizon if arguments.horizon is None else GameTime(seconds=arguments.horizon)
    _CASES[arguments.case](model, arguments.time_limit, half_lives, horizon)


def _game_start() -> GameState:
    """The state a game starts in."""
    return GameState(
        time=GameTime.ZERO,
        resources=Resources(50.0, 0.0),
        supply_used=12.0,
        supply_cap=15.0,
        units={UnitTypeId.SCV: 12, UnitTypeId.COMMAND_CENTER: 1},
        mineral_fields=8,
        free_geysers=2,
    )


def _in_production(
    unit_type: UnitTypeId, producer: UnitTypeId, seconds_left: float, *, needs_tech_lab: bool = False
) -> tuple[UnitCompletion, Occupation]:
    """A unit of `unit_type` that `producer` makes, done in `seconds_left`, and what keeps the producer busy until
    then, its tech lab too if the unit `needs_tech_lab`."""
    time_left = GameTime(seconds=seconds_left)
    occupation = Occupation(
        producer=producer,
        time_until_start=GameTime.ZERO,
        time_left=time_left,
        needs_tech_lab=needs_tech_lab,
    )
    return UnitCompletion(unit_type=unit_type, time_left=time_left), occupation


def _mid_game_states() -> dict[str, GameState]:
    """Two made-up states in the middle of a game, after what the planner's best plan for the growing reward at a
    half-life of 480 s expects there, its fractions made whole: two bases at 3:00, four barracks making marines and a
    marauder, a reactor and a supply depot going up; and at 6:00, five barracks and a starport."""
    at_3_minutes = [
        _in_production(UnitTypeId.MARINE, UnitTypeId.BARRACKS, 10.0),
        _in_production(UnitTypeId.MARINE, UnitTypeId.BARRACKS, 10.0),
        _in_production(UnitTypeId.MARINE, UnitTypeId.BARRACKS, 2.0),
        _in_production(UnitTypeId.MARAUDER, UnitTypeId.BARRACKS, 5.0, needs_tech_lab=True),
        _in_production(UnitTypeId.REACTOR_BARRACKS, UnitTypeId.BARRACKS, 20.0),
        _in_production(UnitTypeId.SCV, UnitTypeId.ORBITAL_COMMAND, 4.0),
        _in_production(UnitTypeId.SCV, UnitTypeId.ORBITAL_COMMAND, 4.0),
    ]
    at_6_minutes = [
        _in_production(UnitTypeId.MARINE, UnitTypeId.BARRACKS, 2.0),
        _in_production(UnitTypeId.MARINE, UnitTypeId.BARRACKS, 2.0),
        _in_production(UnitTypeId.MARINE, UnitTypeId.BARRACKS, 2.0),
        _in_production(UnitTypeId.MARAUDER, UnitTypeId.BARRACKS, 5.0, needs_tech_lab=True),
        _in_production(UnitTypeId.MARAUDER, UnitTypeId.BARRACKS, 5.0, needs_tech_lab=True),
        _in_production(UnitTypeId.MEDIVAC, UnitTypeId.STARPORT, 14.0),
    ]
    return {
        "3:00": GameState(
            time=GameTime(seconds=180),
            resources=Resources(110.0, 10.0),
            supply_used=38.0,
            supply_cap=46.0,
            units={
                UnitTypeId.SCV: 24,
                UnitTypeId.ORBITAL_COMMAND: 2,
                UnitTypeId.SUPPLY_DEPOT: 2,
                UnitTypeId.REFINERY: 1,
                UnitTypeId.BARRACKS: 4,
                UnitTypeId.REACTOR_BARRACKS: 1,
                UnitTypeId.TECH_LAB_BARRACKS: 1,
                UnitTypeId.MARINE: 5,
                UnitTypeId.MARAUDER: 1,
            },
            unit_completions=tuple(completion for completion, _ in at_3_minutes),
            occupations=tuple(occupation for _, occupation in at_3_minutes),
            worker_builds=(
                WorkerBuild(
                    unit_type=UnitTypeId.SUPPLY_DEPOT, time_left=GameTime(seconds=13), builder_walks_there=False
                ),
            ),
            mineral_fields=16,
            refineries_with_vespene=1,
            vespene_collectors=3,
            free_geysers=3,
        ),
        "6:00": GameState(
            time=GameTime(seconds=360),
            resources=Resources(440.0, 60.0),
            supply_used=114.0,
            supply_cap=126.0,
            units={
                UnitTypeId.SCV: 34,
                UnitTypeId.ORBITAL_COMMAND: 2,
                UnitTypeId.SUPPLY_DEPOT: 12,
                UnitTypeId.REFINERY: 2,
                UnitTypeId.BARRACKS: 5,
                UnitTypeId.REACTOR_BARRACKS: 2,
                UnitTypeId.TECH_LAB_BARRACKS: 3,
                UnitTypeId.STARPORT: 1,
                UnitTypeId.MARINE: 39,
                UnitTypeId.MARAUDER: 15,
                UnitTypeId.MEDIVAC: 1,
            },
            unit_completions=tuple(completion for completion, _ in at_6_minutes),
            occupations=tuple(occupation for _, occupation in at_6_minutes),
            mineral_fields=16,
            refineries_with_vespene=2,
            vespene_collectors=6,
            free_geysers=2,
        ),
    }


def _walking(walks: Mapping[UnitTypeId, float], *, reassignment_time: GameTime = GameTime.ZERO) -> Economy:
    """The default economy, but with an SCV's walk to each structure in `walks`, and none to the others, and with
    `reassignment_time`, by default none as in the toy, which moves a worker to or from a refinery for nothing."""
    return Economy(
        reassignment_time=reassignment_time,
        walk_time=lambda unit_type: GameTime(seconds=walks.get(unit_type, 0.0)),
    )


def _solve_and_print(
    label: str,
    model: TerranModel,
    reward: Reward,
    *,
    grid: TimeGrid,
    time_limit: float,
    horizon: GameTime,
    fluid_after: Callable[[UnitTypeId | UpgradeId], GameTime | None] | None = None,
    economy: Economy | None = None,
    loss_rates: Mapping[UnitTypeId, float] = _NO_LOSSES,
    state: GameState | None = None,
) -> Plan | None:
    """Plan for `reward` from `state`, the game's start if `None`, over `horizon` on `grid` within `time_limit` seconds,
    with the windows of whole starts `fluid_after` gives, as `Planner` takes it, by `economy`, with no walks if `None`,
    and `loss_rates`, and print what came of it."""
    items = model.items_for(unit_types=reward.unit_types, upgrades=reward.upgrade_weights)
    planned_from = _game_start() if state is None else state
    planned_by = _walking(_NO_WALKS) if economy is None else economy
    started = time.perf_counter()
    program = PlanProgram(
        items=items,
        model=model,
        edges=grid.edges(horizon),
        state=planned_from,
        reward=reward,
        economy=planned_by,
        loss_rates=loss_rates,
        start_limits=_START_LIMITS,
        fluid_after_seconds=fluid_after_seconds_of(
            items, fluid_after=fluid_after, state=planned_from, economy=planned_by
        ),
        max_ignored_overrun=_MAX_IGNORED_OVERRUN,
        unlock_fraction=_UNLOCK_FRACTION,
        previous_plan=None,
        hysteresis_bonus=0.0,
    )
    built = time.perf_counter() - started
    plan = program.solve(time_limit=time_limit, node_limit=None)
    intervals = len(grid.edges(horizon)) - 1
    size = (
        f"{len(items)} items, {intervals} intervals, {program.linear_program.integral_count} integers, "
        f"built in {built * 1000:.0f} ms"
    )
    if plan is None:
        print(f"{label}: {size}, no plan within {time_limit} s")
        return None
    counts = Counter(planned.item.name for planned in plan.items)
    print(
        f"{label}: {size}, {plan.solve_seconds:.2f} s, objective {plan.objective:.1f}, gap {plan.gap:.1e}"
        f"{', optimal' if plan.is_optimal else ''}"
    )
    print(f"    counts: {dict(counts)}")
    first = [planned for planned in plan.items if (planned.start - plan.made_at).seconds < _FIRST_SECONDS_SHOWN]
    starts = (f"{(planned.start - plan.made_at).seconds:.0f}s {planned.item.name}" for planned in first)
    print("    first 90 s: " + ", ".join(starts))
    return plan


def _run_reference(
    model: TerranModel, time_limit: float, half_lives: Sequence[GameTime | None], horizon: GameTime
) -> None:
    """The reference configuration at each half-life."""
    for half_life in half_lives:
        reward = Reward.weighted(_TOY_CURVES, half_life=half_life)
        _solve_and_print(
            f"reference, {_half_life_label(half_life)}",
            model,
            reward,
            grid=TimeGrid.doubling(),
            time_limit=time_limit,
            horizon=horizon,
            economy=_reference_economy(model),
        )


def _run_replans(
    model: TerranModel, time_limit: float, half_lives: Sequence[GameTime | None], horizon: GameTime
) -> None:
    """The growing reward from each mid-game state, at each half-life."""
    for name, state in _mid_game_states().items():
        for half_life in half_lives:
            _solve_and_print(
                f"replan at {name}, {_half_life_label(half_life)}",
                model,
                Reward.weighted(_GROWING_CURVES, half_life=half_life),
                grid=TimeGrid.doubling(),
                time_limit=time_limit,
                horizon=horizon,
                economy=_reference_economy(model),
                state=state,
            )


def _run_items(model: TerranModel, time_limit: float, half_lives: Sequence[GameTime | None], horizon: GameTime) -> None:
    """The wide reward from the game's start and from each mid-game state, at each half-life."""
    curves: dict[UnitTypeId, tuple[CurveSegment, ...]] = dict(_GROWING_CURVES)
    for unit_type, weight in _FIRST_SIX_WEIGHTS_OF_THE_WIDE_REWARD.items():
        curves[unit_type] = (CurveSegment(weight=weight, end_count=6), CurveSegment(weight=weight / 3))
    for name, state in ({"0:00": _game_start()} | _mid_game_states()).items():
        for half_life in half_lives:
            _solve_and_print(
                f"wide reward at {name}, {_half_life_label(half_life)}",
                model,
                Reward.weighted(curves, upgrade_weights=_UPGRADE_WEIGHTS_OF_THE_WIDE_REWARD, half_life=half_life),
                grid=TimeGrid.doubling(),
                time_limit=time_limit,
                horizon=horizon,
                economy=_reference_economy(model),
                state=state,
            )


def _reference_economy(model: TerranModel) -> Economy:
    """The reference configuration's economy: the default, its reassignment time too, but with a walk of 25 seconds to
    a command center, and of 6 to any other structure."""
    walks = {unit_type: _REFERENCE_WALK_SECONDS for unit_type in model.unit_items} | {UnitTypeId.COMMAND_CENTER: 25.0}
    return _walking(walks, reassignment_time=Economy().reassignment_time)


def _half_life_label(half_life: GameTime | None) -> str:
    """How a solve's label names `half_life`."""
    return f"half-life {'none' if half_life is None else f'{half_life.seconds:.0f} s'}"


def _run_grids(model: TerranModel, time_limit: float, _: Sequence[GameTime | None], horizon: GameTime) -> None:
    """The toy's grids, every start whole for 90 seconds."""
    reward = Reward.weighted(_TOY_WEIGHTS, stock_weight=0.0)
    for name, grid in _TOY_GRIDS.items():
        _solve_and_print(
            f"grid {name}",
            model,
            reward,
            grid=grid,
            time_limit=time_limit,
            horizon=horizon,
            fluid_after=_whole_for_90_seconds,
        )


def _run_windows(model: TerranModel, time_limit: float, _: Sequence[GameTime | None], horizon: GameTime) -> None:
    """Every start whole for 90 seconds, against the toy's lumpy items whole over the horizon, and the default."""
    reward = Reward.weighted(_TOY_WEIGHTS, stock_weight=0.0)
    for label, fluid_after in (
        ("everything whole for 90 s only", _whole_for_90_seconds),
        ("lumpy whole over the horizon, the rest for 90 s", _whole_as_in_the_toy),
        ("the planner's default", None),
    ):
        _solve_and_print(
            label,
            model,
            reward,
            grid=_TOY_DOUBLING_GRID,
            time_limit=time_limit,
            horizon=horizon,
            fluid_after=fluid_after,
        )


def _run_rewards(model: TerranModel, time_limit: float, _: Sequence[GameTime | None], horizon: GameTime) -> None:
    """The linear reward against the concave one."""
    for label, reward in (
        ("linear", Reward.weighted(_TOY_WEIGHTS, stock_weight=0.0)),
        ("concave", Reward.weighted(_TOY_CURVES, stock_weight=0.0)),
    ):
        _solve_and_print(
            f"{label} reward", model, reward, grid=_TOY_DOUBLING_GRID, time_limit=time_limit, horizon=horizon
        )


def _run_enemy(model: TerranModel, time_limit: float, _: Sequence[GameTime | None], horizon: GameTime) -> None:
    """Covering each of the toy's enemies."""
    for name, scenario in _ENEMY_SCENARIOS.items():
        coverages = [
            RewardTerm.covering(_EFFECTIVENESS_AGAINST[kind], counts, value=1.0) for kind, counts in scenario.items()
        ]
        reward = Reward.weighted(_STANDING_WEIGHTS, terms=coverages, stock_weight=0.0)
        plan = _solve_and_print(
            f"enemy {name}", model, reward, grid=_TOY_DOUBLING_GRID, time_limit=time_limit, horizon=horizon
        )
        if plan is not None:
            _print_starts_by_minute(plan, (UnitTypeId.MARINE, UnitTypeId.MARAUDER))


def _run_walks(model: TerranModel, time_limit: float, _: Sequence[GameTime | None], horizon: GameTime) -> None:
    """No walks against the toy's Terran walks."""
    reward = Reward.weighted(_TOY_WEIGHTS, stock_weight=0.0)
    for label, walks in (("no walks", _NO_WALKS), ("Terran walks", _TOY_WALKS)):
        _solve_and_print(
            label,
            model,
            reward,
            grid=_TOY_DOUBLING_GRID,
            time_limit=time_limit,
            horizon=horizon,
            economy=_walking(walks),
        )


def _run_losses(model: TerranModel, time_limit: float, _: Sequence[GameTime | None], horizon: GameTime) -> None:
    """Army units lost at 0.002 a second, or workers at 0.001."""
    reward = Reward.weighted(_TOY_WEIGHTS, stock_weight=0.0)
    for label, rates in (
        ("army lost at 0.002/s", dict.fromkeys(_TOY_WEIGHTS, 0.002)),
        ("workers lost at 0.001/s", {UnitTypeId.SCV: 0.001}),
    ):
        plan = _solve_and_print(
            label, model, reward, grid=_TOY_DOUBLING_GRID, time_limit=time_limit, horizon=horizon, loss_rates=rates
        )
        if plan is not None:
            _print_starts_by_minute(plan, (UnitTypeId.MARINE, UnitTypeId.MARAUDER, UnitTypeId.BARRACKS, UnitTypeId.SCV))


def _run_discount(
    model: TerranModel, time_limit: float, half_lives: Sequence[GameTime | None], horizon: GameTime
) -> None:
    """The linear reward at each half-life."""
    for half_life in half_lives:
        reward = Reward.weighted(_TOY_WEIGHTS, half_life=half_life, stock_weight=0.0)
        _solve_and_print(
            _half_life_label(half_life), model, reward, grid=_TOY_DOUBLING_GRID, time_limit=time_limit, horizon=horizon
        )


def _whole_for_90_seconds(item: UnitTypeId | UpgradeId) -> GameTime | None:
    """Every unit type whole for 90 seconds, as in the toy's runs before run8, and an upgrade by its default."""
    return GameTime(seconds=90) if isinstance(item, UnitTypeId) else None


def _whole_as_in_the_toy(item: UnitTypeId | UpgradeId) -> GameTime | None:
    """The toy's run8 windows: its lumpy items whole over the horizon, the other unit types for 90 seconds, and an
    upgrade by its default."""
    if not isinstance(item, UnitTypeId):
        return None
    return GameTime.INFINITE if item in _WHOLE_OVER_THE_HORIZON_IN_THE_TOY else GameTime(seconds=90)


def _print_starts_by_minute(plan: Plan, unit_types: Sequence[UnitTypeId]) -> None:
    """How many of each of `unit_types` the plan starts in each of its ten minutes."""
    for unit_type in unit_types:
        by_minute = Counter(
            int((planned.start - plan.made_at).seconds // 60) for planned in plan.items if planned.item == unit_type
        )
        print(
            f"    {unit_type.name:>9} started a minute: " + " ".join(f"{by_minute[minute]:3d}" for minute in range(10))
        )


_CASES: Mapping[str, Callable[[TerranModel, float, Sequence[GameTime | None], GameTime], None]] = {
    "reference": _run_reference,
    "grids": _run_grids,
    "windows": _run_windows,
    "rewards": _run_rewards,
    "enemy": _run_enemy,
    "walks": _run_walks,
    "losses": _run_losses,
    "discount": _run_discount,
    "replans": _run_replans,
    "items": _run_items,
}


if __name__ == "__main__":
    main()
