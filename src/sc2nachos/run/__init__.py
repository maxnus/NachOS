"""Playing a game on a client this library starts, or on one a ladder started, and finding StarCraft II on this
machine and starting it."""

from sc2nachos.run._game_process import GameLaunchError, GameProcess, free_port
from sc2nachos.run._installation import (
    MINIMUM_BASE_BUILD,
    GameVersionError,
    Installation,
    InstallationNotFoundError,
    UnsupportedPlatformError,
)
from sc2nachos.run._map_file import MapFile, MapNotFoundError
from sc2nachos.run._runners import run_from_command_line, run_ladder, run_local, run_recording, run_replay

__all__ = [
    "MINIMUM_BASE_BUILD",
    "GameLaunchError",
    "GameProcess",
    "GameVersionError",
    "Installation",
    "InstallationNotFoundError",
    "MapFile",
    "MapNotFoundError",
    "UnsupportedPlatformError",
    "free_port",
    "run_from_command_line",
    "run_ladder",
    "run_local",
    "run_recording",
    "run_replay",
]
