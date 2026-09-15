"""The upgrades a bot assumes another player has."""

from __future__ import annotations

from collections.abc import MutableSet
from typing import TYPE_CHECKING, final

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sc2nachos.ids import UpgradeId


@final
class AssumedUpgrades(MutableSet["UpgradeId"]):
    """The upgrades the enemy is assumed to have researched, which every read of its units' upgraded values counts.

    The game reports no enemy upgrade but the attack, armor and shield levels on each unit in sight, and those count in
    place of any level assumed here. Empty as each game starts.
    """

    __slots__ = ("_upgrades",)

    def __init__(self) -> None:
        # Replaced whole on every change, since the units read it as a key many times a step.
        self._upgrades: frozenset[UpgradeId] = frozenset()

    def __contains__(self, upgrade: object) -> bool:
        return upgrade in self._upgrades

    def __iter__(self) -> Iterator[UpgradeId]:
        return iter(self._upgrades)

    def __len__(self) -> int:
        return len(self._upgrades)

    def __repr__(self) -> str:
        return f"AssumedUpgrades({{{', '.join(sorted(upgrade.name for upgrade in self._upgrades))}}})"

    def add(self, value: UpgradeId) -> None:
        """Assume `value` researched from now on."""
        self._upgrades |= {value}

    def discard(self, value: UpgradeId) -> None:
        """Assume `value` not researched from now on."""
        self._upgrades -= {value}
