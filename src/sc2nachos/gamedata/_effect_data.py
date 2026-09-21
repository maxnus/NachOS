"""What the game says about an effect."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Self, final

from sc2nachos.ids import EffectId

if TYPE_CHECKING:
    from s2clientprotocol import data_pb2


@final
@dataclass(frozen=True, slots=True)
class EffectData:
    """What the game says about one effect, which is a patch of ground something is happening on."""

    id: EffectId
    """Which effect this describes."""
    radius: float
    """How far it reaches from the point it is centered on."""

    @classmethod
    def from_proto(cls, data: data_pb2.EffectData) -> Self:
        """Read one effect out of the game's tables."""
        return cls(
            id=EffectId(data.effect_id),
            radius=data.radius,
        )
