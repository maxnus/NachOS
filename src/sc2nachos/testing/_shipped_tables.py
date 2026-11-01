"""The game's tables NachOS ships for a bot's tests."""

import lzma
from importlib import resources

from s2clientprotocol import sc2api_pb2

from sc2nachos.gamedata import GameData


def shipped_tables() -> GameData:
    """The game's tables as StarCraft II 4.10 for Linux, build 75689, answers them at the start of a game: the version
    NachOS is tested against. They are read afresh on each call, in about 15 ms."""
    # Written from the first corpus game by tools/write_shipped_tables.py.
    compressed_tables = resources.files("sc2nachos.testing").joinpath("game_tables.binpb.xz").read_bytes()
    return GameData(sc2api_pb2.ResponseData.FromString(lzma.decompress(compressed_tables)))
