"""Fixtures shared by the tests."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from loguru import logger

from sc2nachos.gamedata import GameData
from sc2nachos.protocol import Recording

_FIRST_CORPUS_GAME = sorted((Path(__file__).parent / "corpus").glob("*.sc2rec"))[0]


@pytest.fixture(scope="session")
def first_corpus_tables() -> GameData:
    """The game data of the first corpus game."""
    recording = Recording(_FIRST_CORPUS_GAME)
    return GameData(next(exchange.response.data for exchange in recording if exchange.response.HasField("data")))


@pytest.fixture
def logged() -> Iterator[list[str]]:
    """What is logged while the test runs, from info up, each message without its line end."""
    messages: list[str] = []
    sink = logger.add(lambda message: messages.append(message.rstrip("\n")), format="{message}", level="INFO")
    yield messages
    logger.remove(sink)
