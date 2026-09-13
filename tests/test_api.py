"""The api and the two ways to run it, played out without a game wherever that is possible."""

from contextlib import closing
from pathlib import Path

import pytest
from s2clientprotocol import sc2api_pb2

from sc2nachos import Api, ApiBot, NotPlayingError, run_ladder, run_local
from sc2nachos.launch import GameProcess, Map, MapNotFoundError, free_port
from sc2nachos.match import Computer, Difficulty, Participant, Race, Result
from sc2nachos.protocol import (
    Client,
    ConnectionClosedError,
    ProtocolError,
    Recording,
    ReplayTransport,
    Status,
    WebSocketTransport,
)
from support import FakeTransport, make_game_info, make_observation, make_response

# A map from the current AIE ladder pool, which is what a test game should be played on.
_LADDER_MAP = "PylonAIE_v4"


def _game(*steps: int, ending: Result | None = Result.VICTORY, stepped: bool = True) -> list[sc2api_pb2.Response]:
    """The game's whole side of a conversation: the map, the tables, then a turn at each of `steps`.

    Without an `ending` the game never says it is over, which is what a time limit is for.
    """
    responses = [
        make_response(game_info=make_game_info()),
        make_response(data=sc2api_pb2.ResponseData()),
    ]
    for index, step in enumerate(steps):
        final = ending is not None and index == len(steps) - 1
        results = [(1, ending)] if final and ending is not None else []
        status = Status.ENDED if final else Status.IN_GAME
        responses.append(make_response(status, observation=make_observation(step, *results)))
        if not final and stepped:
            responses.append(make_response(step=sc2api_pb2.ResponseStep()))
    return responses


def _joined(*responses: sc2api_pb2.Response) -> tuple[Client, FakeTransport]:
    """A client that has already joined as player one, over a transport that will then answer `responses`."""
    transport = FakeTransport(make_response(join_game=sc2api_pb2.ResponseJoinGame(player_id=1)), *responses)
    client = Client(transport)
    client.join_game(Race.TERRAN)
    return client, transport


def _ladder_transport() -> FakeTransport:
    """A game that answers a join and then two turns, which is enough to run a ladder game to its end."""
    return FakeTransport(
        make_response(join_game=sc2api_pb2.ResponseJoinGame(player_id=1)),
        *_game(0, 2),
        make_response(),
    )


class _FakeGame:
    """A `GameProcess` that never started anything."""

    url = "ws://127.0.0.1:0/sc2api"

    def __init__(self) -> None:
        self.terminated = False

    def __enter__(self) -> "_FakeGame":
        return self

    def __exit__(self, *_: object) -> None:
        self.terminated = True


def _local_transport() -> FakeTransport:
    """A game that answers a creation, a join, two turns, and then being torn down."""
    return FakeTransport(
        make_response(create_game=sc2api_pb2.ResponseCreateGame()),
        make_response(join_game=sc2api_pb2.ResponseJoinGame(player_id=1)),
        *_game(0, 2),
        make_response(),
        make_response(),
    )


def _no_real_game(monkeypatch: pytest.MonkeyPatch, transport: FakeTransport) -> _FakeGame:
    """Stand in for both the client process and the socket, so a local game needs neither."""
    game = _FakeGame()
    monkeypatch.setattr(GameProcess, "launch", classmethod(lambda cls, *args, **kwargs: game))
    monkeypatch.setattr(WebSocketTransport, "connect", classmethod(lambda cls, url, **kwargs: transport))
    return game


def _somewhere() -> Map:
    """A map that needs no installation to find, because it was not looked up."""
    return Map(Path("Somewhere.SC2Map"))


class TestBeforeAGame:
    @pytest.mark.parametrize("name", ["client", "map", "data", "step", "time", "result", "units"])
    def test_what_belongs_to_a_game_says_there_is_none(self, name: str) -> None:
        """Zero is a step a game plays and `None` is a game still going, so neither can stand for no game at all."""
        with pytest.raises(NotPlayingError, match="no game has been joined"):
            getattr(Api(), name)

    def test_the_steps_per_turn_are_the_ones_asked_for(self) -> None:
        assert Api(steps_per_turn=8).steps_per_turn == 8


class TestPlaying:
    def test_a_game_is_played_to_the_result_the_game_gives(self) -> None:
        client, _ = _joined(*_game(0, 2, 4))
        api = Api(steps_per_turn=2)
        assert api.play(client) is Result.VICTORY
        assert api.result is Result.VICTORY
        assert api.client is client
        assert api.map.name == "Somewhere"
        assert api.data.units == {}

    def test_the_map_and_the_tables_are_asked_for_once(self) -> None:
        """The map never changes, the tables are wanted before any upgrade, and game_info alone is 77 KB an ask."""
        client, transport = _joined(*_game(0, 2, 4, 6, 8))
        Api(steps_per_turn=2).play(client)
        kinds = [request.WhichOneof("request") for request in transport.requests]
        assert kinds.count("game_info") == 1
        assert kinds.count("data") == 1

    def test_the_step_is_the_game_loop_last_observed(self) -> None:
        client, _ = _joined(*_game(0, 2, 4))
        api = Api(steps_per_turn=2)
        api.play(client)
        assert api.step == 4

    def test_a_game_over_when_first_observed_is_never_stepped(self) -> None:
        client, transport = _joined(*_game(0))
        api = Api()
        assert api.play(client) is Result.VICTORY
        assert api.step == 0
        assert not any(request.HasField("step") for request in transport.requests)

    def test_every_turn_steps_the_game_on_but_the_one_that_ends_it(self) -> None:
        client, transport = _joined(*_game(0, 8, 16))
        Api(steps_per_turn=8).play(client)
        assert [request.step.count for request in transport.requests if request.HasField("step")] == [8, 8]

    def test_the_time_played_is_the_step_in_seconds(self) -> None:
        client, _ = _joined(*_game(0, 224))
        api = Api()
        api.play(client)
        assert api.time == pytest.approx(10.0)

    def test_a_time_limit_ends_the_game_in_a_tie(self) -> None:
        client, transport = _joined(*_game(0, 112, ending=None))
        api = Api(steps_per_turn=112)
        assert api.play(client, time_limit=5) is Result.TIE
        assert api.step == 112
        assert [request.step.count for request in transport.requests if request.HasField("step")] == [112]

    def test_a_realtime_game_asks_for_the_step_it_wants_instead_of_stepping(self) -> None:
        """A realtime game runs whether or not anyone is watching, so there is nothing to step."""
        client, transport = _joined(*_game(0, 4, 8, ending=Result.DEFEAT, stepped=False))
        assert Api(steps_per_turn=4).play(client, realtime=True) is Result.DEFEAT
        asked = [request.observation.game_loop for request in transport.requests if request.HasField("observation")]
        assert asked == [0, 4, 8]
        assert not any(request.HasField("step") for request in transport.requests)

    def test_a_step_that_says_the_game_is_over_is_followed_by_asking_how(self) -> None:
        """Only an observation carries the results, so the status on a step is not the end of the game."""
        client, _ = _joined(
            make_response(game_info=make_game_info()),
            make_response(data=sc2api_pb2.ResponseData()),
            make_response(observation=make_observation(0)),
            make_response(Status.ENDED, step=sc2api_pb2.ResponseStep()),
            make_response(Status.ENDED, observation=make_observation(2, (1, Result.DEFEAT))),
        )
        assert Api(steps_per_turn=2).play(client) is Result.DEFEAT

    def test_a_game_that_ends_without_saying_how_is_undecided(self) -> None:
        client, _ = _joined(
            make_response(game_info=make_game_info()),
            make_response(data=sc2api_pb2.ResponseData()),
            make_response(Status.ENDED, observation=make_observation(0)),
            make_response(Status.ENDED, observation=make_observation(0)),
        )
        assert Api().play(client) is Result.UNDECIDED

    def test_one_api_plays_game_after_game_each_from_nothing(self) -> None:
        """An api kept at module scope is handed every game its process plays."""
        api = Api(steps_per_turn=4)
        api.play(_joined(*_game(0, 4, 8, ending=Result.DEFEAT))[0])
        client, transport = _joined(*_game(0, 4, stepped=False))
        assert api.play(client, realtime=True) is Result.VICTORY
        assert api.step == 4
        assert api.client is client
        asked = [request.observation.game_loop for request in transport.requests if request.HasField("observation")]
        assert asked == [0, 4]


class TestRunningLocally:
    def test_the_bot_is_a_bare_slot_and_the_opponent_carries_how_it_plays(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A participant says nothing about itself, because the join settles who fills the slot."""
        transport = _local_transport()
        _no_real_game(monkeypatch, transport)

        run_local(_somewhere(), ApiBot(Api(steps_per_turn=2), Race.TERRAN, "NachOS"), Computer(race=Race.ZERG))

        setups = transport.requests[0].create_game.player_setup
        assert [setup.type for setup in setups] == [sc2api_pb2.Participant, sc2api_pb2.Computer]
        assert not setups[0].HasField("race")
        assert setups[1].race == Race.ZERG.value

    def test_a_bot_without_an_opponent_plays_the_map_alone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        transport = _local_transport()
        _no_real_game(monkeypatch, transport)
        run_local(_somewhere(), ApiBot(Api(steps_per_turn=2), Race.TERRAN))
        setups = transport.requests[0].create_game.player_setup
        assert [setup.type for setup in setups] == [sc2api_pb2.Participant]

    def test_the_join_carries_the_race_and_the_name_the_bot_plays_under(self, monkeypatch: pytest.MonkeyPatch) -> None:
        transport = _local_transport()
        _no_real_game(monkeypatch, transport)
        run_local(_somewhere(), ApiBot(Api(steps_per_turn=2), Race.TERRAN, "NachOS"), Computer())
        joined = transport.requests[1].join_game
        assert joined.race == Race.TERRAN.value
        assert joined.player_name == "NachOS"

    def test_the_client_this_library_started_is_stopped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        transport = _local_transport()
        game = _no_real_game(monkeypatch, transport)
        result = run_local(_somewhere(), ApiBot(Api(steps_per_turn=2), Race.TERRAN), Computer())
        assert result is Result.VICTORY
        assert any(request.HasField("quit") for request in transport.requests)
        assert transport.closed
        assert game.terminated

    def test_a_game_that_could_not_be_created_raises_why_and_is_still_torn_down(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The game also refuses to leave a game it never started, which must not hide why it never started."""
        refusal = sc2api_pb2.ResponseCreateGame(error=sc2api_pb2.ResponseCreateGame.InvalidMapPath)
        transport = FakeTransport(
            make_response(Status.LAUNCHED, create_game=refusal),
            make_response(Status.LAUNCHED, error=["A game has not been started yet"]),
            make_response(Status.QUIT),
        )
        game = _no_real_game(monkeypatch, transport)
        with pytest.raises(ProtocolError, match="InvalidMapPath"):
            run_local(_somewhere(), ApiBot(Api(), Race.TERRAN), Computer())
        assert any(request.HasField("quit") for request in transport.requests)
        assert transport.closed
        assert game.terminated

    def test_the_recording_is_finished_even_when_leaving_fails(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        transport = FakeTransport(
            make_response(create_game=sc2api_pb2.ResponseCreateGame()),
            make_response(join_game=sc2api_pb2.ResponseJoinGame(player_id=1)),
            *_game(0, 2),
            make_response(error=["Something no game has said yet"]),
        )
        _no_real_game(monkeypatch, transport)
        path = tmp_path / "game.sc2rec"
        with pytest.raises(ProtocolError, match="Something no game has said yet"):
            run_local(_somewhere(), ApiBot(Api(steps_per_turn=2), Race.TERRAN), Computer(), record_to=path)
        assert transport.closed
        assert [exchange.request.WhichOneof("request") for exchange in Recording(path)][-2:] == [
            "observation",
            "leave_game",
        ]


class TestRunningOnALadder:
    def test_the_join_carries_the_ports_the_ladder_handed_out(self, monkeypatch: pytest.MonkeyPatch) -> None:
        transport = _ladder_transport()
        monkeypatch.setattr(WebSocketTransport, "connect", classmethod(lambda cls, url, **kwargs: transport))

        result = run_ladder(ApiBot(Api(steps_per_turn=2), Race.TERRAN), host="127.0.0.1", port=8000, start_port=1000)

        assert result is Result.VICTORY
        joined = transport.requests[0].join_game
        assert (joined.server_ports.game_port, joined.server_ports.base_port) == (1002, 1003)
        assert [(pair.game_port, pair.base_port) for pair in joined.client_ports] == [(1004, 1005)]

    def test_no_game_is_created_because_the_ladder_already_made_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        transport = _ladder_transport()
        monkeypatch.setattr(WebSocketTransport, "connect", classmethod(lambda cls, url, **kwargs: transport))
        run_ladder(ApiBot(Api(steps_per_turn=2), Race.TERRAN), host="127.0.0.1", port=8000)
        assert not any(request.HasField("create_game") for request in transport.requests)
        assert not transport.requests[0].join_game.HasField("server_ports")

    def test_the_client_the_ladder_started_is_left_running(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The ladder owns the process it started and decides when it stops."""
        transport = _ladder_transport()
        monkeypatch.setattr(WebSocketTransport, "connect", classmethod(lambda cls, url, **kwargs: transport))
        run_ladder(ApiBot(Api(steps_per_turn=2), Race.TERRAN), host="127.0.0.1", port=8000)
        assert not any(request.HasField("quit") for request in transport.requests)
        assert any(request.HasField("leave_game") for request in transport.requests)
        assert transport.closed

    def test_the_connection_is_closed_even_when_leaving_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        transport = FakeTransport(
            make_response(join_game=sc2api_pb2.ResponseJoinGame(player_id=1)),
            *_game(0, 2),
            make_response(error=["Something no game has said yet"]),
        )
        monkeypatch.setattr(WebSocketTransport, "connect", classmethod(lambda cls, url, **kwargs: transport))
        with pytest.raises(ProtocolError, match="Something no game has said yet"):
            run_ladder(ApiBot(Api(steps_per_turn=2), Race.TERRAN), host="127.0.0.1", port=8000)
        assert transport.closed


@pytest.mark.integration
class TestAgainstTheRealGame:
    """Run with `pytest -m integration`. Plays a whole game, so it is slow and needs the game installed."""

    def test_a_bare_api_plays_a_full_game_and_the_recording_replays_it(self, tmp_path: Path) -> None:
        try:
            Map.find(_LADDER_MAP)
        except MapNotFoundError as missing:
            pytest.skip(str(missing))

        path = tmp_path / "game.sc2rec"
        opponent = Computer(race=Race.ZERG, difficulty=Difficulty.VERY_HARD)
        api = Api(steps_per_turn=16)
        result = run_local(_LADDER_MAP, ApiBot(api, Race.TERRAN, "NachOS"), opponent, record_to=path, window=(640, 480))
        assert result in (Result.VICTORY, Result.DEFEAT)
        assert api.time > 60

        # A recording holds the whole run, setup included, so replaying it means replaying the setup too.
        replayed = Api(steps_per_turn=16)
        client = Client(ReplayTransport(Recording(path)))
        client.create_game(_LADDER_MAP, [Participant(), opponent])
        client.join_game(Race.TERRAN)
        assert replayed.play(client) is result
        assert replayed.step == api.step

    def test_a_bare_api_plays_a_full_game_joined_as_on_a_ladder(self) -> None:
        try:
            game_map = Map.find(_LADDER_MAP)
        except MapNotFoundError as missing:
            pytest.skip(str(missing))

        port = free_port()
        with GameProcess.launch(port=port, window=(640, 480)) as game:
            # The ladder creates the match, then lets go of the client, which serves one connection at a time.
            with closing(Client(WebSocketTransport.connect(game.url))) as ladder:
                ladder.create_game(game_map.path, [Participant(), Computer(Race.ZERG, Difficulty.VERY_HARD)])
                with pytest.raises(ConnectionClosedError, match="another connection holds it"):
                    WebSocketTransport.connect(game.url)

            api = Api(steps_per_turn=16)
            result = run_ladder(ApiBot(api, Race.TERRAN, "NachOS"), host="127.0.0.1", port=port)
            assert result in (Result.VICTORY, Result.DEFEAT)
            assert api.time > 60

            with closing(Client(WebSocketTransport.connect(game.url))) as ladder:
                ladder.ping()
                assert ladder.status is Status.LAUNCHED
