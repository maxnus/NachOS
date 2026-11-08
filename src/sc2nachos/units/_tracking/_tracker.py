"""Everything one game's observations are read into."""

from __future__ import annotations

from typing import TYPE_CHECKING, final

from sc2nachos.units._tracking._builder_tracker import _BuilderTracker
from sc2nachos.units._tracking._morph_tracker import _MorphTracker
from sc2nachos.units._tracking._tracker_changes import _TrackerChanges
from sc2nachos.units._tracking._unit_comparer import _UnitComparer
from sc2nachos.units._tracking._unit_tracker import _UnitTracker
from sc2nachos.units._tracking._unit_watcher import _UnitWatcher
from sc2nachos.units._tracking._upgrade_tracker import _UpgradeTracker
from sc2nachos.units._tracking._warp_in_tracker import _WarpInTracker

if TYPE_CHECKING:
    from collections.abc import Callable

    from s2clientprotocol import raw_pb2

    from sc2nachos.gamedata import GameData
    from sc2nachos.match import Enemy
    from sc2nachos.orders import OrderBook


@final
class _Tracker:
    """One game's observations read into: its units, the builder of each of this player's structures, when each morph
    and warp-in began, the upgrades each side has, a comparison of each unit with the previous update, a watch on the
    values each unit crosses, and what the last update found changed; and the book this player's units are ordered
    into."""

    __slots__ = (
        "_builder_tracker",
        "_enemy",
        "_game_data",
        "_last_changes",
        "_morph_tracker",
        "_order_book",
        "_unit_comparer",
        "_unit_tracker",
        "_unit_watcher",
        "_upgrade_tracker",
        "_warp_in_tracker",
    )

    def __init__(
        self,
        game_data: GameData,
        enemy: Enemy | None,
        *,
        id_provider: Callable[[raw_pb2.Unit], int] | None = None,
        order_book: OrderBook,
    ) -> None:
        """Read the observations of a game whose tables are `game_data`, against `enemy`. Each unit is made under its
        alliance's next id, or under the one `id_provider` gives the report it is first seen in. This player's units
        are ordered into `order_book`."""
        self._game_data = game_data
        self._enemy = enemy
        self._order_book = order_book
        # What the last update found changed, numbered by update.
        self._last_changes = _TrackerChanges(0)
        self._unit_tracker = _UnitTracker(self, id_provider=id_provider)
        self._builder_tracker = _BuilderTracker(self)
        self._morph_tracker = _MorphTracker(self)
        self._warp_in_tracker = _WarpInTracker(self)
        self._upgrade_tracker = _UpgradeTracker(game_data, enemy)
        self._unit_comparer = _UnitComparer(self)
        self._unit_watcher = _UnitWatcher(self)

    @property
    def game_data(self) -> GameData:
        """The game's data tables."""
        return self._game_data

    @property
    def enemy(self) -> Enemy | None:
        """The other player, and what is known of it, or `None` in a game with no other player."""
        return self._enemy

    @property
    def order_book(self) -> OrderBook:
        """The book this player's units are ordered into."""
        return self._order_book

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
    def morph_tracker(self) -> _MorphTracker:
        """When each morph under way began, of those the game reports no progress for."""
        return self._morph_tracker

    @property
    def warp_in_tracker(self) -> _WarpInTracker:
        """When each of this player's units warping in began, and how far it has got."""
        return self._warp_in_tracker

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
        """Take in the observation at `step`: this player's upgrades, then the units it reports and the deaths, then
        the morphs and warp-ins they show.

        Raises `UncuratedIdError` if this player holds an upgrade, or a unit is of a type, that the curated ids leave
        out. Those belong among the curated ids.
        """
        self._last_changes = _TrackerChanges(self._last_changes.update + 1)
        if new_upgrades := self._upgrade_tracker.update(observation.player):
            self._last_changes.own_upgrades_finished = new_upgrades
        self._unit_tracker.update(observation, step)
        self._morph_tracker.update(step)
        self._warp_in_tracker.update(step)
