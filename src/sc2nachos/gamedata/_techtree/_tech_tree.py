"""How unit types, abilities and upgrades relate, swept in game where the game's tables leave it out."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, final

from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sc2nachos.gamedata._tech_requirements import TechRequirements
    from sc2nachos.gamedata._unit_type_upgrade import UnitTypeUpgrade
    from sc2nachos.gamedata._upgrade_data import UpgradeType
    from sc2nachos.ids import AbilityId, UnitTypeId, UpgradeId


@final
@dataclass(frozen=True, slots=True)
class TechTreeUnitTypes:
    """What the tech tree says about each unit type."""

    ability_requirements: Mapping[UnitTypeId, Mapping[AbilityId, TechRequirements]]
    """The abilities each unit type can be offered, with the tech each needs first. An action several types perform is
    under its one id, though the game offers each type its own."""
    creation_abilities: Mapping[UnitTypeId, AbilityId]
    """The ability that makes each unit type."""
    producers: Mapping[UnitTypeId, frozenset[UnitTypeId]]
    """The unit types that make each unit type, and for a type whose creation ability names another product, the types
    that carry that ability out: an SCV makes a rich refinery, which a refinery's build puts up on a rich geyser."""
    tech_requirements: Mapping[UnitTypeId, TechRequirements]
    """What each unit type needs first: every tech, tech lab and upgrade each type making it needs for the ability it
    makes it by, together. A type that needs nothing is left out."""
    morph_sources: Mapping[UnitTypeId, UnitTypeId]
    """The source type of each unit type morphed from another."""
    power_consumers: frozenset[UnitTypeId]
    """The unit types that need to be powered by a pylon or a warp prism."""
    upgrades: Mapping[UnitTypeId, Mapping[UpgradeId, UnitTypeUpgrade]]
    """Every upgrade that affects each unit type, with what it adds to the type's weapons, armor and speed, as
    `tools/sweep_upgrades.py` found it."""


@final
@dataclass(frozen=True, slots=True)
class TechTreeAbilities:
    """What the tech tree says about each ability."""

    performers: Mapping[AbilityId, frozenset[UnitTypeId]]
    """The unit types offered each ability."""
    products: Mapping[AbilityId, Mapping[UnitTypeId, UnitTypeId | UpgradeId]]
    """The unit type or upgrade each ability makes, by the unit type that carries it out: a barracks lifts into a
    flying barracks and a starport into a flying starport. A structure that turns itself into another, as a gateway
    becomes a warp gate, carries it out though it is offered nothing."""
    cancels: Mapping[AbilityId, AbilityId]
    """The cancel a structure is offered while it morphs, builds an add-on or arms a nuke. It depends on the product: a
    command center morphing to an orbital command is offered a different cancel from one morphing to a planetary
    fortress."""


@final
@dataclass(frozen=True, slots=True)
class TechTreeUpgrades:
    """What the tech tree says about each upgrade."""

    research_abilities: Mapping[UpgradeId, AbilityId]
    """The ability that researches each upgrade."""
    researchers: Mapping[UpgradeId, frozenset[UnitTypeId]]
    """The unit types whose research ability researches each upgrade."""
    tech_requirements: Mapping[UpgradeId, TechRequirements]
    """What each upgrade needs first: every tech, tech lab and upgrade each of its researchers needs for its research,
    together. An upgrade that needs nothing is left out."""
    types: Mapping[UpgradeId, UpgradeType]
    """The upgrade level each upgrade adds to, for the upgrades a unit reports. The rest are `OTHER` and left out."""
    levels: Mapping[UpgradeId, int]
    """The level of each leveled upgrade within its line."""


@final
@dataclass(frozen=True, slots=True)
class TechTree:
    """How unit types, abilities and upgrades relate on one build of the game, as the sweeps in `tools` found it."""

    base_build: int
    """The game build that was swept."""
    unit_types: TechTreeUnitTypes
    """What it says about each unit type."""
    abilities: TechTreeAbilities
    """What it says about each ability."""
    upgrades: TechTreeUpgrades
    """What it says about each upgrade."""

    def warn_if_not_swept_on(self, base_build: int) -> None:
        """Log a warning if `base_build`, the build a game plays on, is not the one swept."""
        if base_build != self.base_build:
            logger.warning(
                "Playing build {}, but the tech tree is build {}'s: what each ability needs, who is offered it, how it "
                "takes a unit's orders and what each upgrade changes come from that build",
                base_build,
                self.base_build,
            )
