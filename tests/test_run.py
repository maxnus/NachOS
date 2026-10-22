import json
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from loguru import logger
from s2clientprotocol import sc2api_pb2

import sc2nachos.run
from sc2nachos import AIBuild, Api, Computer, Difficulty, Race, Result, run_from_command_line, run_local, run_recording
from sc2nachos._command_line_arguments import _CommandLineArguments
from sc2nachos.events import TurnEvent
from sc2nachos.gamedata._techtree import TECH_TREE
from sc2nachos.launch import GameProcess, Installation, MapFile, MapNotFoundError, free_port
from sc2nachos.match import Participant
from sc2nachos.protocol import Client, ConnectionClosedError, Recording, RecordingTransport, WebSocketTransport
from support import FakeTransport

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
    monkeypatch.setattr(Api, "time", property(lambda self: 754.3841))
    return calls


@pytest.fixture
def ladder_played(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """What `run_ladder` was asked to play, with no game joined."""
    calls: dict[str, Any] = {}

    def run_ladder(api: Api, **kwargs: Any) -> Result:
        calls.update(api=api, **kwargs)
        return Result.DEFEAT

    monkeypatch.setattr(sc2nachos.run, "run_ladder", run_ladder)
    monkeypatch.setattr(Api, "time", property(lambda self: 61.25))
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
        replay = tmp_path / "game.SC2Replay"
        argv += ["--RealTime", "--ReplayFile", str(replay)]
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


def test_a_local_game_takes_the_turn_length_realtime_and_the_replay_file(
    played: dict[str, Any], tmp_path: Path
) -> None:
    replay = tmp_path / "game.SC2Replay"
    run_from_command_line(Api(), ["--RealTime", "--ReplayFile", str(replay)], race=Race.TERRAN, steps_per_turn=4)
    assert (played["steps_per_turn"], played["realtime"], played["save_replay_to"]) == (4, True, replay)


def test_by_default_a_local_game_is_stepped_and_saves_no_replay(played: dict[str, Any]) -> None:
    run_from_command_line(Api(), [], race=Race.TERRAN)
    assert (played["steps_per_turn"], played["realtime"], played["save_replay_to"]) == (1, False, None)


def test_the_arguments_are_the_enums_member_names(played: dict[str, Any]) -> None:
    api = Api()
    args = ["--ComputerRace", "ZERG", "--ComputerDifficulty", "VERY_EASY", "--ComputerBuild", "RUSH"]
    argv = [*args, "--Map", "TorchesAIE_v4", "--TimeLimit", "0"]
    result = run_from_command_line(api, argv, race=Race.TERRAN, name="MyBot")
    assert result is Result.VICTORY
    assert played["opponent"] == Computer(Race.ZERG, Difficulty.VERY_EASY, AIBuild.RUSH)
    assert played["map_file"].name == "TorchesAIE_v4"
    assert played["time_limit"] == 0
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


def _rerecorded(path: Path, *, leaving_out: str) -> Path:
    """The corpus game on Pylon recorded again at `path`, without the exchanges whose requests are `leaving_out`."""
    exchanges = [
        exchange for exchange in Recording(_PYLON_GAME) if exchange.request.WhichOneof("request") != leaving_out
    ]
    with closing(RecordingTransport(FakeTransport(*(exchange.response for exchange in exchanges)), path)) as recorder:
        for exchange in exchanges:
            recorder.request(exchange.request)
    return path


def _last_recorded_step(path: Path) -> int:
    """The step of the last observation recorded at `path`."""
    observations = [
        exchange.response.observation for exchange in Recording(path) if exchange.response.HasField("observation")
    ]
    return observations[-1].observation.game_loop


class TestPlayingARecording:
    def test_a_bot_that_leaves_is_defeated_where_it_left(self) -> None:
        api = Api()
        left_at: list[int] = []

        @api.on(TurnEvent)
        def leave(_: TurnEvent) -> None:
            if api.step >= 160 and not left_at:
                left_at.append(api.step)
                api.leave_game()

        assert run_recording(str(_PYLON_GAME), api) is Result.DEFEAT
        assert [api.step] == left_at

    def test_at_the_time_limit_it_is_a_tie(self) -> None:
        api = Api()
        assert run_recording(_PYLON_GAME, api, time_limit=60) is Result.TIE
        assert 60 <= api.time < 61

    def test_a_recording_of_a_joined_game_is_joined_without_creating_one(self, tmp_path: Path) -> None:
        joined = _rerecorded(tmp_path / "joined.sc2rec", leaving_out="create_game")
        api = Api()
        assert run_recording(joined, api) is Result.DEFEAT
        assert api.step == _last_recorded_step(joined)

    def test_a_recording_of_a_game_in_real_time_is_played_in_real_time(self, tmp_path: Path) -> None:
        realtime = _rerecorded(tmp_path / "realtime.sc2rec", leaving_out="step")
        api = Api()
        assert run_recording(realtime, api) is Result.DEFEAT
        assert api.step == _last_recorded_step(realtime)


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


@pytest.mark.integration
def test_in_a_real_game_joined_from_the_ladders_command_line_the_opponent_and_replay_are_read(tmp_path: Path) -> None:
    """Run with `pytest -m integration`. Plays a second of a game the bot leaves, joined as a ladder's bot joins."""
    try:
        game_map = MapFile.find(_LADDER_MAP)
    except MapNotFoundError as missing:
        pytest.skip(str(missing))
    api = Api()

    @api.on(TurnEvent)
    def give_up(event: TurnEvent) -> None:
        if event.step >= 16:
            api.leave_game()

    port = free_port()
    replay = tmp_path / "game.SC2Replay"
    result_file = tmp_path / "result.json"
    with GameProcess.launch(port=port, window=(640, 480)) as game:
        # The ladder creates the match and disconnects; the client serves one connection at a time.
        with closing(Client(WebSocketTransport.connect(game.url))) as ladder:
            ladder.create_game(game_map.path, [Participant(), Computer(Race.ZERG, Difficulty.VERY_EASY)])
        argv = ["--GamePort", str(port), "--OpponentId", "5a1f", "--ReplayFile", str(replay)]
        argv += ["--ResultFile", str(result_file)]
        result = run_from_command_line(api, argv, race=Race.TERRAN, steps_per_turn=8)

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
