"""Where a structure may stand, as this player knows the map in one observation."""

import math
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from s2clientprotocol import common_pb2, data_pb2, error_pb2, query_pb2, raw_pb2, sc2api_pb2

from sc2nachos import Api
from sc2nachos.events import TurnEvent
from sc2nachos.gamedata import GameData
from sc2nachos.gamemap import GameMap
from sc2nachos.geometry import Grid, Point, Tile
from sc2nachos.ids import AbilityId, UnitTypeId
from sc2nachos.launch import GameProcess, MapFile, MapNotFoundError
from sc2nachos.match import Computer, Difficulty, Participant, Race
from sc2nachos.orders import OrderBook
from sc2nachos.placement import PlacementGrids
from sc2nachos.protocol import Client, Recording, WebSocketTransport
from sc2nachos.state._state import _State
from sc2nachos.units import Alliance, OwnUnit, UnitType, Visibility
from sc2nachos.units._tracking import _Tracker
from support import (
    make_bits,
    make_client,
    make_enemy,
    make_game_info,
    make_observation,
    make_response,
    make_tables,
    make_unit,
)

CORPUS = sorted((Path(__file__).parent / "corpus").glob("*.sc2rec"))

_BUILD_DEPOT = AbilityId.SCV_BUILD_SUPPLY_DEPOT
_BUILD_REFINERY = AbilityId.SCV_BUILD_REFINERY
_BUILD_REACTOR = AbilityId.BUILD_REACTOR
_NEUTRAL = Alliance.NEUTRAL


def _row(
    unit_type: UnitTypeId, *, minerals: int = 0, speed: float = 0.0, alias: tuple[UnitTypeId, ...] = ()
) -> data_pb2.UnitTypeData:
    return data_pb2.UnitTypeData(
        unit_id=unit_type, mineral_cost=minerals, build_time=400.0, movement_speed=speed, tech_alias=alias
    )


# The footprints these units cover come from what the sweep measured.
_TABLES = make_tables(
    _row(UnitTypeId.SCV, minerals=50, speed=2.8125),
    _row(UnitTypeId.MARINE, minerals=50, speed=2.25),
    _row(UnitTypeId.SUPPLY_DEPOT, minerals=100),
    _row(UnitTypeId.REFINERY, minerals=75),
    _row(UnitTypeId.REFINERY_RICH, minerals=75),
    _row(UnitTypeId.BARRACKS, minerals=150),
    _row(UnitTypeId.BARRACKS_FLYING, minerals=150, speed=0.9375),
    _row(UnitTypeId.REACTOR_BARRACKS, minerals=50, alias=(UnitTypeId.REACTOR,)),
    _row(UnitTypeId.SPAWNING_POOL, minerals=200),
    _row(UnitTypeId.HATCHERY, minerals=300),
    _row(UnitTypeId.COMMAND_CENTER, minerals=400),
    _row(UnitTypeId.MINERAL_FIELD),
    _row(UnitTypeId.VESPENE_GEYSER),
    _row(UnitTypeId.VESPENE_GEYSER_RICH),
    _row(UnitTypeId.DESTRUCTIBLE_4X4_ROCK_EX1),
    _row(UnitTypeId.COLLAPSIBLE_TOWER_DIAGONAL_ROCK),
    _row(UnitTypeId.INHIBITOR_ZONE_FLYING_LARGE),
    _row(UnitTypeId.HEALING_SHRINE_XEL_NAGA),
    data_pb2.UnitTypeData(unit_id=UnitTypeId.FORCE_FIELD, race=Race.PROTOSS.value),
    abilities=[
        data_pb2.AbilityData(ability_id=AbilityId.MOVE, target=data_pb2.AbilityData.Target.PointOrUnit),
        data_pb2.AbilityData(ability_id=AbilityId.LAND, target=data_pb2.AbilityData.Target.Point),
        data_pb2.AbilityData(ability_id=_BUILD_DEPOT, target=data_pb2.AbilityData.Target.Point, is_building=True),
        data_pb2.AbilityData(ability_id=_BUILD_REFINERY, target=data_pb2.AbilityData.Target.Unit, is_building=True),
        data_pb2.AbilityData(
            ability_id=_BUILD_REACTOR, target=data_pb2.AbilityData.Target.PointOrNone, is_building=True
        ),
    ],
)

# Open ground 16 tiles square, but for a strip no structure can stand on along its left edge.
_MAP = tuple(".." + "#" * 14 for _ in range(16))


class _Game:
    """A tracker and an order book on a drawn map, fed one observation at a time as `Api.play` feeds them."""

    def __init__(self) -> None:
        self.tracker = _Tracker(_TABLES, make_enemy())
        self.map = GameMap(make_game_info(*_MAP), start_location=Point((0.5, 0.5)))
        self.book = OrderBook(_TABLES)
        self.grids: PlacementGrids | None = None

    def observe(
        self, step: int, *units: raw_pb2.Unit, dead: tuple[int, ...] = (), creep: tuple[str, ...] = ()
    ) -> PlacementGrids:
        """Take in an observation of `units` at `step`, in which the units under the tags `dead` died and creep lies
        where `creep` draws it, top row first, and return its grids, as `_Game` keeps them."""
        creep_image = make_bits(*(creep or ("." * 16,) * 16))
        observation = make_observation(step, units=units, dead=dead, creep=creep_image)
        self.tracker.update(observation.observation.raw_data, step)
        changes = self.tracker.last_changes
        present = self.tracker.unit_tracker.present
        self.book._observe(step, [*changes.units_died, *changes.units_found_dead])
        state = _State(observation, self.tracker, self.map)
        if self.grids is None:
            self.grids = PlacementGrids._build_at_start(
                self.map.placement,
                present_units=present,
                state=state,
                step=step,
                game_data=_TABLES,
                order_book=self.book,
            )
        else:
            self.grids._update(present, state, step)
        return self.grids

    def own(self, tag: int) -> OwnUnit[Any]:
        unit = self.tracker.unit_tracker.by_tag(tag)
        assert isinstance(unit, OwnUnit)
        return unit

    def flush(self) -> None:
        """Send the turn's orders, each command taken."""
        client, _ = make_client(make_response(action=sc2api_pb2.ResponseAction(result=[error_pb2.Success] * 8)))
        self.book._send(client)


def _picture(grid: Grid[bool], *, x0: int = 0, y0: int = 0, x1: int = 15, y1: int = 15) -> list[str]:
    """The tiles from `(x0, y0)` to `(x1, y1)`, the top row first: `#` true, `.` false."""
    return ["".join("#" if grid[Tile(x, y)] else "." for x in range(x0, x1 + 1)) for y in reversed(range(y0, y1 + 1))]


def _scv(tag: int, *orders: raw_pb2.UnitOrder, at: tuple[float, float] = (3.5, 2.5)) -> raw_pb2.Unit:
    return make_unit(tag, UnitTypeId.SCV, at=at, orders=orders)


def _building_depot(at: tuple[float, float]) -> raw_pb2.UnitOrder:
    """The order an SCV shows while it goes to build a depot at `at`, or builds it."""
    return raw_pb2.UnitOrder(ability_id=_BUILD_DEPOT, target_world_space_pos=common_pb2.Point(x=at[0], y=at[1]))


class TestTheStaticGrid:
    def test_it_is_the_maps_buildable_ground_less_its_geysers(self) -> None:
        """The game's grid leaves the ground under a mineral field and a rock open, and so does this one."""
        grids = _Game().observe(
            0,
            make_unit(1, UnitTypeId.VESPENE_GEYSER, at=(8.5, 8.5), alliance=_NEUTRAL, visibility=Visibility.IN_FOG),
            make_unit(2, UnitTypeId.MINERAL_FIELD, at=(5.0, 3.5), alliance=_NEUTRAL, visibility=Visibility.IN_FOG),
            make_unit(3, UnitTypeId.DESTRUCTIBLE_4X4_ROCK_EX1, at=(12.0, 12.0), alliance=_NEUTRAL),
        )
        assert _picture(grids.static, y0=6, y1=10) == [
            "..##############",
            "..#####...######",
            "..#####...######",
            "..#####...######",
            "..##############",
        ]
        assert grids.static[Point((5.0, 3.5))]
        assert grids.static[Point((12.0, 12.0))]

    def test_a_collapsible_tower_is_closed_from_the_start(self) -> None:
        """Felled, a tower stays, still covering its 5x5 (tool `sweep_placement`)."""
        tower = make_unit(1, UnitTypeId.COLLAPSIBLE_TOWER_DIAGONAL_ROCK, at=(8.5, 8.5), alliance=_NEUTRAL, radius=2.94)
        grids = _Game().observe(0, tower)
        assert _picture(grids.static, x0=5, y0=5, x1=11, y1=11) == [
            "#######",
            "#.....#",
            "#.....#",
            "#.....#",
            "#.....#",
            "#.....#",
            "#######",
        ]

    def test_it_stays_as_the_game_began(self) -> None:
        game = _Game()
        static = game.observe(0, make_unit(1, UnitTypeId.VESPENE_GEYSER, at=(8.5, 8.5), alliance=_NEUTRAL)).static
        assert game.observe(1).static is static

    def test_it_refuses_writes(self) -> None:
        with pytest.raises(TypeError, match="read-only"):
            _Game().observe(0).static[Point((5.5, 5.5))] = False


class TestTheDynamicGrid:
    def test_every_structure_and_mineral_field_closes_its_footprint(self) -> None:
        grids = _Game().observe(
            0,
            make_unit(1, UnitTypeId.SUPPLY_DEPOT, at=(4.0, 13.0)),
            make_unit(2, UnitTypeId.BARRACKS, at=(12.5, 12.5), alliance=Alliance.ENEMY, visibility=Visibility.IN_FOG),
            make_unit(3, UnitTypeId.MINERAL_FIELD, at=(5.0, 3.5), alliance=_NEUTRAL),
            make_unit(4, UnitTypeId.DESTRUCTIBLE_4X4_ROCK_EX1, at=(12.0, 5.0), alliance=_NEUTRAL),
        )
        assert _picture(grids.dynamic) == [
            "################",
            "################",
            "###..######...##",
            "###..######...##",
            "###########...##",
            "################",
            "################",
            "################",
            "################",
            "###########..###",
            "##########....##",
            "##########....##",
            "####..#####..###",
            "################",
            "################",
            "################",
        ]

    def test_a_unit_that_moves_a_structure_in_the_air_and_a_force_field_block_nothing(self) -> None:
        grids = _Game().observe(
            0,
            make_unit(1, UnitTypeId.MARINE, at=(5.5, 5.5)),
            make_unit(2, UnitTypeId.BARRACKS_FLYING, at=(10.5, 10.5), is_flying=True),
            make_unit(3, UnitTypeId.FORCE_FIELD, at=(12.5, 5.5), alliance=_NEUTRAL, radius=1.0),
        )
        assert grids.dynamic.all()

    def test_the_geysers_and_collapsible_towers_the_static_grid_closes_are_not_in_it(self) -> None:
        grids = _Game().observe(
            0,
            make_unit(1, UnitTypeId.VESPENE_GEYSER, at=(8.5, 8.5), alliance=_NEUTRAL),
            make_unit(2, UnitTypeId.COLLAPSIBLE_TOWER_DIAGONAL_ROCK, at=(4.5, 12.5), alliance=_NEUTRAL, radius=2.94),
        )
        assert grids.dynamic.all()
        assert not grids.static[Point((8.5, 8.5))]

    def test_one_of_the_maps_units_with_no_footprint_measured_closes_the_square_its_radius_spans(self) -> None:
        zone = make_unit(1, UnitTypeId.INHIBITOR_ZONE_FLYING_LARGE, at=(8.0, 8.0), alliance=_NEUTRAL, radius=1.1)
        grids = _Game().observe(0, zone)
        assert _picture(grids.dynamic, x0=6, y0=6, x1=9, y1=9) == ["####", "#..#", "#..#", "####"]

    def test_the_ground_opens_once_the_structure_is_gone(self) -> None:
        game = _Game()
        assert not game.observe(0, make_unit(1, UnitTypeId.SUPPLY_DEPOT, at=(8.0, 8.0))).dynamic.all()
        assert game.observe(1, dead=(1,)).dynamic.all()

    def test_it_is_built_once_an_observation(self) -> None:
        game = _Game()
        grids = game.observe(0, make_unit(1, UnitTypeId.SUPPLY_DEPOT, at=(8.0, 8.0)))
        first = grids.dynamic
        assert grids.dynamic is first
        assert game.observe(1, make_unit(1, UnitTypeId.SUPPLY_DEPOT, at=(8.0, 8.0))).dynamic is not first

    def test_it_refuses_writes(self) -> None:
        with pytest.raises(TypeError, match="read-only"):
            _Game().observe(0).dynamic[Point((5.5, 5.5))] = False


class TestTheReservedGrid:
    def test_nothing_is_reserved_with_no_build_ordered(self) -> None:
        assert not _Game().observe(0, _scv(1)).reserved.any()

    def test_a_build_issued_this_turn_reserves_its_site(self) -> None:
        game = _Game()
        game.observe(0, _scv(1))
        game.book.issue(game.own(1), _BUILD_DEPOT, target=Point((8.3, 7.8)))
        grids = game.observe(0, _scv(1))
        assert _picture(grids.reserved, x0=6, y0=6, x1=9, y1=9) == ["....", ".##.", ".##.", "...."]

    def test_a_withdrawn_build_frees_its_site(self) -> None:
        game = _Game()
        grids = game.observe(0, _scv(1))
        order = game.book.issue(game.own(1), _BUILD_DEPOT, target=Point((8.0, 8.0)))
        assert grids.reserved.any()
        order.withdraw()
        assert not grids.reserved.any()

    def test_a_build_sent_and_not_observed_yet_reserves_its_site(self) -> None:
        """The game may show an order an observation late, as on the ladder."""
        game = _Game()
        game.observe(0, _scv(1, at=(9.5, 8.5)))
        game.book.issue(game.own(1), _BUILD_DEPOT, target=Point((8.0, 8.0)))
        game.flush()
        grids = game.observe(8, _scv(1, at=(9.5, 8.5)))
        assert not game.book.issued_to(game.own(1))
        assert grids.reserved[Point((8.0, 8.0))]

    def test_a_build_the_worker_shows_reserves_its_site_until_construction_begins(self) -> None:
        game = _Game()
        assert game.observe(0, _scv(1, _building_depot((8.0, 8.0)))).reserved[Point((8.0, 8.0))]
        depot = make_unit(2, UnitTypeId.SUPPLY_DEPOT, at=(8.0, 8.0), build_progress=0.05)
        grids = game.observe(8, _scv(1, _building_depot((8.0, 8.0)), at=(7.0, 8.5)), depot)
        assert not grids.reserved.any()
        assert not grids.dynamic[Point((8.0, 8.0))]

    def test_a_refinery_reserves_its_geysers_ground(self) -> None:
        game = _Game()
        geyser = make_unit(2, UnitTypeId.VESPENE_GEYSER, at=(8.5, 8.5), alliance=_NEUTRAL)
        game.observe(0, _scv(1), geyser)
        game.book.issue(game.own(1), _BUILD_REFINERY, target=game.tracker.unit_tracker.by_tag(2))
        grids = game.observe(0, _scv(1), geyser)
        assert _picture(grids.reserved, x0=6, y0=6, x1=10, y1=10) == [
            ".....",
            ".###.",
            ".###.",
            ".###.",
            ".....",
        ]

    def test_a_rich_geysers_refinery_frees_its_site_once_it_stands_though_it_is_another_type(self) -> None:
        """The SCV shows the build while the refinery goes up, as `REFINERY_RICH` rather than the `REFINERY` its
        ability makes."""
        game = _Game()
        geyser = make_unit(2, UnitTypeId.VESPENE_GEYSER_RICH, at=(8.5, 8.5), alliance=_NEUTRAL)
        building = raw_pb2.UnitOrder(ability_id=_BUILD_REFINERY, target_unit_tag=2)
        assert game.observe(0, _scv(1, building), geyser).reserved[Point((8.5, 8.5))]
        refinery = make_unit(3, UnitTypeId.REFINERY_RICH, at=(8.5, 8.5), build_progress=0.05)
        assert not game.observe(8, _scv(1, building, at=(6.5, 8.5)), geyser, refinery).reserved.any()

    def test_a_build_shown_aimed_by_a_tag_the_game_never_reports_reserves_nothing(self) -> None:
        building = raw_pb2.UnitOrder(ability_id=_BUILD_REFINERY, target_unit_tag=99)
        assert not _Game().observe(0, _scv(1, building)).reserved.any()

    def test_an_add_on_reserves_the_ground_beside_its_host(self) -> None:
        game = _Game()
        game.observe(0, make_unit(1, UnitTypeId.BARRACKS, at=(6.5, 8.5)))
        game.book.issue(game.own(1), _BUILD_REACTOR)
        grids = game.observe(0, make_unit(1, UnitTypeId.BARRACKS, at=(6.5, 8.5)))
        assert _picture(grids.reserved, x0=4, y0=6, x1=10, y1=10) == [
            ".......",
            ".......",
            "....##.",
            "....##.",
            ".......",
        ]

    def test_a_lifted_hosts_add_on_reserves_where_it_lands_too(self) -> None:
        game = _Game()
        flying = make_unit(1, UnitTypeId.BARRACKS_FLYING, at=(12.0, 3.0), is_flying=True)
        game.observe(0, flying)
        game.book.issue(game.own(1), _BUILD_REACTOR, target=Point((6.5, 8.5)))
        grids = game.observe(0, flying)
        assert _picture(grids.reserved, x0=4, y0=6, x1=10, y1=10) == [
            ".......",
            ".###...",
            ".#####.",
            ".#####.",
            ".......",
        ]


_DEPOT = UnitTypeId.SUPPLY_DEPOT


class TestReservingASite:
    def test_a_reservation_reserves_its_footprint_where_the_game_snaps_it_from_the_moment_it_is_made(self) -> None:
        grids = _Game().observe(0, _scv(1))

        reservation = grids.reserve(_DEPOT, at=Point((8.3, 7.8)))

        assert reservation.at == Point((8.0, 8.0))
        assert (reservation.type_id, reservation.margin, reservation.step, reservation.max_steps) == (
            _DEPOT,
            0,
            0,
            None,
        )
        assert grids.reservations == (reservation,)
        assert _picture(grids.reserved, x0=6, y0=6, x1=9, y1=9) == ["....", ".##.", ".##.", "...."]
        assert not grids.is_free(_DEPOT, at=(8.0, 8.0))
        assert not grids.free[Point((8.0, 8.0))]

    def test_a_type_that_covers_no_tile_reserves_none_where_it_snaps(self) -> None:
        """A Xel'Naga healing shrine's footprint is empty, and its bounding box a rectangle with no center."""
        grids = _Game().observe(0, _scv(1))

        reservation = grids.reserve(UnitTypeId.HEALING_SHRINE_XEL_NAGA, at=(3.4, 7.6))

        assert reservation.at == Point((3.0, 8.0))
        assert not grids.reserved.any()

    def test_a_margin_reserves_the_tiles_within_it_too(self) -> None:
        """Two tiles on a depot's right, as a barracks keeps them for its add-on."""
        grids = _Game().observe(0, _scv(1))

        reservation = grids.reserve(_DEPOT, at=(8.0, 8.0), margin=(0, 2, 0, 0))

        assert reservation.margin == (0, 2, 0, 0)
        assert _picture(grids.reserved, x0=6, y0=6, x1=11, y1=9) == ["......", ".####.", ".####.", "......"]
        assert not grids.is_free(_DEPOT, at=(10.0, 8.0))

    def test_nearest_free_keeps_off_a_reservation(self) -> None:
        grids = _Game().observe(0, _scv(1))
        nearest = grids.nearest_free(_DEPOT, near=(8.0, 8.0), max_distance=8.0)

        grids.reserve(_DEPOT, at=(8.0, 8.0))

        assert nearest == Point((8.0, 8.0))
        assert grids.nearest_free(_DEPOT, near=(8.0, 8.0), max_distance=8.0) != nearest

    def test_a_withdrawn_reservation_frees_its_site_at_once(self) -> None:
        grids = _Game().observe(0, _scv(1))
        reservation = grids.reserve(_DEPOT, at=(8.0, 8.0))

        reservation.withdraw()
        reservation.withdraw()

        assert grids.reservations == ()
        assert not grids.reserved.any()

    def test_it_lasts_until_withdrawn(self) -> None:
        game = _Game()
        game.observe(0, _scv(1)).reserve(_DEPOT, at=(8.0, 8.0))

        assert game.observe(1000, _scv(1)).reserved[Point((8.0, 8.0))]

    def test_it_ends_once_max_steps_steps_have_passed(self) -> None:
        game = _Game()
        game.observe(0, _scv(1)).reserve(_DEPOT, at=(8.0, 8.0), max_steps=8)

        assert game.observe(7, _scv(1)).reservations
        assert not game.observe(8, _scv(1)).reservations

    def test_one_step_keeps_it_for_its_own_turn_alone(self) -> None:
        game = _Game()
        grids = game.observe(16, _scv(1))
        grids.reserve(_DEPOT, at=(8.0, 8.0), max_steps=1)

        assert grids.reserved.any()
        assert not game.observe(17, _scv(1)).reserved.any()

    def test_it_ends_once_one_of_this_players_structures_stands_on_its_site_and_never_comes_back(self) -> None:
        """Whatever type it stands as, as the reserved grid frees the site of a build begun."""
        game = _Game()
        game.observe(0, _scv(1)).reserve(_DEPOT, at=(8.0, 8.0))
        barracks = make_unit(2, UnitTypeId.BARRACKS, at=(8.5, 8.5), build_progress=0.05)

        assert game.observe(8, _scv(1)).reservations
        assert not game.observe(16, _scv(1), make_unit(2, _DEPOT, at=(8.0, 8.0), build_progress=0.05)).reservations
        assert not game.observe(24, _scv(1), dead=(2,)).reserved.any()
        assert not game.observe(32, _scv(1), barracks).reservations

    def test_another_players_structure_on_its_site_does_not_end_it(self) -> None:
        game = _Game()
        game.observe(0, _scv(1)).reserve(_DEPOT, at=(8.0, 8.0))
        theirs = make_unit(2, _DEPOT, at=(8.0, 8.0), alliance=Alliance.ENEMY)

        assert game.observe(8, _scv(1), theirs).reservations

    @pytest.mark.parametrize(
        ("unit_type", "max_steps", "message"),
        [
            (UnitTypeId.MARINE, None, "MARINE has no footprint"),
            (UnitTypeId.REFINERY, None, "REFINERY stands on a geyser"),
            (_DEPOT, 0, "max_steps is a number of steps, at least 1, not 0"),
        ],
        ids=["no footprint", "gas building", "no steps"],
    )
    def test_what_cannot_be_reserved_raises(self, unit_type: UnitTypeId, max_steps: int | None, message: str) -> None:
        grids = _Game().observe(0, _scv(1))

        with pytest.raises(ValueError, match=message):
            grids.reserve(unit_type, at=(8.0, 8.0), max_steps=max_steps)

        assert grids.reservations == ()

    @pytest.mark.parametrize(("margin", "error"), [(-1, ValueError), ((1, 1, 1), TypeError)], ids=["negative", "three"])
    def test_a_margin_negative_or_not_a_count_or_four_raises(self, margin: Any, error: type[Exception]) -> None:
        grids = _Game().observe(0, _scv(1))

        with pytest.raises(error, match="a margin"):
            grids.reserve(_DEPOT, at=(8.0, 8.0), margin=margin)

        assert grids.reservations == ()

    @pytest.mark.parametrize("max_steps", [True, 1.5], ids=["a bool", "a fraction"])
    def test_max_steps_that_is_not_a_whole_number_raises(self, max_steps: Any) -> None:
        grids = _Game().observe(0, _scv(1))

        with pytest.raises(TypeError, match="max_steps is a whole number of steps"):
            grids.reserve(_DEPOT, at=(8.0, 8.0), max_steps=max_steps)


# Creep over the right half of the drawn map, top row first.
_CREEP_ON_THE_RIGHT = tuple("." * 8 + "#" * 8 for _ in range(16))


class TestTheFreeGrids:
    def test_free_is_static_and_dynamic_less_reserved(self) -> None:
        game = _Game()
        game.observe(0, _scv(1), make_unit(2, UnitTypeId.SUPPLY_DEPOT, at=(12.0, 12.0)))
        game.book.issue(game.own(1), _BUILD_DEPOT, target=Point((8.0, 8.0)))
        grids = game.observe(0, _scv(1), make_unit(2, UnitTypeId.SUPPLY_DEPOT, at=(12.0, 12.0)))
        assert grids.free.equals(grids.static & grids.dynamic & ~grids.reserved)
        assert not grids.free[Point((1.5, 1.5))]
        assert not grids.free[Point((12.0, 12.0))]
        assert not grids.free[Point((8.0, 8.0))]
        assert grids.free[Point((5.5, 5.5))]

    @pytest.mark.parametrize(
        ("unit_type", "on_creep", "off_creep"),
        [
            (UnitTypeId.SUPPLY_DEPOT, False, True),
            (UnitTypeId.SPAWNING_POOL, True, False),
            (UnitTypeId.HATCHERY, True, True),
        ],
        ids=lambda value: value.name if isinstance(value, UnitTypeId) else None,
    )
    def test_free_for_a_type_keeps_to_the_creep_or_off_it_as_the_type_needs(
        self, unit_type: UnitTypeId, on_creep: bool, off_creep: bool
    ) -> None:
        """A terran structure stands off creep, a zerg one but a hatchery on it, and a hatchery either way."""
        grids = _Game().observe(0, creep=_CREEP_ON_THE_RIGHT)
        free = grids.free_for(unit_type)
        assert free[Point((12.5, 8.5))] is on_creep
        assert free[Point((4.5, 8.5))] is off_creep

    def test_free_for_a_type_with_no_rule_is_free(self) -> None:
        grids = _Game().observe(0, creep=_CREEP_ON_THE_RIGHT)
        assert grids.free_for(UnitTypeId.MARINE).equals(grids.free)


class TestPlacingOneStructure:
    def test_is_free_asks_every_tile_of_the_footprint_where_the_game_snaps_it(self) -> None:
        grids = _Game().observe(0, make_unit(1, UnitTypeId.SUPPLY_DEPOT, at=(12.0, 12.0)))
        assert grids.is_free(UnitTypeId.SUPPLY_DEPOT, at=Point((8.0, 8.0)))
        assert not grids.is_free(UnitTypeId.SUPPLY_DEPOT, at=Point((12.3, 13.4)))
        assert not grids.is_free(UnitTypeId.SUPPLY_DEPOT, at=Point((2.0, 8.0)))
        assert not grids.is_free(UnitTypeId.SUPPLY_DEPOT, at=Point((0.0, 0.0)))
        assert not grids.is_free(UnitTypeId.SPAWNING_POOL, at=Point((8.5, 8.5)))

    def test_a_type_with_no_footprint_raises(self) -> None:
        with pytest.raises(ValueError, match="MARINE has no footprint"):
            _Game().observe(0).is_free(UnitTypeId.MARINE, at=Point((8.0, 8.0)))

    def test_a_gas_building_raises(self) -> None:
        grids = _Game().observe(0, make_unit(1, UnitTypeId.VESPENE_GEYSER, at=(8.5, 8.5), alliance=_NEUTRAL))
        with pytest.raises(ValueError, match="REFINERY stands on a geyser"):
            grids.is_free(UnitTypeId.REFINERY, at=Point((8.5, 8.5)))
        with pytest.raises(ValueError, match="REFINERY stands on a geyser"):
            grids.nearest_free(UnitTypeId.REFINERY, near=Point((8.5, 8.5)), max_distance=3.0)

    def test_nearest_free_finds_the_nearest_site_beside_a_structure(self) -> None:
        grids = _Game().observe(0, make_unit(1, UnitTypeId.SUPPLY_DEPOT, at=(8.0, 8.0)))
        site = grids.nearest_free(UnitTypeId.SUPPLY_DEPOT, near=Point((8.2, 8.3)), max_distance=5.0)
        assert site == Point((8.0, 10.0))

    @pytest.mark.parametrize("margin", [0, 1, (0, 2, 0, 0), (1, 0, 2, 0)], ids=str)
    @pytest.mark.parametrize("unit_type", [UnitTypeId.SUPPLY_DEPOT, UnitTypeId.BARRACKS], ids=lambda t: t.name)
    @pytest.mark.parametrize("near", [Point((6.3, 9.8)), Point((3.0, 3.0)), Point((14.6, 0.4))], ids=str)
    def test_nearest_free_is_the_nearest_center_is_free_takes(
        self, unit_type: UnitTypeId, near: Point, margin: int | tuple[int, int, int, int]
    ) -> None:
        grids = _Game().observe(
            0,
            make_unit(1, UnitTypeId.SUPPLY_DEPOT, at=(6.0, 10.0)),
            make_unit(2, UnitTypeId.BARRACKS, at=(4.5, 4.5)),
            make_unit(3, UnitTypeId.MINERAL_FIELD, at=(13.0, 2.5), alliance=_NEUTRAL),
        )
        footprint = _TABLES.units[unit_type].footprint
        assert footprint is not None
        centers = [footprint.center_in_tile(Tile(x, y)) for x in range(16) for y in range(16)]
        taken = [c for c in centers if c.distance_to(near) <= 4.0 and grids.is_free(unit_type, at=c, margin=margin)]
        expected = min(taken, key=lambda c: c.distance_to(near), default=None)
        found = grids.nearest_free(unit_type, near=near, max_distance=4.0, margin=margin)
        assert (found is None) == (expected is None)
        if found is not None and expected is not None:
            assert found.distance_to(near) == pytest.approx(expected.distance_to(near))
            assert grids.is_free(unit_type, at=found, margin=margin)

    def test_a_margin_keeps_the_tiles_beside_the_footprint_free_on_the_sides_it_names(self) -> None:
        """A depot at (8, 8) covers x 7 to 8; a depot at (11, 8) covers x 10 to 11, one tile clear of it on the
        right."""
        grids = _Game().observe(0, make_unit(1, UnitTypeId.SUPPLY_DEPOT, at=(11.0, 8.0)))
        depot = UnitTypeId.SUPPLY_DEPOT
        assert grids.is_free(depot, at=Point((8.0, 8.0)))
        assert grids.is_free(depot, at=Point((8.0, 8.0)), margin=1)
        assert not grids.is_free(depot, at=Point((8.0, 8.0)), margin=2)
        assert not grids.is_free(depot, at=Point((8.0, 8.0)), margin=(0, 2, 0, 0))
        assert grids.is_free(depot, at=Point((8.0, 8.0)), margin=(2, 1, 2, 2))

    @pytest.mark.parametrize(
        ("margin", "error", "message"),
        [
            (-1, ValueError, "never negative"),
            ((0, 0, -1, 0), ValueError, "never negative"),
            ((1, 1, 1), TypeError, "a tuple of four"),
            ([1, 1, 1, 1], TypeError, "a tuple of four"),
            (1.5, TypeError, "a tuple of four"),
            (True, TypeError, "a tuple of four"),
        ],
        ids=str,
    )
    def test_a_margin_negative_or_not_a_count_or_four_raises(
        self, margin: object, error: type[Exception], message: str
    ) -> None:
        grids = _Game().observe(0)
        with pytest.raises(error, match=message):
            grids.is_free(UnitTypeId.SUPPLY_DEPOT, at=Point((8.0, 8.0)), margin=margin)  # pyright: ignore[reportArgumentType]
        with pytest.raises(error, match=message):
            grids.nearest_free(UnitTypeId.SUPPLY_DEPOT, near=Point((8.0, 8.0)), max_distance=3.0, margin=margin)  # pyright: ignore[reportArgumentType]

    def test_a_build_issued_earlier_in_the_turn_takes_its_site(self) -> None:
        game = _Game()
        grids = game.observe(0, _scv(1))
        assert grids.is_free(UnitTypeId.SUPPLY_DEPOT, at=Point((8.0, 8.0)))
        game.book.issue(game.own(1), _BUILD_DEPOT, target=Point((8.0, 8.0)))
        assert not grids.is_free(UnitTypeId.SUPPLY_DEPOT, at=Point((8.0, 8.0)))
        assert not grids.is_free(UnitTypeId.SUPPLY_DEPOT, at=Point((9.0, 9.0)))
        assert grids.nearest_free(UnitTypeId.SUPPLY_DEPOT, near=Point((8.0, 8.0)), max_distance=1.0) is None

    def test_nearest_free_with_no_limit_searches_the_whole_map(self) -> None:
        grids = _Game().observe(0, make_unit(1, UnitTypeId.SUPPLY_DEPOT, at=(8.0, 8.0)))
        unlimited = grids.nearest_free(UnitTypeId.SUPPLY_DEPOT, near=Point((8.2, 8.3)), max_distance=math.inf)
        assert unlimited == grids.nearest_free(UnitTypeId.SUPPLY_DEPOT, near=Point((8.2, 8.3)), max_distance=40.0)
        far = grids.nearest_free(UnitTypeId.SUPPLY_DEPOT, near=Point((200.0, 200.0)), max_distance=math.inf)
        assert far == Point((15.0, 15.0))

    @pytest.mark.parametrize("max_distance", [-1.0, math.nan], ids=str)
    def test_nearest_free_refuses_a_distance_that_is_none(self, max_distance: float) -> None:
        with pytest.raises(ValueError, match="max_distance"):
            _Game().observe(0).nearest_free(UnitTypeId.SUPPLY_DEPOT, near=Point((8.0, 8.0)), max_distance=max_distance)

    def test_nearest_free_is_none_where_nothing_fits_within_reach(self) -> None:
        grids = _Game().observe(0, make_unit(1, UnitTypeId.SUPPLY_DEPOT, at=(8.0, 8.0)))
        assert grids.nearest_free(UnitTypeId.SUPPLY_DEPOT, near=Point((8.0, 8.0)), max_distance=1.0) is None
        assert grids.nearest_free(UnitTypeId.SUPPLY_DEPOT, near=Point((100.0, 100.0)), max_distance=3.0) is None

    def test_a_townhall_keeps_off_the_ground_around_a_mineral_field(self) -> None:
        """A mineral field keeps a townhall off its 2x1 grown by 3 tiles (tool `sweep_townhall_placement`)."""
        grids = _Game().observe(0, make_unit(1, UnitTypeId.MINERAL_FIELD, at=(8.0, 2.5), alliance=_NEUTRAL))
        assert not grids.free_for(UnitTypeId.COMMAND_CENTER)[Point((8.5, 5.5))]
        assert grids.free_for(UnitTypeId.SUPPLY_DEPOT)[Point((8.5, 5.5))]
        assert grids.is_free(UnitTypeId.COMMAND_CENTER, at=Point((8.5, 8.5)))
        assert not grids.is_free(UnitTypeId.COMMAND_CENTER, at=Point((8.5, 7.5)))

    def test_a_townhall_keeps_off_the_ground_around_a_geyser(self) -> None:
        """A geyser keeps a townhall off its 3x3 grown by 3 tiles, less three at each corner (tool
        `sweep_townhall_placement`)."""
        grids = _Game().observe(0, make_unit(1, UnitTypeId.VESPENE_GEYSER, at=(8.5, 3.5), alliance=_NEUTRAL))
        assert not grids.free_for(UnitTypeId.COMMAND_CENTER)[Point((8.5, 6.5))]
        assert grids.free_for(UnitTypeId.SUPPLY_DEPOT)[Point((8.5, 6.5))]
        assert grids.free_for(UnitTypeId.COMMAND_CENTER)[Point((4.5, 7.5))]
        assert grids.is_free(UnitTypeId.COMMAND_CENTER, at=Point((8.5, 10.5)))
        assert not grids.is_free(UnitTypeId.COMMAND_CENTER, at=Point((8.5, 9.5)))


def _first_observation(path: Path) -> tuple[GameMap, _Tracker, GameData, sc2api_pb2.ResponseObservation]:
    """The map of a recorded game, its first observation, and a tracker that has taken it in."""
    info: sc2api_pb2.ResponseGameInfo | None = None
    data: GameData | None = None
    for exchange in Recording(path):
        response = exchange.response
        if response.HasField("game_info"):
            info = response.game_info
        elif response.HasField("data"):
            data = GameData(response.data)
        elif response.HasField("observation"):
            assert info is not None and data is not None, f"{path.name} has an observation before the map"
            observation = response.observation
            tracker = _Tracker(data, make_enemy())
            tracker.update(observation.observation.raw_data, observation.observation.game_loop)
            return GameMap._from_game(info, observation), tracker, data, observation
    raise AssertionError(f"{path.name} holds no observation")


@pytest.mark.parametrize("path", CORPUS, ids=lambda path: path.stem)
class TestARecordedGamesStart:
    def _grids(self, path: Path) -> tuple[GameMap, _Tracker, PlacementGrids]:
        game_map, tracker, data, observation = _first_observation(path)
        state = _State(observation, tracker, game_map)
        order_book = OrderBook(data)
        present_units = tracker.unit_tracker.present
        grids = PlacementGrids._build_at_start(
            game_map.placement,
            present_units=present_units,
            state=state,
            step=observation.observation.game_loop,
            game_data=data,
            order_book=order_book,
        )
        return game_map, tracker, grids

    def test_every_one_of_the_maps_units_but_an_acceleration_zone_has_a_measured_footprint(self, path: Path) -> None:
        _, tracker, _ = self._grids(path)
        unmeasured = {unit.type_id for unit in tracker.unit_tracker.present.neutral if unit.type_data.footprint is None}
        assert unmeasured <= {UnitTypeId.ACCELERATION_ZONE_SMALL, UnitTypeId.ACCELERATION_ZONE_LARGE}

    def test_the_geysers_and_collapsible_towers_are_closed_from_the_start(self, path: Path) -> None:
        _, tracker, grids = self._grids(path)
        neutral_units = tracker.unit_tracker.present.neutral
        for unit in neutral_units.of_type(UnitType.AnyVespeneGeyser, UnitType.CollapsibleTower):
            assert not grids.static[unit.position]

    def test_this_players_townhall_and_mineral_fields_close_the_ground_under_them(self, path: Path) -> None:
        game_map, tracker, grids = self._grids(path)
        assert grids.static[game_map.start_location]
        assert not grids.dynamic[game_map.start_location]
        for field in tracker.unit_tracker.present.neutral.of_type(UnitType.AnyMineralField):
            assert not grids.dynamic[field.position + (-0.5, 0.0)]
            assert not grids.dynamic[field.position + (0.5, 0.0)]

    def test_nothing_is_reserved_before_an_order(self, path: Path) -> None:
        _, _, grids = self._grids(path)
        assert not grids.reserved.any()


def _depot_site(api: Api) -> Point:
    """Where a supply depot fits on the grids, nearest the point 10 from this player's start toward the middle."""
    toward = api.map.start_location.towards(api.map.playable_area.center, 10.0)
    site = api.placement.nearest_free(UnitTypeId.SUPPLY_DEPOT, near=toward, max_distance=8.0)
    assert site is not None
    return site


def _game_takes_a_sensor_tower(transport: WebSocketTransport, tiles: Sequence[Tile]) -> list[bool]:
    """Whether the game would take a sensor tower, which covers one tile, on each of `tiles`."""
    placements = [
        query_pb2.RequestQueryBuildingPlacement(
            ability_id=AbilityId.SCV_BUILD_SENSOR_TOWER, target_pos=common_pb2.Point2D(x=tile.center.x, y=tile.center.y)
        )
        for tile in tiles
    ]
    response = transport.request(sc2api_pb2.Request(query=query_pb2.RequestQuery(placements=placements)))
    return [answer.result == error_pb2.Success for answer in response.query.placements]


class _DepotBot:
    """A bot that, once it has the minerals, has an SCV build a supply depot where the grids leave room, and records
    each turn after whether the depot stands, and whether its site is reserved and open. Once the depot stands it asks
    the game about every tile within 3 of the site."""

    def __init__(self, api: Api, transport: WebSocketTransport) -> None:
        self.api = api
        self.transport = transport
        self.site: Point | None = None
        self.turns: list[tuple[bool, bool, bool]] = []
        self.game_answers: dict[Tile, bool] = {}
        self.grid_answers: dict[Tile, bool] = {}

    def turn(self, _: TurnEvent) -> None:
        api = self.api
        if self.site is None:
            if api.resources.minerals >= 100:
                self.site = _depot_site(api)
                scv = api.units.own.of_type(UnitTypeId.SCV).closest_to(self.site)
                api.orders.issue(scv, _BUILD_DEPOT, target=self.site)
            return
        placement = api.placement
        stands = bool(api.units.own.of_type(UnitTypeId.SUPPLY_DEPOT))
        self.turns.append((stands, placement.reserved[self.site], placement.dynamic[self.site]))
        if stands and not self.game_answers:
            site = Tile.containing(self.site)
            tiles = [Tile(site.x + dx, site.y + dy) for dx in range(-3, 4) for dy in range(-3, 4)]
            self.game_answers = dict(zip(tiles, _game_takes_a_sensor_tower(self.transport, tiles), strict=True))
            self.grid_answers = {tile: placement.dynamic[tile] for tile in tiles}


@pytest.mark.integration
class TestAgainstTheRealGame:
    """Run with `pytest -m integration`. Plays a minute of a game."""

    def test_a_depot_reserves_its_site_until_it_stands_and_then_the_grid_agrees_with_the_game(self) -> None:
        try:
            game_map = MapFile.find("PylonAIE_v4")
        except MapNotFoundError as missing:
            pytest.skip(str(missing))
        with (
            GameProcess.launch(window=(640, 480)) as process,
            closing(Client(transport := WebSocketTransport.connect(process.url))) as client,
        ):
            client.create_game(game_map.path, [Participant(), Computer(Race.ZERG, Difficulty.VERY_EASY)])
            client.join_game(Race.TERRAN)
            api = Api()
            bot = _DepotBot(api, transport)
            api.on(TurnEvent)(bot.turn)
            api.play(client, steps_per_turn=8, time_limit=60)

        print("the site, turn by turn (depot stands, reserved, open):", bot.turns)
        before = [turn for turn in bot.turns if not turn[0]]
        after = [turn for turn in bot.turns if turn[0]]
        assert before and after
        assert all(reserved and open_ground for _, reserved, open_ground in before)
        assert not any(reserved or open_ground for _, reserved, open_ground in after)
        assert bot.grid_answers == bot.game_answers
