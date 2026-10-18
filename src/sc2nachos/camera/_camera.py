"""This player's camera: where it is centered, and moving it."""

from __future__ import annotations

from typing import TYPE_CHECKING, final

from s2clientprotocol import common_pb2, raw_pb2, sc2api_pb2

from sc2nachos.geometry import Point

if TYPE_CHECKING:
    from sc2nachos.geometry import PointLike


@final
class Camera:
    """This player's camera: where the turn's observation has it centered, and a move sent with the turn's orders."""

    __slots__ = ("_center", "_move_to")

    def __init__(self, center: Point) -> None:
        """A camera centered at `center`."""
        self._center = center
        self._move_to: Point | None = None

    @property
    def center(self) -> Point:
        """Where this player's camera is centered, as the turn's observation reports it."""
        return self._center

    def move_to(self, at: PointLike) -> None:
        """Center the camera on `at`, with the turn's orders. Only the last move of a turn is sent, and its point is
        rounded to the protocol's 32-bit floats."""
        self._move_to = Point._rounded_to_float32(at)

    @classmethod
    def _of_player(cls, player: raw_pb2.PlayerRaw) -> Camera:
        """The camera as `player`, an observation's own player, reports it."""
        return cls(Point((player.camera.x, player.camera.y)))

    def _update(self, player: raw_pb2.PlayerRaw) -> None:
        """Take in where `player`, the turn's observation's own player, has the camera."""
        self._center = Point((player.camera.x, player.camera.y))

    def _create_move_action(self) -> sc2api_pb2.Action | None:
        """The action that sends the turn's move, which is then forgotten, or `None` if the turn moved nothing."""
        if (at := self._move_to) is None:
            return None
        self._move_to = None
        move = raw_pb2.ActionRawCameraMove(center_world_space=common_pb2.Point(x=at.x, y=at.y))
        return sc2api_pb2.Action(action_raw=raw_pb2.ActionRaw(camera_move=move))
