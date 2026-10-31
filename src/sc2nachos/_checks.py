"""Checks that the arguments of several modules share."""

from numbers import Integral
from pathlib import Path

from sc2nachos.game_time import GameTime


def is_whole_number(value: object) -> bool:
    """Whether `value` is a whole number, and not a `bool`."""
    return isinstance(value, Integral) and not isinstance(value, bool)


def check_game_time(value: object, argument_name: str) -> None:
    """Raise `TypeError` unless `value`, given as `argument_name`, is a `GameTime`."""
    if not isinstance(value, GameTime):
        raise TypeError(f"{argument_name} is a GameTime, not {value!r}")


def check_file_to_write(path: Path, purpose: str) -> None:
    """Raise `FileNotFoundError` if `path` is in no folder that exists, and `IsADirectoryError` if it is a folder. Each
    message ends with `purpose`, what the file is for: "record into"."""
    if not path.parent.is_dir():
        raise FileNotFoundError(f"there is no folder {path.parent} to {purpose}")
    if path.is_dir():
        raise IsADirectoryError(f"{path} is a folder, not a file to {purpose}")


def check_output_files(record_to: Path | str | None, save_replay_to: Path | str | None) -> None:
    """Raise `FileNotFoundError` or `IsADirectoryError`, before a game starts, for a file to record into or to save
    the replay into that cannot be written."""
    if record_to is not None:
        check_file_to_write(Path(record_to), "record into")
    if save_replay_to is not None:
        check_file_to_write(Path(save_replay_to), "save the replay into")


def check_time_limit(time_limit: GameTime | None) -> None:
    """Raise `TypeError`, before a game starts, for a `time_limit` that is not a `GameTime`."""
    if time_limit is not None:
        check_game_time(time_limit, "time_limit")
