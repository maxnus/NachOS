"""The game's data NachOS ships for a bot's tests."""

import lzma
import re
from importlib import resources
from importlib.resources.abc import Traversable

from s2clientprotocol import sc2api_pb2

from sc2nachos.gamedata import GameData

# Each file holds the game's answer to `RequestData` at the start of a game of one build, xz-compressed, as
# tools/write_shipped_game_data.py writes it from the first corpus game.
_SHIPPED_FILE_NAME = re.compile(r"game_data_(\d+)\.binpb\.xz")


def load_game_data(*, base_build: int) -> GameData:
    """The game's data, its tables, as StarCraft II of `base_build` answers them at the start of a game, read afresh in
    about 15 ms. Raises `ValueError` for a build NachOS ships no data of: it ships 75689's, StarCraft II 4.10 for
    Linux, the build it is tested against."""
    shipped_files = _shipped_files_by_base_build()
    if base_build not in shipped_files:
        shipped_builds = ", ".join(str(build) for build in sorted(shipped_files))
        raise ValueError(f"NachOS ships no game data of build {base_build}, only of {shipped_builds}")
    return _read_game_data(shipped_files[base_build])


def _load_newest_game_data() -> GameData:
    """The game's data of the newest build NachOS ships."""
    shipped_files = _shipped_files_by_base_build()
    return _read_game_data(shipped_files[max(shipped_files)])


def _shipped_files_by_base_build() -> dict[int, Traversable]:
    """The files of the game's data NachOS ships, by the build each is of."""
    shipped_files = {}
    for package_file in resources.files("sc2nachos.testing").iterdir():
        if shipped_name := _SHIPPED_FILE_NAME.fullmatch(package_file.name):
            shipped_files[int(shipped_name.group(1))] = package_file
    return shipped_files


def _read_game_data(shipped_file: Traversable) -> GameData:
    """The game's data in `shipped_file`."""
    return GameData(sc2api_pb2.ResponseData.FromString(lzma.decompress(shipped_file.read_bytes())))
