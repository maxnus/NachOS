"""How unit types, abilities and upgrades relate, swept in game where the game's tables leave it out."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, final

from sc2nachos.ids import UpgradeId

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sc2nachos.gamedata._tech_requirements import TechRequirements
    from sc2nachos.gamedata._unit_type_upgrade import UnitTypeUpgrade
    from sc2nachos.gamedata._upgrade_data import UpgradeType
    from sc2nachos.ids import AbilityId, UnitTypeId


@final
@dataclass(frozen=True, slots=True)
class TechTree:
    """How unit types, abilities and upgrades relate on one build of the game, as the sweeps in `tools` found it."""

    base_build: int
    """The game build that was swept."""
    ability_requirements: Mapping[UnitTypeId, Mapping[AbilityId, TechRequirements]]
    """The abilities each unit type can be offered, with the tech each needs first."""
    ability_remaps: Mapping[AbilityId, AbilityId]
    """The general ability each exact ability remaps to."""
    ability_cancels: Mapping[AbilityId, AbilityId]
    """The cancel a structure is offered while it morphs, builds an add-on or arms a nuke. It depends on the product: a
    command center morphing to an orbital command is offered a different cancel from one morphing to a planetary
    fortress."""
    creation_abilities: Mapping[UnitTypeId, AbilityId]
    """The ability that makes each unit type."""
    ability_products: Mapping[AbilityId, UnitTypeId | UpgradeId]
    """The unit type or upgrade each ability makes."""
    morph_sources: Mapping[UnitTypeId, UnitTypeId]
    """The source type of each unit type morphed from another."""
    power_consumers: frozenset[UnitTypeId]
    """The unit types that need to be powered by a pylon or a warp prism."""
    unit_type_upgrades: Mapping[UnitTypeId, Mapping[UpgradeId, UnitTypeUpgrade]]
    """Every upgrade that affects each unit type, with what it adds to the type's weapons, armor and speed, as
    `tools/sweep_upgrades.py` found it."""
    upgrade_types: Mapping[UpgradeId, UpgradeType]
    """The upgrade level each upgrade adds to, for the upgrades a unit reports. The rest are `OTHER` and left out."""
    upgrade_levels: Mapping[UpgradeId, int]
    """The level of each leveled upgrade within its line."""
    ability_performers: Mapping[AbilityId, frozenset[UnitTypeId]] = field(init=False)
    """The unit types offered each ability. A general ability's performers are those of the exact abilities that remap
    to it."""
    research_abilities: Mapping[UpgradeId, AbilityId] = field(init=False)
    """The ability that researches each upgrade, inverted from `ability_products`."""

    def __post_init__(self) -> None:
        performers: defaultdict[AbilityId, set[UnitTypeId]] = defaultdict(set)
        for unit_type, abilities in self.ability_requirements.items():
            for ability in abilities:
                performers[ability].add(unit_type)
        for exact, general in self.ability_remaps.items():
            performers[general] |= performers.get(exact, set())
        by_ability = {ability: frozenset(unit_types) for ability, unit_types in performers.items() if unit_types}
        object.__setattr__(self, "ability_performers", MappingProxyType(by_ability))
        researched: dict[UpgradeId, AbilityId] = {}
        for ability, product in self.ability_products.items():
            if isinstance(product, UpgradeId):
                researched[product] = ability
        object.__setattr__(self, "research_abilities", MappingProxyType(researched))
