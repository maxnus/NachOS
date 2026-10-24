"""A replay played back with the fog off, read as two views: what its player knew, and every unit."""

from __future__ import annotations

import itertools
from typing import TYPE_CHECKING, Final

from s2clientprotocol import raw_pb2, sc2api_pb2

from sc2nachos.gamemap._image_data import image_array
from sc2nachos.geometry import Rectangle

if TYPE_CHECKING:
    from collections.abc import Iterator

    from numpy import ndarray
    from s2clientprotocol import common_pb2

_VISIBLE = raw_pb2.DisplayType.Visible
_HIDDEN = raw_pb2.DisplayType.Hidden
_IN_FOG = raw_pb2.DisplayType.Snapshot
_OWN = raw_pb2.Alliance.Self
_ENEMY = raw_pb2.Alliance.Enemy
# A tile the player sees, in the game's visibility image.
_TILE_IN_SIGHT: Final = 2
# The first tag of the copies the player's view lists of units in the fog, far above any the game gives.
_FIRST_COPY_TAG: Final = 1 << 62


# With the fog off, the game lists every unit, those out of the player's sight under their own tags and in full (in
# game). With the fog on, it lists of those only the structures and the map's units the player knows of, each a copy
# under a tag of its own, remembered as last seen, until their spot is in sight (in game). The player's view makes
# those copies, so that the tracker reads it as it reads a game.
class _FogOffPlayback:
    """What the player of a replay played back with the fog off was shown, read from each observation in turn as the
    fog on would have shown it."""

    __slots__ = ("_copy_tags", "_enemy_tags_seen", "_real_tags_of_copies", "_remembered")

    def __init__(self) -> None:
        """Read a playback from its first observation."""
        # The tags of the enemy units the player has seen.
        self._enemy_tags_seen: set[int] = set()
        # The copy of each unit the player remembers in the fog, by the unit's tag: its report as it went into the fog,
        # under a tag of its own.
        self._remembered: dict[int, raw_pb2.Unit] = {}
        # The unit's tag behind each copy's.
        self._real_tags_of_copies: dict[int, int] = {}
        self._copy_tags: Iterator[int] = itertools.count(_FIRST_COPY_TAG)

    def real_tag(self, tag: int) -> int:
        """The game's tag of the unit the player's view lists under `tag`: a copy's unit's, or `tag` itself."""
        return self._real_tags_of_copies.get(tag, tag)

    def player_view(self, fog_off: sc2api_pb2.ResponseObservation) -> sc2api_pb2.ResponseObservation:
        """`fog_off` as the player would have been shown it with the fog on: without the enemy units it does not see,
        and with the units it remembers in the fog listed as copies. Call it once for each observation, in order."""
        view = sc2api_pb2.ResponseObservation()
        view.CopyFrom(fog_off)
        raw = view.observation.raw_data
        listed: list[raw_pb2.Unit] = []
        listed_tags: set[int] = set()
        shown_visible: set[int] = set()
        for unit in fog_off.observation.raw_data.units:
            alliance = unit.alliance
            display = unit.display_type
            listed_tags.add(unit.tag)
            if alliance == _OWN:
                listed.append(unit)
            elif display == _VISIBLE:
                shown_visible.add(unit.tag)
                if alliance == _ENEMY:
                    self._enemy_tags_seen.add(unit.tag)
                self._forget(unit.tag)
                listed.append(unit)
            elif display == _IN_FOG:
                # The map's units are known from the start; an enemy's, once seen.
                known = alliance != _ENEMY or unit.tag in self._enemy_tags_seen
                if known and unit.tag and unit.tag not in self._remembered:
                    self._remember(unit)
            elif display == _HIDDEN and _is_undetected_in_sight(unit):
                # An enemy's placeholder is in its own view alone (in game).
                listed.append(unit)
        in_sight = None
        for tag, copy in list(self._remembered.items()):
            if tag in shown_visible:
                continue
            if tag not in listed_tags:
                # Gone, dead or moved off: remembered until its spot is in sight.
                if in_sight is None:
                    in_sight = _tiles_in_sight(raw.map_state.visibility)
                if _stands_in_sight(copy, in_sight):
                    self._forget(tag)
                    continue
            listed.append(copy)
        del raw.units[:]
        raw.units.extend(listed)
        return view

    def _remember(self, unit: raw_pb2.Unit) -> None:
        """Remember `unit`, gone into the fog, as it is now, under a tag of its own."""
        copy = raw_pb2.Unit()
        copy.CopyFrom(unit)
        copy.tag = next(self._copy_tags)
        self._remembered[unit.tag] = copy
        self._real_tags_of_copies[copy.tag] = unit.tag

    def _forget(self, tag: int) -> None:
        """Forget the copy of the unit under `tag`, if it is remembered."""
        if (copy := self._remembered.pop(tag, None)) is not None:
            del self._real_tags_of_copies[copy.tag]


def every_unit_in_full(fog_off: sc2api_pb2.ResponseObservation) -> raw_pb2.ObservationRaw:
    """The units and deaths of `fog_off`, each unit out of the player's sight shown visible, so that it is read in full:
    the game reports such a unit in full with the fog off (in game). One undetected in the player's sight reports no
    health and the like (in game), and is shown as reported."""
    raw = fog_off.observation.raw_data
    every_unit = raw_pb2.ObservationRaw(player=raw.player, event=raw.event)
    for unit in raw.units:
        display = unit.display_type
        if display == _IN_FOG or (display == _HIDDEN and not _is_undetected_in_sight(unit)):
            shown = every_unit.units.add()
            shown.CopyFrom(unit)
            shown.display_type = _VISIBLE
        else:
            every_unit.units.append(unit)
    return every_unit


def _is_undetected_in_sight(unit: raw_pb2.Unit) -> bool:
    """Whether `unit`, listed hidden, is undetected in the player's sight: the game then reports no owner for it, where
    one out of its sight with the fog off reports its owner, though the tile under it may read in sight (in game)."""
    return not unit.owner


def _tiles_in_sight(visibility: common_pb2.ImageData) -> ndarray:
    """Whether the player sees each tile of the map, indexed `[x, y]`."""
    return image_array(visibility, Rectangle(0, 0, visibility.size.x, visibility.size.y)) == _TILE_IN_SIGHT


def _stands_in_sight(unit: raw_pb2.Unit, in_sight: ndarray) -> bool:
    """Whether the tile under `unit`'s center is in the player's sight."""
    return bool(in_sight[int(unit.pos.x), int(unit.pos.y)])
