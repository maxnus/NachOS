"""The api and the two runners, tested without a game wherever possible."""

from contextlib import closing
from pathlib import Path

import pytest
from s2clientprotocol import sc2api_pb2

from sc2nachos import Api, ApiBot, NotPlayingError, run_ladder, run_local
from sc2nachos.events import (
    Event,
    EventBus,
    GameEndEvent,
    GameStartEvent,
    OwnUnitCreatedEvent,
    TurnEvent,
    TurnStartEvent,
    UnitDiedEvent,
)
from sc2nachos.launch import GameProcess, MapFile, MapNotFoundError, free_port
from sc2nachos.match import Computer, Difficulty, Participant, Race, Result
from sc2nachos.protocol import (
    Client,
    ConnectionClosedError,
    PlaybackTransport,
    ProtocolError,
    Recording,
    Status,
    WebSocketTransport,
)
from support import FakeTransport, make_game_info, make_observation, make_response, make_unit

# A map from the current AIE ladder pool, for the test games.
_LADDER_MAP = "PylonAIE_v4"


def _game(*steps: int, ending: Result | None = Result.VICTORY, stepped: bool = True) -> list[sc2api_pb2.Response]:
    """The game's side of a whole conversation: the map, the tables, then a turn at each of `steps`.

    With `ending` as `None` the game never reports a result, which lets a time limit end it.
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


def _game_of(*observations: sc2api_pb2.ResponseObservation) -> list[sc2api_pb2.Response]:
    """The game's side of a whole conversation: a turn at each of `observations`, the last ending the game."""
    responses = [make_response(game_info=make_game_info()), make_response(data=sc2api_pb2.ResponseData())]
    for index, observation in enumerate(observations):
        final = index == len(observations) - 1
        responses.append(make_response(Status.ENDED if final else Status.IN_GAME, observation=observation))
        if not final:
            responses.append(make_response(step=sc2api_pb2.ResponseStep()))
    return responses


def _joined(*responses: sc2api_pb2.Response) -> tuple[Client, FakeTransport]:
    """A client already joined as player one, over a transport that then returns `responses`."""
    transport = FakeTransport(make_response(join_game=sc2api_pb2.ResponseJoinGame(player_id=1)), *responses)
    client = Client(transport)
    client.join_game(Race.TERRAN)
    return client, transport


def _ladder_transport() -> FakeTransport:
    """A transport for a join and two turns, enough to run a ladder game to its end."""
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
    """A transport for a creation, a join, two turns, and the teardown."""
    return FakeTransport(
        make_response(create_game=sc2api_pb2.ResponseCreateGame()),
        make_response(join_game=sc2api_pb2.ResponseJoinGame(player_id=1)),
        *_game(0, 2),
        make_response(),
        make_response(),
    )


def _no_real_game(monkeypatch: pytest.MonkeyPatch, transport: FakeTransport) -> _FakeGame:
    """Replace the game process and the socket with fakes, so a local game needs neither."""
    game = _FakeGame()
    monkeypatch.setattr(GameProcess, "launch", classmethod(lambda cls, *args, **kwargs: game))
    monkeypatch.setattr(WebSocketTransport, "connect", classmethod(lambda cls, url, **kwargs: transport))
    return game


def _somewhere() -> MapFile:
    """A map file that is never looked up, so no installation is needed."""
    return MapFile(Path("Somewhere.SC2Map"))


class TestBeforeAGame:
    @pytest.mark.parametrize("name", ["client", "map", "data", "step", "time", "result", "units"])
    def test_what_belongs_to_a_game_says_there_is_none(self, name: str) -> None:
        """Zero is a valid step and `None` means a game still going, so neither can mean no game."""
        with pytest.raises(NotPlayingError, match="no game has been joined"):
            getattr(Api(), name)

    def test_the_events_answer_so_handlers_can_subscribe_at_import(self) -> None:
        assert isinstance(Api().events, EventBus)


class TestPlaying:
    def test_a_game_is_played_to_the_result_the_game_gives(self) -> None:
        client, _ = _joined(*_game(0, 2, 4))
        api = Api()
        assert api.play(client, steps_per_turn=2) is Result.VICTORY
        assert api.result is Result.VICTORY
        assert api.client is client
        assert api.map.name == "Somewhere"
        assert api.data.units == {}

    def test_the_map_and_the_tables_are_asked_for_once(self) -> None:
        """The map never changes, the tables are wanted before any upgrade, and game_info alone is 77 KB a request."""
        client, transport = _joined(*_game(0, 2, 4, 6, 8))
        Api().play(client, steps_per_turn=2)
        kinds = [request.WhichOneof("request") for request in transport.requests]
        assert kinds.count("game_info") == 1
        assert kinds.count("data") == 1

    def test_the_step_is_the_game_loop_last_observed(self) -> None:
        client, _ = _joined(*_game(0, 2, 4))
        api = Api()
        api.play(client, steps_per_turn=2)
        assert api.step == 4

    def test_a_game_over_when_first_observed_is_never_stepped(self) -> None:
        client, transport = _joined(*_game(0))
        api = Api()
        assert api.play(client) is Result.VICTORY
        assert api.step == 0
        assert not any(request.HasField("step") for request in transport.requests)

    def test_every_turn_steps_the_game_on_but_the_one_that_ends_it(self) -> None:
        client, transport = _joined(*_game(0, 8, 16))
        Api().play(client, steps_per_turn=8)
        assert [request.step.count for request in transport.requests if request.HasField("step")] == [8, 8]

    def test_the_time_played_is_the_step_in_seconds(self) -> None:
        client, _ = _joined(*_game(0, 224))
        api = Api()
        api.play(client)
        assert api.time == pytest.approx(10.0)

    def test_a_time_limit_ends_the_game_in_a_tie(self) -> None:
        client, transport = _joined(*_game(0, 112, ending=None))
        api = Api()
        assert api.play(client, steps_per_turn=112, time_limit=5) is Result.TIE
        assert api.step == 112
        assert [request.step.count for request in transport.requests if request.HasField("step")] == [112]

    def test_a_realtime_game_asks_for_the_step_it_wants_instead_of_stepping(self) -> None:
        """A realtime game runs on its own, so there is nothing to step."""
        client, transport = _joined(*_game(0, 4, 8, ending=Result.DEFEAT, stepped=False))
        assert Api().play(client, steps_per_turn=4, realtime=True) is Result.DEFEAT
        asked = [request.observation.game_loop for request in transport.requests if request.HasField("observation")]
        assert asked == [0, 4, 8]
        assert not any(request.HasField("step") for request in transport.requests)

    def test_a_step_that_says_the_game_is_over_is_followed_by_asking_how(self) -> None:
        """Only an observation carries the result, so the status on a step response does not end the game."""
        client, _ = _joined(
            make_response(game_info=make_game_info()),
            make_response(data=sc2api_pb2.ResponseData()),
            make_response(observation=make_observation(0)),
            make_response(Status.ENDED, step=sc2api_pb2.ResponseStep()),
            make_response(Status.ENDED, observation=make_observation(2, (1, Result.DEFEAT))),
        )
        assert Api().play(client, steps_per_turn=2) is Result.DEFEAT

    def test_a_game_that_ends_without_saying_how_is_undecided(self) -> None:
        client, _ = _joined(
            make_response(game_info=make_game_info()),
            make_response(data=sc2api_pb2.ResponseData()),
            make_response(Status.ENDED, observation=make_observation(0)),
            make_response(Status.ENDED, observation=make_observation(0)),
        )
        assert Api().play(client) is Result.UNDECIDED

    def test_one_api_plays_game_after_game_each_from_nothing(self) -> None:
        """An api at module scope plays every game of its process."""
        api = Api()
        api.play(_joined(*_game(0, 4, 8, ending=Result.DEFEAT))[0], steps_per_turn=4)
        client, transport = _joined(*_game(0, 4, stepped=False))
        assert api.play(client, steps_per_turn=4, realtime=True) is Result.VICTORY
        assert api.step == 4
        assert api.client is client
        asked = [request.observation.game_loop for request in transport.requests if request.HasField("observation")]
        assert asked == [0, 4]


def _record(api: Api, seen: list[tuple[str, int]]) -> None:
    """Record every lifecycle event `api` hands out into `seen`, as the type name and the step."""
    for kind in (GameStartEvent, TurnStartEvent, TurnEvent, GameEndEvent):

        @api.events.on(kind)
        def note(event: Event) -> None:
            seen.append((type(event).__name__, event.step))


class TestEvents:
    def test_a_game_starts_takes_its_turns_and_ends_and_its_last_observation_gets_no_turn(self) -> None:
        client, _ = _joined(*_game(0, 2, 4, ending=Result.DEFEAT))
        api, seen, results = Api(), [], []
        _record(api, seen)
        api.events.on(GameEndEvent)(lambda event: results.append(event.result))
        api.play(client, steps_per_turn=2)
        assert seen == [
            ("GameStartEvent", 0),
            ("TurnStartEvent", 0),
            ("TurnEvent", 0),
            ("TurnStartEvent", 2),
            ("TurnEvent", 2),
            ("GameEndEvent", 4),
        ]
        assert results == [Result.DEFEAT]

    def test_every_handler_reads_the_game_as_of_its_events_step(self) -> None:
        client, _ = _joined(*_game(0, 2, 4))
        api, steps = Api(), []
        for kind in (GameStartEvent, TurnStartEvent, TurnEvent, GameEndEvent):
            api.events.on(kind)(lambda event: steps.append((event.step, api.step)))
        api.play(client, steps_per_turn=2)
        assert len(steps) == 6
        assert all(event_step == api_step for event_step, api_step in steps)

    def test_a_game_called_at_its_time_limit_ends_in_a_tie_all_the_same(self) -> None:
        client, _ = _joined(*_game(0, 112, ending=None))
        api, results = Api(), []
        api.events.on(GameEndEvent)(lambda event: results.append((event.step, event.result)))
        api.play(client, steps_per_turn=112, time_limit=5)
        assert results == [(112, Result.TIE)]

    def test_what_subscribes_before_or_during_a_game_handles_the_next_one_too(self) -> None:
        """A handler subscribed at import works in every game its process plays, and a `once` handler fires once per
        game."""
        api = Api()
        fired: list[tuple[str, int]] = []

        @api.events.on(TurnEvent, at_step=2)
        def function(event: TurnEvent) -> None:
            fired.append(("function", event.step))

        class Handlers:
            def __init__(self, name: str) -> None:
                self.name = name
                api.events.subscribe(self)

            @api.events.on(TurnEvent, once=True)
            def on_turn(self, event: TurnEvent) -> None:
                fired.append((self.name, event.step))

        Handlers("before")

        @api.events.on(GameStartEvent)
        def make(event: GameStartEvent) -> None:
            # Created in the first game only, subscribed from then on.
            Handlers("during")
            api.events.unsubscribe(make)

        api.play(_joined(*_game(0, 2, 4))[0], steps_per_turn=2)
        first = list(fired)
        fired.clear()
        api.play(_joined(*_game(0, 2, 4))[0], steps_per_turn=2)
        assert first == fired == [("before", 0), ("during", 0), ("function", 2)]

    def test_what_happened_comes_within_each_turn_and_the_last_observations_never_does(self) -> None:
        api = Api()
        seen: list[tuple[str, int]] = []
        _record(api, seen)
        for kind in (OwnUnitCreatedEvent, UnitDiedEvent):
            api.events.on(kind)(lambda event: seen.append((type(event).__name__, event.step)))

        def game() -> list[sc2api_pb2.Response]:
            marine, scv = make_unit(1, build_progress=1.0), make_unit(2, build_progress=1.0)
            return _game_of(
                make_observation(0, units=[marine]),
                make_observation(2, units=[marine, scv]),
                make_observation(4, (1, Result.VICTORY), units=[scv], dead=(1,)),
            )

        api.play(_joined(*game())[0], steps_per_turn=2)
        first = list(seen)
        seen.clear()
        api.play(_joined(*game())[0], steps_per_turn=2)
        assert (
            first
            == seen
            == [
                ("GameStartEvent", 0),
                ("TurnStartEvent", 0),
                ("OwnUnitCreatedEvent", 0),
                ("TurnEvent", 0),
                ("TurnStartEvent", 2),
                ("OwnUnitCreatedEvent", 2),
                ("TurnEvent", 2),
                ("GameEndEvent", 4),
            ]
        )

    def test_a_handler_that_raises_ends_the_game_by_raising(self) -> None:
        client, _ = _joined(*_game(0, 2, 4))
        api = Api()

        @api.events.on(TurnEvent, at_step=2)
        def failing(event: TurnEvent) -> None:
            raise RuntimeError("the handler failed")

        with pytest.raises(RuntimeError, match="the handler failed"):
            api.play(client, steps_per_turn=2)
        assert api.step == 2
        assert api.result is None


class TestRunningLocally:
    def test_the_bot_is_a_bare_slot_and_the_opponent_carries_how_it_plays(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A participant slot carries no race: the join settles who fills it."""
        transport = _local_transport()
        _no_real_game(monkeypatch, transport)

        run_local(_somewhere(), ApiBot(Api(), Race.TERRAN, "NachOS"), Computer(race=Race.ZERG), steps_per_turn=2)

        setups = transport.requests[0].create_game.player_setup
        assert [setup.type for setup in setups] == [sc2api_pb2.Participant, sc2api_pb2.Computer]
        assert not setups[0].HasField("race")
        assert setups[1].race == Race.ZERG.value

    def test_a_bot_without_an_opponent_plays_the_map_alone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        transport = _local_transport()
        _no_real_game(monkeypatch, transport)
        run_local(_somewhere(), ApiBot(Api(), Race.TERRAN), steps_per_turn=2)
        setups = transport.requests[0].create_game.player_setup
        assert [setup.type for setup in setups] == [sc2api_pb2.Participant]

    def test_the_join_carries_the_race_and_the_name_the_bot_plays_under(self, monkeypatch: pytest.MonkeyPatch) -> None:
        transport = _local_transport()
        _no_real_game(monkeypatch, transport)
        run_local(_somewhere(), ApiBot(Api(), Race.TERRAN, "NachOS"), Computer(), steps_per_turn=2)
        joined = transport.requests[1].join_game
        assert joined.race == Race.TERRAN.value
        assert joined.player_name == "NachOS"

    def test_the_client_this_library_started_is_stopped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        transport = _local_transport()
        game = _no_real_game(monkeypatch, transport)
        result = run_local(_somewhere(), ApiBot(Api(), Race.TERRAN), Computer(), steps_per_turn=2)
        assert result is Result.VICTORY
        assert any(request.HasField("quit") for request in transport.requests)
        assert transport.closed
        assert game.terminated

    def test_a_game_that_could_not_be_created_raises_why_and_is_still_torn_down(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The game refuses to leave a game it never started; that refusal must not hide why creation failed."""
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
            run_local(_somewhere(), ApiBot(Api(), Race.TERRAN), Computer(), steps_per_turn=2, record_to=path)
        assert transport.closed
        assert [exchange.request.WhichOneof("request") for exchange in Recording(path)][-2:] == [
            "observation",
            "leave_game",
        ]


class TestRunningOnALadder:
    def test_the_join_carries_the_ports_the_ladder_handed_out(self, monkeypatch: pytest.MonkeyPatch) -> None:
        transport = _ladder_transport()
        monkeypatch.setattr(WebSocketTransport, "connect", classmethod(lambda cls, url, **kwargs: transport))

        result = run_ladder(ApiBot(Api(), Race.TERRAN), host="127.0.0.1", port=8000, start_port=1000, steps_per_turn=2)

        assert result is Result.VICTORY
        joined = transport.requests[0].join_game
        assert (joined.server_ports.game_port, joined.server_ports.base_port) == (1002, 1003)
        assert [(pair.game_port, pair.base_port) for pair in joined.client_ports] == [(1004, 1005)]

    def test_no_game_is_created_because_the_ladder_already_made_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        transport = _ladder_transport()
        monkeypatch.setattr(WebSocketTransport, "connect", classmethod(lambda cls, url, **kwargs: transport))
        run_ladder(ApiBot(Api(), Race.TERRAN), host="127.0.0.1", port=8000, steps_per_turn=2)
        assert not any(request.HasField("create_game") for request in transport.requests)
        assert not transport.requests[0].join_game.HasField("server_ports")

    def test_the_client_the_ladder_started_is_left_running(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The ladder owns the process it started and decides when it stops."""
        transport = _ladder_transport()
        monkeypatch.setattr(WebSocketTransport, "connect", classmethod(lambda cls, url, **kwargs: transport))
        run_ladder(ApiBot(Api(), Race.TERRAN), host="127.0.0.1", port=8000, steps_per_turn=2)
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
            run_ladder(ApiBot(Api(), Race.TERRAN), host="127.0.0.1", port=8000, steps_per_turn=2)
        assert transport.closed


@pytest.mark.integration
class TestAgainstTheRealGame:
    """Run with `pytest -m integration`. Plays whole games, so it is slow and needs the game installed."""

    def test_a_bare_api_plays_a_full_game_and_the_recording_replays_it(self, tmp_path: Path) -> None:
        try:
            MapFile.find(_LADDER_MAP)
        except MapNotFoundError as missing:
            pytest.skip(str(missing))

        path = tmp_path / "game.sc2rec"
        opponent = Computer(race=Race.ZERG, difficulty=Difficulty.VERY_HARD)
        api = Api()
        result = run_local(
            _LADDER_MAP,
            ApiBot(api, Race.TERRAN, "NachOS"),
            opponent,
            steps_per_turn=16,
            record_to=path,
            window=(640, 480),
        )
        assert result in (Result.VICTORY, Result.DEFEAT)
        assert api.time > 60

        # A recording holds the setup too, so replaying it replays the setup.
        replayed = Api()
        client = Client(PlaybackTransport(Recording(path)))
        client.create_game(_LADDER_MAP, [Participant(), opponent])
        client.join_game(Race.TERRAN)
        assert replayed.play(client, steps_per_turn=16) is result
        assert replayed.step == api.step

    def test_a_bare_api_plays_a_full_game_joined_as_on_a_ladder(self) -> None:
        try:
            game_map = MapFile.find(_LADDER_MAP)
        except MapNotFoundError as missing:
            pytest.skip(str(missing))

        port = free_port()
        with GameProcess.launch(port=port, window=(640, 480)) as game:
            # The ladder creates the match and disconnects; the client serves one connection at a time.
            with closing(Client(WebSocketTransport.connect(game.url))) as ladder:
                ladder.create_game(game_map.path, [Participant(), Computer(Race.ZERG, Difficulty.VERY_HARD)])
                with pytest.raises(ConnectionClosedError, match="another connection holds it"):
                    WebSocketTransport.connect(game.url)

            api = Api()
            result = run_ladder(ApiBot(api, Race.TERRAN, "NachOS"), host="127.0.0.1", port=port, steps_per_turn=16)
            assert result in (Result.VICTORY, Result.DEFEAT)
            assert api.time > 60

            with closing(Client(WebSocketTransport.connect(game.url))) as ladder:
                ladder.ping()
                assert ladder.status is Status.LAUNCHED
