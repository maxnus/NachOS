"""Playing a game on a client this library starts, or on one a ladder started."""

import json
import random
from argparse import ArgumentParser, Namespace
from collections.abc import Iterable, Sequence
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from sc2nachos._checks import check_file_to_write
from sc2nachos.api import Api
from sc2nachos.gamedata._techtree import TECH_TREE
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
    record_to: Path | str | None = None,
    replay_to: Path | str | None = None,
    installation: Installation | None = None,
    window: tuple[int, int] = (1024, 768),
) -> Result:
    """Start a game client, create a match on `map_file` for `bot` against `opponent`, and play it out.

    `map_file` is a file or the name of one under the installation. The bot takes the first slot, and without an
    `opponent` it plays the map alone. `realtime` creates the game in real time and plays it so. `record_to` is a file
    to record the whole conversation into, and `replay_to` one to save the game's replay to, as `Api.play` says. Each
    must be in a folder that exists, or `FileNotFoundError` is raised before the game starts, and `IsADirectoryError`
    if it names a folder. However the game ends, the client is stopped and its temporary directory removed. The newest
    build installed is played, with a warning if it is not the one the tech tree was swept on.
    """
    _check_output_files(record_to, replay_to)
    installation = installation or Installation.find()
    if isinstance(map_file, str):
        map_file = MapFile.find(map_file, installation=installation)
    # A participant tells the game a client will fill the slot; who fills it is settled at the join.
    players: list[Player] = [Participant()] if opponent is None else [Participant(), opponent]
    base_build = installation.build_to_launch()
    _check_tech_tree_build(base_build)

    with (
        GameProcess.launch(installation, base_build=base_build, window=window) as game,
        closing(_connect(game.url, record_to)) as client,
    ):
        try:
            client.create_game(map_file.path, players, realtime=realtime, random_seed=random_seed)
            client.join_game(bot.race, name=bot.name)
            return bot.api.play(
                client, steps_per_turn=steps_per_turn, realtime=realtime, time_limit=time_limit, replay_to=replay_to
            )
        finally:
            client.leave_game()
            client.quit()


def run_ladder(
    bot: ApiBot,
    *,
    host: str,
    port: int,
    start_port: int | None = None,
    enemy_ladder_id: str | None = None,
    steps_per_turn: int = 1,
    realtime: bool = False,
    record_to: Path | str | None = None,
    replay_to: Path | str | None = None,
) -> Result:
    """Join the game a ladder has set up, and play it out.

    The ladder starts the client, creates the match, and passes the bot the client's address, `start_port` and the
    opponent's `enemy_ladder_id` on the command line. A game against the built-in computer has no `start_port`.
    `realtime` must match the ladder's `--RealTime` flag, as `Api.play` says. There is no time limit: a bot can only
    end a game early by leaving it, which concedes. `record_to` and `replay_to` are as for `run_local`.
    """
    _check_output_files(record_to, replay_to)
    with closing(_connect(f"ws://{host}:{port}/sc2api", record_to)) as client:
        try:
            ports = GamePorts.from_start_port(start_port) if start_port is not None else None
            client.join_game(bot.race, name=bot.name, ports=ports)
            return bot.api.play(
                client,
                steps_per_turn=steps_per_turn,
                realtime=realtime,
                enemy_ladder_id=enemy_ladder_id,
                replay_to=replay_to,
            )
        finally:
            # The client is the ladder's, so it is left running.
            client.leave_game()


def run_from_command_line(bot: ApiBot, argv: Sequence[str] | None = None, *, steps_per_turn: int = 1) -> Result:
    """Play one game as the command line asks, a ladder's or a local one against the built-in computer, a turn every
    `steps_per_turn` steps, and write how it went.

    With `--GamePort` it joins the game a ladder has set up, as `run_ladder` does, and reads the ladder's arguments:
    `--LadderServer` (127.0.0.1 if not given), `--GamePort`, `--StartPort`, `--OpponentId`, which `Enemy.ladder_id`
    reads, and `--RealTime`. It takes the ladder's `--ComputerOpponent`, `--ComputerRace` and `--ComputerDifficulty`
    and uses none of them, since the ladder has created that game.

    Without it, it plays a local game against the built-in computer, as NachOS's match workflow,
    `.github/workflows/match.yml`, asks: `--opponent-race`, `--opponent-difficulty` and `--opponent-build` as member
    names of `Race`, `Difficulty` and `AIBuild` (`ZERG`, `VERY_EASY`, `RANDOM`; by default `RANDOM`, `VERY_HARD` and
    `RANDOM`), `--map`, by default one at random from the installation's, `--time-limit` in game seconds, and
    `--realtime`.

    In either, `--replay-file` is a file to save the game's replay to, and `--result-file` one to write the JSON the
    match workflow reads: the result, the map, as its file's name in a local game and as its display name in a ladder
    game, and the game time. An argument of the other kind of game is logged as ignored, and one NachOS does not read
    is left for the bot, listed in the log. `argv` defaults to `sys.argv[1:]`.
    """
    args, not_read = _command_line().parse_known_args(argv)
    if not_read:
        logger.info("Not NachOS's arguments, left to the bot: {}", " ".join(not_read))
    if args.GamePort is not None:
        result, map_name = _play_ladder_game(bot, args, steps_per_turn=steps_per_turn)
    else:
        result, map_name = _play_local_game(bot, args, steps_per_turn=steps_per_turn)
    if args.result_file is not None:
        outcome = {
            "result": result.name,
            "map": map_name,
            "game_time": round(bot.api.time, 3),
        }
        args.result_file.write_text(json.dumps(outcome), encoding="utf-8")
    return result


# The arguments that only a ladder game reads and those that only a local game does, each by its name on the parsed
# arguments and its option.
_LADDER_GAME_ARGUMENTS = (
    ("LadderServer", "--LadderServer"),
    ("StartPort", "--StartPort"),
    ("OpponentId", "--OpponentId"),
    ("RealTime", "--RealTime"),
    ("ComputerOpponent", "--ComputerOpponent"),
    ("ComputerRace", "--ComputerRace"),
    ("ComputerDifficulty", "--ComputerDifficulty"),
)
_LOCAL_GAME_ARGUMENTS = (
    ("opponent_race", "--opponent-race"),
    ("opponent_difficulty", "--opponent-difficulty"),
    ("opponent_build", "--opponent-build"),
    ("map", "--map"),
    ("time_limit", "--time-limit"),
    ("realtime", "--realtime"),
)


def _play_ladder_game(bot: ApiBot, args: Namespace, *, steps_per_turn: int) -> tuple[Result, str]:
    """Join the ladder's game as `args` say, and return how it ended and the map's display name."""
    _log_ignored_arguments(args, _LOCAL_GAME_ARGUMENTS, "a ladder game")
    result = run_ladder(
        bot,
        host=args.LadderServer or "127.0.0.1",
        port=args.GamePort,
        start_port=args.StartPort,
        enemy_ladder_id=args.OpponentId,
        steps_per_turn=steps_per_turn,
        realtime=bool(args.RealTime),
        replay_to=args.replay_file,
    )
    return result, bot.api.map.name


def _play_local_game(bot: ApiBot, args: Namespace, *, steps_per_turn: int) -> tuple[Result, str]:
    """Play a local game against the built-in computer as `args` say, and return how it ended and the map file's
    name."""
    _log_ignored_arguments(args, _LADDER_GAME_ARGUMENTS, "a local game")
    installation = Installation.find()
    if args.map is not None:
        map_file = MapFile.find(args.map, installation=installation)
    else:
        names = MapFile._names(installation)
        if not names:
            raise MapNotFoundError(f"{installation.maps} holds no maps to pick from")
        map_file = MapFile.find(random.choice(names), installation=installation)
    opponent = Computer(
        Race.RANDOM if args.opponent_race is None else Race[args.opponent_race],
        Difficulty.VERY_HARD if args.opponent_difficulty is None else Difficulty[args.opponent_difficulty],
        AIBuild.RANDOM if args.opponent_build is None else AIBuild[args.opponent_build],
    )
    result = run_local(
        map_file,
        bot,
        opponent,
        steps_per_turn=steps_per_turn,
        realtime=bool(args.realtime),
        time_limit=args.time_limit,
        replay_to=args.replay_file,
        installation=installation,
    )
    return result, map_file.name


def _log_ignored_arguments(args: Namespace, arguments: Iterable[tuple[str, str]], game: str) -> None:
    """Log a warning naming each of `arguments` given in `args`, which `game` ignores."""
    if given := [option for name, option in arguments if getattr(args, name) is not None]:
        logger.warning("{} ignores {}", game.capitalize(), ", ".join(given))


def _command_line() -> ArgumentParser:
    """The arguments `run_from_command_line` reads, with the enums' member names as the choices. Each reads `None`
    when not given."""
    # A flag's default too, to tell a flag given to the other kind of game.
    races = [race.name for race in Race if race is not Race.NONE]
    parser = ArgumentParser(description="Play one game, a ladder's or one against the built-in computer.")
    parser.add_argument("--LadderServer", help="the ladder's game client's host (default: 127.0.0.1)")
    parser.add_argument("--GamePort", type=int, help="the ladder's game client's port; a ladder game when given")
    parser.add_argument("--StartPort", type=int, help="the first of the ports the ladder handed out")
    parser.add_argument("--OpponentId", help="the ladder's id for the opponent")
    parser.add_argument("--RealTime", action="store_true", default=None, help="the ladder plays in real time")
    for unused in ("--ComputerOpponent", "--ComputerRace", "--ComputerDifficulty"):
        parser.add_argument(unused, help="passed by the ladder, which creates that game itself; not used")
    parser.add_argument("--opponent-race", choices=races, help="the computer's race (default: RANDOM)")
    parser.add_argument(
        "--opponent-difficulty", choices=[d.name for d in Difficulty], help="the computer's (default: VERY_HARD)"
    )
    parser.add_argument(
        "--opponent-build", choices=[b.name for b in AIBuild], help="the computer's build (default: RANDOM)"
    )
    parser.add_argument("--map", help="a map under the installation's maps folder (default: one at random)")
    parser.add_argument("--time-limit", type=float, help="end the game as a tie after this many game seconds")
    parser.add_argument("--realtime", action="store_true", default=None, help="play the local game in real time")
    parser.add_argument("--result-file", type=Path, help="where to write the result, as JSON")
    parser.add_argument("--replay-file", type=Path, help="where to save the game's replay")
    return parser


def _check_tech_tree_build(base_build: int) -> None:
    """Log a warning if `base_build` is not the tech tree's."""
    if base_build != TECH_TREE.base_build:
        logger.warning(
            "Playing build {}, but the tech tree is build {}'s: what each ability needs, who is offered it, how it "
            "takes a unit's orders and what each upgrade changes come from that build",
            base_build,
            TECH_TREE.base_build,
        )


def _check_output_files(record_to: Path | str | None, replay_to: Path | str | None) -> None:
    """Raise `FileNotFoundError` or `IsADirectoryError`, before a game starts, for a file to record into or to save
    the replay into that cannot be written."""
    if record_to is not None:
        check_file_to_write(Path(record_to), "record into")
    if replay_to is not None:
        check_file_to_write(Path(replay_to), "save the replay into")


def _connect(url: str, record_to: Path | str | None) -> Client:
    """A client connected to the game at `url`, recording the whole conversation to `record_to` if given. A recording
    that fails to open closes the connection."""
    transport: Transport = WebSocketTransport.connect(url)
    if record_to is not None:
        try:
            transport = RecordingTransport(transport, record_to)
        except BaseException:
            transport.close()
            raise
    return Client(transport)
