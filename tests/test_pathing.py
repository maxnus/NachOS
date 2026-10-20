"""Pathing distances asked of the game: on a client whose answers are scripted, and in a real game."""

import math
from contextlib import closing
from typing import Any

import pytest
from s2clientprotocol import data_pb2, query_pb2

from sc2nachos.geometry import Point
from sc2nachos.ids import UnitTypeId
from sc2nachos.ids.raw import RawAbilityId
from sc2nachos.launch import GameProcess, MapFile, MapNotFoundError
from sc2nachos.match import Computer, Difficulty, Participant, Race
from sc2nachos.pathing import Pathing
from sc2nachos.protocol import Client, ProtocolError, WebSocketTransport
from sc2nachos.units import Unit
from sc2nachos.units._tracking import _Tracker
from support import (
    FakeTransport,
    RealGame,
    make_client,
    make_enemy,
    make_observation,
    make_response,
    make_tables,
    make_unit,
)

_TABLES = make_tables(data_pb2.UnitTypeData(unit_id=UnitTypeId.REAPER))


def _answering(*distances: float) -> tuple[Pathing, FakeTransport]:
    """Pathing over a client the game answers `distances`, and its transport."""
    answer = query_pb2.ResponseQuery(pathing=[query_pb2.ResponseQueryPathing(distance=d) for d in distances])
    client, transport = make_client(make_response(query=answer))
    return Pathing(client), transport


def _reaper(*, at: tuple[float, float] = (10.0, 10.0), left_out_since: bool = False) -> Unit[Any]:
    """This player's reaper under tag 7, which the last observation left out if `left_out_since`."""
    tracker = _Tracker(_TABLES, make_enemy())
    tracker.update(make_observation(0, units=[make_unit(7, UnitTypeId.REAPER, at=at)]).observation.raw_data, 0)
    (reaper,) = tracker.unit_tracker.present
    if left_out_since:
        tracker.update(make_observation(16).observation.raw_data, 16)
    return reaper


class TestDistances:
    def test_every_pair_is_asked_in_one_request_in_the_order_given(self) -> None:
        pathing, transport = _answering(3.0, 4.5)
        assert pathing.query_distances([((1, 2), (3, 4)), (Point((5, 6)), (7.25, 8))]) == [3.0, 4.5]
        (request,) = transport.requests
        asked = [(pair.start_pos.x, pair.start_pos.y, pair.end_pos.x, pair.end_pos.y) for pair in request.query.pathing]
        assert asked == [(1, 2, 3, 4), (5, 6, 7.25, 8)]

    def test_a_unit_is_asked_from_by_its_tag(self) -> None:
        pathing, transport = _answering(3.3)
        assert pathing.query_distance(_reaper(), (12, 10)) == pytest.approx(3.3)
        (asked,) = transport.requests[0].query.pathing
        assert (asked.WhichOneof("start"), asked.unit_tag) == ("unit_tag", 7)

    def test_a_unit_the_last_observation_left_out_raises_without_asking(self) -> None:
        pathing, transport = _answering()
        with pytest.raises(ValueError, match="did not report"):
            pathing.query_distance(_reaper(left_out_since=True), (12, 10))
        assert not transport.requests

    def test_no_path_reads_infinite(self) -> None:
        pathing, _ = _answering(0.0)
        assert pathing.query_distance((1, 1), (9, 9)) == math.inf

    def test_a_point_to_itself_reads_zero_and_one_a_little_off_it_reads_infinite(self) -> None:
        pathing, _ = _answering(0.0, 0.0)
        assert pathing.query_distances([((1.1, 2.2), (1.1, 2.2)), ((1.1, 2.2), (1.2, 2.2))]) == [0.0, math.inf]

    def test_a_unit_to_its_own_position_reads_zero(self) -> None:
        pathing, _ = _answering(0.0)
        assert pathing.query_distance(_reaper(at=(10.5, 10.5)), (10.5, 10.5)) == 0.0

    def test_no_pairs_ask_nothing(self) -> None:
        pathing, transport = _answering()
        assert pathing.query_distances([]) == []
        assert not transport.requests

    def test_an_answer_short_of_the_pairs_asked_raises(self) -> None:
        pathing, _ = _answering(1.0)
        with pytest.raises(ProtocolError, match="1 of the 2"):
            pathing.query_distances([((1, 1), (2, 2)), ((1, 1), (3, 3))])


@pytest.mark.integration
def test_in_a_real_game_a_ring_of_raised_depots_shuts_a_point_off_and_lowered_lets_the_path_in() -> None:
    """Run with `pytest -m integration`. Plays a few seconds of a game."""
    try:
        game_map = MapFile.find("PylonAIE_v4")
    except MapNotFoundError as missing:
        pytest.skip(str(missing))

    with (
        GameProcess.launch(window=(640, 480)) as process,
        closing(Client(WebSocketTransport.connect(process.url))) as client,
    ):
        client.create_game(game_map.path, [Participant(), Computer(Race.ZERG, Difficulty.VERY_EASY)])
        game = RealGame(client, client.join_game(Race.TERRAN))
        assert game.game is not None
        pathing = game.game.pathing
        home = game.turn(1).own.of_type(UnitTypeId.COMMAND_CENTER)[0].position
        hole = game.open_ground(home.towards(game.map.playable_area.center, 14), size=6)
        outside = Point((hole.x + 6.5, hole.y + 0.5))
        assert game.map.pathing[outside]
        before = pathing.query_distance(outside, hole)
        assert math.isfinite(before)

        sites = [Point((hole.x + dx, hole.y + dy)) for dx in (-2, 0, 2) for dy in (-2, 0, 2) if (dx, dy) != (0, 0)]
        game.debug(*(game.create(UnitTypeId.SUPPLY_DEPOT, site) for site in sites))
        depots = game.turn(8).own.of_type(UnitTypeId.SUPPLY_DEPOT)
        assert len(depots) == len(sites)
        assert pathing.query_distance(outside, hole) == math.inf

        # The path opens some 20 steps after the order, though the depots read lowered at once
        # (docs/reference/game-behavior/map.md).
        for depot in depots:
            game.order(RawAbilityId.Morph_SupplyDepot_Lower, depot)
        distance = pathing.query_distance(outside, hole)
        for _ in range(16):
            if math.isfinite(distance):
                break
            game.turn(4)
            distance = pathing.query_distance(outside, hole)
        assert distance == pytest.approx(before, abs=1)

        client.leave_game()
        client.quit()
