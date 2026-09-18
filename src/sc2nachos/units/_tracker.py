"""Which unit each tag in a game's observations is."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from s2clientprotocol import raw_pb2

from sc2nachos._errors import NachOSError
from sc2nachos.ids import AbilityId, UnitTypeId
from sc2nachos.units._changes import _TrackerChanges
from sc2nachos.units._comparison import _UnitComparison
from sc2nachos.units._construction import _Construction
from sc2nachos.units._errors import UnknownTagError
from sc2nachos.units._own_unit import OwnUnit
from sc2nachos.units._unit import Unit
from sc2nachos.units._units import Units
from sc2nachos.units._upgrades import _Upgrades

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from sc2nachos.enemy import Enemy
    from sc2nachos.gamedata import GameData

_IN_VISION = raw_pb2.DisplayType.Visible
_IN_FOG = raw_pb2.DisplayType.Snapshot
_OWN = raw_pb2.Alliance.Self
_ENEMY = raw_pb2.Alliance.Enemy
# An id is its alliance's digit followed by this many digits counting that alliance's units.
_IDS_PER_ALLIANCE = 100_000
# The types that can leave the spot they are remembered at, by lifting off or uprooting: the performers the curated
# `*_LIFT` and `*_UPROOT` abilities name, which every other ability name puts first too.
_MOVABLE_UNIT_TYPE_IDS = frozenset(
    UnitTypeId[performer]
    for ability in AbilityId
    if ability.name.endswith(("_LIFT", "_UPROOT"))
    and (performer := ability.name.rsplit("_", 1)[0]) in UnitTypeId.__members__
)

type _UnitsByTag = dict[int, Unit[Any]]


class _UnitTracker:
    """The units of one game: one object under one id, whatever tags the game reports it under."""

    __slots__ = (
        "_by_tag",
        "_comparison",
        "_construction",
        "_data",
        "_enemy",
        "_ignored_tags",
        "_ids",
        "_in_fog_tags",
        "_known_units",
        "_last_changes",
        "_number_of_updates",
        "_present_units",
        "_present_units_by_tag",
        "_stale_units",
        "_unfinished",
        "_units_by_id",
        "_units_seen_per_alliance",
        "_upgrades",
    )

    def __init__(self, data: GameData, enemy: Enemy) -> None:
        self._data = data
        self._enemy = enemy
        # Every tag of a unit not dead: a tag in vision for good, a tag in the fog until it leaves.
        self._by_tag: _UnitsByTag = {}
        # The tags among them of structures in the fog, which never come back once they leave.
        self._in_fog_tags: set[int] = set()
        # Tags in the fog of structures seen since elsewhere, left out for as long as the game goes on listing them.
        self._ignored_tags: set[int] = set()
        # Every tag ever linked, dead units' included, to the id it names, and every unit ever made by its id.
        self._ids: dict[int, int] = {}
        self._units_by_id: dict[int, Unit[Any]] = {}
        # The last observation's units, by the tag it reported each under, and as a collection.
        self._present_units_by_tag: _UnitsByTag = {}
        self._present_units: Units[Unit[Any]] = Units()
        # The units out of the observation and not dead, by id.
        self._stale_units: dict[int, Unit[Any]] = {}
        self._known_units: Units[Unit[Any]] | None = None
        # What the last update found changed, and how many updates there have been.
        self._last_changes = _TrackerChanges()
        self._number_of_updates = 0
        self._comparison = _UnitComparison(self)
        # This player's units first seen unfinished and not finished since, by id.
        self._unfinished: dict[int, OwnUnit[Any]] = {}
        # How many units of each alliance have been seen, indexed by the alliance's value, from which ids are made.
        self._units_seen_per_alliance = [0] * 5
        self._construction = _Construction(self)
        self._upgrades = _Upgrades(data, enemy)

    @property
    def data(self) -> GameData:
        """The tables the game is played by."""
        return self._data

    @property
    def enemy(self) -> Enemy:
        """The other player of the game, and what is known of it."""
        return self._enemy

    @property
    def present_units(self) -> Units[Unit[Any]]:
        """Every unit in the last observation, in the order the game listed them."""
        return self._present_units

    @property
    def known_units(self) -> Units[Unit[Any]]:
        """Every unit not known to be dead: those in the last observation, then the stale ones."""
        if self._known_units is None:
            self._known_units = Units((*self._present_units, *self._stale_units.values()))
        return self._known_units

    @property
    def last_changes(self) -> _TrackerChanges:
        """What the last update found changed.

        The game also reports deaths under tags it never reported a unit under (corpus), which name no unit.
        """
        return self._last_changes

    @property
    def comparison(self) -> _UnitComparison:
        """Each unit compared with the update before, into the last changes, while something asks."""
        return self._comparison

    @property
    def construction(self) -> _Construction:
        """Which unit builds which of this player's structures."""
        return self._construction

    @property
    def upgrades(self) -> _Upgrades:
        """The upgrades each side's units have, and what they make of a unit's type."""
        return self._upgrades

    def unit_by_tag(self, tag: int) -> Unit[Any]:
        """The unit the game reported under `tag`, dead or alive."""
        try:
            return self._units_by_id[self._ids[tag]]
        except KeyError:
            raise UnknownTagError(f"the game never reported a unit under tag {tag}") from None

    def update(self, observation: raw_pb2.ObservationRaw, step: int) -> None:
        """Take in the units the observation at `step` reports, what it says died, and this player's upgrades.

        Raises `UncuratedIdError` where this player holds an upgrade the curated ids leave out, which belongs in them.
        """
        self._last_changes = _TrackerChanges()
        self._number_of_updates += 1
        if new_upgrades := self._upgrades.update(observation.player):
            self._last_changes.own_upgrades_finished = new_upgrades
        dead = observation.event.dead_units
        present = self._update_units(observation.units, step, dead)
        self._update_deaths(dead, present)
        if self._unfinished:
            self._update_unfinished_units()
        self._set_present_units(present)

    def _update_units(self, protos: Sequence[raw_pb2.Unit], step: int, dead: Iterable[int]) -> _UnitsByTag:
        """Take in the units the observation lists, and mark those that left it stale, or dead where they must be.

        Answers the units listed by tag, in the order the game listed them.
        """
        previous = self._present_units_by_tag
        present, new = self._update_known_tags(protos, previous, step)
        if new:
            self._identify_new_tags(new, previous, present, step, set(dead))
            # Placed after the rest, so put back in the order the game listed them.
            present = {proto.tag: present[proto.tag] for proto in protos if proto.tag in present}
        self._handle_departed_units(previous, present, step)
        return present

    def _update_deaths(self, dead: Iterable[int], present: _UnitsByTag) -> None:
        """Mark dead each unit the observation reports dead, and each unit that became a structure and is used up, and
        leave the dead out of the enemy's units that left sight."""
        for tag in dead:
            if (unit := self._by_tag.get(tag)) is not None:
                self._mark_unit_dead(unit, present, reported=True)
        self._construction.update(present)
        changes = self._last_changes
        if changes.enemy_units_left_sight:
            # A unit that died went out of the observation first.
            changes.enemy_units_left_sight = [unit for unit in changes.enemy_units_left_sight if not unit._dead]

    def _set_present_units(self, present: _UnitsByTag) -> None:
        """Make `present` the last observation's units, and forget what was worked out from the one before."""
        self._present_units_by_tag = present
        self._present_units = Units(present.values())
        self._known_units = None
        self._construction.forget_observation()

    def _update_known_tags(
        self, protos: Iterable[raw_pb2.Unit], previous: _UnitsByTag, step: int
    ) -> tuple[_UnitsByTag, list[raw_pb2.Unit]]:
        """Update the unit behind every tag already linked, and answer those units by tag and the protos left over.

        Takes each unit it updates out of `previous`, which is left holding the tags that did not come back.
        """
        present: _UnitsByTag = {}
        new: list[raw_pb2.Unit] = []
        by_tag = self._by_tag
        ignored = self._ignored_tags
        pop_previous = previous.pop
        # Runs for every unit in every observation, so the common case, a tag in the last observation too, comes first.
        for proto in protos:
            tag = proto.tag
            if not tag:
                # A radar blip or a structure placed but not yet started, neither of which is a unit yet.
                continue
            unit = pop_previous(tag, None)
            if unit is None:
                if tag in ignored:
                    continue
                unit = by_tag.get(tag)
                if unit is None:
                    new.append(proto)
                    continue
                if unit._stale:
                    del self._stale_units[unit._id]
                    self._record_entered_sight(unit, proto)
                elif unit._tag in self._in_fog_tags:
                    self._ignore_structure_fog_copy(unit._tag, previous, present)
                    self._record_entered_sight(unit, proto)
                unit._tag = tag
            unit._update(proto, step)
            present[tag] = unit
        return present, new

    def _record_entered_sight(self, unit: Unit[Any], proto: raw_pb2.Unit) -> None:
        """Record an enemy unit reported in sight again, having been out of the observation or in the fog."""
        if proto.alliance == _ENEMY and proto.display_type != _IN_FOG:
            self._last_changes.enemy_units_entered_sight.append(unit)

    def _ignore_structure_fog_copy(self, tag: int, previous: _UnitsByTag, present: _UnitsByTag) -> None:
        """Leave out, for good, the copy in the fog under `tag` of a structure that is back in vision.

        Tested in game: a structure that lifts off or uproots out of vision and is then seen elsewhere is reported in
        vision there while its copy in the fog stays listed where it was, until that spot is in vision too.
        """
        self._in_fog_tags.discard(tag)
        del self._by_tag[tag]
        self._ignored_tags.add(tag)
        previous.pop(tag, None)
        present.pop(tag, None)

    def _identify_new_tags(
        self, protos: list[raw_pb2.Unit], previous: _UnitsByTag, present: _UnitsByTag, step: int, dead: set[int]
    ) -> None:
        """Find the unit each proto under a new tag is: a structure passing between vision and fog, or a new unit.

        A structure going out of vision is replaced, in the same observation, by a copy in the fog at the same
        position under a new tag, and coming back into vision swaps the other way. The type is not compared, since a
        structure can change form between two observations as it goes, as a supply depot lowering does (in game).
        Alliance is, since a refinery stands at the same position as its geyser.
        """
        # What left this observation without dying or being updated under another tag, and the own units among them
        # that had an order, one of which may have become a structure.
        departed: dict[tuple[int, float, float], Unit[Any]] = {}
        ordered: list[Unit[Any]] = []
        for tag, unit in previous.items():
            if unit._step != step and tag not in dead:
                report = unit._latest_data
                departed[(report.alliance, report.pos.x, report.pos.y)] = unit
                if unit._own and report.orders:
                    ordered.append(unit)
        for proto in protos:
            display = proto.display_type
            unit = departed.pop((proto.alliance, proto.pos.x, proto.pos.y), None)
            if unit is not None and {display, unit._latest_data.display_type} == {_IN_VISION, _IN_FOG}:
                if proto.alliance == _ENEMY:
                    changes = self._last_changes
                    entered = display == _IN_VISION
                    sight = changes.enemy_units_entered_sight if entered else changes.enemy_units_left_sight
                    sight.append(unit)
                unit._tag = proto.tag
                unit._update(proto, step)
            else:
                unit = self._create_unit(proto, step)
                if ordered and unit._own:
                    self._construction.link_builder(unit, ordered)
            tag = proto.tag
            self._by_tag[tag] = unit
            self._ids[tag] = unit._id
            if display == _IN_FOG:
                self._in_fog_tags.add(tag)
            present[tag] = unit

    def _update_unfinished_units(self) -> None:
        """Record each of this player's unfinished units that has finished, and forget those and the dead."""
        for unit_id, unit in list(self._unfinished.items()):
            if unit._dead:
                del self._unfinished[unit_id]
            elif unit._latest_data.build_progress == 1.0:
                del self._unfinished[unit_id]
                self._last_changes.own_units_finished.append(unit)

    def _handle_departed_units(self, departed: _UnitsByTag, present: _UnitsByTag, step: int) -> None:
        """Unlink the tags that did not come back, and mark the units behind them stale, or dead where they must be."""
        for tag, unit in departed.items():
            if tag in self._in_fog_tags:
                self._in_fog_tags.discard(tag)
                del self._by_tag[tag]
                if unit._step != step and unit._raw_type not in _MOVABLE_UNIT_TYPE_IDS:
                    # Its spot came into vision without it: tested in game, the game reports no death it cannot see.
                    self._mark_unit_dead(unit, present, reported=False)
                    continue
            if unit._step != step and not unit._stale:
                report = unit._latest_data
                if report.alliance == _ENEMY and report.display_type != _IN_FOG:
                    self._last_changes.enemy_units_left_sight.append(unit)
                unit._mark_stale()
                self._stale_units[unit._id] = unit

    def _create_unit(self, proto: raw_pb2.Unit, step: int) -> Unit[Any]:
        """A unit first seen in `proto`, under the next id of its alliance."""
        alliance = proto.alliance
        count = self._units_seen_per_alliance[alliance] + 1
        if count >= _IDS_PER_ALLIANCE:
            raise NachOSError(f"a game has seen {_IDS_PER_ALLIANCE - 1} units of alliance {alliance}, all its ids")
        self._units_seen_per_alliance[alliance] = count
        unit_id = alliance * _IDS_PER_ALLIANCE + count
        changes = self._last_changes
        if alliance == _OWN:
            own: OwnUnit[Any] = OwnUnit(proto, self, unit_id, step)
            changes.own_units_created.append(own)
            if proto.build_progress < 1.0:
                self._unfinished[unit_id] = own
            unit: Unit[Any] = own
        else:
            unit = Unit(proto, self, unit_id, step)
            if alliance == _ENEMY:
                changes.enemy_units_first_seen.append(unit)
                if proto.display_type != _IN_FOG:
                    changes.enemy_units_entered_sight.append(unit)
        self._units_by_id[unit_id] = unit
        return unit

    def _mark_unit_dead(self, unit: Unit[Any], present: _UnitsByTag, *, reported: bool) -> None:
        """Mark `unit` dead and let go of it: `reported` dead, or found dead, such as gone from a spot it could not have
        left."""
        # Its tags: the one it was last reported under, and the one it was last in vision under.
        sighting = unit._latest_data_in_vision
        tags = {unit._tag} if sighting is None else {unit._tag, sighting.tag}
        unit._mark_dead()
        changes = self._last_changes
        (changes.units_died if reported else changes.units_found_dead).append(unit)
        self._stale_units.pop(unit._id, None)
        for tag in tags:
            if self._by_tag.get(tag) is unit:
                del self._by_tag[tag]
            self._in_fog_tags.discard(tag)
            present.pop(tag, None)

    def end(self) -> None:
        """Mark every unit stale, since the game is over."""
        for unit in self._present_units_by_tag.values():
            unit._mark_stale()
        self._set_present_units({})
        self._last_changes = _TrackerChanges()
        self._unfinished = {}
        self._construction.end()
        self._comparison.stop()
