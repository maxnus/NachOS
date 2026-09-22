"""Everything one game's observations are read into."""

from __future__ import annotations

from typing import TYPE_CHECKING, final

from sc2nachos.units._tracking._builder_tracker import _BuilderTracker
from sc2nachos.units._tracking._tracker_changes import _TrackerChanges
from sc2nachos.units._tracking._unit_comparer import _UnitComparer
from sc2nachos.units._tracking._unit_tracker import _UnitTracker
from sc2nachos.units._tracking._unit_watcher import _UnitWatcher
from sc2nachos.units._tracking._upgrade_tracker import _UpgradeTracker

if TYPE_CHECKING:
    from s2clientprotocol import raw_pb2

    from sc2nachos.enemy import Enemy
    from sc2nachos.gamedata import GameData


@final
class _Tracker:
    """One game's observations read into: its units, the builder of each of this player's structures, the upgrades
    each side has, a comparison of each unit with the previous update, a watch on the values each unit crosses, and
    what the last update found changed."""

    __slots__ = (
        "_builder_tracker",
        "_enemy",
        "_game_data",
        "_last_changes",
        "_unit_comparer",
        "_unit_tracker",
        "_unit_watcher",
        "_upgrade_tracker",
    )

    def __init__(self, game_data: GameData, enemy: Enemy) -> None:
        self._game_data = game_data
        self._enemy = enemy
        # What the last update found changed, numbered by update.
        self._last_changes = _TrackerChanges(0)
        self._unit_tracker = _UnitTracker(self)
        self._builder_tracker = _BuilderTracker(self)
        self._upgrade_tracker = _UpgradeTracker(game_data, enemy)
        self._unit_comparer = _UnitComparer(self)
        self._unit_watcher = _UnitWatcher(self)

    @property
    def game_data(self) -> GameData:
        """The game's data tables."""
        return self._game_data

    @property
    def enemy(self) -> Enemy:
        """The other player, and what is known of it."""
        return self._enemy

    @property
    def last_changes(self) -> _TrackerChanges:
        """What the last update found changed.

        The game also reports deaths under tags it never reported a unit under (corpus). Those name no unit.
        """
        return self._last_changes

    @property
    def unit_tracker(self) -> _UnitTracker:
        """Which unit each tag is, and which units are in the last observation, out of it, or dead."""
        return self._unit_tracker

    @property
    def builder_tracker(self) -> _BuilderTracker:
        """The builder of each of this player's structures under construction."""
        return self._builder_tracker

    @property
    def upgrade_tracker(self) -> _UpgradeTracker:
        """The upgrades each side's units have, and what they make of a unit's type."""
        return self._upgrade_tracker

    @property
    def unit_comparer(self) -> _UnitComparer:
        """Compares each unit with the previous update, recording into the last changes, on request."""
        return self._unit_comparer

    @property
    def unit_watcher(self) -> _UnitWatcher:
        """Watches each unit for crossing a value of its energy or life, or the edge of an area, recording into the
        last changes, on request."""
        return self._unit_watcher

    def update(self, observation: raw_pb2.ObservationRaw, step: int) -> None:
        """Take in the observation at `step`: this player's upgrades, then the units it reports and the deaths.

        Raises `UncuratedIdError` if this player holds an upgrade, or a unit is of a type, that the curated ids leave
        out. Those belong among the curated ids.
        """
        self._last_changes = _TrackerChanges(self._last_changes.update + 1)
        if new_upgrades := self._upgrade_tracker.update(observation.player):
            self._last_changes.own_upgrades_finished = new_upgrades
        self._unit_tracker.update(observation, step)

    def end(self) -> None:
        """Mark every unit stale and forget what was kept for the next update. The game is over."""
        self._unit_tracker.end()
        self._builder_tracker.end()
        self._unit_comparer.stop()
        self._unit_watcher.stop()
        self._last_changes = _TrackerChanges(self._last_changes.update)
