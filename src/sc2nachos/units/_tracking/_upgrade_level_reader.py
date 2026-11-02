"""Reading the enemy's upgrades off the attack, armor and shield levels its units report."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, final

from sc2nachos.gamedata import UpgradeType
from sc2nachos.units._values import Alliance, Visibility

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sc2nachos.gamedata import GameData
    from sc2nachos.ids import UnitTypeId, UpgradeId
    from sc2nachos.units._unit import Unit


@final
class _UpgradeLevelReader:
    """Reads the upgrades the enemy's visible units show through the levels they report. Each unit type's upgrade
    lines are worked out once."""

    __slots__ = ("_game_data", "_lines")

    def __init__(self, game_data: GameData) -> None:
        self._game_data = game_data
        self._lines: dict[UnitTypeId, dict[UpgradeType, tuple[UpgradeId, ...]]] = {}

    def read_levels(self, units: Iterable[Unit[Any]]) -> frozenset[UpgradeId]:
        """The enemy's upgrades that its visible units show through the levels they report. A unit a neural parasite
        took from this player shows nothing."""
        shown: set[UpgradeId] = set()
        for unit in units:
            if (
                unit.alliance is not Alliance.ENEMY
                or unit.first_alliance is not Alliance.ENEMY
                or unit.visibility is not Visibility.VISIBLE
            ):
                continue
            shown |= self._levels_of(
                unit.type_id, unit.attack_upgrade_level, unit.armor_upgrade_level, unit.shield_upgrade_level
            )
        return frozenset(shown)

    def shields_of(self, unit_type: UnitTypeId) -> tuple[UpgradeId, ...]:
        """The shield upgrade line of `unit_type`, in level order. The levels its owner has are its shields' armor."""
        return self._lines_of(unit_type)[UpgradeType.SHIELD]

    def _levels_of(self, unit_type: UnitTypeId, attack: int, armor: int, shield: int) -> frozenset[UpgradeId]:
        """The upgrades a unit of `unit_type` reporting these levels shows its owner has.

        A unit reports how many attack and shield levels it has, and how much armor its upgrades add. Each belongs to
        its owner and to the unit's own line: a marine at attack level 2 shows the first two Terran Infantry Weapons,
        which every infantry unit of that player has. A negative level counts as none.
        """
        attack, armor, shield = max(attack, 0), max(armor, 0), max(shield, 0)
        if not attack and not armor and not shield:
            return frozenset()
        lines = self._lines_of(unit_type)
        return frozenset(
            lines[UpgradeType.ATTACK][:attack] + lines[UpgradeType.ARMOR][:armor] + lines[UpgradeType.SHIELD][:shield]
        )

    def _lines_of(self, unit_type: UnitTypeId) -> dict[UpgradeType, tuple[UpgradeId, ...]]:
        """The type's upgrade lines, each in order of level. The armor line is empty for a type that also gets armor
        from an upgrade that is no level: an ultralisk's 2 could be two levels or Chitinous Plating, and reading it as
        levels would armor every zergling."""
        if (lines := self._lines.get(unit_type)) is None:
            rows = sorted(
                (self._game_data.upgrades[upgrade] for upgrade in self._game_data.units[unit_type].upgrades),
                key=lambda row: row.level,
            )
            lines = {
                kind: tuple(row.id for row in rows if row.upgrade_type is kind and row.level) for kind in UpgradeType
            }
            if any(row.upgrade_type is UpgradeType.ARMOR and not row.level for row in rows):
                lines[UpgradeType.ARMOR] = ()
            self._lines[unit_type] = lines
        return lines
