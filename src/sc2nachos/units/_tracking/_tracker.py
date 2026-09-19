"""Everything one game's observations are read into, part by part."""

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
    """One game's observations read into its units, the builder of each of this player's structures, the upgrades
    each side has, a comparison of each unit with the update before, and a watch on what it crosses, with what the last
    update found changed."""

    __slots__ = (
        "_builders",
        "_comparer",
        "_data",
        "_enemy",
        "_last_changes",
        "_units",
        "_upgrades",
        "_watcher",
    )

    def __init__(self, data: GameData, enemy: Enemy) -> None:
        self._data = data
        self._enemy = enemy
        # What the last update found changed, and which update it was.
        self._last_changes = _TrackerChanges(0)
        self._units = _UnitTracker(self)
        self._builders = _BuilderTracker(self)
        self._upgrades = _UpgradeTracker(data, enemy)
        self._comparer = _UnitComparer(self)
        self._watcher = _UnitWatcher(self)

    @property
    def data(self) -> GameData:
        """The tables the game is played by."""
        return self._data

    @property
    def enemy(self) -> Enemy:
        """The other player of the game, and what is known of it."""
        return self._enemy

    @property
    def last_changes(self) -> _TrackerChanges:
        """What the last update found changed.

        The game also reports deaths under tags it never reported a unit under (corpus), which name no unit.
        """
        return self._last_changes

    @property
    def units(self) -> _UnitTracker:
        """Which unit each tag is, and which units are in the last observation, out of it, or dead."""
        return self._units

    @property
    def builders(self) -> _BuilderTracker:
        """The builder of each of this player's structures being built."""
        return self._builders

    @property
    def upgrades(self) -> _UpgradeTracker:
        """The upgrades each side's units have, and what they make of a unit's type."""
        return self._upgrades

    @property
    def comparer(self) -> _UnitComparer:
        """Each unit compared with the update before, into the last changes, while something asks."""
        return self._comparer

    @property
    def watcher(self) -> _UnitWatcher:
        """Each unit watched for crossing a value of its energy or its life, or the edge of an area, into the last
        changes, while something asks."""
        return self._watcher

    def update(self, observation: raw_pb2.ObservationRaw, step: int) -> None:
        """Take in the observation at `step`: this player's upgrades, then the units it reports and what it says died.

        Raises `UncuratedIdError` where this player holds an upgrade, or a unit is of a type, the curated ids leave
        out, which belongs among them.
        """
        self._last_changes = _TrackerChanges(self._last_changes.update + 1)
        if new_upgrades := self._upgrades.update(observation.player):
            self._last_changes.own_upgrades_finished = new_upgrades
        self._units.update(observation, step)

    def end(self) -> None:
        """Mark every unit stale, and forget what was kept for the next update, since the game is over."""
        self._units.end()
        self._builders.end()
        self._comparer.stop()
        self._watcher.stop()
        self._last_changes = _TrackerChanges(self._last_changes.update)
