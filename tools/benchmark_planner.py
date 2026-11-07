"""Benchmark the build planner on the cases of its toy, `tools/planner_toy/`, from the game's data rather than the toy's
14 hand-written items: each solve's time, objective, gap and counts, and the first part of its plan.

Every case plans from the start of a game, twelve SCVs on a main's eight fields beside its command center, 50
minerals, 12 of 15 supply and three free expansions, over 10 minutes, valuing marines, marauders and medivacs as the
toy did, with no walks unless the case says so:

- `reference`: the reference configuration, the doubling grid, starts of townhalls, production and tech structures and
  upgrades whole over the horizon and the rest for 90 seconds, and a discounted concave reward, at each half-life
  given by `--half-lives`, with walks: 25 seconds to the expansion and 6 to anything else.
- `grids`: the toy's run7, its four grids with every start whole for 90 seconds and fluid after.
- `windows`: its run8, those windows against the reference's, with the linear reward.
- `rewards`: its run9, the linear reward against the concave one.
- `enemy`: its run10, covering an enemy armored, turning to air at 4:00, or air.
- `walks`: its run11, no walks against Terran's.
- `losses`: its run12, losing army units at 0.002 a second, or workers at 0.001.
- `discount`: its run2's discount, the linear reward at each half-life given by `--half-lives`.

    uv run python tools/benchmark_planner.py reference --time-limit 3
    uv run python tools/benchmark_planner.py grids --time-limit 10
"""

from __future__ import annotations

import argparse
import dataclasses
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from types import MappingProxyType

from sc2nachos import GameTime
from sc2nachos.extensions.planner import Coverage, CurveSegment, Plan, Reward, TimeGrid
from sc2nachos.extensions.planner._plan_program import PlanProgram
from sc2nachos.extensions.planner._snapshot import Snapshot
from sc2nachos.extensions.planner._terran_model import TerranModel
from sc2nachos.ids import UnitTypeId
from sc2nachos.testing import load_game_data

_HORIZON = GameTime(seconds=600)
_FIRST_SECONDS_SHOWN = 90.0
_TOY_WEIGHTS = {UnitTypeId.MARINE: 1.0, UnitTypeId.MARAUDER: 2.2, UnitTypeId.MEDIVAC: 2.5}
# The toy's run9: the first 30 marines at full weight, the next 30 at a third, then nothing; the others alike.
_TOY_CURVES = {
    UnitTypeId.MARINE: (CurveSegment(slope=1.0, up_to=30), CurveSegment(slope=0.33, up_to=60), CurveSegment(slope=0.0)),
    UnitTypeId.MARAUDER: (
        CurveSegment(slope=2.2, up_to=12),
        CurveSegment(slope=0.7, up_to=30),
        CurveSegment(slope=0.0),
    ),
    UnitTypeId.MEDIVAC: (CurveSegment(slope=2.5, up_to=4), CurveSegment(slope=0.8, up_to=8), CurveSegment(slope=0.0)),
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
_TOY_GRIDS = {
    "step": TimeGrid(
        segments=[
            (GameTime(seconds=2), GameTime(seconds=90)),
            (GameTime(seconds=30), GameTime(seconds=300)),
            (GameTime(seconds=60), GameTime.INFINITE),
        ]
    ),
    "double60": TimeGrid.doubling(first_doubling=GameTime(seconds=60)),
    "double90": TimeGrid.doubling(first_doubling=GameTime(seconds=90), longest_bucket=GameTime(seconds=16)),
    "double45": TimeGrid.doubling(),
}


def main() -> None:
    """Run the case the command line names."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("case", choices=sorted(_CASES))
    parser.add_argument("--time-limit", type=float, default=10.0, help="seconds of wall-clock time for each solve")
    parser.add_argument("--half-lives", default="none,120,240,480", help="the half-lives in seconds, or none")
    arguments = parser.parse_args()
    model = TerranModel(load_game_data())
    half_lives = [None if text == "none" else GameTime(seconds=float(text)) for text in arguments.half_lives.split(",")]
    _CASES[arguments.case](model, arguments.time_limit, half_lives)


def _game_start(model: TerranModel, walks: Mapping[UnitTypeId, float] = _NO_WALKS) -> Snapshot:
    """The state a game starts in, with an SCV's walk to each structure in `walks`, and none to the others."""
    walk_seconds = {
        unit_type: walks.get(unit_type, 0.0) for unit_type, item in model.unit_items.items() if item.is_built_by_worker
    }
    return Snapshot(
        time=GameTime.ZERO,
        minerals=50.0,
        vespene=0.0,
        supply_used=12.0,
        supply_cap=15.0,
        counts={UnitTypeId.SCV: 12, UnitTypeId.COMMAND_CENTER: 1},
        mineral_fields=8,
        free_geysers=2,
        free_expansions=3,
        walk_seconds=walk_seconds,
    )


def _solve_and_print(
    label: str,
    model: TerranModel,
    snapshot: Snapshot,
    reward: Reward,
    *,
    grid: TimeGrid,
    time_limit: float,
    lumpy_whole_over_horizon: bool = True,
) -> Plan | None:
    """Plan for `reward` from `snapshot` on `grid` within `time_limit` seconds, and print what came of it: with
    `lumpy_whole_over_horizon` false, every start is whole for the grid's window alone, as in the toy's runs before
    run8."""
    items = model.items_for(unit_types=reward.unit_types, upgrades=reward.upgrade_weights)
    if not lumpy_whole_over_horizon:
        items = tuple(dataclasses.replace(item, is_whole_over_horizon=False) for item in items)
    started = time.perf_counter()
    program = PlanProgram(
        items=items,
        model=model,
        edges=grid.edges(_HORIZON),
        snapshot=snapshot,
        reward=reward,
        whole_until_seconds=grid.whole_until.seconds,
    )
    built = time.perf_counter() - started
    plan = program.solve(time_limit=time_limit, node_limit=None)
    buckets = len(grid.edges(_HORIZON)) - 1
    size = f"{buckets} buckets, {program.linear_program.integral_count} integers, built in {built * 1000:.0f} ms"
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
    print("    first 90 s: " + ", ".join(f"{planned.start.seconds:.0f}s {planned.item.name}" for planned in first))
    return plan


def _run_reference(model: TerranModel, time_limit: float, half_lives: Sequence[GameTime | None]) -> None:
    """The reference configuration at each half-life."""
    walks = {unit_type: _REFERENCE_WALK_SECONDS for unit_type in model.unit_items} | {UnitTypeId.COMMAND_CENTER: 25.0}
    for half_life in half_lives:
        reward = Reward(curves=_TOY_CURVES, half_life=half_life)
        label = f"reference, half-life {'none' if half_life is None else f'{half_life.seconds:.0f} s'}"
        _solve_and_print(
            label, model, _game_start(model, walks), reward, grid=TimeGrid.doubling(), time_limit=time_limit
        )


def _run_grids(model: TerranModel, time_limit: float, _: Sequence[GameTime | None]) -> None:
    """The toy's grids, every start whole for 90 seconds."""
    reward = Reward(weights=_TOY_WEIGHTS)
    for name, grid in _TOY_GRIDS.items():
        _solve_and_print(
            f"grid {name}",
            model,
            _game_start(model),
            reward,
            grid=grid,
            time_limit=time_limit,
            lumpy_whole_over_horizon=False,
        )


def _run_windows(model: TerranModel, time_limit: float, _: Sequence[GameTime | None]) -> None:
    """Every start whole for 90 seconds, against the reference's windows."""
    reward = Reward(weights=_TOY_WEIGHTS)
    for lumpy_whole in (False, True):
        label = "lumpy whole over the horizon" if lumpy_whole else "everything whole for 90 s only"
        _solve_and_print(
            label,
            model,
            _game_start(model),
            reward,
            grid=TimeGrid.doubling(),
            time_limit=time_limit,
            lumpy_whole_over_horizon=lumpy_whole,
        )


def _run_rewards(model: TerranModel, time_limit: float, _: Sequence[GameTime | None]) -> None:
    """The linear reward against the concave one."""
    for label, reward in (("linear", Reward(weights=_TOY_WEIGHTS)), ("concave", Reward(curves=_TOY_CURVES))):
        _solve_and_print(
            f"{label} reward", model, _game_start(model), reward, grid=TimeGrid.doubling(), time_limit=time_limit
        )


def _run_enemy(model: TerranModel, time_limit: float, _: Sequence[GameTime | None]) -> None:
    """Covering each of the toy's enemies."""
    for name, scenario in _ENEMY_SCENARIOS.items():
        coverages = [
            Coverage(enemy_counts=counts, effectiveness=_EFFECTIVENESS_AGAINST[kind], value=1.0)
            for kind, counts in scenario.items()
        ]
        reward = Reward(weights=_STANDING_WEIGHTS, coverages=coverages)
        plan = _solve_and_print(
            f"enemy {name}", model, _game_start(model), reward, grid=TimeGrid.doubling(), time_limit=time_limit
        )
        if plan is not None:
            _print_starts_by_minute(plan, (UnitTypeId.MARINE, UnitTypeId.MARAUDER))


def _run_walks(model: TerranModel, time_limit: float, _: Sequence[GameTime | None]) -> None:
    """No walks against the toy's Terran walks."""
    reward = Reward(weights=_TOY_WEIGHTS)
    for label, walks in (("no walks", _NO_WALKS), ("Terran walks", _TOY_WALKS)):
        _solve_and_print(
            label, model, _game_start(model, walks), reward, grid=TimeGrid.doubling(), time_limit=time_limit
        )


def _run_losses(model: TerranModel, time_limit: float, _: Sequence[GameTime | None]) -> None:
    """Army units lost at 0.002 a second, or workers at 0.001."""
    reward = Reward(weights=_TOY_WEIGHTS)
    for label, rates in (
        ("army lost at 0.002/s", dict.fromkeys(_TOY_WEIGHTS, 0.002)),
        ("workers lost at 0.001/s", {UnitTypeId.SCV: 0.001}),
    ):
        snapshot = dataclasses.replace(_game_start(model), loss_rates=rates)
        plan = _solve_and_print(label, model, snapshot, reward, grid=TimeGrid.doubling(), time_limit=time_limit)
        if plan is not None:
            _print_starts_by_minute(plan, (UnitTypeId.MARINE, UnitTypeId.MARAUDER, UnitTypeId.BARRACKS, UnitTypeId.SCV))


def _run_discount(model: TerranModel, time_limit: float, half_lives: Sequence[GameTime | None]) -> None:
    """The linear reward at each half-life."""
    for half_life in half_lives:
        reward = Reward(weights=_TOY_WEIGHTS, half_life=half_life)
        label = f"half-life {'none' if half_life is None else f'{half_life.seconds:.0f} s'}"
        _solve_and_print(label, model, _game_start(model), reward, grid=TimeGrid.doubling(), time_limit=time_limit)


def _print_starts_by_minute(plan: Plan, unit_types: Sequence[UnitTypeId]) -> None:
    """How many of each of `unit_types` the plan starts in each of its ten minutes."""
    for unit_type in unit_types:
        by_minute = Counter(
            int((planned.start - plan.made_at).seconds // 60) for planned in plan.items if planned.item == unit_type
        )
        print(
            f"    {unit_type.name:>9} started a minute: " + " ".join(f"{by_minute[minute]:3d}" for minute in range(10))
        )


_CASES: Mapping[str, Callable[[TerranModel, float, Sequence[GameTime | None]], None]] = {
    "reference": _run_reference,
    "grids": _run_grids,
    "windows": _run_windows,
    "rewards": _run_rewards,
    "enemy": _run_enemy,
    "walks": _run_walks,
    "losses": _run_losses,
    "discount": _run_discount,
}


if __name__ == "__main__":
    main()
