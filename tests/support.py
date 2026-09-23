"""Fakes and builders shared by the tests that run the library without a game."""

from collections import deque
from collections.abc import Iterable
from typing import Any

import numpy
from s2clientprotocol import common_pb2, data_pb2, debug_pb2, raw_pb2, sc2api_pb2, score_pb2
from websocket import WebSocketConnectionClosedException

from sc2nachos._game import _Game
from sc2nachos.enemy import Enemy, UpgradeInference
from sc2nachos.events import (
    AlertEvent,
    ChatEvent,
    EnemyUnitCloakChangedEvent,
    EnemyUnitDamagedEvent,
    EnemyUnitEnergyLostEvent,
    EnemyUnitEnteredSightEvent,
    EnemyUnitFirstSeenEvent,
    EnemyUnitGainedBuffEvent,
    EnemyUnitLeftSightEvent,
    EnemyUnitLostBuffEvent,
    Event,
    EventBus,
    EventFilter,
    OwnActionEvent,
    OwnConstructionFinishedEvent,
    OwnConstructionStartedEvent,
    OwnUnitCloakChangedEvent,
    OwnUnitCreatedEvent,
    OwnUnitDamagedEvent,
    OwnUnitEnergyLostEvent,
    OwnUnitGainedBuffEvent,
    OwnUnitLostBuffEvent,
    OwnUpgradeFinishedEvent,
    OwnWarpInFinishedEvent,
    UnitAllianceChangedEvent,
    UnitDiedEvent,
    UnitFoundDeadEvent,
    UnitTypeChangedEvent,
)
from sc2nachos.gamedata import GameData
from sc2nachos.gamemap import GameMap
from sc2nachos.geometry import Point
from sc2nachos.ids import UnitTypeId
from sc2nachos.match import Result
from sc2nachos.orders import OrderBook
from sc2nachos.protocol import Client, Status
from sc2nachos.state._state import _State
from sc2nachos.units import Alliance, Unit, Units, Visibility
from sc2nachos.units._tracking import _Tracker


class FakeTransport:
    """A `Transport` that returns prepared responses in order and records every request."""

    def __init__(self, *responses: sc2api_pb2.Response) -> None:
        self.requests: list[sc2api_pb2.Request] = []
        self.closed = False
        self._responses = deque(responses)

    def request(self, request: sc2api_pb2.Request) -> sc2api_pb2.Response:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("the client asked more of the transport than the test prepared")
        return self._responses.popleft()

    def close(self) -> None:
        self.closed = True


class FakeWebSocket:
    """The three methods `WebSocketTransport` uses, over prepared payloads, or raising `failure` from `recv`."""

    def __init__(self, *payloads: str | bytes, failure: Exception | None = None) -> None:
        self.sent: list[bytes] = []
        self.closed = False
        self._payloads: deque[str | bytes] = deque(payloads)
        self._failure = failure

    def send_binary(self, payload: bytes) -> int:
        if self.closed:
            raise WebSocketConnectionClosedException("socket is already closed.")
        self.sent.append(payload)
        return len(payload)

    def recv(self) -> str | bytes:
        if self._failure is not None:
            raise self._failure
        if not self._payloads:
            raise WebSocketConnectionClosedException("Connection to remote host was lost.")
        return self._payloads.popleft()

    def close(self) -> None:
        self.closed = True


def make_response(status: Status | None = Status.IN_GAME, **fields: Any) -> sc2api_pb2.Response:
    """A response with `fields`, and `status` unless that is `None`."""
    response = sc2api_pb2.Response(**fields)
    if status is not None:
        response.status = status.value
    return response


def make_bits(*rows: str, drawn: str = "#") -> common_pb2.ImageData:
    """A one-bit image from `rows` of characters, top row first, set where the character is in `drawn`."""
    bits = [char in drawn for row in reversed(rows) for char in row]
    size = common_pb2.Size2DI(x=len(rows[0]), y=len(rows))
    return common_pb2.ImageData(bits_per_pixel=1, size=size, data=numpy.packbits(bits).tobytes())


def make_bytes(*rows: list[int]) -> common_pb2.ImageData:
    """A one-byte image from `rows` of values, top row first."""
    data = bytes(value for row in reversed(rows) for value in row)
    return common_pb2.ImageData(bits_per_pixel=8, size=common_pb2.Size2DI(x=len(rows[0]), y=len(rows)), data=data)


def make_game_info(
    *rows: str,
    playable: tuple[int, int, int, int] | None = None,
    heights: common_pb2.ImageData | None = None,
    start_locations: tuple[tuple[float, float], ...] = (),
) -> sc2api_pb2.ResponseGameInfo:
    """A map drawn in `rows`, top row first, or eight by eight of open ground.

    `#` is open ground, `~` is pathable but not buildable, and `.` is neither. The whole map is playable unless
    `playable` gives a smaller area as `(x0, y0, x1, y1)`, and flat unless `heights` is given.
    """
    rows = rows or ("#" * 8,) * 8
    width, height = len(rows[0]), len(rows)
    x0, y0, x1, y1 = playable or (0, 0, width, height)
    return sc2api_pb2.ResponseGameInfo(
        map_name="Somewhere",
        start_raw=raw_pb2.StartRaw(
            map_size=common_pb2.Size2DI(x=width, y=height),
            pathing_grid=make_bits(*rows, drawn="#~"),
            placement_grid=make_bits(*rows),
            terrain_height=heights or make_bytes(*([128] * width for _ in range(height))),
            playable_area=common_pb2.RectangleI(p0=common_pb2.PointI(x=x0, y=y0), p1=common_pb2.PointI(x=x1, y=y1)),
            start_locations=[common_pb2.Point2D(x=x, y=y) for x, y in start_locations],
        ),
    )


def make_observation(
    game_loop: int = 0,
    *results: tuple[int, Result],
    units: Iterable[raw_pb2.Unit] = (),
    dead: Iterable[int] = (),
    score: score_pb2.Score | None = None,
    common: sc2api_pb2.PlayerCommon | None = None,
    upgrades: Iterable[int] = (),
    visibility: common_pb2.ImageData | None = None,
    creep: common_pb2.ImageData | None = None,
    effects: Iterable[raw_pb2.Effect] = (),
    chat: Iterable[tuple[int, str]] = (),
    actions: Iterable[sc2api_pb2.Action] = (),
    action_errors: Iterable[sc2api_pb2.ActionError] = (),
    alerts: Iterable[sc2api_pb2.Alert.ValueType] = (),
) -> sc2api_pb2.ResponseObservation:
    """An observation at `game_loop` with `units`, the tags of the `dead`, the `results` if the game is over, and the
    rest of what an observation reports. `chat` holds each message as the sender's player id and the text."""
    raw = raw_pb2.ObservationRaw(
        player=raw_pb2.PlayerRaw(upgrade_ids=upgrades),
        units=units,
        map_state=raw_pb2.MapState(visibility=visibility, creep=creep),
        event=raw_pb2.Event(dead_units=dead),
        effects=effects,
    )
    observation = sc2api_pb2.Observation(
        game_loop=game_loop, player_common=common, score=score, raw_data=raw, alerts=alerts
    )
    return sc2api_pb2.ResponseObservation(
        actions=actions,
        action_errors=action_errors,
        chat=[sc2api_pb2.ChatReceived(player_id=player, message=text) for player, text in chat],
        observation=observation,
        player_result=[sc2api_pb2.PlayerResult(player_id=player, result=result.value) for player, result in results],
    )


def make_unit(
    tag: int,
    unit_type: int = UnitTypeId.MARINE,
    *,
    at: tuple[float, float] = (10.0, 10.0),
    alliance: Alliance = Alliance.OWN,
    visibility: Visibility = Visibility.IN_VISION,
    **fields: Any,
) -> raw_pb2.Unit:
    """A unit as the game reports it: this player's marine, in sight, unless the arguments say otherwise."""
    return raw_pb2.Unit(
        tag=tag,
        unit_type=unit_type,
        pos=common_pb2.Point(x=at[0], y=at[1]),
        alliance=alliance.value,
        display_type=visibility.value,
        **fields,
    )


# Every event a turn can report, in the order it reports them.
HAPPENINGS: tuple[type[Event], ...] = (
    OwnUnitCreatedEvent,
    EnemyUnitFirstSeenEvent,
    UnitTypeChangedEvent,
    UnitAllianceChangedEvent,
    OwnConstructionStartedEvent,
    OwnConstructionFinishedEvent,
    OwnWarpInFinishedEvent,
    OwnUpgradeFinishedEvent,
    OwnUnitDamagedEvent,
    EnemyUnitDamagedEvent,
    OwnUnitEnergyLostEvent,
    EnemyUnitEnergyLostEvent,
    OwnUnitCloakChangedEvent,
    EnemyUnitCloakChangedEvent,
    OwnUnitGainedBuffEvent,
    EnemyUnitGainedBuffEvent,
    OwnUnitLostBuffEvent,
    EnemyUnitLostBuffEvent,
    EnemyUnitEnteredSightEvent,
    EnemyUnitLeftSightEvent,
    UnitDiedEvent,
    UnitFoundDeadEvent,
    OwnActionEvent,
    ChatEvent,
    AlertEvent,
)


def record(events: EventBus, *event_types: type[Event] | EventFilter[Any]) -> list[Any]:
    """A list that collects every event of `event_types`, or matching the filters, handed out from now on."""
    seen: list[Any] = []
    for event_type in event_types:
        events.on(event_type)(lambda event: seen.append(event))
    return seen


def make_tables(*units: data_pb2.UnitTypeData, abilities: Iterable[data_pb2.AbilityData] = ()) -> GameData:
    """Game data with a row for each of `units` and each of `abilities`."""
    return GameData(sc2api_pb2.ResponseData(units=units, abilities=abilities))


def played(
    game: _Game | None,
    client: Client,
    game_map: GameMap,
    tracker: _Tracker,
    observation: sc2api_pb2.ResponseObservation,
    events: EventBus,
    *,
    infer: UpgradeInference = UpgradeInference.NONE,
) -> _Game:
    """Feed `observation` to `game` and hand its events to `events`, as `Api.play` does each turn. With `game` as
    `None`, first build a game over `tracker` the way `_Game.start` does, without asking the client."""
    step = observation.observation.game_loop
    if game is None:
        state = _State(observation, tracker, game_map)
        orders = OrderBook(tracker.game_data)
        game = _Game(
            client, game_map, tracker.game_data, tracker.enemy, infer, tracker, orders, observation, state, step
        )
    game._take_in(observation, step)
    events._set_step(step)
    events._hand_out(game.report(events))
    return game


def make_client(*responses: sc2api_pb2.Response) -> tuple[Client, FakeTransport]:
    """A client over a transport that will answer `responses`, and that transport."""
    transport = FakeTransport(*responses)
    return Client(transport), transport


class RealGame:
    """A game against the computer, stepped by hand, with its units tracked and each observation's events handed to
    `events`."""

    def __init__(self, client: Client, player: int, events: EventBus | None = None) -> None:
        self.client = client
        self.player = player
        self.events = events or EventBus()
        self.map = GameMap(client.game_info())
        self.enemy = Enemy()
        self.tracker = _Tracker(GameData(client.game_data()), self.enemy)
        self.game: _Game | None = None
        self.state = self._observe()

    def _observe(self) -> _State:
        response = self.client.observation()
        self.step = response.observation.game_loop
        basic = UpgradeInference.BASIC
        self.game = played(self.game, self.client, self.map, self.tracker, response, self.events, infer=basic)
        return self.game.state

    def turn(self, steps: int) -> Units[Unit[Any]]:
        """Let `steps` pass, then observe."""
        self.client.step(steps)
        self.state = self._observe()
        return self.tracker.unit_tracker.present

    def debug(self, *commands: debug_pb2.DebugCommand) -> None:
        self.client.debug(commands)

    def create(self, unit_type: UnitTypeId, at: Point, *, owner: int | None = None) -> debug_pb2.DebugCommand:
        """A debug command that creates a `unit_type` at `at`, owned by this player unless `owner` says otherwise."""
        position = common_pb2.Point2D(x=at.x, y=at.y)
        unit = debug_pb2.DebugCreateUnit(unit_type=unit_type, owner=owner or self.player, pos=position, quantity=1)
        return debug_pb2.DebugCommand(create_unit=unit)

    def kill(self, *units: Unit[Any]) -> debug_pb2.DebugCommand:
        return debug_pb2.DebugCommand(kill_unit=debug_pb2.DebugKillUnit(tag=[unit.tag for unit in units]))

    def order(self, ability: int, unit: Unit[Any], *, target: Unit[Any] | Point | None = None) -> None:
        command = raw_pb2.ActionRawUnitCommand(ability_id=ability, unit_tags=[unit.tag])
        if isinstance(target, Point):
            command.target_world_space_pos.x, command.target_world_space_pos.y = target
        elif target is not None:
            command.target_unit_tag = target.tag
        self.client.act([sc2api_pb2.Action(action_raw=raw_pb2.ActionRaw(unit_command=command))])

    def newest(self, unit_type: UnitTypeId) -> Unit[Any]:
        """The newest unit of `unit_type`, by id."""
        return max(self.tracker.unit_tracker.present.of_type(unit_type), key=lambda unit: unit.id)

    def open_ground(self, near: Point, *, size: int = 2) -> Point:
        """The center of the buildable `size` by `size` square nearest `near`."""
        placement = self.map.placement
        center = near.snapped(step=1) + ((0.5, 0.5) if size % 2 else (0.0, 0.0))
        half = (size - 1) / 2
        offsets = [(dx - half, dy - half) for dx in range(size) for dy in range(size)]
        for reach in range(12):
            for dx in range(-reach, reach + 1):
                for dy in range(-reach, reach + 1):
                    spot = center + (dx, dy)
                    if all(placement[spot + offset] for offset in offsets):
                        return spot
        raise AssertionError(f"no open ground near {near}")
