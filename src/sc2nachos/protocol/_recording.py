"""A game's conversation, kept so it can be played back without the game."""

import lzma
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from google.protobuf.message import DecodeError, Message
from loguru import logger
from s2clientprotocol import sc2api_pb2

from sc2nachos.protocol._errors import ConnectionClosedError, ProtocolError
from sc2nachos.protocol._transport import Transport

# The format: this header, then every message length-prefixed, all of it xz-compressed. Protobuf messages are
# not self-delimiting, and consecutive observations are near-identical, which is worth some 200x to xz.
_HEADER = b"NACHOS RECORDING 1\n"
_LENGTH = 4


@dataclass(frozen=True, slots=True)
class Exchange:
    """One request a game was asked, and the answer it gave."""

    request: sc2api_pb2.Request
    response: sc2api_pb2.Response


@dataclass(frozen=True, slots=True)
class Recording:
    """A game's whole conversation, on disk.

    Iterating re-reads the file, so a game is never held in memory whole and a recording can be read again.
    """

    path: Path

    def __iter__(self) -> Generator[Exchange, None, None]:
        """Every exchange, in the order the game answered them."""
        with lzma.open(self.path, "rb") as stream:
            try:
                if stream.read(len(_HEADER)) != _HEADER:
                    raise ProtocolError(f"{self.path} is not a recording this version of NachOS can read")
                while (request := _read(stream, sc2api_pb2.Request)) is not None:
                    response = _read(stream, sc2api_pb2.Response)
                    if response is None:
                        raise ProtocolError(f"{self.path} ends on a request the game never answered")
                    yield Exchange(request, response)
            except EOFError as cut:
                raise ProtocolError(
                    f"{self.path} stops partway, as it does when its run dies before closing it"
                ) from cut
            except lzma.LZMAError as error:
                raise ProtocolError(f"{self.path} is not a recording, or is a damaged one: {error}") from error


class RecordingTransport:
    """A `Transport` that writes every exchange to disk on its way through to another one.

    The file is whole only once the transport is closed. A run that dies first leaves what the compressor had
    already written out, which misses the last stretch of the game, or all of a short one.
    """

    def __init__(self, transport: Transport, path: Path) -> None:
        """Forward to `transport`, recording into `path`, which is overwritten if it already exists."""
        self._transport = transport
        self._recording = Recording(path)
        self._exchanges = 0
        # The file stays open for the length of the game, and is closed with the transport.
        self._stream = lzma.open(path, "wb")  # noqa: SIM115
        self._stream.write(_HEADER)

    @property
    def recording(self) -> Recording:
        """The file being written, readable once the transport is closed."""
        return self._recording

    def request(self, request: sc2api_pb2.Request) -> sc2api_pb2.Response:
        """Send `request` on, and record it with the answer before handing that answer back."""
        response = self._transport.request(request)
        _write(self._stream, request)
        _write(self._stream, response)
        self._exchanges += 1
        return response

    def close(self) -> None:
        """Close the transport underneath, then finish the file. Doing this twice is not an error."""
        try:
            self._transport.close()
        finally:
            if not self._stream.closed:
                self._stream.close()
                size = self._recording.path.stat().st_size
                logger.info(
                    "Recorded {} exchanges to {} ({:.1f} MB)", self._exchanges, self._recording.path, size / 1e6
                )


class PlaybackTransport:
    """A `Transport` that plays a recording back, so everything above the seam runs with no game.

    Answers come back in the order they were recorded. Only the kind of request is checked and not its
    contents, so a caller may ask about a different game loop than the recording did, but not a different thing.
    """

    def __init__(self, recording: Recording) -> None:
        """Answer from `recording`, starting at its first exchange."""
        self._exchanges = iter(recording)
        self._closed = False

    def request(self, request: sc2api_pb2.Request) -> sc2api_pb2.Response:
        """The next recorded answer, once the recording proves to have been asked the same kind of question."""
        if self._closed:
            raise ConnectionClosedError("the recording is closed")
        asked = request.WhichOneof("request") or "nothing"
        exchange = next(self._exchanges, None)
        if exchange is None:
            raise ProtocolError(f"the recording has no answer left, and was asked for {asked}")
        recorded = exchange.request.WhichOneof("request") or "nothing"
        if asked != recorded:
            raise ProtocolError(f"the recording answers {recorded} next, and was asked for {asked}")
        return exchange.response

    def close(self) -> None:
        """Stop answering, and release the file. A request after this raises, as a closed socket would."""
        self._closed = True
        self._exchanges.close()


def _read[MessageT: Message](stream: IO[bytes], kind: type[MessageT]) -> MessageT | None:
    """The next `kind` in `stream`, or `None` once the stream has run out."""
    header = stream.read(_LENGTH)
    if not header:
        return None
    if len(header) != _LENGTH:
        raise ProtocolError("the recording ends inside a length prefix")
    size = int.from_bytes(header, "big")
    payload = stream.read(size)
    if len(payload) != size:
        raise ProtocolError(f"the recording ends {size - len(payload)} bytes short of a {kind.__name__}")
    message = kind()
    try:
        message.ParseFromString(payload)
    except DecodeError as error:
        raise ProtocolError(f"the recording holds a {kind.__name__} that does not parse") from error
    return message


def _write(stream: IO[bytes], message: Message) -> None:
    """Append `message` to `stream`, prefixed with its length."""
    payload = message.SerializeToString()
    stream.write(len(payload).to_bytes(_LENGTH, "big"))
    stream.write(payload)
