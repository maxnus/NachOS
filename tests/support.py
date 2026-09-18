"""Stand-ins for the game, shared by the tests that drive the library without one."""

from collections import deque
from collections.abc import Iterable
from typing import Any

import numpy
from s2clientprotocol import common_pb2, data_pb2, debug_pb2, raw_pb2, sc2api_pb2, score_pb2
from websocket import WebSocketConnectionClosedException

from sc2nachos._reporter import _Reporter
from sc2nachos.enemy import Enemy
from sc2nachos.events import (
    ChatEvent,
    EnemyUnitEnteredSightEvent,
    EnemyUnitFirstSeenEvent,
    EnemyUnitLeftSightEvent,
    Event,
    EventBus,
    OwnActionEvent,
    OwnConstructionFinishedEvent,
    OwnConstructionStartedEvent,
    OwnUnitCreatedEvent,
    OwnUpgradeFinishedEvent,
    OwnWarpInFinishedEvent,
    UnitAllianceChangedEvent,
    UnitDamagedEvent,
    UnitDiedEvent,
    UnitFoundDeadEvent,
    UnitTypeChangedEvent,
)
from sc2nachos.events._alert_events import _ALERT_EVENTS
from sc2nachos.gamedata import GameData
from sc2nachos.gamemap import GameMap
from sc2nachos.geometry import Point
from sc2nachos.ids import UnitTypeId
from sc2nachos.match import Result
from sc2nachos.protocol import Client, Status
from sc2nachos.state._state import _State
from sc2nachos.units import Alliance, Unit, Units, Visibility
from sc2nachos.units._tracker import _UnitTracker


class FakeTransport:
    """A `Transport` that answers from a prepared queue and remembers what it was asked."""

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
    """The three methods `WebSocketTransport` uses, over a prepared queue of payloads or a `failure` to raise."""

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
    """A response carrying `fields`, and `status` unless it is given as `None`."""
    response = sc2api_pb2.Response(**fields)
    if status is not None:
        response.status = status.value
    return response


def make_bits(*rows: str, drawn: str = "#") -> common_pb2.ImageData:
    """A one-bit image drawn as rows of characters, the top row first, set wherever one of `drawn` stands."""
    bits = [char in drawn for row in reversed(rows) for char in row]
    size = common_pb2.Size2DI(x=len(rows[0]), y=len(rows))
    return common_pb2.ImageData(bits_per_pixel=1, size=size, data=numpy.packbits(bits).tobytes())


def make_bytes(*rows: list[int]) -> common_pb2.ImageData:
    """A one-byte image of `rows` of values, the top row first, the way the map would look."""
    data = bytes(value for row in reversed(rows) for value in row)
    return common_pb2.ImageData(bits_per_pixel=8, size=common_pb2.Size2DI(x=len(rows[0]), y=len(rows)), data=data)


def make_game_info(
    *rows: str,
    playable: tuple[int, int, int, int] | None = None,
    heights: common_pb2.ImageData | None = None,
    start_locations: tuple[tuple[float, float], ...] = (),
) -> sc2api_pb2.ResponseGameInfo:
    """A map drawn in rows, the top row first, or eight by eight of open ground.

    `#` is open ground, `~` ground a unit can walk over but not build on, and `.` ground it can do neither on.
    The map is playable from corner to corner unless `playable` gives the corners `(x0, y0, x1, y1)` of a smaller
    area, and level unless `heights` says otherwise.
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
    alerts: Iterable[sc2api_pb2.Alert.ValueType] = (),
) -> sc2api_pb2.ResponseObservation:
    """What the game saw at `game_loop`: `units`, the tags of those that died, how it ended if it has, and the rest of
    what an observation reports, each message sent to the chat as the sender's id and the text."""
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
    """A unit as the game would report it: this player's marine, in sight, unless told otherwise."""
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
    UnitDamagedEvent,
    EnemyUnitEnteredSightEvent,
    EnemyUnitLeftSightEvent,
    UnitDiedEvent,
    UnitFoundDeadEvent,
    OwnActionEvent,
    ChatEvent,
    *(event_type for event_type in _ALERT_EVENTS.values() if event_type is not None),
)


def record(events: EventBus, *event_types: type[Event]) -> list[Any]:
    """The events of `event_types` handed out from now on, in the order they were."""
    seen: list[Any] = []
    for event_type in event_types:
        events.on(event_type)(lambda event: seen.append(event))
    return seen


def make_tables(*units: data_pb2.UnitTypeData) -> GameData:
    """Tables holding a row for each of `units`."""
    return GameData(sc2api_pb2.ResponseData(units=units))


def make_client(*responses: sc2api_pb2.Response) -> tuple[Client, FakeTransport]:
    """A client over a transport that will answer `responses`, and that transport."""
    transport = FakeTransport(*responses)
    return Client(transport), transport


class RealGame:
    """A game against the computer, played a step at a time by hand, with its units tracked and what each observation
    reports has happened handed to the handlers of `events`."""

    def __init__(self, client: Client, player: int, events: EventBus | None = None) -> None:
        self.client = client
        self.player = player
        self.events = events or EventBus()
        self.map = GameMap(client.game_info())
        self.enemy = Enemy()
        self.tracker = _UnitTracker(GameData(client.game_data()), self.enemy)
        self.reporter = _Reporter(self.tracker)
        self.state = self._observe()

    def _observe(self) -> _State:
        response = self.client.observation()
        step = self.step = response.observation.game_loop
        self.tracker.update(response.observation.raw_data, step)
        self.enemy.assume_upgrades(*self.tracker.upgrade_reader.read_basic_upgrades(self.tracker.present_units))
        state = _State(response, self.tracker, self.map)
        self.reporter.report(self.events, response, state, step)
        return state

    def turn(self, steps: int) -> Units[Unit[Any]]:
        """Let `steps` pass, then observe."""
        self.client.step(steps)
        self.state = self._observe()
        return self.tracker.present_units

    def debug(self, *commands: debug_pb2.DebugCommand) -> None:
        self.client.debug(commands)

    def create(self, unit_type: UnitTypeId, at: Point, *, owner: int | None = None) -> debug_pb2.DebugCommand:
        """The command creating a unit of `unit_type` at `at`, the player's own unless another `owner` is given."""
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
        """The unit of `unit_type` first seen last."""
        return max(self.tracker.present_units.of_type(unit_type), key=lambda unit: unit.id)

    def open_ground(self, near: Point, *, size: int = 2) -> Point:
        """The center nearest to `near` of a square of `size` tiles a side that can all be built on."""
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
