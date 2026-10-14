"""Where a structure may stand, as this player knows the map."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Self, final

from sc2nachos.gamedata import CreepRequirement
from sc2nachos.geometry import Grid
from sc2nachos.placement._build_sites import _BuildSites
from sc2nachos.units import UnitType

if TYPE_CHECKING:
    from sc2nachos.gamedata import GameData
    from sc2nachos.ids import UnitTypeId
    from sc2nachos.orders import OrderBook
    from sc2nachos.state._state import _State
    from sc2nachos.units import Unit, Units


@final
class PlacementGrids:
    """Where a structure may stand, as this player knows the map in the current observation, in layers: `static`, the
    ground the map lets a structure stand on all game; `dynamic`, where nothing stands now that can go; and
    `reserved`, where this player's ordered builds will stand.

    `free` combines them, and `free_for` adds the creep a structure type needs:
    `placement.free_for(unit_type).where_footprint_fits(api.data.units[unit_type].footprint)` gives the centers where
    one may go. None of them knows of power, of units in the way, or of the ground around resources that keeps a
    townhall off, as `api.expansions` does.
    """

    __slots__ = ("_build_sites", "_dynamic", "_game_data", "_present_units", "_state", "_static")

    def __init__(
        self,
        static_placement_grid: Grid[bool],
        *,
        build_sites: _BuildSites,
        present_units: Units[Unit[Any]],
        state: _State,
        game_data: GameData,
    ) -> None:
        """The grids of the observation `state` reads, in which this player knows of `present_units`, in sight or
        remembered, from the game's `static_placement_grid` and its `build_sites`."""
        self._static = static_placement_grid
        self._build_sites = build_sites
        self._present_units = present_units
        self._state = state
        self._game_data = game_data
        self._dynamic: Grid[bool] | None = None

    @classmethod
    def _build_at_start(
        cls,
        placement_grid: Grid[bool],
        *,
        present_units: Units[Unit[Any]],
        state: _State,
        game_data: GameData,
        order_book: OrderBook,
    ) -> Self:
        """The grids of a game's first observation, which `state` reads and in which this player knows of
        `present_units`, from the game's `placement_grid` at the start, and with this player's builds as `order_book`
        holds them. The placement grid at the start blocks no unit, this player's townhall and the mineral fields in
        sight included; it is the pathing grid that blocks those."""
        static = placement_grid.copy()
        for unit in present_units.neutral.of_type(UnitType.AnyVespeneGeyser, UnitType.CollapsibleTower):
            if (footprint := unit.footprint) is not None:
                static.fill_footprint(footprint, at=unit.position, value=False)
        build_sites = _BuildSites(game_data, order_book)
        return cls(
            static.as_readonly(), build_sites=build_sites, present_units=present_units, state=state, game_data=game_data
        )

    def _update(self, present_units: Units[Unit[Any]], state: _State) -> None:
        """Move on to a later observation of the same game, which `state` reads and in which this player knows of
        `present_units`."""
        self._present_units = present_units
        self._state = state
        self._dynamic = None

    @property
    def static(self) -> Grid[bool]:
        """Where a structure may stand whatever happens in the game: the ground the map lets a structure stand on,
        less its geysers and its collapsible towers, which stay where they fall. It reads the ground under rocks,
        Xel'Naga towers, mineral fields and townhalls as it is beneath them; cliffs, ramps and the ground beyond the
        playable area are false."""
        return self._static

    @property
    def dynamic(self) -> Grid[bool]:
        """Where nothing stands now that can go, as far as this player knows: false under the footprint of every
        structure, every player's and the map's, in sight or remembered in the fog, and of every mineral field, and
        true everywhere else, cliffs included. The geysers and collapsible towers `static` closes are not in it, and a
        unit that moves, a structure in the air and a force field block nothing."""
        if self._dynamic is None:
            dynamic = Grid.like(self._static, dtype=bool, outside=False)
            dynamic.fill(True)
            for unit in self._present_units.excluding_type(UnitType.AnyVespeneGeyser, UnitType.CollapsibleTower):
                if (footprint := unit.footprint) is not None:
                    dynamic.fill_footprint(footprint, at=unit.position, value=False)
            self._dynamic = dynamic.as_readonly()
        return self._dynamic

    @property
    def reserved(self) -> Grid[bool]:
        """The tiles under this player's builds that are ordered and not standing yet: held until the unit can start
        one, issued this turn, or sent and not yet begun. A build issued or withdrawn earlier in the turn shows. An
        add-on reserves the ground beside its host, and a lifted host's landing site too."""
        return self._build_sites.reserved_grid(self._static, self._present_units.own)

    @property
    def free(self) -> Grid[bool]:
        """Where a structure may stand now: on `static` and `dynamic`, and off `reserved`."""
        return self._static & self.dynamic & ~self.reserved

    def free_for(self, unit_type: UnitTypeId) -> Grid[bool]:
        """Where a structure of `unit_type` may stand now: `free`, on creep or off it as the type needs
        (`UnitTypeData.creep_requirement`). For a type with no requirement measured, the same as `free`. A gas
        building stands on a geyser, which `static` closes, so it fits nowhere on any of them."""
        row = self._game_data.units.get(unit_type)
        requirement = None if row is None else row.creep_requirement
        if requirement is CreepRequirement.ON_CREEP:
            return self.free & self._state.creep
        if requirement is CreepRequirement.OFF_CREEP:
            return self.free & ~self._state.creep
        return self.free
