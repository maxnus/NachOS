"""What the in-game tools share: a game to play, free ground to put structures on, and the requests they make of the
game."""

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
from sc2nachos.launch import GameProcess, Installation, MapFile, free_port
from sc2nachos.match import Computer, Difficulty, Participant, Race
from sc2nachos.protocol import Client, GamePorts, PortPair, WebSocketTransport

MAP = "PylonAIE_v4"

# A unit created for player 0 belongs to the map, and the game reports it as player 16's.
NEUTRAL = 0
NEUTRAL_REPORTED = 16

SUCCESS = error_pb2.ActionResult.Success


def reported(owner: int) -> int:
    """The owner the game reports for a unit created for `owner`."""
    return NEUTRAL_REPORTED if owner == NEUTRAL else owner


class OpenGround:
    """The placeable ground nothing has claimed yet."""

    def __init__(self, game_map: GameMap, units: Iterable[raw_pb2.Unit]) -> None:
        grid = game_map.placement
        self._origin = grid.origin
        self._free = numpy.array(grid.values, dtype=bool)
        self._taken: dict[tuple[float, float], tuple[int, int, int]] = {}
        for unit in units:
            reach = int(unit.radius) + 2
            self._take(int(unit.pos.x), int(unit.pos.y), reach, free=False)

    def claim(self, near: Point, half: int) -> Point:
        """Take the free square `2 * half + 1` tiles across nearest to `near`, and return its center. `release` gives
        it back."""
        size = 2 * half + 1
        # Each square of that size, by its lowest corner: True where every tile in it is free.
        fits = sliding_window_view(self._free, (size, size)).all(axis=(2, 3))
        xs, ys = numpy.nonzero(fits)
        if not len(xs):
            raise RuntimeError(f"no free ground {size} tiles across is left")
        x0, y0 = near.x - self._origin.x - half, near.y - self._origin.y - half
        best = int(numpy.argmin((xs - x0) ** 2 + (ys - y0) ** 2))
        x, y = int(xs[best]) + half, int(ys[best]) + half
        self._take(x, y, half, free=False)
        at = Point((self._origin.x + x + 0.5, self._origin.y + y + 0.5))
        self._taken[at.x, at.y] = (x, y, half)
        return at

    def release(self, at: Point) -> None:
        """Give back the square `claim` returned `at` for. A point never claimed is ignored, and the ground the map's
        own units stand on is never given back."""
        if (square := self._taken.pop((at.x, at.y), None)) is not None:
            self._take(*square, free=True)

    def _take(self, x: int, y: int, half: int, *, free: bool) -> None:
        """Mark the square `2 * half + 1` tiles across centered on tile `(x, y)` as taken or free."""
        self._free[max(x - half, 0) : x + half + 1, max(y - half, 0) : y + half + 1] = free


@dataclass(frozen=True, slots=True)
class Sandbox:
    """The requests an in-game tool makes of the game `client` has joined, as `player`."""

    client: Client
    transport: WebSocketTransport
    player: int

    def units(self) -> list[raw_pb2.Unit]:
        """Every unit in a fresh observation."""
        return list(self.client.observation().observation.raw_data.units)

    def debug(self, *commands: debug_pb2.DebugCommand) -> None:
        self.client.debug(commands)

    def cheat(self, *names: str) -> None:
        """Turn on the named debug game states, such as `all_resources`."""
        state = debug_pb2.DebugGameState
        self.debug(*(debug_pb2.DebugCommand(game_state=getattr(state, name)) for name in names))

    def create(self, unit_type: UnitTypeId, owner: int, at: Point) -> debug_pb2.DebugCommand:
        """The command that creates one `unit_type` for `owner` at `at`."""
        position = common_pb2.Point2D(x=at.x, y=at.y)
        return debug_pb2.DebugCommand(
            create_unit=debug_pb2.DebugCreateUnit(unit_type=unit_type, owner=owner, pos=position, quantity=1)
        )

    def spawn(self, requests: Sequence[tuple[UnitTypeId, int, Point]]) -> list[raw_pb2.Unit]:
        """Create the units requested and return those the game made; it does not always make every one.

        Units the computer made meanwhile are left out unless their type and owner match a request.
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
        """The abilities each unit is offered, ignoring what they cost."""
        query = query_pb2.RequestQuery(
            abilities=[query_pb2.RequestQueryAvailableAbilities(unit_tag=tag) for tag in tags],
            ignore_resource_requirements=True,
        )
        response = self.transport.request(sc2api_pb2.Request(query=query))
        answers = response.query.abilities
        return {answer.unit_tag: [ability.ability_id for ability in answer.abilities] for answer in answers}

    def placeable(self, ability: int, points: Sequence[Point]) -> list[bool]:
        """Whether the structure `ability` builds could go up at each of `points` right now."""
        placements = [
            query_pb2.RequestQueryBuildingPlacement(ability_id=ability, target_pos=common_pb2.Point2D(x=p.x, y=p.y))
            for p in points
        ]
        response = self.transport.request(sc2api_pb2.Request(query=query_pb2.RequestQuery(placements=placements)))
        return [answer.result == SUCCESS for answer in response.query.placements]

    def order(self, ability: int, tag: int, target: Point | int | None = None) -> error_pb2.ActionResult.ValueType:
        """Order `ability` on the unit `tag`, aimed at a point, a unit's tag or nothing, and return the verdict."""
        command = raw_pb2.ActionRawUnitCommand(ability_id=ability, unit_tags=[tag])
        if isinstance(target, Point):
            command.target_world_space_pos.x, command.target_world_space_pos.y = target
        elif target is not None:
            command.target_unit_tag = target
        action = sc2api_pb2.Action(action_raw=raw_pb2.ActionRaw(unit_command=command))
        return self.client.act([action]).result[0]


def join_with(transport: WebSocketTransport, race: Race, interface: sc2api_pb2.InterfaceOptions) -> int:
    """Join the waiting game as `race` with `interface`, and return the player id.

    The NachOS client asks only for the raw interface; a tool that needs another joins here.
    """
    request = sc2api_pb2.RequestJoinGame(race=race.value, options=interface, player_name="NachOS")
    joined = transport.request(sc2api_pb2.Request(join_game=request)).join_game
    # An unset `error` reads as the proto's first refusal, so check that it is set.
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

    A `realtime` game is never stepped. An `interface` other than the raw one is joined with here rather than through
    the client.
    """
    game_map = MapFile.find(MAP, installation=installation)
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
    """A game on `MAP` between two players both played from here: `me`, and the `enemy` it meets."""

    me: Sandbox
    enemy: Sandbox
    _pool: ThreadPoolExecutor

    def step(self, count: int) -> None:
        """Let `count` steps pass. A game of two steps only once both players have asked."""
        for future in [self._pool.submit(side.client.step, count) for side in (self.me, self.enemy)]:
            future.result()


@contextmanager
def playing_rivals(race: Race, installation: Installation) -> Iterator[Rivals]:
    """A game on `MAP` between two players of `race`, each on its own client. A debug cheat toggles for the whole
    game, so only one player turns each on."""
    game_map = MapFile.find(MAP, installation=installation)
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
