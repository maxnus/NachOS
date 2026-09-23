"""A transport over the websocket of a running game client."""

from typing import Self

from google.protobuf.message import DecodeError
from loguru import logger
from s2clientprotocol import sc2api_pb2
from websocket import (
    WebSocket,
    WebSocketConnectionClosedException,
    WebSocketException,
    WebSocketTimeoutException,
    create_connection,
)

from sc2nachos.protocol._errors import ConnectionClosedError, ConnectionTimeoutError, ProtocolError


class WebSocketTransport:
    """A `Transport` over the websocket of a game client that is already running."""

    def __init__(self, websocket: WebSocket) -> None:
        """Wrap an open websocket. Closing the transport closes the websocket."""
        self._websocket = websocket

    @classmethod
    def connect(cls, url: str, *, timeout: float | None = None) -> Self:
        """Open a websocket to the game client listening at `url`.

        `timeout` bounds every wait on the socket. It defaults to none: loading a map takes as long as it takes,
        and a bot has nothing to do in the meantime.
        """
        try:
            websocket = create_connection(url, timeout=timeout)
        except WebSocketConnectionClosedException as error:
            raise ConnectionClosedError(
                f"the game at {url} dropped the connection as it opened, as it does while another connection holds it"
            ) from error
        except WebSocketTimeoutException as error:
            raise ConnectionTimeoutError(f"the game at {url} did not answer in time") from error
        except WebSocketException as error:
            raise ProtocolError(f"the websocket to {url} failed to open: {error}") from error
        logger.info("Connected to the game at {}", url)
        return cls(websocket)

    def request(self, request: sc2api_pb2.Request) -> sc2api_pb2.Response:
        """Send `request`, wait for the game's answer, and return it."""
        try:
            self._websocket.send_binary(request.SerializeToString())
            payload = self._websocket.recv()
        except WebSocketConnectionClosedException as error:
            raise ConnectionClosedError("the connection to the game closed mid-request") from error
        except WebSocketTimeoutException as error:
            raise ConnectionTimeoutError("the game did not answer in time") from error
        except WebSocketException as error:
            raise ProtocolError(f"the websocket failed: {error}") from error
        except ConnectionError as error:
            # A game that dies resets the socket, which the websocket library passes through as a bare OSError.
            raise ConnectionClosedError(f"the connection to the game was lost: {error}") from error
        if not isinstance(payload, bytes):
            raise ProtocolError(f"the game sent text where the protocol is binary: {payload!r}")
        response = sc2api_pb2.Response()
        try:
            response.ParseFromString(payload)
        except DecodeError as error:
            raise ProtocolError("the game sent an answer that does not parse") from error
        return response

    def close(self) -> None:
        """Close the websocket."""
        self._websocket.close()
