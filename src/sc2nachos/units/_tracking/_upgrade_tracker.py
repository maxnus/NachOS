"""The upgrades each side's units have, and what they make of a unit's type."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, final

from s2clientprotocol import raw_pb2

from sc2nachos.ids import UnitTypeId, UpgradeId
from sc2nachos.upgrade_reader import UpgradeReader

if TYPE_CHECKING:
    from sc2nachos.enemy import Enemy
    from sc2nachos.gamedata import GameData, UnitTypeData
    from sc2nachos.units._unit import Unit

_OWN = raw_pb2.Alliance.Self
_ENEMY = raw_pb2.Alliance.Enemy


@final
class _UpgradeTracker:
    """This player's finished upgrades, as the last observation listed them, and each unit's type as the upgrades its
    owner has leave it: this player's own for its units, what `Enemy.upgrades` holds for the enemy's, and none for
    anyone else's."""

    __slots__ = ("_enemy", "_game_data", "_own", "_reader", "_rows")

    def __init__(self, game_data: GameData, enemy: Enemy) -> None:
        self._game_data = game_data
        self._enemy = enemy
        self._own: frozenset[UpgradeId] = frozenset()
        self._reader = UpgradeReader(game_data)
        # One upgraded row per unit type and set of upgrades held. Without it every read would build a type's weapons
        # again, and a bot reads them for every unit every step, where the sets of upgrades a game ever holds are few.
        self._rows: dict[tuple[UnitTypeId, frozenset[UpgradeId]], UnitTypeData] = {}

    @property
    def own(self) -> frozenset[UpgradeId]:
        """Every upgrade this player has finished researching, as of the last observation."""
        return self._own

    @property
    def reader(self) -> UpgradeReader:
        """Which upgrades each unit type's reported levels stand for, for whoever reads the units' reports."""
        return self._reader

    def update(self, player: raw_pb2.PlayerRaw) -> list[UpgradeId]:
        """Take in this player's upgrades, and answer those new to them, in the order of their ids.

        Raises `UncuratedIdError` where this player holds an upgrade the curated ids leave out, which belongs in them.
        """
        own = frozenset(UpgradeId.read(upgrade) for upgrade in player.upgrade_ids)
        # Upgrades are never lost, so a count unchanged is a set unchanged.
        new = sorted(own - self._own) if len(own) != len(self._own) else []
        self._own = own
        return new

    def upgraded_type(self, unit: Unit[Any]) -> UnitTypeData:
        """The type of `unit` as the upgrades its owner has leave it."""
        unit_type = unit.type_id
        row = self._game_data.units[unit_type]
        upgrades = self._of(unit)
        if not row.upgrades or not upgrades:
            return row
        key = (unit_type, upgrades)
        if (upgraded := self._rows.get(key)) is None:
            upgraded = self._rows[key] = row.with_upgrades(upgrades)
        return upgraded

    def armor_of(self, unit: Unit[Any]) -> float:
        """The armor of `unit`: its base armor with the armor its upgrades add.

        A unit in sight reports that armor itself, which is exact, where what its owner is known to have is a floor.
        """
        upgraded = self.upgraded_type(unit)
        if (report := unit._latest_report_in_vision) is None:
            return upgraded.armor
        return max(upgraded.armor, self._game_data.units[unit.type_id].armor + report.armor_upgrade_level)

    def shield_armor_of(self, unit: Unit[Any]) -> float:
        """The armor the shields of `unit` have, which is the shields levels its owner has, and 0 without shields."""
        upgrades = self._of(unit)
        levels = sum(1 for upgrade in self._reader.shields_of(unit.type_id) if upgrade in upgrades)
        if (report := unit._latest_report_in_vision) is None:
            return levels
        return max(levels, report.shield_upgrade_level)

    def _of(self, unit: Unit[Any]) -> frozenset[UpgradeId]:
        """The upgrades the owner of `unit` has: this player's own, the enemy's known ones, and nothing else."""
        alliance = unit._latest_report.alliance
        if alliance == _OWN:
            return self._own
        return self._enemy.upgrades if alliance == _ENEMY else frozenset()
