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
    from sc2nachos.gamedata import GameData

_VISIBLE = raw_pb2.DisplayType.Visible
_REMEMBERED = raw_pb2.DisplayType.Snapshot
_MINE = raw_pb2.Alliance.Self
# An id is its alliance's digit followed by this many digits counting that alliance's units.
_PER_ALLIANCE = 100_000
# The types that can leave the spot they are remembered at, by lifting off or uprooting: the performers the curated
# `*_LIFT` and `*_UPROOT` abilities name, which every other ability name puts first too.
_MOVABLE = frozenset(
    UnitTypeId[performer].value
    for ability in AbilityId
    if ability.name.endswith(("_LIFT", "_UPROOT"))
    and (performer := ability.name.rsplit("_", 1)[0]) in UnitTypeId.__members__
)


class _UnitTracker:
    """The units of one game: one object under one id, whatever tags the game reports it under."""

    __slots__ = (
        "_by_tag",
        "_counts",
        "_data",
        "_ghosts",
        "_ids",
        "_known",
        "_present",
        "_remembered",
        "_stale",
        "_units",
    )

    def __init__(self, data: GameData) -> None:
        self._data = data
        # Every tag of a unit not dead: a visible tag for good, a remembered one until it leaves.
        self._by_tag: dict[int, Unit[Any]] = {}
        # The remembered tags among them, which never come back once they leave.
        self._remembered: set[int] = set()
        # Remembered tags of units seen since somewhere else, left out for as long as the game goes on listing them.
        self._ghosts: set[int] = set()
        # Every tag ever linked, dead units' included, to the id it names.
        self._ids: dict[int, int] = {}
        # The last observation's units, by the tag it reported each under.
        self._present: dict[int, Unit[Any]] = {}
        # The units out of the observation and not dead, by id.
        self._stale: dict[int, Unit[Any]] = {}
        self._counts = [0] * 5
        self._units: Units[Unit[Any]] = Units()
        self._known: Units[Unit[Any]] | None = None

    @property
    def data(self) -> GameData:
        """The tables the game is played by."""
        return self._data

    @property
    def units(self) -> Units[Unit[Any]]:
        """Every unit in the last observation, in the order the game listed them."""
        return self._units

    @property
    def known_units(self) -> Units[Unit[Any]]:
        """Every unit not known to be dead: those in the last observation, then the stale ones."""
        if self._known is None:
            self._known = Units((*self._units, *self._stale.values()))
        return self._known

    def identify(self, tag: int) -> int:
        """The id of the unit the game reported under `tag`."""
        try:
            return self._ids[tag]
        except KeyError:
            raise UnknownTagError(f"the game never reported a unit under tag {tag}") from None

    def observe(self, observation: raw_pb2.ObservationRaw, step: int) -> None:
        """Take in the units the observation at `step` reports, and what it says died."""
        previous = self._present
        present: dict[int, Unit[Any]] = {}
        by_tag = self._by_tag
        stale = self._stale
        ghosts = self._ghosts
        take = previous.pop
        unknown: list[raw_pb2.Unit] = []
        for proto in observation.units:
            tag = proto.tag
            if not tag:
                # A radar blip or a structure placed but not yet started, neither of which is a unit yet.
                continue
            unit = take(tag, None)
            if unit is None:
                if tag in ghosts:
                    continue
                unit = by_tag.get(tag)
                if unit is None:
                    unknown.append(proto)
                    continue
                if unit._stale:
                    del stale[unit._id]
                elif unit._tag in self._remembered:
                    self._haunt(unit._tag, previous, present)
                unit._tag = tag
            unit._observe(proto, step)
            present[tag] = unit
        if unknown:
            self._place(unknown, previous, present, step, set(observation.event.dead_units))
            # Placed after the rest, so put back in the order the game listed them.
            present = {proto.tag: present[proto.tag] for proto in observation.units if proto.tag in present}
        for tag, unit in previous.items():
            if tag in self._remembered:
                self._remembered.discard(tag)
                del by_tag[tag]
                if unit._step != step and unit._raw_type not in _MOVABLE:
                    # Its spot came into sight without it: tested in game, the game reports no death it cannot see.
                    self._bury(unit, present)
                    continue
            if unit._step != step and not unit._stale:
                unit._leave()
                stale[unit._id] = unit
        for tag in observation.event.dead_units:
            if (unit := by_tag.get(tag)) is not None:
                self._bury(unit, present)
        self._present = present
        self._units = Units(present.values())
        self._known = None

    def _haunt(self, tag: int, previous: dict[int, Unit[Any]], present: dict[int, Unit[Any]]) -> None:
        """Leave out, for good, the remembered copy under `tag` of a unit that is back in sight.

        Tested in game: a structure that lifts off or uproots out of sight and is then seen elsewhere is reported in
        sight there while its remembered copy stays listed where it was, until that spot is seen too.
        """
        self._remembered.discard(tag)
        del self._by_tag[tag]
        self._ghosts.add(tag)
        previous.pop(tag, None)
        present.pop(tag, None)

    def _place(
        self,
        protos: list[raw_pb2.Unit],
        previous: dict[int, Unit[Any]],
        present: dict[int, Unit[Any]],
        step: int,
        dead: set[int],
    ) -> None:
        """Find the unit each proto under a new tag is: one swapping between sight and memory, or a new one.

        A structure going out of sight is replaced, in the same observation, by a remembered copy at the same
        position under a new tag, and coming back into sight swaps the other way. The type is not compared, since a
        structure can change form between two observations as it goes, as a supply depot lowering does (in game).
        Alliance is, since a refinery stands at the same position as its geyser.
        """
        # What left this observation without dying or being observed again under another tag.
        departed: dict[tuple[int, float, float], Unit[Any]] = {}
        for tag, unit in previous.items():
            if unit._step != step and tag not in dead:
                now = unit._now
                departed[(now.alliance, now.pos.x, now.pos.y)] = unit
        for proto in protos:
            display = proto.display_type
            unit = departed.pop((proto.alliance, proto.pos.x, proto.pos.y), None)
            swapped = unit is not None and {display, unit._now.display_type} == {_VISIBLE, _REMEMBERED}
            if unit is not None and swapped:
                unit._tag = proto.tag
                unit._observe(proto, step)
            else:
                unit = self._create(proto, step)
            tag = proto.tag
            self._by_tag[tag] = unit
            self._ids[tag] = unit._id
            if display == _REMEMBERED:
                self._remembered.add(tag)
            present[tag] = unit

    def _create(self, proto: raw_pb2.Unit, step: int) -> Unit[Any]:
        """A unit first seen in `proto`, under the next id of its alliance."""
        alliance = proto.alliance
        count = self._counts[alliance] + 1
        if count >= _PER_ALLIANCE:
            raise NachOSError(f"a game has seen {_PER_ALLIANCE - 1} units of alliance {alliance}, all its ids")
        self._counts[alliance] = count
        cls = OwnUnit if alliance == _MINE else Unit
        return cls(proto, self, alliance * _PER_ALLIANCE + count, step)

    def _bury(self, unit: Unit[Any], present: dict[int, Unit[Any]]) -> None:
        """Let go of `unit`, which the game reported dead, or which is gone from a spot it could not have left."""
        # Its tags: the one it was last reported under, and the one it was last seen in sight under.
        tags = {unit._tag} if unit._seen is None else {unit._tag, unit._seen.tag}
        unit._die()
        self._stale.pop(unit._id, None)
        for tag in tags:
            if self._by_tag.get(tag) is unit:
                del self._by_tag[tag]
            self._remembered.discard(tag)
            present.pop(tag, None)

    def end(self) -> None:
        """Mark every unit stale, since the game is over."""
        for unit in self._present.values():
            unit._leave()
        self._present = {}
        self._units = Units()
        self._known = None
