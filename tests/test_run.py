import json
from collections.abc import Callable, Iterable, Iterator
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Self

import pytest
from loguru import logger
from s2clientprotocol import raw_pb2, sc2api_pb2

import sc2nachos.run
from sc2nachos import (
    AbilityId,
    AIBuild,
    Api,
    Computer,
    Difficulty,
    FogOnError,
    GameTime,
    Race,
    Result,
    UnitTypeId,
    run_from_command_line,
    run_local,
    run_recording,
    run_replay,
)
from sc2nachos._command_line_arguments import _CommandLineArguments
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
from sc2nachos.launch import GameProcess, GameVersionError, Installation, MapFile, MapNotFoundError, free_port
from sc2nachos.match import Participant
from sc2nachos.protocol import (
    Client,
    ConnectionClosedError,
    Exchange,
    ProtocolError,
    Recording,
    Status,
    WebSocketTransport,
)
from sc2nachos.state import UnitCommand
from sc2nachos.units import NotReportedError, Unit, Units, Visibility
from support import FakeTransport, make_response, recorded_observations, write_recording

# A map from the current AIE ladder pool, for the test games.
_LADDER_MAP = "PylonAIE_v4"


@pytest.fixture
def played(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """What `run_local` was asked to play, with an installation holding two maps and no game started."""
    for name in ("PylonAIE_v4", "TorchesAIE_v4"):
        (tmp_path / "maps" / "AIE").mkdir(parents=True, exist_ok=True)
        (tmp_path / "maps" / "AIE" / f"{name}.SC2Map").write_bytes(b"")
    monkeypatch.setenv("SC2PATH", str(tmp_path))
    calls: dict[str, Any] = {}

    def run_local(map_file: MapFile, api: Api, opponent: Computer, **kwargs: Any) -> Result:
        calls.update(map_file=map_file, api=api, opponent=opponent, **kwargs)
        return Result.VICTORY

    monkeypatch.setattr(sc2nachos.run, "run_local", run_local)
    monkeypatch.setattr(Api, "time", property(lambda self: GameTime(seconds=754.3841)))
    return calls


@pytest.fixture
def ladder_played(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """What `run_ladder` was asked to play, with no game joined."""
    calls: dict[str, Any] = {}

    def run_ladder(api: Api, **kwargs: Any) -> Result:
        calls.update(api=api, **kwargs)
        return Result.DEFEAT

    monkeypatch.setattr(sc2nachos.run, "run_ladder", run_ladder)
    monkeypatch.setattr(Api, "time", property(lambda self: GameTime(seconds=61.25)))
    monkeypatch.setattr(Api, "map", property(lambda self: SimpleNamespace(name="Pylon AIE")))
    return calls


@pytest.fixture
def logged() -> Iterator[list[str]]:
    """What is logged while the test runs, from info up, each message without its line end."""
    messages: list[str] = []
    sink = logger.add(lambda message: messages.append(message.rstrip("\n")), format="{message}", level="INFO")
    yield messages
    logger.remove(sink)


class TestALadderGame:
    def test_it_joins_with_the_ladders_arguments(self, ladder_played: dict[str, Any], tmp_path: Path) -> None:
        api = Api()
        argv = ["--LadderServer", "10.0.0.7", "--GamePort", "5000", "--StartPort", "5100", "--OpponentId", "5a1f"]
        replay, record = tmp_path / "game.SC2Replay", tmp_path / "game.sc2rec"
        argv += ["--RealTime", "--ReplayFile", str(replay), "--RecordFile", str(record)]
        result = run_from_command_line(api, argv, race=Race.ZERG, name="MyBot", steps_per_turn=2)
        assert result is Result.DEFEAT
        assert ladder_played == {
            "api": api,
            "race": Race.ZERG,
            "name": "MyBot",
            "host": "10.0.0.7",
            "port": 5000,
            "start_port": 5100,
            "enemy_ladder_id": "5a1f",
            "steps_per_turn": 2,
            "realtime": True,
            "record_to": record,
            "save_replay_to": replay,
        }

    def test_against_the_computer_it_has_no_start_port_and_takes_the_computers_arguments_unused(
        self, ladder_played: dict[str, Any], logged: list[str]
    ) -> None:
        computer = ["--ComputerOpponent", "1", "--ComputerRace", "Zerg", "--ComputerDifficulty", "VeryHard"]
        run_from_command_line(Api(), ["--GamePort", "5000", *computer], race=Race.TERRAN)
        assert (ladder_played["host"], ladder_played["start_port"]) == ("127.0.0.1", None)
        assert (ladder_played["enemy_ladder_id"], ladder_played["realtime"]) == (None, False)
        assert not [message for message in logged if "ignores" in message or "left to the bot" in message]

    def test_by_default_it_saves_no_replay_and_records_nothing(self, ladder_played: dict[str, Any]) -> None:
        run_from_command_line(Api(), ["--GamePort", "5000"], race=Race.TERRAN)
        assert (ladder_played["save_replay_to"], ladder_played["record_to"]) == (None, None)

    def test_a_local_games_argument_is_logged_as_ignored(
        self, ladder_played: dict[str, Any], logged: list[str]
    ) -> None:
        argv = ["--GamePort", "5000", "--ComputerBuild", "AIR", "--Map", "Pylon", "--TimeLimit", "0"]
        run_from_command_line(Api(), argv, race=Race.TERRAN)
        assert "A ladder game ignores --ComputerBuild, --Map, --TimeLimit" in logged
        assert ladder_played

    def test_the_result_file_names_the_map_as_the_game_does(
        self, ladder_played: dict[str, Any], tmp_path: Path
    ) -> None:
        result_file = tmp_path / "result.json"
        run_from_command_line(Api(), ["--GamePort", "5000", "--ResultFile", str(result_file)], race=Race.TERRAN)
        assert json.loads(result_file.read_text(encoding="utf-8")) == {
            "result": "DEFEAT",
            "map": "Pylon AIE",
            "game_time": 61.25,
        }


class TestTheLadderClosingTheConnection:
    @staticmethod
    def _closed_by_the_ladder(monkeypatch: pytest.MonkeyPatch) -> None:
        def run_ladder(api: Api, **kwargs: Any) -> Result:
            raise ConnectionClosedError("the connection to the game closed")

        monkeypatch.setattr(sc2nachos.run, "run_ladder", run_ladder)

    def test_ends_a_ladder_game_undecided_with_a_warning(
        self, ladder_played: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path, logged: list[str]
    ) -> None:
        self._closed_by_the_ladder(monkeypatch)
        result_file = tmp_path / "result.json"
        argv = ["--GamePort", "5000", "--ResultFile", str(result_file)]
        assert run_from_command_line(Api(), argv, race=Race.TERRAN) is Result.UNDECIDED
        assert any(message.startswith("The ladder closed the connection") for message in logged)
        written = json.loads(result_file.read_text(encoding="utf-8"))
        assert (written["result"], written["map"]) == ("UNDECIDED", "Pylon AIE")

    def test_before_the_game_began_leaves_the_map_unknown(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._closed_by_the_ladder(monkeypatch)
        result_file = tmp_path / "result.json"
        argv = ["--GamePort", "5000", "--ResultFile", str(result_file)]
        assert run_from_command_line(Api(), argv, race=Race.TERRAN) is Result.UNDECIDED
        assert json.loads(result_file.read_text(encoding="utf-8")) == {
            "result": "UNDECIDED",
            "map": None,
            "game_time": None,
        }

    def test_in_a_local_game_is_raised(self, played: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
        def run_local(*args: Any, **kwargs: Any) -> Result:
            raise ConnectionClosedError("the connection to the game closed")

        monkeypatch.setattr(sc2nachos.run, "run_local", run_local)
        with pytest.raises(ConnectionClosedError):
            run_from_command_line(Api(), [], race=Race.TERRAN)


def test_a_local_game_has_no_ladders_game_port() -> None:
    with pytest.raises(ValueError, match="a local game has no ladder's game port"):
        _ = _CommandLineArguments([]).game_port


def test_a_ladder_games_argument_in_a_local_game_is_logged_as_ignored(
    played: dict[str, Any], logged: list[str]
) -> None:
    run_from_command_line(Api(), ["--OpponentId", "5a1f", "--StartPort", "5100"], race=Race.TERRAN)
    assert "A local game ignores --StartPort, --OpponentId" in logged
    assert played


def test_an_argument_nachos_does_not_read_is_left_for_the_bot(played: dict[str, Any], logged: list[str]) -> None:
    run_from_command_line(Api(), ["--build", "bio", "--debug", "--Map", "PylonAIE_v4"], race=Race.TERRAN)
    assert "Not NachOS's arguments, left to the bot: --build bio --debug" in logged
    assert played["map_file"].name == "PylonAIE_v4"


def test_a_bots_argument_that_begins_like_one_of_nachoss_is_left_for_the_bot(
    played: dict[str, Any], logged: list[str]
) -> None:
    run_from_command_line(Api(), ["--Replay", "x.txt", "--Real", "--Computer", "abc"], race=Race.TERRAN)
    assert "Not NachOS's arguments, left to the bot: --Replay x.txt --Real --Computer abc" in logged
    assert (played["save_replay_to"], played["realtime"], played["opponent"]) == (None, False, Computer())


def test_a_local_game_takes_the_turn_length_realtime_and_the_replay_and_record_files(
    played: dict[str, Any], tmp_path: Path
) -> None:
    replay, record = tmp_path / "game.SC2Replay", tmp_path / "game.sc2rec"
    argv = ["--RealTime", "--ReplayFile", str(replay), "--RecordFile", str(record)]
    run_from_command_line(Api(), argv, race=Race.TERRAN, steps_per_turn=4)
    assert (played["steps_per_turn"], played["realtime"]) == (4, True)
    assert (played["save_replay_to"], played["record_to"]) == (replay, record)


def test_by_default_a_local_game_is_stepped_saves_no_replay_and_records_nothing(played: dict[str, Any]) -> None:
    run_from_command_line(Api(), [], race=Race.TERRAN)
    assert (played["steps_per_turn"], played["realtime"]) == (1, False)
    assert (played["save_replay_to"], played["record_to"]) == (None, None)


def test_the_arguments_are_the_enums_member_names(played: dict[str, Any]) -> None:
    api = Api()
    args = ["--ComputerRace", "ZERG", "--ComputerDifficulty", "VERY_EASY", "--ComputerBuild", "RUSH"]
    argv = [*args, "--Map", "TorchesAIE_v4", "--TimeLimit", "0"]
    result = run_from_command_line(api, argv, race=Race.TERRAN, name="MyBot")
    assert result is Result.VICTORY
    assert played["opponent"] == Computer(Race.ZERG, Difficulty.VERY_EASY, AIBuild.RUSH)
    assert played["map_file"].name == "TorchesAIE_v4"
    assert played["time_limit"] == GameTime(steps=0)
    assert (played["api"], played["race"], played["name"]) == (api, Race.TERRAN, "MyBot")


def test_without_a_map_one_of_the_installations_is_picked(played: dict[str, Any]) -> None:
    run_from_command_line(Api(), [], race=Race.TERRAN)
    assert played["map_file"].name in {"PylonAIE_v4", "TorchesAIE_v4"}
    assert played["time_limit"] is None
    assert played["opponent"] == Computer(Race.RANDOM, Difficulty.VERY_HARD, AIBuild.RANDOM)


def test_the_result_file_is_what_the_match_workflow_reads(played: dict[str, Any], tmp_path: Path) -> None:
    result_file = tmp_path / "result.json"
    run_from_command_line(Api(), ["--Map", "PylonAIE_v4", "--ResultFile", str(result_file)], race=Race.TERRAN)
    assert json.loads(result_file.read_text(encoding="utf-8")) == {
        "result": "VICTORY",
        "map": "PylonAIE_v4",
        "game_time": 754.384,
    }


@pytest.mark.parametrize(
    "argv",
    [["--ComputerDifficulty", "VeryEasy"], ["--ComputerRace", "NONE"], ["--ComputerBuild", "Rush"]],
    ids=["the ladder's difficulty", "no race", "the ladder's build"],
)
def test_in_a_local_game_a_name_that_is_no_member_is_refused(
    played: dict[str, Any], argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit):
        run_from_command_line(Api(), argv, race=Race.TERRAN)
    assert f"argument {argv[0]}: invalid choice: {argv[1]!r}" in capsys.readouterr().err
    assert not played


def test_an_installation_without_maps_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SC2PATH", str(tmp_path))
    (tmp_path / "Maps").mkdir()
    assert Installation.find().maps == tmp_path / "Maps"
    with pytest.raises(MapNotFoundError, match="no maps"):
        run_from_command_line(Api(), [], race=Race.TERRAN)


_PYLON_GAME = Path(__file__).parent / "corpus" / "PylonAIE_v4-TvZ.sc2rec"


@pytest.mark.parametrize(
    "run",
    [
        lambda limit: run_local("PylonAIE_v4", Api(), race=Race.TERRAN, time_limit=limit),
        lambda limit: run_replay("no such replay.SC2Replay", Api(), player_id=1, time_limit=limit),
        lambda limit: run_recording("no such recording.sc2rec", Api(), time_limit=limit),
    ],
    ids=["run_local", "run_replay", "run_recording"],
)
def test_a_time_limit_in_plain_seconds_is_refused_before_anything_starts(run: Callable[[Any], Result]) -> None:
    """Before looking for StarCraft II, which this test run has none of, or opening the file."""
    with pytest.raises(TypeError, match="time_limit is a GameTime"):
        run(600)


def _pylon_game_rewritten(*, created: bool, stepped: bool) -> Iterator[Exchange]:
    """The exchanges of the corpus game on Pylon as a recording of it holds them if it was `created` or only joined,
    and `stepped` or played in real time."""
    for exchange in Recording(_PYLON_GAME):
        asked_kind = exchange.request.WhichOneof("request")
        if (asked_kind == "create_game" and not created) or (asked_kind == "step" and not stepped):
            continue
        if asked_kind == "create_game":
            exchange.request.create_game.realtime = not stepped
        yield exchange


def _pylon_game_stopped_after(observations: int) -> Iterator[Exchange]:
    """The exchanges of the corpus game on Pylon as a recording of it holds them if it stopped after `observations`,
    as one made with a time limit does: its last observation, then leaving the game and quitting."""
    observations_kept = 0
    for exchange in Recording(_PYLON_GAME):
        asked_kind = exchange.request.WhichOneof("request")
        if observations_kept < observations or asked_kind in ("leave_game", "quit"):
            observations_kept += asked_kind == "observation"
            yield exchange


def _pylon_game_left_after(observations: int) -> Iterator[Exchange]:
    """The exchanges of the corpus game on Pylon as a recording of it holds them if the bot left after `observations`,
    saving the replay first."""
    for exchange in _pylon_game_stopped_after(observations):
        if exchange.request.HasField("leave_game"):
            replay = sc2api_pb2.ResponseSaveReplay(data=b"the replay recorded")
            yield Exchange(
                sc2api_pb2.Request(save_replay=sc2api_pb2.RequestSaveReplay()), make_response(save_replay=replay)
            )
        if not exchange.request.HasField("quit"):
            yield exchange


def _played_back(
    exchanges: Iterable[Exchange], *, disable_fog: bool = False, map_data: bytes | None = None
) -> Iterator[Exchange]:
    """`exchanges`, the corpus game on Pylon's, as a recording of its replay played back as the player it joined as,
    with the fog off if `disable_fog` and the map's bytes sent if `map_data`, holds them: the replay started where the
    game was joined, never created, and played back rather than played."""
    for exchange in exchanges:
        asked_kind = exchange.request.WhichOneof("request")
        if asked_kind == "create_game":
            continue
        if asked_kind == "join_game":
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


def _last_recorded_step(recording: Recording) -> int:
    """The step of the last observation in `recording`."""
    return recorded_observations(recording)[-1].observation.game_loop


class TestPlayingARecording:
    def test_by_default_a_bot_that_asks_what_the_recording_was_not_asked_raises(self) -> None:
        api = Api()

        @api.on(TurnEvent)
        def greet(_: TurnEvent) -> None:
            api.chat("hello")

        with pytest.raises(ProtocolError, match="was asked for action"):
            run_recording(_PYLON_GAME, api)

    def test_by_default_a_handlers_exception_raises_out_as_it_is(self) -> None:
        api = Api()

        @api.on(TurnEvent)
        def crash(_: TurnEvent) -> None:
            if api.step >= 160:
                raise RuntimeError("the crash being reproduced")

        with pytest.raises(RuntimeError, match="the crash being reproduced"):
            run_recording(_PYLON_GAME, api)

    def test_by_default_the_enemys_ladder_id_is_the_one_given(self) -> None:
        api = Api()
        ladder_ids: set[str | None] = set()

        @api.on(TurnEvent)
        def read(_: TurnEvent) -> None:
            ladder_ids.add(api.enemy.ladder_id)

        run_recording(_PYLON_GAME, api, enemy_ladder_id="5a1f")
        assert ladder_ids == {"5a1f"}

    def test_a_permissive_bot_that_leaves_is_defeated_where_it_left(self) -> None:
        api = Api()
        left_at: list[int] = []

        @api.on(TurnEvent)
        def leave(_: TurnEvent) -> None:
            if api.step >= 160 and not left_at:
                left_at.append(api.step)
                api.leave_game()

        assert run_recording(str(_PYLON_GAME), api, permissive=True) is Result.DEFEAT
        assert [api.step] == left_at

    @pytest.mark.parametrize("permissive", [False, True], ids=["strict", "permissive"])
    def test_at_the_time_limit_it_is_a_tie(self, permissive: bool) -> None:
        api = Api()
        assert run_recording(_PYLON_GAME, api, permissive=permissive, time_limit=GameTime(seconds=60)) is Result.TIE
        assert GameTime(seconds=60) <= api.time < GameTime(seconds=61)

    def test_by_default_a_recording_that_stops_before_the_games_end_needs_its_time_limit(self, tmp_path: Path) -> None:
        stopped = write_recording(tmp_path / "stopped.sc2rec", _pylon_game_stopped_after(100))
        last_step = _last_recorded_step(stopped)
        with pytest.raises(ProtocolError, match="answers leave_game next, and was asked for step"):
            run_recording(stopped, Api())
        api = Api()
        assert run_recording(stopped, api, time_limit=GameTime(steps=last_step)) is Result.TIE
        assert api.step == last_step

    def test_by_default_a_bot_that_left_after_saving_its_replay_needs_where_to_save_it(self, tmp_path: Path) -> None:
        left = write_recording(tmp_path / "left.sc2rec", _pylon_game_left_after(100))
        last_step = _last_recorded_step(left)

        def leaving_api() -> Api:
            api = Api()

            @api.on(TurnEvent)
            def leave(_: TurnEvent) -> None:
                if api.step >= last_step:
                    api.leave_game()

            return api

        with pytest.raises(ProtocolError, match="answers save_replay next, and was asked for leave_game"):
            run_recording(left, leaving_api())
        replay = tmp_path / "game.SC2Replay"
        assert run_recording(left, leaving_api(), save_replay_to=replay) is Result.DEFEAT
        assert replay.read_bytes() == b"the replay recorded"

    def test_a_permissive_one_saves_no_replay_and_says_so(self, tmp_path: Path, logged: list[str]) -> None:
        replay = tmp_path / "game.SC2Replay"
        run_recording(_PYLON_GAME, Api(), permissive=True, save_replay_to=replay)
        assert not replay.exists()
        assert any(message.startswith(f"No replay was saved to {replay}") for message in logged)

    def test_a_permissive_one_of_a_recording_that_stops_before_the_games_end_ends_undecided(
        self, tmp_path: Path
    ) -> None:
        stopped = write_recording(tmp_path / "stopped.sc2rec", _pylon_game_stopped_after(100))
        api = Api()
        assert run_recording(stopped, api, permissive=True) is Result.UNDECIDED
        assert api.step == _last_recorded_step(stopped)

    @pytest.mark.parametrize("permissive", [False, True], ids=["strict", "permissive"])
    @pytest.mark.parametrize(
        ("created", "stepped"), [(False, True), (True, False), (False, False)], ids=["joined", "real time", "both"]
    )
    def test_a_recording_plays_to_its_end_however_the_game_was_set_up(
        self, tmp_path: Path, permissive: bool, created: bool, stepped: bool
    ) -> None:
        rewritten = write_recording(
            tmp_path / "rewritten.sc2rec", _pylon_game_rewritten(created=created, stepped=stepped)
        )
        api = Api()
        assert run_recording(rewritten, api, permissive=permissive) is Result.DEFEAT
        assert api.step == _last_recorded_step(rewritten)


class TestPlayingARecordedReplay:
    """A recording of a replay played back by `run_replay`, played back again with no game."""

    def test_it_plays_back_as_the_player_it_was_played_for_to_the_replays_end(self, tmp_path: Path) -> None:
        recording = write_recording(tmp_path / "replay.sc2rec", _played_back(Recording(_PYLON_GAME)))
        start_replay_request = recording.start_replay_request
        assert start_replay_request is not None
        api = Api()
        assert run_recording(recording, api) is Result.DEFEAT
        assert api.player.id == start_replay_request.observed_player_id
        assert api.step == _last_recorded_step(recording)

    def test_at_the_time_limit_it_is_undecided(self, tmp_path: Path) -> None:
        recording = write_recording(tmp_path / "replay.sc2rec", _played_back(Recording(_PYLON_GAME)))
        api = Api()
        assert run_recording(recording, api, time_limit=GameTime(seconds=60)) is Result.UNDECIDED
        assert GameTime(seconds=60) <= api.time < GameTime(seconds=61)

    def test_a_bot_that_leaves_it_is_undecided_where_it_left(self, tmp_path: Path) -> None:
        recording = write_recording(tmp_path / "replay.sc2rec", _played_back(_pylon_game_stopped_after(100)))
        last_step = _last_recorded_step(recording)
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
        exchanges = _played_back(Recording(_PYLON_GAME), map_data=map_data)
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
        recording = write_recording(tmp_path / "replay.sc2rec", _played_back(Recording(_PYLON_GAME)))
        with pytest.raises(ValueError, match="is a replay played back, which plays back only as it was recorded"):
            run_recording(recording, Api(), permissive=True)


class TestAReplayPlayedBackWithTheFogOff:
    def _recording(self, tmp_path: Path) -> Recording:
        """The corpus game on Pylon, for 100 observations, as a replay played back with the fog off, an enemy marine
        out of sight in every observation."""
        exchanges = _with_an_enemy_marine_unseen(_pylon_game_stopped_after(100))
        return write_recording(tmp_path / "replay.sc2rec", _played_back(exchanges, disable_fog=True))

    def _turns(self, recording: Recording) -> list[tuple[Units[Unit[Any]], Units[Unit[Any]]]]:
        """Each turn's units and all units, played back from `recording` to its last observation."""
        api = Api()
        turns: list[tuple[Units[Unit[Any]], Units[Unit[Any]]]] = []

        @api.on(TurnEvent)
        def note(_: TurnEvent) -> None:
            turns.append((api.units, api.all_units))

        run_recording(recording, api, time_limit=GameTime(steps=_last_recorded_step(recording)))
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
            _PYLON_GAME
            if disable_fog is None
            else write_recording(tmp_path / "replay.sc2rec", _played_back(Recording(_PYLON_GAME)))
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
        sc2nachos.run._check_replay_player(info, player_id)

    @pytest.mark.parametrize("player_id", [0, 3])
    def test_a_player_the_replay_does_not_list_is_refused(self, player_id: int) -> None:
        info = self._replay_info_of_players(sc2api_pb2.PlayerType.Participant, sc2api_pb2.PlayerType.Computer)
        with pytest.raises(ValueError, match=f"the replay's players are 1 and 2, and {player_id} is not one of them"):
            sc2nachos.run._check_replay_player(info, player_id)

    def test_an_observer_is_not_one_of_its_players(self) -> None:
        info = self._replay_info_of_players(
            sc2api_pb2.PlayerType.Participant, sc2api_pb2.PlayerType.Participant, sc2api_pb2.PlayerType.Observer
        )
        with pytest.raises(ValueError, match="the replay's players are 1 and 2, and 3 is not one of them"):
            sc2nachos.run._check_replay_player(info, 3)


class _ClientStarted(Exception):
    """Raised where a client would have started."""


@pytest.mark.parametrize(("base_build", "warned"), [(TECH_TREE.base_build, False), (TECH_TREE.base_build + 1, True)])
def test_a_local_game_on_a_build_the_tech_tree_was_not_swept_on_is_warned_of(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, base_build: int, warned: bool
) -> None:
    (tmp_path / "Versions" / f"Base{base_build}").mkdir(parents=True)
    launched: list[int | None] = []

    def launch(installation: Installation, **kwargs: Any) -> GameProcess:
        launched.append(kwargs.get("base_build"))
        raise _ClientStarted

    monkeypatch.setattr(GameProcess, "launch", launch)
    messages: list[str] = []
    sink = logger.add(messages.append, format="{message}", level="WARNING")
    try:
        with pytest.raises(_ClientStarted):
            run_local(
                MapFile(tmp_path / "Pylon.SC2Map"),
                Api(),
                race=Race.TERRAN,
                installation=Installation(tmp_path, "Linux"),
            )
    finally:
        logger.remove(sink)
    assert launched == [base_build]
    assert any("tech tree" in message for message in messages) is warned


class _ClientLaunched:
    """A game client `run_replay` launched, which records whether it was stopped."""

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
    """Which client `run_replay` starts for a replay, stopped where it would start playing."""

    _NEWEST = TECH_TREE.base_build + 1
    _OLDER = TECH_TREE.base_build

    def _run(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        *,
        installed: tuple[int, ...],
        replay_build: int,
        raising: type[Exception] = _ClientStarted,
    ) -> tuple[list[int | None], list[_ClientLaunched]]:
        """Run `run_replay` on an installation of the `installed` builds for a replay of `replay_build`, where it would
        connect to play raising `_ClientStarted`, expecting it to raise `raising`, and return the builds it launched and
        the clients."""
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
                raise _ClientStarted
            launched_clients.append(_ClientLaunched())
            return launched_clients[-1]

        def connect(url: str) -> FakeTransport:
            info = sc2api_pb2.ResponseReplayInfo(base_build=replay_build)
            info.player_info.add().player_info.CopyFrom(sc2api_pb2.PlayerInfo(player_id=1))
            return FakeTransport(make_response(Status.LAUNCHED, replay_info=info))

        def connect_to_play(url: str, record_to: Path | str | None) -> Client:
            raise _ClientStarted

        monkeypatch.setattr(GameProcess, "launch", launch)
        monkeypatch.setattr(WebSocketTransport, "connect", connect)
        monkeypatch.setattr(sc2nachos.run, "_connect", connect_to_play)
        with pytest.raises(raising):
            run_replay(replay, Api(), player_id=1, installation=Installation(tmp_path, "Linux"))
        return launched_builds, launched_clients

    def test_a_replay_of_the_newest_build_installed_plays_on_the_client_that_read_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        installed = (self._OLDER, self._NEWEST)
        launched, clients = self._run(tmp_path, monkeypatch, installed=installed, replay_build=self._NEWEST)
        assert launched == [self._NEWEST]
        assert clients[0].terminated

    def test_a_replay_of_an_older_build_installed_stops_that_client_and_starts_its_own(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        installed = (self._OLDER, self._NEWEST)
        launched, clients = self._run(tmp_path, monkeypatch, installed=installed, replay_build=self._OLDER)
        assert launched == [self._NEWEST, self._OLDER]
        assert clients[0].terminated

    def test_a_replay_of_a_build_not_installed_is_refused_once_read(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        launched, clients = self._run(
            tmp_path, monkeypatch, installed=(self._NEWEST,), replay_build=self._OLDER, raising=GameVersionError
        )
        assert launched == [self._NEWEST]
        assert clients[0].terminated


class _ReplayStartCaught:
    """A client `run_replay` would play on, which records the map sent with the replay and stops there."""

    def __init__(self) -> None:
        self.map_data_sent: list[bytes | None] = []

    def start_replay(self, replay: bytes, *, player_id: int, disable_fog: bool, map_data: bytes | None) -> None:
        self.map_data_sent.append(map_data)
        raise _ClientStarted

    def leave_game(self) -> None:
        pass

    def quit(self) -> None:
        pass

    def close(self) -> None:
        pass


class TestTheReplaysMap:
    """Which map `run_replay` sends the game with a replay, stopped where it would start playing."""

    _INSTALLED_MAP = b"the installed map"

    def _map_data_sent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replay_map_path: str) -> bytes | None:
        """What `run_replay` sends as the map of a replay whose info names its map at `replay_map_path`, on an
        installation with Pylon in a folder of its maps."""
        (tmp_path / "Versions" / f"Base{TECH_TREE.base_build}").mkdir(parents=True)
        installed_map = tmp_path / "Maps" / "AIE" / "PylonAIE_v4.SC2Map"
        installed_map.parent.mkdir(parents=True, exist_ok=True)
        installed_map.write_bytes(self._INSTALLED_MAP)
        replay = tmp_path / "game.SC2Replay"
        replay.write_bytes(b"the replay")
        client = _ReplayStartCaught()

        def connect(url: str) -> FakeTransport:
            info = sc2api_pb2.ResponseReplayInfo(base_build=TECH_TREE.base_build, local_map_path=replay_map_path)
            info.player_info.add().player_info.CopyFrom(sc2api_pb2.PlayerInfo(player_id=1))
            return FakeTransport(make_response(Status.LAUNCHED, replay_info=info))

        monkeypatch.setattr(GameProcess, "launch", lambda installation, **kwargs: _ClientLaunched())
        monkeypatch.setattr(WebSocketTransport, "connect", connect)
        monkeypatch.setattr(sc2nachos.run, "_connect", lambda url, record_to: client)
        with pytest.raises(_ClientStarted):
            run_replay(replay, Api(), player_id=1, installation=Installation(tmp_path, "Linux"))
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


@pytest.mark.integration
def test_in_a_real_game_joined_from_the_ladders_command_line_the_opponent_replay_and_recording_are_read(
    tmp_path: Path,
) -> None:
    """Run with `pytest -m integration`. Plays a second of a game the bot leaves, joined as a ladder's bot joins, then
    plays it back exactly from its recording."""
    try:
        game_map = MapFile.find(_LADDER_MAP)
    except MapNotFoundError as missing:
        pytest.skip(str(missing))
    api = Api()

    @api.on(TurnEvent)
    def give_up(event: TurnEvent) -> None:
        if event.time.steps >= 16:
            api.leave_game()

    port = free_port()
    replay, record = tmp_path / "game.SC2Replay", tmp_path / "game.sc2rec"
    result_file = tmp_path / "result.json"
    with GameProcess.launch(port=port, window=(640, 480)) as game:
        # The ladder creates the match and disconnects; the client serves one connection at a time.
        with closing(Client(WebSocketTransport.connect(game.url))) as ladder:
            ladder.create_game(game_map.path, [Participant(), Computer(Race.ZERG, Difficulty.VERY_EASY)])
        argv = ["--GamePort", str(port), "--OpponentId", "5a1f", "--ReplayFile", str(replay)]
        argv += ["--ResultFile", str(result_file), "--RecordFile", str(record)]
        result = run_from_command_line(api, argv, race=Race.TERRAN, steps_per_turn=8)
        left_at_step = api.step

        with closing(WebSocketTransport.connect(game.url)) as transport:
            request = sc2api_pb2.Request(replay_info=sc2api_pb2.RequestReplayInfo(replay_data=replay.read_bytes()))
            read_back = transport.request(request).replay_info

    assert result is Result.DEFEAT
    assert api.enemy.ladder_id == "5a1f"
    written = json.loads(result_file.read_text(encoding="utf-8"))
    assert (written["result"], written["map"]) == ("DEFEAT", api.map.name)
    assert not read_back.HasField("error")
    assert read_back.game_duration_loops >= 8
    assert [entry.player_info.player_id for entry in read_back.player_info] == [1, 2]

    replayed = tmp_path / "replayed.SC2Replay"
    assert run_recording(record, api, enemy_ladder_id="5a1f", save_replay_to=replayed) is Result.DEFEAT
    assert (api.step, api.enemy.ladder_id) == (left_at_step, "5a1f")
    assert replayed.read_bytes() == replay.read_bytes()


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
        time_limit=GameTime(seconds=20),
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
        time_limit=GameTime(seconds=5),
        save_replay_to=replay,
    )
    assert played is Result.TIE
    played_on.unlink()

    api = Api()
    assert run_replay(replay, api, player_id=1, steps_per_turn=8) is Result.UNDECIDED
    assert api.step > 0


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
        time_limit=GameTime(seconds=100),
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
