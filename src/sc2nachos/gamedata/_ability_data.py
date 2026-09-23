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

    # The game names this value `None`, which is not a valid attribute name, so it is read off the descriptor.
    NOTHING = data_pb2.AbilityData.Target.Value("None")
    POINT = data_pb2.AbilityData.Target.Point
    UNIT = data_pb2.AbilityData.Target.Unit
    POINT_OR_UNIT = data_pb2.AbilityData.Target.PointOrUnit
    POINT_OR_NOTHING = data_pb2.AbilityData.Target.PointOrNone


class OrderBehavior(Enum):
    """What ordering an ability does to the unit's current orders (tool `sweep_orders`)."""

    REPLACES = "replaces"
    """Drops the unit's current orders and runs instead: a move, an attack, a worker's build."""
    QUEUES = "queues"
    """Goes behind whatever the structure is making, queued or not: a train or a research."""
    NEEDS_IDLE = "needs idle"
    """A structure accepts it only while making nothing, and otherwise answers `NOT_SUPPORTED`: an add-on, a morph, a
    lift."""
    KEEPS_ORDERS = "keeps orders"
    """Runs without disturbing the unit's orders, so it competes with nothing: stim, both halves of a toggle and the
    rest of `KEEPS_ORDERS_ABILITIES`, plus every ability that makes nothing and is offered only to types the game
    offers no move — a structure's own rally, load, cancel and energy casts, and the way back out of a sieged form.
    Ordered on a producer, each of those leaves what it is making at its current progress (in game). `GENERAL_CANCEL`
    is not one of them: a ghost and an infestor are offered it too, and it takes them off what they are channeling."""


def order_behaviors(tech_tree: TechTree, structures: frozenset[UnitTypeId]) -> Mapping[AbilityId, OrderBehavior]:
    """The order behavior of each ability that does not simply replace the unit's orders.

    `structures` is the set of structure types; it tells a barracks training a marine from a larva morphing into one.
    """
    behaviors: dict[AbilityId, OrderBehavior] = {}
    for ability, product in tech_tree.ability_products.items():
        performers = tech_tree.ability_performers.get(ability, frozenset())
        if performers - structures:
            # A non-structure performs it, so it replaces that unit's orders: a worker's build, a larva's train, a
            # unit's own morph.
            continue
        makes_structure = isinstance(product, UnitTypeId) and product in structures
        if not performers and not makes_structure:
            # An id a unit only reports and is never offered, such as `LIBERATOR_SIEGE_EXACT`. One that makes a
            # structure is kept: a gateway is offered no warp gate morph either, yet turns itself into one.
            continue
        behaviors[ability] = OrderBehavior.NEEDS_IDLE if makes_structure else OrderBehavior.QUEUES
    behaviors.update(dict.fromkeys(KEEPS_ORDERS_ABILITIES, OrderBehavior.KEEPS_ORDERS))
    # A general id takes the behavior of the exact ids that remap to it, which all share one: a research level
    # queues, an add-on needs an idle structure, a stim acts at once. A general id whose exact ids are unclassified
    # is left to the pass below, which looks at every unit type it is offered to.
    for exact, general in tech_tree.ability_remaps.items():
        behavior = behaviors.get(exact)
        if behavior is not None:
            behaviors[general] = behavior
    movers = _unit_types_offered_a_move(tech_tree)
    for ability, performers in tech_tree.ability_performers.items():
        if ability in behaviors or ability in tech_tree.ability_products or not performers:
            continue
        if not performers & movers:
            # An ability that makes nothing, offered only to types the game offers no move: a structure, an egg, a
            # cocoon. The only order they could be taken off is what they are making, and a rally and a cancel were
            # both seen to leave that alone (docs/game-behavior.md).
            behaviors[ability] = OrderBehavior.KEEPS_ORDERS
    return behaviors


# The cost of an ability that makes nothing.
_FREE = Cost(0, 0)


def ability_costs(
    units: Mapping[UnitTypeId, UnitTypeData],
    upgrades: Mapping[UpgradeId, UpgradeData],
    tech_tree: TechTree,
) -> Mapping[AbilityId, Cost]:
    """The cost of ordering each ability, supply included.

    A unit type's row holds everything spent to reach it, so a morph costs the difference from its source type.
    `COST_OVERRIDES` corrects the few that gets wrong.
    """
    costs: dict[AbilityId, Cost] = {}
    for ability, product in tech_tree.ability_products.items():
        if (derived := _derived_cost(product, units, upgrades)) is not None:
            costs[ability] = derived
    costs.update(COST_OVERRIDES)
    # A general id takes the price its exact ids share, such as every tech lab's 50/25. A general research id stands
    # for three levels with different prices and takes the first level's, since that is what it runs until that
    # level is done.
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
    """The ability that cancels each ability on a structure carrying it out, for those that have one.

    A train or a research is cancelled by `GENERAL_CANCEL_LAST`, which every queue cancel remaps to, provided every
    type offered the ability keeps a queue: a warp gate keeps none (in game). A morph, an add-on and arming a nuke
    take the cancel they were seen offered (tool `sweep_tech_tree`). A general id takes the cancel its exact ids
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
    """Whether `ability` cancels the last item of a queue: it is `GENERAL_CANCEL_LAST` or remaps to it."""
    return AbilityId.GENERAL_CANCEL_LAST in (ability, tech_tree.ability_remaps.get(ability))


def _derived_cost(
    product: UnitTypeId | UpgradeId, units: Mapping[UnitTypeId, UnitTypeData], upgrades: Mapping[UpgradeId, UpgradeData]
) -> Cost | None:
    """The cost of making `product`, derived from the tables: an upgrade's cost, or a unit type's cost less its source
    type's. `None` where either has no row."""
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
    """The unit types the game offers a move: the ones an order can take off their current orders.

    A structure, an egg, a cocoon and a unit in a form it cannot move in (a sieged tank, a burrowed lurker, a lowered
    depot, a warp gate) are offered none. A flying structure is offered one under its flying type.
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
    """One ability: what the game's table says about it, and what ordering it takes."""

    id: AbilityId
    """The ability described."""
    target_type: TargetType
    """What an order of it must be aimed at."""
    cast_range: float
    """Its range, or zero if it has none of its own."""
    footprint_radius: float | None
    """The radius around the ordered point that must be clear, or `None` if nothing is placed: half a structure's
    width, and for an add-on the reach past the far side of the structure it attaches to."""
    needs_placement: bool
    """Whether ordering it places a structure."""
    allows_autocast: bool
    """Whether it can be set to autocast."""
    remaps_to: AbilityId | None
    """The general ability this exact one remaps to. A unit reports the exact id; either can be ordered."""
    performers: frozenset[UnitTypeId]
    """The unit types offered it. A general ability such as `GENERAL_BURROW` is offered to no unit directly, so its
    performers are the types offered an exact ability that remaps to it, such as `ZERGLING_BURROW`. Empty for an id a
    unit only reports, such as `LIBERATOR_SIEGE_EXACT`."""
    product: UnitTypeId | UpgradeId | None
    """The unit type it makes, or the upgrade it researches."""
    cost: Cost
    """What ordering it charges. For a morph that is the difference from the source type: 150 for an orbital command,
    not the 550 its row holds as everything spent to reach it. Supply is charged as the product starts, less what the
    unit used up gives back: 1 for a marine, -1 for a spawning pool, 0 for a baneling. A general id holds the cost its
    exact ids share; a general research id holds the first level's, since that is what it runs until that level is
    done, so budget a later level by its exact id. An ability that makes nothing costs nothing."""
    cancelled_by: AbilityId | None
    """The ability that cancels this one on a structure carrying it out. For a train or a research it is
    `GENERAL_CANCEL_LAST`, which every structure's own queue cancel remaps to and which takes the last item off a
    barracks, an engineering bay and a command center alike (in game). A morph and an add-on have their own, since
    those answer `GENERAL_CANCEL_LAST` with `ERROR`: `COMMAND_CENTER_CANCEL_ORBITAL_COMMAND` for the orbital morph,
    `BARRACKS_CANCEL_ADD_ON` for either add-on (tool `sweep_tech_tree`). A general id holds the cancel its exact ids
    share, and `None` where they differ, as a general add-on's do: send the exact id's cancel. `None` for everything
    else, a warp-in and a build among them: a warp gate keeps no queue, and a structure under construction is
    cancelled on itself with `GENERAL_CANCEL_BUILDING`."""
    order_behavior: OrderBehavior
    """What ordering it does to the unit's current orders."""

    @classmethod
    def _from_proto(
        cls,
        data: data_pb2.AbilityData,
        tech_tree: TechTree,
        behaviors: Mapping[AbilityId, OrderBehavior],
        costs: Mapping[AbilityId, Cost],
        cancels: Mapping[AbilityId, AbilityId],
    ) -> Self:
        """Read one ability from the game's table, with what `tech_tree`, `behaviors`, `costs` and `cancels` say
        about it."""
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
