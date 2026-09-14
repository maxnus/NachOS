"""What an ability leaves on the ground for a while."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Self, final

from sc2nachos.geometry import Point
from sc2nachos.ids import EffectId
from sc2nachos.units import Alliance

if TYPE_CHECKING:
    from s2clientprotocol import raw_pb2


@final
@dataclass(frozen=True, slots=True)
class Effect:
    """What an ability leaves on the ground for a while, such as a storm or a corrosive bile about to land.

    Force fields and reaper grenades are units, not effects, as the game reports them.
    """

    id: EffectId
    positions: tuple[Point, ...]
    """Where it is: one point, or several along the line an effect such as a lurker's spines covers."""
    radius: float
    """How far from each of its positions it reaches."""
    alliance: Alliance
    """Whose side the player it belongs to is on."""
    owner_id: int
    """The id of the player it belongs to."""

    @classmethod
    def from_proto(cls, proto: raw_pb2.Effect) -> Self:
        """Read an effect an observation reports. Raises `UncuratedIdError` for an effect the curated ids leave out."""
        return cls(
            id=EffectId.read(proto.effect_id),
            positions=tuple(Point((position.x, position.y)) for position in proto.pos),
            radius=proto.radius,
            alliance=Alliance(proto.alliance),
            owner_id=proto.owner,
        )
