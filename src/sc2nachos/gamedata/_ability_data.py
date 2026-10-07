"""What the game says about an ability."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING, Self, final

from s2clientprotocol import data_pb2

from sc2nachos._enum import ReadableIntEnum
from sc2nachos.gamedata._cost import Cost
from sc2nachos.gamedata._sent_as import Aim, SentAs
from sc2nachos.gamedata._techtree._overrides import (
    ABILITIES_SENT_AS_ANOTHER,
    COST_OVERRIDES,
    KEEPS_ORDERS_ABILITIES,
    KEEPS_ORDERS_BY_TYPE,
)
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
    rest of `KEEPS_ORDERS_ABILITIES`, plus every ability that makes nothing and is offered only to types that hold no
    order of their own — a structure's own rally, load, cancel and energy casts. Ordered on a producer, each of those
    leaves what it is making at its current progress (in game). `GENERAL_CANCEL` is not one of them: a ghost and an
    infestor are offered it too, and it takes them off what they are channeling."""


# The behaviors by type of an ability whose performers all share one.
_NO_BEHAVIORS: Mapping[UnitTypeId, OrderBehavior] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class OrderBehaviors:
    """What ordering one ability does to a unit's current orders: its own behavior, and each unit type's where that
    differs."""

    own: OrderBehavior = OrderBehavior.REPLACES
    by_type: Mapping[UnitTypeId, OrderBehavior] = field(default_factory=lambda: _NO_BEHAVIORS)


def order_behaviors(
    tech_tree: TechTree, structures: frozenset[UnitTypeId], non_structures: frozenset[UnitTypeId]
) -> Mapping[AbilityId, OrderBehaviors]:
    """The order behaviors of each ability that does not simply replace every unit's orders, judged for each unit type
    that carries it out: an action several types perform does to each what that type's own does.

    In order of precedence, an ability keeps the orders of the types `KEEPS_ORDERS_BY_TYPE` names for it, and of every
    type where it is in `KEEPS_ORDERS_ABILITIES`. An ability that makes something queues on a structure making a unit
    or a research, and needs the structure idle where it makes a structure: a morph, an add-on, a lift. An ability
    that makes nothing keeps the orders of a type that holds none of its own: a structure, an egg, a cocoon. Anything
    else replaces a unit's orders. An ability's own behavior is the one the types carrying it out share, or `REPLACES`
    where they differ; a type it is not offered to takes that.

    `structures` and `non_structures` are the types the tables say are and are not structures. They tell a barracks
    training a marine from a larva morphing into one, and a sieged tank from a bunker.
    """
    holders = _unit_types_holding_orders(tech_tree, non_structures)
    behaviors: dict[AbilityId, OrderBehaviors] = {}
    for ability in (
        tech_tree.ability_performers.keys()
        | tech_tree.ability_products.keys()
        | KEEPS_ORDERS_ABILITIES
        | KEEPS_ORDERS_BY_TYPE.keys()
    ):
        products = tech_tree.ability_products.get(ability, {})
        makers = _performers_of(ability, tech_tree) | products.keys()
        kinds = {
            unit_type: _behavior(ability, unit_type, products.get(unit_type), structures, holders)
            for unit_type in makers | KEEPS_ORDERS_BY_TYPE.get(ability, frozenset())
        }
        shared = {kinds[unit_type] for unit_type in makers}
        own = shared.pop() if len(shared) == 1 else OrderBehavior.REPLACES
        by_type = {unit_type: kind for unit_type, kind in kinds.items() if kind is not own}
        if own is not OrderBehavior.REPLACES or by_type:
            behaviors[ability] = OrderBehaviors(own, MappingProxyType(by_type))
    return behaviors


def _behavior(
    ability: AbilityId,
    unit_type: UnitTypeId,
    product: UnitTypeId | UpgradeId | None,
    structures: frozenset[UnitTypeId],
    holders: frozenset[UnitTypeId],
) -> OrderBehavior:
    """What `ability` does to the orders of a unit of `unit_type`, which makes `product` by it."""
    if unit_type in KEEPS_ORDERS_BY_TYPE.get(ability, ()) or ability in KEEPS_ORDERS_ABILITIES:
        return OrderBehavior.KEEPS_ORDERS
    if product is not None:
        if unit_type not in structures:
            # A worker's build, a larva's train, a unit's own morph.
            return OrderBehavior.REPLACES
        makes_structure = isinstance(product, UnitTypeId) and product in structures
        return OrderBehavior.NEEDS_IDLE if makes_structure else OrderBehavior.QUEUES
    return OrderBehavior.REPLACES if unit_type in holders else OrderBehavior.KEEPS_ORDERS


# The behaviors of an ability that replaces every unit's orders.
_REPLACES = OrderBehaviors()


def _performers_of(ability: AbilityId, tech_tree: TechTree) -> frozenset[UnitTypeId]:
    """The unit types offered `ability`. The off half of a toggle is offered only once its on half has taken (in
    game), so the tables offer it to nobody, and it takes its on half's."""
    if (performers := tech_tree.ability_performers.get(ability)) or not ability.name.endswith("_OFF"):
        return performers or frozenset()
    on_half = AbilityId.__members__.get(f"{ability.name.removesuffix('_OFF')}_ON")
    return tech_tree.ability_performers.get(on_half, frozenset()) if on_half is not None else frozenset()


# The products of an ability that makes nothing.
_MAKES_NOTHING: Mapping[UnitTypeId, UnitTypeId | UpgradeId] = MappingProxyType({})

# The cost of an ability that makes nothing.
_FREE = Cost(0, 0)


def ability_costs(
    units: Mapping[UnitTypeId, UnitTypeData],
    upgrades: Mapping[UpgradeId, UpgradeData],
    tech_tree: TechTree,
) -> Mapping[AbilityId, Cost]:
    """The cost of ordering each ability, supply included.

    A unit type's row holds everything spent to reach it, so a morph costs the difference from its source type. An
    action several types perform costs what each makes costs, which is one price for every action there is: a lift
    nothing, a tech lab 50/25. `COST_OVERRIDES` corrects the few the tables get wrong.
    """
    costs: dict[AbilityId, Cost] = {}
    for ability, products in tech_tree.ability_products.items():
        prices = {_derived_cost(product, units, upgrades) for product in products.values()}
        if len(prices) == 1 and (price := prices.pop()) is not None:
            costs[ability] = price
    costs.update(COST_OVERRIDES)
    return costs


def cancel_abilities(
    tech_tree: TechTree, behaviors: Mapping[AbilityId, OrderBehaviors]
) -> Mapping[AbilityId, AbilityId]:
    """The ability that cancels each ability on a structure carrying it out, for those that have one.

    A train or a research is cancelled by `GENERAL_CANCEL_LAST`, provided every type offered the ability keeps a queue,
    which is to say is offered that cancel: a warp gate keeps none (in game). A morph, an add-on and arming a nuke take
    the cancel they were seen offered (tool `sweep_tech_tree`), `GENERAL_CANCEL` for every one of them.
    """
    keeps_a_queue = tech_tree.ability_performers.get(AbilityId.GENERAL_CANCEL_LAST, frozenset())
    cancels = dict(tech_tree.ability_cancels)
    for ability, behavior in behaviors.items():
        performers = tech_tree.ability_performers.get(ability, frozenset())
        if behavior.own is OrderBehavior.QUEUES and performers and performers <= keeps_a_queue:
            cancels[ability] = AbilityId.GENERAL_CANCEL_LAST
    return cancels


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


def _unit_types_holding_orders(tech_tree: TechTree, non_structures: frozenset[UnitTypeId]) -> frozenset[UnitTypeId]:
    """The unit types that hold an order of their own, which an order can take them off: those the game offers a
    move, and a unit offered an attack, a stop or a hold even where it cannot move, such as a sieged tank or a burrowed
    lurker. A structure, an egg and a cocoon hold none. A flying structure is offered a move under its flying type."""
    held = (AbilityId.GENERAL_ATTACK, AbilityId.GENERAL_STOP, AbilityId.GENERAL_HOLD_POSITION)
    holders: set[UnitTypeId] = set()
    for unit_type, abilities in tech_tree.ability_requirements.items():
        if AbilityId.GENERAL_MOVE in abilities or (
            unit_type in non_structures and not abilities.keys().isdisjoint(held)
        ):
            holders.add(unit_type)
    return frozenset(holders)


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
    """Whether it can be set to autocast, for some type that carries it out. An unburrow can for a roach and not for a
    drone (in game), and the switch takes effect only under each type's own game id, not `GENERAL_UNBURROW`'s."""
    performers: frozenset[UnitTypeId]
    """The unit types offered it. An action several types perform is offered to each under its one id, though the
    game offers each type its own. Empty for `GATEWAY_MORPH_WARP_GATE`, which a gateway carries out by itself once the
    research is done."""
    products: Mapping[UnitTypeId, UnitTypeId | UpgradeId] = field(hash=False)
    """The unit type it makes, or the upgrade it researches, by the unit type that carries it out: `GENERAL_LIFT`
    makes a flying barracks of a barracks and a flying starport of a starport. Empty for an ability that makes
    nothing."""
    cost: Cost
    """What ordering it charges. For a morph that is the difference from the source type: 150 for an orbital command,
    not the 550 its row holds as everything spent to reach it. Supply is charged as the product starts, less what the
    unit used up gives back: 1 for a marine, -1 for a spawning pool, 0 for a baneling. An action several types perform
    costs the same for each. An ability that makes nothing costs nothing."""
    cancelled_by: AbilityId | None
    """The ability that cancels this one on a structure carrying it out. For a train or a research it is
    `GENERAL_CANCEL_LAST`, which takes the last item off a barracks, an engineering bay and a command center alike (in
    game). For a morph, an add-on and arming a nuke it is `GENERAL_CANCEL`, since those answer `GENERAL_CANCEL_LAST`
    with `ERROR` (tool `sweep_tech_tree`). `None` for everything else, a warp-in and a build among them: a warp gate
    keeps no queue, and a structure under construction is cancelled on itself with `GENERAL_CANCEL`."""
    order_behavior: OrderBehavior
    """What ordering it does to the unit's current orders: the behavior the types carrying it out share, or `REPLACES`
    where they differ; `order_behavior_for` gives each unit type's."""
    sent_as: Mapping[UnitTypeId, SentAs] = field(hash=False)
    """The game's ability it goes out as for each unit type named, and what that is aimed at; any other type is sent
    the ability itself. `GENERAL_UNLOAD` goes out to a medivac as its unload at a point aimed at the medivac, and a
    custom id names every type it can be given to: `GENERAL_SIEGE` a tank's siege mode and a liberator's defender
    mode, aimed at the order's point. Empty for most abilities."""
    _behaviors_by_performer: Mapping[UnitTypeId, OrderBehavior] = field(repr=False, compare=False)

    def order_behavior_for(self, unit_type: UnitTypeId) -> OrderBehavior:
        """What ordering it does to the current orders of a unit of `unit_type`: what that type's own does, as a
        ghost's hold fire keeps its orders and a burrowed lurker's takes it off its attack, and keeping the orders of
        the types `KEEPS_ORDERS_BY_TYPE` names for it, such as a barracks given a smart (in game)."""
        return self._behaviors_by_performer.get(unit_type, self.order_behavior)

    @classmethod
    def _from_proto(
        cls,
        data: data_pb2.AbilityData,
        tech_tree: TechTree,
        behaviors: Mapping[AbilityId, OrderBehaviors],
        costs: Mapping[AbilityId, Cost],
        cancels: Mapping[AbilityId, AbilityId],
    ) -> Self:
        """Read one ability from the game's table, with what `tech_tree`, `behaviors`, `costs` and `cancels` say about
        it."""
        ability = AbilityId(data.ability_id)
        behavior = behaviors.get(ability, _REPLACES)
        return cls(
            id=ability,
            target_type=TargetType(data.target),
            cast_range=data.cast_range,
            footprint_radius=data.footprint_radius if data.HasField("footprint_radius") else None,
            needs_placement=data.is_building,
            allows_autocast=data.allow_autocast,
            performers=_performers(ability, tech_tree),
            products=tech_tree.ability_products.get(ability, _MAKES_NOTHING),
            cost=costs.get(ability, _FREE),
            cancelled_by=cancels.get(ability),
            order_behavior=behavior.own,
            sent_as=ABILITIES_SENT_AS_ANOTHER.get(ability, _SENT_UNCHANGED),
            _behaviors_by_performer=behavior.by_type,
        )

    @classmethod
    def _custom(
        cls,
        ability: AbilityId,
        game_rows: Mapping[int, data_pb2.AbilityData],
        tech_tree: TechTree,
        behaviors: Mapping[AbilityId, OrderBehaviors],
        costs: Mapping[AbilityId, Cost],
        cancels: Mapping[AbilityId, AbilityId],
    ) -> Self:
        """The custom ability `ability`, sent as `ABILITIES_SENT_AS_ANOTHER` names for each type, with what
        `tech_tree`, `behaviors`, `costs` and `cancels` say about it. It takes a target where the abilities it goes out
        as at the order's target take one, and reaches as far as the furthest of them."""
        sent_as = ABILITIES_SENT_AS_ANOTHER[ability]
        aimed = [game_rows[sending.ability] for sending in sent_as.values() if sending.aim is Aim.TARGET]
        behavior = behaviors.get(ability, _REPLACES)
        return cls(
            id=ability,
            target_type=_target_type({TargetType(row.target) for row in aimed}, untargeted=len(aimed) < len(sent_as)),
            cast_range=max((row.cast_range for row in aimed), default=0.0),
            footprint_radius=None,
            needs_placement=False,
            allows_autocast=False,
            performers=_performers(ability, tech_tree),
            products=tech_tree.ability_products.get(ability, _MAKES_NOTHING),
            cost=costs.get(ability, _FREE),
            cancelled_by=cancels.get(ability),
            order_behavior=behavior.own,
            sent_as=sent_as,
            _behaviors_by_performer=behavior.by_type,
        )


# What an ability goes out as where no type is sent another: itself, for every type.
_SENT_UNCHANGED: Mapping[UnitTypeId, SentAs] = MappingProxyType({})


def _performers(ability: AbilityId, tech_tree: TechTree) -> frozenset[UnitTypeId]:
    """The unit types offered `ability`, and those offered the ability it goes out to them as: a medivac is offered
    the unload at a point that `GENERAL_UNLOAD` goes out to it as."""
    performers = tech_tree.ability_performers
    offered = performers.get(ability, frozenset())
    sent_as = ABILITIES_SENT_AS_ANOTHER.get(ability, _SENT_UNCHANGED)
    return offered | {
        unit_type
        for unit_type, sending in sent_as.items()
        if (sent := AbilityId.get(sending.ability)) is not None and unit_type in performers.get(sent, frozenset())
    }


def _target_type(aimed: set[TargetType], *, untargeted: bool) -> TargetType:
    """What an ability takes that goes out as abilities taking `aimed` at its target, and to other types as one aimed
    at nothing, if `untargeted`."""
    if not aimed:
        return TargetType.NOTHING
    if len(aimed) > 1:
        raise ValueError(f"an ability goes out as ones taking {sorted(kind.name for kind in aimed)}")
    (kind,) = aimed
    if not untargeted:
        return kind
    if kind is not TargetType.POINT:
        raise ValueError(f"an ability goes out as ones taking {kind.name} and ones taking nothing")
    return TargetType.POINT_OR_NOTHING
