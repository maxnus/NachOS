"""What the game says about an ability."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Self, final

from s2clientprotocol import data_pb2

from sc2nachos._enum import ReadableIntEnum
from sc2nachos.gamedata._resources import Resources
from sc2nachos.gamedata._techtree._overrides import CHARGED_COSTS, CHARGED_SUPPLY, KEEPS_ORDERS_ABILITIES
from sc2nachos.ids import AbilityId, UnitTypeId, UpgradeId

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sc2nachos.gamedata._techtree import TechTree
    from sc2nachos.gamedata._unittype import UnitTypeData
    from sc2nachos.gamedata._upgrade import UpgradeData


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
    KEEPS_ORDERS = "keeps orders"
    """It is carried out and the unit goes on with its orders, so it competes with nothing: stim, both halves of a
    toggle and the rest of `KEEPS_ORDERS_ABILITIES`, and everything besides making something and cancelling that is
    offered only to a type the game offers no move — a structure's own rally, load and energy casts, and the way
    back out of a sieged form. Every one of those a producer is offered leaves what it is making at the progress it
    stood at (in game)."""
    CANCELS = "cancels"
    """It takes the last thing a structure is making off it and gives back what the game refunds, leaving the rest
    of its queue where it was: the structure's own cancel, which is the one it is offered and which turns on what it
    is making. It competes with nothing, and frees neither a slot nor a mineral before the game has stepped
    (in game). `GENERAL_CANCEL` is not one of these: a ghost and an infestor are offered it too, and it takes them
    off what they are channeling."""


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
    behaviors.update(dict.fromkeys(KEEPS_ORDERS_ABILITIES, OrderBehavior.KEEPS_ORDERS))
    # A general id stands for exact ones, which are of one class: a research level queues, an add-on needs an idle
    # structure, a stim acts at once. A general id no exact one classifies is left for the pass below, which reads
    # every unit type it is offered to rather than one of them.
    for exact, general in tech_tree.ability_remaps.items():
        behavior = behaviors.get(exact)
        if behavior is not None:
            behaviors[general] = behavior
    movers = _unit_types_offered_a_move(tech_tree)
    for ability, performers in tech_tree.ability_performers.items():
        if ability in behaviors or ability in tech_tree.ability_products or not performers:
            continue
        if not performers & movers:
            # An ability that makes nothing, offered only to things the game offers no move: a structure, an egg, a
            # cocoon. They have nothing an order could take them off but what they are making, and a rally and a
            # cancel were both seen to leave that alone (docs/game-behavior.md).
            behaviors[ability] = OrderBehavior.KEEPS_ORDERS
    for ability, performers in tech_tree.ability_performers.items():
        # A cancel of a structure's own, which the game remaps onto one of the two general ones. `GENERAL_CANCEL`
        # itself is left out by the same test as ever: a ghost and an infestor are offered it, and both can move.
        if not performers & movers and _cancels(ability, tech_tree):
            behaviors[ability] = OrderBehavior.CANCELS
    return behaviors


def _cancels(ability: AbilityId, tech_tree: TechTree) -> bool:
    """Whether the ability takes back what a structure is making, which the game's own tables say by remapping every
    one of them onto `GENERAL_CANCEL` or `GENERAL_CANCEL_LAST`."""
    return ability in _CANCELS or tech_tree.ability_remaps.get(ability) in _CANCELS


def ability_costs(
    units: Mapping[UnitTypeId, UnitTypeData],
    upgrades: Mapping[UpgradeId, UpgradeData],
    tech_tree: TechTree,
) -> Mapping[AbilityId, tuple[Resources, float]]:
    """What the game charges as each ability is ordered, and the supply it takes, for those that take anything.

    A morph is charged the difference from what it is made out of, the game's row for a type holding everything
    spent to reach it, and `CHARGED_COSTS` and `CHARGED_SUPPLY` hold what that leaves wrong.
    """
    charges: dict[AbilityId, tuple[Resources, float]] = {}
    for ability, product in tech_tree.ability_products.items():
        if isinstance(product, UpgradeId):
            if (upgrade := upgrades.get(product)) is not None:
                charges[ability] = (upgrade.cost, 0.0)
            continue
        if (made := units.get(product)) is None:
            continue
        source = made.morphed_from or made.base_type
        used = units.get(source) if source is not None else None
        if used is None:
            charges[ability] = (made.cost, made.supply_cost)
        else:
            charges[ability] = (made.cost - used.cost, made.supply_cost - used.supply_cost)
    for ability in CHARGED_COSTS.keys() | CHARGED_SUPPLY.keys():
        cost, supply = charges.get(ability, (Resources(0, 0), 0.0))
        charges[ability] = (CHARGED_COSTS.get(ability, cost), CHARGED_SUPPLY.get(ability, supply))
    # A general id stands for exact ones of several prices -- the three levels of a research -- and which it will run
    # is not known until it is ordered, so it is charged the least of them, which refuses no order the game takes.
    for exact, general in tech_tree.ability_remaps.items():
        charge = charges.get(exact)
        if charge is None or general in tech_tree.ability_products or general in _CORRECTED:
            continue
        standing = charges.get(general)
        if standing is None or charge[0].total < standing[0].total:
            charges[general] = charge
    return charges


_CANCELS = frozenset({AbilityId.GENERAL_CANCEL, AbilityId.GENERAL_CANCEL_LAST})
# What a hand-written price or supply is held for, which the pass over general ids must not undo.
_CORRECTED = CHARGED_COSTS.keys() | CHARGED_SUPPLY.keys()


def _unit_types_offered_a_move(tech_tree: TechTree) -> frozenset[UnitTypeId]:
    """The unit types the game offers a move, which are the ones an order can take off what they are doing.

    A structure is offered none, nor is an egg, a cocoon, or a unit in a form it cannot move in: a sieged tank, a
    burrowed lurker, a lowered depot, a warp gate. A structure in the air is offered one under its own flying type.
    """
    move = AbilityId.GENERAL_MOVE
    remaps = tech_tree.ability_remaps
    return frozenset(
        unit_type
        for unit_type, abilities in tech_tree.ability_requirements.items()
        if any(ability is move or remaps.get(ability) is move for ability in abilities)
    )


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
    cost: Resources
    """What the game takes as it is ordered, which for a morph is the difference from what it is made out of: 150 for
    an orbital command, not the 550 its type's row holds as everything spent to reach it. A general id that stands for
    several prices, as a research level does, holds the least of them, and an ability that makes nothing costs
    nothing."""
    supply_cost: float
    """What it takes of the supply cap as what it makes starts, and what it gives back where it uses up the unit that
    orders it: 1 for a marine, -1 for a spawning pool, none for a baneling."""
    cancelled_by: AbilityId | None
    """The cancel the game offers a structure carrying this out, which is its own: `COMMAND_CENTER_CANCEL_ORBITAL_
    COMMAND` for the orbital morph, `BARRACKS_CANCEL_ADD_ON` for an add-on, and the structure's queue cancel for a
    train or a research. `None` for everything the game offers no cancel for (tool `sweep_tech_tree`)."""
    behavior: OrderBehavior
    """What ordering it does to what the unit is already doing."""

    @classmethod
    def from_proto(
        cls,
        data: data_pb2.AbilityData,
        tech_tree: TechTree,
        behaviors: Mapping[AbilityId, OrderBehavior],
        charges: Mapping[AbilityId, tuple[Resources, float]],
    ) -> Self:
        """Read one ability out of the game's tables, with what `tech_tree`, `behaviors` and `charges` found about it
        in game."""
        ability = AbilityId(data.ability_id)
        cost, supply_cost = charges.get(ability, (Resources(0, 0), 0.0))
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
            cost=cost,
            supply_cost=supply_cost,
            cancelled_by=tech_tree.ability_cancels.get(ability),
            behavior=behaviors.get(ability, OrderBehavior.REPLACES),
        )
