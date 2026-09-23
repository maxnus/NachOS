"""Reading a player's upgrades off what its units and effects show."""

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
    """Reads off an observation's units and effects the upgrades their owners have. What a unit type can show is
    worked out once."""

    __slots__ = ("_game_data", "_lines", "_types")

    # Buffs whose wearer shows its owner has the upgrade the buff's ability needs.
    _BUFF_EVIDENCE_OF_OWNER: Final[Mapping[BuffId, UpgradeId]] = MappingProxyType(
        {
            BuffId.BANSHEE_CLOAK: UpgradeId.BANSHEE_CLOAK,
            BuffId.GHOST_CLOAK: UpgradeId.GHOST_CLOAK,
            BuffId.HYDRALISK_LUNGE: UpgradeId.HYDRALISK_LUNGE,
            # On the parasited unit, which the game reports as its controller's while the parasite lasts.
            BuffId.INFESTOR_NEURAL_PARASITE: UpgradeId.NEURAL_PARASITE,
            BuffId.MARAUDER_STIMMED: UpgradeId.STIMPACK,
            BuffId.MARINE_STIMMED: UpgradeId.STIMPACK,
            BuffId.ZEALOT_CHARGING: UpgradeId.CHARGE,
        }
    )
    # Buffs whose wearer shows its owner's opponent has the upgrade the buff's ability needs. The mappings stay
    # separate: a dict cannot key on ids of two enums, since a buff id and a unit type id of one number are one key.
    _BUFF_EVIDENCE_OF_OPPONENT: Final[Mapping[BuffId, UpgradeId]] = MappingProxyType(
        {
            BuffId.MARAUDER_CONCUSSIVE_SHELLS_SLOW: UpgradeId.CONCUSSIVE_SHELLS,
            BuffId.RAVEN_INTERFERENCE_MATRIX: UpgradeId.INTERFERENCE_MATRIX,
        }
    )
    _EFFECT_EVIDENCE: Final[Mapping[EffectId, UpgradeId]] = MappingProxyType(
        {EffectId.HIGH_TEMPLAR_STORM: UpgradeId.STORM}
    )
    # A gateway becomes a warp gate on its own once Warp Gate is researched, and nothing else makes one.
    _UNIT_TYPE_EVIDENCE: Final[Mapping[UnitTypeId, UpgradeId]] = MappingProxyType(
        {UnitTypeId.WARP_GATE: UpgradeId.WARP_GATE}
    )
    # A unit's health without the one upgrade that raises it, measured in game: the game's tables hold no health.
    _HEALTH_EVIDENCE: Final[Mapping[UnitTypeId, tuple[float, UpgradeId]]] = MappingProxyType(
        {UnitTypeId.MARINE: (45.0, UpgradeId.COMBAT_SHIELD)}
    )

    def __init__(self, game_data: GameData) -> None:
        self._game_data = game_data
        self._lines: dict[UnitTypeId, dict[UpgradeType, tuple[UpgradeId, ...]]] = {}
        self._types: dict[UnitTypeId, frozenset[UpgradeId]] = {}

    # --- The levels a unit reports

    def read_basic_upgrades(self, units: Iterable[Unit[Any]]) -> frozenset[UpgradeId]:
        """The enemy's upgrades that its units in sight show through the levels they report."""
        shown: set[UpgradeId] = set()
        for unit in units:
            if unit.alliance is not Alliance.ENEMY or unit.visibility is not Visibility.IN_VISION:
                continue
            shown |= self._levels_of(
                unit.type_id, unit.attack_upgrade_level, unit.armor_upgrade_level, unit.shield_upgrade_level
            )
        return frozenset(shown)

    def _levels_of(self, unit_type: UnitTypeId, attack: int, armor: int, shield: int) -> frozenset[UpgradeId]:
        """The upgrades a unit of `unit_type` reporting these levels shows its owner has.

        A unit reports how many attack and shield levels it has, and how much armor its upgrades add. Each belongs to
        its owner and to the unit's own line: a marine at attack level 2 shows the first two Terran Infantry Weapons,
        which every infantry unit of that player has.
        """
        if not attack and not armor and not shield:
            return frozenset()
        lines = self._lines_of(unit_type)
        return frozenset(
            lines[UpgradeType.ATTACK][:attack] + lines[UpgradeType.ARMOR][:armor] + lines[UpgradeType.SHIELD][:shield]
        )

    def shields_of(self, unit_type: UnitTypeId) -> tuple[UpgradeId, ...]:
        """The shield upgrade line of `unit_type`, in level order. The levels its owner has are its shields' armor."""
        return self._lines_of(unit_type)[UpgradeType.SHIELD]

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

    # --- What only an upgrade can cause

    def read_intermediate_upgrades(self, units: Iterable[Unit[Any]], effects: Iterable[Effect]) -> frozenset[UpgradeId]:
        """The enemy's upgrades shown by its units and effects and by the buffs on this player's units, beyond the
        levels its units report."""
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
        """The upgrades `unit` shows its owner has: by its type, and while in sight, by its buffs and its health.

        A unit under a neural parasite shows only that its controller has Neural Parasite: its buffs and type belong to
        the player it was taken from.
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
        """The upgrades the buffs on `unit` show its owner's opponent has. Nothing for a unit out of sight."""
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
        """The upgrades a unit of `unit_type` cannot exist without: those every performer of its creation ability
        needs for it, as a burrowed zergling needs Burrow, plus those in `_UNIT_TYPE_EVIDENCE`."""
        if (upgrades := self._types.get(unit_type)) is None:
            upgrades = frozenset()
            if (ability := self._game_data.units[unit_type].creation_ability) is not None:
                needs = [
                    requirements.upgrades
                    for performer in self._game_data.abilities[ability].performers
                    if (requirements := self._game_data.units[performer].ability_requirements.get(ability)) is not None
                ]
                upgrades = frozenset.intersection(*needs) if needs else frozenset()
            if (only := self._UNIT_TYPE_EVIDENCE.get(unit_type)) is not None:
                upgrades |= {only}
            self._types[unit_type] = upgrades
        return upgrades
