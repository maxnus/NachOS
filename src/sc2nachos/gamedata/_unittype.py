"""What the game says about a type of unit."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Self, final

from s2clientprotocol import data_pb2

from sc2nachos._enum import ReadableIntEnum
from sc2nachos.gamedata._resources import Resources
from sc2nachos.ids import AbilityId, UnitTypeId
from sc2nachos.match import Race

if TYPE_CHECKING:
    from collections.abc import Mapping


# A game second at Normal speed; ladder games run at Faster, which is 22.4 steps a real second.
_STEPS_PER_NORMAL_SECOND = 16


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
    """Where the things a weapon can be fired at stand."""

    GROUND = data_pb2.Weapon.TargetType.Ground
    AIR = data_pb2.Weapon.TargetType.Air
    ANY = data_pb2.Weapon.TargetType.Any


@final
@dataclass(frozen=True, slots=True)
class Weapon:
    """One of a unit's attacks, as it stands before any upgrade."""

    target_domain: TargetDomain
    """Where the things it can be fired at stand, rather than what it is firing at."""
    damage: float
    """What one hit takes off, before the target's armor."""
    attacks: int
    """Hits landed per attack, which is two for a colossus."""
    range: float
    """How far it reaches."""
    cooldown_steps: float
    """Steps between one attack and the next."""
    damage_bonuses: Mapping[Attribute, float]
    """What each attribute the target has adds to the damage of a hit."""

    @classmethod
    def from_proto(cls, weapon: data_pb2.Weapon) -> Self:
        """Read one weapon out of the game's tables."""
        damage_bonuses = {Attribute(bonus.attribute): bonus.bonus for bonus in weapon.damage_bonus}
        return cls(
            target_domain=TargetDomain(weapon.type),
            damage=weapon.damage,
            attacks=weapon.attacks,
            range=weapon.range,
            # The game calls this the weapon's speed, though a longer one means a slower weapon, and gives it in
            # seconds of the game's Normal speed, which runs 16 steps a second.
            cooldown_steps=weapon.speed * _STEPS_PER_NORMAL_SECOND,
            damage_bonuses=MappingProxyType(damage_bonuses),
        )


@final
@dataclass(frozen=True, slots=True)
class UnitTypeData:
    """What the game says about one type of unit, as it stands before any upgrade."""

    id: UnitTypeId
    """Which type of unit this describes."""
    race: Race
    """The race it belongs to."""
    cost: Resources
    """Everything spent to reach this type: an orbital command is 550, the command center's 400 included."""
    build_steps: float
    """Steps to make one, counting only the last stage where it morphs from another type."""
    supply_cost: float
    """What it takes of the supply cap."""
    supply_provided: float
    """What it adds to the supply cap."""
    cargo_size: int
    """Slots it fills in a transport."""
    sight_range: float
    """How far it reveals."""
    speed: float
    """How fast it moves, and zero for a structure."""
    armor: float
    """What it takes off each hit it receives."""
    attributes: frozenset[Attribute]
    """What it is made of, which weapons earn bonus damage against."""
    weapons: tuple[Weapon, ...]
    """Every attack it carries."""
    creation_ability: AbilityId | None
    """The ability that makes one, or `None` where the game names one it no longer honors."""
    tech_requirement: UnitTypeId | None
    """The structure that must stand before one can be made."""
    tech_requirement_attached: bool
    """Whether that structure must be an add-on on the one making it, as a tech lab is."""
    tech_aliases: tuple[UnitTypeId, ...]
    """Other types that satisfy the same tech requirement, an orbital command counting as a command center."""
    base_type: UnitTypeId | None
    """The type this is a temporary form of, a sieged tank's being the siege tank."""
    has_minerals: bool
    """Whether minerals can be mined from it."""
    has_vespene: bool
    """Whether vespene can be mined from it."""

    @classmethod
    def from_proto(cls, unit: data_pb2.UnitTypeData) -> Self:
        """Read one unit type out of the game's tables."""
        return cls(
            id=UnitTypeId(unit.unit_id),
            race=Race(unit.race),
            cost=Resources(unit.mineral_cost, unit.vespene_cost),
            build_steps=unit.build_time,
            supply_cost=unit.food_required,
            supply_provided=unit.food_provided,
            cargo_size=unit.cargo_size,
            sight_range=unit.sight_range,
            speed=unit.movement_speed,
            armor=unit.armor,
            attributes=frozenset(Attribute(attribute) for attribute in unit.attributes),
            weapons=tuple(Weapon.from_proto(weapon) for weapon in unit.weapons),
            creation_ability=AbilityId.get(unit.ability_id),
            tech_requirement=UnitTypeId.get(unit.tech_requirement),
            tech_requirement_attached=unit.require_attached,
            # An alias the curated ids leave out is dropped: a viking's names an empty row nothing is ever one of.
            tech_aliases=tuple(filter(None, (UnitTypeId.get(alias) for alias in unit.tech_alias))),
            # The game calls this the morphed variant, though it names the type morphed from.
            base_type=UnitTypeId.get(unit.unit_alias),
            has_minerals=unit.has_minerals,
            has_vespene=unit.has_vespene,
        )
