"""Find what the game does with the requests a bot makes besides its orders: debug commands from a client that did not
create the game, the cheats that hand something out, chat, leaving, the pathing query, the tables it answers, and
what it says of each player.

Needs StarCraft II installed. Each sweep plays its own games on the sandbox's map:

- `debug-joined` plays a game of two players, both from here. The client that joined the game, and then the one that
  created it, each creates a marine, sends the `minerals` cheat and sends a drawing. It records what each was
  answered and what came of it.
- `cheats-given` removes this player's workers, then sends `minerals`, `gas`, `all_resources` and `upgrade` twice
  each. It records the minerals, vespene and upgrades after each send and after a wait, and whether `all_resources`
  refills what a train spends.
- `chat`, each trial after a rest of 30 game seconds and 5 real ones:
  - sends twelve short messages at once, a step apart, a second apart and five seconds apart, and ten of each of
    four sizes back to back, and reads which come back;
  - sends messages of several lengths, one to the team and one of non-ASCII text, and reads each back within 24
    steps;
  - sends one message of just under and one of just over the size that kills the game client, each in a game of its
    own;
  - counts the results the game answers for chat sent alongside a command and a camera move;
  - in games of two players, has one player send chat and leave in the same step, or 8 steps later with the other
    observing every step or not at all, and reads what the other received, and when.
- `leave` plays three games against the computer: one it leaves, one it surrenders and one it wins, the last two
  through `DebugEndGame`. In a game of two, both players leave in the same step. It records what the game answers and
  the client's status after each.
- `pathing` asks the game for pathing distances:
  - between points the same, 0.1, 0.5, 1 and 2 apart;
  - to a tile no path reaches;
  - from and to inside this player's townhall;
  - from a marine's, a reaper's, a colossus's and a medivac's tag across a cliff;
  - from an unknown tag;
  - into and out of a ring of supply depots, raised and lowered, every 2 steps, observing each time or not at all.

  It times one pair and 100, and compares 100 random pairs with `Grid.path_distance_from`.
- `tables` asks for the tables in a game against the computer, and in a game of two of each player, and records each
  row that differs from the first corpus game's, with the fields that differ, and how many rows differ only in which
  empty fields they set.
- `players` plays a game of two players, joined as "Alpha", playing Terran, and "Beta", asking for Random, and a game
  against a computer asking for Random, joined as "Alpha". It records the player list each client is answered: each
  player's id, type, races requested and actual, and name.

Run it as::

    uv run python tools/sweep_requests.py
    uv run python tools/sweep_requests.py pathing --out requests-pathing.json

The findings are written as JSON, one object per sweep.
"""

import argparse
import json
import math
import random
import statistics
import sys
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing
from pathlib import Path
from typing import Any

import numpy
import scipy.ndimage
from _sandbox import MAP, Cheat, OpenGround, Rivals, Sandbox, playing, playing_rivals
from generate_unit_types import corpus_answer
from loguru import logger
from s2clientprotocol import common_pb2, debug_pb2, query_pb2, raw_pb2, sc2api_pb2

from sc2nachos.gamemap import GameMap
from sc2nachos.geometry import Point
from sc2nachos.ids import UnitTypeId
from sc2nachos.ids.raw import RawAbilityId, RawUpgradeId
from sc2nachos.launch import GameProcess, Installation, MapFile, free_port
from sc2nachos.match import Computer, Difficulty, Participant, Race
from sc2nachos.protocol import Client, ConnectionClosedError, GamePorts, PortPair, WebSocketTransport

_OWN = raw_pb2.Alliance.Self
_BROADCAST = sc2api_pb2.ActionChat.Channel.Broadcast
_TEAM = sc2api_pb2.ActionChat.Channel.Team
# Steps waited for a cheat or a created unit to take effect; the in-game tests wait 8.
_SETTLE = 16
_TIMING_REPEATS = 20
# Steps rested before each chat trial, 30 game seconds, so that chat the game drops for coming too fast after the last
# trial's does not decide it.
_CHAT_REST = 672
# And real seconds, in case the game counts the time chat takes by the clock rather than in steps.
_CHAT_REST_SECONDS = 5.0


def _response(
    transport_request: Callable[[sc2api_pb2.Request], sc2api_pb2.Response], request: sc2api_pb2.Request
) -> dict[str, Any]:
    """What the game answered `request`: its errors, the kind of answer and the status, or the exception it raised."""
    try:
        response = transport_request(request)
    except Exception as error:  # noqa: BLE001 -- the sweep records whatever the game does
        return {"raised": f"{type(error).__name__}: {error}"}
    return {
        "errors": list(response.error),
        "answered": response.WhichOneof("response"),
        "status": sc2api_pb2.Status.Name(response.status) if response.HasField("status") else None,
    }


def _observe(transport_request: Callable[[sc2api_pb2.Request], sc2api_pb2.Response]) -> dict[str, Any]:
    """A fresh observation's status and results, and the chat in it by player, or the exception asking raised."""
    try:
        response = transport_request(sc2api_pb2.Request(observation=sc2api_pb2.RequestObservation()))
    except Exception as error:  # noqa: BLE001 -- the sweep records whatever the game does
        return {"raised": f"{type(error).__name__}: {error}"}
    observation = response.observation
    return {
        "errors": list(response.error),
        "status": sc2api_pb2.Status.Name(response.status) if response.HasField("status") else None,
        "results": [(entry.player_id, sc2api_pb2.Result.Name(entry.result)) for entry in observation.player_result],
        "chat": [(message.player_id, message.message) for message in observation.chat],
    }


def _debug(sandbox: Sandbox, *commands: debug_pb2.DebugCommand) -> dict[str, Any]:
    """Send `commands` straight to the game and return what it answered."""
    return _response(sandbox.transport.request, sc2api_pb2.Request(debug=sc2api_pb2.RequestDebug(debug=commands)))


def _chat(text: str, channel: sc2api_pb2.ActionChat.Channel.ValueType = _BROADCAST) -> sc2api_pb2.Action:
    return sc2api_pb2.Action(action_chat=sc2api_pb2.ActionChat(channel=channel, message=text))


def _own(units: Sequence[raw_pb2.Unit], unit_type: UnitTypeId) -> list[raw_pb2.Unit]:
    return [unit for unit in units if unit.alliance == _OWN and unit.unit_type == unit_type]


def _at(unit: raw_pb2.Unit) -> Point:
    return Point((unit.pos.x, unit.pos.y))


def _upgrade_name(upgrade: int) -> str:
    try:
        return RawUpgradeId(upgrade).name
    except ValueError:
        return str(upgrade)


# --- Debug commands from the client that joined


def _debug_trials(sandbox: Sandbox, rivals: Rivals, role: str) -> dict[str, Any]:
    """Create a marine, send the `minerals` cheat and a drawing from `sandbox`'s client, and what came of each."""
    units = sandbox.units()
    (townhall,) = _own(units, UnitTypeId.COMMAND_CENTER)
    marines = len(_own(units, UnitTypeId.MARINE))
    minerals = sandbox.client.observation().observation.player_common.minerals
    at = Point((townhall.pos.x, townhall.pos.y - 5))
    answers = {
        "create_unit": _debug(sandbox, sandbox.create(UnitTypeId.MARINE, sandbox.player, at)),
        "minerals_cheat": _debug(
            sandbox, debug_pb2.DebugCommand(game_state=debug_pb2.DebugGameState.Value(Cheat.MINERALS))
        ),
        "draw": _debug(
            sandbox,
            debug_pb2.DebugCommand(
                draw=debug_pb2.DebugDraw(
                    text=[debug_pb2.DebugText(text=role, virtual_pos=common_pb2.Point(x=0.5, y=0.5, z=0))]
                )
            ),
        ),
    }
    rivals.step(_SETTLE)
    after = sandbox.client.observation().observation
    return {
        "role": role,
        "player": sandbox.player,
        "answers": answers,
        "marines_made": len(_own(list(after.raw_data.units), UnitTypeId.MARINE)) - marines,
        "minerals_gained": after.player_common.minerals - minerals,
    }


def debug_joined(installation: Installation) -> list[dict[str, Any]]:
    """Debug commands from the client that joined a game of two, and from the one that created it."""
    with playing_rivals(Race.TERRAN, installation) as rivals:
        # `playing_rivals` creates the game on `me`'s client; `enemy` only joined it.
        findings = [_debug_trials(rivals.enemy, rivals, "joined"), _debug_trials(rivals.me, rivals, "created")]
    for finding in findings:
        logger.info(
            "{}: {} marines made, {} minerals gained, answers {}",
            finding["role"],
            finding["marines_made"],
            finding["minerals_gained"],
            finding["answers"],
        )
    return findings


# --- The cheats that hand something out


def _holdings(client: Client) -> dict[str, Any]:
    observation = client.observation().observation
    common = observation.player_common
    upgrades = sorted(_upgrade_name(upgrade) for upgrade in observation.raw_data.player.upgrade_ids)
    return {"step": observation.game_loop, "minerals": common.minerals, "vespene": common.vespene, "upgrades": upgrades}


def cheats_given(installation: Installation) -> list[dict[str, Any]]:
    """What `minerals`, `gas`, `all_resources` and `upgrade` give, sent once and again, with no workers mining."""
    findings = []
    with playing(Race.TERRAN, installation) as sandbox:
        client = sandbox.client
        sandbox.remove(unit.tag for unit in _own(sandbox.units(), UnitTypeId.SCV))
        for cheat in (Cheat.MINERALS, Cheat.GAS, Cheat.ALL_RESOURCES, Cheat.UPGRADE):
            finding: dict[str, Any] = {"cheat": cheat.value, "before": _holdings(client)}
            sandbox.cheat(cheat)
            client.step(_SETTLE)
            finding["after_one"] = _holdings(client)
            client.step(10 * _SETTLE)
            finding["after_a_wait"] = _holdings(client)
            if cheat is Cheat.ALL_RESOURCES:
                # Whether it hands out once, or keeps topping up what is spent.
                (townhall,) = _own(sandbox.units(), UnitTypeId.COMMAND_CENTER)
                finding["train_verdicts"] = [
                    int(sandbox.order(RawAbilityId.CommandCenterTrain_SCV, townhall.tag)) for _ in range(5)
                ]
                client.step(_SETTLE)
                finding["after_spending"] = _holdings(client)
            sandbox.cheat(cheat)
            client.step(_SETTLE)
            finding["after_two"] = _holdings(client)
            client.step(10 * _SETTLE)
            finding["after_two_and_a_wait"] = _holdings(client)
            findings.append(finding)
            logger.info("{}: {}", cheat.value, finding)
    return findings


# --- Chat


def _own_chat(sandbox: Sandbox) -> list[str]:
    """This player's own chat in a fresh observation."""
    return [message.message for message in sandbox.client.observation().chat if message.player_id == sandbox.player]


def _chat_back(sandbox: Sandbox, actions: Sequence[sc2api_pb2.Action]) -> dict[str, Any]:
    """Rest, send `actions`, and return the results answered and this player's chat seen within 2 and 24 steps."""
    sandbox.client.step(_CHAT_REST)
    time.sleep(_CHAT_REST_SECONDS)
    stale = _own_chat(sandbox)
    results = [int(result) for result in sandbox.client.act(actions).result]
    sandbox.client.step(2)
    soon = _own_chat(sandbox)
    sandbox.client.step(22)
    return {"results": results, "within_2": soon, "within_24": soon + _own_chat(sandbox), "stale": stale}


def _chat_rate(installation: Installation) -> dict[str, list[bool]]:
    """Which of twelve short messages come back, sent at once, a step apart, a second apart and five seconds apart."""
    findings = {}
    with playing(Race.TERRAN, installation) as sandbox:
        client = sandbox.client
        for name, gap in (("at_once", 0), ("a_step_apart", 1), ("a_second_apart", 22), ("five_seconds_apart", 112)):
            client.step(_CHAT_REST)
            sent = [f"{name} {index}" for index in range(12)]
            received = []
            if gap == 0:
                client.act([_chat(text) for text in sent])
            else:
                for text in sent:
                    client.act([_chat(text)])
                    client.step(gap)
                    received += _own_chat(sandbox)
            client.step(24)
            received += _own_chat(sandbox)
            findings[name] = [text in received for text in sent]
    return findings


def _chat_and_leave(installation: Installation, *, steps_between: int, watching: bool = True) -> dict[str, Any]:
    """One player of two chats and leaves `steps_between` steps later, the other observing after each step if
    `watching`; what the other receives, and when."""
    with playing_rivals(Race.TERRAN, installation) as rivals:
        leaver, stayer = rivals.me, rivals.enemy
        text = f"gg, leaving {steps_between} steps later"
        leaver.client.act([_chat(text)])
        before = []
        for _ in range(steps_between):
            rivals.step(1)
            seen = _observe(stayer.transport.request) if watching else {}
            before.append([message for player, message in seen.get("chat", []) if player == leaver.player])
        leave = _response(leaver.transport.request, sc2api_pb2.Request(leave_game=sc2api_pb2.RequestLeaveGame()))
        after = []
        for _ in range(4):
            step = sc2api_pb2.Request(step=sc2api_pb2.RequestStep(count=2))
            after.append({"step": _response(stayer.transport.request, step), **_observe(stayer.transport.request)})
            if after[-1].get("results") or "raised" in after[-1]:
                break
        arrived = [step for step, messages in enumerate(before, start=1) if messages]
        received = [text for entry in after for player, text in entry.get("chat", []) if player == leaver.player]
        return {
            "steps_between": steps_between,
            "watching": watching,
            "sent": text,
            "first_seen_after_steps": arrived[0] if arrived else None,
            "leave": leave,
            "stayer_after_the_leave": after,
            "received_after_the_leave": received,
        }


def _chat_flood(installation: Installation) -> dict[int, list[bool]]:
    """Which of ten messages of each size come back, sent back to back a step apart after a rest by the clock."""
    findings = {}
    with playing(Race.TERRAN, installation) as sandbox:
        client = sandbox.client
        for size in (20, 50, 100, 255):
            time.sleep(2 * _CHAT_REST_SECONDS)
            sent = [(f"{size}/{index}:" + "x" * size)[:size] for index in range(10)]
            received = []
            for text in sent:
                client.act([_chat(text)])
                client.step(2)
                received += _own_chat(sandbox)
            client.step(24)
            received += _own_chat(sandbox)
            findings[size] = [text in received for text in sent]
    return findings


def _chat_longest(installation: Installation) -> dict[str, str]:
    """What becomes of a message of just under and just over the size that kills the game client, each in a game of
    its own."""
    findings = {}
    for letter, count in (("a", 1039), ("a", 1040), ("é", 519), ("é", 520)):
        text = letter * count
        name = f"{count} x {letter}, {len(text.encode())} bytes"
        try:
            with playing(Race.TERRAN, installation) as sandbox:
                back = _chat_back(sandbox, [_chat(text)])
                findings[name] = "came back whole" if text in back["within_24"] else f"came back as {back}"
        except ConnectionClosedError as error:
            findings[name] = f"the game client died: {error}"
    return findings


def chat(installation: Installation) -> dict[str, Any]:
    """What comes back of chat: sent fast, by length, to the team, non-ASCII; its results; and chat sent as a player
    leaves."""
    findings: dict[str, Any] = {
        "rate": _chat_rate(installation),
        "back_to_back": _chat_flood(installation),
        "longest": _chat_longest(installation),
    }
    with playing(Race.TERRAN, installation) as sandbox:
        texts = [
            *((f"{length}:" + "abcdefghij" * 100)[:length] for length in (100, 255, 256, 500, 1000)),
            *("é" * count for count in (127, 128)),
            *("日" * count for count in (85, 86)),
            "a" * 253 + "é",
            "a" * 253 + "日",
            "a" * 252 + "日",
        ]
        by_length = {}
        for text in texts:
            back = _chat_back(sandbox, [_chat(text)])
            by_length[f"{len(text)} characters, {len(text.encode())} bytes"] = {
                "came_back_whole": text in back["within_24"],
                "within_2": text in back["within_2"],
                "others": [(len(message), len(message.encode())) for message in back["within_24"] if message != text],
                "stale": len(back["stale"]),
            }
        findings["by_length"] = by_length
        findings["team"] = _chat_back(sandbox, [_chat("to the team only", _TEAM)])
        text = "gg ✓ é ñ 日本"
        back = _chat_back(sandbox, [_chat(text)])
        findings["non_ascii"] = {"sent": text, **back, "same": text in back["within_24"]}
        (scv, *_) = _own(sandbox.units(), UnitTypeId.SCV)
        command = raw_pb2.ActionRawUnitCommand(ability_id=RawAbilityId.Move_Move, unit_tags=[scv.tag])
        command.target_world_space_pos.x, command.target_world_space_pos.y = scv.pos.x + 3, scv.pos.y
        camera = raw_pb2.ActionRawCameraMove()
        camera.center_world_space.x, camera.center_world_space.y = scv.pos.x, scv.pos.y
        actions = [
            sc2api_pb2.Action(action_raw=raw_pb2.ActionRaw(unit_command=command)),
            sc2api_pb2.Action(action_raw=raw_pb2.ActionRaw(camera_move=camera)),
            _chat("first"),
            _chat("second"),
        ]
        findings["with_a_command_and_a_camera_move"] = _chat_back(sandbox, actions)
    findings["chat_then_leave"] = [
        _chat_and_leave(installation, steps_between=0),
        *(_chat_and_leave(installation, steps_between=8) for _ in range(6)),
        _chat_and_leave(installation, steps_between=8, watching=False),
    ]
    logger.info("chat: {}", findings)
    return findings


# --- Leaving and ending a game


def _end(installation: Installation, how: str) -> dict[str, Any]:
    """Play a few steps against the computer, end the game `how`, and record what follows."""
    with playing(Race.TERRAN, installation) as sandbox:
        request = sandbox.transport.request
        sandbox.client.step(_SETTLE)
        if how == "leave":
            answer = _response(request, sc2api_pb2.Request(leave_game=sc2api_pb2.RequestLeaveGame()))
        else:
            result = debug_pb2.DebugEndGame.EndResult.Value(how)
            answer = _debug(sandbox, debug_pb2.DebugCommand(end_game=debug_pb2.DebugEndGame(end_result=result)))
        after = []
        for _ in range(3):
            step = sc2api_pb2.Request(step=sc2api_pb2.RequestStep(count=2))
            after.append({"step": _response(request, step), **_observe(request)})
        return {"how": how, "player": sandbox.player, "answer": answer, "after": after}


def _both_leave(installation: Installation) -> dict[str, Any]:
    """Both players of a game of two leave in the same step: what each is answered, and observes after."""
    leaving = sc2api_pb2.Request(leave_game=sc2api_pb2.RequestLeaveGame())
    with playing_rivals(Race.TERRAN, installation) as rivals:
        rivals.step(_SETTLE)
        sides = {"created": rivals.me, "joined": rivals.enemy}
        answers = {name: _response(side.transport.request, leaving) for name, side in sides.items()}
        after = {name: _observe(side.transport.request) for name, side in sides.items()}
    return {"how": "both leave", "answer": answers, "after": [after]}


def leave(installation: Installation) -> list[dict[str, Any]]:
    """Leave a game against the computer, surrender one and win one, both leave a game of two in the same step, and
    what the game answers after each."""
    findings = [_end(installation, how) for how in ("leave", "Surrender", "DeclareVictory")]
    findings.append(_both_leave(installation))
    for finding in findings:
        logger.info("{}: {}", finding["how"], finding)
    return findings


# --- The pathing query


def _pathing_query(sandbox: Sandbox, pairs: Sequence[tuple[Point | int, Point]]) -> list[float]:
    """The game's pathing distance for each pair, from a point or a unit's tag to a point."""
    requests = []
    for start, end in pairs:
        request = query_pb2.RequestQueryPathing(end_pos=common_pb2.Point2D(x=end.x, y=end.y))
        if isinstance(start, Point):
            request.start_pos.x, request.start_pos.y = start.x, start.y
        else:
            request.unit_tag = start
        requests.append(request)
    response = sandbox.transport.request(sc2api_pb2.Request(query=query_pb2.RequestQuery(pathing=requests)))
    if response.error:
        raise RuntimeError(f"the game refused the query: {list(response.error)}")
    return [answer.distance for answer in response.query.pathing]


def _tile_centers(grid_values: numpy.ndarray, origin: tuple[int, int]) -> list[Point]:
    xs, ys = numpy.nonzero(grid_values)
    return [Point((origin[0] + int(x) + 0.5, origin[1] + int(y) + 0.5)) for x, y in zip(xs, ys, strict=True)]


def _cliff(game_map: GameMap, near: Point) -> tuple[Point, Point]:
    """A pathable point on the main's ground and one below its cliff within 4, the farthest apart by path from `near`,
    a pathable point in the main."""
    pathing, height = game_map.pathing, game_map.height
    start = game_map.start_location
    main_height = game_map.height_at(start)
    distances = pathing.path_distance_from(near)
    best: tuple[float, Point, Point] | None = None
    centers = _tile_centers(pathing.values, (pathing.origin.x, pathing.origin.y))
    high = [p for p in centers if abs(height[p] - main_height) < 0.5 and math.dist(p, start) < 30]
    low = [p for p in centers if height[p] < main_height - 1.5 and math.dist(p, start) < 34]
    for top in high:
        for bottom in low:
            if math.dist(top, bottom) <= 4 and math.isfinite(distances[bottom]):
                apart = distances[bottom] - distances[top]
                if best is None or apart > best[0]:
                    best = (apart, top, bottom)
    if best is None:
        raise RuntimeError("found no cliff beside the main")
    return best[1], best[2]


def _every_other_step(
    sandbox: Sandbox, pairs: Sequence[tuple[Point | int, Point]], times: int, *, observing: bool
) -> list[Any]:
    """The pathing distances of `pairs` after each of `times` 2 steps, observing first if `observing`, as runs: the
    steps each answer first came at."""
    runs: list[Any] = []
    for index in range(1, times + 1):
        sandbox.client.step(2)
        if observing:
            sandbox.units()
        answers = _pathing_query(sandbox, pairs)
        if not runs or runs[-1][1] != answers:
            runs.append((2 * index, answers))
    return runs


def _timed(call: Callable[[], object]) -> float:
    """The median milliseconds `call` takes over the timing repeats."""
    times = []
    for _ in range(_TIMING_REPEATS):
        started = time.perf_counter()
        call()
        times.append((time.perf_counter() - started) * 1000)
    return statistics.median(times)


def pathing(installation: Installation) -> dict[str, Any]:
    """What the pathing query answers in the cases the module docstring lists, how long it takes, and how it compares
    with `Grid.path_distance_from`."""
    findings: dict[str, Any] = {}
    with playing(Race.TERRAN, installation) as sandbox:
        client = sandbox.client
        game_map = GameMap._of_game(client.game_info(), client.observation())
        grid = game_map.pathing
        start = game_map.start_location
        origin = (grid.origin.x, grid.origin.y)
        centers = _tile_centers(grid.values, origin)
        near = min((p for p in centers if 7 <= math.dist(p, start) <= 9), key=lambda p: math.dist(p, start))
        findings["near"] = near
        # The start's grid blocks the townhall the start location lies in, so distances are taken from `near`.
        from_near = grid.path_distance_from(near)

        offsets = (0.0, 0.1, 0.5, 1.0, 2.0)
        answers = _pathing_query(sandbox, [(near, Point((near.x + offset, near.y))) for offset in offsets])
        findings["apart"] = dict(zip(offsets, answers, strict=True))

        # A tile in the playable area with no pathable tile within 2 of it.
        deep = scipy.ndimage.binary_erosion(~grid.values, structure=numpy.ones((5, 5), dtype=bool))
        playable = game_map.playable_area
        deep_points = [
            p
            for p in _tile_centers(deep, origin)
            if playable.left < p.x < playable.right and playable.bottom < p.y < playable.top
        ]
        unreachable = min(deep_points, key=lambda p: math.dist(p, near))
        islands = [p for p in centers if not math.isfinite(from_near[p])]
        targets = {"deep_unpathable": unreachable, **({"island": islands[0]} if islands else {})}
        answers = _pathing_query(sandbox, [(near, target) for target in targets.values()])
        findings["unreachable"] = {
            name: {"at": target, "distance": d} for (name, target), d in zip(targets.items(), answers, strict=True)
        }
        findings["islands_by_the_grid"] = len(islands)

        townhall_pairs = {
            "from_townhall": (start, near),
            "to_townhall": (near, start),
            "townhall_to_itself": (start, start),
        }
        answers = _pathing_query(sandbox, list(townhall_pairs.values()))
        findings["townhall"] = dict(zip(townhall_pairs, answers, strict=True))
        findings["townhall_by_the_grid"] = {"near_from_start": grid.path_distance_from(start)[near]}

        try:
            findings["unknown_tag"] = _pathing_query(sandbox, [(123456789, near)])
        except RuntimeError as error:
            findings["unknown_tag"] = str(error)

        top, bottom = _cliff(game_map, near)
        movers = (UnitTypeId.MARINE, UnitTypeId.REAPER, UnitTypeId.COLOSSUS, UnitTypeId.MEDIVAC)
        made = sandbox.spawn([(unit_type, sandbox.player, top) for unit_type in movers])
        client.step(_SETTLE)
        by_type = {
            unit.unit_type: unit for unit in sandbox.units() if unit.tag in {made_unit.tag for made_unit in made}
        }
        cliff: dict[str, Any] = {"top": top, "bottom": bottom, "straight": math.dist(top, bottom)}
        cliff["from_the_point"] = _pathing_query(sandbox, [(top, bottom)])[0]
        cliff["grid"] = grid.path_distance_from(top)[bottom]
        for unit_type in movers:
            if (unit := by_type.get(unit_type)) is not None:
                from_tag, from_position = _pathing_query(sandbox, [(unit.tag, bottom), (_at(unit), bottom)])
                cliff[unit_type.name] = {"at": _at(unit), "from_tag": from_tag, "from_its_position": from_position}
        findings["cliff"] = cliff
        sandbox.remove(unit.tag for unit in made)

        ground = OpenGround(game_map, sandbox.units())
        square = ground.claim(Point((start.x + 12, start.y + 12)), 3)
        hole = Point((square.x + 0.5, square.y + 0.5))
        outside = min(
            (p for p in centers if 10 <= math.dist(p, hole) <= 12), key=lambda p: abs(math.dist(p, hole) - 11)
        )
        ring: dict[str, Any] = {
            "hole": hole,
            "outside": outside,
            "before": _pathing_query(sandbox, [(outside, hole)])[0],
        }
        sites = [Point((hole.x + dx, hole.y + dy)) for dx in (-2, 0, 2) for dy in (-2, 0, 2) if (dx, dy) != (0, 0)]
        depots = sandbox.spawn([(UnitTypeId.SUPPLY_DEPOT, sandbox.player, site) for site in sites])
        ring["depots_made_at"] = sorted(_at(depot) for depot in depots)
        # Into the ring and out of it every 2 steps, observing each time as a bot does, after the depots stand and
        # after they are ordered down; then raised and lowered again, observing only at the end.
        pairs_in_and_out = [(outside, hole), (hole, outside)]
        ring["raised"] = _every_other_step(sandbox, pairs_in_and_out, 24, observing=True)
        for depot in depots:
            sandbox.order(RawAbilityId.Morph_SupplyDepot_Lower, depot.tag)
        ring["after_the_lower_order"] = _every_other_step(sandbox, pairs_in_and_out, 24, observing=True)
        for depot in depots:
            sandbox.order(RawAbilityId.Morph_SupplyDepot_Raise, depot.tag)
        ring["after_the_raise_order_unobserved"] = _every_other_step(sandbox, pairs_in_and_out, 24, observing=False)
        sandbox.units()
        ring["then_observed"] = _pathing_query(sandbox, pairs_in_and_out)
        for depot in depots:
            sandbox.order(RawAbilityId.Morph_SupplyDepot_Lower, depot.tag)
        ring["after_the_lower_order_unobserved"] = _every_other_step(sandbox, pairs_in_and_out, 24, observing=False)
        sandbox.units()
        ring["then_observed_again"] = _pathing_query(sandbox, pairs_in_and_out)
        lowered = [unit for unit in sandbox.units() if unit.unit_type == UnitTypeId.SUPPLY_DEPOT_LOWERED]
        ring["depots_lowered"] = len(lowered)
        findings["depot_ring"] = ring

        rng = random.Random(0)
        reachable = [p for p in centers if math.isfinite(from_near[p])]
        pairs = [(rng.choice(reachable), rng.choice(reachable)) for _ in range(100)]
        answers = _pathing_query(sandbox, pairs)
        compared = [(answer, grid.path_distance_from(a)[b]) for (a, b), answer in zip(pairs, answers, strict=True)]
        ratios = [
            answer / by_grid for answer, by_grid in compared if answer > 0 and by_grid > 0 and math.isfinite(by_grid)
        ]
        findings["random_pairs"] = {
            "pairs": len(pairs),
            "game_zero_grid_finite": [
                (a, b, by_grid)
                for (a, b), (answer, by_grid) in zip(pairs, compared, strict=True)
                if answer == 0 and 0 < by_grid < math.inf
            ],
            "ratio_min": min(ratios),
            "ratio_median": statistics.median(ratios),
            "ratio_max": max(ratios),
            "compared": len(ratios),
        }
        findings["milliseconds"] = {
            "one_pair": _timed(lambda: _pathing_query(sandbox, pairs[:1])),
            "hundred_pairs": _timed(lambda: _pathing_query(sandbox, pairs)),
        }
    logger.info("pathing: {}", findings)
    return findings


# Each table of `ResponseData`, and the field its rows are keyed by.
_TABLE_KEYS = {
    "units": "unit_id",
    "abilities": "ability_id",
    "upgrades": "upgrade_id",
    "buffs": "buff_id",
    "effects": "effect_id",
}


def _field_value(value: Any) -> Any:
    """A row's field as JSON holds it: a repeated field as a list."""
    return value if isinstance(value, bool | int | float | str) else [str(item) for item in value]


def _row_differences(corpus_row: Any, answered_row: Any) -> dict[str, list[Any]]:
    """The fields where `answered_row` reads otherwise than `corpus_row`, each as the corpus has it and as answered."""
    names = {field.name for field, _ in corpus_row.ListFields()} | {
        field.name for field, _ in answered_row.ListFields()
    }
    return {
        name: [_field_value(getattr(corpus_row, name)), _field_value(getattr(answered_row, name))]
        for name in sorted(names)
        if getattr(answered_row, name) != getattr(corpus_row, name)
    }


def _table_differences(corpus: sc2api_pb2.ResponseData, answer: sc2api_pb2.ResponseData) -> dict[str, Any]:
    """How each table of `answer` differs from `corpus`'s: the rows whose fields read otherwise, by id and the name
    the corpus gives them, how many differ only in which empty fields they set, and the ids only one of them has."""
    differences: dict[str, Any] = {}
    for table, key in _TABLE_KEYS.items():
        corpus_rows = {getattr(row, key): row for row in getattr(corpus, table)}
        answered_rows = {getattr(row, key): row for row in getattr(answer, table)}
        changed: dict[str, dict[str, list[Any]]] = {}
        differing_in_empty_fields = 0
        for row_id in corpus_rows.keys() & answered_rows.keys():
            corpus_row, answered_row = corpus_rows[row_id], answered_rows[row_id]
            if answered_row == corpus_row:
                continue
            if fields := _row_differences(corpus_row, answered_row):
                name = getattr(corpus_row, "name", "") or getattr(corpus_row, "friendly_name", "")
                changed[f"{row_id} {name}"] = fields
            else:
                differing_in_empty_fields += 1
        differences[table] = {
            "rows differing": dict(sorted(changed.items(), key=lambda entry: int(entry[0].split()[0]))),
            "rows differing only in which empty fields they set": differing_in_empty_fields,
            "ids only in the corpus": sorted(corpus_rows.keys() - answered_rows.keys()),
            "ids only answered": sorted(answered_rows.keys() - corpus_rows.keys()),
        }
    return differences


def tables(installation: Installation) -> dict[str, Any]:
    """Ask for the tables in a game against the computer, and in a game of two of each player, and record how each
    differs from the first corpus game's."""
    corpus = corpus_answer()
    with playing(Race.TERRAN, installation) as sandbox:
        against_the_computer = _table_differences(corpus, sandbox.client.game_data())
    with playing_rivals(Race.TERRAN, installation) as rivals:
        of_two = {
            "creator": _table_differences(corpus, rivals.me.client.game_data()),
            "joiner": _table_differences(corpus, rivals.enemy.client.game_data()),
        }
    findings = {"against the computer": against_the_computer, "in a game of two": of_two}
    logger.info("tables: {} unit rows differ", len(against_the_computer["units"]["rows differing"]))
    return findings


def _player_list(client: Client) -> list[dict[str, Any]]:
    """The player list `client`'s game answers it with."""
    return [
        {
            "id": entry.player_id,
            "type": sc2api_pb2.PlayerType.Name(entry.type),
            "race requested": Race(entry.race_requested).name,
            "race actual": Race(entry.race_actual).name,
            "name": entry.player_name,
        }
        for entry in client.game_info().player_info
    ]


def players(installation: Installation) -> dict[str, Any]:
    """Record the player list each client is answered in a game of two, joined with names, one asking for Random, and
    in a game against a computer asking for Random."""
    game_map = MapFile.find(MAP, installation=installation)
    joining = (("Alpha", Race.TERRAN), ("Beta", Race.RANDOM))
    with ExitStack() as stack, ThreadPoolExecutor(2) as pool:
        games = [stack.enter_context(GameProcess.launch(installation, window=(640, 480))) for _ in joining]
        clients = [stack.enter_context(closing(Client(WebSocketTransport.connect(game.url)))) for game in games]
        clients[0].create_game(game_map.path, [Participant(), Participant()])
        ports = GamePorts(PortPair(free_port(), free_port()), (PortPair(free_port(), free_port()),))
        # Each join waits for the other, so they are sent together.
        joins = [
            pool.submit(client.join_game, race, name=name, ports=ports)
            for client, (name, race) in zip(clients, joining, strict=True)
        ]
        for join in joins:
            join.result()
        of_two = {name: _player_list(client) for client, (name, _) in zip(clients, joining, strict=True)}
        for client in clients:
            client.leave_game()
    with (
        GameProcess.launch(installation, window=(640, 480)) as game,
        closing(Client(WebSocketTransport.connect(game.url))) as client,
    ):
        client.create_game(game_map.path, [Participant(), Computer(Race.RANDOM, Difficulty.VERY_EASY)])
        client.join_game(Race.TERRAN, name="Alpha")
        against_the_computer = _player_list(client)
        client.leave_game()
    findings = {"in a game of two": of_two, "against the computer": against_the_computer}
    logger.info("players: {}", findings)
    return findings


_SWEEPS: dict[str, Callable[[Installation], object]] = {
    "debug-joined": debug_joined,
    "cheats-given": cheats_given,
    "chat": chat,
    "leave": leave,
    "pathing": pathing,
    "tables": tables,
    "players": players,
}


def main(argv: Sequence[str]) -> None:
    """Run the sweeps named, or all of them, and write the findings."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sweeps", nargs="*", help=f"{', '.join(_SWEEPS)}; all when none are named")
    parser.add_argument("--out", type=Path, default=Path("requests.json"), help="where to write the findings")
    arguments = parser.parse_args(argv)
    if unknown := set(arguments.sweeps) - set(_SWEEPS):
        raise SystemExit(f"No sweep is called {', '.join(sorted(unknown))}")
    installation = Installation.find()
    findings = {
        name: sweep(installation) for name, sweep in _SWEEPS.items() if name in arguments.sweeps or not arguments.sweeps
    }
    arguments.out.write_text(json.dumps(findings, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
    logger.info("Wrote the findings to {}", arguments.out)


if __name__ == "__main__":
    main(sys.argv[1:])
