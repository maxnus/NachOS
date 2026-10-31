"""How unit types, abilities and upgrades relate, swept in game where the game's tables leave it out."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, final

from sc2nachos.gamedata._tech_requirements import TechRequirements
from sc2nachos.gamedata._upgrade_data import UpgradeType

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sc2nachos.gamedata._unit_type_upgrade import UnitTypeUpgrade
    from sc2nachos.ids import AbilityId, UnitTypeId, UpgradeId


@final
@dataclass(frozen=True, slots=True)
class TechTreeUnitType:
    """What the tech tree says about one unit type. What it says nothing about is at its default."""

    creation_ability: AbilityId | None = None
    """The ability that makes it."""
    producers: frozenset[UnitTypeId] = frozenset()
    """The unit types that make it, and for a type whose creation ability names another product, the types that carry
    that ability out: an SCV makes a rich refinery, which a refinery's build puts up on a rich geyser."""
    tech_requirements: TechRequirements = TechRequirements()
    """What it needs first: every tech, tech lab and upgrade each type making it needs for the ability it makes it by,
    together."""
    morphed_from: UnitTypeId | None = None
    """The unit type it is morphed from, if it is morphed from another."""
    ability_requirements: Mapping[AbilityId, TechRequirements] = MappingProxyType({})
    """The abilities it can be offered, with the tech each needs first. An action several types perform is under its one
    id, though the game offers each type its own."""
    needs_power: bool = False
    """Whether it needs to be powered by a pylon or a warp prism."""
    upgrades: Mapping[UpgradeId, UnitTypeUpgrade] = MappingProxyType({})
    """Every upgrade that affects it, with what it adds to its weapons, armor and speed, as `tools/sweep_upgrades.py`
    found it."""


@final
@dataclass(frozen=True, slots=True)
class TechTreeAbility:
    """What the tech tree says about one ability. What it says nothing about is at its default."""

    performers: frozenset[UnitTypeId] = frozenset()
    """The unit types offered it."""
    products: Mapping[UnitTypeId, UnitTypeId | UpgradeId] = MappingProxyType({})
    """The unit type or upgrade it makes, by the unit type that carries it out: a barracks lifts into a flying barracks
    and a starport into a flying starport. A structure that turns itself into another, as a gateway becomes a warp gate,
    carries it out though it is offered nothing."""
    cancelled_by: AbilityId | None = None
    """The cancel a structure is offered while it morphs, builds an add-on or arms a nuke by it. It depends on the
    product: a command center morphing to an orbital command is offered a different cancel from one morphing to a
    planetary fortress."""


@final
@dataclass(frozen=True, slots=True)
class TechTreeUpgrade:
    """What the tech tree says about one upgrade. What it says nothing about is at its default."""

    research_ability: AbilityId | None = None
    """The ability that researches it."""
    researchers: frozenset[UnitTypeId] = frozenset()
    """The unit types whose research ability researches it."""
    tech_requirements: TechRequirements = TechRequirements()
    """What it needs first: every tech, tech lab and upgrade each of its researchers needs for its research,
    together."""
    upgrade_type: UpgradeType = UpgradeType.OTHER
    """The upgrade level it adds to, for an upgrade a unit reports; `OTHER` for the rest."""
    level: int = 0
    """Its level within its line, for a leveled upgrade."""


@final
@dataclass(frozen=True, slots=True)
class TechTree:
    """How unit types, abilities and upgrades relate on one build of the game, as the sweeps in `tools` found it."""

    base_build: int
    """The game build that was swept."""
    unit_types: Mapping[UnitTypeId, TechTreeUnitType]
    """What it says about each unit type it says anything about."""
    abilities: Mapping[AbilityId, TechTreeAbility]
    """What it says about each ability it says anything about."""
    upgrades: Mapping[UpgradeId, TechTreeUpgrade]
    """What it says about each upgrade it says anything about."""

    def unit_type(self, unit_type: UnitTypeId) -> TechTreeUnitType:
        """What it says about `unit_type`: nothing, for a type it holds no record of."""
        return self.unit_types.get(unit_type, _UNIT_TYPE_NOT_IN_THE_TREE)

    def ability(self, ability: AbilityId) -> TechTreeAbility:
        """What it says about `ability`: nothing, for an ability it holds no record of."""
        return self.abilities.get(ability, _ABILITY_NOT_IN_THE_TREE)

    def upgrade(self, upgrade: UpgradeId) -> TechTreeUpgrade:
        """What it says about `upgrade`: nothing, for an upgrade it holds no record of."""
        return self.upgrades.get(upgrade, _UPGRADE_NOT_IN_THE_TREE)


# What the tech tree says about an id it holds no record of.
_UNIT_TYPE_NOT_IN_THE_TREE = TechTreeUnitType()
_ABILITY_NOT_IN_THE_TREE = TechTreeAbility()
_UPGRADE_NOT_IN_THE_TREE = TechTreeUpgrade()
