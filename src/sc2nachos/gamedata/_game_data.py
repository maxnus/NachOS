"""The game's data tables."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, final

from sc2nachos.gamedata._ability_data import AbilityData, ability_costs, cancel_abilities, order_behaviors
from sc2nachos.gamedata._effect_data import EffectData
from sc2nachos.gamedata._techtree import TECH_TREE
from sc2nachos.gamedata._unit_type_data import Attribute, UnitTypeData
from sc2nachos.gamedata._upgrade_data import UpgradeData

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from s2clientprotocol import sc2api_pb2

    from sc2nachos.ids import AbilityId, EffectId, UnitTypeId, UpgradeId


@final
class GameData:
    """The game's tables of unit types, abilities, upgrades and effects, as they stand before any upgrade.

    A table holds only the rows the curated ids name, which leaves out most of what the game describes.
    """

    __slots__ = ("_abilities", "_effects", "_units", "_upgrades")

    def __init__(self, data: sc2api_pb2.ResponseData) -> None:
        """Read the tables from the game's answer to `RequestData`."""
        self._units = _read_table(
            data.units, lambda unit: UnitTypeData._from_proto(unit, TECH_TREE), lambda row: row.id
        )
        structures = frozenset(row.id for row in self._units.values() if Attribute.STRUCTURE in row.attributes)
        behaviors, behaviors_by_performer = order_behaviors(
            TECH_TREE, structures, frozenset(self._units.keys() - structures)
        )
        self._upgrades = _read_table(
            data.upgrades, lambda upgrade: UpgradeData._from_proto(upgrade, TECH_TREE), lambda row: row.id
        )
        # An ability's cost is derived from its product, so the other tables come first.
        costs = ability_costs(self._units, self._upgrades, TECH_TREE)
        cancels = cancel_abilities(TECH_TREE, behaviors)
        self._abilities = _read_table(
            data.abilities,
            lambda ability: AbilityData._from_proto(
                ability, TECH_TREE, behaviors, behaviors_by_performer, costs, cancels
            ),
            lambda row: row.id,
        )
        self._effects = _read_table(data.effects, EffectData._from_proto, lambda row: row.id)

    @property
    def units(self) -> Mapping[UnitTypeId, UnitTypeData]:
        """The unit types, by id."""
        return self._units

    @property
    def abilities(self) -> Mapping[AbilityId, AbilityData]:
        """The abilities, by id."""
        return self._abilities

    @property
    def upgrades(self) -> Mapping[UpgradeId, UpgradeData]:
        """The upgrades, by id."""
        return self._upgrades

    @property
    def effects(self) -> Mapping[EffectId, EffectData]:
        """The effects, by id."""
        return self._effects


def _read_table[MessageT, IdT, RowT](
    entries: Iterable[MessageT], read: Callable[[MessageT], RowT], key: Callable[[RowT], IdT]
) -> Mapping[IdT, RowT]:
    """The entries the curated ids name, read into rows keyed by id."""
    rows: dict[IdT, RowT] = {}
    for entry in entries:
        try:
            row = read(entry)
        except ValueError:
            # An uncurated id, which most are, or one newer than `data/stableid.json`.
            continue
        rows[key(row)] = row
    return MappingProxyType(rows)
