"""Write the game's tables that `sc2nachos.testing` ships, from the first corpus game's answer to `RequestData`.

Run it after recording the corpus again, which `test_made_up_units.py` asks for when the two differ::

    uv run python tools/write_shipped_tables.py
"""

import lzma
from contextlib import closing
from pathlib import Path

from s2clientprotocol import sc2api_pb2

from sc2nachos.protocol import Recording

REPO = Path(__file__).parents[1]
CORPUS_DIRECTORY = REPO / "tests" / "corpus"
OUTPUT = REPO / "src" / "sc2nachos" / "testing" / "game_tables.binpb.xz"


def first_corpus_game_tables() -> sc2api_pb2.ResponseData:
    """The tables the first corpus game, in the order of their names, was answered at its start."""
    first_game = sorted(CORPUS_DIRECTORY.glob("*.sc2rec"))[0]
    with closing(iter(Recording(first_game))) as exchanges:
        for exchange in exchanges:
            if exchange.response.HasField("data"):
                return exchange.response.data
    raise ValueError(f"{first_game} holds no answer to RequestData")


def main() -> None:
    """Write the first corpus game's tables, xz-compressed, where the package reads them."""
    serialized_tables = first_corpus_game_tables().SerializeToString(deterministic=True)
    OUTPUT.write_bytes(lzma.compress(serialized_tables, preset=9 | lzma.PRESET_EXTREME))
    print(f"wrote {OUTPUT.relative_to(REPO)}: {OUTPUT.stat().st_size / 1e3:.0f} KB")


if __name__ == "__main__":
    main()
