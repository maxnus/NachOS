"""The enemy player: what is known of it, and how much of that NachOS infers."""

from __future__ import annotations

from typing import TYPE_CHECKING, final

from sc2nachos._enum import ReadableIntEnum

if TYPE_CHECKING:
    from sc2nachos.ids import UpgradeId


class UpgradeInference(ReadableIntEnum):
    """How much of `Enemy.upgrades` NachOS infers. Each level includes the ones below it."""

    NONE = 0
    """Nothing. Only the bot changes the upgrades."""
    BASIC = 1
    """The attack, armor and shield levels enemy units in sight report. `UpgradeReader.read_basic_upgrades` reads them
    as levels of each unit type's own upgrade lines."""
    INTERMEDIATE = 2
    """Also what only an upgrade can cause, read by `UpgradeReader.read_intermediate_upgrades`: Burrow, Warp Gate,
    Stimpack, Combat Shield, Concussive Shells, Charge, Psionic Storm, Neural Parasite, Interference Matrix,
    Nanomuscular Swell, Cloaking Field and Personal Cloaking. This reads the buffs on every unit in sight, so a buff
    the curated ids leave out raises `UncuratedIdError`, which the lower levels never do."""


@final
class Enemy:
    """The other player, and what NachOS has learned or been told about it. Each game has its own."""

    __slots__ = ("_upgrades",)

    def __init__(self) -> None:
        # Replaced whole on every change, since units read it as part of a key many times a step.
        self._upgrades: frozenset[UpgradeId] = frozenset()

    def __repr__(self) -> str:
        return f"Enemy(upgrades={{{', '.join(sorted(upgrade.name for upgrade in self._upgrades))}}})"

    @property
    def upgrades(self) -> frozenset[UpgradeId]:
        """Every upgrade the enemy is known or assumed to have.

        The game never reports an enemy upgrade outright. It reports the attack, armor and shield levels of each unit
        in sight, and what only an upgrade can cause, such as a burrowed zergling or a stimmed marine. `UpgradeReader`
        reads both, and NachOS adds them here as far as the `Api` was told to with `UpgradeInference`. Every enemy
        unit, in sight or not, counts what is held here. Anything else, such as Grooved Spines or Metabolic Boost, the
        bot adds with `assume_upgrades` when it works it out.
        """
        return self._upgrades

    def assume_upgrades(self, *upgrades: UpgradeId) -> None:
        """Assume the enemy has `upgrades` from now on."""
        self._upgrades |= frozenset(upgrades)

    def forget_upgrades(self, *upgrades: UpgradeId) -> None:
        """Assume the enemy no longer has `upgrades`.

        If NachOS infers upgrades, an enemy unit that shows one again puts it back.
        """
        self._upgrades -= frozenset(upgrades)
