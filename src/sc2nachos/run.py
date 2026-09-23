"""Playing a game on a client this library starts, or on one a ladder started."""

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from sc2nachos.api import Api
from sc2nachos.launch import GameProcess, Installation, MapFile
from sc2nachos.match import Computer, Participant, Player, Race, Result
from sc2nachos.protocol import Client, GamePorts, RecordingTransport, Transport, WebSocketTransport


@dataclass(frozen=True, slots=True)
class ApiBot:
    """A player driven by an `Api`, with its race and name. The other kind of player is a `Computer`."""

    api: Api
    race: Race
    name: str | None = None


def run_local(
    map_file: MapFile | str,
    bot: ApiBot,
    opponent: Computer | None = None,
    *,
    steps_per_turn: int = 1,
    realtime: bool = False,
    time_limit: float | None = None,
    random_seed: int | None = None,
    record_to: Path | None = None,
    installation: Installation | None = None,
    window: tuple[int, int] = (1024, 768),
) -> Result:
    """Start a game client, create a match on `map_file` for `bot` against `opponent`, and play it out.

    `map_file` is a file or the name of one under the installation. The bot takes the first slot, and without an
    `opponent` it plays the map alone. However the game ends, the client is stopped and its temporary directory
    removed.
    """
    if isinstance(map_file, str):
        installation = installation or Installation.find()
        map_file = MapFile.find(map_file, installation=installation)
    # A participant tells the game a client will fill the slot; who fills it is settled at the join.
    players: list[Player] = [Participant()] if opponent is None else [Participant(), opponent]

    with GameProcess.launch(installation, window=window) as game, closing(_connect(game.url, record_to)) as client:
        try:
            client.create_game(map_file.path, players, realtime=realtime, random_seed=random_seed)
            client.join_game(bot.race, name=bot.name)
            return bot.api.play(client, steps_per_turn=steps_per_turn, realtime=realtime, time_limit=time_limit)
        finally:
            client.leave_game()
            client.quit()


def run_ladder(
    bot: ApiBot,
    *,
    host: str,
    port: int,
    start_port: int | None = None,
    steps_per_turn: int = 1,
    realtime: bool = False,
    record_to: Path | None = None,
) -> Result:
    """Join the game a ladder has set up, and play it out.

    The ladder starts the client, creates the match, and passes the bot the client's address and `start_port` on
    the command line. A game against the built-in computer has no `start_port`. There is no time limit: a bot can
    only end a game early by leaving it, which concedes.
    """
    with closing(_connect(f"ws://{host}:{port}/sc2api", record_to)) as client:
        try:
            ports = GamePorts.from_start_port(start_port) if start_port is not None else None
            client.join_game(bot.race, name=bot.name, ports=ports)
            return bot.api.play(client, steps_per_turn=steps_per_turn, realtime=realtime)
        finally:
            # The client is the ladder's, so it is left running.
            client.leave_game()


def _connect(url: str, record_to: Path | None) -> Client:
    """A client connected to the game at `url`, recording the whole conversation to `record_to` if given."""
    transport: Transport = WebSocketTransport.connect(url)
    if record_to is not None:
        transport = RecordingTransport(transport, record_to)
    return Client(transport)
