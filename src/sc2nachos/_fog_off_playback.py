"""A replay played back with the fog off, read as two views: what its player knew, and every unit."""

from __future__ import annotations

import itertools
from typing import TYPE_CHECKING, Final

from s2clientprotocol import raw_pb2, sc2api_pb2

from sc2nachos.gamemap._image_data import image_array
from sc2nachos.geometry import Grid, Rectangle
from sc2nachos.units._tracking._tracker import _Tracker

if TYPE_CHECKING:
    from collections.abc import Iterator

    from s2clientprotocol import common_pb2

    from sc2nachos.enemy import Enemy
    from sc2nachos.gamedata import GameData

_VISIBLE_DISPLAY = raw_pb2.DisplayType.Visible
_HIDDEN_DISPLAY = raw_pb2.DisplayType.Hidden
_IN_FOG_DISPLAY = raw_pb2.DisplayType.Snapshot
_OWN_ALLIANCE = raw_pb2.Alliance.Self
_ENEMY_ALLIANCE = raw_pb2.Alliance.Enemy
# A tile the player sees, in the game's visibility image.
_TILE_IN_SIGHT: Final = 2
# The first tag of the copies the player's view lists of units in the fog, far above any the game gives.
_FIRST_COPY_TAG: Final = 1 << 62


# With the fog off, the game lists every unit, those out of the player's sight under their own tags and in full (in
# game). With the fog on, it lists of those only the structures and the map's units the player knows of, each a copy
# under a tag of its own, remembered as last seen, until their spot is in sight (in game). The player's view makes
# those copies, so that the tracker reads it as it reads a game.
class _FogOffPlayback:
    """A replay played back with the fog off, read from each observation in turn as two views: what its player was
    shown, as the fog on would have shown it, and every unit, in a tracker of its own."""

    __slots__ = (
        "_copies_by_unit_tag",
        "_copy_tags",
        "_enemy_tags_seen",
        "_real_tags_of_copies",
        "_whole_game_tracker",
    )

    def __init__(self, game_data: GameData, enemy: Enemy | None) -> None:
        """Read a playback of a game whose tables are `game_data`, against `enemy`, from its first observation."""
        self._whole_game_tracker = _Tracker(game_data, enemy)
        # The tags of the enemy units the player has seen.
        self._enemy_tags_seen: set[int] = set()
        # The copy of each unit the player remembers in the fog, by the unit's tag: its report as it went into the fog,
        # under a tag of its own.
        self._copies_by_unit_tag: dict[int, raw_pb2.Unit] = {}
        # The unit's tag behind each copy's.
        self._real_tags_of_copies: dict[int, int] = {}
        self._copy_tags: Iterator[int] = itertools.count(_FIRST_COPY_TAG)

    @property
    def whole_game_tracker(self) -> _Tracker:
        """Every unit of the game, each read in full."""
        return self._whole_game_tracker

    def read(self, fog_off_observation: sc2api_pb2.ResponseObservation, step: int) -> sc2api_pb2.ResponseObservation:
        """Take every unit of `fog_off_observation`, the observation at `step`, into the whole game's tracker, and
        return what the player was shown of it. Call it once for each observation, in order."""
        self._whole_game_tracker.update(_raw_data_with_unseen_units_shown_visible(fog_off_observation), step)
        return self.player_view(fog_off_observation)

    def id_of_unit(self, unit: raw_pb2.Unit) -> int:
        """The id the whole game's tracker gives the unit the player's view lists as `unit`: a copy's unit's, or
        `unit`'s own."""
        real_tag = self._real_tags_of_copies.get(unit.tag, unit.tag)
        return self._whole_game_tracker.unit_tracker.by_tag(real_tag).id

    def player_view(self, fog_off_observation: sc2api_pb2.ResponseObservation) -> sc2api_pb2.ResponseObservation:
        """`fog_off_observation` as the player would have been shown it with the fog on: without the enemy units it
        does not see, and with the units it remembers in the fog listed as copies. Call it once for each observation,
        in order."""
        player_observation = sc2api_pb2.ResponseObservation()
        player_observation.CopyFrom(fog_off_observation)
        raw_data = player_observation.observation.raw_data
        units_listed: list[raw_pb2.Unit] = []
        positions_listed: dict[int, tuple[float, float]] = {}
        shown_visible: set[int] = set()
        for unit in fog_off_observation.observation.raw_data.units:
            alliance = unit.alliance
            display = unit.display_type
            positions_listed[unit.tag] = (unit.pos.x, unit.pos.y)
            if alliance == _OWN_ALLIANCE:
                units_listed.append(unit)
            elif display == _VISIBLE_DISPLAY:
                shown_visible.add(unit.tag)
                if alliance == _ENEMY_ALLIANCE:
                    self._enemy_tags_seen.add(unit.tag)
                self._forget_copy_of(unit.tag)
                units_listed.append(unit)
            elif display == _IN_FOG_DISPLAY:
                # The map's units are known from the start; an enemy's, once seen.
                known_to_the_player = alliance != _ENEMY_ALLIANCE or unit.tag in self._enemy_tags_seen
                if known_to_the_player and unit.tag and unit.tag not in self._copies_by_unit_tag:
                    self._remember_copy_of(unit)
            elif display == _HIDDEN_DISPLAY and not unit.owner:
                # Undetected in the player's sight: the game reports no owner for it then, where one out of its sight
                # reports its owner, though the tile under it may read in sight (in game).
                units_listed.append(unit)
            # An enemy's placeholder, a structure ordered and not yet placed, is in its own view alone (in game).
        tiles_in_sight = None
        for unit_tag, remembered_copy in list(self._copies_by_unit_tag.items()):
            if unit_tag in shown_visible:
                continue
            if positions_listed.get(unit_tag) != (remembered_copy.pos.x, remembered_copy.pos.y):
                # Gone from its spot, dead or moved off: remembered until the spot is in sight.
                if tiles_in_sight is None:
                    tiles_in_sight = _tiles_in_sight(raw_data.map_state.visibility)
                if tiles_in_sight[remembered_copy.pos.x, remembered_copy.pos.y]:
                    self._forget_copy_of(unit_tag)
                    continue
            units_listed.append(remembered_copy)
        del raw_data.units[:]
        raw_data.units.extend(units_listed)
        return player_observation

    def _remember_copy_of(self, unit: raw_pb2.Unit) -> None:
        """Remember `unit`, gone into the fog, as it is now, as a copy under a tag of its own."""
        remembered_copy = raw_pb2.Unit()
        remembered_copy.CopyFrom(unit)
        remembered_copy.tag = next(self._copy_tags)
        self._copies_by_unit_tag[unit.tag] = remembered_copy
        self._real_tags_of_copies[remembered_copy.tag] = unit.tag

    def _forget_copy_of(self, unit_tag: int) -> None:
        """Forget the copy of the unit under `unit_tag`, if one is remembered."""
        if (remembered_copy := self._copies_by_unit_tag.pop(unit_tag, None)) is not None:
            del self._real_tags_of_copies[remembered_copy.tag]


def _raw_data_with_unseen_units_shown_visible(
    fog_off_observation: sc2api_pb2.ResponseObservation,
) -> raw_pb2.ObservationRaw:
    """The units and deaths of `fog_off_observation`, for the whole game's tracker, with each unit the player does not
    see shown visible.

    A tracker reads a unit's health and the like from the last report that lists it visible, and with the fog off the
    game lists a unit out of the player's sight hidden, or a structure there in the fog, yet reports it in full (in
    game). Shown visible, such a unit is read as it is. A unit undetected in the player's sight reports no owner, and no
    health and the like, with the fog off too (in game): it keeps its display, and so reads as last reported in full.
    """
    raw_data = fog_off_observation.observation.raw_data
    raw_data_shown = raw_pb2.ObservationRaw(player=raw_data.player, event=raw_data.event)
    for unit in raw_data.units:
        display = unit.display_type
        # A hidden unit that reports its owner is out of the player's sight, not undetected in it (in game).
        if display == _IN_FOG_DISPLAY or (display == _HIDDEN_DISPLAY and unit.owner):
            unit_shown = raw_data_shown.units.add()
            unit_shown.CopyFrom(unit)
            unit_shown.display_type = _VISIBLE_DISPLAY
        else:
            raw_data_shown.units.append(unit)
    return raw_data_shown


def _tiles_in_sight(visibility: common_pb2.ImageData) -> Grid[bool]:
    """Whether the player sees each tile of the map."""
    whole_map = Rectangle(0, 0, visibility.size.x, visibility.size.y)
    return Grid(image_array(visibility, whole_map) == _TILE_IN_SIGHT, outside=False)
