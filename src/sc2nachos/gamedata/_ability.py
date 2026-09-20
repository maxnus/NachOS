"""What the game says about an ability."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Self, final

from s2clientprotocol import data_pb2

from sc2nachos._enum import ReadableIntEnum
from sc2nachos.gamedata._techtree._overrides import ACTS_AT_ONCE
from sc2nachos.ids import AbilityId, UnitTypeId, UpgradeId

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sc2nachos.gamedata._techtree import TechTree


class TargetType(ReadableIntEnum):
    """What an ability must be aimed at."""

    # The game calls this one `None`, which no expression can spell, so it is read off the descriptor.
    NOTHING = data_pb2.AbilityData.Target.Value("None")
    POINT = data_pb2.AbilityData.Target.Point
    UNIT = data_pb2.AbilityData.Target.Unit
    POINT_OR_UNIT = data_pb2.AbilityData.Target.PointOrUnit
    POINT_OR_NOTHING = data_pb2.AbilityData.Target.PointOrNone


class OrderBehavior(Enum):
    """What ordering an ability does to what a unit is already doing (tool `sweep_orders`)."""

    REPLACES = "replaces"
    """It drops the unit's orders and is carried out instead, as a move, an attack or a worker's build does."""
    QUEUES = "queues"
    """It goes behind what a structure is making, queued or not: a train or a research."""
    NEEDS_IDLE = "needs idle"
    """A structure takes it only while it is making nothing, and is answered `NOT_SUPPORTED` otherwise: an add-on, a
    morph, a lift."""
    AT_ONCE = "at once"
    """It is carried out and the unit goes on with its orders, so it competes with nothing: stim and the twelve
    others of `ACTS_AT_ONCE`."""


def order_behaviors(tech_tree: TechTree, structures: frozenset[UnitTypeId]) -> Mapping[AbilityId, OrderBehavior]:
    """What each ability does to what a unit is already doing, for those that do not simply replace its orders.

    `structures` names the unit types that stand on the ground, which is what tells a barracks training a marine from
    a larva morphing into one.
    """
    behaviors: dict[AbilityId, OrderBehavior] = {}
    for ability, product in tech_tree.ability_products.items():
        performers = tech_tree.ability_performers.get(ability, frozenset())
        if performers - structures:
            # Something that is not a structure performs it, so it replaces what that unit was doing: a worker's
            # build, a larva's train, a unit's own morph.
            continue
        makes_structure = isinstance(product, UnitTypeId) and product in structures
        if not performers and not makes_structure:
            # An id a unit only reports and is never offered, such as `LIBERATOR_SIEGE_EXACT`. One that makes a
            # structure is kept: a gateway is offered no warp gate morph either, and turns itself into one.
            continue
        behaviors[ability] = OrderBehavior.NEEDS_IDLE if makes_structure else OrderBehavior.QUEUES
    behaviors.update(dict.fromkeys(ACTS_AT_ONCE, OrderBehavior.AT_ONCE))
    generals = (general for exact, general in tech_tree.ability_remaps.items() if exact in ACTS_AT_ONCE)
    behaviors.update(dict.fromkeys(generals, OrderBehavior.AT_ONCE))
    return behaviors


@final
@dataclass(frozen=True, slots=True)
class AbilityData:
    """What the game says about one ability, and what it takes to order one."""

    id: AbilityId
    """Which ability this describes."""
    target_type: TargetType
    """What must be supplied to order it."""
    cast_range: float
    """How far it reaches, and zero where it has no range of its own."""
    footprint_radius: float | None
    """How far from the point ordered must be clear, and `None` where nothing is placed: half a structure's
    width, and for an add-on the reach past the far side of the structure it attaches to."""
    needs_placement: bool
    """Whether ordering it puts a structure on the ground."""
    allows_autocast: bool
    """Whether it can be left to fire on its own."""
    remaps_to: AbilityId | None
    """The general ability this one stands for: a unit reports the exact id, and either can be ordered."""
    performers: frozenset[UnitTypeId]
    """The unit types offered it. A general ability such as `GENERAL_BURROW` is offered to no unit itself, so its
    performers are the types offered an ability it stands for, such as `ZERGLING_BURROW`. None for an id a unit only
    reports, such as `LIBERATOR_SIEGE_EXACT`."""
    product: UnitTypeId | UpgradeId | None
    """The unit type it makes, or the upgrade it researches."""
    behavior: OrderBehavior
    """What ordering it does to what the unit is already doing."""

    @classmethod
    def from_proto(
        cls, data: data_pb2.AbilityData, tech_tree: TechTree, behaviors: Mapping[AbilityId, OrderBehavior]
    ) -> Self:
        """Read one ability out of the game's tables, with what `tech_tree` and `behaviors` found about it in
        game."""
        ability = AbilityId(data.ability_id)
        return cls(
            id=ability,
            target_type=TargetType(data.target),
            cast_range=data.cast_range,
            footprint_radius=data.footprint_radius if data.HasField("footprint_radius") else None,
            needs_placement=data.is_building,
            allows_autocast=data.allow_autocast,
            remaps_to=AbilityId.get(data.remaps_to_ability_id),
            performers=tech_tree.ability_performers.get(ability, frozenset()),
            product=tech_tree.ability_products.get(ability),
            behavior=behaviors.get(ability, OrderBehavior.REPLACES),
        )
