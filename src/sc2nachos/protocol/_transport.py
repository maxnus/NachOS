"""The seam between the library and the game client."""

from typing import Protocol

from s2clientprotocol import sc2api_pb2


class Transport(Protocol):
    """Carries one request to a game client and brings back its response.

    Serialization lives below this seam and protobuf messages above it, so a recorded game and a live one drive
    the same code. The game answers one request at a time, so a request blocks until it is answered.
    """

    def request(self, request: sc2api_pb2.Request) -> sc2api_pb2.Response:
        """Send `request`, wait for the game's answer, and return it."""
        ...

    def close(self) -> None:
        """Release the transport. A request after this raises `ConnectionClosedError`."""
        ...
