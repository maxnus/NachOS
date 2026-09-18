"""What an observation reports beyond the units in it."""

from __future__ import annotations

from functools import cached_property
from typing import TYPE_CHECKING

from sc2nachos.gamedata import Resources
from sc2nachos.gamemap._image_data import image_tiles
from sc2nachos.geometry import Grid
from sc2nachos.ids import UpgradeId
from sc2nachos.state._actions import Action, read_action
from sc2nachos.state._effect import Effect
from sc2nachos.state._score import Score
from sc2nachos.state._supply import Supply
from sc2nachos.state._ui_unit_counts import UiUnitCounts
from sc2nachos.units import Alliance
from sc2nachos.units._tracking._unit_tracker import _IDS_PER_ALLIANCE

if TYPE_CHECKING:
    from numpy import ndarray
    from s2clientprotocol import sc2api_pb2

    from sc2nachos.gamemap import GameMap
    from sc2nachos.units._tracking import _Tracker


class _State:
    """What one observation reports beyond its units, each read out of it when first asked for."""

    def __init__(self, observation: sc2api_pb2.ResponseObservation, tracker: _Tracker, game_map: GameMap) -> None:
        """Read `observation`, whose units `tracker` has taken in, on `game_map`."""
        self._response = observation
        self._observation = observation.observation
        self._tracker = tracker
        self._map = game_map

    @cached_property
    def score(self) -> Score:
        return Score.from_proto(self._observation.score)

    # The player's counters.

    @cached_property
    def resources(self) -> Resources:
        common = self._observation.player_common
        return Resources(common.minerals, common.vespene)

    @cached_property
    def supply(self) -> Supply:
        """The supply as the game reports it, with the half supply it rounds away added back.

        The game rounds the supply in use, and the army's, down (in game): one zergling leaves both where they were.
        """
        common = self._observation.player_common
        rounded_away = self._half_supply()
        return Supply(
            common.food_used + rounded_away, common.food_cap, common.food_army + rounded_away, common.food_workers
        )

    def _half_supply(self) -> float:
        """What the units first seen as this player's and not dead take beyond whole supplies, those inside another
        unit included: 0.5 for an odd number of zerglings and banelings, and 0 otherwise."""
        rows = self._tracker.data.units
        taken = 0.0
        for unit in self._tracker.units.known:
            if unit._id // _IDS_PER_ALLIANCE != Alliance.OWN:
                continue
            if (row := rows.get(unit._type_id)) is not None:
                # What the unit takes beyond a whole supply: 0.5 for a zergling, 0 for a roach.
                taken += row.supply_cost % 1
        # What is left of the sum beyond whole supplies, which the game does count: two zerglings leave nothing.
        return taken % 1

    @cached_property
    def ui_unit_counts(self) -> UiUnitCounts:
        common = self._observation.player_common
        return UiUnitCounts(common.idle_worker_count, common.army_count, common.warp_gate_count)

    # The map as it stands.

    @property
    def upgrades(self) -> frozenset[UpgradeId]:
        return self._tracker.upgrades.own

    @cached_property
    def _visibility(self) -> ndarray:
        """Per tile: 0 where it has never been in sight, 1 where it has been, and 2 where it is."""
        return image_tiles(self._observation.raw_data.map_state.visibility, self._map.playable_area)

    @cached_property
    def vision(self) -> Grid[bool]:
        return self._make_readonly_grid(self._visibility == 2)

    @cached_property
    def explored(self) -> Grid[bool]:
        return self._make_readonly_grid(self._visibility != 0)

    @cached_property
    def creep(self) -> Grid[bool]:
        creep = image_tiles(self._observation.raw_data.map_state.creep, self._map.playable_area)
        return self._make_readonly_grid(creep != 0)

    def _make_readonly_grid(self, values: ndarray) -> Grid[bool]:
        """A grid over the playable area, as the map's grids are."""
        return Grid(values, origin=self._map.pathing.origin, outside=False, readonly=True)

    @cached_property
    def effects(self) -> tuple[Effect, ...]:
        return tuple(Effect.from_proto(effect) for effect in self._observation.raw_data.effects)

    # What this player did since the observation before. The game reports each action once, in the next observation
    # however many steps it spans, a realtime game's included (in game), as it does chat and alerts. Read by the
    # events that hand them on.

    @cached_property
    def actions(self) -> tuple[Action, ...]:
        """What this player did.

        Raises `UncuratedIdError` where an action names an ability the curated ids leave out.
        """
        unit_by_tag = self._tracker.units.by_tag
        actions = (read_action(action, unit_by_tag) for action in self._response.actions)
        return tuple(action for action in actions if action is not None)
