"""Reading a player's upgrades off what its units and effects show of them."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final

from sc2nachos.gamedata import UpgradeType
from sc2nachos.ids import BuffId, EffectId, UnitTypeId, UpgradeId
from sc2nachos.units import Alliance, Visibility

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from sc2nachos.gamedata import GameData
    from sc2nachos.state import Effect
    from sc2nachos.units import Unit


class UpgradeReader:
    """What the units and effects of an observation show of the upgrades their owners have, worked out once per unit
    type."""

    __slots__ = ("_data", "_lines", "_types")

    # The buffs whose wearer shows its owner has an upgrade, which the ability that puts each on needs.
    _BUFF_EVIDENCE_OF_OWNER: Final[Mapping[BuffId, UpgradeId]] = MappingProxyType(
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
    # The buffs whose wearer shows its owner's opponent has an upgrade, whose ability put each on. These mappings stay
    # apart, rather than becoming one keyed by every kind of evidence, since a dict cannot hold ids of two enums: a
    # buff id and a unit type id of the same number are one key.
    _BUFF_EVIDENCE_OF_OPPONENT: Final[Mapping[BuffId, UpgradeId]] = MappingProxyType(
        {
            BuffId.MARAUDER_CONCUSSIVE_SHELLS_SLOW: UpgradeId.CONCUSSIVE_SHELLS,
            BuffId.RAVEN_INTERFERENCE_MATRIX: UpgradeId.INTERFERENCE_MATRIX,
        }
    )
    _EFFECT_EVIDENCE: Final[Mapping[EffectId, UpgradeId]] = MappingProxyType(
        {EffectId.HIGH_TEMPLAR_STORM: UpgradeId.STORM}
    )
    # A gateway turns into a warp gate by itself once Warp Gate is researched, and nothing else makes one.
    _UNIT_TYPE_EVIDENCE: Final[Mapping[UnitTypeId, UpgradeId]] = MappingProxyType(
        {UnitTypeId.WARP_GATE: UpgradeId.WARP_GATE}
    )
    # The health a unit has without the one upgrade that adds to it, measured in game: the game's tables hold no
    # health at all.
    _HEALTH_EVIDENCE: Final[Mapping[UnitTypeId, tuple[float, UpgradeId]]] = MappingProxyType(
        {UnitTypeId.MARINE: (45.0, UpgradeId.COMBAT_SHIELD)}
    )

    def __init__(self, data: GameData) -> None:
        self._data = data
        self._lines: dict[UnitTypeId, dict[UpgradeType, tuple[UpgradeId, ...]]] = {}
        self._types: dict[UnitTypeId, frozenset[UpgradeId]] = {}

    # --- The levels a unit reports

    def read_basic_upgrades(self, units: Iterable[Unit[Any]]) -> frozenset[UpgradeId]:
        """What the enemy's units in sight show of its upgrades, read off the levels each reports."""
        shown: set[UpgradeId] = set()
        for unit in units:
            if unit.alliance is not Alliance.ENEMY or unit.visibility is not Visibility.IN_VISION:
                continue
            shown |= self._levels_of(
                unit.type_id, unit.attack_upgrade_level, unit.armor_upgrade_level, unit.shield_upgrade_level
            )
        return frozenset(shown)

    def _levels_of(self, unit_type: UnitTypeId, attack: int, armor: int, shield: int) -> frozenset[UpgradeId]:
        """The upgrades a unit of `unit_type` reporting these levels shows its owner to have.

        A unit reports how many attack and shields levels it has and how much armor its upgrades add, and each of those
        belongs to its owner and to the line it is of: a marine at attack level 2 shows the first two Terran Infantry
        Weapons, which every one of that player's infantry has.
        """
        if not attack and not armor and not shield:
            return frozenset()
        lines = self._lines_of(unit_type)
        return frozenset(
            lines[UpgradeType.ATTACK][:attack] + lines[UpgradeType.ARMOR][:armor] + lines[UpgradeType.SHIELD][:shield]
        )

    def shields_of(self, unit_type: UnitTypeId) -> tuple[UpgradeId, ...]:
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

    # --- What only an upgrade brings about

    def read_intermediate_upgrades(self, units: Iterable[Unit[Any]], effects: Iterable[Effect]) -> frozenset[UpgradeId]:
        """What the enemy's units and effects, and the buffs this player's units wear, show of the enemy's upgrades
        beyond the levels its units report."""
        shown: set[UpgradeId] = set()
        for unit in units:
            if unit.alliance is Alliance.ENEMY:
                shown |= self._of_owner(unit)
            elif unit.alliance is Alliance.OWN:
                shown |= self._of_opponent(unit)
        for effect in effects:
            if effect.alliance is Alliance.ENEMY:
                shown |= self._of_effect(effect)
        return frozenset(shown)

    def _of_owner(self, unit: Unit[Any]) -> frozenset[UpgradeId]:
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
        worn = {self._BUFF_EVIDENCE_OF_OWNER[buff] for buff in buffs if buff in self._BUFF_EVIDENCE_OF_OWNER}
        if (health := self._HEALTH_EVIDENCE.get(unit.type_id)) is not None and unit.health_max > health[0]:
            worn.add(health[1])
        return shown | worn if worn else shown

    @classmethod
    def _of_opponent(cls, unit: Unit[Any]) -> frozenset[UpgradeId]:
        """The upgrades the buffs `unit` wears show its owner's opponent has, and nothing for a unit out of sight."""
        if unit.visibility is not Visibility.IN_VISION:
            return frozenset()
        return frozenset(
            cls._BUFF_EVIDENCE_OF_OPPONENT[buff] for buff in unit.buffs if buff in cls._BUFF_EVIDENCE_OF_OPPONENT
        )

    @classmethod
    def _of_effect(cls, effect: Effect) -> frozenset[UpgradeId]:
        """The upgrades `effect` shows its owner has."""
        upgrade = cls._EFFECT_EVIDENCE.get(effect.id)
        return frozenset() if upgrade is None else frozenset({upgrade})

    def _of_type(self, unit_type: UnitTypeId) -> frozenset[UpgradeId]:
        """The upgrades a unit of `unit_type` cannot be made without: those every type offered its creation ability
        needs for it, as a burrowed zergling needs Burrow, and those `_UNIT_TYPE_EVIDENCE` holds."""
        if (upgrades := self._types.get(unit_type)) is None:
            upgrades = frozenset()
            if (ability := self._data.units[unit_type].creation_ability) is not None:
                needs = [
                    requirements.upgrades
                    for performer in self._data.abilities[ability].performers
                    if (requirements := self._data.units[performer].ability_requirements.get(ability)) is not None
                ]
                upgrades = frozenset.intersection(*needs) if needs else frozenset()
            if (only := self._UNIT_TYPE_EVIDENCE.get(unit_type)) is not None:
                upgrades |= {only}
            self._types[unit_type] = upgrades
        return upgrades
