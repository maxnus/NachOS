"""Game identifiers.

The curated enums here are the public API. `sc2nachos.ids.raw` holds the complete generated catalog
they are defined from.
"""

from sc2nachos.ids._uncurated import UncuratedIdError
from sc2nachos.ids.ability import AbilityId
from sc2nachos.ids.buff import BuffId
from sc2nachos.ids.effect import EffectId
from sc2nachos.ids.unit_type import UnitTypeId
from sc2nachos.ids.upgrade import UpgradeId

__all__ = [
    "AbilityId",
    "BuffId",
    "EffectId",
    "UncuratedIdError",
    "UnitTypeId",
    "UpgradeId",
]
