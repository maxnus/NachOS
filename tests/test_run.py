import json
from pathlib import Path
from typing import Any

import pytest

import sc2nachos.run
from sc2nachos import AIBuild, Api, ApiBot, Computer, Difficulty, Race, Result, run_from_command_line
from sc2nachos.launch import Installation, MapFile, MapNotFoundError


@pytest.fixture
def played(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """What `run_local` was asked to play, with an installation holding two maps and no game started."""
    for name in ("PylonAIE_v4", "TorchesAIE_v4"):
        (tmp_path / "maps" / "AIE").mkdir(parents=True, exist_ok=True)
        (tmp_path / "maps" / "AIE" / f"{name}.SC2Map").write_bytes(b"")
    monkeypatch.setenv("SC2PATH", str(tmp_path))
    calls: dict[str, Any] = {}

    def run_local(map_file: MapFile, bot: ApiBot, opponent: Computer, **kwargs: Any) -> Result:
        calls.update(map_file=map_file, bot=bot, opponent=opponent, **kwargs)
        return Result.VICTORY

    monkeypatch.setattr(sc2nachos.run, "run_local", run_local)
    monkeypatch.setattr(Api, "time", property(lambda self: 754.3841))
    return calls


def test_the_arguments_are_read_in_the_games_own_spelling(played: dict[str, Any]) -> None:
    bot = ApiBot(Api(), Race.TERRAN)
    args = ["--opponent-race", "Zerg", "--opponent-difficulty", "VeryEasy", "--opponent-build", "RandomBuild"]
    result = run_from_command_line(bot, [*args, "--map", "torchesaie_v4", "--time-limit", "0"])
    assert result is Result.VICTORY
    assert played["opponent"] == Computer(Race.ZERG, Difficulty.VERY_EASY, AIBuild.RANDOM)
    assert played["map_file"].name == "TorchesAIE_v4"
    assert played["time_limit"] == 0
    assert played["bot"] is bot


def test_without_a_map_one_of_the_installations_is_picked(played: dict[str, Any]) -> None:
    run_from_command_line(ApiBot(Api(), Race.TERRAN), [])
    assert played["map_file"].name in {"PylonAIE_v4", "TorchesAIE_v4"}
    assert played["time_limit"] is None
    assert played["opponent"] == Computer(Race.RANDOM, Difficulty.VERY_HARD, AIBuild.RANDOM)


def test_the_result_file_is_what_the_match_workflow_reads(played: dict[str, Any], tmp_path: Path) -> None:
    result_file = tmp_path / "result.json"
    run_from_command_line(ApiBot(Api(), Race.TERRAN), ["--map", "PylonAIE_v4", "--result-file", str(result_file)])
    assert json.loads(result_file.read_text(encoding="utf-8")) == {
        "result": "Victory",
        "map": "PylonAIE_v4",
        "game_time": 754.384,
    }


def test_a_name_outside_the_games_spelling_is_refused(played: dict[str, Any]) -> None:
    with pytest.raises(SystemExit):
        run_from_command_line(ApiBot(Api(), Race.TERRAN), ["--opponent-difficulty", "VERY_EASY"])
    assert not played


def test_an_installation_without_maps_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SC2PATH", str(tmp_path))
    (tmp_path / "Maps").mkdir()
    assert Installation.find().maps == tmp_path / "Maps"
    with pytest.raises(MapNotFoundError, match="no maps"):
        run_from_command_line(ApiBot(Api(), Race.TERRAN), [])
