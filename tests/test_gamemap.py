"""The map as the game describes it, from maps drawn here and from the recorded games."""

from pathlib import Path

import numpy
import pytest
from s2clientprotocol import raw_pb2, sc2api_pb2

from sc2nachos.gamemap import GameMap
from sc2nachos.geometry import Point, Rectangle, Tile
from sc2nachos.ids import UnitTypeId
from sc2nachos.protocol import ProtocolError, Recording
from support import make_bytes, make_game_info

CORPUS = sorted((Path(__file__).parent / "corpus").glob("*.sc2rec"))
_TOWNHALLS = {UnitTypeId.COMMAND_CENTER, UnitTypeId.HATCHERY, UnitTypeId.NEXUS}

# Six tiles wide, climbing a level over the middle four. The corner bytes give tile heights 8.0, 8.125, 8.625,
# 9.375, 9.875 and 10.0, and no tile has two corners a cliff apart.
_SLOPE = make_bytes(*[[191, 191, 193, 199, 205, 207, 207]] * 8)


class TestReadingAMap:
    def test_the_grids_read_the_map_the_way_up_it_is_drawn(self) -> None:
        # Sixteen across, so each row spans two bytes and a wrong bit order would move the open tile.
        game_map = GameMap(
            make_game_info(
                "................",
                "..........#.....",
                "................",
            )
        )
        assert game_map.pathing[Tile(10, 1)]
        assert game_map.placement[Tile(10, 1)]
        assert game_map.pathing.sum() == game_map.placement.sum() == 1

    def test_the_grids_cover_the_playable_area_and_read_closed_past_it(self) -> None:
        game_map = GameMap(make_game_info(*["#" * 8] * 6, playable=(1, 2, 7, 5)))
        assert game_map.playable_area == Rectangle(1, 2, 6, 3)
        for grid in (game_map.pathing, game_map.placement, game_map.height):
            assert grid.bounds == game_map.playable_area
        assert game_map.pathing[Tile(1, 2)]
        # Open in the drawing, but outside the playable area.
        assert not game_map.pathing[Tile(0, 0)]
        assert not game_map.placement[Point((7.5, 4.5))]
        with pytest.raises(IndexError, match="lies outside"):
            _ = game_map.height[Tile(0, 0)]

    # 191, 207, 223 and 239 are the levels units stand on: 8, 10, 12 and 14.
    @pytest.mark.parametrize(
        ("byte", "height"), [(0, -15.875), (127, 0.0), (191, 8.0), (207, 10.0), (223, 12.0), (239, 14.0), (255, 16.0)]
    )
    def test_a_byte_of_height_is_an_eighth_with_127_at_zero(self, byte: int, height: float) -> None:
        game_map = GameMap(make_game_info("##", "##", heights=make_bytes([byte] * 2, [byte] * 2)))
        assert game_map.height[Tile(0, 0)] == game_map.height_at(Point((0.3, 0.8))) == height

    def test_a_tile_on_a_ramp_is_as_high_as_its_corners_on_average(self) -> None:
        # The bytes are corner heights, so four tiles have five corners, each a quarter higher.
        heights = make_bytes(*[[191, 193, 195, 197, 199]] * 3)
        game_map = GameMap(make_game_info(*["#####"] * 3, playable=(0, 0, 4, 2), heights=heights))
        assert [game_map.height[Tile(x, 0)] for x in range(4)] == [8.125, 8.375, 8.625, 8.875]
        assert game_map.height_at(Point((1.25, 0.5))) == 8.3125
        assert game_map.height_at(Point((1.0, 0.0))) == 8.25

    @pytest.mark.parametrize(
        ("rows", "height"),
        [
            # The top of a ramp beside the cliff it runs along: the corner on the cliff line is on the level below.
            ([[207, 207, 207], [207, 207, 207], [191, 207, 207]], 10.0),
            # The foot of a cliff: the corner on the cliff line is on the level above.
            ([[239, 239, 239], [239, 239, 239], [255, 239, 239]], 14.0),
            # Two corners on each level: the tile is on the upper one.
            ([[207, 207, 207], [207, 207, 207], [191, 191, 207]], 10.0),
        ],
    )
    def test_beside_a_cliff_a_tile_stands_on_the_side_most_of_its_corners_are_on(
        self, rows: list[list[int]], height: float
    ) -> None:
        game_map = GameMap(make_game_info(*["###"] * 3, playable=(0, 0, 2, 2), heights=make_bytes(*rows)))
        assert game_map.height[Tile(0, 0)] == height
        assert game_map.height_at(Point((0.1, 0.1))) == height

    def test_a_height_at_takes_a_point_and_not_a_tile(self) -> None:
        with pytest.raises(TypeError, match="pass its .center"):
            GameMap(make_game_info()).height_at(Tile(1, 1))

    def test_a_height_at_past_the_playable_area_raises(self) -> None:
        with pytest.raises(IndexError, match="lies outside"):
            GameMap(make_game_info(*["#" * 8] * 6, playable=(1, 2, 7, 5))).height_at(Point((0.5, 0.5)))

    def test_the_grids_refuse_writes_but_a_copy_does_not(self) -> None:
        game_map = GameMap(make_game_info())
        assert game_map.pathing.readonly
        with pytest.raises(TypeError, match="is read-only"):
            game_map.pathing[Tile(0, 0)] = False
        copy = game_map.pathing.copy()
        copy[Tile(0, 0)] = False
        assert game_map.pathing[Tile(0, 0)]

    def test_the_opponent_start_locations_are_the_ones_the_game_names(self) -> None:
        game_map = GameMap(make_game_info(start_locations=((5.5, 2.5),)))
        assert game_map.opponent_start_locations == (Point((5.5, 2.5)),)

    def test_a_slope_a_unit_walks_but_cannot_build_on_is_a_ramp(self) -> None:
        game_map = GameMap(make_game_info(*["#~~~~#"] * 3, heights=_SLOPE))
        (ramp,) = game_map.ramps
        assert set(ramp.tiles) == {Tile(x, y) for x in range(1, 5) for y in range(3)}
        assert set(ramp.top) == {Tile(4, y) for y in range(3)}
        assert set(ramp.bottom) == {Tile(1, y) for y in range(3)}
        assert ramp.top.center == Point((4.5, 1.5))

    def test_a_ramp_end_is_the_whole_row_where_the_row_is_not_quite_level(self) -> None:
        # The two corners on the left edge are a byte low, so the tile on them sits half a byte below the rest of its
        # row. Read exactly, the bottom of this ramp would be that one tile.
        heights = make_bytes(
            [207, 207, 207, 207, 207],
            [205, 205, 205, 205, 205],
            [199, 199, 199, 199, 199],
            [192, 193, 193, 193, 193],
            [190, 191, 191, 191, 191],
        )
        game_map = GameMap(make_game_info(*["~~~~"] * 4, heights=heights))
        (ramp,) = game_map.ramps
        assert game_map.height[Tile(0, 0)] == game_map.height[Tile(1, 0)] - 0.0625
        assert set(ramp.bottom) == {Tile(x, 0) for x in range(4)}
        assert set(ramp.top) == {Tile(x, 3) for x in range(4)}

    def test_a_ramp_that_runs_diagonally_is_one_ramp(self) -> None:
        # No two of the three tiles share an edge, so connecting only across edges would find no ramp.
        game_map = GameMap(make_game_info("#~####", "##~###", "###~##", heights=_SLOPE))
        (ramp,) = game_map.ramps
        assert set(ramp.tiles) == {Tile(1, 2), Tile(2, 1), Tile(3, 0)}

    def test_ground_that_climbs_no_level_is_no_ramp(self) -> None:
        # A bridge or a stand of trees, level throughout, and the first two tiles of the slope, 0.5 apart.
        assert not GameMap(make_game_info("####", "#~~#", "####")).ramps
        assert not GameMap(make_game_info(*["#~~###"] * 3, heights=_SLOPE)).ramps

    def test_ramps_are_ordered_by_their_lower_left_tile(self) -> None:
        game_map = GameMap(make_game_info(*["#~~~~#"] * 3, "......", *["#~~~~#"] * 3, heights=_SLOPE))
        assert [min(ramp.tiles) for ramp in game_map.ramps] == [Tile(1, 0), Tile(1, 4)]

    def test_an_image_too_short_for_its_size_is_refused(self) -> None:
        info = make_game_info()
        info.start_raw.pathing_grid.data = info.start_raw.pathing_grid.data[:-1]
        with pytest.raises(ProtocolError, match="cannot be 7 bytes"):
            GameMap(info)

    def test_a_playable_area_past_the_image_is_refused(self) -> None:
        with pytest.raises(ProtocolError, match="does not cover"):
            GameMap(make_game_info(playable=(0, 0, 9, 8)))


def _start(path: Path) -> tuple[sc2api_pb2.ResponseGameInfo, sc2api_pb2.Observation]:
    """The map of a recorded game and its first observation."""
    info = None
    for exchange in Recording(path):
        response = exchange.response
        if response.HasField("game_info"):
            info = response.game_info
        elif response.HasField("observation"):
            assert info is not None, f"{path.name} has an observation before the map"
            return info, response.observation.observation
    raise AssertionError(f"{path.name} holds no observation")


def _own_townhall(observation: sc2api_pb2.Observation) -> Point:
    """Where this player's townhall stood at the start."""
    return next(
        Point((unit.pos.x, unit.pos.y))
        for unit in observation.raw_data.units
        if unit.alliance == raw_pb2.Alliance.Self and unit.unit_type in _TOWNHALLS
    )


@pytest.mark.parametrize("path", CORPUS, ids=lambda path: path.stem)
class TestARecordedMap:
    def test_every_unit_on_flat_ground_stands_at_its_height(self, path: Path) -> None:
        info, observation = _start(path)
        game_map = GameMap(info)
        for unit in observation.raw_data.units:
            tile = Tile(int(unit.pos.x), int(unit.pos.y))
            around = {game_map.height[Tile(tile.x + dx, tile.y + dy)] for dx in (-1, 0, 1) for dy in (-1, 0, 1)}
            if not unit.is_flying and len(around) == 1:
                # Units stand up to a few hundredths below the ground, and the ground can sit a little above, by
                # 0.15 in Pylon's main base.
                position = Point((unit.pos.x, unit.pos.y))
                assert -0.03 < unit.pos.z - game_map.height_at(position) < 0.16
                assert game_map.height_at(position) == game_map.height[tile]

    def test_nothing_open_is_left_off_the_playable_area(self, path: Path) -> None:
        info, _ = _start(path)
        game_map = GameMap(info)
        for grid, image in (
            (game_map.pathing, info.start_raw.pathing_grid),
            (game_map.placement, info.start_raw.placement_grid),
        ):
            assert grid.sum() == int(numpy.unpackbits(numpy.frombuffer(image.data, dtype=numpy.uint8)).sum())

    def test_this_players_own_townhall_blocks_pathing_but_not_placement(self, path: Path) -> None:
        info, observation = _start(path)
        game_map = GameMap(info)
        townhall = _own_townhall(observation)
        assert not game_map.pathing[townhall]
        assert game_map.placement[townhall]

    def test_the_opponent_start_locations_leave_out_this_players_own(self, path: Path) -> None:
        info, observation = _start(path)
        game_map = GameMap(info)
        assert game_map.opponent_start_locations
        assert _own_townhall(observation) not in game_map.opponent_start_locations

    def test_a_ramp_is_walkable_ground_no_structure_can_stand_on(self, path: Path) -> None:
        info, _ = _start(path)
        game_map = GameMap(info)
        assert game_map.ramps
        for ramp in game_map.ramps:
            for tile in ramp.tiles:
                assert game_map.pathing[tile]
                assert not game_map.placement[tile]

    def test_a_ramp_climbs_one_level_and_no_more(self, path: Path) -> None:
        info, _ = _start(path)
        game_map = GameMap(info)
        for ramp in game_map.ramps:
            climb = game_map.height[min(ramp.top)] - game_map.height[min(ramp.bottom)]
            assert 1.0 <= climb < 2.0

    def test_a_ramp_has_two_ends_that_do_not_meet(self, path: Path) -> None:
        """Each end reaches a byte into a ramp that climbs at least half a level, so the ends cannot overlap."""
        info, _ = _start(path)
        game_map = GameMap(info)
        for ramp in game_map.ramps:
            assert ramp.top and ramp.bottom
            assert not set(ramp.top) & set(ramp.bottom)

    def test_a_ramp_leads_out_of_this_players_own_base(self, path: Path) -> None:
        info, observation = _start(path)
        game_map = GameMap(info)
        townhall = _own_townhall(observation)
        assert min(ramp.top.center.distance_to(townhall) for ramp in game_map.ramps) < 20
