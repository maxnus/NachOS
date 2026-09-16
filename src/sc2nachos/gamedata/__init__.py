"""The tables a game is played by."""

from sc2nachos.gamedata._ability import AbilityData, TargetType
from sc2nachos.gamedata._effect import EffectData
from sc2nachos.gamedata._gamedata import GameData
from sc2nachos.gamedata._resources import Resources
from sc2nachos.gamedata._tech_requirements import TechRequirements
from sc2nachos.gamedata._unit_type_upgrade import UnitTypeUpgrade, WeaponUpgrade
from sc2nachos.gamedata._unittype import Attribute, TargetDomain, UnitTypeData, Weapon
from sc2nachos.gamedata._upgrade import UpgradeData, UpgradeType

__all__ = [
    "AbilityData",
    "Attribute",
    "EffectData",
    "GameData",
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
