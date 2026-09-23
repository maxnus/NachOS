"""A unit of the game, as last reported."""

from __future__ import annotations

from typing import TYPE_CHECKING

from s2clientprotocol import raw_pb2

from sc2nachos.constants import STEPS_PER_SECOND
from sc2nachos.gamedata import Attribute
from sc2nachos.geometry import Point
from sc2nachos.ids import BuffId, UnitTypeId
from sc2nachos.units._errors import NotReportedError
from sc2nachos.units._unit_type import UnitType
from sc2nachos.units._values import Alliance, CloakState, Visibility

if TYPE_CHECKING:
    from sc2nachos.gamedata import UnitTypeData, Weapon
    from sc2nachos.units._tracking import _Tracker

_IN_VISION = raw_pb2.DisplayType.Visible
_IN_FOG = raw_pb2.DisplayType.Snapshot
_OWN = raw_pb2.Alliance.Self
# Looked up by the protocol's value, which is faster than calling the enum. A placeholder has no tag, so it is never
# read into a unit.
_VISIBILITIES = {int(member): member for member in Visibility}
_ALLIANCES = {int(member): member for member in Alliance}
_CLOAKS = {int(member): member for member in CloakState}
# Worn by a unit a phoenix holds in the air. The game reports such a unit as not flying.
_LIFTED = int(BuffId.PHOENIX_GRAVITON_BEAM)
# An id is the alliance's digit followed by a count of that alliance's units, below this.
_IDS_PER_ALLIANCE = 100_000


def _copy(report: raw_pb2.Unit) -> raw_pb2.Unit:
    """A standalone copy of `report`, which keeps no observation alive."""
    copy = raw_pb2.Unit()
    copy.CopyFrom(report)
    return copy


class Unit[K: UnitType.AnyType]:
    """A unit of the game: one object under one id for the whole game, reading as the game last reported it.

    Position, type and visibility read as of the last observation that listed the unit. Everything else reads as
    of the last observation that showed it in sight, `last_seen`, and raises `NotReportedError` for a unit never
    shown in sight. A structure out of sight stays in the observation as `IN_FOG`. A unit that leaves the
    observation is stale: it keeps its last values, and is observed under the same id again if it comes back.
    """

    # `OwnUnit` declares no slots, so a unit can change between the two classes in place. `__weakref__` lets a bot
    # key its own data by unit in a `weakref.WeakKeyDictionary`, which slots would otherwise rule out.
    __slots__ = (
        "__weakref__",
        "_dead",
        "_id",
        "_latest_report",
        "_last_seen",
        "_latest_report_in_vision",
        "_own",
        "_position",
        "_previous_position",
        "_previous_step",
        "_raw_type",
        "_stale",
        "_step",
        "_tag",
        "_tracker",
        "_type_id",
    )

    _tracker: _Tracker
    _id: int
    _tag: int
    _latest_report: raw_pb2.Unit
    _latest_report_in_vision: raw_pb2.Unit | None
    _last_seen: int | None
    _raw_type: int
    _type_id: UnitTypeId
    _position: Point
    _own: bool
    _step: int
    _previous_position: Point | None
    _previous_step: int
    _stale: bool
    _dead: bool

    def __init__(self, report: raw_pb2.Unit, tracker: _Tracker, unit_id: int, step: int) -> None:
        self._tracker = tracker
        self._id = unit_id
        self._tag = report.tag
        self._latest_report = report
        self._latest_report_in_vision = report if report.display_type == _IN_VISION else None
        # Set only for a unit out of sight; a unit in sight was last seen at its last observation.
        self._last_seen = None
        self._position = Point((report.pos.x, report.pos.y))
        self._own = report.alliance == _OWN
        self._step = step
        self._previous_position = None
        self._previous_step = step
        self._stale = False
        self._dead = False
        self._update_unit_type(report.unit_type)

    def _update(self, report: raw_pb2.Unit, step: int) -> None:
        """Read this unit's report from the observation at `step`, under the tag it had before.

        The tracker sets `_tag` itself when the unit is observed under a new one.
        """
        position = self._position
        last = self._step
        if self._stale:
            # Back in the observation. How it moved while away is not known.
            self._stale = False
            self._previous_position = None
        elif step != last:
            self._previous_position = position
            self._previous_step = last
        self._step = step
        reported = report.pos
        x, y = reported.x, reported.y
        # Most units stand still, and comparing is faster than building a new point.
        if x != position[0] or y != position[1]:
            self._position = Point((x, y))
        if report.display_type == _IN_VISION:
            self._latest_report_in_vision = report
        elif self._latest_report_in_vision is self._latest_report:
            # Just went out of sight: the last sighting must outlive the observation it came in.
            self._latest_report_in_vision = _copy(self._latest_report)
            self._last_seen = last
        if (report.alliance == _OWN) is not self._own:
            # The old alliance is read before the new report replaces the old one.
            self._tracker.last_changes.units_alliance_changed.append((self, _ALLIANCES[self._latest_report.alliance]))
            self._update_alliance()
        self._latest_report = report
        if report.unit_type != self._raw_type:
            previous = self._type_id
            self._update_unit_type(report.unit_type)
            self._tracker.last_changes.units_type_changed.append((self, previous))

    def _mark_stale(self) -> None:
        """Mark the unit stale: out of the observation, keeping its last values without keeping that observation
        alive."""
        self._stale = True
        report = self._latest_report
        copy = _copy(report)
        if self._latest_report_in_vision is report:
            self._latest_report_in_vision = copy
        self._latest_report = copy

    def _mark_dead(self) -> None:
        """Mark the unit dead: out of the observation for good."""
        if not self._stale:
            self._mark_stale()
        self._dead = True

    def _update_unit_type(self, raw_type: int) -> None:
        """Set the unit's type to `raw_type`. This is the one place a type is set, on creation and at every morph.

        Raises `UncuratedIdError`, as the observation is taken in, for a type the curated ids leave out: a type the
        game reports belongs among them.
        """
        self._raw_type = raw_type
        self._type_id = UnitTypeId.read(raw_type)

    def _update_alliance(self) -> None:
        """Change sides, and with them class, to `OwnUnit` or back, as when a neural parasite takes a unit over or
        lets it go."""
        from sc2nachos.units._own_unit import OwnUnit

        self._own = not self._own
        # Assigning `self.__class__` directly type-checks only for a class of `Self`.
        object.__setattr__(self, "__class__", OwnUnit if self._own else Unit)

    def _never_seen_error(self, what: str) -> NotReportedError:
        """The error for reading `what` from a unit the game has never shown in sight."""
        return NotReportedError(f"the game never showed {self!r} in sight, so it never reported its {what}")

    def __repr__(self) -> str:
        state = ", dead" if self._dead else ", stale" if self._stale else ""
        return f"{type(self).__name__}({self._type_id.name}, id={self._id}, at {self._position}{state})"

    # Identity.

    @property
    def id(self) -> int:
        """NachOS's id for this unit, the same for the whole game.

        The first digit is the unit's alliance when first seen, `Alliance.OWN` being 1 and `Alliance.ENEMY` 4. The
        rest counts that alliance's units in the order they were first seen.
        """
        return self._id

    @property
    def first_alliance(self) -> Alliance:
        """Whose side the unit was on when first seen, as its `id` records. A unit of this player's under a neural
        parasite reads `Alliance.ENEMY` for `alliance` and `Alliance.OWN` here, and still takes this player's
        supply."""
        return _ALLIANCES[self._id // _IDS_PER_ALLIANCE]

    @property
    def tag(self) -> int:
        """The game's current tag for this unit. A structure's tag changes each time it goes out of sight."""
        return self._tag

    @property
    def type_id(self) -> UnitTypeId:
        """The unit's type."""
        return self._type_id

    @property
    def alliance(self) -> Alliance:
        """Whose side the unit is on."""
        return _ALLIANCES[self._latest_report.alliance]

    @property
    def owner_id(self) -> int:
        """The id of the player the unit belongs to. 16 for the map's own units."""
        if (report := self._latest_report_in_vision) is None:
            raise self._never_seen_error("owner")
        return report.owner

    # Whether the game still reports it.

    @property
    def is_stale(self) -> bool:
        """Whether the last observation left the unit out: it went out of sight, into a transport or a gas building,
        or into a structure it is becoming, as a drone does, or it died."""
        return self._stale

    @property
    def is_dead(self) -> bool:
        """Whether the unit is dead: reported dead, or a structure that cannot lift off or uproot gone from its spot.

        The game reports only the deaths a player can see. A structure that dies out of sight is found dead when its
        spot comes back into sight without it. A unit of this player's that became a structure, as a drone does, is
        dead once that structure finishes, or once it dies without the unit coming back from a cancel. A dead unit is
        stale for good.
        """
        return self._dead

    @property
    def last_seen(self) -> int | None:
        """The step of the last observation that showed the unit in sight, or `None` if none has."""
        if self._latest_report.display_type == _IN_VISION:
            return self._step
        return self._last_seen

    # Where it is.

    @property
    def position(self) -> Point:
        """The unit's position on the ground plane."""
        return self._position

    @property
    def height(self) -> float:
        """The unit's height, on the scale of the map's terrain heights."""
        return self._latest_report.pos.z

    @property
    def radius(self) -> float:
        """The radius of the circle the unit takes up around its position."""
        return self._latest_report.radius

    @property
    def facing(self) -> float:
        """The direction the unit faces, in radians from the positive x-axis."""
        if (report := self._latest_report_in_vision) is None:
            raise self._never_seen_error("facing")
        return report.facing

    @property
    def velocity(self) -> Point | None:
        """The unit's velocity between its last two observations, in distance per second.

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
        """Whether the unit is in sight, a structure remembered out of sight, or in sight but undetected."""
        return _VISIBILITIES[self._latest_report.display_type]

    @property
    def cloak_state(self) -> CloakState:
        """Whether the unit is cloaked, and whether this player sees through it."""
        return _CLOAKS[self._latest_report.cloak]

    @property
    def is_flying(self) -> bool:
        """Whether the unit is in the air, including one held up by a phoenix's graviton beam."""
        now = self._latest_report
        if now.display_type != _IN_FOG:
            return now.is_flying or _LIFTED in now.buff_ids
        if (report := self._latest_report_in_vision) is None:
            raise self._never_seen_error("flying")
        return report.is_flying

    @property
    def is_burrowed(self) -> bool:
        """Whether the unit is burrowed."""
        now = self._latest_report
        if now.display_type != _IN_FOG:
            return now.is_burrowed
        if (report := self._latest_report_in_vision) is None:
            raise self._never_seen_error("burrowing")
        return report.is_burrowed

    @property
    def is_hallucination(self) -> bool:
        """Whether the unit is a hallucination. Always known for this player's own units; for an enemy's, only once
        detected."""
        if (report := self._latest_report_in_vision) is None:
            raise self._never_seen_error("hallucination")
        return report.is_hallucination

    @property
    def detect_range(self) -> float:
        """How far the unit detects cloaked and burrowed units. 0 for a unit that does not detect."""
        if (report := self._latest_report_in_vision) is None:
            raise self._never_seen_error("detection range")
        return report.detect_range

    @property
    def radar_range(self) -> float:
        """How far the unit's radar shows units. 0 for a unit without one."""
        if (report := self._latest_report_in_vision) is None:
            raise self._never_seen_error("radar range")
        return report.radar_range

    # Vitals.

    @property
    def health(self) -> float:
        """The unit's current health."""
        if (report := self._latest_report_in_vision) is None:
            raise self._never_seen_error("health")
        return report.health

    @property
    def health_max(self) -> float:
        """The unit's maximum health."""
        if (report := self._latest_report_in_vision) is None:
            raise self._never_seen_error("health")
        return report.health_max

    @property
    def health_fraction(self) -> float:
        """The unit's health as a fraction of its maximum."""
        if (report := self._latest_report_in_vision) is None:
            raise self._never_seen_error("health")
        return report.health / report.health_max if report.health_max else 0.0

    @property
    def shield(self) -> float:
        """The unit's current shield."""
        if (report := self._latest_report_in_vision) is None:
            raise self._never_seen_error("shield")
        return report.shield

    @property
    def shield_max(self) -> float:
        """The unit's maximum shield. 0 for a unit without one."""
        if (report := self._latest_report_in_vision) is None:
            raise self._never_seen_error("shield")
        return report.shield_max

    @property
    def shield_fraction(self) -> float:
        """The unit's shield as a fraction of its maximum. 0 for a unit without one."""
        if (report := self._latest_report_in_vision) is None:
            raise self._never_seen_error("shield")
        return report.shield / report.shield_max if report.shield_max else 0.0

    @property
    def life(self) -> float:
        """The unit's health and shield together."""
        if (report := self._latest_report_in_vision) is None:
            raise self._never_seen_error("health")
        return report.health + report.shield

    @property
    def life_max(self) -> float:
        """The unit's maximum health and shield together."""
        if (report := self._latest_report_in_vision) is None:
            raise self._never_seen_error("health")
        return report.health_max + report.shield_max

    @property
    def life_fraction(self) -> float:
        """The unit's health and shield together, as a fraction of their combined maximum."""
        if (report := self._latest_report_in_vision) is None:
            raise self._never_seen_error("health")
        most = report.health_max + report.shield_max
        return (report.health + report.shield) / most if most else 0.0

    @property
    def energy(self) -> float:
        """The unit's current energy."""
        if (report := self._latest_report_in_vision) is None:
            raise self._never_seen_error("energy")
        return report.energy

    @property
    def energy_max(self) -> float:
        """The unit's maximum energy. 0 for a unit without energy."""
        if (report := self._latest_report_in_vision) is None:
            raise self._never_seen_error("energy")
        return report.energy_max

    @property
    def energy_fraction(self) -> float:
        """The unit's energy as a fraction of its maximum. 0 for a unit without energy."""
        if (report := self._latest_report_in_vision) is None:
            raise self._never_seen_error("energy")
        return report.energy / report.energy_max if report.energy_max else 0.0

    # State.

    @property
    def build_progress(self) -> float:
        """A structure's progress through construction, or a unit's through warping in, from 0 to 1.

        Anything else reads 1, including an egg or a cocoon morphing into a unit, whose progress is its order's.
        """
        if (report := self._latest_report_in_vision) is None:
            raise self._never_seen_error("build progress")
        return report.build_progress

    @property
    def is_complete(self) -> bool:
        """Whether a structure has finished construction, or a unit has finished warping in. Anything else is
        complete."""
        if (report := self._latest_report_in_vision) is None:
            raise self._never_seen_error("build progress")
        return report.build_progress == 1.0

    @property
    def is_powered(self: Unit[UnitType.ProtossStructure]) -> bool:
        """Whether a Protoss structure stands in a pylon's power field. False for one that needs no power, such as a
        nexus, a pylon or an assimilator."""
        if (report := self._latest_report_in_vision) is None:
            raise self._never_seen_error("power")
        return report.is_powered

    @property
    def is_active(self) -> bool:
        """Whether the unit is busy: moving, attacking, gathering, training or researching. Reported for enemy units
        too."""
        if (report := self._latest_report_in_vision) is None:
            raise self._never_seen_error("activity")
        return report.is_active

    @property
    def attack_upgrade_level(self) -> int:
        """The unit's attack upgrade level."""
        if (report := self._latest_report_in_vision) is None:
            raise self._never_seen_error("attack upgrades")
        return report.attack_upgrade_level

    @property
    def armor_upgrade_level(self) -> int:
        """The armor the unit's upgrades add. Not a count of levels: Chitinous Plating adds 2 to an ultralisk's."""
        if (report := self._latest_report_in_vision) is None:
            raise self._never_seen_error("armor upgrades")
        return report.armor_upgrade_level

    @property
    def shield_upgrade_level(self) -> int:
        """The unit's shield upgrade level."""
        if (report := self._latest_report_in_vision) is None:
            raise self._never_seen_error("shield upgrades")
        return report.shield_upgrade_level

    @property
    def mineral_contents(self) -> int:
        """The minerals left in a mineral field. 0 for anything else."""
        if (report := self._latest_report_in_vision) is None:
            raise self._never_seen_error("minerals")
        return report.mineral_contents

    @property
    def vespene_contents(self) -> int:
        """The vespene left in a geyser, or in the gas building on it. 0 for anything else."""
        if (report := self._latest_report_in_vision) is None:
            raise self._never_seen_error("vespene")
        return report.vespene_contents

    @property
    def buffs(self) -> frozenset[BuffId]:
        """The buffs on the unit."""
        if (report := self._latest_report_in_vision) is None:
            raise self._never_seen_error("buffs")
        return frozenset(BuffId.read(buff) for buff in report.buff_ids)

    # From the game's tables.

    @property
    def type_data(self) -> UnitTypeData:
        """The game's table entry for the unit's type, before any upgrade."""
        return self._tracker.game_data.units[self.type_id]

    @property
    def weapons(self) -> tuple[Weapon, ...]:
        """The unit type's weapons with its owner's upgrades applied: this player's upgrades for its own units,
        `Api.enemy.upgrades` for the enemy's, and none for anyone else's."""
        return self._tracker.upgrade_tracker.upgraded_type(self).weapons

    @property
    def speed(self) -> float:
        """The unit type's speed with its owner's upgrades applied, as for `weapons`, before creep or any buff. In
        distance per second, like `velocity`."""
        return self._tracker.upgrade_tracker.upgraded_type(self).speed

    @property
    def armor(self) -> float:
        """The unit's base armor plus the armor its upgrades add: as the unit reports it when in sight, and as its
        owner is known to have when out of sight."""
        return self._tracker.upgrade_tracker.armor_of(self)

    @property
    def shield_armor(self) -> float:
        """The armor of the unit's shields: the shield upgrade levels its owner has, and 0 for a unit without shields.

        The game's tables do not hold this. `docs/curating-ids.md` has what a level was measured to take off a hit.
        """
        return self._tracker.upgrade_tracker.shield_armor_of(self)

    @property
    def is_structure(self) -> bool:
        """Whether the game's tables give the unit's type the structure attribute."""
        return Attribute.STRUCTURE in self.type_data.attributes
