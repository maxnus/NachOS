"""The tables a game is played by."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, final

from sc2nachos.gamedata._ability import AbilityData
from sc2nachos.gamedata._effect import EffectData
from sc2nachos.gamedata._tech_tree_95841 import TECH_TREE
from sc2nachos.gamedata._unittype import UnitTypeData
from sc2nachos.gamedata._upgrade import UpgradeData

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from s2clientprotocol import sc2api_pb2

    from sc2nachos.ids import AbilityId, EffectId, UnitTypeId, UpgradeId


@final
class GameData:
    """What every unit type, ability, upgrade and effect in the game is, as they stand before any upgrade.

    A table holds only the rows the curated ids name, so most of what the game describes is not in one.
    """

    __slots__ = ("_abilities", "_effects", "_units", "_upgrades")

    def __init__(self, data: sc2api_pb2.ResponseData) -> None:
        """Read the tables out of the game's answer to `RequestData`."""
        self._units = _read_table(data.units, lambda unit: UnitTypeData.from_proto(unit, TECH_TREE), lambda row: row.id)
        self._abilities = _read_table(
            data.abilities, lambda ability: AbilityData.from_proto(ability, TECH_TREE), lambda row: row.id
        )
        self._upgrades = _read_table(data.upgrades, UpgradeData.from_proto, lambda row: row.id)
        self._effects = _read_table(data.effects, EffectData.from_proto, lambda row: row.id)

    @property
    def units(self) -> Mapping[UnitTypeId, UnitTypeData]:
        """What each type of unit is."""
        return self._units

    @property
    def abilities(self) -> Mapping[AbilityId, AbilityData]:
        """What each ability is."""
        return self._abilities

    @property
    def upgrades(self) -> Mapping[UpgradeId, UpgradeData]:
        """What each upgrade is."""
        return self._upgrades

    @property
    def effects(self) -> Mapping[EffectId, EffectData]:
        """What each effect is."""
        return self._effects


def _read_table[MessageT, IdT, RowT](
    entries: Iterable[MessageT], read: Callable[[MessageT], RowT], key: Callable[[RowT], IdT]
) -> Mapping[IdT, RowT]:
    """Every entry the curated ids name, read into a row and filed under its own id."""
    rows: dict[IdT, RowT] = {}
    for entry in entries:
        try:
            row = read(entry)
        except ValueError:
            # An id the curation leaves out, which is most of them, or one newer than `data/stableid.json`.
            continue
        rows[key(row)] = row
    return MappingProxyType(rows)
