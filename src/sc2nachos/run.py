"""Playing a game, on a client this library starts or one a ladder has already started."""

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from sc2nachos.api import Api
from sc2nachos.launch import GameProcess, Installation, Map
from sc2nachos.match import Computer, Participant, Player, Race, Result
from sc2nachos.protocol import Client, GamePorts, RecordingTransport, Transport, WebSocketTransport


@dataclass(frozen=True, slots=True)
class ApiBot:
    """A player driven by an api of ours, as which race and under what name.

    The other kind of player is a `Computer`, which the game drives itself and which needs no client.
    """

    api: Api
    race: Race
    name: str | None = None


def run_local(
    game_map: Map | str,
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
    """Start a game client, create a match on `game_map` for `bot` against `opponent`, and play it out.

    The bot takes the first slot, and without an `opponent` it plays the map alone. The client is stopped and
    its temporary directory removed however the game ends.
    """
    if isinstance(game_map, str):
        installation = installation or Installation.find()
        game_map = Map.find(game_map, installation=installation)
    # A participant tells the game that a client will fill the slot; who fills it is settled at the join.
    players: list[Player] = [Participant()] if opponent is None else [Participant(), opponent]

    with GameProcess.launch(installation, window=window) as game, closing(_connect(game.url, record_to)) as client:
        try:
            client.create_game(game_map.path, players, realtime=realtime, random_seed=random_seed)
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
    """Join the game a ladder has already set up, and play it out.

    The ladder starts the client and creates the match, then hands the bot its address and `start_port` on the
    command line. A game against the built-in computer has no `start_port`, since nobody else is joining.
    There is no time limit: the only way a bot can end a game early is to leave it, which concedes it.
    """
    with closing(_connect(f"ws://{host}:{port}/sc2api", record_to)) as client:
        try:
            ports = GamePorts.from_start_port(start_port) if start_port is not None else None
            client.join_game(bot.race, name=bot.name, ports=ports)
            return bot.api.play(client, steps_per_turn=steps_per_turn, realtime=realtime)
        finally:
            # The ladder owns the client it started, so it is left running to be told what to do next.
            client.leave_game()


def _connect(url: str, record_to: Path | None) -> Client:
    """A client talking to the game at `url`, writing the whole conversation down if asked to."""
    transport: Transport = WebSocketTransport.connect(url)
    if record_to is not None:
        transport = RecordingTransport(transport, record_to)
    return Client(transport)
