"""What powers this player's structures."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Self, cast, final

from sc2nachos.geometry import Point

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any

    from s2clientprotocol import raw_pb2

    from sc2nachos.units import OwnUnit, Unit, UnitType


@final
@dataclass(frozen=True, slots=True)
class PowerSource:
    """One of this player's power sources, as the game reports them each observation: a finished pylon, with a radius
    of 6.5, or a warp prism in phasing mode, with 3.75 (in game). A pylon still being built powers nothing, nor does
    another player's, and a warp prism in transport mode is none."""

    unit: OwnUnit[UnitType.Pylon | UnitType.WarpPrismPhasing]
    """The pylon or the warp prism."""
    position: Point
    """The center of the ground it powers."""
    radius: float
    """How far it reaches from `position`: a structure whose center lies strictly closer may be powered, as
    `PlacementGrids.powered_for` says."""

    @classmethod
    def _from_proto(cls, proto: raw_pb2.PowerSource, unit_by_tag: Callable[[int], Unit[Any]]) -> Self:
        """Read a power source an observation reports, its unit found by `unit_by_tag`."""
        unit = cast("OwnUnit[UnitType.Pylon | UnitType.WarpPrismPhasing]", unit_by_tag(proto.tag))
        return cls(unit=unit, position=Point((proto.pos.x, proto.pos.y)), radius=proto.radius)
