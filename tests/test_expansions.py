"""Tests for the expansions found at the start of a game."""

import math
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, cast

import pytest
from s2clientprotocol import data_pb2, raw_pb2, sc2api_pb2

from sc2nachos import Api, Race, Result
from sc2nachos.events import GameStartEvent
from sc2nachos.gamedata import GameData
from sc2nachos.gamemap import (
    Expansion,
    GameMap,
    NoMainRampError,
    NoNaturalError,
    NoStartLocationError,
    StartLocation,
)
from sc2nachos.gamemap._expansion_locations import _ExpansionLocations
from sc2nachos.geometry import Point, Tile
from sc2nachos.ids import UnitTypeId
from sc2nachos.protocol import Client, Recording, Status
from sc2nachos.units import Alliance, NotReportedError, Unit, Units, UnitType, Visibility
from sc2nachos.units._tracking import _Tracker
from sc2nachos.units._tracking._unit_tracker import _UnitTracker
from support import (
    PLAYER_ID,
    FakeTransport,
    make_enemy,
    make_game_info,
    make_observation,
    make_response,
    make_tables,
    make_unit,
)

CORPUS = sorted((Path(__file__).parent / "corpus").glob("*.sc2rec"))

# Rows for the map's units these tests put down, which bring the footprints measured in game.
_TABLES = make_tables(
    data_pb2.UnitTypeData(unit_id=UnitTypeId.WATCHTOWER),
    data_pb2.UnitTypeData(unit_id=UnitTypeId.COLLAPSIBLE_TOWER_DIAGONAL_ROCK),
    data_pb2.UnitTypeData(unit_id=UnitTypeId.COMMAND_CENTER),
    data_pb2.UnitTypeData(unit_id=UnitTypeId.COMMAND_CENTER_FLYING),
    data_pb2.UnitTypeData(unit_id=UnitTypeId.HATCHERY),
)

# Pylon's main base, by each resource's offset from where its townhall stands: 8 fields and 2 geysers (corpus).
_MAIN_FIELDS = (
    (-0.5, -6.0),
    (6.5, 0.0),
    (2.5, -6.0),
    (6.5, -2.0),
    (0.5, -7.0),
    (5.5, -5.0),
    (7.5, -1.0),
    (6.5, -4.0),
)
_MAIN_GEYSERS = ((7.0, 3.0), (-4.0, -7.0))

# One of Torches' gold bases, by offset from its lower townhall: 6 fields in a line with a geyser at each end, one
# beside each townhall, the upper standing 13 tiles up (corpus).
_GOLD_FIELDS = ((2.5, 6.0), (-2.5, 6.0), (-1.5, 7.0), (5.5, 6.0), (4.5, 7.0), (-4.5, 7.0))
_GOLD_GEYSERS = ((7.0, 2.0), (7.0, 11.0))


def _map_rows(width: int, height: int, blocked: Callable[[int, int], bool] = lambda x, y: False) -> list[str]:
    """A map's rows, top row first: open ground, but where `blocked` says a tile is neither walkable nor buildable."""
    return ["".join("." if blocked(x, y) else "#" for x in range(width)) for y in reversed(range(height))]


class _NeutralUnits:
    """Neutral units to put on a map, as the game reports them at the start: out of sight."""

    def __init__(self) -> None:
        self.units: list[raw_pb2.Unit] = []

    def add(self, unit_type: UnitTypeId, at: tuple[float, float], **fields: float) -> raw_pb2.Unit:
        unit = make_unit(
            len(self.units) + 1, unit_type, at=at, alliance=Alliance.NEUTRAL, visibility=Visibility.IN_FOG, **fields
        )
        self.units.append(unit)
        return unit

    def base(
        self,
        at: tuple[float, float],
        fields: Sequence[tuple[float, float]] = _MAIN_FIELDS,
        geysers: Sequence[tuple[float, float]] = _MAIN_GEYSERS,
    ) -> None:
        """A base laid out as `fields` and `geysers` say, around a townhall at `at`."""
        for dx, dy in fields:
            self.add(UnitTypeId.MINERAL_FIELD, (at[0] + dx, at[1] + dy))
        for dx, dy in geysers:
            self.add(UnitTypeId.VESPENE_GEYSER, (at[0] + dx, at[1] + dy))


class _MapAtStart:
    """A drawn map with neutral units on it, read as a game reads it at the start."""

    def __init__(
        self,
        rows: Sequence[str],
        neutral_units: _NeutralUnits,
        *,
        start: tuple[float, float],
        opponent_starts: tuple[tuple[float, float], ...] = (),
    ) -> None:
        self.map = GameMap(make_game_info(*rows, start_locations=opponent_starts), start_location=Point(start))
        self.tracker = _Tracker(_TABLES, make_enemy(_TABLES))
        self.tracker.update(make_observation(units=neutral_units.units).observation.raw_data, 0)

    @property
    def neutral_units(self) -> Units[Unit[Any]]:
        return self.tracker.unit_tracker.present.neutral

    def expansions(self) -> tuple[Expansion, ...]:
        return Expansion._find_all(self.map, self.tracker.unit_tracker)

    def townhall_refusals_around(self, resource: tuple[float, float]) -> list[str]:
        """The townhall centers within 7 tiles of `resource` along each axis, the top row first: `#` refused."""
        neutral_units = self.neutral_units
        expansion_locations = _ExpansionLocations(
            self.map.placement,
            mineral_fields=neutral_units.of_type(UnitType.AnyMineralField),
            geysers=neutral_units.of_type(UnitType.AnyVespeneGeyser),
            blockers=neutral_units.excluding_type(UnitType.AnyMineralField, UnitType.AnyVespeneGeyser),
        )
        # The tile centers nearest the resource's own center, a half tile off it where it stands on a tile edge.
        x0, y0 = int(resource[0]) + 0.5, int(resource[1]) + 0.5
        return [
            "".join("." if expansion_locations.allows((x0 + dx, y0 + dy)) else "#" for dx in range(-7, 8))
            for dy in range(7, -8, -1)
        ]


def _centers(expansions: Sequence[Expansion]) -> list[tuple[float, float]]:
    return [(expansion.center.x, expansion.center.y) for expansion in expansions]


class TestWhereATownhallGoes:
    def test_a_mineral_field_refuses_a_townhall_within_three_tiles_but_at_the_corners(self) -> None:
        """Both gaps between the footprints under 3, but not both 2 (tool `sweep_townhall_placement`)."""
        neutral_units = _NeutralUnits()
        neutral_units.add(UnitTypeId.MINERAL_FIELD, (16.0, 16.5))
        assert _MapAtStart(_map_rows(32, 32), neutral_units, start=(0.5, 0.5)).townhall_refusals_around(
            (16.0, 16.5)
        ) == [
            "...............",
            "...............",
            "..##########...",
            ".############..",
            ".############..",
            ".############..",
            ".############..",
            ".############..",
            ".############..",
            ".############..",
            ".############..",
            ".############..",
            "..##########...",
            "...............",
            "...............",
        ]

    def test_a_geyser_refuses_a_townhall_while_the_gaps_add_up_to_under_three(self) -> None:
        neutral_units = _NeutralUnits()
        neutral_units.add(UnitTypeId.VESPENE_GEYSER_RICH, (16.5, 16.5))
        assert _MapAtStart(_map_rows(32, 32), neutral_units, start=(0.5, 0.5)).townhall_refusals_around(
            (16.5, 16.5)
        ) == [
            "...............",
            "...#########...",
            "..###########..",
            ".#############.",
            ".#############.",
            ".#############.",
            ".#############.",
            ".#############.",
            ".#############.",
            ".#############.",
            ".#############.",
            ".#############.",
            "..###########..",
            "...#########...",
            "...............",
        ]

    def test_another_neutral_unit_blocks_its_footprint(self) -> None:
        """The placement grid leaves a watchtower's ground open, though the game takes no townhall on it."""
        neutral_units = _NeutralUnits()
        neutral_units.add(UnitTypeId.WATCHTOWER, (16.0, 16.0), radius=1.125)
        assert _MapAtStart(_map_rows(32, 32), neutral_units, start=(0.5, 0.5)).townhall_refusals_around(
            (16.0, 16.0)
        ) == [
            "...............",
            "...............",
            "...............",
            "...............",
            "...............",
            "....######.....",
            "....######.....",
            "....######.....",
            "....######.....",
            "....######.....",
            "....######.....",
            "...............",
            "...............",
            "...............",
            "...............",
        ]

    def test_a_collapsible_tower_blocks_less_than_the_square_its_radius_spans(self) -> None:
        """Its radius spans 6 tiles, and it covers 5 (tool `sweep_placement`)."""
        neutral_units = _NeutralUnits()
        neutral_units.add(UnitTypeId.COLLAPSIBLE_TOWER_DIAGONAL_ROCK, (16.5, 16.5), radius=2.9375)
        assert _MapAtStart(_map_rows(32, 32), neutral_units, start=(0.5, 0.5)).townhall_refusals_around(
            (16.5, 16.5)
        ) == [
            "...............",
            "...............",
            "...............",
            "...#########...",
            "...#########...",
            "...#########...",
            "...#########...",
            "...#########...",
            "...#########...",
            "...#########...",
            "...#########...",
            "...#########...",
            "...............",
            "...............",
            "...............",
        ]


class TestWhatIsAnExpansion:
    def test_a_base_is_one_expansion_where_its_townhall_stood_holding_its_resources(self) -> None:
        neutral_units = _NeutralUnits()
        neutral_units.base((24.5, 24.5))
        map_at_start = _MapAtStart(_map_rows(48, 48), neutral_units, start=(24.5, 24.5))
        (expansion,) = map_at_start.expansions()
        assert expansion.center == Point((24.5, 24.5))
        assert isinstance(expansion, StartLocation)
        assert expansion.pathing_distance_from_start == 0.0
        assert {unit.tag for unit in expansion.mineral_fields} == set(range(1, 9))
        assert {unit.tag for unit in expansion.geysers} == {9, 10}
        distances = [field.position.distance_to(expansion.center) for field in expansion.mineral_fields]
        assert distances == sorted(distances)

    @pytest.mark.parametrize(("field_count", "expansion_count"), [(5, 0), (6, 1), (10, 1), (11, 0)])
    def test_an_expansion_has_6_to_10_mineral_fields(self, field_count: int, expansion_count: int) -> None:
        """Fewer are a blocker to mine out, more a wall."""
        neutral_units = _NeutralUnits()
        for index in range(field_count):
            neutral_units.add(UnitTypeId.MINERAL_FIELD_RICH, (8.0 + 2 * index, 20.5))
        assert len(_MapAtStart(_map_rows(48, 48), neutral_units, start=(0.5, 0.5)).expansions()) == expansion_count

    def test_geysers_do_not_count_toward_the_fields(self) -> None:
        neutral_units = _NeutralUnits()
        neutral_units.base((24.5, 24.5), _MAIN_FIELDS[:5], _MAIN_GEYSERS)
        assert _MapAtStart(_map_rows(48, 48), neutral_units, start=(0.5, 0.5)).expansions() == ()

    def test_fields_mined_from_either_side_are_an_expansion_on_each_with_its_own_geyser(self) -> None:
        neutral_units = _NeutralUnits()
        neutral_units.base((24.5, 14.5), _GOLD_FIELDS, _GOLD_GEYSERS)
        map_at_start = _MapAtStart(_map_rows(48, 48), neutral_units, start=(24.5, 14.5))
        lower, upper = map_at_start.expansions()
        assert (lower.center, upper.center) == (Point((24.5, 14.5)), Point((24.5, 27.5)))
        assert [unit.tag for unit in lower.geysers] == [7]
        assert [unit.tag for unit in upper.geysers] == [8]
        assert set(lower.mineral_fields) == set(upper.mineral_fields)
        assert len(lower.mineral_fields) == 6

    def test_a_field_out_of_sight_at_the_start_is_the_same_unit_once_seen(self) -> None:
        """The game lists a field anew under another tag when it comes into sight."""
        neutral_units = _NeutralUnits()
        neutral_units.base((24.5, 24.5))
        map_at_start = _MapAtStart(_map_rows(48, 48), neutral_units, start=(24.5, 24.5))
        (expansion,) = map_at_start.expansions()
        remembered = neutral_units.units[0]
        seen = make_unit(
            100,
            UnitTypeId.MINERAL_FIELD,
            at=(remembered.pos.x, remembered.pos.y),
            alliance=Alliance.NEUTRAL,
            mineral_contents=1800,
        )
        units = [seen, *neutral_units.units[1:]]
        map_at_start.tracker.update(make_observation(16, units=units).observation.raw_data, 16)
        (field,) = [unit for unit in expansion.mineral_fields if unit.tag == 100]
        assert field.mineral_contents == 1800
        assert field in map_at_start.tracker.unit_tracker.present


class TestTheTownhallStandingOnIt:
    def _expansion_with(self, *townhalls: raw_pb2.Unit) -> tuple[Expansion, _UnitTracker]:
        """The one expansion of a drawn base, seen in an observation that also lists `townhalls`."""
        neutral_units = _NeutralUnits()
        neutral_units.base((24.5, 24.5))
        map_at_start = _MapAtStart(_map_rows(48, 48), neutral_units, start=(24.5, 24.5))
        (expansion,) = map_at_start.expansions()
        observation = make_observation(16, units=[*neutral_units.units, *townhalls]).observation.raw_data
        map_at_start.tracker.update(observation, 16)
        return expansion, map_at_start.tracker.unit_tracker

    def test_it_is_the_townhall_whose_footprint_covers_the_center(self) -> None:
        expansion, units = self._expansion_with(make_unit(200, UnitTypeId.COMMAND_CENTER, at=(25.5, 23.5)))
        assert expansion.townhall is units.by_tag(200)

    def test_it_can_be_an_enemy_s_remembered_in_the_fog(self) -> None:
        hatchery = make_unit(
            200, UnitTypeId.HATCHERY, at=(24.5, 24.5), alliance=Alliance.ENEMY, visibility=Visibility.IN_FOG
        )
        expansion, units = self._expansion_with(hatchery)
        assert expansion.townhall is units.by_tag(200)

    @pytest.mark.parametrize(
        "townhall",
        [
            make_unit(200, UnitTypeId.COMMAND_CENTER_FLYING, at=(24.5, 24.5)),
            make_unit(200, UnitTypeId.COMMAND_CENTER, at=(27.5, 24.5)),
            None,
        ],
        ids=["in the air", "off the center", "none"],
    )
    def test_there_is_none_unless_one_stands_on_the_center(self, townhall: raw_pb2.Unit | None) -> None:
        expansion, _ = self._expansion_with(*(() if townhall is None else (townhall,)))
        assert expansion.townhall is None


class TestTheOrder:
    # A wall at x = 25 and 26 up to y = 50, and a wall at x = 48 and 49 the whole way up, beyond which nothing walks.
    _ROWS = _map_rows(80, 60, lambda x, y: (x in (25, 26) and y <= 50) or x in (48, 49))

    def test_the_start_comes_first_then_the_nearest_by_walking(self) -> None:
        """The base behind the wall is nearer in a line, 28 to 35, and farther to walk."""
        neutral_units = _NeutralUnits()
        for at in ((10.5, 15.5), (38.5, 15.5), (10.5, 50.5)):
            neutral_units.base(at)
        map_at_start = _MapAtStart(self._ROWS, neutral_units, start=(10.5, 15.5))
        expansions = map_at_start.expansions()
        assert _centers(expansions) == [(10.5, 15.5), (10.5, 50.5), (38.5, 15.5)]
        start, open_side, behind_the_wall = (expansion.pathing_distance_from_start for expansion in expansions)
        assert (start, open_side) == (0.0, 35.0)
        assert behind_the_wall > open_side

    def test_a_base_no_walk_reaches_comes_last(self) -> None:
        neutral_units = _NeutralUnits()
        for at in ((64.5, 30.5), (10.5, 15.5), (38.5, 15.5)):
            neutral_units.base(at)
        map_at_start = _MapAtStart(self._ROWS, neutral_units, start=(10.5, 15.5))
        expansions = map_at_start.expansions()
        assert _centers(expansions) == [(10.5, 15.5), (38.5, 15.5), (64.5, 30.5)]
        assert expansions[-1].pathing_distance_from_start == math.inf


# The three bases of `TestTheOrder`: the start, one past the end of the wall, and one behind it, nearer in a line.
_START = (10.5, 15.5)
_PAST_THE_WALL = (10.5, 50.5)
_BEHIND_THE_WALL = (38.5, 15.5)


class TestAStartLocation:
    _ROWS = TestTheOrder._ROWS

    def _expansions(self, *, opponent_starts: tuple[tuple[float, float], ...]) -> tuple[Expansion, ...]:
        neutral_units = _NeutralUnits()
        for at in (_START, _PAST_THE_WALL, _BEHIND_THE_WALL):
            neutral_units.base(at)
        map_at_start = _MapAtStart(self._ROWS, neutral_units, start=_START, opponent_starts=opponent_starts)
        return map_at_start.expansions()

    def test_every_start_is_a_start_location_and_no_other_expansion_is(self) -> None:
        expansions = self._expansions(opponent_starts=(_BEHIND_THE_WALL,))
        assert _centers(expansions) == [_START, _PAST_THE_WALL, _BEHIND_THE_WALL]
        assert [type(expansion) for expansion in expansions] == [StartLocation, Expansion, StartLocation]

    def test_its_natural_is_the_nearest_expansion_to_walk_to_not_the_nearest_in_a_line(self) -> None:
        start, past_the_wall, _ = self._expansions(opponent_starts=())
        assert isinstance(start, StartLocation)
        assert start.natural is past_the_wall

    def test_its_natural_is_never_where_a_player_starts(self) -> None:
        start, opponent_start, behind_the_wall = self._expansions(opponent_starts=(_PAST_THE_WALL,))
        assert isinstance(start, StartLocation) and isinstance(opponent_start, StartLocation)
        assert start.natural is opponent_start.natural is behind_the_wall

    def test_with_no_other_expansion_to_walk_to_its_natural_raises(self) -> None:
        neutral_units = _NeutralUnits()
        neutral_units.base(_START)
        neutral_units.base((64.5, 30.5))
        start, beyond_the_far_wall = _MapAtStart(self._ROWS, neutral_units, start=_START).expansions()
        assert beyond_the_far_wall.pathing_distance_from_start == math.inf
        assert isinstance(start, StartLocation)
        with pytest.raises(NoNaturalError, match="no expansion where no player starts can be walked to"):
            _ = start.natural

    def test_with_no_ramp_out_of_its_main_its_main_ramp_raises(self) -> None:
        (start, *_) = self._expansions(opponent_starts=())
        assert isinstance(start, StartLocation)
        with pytest.raises(NoMainRampError, match="no ramp out of the main"):
            _ = start.main_ramp

    def test_its_main_area_is_the_ground_joined_to_it_at_its_level(self) -> None:
        """A wall two tiles wide at x = 48 and 49 parts the map's 80 columns of 60 tiles."""
        neutral_units = _NeutralUnits()
        neutral_units.base(_START)
        neutral_units.base((64.5, 30.5))
        rows = _map_rows(80, 60, lambda x, y: x in (48, 49))
        this_side, far_side = _MapAtStart(
            rows, neutral_units, start=_START, opponent_starts=((64.5, 30.5),)
        ).expansions()
        assert isinstance(this_side, StartLocation) and isinstance(far_side, StartLocation)
        assert len(this_side.main_area) == 48 * 60
        assert Tile.at(this_side.center) in this_side.main_area
        assert Tile(47, 0) in this_side.main_area and Tile(48, 0) not in this_side.main_area
        assert len(far_side.main_area) == 30 * 60
        assert Tile(50, 59) in far_side.main_area


def _game_at_the_start(
    neutral_units: _NeutralUnits, *, opponent_starts: tuple[tuple[float, float], ...]
) -> dict[str, object]:
    """What the api reads of the starts as a game begins, this player's townhall at `_START` on `TestTheOrder`'s map
    with `neutral_units` on it: each read's value, or the error it raised."""
    townhall = make_unit(100, UnitTypeId.COMMAND_CENTER, at=_START)
    units = [*neutral_units.units, townhall]
    observations = (make_observation(0, units=units), make_observation(16, (PLAYER_ID, Result.VICTORY), units=units))
    transport = FakeTransport(
        make_response(join_game=sc2api_pb2.ResponseJoinGame(player_id=PLAYER_ID)),
        make_response(game_info=make_game_info(*TestTheOrder._ROWS, start_locations=opponent_starts)),
        make_response(data=sc2api_pb2.ResponseData()),
        make_response(observation=observations[0]),
        make_response(step=sc2api_pb2.ResponseStep()),
        make_response(Status.ENDED, observation=observations[1]),
    )
    client = Client(transport)
    client.join_game(Race.TERRAN)
    api = Api()
    read: dict[str, object] = {}

    @api.on(GameStartEvent)
    def take_the_starts(_: GameStartEvent) -> None:
        read["expansions"] = api.expansions
        read["map start"] = api.map.start_location
        for name, reader in (
            ("player", lambda: api.player.start_location),
            ("enemy", lambda: api.enemy.start_location),
        ):
            try:
                read[name] = reader()
            except (NotReportedError, NoStartLocationError) as error:
                read[name] = error

    api.play(client)
    return read


class TestWhereEachPlayerStarted:
    def _bases(self) -> _NeutralUnits:
        neutral_units = _NeutralUnits()
        for at in (_START, _PAST_THE_WALL, _BEHIND_THE_WALL):
            neutral_units.base(at)
        return neutral_units

    def test_this_player_started_where_its_townhall_stood_and_the_enemy_at_the_other_start(self) -> None:
        read = _game_at_the_start(self._bases(), opponent_starts=(_BEHIND_THE_WALL,))
        start, past_the_wall, behind_the_wall = cast("tuple[Expansion, ...]", read["expansions"])
        assert isinstance(start, StartLocation)
        assert read["player"] is start
        assert start.center == read["map start"]
        assert read["enemy"] is behind_the_wall
        assert start.natural is past_the_wall

    def test_on_a_map_of_more_than_two_starts_where_the_enemy_started_is_not_reported(self) -> None:
        read = _game_at_the_start(self._bases(), opponent_starts=(_PAST_THE_WALL, _BEHIND_THE_WALL))
        assert isinstance(read["player"], StartLocation)
        assert isinstance(read["enemy"], NotReportedError)
        assert "2 start locations but this player's" in str(read["enemy"])

    def test_where_no_expansion_stands_at_a_start_reading_it_raises(self) -> None:
        read = _game_at_the_start(_NeutralUnits(), opponent_starts=(_BEHIND_THE_WALL,))
        assert read["expansions"] == ()
        assert isinstance(read["player"], NoStartLocationError)
        assert isinstance(read["enemy"], NoStartLocationError)


def _recorded_map_and_units(path: Path) -> tuple[GameMap, _UnitTracker]:
    """The map of a recorded game, and the units of its first observation."""
    info: sc2api_pb2.ResponseGameInfo | None = None
    data: sc2api_pb2.ResponseData | None = None
    for exchange in Recording(path):
        response = exchange.response
        if response.HasField("game_info"):
            info = response.game_info
        elif response.HasField("data"):
            data = response.data
        elif response.HasField("observation"):
            assert info is not None and data is not None, f"{path.name} has an observation before the map"
            observation = response.observation
            tables = GameData(data)
            tracker = _Tracker(tables, make_enemy(tables))
            tracker.update(observation.observation.raw_data, observation.observation.game_loop)
            return GameMap._from_game(info, observation), tracker.unit_tracker
    raise AssertionError(f"{path.name} holds no observation")


# How many expansions each map of the corpus has.
_COUNTS = {
    "IncorporealAIE_v4": 14,
    "LeyLinesAIE_v3": 18,
    "MagannathaAIE_v2": 16,
    "PersephoneAIE_v4": 15,
    "PylonAIE_v4": 14,
    "TorchesAIE_v4": 18,
    "UltraloveAIE_v2": 14,
}


# Each start location's natural, by map, both below their mains' ramps (corpus).
_NATURALS = {
    "IncorporealAIE_v4": {(32.5, 139.5): (59.5, 138.5), (123.5, 24.5): (96.5, 25.5)},
    "LeyLinesAIE_v3": {(42.5, 40.5): (40.5, 69.5), (155.5, 133.5): (157.5, 104.5)},
    "MagannathaAIE_v2": {(141.5, 38.5): (117.5, 28.5), (38.5, 141.5): (28.5, 117.5)},
    "PersephoneAIE_v4": {(37.5, 34.5): (65.5, 30.5), (37.5, 145.5): (65.5, 149.5)},
    "PylonAIE_v4": {(175.5, 76.5): (149.5, 74.5), (72.5, 171.5): (98.5, 173.5)},
    "TorchesAIE_v4": {(124.5, 159.5): (95.5, 157.5), (124.5, 48.5): (95.5, 50.5)},
    "UltraloveAIE_v2": {(42.5, 46.5): (68.5, 42.5), (141.5, 137.5): (115.5, 141.5)},
}

# How many tiles each main's ground covers, by map, this player's first: the two within a tile of each other, though
# the game's pathing grid blocks the resources of this player's alone (corpus).
_MAIN_AREA_SIZES = {
    "IncorporealAIE_v4": (755, 755),
    "LeyLinesAIE_v3": (770, 771),
    "MagannathaAIE_v2": (744, 744),
    "PersephoneAIE_v4": (774, 774),
    "PylonAIE_v4": (794, 794),
    "TorchesAIE_v4": (777, 777),
    "UltraloveAIE_v2": (793, 793),
}


@pytest.mark.parametrize("path", CORPUS, ids=lambda path: path.stem)
class TestARecordedMap:
    def test_this_player_starts_at_the_first_expansion_holding_the_fields_it_sees(self, path: Path) -> None:
        game_map, unit_tracker = _recorded_map_and_units(path)
        first = Expansion._find_all(game_map, unit_tracker)[0]
        assert first.center == game_map.start_location
        assert len(first.mineral_fields) == 8
        assert all(field.visibility is Visibility.VISIBLE for field in first.mineral_fields)

    def test_the_start_locations_are_the_expansions_marked_as_one(self, path: Path) -> None:
        game_map, unit_tracker = _recorded_map_and_units(path)
        expansions = Expansion._find_all(game_map, unit_tracker)
        marked = {expansion.center for expansion in expansions if isinstance(expansion, StartLocation)}
        assert marked == {game_map.start_location, *game_map.opponent_start_locations}

    def test_the_map_has_as_many_expansions_as_counted(self, path: Path) -> None:
        game_map, unit_tracker = _recorded_map_and_units(path)
        assert len(Expansion._find_all(game_map, unit_tracker)) == _COUNTS[path.stem.split("-")[0]]

    def test_each_start_location_s_natural_is_the_expansion_below_its_main(self, path: Path) -> None:
        game_map, unit_tracker = _recorded_map_and_units(path)
        expansions = Expansion._find_all(game_map, unit_tracker)
        starts = [expansion for expansion in expansions if isinstance(expansion, StartLocation)]
        naturals = {
            (start.center.x, start.center.y): (start.natural.center.x, start.natural.center.y) for start in starts
        }
        assert naturals == _NATURALS[path.stem.split("-")[0]]
        assert starts[0].natural is expansions[1]

    def test_each_main_area_holds_its_start_and_no_other_expansion_and_borders_its_main_ramp_alone(
        self, path: Path
    ) -> None:
        game_map, unit_tracker = _recorded_map_and_units(path)
        expansions = Expansion._find_all(game_map, unit_tracker)
        starts = [expansion for expansion in expansions if isinstance(expansion, StartLocation)]
        assert tuple(len(start.main_area) for start in starts) == _MAIN_AREA_SIZES[path.stem.split("-")[0]]
        for start in starts:
            main_area = start.main_area
            assert [expansion for expansion in expansions if Tile.at(expansion.center) in main_area] == [start]
            bordering = [
                ramp
                for ramp in game_map.ramps
                if any(neighbor in main_area for tile in ramp.top for neighbor in tile.neighbors8)
            ]
            assert bordering == [start.main_ramp]
