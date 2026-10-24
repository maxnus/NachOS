"""What the game says about an upgrade."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Self, final

from sc2nachos._enum import ReadableIntEnum
from sc2nachos.gamedata._cost import Cost
from sc2nachos.ids import AbilityId, UpgradeId

if TYPE_CHECKING:
    from s2clientprotocol import data_pb2

    from sc2nachos.gamedata._techtree import TechTreeUpgrades


class UpgradeType(ReadableIntEnum):
    """The upgrade level a unit reports an upgrade adding to."""

    OTHER = 0
    """An upgrade a unit reports nothing of, such as Stimpack or Grooved Spines."""
    ATTACK = 1
    """`Unit.attack_upgrade_level`, a count of levels."""
    ARMOR = 2
    """`Unit.armor_upgrade_level`, the armor its upgrades add."""
    SHIELD = 3
    """`Unit.shield_upgrade_level`, a count of levels."""


@final
@dataclass(frozen=True, slots=True)
class UpgradeData:
    """One upgrade, as the game's table describes it."""

    id: UpgradeId
    """The upgrade described."""
    cost: Cost
    """The cost of researching it, its steps `research_steps`."""
    research_steps: int
    """Steps it takes to research: a whole number for every upgrade in the game's tables, and above 0 for every curated
    one (corpus)."""
    research_ability: AbilityId | None
    """The ability that researches it."""
    upgrade_type: UpgradeType
    """The upgrade level the units it affects report it adding to, or `OTHER` if they report nothing of it."""
    level: int
    """Its level within its line, from 1; 0 for an upgrade that is no level, such as Chitinous Plating."""

    @classmethod
    def _from_proto(cls, data: data_pb2.UpgradeData, tech_tree_upgrades: TechTreeUpgrades) -> Self:
        """Read one upgrade from the game's table, with what `tech_tree_upgrades` says about it."""
        upgrade = UpgradeId(data.upgrade_id)
        research_steps = round(data.research_time)
        return cls(
            id=upgrade,
            cost=Cost(data.mineral_cost, data.vespene_cost, steps=research_steps),
            research_steps=research_steps,
            research_ability=AbilityId.get(data.ability_id) or tech_tree_upgrades.research_abilities.get(upgrade),
            upgrade_type=tech_tree_upgrades.types.get(upgrade, UpgradeType.OTHER),
            level=tech_tree_upgrades.levels.get(upgrade, 0),
        )
