"""What the game's tables leave out of attacks, as `tools/sweep_combat.py` measured it on build 75689 (in game).
Hand-written, never generated; `docs/reference/game-behavior/damage-and-buffs.md` has what was seen.

`with_missing_attacks` gives a type these weapons, with their upgrades, only where the tables give it none, so
`tests/test_gamedata.py` fails once the tables give one of these types a weapon, and the entry can go.
"""

from collections.abc import Mapping
from dataclasses import replace
from types import MappingProxyType
from typing import Final

from sc2nachos.game_time import GameTime
from sc2nachos.gamedata._unit_type_data import Attribute, TargetDomain, UnitTypeData, Weapon
from sc2nachos.gamedata._unit_type_upgrade import UnitTypeUpgrade, WeaponUpgrade
from sc2nachos.ids import BuffId, UnitTypeId, UpgradeId

_NO_BONUS: Final[Mapping[Attribute, float]] = MappingProxyType({})

MISSING_WEAPONS: Final[Mapping[UnitTypeId, tuple[Weapon, ...]]] = MappingProxyType(
    {
        # Its shots come 4, 3, 4, 4 and 3 steps apart, over and over (in game).
        UnitTypeId.BATTLECRUISER: (
            Weapon(
                TargetDomain.GROUND,
                damage=8.0,
                attacks=1,
                range=6.0,
                base_cooldown=GameTime(steps=3.6),
                damage_bonuses=_NO_BONUS,
                mean_delay=GameTime.zero,
            ),
            Weapon(
                TargetDomain.AIR,
                damage=5.0,
                attacks=1,
                range=6.0,
                base_cooldown=GameTime(steps=3.6),
                damage_bonuses=_NO_BONUS,
                mean_delay=GameTime.zero,
            ),
        ),
        # Its beam, which fires only while it is on, and at nothing in the air (in game).
        UnitTypeId.ORACLE: (
            Weapon(
                TargetDomain.GROUND,
                damage=15.0,
                attacks=1,
                range=4.0,
                base_cooldown=GameTime(steps=14.0),
                damage_bonuses=MappingProxyType({Attribute.LIGHT: 7.0}),
                ignores_armor=True,
                mean_delay=GameTime.zero,
            ),
        ),
        UnitTypeId.VOID_RAY: (
            Weapon(
                TargetDomain.ANY,
                damage=6.0,
                attacks=1,
                range=6.0,
                base_cooldown=GameTime(steps=8.0),
                damage_bonuses=MappingProxyType({Attribute.ARMORED: 4.0}),
                mean_delay=GameTime.zero,
            ),
        ),
        UnitTypeId.SENTRY: (
            Weapon(
                TargetDomain.ANY,
                damage=6.0,
                attacks=1,
                range=5.0,
                base_cooldown=GameTime(steps=16.0),
                damage_bonuses=_NO_BONUS,
                mean_delay=GameTime.zero,
            ),
        ),
    }
)
"""The weapons of the types that attack though the tables give them none. Their shots come with no random delay (in
game), so each base cooldown is the gap between them, and the mean delay nothing."""


def _create_weapon_upgrades(
    levels: tuple[UpgradeId, ...], weapon_count: int, damage: float = 1.0
) -> Mapping[UpgradeId, UnitTypeUpgrade]:
    """Each of `levels` adding `damage` to each of `weapon_count` weapons, and nothing to a bonus."""
    change = UnitTypeUpgrade(weapons=(WeaponUpgrade(damage=damage),) * weapon_count)
    return MappingProxyType(dict.fromkeys(levels, change))


_MISSING_WEAPON_UPGRADES: Final[Mapping[UnitTypeId, Mapping[UpgradeId, UnitTypeUpgrade]]] = MappingProxyType(
    {
        UnitTypeId.BATTLECRUISER: _create_weapon_upgrades(
            (UpgradeId.TERRAN_SHIP_WEAPONS_1, UpgradeId.TERRAN_SHIP_WEAPONS_2, UpgradeId.TERRAN_SHIP_WEAPONS_3), 2
        ),
        UnitTypeId.VOID_RAY: _create_weapon_upgrades(
            (UpgradeId.PROTOSS_AIR_WEAPONS_1, UpgradeId.PROTOSS_AIR_WEAPONS_2, UpgradeId.PROTOSS_AIR_WEAPONS_3), 1
        ),
        UnitTypeId.SENTRY: _create_weapon_upgrades(
            (
                UpgradeId.PROTOSS_GROUND_WEAPONS_1,
                UpgradeId.PROTOSS_GROUND_WEAPONS_2,
                UpgradeId.PROTOSS_GROUND_WEAPONS_3,
            ),
            1,
        ),
    }
)
"""What the attack levels add to `MISSING_WEAPONS`: 1 to each hit's damage, none to a bonus, and nothing to an
oracle's beam (in game)."""

_ATTACK_SPEED_UPGRADES: Final[Mapping[UnitTypeId, Mapping[UpgradeId, float]]] = MappingProxyType(
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


def with_missing_attacks(units: Mapping[UnitTypeId, UnitTypeData]) -> Mapping[UnitTypeId, UnitTypeData]:
    """`units`, where a type the tables give no weapon takes the weapons `MISSING_WEAPONS` has for it, with what its
    upgrades add to them, and a type an upgrade attacks faster takes that upgrade's attack speed multiplier on each of
    its weapons."""
    rows = dict(units)
    for unit_type, weapons in MISSING_WEAPONS.items():
        if (row := rows.get(unit_type)) is not None and not row.weapons:
            upgrades = {**row.upgrades, **_MISSING_WEAPON_UPGRADES.get(unit_type, {})}
            rows[unit_type] = replace(row, weapons=weapons, upgrades=MappingProxyType(upgrades))
    for unit_type, multipliers in _ATTACK_SPEED_UPGRADES.items():
        if (row := rows.get(unit_type)) is None or not row.weapons:
            continue
        upgrades = dict(row.upgrades)
        for upgrade, attack_speed_multiplier in multipliers.items():
            change = upgrades.get(upgrade, UnitTypeUpgrade())
            weapon_changes = change.weapons or (WeaponUpgrade(),) * len(row.weapons)
            weapon_changes = tuple(
                replace(weapon_change, attack_speed_multiplier=attack_speed_multiplier)
                for weapon_change in weapon_changes
            )
            upgrades[upgrade] = replace(change, weapons=weapon_changes)
        rows[unit_type] = replace(row, upgrades=MappingProxyType(upgrades))
    return MappingProxyType(rows)
