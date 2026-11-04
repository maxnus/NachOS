"""Checks of the runners' arguments, made before a game or a replay starts."""

from pathlib import Path

from sc2nachos._shared.checks import check_file_to_write
from sc2nachos.gametime import GameTime
from sc2nachos.gametime._game_time import check_game_time


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
