"""What the game's table says about a unit type."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Self, final

from s2clientprotocol import data_pb2

from sc2nachos._enum import ReadableIntEnum
from sc2nachos.constants import FASTER_PER_NORMAL_SPEED, STEPS_PER_NORMAL_SECOND, STEPS_PER_SECOND
from sc2nachos.gamedata._cost import Cost
from sc2nachos.gamedata._footprints import CREEP_REQUIREMENTS, FOOTPRINTS
from sc2nachos.gamedata._tech_requirements import TechRequirements
from sc2nachos.gamedata._techtree._overrides import PRODUCERS_FALLBACK
from sc2nachos.gamedata._unit_type_upgrade import UnitTypeUpgrade, WeaponUpgrade
from sc2nachos.ids import AbilityId, UnitTypeId, UpgradeId
from sc2nachos.match import Race

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Mapping

    from sc2nachos.gamedata._creep_requirement import CreepRequirement
    from sc2nachos.gamedata._techtree import TechTreeUnitTypes
    from sc2nachos.geometry import Footprint

# The change of an upgrade that changes nothing in the row, such as a shields level, which only raises a level the
# type's units report.
_NO_CHANGE = UnitTypeUpgrade()
# What adds nothing to a weapon, from which the changes to one are summed.
_NO_WEAPON_CHANGE = WeaponUpgrade()

# Each attack adds to the tables' cooldown a delay of -1 + 3r/4096 steps, r drawn evenly from 0 to 4095 (in game).
_MEAN_ATTACK_DELAY_STEPS = -1 + 3 * (4095 / 2) / 4096
# The least a hit takes off health, whatever the armor (in game).
_MINIMUM_DAMAGE = 0.5


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
    base_cooldown_steps: float
    """The steps from one attack to the next before the random delay the game adds to each: the tables' cooldown,
    shortened by an upgrade that speeds the attack."""
    damage_bonuses: Mapping[Attribute, float]
    """The extra damage per hit against a target with each attribute."""
    ignores_armor: bool = False
    """Whether a hit takes off its whole damage, whatever the target's armor and shield armor: an oracle's beam's (in
    game)."""
    mean_delay_steps: float = _MEAN_ATTACK_DELAY_STEPS
    """The mean of the random delay the game adds to each attack: 0.4996 steps for a weapon from the tables, whose delay
    runs from -1 to 2, and nothing for the weapons measured for the types the tables give none (in game)."""
    turns_to_fire: bool = True
    """Whether its unit must face a target to fire it, as most do, to within a degree; false for one that fires at any
    angle off its facing, such as a battlecruiser's or a planetary fortress's, though a turret the game does not report
    may still take some steps to come round (in game; `docs/reference/game-behavior/damage-and-buffs.md`). True for a
    weapon never seen to fire, such as a sieged liberator's."""

    @property
    def average_cooldown_steps(self) -> float:
        """The steps from one attack to the next on average, the random delay counted. A thor's and an ultralisk's
        attacks were not seen to keep to it. A unit's own `weapon_cooldown_steps` counts down the delay drawn for its
        last attack."""
        return self.base_cooldown_steps + self.mean_delay_steps

    @property
    def dps(self) -> float:
        """Damage per second of real time at the game's Faster speed: every hit of an attack, each
        `average_cooldown_steps`, against a target with no armor and no attribute it has a bonus against."""
        return self.damage * self.attacks * STEPS_PER_SECOND / self.average_cooldown_steps

    def damage_against(
        self, *, attributes: Collection[Attribute], armor: float, shield_armor: float = 0.0, shield: float = 0.0
    ) -> float:
        """What one attack takes off a target with these attributes, armor and shield, its shield and health together.

        Each hit takes off the damage and its bonus against the target's attributes: from the shield first, less
        `shield_armor`, and what is more than the shield has left goes on to health less `armor`, at nothing at least;
        from health alone, less `armor`, but at least 0.5 (in game). A weapon that `ignores_armor` takes off the whole
        hit. Each hit of an attack of several finds the shield the one before left. The damage may come to more than
        the target has left.
        """
        bonus_damage = sum(bonus for attribute, bonus in self.damage_bonuses.items() if attribute in attributes)
        hit_damage = self.damage + bonus_damage
        if self.ignores_armor:
            armor = shield_armor = 0.0
        damage_taken = 0.0
        for _ in range(self.attacks):
            if shield <= 0:
                damage_taken += max(hit_damage - armor, _MINIMUM_DAMAGE)
                continue
            # Taken to be at least 0.5, as off health: no hit seen was small enough to tell.
            off_shield = max(hit_damage - shield_armor, _MINIMUM_DAMAGE)
            if off_shield <= shield:
                damage_taken += off_shield
                shield -= off_shield
            else:
                damage_taken += shield + max(off_shield - shield - armor, 0.0)
                shield = 0.0
        return damage_taken

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
            base_cooldown_steps=weapon.speed * STEPS_PER_NORMAL_SECOND,
            damage_bonuses=MappingProxyType(damage_bonuses),
        )

    def _with_weapon_upgrade(self, change: WeaponUpgrade) -> Weapon:
        """This weapon with `change` added, and its base cooldown divided by the change's attack speed multiplier:
        what speeds an attack leaves the random delay as it is (in game)."""
        bonuses = dict(self.damage_bonuses)
        for attribute, bonus in change.damage_bonuses.items():
            bonuses[attribute] = bonuses.get(attribute, 0.0) + bonus
        return replace(
            self,
            damage=self.damage + change.damage,
            range=self.range + change.range,
            base_cooldown_steps=self.base_cooldown_steps / change.attack_speed_multiplier,
            damage_bonuses=MappingProxyType(bonuses),
        )


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
    400 included. Its steps are the build steps of each stage that costs something: an orbital command's 2160 are the
    command center's 1600 and its own 560, and a sieged tank's or a warp gate's are the tank's 720 or the gateway's
    1040, since the switch costs nothing."""
    build_steps: int
    """Steps to make one: the tables' build time rounded up to a whole step, as a train takes it, a void ray's 963.2
    as 964 (in game); for a morph, only the last stage, which a unit's morph does not keep to, a lurker's taking 403 of
    554 (in game; `docs/reference/game-behavior/upgrades-and-tables.md`). A type the table gives none takes the build
    time of the one other type its creation ability makes: a rich gas building its plain one's."""
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
    """Its attacks: the tables', or for the battlecruiser, the oracle, the void ray and the sentry, which the tables
    give none, the attacks `tools/sweep_combat.py` measured. An oracle's fires only while its beam is on."""
    creation_ability: AbilityId | None
    """The ability that makes one, or `None` if nothing does."""
    producers: frozenset[UnitTypeId]
    """The unit types that make one: a gateway and a warp gate, for a zealot; a high and a dark templar, for an archon.
    Empty for a type nothing is recorded making, such as a larva, a MULE or an interceptor."""
    morphed_from: UnitTypeId | None
    """The unit type used up to make one: a command center for an orbital command, a larva for a zergling, `None` for
    a marine."""
    ability_requirements: Mapping[AbilityId, TechRequirements]
    """The abilities a unit of this type can be offered, with the tech each needs first."""
    needs_power: bool
    """Whether it needs to be powered by a pylon or a warp prism."""
    creep_requirement: CreepRequirement | None
    """Whether a structure of this type may stand on creep, as the game answered its placement on creep and off it,
    for every type a unit places at a point; `None` for any other type, such as a gas building, which goes on a
    geyser, an add-on, or a form a structure morphs into."""
    tech_aliases: tuple[UnitTypeId, ...]
    """Other types that satisfy the same tech requirement: an orbital command counts as a command center."""
    has_minerals: bool
    """Whether minerals can be mined from it."""
    has_vespene: bool
    """Whether vespene can be mined from it."""
    footprint: Footprint | None
    """The tiles one covers where it stands, as measured in game, for every structure type that stands and every type
    of the map's own. `None` for any other type, such as a unit, an egg or a force field, and for the few the game would
    not create to be measured: the acceleration zones and the flying inhibitor zones."""
    upgrades: Mapping[UpgradeId, UnitTypeUpgrade]
    """Every upgrade that affects it, with what each adds to its weapons, their attack speed, its armor and its speed.
    An upgrade that only raises a level its units report, such as a shields level, adds nothing."""
    turn_rate: float | None = None
    """The most it turns in a step, in radians: a whole number of 1/4096 of a full turn, 711 for a marine (62.49
    degrees) and 512 for a thor (45) (in game). A turn takes its angle over this, rounded up, in steps, give or take
    one, from the step it begins. `None` for a type whose facing the game never changes, such as a colossus or an
    immortal, and for one that does not move (`docs/reference/game-behavior/units.md`)."""
    moves_while_turning: bool | None = None
    """Whether a unit of this type moves while it still turns, from the step after its turn begins, as most do; false
    for a thor, an ultralisk and a siege tank, which move only once they face where they go (in game). `None` wherever
    `turn_rate` is."""

    @property
    def abilities(self) -> frozenset[AbilityId]:
        """The abilities a unit of this type can be offered."""
        return frozenset(self.ability_requirements)

    def with_upgrades(self, upgrades: Iterable[UpgradeId]) -> UnitTypeData:
        """This type with `upgrades` researched: the weapons, their attack speed, the armor and the speed they change.

        Nothing else an upgrade does is applied. Anabolic Synthesis counts whether or not the unit is on creep, as in
        the game's rows.
        """
        changes = [self.upgrades[upgrade] for upgrade in sorted(set(upgrades) & self.upgrades.keys())]
        changes = [change for change in changes if change != _NO_CHANGE]
        if not changes:
            return self
        weapons = tuple(
            weapon._with_weapon_upgrade(
                sum((change.weapons[index] for change in changes if change.weapons), _NO_WEAPON_CHANGE)
            )
            for index, weapon in enumerate(self.weapons)
        )
        return replace(
            self,
            armor=self.armor + sum(change.armor for change in changes),
            speed=self.speed + sum(change.speed for change in changes),
            weapons=weapons,
        )

    @classmethod
    def _from_proto(cls, unit: data_pb2.UnitTypeData, tech_tree_unit_types: TechTreeUnitTypes) -> Self:
        """Read one unit type from the game's table, with what `tech_tree_unit_types` says about it."""
        unit_type = UnitTypeId(unit.unit_id)
        return cls(
            id=unit_type,
            race=Race(unit.race),
            cost=Cost(unit.mineral_cost, unit.vespene_cost, unit.food_required),
            build_steps=math.ceil(unit.build_time),
            supply_provided=unit.food_provided,
            cargo_size=unit.cargo_size,
            sight_range=unit.sight_range,
            # The game gives it in distance per second at Normal speed, 16 steps a second.
            speed=unit.movement_speed * FASTER_PER_NORMAL_SPEED,
            armor=unit.armor,
            attributes=frozenset(Attribute(attribute) for attribute in unit.attributes),
            weapons=tuple(Weapon._from_proto(weapon) for weapon in unit.weapons),
            creation_ability=tech_tree_unit_types.creation_abilities.get(unit_type),
            producers=tech_tree_unit_types.producers.get(unit_type) or PRODUCERS_FALLBACK.get(unit_type, frozenset()),
            morphed_from=tech_tree_unit_types.morph_sources.get(unit_type),
            ability_requirements=tech_tree_unit_types.ability_requirements.get(unit_type, MappingProxyType({})),
            needs_power=unit_type in tech_tree_unit_types.power_consumers,
            creep_requirement=CREEP_REQUIREMENTS.get(unit_type),
            # An uncurated alias is dropped: a viking's names an empty row that no unit is ever an instance of.
            tech_aliases=tuple(filter(None, (UnitTypeId.get(alias) for alias in unit.tech_alias))),
            has_minerals=unit.has_minerals,
            has_vespene=unit.has_vespene,
            footprint=FOOTPRINTS.get(unit_type),
            upgrades=tech_tree_unit_types.upgrades.get(unit_type, MappingProxyType({})),
        )
