"""What the game's tables leave out about how unit types and abilities relate, as it was found in game."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, final

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sc2nachos.gamedata._tech_requirements import TechRequirements
    from sc2nachos.gamedata._unit_type_upgrade import UnitTypeUpgrade
    from sc2nachos.gamedata._upgrade import UpgradeType
    from sc2nachos.ids import AbilityId, UnitTypeId, UpgradeId


@final
@dataclass(frozen=True, slots=True)
class TechTree:
    """How unit types, abilities and upgrades relate on one build of the game, as the sweeps in `tools` found it."""

    base_build: int
    """The build of the game swept."""
    ability_requirements: Mapping[UnitTypeId, Mapping[AbilityId, TechRequirements]]
    """Each ability each unit type can be offered, with what must stand or be researched first."""
    ability_remaps: Mapping[AbilityId, AbilityId]
    """The general ability each ability stands for."""
    creation_abilities: Mapping[UnitTypeId, AbilityId]
    """The ability that makes each unit type."""
    ability_products: Mapping[AbilityId, UnitTypeId | UpgradeId]
    """The unit type or upgrade each ability makes."""
    morph_sources: Mapping[UnitTypeId, UnitTypeId]
    """The unit type used up to make each unit type made out of another."""
    power_consumers: frozenset[UnitTypeId]
    """The unit types that need to be powered by a pylon or a warp prism."""
    unit_type_upgrades: Mapping[UnitTypeId, Mapping[UpgradeId, UnitTypeUpgrade]]
    """Every upgrade that affects each unit type, with what it adds to the type's weapons, armor and speed, as
    `tools/sweep_upgrades.py` found it."""
    upgrade_types: Mapping[UpgradeId, UpgradeType]
    """The kind of each upgrade a unit reports something of: the upgrade level it adds to. The rest are `OTHER`, and
    left out."""
    upgrade_levels: Mapping[UpgradeId, int]
    """Which level of its line each leveled upgrade is."""
    ability_performers: Mapping[AbilityId, frozenset[UnitTypeId]] = field(init=False)
    """The unit types that perform each ability. A general ability's performers are those of the abilities that stand
    for it."""

    def __post_init__(self) -> None:
        performers: defaultdict[AbilityId, set[UnitTypeId]] = defaultdict(set)
        for unit_type, abilities in self.ability_requirements.items():
            for ability in abilities:
                performers[ability].add(unit_type)
        for exact, general in self.ability_remaps.items():
            performers[general] |= performers.get(exact, set())
        by_ability = {ability: frozenset(unit_types) for ability, unit_types in performers.items() if unit_types}
        object.__setattr__(self, "ability_performers", MappingProxyType(by_ability))
