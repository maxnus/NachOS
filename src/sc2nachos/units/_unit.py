"""A unit of the game, as last reported."""

from __future__ import annotations

from typing import TYPE_CHECKING

from s2clientprotocol import raw_pb2

from sc2nachos.constants import STEPS_PER_SECOND
from sc2nachos.gamedata import Attribute
from sc2nachos.geometry import Point
from sc2nachos.ids import BuffId, UncuratedIdError, UnitTypeId
from sc2nachos.ids._uncurated import read_id
from sc2nachos.units._errors import NotReportedError
from sc2nachos.units._kind import Kind
from sc2nachos.units._values import Alliance, CloakState, Visibility

if TYPE_CHECKING:
    from sc2nachos.gamedata import UnitTypeData
    from sc2nachos.units._tracker import _UnitTracker

_VISIBLE = raw_pb2.DisplayType.Visible
_REMEMBERED = raw_pb2.DisplayType.Snapshot
_MINE = raw_pb2.Alliance.Self
# Looked up by the protocol's value, which is quicker than calling the enum. A placeholder has no tag, so it is never
# read into a unit.
_VISIBILITIES = {int(member): member for member in Visibility}
_ALLIANCES = {int(member): member for member in Alliance}
_CLOAKS = {int(member): member for member in CloakState}


def _copy(proto: raw_pb2.Unit) -> raw_pb2.Unit:
    """A message of its own holding what `proto` holds, which keeps no observation alive."""
    copy = raw_pb2.Unit()
    copy.CopyFrom(proto)
    return copy


class Unit[K: Kind]:
    """A unit of the game, one object under one id for the whole game, reading as the game last reported it.

    Where it is, what type it is and how it is seen read as of the last observation that held it. Everything else
    reads as of the last observation that showed it in sight, `last_seen`, and raises `NotReportedError` for a unit
    never shown in sight. A structure out of sight stays in the observation as `REMEMBERED`. A unit that leaves the
    observation is stale, keeps what it last read, and is observed again under the same id if it comes back.
    """

    # `OwnUnit` declares none, so that a unit can change between the two classes in place.
    __slots__ = (
        "__weakref__",
        "_dead",
        "_id",
        "_last_seen",
        "_mine",
        "_now",
        "_position",
        "_previous_position",
        "_previous_step",
        "_raw_type",
        "_seen",
        "_stale",
        "_step",
        "_tag",
        "_tracker",
        "_type_id",
    )

    _tracker: _UnitTracker
    _id: int
    _tag: int
    _now: raw_pb2.Unit
    _seen: raw_pb2.Unit | None
    _last_seen: int | None
    _raw_type: int
    _type_id: UnitTypeId | None
    _position: Point
    _mine: bool
    _step: int
    _previous_position: Point | None
    _previous_step: int
    _stale: bool
    _dead: bool

    def __init__(self, proto: raw_pb2.Unit, tracker: _UnitTracker, unit_id: int, step: int) -> None:
        self._tracker = tracker
        self._id = unit_id
        self._tag = proto.tag
        self._now = proto
        self._seen = proto if proto.display_type == _VISIBLE else None
        # Kept only for a unit out of sight; one in sight was last seen at its last observation.
        self._last_seen = None
        self._position = Point((proto.pos.x, proto.pos.y))
        self._mine = proto.alliance == _MINE
        self._step = step
        self._previous_position = None
        self._previous_step = step
        self._stale = False
        self._dead = False
        self._retype(proto.unit_type)

    def _observe(self, proto: raw_pb2.Unit, step: int) -> None:
        """Read what the observation at `step` reports of this unit, under the tag it had last time.

        The tracker sets `_tag` itself when the unit is observed under another.
        """
        position = self._position
        last = self._step
        if self._stale:
            # Back in the observation: how it moved while away is not known.
            self._stale = False
            self._previous_position = None
        elif step != last:
            self._previous_position = position
            self._previous_step = last
        self._step = step
        reported = proto.pos
        x, y = reported.x, reported.y
        # Most units stand still, and comparing is quicker than building another point.
        if x != position[0] or y != position[1]:
            self._position = Point((x, y))
        if proto.display_type == _VISIBLE:
            self._seen = proto
        elif self._seen is self._now:
            # Out of sight from now on: what was last seen outlives the observation it came in.
            self._seen = _copy(self._now)
            self._last_seen = last
        self._now = proto
        if proto.unit_type != self._raw_type:
            self._retype(proto.unit_type)
        if (proto.alliance == _MINE) is not self._mine:
            self._realign()

    def _leave(self) -> None:
        """Be stale: out of the observation, keeping what it last read without keeping that observation alive."""
        self._stale = True
        now = self._now
        copy = _copy(now)
        if self._seen is now:
            self._seen = copy
        self._now = copy

    def _die(self) -> None:
        """Be dead, as the game reported: out of the observation for good."""
        if not self._stale:
            self._leave()
        self._dead = True

    def _retype(self, raw_type: int) -> None:
        """Become a unit of `raw_type`: the one place a unit's type is set, on creation and at every morph."""
        self._raw_type = raw_type
        try:
            self._type_id = UnitTypeId(raw_type)
        except ValueError:
            # Raised when read, so a unit nobody asks about crashes nothing.
            self._type_id = None

    def _realign(self) -> None:
        """Change sides, and so class: a neural parasite takes an enemy unit over, and lets go of it again."""
        from sc2nachos.units._own_unit import OwnUnit

        self._mine = not self._mine
        self.__class__ = OwnUnit if self._mine else Unit  # pyright: ignore[reportAttributeAccessIssue]

    def _never_seen(self, what: str) -> NotReportedError:
        """The error for reading `what` of a unit the game has never shown in sight."""
        return NotReportedError(f"the game never showed {self!r} in sight, so it never reported its {what}")

    def __repr__(self) -> str:
        name = self._type_id.name if self._type_id is not None else f"uncurated type {self._raw_type}"
        state = ", dead" if self._dead else ", stale" if self._stale else ""
        return f"{type(self).__name__}({name}, id={self._id}, at {self._position}{state})"

    # Identity.

    @property
    def id(self) -> int:
        """NachOS's id for this unit, the same for the whole game.

        Its first digit is the unit's alliance when first seen, `Alliance.MINE` being 1 and `Alliance.ENEMY` 4, and
        the rest counts that alliance's units in the order they were first seen.
        """
        return self._id

    @property
    def tag(self) -> int:
        """The game's current handle for this unit, which changes each time a structure goes out of sight."""
        return self._tag

    @property
    def type_id(self) -> UnitTypeId:
        """What type of unit this is. Raises `UncuratedIdError` for a type the curated ids leave out."""
        if (type_id := self._type_id) is None:
            raise UncuratedIdError(UnitTypeId, self._raw_type)
        return type_id

    @property
    def alliance(self) -> Alliance:
        """Whose side it is on."""
        return _ALLIANCES[self._now.alliance]

    @property
    def owner(self) -> int:
        """The id of the player it belongs to, which is 16 for the map's own units."""
        if (seen := self._seen) is None:
            raise self._never_seen("owner")
        return seen.owner

    # Its place in the game's reports.

    @property
    def is_stale(self) -> bool:
        """Whether the last observation left it out: it went out of sight, into a transport, or died."""
        return self._stale

    @property
    def is_dead(self) -> bool:
        """Whether it is dead: reported so, or a structure that cannot lift off or uproot missing from its spot.

        The game reports only the deaths a player can see. A structure that dies out of sight is found dead when its
        spot comes into sight again and it is not there. A dead unit is stale for good.
        """
        return self._dead

    @property
    def last_seen(self) -> int | None:
        """The step of the last observation that showed it in sight, or `None` if none has."""
        if self._now.display_type == _VISIBLE:
            return self._step
        return self._last_seen

    # Where it is.

    @property
    def position(self) -> Point:
        """Where it stands on the ground plane."""
        return self._position

    @property
    def height(self) -> float:
        """How high it is, on the scale of the map's terrain heights."""
        return self._now.pos.z

    @property
    def radius(self) -> float:
        """The radius of the circle it takes up around its position."""
        return self._now.radius

    @property
    def facing(self) -> float:
        """The direction it faces, in radians from the positive x-axis."""
        if (seen := self._seen) is None:
            raise self._never_seen("facing")
        return seen.facing

    @property
    def velocity(self) -> Point | None:
        """How fast it moved between its last two observations, in distance per second.

        `None` for a unit observed only once since it was first seen or since it came back.
        """
        previous = self._previous_position
        if previous is None:
            return None
        position = self._position
        scale = STEPS_PER_SECOND / (self._step - self._previous_step)
        return Point(((position[0] - previous[0]) * scale, (position[1] - previous[1]) * scale))

    # How it is seen.

    @property
    def visibility(self) -> Visibility:
        """Whether it is in sight, a structure remembered out of sight, or in sight but undetected."""
        return _VISIBILITIES[self._now.display_type]

    @property
    def cloak(self) -> CloakState:
        """Whether it is cloaked, and whether that is seen through."""
        return _CLOAKS[self._now.cloak]

    @property
    def is_flying(self) -> bool:
        """Whether it is in the air."""
        now = self._now
        if now.display_type != _REMEMBERED:
            return now.is_flying
        if (seen := self._seen) is None:
            raise self._never_seen("flying")
        return seen.is_flying

    @property
    def is_burrowed(self) -> bool:
        """Whether it is burrowed."""
        now = self._now
        if now.display_type != _REMEMBERED:
            return now.is_burrowed
        if (seen := self._seen) is None:
            raise self._never_seen("burrowing")
        return seen.is_burrowed

    @property
    def is_hallucination(self) -> bool:
        """Whether it is a hallucination this player can tell apart."""
        if (seen := self._seen) is None:
            raise self._never_seen("hallucination")
        return seen.is_hallucination

    @property
    def detect_range(self) -> float:
        """How far it detects cloaked and burrowed units, and 0 for a unit that does not."""
        if (seen := self._seen) is None:
            raise self._never_seen("detection range")
        return seen.detect_range

    @property
    def radar_range(self) -> float:
        """How far its radar shows units, and 0 for a unit without one."""
        if (seen := self._seen) is None:
            raise self._never_seen("radar range")
        return seen.radar_range

    # Vitals.

    @property
    def health(self) -> float:
        """The health it has left."""
        if (seen := self._seen) is None:
            raise self._never_seen("health")
        return seen.health

    @property
    def health_max(self) -> float:
        """The health it has when unhurt."""
        if (seen := self._seen) is None:
            raise self._never_seen("health")
        return seen.health_max

    @property
    def health_fraction(self) -> float:
        """The health it has left, as a fraction of its most."""
        if (seen := self._seen) is None:
            raise self._never_seen("health")
        return seen.health / seen.health_max if seen.health_max else 0.0

    @property
    def shield(self) -> float:
        """The shield it has left."""
        if (seen := self._seen) is None:
            raise self._never_seen("shield")
        return seen.shield

    @property
    def shield_max(self) -> float:
        """The shield it has when full, and 0 for a unit without one."""
        if (seen := self._seen) is None:
            raise self._never_seen("shield")
        return seen.shield_max

    @property
    def shield_fraction(self) -> float:
        """The shield it has left, as a fraction of its most, and 0 for a unit without one."""
        if (seen := self._seen) is None:
            raise self._never_seen("shield")
        return seen.shield / seen.shield_max if seen.shield_max else 0.0

    @property
    def energy(self) -> float:
        """The energy it has."""
        if (seen := self._seen) is None:
            raise self._never_seen("energy")
        return seen.energy

    @property
    def energy_max(self) -> float:
        """The most energy it can hold, and 0 for a unit without energy."""
        if (seen := self._seen) is None:
            raise self._never_seen("energy")
        return seen.energy_max

    @property
    def energy_fraction(self) -> float:
        """The energy it has, as a fraction of its most, and 0 for a unit without energy."""
        if (seen := self._seen) is None:
            raise self._never_seen("energy")
        return seen.energy / seen.energy_max if seen.energy_max else 0.0

    # Its state.

    @property
    def build_progress(self) -> float:
        """How far it is through being built, from 0 to 1."""
        if (seen := self._seen) is None:
            raise self._never_seen("build progress")
        return seen.build_progress

    @property
    def is_ready(self) -> bool:
        """Whether it is finished being built."""
        if (seen := self._seen) is None:
            raise self._never_seen("build progress")
        return seen.build_progress == 1.0

    @property
    def is_powered(self) -> bool:
        """Whether it has the power it needs, and False for a unit that needs none."""
        if (seen := self._seen) is None:
            raise self._never_seen("power")
        return seen.is_powered

    @property
    def is_active(self) -> bool:
        """Whether the game animates it as busy, as it does a structure that is training or researching."""
        if (seen := self._seen) is None:
            raise self._never_seen("activity")
        return seen.is_active

    @property
    def attack_upgrade_level(self) -> int:
        """The attack upgrades it has."""
        if (seen := self._seen) is None:
            raise self._never_seen("attack upgrades")
        return seen.attack_upgrade_level

    @property
    def armor_upgrade_level(self) -> int:
        """The armor upgrades it has."""
        if (seen := self._seen) is None:
            raise self._never_seen("armor upgrades")
        return seen.armor_upgrade_level

    @property
    def shield_upgrade_level(self) -> int:
        """The shield upgrades it has."""
        if (seen := self._seen) is None:
            raise self._never_seen("shield upgrades")
        return seen.shield_upgrade_level

    @property
    def mineral_contents(self) -> int:
        """The minerals left to mine from it, and 0 for anything but a mineral field."""
        if (seen := self._seen) is None:
            raise self._never_seen("minerals")
        return seen.mineral_contents

    @property
    def vespene_contents(self) -> int:
        """The vespene left to mine from it, and 0 for anything but a geyser or what stands on one."""
        if (seen := self._seen) is None:
            raise self._never_seen("vespene")
        return seen.vespene_contents

    @property
    def buffs(self) -> frozenset[BuffId]:
        """The buffs it wears."""
        if (seen := self._seen) is None:
            raise self._never_seen("buffs")
        return frozenset(read_id(BuffId, buff) for buff in seen.buff_ids)

    # From the game's tables.

    @property
    def type_data(self) -> UnitTypeData:
        """What the game's tables say about its type, before any upgrade."""
        return self._tracker.data.units[self.type_id]

    @property
    def is_structure(self) -> bool:
        """Whether the game's tables give its type the structure attribute."""
        return Attribute.STRUCTURE in self.type_data.attributes
