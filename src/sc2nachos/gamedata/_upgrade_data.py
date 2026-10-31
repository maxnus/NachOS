"""What the game says about an upgrade."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Self, final

from sc2nachos._enum import ReadableIntEnum
from sc2nachos.game_time import GameTime
from sc2nachos.gamedata._cost import Cost
from sc2nachos.gamedata._tech_requirements import TechRequirements
from sc2nachos.ids import AbilityId, UnitTypeId, UpgradeId

if TYPE_CHECKING:
    from s2clientprotocol import data_pb2

    from sc2nachos.gamedata._techtree import TechTree


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
    """The cost of researching it. Its time is the research time, a whole number of steps for every upgrade in the
    game's tables and above 0 for every curated one (corpus), which a research takes exactly (in game)."""
    research_ability: AbilityId | None
    """The ability that researches it."""
    researchers: frozenset[UnitTypeId]
    """The structure types whose `research_ability` researches it: an engineering bay for infantry weapons, a spire and
    a greater spire for flyer attacks. Empty for an upgrade nothing researches."""
    tech_requirements: TechRequirements
    """What a researcher needs before it is offered `research_ability`: `Tech.ARMORY` and level 1 for infantry weapons
    level 2. Every researcher of an upgrade needs the same (tool `sweep_tech_tree`)."""
    upgrade_type: UpgradeType
    """The upgrade level the units it affects report it adding to, or `OTHER` if they report nothing of it."""
    level: int
    """Its level within its line, from 1; 0 for an upgrade that is no level, such as Chitinous Plating."""

    @classmethod
    def _from_proto(cls, data: data_pb2.UpgradeData, tech_tree: TechTree) -> Self:
        """Read one upgrade from the game's table, with what `tech_tree` says about it."""
        upgrade = UpgradeId(data.upgrade_id)
        in_tree = tech_tree.upgrade(upgrade)
        return cls(
            id=upgrade,
            cost=Cost(data.mineral_cost, data.vespene_cost, time=GameTime(steps=round(data.research_time))),
            research_ability=AbilityId.get(data.ability_id) or in_tree.research_ability,
            researchers=in_tree.researchers,
            tech_requirements=in_tree.tech_requirements,
            upgrade_type=in_tree.upgrade_type,
            level=in_tree.level,
        )
