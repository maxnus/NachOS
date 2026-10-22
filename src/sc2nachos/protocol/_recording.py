"""A game's requests and answers, recorded so they can be played back without the game."""

import lzma
import math
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from google.protobuf.message import DecodeError, Message
from loguru import logger
from s2clientprotocol import common_pb2, error_pb2, query_pb2, sc2api_pb2

from sc2nachos.protocol._errors import ConnectionClosedError, ProtocolError
from sc2nachos.protocol._transport import Transport

# The format: this header, then every message length-prefixed, all of it xz-compressed. Protobuf messages are
# not self-delimiting, and consecutive observations are near-identical, which xz compresses some 200x.
_HEADER = b"NACHOS RECORDING 1\n"
_LENGTH = 4

# What a bot asks of its own accord during a game, and the step. Played back for another bot, each is answered as the
# transport makes it up, and the recording's own are passed over.
_BOT_REQUESTS = frozenset({"action", "debug", "query", "save_replay", "step"})

# What a recorded bot may have asked between its turn's other requests, which another bot need not ask. Played back for
# it, each is passed over while something else is asked, the last game info kept.
_ANSWERED_ONLY_WHEN_ASKED = frozenset({"game_info", "ping"})

# What a recording holds after the last observation of its game.
_AFTER_THE_GAME = frozenset({"leave_game", "quit", "nothing"})


@dataclass(frozen=True, slots=True)
class Exchange:
    """One request to the game, and its answer."""

    request: sc2api_pb2.Request
    response: sc2api_pb2.Response


@dataclass(frozen=True, slots=True)
class Recording:
    """A whole game's exchanges, on disk.

    Iterating re-reads the file, so a game is never held in memory whole, and a recording can be read again.
    """

    path: Path

    def __iter__(self) -> Generator[Exchange, None, None]:
        """Every exchange, in the order the game answered."""
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
    """A `Transport` that writes every exchange to disk on its way through to another transport.

    The file is complete only once the transport is closed. A run that dies first leaves what the compressor had
    already written, which misses the last stretch of the game, or all of a short one.
    """

    def __init__(self, transport: Transport, path: Path | str) -> None:
        """Forward to `transport`, recording into `path`. An existing file at `path` is overwritten."""
        path = Path(path)
        self._transport = transport
        self._recording = Recording(path)
        self._exchanges = 0
        # The file stays open for the whole game and is closed with the transport.
        self._stream = lzma.open(path, "wb")  # noqa: SIM115
        self._stream.write(_HEADER)

    @property
    def recording(self) -> Recording:
        """The file being written, readable once the transport is closed."""
        return self._recording

    def request(self, request: sc2api_pb2.Request) -> sc2api_pb2.Response:
        """Forward `request`, record it with its answer, and return the answer."""
        response = self._transport.request(request)
        _write(self._stream, request)
        _write(self._stream, response)
        self._exchanges += 1
        return response

    def close(self) -> None:
        """Close the underlying transport, then finish the file. Closing twice is not an error."""
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
    """A `Transport` that plays a recording back, so everything above the seam runs without a game.

    Answers come back in the order they were recorded. Only the kind of request is checked, not its contents, so a
    caller may ask about a different game loop than the recording did, but not for a different thing.

    With `answer_unrecorded`, a bot other than the one recorded can play the recording through, though nothing it does
    changes what comes next. A step, and what the bot asks of its own accord, is answered as made up here, under the
    status of the last recorded answer, and the recording's own answers to such requests are passed over:
    - a step, an empty answer;
    - an action, every one of its actions `Success`;
    - a debug request, an empty answer;
    - a query, each pathing distance as the straight line between its ends, from a unit's tag at its position in the
      last recorded observation, or 0, which the game answers for no path, for a tag that observation does not hold;
    - a request to save the replay, refused.

    The rest plays as recorded, a recorded game info or ping passed over while something else is asked, and is made
    up where the recording does not answer it next:
    - creating a game it did not create, with the status `init_game`;
    - a game info, as the last one it answered;
    - an observation once only leaving the game, quitting or nothing is left, as its last observation again with the
      status `ended`, which ends a game recorded without its result;
    - leaving the game, with the status `launched`.

    A query for abilities or placements raises `ProtocolError`.
    """

    def __init__(self, recording: Recording, *, answer_unrecorded: bool = False) -> None:
        """Answer from `recording`, starting at its first exchange, and make up answers if `answer_unrecorded`."""
        self._exchanges = iter(recording)
        self._answer_unrecorded = answer_unrecorded
        self._next_exchange: Exchange | None = None
        self._last_status: sc2api_pb2.Status.ValueType | None = None
        self._last_game_info: sc2api_pb2.Response | None = None
        self._last_observation: sc2api_pb2.ResponseObservation | None = None
        self._positions_by_tag: dict[int, common_pb2.Point] | None = None
        self._closed = False

    def request(self, request: sc2api_pb2.Request) -> sc2api_pb2.Response:
        """The next recorded answer, if the recording was asked the same kind of question, or one made up as the class
        says."""
        if self._closed:
            raise ConnectionClosedError("the recording is closed")
        asked_kind = request.WhichOneof("request") or "nothing"
        if self._answer_unrecorded and asked_kind in _BOT_REQUESTS:
            return self._made_up_answer(request, asked_kind)
        exchange = self._peek_past_unrecorded(asked_kind)
        recorded_kind = "nothing" if exchange is None else exchange.request.WhichOneof("request") or "nothing"
        if exchange is not None and recorded_kind == asked_kind:
            self._next_exchange = None
            return self._remember(exchange.response)
        if self._answer_unrecorded and (response := self._unrecorded_answer(asked_kind, recorded_kind)) is not None:
            return response
        if exchange is None:
            raise ProtocolError(f"the recording has no answer left, and was asked for {asked_kind}")
        raise ProtocolError(f"the recording answers {recorded_kind} next, and was asked for {asked_kind}")

    def close(self) -> None:
        """Stop answering and release the file. A request after this raises, as it would on a closed socket."""
        self._closed = True
        self._exchanges.close()

    def _peek_past_unrecorded(self, asked_kind: str) -> Exchange | None:
        """The next exchange, unread, or `None` once there is none. With `answer_unrecorded`, the answers to a bot's own
        requests are passed over first, and so is a game info or a ping while something else is asked, the last game
        info kept."""
        while True:
            if self._next_exchange is None:
                self._next_exchange = next(self._exchanges, None)
            exchange = self._next_exchange
            if exchange is None or not self._answer_unrecorded:
                return exchange
            recorded_kind = exchange.request.WhichOneof("request")
            if recorded_kind not in _BOT_REQUESTS and (
                recorded_kind not in _ANSWERED_ONLY_WHEN_ASKED or recorded_kind == asked_kind
            ):
                return exchange
            self._next_exchange = None
            self._remember(exchange.response)

    def _remember(self, response: sc2api_pb2.Response) -> sc2api_pb2.Response:
        """Keep what a made-up answer reads from `response`, a recorded answer, and return it."""
        if response.HasField("status"):
            self._last_status = response.status
        if response.HasField("game_info"):
            self._last_game_info = response
        elif response.HasField("observation"):
            self._last_observation = response.observation
            self._positions_by_tag = None
        return response

    def _made_up_answer(self, request: sc2api_pb2.Request, asked_kind: str) -> sc2api_pb2.Response:
        """An answer to `request`, a step or a bot's own `asked_kind`, as the class says."""
        response = sc2api_pb2.Response()
        if self._last_status is not None:
            response.status = self._last_status
        if asked_kind == "step":
            response.step.SetInParent()
        elif asked_kind == "action":
            response.action.result.extend([error_pb2.Success] * len(request.action.actions))
        elif asked_kind == "debug":
            response.debug.SetInParent()
        elif asked_kind == "query":
            response.query.CopyFrom(self._straight_line_distances(request.query))
        elif asked_kind == "save_replay":
            response.error.append("a recording holds no replay to save")
        else:
            raise ValueError(f"no answer to {asked_kind} is made up")
        return response

    def _unrecorded_answer(self, asked_kind: str, recorded_kind: str) -> sc2api_pb2.Response | None:
        """An answer to `asked_kind`, which the recording does not answer next but `recorded_kind`, as the class says,
        or `None` if none is made up for it."""
        if asked_kind == "create_game":
            return sc2api_pb2.Response(create_game=sc2api_pb2.ResponseCreateGame(), status=sc2api_pb2.init_game)
        if asked_kind == "game_info":
            return self._last_game_info
        if asked_kind == "observation" and recorded_kind in _AFTER_THE_GAME and self._last_observation is not None:
            return self._remember(sc2api_pb2.Response(observation=self._last_observation, status=sc2api_pb2.ended))
        if asked_kind == "leave_game":
            return sc2api_pb2.Response(leave_game=sc2api_pb2.ResponseLeaveGame(), status=sc2api_pb2.launched)
        return None

    def _straight_line_distances(self, query: query_pb2.RequestQuery) -> query_pb2.ResponseQuery:
        """The answer to `query`, each pathing distance the straight line between its ends."""
        if query.abilities or query.placements:
            raise ProtocolError("a recording answers a query only for pathing distances, not abilities or placements")
        query_answer = query_pb2.ResponseQuery()
        for pathing in query.pathing:
            start_point = (
                pathing.start_pos if pathing.WhichOneof("start") == "start_pos" else self._position_of(pathing.unit_tag)
            )
            straight_distance = (
                0.0
                if start_point is None
                else math.hypot(pathing.end_pos.x - start_point.x, pathing.end_pos.y - start_point.y)
            )
            query_answer.pathing.add(distance=straight_distance)
        return query_answer

    def _position_of(self, tag: int) -> common_pb2.Point | None:
        """Where the last recorded observation had the unit of `tag`, or `None` if it did not report it."""
        if self._positions_by_tag is None:
            units = () if self._last_observation is None else self._last_observation.observation.raw_data.units
            self._positions_by_tag = {unit.tag: unit.pos for unit in units}
        return self._positions_by_tag.get(tag)


def _read[MessageT: Message](stream: IO[bytes], kind: type[MessageT]) -> MessageT | None:
    """The next `kind` message in `stream`, or `None` once the stream has run out."""
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
    """Append `message` to `stream`, length-prefixed."""
    payload = message.SerializeToString()
    stream.write(len(payload).to_bytes(_LENGTH, "big"))
    stream.write(payload)
