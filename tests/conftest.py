"""Fixtures shared by the tests."""

import os
import socket
from collections.abc import Iterator
from pathlib import Path

import pytest
from loguru import logger

from sc2nachos.gamedata import GameData
from sc2nachos.protocol import Recording

_FIRST_CORPUS_GAME = sorted((Path(__file__).parent / "corpus").glob("*.sc2rec"))[0]
# Below the ports an OS hands out for port 0: from 32768 on Linux, 49152 on Windows.
_FIRST_WORKER_PORT = 20_000
_PORTS_PER_WORKER = 100


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


@pytest.fixture(scope="session")
def ports_of_this_worker() -> Iterator[int] | None:
    """Under pytest-xdist, the ports of this worker's own block of 100 from 20,000, `gw0`'s first; `None` otherwise."""
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if worker is None:
        return None
    first = _FIRST_WORKER_PORT + _PORTS_PER_WORKER * int(worker.removeprefix("gw"))
    return iter(range(first, first + _PORTS_PER_WORKER))


@pytest.fixture(autouse=True)
def in_game_clients_take_the_workers_ports(
    request: pytest.FixtureRequest, ports_of_this_worker: Iterator[int] | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under pytest-xdist, a client an in-game test starts listens on the next free port of its worker's block, as
    does one on a port the test takes from `free_port` itself."""
    if ports_of_this_worker is None or request.node.get_closest_marker("integration") is None:
        return
    ports = ports_of_this_worker

    def next_free_port_of_the_worker() -> int:
        return next(port for port in ports if _nothing_listens_on(port))

    monkeypatch.setattr("sc2nachos.run._game_process.free_port", next_free_port_of_the_worker)
    if hasattr(request.module, "free_port"):
        monkeypatch.setattr(request.module, "free_port", next_free_port_of_the_worker)


def _nothing_listens_on(port: int) -> bool:
    """Whether `port` on the loopback interface can be bound now."""
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True
