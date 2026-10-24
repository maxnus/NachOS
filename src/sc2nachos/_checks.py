"""Checks that the arguments of several modules share."""

from numbers import Integral
from pathlib import Path

from sc2nachos.game_time import GameTime


def is_whole_number(value: object) -> bool:
    """Whether `value` is a whole number, and not a `bool`."""
    return isinstance(value, Integral) and not isinstance(value, bool)


def whole_steps_of(time: GameTime) -> int | None:
    """The steps of `time` as an `int`, or `None` if they are not a whole number. Raises `TypeError` for anything but
    a `GameTime`."""
    if not isinstance(time, GameTime):
        raise TypeError(f"a game time is a GameTime, not {time!r}")
    return int(time.steps) if float(time.steps).is_integer() else None


def check_file_to_write(path: Path, purpose: str) -> None:
    """Raise `FileNotFoundError` if `path` is in no folder that exists, and `IsADirectoryError` if it is a folder. Each
    message ends with `purpose`, what the file is for: "record into"."""
    if not path.parent.is_dir():
        raise FileNotFoundError(f"there is no folder {path.parent} to {purpose}")
    if path.is_dir():
        raise IsADirectoryError(f"{path} is a folder, not a file to {purpose}")
