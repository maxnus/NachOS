"""Playing a game on a client this library starts, or on one a ladder started."""

import json
import random
from argparse import ArgumentParser
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from sc2nachos.api import Api
from sc2nachos.launch import GameProcess, Installation, MapFile, MapNotFoundError
from sc2nachos.match import AIBuild, Computer, Difficulty, Participant, Player, Race, Result
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


def run_from_command_line(bot: ApiBot, argv: Sequence[str] | None = None) -> Result:
    """Play one game against the built-in computer as the command line asks, and write how it went.

    This is the game script NachOS's match workflow runs, `.github/workflows/match.yml`: the arguments are the ones it
    passes, `--opponent-race`, `--opponent-difficulty` and `--opponent-build` as member names of `Race`, `Difficulty`
    and `AIBuild` (`ZERG`, `VERY_EASY`, `RANDOM`), `--map`, `--time-limit` in game seconds, and `--result-file`, which
    gets the JSON the workflow reads. Without `--map`, a map is picked at random from the installation's. `argv`
    defaults to `sys.argv[1:]`.
    """
    args = _command_line().parse_args(argv)
    if args.replay_file is not None:
        logger.warning("No replay is saved: --replay-file is accepted, not yet supported")
    installation = Installation.find()
    if args.map is not None:
        map_file = MapFile.find(args.map, installation=installation)
    else:
        names = sorted({path.stem for path in installation.maps.rglob("*.SC2Map")})
        if not names:
            raise MapNotFoundError(f"{installation.maps} holds no maps to pick from")
        map_file = MapFile.find(random.choice(names), installation=installation)
    opponent = Computer(Race[args.opponent_race], Difficulty[args.opponent_difficulty], AIBuild[args.opponent_build])
    result = run_local(map_file, bot, opponent, time_limit=args.time_limit, installation=installation)
    if args.result_file is not None:
        outcome = {
            "result": result.name,
            "map": map_file.name,
            "game_time": round(bot.api.time, 3),
        }
        args.result_file.write_text(json.dumps(outcome), encoding="utf-8")
    return result


def _command_line() -> ArgumentParser:
    """The arguments `run_from_command_line` reads, with the enums' member names as the choices."""
    races = [race.name for race in Race if race is not Race.NONE]
    parser = ArgumentParser(description="Play one game against the built-in computer and write how it went.")
    parser.add_argument("--opponent-race", choices=races, default=Race.RANDOM.name)
    parser.add_argument(
        "--opponent-difficulty", choices=[d.name for d in Difficulty], default=Difficulty.VERY_HARD.name
    )
    parser.add_argument("--opponent-build", choices=[b.name for b in AIBuild], default=AIBuild.RANDOM.name)
    parser.add_argument("--map", help="a map under the installation's maps folder (default: one at random)")
    parser.add_argument("--time-limit", type=float, help="end the game as a tie after this many game seconds")
    parser.add_argument("--result-file", type=Path, help="where to write the result, as JSON")
    parser.add_argument("--replay-file", type=Path, help="accepted for the match workflow; no replay is saved yet")
    return parser


def _connect(url: str, record_to: Path | None) -> Client:
    """A client connected to the game at `url`, recording the whole conversation to `record_to` if given."""
    transport: Transport = WebSocketTransport.connect(url)
    if record_to is not None:
        transport = RecordingTransport(transport, record_to)
    return Client(transport)
