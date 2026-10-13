"""Where a structure may stand, as this player knows the map in one observation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Self, final

from sc2nachos.gamedata import OnCreep
from sc2nachos.geometry import Grid
from sc2nachos.state._build_sites import _BuildSites
from sc2nachos.units import UnitType

if TYPE_CHECKING:
    from sc2nachos.gamedata import GameData
    from sc2nachos.ids import UnitTypeId
    from sc2nachos.orders import OrderBook
    from sc2nachos.state._state import _State
    from sc2nachos.units import Unit, Units


@final
class PlacementGrids:
    """Where a structure may stand, as this player knows the map in one observation, in layers: `static`, the ground
    the map lets a structure stand on all game; `dynamic`, where nothing stands now that can go; and `reserved`, where
    this player's ordered builds will stand.

    `free` combines them, and `free_for` adds the creep a structure type needs:
    `placement.free_for(unit_type).centers_fitting(api.data.units[unit_type].footprint)` gives the centers where one
    may go. None of them knows of power, of units in the way, or of the ground around resources that keeps a townhall
    off, as `api.expansions` does.
    """

    __slots__ = ("_build_sites", "_dynamic", "_game_data", "_state", "_static", "_units")

    def __init__(
        self,
        static: Grid[bool],
        *,
        build_sites: _BuildSites,
        units: Units[Unit[Any]],
        state: _State,
        game_data: GameData,
    ) -> None:
        """The grids of the observation `state` reads, whose units are `units`, from the game's `static` grid and its
        `build_sites`."""
        self._static = static
        self._build_sites = build_sites
        self._units = units
        self._state = state
        self._game_data = game_data
        self._dynamic: Grid[bool] | None = None

    @classmethod
    def _at_start(
        cls,
        placement: Grid[bool],
        *,
        units: Units[Unit[Any]],
        state: _State,
        game_data: GameData,
        orders: OrderBook,
    ) -> Self:
        """The grids of a game's first observation, which `state` reads, whose units are `units`, from the game's
        placement grid at the start, which blocks no unit, those in sight included, and with this player's builds as
        `orders` holds them."""
        static = placement.copy()
        for unit in units.neutral.of_type(UnitType.AnyVespeneGeyser, UnitType.CollapsibleTower):
            if (footprint := unit.footprint) is not None:
                static.fill_footprint(footprint, at=unit.position, value=False)
        build_sites = _BuildSites(game_data, orders)
        return cls(static.as_readonly(), build_sites=build_sites, units=units, state=state, game_data=game_data)

    def _for_observation(self, units: Units[Unit[Any]], state: _State) -> PlacementGrids:
        """The grids of a later observation of the same game, which `state` reads and whose units are `units`."""
        return PlacementGrids(
            self._static, build_sites=self._build_sites, units=units, state=state, game_data=self._game_data
        )

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
            for unit in self._units.excluding_type(UnitType.AnyVespeneGeyser, UnitType.CollapsibleTower):
                if (footprint := unit.footprint) is not None:
                    dynamic.fill_footprint(footprint, at=unit.position, value=False)
            self._dynamic = dynamic.as_readonly()
        return self._dynamic

    @property
    def reserved(self) -> Grid[bool]:
        """The tiles under this player's builds that are ordered and not standing yet: held until the unit can start
        one, issued this turn, or sent and not yet begun. A build issued or withdrawn earlier in the turn shows. An
        add-on reserves the ground beside its host, and a lifted host's landing site too."""
        return self._build_sites.reserved(self._static, self._units)

    @property
    def free(self) -> Grid[bool]:
        """Where a structure may stand now: on `static` and `dynamic`, and off `reserved`."""
        return self._static & self.dynamic & ~self.reserved

    def free_for(self, unit_type: UnitTypeId) -> Grid[bool]:
        """Where a structure of `unit_type` may stand now: `free`, on creep or off it as the type needs
        (`UnitTypeData.on_creep`). For a type with no rule measured, the same as `free`."""
        row = self._game_data.units.get(unit_type)
        rule = None if row is None else row.on_creep
        if rule is OnCreep.REQUIRED:
            return self.free & self._state.creep
        if rule is OnCreep.REFUSED:
            return self.free & ~self._state.creep
        return self.free
