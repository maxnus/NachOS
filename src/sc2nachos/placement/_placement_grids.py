"""Where a structure may stand, as this player knows the map."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Self, final

from sc2nachos._checks import check_game_time
from sc2nachos.game_time import GameTime
from sc2nachos.gamedata import CreepRequirement
from sc2nachos.geometry import Grid, Point, Rectangle
from sc2nachos.geometry._footprint import _margin_sides
from sc2nachos.placement._build_sites import _BuildSites, _placement_key, _standing_centers
from sc2nachos.placement._placement_reservation import PlacementReservation
from sc2nachos.placement._townhall_zones import _keep_townhalls_off_resources
from sc2nachos.placement._walls import ProtossWall, TerranWall
from sc2nachos.units import UnitType

if TYPE_CHECKING:
    from sc2nachos.gamedata import GameData
    from sc2nachos.gamemap import GameMap, Ramp
    from sc2nachos.geometry import Footprint, Margin, PointLike
    from sc2nachos.ids import UnitTypeId
    from sc2nachos.orders import OrderBook
    from sc2nachos.state._state import _State
    from sc2nachos.units import Unit, Units


@final
class PlacementGrids:
    """Where a structure may stand, as this player knows the map in the current observation, in layers: `static`, the
    ground the map lets a structure stand on all game; `dynamic`, where nothing stands now that can go; and
    `reserved`, where this player's ordered builds will stand, and what it has reserved with `reserve`.

    `free` combines them, and `free_for` adds what a structure type needs: the creep, and for a townhall, the ground
    resources leave it. `is_free` says whether one may stand at a point, and `nearest_free` finds the nearest site where
    one may. None of them knows of power or of units in the way. `terran_wall` and `protoss_wall` say where structures
    close a main's ramp at its top.
    """

    __slots__ = (
        "_build_sites",
        "_dynamic",
        "_game_data",
        "_game_map",
        "_ground_by_needs",
        "_present_units",
        "_reservations",
        "_state",
        "_static",
        "_step",
        "_townhall_ground",
    )

    def __init__(
        self,
        static_placement_grid: Grid[bool],
        *,
        game_map: GameMap,
        build_sites: _BuildSites,
        present_units: Units[Unit[Any]],
        state: _State,
        step: int,
        game_data: GameData,
    ) -> None:
        """The grids of the observation `state` reads, made at `step`, in which this player knows of `present_units`,
        in sight or remembered, from the game's `static_placement_grid` and its `build_sites`, on `game_map`."""
        self._static = static_placement_grid
        self._game_map = game_map
        self._build_sites = build_sites
        self._present_units = present_units
        self._state = state
        self._step = step
        self._game_data = game_data
        # What `reserve` made that has not ended, withdrawn ones until the next observation.
        self._reservations: list[PlacementReservation] = []
        self._dynamic: Grid[bool] | None = None
        self._townhall_ground: Grid[bool] | None = None
        self._ground_by_needs: dict[tuple[bool, CreepRequirement | None], Grid[bool]] = {}

    @classmethod
    def _build_at_start(
        cls,
        game_map: GameMap,
        *,
        present_units: Units[Unit[Any]],
        state: _State,
        step: int,
        game_data: GameData,
        order_book: OrderBook,
    ) -> Self:
        """The grids of a game's first observation on `game_map`, which `state` reads, made at `step`, and in which this
        player knows of `present_units`, from the map's placement grid at the start, and with this player's builds as
        `order_book` holds them. The placement grid at the start blocks no unit, this player's townhall and the mineral
        fields in sight included; it is the pathing grid that blocks those."""
        static = game_map.placement.copy()
        for unit in present_units.of_type(UnitType.AnyVespeneGeyser, UnitType.CollapsibleTower):
            if (footprint := unit.footprint) is not None:
                static.fill_footprint(footprint, at=unit.position, value=False)
        build_sites = _BuildSites(game_data, order_book)
        return cls(
            static.as_readonly(),
            game_map=game_map,
            build_sites=build_sites,
            present_units=present_units,
            state=state,
            step=step,
            game_data=game_data,
        )

    def _update(self, present_units: Units[Unit[Any]], state: _State, step: int) -> None:
        """Move on to a later observation of the same game, which `state` reads, made at `step`, and in which this
        player knows of `present_units`. A reservation ends here if it has run its time or one of this player's
        structures stands on its site."""
        self._present_units = present_units
        self._state = state
        self._step = step
        if self._reservations:
            standing = _standing_centers(present_units.own)
            self._reservations = [
                reservation
                for reservation in self._reservations
                if not reservation._withdrawn
                and (reservation._max_time is None or step < (reservation._time + reservation._max_time).steps)
                and _placement_key(reservation.at) not in standing
            ]
        self._dynamic = None
        self._townhall_ground = None
        self._ground_by_needs = {}

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
        """The tiles under this player's builds that are ordered and not standing yet, issued this turn or sent and not
        yet begun, and under each of `reservations`. A build or a reservation made or withdrawn earlier in the turn
        shows. An add-on reserves the ground beside its host, and a lifted host's landing site too."""
        return self._build_sites.reserved_grid(self._static, self._present_units.own, self.reservations)

    @property
    def reservations(self) -> tuple[PlacementReservation, ...]:
        """Every reservation `reserve` made that has not ended, oldest first."""
        return tuple(reservation for reservation in self._reservations if not reservation._withdrawn)

    def reserve(
        self, unit_type: UnitTypeId, *, at: PointLike, margin: Margin = 0, max_time: GameTime | None = None
    ) -> PlacementReservation:
        """Keep the ground a structure of `unit_type` would cover at `at`, placed as the game snaps it, and with
        `margin`, every tile within it of one the footprint covers, as `is_free` takes it, out of `free`, `free_for`,
        `is_free` and `nearest_free` from now, the rest of this turn included, until the reservation is withdrawn, until
        `max_time` has passed if given, or until an observation shows one of this player's structures standing on its
        site, whatever type it stands as. `max_time=GameTime(steps=1)` keeps it for this turn alone. Reservations may
        overlap, and each game starts with none.

        Raises `TypeError` for a `max_time` that is not a `GameTime` or a margin that is neither a count of tiles nor a
        tuple of four, and `ValueError` for a type with no footprint, for a gas building, which stands on a geyser, for
        a negative margin and for a `max_time` under one step."""
        footprint = self._footprint_of(unit_type)
        _margin_sides(margin)
        if max_time is not None:
            check_game_time(max_time, "max_time")
            if max_time.steps < 1:
                raise ValueError(f"max_time is at least one step, not {max_time!r}")
        center = footprint.snapped_center(at=at)
        reservation = PlacementReservation(
            unit_type, footprint, at=center, margin=margin, time=GameTime(steps=self._step), max_time=max_time
        )
        self._reservations.append(reservation)
        return reservation

    @property
    def free(self) -> Grid[bool]:
        """Where a structure may stand now: on `static` and `dynamic`, and off `reserved`."""
        return self._static & self.dynamic & ~self.reserved

    def free_for(self, unit_type: UnitTypeId) -> Grid[bool]:
        """Where a structure of `unit_type` may stand now: `free`, on creep or off it as the type needs
        (`UnitTypeData.creep_requirement`), and for a townhall, off the ground around each mineral field and geyser
        this player knows of that keeps one off, as `api.expansions` heeds it. A gas building stands on a geyser,
        which `static` closes, so it fits nowhere on any of them."""
        return self._ground_for(unit_type) & ~self.reserved

    def is_free(self, unit_type: UnitTypeId, *, at: PointLike, margin: Margin = 0) -> bool:
        """Whether a structure of `unit_type` may stand at `at` now, placed as the game snaps it: whether every tile
        its footprint covers is true on `free_for(unit_type)`, and with `margin`, every tile within it of one the
        footprint covers, as `Grid.fits_footprint` takes it. Raises `ValueError` for a type with no footprint, for a
        gas building, which stands on a geyser, and for a negative margin."""
        footprint = self._footprint_of(unit_type)
        if not self._ground_for(unit_type).fits_footprint(footprint, at=at, margin=margin):
            return False
        return (~self.reserved).fits_footprint(footprint, at=at, margin=margin)

    def nearest_free(
        self, unit_type: UnitTypeId, *, near: PointLike, max_distance: float, margin: Margin = 0
    ) -> Point | None:
        """The center nearest `near`, at most `max_distance` from it, where a structure of `unit_type` may stand now
        with `margin` free beside it, as `is_free` says, and as the game snaps a structure; the first in tile order of
        those as near. `None` if there is none; an infinite `max_distance` reaches the whole map. Raises `ValueError`
        for a type with no footprint, for a gas building, which stands on a geyser, for a `max_distance` that is
        negative or not a number, and for a negative margin."""
        if not max_distance >= 0:
            raise ValueError(f"max_distance is a distance, at least 0, not {max_distance}")
        footprint = self._footprint_of(unit_type)
        sides = _margin_sides(margin)
        x, y = near[0], near[1]
        if math.isinf(max_distance):
            window = self._static.bounds
        else:
            # Wide enough that every footprint centered within `max_distance` lies wholly inside, margin and all.
            reach = math.ceil(max_distance) + max(footprint.width, footprint.height) + max(sides)
            window = Rectangle(math.floor(x) - reach, math.floor(y) - reach, 2 * reach + 1, 2 * reach + 1)
        free = self._ground_for(unit_type).cropped(window) & ~self.reserved.cropped(window)
        fitting = free.where_footprint_fits(footprint, margin=margin)
        # `distance_from` measures from each tile's center, which along an even side lies half a tile off the center
        # of the footprint centered in that tile, so `near` moves by as much.
        shift_x, shift_y = 0.5 - 0.5 * (footprint.width % 2), 0.5 - 0.5 * (footprint.height % 2)
        distance = fitting.distance_from(Point((x + shift_x, y + shift_y)))
        candidates = distance.where(fitting & (distance <= max_distance), math.inf)
        if not fitting.any() or candidates.min() == math.inf:
            return None
        return footprint.center_in_tile(candidates.argmin())

    def terran_wall(self, main_ramp: Ramp) -> TerranWall | None:
        """Where a Terran wall closes `main_ramp`, the ramp out of a start location's main as `GameMap.main_ramp_of`
        finds it, at its top, as `TerranWall` says, near and far going by that start: `None` if the ramp is not of the
        shape every main ramp of the pool is, two top tiles 2 wide climbing along a diagonal. Whether a structure may
        stand at a site now is for `is_free` to say. Raises `ValueError` for a ramp out of no start location's main."""
        return TerranWall._from_ramp(main_ramp, start=self._start_of_main_ramp(main_ramp))

    def protoss_wall(self, main_ramp: Ramp) -> ProtossWall | None:
        """Where a Protoss wall closes `main_ramp` at its top, as `ProtossWall` says, its gap at the corner nearer the
        start whose main the ramp leaves: `None` where `terran_wall` is. Whether a structure may stand at a site now is
        for `is_free` to say. Raises `ValueError` for a ramp out of no start location's main."""
        return ProtossWall._from_ramp(main_ramp, start=self._start_of_main_ramp(main_ramp))

    def _start_of_main_ramp(self, main_ramp: Ramp) -> Point:
        """The start location whose main `main_ramp` leaves; raises `ValueError` for a ramp out of no start location's
        main."""
        for start_location in self._game_map.all_start_locations:
            if self._game_map.main_ramp_of(start_location) == main_ramp:
                return start_location
        raise ValueError(f"the ramp at {main_ramp.top.center} leads out of no start location's main")

    def _ground_for(self, unit_type: UnitTypeId) -> Grid[bool]:
        """`free_for(unit_type)` before `reserved` is taken off, which no order changes: one grid for the types that
        are townhalls or not alike and keep to creep or off it alike, built once an observation."""
        row = self._game_data.units.get(unit_type)
        requirement = None if row is None else row.creep_requirement
        keeps_to = requirement if requirement in (CreepRequirement.ON_CREEP, CreepRequirement.OFF_CREEP) else None
        needs = (unit_type in UnitType.Townhall.type_ids, keeps_to)
        if (ground := self._ground_by_needs.get(needs)) is None:
            ground = self._static & self.dynamic
            if needs[0]:
                ground = ground & self._ground_townhalls_may_cover()
            if keeps_to is CreepRequirement.ON_CREEP:
                ground = ground & self._state.creep
            elif keeps_to is CreepRequirement.OFF_CREEP:
                ground = ground & ~self._state.creep
            ground = self._ground_by_needs[needs] = ground.as_readonly()
        return ground

    def _footprint_of(self, unit_type: UnitTypeId) -> Footprint:
        """The footprint a structure of `unit_type` covers; raises `ValueError` for a type with none, and for a gas
        building, since `static` closes the geysers it stands on."""
        if unit_type in UnitType.GasBuilding.type_ids:
            raise ValueError(f"{unit_type.name} stands on a geyser, which no placement grid leaves free")
        row = self._game_data.units.get(unit_type)
        if row is None or row.footprint is None:
            raise ValueError(f"{unit_type.name} has no footprint, so it stands nowhere")
        return row.footprint

    def _ground_townhalls_may_cover(self) -> Grid[bool]:
        """True everywhere but on the ground around each mineral field and geyser this player knows of that a
        townhall may not cover. Built once an observation."""
        if self._townhall_ground is None:
            ground = Grid.like(self._static, dtype=bool, outside=False)
            ground.fill(True)
            _keep_townhalls_off_resources(
                ground,
                mineral_fields=self._present_units.of_type(UnitType.AnyMineralField),
                geysers=self._present_units.of_type(UnitType.AnyVespeneGeyser),
            )
            self._townhall_ground = ground.as_readonly()
        return self._townhall_ground
