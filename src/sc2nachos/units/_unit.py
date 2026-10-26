"""A unit of the game, as last reported."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, NamedTuple

from s2clientprotocol import raw_pb2

from sc2nachos.constants import STEPS_PER_SECOND
from sc2nachos.game_time import GameTime
from sc2nachos.gamedata import Attribute, Cost, TargetDomain
from sc2nachos.gamedata._missing_attacks import ATTACK_SPEED_BUFFS
from sc2nachos.gamedata._repair_steps import REPAIR_COST_FRACTION, REPAIR_STEPS_EXCEPTIONS
from sc2nachos.geometry import Footprint, Point
from sc2nachos.ids import BuffId, UnitTypeId
from sc2nachos.match import Race
from sc2nachos.units._errors import NotReportedError
from sc2nachos.units._snapshot import _Snapshot
from sc2nachos.units._unit_type import UnitType
from sc2nachos.units._values import Alliance, CloakState, Visibility

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable

    from sc2nachos.gamedata import UnitTypeData, Weapon
    from sc2nachos.geometry import PointLike
    from sc2nachos.units._tracking import _Tracker
    from sc2nachos.units._values import UnitOrder

_VISIBLE = raw_pb2.DisplayType.Visible
# A unit this close to facing a target counts as facing it, unless `time_to_face` is told otherwise: most attackers fire
# facing their target to within a degree (in game), though a zealot, a reaper and a marauder were seen to fire some 10
# degrees off.
_DEFAULT_FACING_TOLERANCE = math.radians(1.0)
_IN_FOG = raw_pb2.DisplayType.Snapshot
# The buffs a worker carries resources under: minerals under one for every race and another for rich minerals, and gas
# from a plain geyser under one, each race's game id read as it. Gas from a rich geyser comes under none (in game).
_CARRYING_MINERALS_BUFFS = frozenset({BuffId.CARRYING_MINERALS, BuffId.CARRYING_MINERALS_RICH})
_CARRYING_RESOURCE_BUFFS = _CARRYING_MINERALS_BUFFS | {BuffId.CARRYING_GAS}
_OWN_ALLIANCE = raw_pb2.Alliance.Self
_ENEMY_ALLIANCE = raw_pb2.Alliance.Enemy
# Looked up by the protocol's value, which is faster than calling the enum. A placeholder has no tag, so it is never
# read into a unit.
_VISIBILITIES = {int(member): member for member in Visibility}
_ALLIANCES = {int(member): member for member in Alliance}
_CLOAKS = {int(member): member for member in CloakState}
# Worn by a unit a phoenix holds in the air. The game reports such a unit as not flying.
_LIFTED = int(BuffId.PHOENIX_GRAVITON_BEAM)
# An id is the alliance's digit followed by a count of that alliance's units, below this.
_IDS_PER_ALLIANCE = 100_000
# Air weapons hit these, though they stand on the ground: a viking, which has no ground weapon, fired at a colossus
# (in game).
_GROUND_TYPES_HIT_FROM_THE_AIR = frozenset({UnitTypeId.COLOSSUS})
# How many times as often each buff has its wearer attack, by raw id, as a report lists its buffs.
_ATTACK_SPEED_BY_RAW_BUFF = {int(buff): multiplier for buff, multiplier in ATTACK_SPEED_BUFFS.items()}


def _copy(report: raw_pb2.Unit) -> raw_pb2.Unit:
    """A standalone copy of `report`, which keeps no observation alive."""
    copy = raw_pb2.Unit()
    copy.CopyFrom(report)
    return copy


class _SideOrTypeChange(NamedTuple):
    """What a report changed of a unit's side and type: what each was before, `None` for one that did not change. Its
    side changes when it changes to or from this player's, not between the others' alliances."""

    previous_alliance: Alliance | None
    previous_type: UnitTypeId | None


class Unit[K: UnitType.AnyType]:
    """A unit of the game: one object under one id for the whole game, reading as the game last reported it.

    Position, type and visibility read as of the last observation that listed the unit. Everything else reads as
    of the last observation that showed it visible, `last_seen`, and raises `NotReportedError` for a unit never
    shown visible. A structure out of sight stays in the observation as `IN_FOG`. A unit that leaves the
    observation is stale: it keeps its last values, and is observed under the same id again if it comes back.
    """

    # `OwnUnit` declares no slots, so a unit can change between the two classes in place. `__weakref__` lets a bot
    # key its own data by unit in a `weakref.WeakKeyDictionary`, which slots would otherwise rule out.
    __slots__ = (
        "__weakref__",
        "_damage_taken",
        "_dead",
        "_first_seen",
        "_hallucination_confirmed",
        "_id",
        "_latest_report",
        "_last_seen",
        "_latest_visible_report",
        "_order_targets",
        "_orders",
        "_own",
        "_position",
        "_previous_position",
        "_previous_report",
        "_previous_step",
        "_raw_alliance",
        "_raw_type",
        "_radius_footprint",
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
    _latest_visible_report: raw_pb2.Unit | None
    _last_seen: int | None
    # The step of the first observation that showed the unit visible, or `None` before one has.
    _first_seen: int | None
    # The alliance of the latest report, as the protocol numbers it, read once per report.
    _raw_alliance: int
    _raw_type: int
    _type_id: UnitTypeId
    _position: Point
    _own: bool
    _step: int
    # Where the unit was at the last step before its latest, and that step, for `velocity`. Kept while the unit is out
    # of the observation, and across a second observation at one step; let go as it comes back.
    _previous_position: Point | None
    _previous_step: int
    # The report of the observation just before, for `damage_taken`, at every update. Let go as the unit leaves the
    # observation, so it keeps that one observation alive and no other.
    _previous_report: raw_pb2.Unit | None
    # `damage_taken` as last worked out, with the tracker update it was worked out at.
    _damage_taken: _Snapshot[float | None]
    _stale: bool
    _dead: bool
    # Whether the enemy's unit was once reported a hallucination, which the game flags only while a detector is near
    # (in game).
    _hallucination_confirmed: bool
    # The square its radius spans, kept once made, for a type of the map's own with no footprint measured.
    _radius_footprint: Footprint | None
    # For each ability, the unit this unit's latest order of that ability was aimed at, kept while the game names
    # that unit by a tag it reports. Read when an order names a tag the game never reports. Keyed by the ability's raw
    # id; `None` until an order is aimed at a unit.
    _order_targets: dict[int, Unit[Any]] | None
    # `OwnUnit.orders` as last read, with the tracker update it was read at, kept here since `OwnUnit` declares no
    # slots of its own. It holds no report, so it keeps no observation alive.
    _orders: _Snapshot[tuple[UnitOrder, ...]]

    def __init__(self, report: raw_pb2.Unit, tracker: _Tracker, unit_id: int, step: int) -> None:
        self._tracker = tracker
        self._id = unit_id
        self._tag = report.tag
        self._latest_report = report
        self._latest_visible_report = report if report.display_type == _VISIBLE else None
        # Set only for a unit not visible; a visible unit was last seen at its last observation.
        self._last_seen = None
        self._first_seen = step if report.display_type == _VISIBLE else None
        self._position = Point((report.pos.x, report.pos.y))
        self._raw_alliance = report.alliance
        self._own = self._raw_alliance == _OWN_ALLIANCE
        self._step = step
        self._previous_position = None
        self._previous_report = None
        self._previous_step = step
        self._damage_taken = _Snapshot(None)
        self._stale = False
        self._dead = False
        self._hallucination_confirmed = not self._own and report.display_type == _VISIBLE and report.is_hallucination
        self._radius_footprint = None
        self._order_targets = None
        self._orders = _Snapshot(())
        self._update_unit_type(report.unit_type)

    def _update(self, report: raw_pb2.Unit, step: int) -> _SideOrTypeChange | None:
        """Read this unit's report from the observation at `step`, under the tag it had before. Returns what the report
        changed of its side and type, or `None` if it changed neither.

        The tracker sets `_tag` itself when the unit is observed under a new one.
        """
        position = self._position
        last_step = self._step
        if self._stale:
            # Back in the observation. How it moved while away is not known.
            self._stale = False
            self._previous_position = None
        else:
            # A unit is updated once an observation, so this is the report of the observation before.
            self._previous_report = self._latest_report
            if step != last_step:
                self._previous_position = position
                self._previous_step = last_step
        self._step = step
        reported = report.pos
        x, y = reported.x, reported.y
        # Most units stand still, and comparing is faster than building a new point.
        if x != position[0] or y != position[1]:
            self._position = Point((x, y))
        if report.display_type == _VISIBLE:
            if self._first_seen is None:
                self._first_seen = step
            self._latest_visible_report = report
            if not self._own and not self._hallucination_confirmed:
                self._hallucination_confirmed = report.is_hallucination
        elif self._latest_visible_report is self._latest_report:
            # Just stopped being visible: the last sighting must outlive the observation it came in.
            self._latest_visible_report = _copy(self._latest_report)
            self._last_seen = last_step
        previous_alliance = None
        if (alliance := report.alliance) != self._raw_alliance:
            if (alliance == _OWN_ALLIANCE) is not self._own:
                previous_alliance = _ALLIANCES[self._raw_alliance]
                self._update_alliance()
            self._raw_alliance = alliance
        # The game reports orders for this player's units alone.
        if self._own and (orders := self._latest_report.orders):
            self._remember_order_targets(orders)
        self._latest_report = report
        if report.unit_type == self._raw_type:
            return None if previous_alliance is None else _SideOrTypeChange(previous_alliance, None)
        previous_type = self._type_id
        self._update_unit_type(report.unit_type)
        return _SideOrTypeChange(previous_alliance, previous_type)

    def _remember_order_targets(self, orders: Iterable[raw_pb2.UnitOrder]) -> None:
        """Keep, by ability, the unit each of `orders`, the latest report's, names under a tag the game has reported."""
        targets = self._order_targets
        for order in orders:
            tag = order.target_unit_tag
            if not tag:
                continue
            # Most orders name the unit the last one did, under the tag it still has.
            if targets is not None and (known := targets.get(order.ability_id)) is not None and known._tag == tag:
                continue
            if (unit := self._tracker.unit_tracker.get_by_tag(tag)) is not None:
                if targets is None:
                    targets = self._order_targets = {}
                targets[order.ability_id] = unit

    def _mark_stale(self) -> None:
        """Mark the unit stale: out of the observation, keeping its last values without keeping that observation
        alive."""
        self._stale = True
        self._previous_report = None
        report = self._latest_report
        copy = _copy(report)
        if self._latest_visible_report is report:
            self._latest_visible_report = copy
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
        """The error for reading `what` from a unit the game has never shown visible."""
        return NotReportedError(f"the game never showed {self!r} visible, so it never reported its {what}")

    def __repr__(self) -> str:
        state = ", dead" if self._dead else ", stale" if self._stale else ""
        return f"{type(self).__name__}({self._type_id.name}, id={self._id}, at {self._position}{state})"

    # Identity.

    @property
    def id(self) -> int:
        """NachOS's id for this unit, the same for the whole game.

        The first digit is the unit's alliance when first seen, `Alliance.OWN` being 1 and `Alliance.ENEMY` 4. The
        rest counts that alliance's units in the order they were first seen; in a replay played back with the fog off,
        in the order the game first listed them, so that the unit is under the same id in `Api.all_units`.
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
        return _ALLIANCES[self._raw_alliance]

    @property
    def is_own(self) -> bool:
        """Whether the unit is this player's."""
        return self._raw_alliance == _OWN_ALLIANCE

    @property
    def is_enemy(self) -> bool:
        """Whether the unit is the enemy's."""
        return self._raw_alliance == _ENEMY_ALLIANCE

    @property
    def owner_id(self) -> int:
        """The id of the player the unit belongs to. 16 for the map's own units."""
        if (report := self._latest_visible_report) is None:
            raise self._never_seen_error("owner")
        return report.owner

    # Whether the game still reports it.

    @property
    def is_stale(self) -> bool:
        """Whether the last observation left the unit out: it went out of sight, into a transport or a gas building,
        or into a structure it is becoming, as a drone does, or it died."""
        return self._stale

    @property
    def is_not_stale(self) -> bool:
        """Whether the last observation listed the unit: in sight, in sight but cloaked or burrowed and undetected, or
        a structure remembered in the fog. The opposite of `is_stale`; `is_visible` is the first of these alone."""
        return not self._stale

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
    def is_alive(self) -> bool:
        """Whether the unit is not known to be dead: the opposite of `is_dead`. True for an enemy's unit that died out
        of sight, whose death the game never reports, until NachOS finds it dead, as `is_dead` says for a structure."""
        return not self._dead

    @property
    def is_orderable(self) -> bool:
        """Whether this player can order the unit now: the last observation listed it as this player's. Not while it
        is inside a gas building or a transport, nor under a neural parasite, which makes it an enemy `Unit`, nor once
        it is dead. Read it before ordering a unit kept from an earlier observation, or reading what only `OwnUnit`
        has. It goes by the listing alone, so a reaper's grenade, listed as its thrower's, reads orderable."""
        return not self._stale and self._own

    @property
    def first_seen(self) -> GameTime | None:
        """The time of the first observation that showed the unit visible, or `None` if none has, as for `last_seen`.
        A structure remembered under a new tag keeps it, as it keeps its `id`. Later than `EnemyUnitFirstSeenEvent` for
        an enemy's unit first listed in the fog or undetected, which that event counts as first seen."""
        return None if self._first_seen is None else GameTime(steps=self._first_seen)

    @property
    def last_seen(self) -> GameTime | None:
        """The time of the last observation that showed the unit visible, or `None` if none has."""
        if self._latest_report.display_type == _VISIBLE:
            return GameTime(steps=self._step)
        return None if self._last_seen is None else GameTime(steps=self._last_seen)

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
        if (report := self._latest_visible_report) is None:
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

    @property
    def damage_taken(self) -> float | None:
        """The health and shields the unit lost since the observation before, as `OwnUnitDamagedEvent` and
        `EnemyUnitDamagedEvent` report it, and a neutral unit's alike, which no event reports; `None` unless it was
        visible in both and kept its type. Health regained offsets only health lost, and shields regained only shields
        lost. The health a stim costs is lost like any other."""
        update = self._tracker.last_changes.update
        if (cached := self._damage_taken).update == update:
            return cached.value
        damage = self._damage_since_previous_report()
        self._damage_taken = _Snapshot(damage, update)
        return damage

    def _damage_since_previous_report(self) -> float | None:
        """The health and shields lost between the report of the observation before and the latest, as
        `damage_taken` defines it."""
        previous = self._previous_report
        if previous is None:
            return None
        report = self._latest_report
        if (
            report.display_type != _VISIBLE
            or previous.display_type != _VISIBLE
            or report.unit_type != previous.unit_type
        ):
            return None
        return max(0.0, previous.health - report.health) + max(0.0, previous.shield - report.shield)

    # How it is seen.

    @property
    def visibility(self) -> Visibility:
        """Whether the unit is visible, a structure remembered out of sight, or in sight but undetected."""
        return _VISIBILITIES[self._latest_report.display_type]

    @property
    def is_visible(self) -> bool:
        """Whether the last observation showed the unit visible: in it, and seen, not cloaked or detected. False for a
        stale unit, whose `visibility` reads as it was last listed."""
        return not self._stale and self._latest_report.display_type == _VISIBLE

    @property
    def cloak_state(self) -> CloakState:
        """Whether the unit is cloaked, and whether this player sees through it."""
        return _CLOAKS[self._latest_report.cloak]

    @property
    def is_selected(self) -> bool:
        """Whether a human has the unit selected in the game window."""
        return self._latest_report.is_selected

    @property
    def is_flying(self) -> bool:
        """Whether the unit is in the air, including one held up by a phoenix's graviton beam."""
        now = self._latest_report
        if now.display_type != _IN_FOG:
            return now.is_flying or _LIFTED in now.buff_ids
        if (report := self._latest_visible_report) is None:
            raise self._never_seen_error("flying")
        return report.is_flying

    @property
    def is_burrowed(self) -> bool:
        """Whether the unit is burrowed."""
        now = self._latest_report
        if now.display_type != _IN_FOG:
            return now.is_burrowed
        if (report := self._latest_visible_report) is None:
            raise self._never_seen_error("burrowing")
        return report.is_burrowed

    @property
    def is_hallucination(self) -> bool:
        """Whether the unit is a hallucination. Always known for this player's own units; an enemy's reads true once a
        detector has shown it to be one, and stays true."""
        if self._hallucination_confirmed:
            return True
        if (report := self._latest_visible_report) is None:
            raise self._never_seen_error("hallucination")
        return report.is_hallucination

    @property
    def detect_range(self) -> float:
        """How far the unit detects cloaked and burrowed units. 0 for a unit that does not detect."""
        if (report := self._latest_visible_report) is None:
            raise self._never_seen_error("detection range")
        return report.detect_range

    @property
    def radar_range(self) -> float:
        """How far the unit's radar shows units. 0 for a unit without one."""
        if (report := self._latest_visible_report) is None:
            raise self._never_seen_error("radar range")
        return report.radar_range

    # Vitals.

    @property
    def health(self) -> float:
        """The unit's current health."""
        if (report := self._latest_visible_report) is None:
            raise self._never_seen_error("health")
        return report.health

    @property
    def health_max(self) -> float:
        """The unit's maximum health."""
        if (report := self._latest_visible_report) is None:
            raise self._never_seen_error("health")
        return report.health_max

    @property
    def health_fraction(self) -> float:
        """The unit's health as a fraction of its maximum."""
        if (report := self._latest_visible_report) is None:
            raise self._never_seen_error("health")
        return report.health / report.health_max if report.health_max else 0.0

    @property
    def shield(self) -> float:
        """The unit's current shield."""
        if (report := self._latest_visible_report) is None:
            raise self._never_seen_error("shield")
        return report.shield

    @property
    def shield_max(self) -> float:
        """The unit's maximum shield. 0 for a unit without one."""
        if (report := self._latest_visible_report) is None:
            raise self._never_seen_error("shield")
        return report.shield_max

    @property
    def shield_fraction(self) -> float:
        """The unit's shield as a fraction of its maximum. 0 for a unit without one."""
        if (report := self._latest_visible_report) is None:
            raise self._never_seen_error("shield")
        return report.shield / report.shield_max if report.shield_max else 0.0

    @property
    def life(self) -> float:
        """The unit's health and shield together."""
        if (report := self._latest_visible_report) is None:
            raise self._never_seen_error("health")
        return report.health + report.shield

    @property
    def life_max(self) -> float:
        """The unit's maximum health and shield together."""
        if (report := self._latest_visible_report) is None:
            raise self._never_seen_error("health")
        return report.health_max + report.shield_max

    @property
    def life_fraction(self) -> float:
        """The unit's health and shield together, as a fraction of their combined maximum."""
        if (report := self._latest_visible_report) is None:
            raise self._never_seen_error("health")
        most = report.health_max + report.shield_max
        return (report.health + report.shield) / most if most else 0.0

    @property
    def energy(self) -> float:
        """The unit's current energy."""
        if (report := self._latest_visible_report) is None:
            raise self._never_seen_error("energy")
        return report.energy

    @property
    def energy_max(self) -> float:
        """The unit's maximum energy. 0 for a unit without energy."""
        if (report := self._latest_visible_report) is None:
            raise self._never_seen_error("energy")
        return report.energy_max

    @property
    def energy_fraction(self) -> float:
        """The unit's energy as a fraction of its maximum. 0 for a unit without energy."""
        if (report := self._latest_visible_report) is None:
            raise self._never_seen_error("energy")
        return report.energy / report.energy_max if report.energy_max else 0.0

    # State.

    @property
    def build_progress(self) -> float:
        """A structure's progress through construction, or a unit's through warping in, from 0 to 1.

        Anything else reads 1, anything morphing included. A larva's egg has its progress in its order; a cocoon and a
        structure morphing report none (in game).
        """
        if (report := self._latest_visible_report) is None:
            raise self._never_seen_error("build progress")
        return report.build_progress

    @property
    def is_complete(self) -> bool:
        """Whether a structure has finished construction, or a unit has finished warping in. Anything else is
        complete."""
        if (report := self._latest_visible_report) is None:
            raise self._never_seen_error("build progress")
        return report.build_progress == 1.0

    @property
    def is_powered(self: Unit[UnitType.ProtossStructure]) -> bool:
        """Whether a Protoss structure stands in a pylon's power field. False for one that needs no power, such as a
        nexus, a pylon or an assimilator."""
        if (report := self._latest_visible_report) is None:
            raise self._never_seen_error("power")
        return report.is_powered

    @property
    def is_active(self) -> bool:
        """Whether the unit is busy: moving, attacking, gathering, training or researching. Reported for enemy units
        too."""
        if (report := self._latest_visible_report) is None:
            raise self._never_seen_error("activity")
        return report.is_active

    @property
    def attack_upgrade_level(self) -> int:
        """The unit's attack upgrade level."""
        if (report := self._latest_visible_report) is None:
            raise self._never_seen_error("attack upgrades")
        return report.attack_upgrade_level

    @property
    def armor_upgrade_level(self) -> int:
        """The armor the unit's upgrades add. Not a count of levels: Chitinous Plating adds 2 to an ultralisk's."""
        if (report := self._latest_visible_report) is None:
            raise self._never_seen_error("armor upgrades")
        return report.armor_upgrade_level

    @property
    def shield_upgrade_level(self) -> int:
        """The unit's shield upgrade level."""
        if (report := self._latest_visible_report) is None:
            raise self._never_seen_error("shield upgrades")
        return report.shield_upgrade_level

    @property
    def mineral_contents(self) -> int:
        """The minerals left in a mineral field. 0 for anything else."""
        if (report := self._latest_visible_report) is None:
            raise self._never_seen_error("minerals")
        return report.mineral_contents

    @property
    def vespene_contents(self) -> int:
        """The vespene left in a geyser, or in the gas building on it. 0 for anything else."""
        if (report := self._latest_visible_report) is None:
            raise self._never_seen_error("vespene")
        return report.vespene_contents

    @property
    def buffs(self) -> frozenset[BuffId]:
        """The buffs on the unit."""
        if (report := self._latest_visible_report) is None:
            raise self._never_seen_error("buffs")
        return frozenset(BuffId.read(buff) for buff in report.buff_ids)

    def has_buff(self, buff_ids: BuffId | Collection[BuffId]) -> bool:
        """Whether `buff_ids`, one buff or any of several, is among the unit's `buffs`. Raises `NotReportedError` for a
        unit never shown visible, as `buffs` does."""
        unit_buffs = self.buffs
        if isinstance(buff_ids, BuffId):
            return buff_ids in unit_buffs
        return not unit_buffs.isdisjoint(buff_ids)

    @property
    def is_carrying_resource(self) -> bool:
        """Whether the unit carries minerals or gas, as its buffs show: false for a worker carrying gas from a rich
        geyser, which the game gives no buff (in game). Raises `NotReportedError` for a unit never shown visible, as
        `buffs` does."""
        return self.has_buff(_CARRYING_RESOURCE_BUFFS)

    @property
    def is_carrying_minerals(self) -> bool:
        """Whether the unit carries minerals, from a plain field or a rich one, as its buffs show. Raises
        `NotReportedError` for a unit never shown visible, as `buffs` does."""
        return self.has_buff(_CARRYING_MINERALS_BUFFS)

    @property
    def is_carrying_gas(self) -> bool:
        """Whether the unit carries gas, as its buffs show: false for a worker carrying gas from a rich geyser, which
        the game gives no buff (in game). Raises `NotReportedError` for a unit never shown visible, as `buffs` does."""
        return self.has_buff(BuffId.CARRYING_GAS)

    # From the game's tables.

    @property
    def type_data(self) -> UnitTypeData:
        """The game's table entry for the unit's type, before any upgrade."""
        return self._tracker.game_data.units[self.type_id]

    @property
    def weapons(self) -> tuple[Weapon, ...]:
        """The unit type's weapons with its owner's upgrades applied: this player's upgrades for its own units,
        `Api.enemy.upgrades` for the enemy's, and none for anyone else's. `active_weapons` are those it can fire now."""
        return self._tracker.upgrade_tracker.upgraded_type(self).weapons

    @property
    def speed(self) -> float:
        """The unit type's speed with its owner's upgrades applied, as for `weapons`, before creep or any buff. In
        distance per second, like `velocity`."""
        return self._tracker.upgrade_tracker.upgraded_type(self).speed

    @property
    def armor(self) -> float:
        """The unit's base armor plus the armor its upgrades add: as the unit reports it when `is_visible`, and
        otherwise the more of what it last reported visible and what its owner is known to have."""
        return self._tracker.upgrade_tracker.armor_of(self)

    @property
    def shield_armor(self) -> float:
        """The armor of the unit's shields: its shield upgrade level as it reports it when `is_visible`, and otherwise
        the more of what it last reported visible and the levels its owner is known to have; 0 for a unit without
        shields.

        The game's tables do not hold this. `docs/reference/game-behavior/damage-and-buffs.md` has what a level was
        measured to take off a hit.
        """
        return self._tracker.upgrade_tracker.shield_armor_of(self)

    @property
    def is_structure(self) -> bool:
        """Whether the game's tables give the unit's type the structure attribute."""
        return Attribute.STRUCTURE in self.type_data.attributes

    @property
    def is_ground_structure(self) -> bool:
        """Whether the unit is a structure standing on the ground: one not lifted, and one remembered out of sight,
        whose flying the game does not report."""
        return self.is_structure and (self._latest_report.display_type == _IN_FOG or not self.is_flying)

    def distance_to(self, other: PointLike | Unit[Any]) -> float:
        """The distance from this unit's position to `other`, a point or another unit's position."""
        return self._position.distance_to(other._position if isinstance(other, Unit) else other)

    @property
    def footprint(self) -> Footprint | None:
        """The tiles it covers where it stands: its type's footprint, or for a type of the map's own, of no race, that
        does not move and has none measured, the square its radius spans. `None` for a unit that covers none, a force
        field and a critter among them."""
        row = self._tracker.game_data.units.get(self.type_id)
        if row is None:
            return None
        if row.footprint is None and row.race is Race.NONE and not row.speed:
            side = round(2 * self.radius)
            if not side:
                return None
            if self._radius_footprint is None or self._radius_footprint.width != side:
                self._radius_footprint = Footprint.square(side)
            return self._radius_footprint
        return row.footprint

    @property
    def required_repair_cost(self) -> Cost | None:
        """What one SCV's repair of the unit from its health now to whole takes: the time and resources of
        `total_repair_cost` for the share of its health missing, each rounded up to a whole one, steps for the time (in
        game), and nothing for a unit whole. `None` wherever `total_repair_cost` is."""
        return self._repair_cost_of(self.health_max - self.health)

    @property
    def total_repair_cost(self) -> Cost | None:
        """What one SCV's repair of a unit of this type from no health to whole takes: the time its type costs, but
        for a MULE, an auto-turret, a widow mine and an add-on standing on its own, and a quarter of the resources its
        type costs, each rounded up, with no supply (in game). `None` for a structure under construction, which an SCV
        is refused, and for any type but a Terran mechanical one. `docs/reference/game-behavior/damage-and-buffs.md`
        has what was measured."""
        return self._repair_cost_of(self.health_max)

    @property
    def is_repairable(self) -> bool:
        """Whether `total_repair_cost` gives a cost: whether the unit is a complete Terran mechanical one, whoever's it
        is, an enemy's included. Raises `NotReportedError` for a Terran mechanical unit never
        shown visible, whose build progress the game never reported."""
        return self.total_repair_cost is not None

    def time_to_face(
        self, target: PointLike | Unit[Any], *, facing_tolerance: float = _DEFAULT_FACING_TOLERANCE
    ) -> GameTime | None:
        """The time the unit takes to turn to face `target`, a point or another unit: the angle to turn over its type's
        `turn_rate`, rounded up to a whole step (in game); zero if it faces it already, to within `facing_tolerance`
        radians, a degree unless given. Counted from the step its turn begins, which an order sets off a step after it
        in a game stepped by the bot, and may set off later in a realtime one, as on the ladder. `None` wherever
        `turn_rate` is: for a type whose facing the game never changes and for one that does not move.
        `docs/reference/game-behavior/units.md` has what was measured."""
        if (turn_rate := self.type_data.turn_rate) is None:
            return None
        target_position = target._position if isinstance(target, Unit) else target
        if self._position.distance_to(target_position) == 0:
            return GameTime.ZERO
        bearing = self._position.angle_to(target_position)
        angle_to_turn = abs((bearing - self.facing + math.pi) % math.tau - math.pi)
        if angle_to_turn <= facing_tolerance:
            return GameTime.ZERO
        return GameTime(steps=math.ceil(angle_to_turn / turn_rate))

    def _repair_cost_of(self, missing_health: float) -> Cost | None:
        """What one SCV's repair of `missing_health` of the unit takes; `None` wherever `total_repair_cost` is."""
        type_data = self.type_data
        is_terran_and_mechanical = type_data.race is Race.TERRAN and Attribute.MECHANICAL in type_data.attributes
        if not is_terran_and_mechanical or not self.is_complete:
            return None
        type_cost, health_max = type_data.cost, self.health_max
        whole_repair_steps = REPAIR_STEPS_EXCEPTIONS.get(self.type_id, type_cost.time.steps)
        # Multiplied out before the one division, so that a whole number of resources comes out whole.
        return Cost(
            math.ceil(missing_health * type_cost.minerals * REPAIR_COST_FRACTION / health_max),
            math.ceil(missing_health * type_cost.vespene * REPAIR_COST_FRACTION / health_max),
            time=GameTime(steps=math.ceil(whole_repair_steps * missing_health / health_max)),
        )

    # Combat.

    @property
    def active_weapons(self) -> tuple[Weapon, ...]:
        """The weapons of `weapons` the unit can fire now: all of them, but none for an oracle whose beam is off, which
        wears `BuffId.ORACLE_PULSAR_BEAM` exactly while it is on (in game). Raises `NotReportedError` for an oracle
        never shown visible."""
        if self.type_id is UnitTypeId.ORACLE and BuffId.ORACLE_PULSAR_BEAM not in self.buffs:
            return ()
        return self.weapons

    @property
    def ground_weapon(self) -> Weapon | None:
        """The weapon of `active_weapons` that fires at the ground, alone or with the air, or `None`. No type has two
        that do (corpus). Raises as `active_weapons` does."""
        return self._weapon_firing_at(TargetDomain.GROUND)

    @property
    def air_weapon(self) -> Weapon | None:
        """The weapon of `active_weapons` that fires at the air, alone or with the ground, or `None`, as
        `ground_weapon` is for the ground."""
        return self._weapon_firing_at(TargetDomain.AIR)

    @property
    def ground_range(self) -> float:
        """The range of `ground_weapon`, with its owner's upgrades, or 0 without one."""
        weapon = self.ground_weapon
        return 0.0 if weapon is None else weapon.range

    @property
    def air_range(self) -> float:
        """The range of `air_weapon`, with its owner's upgrades, or 0 without one."""
        weapon = self.air_weapon
        return 0.0 if weapon is None else weapon.range

    @property
    def ground_damage(self) -> float:
        """What one attack of `ground_weapon` does before bonuses and armor, its `total_damage`, or 0 without
        one. A hallucination's counts; `damage_against` is what an attack takes off a target."""
        weapon = self.ground_weapon
        return 0.0 if weapon is None else weapon.total_damage

    @property
    def air_damage(self) -> float:
        """What one attack of `air_weapon` does before bonuses and armor, as `ground_damage` is for the ground."""
        weapon = self.air_weapon
        return 0.0 if weapon is None else weapon.total_damage

    @property
    def ground_dps(self) -> float:
        """The `dps` of `ground_weapon`, per second of the game's Faster speed with the mean attack delay, before
        bonuses, armor and buffs, or 0 without one. A hallucination's counts; `dps_against` is what a target takes."""
        weapon = self.ground_weapon
        return 0.0 if weapon is None else weapon.dps

    @property
    def air_dps(self) -> float:
        """The `dps` of `air_weapon`, as `ground_dps` is for the ground."""
        weapon = self.air_weapon
        return 0.0 if weapon is None else weapon.dps

    @property
    def is_melee(self) -> bool:
        """Whether the unit fires at the ground from no farther than 1: its `ground_range` above 0 and at most 1, as a
        zealot's and a zergling's are."""
        return 0.0 < self.ground_range <= 1.0

    def _weapon_firing_at(self, domain: TargetDomain) -> Weapon | None:
        """The weapon of `active_weapons` that fires at `domain`, the ground or the air, alone or with the other."""
        return next(
            (weapon for weapon in self.active_weapons if weapon.target_domain in (domain, TargetDomain.ANY)), None
        )

    def weapon_against(self, target: Unit[Any]) -> Weapon | None:
        """The weapon of `active_weapons` this unit fires at `target`: one that hits the air for a target that flies,
        and one that hits the ground for a target on it, or failing that, for a colossus, one that hits the air, as a
        viking's did (in game). `None` if it has none that hits `target`, an oracle whose beam is off among them.

        Raises `NotReportedError` for a target remembered in the fog and never seen visible, as `is_flying` does, and
        for an oracle never shown visible.
        """
        weapons = self.active_weapons
        if target.is_flying:
            return next((weapon for weapon in weapons if weapon.target_domain is not TargetDomain.GROUND), None)
        on_the_ground = next((weapon for weapon in weapons if weapon.target_domain is not TargetDomain.AIR), None)
        if on_the_ground is None and target.type_id in _GROUND_TYPES_HIT_FROM_THE_AIR:
            return next((weapon for weapon in weapons if weapon.target_domain is TargetDomain.AIR), None)
        return on_the_ground

    def has_in_range(self, target: Unit[Any], *, margin: float = 0.0) -> bool:
        """Whether `target` is within the reach of `weapon_against(target)`: their centers no farther apart than its
        range, both radii and `margin`. False if this unit has no weapon against `target`.

        That is where a unit that can move fires at another unit (in game). An attacker that cannot move, such as a
        sieged tank or a spine crawler, fires from up to some 0.1 nearer, and any attacker fires at a structure from up
        to some 0.26 nearer, so pass a negative `margin` to be sure the shot can land. Raises as `weapon_against` does.
        """
        weapon = self.weapon_against(target)
        return weapon is not None and self.distance_to(target) <= weapon.range + self.radius + target.radius + margin

    def damage_against(self, target: Unit[Any]) -> float:
        """What one attack of `weapon_against(target)` takes off `target`'s shields and health as they stand, every hit
        of it counted, as `Weapon.damage_against` works it out from the target's attributes, `armor`, `shield_armor`
        and `shield`. 0 if
        this unit has no weapon against it or is a hallucination, which does no damage (stated). An enemy's reads as
        one only once revealed, as `is_hallucination` has it. No buff on either unit counts.

        Raises `NotReportedError` as `weapon_against` does, and for a unit of the two never shown visible, whose
        shields or hallucination the game never reported.
        """
        weapon = self.weapon_against(target)
        return 0.0 if weapon is None or self.is_hallucination else _damage_of_one_attack(weapon, target)

    def bonus_damage_against(self, target: Unit[Any]) -> float:
        """The bonus damage one attack of `weapon_against(target)` does for `target`'s attributes, before armor: its
        `Weapon.bonus_per_hit_against` them, times its attacks. 0 if this unit has no weapon against it. A
        hallucination's counts, as for `ground_damage`. Raises `NotReportedError` as `weapon_against` does."""
        weapon = self.weapon_against(target)
        if weapon is None:
            return 0.0
        return weapon.bonus_per_hit_against(target.type_data.attributes) * weapon.attacks

    def dps_against(self, target: Unit[Any]) -> float:
        """`damage_against(target)` per second of real time at the game's Faster speed, with `weapon_against(target)`
        attacking as often as the buffs on this unit have it: stim makes a marine's and a marauder's 1.5 times as often
        (in game). 0 where `damage_against` is.

        Raises `NotReportedError` as `damage_against` does.
        """
        weapon = self.weapon_against(target)
        if weapon is None or self.is_hallucination:
            return 0.0
        damage = _damage_of_one_attack(weapon, target)
        if (report := self._latest_visible_report) is None:
            raise self._never_seen_error("buffs")
        # What speeds an attack divides the base cooldown, and leaves the random delay as it is (in game).
        attack_speed_multiplier = math.prod(_ATTACK_SPEED_BY_RAW_BUFF.get(buff, 1.0) for buff in report.buff_ids)
        # In plain steps, which spares two `GameTime`s a call in a targeting loop.
        cooldown_steps = weapon.base_cooldown.steps / attack_speed_multiplier + weapon.mean_delay.steps
        return damage * STEPS_PER_SECOND / cooldown_steps


def _damage_of_one_attack(weapon: Weapon, target: Unit[Any]) -> float:
    """What one attack of `weapon` takes off `target` as it stands, as `Weapon.damage_against` has it."""
    return weapon.damage_against(
        attributes=target.type_data.attributes,
        armor=target.armor,
        shield_armor=target.shield_armor,
        shield=target.shield,
    )
