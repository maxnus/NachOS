"""The player on the other side, and what is known of it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, final

from sc2nachos.gamedata import UpgradeType
from sc2nachos.units import Alliance, Visibility

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sc2nachos.gamedata import GameData
    from sc2nachos.ids import UnitTypeId, UpgradeId
    from sc2nachos.units import Unit


@final
class Enemy:
    """The other player of a game, and what NachOS has learned or been told about it.

    One game has one of these, made with the game and dropped with it.
    """

    __slots__ = ("_upgrades",)

    def __init__(self) -> None:
        # Replaced whole on every change, since the units read it as part of a key many times a step.
        self._upgrades: frozenset[UpgradeId] = frozenset()

    def __repr__(self) -> str:
        return f"Enemy(upgrades={{{', '.join(sorted(upgrade.name for upgrade in self._upgrades))}}})"

    @property
    def upgrades(self) -> frozenset[UpgradeId]:
        """Every upgrade the enemy is known to have, and every one a bot has assumed it has.

        The game reports no enemy upgrade but the attack, armor and shield levels on each unit in sight, which
        `upgrades_shown_by` reads as the levels of that unit type's own lines, and every unit of the enemy's counts what
        is held here, those out of sight included. Everything else, such as Grooved Spines or Metabolic Boost, the game
        never reports, and a bot that works one out says so with `assume_upgrades`.
        """
        return self._upgrades

    def assume_upgrades(self, *upgrades: UpgradeId) -> None:
        """Take the enemy to have `upgrades` from now on."""
        self._upgrades |= frozenset(upgrades)

    def forget_upgrades(self, *upgrades: UpgradeId) -> None:
        """Take the enemy not to have `upgrades` from now on.

        A unit of the enemy's that shows one again puts it back.
        """
        self._upgrades -= frozenset(upgrades)


class UpgradeLines:
    """The upgrades each unit type's reported levels stand for, worked out once per type.

    A unit reports how many attack and shields levels it has and how much armor its upgrades add, and each of those
    belongs to its owner and to the line it is of: a marine at attack level 2 shows the first two Terran Infantry
    Weapons, which every one of that player's infantry has.
    """

    __slots__ = ("_data", "_lines")

    def __init__(self, data: GameData) -> None:
        self._data = data
        self._lines: dict[UnitTypeId, dict[UpgradeType, tuple[UpgradeId, ...]]] = {}

    def shown_by(self, unit_type: UnitTypeId, attack: int, armor: int, shield: int) -> frozenset[UpgradeId]:
        """The upgrades a unit of `unit_type` reporting these levels shows its owner to have."""
        if not attack and not armor and not shield:
            return frozenset()
        lines = self._lines_of(unit_type)
        return frozenset(
            lines[UpgradeType.ATTACK][:attack] + lines[UpgradeType.ARMOR][:armor] + lines[UpgradeType.SHIELD][:shield]
        )

    def shields(self, unit_type: UnitTypeId) -> tuple[UpgradeId, ...]:
        """The shields levels of `unit_type`, whose count is the armor its shields have."""
        return self._lines_of(unit_type)[UpgradeType.SHIELD]

    def _lines_of(self, unit_type: UnitTypeId) -> dict[UpgradeType, tuple[UpgradeId, ...]]:
        """Each of the type's lines, in order of level, and no armor line where an upgrade that is no level gives it
        armor too: an ultralisk's 2 could be two levels or Chitinous Plating, and reading it as levels would armor
        every zergling."""
        if (lines := self._lines.get(unit_type)) is None:
            rows = sorted(
                (self._data.upgrades[upgrade] for upgrade in self._data.units[unit_type].upgrades),
                key=lambda row: row.level,
            )
            lines = {kind: tuple(row.id for row in rows if row.type is kind and row.level) for kind in UpgradeType}
            if any(row.type is UpgradeType.ARMOR and not row.level for row in rows):
                lines[UpgradeType.ARMOR] = ()
            self._lines[unit_type] = lines
        return lines


def upgrades_shown_by(units: Iterable[Unit[Any]], lines: UpgradeLines) -> frozenset[UpgradeId]:
    """What the enemy's units in sight show of its upgrades, read off the levels each reports."""
    shown: set[UpgradeId] = set()
    for unit in units:
        if unit.alliance is not Alliance.ENEMY or unit.visibility is not Visibility.IN_VISION:
            continue
        shown |= lines.shown_by(
            unit.type_id, unit.attack_upgrade_level, unit.armor_upgrade_level, unit.shield_upgrade_level
        )
    return frozenset(shown)
