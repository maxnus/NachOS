"""What the game client is currently doing."""

from s2clientprotocol import sc2api_pb2

from sc2nachos._enum import ReadableIntEnum


class Status(ReadableIntEnum):
    """The game client's own status, from launching to quitting.

    Every response carries one, so it is read rather than tracked.
    """

    LAUNCHED = sc2api_pb2.Status.launched
    INIT_GAME = sc2api_pb2.Status.init_game
    IN_GAME = sc2api_pb2.Status.in_game
    IN_REPLAY = sc2api_pb2.Status.in_replay
    ENDED = sc2api_pb2.Status.ended
    QUIT = sc2api_pb2.Status.quit
    UNKNOWN = sc2api_pb2.Status.unknown
