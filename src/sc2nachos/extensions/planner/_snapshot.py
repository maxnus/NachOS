"""The state of the game as the planner reads it: plain numbers, taken from the api."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, final

from sc2nachos.gametime import GameTime
from sc2nachos.ids import UnitTypeId, UpgradeId
from sc2nachos.units import UnitType

if TYPE_CHECKING:
    from sc2nachos import Api
    from sc2nachos.extensions.planner._terran_model import TerranModel
    from sc2nachos.gamemap import Expansion
    from sc2nachos.state import InProduction
    from sc2nachos.units import OwnUnit


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Occupation:
    """A producer kept busy by what it makes now: a train, a morph, an add-on or a research."""

    producer: UnitTypeId
    """The producer's type, in its usual form."""
    start_seconds: float
    """When it starts, in seconds from the snapshot: 0 for one running, later for one queued behind another."""
    end_seconds: float
    """When it is done, in seconds from the snapshot."""
    needs_tech_lab: bool
    """Whether it is a unit its producer makes only with a tech lab."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Snapshot:
    """The state of the game the planner plans from, as plain numbers, and what the bot expects of it."""

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
    """The number of this player's finished units alive of each type, in its usual form."""
    upgrades: frozenset[UpgradeId] = frozenset()
    """The upgrades done."""
    unit_completions: tuple[tuple[UnitTypeId, float], ...] = ()
    """Each unit in production, of its type in its usual form, with the seconds until it is done."""
    upgrade_completions: Mapping[UpgradeId, float] = field(default_factory=dict)
    """Each upgrade being researched, with the seconds until it is done."""
    occupations: tuple[Occupation, ...] = ()
    """What keeps each producer busy now."""
    builders_free_after: tuple[float, ...] = ()
    """For each SCV building a structure or walking to build one, the seconds until it is back at work."""
    mineral_fields: int = 0
    """The mineral fields left at bases with a finished townhall of this player's."""
    refineries_with_vespene: int = 0
    """This player's finished refineries with vespene left."""
    free_geysers: int = 0
    """The geysers without a gas building at bases with a finished townhall of this player's, less one for each refinery
    an SCV is on its way to build."""
    free_expansions: int = 0
    """The expansions with no townhall on them, in sight or remembered, that an SCV can walk to from this player's
    start, less one for each command center an SCV is on its way to build."""
    walk_seconds: Mapping[UnitTypeId, float] = field(default_factory=dict)
    """The seconds an SCV walks to build each structure, and walks back after: to the nearest free expansion for a
    command center."""
    loss_rates: Mapping[UnitTypeId, float] = field(default_factory=dict)
    """The share of a unit type's units the bot expects lost each second."""

    @classmethod
    def from_api(
        cls,
        api: Api[object],
        *,
        model: TerranModel,
        walk_time: GameTime,
        loss_rates: Mapping[UnitTypeId, float],
    ) -> Snapshot:
        """The state of the game `api` plays now, its unit types read by `model`. An SCV walks `walk_time` to build any
        structure but a command center, and a build it has not begun takes that walk first."""
        counts, refineries_with_vespene = _counts_of_own_units(api, model)
        fields, free_geysers, free_expansions, seconds_to_expansion = _bases_of(api)
        walk_seconds = {
            unit_type: seconds_to_expansion if unit_type == UnitTypeId.COMMAND_CENTER else walk_time.seconds
            for unit_type, item in model.unit_items.items()
            if item.is_built_by_worker
        }
        unit_completions, occupations, builders = _production_of(api, model, walk_seconds)
        not_begun = _builds_not_begun(api, model)
        upgrade_completions, research_occupations = _researches_of(api, model)
        return cls(
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
            builders_free_after=tuple(builders),
            mineral_fields=fields,
            refineries_with_vespene=refineries_with_vespene,
            free_geysers=max(free_geysers - not_begun.get(UnitTypeId.REFINERY, 0), 0),
            free_expansions=max(free_expansions - not_begun.get(UnitTypeId.COMMAND_CENTER, 0), 0),
            walk_seconds=walk_seconds,
            loss_rates={model.usual_form(unit_type): rate for unit_type, rate in loss_rates.items()},
        )


def _counts_of_own_units(api: Api[object], model: TerranModel) -> tuple[dict[UnitTypeId, int], int]:
    """The number of this player's finished units of each type, in its usual form, and of its finished refineries with
    vespene left."""
    counts: dict[UnitTypeId, int] = {}
    refineries_with_vespene = 0
    for unit in api.units.own:
        if not unit.is_complete:
            continue
        usual = model.usual_form(unit.type_id)
        counts[usual] = counts.get(usual, 0) + 1
        if UnitType.GasBuilding.includes(unit) and unit.vespene_contents > 0:
            refineries_with_vespene += 1
    return counts, refineries_with_vespene


def _bases_of(api: Api[object]) -> tuple[int, int, int, float]:
    """The mineral fields left and the geysers free at bases with a finished townhall of this player's, the expansions
    with no townhall that an SCV can walk to from this player's start, and the seconds it walks to the nearest of those,
    0 with none."""
    gas_building_positions = {building.position for building in api.units.gas_buildings}
    fields = 0
    free_geysers = 0
    free: list[Expansion] = []
    for expansion in api.expansions:
        townhall = expansion.townhall
        if townhall is None:
            free.append(expansion)
        elif townhall.is_own and townhall.is_complete:
            fields += len(expansion.mineral_fields.alive)
            free_geysers += sum(geyser.position not in gas_building_positions for geyser in expansion.geysers)
    if not free:
        return fields, free_geysers, 0, 0.0
    distances = api.pathing.cheapest_paths_from(api.player.start_location.center).distances
    reachable = [distance for expansion in free if math.isfinite(distance := distances[expansion.center])]
    if not reachable:
        return fields, free_geysers, 0, 0.0
    return fields, free_geysers, len(reachable), min(reachable) / api.data.units[UnitTypeId.SCV].speed


def _production_of(
    api: Api[object],
    model: TerranModel,
    walk_seconds: Mapping[UnitTypeId, float],
) -> tuple[list[tuple[UnitTypeId, float]], list[Occupation], list[float]]:
    """Each unit in production with the seconds until it is done, what keeps each producer busy, and the seconds until
    each SCV building or walking to build is back at work. A structure not begun, or going up with no SCV building it,
    waits for an SCV's walk there."""
    completions: list[tuple[UnitTypeId, float]] = []
    builders: list[float] = []
    by_producer: dict[int, list[InProduction]] = {}
    producers: dict[int, OwnUnit[Any]] = {}
    for in_production in api.in_production(model.forms_of_items):
        unit_type = model.usual_form(in_production.product)
        item = model.unit_items[unit_type]
        producer = in_production.producer
        if item.is_built_by_worker:
            walk = walk_seconds.get(unit_type, 0.0)
            done_after = in_production.time_left.seconds
            done_after += walk if in_production.unit is None or producer is None else 0.0
            completions.append((unit_type, done_after))
            builders.append(done_after + walk)
        elif producer is None:
            completions.append((unit_type, in_production.time_left.seconds))
        else:
            by_producer.setdefault(producer.id, []).append(in_production)
            producers[producer.id] = producer
    occupations: list[Occupation] = []
    for producer_id, queue in by_producer.items():
        producer = producers[producer_id]
        lanes = [0.0] * max(producer.production_capacity, 1)
        for in_production in sorted(queue, key=lambda queued: queued.fraction == 0):
            unit_type = model.usual_form(in_production.product)
            item = model.unit_items[unit_type]
            lane = lanes.index(min(lanes))
            start = lanes[lane]
            lanes[lane] = start + in_production.time_left.seconds
            completions.append((unit_type, lanes[lane]))
            occupations.append(
                Occupation(
                    producer=model.usual_form(producer.type_id),
                    start_seconds=start,
                    end_seconds=lanes[lane],
                    needs_tech_lab=item.needs_tech_lab,
                )
            )
    return completions, occupations, builders


def _builds_not_begun(api: Api[object], model: TerranModel) -> dict[UnitTypeId, int]:
    """How many structures of each type, in its usual form, an SCV is on its way to build."""
    not_begun: dict[UnitTypeId, int] = {}
    for in_production in api.in_production(model.forms_of_items):
        unit_type = model.usual_form(in_production.product)
        if in_production.unit is None and model.unit_items[unit_type].is_built_by_worker:
            not_begun[unit_type] = not_begun.get(unit_type, 0) + 1
    return not_begun


def _researches_of(api: Api[object], model: TerranModel) -> tuple[dict[UpgradeId, float], list[Occupation]]:
    """Each research's seconds until it is done, one structure researching one at a time, and what keeps each
    researcher busy."""
    completions: dict[UpgradeId, float] = {}
    occupations: list[Occupation] = []
    free_after: dict[int, float] = {}
    researches = sorted(api.researches.items(), key=lambda research: research[1].fraction == 0)
    for upgrade, research in researches:
        start = free_after.get(research.unit.id, 0.0)
        end = start + research.time_left.seconds
        free_after[research.unit.id] = end
        completions[upgrade] = end
        occupations.append(
            Occupation(
                producer=model.usual_form(research.unit.type_id),
                start_seconds=start,
                end_seconds=end,
                needs_tech_lab=False,
            )
        )
    return completions, occupations
