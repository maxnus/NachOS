"""The player on the other side, and what is known of it."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, final

from sc2nachos._enum import ReadableIntEnum
from sc2nachos.gamedata import UpgradeType
from sc2nachos.ids import BuffId, EffectId, UnitTypeId, UpgradeId
from sc2nachos.units import Alliance, Visibility

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from sc2nachos.gamedata import GameData
    from sc2nachos.state import Effect
    from sc2nachos.units import Unit


class UpgradeInference(ReadableIntEnum):
    """How much of `Enemy.upgrades` NachOS works out for itself, each setting working out all the one before it does."""

    NONE = 0
    """Nothing: only a bot changes it."""
    BASIC = 1
    """The attack, armor and shield levels the enemy's units in sight report, as `upgrades_shown_by` reads them."""
    INTERMEDIATE = 2
    """What the enemy's units and effects and this player's units show beyond the levels, each of which only an
    upgrade can bring about, as `upgrades_evident_from` reads them: a warp gate, a burrowed zergling, a stimmed marine.
    It reads the buffs units wear, so a buff the curated ids leave out raises `UncuratedIdError`."""


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
        sight, which `upgrades_shown_by` reads as the levels of that unit type's own lines, and what only an upgrade
        can bring about, such as a burrowed zergling or a stimmed marine, which `upgrades_evident_from` reads. NachOS
        adds these here as far as the `Api` was told to with `UpgradeInference`. Every unit of the enemy's counts what
        is held here, those out of sight included. Anything else, such as Grooved Spines or Metabolic Boost, a bot that
        works it out says with `assume_upgrades`.
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


# The buffs whose wearer shows its owner has an upgrade, which the ability that puts each on needs.
_WORN_WITH: Final[Mapping[BuffId, UpgradeId]] = MappingProxyType(
    {
        BuffId.BANSHEE_CLOAK: UpgradeId.BANSHEE_CLOAK,
        BuffId.GHOST_CLOAK: UpgradeId.GHOST_CLOAK,
        BuffId.HYDRALISK_LUNGE: UpgradeId.HYDRALISK_LUNGE,
        # On the unit parasited, which the game reports as its controller's for as long as it lasts.
        BuffId.INFESTOR_NEURAL_PARASITE: UpgradeId.NEURAL_PARASITE,
        BuffId.MARAUDER_STIMMED: UpgradeId.STIMPACK,
        BuffId.MARINE_STIMMED: UpgradeId.STIMPACK,
        BuffId.ZEALOT_CHARGING: UpgradeId.CHARGE,
    }
)
# The buffs whose wearer shows its owner's opponent has an upgrade, whose ability put each on.
_PUT_ON_WITH: Final[Mapping[BuffId, UpgradeId]] = MappingProxyType(
    {
        BuffId.MARAUDER_CONCUSSIVE_SHELLS_SLOW: UpgradeId.CONCUSSIVE_SHELLS,
        BuffId.RAVEN_INTERFERENCE_MATRIX: UpgradeId.INTERFERENCE_MATRIX,
    }
)
_LEFT_WITH: Final[Mapping[EffectId, UpgradeId]] = MappingProxyType({EffectId.HIGH_TEMPLAR_STORM: UpgradeId.STORM})
# A gateway turns into a warp gate by itself once Warp Gate is researched, and nothing else makes one.
_ONLY_WITH: Final[Mapping[UnitTypeId, UpgradeId]] = MappingProxyType({UnitTypeId.WARP_GATE: UpgradeId.WARP_GATE})
# The health a unit has without the one upgrade that adds to it, which the game's tables do not hold.
_HEALTH_WITHOUT: Final[Mapping[UnitTypeId, tuple[float, UpgradeId]]] = MappingProxyType(
    {UnitTypeId.MARINE: (45.0, UpgradeId.COMBAT_SHIELD)}
)


class UpgradeSigns:
    """The upgrades units and effects show beyond the levels units report, each of which only that upgrade can bring
    about, worked out once per unit type."""

    __slots__ = ("_data", "_types")

    def __init__(self, data: GameData) -> None:
        self._data = data
        self._types: dict[UnitTypeId, frozenset[UpgradeId]] = {}

    def of_owner(self, unit: Unit[Any]) -> frozenset[UpgradeId]:
        """The upgrades `unit` shows its owner has, by its type, and while in sight by the buffs it wears and its
        health.

        A unit under a neural parasite shows only that its controller has Neural Parasite, since what it wore and
        what it was made as belong to the player it was taken from.
        """
        shown = self._of_type(unit.type_id)
        if unit.visibility is not Visibility.IN_VISION:
            return shown
        buffs = unit.buffs
        if BuffId.INFESTOR_NEURAL_PARASITE in buffs:
            return frozenset({UpgradeId.NEURAL_PARASITE})
        worn = {_WORN_WITH[buff] for buff in buffs if buff in _WORN_WITH}
        if (health := _HEALTH_WITHOUT.get(unit.type_id)) is not None and unit.health_max > health[0]:
            worn.add(health[1])
        return shown | worn if worn else shown

    @staticmethod
    def of_opponent(unit: Unit[Any]) -> frozenset[UpgradeId]:
        """The upgrades the buffs `unit` wears show its owner's opponent has, and nothing for a unit out of sight."""
        if unit.visibility is not Visibility.IN_VISION:
            return frozenset()
        return frozenset(_PUT_ON_WITH[buff] for buff in unit.buffs if buff in _PUT_ON_WITH)

    @staticmethod
    def of_effect(effect: Effect) -> frozenset[UpgradeId]:
        """The upgrades `effect` shows its owner has."""
        upgrade = _LEFT_WITH.get(effect.id)
        return frozenset() if upgrade is None else frozenset({upgrade})

    def _of_type(self, unit_type: UnitTypeId) -> frozenset[UpgradeId]:
        """The upgrades a unit of `unit_type` cannot be made without: those every type offered its creation ability
        needs for it, as a burrowed zergling needs Burrow, and those `_ONLY_WITH` holds."""
        if (upgrades := self._types.get(unit_type)) is None:
            upgrades = frozenset()
            if (ability := self._data.units[unit_type].creation_ability) is not None:
                needs = [
                    requirements.upgrades
                    for performer in self._data.abilities[ability].performers
                    if (requirements := self._data.units[performer].ability_requirements.get(ability)) is not None
                ]
                upgrades = frozenset.intersection(*needs) if needs else frozenset()
            if (only := _ONLY_WITH.get(unit_type)) is not None:
                upgrades |= {only}
            self._types[unit_type] = upgrades
        return upgrades


def upgrades_evident_from(
    units: Iterable[Unit[Any]], effects: Iterable[Effect], signs: UpgradeSigns
) -> frozenset[UpgradeId]:
    """What the enemy's units and effects, and the buffs this player's units wear, show of the enemy's upgrades beyond
    the levels its units report."""
    evident: set[UpgradeId] = set()
    for unit in units:
        if unit.alliance is Alliance.ENEMY:
            evident |= signs.of_owner(unit)
        elif unit.alliance is Alliance.OWN:
            evident |= signs.of_opponent(unit)
    for effect in effects:
        if effect.alliance is Alliance.ENEMY:
            evident |= signs.of_effect(effect)
    return frozenset(evident)
