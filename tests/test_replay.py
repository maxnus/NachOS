from collections.abc import Iterable, Iterator
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Self

import pytest
from s2clientprotocol import raw_pb2, sc2api_pb2

import sc2nachos.replay
from sc2nachos import (
    AbilityId,
    Api,
    Computer,
    Difficulty,
    FogOnError,
    GameTime,
    Race,
    ReplayPlayback,
    Result,
    UnitTypeId,
    open_replay,
    open_replay_recording,
    run_local,
    run_recording,
    run_replay,
)
from sc2nachos.events import (
    AnyUnitEvent,
    EnemyUnitDiedEvent,
    EnemyUnitEnteredSightEvent,
    EnemyUnitFirstSeenEvent,
    EnemyUnitLeftSightEvent,
    OwnActionEvent,
    TurnEvent,
    UnitFoundDeadEvent,
)
from sc2nachos.gamedata._techtree import TECH_TREE
from sc2nachos.launch import GameProcess, GameVersionError, Installation, MapFile, MapNotFoundError
from sc2nachos.protocol import (
    Client,
    Exchange,
    ProtocolError,
    Recording,
    Status,
    WebSocketTransport,
)
from sc2nachos.state import UnitCommand
from sc2nachos.units import NotReportedError, Unit, Units, Visibility
from support import (
    PYLON_CORPUS_GAME,
    ClientStarted,
    FakeTransport,
    last_recorded_step,
    make_response,
    pylon_game_stopped_after,
    write_recording,
)

# A map from the current AIE ladder pool, for the test games.
_LADDER_MAP = "PylonAIE_v4"


# The replay's info a recording `_played_back` makes holds: the corpus game's build and its two players.
_REPLAY_INFO = sc2api_pb2.ResponseReplayInfo(
    base_build=TECH_TREE.base_build,
    map_name="Pylon AIE",
    player_info=[
        sc2api_pb2.PlayerInfoExtra(
            player_info=sc2api_pb2.PlayerInfo(player_id=1, type=sc2api_pb2.PlayerType.Participant)
        ),
        sc2api_pb2.PlayerInfoExtra(player_info=sc2api_pb2.PlayerInfo(player_id=2, type=sc2api_pb2.PlayerType.Computer)),
    ],
)


def _played_back(
    exchanges: Iterable[Exchange], *, disable_fog: bool = False, map_data: bytes | None = None
) -> Iterator[Exchange]:
    """`exchanges`, the corpus game on Pylon's, as a recording of its replay played back as the player it joined as,
    with the fog off if `disable_fog` and the map's bytes sent if `map_data`, holds them: the replay's info read and
    the replay started where the game was joined, never created, and played back rather than played."""
    for exchange in exchanges:
        asked_kind = exchange.request.WhichOneof("request")
        if asked_kind == "create_game":
            continue
        if asked_kind == "join_game":
            asked_info = sc2api_pb2.RequestReplayInfo(replay_data=b"the replay")
            read = make_response(Status.LAUNCHED, replay_info=_REPLAY_INFO)
            yield Exchange(sc2api_pb2.Request(replay_info=asked_info), read)
            start = sc2api_pb2.RequestStartReplay(
                replay_data=b"the replay",
                observed_player_id=exchange.response.join_game.player_id,
                disable_fog=disable_fog,
            )
            if map_data is not None:
                start.map_data = map_data
            started = make_response(Status.IN_REPLAY, start_replay=sc2api_pb2.ResponseStartReplay())
            yield Exchange(sc2api_pb2.Request(start_replay=start), started)
            continue
        if exchange.response.status == sc2api_pb2.Status.in_game:
            exchange.response.status = sc2api_pb2.Status.in_replay
        yield exchange


# The tag of the enemy marine `_with_an_enemy_marine_unseen` adds, which no corpus unit has.
_UNSEEN_MARINE = 0x7FFF0001


def _with_an_enemy_marine_unseen(exchanges: Iterable[Exchange]) -> Iterator[Exchange]:
    """`exchanges` with an enemy marine in every observation, standing out of sight at the enemy's start, as a game
    played back with the fog off lists one the player does not see."""
    enemy_start = None
    for exchange in exchanges:
        response = exchange.response
        if response.HasField("game_info"):
            enemy_start = response.game_info.start_raw.start_locations[0]
        elif response.HasField("observation") and enemy_start is not None:
            marine = response.observation.observation.raw_data.units.add(
                tag=_UNSEEN_MARINE,
                unit_type=UnitTypeId.MARINE,
                alliance=raw_pb2.Alliance.Enemy,
                display_type=raw_pb2.DisplayType.Hidden,
                owner=2,
                health=45,
                health_max=45,
            )
            marine.pos.x, marine.pos.y = enemy_start.x, enemy_start.y
        yield exchange


class TestPlayingARecordedReplay:
    """A recording of a replay played back by `run_replay`, played back again with no game."""

    def test_it_plays_back_as_the_player_it_was_played_for_to_the_replays_end(self, tmp_path: Path) -> None:
        recording = write_recording(tmp_path / "replay.sc2rec", _played_back(Recording(PYLON_CORPUS_GAME)))
        start_replay_request = recording.start_replay_request
        assert start_replay_request is not None
        api = Api()
        assert run_recording(recording, api) is Result.DEFEAT
        assert api.player.id == start_replay_request.observed_player_id
        assert api.step == last_recorded_step(recording)

    def test_at_the_time_limit_it_is_undecided(self, tmp_path: Path) -> None:
        recording = write_recording(tmp_path / "replay.sc2rec", _played_back(Recording(PYLON_CORPUS_GAME)))
        api = Api()
        assert run_recording(recording, api, time_limit=GameTime.from_seconds(60)) is Result.UNDECIDED
        assert GameTime.from_seconds(60) <= api.time < GameTime.from_seconds(61)

    def test_a_bot_that_leaves_it_is_undecided_where_it_left(self, tmp_path: Path) -> None:
        recording = write_recording(tmp_path / "replay.sc2rec", _played_back(pylon_game_stopped_after(100)))
        last_step = last_recorded_step(recording)
        api = Api()

        @api.on(TurnEvent)
        def leave(_: TurnEvent) -> None:
            if api.step >= last_step:
                api.leave_game()

        assert run_recording(recording, api) is Result.UNDECIDED
        assert api.step == last_step

    @pytest.mark.parametrize("map_data", [b"the map", None], ids=["the map sent", "no map sent"])
    def test_it_sends_the_map_sent_when_it_was_recorded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, map_data: bytes | None
    ) -> None:
        exchanges = _played_back(Recording(PYLON_CORPUS_GAME), map_data=map_data)
        recording = write_recording(tmp_path / "replay.sc2rec", exchanges)
        start_replay = Client.start_replay
        maps_sent: list[bytes | None] = []

        def recording_the_map(client: Client, replay: bytes, **kwargs: Any) -> None:
            maps_sent.append(kwargs.get("map_data"))
            start_replay(client, replay, **kwargs)

        monkeypatch.setattr(Client, "start_replay", recording_the_map)
        run_recording(recording, Api())
        assert maps_sent == [map_data]

    def test_it_is_refused_permissive(self, tmp_path: Path) -> None:
        recording = write_recording(tmp_path / "replay.sc2rec", _played_back(Recording(PYLON_CORPUS_GAME)))
        with pytest.raises(ValueError, match="is a replay played back, which plays back only as it was recorded"):
            run_recording(recording, Api(), permissive=True)


class TestAReplayPlayedBackWithTheFogOff:
    def _recording(self, tmp_path: Path) -> Recording:
        """The corpus game on Pylon, for 100 observations, as a replay played back with the fog off, an enemy marine
        out of sight in every observation."""
        exchanges = _with_an_enemy_marine_unseen(pylon_game_stopped_after(100))
        return write_recording(tmp_path / "replay.sc2rec", _played_back(exchanges, disable_fog=True))

    def _turns(self, recording: Recording) -> list[tuple[Units[Unit[Any]], Units[Unit[Any]]]]:
        """Each turn's units and all units, played back from `recording` to its last observation."""
        api = Api()
        turns: list[tuple[Units[Unit[Any]], Units[Unit[Any]]]] = []

        @api.on(TurnEvent)
        def note(_: TurnEvent) -> None:
            turns.append((api.units, api.all_units))

        run_recording(recording, api, time_limit=GameTime(steps=last_recorded_step(recording)))
        return turns

    def test_all_units_holds_the_unit_the_player_did_not_see_in_full(self, tmp_path: Path) -> None:
        turns = self._turns(self._recording(tmp_path))
        assert turns
        for units, all_units in turns:
            assert not [unit for unit in units if unit.tag == _UNSEEN_MARINE]
            (marine,) = [unit for unit in all_units if unit.tag == _UNSEEN_MARINE]
            assert (marine.visibility, marine.health) == (Visibility.VISIBLE, 45)

    def test_every_unit_the_player_knew_is_among_all_units_under_its_id(self, tmp_path: Path) -> None:
        for units, all_units in self._turns(self._recording(tmp_path)):
            assert len(all_units) > len(units)
            assert all(
                (whole := all_units.get(unit.id)) is not None and whole.type_id == unit.type_id for unit in units
            )

    @pytest.mark.parametrize("disable_fog", [False, None], ids=["replay with the fog on", "game"])
    def test_all_units_raises_where_the_fog_is_on(self, tmp_path: Path, disable_fog: bool | None) -> None:
        recording = (
            PYLON_CORPUS_GAME
            if disable_fog is None
            else write_recording(tmp_path / "replay.sc2rec", _played_back(Recording(PYLON_CORPUS_GAME)))
        )
        api = Api()

        @api.on(TurnEvent, once=True)
        def read(_: TurnEvent) -> None:
            api.all_units  # noqa: B018

        with pytest.raises(FogOnError, match="the fog is on, so the game reports only what this player sees"):
            run_recording(recording, api)


class TestTheReplaysPlayer:
    def _replay_info_of_players(self, *types: sc2api_pb2.PlayerType.ValueType) -> sc2api_pb2.ResponseReplayInfo:
        """The info of a replay of players of `types`, numbered from 1."""
        info = sc2api_pb2.ResponseReplayInfo()
        for player_id, player_type in enumerate(types, start=1):
            info.player_info.add().player_info.CopyFrom(sc2api_pb2.PlayerInfo(player_id=player_id, type=player_type))
        return info

    @pytest.mark.parametrize("player_id", [1, 2])
    def test_either_player_of_a_game_against_the_computer_may_be_played_for(self, player_id: int) -> None:
        info = self._replay_info_of_players(sc2api_pb2.PlayerType.Participant, sc2api_pb2.PlayerType.Computer)
        sc2nachos.replay._check_replay_player(info, player_id)

    @pytest.mark.parametrize("player_id", [0, 3])
    def test_a_player_the_replay_does_not_list_is_refused(self, player_id: int) -> None:
        info = self._replay_info_of_players(sc2api_pb2.PlayerType.Participant, sc2api_pb2.PlayerType.Computer)
        with pytest.raises(ValueError, match=f"the replay's players are 1 and 2, and {player_id} is not one of them"):
            sc2nachos.replay._check_replay_player(info, player_id)

    def test_an_observer_is_not_one_of_its_players(self) -> None:
        info = self._replay_info_of_players(
            sc2api_pb2.PlayerType.Participant, sc2api_pb2.PlayerType.Participant, sc2api_pb2.PlayerType.Observer
        )
        with pytest.raises(ValueError, match="the replay's players are 1 and 2, and 3 is not one of them"):
            sc2nachos.replay._check_replay_player(info, 3)


class _ClientLaunched:
    """A game client `open_replay` launched, which records whether it was stopped."""

    def __init__(self) -> None:
        self.url = "ws://127.0.0.1:1/sc2api"
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.terminate()


class TestTheReplaysBuild:
    """Which client `open_replay` starts for a replay, stopped where it would start playing."""

    _NEWEST = TECH_TREE.base_build + 1
    _OLDER = TECH_TREE.base_build

    def _open_on_an_installation(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        *,
        installed: tuple[int, ...],
        replay_build: int,
        raising: type[Exception] = ClientStarted,
    ) -> tuple[list[int | None], list[_ClientLaunched]]:
        """Open the replay with `open_replay` on an installation of the `installed` builds for a replay of
        `replay_build`, where it would connect to play raising `ClientStarted`, expecting it to raise `raising`, and
        return the builds it launched and the clients."""
        for build in installed:
            (tmp_path / "Versions" / f"Base{build}").mkdir(parents=True)
        replay = tmp_path / "game.SC2Replay"
        replay.write_bytes(b"the replay")
        launched_builds: list[int | None] = []
        launched_clients: list[_ClientLaunched] = []

        def launch(installation: Installation, **kwargs: Any) -> _ClientLaunched:
            launched_builds.append(kwargs.get("base_build"))
            if launched_clients and not launched_clients[0].terminated:
                raise AssertionError("a second client was started while the first still ran")
            if len(launched_builds) > 1:
                raise ClientStarted
            launched_clients.append(_ClientLaunched())
            return launched_clients[-1]

        def connect(url: str) -> FakeTransport:
            info = sc2api_pb2.ResponseReplayInfo(base_build=replay_build)
            info.player_info.add().player_info.CopyFrom(sc2api_pb2.PlayerInfo(player_id=1))
            return FakeTransport(make_response(Status.LAUNCHED, replay_info=info))

        def connect_to_play(url: str, record_to: Path | str | None) -> Client:
            raise ClientStarted

        monkeypatch.setattr(GameProcess, "launch", launch)
        monkeypatch.setattr(WebSocketTransport, "connect", connect)
        monkeypatch.setattr(sc2nachos.replay, "connect_client", connect_to_play)
        with pytest.raises(raising), open_replay(replay, player_id=1, installation=Installation(tmp_path, "Linux")):
            pass
        return launched_builds, launched_clients

    def test_a_replay_of_the_newest_build_installed_plays_on_the_client_that_read_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        installed = (self._OLDER, self._NEWEST)
        launched, clients = self._open_on_an_installation(
            tmp_path, monkeypatch, installed=installed, replay_build=self._NEWEST
        )
        assert launched == [self._NEWEST]
        assert clients[0].terminated

    def test_a_replay_of_an_older_build_installed_stops_that_client_and_starts_its_own(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        installed = (self._OLDER, self._NEWEST)
        launched, clients = self._open_on_an_installation(
            tmp_path, monkeypatch, installed=installed, replay_build=self._OLDER
        )
        assert launched == [self._NEWEST, self._OLDER]
        assert clients[0].terminated

    def test_a_replay_of_a_build_not_installed_is_refused_once_read(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        launched, clients = self._open_on_an_installation(
            tmp_path, monkeypatch, installed=(self._NEWEST,), replay_build=self._OLDER, raising=GameVersionError
        )
        assert launched == [self._NEWEST]
        assert clients[0].terminated


class _ReplayStartCaught:
    """A client `open_replay` would play on, which reads the replay's info as `info`, records the map sent with the
    replay and stops there."""

    def __init__(self, info: sc2api_pb2.ResponseReplayInfo) -> None:
        self.map_data_sent: list[bytes | None] = []
        self._info = info

    def replay_info(self, replay: bytes) -> sc2api_pb2.ResponseReplayInfo:
        return self._info

    def start_replay(self, replay: bytes, *, player_id: int, disable_fog: bool, map_data: bytes | None) -> None:
        self.map_data_sent.append(map_data)
        raise ClientStarted

    def leave_game(self) -> None:
        pass

    def quit(self) -> None:
        pass

    def close(self) -> None:
        pass


class TestTheReplaysMap:
    """Which map `open_replay` sends the game with a replay, stopped where it would start playing."""

    _INSTALLED_MAP = b"the installed map"

    def _map_data_sent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replay_map_path: str) -> bytes | None:
        """What `open_replay` sends as the map of a replay whose info names its map at `replay_map_path`, on an
        installation with Pylon in a folder of its maps."""
        (tmp_path / "Versions" / f"Base{TECH_TREE.base_build}").mkdir(parents=True)
        installed_map = tmp_path / "Maps" / "AIE" / "PylonAIE_v4.SC2Map"
        installed_map.parent.mkdir(parents=True, exist_ok=True)
        installed_map.write_bytes(self._INSTALLED_MAP)
        replay = tmp_path / "game.SC2Replay"
        replay.write_bytes(b"the replay")
        info = sc2api_pb2.ResponseReplayInfo(base_build=TECH_TREE.base_build, local_map_path=replay_map_path)
        info.player_info.add().player_info.CopyFrom(sc2api_pb2.PlayerInfo(player_id=1))
        client = _ReplayStartCaught(info)
        monkeypatch.setattr(GameProcess, "launch", lambda installation, **kwargs: _ClientLaunched())
        monkeypatch.setattr(
            WebSocketTransport, "connect", lambda url: FakeTransport(make_response(Status.LAUNCHED, replay_info=info))
        )
        monkeypatch.setattr(sc2nachos.replay, "connect_client", lambda url, record_to: client)
        with (
            pytest.raises(ClientStarted),
            open_replay(replay, player_id=1, installation=Installation(tmp_path, "Linux")),
        ):
            pass
        (map_data,) = client.map_data_sent
        return map_data

    def test_a_bare_map_name_sends_the_installed_map_of_that_name(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """As an AI Arena replay names its map, which the game then does not open (in game)."""
        assert self._map_data_sent(tmp_path, monkeypatch, "PylonAIE_v4.SC2Map") == self._INSTALLED_MAP

    @pytest.mark.parametrize(
        "replay_map_path",
        [
            "/home/someone/StarCraftII/Maps/PylonAIE_v4.SC2Map",
            r"C:\Program Files (x86)\StarCraft II\Maps\PylonAIE_v4.SC2Map",
        ],
        ids=["gone from where it was played", "played on Windows"],
    )
    def test_a_path_to_no_file_here_sends_the_installed_map_of_its_name(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replay_map_path: str
    ) -> None:
        assert self._map_data_sent(tmp_path, monkeypatch, replay_map_path) == self._INSTALLED_MAP

    def test_a_relative_path_sends_the_map_it_names_under_the_maps_over_one_of_its_name_elsewhere(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The shallowest map of a name is the one found by name.
        shallower = tmp_path / "Maps" / "PylonAIE_v4.SC2Map"
        shallower.parent.mkdir(parents=True)
        shallower.write_bytes(b"another map of its name")
        assert self._map_data_sent(tmp_path, monkeypatch, "AIE/PylonAIE_v4.SC2Map") == self._INSTALLED_MAP

    def test_a_path_to_a_file_here_sends_no_map_for_the_game_to_open_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        played_on = tmp_path / "played-on" / "PylonAIE_v4.SC2Map"
        played_on.parent.mkdir()
        played_on.write_bytes(b"the map it was played on")
        assert self._map_data_sent(tmp_path, monkeypatch, str(played_on)) is None

    def test_a_map_not_installed_sends_no_map_and_is_warned_of(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, logged: list[str]
    ) -> None:
        assert self._map_data_sent(tmp_path, monkeypatch, "UnknownAIE.SC2Map") is None
        assert any("The replay's map, UnknownAIE, is not under" in message for message in logged)

    def test_a_replay_naming_no_map_sends_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        assert self._map_data_sent(tmp_path, monkeypatch, "") is None


class TestOpeningAReplay:
    """What `open_replay` asks of the game, over prepared answers, with no game client."""

    _OBSERVED = sc2api_pb2.ResponseObservation(observation=sc2api_pb2.Observation(game_loop=0))

    def _prepare_the_replay(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, refused: bool = False
    ) -> tuple[FakeTransport, _ClientLaunched]:
        """Prepare `open_replay` to open a replay of the corpus game's build on an installation of that build: the
        connection that reads the replay's info, then the connection it plays on, which answers its info again, the
        start, one observation, the leave and the quit, or if `refused`, refuses the start and answers the leave and the
        quit. Return the connection it plays on and the client launched."""
        (tmp_path / "Versions" / f"Base{TECH_TREE.base_build}").mkdir(parents=True)
        (tmp_path / "game.SC2Replay").write_bytes(b"the replay")
        launched = _ClientLaunched()
        if refused:
            refusal = sc2api_pb2.ResponseStartReplay(error=sc2api_pb2.ResponseStartReplay.LaunchError)
            started = [make_response(Status.LAUNCHED, start_replay=refusal)]
        else:
            started = [
                make_response(Status.IN_REPLAY, start_replay=sc2api_pb2.ResponseStartReplay()),
                make_response(Status.IN_REPLAY, observation=self._OBSERVED),
            ]
        playing = FakeTransport(
            make_response(Status.LAUNCHED, replay_info=_REPLAY_INFO),
            *started,
            make_response(Status.LAUNCHED, leave_game=sc2api_pb2.ResponseLeaveGame()),
            make_response(Status.QUIT, quit=sc2api_pb2.ResponseQuit()),
        )
        connections = iter([FakeTransport(make_response(Status.LAUNCHED, replay_info=_REPLAY_INFO)), playing])
        monkeypatch.setattr(GameProcess, "launch", lambda installation, **kwargs: launched)
        monkeypatch.setattr(WebSocketTransport, "connect", lambda url: next(connections))
        return playing, launched

    def _open_the_replay_prepared(self, tmp_path: Path, **kwargs: Any) -> AbstractContextManager[ReplayPlayback]:
        """`open_replay` of the replay `_prepare_the_replay` prepared, as player 2, with `kwargs`."""
        return open_replay(
            tmp_path / "game.SC2Replay", player_id=2, installation=Installation(tmp_path, "Linux"), **kwargs
        )

    def test_it_reads_the_info_and_starts_the_replay_on_the_connection_it_plays_on_then_leaves_and_quits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        playing, launched = self._prepare_the_replay(tmp_path, monkeypatch)
        with self._open_the_replay_prepared(tmp_path, disable_fog=True) as playback:
            assert isinstance(playback, ReplayPlayback)
            assert playback.info == _REPLAY_INFO
            assert playback.client.in_replay
            assert playback.client.observation() == self._OBSERVED
        asked = [request.WhichOneof("request") for request in playing.requests]
        assert asked == ["replay_info", "start_replay", "observation", "leave_game", "quit"]
        started = playing.requests[1].start_replay
        assert (started.observed_player_id, started.disable_fog) == (2, True)
        assert playing.closed
        assert launched.terminated

    def test_it_leaves_and_quits_when_reading_it_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        playing, launched = self._prepare_the_replay(tmp_path, monkeypatch)
        with pytest.raises(RuntimeError, match="a reader's error"), self._open_the_replay_prepared(tmp_path):
            raise RuntimeError("a reader's error")
        assert [request.WhichOneof("request") for request in playing.requests][-2:] == ["leave_game", "quit"]
        assert launched.terminated

    def test_a_replay_the_game_refuses_to_start_is_raised_and_its_client_stopped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        playing, launched = self._prepare_the_replay(tmp_path, monkeypatch, refused=True)
        with (
            pytest.raises(ProtocolError, match="the game refused to play the replay back, as LaunchError"),
            self._open_the_replay_prepared(tmp_path),
        ):
            pass
        assert [request.WhichOneof("request") for request in playing.requests][-2:] == ["leave_game", "quit"]
        assert launched.terminated

    def test_its_recording_plays_back_with_no_game_as_it_was_read(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._prepare_the_replay(tmp_path, monkeypatch)
        record = tmp_path / "played-back.sc2rec"
        with self._open_the_replay_prepared(tmp_path, record_to=record) as playback:
            observed = playback.client.observation()
        with open_replay_recording(record) as again:
            assert again.info == _REPLAY_INFO
            assert again.client.in_replay
            assert again.client.observation() == observed
        start_replay_request = Recording(record).start_replay_request
        assert start_replay_request is not None
        assert (start_replay_request.replay_data, start_replay_request.observed_player_id) == (b"the replay", 2)


class TestOpeningARecordedReplay:
    """A recording of a replay played back, opened with `open_replay_recording`, with no game client."""

    def test_it_starts_the_replay_as_it_was_recorded(self, tmp_path: Path) -> None:
        exchanges = _played_back(pylon_game_stopped_after(10), disable_fog=True, map_data=b"the map")
        recording = write_recording(tmp_path / "replay.sc2rec", exchanges)
        with open_replay_recording(recording) as playback:
            assert playback.info == _REPLAY_INFO
            assert (playback.client.in_replay, playback.client.fog_disabled) == (True, True)

    def test_a_recording_of_a_game_played_is_refused(self) -> None:
        with (
            pytest.raises(ValueError, match="is a game played, not a replay played back"),
            open_replay_recording(PYLON_CORPUS_GAME),
        ):
            pass

    def test_a_recording_made_before_a_replays_info_was_recorded_is_refused_by_name(self, tmp_path: Path) -> None:
        exchanges = [
            exchange
            for exchange in _played_back(pylon_game_stopped_after(10))
            if not exchange.request.HasField("replay_info")
        ]
        recording = write_recording(tmp_path / "replay.sc2rec", exchanges)
        with pytest.raises(ValueError, match="holds no replay info, as a recording made before NachOS recorded one"):
            run_recording(recording, Api())


class _GameWatcher:
    """An api that notes, every turn, its own units and how many units it is shown, and every order it gives."""

    def __init__(self) -> None:
        self.api = Api()
        self.own_units: dict[int, list[tuple[UnitTypeId, float, float, float]]] = {}
        self.units_shown: dict[int, int] = {}
        self.orders_given: list[tuple[int, AbilityId]] = []
        self.api.on(TurnEvent)(self._note_the_units)
        self.api.on(OwnActionEvent)(self._note_the_order)

    def _note_the_units(self, event: TurnEvent) -> None:
        own = self.api.units.own
        self.own_units[self.api.step] = sorted(
            (unit.type_id, round(unit.position.x, 3), round(unit.position.y, 3), unit.health) for unit in own
        )
        self.units_shown[self.api.step] = len(self.api.units)

    def _note_the_order(self, event: OwnActionEvent) -> None:
        if isinstance(event.action, UnitCommand):
            self.orders_given.append((self.api.step, event.action.ability))


def _train_an_scv_on_the_first_turn(api: Api) -> None:
    """Have `api`, playing Terran, train an SCV on its first turn."""

    @api.on(TurnEvent, once=True)
    def train(_: TurnEvent) -> None:
        api.orders.issue(api.units.own.townhalls[0], AbilityId.COMMAND_CENTER_TRAIN_SCV)


def _send_a_worker_to_the_natural(api: Api, *, at_time: GameTime) -> None:
    """Have `api` send one of its workers to its natural at `at_time`."""

    @api.on(TurnEvent, at_time=at_time)
    def send(_: TurnEvent) -> None:
        api.orders.issue(api.units.own.workers[0], AbilityId.MOVE, target=api.expansions[1].center)


@pytest.mark.integration
def test_in_a_real_game_its_replay_plays_back_as_it_was_played(tmp_path: Path) -> None:
    """Run with `pytest -m integration`. Plays 20 seconds of a game the bot gives orders in, saving its replay, then
    plays the replay back as the bot's player, recorded, as the computer's, which may not order, and from the recording
    with no game."""
    try:
        game_map = MapFile.find(_LADDER_MAP)
    except MapNotFoundError as missing:
        pytest.skip(str(missing))
    replay = tmp_path / "game.SC2Replay"
    record = tmp_path / "played-back.sc2rec"
    played = _GameWatcher()
    _train_an_scv_on_the_first_turn(played.api)
    _send_a_worker_to_the_natural(played.api, at_time=GameTime(steps=40))
    opponent = Computer(Race.ZERG, Difficulty.VERY_EASY)
    result = run_local(
        game_map,
        played.api,
        opponent,
        race=Race.TERRAN,
        steps_per_turn=8,
        time_limit=GameTime.from_seconds(20),
        save_replay_to=replay,
    )
    assert result is Result.TIE
    assert {ability for _, ability in played.orders_given} == {AbilityId.COMMAND_CENTER_TRAIN_SCV, AbilityId.MOVE}

    as_the_bot = _GameWatcher()
    assert run_replay(replay, as_the_bot.api, player_id=1, steps_per_turn=8, record_to=record) is Result.UNDECIDED
    assert (as_the_bot.api.player.id, as_the_bot.api.enemy.id) == (1, 2)
    # Only the last turn may be the game's alone or the replay's alone: they end differently.
    last_steps = {max(played.own_units), max(as_the_bot.own_units)}
    assert played.own_units.keys() ^ as_the_bot.own_units.keys() <= last_steps
    steps_in_both = played.own_units.keys() & as_the_bot.own_units.keys()
    assert all(played.own_units[step] == as_the_bot.own_units[step] for step in steps_in_both)
    assert as_the_bot.orders_given == played.orders_given

    as_the_computer = _GameWatcher()
    _send_a_worker_to_the_natural(as_the_computer.api, at_time=GameTime(steps=80))
    with pytest.raises(ProtocolError, match="Can't issue actions in a replay"):
        run_replay(replay, as_the_computer.api, player_id=2, steps_per_turn=8)
    assert (as_the_computer.api.player.id, as_the_computer.api.player.race) == (2, Race.ZERG)
    own_types = {unit_type for units in as_the_computer.own_units.values() for unit_type, *_ in units}
    assert UnitTypeId.DRONE in own_types
    assert all(as_the_computer.api.data.units[unit_type].race is Race.ZERG for unit_type in own_types)

    with pytest.raises(ValueError, match="the replay's players are 1 and 2, and 0 is not one of them"):
        run_replay(replay, Api(), player_id=0)

    from_the_recording = _GameWatcher()
    assert run_recording(record, from_the_recording.api) is Result.UNDECIDED
    assert from_the_recording.own_units == as_the_bot.own_units
    assert from_the_recording.units_shown == as_the_bot.units_shown
    assert from_the_recording.orders_given == played.orders_given


# The events that follow what the player sees, which a playback with the fog off must hand out as one with it on.
_SIGHT_EVENT_TYPES: tuple[type[AnyUnitEvent], ...] = (
    EnemyUnitFirstSeenEvent,
    EnemyUnitEnteredSightEvent,
    EnemyUnitLeftSightEvent,
    EnemyUnitDiedEvent,
    UnitFoundDeadEvent,
)


class _ViewWatcher:
    """An api that notes, every turn, how it reads each unit it knows of, and every event of what the player sees."""

    def __init__(self) -> None:
        self.api = Api()
        self.readings: dict[int, list[tuple[Any, ...]]] = {}
        self.sight_events: list[tuple[str, int, UnitTypeId]] = []
        self.api.on(TurnEvent)(self._note_the_units)
        for event_type in _SIGHT_EVENT_TYPES:
            self.api.on(event_type)(self._note_the_event)

    def _note_the_units(self, event: TurnEvent) -> None:
        self.readings[self.api.step] = sorted(map(_reading, self.api.units), key=repr)

    def _note_the_event(self, event: AnyUnitEvent) -> None:
        self.sight_events.append((type(event).__name__, self.api.step, event.unit.type_id))


def _reading(unit: Unit[Any]) -> tuple[Any, ...]:
    """How `unit` reads: its side, type, position, visibility and health, or `None` where the game never reported it."""
    try:
        health: float | None = unit.health
    except NotReportedError:
        health = None
    return (unit.alliance, unit.type_id, round(unit.position.x, 2), round(unit.position.y, 2), unit.visibility, health)


def _scout_the_enemys_main_and_come_back(api: Api, *, back_at_step: int) -> None:
    """Have `api` send one of its workers to the enemy's start on its first turn, and back home at `back_at_step`."""
    scout_ids: list[int] = []

    @api.on(TurnEvent, once=True)
    def send(_: TurnEvent) -> None:
        scout = api.units.own.workers[0]
        scout_ids.append(scout.id)
        api.orders.issue(scout, AbilityId.MOVE, target=api.map.all_start_locations[1])

    @api.on(TurnEvent, at_time=GameTime(steps=back_at_step))
    def come_back(_: TurnEvent) -> None:
        if (scout := api.units.own.get(scout_ids[0])) is not None:
            api.orders.issue(scout, AbilityId.MOVE, target=api.map.start_location)


@pytest.mark.integration
def test_in_a_real_game_its_replay_plays_back_once_its_map_is_gone_from_where_it_was_played(tmp_path: Path) -> None:
    """Run with `pytest -m integration`. Plays 5 seconds of a game on a copy of the map, saving its replay, deletes the
    copy, and plays the replay back: the game cannot open the map the replay names, as with an AI Arena replay, so
    `run_replay` sends the installed map of its name."""
    try:
        installed_map = MapFile.find(_LADDER_MAP)
    except MapNotFoundError as missing:
        pytest.skip(str(missing))
    played_on = tmp_path / "played-on" / installed_map.path.name
    played_on.parent.mkdir()
    played_on.write_bytes(installed_map.path.read_bytes())
    replay = tmp_path / "game.SC2Replay"
    opponent = Computer(Race.ZERG, Difficulty.VERY_EASY)
    played = run_local(
        MapFile(played_on),
        Api(),
        opponent,
        race=Race.TERRAN,
        steps_per_turn=8,
        time_limit=GameTime.from_seconds(5),
        save_replay_to=replay,
    )
    assert played is Result.TIE
    played_on.unlink()

    api = Api()
    assert run_replay(replay, api, player_id=1, steps_per_turn=8) is Result.UNDECIDED
    assert api.step > 0


def _observed_to_the_end(client: Client) -> list[sc2api_pb2.ResponseObservation]:
    """Every observation of the replay `client` plays back, 8 steps apart, to its end."""
    observations: list[sc2api_pb2.ResponseObservation] = []
    while True:
        observations.append(client.observation())
        if not client.in_game:
            return observations
        client.step(8)


def _own_unit_owners(observations: list[sc2api_pb2.ResponseObservation]) -> set[int]:
    """The owners of the units `observations` list as the player's own."""
    return {
        unit.owner
        for observation in observations
        for unit in observation.observation.raw_data.units
        if unit.alliance == raw_pb2.Alliance.Self
    }


@pytest.mark.integration
def test_in_a_real_game_its_replay_opens_as_each_player_at_once_and_reads_to_one_end(tmp_path: Path) -> None:
    """Run with `pytest -m integration`. Plays 10 seconds of a game, saving its replay, opens the replay as each player
    at once, recording the first, reads both straight from the protocol to the end, and reads the recording again with
    no game."""
    try:
        game_map = MapFile.find(_LADDER_MAP)
    except MapNotFoundError as missing:
        pytest.skip(str(missing))
    replay = tmp_path / "game.SC2Replay"
    record = tmp_path / "played-back.sc2rec"
    opponent = Computer(Race.ZERG, Difficulty.VERY_EASY)
    played = run_local(
        game_map,
        Api(),
        opponent,
        race=Race.TERRAN,
        steps_per_turn=8,
        time_limit=GameTime.from_seconds(10),
        save_replay_to=replay,
    )
    assert played is Result.TIE

    with (
        open_replay(replay, player_id=1, record_to=record) as first,
        open_replay(replay, player_id=2) as second,
    ):
        assert first.info == second.info
        assert first.info.base_build == TECH_TREE.base_build
        as_the_first = _observed_to_the_end(first.client)
        as_the_second = _observed_to_the_end(second.client)
    assert as_the_first[-1].observation.game_loop == as_the_second[-1].observation.game_loop > 0
    assert (_own_unit_owners(as_the_first), _own_unit_owners(as_the_second)) == ({1}, {2})

    with open_replay_recording(record) as again:
        assert again.info == first.info
        assert _observed_to_the_end(again.client) == as_the_first


@pytest.mark.integration
def test_in_a_real_game_its_replay_with_the_fog_off_shows_the_player_what_the_fog_on_does_and_every_unit_besides(
    tmp_path: Path,
) -> None:
    """Run with `pytest -m integration`. Plays 100 seconds of a game in which the bot scouts the enemy's main and comes
    back, then plays its replay back as the bot's player with the fog on and with it off."""
    try:
        game_map = MapFile.find(_LADDER_MAP)
    except MapNotFoundError as missing:
        pytest.skip(str(missing))
    replay = tmp_path / "game.SC2Replay"
    api = Api()
    _scout_the_enemys_main_and_come_back(api, back_at_step=1500)
    opponent = Computer(Race.ZERG, Difficulty.VERY_EASY)
    run_local(
        game_map,
        api,
        opponent,
        race=Race.TERRAN,
        steps_per_turn=8,
        time_limit=GameTime.from_seconds(100),
        save_replay_to=replay,
    )

    # Observed every step: one sighting between two observations would have the fog on remember a structure under a
    # new tag, which NachOS cannot tell from another structure, while the fog off keeps its tag.
    fog_on = _ViewWatcher()
    run_replay(replay, fog_on.api, player_id=1)
    fog_off = _ViewWatcher()
    unseen_by_turn: dict[int, int] = {}

    @fog_off.api.on(TurnEvent)
    def note_every_unit(event: TurnEvent) -> None:
        known_ids = fog_off.api.units.ids
        all_units = fog_off.api.all_units
        assert known_ids <= all_units.ids
        unseen_by_turn[fog_off.api.step] = len(all_units.enemy.without_ids(known_ids))

    run_replay(replay, fog_off.api, player_id=1, disable_fog=True)

    assert fog_off.readings.keys() == fog_on.readings.keys()
    differences = {
        step: (
            sorted(set(fog_on.readings[step]) - set(fog_off.readings[step]), key=repr),
            sorted(set(fog_off.readings[step]) - set(fog_on.readings[step]), key=repr),
        )
        for step in fog_on.readings
        if fog_on.readings[step] != fog_off.readings[step]
    }
    assert not differences, f"with the fog on alone, and off alone: {differences}"
    assert fog_off.sight_events == fog_on.sight_events
    seen = {name for name, _, _ in fog_on.sight_events}
    assert {"EnemyUnitFirstSeenEvent", "EnemyUnitLeftSightEvent"} <= seen
    assert max(unseen_by_turn.values()) > 0
    remembered = fog_off.api.units.enemy.filter(lambda unit: unit.visibility is Visibility.IN_FOG)
    assert remembered, "the scout saw no enemy structure to remember"
    for unit in remembered:
        as_it_is = fog_off.api.all_units.get(unit.id)
        assert as_it_is is not None
        assert as_it_is.type_id == unit.type_id
        assert as_it_is.visibility is Visibility.VISIBLE
        assert as_it_is.health > 0
