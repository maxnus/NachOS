"""Playing a game on a client this library starts, or on one a ladder started."""

import json
import random
from argparse import ArgumentParser, Namespace
from collections.abc import Iterable, Mapping, Sequence
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
    save_replay_to: Path | str | None = None,
    installation: Installation | None = None,
    window: tuple[int, int] = (1024, 768),
) -> Result:
    """Start a game client, create a match on `map_file` for `bot` against `opponent`, and play it out.

    `map_file` is a file or the name of one under the installation. The bot takes the first slot, and without an
    `opponent` it plays the map alone. `realtime` creates the game in real time and plays it so. `record_to` is a file
    to record the whole conversation into, and `save_replay_to` one to save the game's replay to, as `Api.play` says.
    Each must be in a folder that exists, or `FileNotFoundError` is raised before the game starts, and
    `IsADirectoryError` if it names a folder. However the game ends, the client is stopped and its temporary directory
    removed. The newest build installed is played, with a warning if it is not the one the tech tree was swept on.
    """
    _check_output_files(record_to, save_replay_to)
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
                client,
                steps_per_turn=steps_per_turn,
                realtime=realtime,
                time_limit=time_limit,
                save_replay_to=save_replay_to,
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
    save_replay_to: Path | str | None = None,
) -> Result:
    """Join the game a ladder has set up, and play it out.

    The ladder starts the client, creates the match, and passes the bot the client's address, `start_port` and the
    opponent's `enemy_ladder_id` on the command line. A game against the built-in computer has no `start_port`.
    `realtime` must match the ladder's `--RealTime` flag, as `Api.play` says. There is no time limit: a bot can only
    end a game early by leaving it, which concedes. `record_to` and `save_replay_to` are as for `run_local`.
    """
    _check_output_files(record_to, save_replay_to)
    with closing(_connect(f"ws://{host}:{port}/sc2api", record_to)) as client:
        try:
            ports = GamePorts.from_start_port(start_port) if start_port is not None else None
            client.join_game(bot.race, name=bot.name, ports=ports)
            return bot.api.play(
                client,
                steps_per_turn=steps_per_turn,
                realtime=realtime,
                enemy_ladder_id=enemy_ladder_id,
                save_replay_to=save_replay_to,
            )
        finally:
            # The client is the ladder's, so it is left running.
            client.leave_game()


def run_from_command_line(bot: ApiBot, argv: Sequence[str] | None = None, *, steps_per_turn: int = 1) -> Result:
    """Play one game as the command line asks, a ladder's or a local one against the built-in computer, a turn every
    `steps_per_turn` steps, and write how it went.

    With `--GamePort` it joins the game a ladder has set up, as `run_ladder` does, with the ladder's `--LadderServer`
    (127.0.0.1 if not given), `--StartPort` and `--OpponentId`, which `Enemy.ladder_id` reads. It takes the ladder's
    `--ComputerOpponent`, `--ComputerRace` and `--ComputerDifficulty` and uses none of them.

    Without it, it plays a local game against the built-in computer, as NachOS's match workflow,
    `.github/workflows/match.yml`, asks: `--ComputerRace`, `--ComputerDifficulty` and `--ComputerBuild` as member names
    of `Race`, `Difficulty` and `AIBuild` (`ZERG`, `VERY_EASY`, `RANDOM`; by default `RANDOM`, `VERY_HARD` and
    `RANDOM`), `--Map`, by default one at random from the installation's, and `--TimeLimit` in game seconds. A value
    that names no member stops the run, as an argument argparse refuses does.

    In either, `--RealTime` plays the game in real time, `--ReplayFile` is a file to save its replay to, and
    `--ResultFile` one to write the JSON the match workflow reads: the result, the map, as its file's name in a local
    game and as its display name in a ladder game, and the game time. An argument of the other kind of game is logged
    as ignored, and one NachOS does not read is left for the bot, listed in the log. `argv` defaults to
    `sys.argv[1:]`.
    """
    parser = _command_line()
    args, not_read = parser.parse_known_args(argv)
    if not_read:
        logger.info("Not NachOS's arguments, left to the bot: {}", " ".join(not_read))
    if args.GamePort is not None:
        _log_ignored_arguments(parser, args, _LOCAL_GAME_OPTIONS, "a ladder game")
        result, map_name = _play_ladder_game(bot, args, steps_per_turn=steps_per_turn)
    else:
        _log_ignored_arguments(parser, args, _LADDER_GAME_OPTIONS, "a local game")
        opponent = _computer_asked_for(parser, args)
        result, map_name = _play_local_game(bot, args, opponent, steps_per_turn=steps_per_turn)
    if args.ResultFile is not None:
        outcome = {
            "result": result.name,
            "map": map_name,
            "game_time": round(bot.api.time, 3),
        }
        args.ResultFile.write_text(json.dumps(outcome), encoding="utf-8")
    return result


# The options only a ladder game reads and those only a local game reads, each by its name on the parsed arguments.
# `--ComputerRace` and `--ComputerDifficulty` are in neither: a ladder passes them for its game against the computer,
# and a local game reads them.
_LADDER_GAME_OPTIONS = (
    ("LadderServer", "--LadderServer"),
    ("StartPort", "--StartPort"),
    ("OpponentId", "--OpponentId"),
    ("ComputerOpponent", "--ComputerOpponent"),
)
_LOCAL_GAME_OPTIONS = (
    ("ComputerBuild", "--ComputerBuild"),
    ("Map", "--Map"),
    ("TimeLimit", "--TimeLimit"),
)
_RACES_BY_NAME = {race.name: race for race in Race if race is not Race.NONE}
_DIFFICULTIES_BY_NAME = {difficulty.name: difficulty for difficulty in Difficulty}
_BUILDS_BY_NAME = {build.name: build for build in AIBuild}


def _play_ladder_game(bot: ApiBot, args: Namespace, *, steps_per_turn: int) -> tuple[Result, str]:
    """Join the ladder's game as `args` say, and return how it ended and the map's display name."""
    result = run_ladder(
        bot,
        host=args.LadderServer,
        port=args.GamePort,
        start_port=args.StartPort,
        enemy_ladder_id=args.OpponentId,
        steps_per_turn=steps_per_turn,
        realtime=args.RealTime,
        save_replay_to=args.ReplayFile,
    )
    return result, bot.api.map.name


def _play_local_game(bot: ApiBot, args: Namespace, opponent: Computer, *, steps_per_turn: int) -> tuple[Result, str]:
    """Play a local game against `opponent` as `args` say, and return how it ended and the map file's name."""
    installation = Installation.find()
    if args.Map is not None:
        map_file = MapFile.find(args.Map, installation=installation)
    else:
        names = MapFile._names(installation)
        if not names:
            raise MapNotFoundError(f"{installation.maps} holds no maps to pick from")
        map_file = MapFile.find(random.choice(names), installation=installation)
    result = run_local(
        map_file,
        bot,
        opponent,
        steps_per_turn=steps_per_turn,
        realtime=args.RealTime,
        time_limit=args.TimeLimit,
        save_replay_to=args.ReplayFile,
        installation=installation,
    )
    return result, map_file.name


def _computer_asked_for(parser: ArgumentParser, args: Namespace) -> Computer:
    """The computer a local game's `args` ask for, the run stopped by `parser` for a value that names no member."""
    return Computer(
        _named_member(parser, "--ComputerRace", args.ComputerRace, _RACES_BY_NAME),
        _named_member(parser, "--ComputerDifficulty", args.ComputerDifficulty, _DIFFICULTIES_BY_NAME),
        _named_member(parser, "--ComputerBuild", args.ComputerBuild, _BUILDS_BY_NAME),
    )


def _named_member[M](parser: ArgumentParser, option: str, name: str, members_by_name: Mapping[str, M]) -> M:
    """The member `name` names among `members_by_name`, or the run stopped by `parser` naming `option` and the names
    it takes."""
    if (member := members_by_name.get(name)) is None:
        parser.error(f"argument {option}: invalid choice: {name!r} (choose from {', '.join(members_by_name)})")
    return member


def _log_ignored_arguments(
    parser: ArgumentParser, args: Namespace, options: Iterable[tuple[str, str]], game: str
) -> None:
    """Log a warning naming each of `options` that `args` holds other than `parser`'s default, which `game` ignores."""
    if given := [option for name, option in options if getattr(args, name) != parser.get_default(name)]:
        logger.warning("{} ignores {}", game.capitalize(), ", ".join(given))


def _command_line() -> ArgumentParser:
    """The arguments `run_from_command_line` reads, none of them from a prefix of its name."""
    parser = ArgumentParser(
        description="Play one game, a ladder's or one against the built-in computer.", allow_abbrev=False
    )
    parser.add_argument("--LadderServer", default="127.0.0.1", help="the ladder's game client's host (%(default)s)")
    parser.add_argument("--GamePort", type=int, help="the ladder's game client's port; a ladder game when given")
    parser.add_argument("--StartPort", type=int, help="the first of the ports the ladder handed out")
    parser.add_argument("--OpponentId", help="the ladder's id for the opponent")
    parser.add_argument("--ComputerOpponent", help="passed by the ladder for its game against the computer; not used")
    # Read, in a local game, as member names, and checked there alone: the ladder passes them in a form of its own.
    parser.add_argument("--ComputerRace", default=Race.RANDOM.name, help="the computer's race (%(default)s)")
    parser.add_argument(
        "--ComputerDifficulty", default=Difficulty.VERY_HARD.name, help="the computer's difficulty (%(default)s)"
    )
    parser.add_argument("--ComputerBuild", default=AIBuild.RANDOM.name, help="the computer's build (%(default)s)")
    parser.add_argument("--Map", help="a map under the installation's maps folder (default: one at random)")
    parser.add_argument("--TimeLimit", type=float, help="end the game as a tie after this many game seconds")
    parser.add_argument("--RealTime", action="store_true", help="play the game in real time")
    parser.add_argument("--ResultFile", type=Path, help="where to write the result, as JSON")
    parser.add_argument("--ReplayFile", type=Path, help="where to save the game's replay")
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


def _check_output_files(record_to: Path | str | None, save_replay_to: Path | str | None) -> None:
    """Raise `FileNotFoundError` or `IsADirectoryError`, before a game starts, for a file to record into or to save
    the replay into that cannot be written."""
    if record_to is not None:
        check_file_to_write(Path(record_to), "record into")
    if save_replay_to is not None:
        check_file_to_write(Path(save_replay_to), "save the replay into")


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
