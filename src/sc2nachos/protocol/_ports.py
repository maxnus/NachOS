"""The ports a client needs to join a multiplayer game."""

from dataclasses import dataclass
from typing import Self


@dataclass(frozen=True, slots=True)
class PortPair:
    """The pair of ports the game needs for one connection.

    Every participant in a match must send the same ports in the same order, or the join fails.
    """

    game: int
    base: int


@dataclass(frozen=True, slots=True)
class GamePorts:
    """The ports every participant in a multiplayer game must agree on: one pair for the host, and one for each
    guest."""

    server: PortPair
    players: tuple[PortPair, ...]

    @classmethod
    def from_start_port(cls, start_port: int) -> Self:
        """The ladder's layout: five consecutive ports from `start_port`, the first of them unused."""
        return cls(
            server=PortPair(start_port + 2, start_port + 3),
            players=(PortPair(start_port + 4, start_port + 5),),
        )
