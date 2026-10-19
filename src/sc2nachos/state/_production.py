"""What this player's units are making in one observation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, final

from sc2nachos.gamedata import OrderBehavior
from sc2nachos.ids import AbilityId, UnitTypeId, UpgradeId
from sc2nachos.state._in_production import InProduction
from sc2nachos.state._research import Research

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Mapping

    from sc2nachos.gamedata import AbilityData, GameData
    from sc2nachos.units import OwnUnit


@final
class _Production:
    """What this player's units are making in one observation: each unit in production, and how far along each
    research is.

    A unit is in production while it goes up: a structure, an add-on, a unit warping in. So is what each order a unit
    shows makes, if it costs and makes a unit type: a train, queued ones included, an egg's or a cocoon's morph, a
    structure's morph. A build or an add-on is read from its order only until its construction starts, and then from
    the structure itself. An order not yet sent is nowhere.
    """

    __slots__ = ("_items", "_researches")

    def __init__(self, units: Iterable[OwnUnit[Any]], game_data: GameData) -> None:
        """Read what `units` are making, by what `game_data` says each ability makes."""
        self._items: list[InProduction] = []
        self._researches: dict[UpgradeId, Research] = {}
        for unit in units:
            self._read(unit, game_data)

    def of_types(self, unit_types: Collection[UnitTypeId]) -> tuple[InProduction, ...]:
        """What is being made of `unit_types`, in the order the observation lists the units it is read from."""
        return tuple(item for item in self._items if item.type_id in unit_types)

    @property
    def researches(self) -> Mapping[UpgradeId, Research]:
        """Each research running or queued, by upgrade, in the order the observation lists the structures and each its
        orders."""
        return self._researches

    def _read(self, unit: OwnUnit[Any], game_data: GameData) -> None:
        """Read what `unit` makes: itself while it goes up, and what its orders make."""
        report = unit._latest_report
        if report.build_progress < 1.0:
            self._items.append(InProduction(unit.type_id, unit, report.build_progress))
        for order in report.orders:
            row = game_data.abilities.get(AbilityId.get(order.ability_id) or AbilityId.NULL)
            if row is None or not (row.cost.minerals or row.cost.vespene):
                continue
            product = _ability_product(row, unit.type_id)
            if isinstance(product, UpgradeId):
                if product not in self._researches:
                    total = game_data.upgrades[product].research_steps
                    # The game's progress is the steps done over the total, as a 32-bit float (in game).
                    self._researches[product] = Research(product, unit, total, round((1 - order.progress) * total))
            elif product is not None and not (row.needs_placement and _construction_has_started(unit)):
                self._items.append(InProduction(product, unit, _order_progress(row, unit.type_id, order.progress)))


def _ability_product(row: AbilityData, unit_type: UnitTypeId) -> UnitTypeId | UpgradeId | None:
    """What `row`'s ability makes when a unit of `unit_type` shows it: that type's product, or the one product the
    ability makes for every type, as an egg or a cocoon shows what its larva or unit was given."""
    product = row.products.get(unit_type)
    if product is not None:
        return product
    products = set(row.products.values())
    return products.pop() if len(products) == 1 else None


def _construction_has_started(unit: OwnUnit[Any]) -> bool:
    """Whether the structure `unit` was ordered to build, or its add-on, has begun."""
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
