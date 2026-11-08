"""What this player's units are making in one observation."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Any, final

from sc2nachos.gamedata import Attribute, OrderBehavior
from sc2nachos.gametime import GameTime
from sc2nachos.ids import AbilityId, UnitTypeId, UpgradeId
from sc2nachos.state._in_production import InProduction
from sc2nachos.state._research import Research
from sc2nachos.units._tracking._warp_in_tracker import WARP_IN_TYPES

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Mapping

    from sc2nachos.gamedata import AbilityData, GameData, UnitTypeData
    from sc2nachos.units import OwnUnit


@final
class _Production:
    """What this player's units are making in one observation: each unit in production, and how far along each
    research is.

    A unit is in production while it goes up: a structure, an add-on, a unit warping in. So is what each order a unit
    shows makes, if it costs and makes a unit type: a train, queued ones included, an egg's or a cocoon's morph, a
    structure's morph. A build or an add-on is read from its order only until its construction starts, and then from
    the structure itself. An order not yet sent is nowhere.

    A train's progress, an egg's, and a structure's build progress, are the steps done over the tables' build time,
    sent as a 32-bit float, and it is done once that time rounded up has passed: so rounding the progress times the
    unrounded time gives the steps done exactly, and the rounded-up time less those the steps left (in game).

    Raises `NotReportedError` for a morph or a warp-in under way in the api's first observation, which no game has.
    """

    __slots__ = ("_items", "_researches")

    def __init__(self, units: Iterable[OwnUnit[Any]], game_data: GameData) -> None:
        """Read what `units` are making, by what `game_data` says each ability makes."""
        self._items: list[InProduction] = []
        self._researches: dict[UpgradeId, Research] = {}
        for unit in units:
            self._read_production(unit, game_data)

    def of_types(self, unit_types: Collection[UnitTypeId]) -> tuple[InProduction, ...]:
        """What is being made of `unit_types`, in the order the observation lists the units it is read from."""
        return tuple(item for item in self._items if item.product in unit_types)

    @property
    def researches(self) -> Mapping[UpgradeId, Research]:
        """Each research running or queued, by upgrade, in the order the observation lists the structures and each its
        orders."""
        return MappingProxyType(self._researches)

    def _read_production(self, unit: OwnUnit[Any], game_data: GameData) -> None:
        """Read what `unit` makes: itself while it goes up, and what its orders make."""
        report = unit._latest_report
        if report.build_progress < 1.0:
            self._items.append(_going_up(unit, game_data))
        for order in report.orders:
            row = game_data.abilities.get(AbilityId.get(order.ability_id) or AbilityId.NULL)
            if row is None or not (row.cost.minerals or row.cost.vespene):
                continue
            product = _ability_product(row, unit.type_id)
            if isinstance(product, UpgradeId):
                if product not in self._researches:
                    total_steps = game_data.upgrades[product].cost.time.nearest_step
                    # The game's progress is the steps done over the total, as a 32-bit float (in game).
                    steps_left = round((1 - order.progress) * total_steps)
                    self._researches[product] = Research(
                        product, order.progress, GameTime(steps=total_steps), GameTime(steps=steps_left), unit
                    )
            elif product is not None and not (row.needs_placement and _construction_has_started(unit)):
                progress = _order_progress(row, unit.type_id, order.progress)
                if (item := _made_by_order(product, unit, progress, game_data)) is not None:
                    self._items.append(item)


def _going_up(unit: OwnUnit[Any], game_data: GameData) -> InProduction:
    """`unit` itself while it goes up: a unit warping in, made by nothing, in a time how fast it goes up tells; or a
    structure, made by its builder, or an archon two high templar merged into, made by nothing, each in its row's
    time."""
    if unit.type_id in WARP_IN_TYPES:
        warp_in = unit._tracker.warp_in_tracker.progress_of(unit)
        return InProduction(unit.type_id, warp_in.fraction, warp_in.total_time, warp_in.time_left, None, unit)
    row = game_data.units[unit.type_id]
    build_progress = unit._latest_report.build_progress
    total_time, time_left = _total_time_and_time_left(row, build_progress)
    builder = unit.builder if Attribute.STRUCTURE in row.attributes else None
    return InProduction(unit.type_id, build_progress, total_time, time_left, builder, unit)


def _made_by_order(
    product: UnitTypeId, producer: OwnUnit[Any], progress: float | None, game_data: GameData
) -> InProduction | None:
    """A `product` of `producer`'s order at `progress`, or, where the order reports none, at the progress of
    `producer`'s morph, `None` where it shows none under way, as a lair still showing the lair morph does in its last
    step (in game)."""
    if progress is None:
        morph_progress = producer.morph_progress
        if morph_progress is None:
            return None
        return InProduction(
            product, morph_progress.fraction, morph_progress.total_time, morph_progress.time_left, producer, None
        )
    total_time, time_left = _total_time_and_time_left(game_data.units[product], progress)
    return InProduction(product, progress, total_time, time_left, producer, None)


def _total_time_and_time_left(row: UnitTypeData, progress: float) -> tuple[GameTime, GameTime]:
    """The time a unit of `row`'s type takes, and the time it has left at `progress`."""
    steps_done = round(progress * row._unrounded_build_time.steps)
    return row.build_time, GameTime(steps=row.build_time.nearest_step - steps_done)


def _ability_product(row: AbilityData, unit_type: UnitTypeId) -> UnitTypeId | UpgradeId | None:
    """What `row`'s ability makes when a unit of `unit_type` shows it: that type's product, or the one product the
    ability makes for every type, as an egg or a cocoon shows what its larva or unit was given."""
    product = row.products.get(unit_type)
    if product is not None:
        return product
    products = set(row.products.values())
    return products.pop() if len(products) == 1 else None


def _construction_has_started(unit: OwnUnit[Any]) -> bool:
    """Whether the structure `unit` was ordered to build, or its add-on, has begun: whether it has a construction, or
    an add-on finished as its order still shows."""
    return unit.construction is not None or unit.add_on is not None


def _order_progress(row: AbilityData, unit_type: UnitTypeId, progress: float) -> float | None:
    """How far along an order of `row`'s ability is that a unit of `unit_type` shows at `progress`: that for a train
    or a larva's morph, 0 for a build or an add-on not begun, and `None` for any other morph, whose order reads 0 from
    start to end (in game)."""
    if row.needs_placement:
        return 0.0
    if UnitTypeId.LARVA in row.performers or row.order_behavior_for(unit_type) is OrderBehavior.QUEUES:
        return progress
    return None
