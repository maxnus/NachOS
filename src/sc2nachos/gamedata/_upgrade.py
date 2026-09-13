"""What the game says about an upgrade."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Self, final

from sc2nachos.gamedata._resources import Resources
from sc2nachos.ids import AbilityId, UpgradeId

if TYPE_CHECKING:
    from s2clientprotocol import data_pb2


@final
@dataclass(frozen=True, slots=True)
class UpgradeData:
    """What the game says about one upgrade."""

    id: UpgradeId
    """Which upgrade this describes."""
    cost: Resources
    """What researching it takes."""
    research_steps: float
    """Steps it takes to research."""
    research_ability: AbilityId | None
    """The ability that researches it."""

    @classmethod
    def from_proto(cls, data: data_pb2.UpgradeData) -> Self:
        """Read one upgrade out of the game's tables."""
        return cls(
            id=UpgradeId(data.upgrade_id),
            cost=Resources(data.mineral_cost, data.vespene_cost),
            research_steps=data.research_time,
            research_ability=AbilityId.get(data.ability_id),
        )
