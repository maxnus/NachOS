"""The game's data NachOS ships for a bot's tests."""

import lzma
import re
from importlib import resources

from s2clientprotocol import sc2api_pb2

from sc2nachos.gamedata import GameData

# Each file holds the game's answer to `RequestData` at the start of a game of one build, xz-compressed, as
# tools/write_shipped_game_data.py writes it from the first corpus game.
_SHIPPED_FILE_NAME = re.compile(r"game_data_(\d+)\.binpb\.xz")


def load_game_data(*, base_build: int = 75689) -> GameData:
    """The game's data, its tables, as StarCraft II of `base_build` answers them at the start of a game, read afresh in
    about 15 ms: by default build 75689's, StarCraft II 4.10 for Linux, the build NachOS is tested against. Raises
    `ValueError` for a build NachOS ships no data of."""
    shipped_file = resources.files("sc2nachos.testing").joinpath(f"game_data_{base_build}.binpb.xz")
    if not shipped_file.is_file():
        shipped_builds = ", ".join(str(build) for build in _shipped_base_builds())
        raise ValueError(f"NachOS ships no game data of build {base_build}, only of {shipped_builds}")
    return GameData(sc2api_pb2.ResponseData.FromString(lzma.decompress(shipped_file.read_bytes())))


def _shipped_base_builds() -> list[int]:
    """The builds NachOS ships the game's data of, in order."""
    package_files = resources.files("sc2nachos.testing").iterdir()
    shipped_names = [_SHIPPED_FILE_NAME.fullmatch(package_file.name) for package_file in package_files]
    return sorted(int(shipped_name.group(1)) for shipped_name in shipped_names if shipped_name is not None)
