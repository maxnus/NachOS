"""A message sent to the game's chat."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Self, final

if TYPE_CHECKING:
    from s2clientprotocol import sc2api_pb2


@final
@dataclass(frozen=True, slots=True)
class ChatMessage:
    """A message a player sent to the game's chat, this player's own included."""

    player_id: int
    """The id of the player who sent it."""
    text: str

    @classmethod
    def from_proto(cls, proto: sc2api_pb2.ChatReceived) -> Self:
        return cls(proto.player_id, proto.message)
