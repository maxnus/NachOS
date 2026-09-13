"""Which unit each tag in a game's observations is."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from s2clientprotocol import raw_pb2

from sc2nachos._errors import NachOSError
from sc2nachos.ids import AbilityId, UnitTypeId
from sc2nachos.units._errors import UnknownTagError
from sc2nachos.units._own_unit import OwnUnit
from sc2nachos.units._unit import Unit
from sc2nachos.units._units import Units

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sc2nachos.gamedata import GameData

_IN_VISION = raw_pb2.DisplayType.Visible
_IN_FOG = raw_pb2.DisplayType.Snapshot
_OWN = raw_pb2.Alliance.Self
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
        "_data",
        "_ignored_tags",
        "_ids",
        "_in_fog_tags",
        "_known_units",
        "_present_units",
        "_present_units_by_tag",
        "_stale_units",
        "_units_seen_per_alliance",
    )

    def __init__(self, data: GameData) -> None:
        self._data = data
        # Every tag of a unit not dead: a tag in vision for good, a tag in the fog until it leaves.
        self._by_tag: _UnitsByTag = {}
        # The tags among them of structures in the fog, which never come back once they leave.
        self._in_fog_tags: set[int] = set()
        # Tags in the fog of structures seen since elsewhere, left out for as long as the game goes on listing them.
        self._ignored_tags: set[int] = set()
        # Every tag ever linked, dead units' included, to the id it names.
        self._ids: dict[int, int] = {}
        # The last observation's units, by the tag it reported each under, and as a collection.
        self._present_units_by_tag: _UnitsByTag = {}
        self._present_units: Units[Unit[Any]] = Units()
        # The units out of the observation and not dead, by id.
        self._stale_units: dict[int, Unit[Any]] = {}
        self._known_units: Units[Unit[Any]] | None = None
        # How many units of each alliance have been seen, indexed by the alliance's value, from which ids are made.
        self._units_seen_per_alliance = [0] * 5

    @property
    def data(self) -> GameData:
        """The tables the game is played by."""
        return self._data

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

    def identify(self, tag: int) -> int:
        """The id of the unit the game reported under `tag`."""
        try:
            return self._ids[tag]
        except KeyError:
            raise UnknownTagError(f"the game never reported a unit under tag {tag}") from None

    def update(self, observation: raw_pb2.ObservationRaw, step: int) -> None:
        """Take in the units the observation at `step` reports, and what it says died."""
        previous = self._present_units_by_tag
        dead = observation.event.dead_units
        present, new = self._update_known_tags(observation.units, previous, step)
        if new:
            self._identify_new_tags(new, previous, present, step, set(dead))
            # Placed after the rest, so put back in the order the game listed them.
            present = {proto.tag: present[proto.tag] for proto in observation.units if proto.tag in present}
        self._handle_departures(previous, present, step)
        for tag in dead:
            if (unit := self._by_tag.get(tag)) is not None:
                self._mark_dead(unit, present)
        self._present_units_by_tag = present
        self._present_units = Units(present.values())
        self._known_units = None

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
                elif unit._tag in self._in_fog_tags:
                    self._ignore_fog_copy(unit._tag, previous, present)
                unit._tag = tag
            unit._update(proto, step)
            present[tag] = unit
        return present, new

    def _ignore_fog_copy(self, tag: int, previous: _UnitsByTag, present: _UnitsByTag) -> None:
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
        # What left this observation without dying or being updated under another tag.
        departed: dict[tuple[int, float, float], Unit[Any]] = {}
        for tag, unit in previous.items():
            if unit._step != step and tag not in dead:
                report = unit._latest_data
                departed[(report.alliance, report.pos.x, report.pos.y)] = unit
        for proto in protos:
            display = proto.display_type
            unit = departed.pop((proto.alliance, proto.pos.x, proto.pos.y), None)
            if unit is not None and {display, unit._latest_data.display_type} == {_IN_VISION, _IN_FOG}:
                unit._tag = proto.tag
                unit._update(proto, step)
            else:
                unit = self._create_unit(proto, step)
            tag = proto.tag
            self._by_tag[tag] = unit
            self._ids[tag] = unit._id
            if display == _IN_FOG:
                self._in_fog_tags.add(tag)
            present[tag] = unit

    def _handle_departures(self, departed: _UnitsByTag, present: _UnitsByTag, step: int) -> None:
        """Unlink the tags that did not come back, and mark the units behind them stale, or dead where they must be."""
        for tag, unit in departed.items():
            if tag in self._in_fog_tags:
                self._in_fog_tags.discard(tag)
                del self._by_tag[tag]
                if unit._step != step and unit._raw_type not in _MOVABLE_UNIT_TYPE_IDS:
                    # Its spot came into vision without it: tested in game, the game reports no death it cannot see.
                    self._mark_dead(unit, present)
                    continue
            if unit._step != step and not unit._stale:
                unit._mark_stale()
                self._stale_units[unit._id] = unit

    def _create_unit(self, proto: raw_pb2.Unit, step: int) -> Unit[Any]:
        """A unit first seen in `proto`, under the next id of its alliance."""
        alliance = proto.alliance
        count = self._units_seen_per_alliance[alliance] + 1
        if count >= _IDS_PER_ALLIANCE:
            raise NachOSError(f"a game has seen {_IDS_PER_ALLIANCE - 1} units of alliance {alliance}, all its ids")
        self._units_seen_per_alliance[alliance] = count
        cls = OwnUnit if alliance == _OWN else Unit
        return cls(proto, self, alliance * _IDS_PER_ALLIANCE + count, step)

    def _mark_dead(self, unit: Unit[Any], present: _UnitsByTag) -> None:
        """Mark `unit` dead and let go of it: reported dead, or gone from a spot it could not have left."""
        # Its tags: the one it was last reported under, and the one it was last in vision under.
        sighting = unit._latest_data_in_vision
        tags = {unit._tag} if sighting is None else {unit._tag, sighting.tag}
        unit._mark_dead()
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
        self._present_units_by_tag = {}
        self._present_units = Units()
        self._known_units = None
