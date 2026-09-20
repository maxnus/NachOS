"""Talking to a game played to find things out, which the in-game tools share: a game to play, ground to put
structures on, and the few requests those tools make of it."""

from collections.abc import Iterable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing, contextmanager
from dataclasses import dataclass

import numpy
from loguru import logger
from numpy.lib.stride_tricks import sliding_window_view
from s2clientprotocol import common_pb2, debug_pb2, error_pb2, query_pb2, raw_pb2, sc2api_pb2

from sc2nachos.gamemap import GameMap
from sc2nachos.geometry import Point
from sc2nachos.ids import UnitTypeId
from sc2nachos.ids.raw import RawUnitTypeId
from sc2nachos.launch import GameProcess, Installation, Map, free_port
from sc2nachos.match import Computer, Difficulty, Participant, Race
from sc2nachos.protocol import Client, GamePorts, PortPair, WebSocketTransport

MAP = "PylonAIE_v4"

# A unit created for player 0 is the map's own, and the game then reports it as belonging to player 16.
NEUTRAL = 0
NEUTRAL_REPORTED = 16

SUCCESS = error_pb2.ActionResult.Success


def reported(owner: int) -> int:
    """The player the game reports a unit created for `owner` as belonging to."""
    return NEUTRAL_REPORTED if owner == NEUTRAL else owner


class OpenGround:
    """The ground a structure can be put on that nothing has claimed yet."""

    def __init__(self, game_map: GameMap, units: Iterable[raw_pb2.Unit]) -> None:
        grid = game_map.placement
        self._origin = grid.origin
        self._free = numpy.array(grid.values, dtype=bool)
        for unit in units:
            reach = int(unit.radius) + 2
            self._claim(int(unit.pos.x), int(unit.pos.y), reach)

    def claim(self, near: Point, half: int) -> Point:
        """The center of the free square `2 * half + 1` tiles across nearest to `near`, which is then taken, so that
        what is put down next does not land on it."""
        size = 2 * half + 1
        # Every square of that size the grid holds, marked where all of its tiles are free, by its lowest corner.
        fits = sliding_window_view(self._free, (size, size)).all(axis=(2, 3))
        xs, ys = numpy.nonzero(fits)
        if not len(xs):
            raise RuntimeError(f"no free ground {size} tiles across is left")
        x0, y0 = near.x - self._origin.x - half, near.y - self._origin.y - half
        best = int(numpy.argmin((xs - x0) ** 2 + (ys - y0) ** 2))
        x, y = int(xs[best]) + half, int(ys[best]) + half
        self._claim(x, y, half)
        return Point((self._origin.x + x + 0.5, self._origin.y + y + 0.5))

    def _claim(self, x: int, y: int, half: int) -> None:
        """Take the square `2 * half + 1` tiles across centered on the tile at index `(x, y)`."""
        self._free[max(x - half, 0) : x + half + 1, max(y - half, 0) : y + half + 1] = False


@dataclass(frozen=True, slots=True)
class Sandbox:
    """The requests an in-game tool makes, for `player` in the game `client` has joined."""

    client: Client
    transport: WebSocketTransport
    player: int

    def units(self) -> list[raw_pb2.Unit]:
        """Every unit in an observation made now."""
        return list(self.client.observation().observation.raw_data.units)

    def debug(self, *commands: debug_pb2.DebugCommand) -> None:
        self.client.debug(commands)

    def cheat(self, *names: str) -> None:
        """Turn on the debug game states named, such as `all_resources`."""
        state = debug_pb2.DebugGameState
        self.debug(*(debug_pb2.DebugCommand(game_state=getattr(state, name)) for name in names))

    def create(self, unit_type: UnitTypeId, owner: int, at: Point) -> debug_pb2.DebugCommand:
        """The command that creates one `unit_type` for `owner` at `at`."""
        position = common_pb2.Point2D(x=at.x, y=at.y)
        return debug_pb2.DebugCommand(
            create_unit=debug_pb2.DebugCreateUnit(unit_type=unit_type, owner=owner, pos=position, quantity=1)
        )

    def spawn(self, requests: Sequence[tuple[UnitTypeId, int, Point]]) -> list[raw_pb2.Unit]:
        """Create every unit asked for, and return those the game really made, which it does not always.

        What the computer happened to make meanwhile is left out, unless it is of a type and owner asked for.
        """
        before = {unit.tag for unit in self.units()}
        self.debug(*(self.create(unit_type, owner, at) for unit_type, owner, at in requests))
        self.client.step(4)
        wanted = {(unit_type, reported(owner)) for unit_type, owner, _ in requests}
        made = [unit for unit in self.units() if unit.tag not in before and (unit.unit_type, unit.owner) in wanted]
        missing = wanted - {(unit.unit_type, unit.owner) for unit in made}
        for unit_type, owner in sorted(missing):
            logger.warning("The game did not create a {} for player {}", RawUnitTypeId(unit_type).name, owner)
        return made

    def kill(self, tags: Iterable[int]) -> None:
        if tags := list(tags):
            self.debug(debug_pb2.DebugCommand(kill_unit=debug_pb2.DebugKillUnit(tag=tags)))

    def set_value(
        self, kind: debug_pb2.DebugSetUnitValue.UnitValue.ValueType, value: float, tags: Iterable[int]
    ) -> None:
        """Set one of each unit's vitals, such as its energy."""
        set_value = debug_pb2.DebugSetUnitValue
        commands = [set_value(unit_value=kind, value=value, unit_tag=tag) for tag in tags]
        if commands:
            self.debug(*(debug_pb2.DebugCommand(unit_value=command) for command in commands))

    def offered(self, tags: Iterable[int]) -> dict[int, list[int]]:
        """Every ability each unit is offered, whatever it would cost."""
        query = query_pb2.RequestQuery(
            abilities=[query_pb2.RequestQueryAvailableAbilities(unit_tag=tag) for tag in tags],
            ignore_resource_requirements=True,
        )
        response = self.transport.request(sc2api_pb2.Request(query=query))
        answers = response.query.abilities
        return {answer.unit_tag: [ability.ability_id for ability in answer.abilities] for answer in answers}

    def placeable(self, ability: int, points: Sequence[Point]) -> list[bool]:
        """Whether the structure `ability` puts up could be put up at each of `points` now."""
        placements = [
            query_pb2.RequestQueryBuildingPlacement(ability_id=ability, target_pos=common_pb2.Point2D(x=p.x, y=p.y))
            for p in points
        ]
        response = self.transport.request(sc2api_pb2.Request(query=query_pb2.RequestQuery(placements=placements)))
        return [answer.result == SUCCESS for answer in response.query.placements]

    def order(self, ability: int, tag: int, target: Point | int | None = None) -> error_pb2.ActionResult.ValueType:
        """Order `ability` on the unit `tag`, aimed at a point, a unit's tag or nothing, and return the answer."""
        command = raw_pb2.ActionRawUnitCommand(ability_id=ability, unit_tags=[tag])
        if isinstance(target, Point):
            command.target_world_space_pos.x, command.target_world_space_pos.y = target
        elif target is not None:
            command.target_unit_tag = target
        action = sc2api_pb2.Action(action_raw=raw_pb2.ActionRaw(unit_command=command))
        return self.client.act([action]).result[0]


def join_with(transport: WebSocketTransport, race: Race, interface: sc2api_pb2.InterfaceOptions) -> int:
    """Join the waiting game as `race` asking for `interface`, and answer the player id.

    NachOS's own client asks for the raw interface and nothing else, so a tool that needs another joins here.
    """
    request = sc2api_pb2.RequestJoinGame(race=race.value, options=interface, player_name="NachOS")
    joined = transport.request(sc2api_pb2.Request(join_game=request)).join_game
    # An unset error field reads as the first refusal the proto declares, so ask before reading it.
    if joined.HasField("error"):
        reason = sc2api_pb2.ResponseJoinGame.Error.Name(joined.error)
        raise RuntimeError(f"the game refused the join as {reason}: {joined.error_details or 'no detail given'}")
    return joined.player_id


@contextmanager
def playing(
    race: Race,
    installation: Installation,
    *,
    realtime: bool = False,
    interface: sc2api_pb2.InterfaceOptions | None = None,
) -> Iterator[Sandbox]:
    """A game on `MAP` as `race` against the easiest computer, which keeps the game open and leaves the player alone.

    A `realtime` game runs on its own and is never stepped, and an `interface` other than the raw one is joined for
    here rather than through the client.
    """
    game_map = Map.find(MAP, installation=installation)
    with GameProcess.launch(installation, window=(1024, 768)) as game:
        transport = WebSocketTransport.connect(game.url)
        with closing(Client(transport)) as client:
            try:
                players = [Participant(), Computer(race, Difficulty.VERY_EASY)]
                client.create_game(game_map.path, players, realtime=realtime)
                if interface is None:
                    player = client.join_game(race, name="NachOS")
                else:
                    player = join_with(transport, race, interface)
                yield Sandbox(client, transport, player)
            finally:
                client.leave_game()
                client.quit()


@dataclass(frozen=True, slots=True)
class Rivals:
    """A game on `MAP` between two players, both played from here: `me`, and `enemy`, whose units `me` meets."""

    me: Sandbox
    enemy: Sandbox
    _pool: ThreadPoolExecutor

    def step(self, count: int) -> None:
        """Let `count` steps pass, which a game of two does only once both players ask for them."""
        for future in [self._pool.submit(side.client.step, count) for side in (self.me, self.enemy)]:
            future.result()


@contextmanager
def playing_rivals(race: Race, installation: Installation) -> Iterator[Rivals]:
    """A game on `MAP` between two players of `race`, each on a client of its own. A debug cheat is a toggle for the
    whole game, so only one of them turns each on."""
    game_map = Map.find(MAP, installation=installation)
    with ExitStack() as stack, ThreadPoolExecutor(2) as pool:
        games = [stack.enter_context(GameProcess.launch(installation, window=(800, 600))) for _ in range(2)]
        transports = [WebSocketTransport.connect(game.url) for game in games]
        clients = [stack.enter_context(closing(Client(transport))) for transport in transports]
        try:
            clients[0].create_game(game_map.path, [Participant(), Participant()])
            ports = GamePorts(PortPair(free_port(), free_port()), (PortPair(free_port(), free_port()),))
            # Each join waits for the other, so they are sent together.
            joins = [pool.submit(client.join_game, race, ports=ports) for client in clients]
            me, enemy = (
                Sandbox(client, transport, join.result())
                for client, transport, join in zip(clients, transports, joins, strict=True)
            )
            yield Rivals(me, enemy, pool)
        finally:
            for client in clients:
                client.leave_game()
                client.quit()
