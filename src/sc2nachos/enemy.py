"""The player on the other side, and what is known of it."""

from __future__ import annotations

from typing import TYPE_CHECKING, final

if TYPE_CHECKING:
    from sc2nachos.ids import UpgradeId


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

        The game reports no enemy upgrade but the attack, armor and shield levels on each unit in sight, which NachOS
        reads as the levels of that unit type's own upgrade lines and keeps here, so every unit of the enemy's counts
        them, those out of sight included. Everything else, such as Grooved Spines or Metabolic Boost, the game never
        reports, and a bot that works one out says so with `assume_upgrade`.
        """
        return self._upgrades

    def assume_upgrade(self, upgrade: UpgradeId) -> None:
        """Take the enemy to have `upgrade` from now on."""
        self._upgrades |= {upgrade}

    def forget_upgrade(self, upgrade: UpgradeId) -> None:
        """Take the enemy not to have `upgrade` from now on.

        A unit of the enemy's that reports it again puts it back.
        """
        self._upgrades -= {upgrade}

    def _learn_upgrades(self, upgrades: frozenset[UpgradeId]) -> None:
        """Take in what the enemy's units have shown of their upgrades."""
        self._upgrades |= upgrades
