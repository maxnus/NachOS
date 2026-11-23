"""The demo bots in examples/, a whole bot in one file for each race."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from sc2nachos import Api, Computer, Difficulty, GameTime, Race, Result, run_local
from sc2nachos.run import MapFile, MapNotFoundError

_EXAMPLES = Path(__file__).parent.parent / "examples"
# Each demo bot by its module's name, with the race it plays and the computer's it is tested against here.
_RACES_OF_THE_DEMO_BOTS = {
    "terran_demo_bot": (Race.TERRAN, Race.ZERG),
    "zerg_demo_bot": (Race.ZERG, Race.TERRAN),
}


def _load_demo_bot(name: str) -> ModuleType:
    """The demo bot's module, loaded afresh, with an api of its own."""
    spec = importlib.util.spec_from_file_location(name, _EXAMPLES / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", sorted(_RACES_OF_THE_DEMO_BOTS))
def test_a_demo_bot_loads_without_a_game(name: str) -> None:
    assert isinstance(_load_demo_bot(name).api, Api)


@pytest.mark.integration
@pytest.mark.parametrize("name", sorted(_RACES_OF_THE_DEMO_BOTS))
def test_a_demo_bot_beats_the_computer_at_medium(name: str) -> None:
    """Run with `pytest -m integration`. Plays a whole game, about a minute."""
    try:
        game_map = MapFile.find("PylonAIE_v4")
    except MapNotFoundError as missing:
        pytest.skip(str(missing))
    race, computer_race = _RACES_OF_THE_DEMO_BOTS[name]
    result = run_local(
        game_map,
        _load_demo_bot(name).api,
        Computer(computer_race, Difficulty.MEDIUM),
        race=race,
        time_limit=GameTime(seconds=900),
        window=(640, 480),
    )
    assert result is Result.VICTORY
