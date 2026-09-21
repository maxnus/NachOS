"""Draw the ramps NachOS finds onto the map itself, in a game with the fog lifted.

Needs StarCraft II installed. Opens each map in turn, reveals it, and holds it on the screen so the drawing can
be compared with the ground. Move the camera around while it runs; close it or press Ctrl+C to go on to the next
map::

    uv run python tools/show_ramps.py
    uv run python tools/show_ramps.py PylonAIE_v4 TorchesAIE_v4
    uv run python tools/show_ramps.py --seconds 300 PylonAIE_v4

A ramp is drawn tile by tile: its top green, its bottom red, the tile at the middle of it yellow, and the rest of
it blue. The label on each ramp is its index in `api.map.ramps` and how many tiles it covers.
"""

import argparse
import sys
import time
from collections.abc import Sequence
from contextlib import closing, suppress

from loguru import logger
from s2clientprotocol import common_pb2, debug_pb2

from sc2nachos.gamemap import GameMap, Ramp
from sc2nachos.geometry import Tile
from sc2nachos.launch import GameProcess, Installation, MapFile
from sc2nachos.match import AIBuild, Computer, Difficulty, Participant, Race
from sc2nachos.protocol import Client, GameEndedError, WebSocketTransport

# Every map the corpus is recorded on, which is the current ladder pool.
MAPS = (
    "PylonAIE_v4",
    "TorchesAIE_v4",
    "MagannathaAIE_v2",
    "LeyLinesAIE_v3",
    "PersephoneAIE_v4",
    "UltraloveAIE_v2",
    "IncorporealAIE_v4",
)

_WHOLE = (40, 90, 255)
_TOP = (60, 230, 60)
_BOTTOM = (240, 60, 60)
_CENTER = (255, 230, 40)

# A box drawn flush with the ground is lost in it, so each one is lifted and stands this tall.
_LIFT = 0.05
_HEIGHT = 0.4
# The gap left around a box, so that neighboring tiles read as two boxes rather than one slab.
_MARGIN = 0.05


def _box(game_map: GameMap, tile: Tile, color: tuple[int, int, int]) -> debug_pb2.DebugBox:
    """A box standing on `tile`, drawn in `color`."""
    ground = game_map.height[tile] + _LIFT
    return debug_pb2.DebugBox(
        color=debug_pb2.Color(r=color[0], g=color[1], b=color[2]),
        min=common_pb2.Point(x=tile.x + _MARGIN, y=tile.y + _MARGIN, z=ground),
        max=common_pb2.Point(x=tile.x + 1 - _MARGIN, y=tile.y + 1 - _MARGIN, z=ground + _HEIGHT),
    )


def _label(game_map: GameMap, index: int, ramp: Ramp) -> debug_pb2.DebugText:
    """The text naming one ramp, standing over the middle of it."""
    # A ramp is a solid patch, so the tile its middle falls in is one of its own.
    tile = Tile.containing(ramp.tiles.center)
    return debug_pb2.DebugText(
        color=debug_pb2.Color(r=_CENTER[0], g=_CENTER[1], b=_CENTER[2]),
        text=f"ramp {index}: {len(ramp.tiles)} tiles",
        world_pos=common_pb2.Point(x=tile.center.x, y=tile.center.y, z=game_map.height[tile] + 2.0),
        size=12,
    )


def drawing(game_map: GameMap) -> list[debug_pb2.DebugCommand]:
    """The whole drawing of a map's ramps, as the commands that put it on the screen."""
    boxes: list[debug_pb2.DebugBox] = []
    texts: list[debug_pb2.DebugText] = []
    for index, ramp in enumerate(game_map.ramps):
        # Later boxes are drawn over earlier ones, so the ends and the middle go on top of the whole ramp.
        for tiles, color in ((ramp.tiles, _WHOLE), (ramp.top, _TOP), (ramp.bottom, _BOTTOM)):
            boxes.extend(_box(game_map, tile, color) for tile in tiles)
        boxes.append(_box(game_map, Tile.containing(ramp.tiles.center), _CENTER))
        texts.append(_label(game_map, index, ramp))
    return [debug_pb2.DebugCommand(draw=debug_pb2.DebugDraw(boxes=boxes, text=texts))]


def show(name: str, seconds: float, installation: Installation) -> None:
    """Open `name`, reveal it, and hold the drawing of its ramps on the screen for `seconds`."""
    game_map = MapFile.find(name, installation=installation)
    with (
        GameProcess.launch(installation, window=(1600, 900)) as game,
        closing(Client(WebSocketTransport.connect(game.url))) as client,
    ):
        try:
            # A game with nobody to beat is won the moment it starts, so there is an opponent to keep it open.
            players = [Participant(), Computer(Race.TERRAN, Difficulty.VERY_EASY, AIBuild.MACRO)]
            client.create_game(game_map.path, players, realtime=True)
            client.join_game(Race.TERRAN, name="NachOS")
            drawn = GameMap(client.game_info())
            covered = sum(len(ramp.tiles) for ramp in drawn.ramps)
            logger.info("{} has {} ramps, covering {} tiles", drawn.name, len(drawn.ramps), covered)
            picture = drawing(drawn)
            # Revealing the map is a toggle, so it is sent once and the drawing alone is sent again after it.
            client.debug([debug_pb2.DebugCommand(game_state=debug_pb2.DebugGameState.show_map), *picture])
            _hold(client, picture, seconds)
        finally:
            client.leave_game()
            client.quit()


def _hold(client: Client, commands: Sequence[debug_pb2.DebugCommand], seconds: float) -> None:
    """Keep the drawing on the screen until `seconds` of it have passed, or the game or the user ends it."""
    until = time.monotonic() + seconds
    with suppress(KeyboardInterrupt, GameEndedError):
        while time.monotonic() < until and client.in_game:
            client.debug(commands)
            time.sleep(0.2)
    if not client.in_game:
        logger.info("The game ended, so there is nothing left to draw on")


def main(argv: Sequence[str]) -> None:
    """Show the maps named, or every map of the pool when none are."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("maps", nargs="*", default=[], help="which maps to show, by name")
    parser.add_argument("--seconds", type=float, default=120.0, help="how long to hold each map on the screen")
    arguments = parser.parse_args(argv)
    installation = Installation.find()
    for name in arguments.maps or MAPS:
        show(name, arguments.seconds, installation)


if __name__ == "__main__":
    main(sys.argv[1:])
