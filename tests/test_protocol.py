"""The protocol layer, driven entirely through the transport seam."""

import lzma
import socket
import time
from pathlib import Path
from typing import cast

import pytest
from loguru import logger
from s2clientprotocol import common_pb2, debug_pb2, error_pb2, query_pb2, raw_pb2, sc2api_pb2
from websocket import WebSocket, WebSocketConnectionClosedException, WebSocketException, WebSocketTimeoutException

from sc2nachos.match import AIBuild, Computer, Difficulty, Participant, Race, Result
from sc2nachos.protocol import (
    Client,
    ConnectionClosedError,
    ConnectionTimeoutError,
    Exchange,
    GameEndedError,
    GameNotStartedError,
    GamePorts,
    PermissivePlaybackTransport,
    PlaybackTransport,
    PortPair,
    ProtocolError,
    Recording,
    RecordingTransport,
    Status,
    WebSocketTransport,
)
from support import (
    FakeTransport,
    FakeWebSocket,
    make_client,
    make_observation,
    make_response,
    write_recording,
)


class TestStatus:
    def test_the_client_reports_what_the_game_last_said(self) -> None:
        client, _ = make_client(make_response(Status.INIT_GAME, ping=sc2api_pb2.ResponsePing()))
        assert client.status is None
        client.ping()
        assert client.status is Status.INIT_GAME

    def test_a_response_without_a_status_leaves_the_last_one_standing(self) -> None:
        """An unset status field reads as `launched`, so reading it blindly would move the status backwards."""
        client, _ = make_client(
            make_response(Status.IN_GAME, ping=sc2api_pb2.ResponsePing()),
            make_response(None, ping=sc2api_pb2.ResponsePing()),
        )
        client.ping()
        client.ping()
        assert client.status is Status.IN_GAME

    def test_in_game_covers_playing_and_watching(self) -> None:
        for status, expected in [
            (Status.IN_GAME, True),
            (Status.IN_REPLAY, True),
            (Status.INIT_GAME, False),
            (Status.ENDED, False),
        ]:
            client, _ = make_client(make_response(status, ping=sc2api_pb2.ResponsePing()))
            client.ping()
            assert client.in_game is expected, status


class TestErrors:
    def test_a_refused_request_raises(self) -> None:
        client, _ = make_client(make_response(error=["Doh!"]))
        with pytest.raises(ProtocolError, match="Doh!"):
            client.ping()

    def test_what_the_game_says_beside_the_answer_is_a_warning_and_the_answer_comes_back(self) -> None:
        """The proto: "If command is missing, this will contain the error. Otherwise this will contain any
        warnings." """
        client, _ = make_client(make_response(error=["Careful"], ping=sc2api_pb2.ResponsePing(base_build=75689)))
        messages: list[str] = []
        sink = logger.add(messages.append, format="{message}", level="WARNING")
        try:
            assert client.ping().base_build == 75689
        finally:
            logger.remove(sink)
        assert messages == ["The game answered ping with warnings: Careful\n"]

    def test_a_request_with_no_answer_to_read_raises_on_anything_the_game_says(self) -> None:
        client, _ = make_client(make_response(error=["Doh!"]))
        with pytest.raises(ProtocolError, match="Doh!"):
            client.debug([])

    def test_a_finished_game_raises_its_own_error(self) -> None:
        client, _ = make_client(make_response(Status.ENDED, error=["Game has already ended"]))
        with pytest.raises(GameEndedError):
            client.ping()

    def test_a_game_that_has_not_started_raises_its_own_error(self) -> None:
        client, _ = make_client(make_response(Status.LAUNCHED, error=["A game has not been started yet"]))
        with pytest.raises(GameNotStartedError):
            client.observation()

    def test_every_failure_is_a_protocol_error(self) -> None:
        for error in (GameEndedError, GameNotStartedError, ConnectionClosedError, ConnectionTimeoutError):
            assert issubclass(error, ProtocolError)

    def test_an_answer_to_a_different_question_names_both(self) -> None:
        client, _ = make_client(make_response(ping=sc2api_pb2.ResponsePing()))
        with pytest.raises(ProtocolError, match="asked for game_info and answered ping"):
            client.game_info()

    def test_a_response_carrying_no_answer_at_all_says_so(self) -> None:
        client, _ = make_client(make_response())
        with pytest.raises(ProtocolError, match="asked for ping and answered nothing"):
            client.ping()


class TestJoining:
    def test_the_join_asks_for_the_raw_interface_only(self) -> None:
        client, transport = make_client(make_response(join_game=sc2api_pb2.ResponseJoinGame(player_id=2)))
        assert client.join_game(Race.TERRAN) == 2
        assert client.player_id == 2
        options = transport.requests[0].join_game.options
        assert options.raw and options.score
        assert not options.HasField("render") and not options.HasField("feature_layer")

    def test_a_name_and_ports_reach_the_request(self) -> None:
        client, transport = make_client(make_response(join_game=sc2api_pb2.ResponseJoinGame(player_id=1)))
        client.join_game(Race.ZERG, name="AvocaDOS", ports=GamePorts.from_start_port(1000))
        request = transport.requests[0].join_game
        assert request.race == Race.ZERG.value
        assert request.player_name == "AvocaDOS"
        assert (request.server_ports.game_port, request.server_ports.base_port) == (1002, 1003)
        assert [(port.game_port, port.base_port) for port in request.client_ports] == [(1004, 1005)]

    def test_a_refused_join_raises_even_without_a_top_level_error(self) -> None:
        refusal = sc2api_pb2.ResponseJoinGame(
            error=sc2api_pb2.ResponseJoinGame.MissingParticipation, error_details="no slot"
        )
        client, _ = make_client(make_response(join_game=refusal))
        with pytest.raises(ProtocolError, match="MissingParticipation.*no slot"):
            client.join_game(Race.PROTOSS)


class TestReplays:
    def test_a_replay_is_played_back_from_its_bytes_as_the_player_asked(self) -> None:
        client, transport = make_client(make_response(Status.IN_REPLAY, start_replay=sc2api_pb2.ResponseStartReplay()))
        client.start_replay(b"MPQ\x1b replay", player_id=2)
        request = transport.requests[0].start_replay
        assert (request.replay_data, request.observed_player_id) == (b"MPQ\x1b replay", 2)
        assert not request.disable_fog and not request.realtime
        assert request.options.raw and not request.options.HasField("render")
        assert client.player_id == 2
        assert client.in_replay and client.in_game

    def test_a_replay_played_back_with_the_fog_off_says_so_until_the_next_game(self) -> None:
        client, transport = make_client(
            make_response(Status.IN_REPLAY, start_replay=sc2api_pb2.ResponseStartReplay()),
            make_response(Status.IN_GAME, join_game=sc2api_pb2.ResponseJoinGame(player_id=1)),
        )
        assert not client.fog_disabled
        client.start_replay(b"replay", player_id=1, disable_fog=True)
        assert transport.requests[0].start_replay.disable_fog
        assert client.fog_disabled
        client.join_game(Race.TERRAN)
        assert not client.fog_disabled

    def test_a_game_is_not_a_replay(self) -> None:
        client, _ = make_client(make_response(Status.IN_GAME, join_game=sc2api_pb2.ResponseJoinGame(player_id=1)))
        client.join_game(Race.TERRAN)
        assert client.in_game and not client.in_replay

    def test_a_refused_replay_raises_with_the_games_reason_and_keeps_no_player(self) -> None:
        refusal = sc2api_pb2.ResponseStartReplay(
            error=sc2api_pb2.ResponseStartReplay.InvalidObservedPlayerId, error_details="no player 3"
        )
        client, _ = make_client(make_response(Status.LAUNCHED, start_replay=refusal))
        with pytest.raises(ProtocolError, match="InvalidObservedPlayerId: no player 3"):
            client.start_replay(b"replay", player_id=3)
        assert client.player_id is None

    def test_the_info_of_a_replay_is_what_the_game_read_from_its_bytes(self) -> None:
        info = sc2api_pb2.ResponseReplayInfo(map_name="Pylon AIE", base_build=75689, game_duration_loops=6720)
        client, transport = make_client(make_response(Status.LAUNCHED, replay_info=info))
        assert client.replay_info(b"replay") == info
        assert transport.requests[0].replay_info.replay_data == b"replay"

    def test_a_replay_the_game_cannot_read_raises(self) -> None:
        unread = sc2api_pb2.ResponseReplayInfo(error=sc2api_pb2.ResponseReplayInfo.ParsingError)
        client, _ = make_client(make_response(Status.LAUNCHED, replay_info=unread))
        with pytest.raises(ProtocolError, match="could not read the replay, as ParsingError: no detail given"):
            client.replay_info(b"not a replay")


class TestGameAfterGame:
    """One connection can play game after game, so nothing from one game may carry into the next."""

    def _won(self, *after: sc2api_pb2.Response) -> Client:
        """A client whose game ended in a victory, over a transport that then returns `after`."""
        client, _ = make_client(
            make_response(join_game=sc2api_pb2.ResponseJoinGame(player_id=1)),
            make_response(Status.ENDED, observation=make_observation(100, (1, Result.VICTORY))),
            *after,
        )
        client.join_game(Race.TERRAN)
        client.observation()
        assert client.result is Result.VICTORY
        return client

    def test_leaving_forgets_the_game(self) -> None:
        client = self._won(make_response(Status.LAUNCHED))
        client.leave_game()
        assert (client.player_id, client.result, dict(client.results)) == (None, None, {})

    def test_creating_the_next_game_forgets_the_last(self) -> None:
        """A game that has ended need not be left before the next is created."""
        client = self._won(make_response(Status.INIT_GAME, create_game=sc2api_pb2.ResponseCreateGame()))
        client.create_game("Next.SC2Map", [Participant(), Participant()])
        assert (client.player_id, client.result) == (None, None)

    def test_a_refused_join_leaves_nothing_of_the_last_game(self) -> None:
        refused = sc2api_pb2.ResponseJoinGame(error=sc2api_pb2.ResponseJoinGame.MissingParticipation)
        client = self._won(make_response(Status.ENDED, join_game=refused))
        with pytest.raises(ProtocolError, match="MissingParticipation"):
            client.join_game(Race.TERRAN)
        assert (client.player_id, client.result) == (None, None)


class TestObservation:
    def test_a_running_game_answers_in_one_request(self) -> None:
        client, transport = make_client(
            make_response(ping=sc2api_pb2.ResponsePing()),
            make_response(observation=sc2api_pb2.ResponseObservation()),
        )
        client.ping()
        client.observation()
        assert len(transport.requests) == 2
        assert client.results == {}

    def test_a_requested_game_loop_reaches_the_request(self) -> None:
        client, transport = make_client(make_response(observation=sc2api_pb2.ResponseObservation()))
        client.observation(game_loop=448)
        assert transport.requests[0].observation.game_loop == 448

    def test_the_results_land_when_the_game_ends(self) -> None:
        ended = make_observation(0, (1, Result.VICTORY), (2, Result.DEFEAT))
        client, _ = make_client(make_response(Status.ENDED, observation=ended))
        client.observation()
        assert client.results == {1: Result.VICTORY, 2: Result.DEFEAT}

    def test_a_game_that_ends_a_step_early_costs_one_more_request(self) -> None:
        """The game reports itself ended a step before it says who won."""
        client, transport = make_client(
            make_response(Status.ENDED, observation=sc2api_pb2.ResponseObservation()),
            make_response(Status.ENDED, observation=make_observation(0, (1, Result.DEFEAT))),
        )
        client.observation()
        assert len(transport.requests) == 2
        assert client.results == {1: Result.DEFEAT}

    def test_the_results_are_kept_through_observations_that_report_none(self) -> None:
        """The proto fills `player_result` only in the step the game ended, so the next observation has none."""
        client, transport = make_client(
            make_response(Status.ENDED, observation=make_observation(0, (1, Result.VICTORY))),
            make_response(Status.ENDED, observation=sc2api_pb2.ResponseObservation()),
        )
        client.observation()
        client.observation()
        assert len(transport.requests) == 2
        assert client.results == {1: Result.VICTORY}

    def test_a_result_the_game_left_unset_is_not_read_as_a_victory(self) -> None:
        ended = sc2api_pb2.ResponseObservation(
            player_result=[
                sc2api_pb2.PlayerResult(player_id=1),
                sc2api_pb2.PlayerResult(player_id=2, result=sc2api_pb2.Defeat),
            ]
        )
        client, _ = make_client(make_response(Status.ENDED, observation=ended))
        client.observation()
        assert client.results == {2: Result.DEFEAT}

    def test_joining_clears_the_results_of_the_last_game(self) -> None:
        client, _ = make_client(
            make_response(Status.ENDED, observation=make_observation(0, (1, Result.VICTORY))),
            make_response(Status.IN_GAME, join_game=sc2api_pb2.ResponseJoinGame(player_id=1)),
        )
        client.observation()
        client.join_game(Race.TERRAN)
        assert client.results == {}


class TestRequests:
    def test_step_asks_for_a_count_of_frames(self) -> None:
        client, transport = make_client(make_response(step=sc2api_pb2.ResponseStep(simulation_loop=112)))
        assert client.step(4).simulation_loop == 112
        assert transport.requests[0].step.count == 4

    def test_act_returns_a_verdict_per_action(self) -> None:
        actions = [sc2api_pb2.Action(), sc2api_pb2.Action()]
        client, transport = make_client(make_response(action=sc2api_pb2.ResponseAction(result=[error_pb2.Success] * 2)))
        assert len(client.act(actions).result) == 2
        assert len(transport.requests[0].action.actions) == 2

    def test_debug_sends_every_command_and_expects_no_answer(self) -> None:
        commands = [debug_pb2.DebugCommand(game_state=debug_pb2.show_map), debug_pb2.DebugCommand()]
        client, transport = make_client(make_response())
        client.debug(commands)
        assert list(transport.requests[0].debug.debug) == commands

    def test_a_query_returns_the_games_answers(self) -> None:
        answer = query_pb2.ResponseQuery(pathing=[query_pb2.ResponseQueryPathing(distance=2.5)])
        client, transport = make_client(make_response(query=answer))
        request = query_pb2.RequestQuery(pathing=[query_pb2.RequestQueryPathing(unit_tag=7)])
        assert client.query(request) == answer
        assert transport.requests[0].query == request

    def test_game_data_asks_for_every_table_by_default(self) -> None:
        client, transport = make_client(make_response(data=sc2api_pb2.ResponseData()))
        client.game_data()
        request = transport.requests[0].data
        assert (request.ability_id, request.unit_type_id, request.upgrade_id, request.buff_id, request.effect_id) == (
            True,
            True,
            True,
            True,
            True,
        )

    def test_a_replay_comes_back_as_bytes(self) -> None:
        client, _ = make_client(make_response(save_replay=sc2api_pb2.ResponseSaveReplay(data=b"replay")))
        assert client.save_replay() == b"replay"

    def test_quit_forgives_a_connection_that_has_already_gone(self) -> None:
        class ClosedTransport:
            def request(self, request: sc2api_pb2.Request) -> sc2api_pb2.Response:
                raise ConnectionClosedError("gone")

            def close(self) -> None: ...

        Client(ClosedTransport()).quit()

    @pytest.mark.parametrize(
        "answer",
        [
            make_response(Status.LAUNCHED, error=["A game has not been started yet"]),
            make_response(Status.ENDED, error=["Game has already ended"]),
        ],
        ids=["never started", "already over"],
    )
    def test_leaving_a_game_that_is_not_being_played_does_nothing(self, answer: sc2api_pb2.Response) -> None:
        client, transport = make_client(answer)
        client.leave_game()
        assert transport.requests[0].HasField("leave_game")

    def test_closing_the_client_releases_the_transport(self) -> None:
        client, transport = make_client()
        client.close()
        assert transport.closed


class TestWebSocketTransport:
    def _transport(self, websocket: FakeWebSocket) -> WebSocketTransport:
        return WebSocketTransport(cast(WebSocket, websocket))

    def test_a_request_goes_out_serialized_and_the_answer_comes_back_parsed(self) -> None:
        answer = make_response(ping=sc2api_pb2.ResponsePing(base_build=93333))
        websocket = FakeWebSocket(answer.SerializeToString())
        response = self._transport(websocket).request(sc2api_pb2.Request(ping=sc2api_pb2.RequestPing()))
        assert response.ping.base_build == 93333
        assert sc2api_pb2.Request.FromString(websocket.sent[0]).HasField("ping")

    def test_a_closed_socket_fails_the_send(self) -> None:
        websocket = FakeWebSocket()
        websocket.close()
        with pytest.raises(ConnectionClosedError):
            self._transport(websocket).request(sc2api_pb2.Request(ping=sc2api_pb2.RequestPing()))

    def test_a_socket_that_closes_mid_request_fails_the_receive(self) -> None:
        websocket = FakeWebSocket()
        with pytest.raises(ConnectionClosedError):
            self._transport(websocket).request(sc2api_pb2.Request(ping=sc2api_pb2.RequestPing()))

    def test_a_game_that_never_answers_raises_a_timeout(self) -> None:
        websocket = FakeWebSocket(failure=WebSocketTimeoutException("timed out"))
        with pytest.raises(ConnectionTimeoutError):
            self._transport(websocket).request(sc2api_pb2.Request(ping=sc2api_pb2.RequestPing()))

    def test_a_game_that_died_is_a_closed_connection(self) -> None:
        websocket = FakeWebSocket(failure=ConnectionResetError(10054, "An existing connection was forcibly closed"))
        with pytest.raises(ConnectionClosedError, match="forcibly closed"):
            self._transport(websocket).request(sc2api_pb2.Request(ping=sc2api_pb2.RequestPing()))

    def test_a_text_frame_is_not_a_response(self) -> None:
        """The protocol is binary throughout, so a text frame is the game or a proxy reporting a failure."""
        websocket = FakeWebSocket("Bad Request")
        with pytest.raises(ProtocolError, match="text where the protocol is binary"):
            self._transport(websocket).request(sc2api_pb2.Request(ping=sc2api_pb2.RequestPing()))

    def test_a_close_frame_is_a_closed_connection(self) -> None:
        """The websocket library reads a close frame as an empty text message."""
        websocket = FakeWebSocket("")
        with pytest.raises(ConnectionClosedError, match="closed the connection"):
            self._transport(websocket).request(sc2api_pb2.Request(ping=sc2api_pb2.RequestPing()))

    def test_bytes_that_do_not_parse_are_not_a_response(self) -> None:
        websocket = FakeWebSocket(b"\xff\xff\xff")
        with pytest.raises(ProtocolError, match="does not parse"):
            self._transport(websocket).request(sc2api_pb2.Request(ping=sc2api_pb2.RequestPing()))

    @pytest.mark.parametrize(
        "websocket",
        [
            FakeWebSocket(failure=WebSocketTimeoutException("timed out")),
            FakeWebSocket(failure=ConnectionResetError(104, "Connection reset by peer")),
            FakeWebSocket("Bad Request"),
            FakeWebSocket(b"\xff\xff\xff"),
        ],
        ids=["timeout", "reset", "text", "garbage"],
    )
    def test_a_request_that_fails_once_sent_closes_the_connection(self, websocket: FakeWebSocket) -> None:
        """An answer still to come would otherwise be read as the next request's."""
        transport = self._transport(websocket)
        with pytest.raises(ProtocolError):
            transport.request(sc2api_pb2.Request(ping=sc2api_pb2.RequestPing()))
        assert websocket.closed
        with pytest.raises(ConnectionClosedError):
            transport.request(sc2api_pb2.Request(game_info=sc2api_pb2.RequestGameInfo()))

    def test_a_real_websocket_that_times_out_is_closed_without_waiting_for_the_game(self) -> None:
        """Over a socket pair whose far end, the game, never answers."""
        ours, game = socket.socketpair()
        ours.settimeout(0.05)
        websocket = WebSocket()
        websocket.sock, websocket.connected = ours, True
        transport = WebSocketTransport(websocket)
        start = time.monotonic()
        with pytest.raises(ConnectionTimeoutError, match="so the connection is closed"):
            transport.request(sc2api_pb2.Request(ping=sc2api_pb2.RequestPing()))
        assert time.monotonic() - start < 1
        assert not websocket.connected
        with pytest.raises(ConnectionClosedError):
            transport.request(sc2api_pb2.Request(ping=sc2api_pb2.RequestPing()))
        game.close()

    def test_closing_closes_the_socket(self) -> None:
        websocket = FakeWebSocket()
        self._transport(websocket).close()
        assert websocket.closed

    @staticmethod
    def _refuse_to_open(monkeypatch: pytest.MonkeyPatch, failure: Exception) -> None:
        def create_connection(url: str, **kwargs: object) -> WebSocket:
            raise failure

        monkeypatch.setattr("sc2nachos.protocol._websocket.create_connection", create_connection)

    def test_a_connection_dropped_as_it_opens_is_one_another_connection_may_hold(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._refuse_to_open(monkeypatch, WebSocketConnectionClosedException("Connection to remote host was lost."))
        with pytest.raises(ConnectionClosedError, match="another connection holds it"):
            WebSocketTransport.connect("ws://127.0.0.1:5000/sc2api")

    def test_a_game_that_never_opens_the_connection_raises_a_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._refuse_to_open(monkeypatch, WebSocketTimeoutException("timed out"))
        with pytest.raises(ConnectionTimeoutError):
            WebSocketTransport.connect("ws://127.0.0.1:5000/sc2api")

    @pytest.mark.parametrize(
        ("failure", "raised"),
        [
            (ConnectionRefusedError(111, "Connection refused"), ConnectionClosedError),
            (TimeoutError("timed out"), ConnectionTimeoutError),
            (OSError(113, "No route to host"), ProtocolError),
        ],
    )
    def test_a_socket_that_fails_to_open_raises_the_library_error_for_it(
        self, monkeypatch: pytest.MonkeyPatch, failure: OSError, raised: type[ProtocolError]
    ) -> None:
        """The websocket library passes these through as they are."""
        self._refuse_to_open(monkeypatch, failure)
        with pytest.raises(raised) as caught:
            WebSocketTransport.connect("ws://127.0.0.1:5000/sc2api")
        assert caught.value.__cause__ is failure

    def test_any_other_failure_to_open_is_a_protocol_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._refuse_to_open(monkeypatch, WebSocketException("Invalid WebSocket Header"))
        with pytest.raises(ProtocolError, match="Invalid WebSocket Header"):
            WebSocketTransport.connect("ws://127.0.0.1:5000/sc2api")


class TestGamePorts:
    def test_the_ladder_layout_skips_the_first_port(self) -> None:
        assert GamePorts.from_start_port(5000) == GamePorts(
            server=PortPair(5002, 5003), players=(PortPair(5004, 5005),)
        )


class TestCreatingAGame:
    def test_the_map_and_the_players_reach_the_request(self) -> None:
        client, transport = make_client(make_response(create_game=sc2api_pb2.ResponseCreateGame()))
        client.create_game(Path("/sc2/Maps/PylonAIE.SC2Map"), [Participant(), Computer()])
        request = transport.requests[0].create_game
        assert request.local_map.map_path.endswith("PylonAIE.SC2Map")
        assert [setup.type for setup in request.player_setup] == [sc2api_pb2.Participant, sc2api_pb2.Computer]

    def test_a_participant_says_nothing_about_itself(self) -> None:
        """The race and the name are settled at the join; the game ignores them here."""
        client, transport = make_client(make_response(create_game=sc2api_pb2.ResponseCreateGame()))
        client.create_game("map", [Participant()])
        setup = transport.requests[0].create_game.player_setup[0]
        assert not setup.HasField("race") and not setup.HasField("player_name")

    def test_a_computer_carries_how_it_plays(self) -> None:
        client, transport = make_client(make_response(create_game=sc2api_pb2.ResponseCreateGame()))
        opponent = Computer(race=Race.ZERG, difficulty=Difficulty.HARD, build=AIBuild.RUSH, name="Roachy")
        client.create_game("map", [Participant(), opponent])
        setup = transport.requests[0].create_game.player_setup[1]
        assert (setup.race, setup.difficulty, setup.ai_build) == (
            Race.ZERG.value,
            Difficulty.HARD.value,
            AIBuild.RUSH.value,
        )
        assert setup.player_name == "Roachy"

    def test_an_unnamed_computer_keeps_the_name_the_game_gives_it(self) -> None:
        client, transport = make_client(make_response(create_game=sc2api_pb2.ResponseCreateGame()))
        client.create_game("map", [Computer()])
        assert not transport.requests[0].create_game.player_setup[0].HasField("player_name")

    def test_the_game_settings_reach_the_request(self) -> None:
        client, transport = make_client(make_response(create_game=sc2api_pb2.ResponseCreateGame()))
        client.create_game("map", [Participant()], realtime=True, disable_fog=True, random_seed=7)
        request = transport.requests[0].create_game
        assert request.realtime and request.disable_fog
        assert request.random_seed == 7

    def test_no_seed_asked_for_leaves_the_game_to_pick_one(self) -> None:
        """Zero is a valid seed, so an unset field is the only way to say `any`."""
        client, transport = make_client(make_response(create_game=sc2api_pb2.ResponseCreateGame()))
        client.create_game("map", [Participant()])
        assert not transport.requests[0].create_game.HasField("random_seed")

    def test_a_refused_creation_raises_even_without_a_top_level_error(self) -> None:
        refusal = sc2api_pb2.ResponseCreateGame(
            error=sc2api_pb2.ResponseCreateGame.InvalidMapPath, error_details="no such map"
        )
        client, _ = make_client(make_response(create_game=refusal))
        with pytest.raises(ProtocolError, match="InvalidMapPath.*no such map"):
            client.create_game("map", [Participant()])

    def test_a_creation_the_game_did_not_answer_raises(self) -> None:
        """An unset create_game field would otherwise read as a success."""
        client, _ = make_client(make_response(ping=sc2api_pb2.ResponsePing()))
        with pytest.raises(ProtocolError, match="asked for create_game and answered ping"):
            client.create_game("map", [Participant()])


class _BrokenTransport:
    """A `Transport` that never gets an answer."""

    def request(self, request: sc2api_pb2.Request) -> sc2api_pb2.Response:
        raise ConnectionClosedError("the connection to the game closed mid-request")

    def close(self) -> None:
        pass


def _recorder(path: Path, *responses: sc2api_pb2.Response) -> RecordingTransport:
    """A recorder writing to `path`, over a transport that returns `responses`."""
    return RecordingTransport(FakeTransport(*responses), path)


_VERSION = make_response(ping=sc2api_pb2.ResponsePing(game_version="5.0.14." + "0" * 100))


class TestRecording:
    def test_a_whole_exchange_survives_the_round_trip(self, tmp_path: Path) -> None:
        recorder = _recorder(tmp_path / "game.sc2rec", _VERSION)
        Client(recorder).ping()
        recorder.close()

        (exchange,) = list(recorder.recording)
        assert exchange.request.HasField("ping")
        assert exchange.response.ping.game_version == _VERSION.ping.game_version

    def test_a_recording_can_be_read_again(self, tmp_path: Path) -> None:
        """Reading re-opens the file, so a corpus serves any number of tests."""
        recorder = _recorder(tmp_path / "game.sc2rec", _VERSION)
        Client(recorder).ping()
        recorder.close()
        assert list(recorder.recording) == list(recorder.recording)

    def test_a_recording_is_far_smaller_than_the_game_it_holds(self, tmp_path: Path) -> None:
        """Committing a corpus is affordable only because consecutive observations barely differ."""
        path = tmp_path / "game.sc2rec"
        answers = _crowded_observations(200)
        recorder = _recorder(path, *answers)
        client = Client(recorder)
        for _ in answers:
            client.observation()
        recorder.close()

        raw = sum(len(answer.SerializeToString()) for answer in answers)
        assert raw > 1_000_000
        assert path.stat().st_size < raw // 20

    def test_a_request_the_game_never_answered_is_not_recorded(self, tmp_path: Path) -> None:
        recorder = RecordingTransport(_BrokenTransport(), tmp_path / "game.sc2rec")
        with pytest.raises(ConnectionClosedError):
            recorder.request(sc2api_pb2.Request(ping=sc2api_pb2.RequestPing()))
        recorder.close()
        assert list(recorder.recording) == []

    def test_closing_closes_the_transport_underneath_and_may_be_repeated(self, tmp_path: Path) -> None:
        game = FakeTransport()
        recorder = RecordingTransport(game, tmp_path / "game.sc2rec")
        recorder.close()
        recorder.close()
        assert game.closed

    def test_a_file_that_is_not_a_recording_says_so(self, tmp_path: Path) -> None:
        path = tmp_path / "junk.sc2rec"
        path.write_bytes(lzma.compress(b"whatever this file is, it is not one of ours"))
        with pytest.raises(ProtocolError, match="not a recording"):
            list(Recording(path))

    def test_a_file_that_is_not_even_compressed_says_so(self, tmp_path: Path) -> None:
        path = tmp_path / "junk.sc2rec"
        path.write_bytes(b"whatever this file is, it is not one of ours")
        with pytest.raises(ProtocolError, match="not a recording"):
            list(Recording(path))

    def test_a_recording_holding_a_message_that_does_not_parse_says_so(self, tmp_path: Path) -> None:
        path = tmp_path / "game.sc2rec"
        recorder = _recorder(path, _VERSION)
        Client(recorder).ping()
        recorder.close()
        answer = len(_VERSION.SerializeToString())
        whole = lzma.decompress(path.read_bytes())
        path.write_bytes(lzma.compress(whole[:-answer] + b"\xff" * answer))
        with pytest.raises(ProtocolError, match="Response that does not parse"):
            list(Recording(path))

    def test_a_recording_cut_short_says_so(self, tmp_path: Path) -> None:
        path = _truncated(tmp_path, cut=5)
        with pytest.raises(ProtocolError, match="short of a Response"):
            list(Recording(path))

    def test_a_recording_ending_on_an_unanswered_request_says_so(self, tmp_path: Path) -> None:
        path = _truncated(tmp_path, cut=len(_VERSION.SerializeToString()) + 4)
        with pytest.raises(ProtocolError, match="never answered"):
            list(Recording(path))

    def test_a_recording_whose_run_died_gives_what_it_holds_then_says_so(self, tmp_path: Path) -> None:
        """A run that dies never closes the compressed stream, so it leaves a prefix of the file it would write."""
        path = tmp_path / "game.sc2rec"
        recorder = _recorder(path, *_crowded_observations(200))
        client = Client(recorder)
        for _ in range(200):
            client.observation()
        recorder.close()
        whole = path.read_bytes()
        path.write_bytes(whole[: len(whole) // 2])

        read: list[Exchange] = []
        with pytest.raises(ProtocolError, match="dies before closing"):
            read.extend(Recording(path))
        assert 0 < len(read) < 200


def _crowded_observations(count: int) -> list[sc2api_pb2.Response]:
    """Responses to `count` observation requests, each of the same 500 marines, a game loop apart."""
    crowd = [raw_pb2.Unit(tag=index, unit_type=48, health=45.0) for index in range(500)]
    return [
        make_response(
            observation=sc2api_pb2.ResponseObservation(
                observation=sc2api_pb2.Observation(game_loop=loop, raw_data=raw_pb2.ObservationRaw(units=crowd))
            )
        )
        for loop in range(count)
    ]


def _truncated(tmp_path: Path, *, cut: int) -> Path:
    """A recording of one exchange missing its last `cut` bytes, in a properly closed stream."""
    path = tmp_path / "game.sc2rec"
    recorder = _recorder(path, _VERSION)
    Client(recorder).ping()
    recorder.close()
    path.write_bytes(lzma.compress(lzma.decompress(path.read_bytes())[:-cut]))
    return path


class TestPlayback:
    def _recorded(self, tmp_path: Path) -> Recording:
        """A recording of a game created, joined and observed once."""
        recorder = _recorder(
            tmp_path / "game.sc2rec",
            make_response(create_game=sc2api_pb2.ResponseCreateGame()),
            make_response(join_game=sc2api_pb2.ResponseJoinGame(player_id=2)),
            make_response(observation=sc2api_pb2.ResponseObservation(observation=sc2api_pb2.Observation(game_loop=48))),
        )
        client = Client(recorder)
        client.create_game("map", [Participant(), Computer()])
        client.join_game(Race.TERRAN)
        client.observation()
        client.close()
        return recorder.recording

    def test_a_recorded_conversation_drives_a_client_with_no_game(self, tmp_path: Path) -> None:
        client = Client(PlaybackTransport(self._recorded(tmp_path)))
        client.create_game("map", [Participant(), Computer()])
        assert client.join_game(Race.TERRAN) == 2
        assert client.observation().observation.game_loop == 48

    def test_the_details_of_a_request_need_not_match(self, tmp_path: Path) -> None:
        """Only the kind of request is matched, so a caller may ask for a loop the recording did not."""
        client = Client(PlaybackTransport(self._recorded(tmp_path)))
        client.create_game("other map", [Computer()])
        assert client.join_game(Race.ZERG, name="someone else") == 2

    def test_a_question_the_recording_was_not_asked_raises(self, tmp_path: Path) -> None:
        client = Client(PlaybackTransport(self._recorded(tmp_path)))
        with pytest.raises(ProtocolError, match="answers create_game next, and was asked for ping"):
            client.ping()

    def test_a_recording_that_has_run_out_says_so(self, tmp_path: Path) -> None:
        client = Client(PlaybackTransport(self._recorded(tmp_path)))
        client.create_game("map", [Participant(), Computer()])
        client.join_game(Race.TERRAN)
        client.observation()
        with pytest.raises(ProtocolError, match="no answer left, and was asked for ping"):
            client.ping()

    def test_a_request_after_closing_raises(self, tmp_path: Path) -> None:
        transport = PlaybackTransport(self._recorded(tmp_path))
        transport.close()
        with pytest.raises(ConnectionClosedError):
            transport.request(sc2api_pb2.Request(ping=sc2api_pb2.RequestPing()))

    def test_a_bots_own_request_the_recording_was_not_asked_raises(self, tmp_path: Path) -> None:
        client = Client(PlaybackTransport(_recording_of(tmp_path / "game.sc2rec", (_OBSERVE, _observed(16)))))
        with pytest.raises(ProtocolError, match="answers observation next, and was asked for action"):
            client.act([sc2api_pb2.Action()])


def _recording_of(path: Path, *exchanges: tuple[sc2api_pb2.Request, sc2api_pb2.Response]) -> Recording:
    """A recording at `path` of `exchanges`, each a request and its answer."""
    return write_recording(path, (Exchange(request, response) for request, response in exchanges))


_OBSERVE = sc2api_pb2.Request(observation=sc2api_pb2.RequestObservation())
_STEP = sc2api_pb2.Request(step=sc2api_pb2.RequestStep(count=16))
_GAME_INFO = sc2api_pb2.Request(game_info=sc2api_pb2.RequestGameInfo())
_STEPPED = make_response(step=sc2api_pb2.ResponseStep())


def _observed(game_loop: int, *units: raw_pb2.Unit) -> sc2api_pb2.Response:
    """The answer to an observation at `game_loop` of `units`."""
    return make_response(observation=make_observation(game_loop, units=units))


def _pathing_query(*pairs: tuple[tuple[float, float] | int, tuple[float, float]]) -> query_pb2.RequestQuery:
    """A query for the pathing distance of each pair, from a point or a unit's tag to a point."""
    query = query_pb2.RequestQuery()
    for start, (end_x, end_y) in pairs:
        pathing = query.pathing.add(end_pos=common_pb2.Point2D(x=end_x, y=end_y))
        if isinstance(start, int):
            pathing.unit_tag = start
        else:
            pathing.start_pos.x, pathing.start_pos.y = start
    return query


_JOIN = sc2api_pb2.Request(join_game=sc2api_pb2.RequestJoinGame())
_JOINED = make_response(Status.IN_GAME, join_game=sc2api_pb2.ResponseJoinGame(player_id=1))


def _create(*, realtime: bool) -> tuple[sc2api_pb2.Request, sc2api_pb2.Response]:
    """Creating a game, played in real time if `realtime`, and the game's answer."""
    request = sc2api_pb2.Request(create_game=sc2api_pb2.RequestCreateGame(realtime=realtime))
    return request, make_response(Status.INIT_GAME, create_game=sc2api_pb2.ResponseCreateGame())


class TestRecordedSetup:
    @pytest.mark.parametrize("realtime", [False, True], ids=["stepped", "real time"])
    def test_a_game_created_was_played_in_real_time_as_its_create_asked(self, tmp_path: Path, realtime: bool) -> None:
        recording = _recording_of(
            tmp_path / "game.sc2rec", _create(realtime=realtime), (_JOIN, _JOINED), (_OBSERVE, _observed(16))
        )
        assert recording.created_its_game
        assert recording.played_in_real_time is realtime

    def test_a_game_only_joined_that_steps_was_stepped(self, tmp_path: Path) -> None:
        recording = _recording_of(
            tmp_path / "game.sc2rec",
            (_JOIN, _JOINED),
            (_OBSERVE, _observed(16)),
            (_STEP, _STEPPED),
            (_OBSERVE, _observed(32)),
        )
        assert not recording.created_its_game
        assert not recording.played_in_real_time

    def test_a_game_only_joined_that_observes_twice_without_a_step_was_played_in_real_time(
        self, tmp_path: Path
    ) -> None:
        recording = _recording_of(
            tmp_path / "game.sc2rec", (_JOIN, _JOINED), (_OBSERVE, _observed(16)), (_OBSERVE, _observed(32))
        )
        assert not recording.created_its_game
        assert recording.played_in_real_time

    def test_a_game_created_after_a_ping_was_created(self, tmp_path: Path) -> None:
        recording = _recording_of(
            tmp_path / "game.sc2rec",
            (sc2api_pb2.Request(ping=sc2api_pb2.RequestPing()), _VERSION),
            _create(realtime=False),
            (_JOIN, _JOINED),
        )
        assert recording.created_its_game

    def test_an_empty_recording_created_nothing_and_was_stepped(self, tmp_path: Path) -> None:
        recording = _recording_of(tmp_path / "game.sc2rec")
        assert not recording.created_its_game
        assert not recording.played_in_real_time
        assert recording.start_replay_request is None

    def test_a_replay_played_back_holds_the_request_that_started_it(self, tmp_path: Path) -> None:
        start = sc2api_pb2.RequestStartReplay(replay_data=b"replay", observed_player_id=2)
        started = make_response(Status.IN_REPLAY, start_replay=sc2api_pb2.ResponseStartReplay())
        recording = _recording_of(
            tmp_path / "replay.sc2rec",
            (sc2api_pb2.Request(start_replay=start), started),
            (_OBSERVE, _observed(0)),
        )
        assert recording.start_replay_request == start
        assert not recording.created_its_game

    @pytest.mark.parametrize("created", [False, True], ids=["joined", "created"])
    def test_a_game_played_holds_no_replay_start(self, tmp_path: Path, created: bool) -> None:
        setup = [_create(realtime=False)] if created else []
        recording = _recording_of(tmp_path / "game.sc2rec", *setup, (_JOIN, _JOINED), (_OBSERVE, _observed(16)))
        assert recording.start_replay_request is None


class TestPermissivePlayback:
    """A recording played back for a bot other than the one recorded."""

    def _client(self, tmp_path: Path, *exchanges: tuple[sc2api_pb2.Request, sc2api_pb2.Response]) -> Client:
        recording = _recording_of(tmp_path / "game.sc2rec", *exchanges)
        return Client(PermissivePlaybackTransport(recording))

    def test_every_action_succeeds_and_the_recording_goes_on(self, tmp_path: Path) -> None:
        client = self._client(tmp_path, (_OBSERVE, _observed(16)), (_STEP, _STEPPED), (_OBSERVE, _observed(32)))
        client.observation()
        assert list(client.act([sc2api_pb2.Action(), sc2api_pb2.Action()]).result) == [error_pb2.Success] * 2
        client.step(16)
        assert client.observation().observation.game_loop == 32

    def test_a_debug_request_is_answered(self, tmp_path: Path) -> None:
        client = self._client(tmp_path, (_OBSERVE, _observed(16)))
        client.debug([debug_pb2.DebugCommand(draw=debug_pb2.DebugDraw())])
        assert client.observation().observation.game_loop == 16

    def test_a_pathing_distance_is_the_straight_line_between_its_ends(self, tmp_path: Path) -> None:
        client = self._client(tmp_path)
        answers = client.query(_pathing_query(((1, 2), (4, 6)), ((3, 3), (3, 3)))).pathing
        assert [answer.distance for answer in answers] == [5.0, 0.0]

    def test_a_distance_from_a_unit_is_from_where_the_last_observation_had_it(self, tmp_path: Path) -> None:
        client = self._client(
            tmp_path,
            (_OBSERVE, _observed(16, raw_pb2.Unit(tag=7, pos=common_pb2.Point(x=10, y=10)))),
            (_STEP, _STEPPED),
            (_OBSERVE, _observed(32, raw_pb2.Unit(tag=7, pos=common_pb2.Point(x=13, y=14)))),
        )
        client.observation()
        client.step(16)
        client.observation()
        answers = client.query(_pathing_query((7, (13, 10)), (8, (13, 10)))).pathing
        assert [answer.distance for answer in answers] == [4.0, 0.0]

    def test_a_query_for_placements_or_abilities_raises(self, tmp_path: Path) -> None:
        client = self._client(tmp_path)
        placements = query_pb2.RequestQuery(placements=[query_pb2.RequestQueryBuildingPlacement(ability_id=318)])
        abilities = query_pb2.RequestQuery(abilities=[query_pb2.RequestQueryAvailableAbilities(unit_tag=7)])
        for query in (placements, abilities):
            with pytest.raises(ProtocolError, match="only for pathing distances"):
                client.query(query)

    def test_saving_the_replay_is_refused(self, tmp_path: Path) -> None:
        client = self._client(tmp_path, (_OBSERVE, _observed(16)))
        with pytest.raises(ProtocolError, match="no replay to save"):
            client.save_replay()

    def test_a_game_info_the_recording_does_not_answer_next_is_the_last_it_answered(self, tmp_path: Path) -> None:
        client = self._client(
            tmp_path,
            (_GAME_INFO, make_response(game_info=sc2api_pb2.ResponseGameInfo(map_name="At the start"))),
            (_OBSERVE, _observed(16)),
            (_STEP, _STEPPED),
            (_OBSERVE, _observed(32)),
            (_GAME_INFO, make_response(game_info=sc2api_pb2.ResponseGameInfo(map_name="In the turn at 32"))),
            (_STEP, _STEPPED),
            (_OBSERVE, _observed(48)),
        )
        assert client.game_info().map_name == "At the start"
        client.observation()
        assert client.game_info().map_name == "At the start"
        client.step(16)
        client.observation()
        client.step(16)
        assert client.observation().observation.game_loop == 48
        assert client.game_info().map_name == "In the turn at 32"

    def test_a_game_info_once_the_recording_has_run_out_is_the_last_it_answered(self, tmp_path: Path) -> None:
        client = self._client(
            tmp_path, (_GAME_INFO, make_response(game_info=sc2api_pb2.ResponseGameInfo(map_name="At the start")))
        )
        client.game_info()
        assert client.game_info().map_name == "At the start"

    def test_the_recorded_bots_own_requests_are_passed_over(self, tmp_path: Path) -> None:
        refused = make_response(action=sc2api_pb2.ResponseAction(result=[error_pb2.Error]))
        debug = sc2api_pb2.Request(debug=sc2api_pb2.RequestDebug())
        query = sc2api_pb2.Request(query=_pathing_query(((1, 2), (4, 6))))
        no_path = make_response(query=query_pb2.ResponseQuery(pathing=[query_pb2.ResponseQueryPathing(distance=0)]))
        save_replay = sc2api_pb2.Request(save_replay=sc2api_pb2.RequestSaveReplay())
        replay = make_response(save_replay=sc2api_pb2.ResponseSaveReplay(data=b"replay"))
        client = self._client(
            tmp_path,
            (_OBSERVE, _observed(16)),
            (sc2api_pb2.Request(action=sc2api_pb2.RequestAction(actions=[sc2api_pb2.Action()])), refused),
            (debug, make_response(debug=sc2api_pb2.ResponseDebug())),
            (query, no_path),
            (save_replay, replay),
            (_STEP, _STEPPED),
            (_OBSERVE, _observed(32)),
        )
        client.observation()
        assert list(client.act([sc2api_pb2.Action()]).result) == [error_pb2.Success]
        assert [answer.distance for answer in client.query(query.query).pathing] == [5.0]
        client.step(16)
        assert client.observation().observation.game_loop == 32

    def test_a_step_is_answered_where_the_recording_has_none(self, tmp_path: Path) -> None:
        """As in a game recorded in real time, whose observations follow one another with no step between."""
        client = self._client(tmp_path, (_OBSERVE, _observed(16)), (_OBSERVE, _observed(32)))
        client.observation()
        client.step(16)
        assert client.observation().observation.game_loop == 32

    def test_a_game_the_recording_only_joined_is_created(self, tmp_path: Path) -> None:
        joined = make_response(Status.IN_GAME, join_game=sc2api_pb2.ResponseJoinGame(player_id=2))
        client = self._client(tmp_path, (sc2api_pb2.Request(join_game=sc2api_pb2.RequestJoinGame()), joined))
        client.create_game("map", [Participant(), Computer()])
        assert client.status is Status.INIT_GAME
        assert client.join_game(Race.TERRAN) == 2

    def test_a_recorded_ping_is_passed_over_while_something_else_is_asked(self, tmp_path: Path) -> None:
        joined = make_response(Status.IN_GAME, join_game=sc2api_pb2.ResponseJoinGame(player_id=2))
        client = self._client(
            tmp_path,
            (sc2api_pb2.Request(ping=sc2api_pb2.RequestPing()), _VERSION),
            (sc2api_pb2.Request(join_game=sc2api_pb2.RequestJoinGame()), joined),
        )
        client.create_game("map", [Participant(), Computer()])
        assert client.join_game(Race.TERRAN) == 2

    @pytest.mark.parametrize("leaves", [True, False], ids=["then leaves", "then stops"])
    def test_an_observation_after_the_last_is_the_last_again_with_the_game_ended(
        self, tmp_path: Path, leaves: bool
    ) -> None:
        """As in a game recorded with a time limit, which stops with no result."""
        leave = sc2api_pb2.Request(leave_game=sc2api_pb2.RequestLeaveGame())
        left = make_response(Status.LAUNCHED, leave_game=sc2api_pb2.ResponseLeaveGame())
        client = self._client(tmp_path, (_OBSERVE, _observed(16)), *([(leave, left)] if leaves else []))
        client.observation()
        client.step(16)
        assert client.observation().observation.game_loop == 16
        assert client.status is Status.ENDED
        assert client.result is None

    def test_another_request_the_recording_does_not_answer_next_still_raises(self, tmp_path: Path) -> None:
        client = self._client(tmp_path, (_OBSERVE, _observed(16)))
        with pytest.raises(ProtocolError, match="answers observation next, and was asked for ping"):
            client.ping()

    def test_leaving_before_the_recording_does_is_answered(self, tmp_path: Path) -> None:
        client = self._client(tmp_path, (_OBSERVE, _observed(16)), (_STEP, _STEPPED), (_OBSERVE, _observed(32)))
        client.observation()
        client.leave_game()
        assert client.status is Status.LAUNCHED

    def test_a_made_up_answer_has_the_last_recorded_status(self, tmp_path: Path) -> None:
        recording = _recording_of(
            tmp_path / "game.sc2rec", (_OBSERVE, make_response(Status.ENDED, observation=make_observation(16)))
        )
        transport = PermissivePlaybackTransport(recording)
        transport.request(_OBSERVE)
        answer = transport.request(sc2api_pb2.Request(action=sc2api_pb2.RequestAction(actions=[sc2api_pb2.Action()])))
        assert answer.status == Status.ENDED.value

    def test_a_request_after_closing_raises(self, tmp_path: Path) -> None:
        transport = PermissivePlaybackTransport(_recording_of(tmp_path / "game.sc2rec"))
        transport.close()
        with pytest.raises(ConnectionClosedError):
            transport.request(sc2api_pb2.Request(ping=sc2api_pb2.RequestPing()))
