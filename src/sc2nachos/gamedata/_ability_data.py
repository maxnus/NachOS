"""What the game says about an ability."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Self, final

from s2clientprotocol import data_pb2

from sc2nachos._enum import ReadableIntEnum
from sc2nachos.gamedata._cost import Cost
from sc2nachos.gamedata._techtree._overrides import COST_OVERRIDES, KEEPS_ORDERS_ABILITIES
from sc2nachos.ids import AbilityId, UnitTypeId, UpgradeId

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sc2nachos.gamedata._techtree import TechTree
    from sc2nachos.gamedata._unit_type_data import UnitTypeData
    from sc2nachos.gamedata._upgrade_data import UpgradeData


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
    toggle and the rest of `KEEPS_ORDERS_ABILITIES`, and everything besides making something that is offered only to
    a type the game offers no move — a structure's own rally, load, cancel and energy casts, and the way back out of
    a sieged form. Every one of those a producer is offered leaves what it is making at the progress it stood at
    (in game). `GENERAL_CANCEL` is not one of them: a ghost and an infestor are offered it too, and it takes them
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
    return behaviors


# What an ability that makes nothing charges.
_FREE = Cost(0, 0)


def ability_costs(
    units: Mapping[UnitTypeId, UnitTypeData],
    upgrades: Mapping[UpgradeId, UpgradeData],
    tech_tree: TechTree,
) -> Mapping[AbilityId, Cost]:
    """What the game charges as each ability is ordered, supply included.

    A type's row holds everything spent to reach it, so a morph is charged the difference from what it is made out
    of; `COST_OVERRIDES` corrects the few that gets wrong.
    """
    costs: dict[AbilityId, Cost] = {}
    for ability, product in tech_tree.ability_products.items():
        if (derived := _derived_cost(product, units, upgrades)) is not None:
            costs[ability] = derived
    costs.update(COST_OVERRIDES)
    # A general id takes the price the exact ones it stands for share, as every tech lab's 50/25. A research's stands
    # for its three levels, which differ, and takes the first's: the one it runs until that is done.
    prices_of: defaultdict[AbilityId, set[Cost]] = defaultdict(set)
    first_level: dict[AbilityId, AbilityId] = {}
    for exact, general in tech_tree.ability_remaps.items():
        if general in costs:
            continue
        prices_of[general].add(costs.get(exact, _FREE))
        product = tech_tree.ability_products.get(exact)
        if isinstance(product, UpgradeId) and tech_tree.upgrade_levels.get(product) == 1:
            first_level[general] = exact
    for general, prices in prices_of.items():
        if len(prices) == 1:
            costs[general] = next(iter(prices))
        elif general in first_level:
            costs[general] = costs.get(first_level[general], _FREE)
    return costs


def cancel_abilities(
    tech_tree: TechTree, behaviors: Mapping[AbilityId, OrderBehavior]
) -> Mapping[AbilityId, AbilityId]:
    """The cancel that takes each ability back off a structure carrying it out, for those that have one.

    A train or a research is taken back by `GENERAL_CANCEL_LAST`, which every queue cancel stands for, where each type
    it is offered to keeps a queue: a warp gate keeps none (in game). A morph, an add-on and arming a nuke take the
    cancel they were seen offered (tool `sweep_tech_tree`), and a general id the cancel the exact ones it stands for
    share.
    """
    keeps_a_queue = {
        unit_type
        for unit_type, abilities in tech_tree.ability_requirements.items()
        if any(_is_queue_cancel(ability, tech_tree) for ability in abilities)
    }
    cancels = dict(tech_tree.ability_cancels)
    for ability, behavior in behaviors.items():
        performers = tech_tree.ability_performers.get(ability, frozenset())
        if behavior is OrderBehavior.QUEUES and performers and performers <= keeps_a_queue:
            cancels[ability] = AbilityId.GENERAL_CANCEL_LAST
    cancels_of: defaultdict[AbilityId, set[AbilityId | None]] = defaultdict(set)
    for exact, general in tech_tree.ability_remaps.items():
        if general not in cancels:
            cancels_of[general].add(cancels.get(exact))
    for general, shared in cancels_of.items():
        if len(shared) == 1 and (cancel := next(iter(shared))) is not None:
            cancels[general] = cancel
    return cancels


def _is_queue_cancel(ability: AbilityId, tech_tree: TechTree) -> bool:
    """Whether `ability` takes back the last thing a queue holds, which the game says by remapping it onto
    `GENERAL_CANCEL_LAST`."""
    return AbilityId.GENERAL_CANCEL_LAST in (ability, tech_tree.ability_remaps.get(ability))


def _derived_cost(
    product: UnitTypeId | UpgradeId, units: Mapping[UnitTypeId, UnitTypeData], upgrades: Mapping[UpgradeId, UpgradeData]
) -> Cost | None:
    """What the game's rows say the ability that makes `product` costs: an upgrade's cost, or a unit type's less that
    of what it is made out of. `None` where the tables have no row for it or for what it is made out of."""
    if isinstance(product, UpgradeId):
        upgrade = upgrades.get(product)
        return None if upgrade is None else upgrade.cost
    if (made := units.get(product)) is None:
        return None
    source = made.morphed_from or made.base_type
    if source is None:
        return made.cost
    used = units.get(source)
    return None if used is None else made.cost - used.cost


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
    cost: Cost
    """What the game takes as it is ordered, which for a morph is the difference from what it is made out of: 150 for
    an orbital command, not the 550 its type's row holds as everything spent to reach it. Its supply is taken as what
    it makes starts, less what the unit it uses up gives back: 1 for a marine, -1 for a spawning pool, 0 for a
    baneling. A general id holds the cost the exact ones it stands for share, and a research's the first level's, which
    is what it runs until that level is done: budget a later level by its exact id. An ability that makes nothing
    costs nothing."""
    cancelled_by: AbilityId | None
    """The cancel that takes this back off a structure carrying it out. For a train or a research it is
    `GENERAL_CANCEL_LAST`, which every structure's own queue cancel stands for and which takes the last item off a
    barracks, an engineering bay and a command center alike (in game). A morph and an add-on have their own, since
    `GENERAL_CANCEL_LAST` is answered `ERROR` by those: `COMMAND_CENTER_CANCEL_ORBITAL_COMMAND` for the orbital morph,
    `BARRACKS_CANCEL_ADD_ON` for either add-on (tool `sweep_tech_tree`). A general id holds the cancel the exact
    ones it stands for share, and none where they differ, as a general add-on's do: send the exact id's. `None` for
    anything else, a warp-in and a build among them: a warp gate keeps no queue, and a structure going up is
    cancelled on itself, with `GENERAL_CANCEL_BUILDING`."""
    order_behavior: OrderBehavior
    """What ordering it does to what the unit is already doing."""

    @classmethod
    def _from_proto(
        cls,
        data: data_pb2.AbilityData,
        tech_tree: TechTree,
        behaviors: Mapping[AbilityId, OrderBehavior],
        costs: Mapping[AbilityId, Cost],
        cancels: Mapping[AbilityId, AbilityId],
    ) -> Self:
        """Read one ability out of the game's tables, with what `tech_tree`, `behaviors`, `costs` and `cancels`
        found about it in game."""
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
            cost=costs.get(ability, _FREE),
            cancelled_by=cancels.get(ability),
            order_behavior=behaviors.get(ability, OrderBehavior.REPLACES),
        )
