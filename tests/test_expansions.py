"""Tests for the expansions found at the start of a game."""

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest
from s2clientprotocol import raw_pb2, sc2api_pb2

from sc2nachos.enemy import Enemy
from sc2nachos.gamedata import GameData
from sc2nachos.gamemap import Expansion, GameMap
from sc2nachos.gamemap._expansion import _where_townhall_allowed
from sc2nachos.geometry import Point
from sc2nachos.ids import UnitTypeId
from sc2nachos.protocol import Recording
from sc2nachos.units import Alliance, Unit, Units, UnitType, Visibility
from sc2nachos.units._tracking import _Tracker
from support import make_game_info, make_observation, make_tables, make_unit

CORPUS = sorted((Path(__file__).parent / "corpus").glob("*.sc2rec"))

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

    def __init__(self, rows: Sequence[str], neutral_units: _NeutralUnits, *, start: tuple[float, float]) -> None:
        self.map = GameMap(make_game_info(*rows), start_location=Point(start))
        self.tracker = _Tracker(make_tables(), Enemy())
        self.tracker.update(make_observation(units=neutral_units.units).observation.raw_data, 0)

    @property
    def neutral_units(self) -> Units[Unit[Any]]:
        return self.tracker.unit_tracker.present.neutral

    def expansions(self) -> tuple[Expansion, ...]:
        return self.map._expansions_among(self.neutral_units)

    def townhall_refusals_around(self, resource: tuple[float, float]) -> list[str]:
        """The townhall centers within 7 tiles of `resource` along each axis, the top row first: `#` refused."""
        neutral_units = self.neutral_units
        townhall_allowed = _where_townhall_allowed(
            self.map.placement,
            neutral_units.of_type(UnitType.AnyMineralField),
            neutral_units.of_type(UnitType.AnyVespeneGeyser),
            neutral_units.excluding_type(UnitType.AnyMineralField, UnitType.AnyVespeneGeyser),
        )
        # The tile centers nearest the resource's own center, a half tile off it where it stands on a tile edge.
        x0, y0 = int(resource[0]) + 0.5, int(resource[1]) + 0.5
        return [
            "".join(
                "." if townhall_allowed[self.map.placement.index_of((x0 + dx, y0 + dy))] else "#" for dx in range(-7, 8)
            )
            for dy in range(7, -8, -1)
        ]


def _locations(expansions: Sequence[Expansion]) -> list[tuple[float, float]]:
    return [(expansion.location.x, expansion.location.y) for expansion in expansions]


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

    def test_another_neutral_unit_blocks_the_square_its_radius_spans(self) -> None:
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


class TestWhatIsAnExpansion:
    def test_a_base_is_one_expansion_where_its_townhall_stood_holding_its_resources(self) -> None:
        neutral_units = _NeutralUnits()
        neutral_units.base((24.5, 24.5))
        map_at_start = _MapAtStart(_map_rows(48, 48), neutral_units, start=(24.5, 24.5))
        (expansion,) = map_at_start.expansions()
        assert expansion.location == Point((24.5, 24.5))
        assert {unit.tag for unit in expansion.mineral_fields} == set(range(1, 9))
        assert {unit.tag for unit in expansion.geysers} == {9, 10}
        distances = [field.position.distance_to(expansion.location) for field in expansion.mineral_fields]
        assert distances == sorted(distances)

    @pytest.mark.parametrize("count", [4, 13])
    def test_too_few_resources_together_or_too_many_are_no_expansion(self, count: int) -> None:
        """Four are a blocker to mine out, thirteen a wall."""
        neutral_units = _NeutralUnits()
        for index in range(count):
            neutral_units.add(UnitTypeId.MINERAL_FIELD_RICH, (8.0 + 2 * index, 20.5))
        assert _MapAtStart(_map_rows(48, 48), neutral_units, start=(0.5, 0.5)).expansions() == ()

    def test_fields_mined_from_either_side_are_an_expansion_on_each_with_its_own_geyser(self) -> None:
        neutral_units = _NeutralUnits()
        neutral_units.base((24.5, 14.5), _GOLD_FIELDS, _GOLD_GEYSERS)
        map_at_start = _MapAtStart(_map_rows(48, 48), neutral_units, start=(24.5, 14.5))
        lower, upper = map_at_start.expansions()
        assert (lower.location, upper.location) == (Point((24.5, 14.5)), Point((24.5, 27.5)))
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


class TestTheOrder:
    # A wall at x = 25 and 26 up to y = 50, and a wall at x = 48 and 49 the whole way up, beyond which nothing walks.
    _ROWS = _map_rows(80, 60, lambda x, y: (x in (25, 26) and y <= 50) or x in (48, 49))

    def test_the_start_comes_first_then_the_nearest_by_walking(self) -> None:
        """The base behind the wall is nearer in a line, 28 to 35, and farther to walk."""
        neutral_units = _NeutralUnits()
        for at in ((10.5, 15.5), (38.5, 15.5), (10.5, 50.5)):
            neutral_units.base(at)
        map_at_start = _MapAtStart(self._ROWS, neutral_units, start=(10.5, 15.5))
        assert _locations(map_at_start.expansions()) == [(10.5, 15.5), (10.5, 50.5), (38.5, 15.5)]

    def test_a_base_no_walk_reaches_comes_last(self) -> None:
        neutral_units = _NeutralUnits()
        for at in ((64.5, 30.5), (10.5, 15.5), (38.5, 15.5)):
            neutral_units.base(at)
        map_at_start = _MapAtStart(self._ROWS, neutral_units, start=(10.5, 15.5))
        assert _locations(map_at_start.expansions()) == [(10.5, 15.5), (38.5, 15.5), (64.5, 30.5)]


def _recorded_map_and_neutral_units(path: Path) -> tuple[GameMap, Units[Unit[Any]]]:
    """The map of a recorded game, and the neutral units of its first observation."""
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
            tracker = _Tracker(GameData(data), Enemy())
            tracker.update(observation.observation.raw_data, observation.observation.game_loop)
            return GameMap._of_game(info, observation), tracker.unit_tracker.present.neutral
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


@pytest.mark.parametrize("path", CORPUS, ids=lambda path: path.stem)
class TestARecordedMap:
    def test_this_player_starts_at_the_first_expansion_holding_the_fields_it_sees(self, path: Path) -> None:
        game_map, neutral_units = _recorded_map_and_neutral_units(path)
        first = game_map._expansions_among(neutral_units)[0]
        assert first.location == game_map.start_location
        assert len(first.mineral_fields) == 8
        assert all(field.visibility is Visibility.IN_VISION for field in first.mineral_fields)

    def test_every_opponent_starts_at_an_expansion(self, path: Path) -> None:
        game_map, neutral_units = _recorded_map_and_neutral_units(path)
        locations = {expansion.location for expansion in game_map._expansions_among(neutral_units)}
        assert set(game_map.opponent_start_locations) <= locations

    def test_the_map_has_as_many_expansions_as_counted(self, path: Path) -> None:
        game_map, neutral_units = _recorded_map_and_neutral_units(path)
        assert len(game_map._expansions_among(neutral_units)) == _COUNTS[path.stem.split("-")[0]]
