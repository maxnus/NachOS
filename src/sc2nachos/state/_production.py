"""What this player's units are making in one observation."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Any, final

from sc2nachos.ids import AbilityId, UnitTypeId, UpgradeId

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sc2nachos.gamedata import AbilityData, GameData
    from sc2nachos.units import OwnUnit


@final
class _Production:
    """What this player's units are making in one observation: how many of each unit type, and how far along each
    research is.

    A unit counts that is still going up: a structure, an add-on, a unit warping in. So does each order a unit shows
    that costs and makes a unit type: a train, queued ones included, an egg's or a cocoon's morph, a structure's morph.
    A build or an add-on counts only until what it places stands, which then counts itself. What NachOS holds and has
    not sent counts nowhere.
    """

    __slots__ = ("_counts", "_progress")

    def __init__(self, units: Iterable[OwnUnit[Any]], game_data: GameData) -> None:
        """Read what `units` are making, by what `game_data` says each ability makes."""
        self._counts: Counter[UnitTypeId] = Counter()
        self._progress: dict[UpgradeId, float] = {}
        for unit in units:
            self._read(unit, game_data)

    def count(self, unit_types: Iterable[UnitTypeId]) -> int:
        """How many units of `unit_types` are being made."""
        return sum(self._counts[unit_type] for unit_type in unit_types)

    def progress(self, upgrade: UpgradeId) -> float | None:
        """How far along a research of `upgrade` is, from 0 to 1, or `None` if nothing researches it."""
        return self._progress.get(upgrade)

    def _read(self, unit: OwnUnit[Any], game_data: GameData) -> None:
        """Count what `unit` is: a unit still going up, and what its orders make."""
        report = unit._latest_report
        if report.build_progress < 1.0:
            self._counts[unit.type_id] += 1
        for order in report.orders:
            row = game_data.abilities.get(AbilityId.get(order.ability_id) or AbilityId.NULL)
            if row is None or not (row.cost.minerals or row.cost.vespene):
                continue
            product = _product(row, unit.type_id)
            if isinstance(product, UpgradeId):
                self._progress[product] = max(self._progress.get(product, 0.0), order.progress)
            elif product is not None and not (row.needs_placement and _has_placed(unit)):
                self._counts[product] += 1


def _product(row: AbilityData, unit_type: UnitTypeId) -> UnitTypeId | UpgradeId | None:
    """What `row`'s ability makes when a unit of `unit_type` shows it: that type's product, or the one product the
    ability makes for every type, as an egg or a cocoon shows what its larva or unit was given."""
    product = row.products.get(unit_type)
    if product is not None:
        return product
    products = set(row.products.values())
    return products.pop() if len(products) == 1 else None


def _has_placed(unit: OwnUnit[Any]) -> bool:
    """Whether what `unit` was ordered to place stands: the structure it builds, or its add-on."""
    return unit.construction is not None or unit.add_on is not None
