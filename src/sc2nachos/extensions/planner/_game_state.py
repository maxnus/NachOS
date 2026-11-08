"""The state of the game as the planner reads it: plain values, taken from the api, which a bot may change."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, final

from sc2nachos.gametime import GameTime
from sc2nachos.ids import UnitTypeId, UpgradeId
from sc2nachos.units import UnitType

if TYPE_CHECKING:
    from sc2nachos import Api
    from sc2nachos.extensions.planner._terran_model import TerranModel
    from sc2nachos.state import InProduction
    from sc2nachos.units import OwnUnit


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Occupation:
    """A producer kept busy by what it makes now: a train, a morph, an add-on or a research."""

    producer: UnitTypeId
    """The producer's type, in its usual form."""
    time_until_start: GameTime
    """The time from the state's until it starts: zero for one running, more for one queued behind another."""
    time_left: GameTime
    """The time from the state's until it is done."""
    needs_tech_lab: bool
    """Whether it is a unit its producer makes only with a tech lab."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class WorkerBuild:
    """A structure an SCV builds, going up or on its way."""

    unit_type: UnitTypeId
    """Its type, in its usual form."""
    time_left: GameTime
    """The building it has left: its whole build time if not begun."""
    builder_walks_there: bool
    """Whether an SCV has yet to walk there: for a build not begun, and for a structure going up whose SCV has left."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class GameState:
    """The state of the game a plan starts from, as plain values: what `Planner.game_state` reads, which a bot may
    change with `dataclasses.replace` and hand to `Planner.plan_now`, to leave out a base about to fall, say. The
    planner pools what it holds: it never knows which base a field, a refinery or a worker is at."""

    time: GameTime
    """The game time it was taken at, from which the plan's times count."""
    minerals: float
    """The minerals in stock."""
    vespene: float
    """The vespene in stock."""
    supply_used: float
    """The supply taken, by units alive and in production."""
    supply_cap: float
    """The supply cap, up to the game's limit."""
    counts: Mapping[UnitTypeId, int]
    """The number of this player's finished units alive of each type, in its usual form, those inside a gas building or
    a transport included, which the observation leaves out."""
    upgrades: frozenset[UpgradeId] = frozenset()
    """The upgrades done."""
    unit_completions: tuple[tuple[UnitTypeId, GameTime], ...] = ()
    """Each unit in production but a structure an SCV builds, of its type in its usual form, with the time until it is
    done."""
    upgrade_completions: Mapping[UpgradeId, GameTime] = field(default_factory=dict)
    """Each upgrade being researched, with the time until it is done."""
    occupations: tuple[Occupation, ...] = ()
    """What keeps each producer busy now."""
    worker_builds: tuple[WorkerBuild, ...] = ()
    """Each structure an SCV builds, going up or on its way."""
    mineral_fields: int = 0
    """The mineral fields left at bases with a finished townhall of this player's."""
    refineries_with_vespene: int = 0
    """This player's finished refineries with vespene left."""
    free_geysers: int = 0
    """The geysers without a gas building at bases with a finished townhall of this player's, less one for each refinery
    an SCV is on its way to build."""


def read_game_state(api: Api[object], *, model: TerranModel) -> GameState:
    """The state of the game `api` plays now, its unit types read by `model`."""
    counts, refineries_with_vespene = _counts_of_own_units(api, model)
    fields, free_geysers = _bases_of(api)
    unit_completions, occupations, worker_builds = _production_of(api, model)
    refineries_not_begun = sum(
        build.unit_type == UnitTypeId.REFINERY and in_production.unit is None for build, in_production in worker_builds
    )
    upgrade_completions, research_occupations = _researches_of(api, model)
    return GameState(
        time=api.time,
        minerals=api.resources.minerals,
        vespene=api.resources.vespene,
        supply_used=api.supply.used,
        supply_cap=api.supply.cap,
        counts=counts,
        upgrades=api.upgrades,
        unit_completions=tuple(unit_completions),
        upgrade_completions=upgrade_completions,
        occupations=tuple(occupations + research_occupations),
        worker_builds=tuple(build for build, _ in worker_builds),
        mineral_fields=fields,
        refineries_with_vespene=refineries_with_vespene,
        free_geysers=max(free_geysers - refineries_not_begun, 0),
    )


def _counts_of_own_units(api: Api[object], model: TerranModel) -> tuple[dict[UnitTypeId, int], int]:
    """The number of this player's finished units of each type, in its usual form, and of its finished refineries with
    vespene left. Those known alive count, an SCV inside a refinery and a marine in a bunker among them, which the last
    observation leaves out (in game)."""
    counts: dict[UnitTypeId, int] = {}
    refineries_with_vespene = 0
    for unit in api.known_units.own:
        if not unit.is_complete:
            continue
        usual = model.usual_form(unit.type_id)
        counts[usual] = counts.get(usual, 0) + 1
        if UnitType.GasBuilding.includes(unit) and unit.vespene_contents > 0:
            refineries_with_vespene += 1
    return counts, refineries_with_vespene


def _bases_of(api: Api[object]) -> tuple[int, int]:
    """The mineral fields left and the geysers free at bases with a finished townhall of this player's."""
    gas_building_positions = {building.position for building in api.units.gas_buildings}
    fields = 0
    free_geysers = 0
    for expansion in api.expansions:
        townhall = expansion.townhall
        if townhall is not None and townhall.is_own and townhall.is_complete:
            fields += len(expansion.mineral_fields.alive)
            free_geysers += sum(geyser.position not in gas_building_positions for geyser in expansion.geysers)
    return fields, free_geysers


def _production_of(
    api: Api[object], model: TerranModel
) -> tuple[list[tuple[UnitTypeId, GameTime]], list[Occupation], list[tuple[WorkerBuild, InProduction]]]:
    """Each unit in production but a structure an SCV builds, with the time until it is done; what keeps each
    producer busy; and each structure an SCV builds, with what it was read from. A structure not begun, or going up
    with no SCV building it, waits for an SCV's walk there."""
    completions: list[tuple[UnitTypeId, GameTime]] = []
    worker_builds: list[tuple[WorkerBuild, InProduction]] = []
    by_producer: dict[int, list[InProduction]] = {}
    producers: dict[int, OwnUnit[Any]] = {}
    for in_production in api.in_production(model.forms_of_items):
        unit_type = model.usual_form(in_production.product)
        producer = in_production.producer
        if model.unit_items[unit_type].is_built_by_worker:
            walks_there = in_production.unit is None or producer is None
            build = WorkerBuild(unit_type=unit_type, time_left=in_production.time_left, builder_walks_there=walks_there)
            worker_builds.append((build, in_production))
        elif producer is None:
            completions.append((unit_type, in_production.time_left))
        else:
            by_producer.setdefault(producer.id, []).append(in_production)
            producers[producer.id] = producer
    occupations: list[Occupation] = []
    for producer_id, queue in by_producer.items():
        producer = producers[producer_id]
        lanes = [GameTime.ZERO] * max(producer.production_capacity, 1)
        for in_production in sorted(queue, key=lambda queued: queued.fraction == 0):
            unit_type = model.usual_form(in_production.product)
            lane = lanes.index(min(lanes))
            start = lanes[lane]
            lanes[lane] = start + in_production.time_left
            completions.append((unit_type, lanes[lane]))
            occupations.append(
                Occupation(
                    producer=model.usual_form(producer.type_id),
                    time_until_start=start,
                    time_left=lanes[lane],
                    needs_tech_lab=model.unit_items[unit_type].needs_tech_lab,
                )
            )
    return completions, occupations, worker_builds


def _researches_of(api: Api[object], model: TerranModel) -> tuple[dict[UpgradeId, GameTime], list[Occupation]]:
    """Each research's time until it is done, one structure researching one at a time, and what keeps each researcher
    busy."""
    completions: dict[UpgradeId, GameTime] = {}
    occupations: list[Occupation] = []
    free_after: dict[int, GameTime] = {}
    researches = sorted(api.researches.items(), key=lambda research: research[1].fraction == 0)
    for upgrade, research in researches:
        start = free_after.get(research.unit.id, GameTime.ZERO)
        end = start + research.time_left
        free_after[research.unit.id] = end
        completions[upgrade] = end
        occupations.append(
            Occupation(
                producer=model.usual_form(research.unit.type_id),
                time_until_start=start,
                time_left=end,
                needs_tech_lab=False,
            )
        )
    return completions, occupations
