"""The tables a game is played by."""

from sc2nachos.gamedata._ability_data import AbilityData, OrderBehavior, TargetType
from sc2nachos.gamedata._cost import Cost
from sc2nachos.gamedata._effect_data import EffectData
from sc2nachos.gamedata._game_data import GameData
from sc2nachos.gamedata._resources import Resources
from sc2nachos.gamedata._tech_requirements import TechRequirements
from sc2nachos.gamedata._unit_type_data import Attribute, TargetDomain, UnitTypeData, Weapon
from sc2nachos.gamedata._unit_type_upgrade import UnitTypeUpgrade, WeaponUpgrade
from sc2nachos.gamedata._upgrade_data import UpgradeData, UpgradeType

__all__ = [
    "AbilityData",
    "Attribute",
    "Cost",
    "EffectData",
    "GameData",
    "OrderBehavior",
    "TechRequirements",
    "Resources",
    "TargetDomain",
    "TargetType",
    "UnitTypeData",
    "UnitTypeUpgrade",
    "UpgradeData",
    "UpgradeType",
    "Weapon",
    "WeaponUpgrade",
]
