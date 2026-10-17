"""This player's camera, and the units a human has selected: read from each observation, and moved with the turn's
orders."""

import math
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from s2clientprotocol import data_pb2, error_pb2, sc2api_pb2

from sc2nachos import Api
from sc2nachos.camera import Camera
from sc2nachos.enemy import Enemy
from sc2nachos.events import TurnEvent
from sc2nachos.gamemap import GameMap
from sc2nachos.geometry import Point
from sc2nachos.ids import AbilityId, UnitTypeId
from sc2nachos.launch import GameProcess, MapFile, MapNotFoundError
from sc2nachos.match import Computer, Difficulty, Participant, Race, Result
from sc2nachos.protocol import Client, Recording, Status, WebSocketTransport
from sc2nachos.units import Unit
from sc2nachos.units._tracking import _Tracker
from support import FakeTransport, RealGame, make_game_info, make_observation, make_response, make_tables, make_unit

CORPUS = sorted((Path(__file__).parent / "corpus").glob("*.sc2rec"))


def _moves(camera: Camera) -> list[tuple[float, float]]:
    """Where the turn's actions from `camera` center it."""
    return [
        (action.action_raw.camera_move.center_world_space.x, action.action_raw.camera_move.center_world_space.y)
        for action in camera._take_moves()
    ]


class TestMoving:
    def test_only_the_last_move_of_a_turn_goes_out_and_then_it_is_forgotten(self) -> None:
        camera = Camera(Point((1.0, 1.0)))
        camera.move_to((7.0, 8.0))
        camera.move_to(Point((5.0, 6.0)))
        assert _moves(camera) == [(5.0, 6.0)]
        assert _moves(camera) == []

    def test_a_move_is_kept_as_the_protocol_carries_it(self) -> None:
        camera = Camera(Point((1.0, 1.0)))
        camera.move_to((157.123288015625, 3.0))
        assert _moves(camera) == [(157.123291015625, 3.0)]

    def test_moving_leaves_the_center_as_observed(self) -> None:
        camera = Camera(Point((1.0, 1.0)))
        camera.move_to((7.0, 8.0))
        assert camera.center == Point((1.0, 1.0))


def test_the_center_is_read_from_each_observation_and_a_move_goes_out_after_the_turns_orders() -> None:
    marine = make_unit(1)
    transport = FakeTransport(
        make_response(join_game=sc2api_pb2.ResponseJoinGame(player_id=1)),
        make_response(game_info=make_game_info()),
        make_response(data=sc2api_pb2.ResponseData()),
        make_response(observation=make_observation(0, units=[marine], camera=(3.0, 4.0))),
        make_response(action=sc2api_pb2.ResponseAction(result=[error_pb2.Success, error_pb2.Success])),
        make_response(step=sc2api_pb2.ResponseStep()),
        make_response(observation=make_observation(2, units=[marine], camera=(5.0, 6.0))),
        make_response(step=sc2api_pb2.ResponseStep()),
        make_response(Status.ENDED, observation=make_observation(4, (1, Result.VICTORY), units=[marine])),
    )
    client = Client(transport)
    client.join_game(Race.TERRAN)
    api = Api()
    centers: list[Point] = []

    @api.events.on(TurnEvent)
    def look(event: TurnEvent) -> None:
        centers.append(api.camera.center)
        if event.step == 0:
            api.camera.move_to((5.0, 6.0))
            api.orders.issue(api.units.own[0], AbilityId.MOVE, target=(20.0, 21.0))

    api.play(client, steps_per_turn=2)

    assert centers == [Point((3.0, 4.0)), Point((5.0, 6.0))]
    (request,) = [request.action for request in transport.requests if request.HasField("action")]
    sent = [action.action_raw for action in request.actions]
    assert [(raw.HasField("unit_command"), raw.HasField("camera_move")) for raw in sent] == [
        (True, False),
        (False, True),
    ]


@pytest.mark.parametrize("path", CORPUS, ids=lambda path: path.stem)
def test_in_the_corpus_the_camera_starts_on_this_players_main(path: Path) -> None:
    recording = Recording(path)
    info = next(exchange.response.game_info for exchange in recording if exchange.response.HasField("game_info"))
    first = next(exchange.response.observation for exchange in recording if exchange.response.HasField("observation"))
    camera = Camera._of_player(first.observation.raw_data.player)
    assert math.dist(camera.center, GameMap._of_game(info, first).start_location) < 4


class TestSelected:
    def _one(self, **fields: Any) -> Unit[Any]:
        tracker = _Tracker(make_tables(data_pb2.UnitTypeData(unit_id=UnitTypeId.MARINE)), Enemy())
        tracker.update(make_observation(0, units=[make_unit(1, **fields)]).observation.raw_data, 0)
        return tracker.unit_tracker.present[0]

    def test_a_unit_reads_selected_as_the_game_reports_it(self) -> None:
        assert self._one(is_selected=True).is_selected
        assert not self._one().is_selected


@pytest.mark.integration
def test_in_a_real_game_the_camera_is_centered_where_it_was_moved() -> None:
    """Run with `pytest -m integration`. Plays a few steps of a game."""
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
        middle = game.map.playable_area.center
        game.game.camera.move_to(middle)
        game.game.send_turn()
        # A move can show an observation late, so it is waited for.
        for _ in range(8):
            game.turn(2)
            if math.dist(game.game.camera.center, middle) < 0.01:
                break
        assert math.dist(game.game.camera.center, middle) < 0.01
        assert not any(unit.is_selected for unit in game.tracker.unit_tracker.present)

        client.leave_game()
        client.quit()
