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
    """One effect: a patch of ground something is happening on, as the game's table describes it."""

    id: EffectId
    """The effect described."""
    radius: float
    """Its radius around its center."""

    @classmethod
    def _from_proto(cls, data: data_pb2.EffectData) -> Self:
        """Read one effect from the game's table."""
        return cls(
            id=EffectId(data.effect_id),
            radius=data.radius,
        )
