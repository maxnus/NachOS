"""The upgrades a bot assumes another player has."""

from __future__ import annotations

from collections.abc import MutableSet
from typing import TYPE_CHECKING, final

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from sc2nachos.ids import UpgradeId


@final
class AssumedUpgrades(MutableSet["UpgradeId"]):
    """The upgrades the enemy is assumed to have researched, which every read of its units' upgraded values counts.

    The game reports no enemy upgrade but the attack, armor and shield levels on each unit in sight, and what a unit
    reports counts in place of what it covers here. Empty as each game starts. What `|`, `-`, `&` and `^` make of it is
    a `frozenset`, which assumes nothing; the in-place `|=`, `-=`, `&=` and `^=` change what is assumed.
    """

    __slots__ = ("_upgrades",)

    def __init__(self) -> None:
        # Replaced whole on every change, since the units read it as a key many times a step.
        self._upgrades: frozenset[UpgradeId] = frozenset()

    @classmethod
    def _from_iterable[T](cls, upgrades: Iterable[T]) -> frozenset[T]:
        """What the set operators make: a plain set, since only this one is read as what is assumed."""
        return frozenset(upgrades)

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
