"""A client connected to a game this library or a ladder started."""

from pathlib import Path

from sc2nachos.protocol._client import Client
from sc2nachos.protocol._recording import RecordingTransport
from sc2nachos.protocol._transport import Transport
from sc2nachos.protocol._websocket import WebSocketTransport


def connect_client(url: str, record_to: Path | str | None) -> Client:
    """A client connected to the game at `url`, recording the whole conversation to `record_to` if given. A recording
    that fails to open closes the connection."""
    transport: Transport = WebSocketTransport.connect(url)
    if record_to is not None:
        try:
            transport = RecordingTransport(transport, record_to)
        except BaseException:
            transport.close()
            raise
    return Client(transport)
