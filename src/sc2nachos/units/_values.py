"""The small value types a unit's report is read into."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Self, final

from s2clientprotocol import raw_pb2

from sc2nachos._enum import ReadableIntEnum
from sc2nachos.geometry import Point
from sc2nachos.ids import AbilityId, UnitTypeId

if TYPE_CHECKING:
    from collections.abc import Callable

    from s2clientprotocol import common_pb2

    from sc2nachos.units._unit import Unit


class Alliance(ReadableIntEnum):
    """Whose side a unit is on, from this player's point of view."""

    OWN = raw_pb2.Alliance.Self
    ALLY = raw_pb2.Alliance.Ally
    NEUTRAL = raw_pb2.Alliance.Neutral
    ENEMY = raw_pb2.Alliance.Enemy


class Visibility(ReadableIntEnum):
    """How much of a unit this player can see."""

    IN_VISION = raw_pb2.DisplayType.Visible
    """In sight."""
    IN_FOG = raw_pb2.DisplayType.Snapshot
    """A structure out of sight, remembered where it was last seen."""
    INVISIBLE = raw_pb2.DisplayType.Hidden
    """In sight, but cloaked and undetected. A burrowed unit nothing detects is not listed at all (in game)."""


class CloakState(ReadableIntEnum):
    """Whether a unit is cloaked, and whether this player sees through it."""

    UNKNOWN = raw_pb2.CloakState.CloakedUnknown
    """A remembered unit, so whether it is cloaked is not known."""
    CLOAKED = raw_pb2.CloakState.Cloaked
    """Cloaked, and nothing of this player's detects it."""
    CLOAKED_DETECTED = raw_pb2.CloakState.CloakedDetected
    """Cloaked and detected."""
    NOT_CLOAKED = raw_pb2.CloakState.NotCloaked
    """Not cloaked."""
    CLOAKED_ALLIED = raw_pb2.CloakState.CloakedAllied
    """Cloaked, but on this player's side, so seen anyway."""


class VitalType(Enum):
    """A vital of a unit, health, shields or energy, as an amount or as a fraction of its maximum.

    A unit without shields or energy, whose maximum is 0, has neither to read.
    """

    HEALTH = "health"
    SHIELD = "shield"
    LIFE = "life"
    """Health and shields together."""
    ENERGY = "energy"
    HEALTH_FRACTION = "health fraction"
    SHIELD_FRACTION = "shield fraction"
    LIFE_FRACTION = "life fraction"
    """Health and shields together, as a fraction of their combined maximum."""
    ENERGY_FRACTION = "energy fraction"

    @property
    def is_fraction(self) -> bool:
        """Whether the vital is a fraction of its maximum, from 0 to 1."""
        return self.value.endswith("fraction")


# The tag a rally holds once the unit it was set on is gone, as a mined-out mineral field is (corpus). No unit is ever
# reported under it.
_NO_UNIT = 1 << 32


type Target = Point | Unit[Any]
"""What an order or a rally point is aimed at: a position on the ground, or a unit."""


def _target_point(point: common_pb2.Point) -> Point:
    """The ground position an order or rally point names. The game reports a height of zero for every one."""
    return Point((point.x, point.y))


@final
@dataclass(frozen=True, slots=True)
class UnitOrder:
    """An order a unit is carrying out or has queued, as the unit reports it."""

    ability: AbilityId
    """The ability ordered."""
    target: Target | None
    """The point or unit the order is aimed at, or `None` for an order that takes neither."""
    progress: float
    """The order's progress from 0 to 1 for a train or a research, and 0 otherwise."""

    @classmethod
    def _from_proto(cls, order: raw_pb2.UnitOrder, unit_by_tag: Callable[[int], Unit[Any]]) -> Self:
        """Read an order a unit reported, looking up a unit target through `unit_by_tag`."""
        match order.WhichOneof("target"):
            case "target_world_space_pos":
                target: Target | None = _target_point(order.target_world_space_pos)
            case "target_unit_tag":
                target = unit_by_tag(order.target_unit_tag)
            case _:
                target = None
        return cls(AbilityId.read(order.ability_id), target, order.progress)


@final
@dataclass(frozen=True, slots=True)
class RallyTarget:
    """Where a structure sends what it makes."""

    target: Target
    """The unit rallied to, or the point: a spot on the ground, or where a unit now gone stood."""

    @classmethod
    def _from_proto(cls, rally: raw_pb2.RallyTarget, unit_by_tag: Callable[[int], Unit[Any]]) -> Self:
        """Read a rally point a structure reported, looking up a unit target through `unit_by_tag`."""
        tag = rally.tag
        if rally.HasField("tag") and tag != _NO_UNIT:
            return cls(unit_by_tag(tag))
        return cls(_target_point(rally.point))


@final
@dataclass(frozen=True, slots=True)
class Passenger:
    """A unit inside a transport, bunker or other carrier, as the carrier reports it."""

    unit: Unit[Any]
    """The unit itself. It is stale while inside, and reads as it was when it went in."""
    type_id: UnitTypeId
    health: float
    health_max: float
    shield: float
    shield_max: float
    energy: float
    energy_max: float

    @classmethod
    def _from_proto(cls, passenger: raw_pb2.PassengerUnit, unit_by_tag: Callable[[int], Unit[Any]]) -> Self:
        """Read a passenger a carrier reported, looking up the unit through `unit_by_tag`."""
        return cls(
            unit=unit_by_tag(passenger.tag),
            type_id=UnitTypeId.read(passenger.unit_type),
            health=passenger.health,
            health_max=passenger.health_max,
            shield=passenger.shield,
            shield_max=passenger.shield_max,
            energy=passenger.energy,
            energy_max=passenger.energy_max,
        )
