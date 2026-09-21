"""The player on the other side, what is known of it, and how much of that NachOS works out for itself."""

from __future__ import annotations

from typing import TYPE_CHECKING, final

from sc2nachos._enum import ReadableIntEnum

if TYPE_CHECKING:
    from sc2nachos.ids import UpgradeId


class UpgradeInference(ReadableIntEnum):
    """How much of `Enemy.upgrades` NachOS works out for itself, each setting working out all the one before it does."""

    NONE = 0
    """Nothing: only a bot changes it."""
    BASIC = 1
    """The attack, armor and shield levels the enemy's units in sight report, which `UpgradeReader.read_basic_upgrades`
    reads as the levels of each type's own lines."""
    INTERMEDIATE = 2
    """What only an upgrade can bring about, which `UpgradeReader.read_intermediate_upgrades` reads: Burrow, Warp Gate,
    Stimpack, Combat Shield, Concussive Shells, Charge, Psionic Storm, Neural Parasite, Interference Matrix,
    Nanomuscular Swell, Cloaking Field and Personal Cloaking. It reads the buffs every unit in sight wears, so a buff
    the curated ids leave out raises `UncuratedIdError` where the settings below it never would."""


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

        The game reports no enemy upgrade outright. It reports the attack, armor and shield levels on each unit in
        sight, and what only an upgrade can bring about, such as a burrowed zergling or a stimmed marine, both of which
        `UpgradeReader` reads and NachOS adds here as far as the `Api` was told to with `UpgradeInference`. Every unit
        of the enemy's counts what is held here, those out of sight included. Anything else, such as Grooved Spines or
        Metabolic Boost, a bot that works it out says with `assume_upgrades`.
        """
        return self._upgrades

    def assume_upgrades(self, *upgrades: UpgradeId) -> None:
        """Take the enemy to have `upgrades` from now on."""
        self._upgrades |= frozenset(upgrades)

    def forget_upgrades(self, *upgrades: UpgradeId) -> None:
        """Take the enemy not to have `upgrades` from now on.

        Where NachOS reads what the enemy's units show, a unit that shows one again puts it back.
        """
        self._upgrades -= frozenset(upgrades)
