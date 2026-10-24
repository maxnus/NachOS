"""The library's requests to one game client, and the answers."""

from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Literal

from loguru import logger
from s2clientprotocol import debug_pb2, query_pb2, sc2api_pb2

from sc2nachos.match import Computer, Participant, Player, Race, Result
from sc2nachos.protocol._errors import ConnectionClosedError, GameEndedError, GameNotStartedError, ProtocolError
from sc2nachos.protocol._ports import GamePorts
from sc2nachos.protocol._status import Status
from sc2nachos.protocol._transport import Transport

# What the game says when asked, after a game is over, for something only a running game can give.
_GAME_OVER_ERRORS = frozenset({"Game has already ended", "Not supported if game has already ended"})
# What it says before a game has started, including after the client has left the last one.
_NOT_STARTED_ERRORS = frozenset({"A game has not been started yet"})


# The answers this client reads. A `Literal` rather than `str`, so pyright checks each name against the response
# fields the stubs declare.
type _Answer = Literal[
    "action",
    "create_game",
    "data",
    "game_info",
    "join_game",
    "observation",
    "ping",
    "query",
    "replay_info",
    "save_replay",
    "start_replay",
    "step",
]


def _raw_interface(*, raw_affects_selection: bool) -> sc2api_pb2.InterfaceOptions:
    """The interface NachOS plays on, the raw one; the rendered interfaces are never requested."""
    return sc2api_pb2.InterfaceOptions(
        raw=True,
        score=True,
        show_cloaked=True,
        show_burrowed_shadows=True,
        show_placeholders=True,
        raw_affects_selection=raw_affects_selection,
        raw_crop_to_playable_area=False,
    )


def _player_setup(player: Player) -> sc2api_pb2.PlayerSetup:
    """One slot in a game being created. A participant's slot carries nothing: it says who it is when it joins."""
    match player:
        case Participant():
            return sc2api_pb2.PlayerSetup(type=sc2api_pb2.Participant)
        case Computer(race=race, difficulty=difficulty, build=build, name=name):
            setup = sc2api_pb2.PlayerSetup(
                type=sc2api_pb2.Computer, race=race.value, difficulty=difficulty.value, ai_build=build.value
            )
            if name is not None:
                setup.player_name = name
            return setup


class Client:
    """A game client, addressed over a transport.

    Every method is one request and blocks until the game answers. The protocol carries one request at a time and
    matches an answer only to the last question, so nothing can overlap.

    One connection can play game after game. What the client keeps of a game, its own player id and how the game
    ended, lasts from the join until the game is left or the next one is set up.

    Nothing is interpreted here beyond what the protocol demands: the responses are the game's own messages, and
    turning them into a data model belongs above.
    """

    def __init__(self, transport: Transport) -> None:
        """Talk to the game over `transport`. Nothing is sent until a request is made."""
        self._transport = transport
        self._status: Status | None = None
        self._player_id: int | None = None
        self._results: dict[int, Result] = {}

    @property
    def status(self) -> Status | None:
        """The status the game reported in its last answer, or `None` before it has answered at all."""
        return self._status

    @property
    def in_game(self) -> bool:
        """Whether a game is running, rather than loading, ended or not yet started. A replay being played back is
        one."""
        return self._status in (Status.IN_GAME, Status.IN_REPLAY)

    @property
    def in_replay(self) -> bool:
        """Whether a replay is being played back, rather than a game played, or nothing running."""
        return self._status is Status.IN_REPLAY

    @property
    def player_id(self) -> int | None:
        """This client's player id, once it has joined a game."""
        return self._player_id

    @property
    def result(self) -> Result | None:
        """How the game ended for this client's own player, or `None` until it ends."""
        if self._player_id is None:
            return None
        return self._results.get(self._player_id)

    @property
    def results(self) -> Mapping[int, Result]:
        """How the game ended, by player id. Empty until it ends."""
        return self._results

    def ping(self) -> sc2api_pb2.ResponsePing:
        """Ask the game for its version. This also proves the connection works."""
        response = self._send(sc2api_pb2.Request(ping=sc2api_pb2.RequestPing()), "ping")
        return response.ping

    def create_game(
        self,
        map_path: Path | str,
        players: Sequence[Player],
        *,
        realtime: bool = False,
        disable_fog: bool = False,
        random_seed: int | None = None,
    ) -> None:
        """Set up a match on `map_path` for `players`. Each participant then joins it.

        Only the client that creates the game sends this; on a ladder the game already exists. At least one player
        must be a participant. The game seats only as many players as the map has slots and silently drops the rest.
        """
        self._forget_game()
        request = sc2api_pb2.RequestCreateGame(
            local_map=sc2api_pb2.LocalMap(map_path=str(map_path)),
            player_setup=[_player_setup(player) for player in players],
            realtime=realtime,
            disable_fog=disable_fog,
        )
        if random_seed is not None:
            request.random_seed = random_seed

        response = self._send(sc2api_pb2.Request(create_game=request), "create_game")
        created = response.create_game
        # An unset error field reads as the first error the proto declares, so check that it is set first.
        if created.HasField("error"):
            reason = sc2api_pb2.ResponseCreateGame.Error.Name(created.error)
            detail = created.error_details or "no detail given"
            raise ProtocolError(f"the game refused to create the match as {reason}: {detail}")
        logger.info("Created a game on {} for {} players", map_path, len(players))

    def join_game(
        self,
        race: Race,
        *,
        name: str | None = None,
        ports: GamePorts | None = None,
        raw_affects_selection: bool = False,
    ) -> int:
        """Join the waiting game as `race` and return the player id the game assigns.

        NachOS plays on the raw interface; the rendered interfaces are never requested.
        """
        self._forget_game()
        request = sc2api_pb2.RequestJoinGame(
            race=race.value, options=_raw_interface(raw_affects_selection=raw_affects_selection)
        )
        if name is not None:
            request.player_name = name
        if ports is not None:
            request.server_ports.game_port = ports.server.game
            request.server_ports.base_port = ports.server.base
            for pair in ports.players:
                request.client_ports.add(game_port=pair.game, base_port=pair.base)

        response = self._send(sc2api_pb2.Request(join_game=request), "join_game")
        joined = response.join_game
        # An unset error field reads as the first error the proto declares, so check that it is set first.
        if joined.HasField("error"):
            reason = sc2api_pb2.ResponseJoinGame.Error.Name(joined.error)
            detail = joined.error_details or "no detail given"
            raise ProtocolError(f"the game refused the join as {reason}: {detail}")
        self._player_id = joined.player_id
        logger.info("Joined the game as player {} playing {}", joined.player_id, race)
        return joined.player_id

    def replay_info(self, replay: bytes) -> sc2api_pb2.ResponseReplayInfo:
        """What the game reads from `replay`, the bytes of a `.SC2Replay` file: its map, its length, the build it was
        played on and its players. Raises `ProtocolError` if the game cannot read it."""
        request = sc2api_pb2.RequestReplayInfo(replay_data=replay)
        info = self._send(sc2api_pb2.Request(replay_info=request), "replay_info").replay_info
        # An unset error field reads as the first error the proto declares, so check that it is set first.
        if info.HasField("error"):
            reason = sc2api_pb2.ResponseReplayInfo.Error.Name(info.error)
            detail = info.error_details or "no detail given"
            raise ProtocolError(f"the game could not read the replay, as {reason}: {detail}")
        return info

    def start_replay(self, replay: bytes, *, player_id: int) -> None:
        """Play back `replay`, the bytes of a `.SC2Replay` file, as the player `player_id` saw it, which then stands
        for the player id a join assigns.

        The game then answers observations, steps, queries and the rest as in a game, and as that player saw the game
        when it was played, but refuses every action and debug command (in game). It is played stepped, never in real
        time.
        """
        self._forget_game()
        request = sc2api_pb2.RequestStartReplay(
            replay_data=replay,
            observed_player_id=player_id,
            options=_raw_interface(raw_affects_selection=False),
            realtime=False,
        )
        started = self._send(sc2api_pb2.Request(start_replay=request), "start_replay").start_replay
        # An unset error field reads as the first error the proto declares, so check that it is set first.
        if started.HasField("error"):
            reason = sc2api_pb2.ResponseStartReplay.Error.Name(started.error)
            detail = started.error_details or "no detail given"
            raise ProtocolError(f"the game refused to play the replay back, as {reason}: {detail}")
        self._player_id = player_id
        logger.info("Playing a replay back as player {}", player_id)

    def game_info(self) -> sc2api_pb2.ResponseGameInfo:
        """The map: its size, terrain, start locations and players."""
        response = self._send(sc2api_pb2.Request(game_info=sc2api_pb2.RequestGameInfo()), "game_info")
        return response.game_info

    def game_data(
        self,
        *,
        abilities: bool = True,
        unit_types: bool = True,
        upgrades: bool = True,
        buffs: bool = True,
        effects: bool = True,
    ) -> sc2api_pb2.ResponseData:
        """The tables behind the ids: costs, ranges, requirements and names.

        Unit weapons, armor and movement speed include the upgrades this player holds at the time of asking.
        """
        request = sc2api_pb2.RequestData(
            ability_id=abilities,
            unit_type_id=unit_types,
            upgrade_id=upgrades,
            buff_id=buffs,
            effect_id=effects,
        )
        response = self._send(sc2api_pb2.Request(data=request), "data")
        return response.data

    def observation(self, *, game_loop: int | None = None) -> sc2api_pb2.ResponseObservation:
        """What this player can see now, or once the game reaches `game_loop`.

        When the game is over, each player's result is stored in `results`, and kept until the next game, though later
        observations report none.
        """
        observation = self._observe(game_loop)
        if not observation.player_result:
            if self.in_game or self._results:
                return observation
            # The game reports itself ended one step before it says who won, so ask once more.
            observation = self._observe(None)
        # An unset result reads as the first the proto declares, a victory, so only the set ones count.
        results = {
            entry.player_id: Result(entry.result) for entry in observation.player_result if entry.HasField("result")
        }
        if results:
            self._results = results
            logger.info("The game ended: {}", results)
        return observation

    def step(self, count: int) -> sc2api_pb2.ResponseStep:
        """Let the game run `count` game loops. Only a stepped game needs this; a realtime one runs on its own."""
        response = self._send(sc2api_pb2.Request(step=sc2api_pb2.RequestStep(count=count)), "step")
        return response.step

    def act(self, actions: Sequence[sc2api_pb2.Action]) -> sc2api_pb2.ResponseAction:
        """Send `actions` and return the game's result for each, in the order they were given."""
        response = self._send(sc2api_pb2.Request(action=sc2api_pb2.RequestAction(actions=actions)), "action")
        return response.action

    def debug(self, commands: Sequence[debug_pb2.DebugCommand]) -> None:
        """Send debug `commands`, which the game answers with nothing to read, from a client that joined the game as
        from the one that created it (in game).

        A drawing lasts until the next debug request, so one that should stay on screen goes with every request.
        """
        self._send(sc2api_pb2.Request(debug=sc2api_pb2.RequestDebug(debug=commands)))

    def query(self, request: query_pb2.RequestQuery) -> query_pb2.ResponseQuery:
        """Ask the game `request`'s questions, and return its answers, each list in the order asked."""
        return self._send(sc2api_pb2.Request(query=request), "query").query

    def save_replay(self) -> bytes:
        """The replay of the game so far, as the bytes of a `.SC2Replay` file."""
        response = self._send(sc2api_pb2.Request(save_replay=sc2api_pb2.RequestSaveReplay()), "save_replay")
        return response.save_replay.data

    def leave_game(self) -> None:
        """Leave the game, conceding it if it has not already ended, and return the client to `launched`.

        Leaving when there is no game, or no connection left, is not an error.
        """
        with suppress(GameNotStartedError, GameEndedError, ConnectionClosedError):
            self._send(sc2api_pb2.Request(leave_game=sc2api_pb2.RequestLeaveGame()))
        self._forget_game()

    def quit(self) -> None:
        """Ask the game client to exit. A connection already gone is not an error here."""
        with suppress(ConnectionClosedError):
            self._send(sc2api_pb2.Request(quit=sc2api_pb2.RequestQuit()))

    def close(self) -> None:
        """Release the transport."""
        self._transport.close()

    def _forget_game(self) -> None:
        """Drop what the client kept of the last game, so none of it carries over to the next."""
        self._player_id = None
        self._results = {}

    def _observe(self, game_loop: int | None) -> sc2api_pb2.ResponseObservation:
        request = sc2api_pb2.RequestObservation()
        if game_loop is not None:
            request.game_loop = game_loop
        response = self._send(sc2api_pb2.Request(observation=request), "observation")
        return response.observation

    def _send(self, request: sc2api_pb2.Request, answer: _Answer | None = None) -> sc2api_pb2.Response:
        """Send `request` and return the response, checking that it holds `answer` and that the game did not refuse
        it. What the game says beside the answer asked for is a warning, which is logged."""
        response = self._transport.request(request)
        # An unset status field reads as `launched`, the first value the proto declares, so check that it is set.
        if response.HasField("status"):
            status = Status(response.status)
            if status is not self._status:
                logger.info("The game client is now {}", status)
                self._status = status
        if response.error:
            errors = list(response.error)
            if _GAME_OVER_ERRORS.intersection(errors):
                raise GameEndedError(f"the game is over: {'; '.join(errors)}")
            if _NOT_STARTED_ERRORS.intersection(errors):
                raise GameNotStartedError(f"no game has started: {'; '.join(errors)}")
            # With the answer there, the proto calls these warnings; without it, the reason there is none.
            if answer is None or not response.HasField(answer):
                raise ProtocolError(f"the game refused the request: {'; '.join(errors)}")
            logger.warning("The game answered {} with warnings: {}", answer, "; ".join(errors))
        if answer is not None and not response.HasField(answer):
            asked = response.WhichOneof("response") or "nothing"
            raise ProtocolError(f"the game was asked for {answer} and answered {asked}")
        return response
