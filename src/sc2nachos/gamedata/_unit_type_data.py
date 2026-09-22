"""What the game's table says about a unit type."""

from __future__ import annotations

from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Self, final

from s2clientprotocol import data_pb2

from sc2nachos._enum import ReadableIntEnum
from sc2nachos.constants import FASTER_PER_NORMAL_SPEED, STEPS_PER_NORMAL_SECOND
from sc2nachos.gamedata._cost import Cost
from sc2nachos.gamedata._tech_requirements import TechRequirements
from sc2nachos.gamedata._unit_type_upgrade import UnitTypeUpgrade
from sc2nachos.ids import AbilityId, UnitTypeId, UpgradeId
from sc2nachos.match import Race

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from sc2nachos.gamedata._techtree import TechTree
    from sc2nachos.gamedata._unit_type_upgrade import WeaponUpgrade

# The change of an upgrade that changes nothing in the row, such as a shields level, which only raises a level the
# type's units report.
_NO_CHANGE = UnitTypeUpgrade()


class Attribute(ReadableIntEnum):
    """A property of a unit that weapons deal bonus damage against."""

    LIGHT = data_pb2.Attribute.Light
    ARMORED = data_pb2.Attribute.Armored
    BIOLOGICAL = data_pb2.Attribute.Biological
    MECHANICAL = data_pb2.Attribute.Mechanical
    ROBOTIC = data_pb2.Attribute.Robotic
    PSIONIC = data_pb2.Attribute.Psionic
    MASSIVE = data_pb2.Attribute.Massive
    STRUCTURE = data_pb2.Attribute.Structure
    HOVER = data_pb2.Attribute.Hover
    HEROIC = data_pb2.Attribute.Heroic
    SUMMONED = data_pb2.Attribute.Summoned


class TargetDomain(ReadableIntEnum):
    """Whether a weapon fires at ground, air or both."""

    GROUND = data_pb2.Weapon.TargetType.Ground
    AIR = data_pb2.Weapon.TargetType.Air
    ANY = data_pb2.Weapon.TargetType.Any


@final
@dataclass(frozen=True, slots=True)
class Weapon:
    """One of a unit type's attacks, before any upgrade unless `UnitTypeData.with_upgrades` made it."""

    target_domain: TargetDomain
    """Whether it can fire at ground, air or both."""
    damage: float
    """The damage of one hit, before the target's armor."""
    attacks: int
    """Hits per attack: two for a colossus."""
    range: float
    """Its range."""
    cooldown_steps: float
    """Steps between one attack and the next."""
    damage_bonuses: Mapping[Attribute, float]
    """The extra damage per hit against a target with each attribute."""

    @classmethod
    def _from_proto(cls, weapon: data_pb2.Weapon) -> Self:
        """Read one weapon from the game's table."""
        damage_bonuses = {Attribute(bonus.attribute): bonus.bonus for bonus in weapon.damage_bonus}
        return cls(
            target_domain=TargetDomain(weapon.type),
            damage=weapon.damage,
            attacks=weapon.attacks,
            range=weapon.range,
            # The game calls this the weapon's speed, though a larger value is a slower weapon, and gives it in
            # seconds at Normal speed, 16 steps a second.
            cooldown_steps=weapon.speed * STEPS_PER_NORMAL_SECOND,
            damage_bonuses=MappingProxyType(damage_bonuses),
        )

    def _with_weapon_upgrades(self, changes: Iterable[WeaponUpgrade]) -> Weapon:
        """This weapon with each of `changes` added."""
        damage, weapon_range = self.damage, self.range
        bonuses = dict(self.damage_bonuses)
        for change in changes:
            damage += change.damage
            weapon_range += change.range
            for attribute, bonus in change.damage_bonuses.items():
                bonuses[attribute] = bonuses.get(attribute, 0.0) + bonus
        return replace(self, damage=damage, range=weapon_range, damage_bonuses=MappingProxyType(bonuses))


@final
@dataclass(frozen=True, slots=True)
class UnitTypeData:
    """One unit type, before any upgrade unless `with_upgrades` made it."""

    id: UnitTypeId
    """The unit type described."""
    race: Race
    """The race it belongs to."""
    cost: Cost
    """Everything spent to reach this type, and the supply it takes: an orbital command is 550, the command center's
    400 included."""
    build_steps: float
    """Steps to make one; for a morph, only the last stage."""
    supply_provided: float
    """What it adds to the supply cap."""
    cargo_size: int
    """Slots it fills in a transport."""
    sight_range: float
    """Its sight range."""
    speed: float
    """Its movement speed, in distance per second at the game's Faster speed (22.4 steps); zero for a structure."""
    armor: float
    """The damage it takes off each hit."""
    attributes: frozenset[Attribute]
    """Its attributes, which weapons deal bonus damage against."""
    weapons: tuple[Weapon, ...]
    """Its attacks."""
    creation_ability: AbilityId | None
    """The ability that makes one, or `None` if nothing does."""
    morphed_from: UnitTypeId | None
    """The unit type used up to make one: a command center for an orbital command, a larva for a zergling, `None` for
    a marine."""
    ability_requirements: Mapping[AbilityId, TechRequirements]
    """The abilities a unit of this type can be offered, with the tech each needs first."""
    needs_power: bool
    """Whether it needs to be powered by a pylon or a warp prism."""
    tech_aliases: tuple[UnitTypeId, ...]
    """Other types that satisfy the same tech requirement: an orbital command counts as a command center."""
    base_type: UnitTypeId | None
    """The type this is a temporary form of: a siege tank, for a sieged tank."""
    has_minerals: bool
    """Whether minerals can be mined from it."""
    has_vespene: bool
    """Whether vespene can be mined from it."""
    upgrades: Mapping[UpgradeId, UnitTypeUpgrade]
    """Every upgrade that affects it, with what each adds to its weapons, armor and speed. An upgrade that only raises
    a level its units report, such as a shields level, adds nothing."""

    @property
    def abilities(self) -> frozenset[AbilityId]:
        """The abilities a unit of this type can be offered."""
        return frozenset(self.ability_requirements)

    def with_upgrades(self, upgrades: Iterable[UpgradeId]) -> UnitTypeData:
        """This type with `upgrades` researched: the weapons, armor and speed they change.

        Nothing else an upgrade does, such as attack speed, is applied. Anabolic Synthesis counts whether or not the
        unit is on creep, as in the game's rows.
        """
        changes = [self.upgrades[upgrade] for upgrade in sorted(set(upgrades) & self.upgrades.keys())]
        changes = [change for change in changes if change != _NO_CHANGE]
        if not changes:
            return self
        weapons = tuple(
            weapon._with_weapon_upgrades(change.weapons[index] for change in changes if change.weapons)
            for index, weapon in enumerate(self.weapons)
        )
        return replace(
            self,
            armor=self.armor + sum(change.armor for change in changes),
            speed=self.speed + sum(change.speed for change in changes),
            weapons=weapons,
        )

    @classmethod
    def _from_proto(cls, unit: data_pb2.UnitTypeData, tech_tree: TechTree) -> Self:
        """Read one unit type from the game's table, with what `tech_tree` found about it in game."""
        unit_type = UnitTypeId(unit.unit_id)
        return cls(
            id=unit_type,
            race=Race(unit.race),
            cost=Cost(unit.mineral_cost, unit.vespene_cost, unit.food_required),
            build_steps=unit.build_time,
            supply_provided=unit.food_provided,
            cargo_size=unit.cargo_size,
            sight_range=unit.sight_range,
            # The game gives it in distance per second at Normal speed, 16 steps a second.
            speed=unit.movement_speed * FASTER_PER_NORMAL_SPEED,
            armor=unit.armor,
            attributes=frozenset(Attribute(attribute) for attribute in unit.attributes),
            weapons=tuple(Weapon._from_proto(weapon) for weapon in unit.weapons),
            creation_ability=tech_tree.creation_abilities.get(unit_type),
            morphed_from=tech_tree.morph_sources.get(unit_type),
            ability_requirements=tech_tree.ability_requirements.get(unit_type, MappingProxyType({})),
            needs_power=unit_type in tech_tree.power_consumers,
            # An uncurated alias is dropped: a viking's names an empty row that no unit is ever an instance of.
            tech_aliases=tuple(filter(None, (UnitTypeId.get(alias) for alias in unit.tech_alias))),
            # The game calls this the morphed variant, though it names the type morphed from.
            base_type=UnitTypeId.get(unit.unit_alias),
            has_minerals=unit.has_minerals,
            has_vespene=unit.has_vespene,
            upgrades=tech_tree.unit_type_upgrades.get(unit_type, MappingProxyType({})),
        )
