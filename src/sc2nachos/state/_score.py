"""The score the game keeps for a player."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Self, final

from sc2nachos.constants import STEPS_PER_NORMAL_SECOND
from sc2nachos.gamedata import Resources

if TYPE_CHECKING:
    from s2clientprotocol import score_pb2


@final
@dataclass(frozen=True, slots=True)
class CategoryScore:
    """An amount split by what it went to."""

    none: float
    """What the game's tables give no category."""
    army: float
    """Military units, not workers."""
    economy: float
    """Townhalls, supply structures, gas buildings and workers."""
    technology: float
    """Structures that produce units or research, such as a barracks or an engineering bay."""
    upgrade: float
    """Upgrades, such as the warp gate or weapons."""

    @property
    def total(self) -> float:
        """The amount across every category."""
        return self.none + self.army + self.economy + self.technology + self.upgrade

    @classmethod
    def _from_proto(cls, proto: score_pb2.CategoryScoreDetails) -> Self:
        return cls(proto.none, proto.army, proto.economy, proto.technology, proto.upgrade)


@final
@dataclass(frozen=True, slots=True)
class VitalScore:
    """An amount of damage or healing, split by what it took from or gave back."""

    life: float
    shields: float
    energy: float

    @property
    def total(self) -> float:
        """The amount across life, shields and energy."""
        return self.life + self.shields + self.energy

    @classmethod
    def _from_proto(cls, proto: score_pb2.VitalScoreDetails) -> Self:
        return cls(proto.life, proto.shields, proto.energy)


@final
@dataclass(frozen=True, slots=True)
class ValueScore:
    """What units and structures cost, in minerals and vespene together."""

    units: float
    structures: float

    @property
    def total(self) -> float:
        """The value of the units and the structures together."""
        return self.units + self.structures


@final
@dataclass(frozen=True, slots=True)
class Score:
    """The score the game keeps for this player, as it stands in the last observation.

    The game's recent APM is left out, since it reads zero in a game played through the raw interface.
    """

    score: int
    """The score the game shows at the end of a game: what the units and structures, finished or not, are worth,
    and the resources in the bank."""
    idle_production_steps: int
    """The steps structures able to produce have spent producing nothing, summed over the structures."""
    idle_worker_steps: int
    """The steps workers have spent neither mining nor building, summed over the workers."""
    total_value: ValueScore
    """The value of the finished units and structures."""
    killed_value: ValueScore
    """The value of the opponent's units and structures this player destroyed."""
    collected: Resources
    collection_rate: Resources
    """What the current income brings in a minute."""
    spent: Resources
    """What was spent, counted when an order is queued and taken back when it is cancelled."""
    food_used: CategoryScore
    """The supply in use."""
    killed_minerals: CategoryScore
    """The minerals the opponent's units and structures this player destroyed cost."""
    killed_vespene: CategoryScore
    """The vespene the opponent's units and structures this player destroyed cost."""
    lost_minerals: CategoryScore
    """The minerals this player's units and structures that were destroyed cost."""
    lost_vespene: CategoryScore
    """The vespene this player's units and structures that were destroyed cost."""
    friendly_fire_minerals: CategoryScore
    """The minerals this player's units and structures it destroyed itself cost."""
    friendly_fire_vespene: CategoryScore
    """The vespene this player's units and structures it destroyed itself cost."""
    used_minerals: CategoryScore
    """The minerals this player's units, structures and upgrades cost, less those destroyed."""
    used_vespene: CategoryScore
    """The vespene this player's units, structures and upgrades cost, less those destroyed."""
    total_used_minerals: CategoryScore
    """The minerals this player's units, structures and upgrades have cost over the game, those destroyed included."""
    total_used_vespene: CategoryScore
    """The vespene this player's units, structures and upgrades have cost over the game, those destroyed included."""
    total_damage_dealt: VitalScore
    """The damage dealt to the opponent."""
    total_damage_taken: VitalScore
    """The damage this player's units and structures have taken."""
    total_healed: VitalScore
    """What this player has healed and repaired."""

    @classmethod
    def _from_proto(cls, proto: score_pb2.Score) -> Self:
        """Read the score an observation reports."""
        details = proto.score_details
        category = CategoryScore._from_proto
        vital = VitalScore._from_proto
        return cls(
            score=proto.score,
            # Counted in seconds of the game's Normal speed, always whole steps (corpus).
            idle_production_steps=round(details.idle_production_time * STEPS_PER_NORMAL_SECOND),
            idle_worker_steps=round(details.idle_worker_time * STEPS_PER_NORMAL_SECOND),
            total_value=ValueScore(details.total_value_units, details.total_value_structures),
            killed_value=ValueScore(details.killed_value_units, details.killed_value_structures),
            collected=Resources(details.collected_minerals, details.collected_vespene),
            collection_rate=Resources(details.collection_rate_minerals, details.collection_rate_vespene),
            spent=Resources(details.spent_minerals, details.spent_vespene),
            food_used=category(details.food_used),
            killed_minerals=category(details.killed_minerals),
            killed_vespene=category(details.killed_vespene),
            lost_minerals=category(details.lost_minerals),
            lost_vespene=category(details.lost_vespene),
            friendly_fire_minerals=category(details.friendly_fire_minerals),
            friendly_fire_vespene=category(details.friendly_fire_vespene),
            used_minerals=category(details.used_minerals),
            used_vespene=category(details.used_vespene),
            total_used_minerals=category(details.total_used_minerals),
            total_used_vespene=category(details.total_used_vespene),
            total_damage_dealt=vital(details.total_damage_dealt),
            total_damage_taken=vital(details.total_damage_taken),
            total_healed=vital(details.total_healed),
        )
