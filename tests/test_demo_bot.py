"""The demo bot in examples/demo_bot.py."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from sc2nachos import Api, Computer, Difficulty, GameTime, Race, Result, run_local
from sc2nachos.launch import MapFile, MapNotFoundError

_DEMO_BOT = Path(__file__).parent.parent / "examples" / "demo_bot.py"


def _load_demo_bot() -> ModuleType:
    """The demo bot's module, loaded afresh, with an api of its own."""
    spec = importlib.util.spec_from_file_location("demo_bot", _DEMO_BOT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_demo_bot_loads_without_a_game() -> None:
    assert isinstance(_load_demo_bot().api, Api)


@pytest.mark.integration
def test_the_demo_bot_beats_the_computer_at_medium() -> None:
    """Run with `pytest -m integration`. Plays a whole game, about half a minute."""
    try:
        game_map = MapFile.find("PylonAIE_v4")
    except MapNotFoundError as missing:
        pytest.skip(str(missing))
    demo_bot = _load_demo_bot()
    result = run_local(
        game_map,
        demo_bot.api,
        Computer(Race.ZERG, Difficulty.MEDIUM),
        race=Race.TERRAN,
        time_limit=GameTime(seconds=900),
        window=(640, 480),
    )
    assert result is Result.VICTORY
