"""What the game says about an ability."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Self, final

from s2clientprotocol import data_pb2

from sc2nachos._enum import ReadableIntEnum
from sc2nachos.ids import AbilityId


class TargetType(ReadableIntEnum):
    """What an ability must be aimed at."""

    # The game calls this one `None`, which no expression can spell, so it is read off the descriptor.
    NOTHING = data_pb2.AbilityData.Target.Value("None")
    POINT = data_pb2.AbilityData.Target.Point
    UNIT = data_pb2.AbilityData.Target.Unit
    POINT_OR_UNIT = data_pb2.AbilityData.Target.PointOrUnit
    POINT_OR_NOTHING = data_pb2.AbilityData.Target.PointOrNone


@final
@dataclass(frozen=True, slots=True)
class AbilityData:
    """What the game says about one ability, and what it takes to order one."""

    id: AbilityId
    """Which ability this describes."""
    target_type: TargetType
    """What must be supplied to order it."""
    cast_range: float
    """How far it reaches, and zero where it has no range of its own."""
    footprint_radius: float | None
    """How far from the point ordered must be clear, and `None` where nothing is placed: half a structure's
    width, and for an add-on the reach past the far side of the structure it attaches to."""
    needs_placement: bool
    """Whether ordering it puts a structure on the ground."""
    allows_autocast: bool
    """Whether it can be left to fire on its own."""
    remaps_to: AbilityId | None
    """The general ability this one stands for: a unit reports the exact id, and either can be ordered."""

    @classmethod
    def from_proto(cls, data: data_pb2.AbilityData) -> Self:
        """Read one ability out of the game's tables."""
        return cls(
            id=AbilityId(data.ability_id),
            target_type=TargetType(data.target),
            cast_range=data.cast_range,
            footprint_radius=data.footprint_radius if data.HasField("footprint_radius") else None,
            needs_placement=data.is_building,
            allows_autocast=data.allow_autocast,
            remaps_to=AbilityId.get(data.remaps_to_ability_id),
        )
