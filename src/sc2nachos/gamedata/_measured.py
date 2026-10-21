"""What the game's tables leave out of attacks, as `tools/sweep_combat.py` measured it on build 75689 (in game).
Hand-written, never generated; `docs/reference/game-behavior/damage-and-buffs.md` has what was seen.

`GameData` gives a type these weapons, with their upgrades, only where the tables give it none, so
`tests/test_gamedata.py` fails once the tables give one of these types a weapon, and the entry can go.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from sc2nachos.gamedata._unit_type_data import Attribute, TargetDomain, Weapon
from sc2nachos.gamedata._unit_type_upgrade import UnitTypeUpgrade, WeaponUpgrade
from sc2nachos.ids import BuffId, UnitTypeId, UpgradeId

_NO_BONUS: Final[Mapping[Attribute, float]] = MappingProxyType({})

MEASURED_WEAPONS: Final[Mapping[UnitTypeId, tuple[Weapon, ...]]] = MappingProxyType(
    {
        # Its shots come 4, 3, 4, 4 and 3 steps apart, over and over (in game).
        UnitTypeId.BATTLECRUISER: (
            Weapon(TargetDomain.GROUND, damage=8.0, attacks=1, range=6.0, cooldown_steps=3.6, damage_bonuses=_NO_BONUS),
            Weapon(TargetDomain.AIR, damage=5.0, attacks=1, range=6.0, cooldown_steps=3.6, damage_bonuses=_NO_BONUS),
        ),
        # Its beam, which fires only while it is on, and at nothing in the air (in game).
        UnitTypeId.ORACLE: (
            Weapon(
                TargetDomain.GROUND,
                damage=15.0,
                attacks=1,
                range=4.0,
                cooldown_steps=14.0,
                damage_bonuses=MappingProxyType({Attribute.LIGHT: 7.0}),
                ignores_armor=True,
            ),
        ),
        UnitTypeId.VOID_RAY: (
            Weapon(
                TargetDomain.ANY,
                damage=6.0,
                attacks=1,
                range=6.0,
                cooldown_steps=8.0,
                damage_bonuses=MappingProxyType({Attribute.ARMORED: 4.0}),
            ),
        ),
        UnitTypeId.SENTRY: (
            Weapon(TargetDomain.ANY, damage=6.0, attacks=1, range=5.0, cooldown_steps=16.0, damage_bonuses=_NO_BONUS),
        ),
    }
)
"""The weapons of the types that attack though the tables give them none. Their shots come with no random delay (in
game), so each cooldown is the gap between them, with nothing added."""


def _one_damage_a_level(levels: tuple[UpgradeId, ...], weapon_count: int) -> Mapping[UpgradeId, UnitTypeUpgrade]:
    """Each of `levels` adding 1 to the damage of each of `weapon_count` weapons, and nothing to a bonus."""
    return MappingProxyType(dict.fromkeys(levels, UnitTypeUpgrade(weapons=(WeaponUpgrade(damage=1.0),) * weapon_count)))


MEASURED_WEAPON_UPGRADES: Final[Mapping[UnitTypeId, Mapping[UpgradeId, UnitTypeUpgrade]]] = MappingProxyType(
    {
        UnitTypeId.BATTLECRUISER: _one_damage_a_level(
            (UpgradeId.TERRAN_SHIP_WEAPONS_1, UpgradeId.TERRAN_SHIP_WEAPONS_2, UpgradeId.TERRAN_SHIP_WEAPONS_3), 2
        ),
        UnitTypeId.VOID_RAY: _one_damage_a_level(
            (UpgradeId.PROTOSS_AIR_WEAPONS_1, UpgradeId.PROTOSS_AIR_WEAPONS_2, UpgradeId.PROTOSS_AIR_WEAPONS_3), 1
        ),
        UnitTypeId.SENTRY: _one_damage_a_level(
            (
                UpgradeId.PROTOSS_GROUND_WEAPONS_1,
                UpgradeId.PROTOSS_GROUND_WEAPONS_2,
                UpgradeId.PROTOSS_GROUND_WEAPONS_3,
            ),
            1,
        ),
    }
)
"""What the attack levels add to `MEASURED_WEAPONS`: 1 to each hit's damage, none to a bonus, and nothing to an
oracle's beam (in game)."""

ATTACK_SPEED_UPGRADES: Final[Mapping[UnitTypeId, Mapping[UpgradeId, float]]] = MappingProxyType(
    {
        UnitTypeId.ZERGLING: MappingProxyType({UpgradeId.ADRENAL_GLANDS: 1.4}),
        UnitTypeId.ADEPT: MappingProxyType({UpgradeId.GLAIVES: 1.45}),
    }
)
"""How many times as often each upgrade has a type's weapons attack, which the tables leave out (in game)."""

ATTACK_SPEED_BUFFS: Final[Mapping[BuffId, float]] = MappingProxyType(
    {
        BuffId.MARINE_STIMMED: 1.5,
        BuffId.MARAUDER_STIMMED: 1.5,
    }
)
"""How many times as often each buff has its wearer's weapons attack (in game)."""
