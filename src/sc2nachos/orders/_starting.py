"""When a unit can start what it was ordered: its reach to a site, its production slots, and the time either takes."""

from __future__ import annotations

import heapq
import math
from typing import TYPE_CHECKING, Any

from sc2nachos.constants import STEPS_PER_SECOND
from sc2nachos.ids import UnitTypeId, UpgradeId
from sc2nachos.units import Unit

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sc2nachos.gamedata import AbilityData, GameData
    from sc2nachos.geometry import Point
    from sc2nachos.units import OwnUnit, Target


# Where a hatchery's larvae gather, from its center: they stood within 2.3 of it (tool `sweep_orders`, `larvae`).
_LARVA_SPOT = (0.0, -2.85)


def larva_spot(hatchery: Unit[Any]) -> Point:
    """Where the larvae of `hatchery`, a lair or a hive gather: south of its center."""
    return hatchery.position + _LARVA_SPOT


def site_of(target: Target) -> Point:
    """Where an order aimed at `target` is carried out: the point, or where the unit stands."""
    return target.position if isinstance(target, Unit) else target


def within_reach(unit: Unit[Any], target: Target, reach: float) -> bool:
    """Whether `unit` stands within `reach` of `target`: of the point, or of a unit's edge, as a geyser's."""
    distance = unit.position.distance_to(site_of(target))
    if isinstance(target, Unit):
        distance -= target.radius
    return distance <= reach


def travel_steps(unit: Unit[Any], target: Target) -> float:
    """The steps `unit` takes to go straight to `target` at its speed. Infinite for one that does not move."""
    per_step = unit.speed / STEPS_PER_SECOND
    if per_step <= 0.0:
        return math.inf
    return unit.position.distance_to(site_of(target)) / per_step


def slots(unit: OwnUnit[Any], game_data: GameData) -> int:
    """How many trains or researches `unit` runs at once: two with a finished reactor, none while flying, else one."""
    if unit.is_flying:
        return 0
    add_on = unit.add_on
    if add_on is None or not add_on.is_complete:
        return 1
    row = game_data.units.get(add_on.type_id)
    return 2 if row is not None and UnitTypeId.REACTOR in row.tech_aliases else 1


def product_steps(row: AbilityData | None, unit_type: UnitTypeId, game_data: GameData) -> float:
    """The steps a unit of `unit_type` takes to make what `row`'s ability makes, or 0 where the tables do not say."""
    product = None if row is None else row.products.get(unit_type)
    if isinstance(product, UpgradeId):
        upgrade = game_data.upgrades.get(product)
        return 0.0 if upgrade is None else upgrade.research_steps
    made = None if product is None else game_data.units.get(product)
    return 0.0 if made is None else made.build_steps


def steps_until_free(durations: Iterable[float], slots: int, *, every_slot: bool) -> float:
    """The steps until a structure that runs `slots` items at once and has items lasting `durations` to run, in turn,
    has a slot free, or with `every_slot` is idle. Infinite for one with no slot."""
    if slots <= 0:
        return math.inf
    ends = [0.0] * slots
    for duration in durations:
        heapq.heapreplace(ends, ends[0] + duration)
    return max(ends) if every_slot else ends[0]
