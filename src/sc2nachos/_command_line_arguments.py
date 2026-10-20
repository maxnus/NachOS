"""The arguments `run_from_command_line` reads from the command line."""

from argparse import ArgumentParser
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import ClassVar, final

from loguru import logger

from sc2nachos.match import AIBuild, Computer, Difficulty, Race


@final
class _CommandLineArguments:
    """The arguments of one run: a ladder's game when they give `--GamePort`, else a local one against the built-in
    computer. None is read from a prefix of its name, and those NachOS does not read are left for the bot."""

    __slots__ = ("_arguments", "_not_read", "_parser")

    # The options only a ladder game reads and those only a local game reads. Each is read as its name without the
    # dashes. `--ComputerRace` and `--ComputerDifficulty` are in neither: a ladder passes them for its game against the
    # computer, and a local game reads them.
    _LADDER_GAME_OPTIONS: ClassVar[tuple[str, ...]] = (
        "--LadderServer",
        "--StartPort",
        "--OpponentId",
        "--ComputerOpponent",
    )
    _LOCAL_GAME_OPTIONS: ClassVar[tuple[str, ...]] = ("--ComputerBuild", "--Map", "--TimeLimit")

    def __init__(self, argv: Sequence[str] | None) -> None:
        """Read `argv`, by default `sys.argv[1:]`."""
        self._parser = self._argument_parser()
        self._arguments, self._not_read = self._parser.parse_known_args(argv)

    @property
    def is_ladder_game(self) -> bool:
        """Whether the game is a ladder's, which the ladder's `--GamePort` says."""
        return self._arguments.GamePort is not None

    @property
    def ladder_server(self) -> str:
        """The host of the ladder's game client, 127.0.0.1 if not given."""
        return self._arguments.LadderServer

    @property
    def game_port(self) -> int:
        """The port of the ladder's game client. Raises `ValueError` outside a ladder game."""
        if (port := self._arguments.GamePort) is None:
            raise ValueError("a local game has no ladder's game port")
        return port

    @property
    def start_port(self) -> int | None:
        """The first of the ports the ladder handed out, which a ladder game against the computer does without."""
        return self._arguments.StartPort

    @property
    def opponent_id(self) -> str | None:
        """The ladder's id for the opponent."""
        return self._arguments.OpponentId

    @property
    def map_name(self) -> str | None:
        """The map a local game is played on, `None` for one at random."""
        return self._arguments.Map

    @property
    def time_limit(self) -> float | None:
        """The game seconds after which a local game ends as a tie."""
        return self._arguments.TimeLimit

    @property
    def realtime(self) -> bool:
        """Whether the game is played in real time."""
        return self._arguments.RealTime

    @property
    def result_file(self) -> Path | None:
        """Where to write how the game went, as JSON."""
        return self._arguments.ResultFile

    @property
    def replay_file(self) -> Path | None:
        """Where to save the game's replay."""
        return self._arguments.ReplayFile

    def create_computer_opponent(self) -> Computer:
        """The computer a local game is played against, from `--ComputerRace`, `--ComputerDifficulty` and
        `--ComputerBuild` as member names. A name that is no member stops the run, as an argument argparse refuses
        does."""
        return Computer(
            self._named_member(
                "--ComputerRace",
                self._arguments.ComputerRace,
                {race.name: race for race in Race if race is not Race.NONE},
            ),
            self._named_member(
                "--ComputerDifficulty",
                self._arguments.ComputerDifficulty,
                {difficulty.name: difficulty for difficulty in Difficulty},
            ),
            self._named_member(
                "--ComputerBuild", self._arguments.ComputerBuild, {build.name: build for build in AIBuild}
            ),
        )

    def log_arguments_not_used(self) -> None:
        """Log the arguments NachOS does not read, which are left for the bot, and warn of those given to the other kind
        of game, which it ignores."""
        if self._not_read:
            logger.info("Not NachOS's arguments, left to the bot: {}", " ".join(self._not_read))
        if self.is_ladder_game:
            self._warn_of_ignored(self._LOCAL_GAME_OPTIONS, "a ladder game")
        else:
            self._warn_of_ignored(self._LADDER_GAME_OPTIONS, "a local game")

    def _named_member[M](self, option: str, name: str, members_by_name: Mapping[str, M]) -> M:
        """The member `name` names among `members_by_name`, or the run stopped, naming `option` and the names it
        takes."""
        if (member := members_by_name.get(name)) is None:
            self._parser.error(
                f"argument {option}: invalid choice: {name!r} (choose from {', '.join(members_by_name)})"
            )
        return member

    def _warn_of_ignored(self, options: Iterable[str], game: str) -> None:
        """Warn of each of `options` given other than its default, which `game` ignores."""
        if given := [option for option in options if self._given_other_than_its_default(option.removeprefix("--"))]:
            logger.warning("{} ignores {}", game.capitalize(), ", ".join(given))

    def _given_other_than_its_default(self, name: str) -> bool:
        """Whether the argument read as `name` was given, other than its default."""
        return getattr(self._arguments, name) != self._parser.get_default(name)

    @staticmethod
    def _argument_parser() -> ArgumentParser:
        """The parser of the arguments read, none of them from a prefix of its name."""
        parser = ArgumentParser(
            description="Play one game, a ladder's or one against the built-in computer.", allow_abbrev=False
        )
        parser.add_argument("--LadderServer", default="127.0.0.1", help="the ladder's game client's host (%(default)s)")
        parser.add_argument("--GamePort", type=int, help="the ladder's game client's port; a ladder game when given")
        parser.add_argument("--StartPort", type=int, help="the first of the ports the ladder handed out")
        parser.add_argument("--OpponentId", help="the ladder's id for the opponent")
        parser.add_argument(
            "--ComputerOpponent", help="passed by the ladder for its game against the computer; not used"
        )
        # Read as member names in a local game, and checked there alone: what the ladder passes in them was not checked.
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
