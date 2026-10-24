"""Find what the game does when it plays a replay back, from either player's view and with the fog off, and what
playing one back costs.

Needs StarCraft II installed. Every sweep plays back one shared game, and `ending` one more besides: the demo bot
against the computer at Medium on the sandbox's map for 300 game seconds, a turn every 8 steps, recorded and its
replay saved at its time limit.

- `views` plays the replay back as player 1 and compares each observation with the one recorded: the units by tag,
  then each one in both by its type, owner, display, position, health, shields, energy and number of orders, and the
  actions. It then plays it back as player 1 with the fog on and off, as player 2 with it off, and as player 0 with it
  on and off, and records at four steps the units by alliance and owner and by display, the player's minerals, supply
  and score, the tiles it sees, and whether two of the views list the same tags. Last, it compares the enemy units
  player 1 did not see, as the fog off shows them at step 4480, with how player 2, whose they are, saw them then.
- `requests` asks, in the replay played back as player 1, for an action of each kind (a move, an empty one, chat and
  a camera move), an observer's camera move, a debug drawing, the queries for abilities and pathing, a replay save,
  the tables and the game info; then starts the replay from its path, outside the installation's `Replays`, and
  leaves it.
- `ending` plays a game the computer is defeated in through `DebugEndGame`, saves its replay once it has ended, and
  plays that back as each player, to its end: the result and status there, the results the replay's info gives, and
  what a step after the end is answered. It does the same for the shared game, saved at its time limit.
- `unseen` plays a game of its own between two players from here, in which the first sees the enemy's main and loses
  sight of it, has an undetected enemy observer, burrowed zergling and burrowed widow mine in its main, and is far
  from an enemy marine, all made by debug commands, and then the enemy's townhall and that marine die out of its
  sight; and saves its replay. Played back as the first player with the fog on and with it off, at a step while it sees
  the enemy's main, one after, and one after the two died, it compares every enemy unit of the fog-off view with the
  fog-on view: its display, whether its tile is in the player's sight, whether the fog-on view lists it, under its tag
  or as a copy where it stands, its health and orders, and its owner, maximum health, cloak and burrow; the enemy units
  the fog-on view lists alone; the deaths each view reports; and whether the two views' tiles in sight are the same.
- `scouting` plays four games of 100 seconds against the computer, seeded 1 to 4, in which an SCV scouts the enemy's
  main and comes back, as the integration test of a playback with the fog off does, and plays each one's replay back
  with the fog on and off, 8 steps a turn. It sorts the enemy's hidden units of the fog-off view by whether the tile
  under them is in sight, whether they report an owner and a maximum health and whether the fog-on view lists them;
  the map's units in the fog by the same and by whether they report their contents; counts the observations whose
  creep, visibility, score, player, alerts, actions, effects or deaths differ between the views; and lists the copies
  the fog-on view gives a new tag in the fog between two observations.
- `speed` times starting the replay, and playing it back, observing each turn, at 8 steps a turn and at every step,
  with the fog on and off, with the size of an observation. It then plays it back through `run_replay` at every step,
  recording it, and gives the recording's size and how long `run_recording` takes to play it back.

Run it as::

    uv run python tools/sweep_replays.py
    uv run python tools/sweep_replays.py views --out replays-views.json

The findings are written as JSON, one object per sweep.
"""

import argparse
import importlib.util
import itertools
import json
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

import numpy
from _sandbox import MAP, Cheat, OpenGround, playing, playing_rivals
from loguru import logger
from s2clientprotocol import common_pb2, debug_pb2, query_pb2, raw_pb2, sc2api_pb2

from sc2nachos import AbilityId, Api, Computer, Difficulty, GameTime, Race, run_local, run_recording, run_replay
from sc2nachos.events import TurnEvent
from sc2nachos.gamemap import GameMap
from sc2nachos.gamemap._image_data import image_array
from sc2nachos.geometry import Rectangle
from sc2nachos.ids import UnitTypeId
from sc2nachos.launch import GameProcess, Installation
from sc2nachos.protocol import ConnectionClosedError, Recording, WebSocketTransport
from sc2nachos.protocol._client import _raw_interface

_DEMO_BOT = Path(__file__).parents[1] / "examples" / "demo_bot.py"
_GAME_SECONDS = 300
_STEPS_PER_TURN = 8
_IN_REPLAY = sc2api_pb2.Status.in_replay
_ENDED_STATUS = sc2api_pb2.Status.ended
# The interface NachOS plays a replay back on, so that the sweep sees what the library does.
_RAW_INTERFACE = _raw_interface(raw_affects_selection=False)
_MOVE_ABILITY = 16
_SCV_TYPE = 45
# The steps at which `views` compares the views.
_VIEW_STEPS = (0, 2240, 4480, 6712)
_UNIT_FIELDS = ("type", "owner", "display", "x", "y", "health", "shield", "energy", "orders")
# The step at which `views` compares the enemy units player 1 did not see with their owner's view of them, and the
# fields it compares.
_UNSEEN_UNITS_STEP = 4480
# The steps `unseen` lets pass after each thing it makes happen, and how near a copy of a unit stands to it.
_UNSEEN_SETTLE_STEPS = 64
# The raw ability that burrows a zergling, and the steps `unseen` lets it take.
_BURROW_ZERGLING = 1390
_BURROW_STEPS = 16
# How far from the enemy's start, toward the middle, `unseen` keeps it a structure, out of the first player's sight.
_ENEMY_BAY_DISTANCE = 30.0
_SAME_PLACE = 0.5
# How long `scouting` plays, and the step at which its scout turns back.
_SCOUTING_SECONDS = 100
_SCOUTING_SEEDS = (1, 2, 3, 4)
_SCOUT_BACK_AT_STEP = 1500
_SNAPSHOT_DISPLAY = raw_pb2.DisplayType.Snapshot
# A tile the player sees, in the game's visibility image.
_TILE_IN_SIGHT = 2
# The kinds of unit `unseen` always gives an example of: a display that its tile's sight does not match.
_KINDS_AT_THE_EDGE_OF_SIGHT = frozenset({"Visible, on a tile out of sight", "Hidden, on a tile in sight"})
_REPORTED_FIELDS = (
    "unit_type",
    "health",
    "health_max",
    "shield",
    "energy",
    "build_progress",
    "is_flying",
    "is_burrowed",
    "weapon_cooldown",
    "facing",
)


def _record_the_shared_game(directory: Path) -> tuple[Path, Path]:
    """Play the demo bot against the computer, recording the game into `directory` and saving its replay there, and
    return the replay's path and the recording's."""
    spec = importlib.util.spec_from_file_location("demo_bot", _DEMO_BOT)
    if spec is None or spec.loader is None:
        raise SystemExit(f"{_DEMO_BOT} cannot be loaded")
    demo_bot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo_bot)
    replay = directory / "shared.SC2Replay"
    recording = directory / "shared.sc2rec"
    result = run_local(
        MAP,
        demo_bot.api,
        Computer(Race.ZERG, Difficulty.MEDIUM),
        race=Race.TERRAN,
        steps_per_turn=_STEPS_PER_TURN,
        time_limit=GameTime.from_seconds(_GAME_SECONDS),
        random_seed=11,
        record_to=recording,
        save_replay_to=replay,
    )
    logger.info("The shared game ended in a {}", result)
    return replay, recording


@contextmanager
def _replay_client(installation: Installation) -> Iterator[WebSocketTransport]:
    """A client of the newest build installed, to play replays back on, over a transport that sends raw requests."""
    with GameProcess.launch(installation, window=(1024, 768)) as game:
        transport = WebSocketTransport.connect(game.url)
        try:
            yield transport
        finally:
            # A client that died mid-sweep cannot quit, and the error that stopped the sweep is the one to see.
            with suppress(ConnectionClosedError):
                transport.request(sc2api_pb2.Request(quit=sc2api_pb2.RequestQuit()))
            transport.close()


def _answer_to(transport: WebSocketTransport, **request: Any) -> sc2api_pb2.Response:
    """The game's answer to the request of `request`'s one field."""
    return transport.request(sc2api_pb2.Request(**request))


def _summary_of(response: sc2api_pb2.Response) -> dict[str, Any]:
    """The status a response gives, its errors and which answer it holds."""
    return {
        "status": sc2api_pb2.Status.Name(response.status) if response.HasField("status") else None,
        "errors": list(response.error),
        "answer": response.WhichOneof("response"),
    }


def _replay_info(transport: WebSocketTransport, replay_data: bytes) -> dict[str, Any]:
    """What the game reads from the replay `replay_data`."""
    response = _answer_to(transport, replay_info=sc2api_pb2.RequestReplayInfo(replay_data=replay_data))
    info = response.replay_info
    return _summary_of(response) | {
        "map": info.map_name,
        "steps": info.game_duration_loops,
        "base build": info.base_build,
        "data version": info.data_version,
        "players": [
            {
                "id": entry.player_info.player_id,
                "type": sc2api_pb2.PlayerType.Name(entry.player_info.type),
                "name": entry.player_info.player_name,
                "result": sc2api_pb2.Result.Name(entry.player_result.result)
                if entry.HasField("player_result")
                else None,
                "apm": entry.player_apm,
            }
            for entry in info.player_info
        ],
    }


def _start_replay(
    transport: WebSocketTransport,
    *,
    player: int,
    disable_fog: bool = False,
    replay_data: bytes | None = None,
    replay_path: Path | None = None,
) -> dict[str, Any]:
    """Start playing back the replay given by its bytes or its path as `player` sees it, leaving whatever runs first,
    and return what the game answered and how long it took."""
    if _answer_to(transport, ping=sc2api_pb2.RequestPing()).status != sc2api_pb2.Status.launched:
        _answer_to(transport, leave_game=sc2api_pb2.RequestLeaveGame())
    request = sc2api_pb2.RequestStartReplay(observed_player_id=player, options=_RAW_INTERFACE, disable_fog=disable_fog)
    if replay_data is not None:
        request.replay_data = replay_data
    if replay_path is not None:
        request.replay_path = str(replay_path.resolve())
    began = time.perf_counter()
    response = _answer_to(transport, start_replay=request)
    started = response.start_replay
    return _summary_of(response) | {
        "seconds": round(time.perf_counter() - began, 2),
        "error": sc2api_pb2.ResponseStartReplay.Error.Name(started.error) if started.HasField("error") else None,
        "details": started.error_details,
    }


def _observe_the_replay(transport: WebSocketTransport) -> sc2api_pb2.Response:
    """The game's answer to an observation."""
    return _answer_to(transport, observation=sc2api_pb2.RequestObservation())


def _observations_to_the_end(transport: WebSocketTransport, steps_per_turn: int) -> Iterator[sc2api_pb2.Response]:
    """Every observation of the replay being played back, a turn every `steps_per_turn` steps, to its end."""
    while True:
        response = _observe_the_replay(transport)
        yield response
        if response.status != _IN_REPLAY:
            return
        _answer_to(transport, step=sc2api_pb2.RequestStep(count=steps_per_turn))


def _unit_reading(unit: raw_pb2.Unit) -> tuple[Any, ...]:
    """What `views` compares of a unit, in the order of `_UNIT_FIELDS`."""
    return (
        unit.unit_type,
        unit.owner,
        unit.display_type,
        round(unit.pos.x, 3),
        round(unit.pos.y, 3),
        round(unit.health, 2),
        round(unit.shield, 2),
        round(unit.energy, 2),
        len(unit.orders),
    )


def _unit_place(unit: raw_pb2.Unit) -> tuple[Any, ...]:
    """A unit's type, alliance, display and position, by which a unit under another tag is matched."""
    return (unit.unit_type, unit.alliance, unit.display_type, round(unit.pos.x, 2), round(unit.pos.y, 2))


def _unit_kind(unit: raw_pb2.Unit) -> str:
    """A unit's type, alliance and display, as one key."""
    return f"{unit.unit_type} {raw_pb2.Alliance.Name(unit.alliance)} {raw_pb2.DisplayType.Name(unit.display_type)}"


def _recorded_observations(recording: Path) -> dict[int, sc2api_pb2.ResponseObservation]:
    """Every observation in `recording`, by step."""
    observations = {}
    for exchange in Recording(recording):
        if exchange.response.WhichOneof("response") == "observation":
            observation = exchange.response.observation
            observations[observation.observation.game_loop] = observation
    return observations


def _compare_with_the_recording(transport: WebSocketTransport, replay_data: bytes, recording: Path) -> dict[str, Any]:
    """Play the replay back as player 1 and compare each observation with the one recorded at its step."""
    recorded = _recorded_observations(recording)
    start = _start_replay(transport, player=1, replay_data=replay_data)
    differences: Counter[str] = Counter()
    retagged: Counter[str] = Counter()
    unmatched: list[dict[str, Any]] = []
    steps_compared = 0
    units_compared = 0
    actions_recorded = 0
    actions_played_back = 0
    steps_whose_actions_differ = 0
    for response in _observations_to_the_end(transport, _STEPS_PER_TURN):
        observation = response.observation
        live = recorded.get(observation.observation.game_loop)
        if live is None:
            continue
        steps_compared += 1
        live_units = {unit.tag: unit for unit in live.observation.raw_data.units}
        played_back_units = {unit.tag: unit for unit in observation.observation.raw_data.units}
        for tag in live_units.keys() & played_back_units.keys():
            units_compared += 1
            pairs = zip(
                _UNIT_FIELDS, _unit_reading(live_units[tag]), _unit_reading(played_back_units[tag]), strict=True
            )
            differences.update(
                name for name, recorded_value, played_back_value in pairs if recorded_value != played_back_value
            )
        only_played_back = {_unit_place(played_back_units[tag]) for tag in played_back_units.keys() - live_units.keys()}
        for tag in live_units.keys() - played_back_units.keys():
            unit = live_units[tag]
            if _unit_place(unit) in only_played_back:
                retagged[_unit_kind(unit)] += 1
            elif len(unmatched) < 10:
                unmatched.append({"step": observation.observation.game_loop, "unit": list(_unit_place(unit))})
        live_actions = sorted(action.SerializeToString() for action in live.actions)
        played_back_actions = sorted(action.SerializeToString() for action in observation.actions)
        actions_recorded += len(live_actions)
        actions_played_back += len(played_back_actions)
        steps_whose_actions_differ += live_actions != played_back_actions
    return {
        "start": start,
        "steps recorded": len(recorded),
        "steps compared": steps_compared,
        "units compared": units_compared,
        "fields that differ": dict(differences),
        "units under another tag, matched by type, alliance, display and position": dict(retagged),
        "units only in the recording, unmatched": unmatched,
        "actions recorded": actions_recorded,
        "actions played back": actions_played_back,
        "steps whose actions differ": steps_whose_actions_differ,
    }


def _view_at_steps(
    transport: WebSocketTransport, replay_data: bytes, *, player: int, disable_fog: bool
) -> dict[int, dict[str, Any]]:
    """What the replay shows `player` at each of `_VIEW_STEPS`, with the tags it lists. Raises `RuntimeError` if the
    game refuses to start it."""
    start = _start_replay(transport, player=player, disable_fog=disable_fog, replay_data=replay_data)
    if start["errors"] or start["error"]:
        raise RuntimeError(f"the game refused to play the replay back as player {player}: {start}")
    seen: dict[int, dict[str, Any]] = {}
    for step in _VIEW_STEPS:
        observation = _observe_the_replay(transport).observation
        if (now := observation.observation.game_loop) < step:
            _answer_to(transport, step=sc2api_pb2.RequestStep(count=step - now))
            observation = _observe_the_replay(transport).observation
        units = observation.observation.raw_data.units
        common = observation.observation.player_common
        seen[observation.observation.game_loop] = {
            "units": len(units),
            "by alliance and owner": {
                f"{raw_pb2.Alliance.Name(alliance)} of {owner}": count
                for (alliance, owner), count in sorted(Counter((unit.alliance, unit.owner) for unit in units).items())
            },
            "by display": dict(Counter(raw_pb2.DisplayType.Name(unit.display_type) for unit in units)),
            "player": {
                "id": common.player_id,
                "minerals": common.minerals,
                "supply used": common.food_used,
                "supply provided": common.food_cap,
            },
            "score": observation.observation.score.score,
            "tiles seen": sum(1 for value in observation.observation.raw_data.map_state.visibility.data if value == 2),
            "actions": len(observation.actions),
            "tags": sorted(unit.tag for unit in units),
        }
    return seen


def _compare_views(transport: WebSocketTransport, replay_data: bytes) -> dict[str, Any]:
    """Each view of the replay at `_VIEW_STEPS`, and whether two views list the same tags."""
    views = {
        "player 1": _view_at_steps(transport, replay_data, player=1, disable_fog=False),
        "player 1, fog off": _view_at_steps(transport, replay_data, player=1, disable_fog=True),
        "player 2, fog off": _view_at_steps(transport, replay_data, player=2, disable_fog=True),
        "player 0": _view_at_steps(transport, replay_data, player=0, disable_fog=False),
        "player 0, fog off": _view_at_steps(transport, replay_data, player=0, disable_fog=True),
    }
    tags_by_view = {name: {step: view.pop("tags", None) for step, view in seen.items()} for name, seen in views.items()}
    pairs = (("player 1, fog off", "player 2, fog off"), ("player 1, fog off", "player 0"))
    same_tags = {
        f"{first} and {second}": {
            step: tags_by_view[first].get(step) == tags_by_view[second].get(step) for step in tags_by_view[first]
        }
        for first, second in pairs
    }
    return views | {"same tags": same_tags}


def _units_at_step(
    transport: WebSocketTransport, replay_data: bytes, *, player: int, disable_fog: bool, step: int
) -> dict[int, raw_pb2.Unit]:
    """The units the replay shows `player` at `step`, by tag."""
    _start_replay(transport, player=player, disable_fog=disable_fog, replay_data=replay_data)
    _answer_to(transport, step=sc2api_pb2.RequestStep(count=step))
    return {unit.tag: unit for unit in _observe_the_replay(transport).observation.observation.raw_data.units}


def _unseen_units_against_their_owners_view(transport: WebSocketTransport, replay_data: bytes) -> dict[str, Any]:
    """The enemy units player 1 did not see, as the fog off shows them at `_UNSEEN_UNITS_STEP`, against how player 2,
    whose they are, saw them then."""
    shown = _units_at_step(transport, replay_data, player=1, disable_fog=True, step=_UNSEEN_UNITS_STEP)
    owners_view = _units_at_step(transport, replay_data, player=2, disable_fog=False, step=_UNSEEN_UNITS_STEP)
    unseen = [unit for unit in shown.values() if unit.alliance == raw_pb2.Alliance.Enemy]
    fields_that_differ: Counter[str] = Counter()
    orders_that_differ = 0
    missing_from_the_owners_view = 0
    for unit in unseen:
        if (owners := owners_view.get(unit.tag)) is None:
            missing_from_the_owners_view += 1
            continue
        fields_that_differ.update(
            name for name in _REPORTED_FIELDS if round(getattr(unit, name), 3) != round(getattr(owners, name), 3)
        )
        orders_that_differ += len(unit.orders) != len(owners.orders)
    return {
        "step": _UNSEEN_UNITS_STEP,
        "enemy units by display": dict(Counter(raw_pb2.DisplayType.Name(unit.display_type) for unit in unseen)),
        "missing from the owner's view": missing_from_the_owners_view,
        "fields that differ": dict(fields_that_differ),
        "units whose number of orders differs": orders_that_differ,
    }


def sweep_views(installation: Installation, replay: Path, recording: Path) -> dict[str, Any]:
    """The replay as player 1 against the recording, and each view of it at a few steps."""
    replay_data = replay.read_bytes()
    with _replay_client(installation) as transport:
        return {
            "replay info": _replay_info(transport, replay_data),
            "as player 1, against the recording": _compare_with_the_recording(transport, replay_data, recording),
            "views": _compare_views(transport, replay_data),
            "enemy units unseen, with the fog off, against their owner's view": (
                _unseen_units_against_their_owners_view(transport, replay_data)
            ),
        }


def _requests_in_a_replay(transport: WebSocketTransport) -> dict[str, Any]:
    """What the game answers each request a bot makes besides observing, in a replay."""
    units = _observe_the_replay(transport).observation.observation.raw_data.units
    scv = next(unit for unit in units if unit.alliance == raw_pb2.Alliance.Self and unit.unit_type == _SCV_TYPE)
    point = common_pb2.Point2D(x=60, y=60)
    move = raw_pb2.ActionRawUnitCommand(ability_id=_MOVE_ABILITY, target_world_space_pos=point, unit_tags=[scv.tag])
    camera = raw_pb2.ActionRawCameraMove(center_world_space=common_pb2.Point(x=60, y=60))
    actions = {
        "a move": sc2api_pb2.Action(action_raw=raw_pb2.ActionRaw(unit_command=move)),
        "chat": sc2api_pb2.Action(action_chat=sc2api_pb2.ActionChat(message="hello")),
        "a camera move": sc2api_pb2.Action(action_raw=raw_pb2.ActionRaw(camera_move=camera)),
    }
    findings: dict[str, Any] = {
        f"action, {name}": _summary_of(_answer_to(transport, action=sc2api_pb2.RequestAction(actions=[action])))
        for name, action in actions.items()
    }
    findings["action, empty"] = _summary_of(_answer_to(transport, action=sc2api_pb2.RequestAction()))
    observer_camera = sc2api_pb2.ObserverAction(camera_move=sc2api_pb2.ActionObserverCameraMove(world_pos=point))
    findings["observer's camera move"] = _summary_of(
        _answer_to(transport, obs_action=sc2api_pb2.RequestObserverAction(actions=[observer_camera]))
    )
    text = debug_pb2.DebugText(text="replay", virtual_pos=common_pb2.Point(x=60, y=60, z=10))
    drawing = debug_pb2.DebugCommand(draw=debug_pb2.DebugDraw(text=[text]))
    findings["debug drawing"] = _summary_of(_answer_to(transport, debug=sc2api_pb2.RequestDebug(debug=[drawing])))
    abilities = query_pb2.RequestQuery(abilities=[query_pb2.RequestQueryAvailableAbilities(unit_tag=scv.tag)])
    response = _answer_to(transport, query=abilities)
    findings["query, abilities"] = _summary_of(response) | {"offered": len(response.query.abilities[0].abilities)}
    start = common_pb2.Point2D(x=scv.pos.x, y=scv.pos.y)
    near = common_pb2.Point2D(x=scv.pos.x + 3, y=scv.pos.y)
    pathing = query_pb2.RequestQuery(pathing=[query_pb2.RequestQueryPathing(start_pos=start, end_pos=near)])
    response = _answer_to(transport, query=pathing)
    findings["query, pathing 3 apart"] = _summary_of(response) | {"distance": response.query.pathing[0].distance}
    response = _answer_to(transport, save_replay=sc2api_pb2.RequestSaveReplay())
    findings["replay save"] = _summary_of(response)
    response = _answer_to(transport, data=sc2api_pb2.RequestData(unit_type_id=True))
    findings["tables"] = _summary_of(response) | {"unit types": len(response.data.units)}
    response = _answer_to(transport, game_info=sc2api_pb2.RequestGameInfo())
    info = response.game_info
    findings["game info"] = _summary_of(response) | {
        "players": [
            {
                "id": entry.player_id,
                "type": sc2api_pb2.PlayerType.Name(entry.type),
                "race requested": entry.race_requested,
                "race": entry.race_actual,
                "name": entry.player_name,
            }
            for entry in info.player_info
        ],
        "start locations": [[location.x, location.y] for location in info.start_raw.start_locations],
    }
    return findings


def sweep_requests_answered(installation: Installation, replay: Path, recording: Path) -> dict[str, Any]:
    """What the game answers in a replay, and starting one from its path."""
    with _replay_client(installation) as transport:
        findings = {"start": _start_replay(transport, player=1, replay_data=replay.read_bytes())}
        findings |= _requests_in_a_replay(transport)
        findings["start from its path"] = _start_replay(transport, player=1, replay_path=replay)
        findings["leaving"] = _summary_of(_answer_to(transport, leave_game=sc2api_pb2.RequestLeaveGame()))
        findings["after leaving"] = _summary_of(_answer_to(transport, ping=sc2api_pb2.RequestPing()))
        return findings


def _replay_of_a_game_won(installation: Installation) -> bytes:
    """The replay of a game the computer is defeated in, saved once it has ended."""
    with playing(Race.TERRAN, installation) as sandbox:
        request = sandbox.transport.request
        sandbox.client.step(16)
        victory = debug_pb2.DebugEndGame(end_result=debug_pb2.DebugEndGame.DeclareVictory)
        request(sc2api_pb2.Request(debug=sc2api_pb2.RequestDebug(debug=[debug_pb2.DebugCommand(end_game=victory)])))
        for _ in range(16):
            if request(sc2api_pb2.Request(observation=sc2api_pb2.RequestObservation())).status == _ENDED_STATUS:
                break
            request(sc2api_pb2.Request(step=sc2api_pb2.RequestStep(count=2)))
        return request(sc2api_pb2.Request(save_replay=sc2api_pb2.RequestSaveReplay())).save_replay.data


def _played_to_its_end(transport: WebSocketTransport, replay_data: bytes, player: int) -> dict[str, Any]:
    """Play the replay back as `player` to its end: the last observation's step, status and results, and what a step
    after it is answered."""
    start = _start_replay(transport, player=player, replay_data=replay_data)
    last = None
    for response in _observations_to_the_end(transport, _STEPS_PER_TURN):
        last = response
    if last is None:
        return {"start": start}
    results = [[entry.player_id, sc2api_pb2.Result.Name(entry.result)] for entry in last.observation.player_result]
    return {
        "start": start,
        "last step": last.observation.observation.game_loop,
        "last observation": _summary_of(last) | {"results": results},
        "a step after the end": _summary_of(_answer_to(transport, step=sc2api_pb2.RequestStep(count=_STEPS_PER_TURN))),
    }


def sweep_ending(installation: Installation, replay: Path, recording: Path) -> dict[str, Any]:
    """How a replay of a game won ends, and how the shared game's, saved at its time limit, ends."""
    replays = {"a game won": _replay_of_a_game_won(installation), "the shared game": replay.read_bytes()}
    findings: dict[str, Any] = {}
    with _replay_client(installation) as transport:
        for name, replay_data in replays.items():
            findings[name] = {
                "info": _replay_info(transport, replay_data),
                **{f"as player {player}": _played_to_its_end(transport, replay_data, player) for player in (1, 2)},
            }
    return findings


def _replay_of_units_unseen(installation: Installation) -> tuple[bytes, int, dict[str, int]]:
    """Play a game of two players from here in which the first sees the enemy's main and then loses sight of it, has an
    undetected enemy observer in its main and is far from an enemy marine, all made by debug commands, and then the
    enemy's townhall and that marine die out of its sight. Return its replay, the first player's id, and the steps at
    which it sees the enemy's main, after it lost sight of it, and after the two died."""
    with playing_rivals(Race.TERRAN, installation) as rivals:
        me, enemy = rivals.me, rivals.enemy
        observation = me.client.observation()
        game_map = GameMap._from_game(me.client.game_info(), observation)
        own_start, enemy_start = game_map.all_start_locations
        middle = game_map.playable_area.center
        open_ground = OpenGround(game_map, observation.observation.raw_data.units)
        # In a game of two a step passes only once both ask for it, so the units are made without the sandbox's
        # spawn, which steps one of them.
        me.debug(me.create(UnitTypeId.MARINE, me.player, enemy_start.towards(middle, 7.0)))
        # A player whose structures are all gone has lost, so the enemy keeps a bay when its townhall dies.
        kept_structure_site = open_ground.claim(enemy_start.towards(middle, _ENEMY_BAY_DISTANCE), half=2)
        enemy.debug(
            enemy.create(UnitTypeId.OBSERVER, enemy.player, own_start.towards(middle, 4.0)),
            enemy.create(UnitTypeId.ZERGLING, enemy.player, own_start.towards(middle, 5.0)),
            enemy.create(UnitTypeId.WIDOW_MINE_BURROWED, enemy.player, own_start.towards(middle, 6.0)),
            enemy.create(UnitTypeId.MARINE, enemy.player, open_ground.claim(middle, half=1)),
            enemy.create(UnitTypeId.ENGINEERING_BAY, enemy.player, kept_structure_site),
        )
        # A zergling made burrowed is not made, so this one burrows, under the cheat that gives every upgrade.
        enemy.cheat(Cheat.UPGRADE)
        rivals.step(_UNSEEN_SETTLE_STEPS)
        zerglings = [u.tag for u in enemy.units() if u.owner == enemy.player and u.unit_type == UnitTypeId.ZERGLING]
        for tag in zerglings:
            enemy.order(_BURROW_ZERGLING, tag)
        rivals.step(_BURROW_STEPS)
        steps = {"seeing the enemy's main": me.client.observation().observation.game_loop}
        me.kill(unit.tag for unit in me.units() if unit.owner == me.player and unit.unit_type == UnitTypeId.MARINE)
        rivals.step(_UNSEEN_SETTLE_STEPS)
        steps["after losing sight of it"] = me.client.observation().observation.game_loop
        doomed_types = (UnitTypeId.COMMAND_CENTER, UnitTypeId.MARINE)
        enemy.kill(unit.tag for unit in enemy.units() if unit.owner == enemy.player and unit.unit_type in doomed_types)
        rivals.step(_UNSEEN_SETTLE_STEPS)
        steps["after its townhall and far marine died"] = me.client.observation().observation.game_loop
        rivals.step(_UNSEEN_SETTLE_STEPS)
        replay = me.transport.request(sc2api_pb2.Request(save_replay=sc2api_pb2.RequestSaveReplay()))
        return replay.save_replay.data, me.player, steps


def _observations_at(
    transport: WebSocketTransport, replay_data: bytes, *, player: int, disable_fog: bool, steps: Sequence[int]
) -> list[sc2api_pb2.ResponseObservation]:
    """The observations `player` is shown at each of `steps` of the replay, in order, with the fog off or on."""
    _start_replay(transport, player=player, disable_fog=disable_fog, replay_data=replay_data)
    observations = []
    for step in steps:
        now = _observe_the_replay(transport).observation.observation.game_loop
        _answer_to(transport, step=sc2api_pb2.RequestStep(count=step - now))
        observations.append(_observe_the_replay(transport).observation)
    return observations


def _tiles_in_sight(observation: sc2api_pb2.ResponseObservation) -> numpy.ndarray:
    """Whether player 1 sees each tile, indexed `[x, y]`."""
    visibility = observation.observation.raw_data.map_state.visibility
    whole_map = Rectangle(0, 0, visibility.size.x, visibility.size.y)
    return image_array(visibility, whole_map) == _TILE_IN_SIGHT


def _enemy_units_unseen(
    fog_on: sc2api_pb2.ResponseObservation, fog_off: sc2api_pb2.ResponseObservation
) -> dict[str, Any]:
    """Every enemy unit of `fog_off`, sorted by its display and whether its tile is in player 1's sight, against
    `fog_on`: whether that lists it under its tag or as a copy of its type where it stands, and its health and orders
    in both."""
    in_sight = _tiles_in_sight(fog_off)
    listed_with_the_fog_on = list(fog_on.observation.raw_data.units)
    by_tag = {unit.tag: unit for unit in listed_with_the_fog_on}
    kinds: dict[str, Counter[str]] = {}
    examples: list[dict[str, Any]] = []
    for unit in fog_off.observation.raw_data.units:
        if unit.alliance != raw_pb2.Alliance.Enemy:
            continue
        sight = "in sight" if in_sight[int(unit.pos.x), int(unit.pos.y)] else "out of sight"
        kind = f"{raw_pb2.DisplayType.Name(unit.display_type)}, on a tile {sight}"
        fog_on_twin = by_tag.get(unit.tag)
        copies = [
            other
            for other in listed_with_the_fog_on
            if other.tag != unit.tag
            and other.unit_type == unit.unit_type
            and abs(other.pos.x - unit.pos.x) < _SAME_PLACE
            and abs(other.pos.y - unit.pos.y) < _SAME_PLACE
        ]
        if fog_on_twin is not None:
            listed = f"listed with the fog on under its tag, {raw_pb2.DisplayType.Name(fog_on_twin.display_type)}"
        elif copies:
            listed = f"listed with the fog on as a copy, {raw_pb2.DisplayType.Name(copies[0].display_type)}"
        else:
            listed = "not listed with the fog on"
        kinds.setdefault(kind, Counter())[listed] += 1
        if len(examples) < 12 or kind in _KINDS_AT_THE_EDGE_OF_SIGHT:
            seen_as = fog_on_twin or (copies[0] if copies else None)
            examples.append(
                {
                    "type": unit.unit_type,
                    "kind": kind,
                    "listed": listed,
                    "distance to the nearest tile in sight": _distance_to_sight(in_sight, unit),
                    "radius": unit.radius,
                    "health, fog off": unit.health,
                    "health, fog on": None if seen_as is None else seen_as.health,
                    "owner and maximum health, fog off": [unit.owner, unit.health_max],
                    "cloak and burrowed, fog off": [raw_pb2.CloakState.Name(unit.cloak), unit.is_burrowed],
                    "orders, fog off": len(unit.orders),
                    "orders, fog on": None if seen_as is None else len(seen_as.orders),
                }
            )
    listed_with_the_fog_off = list(fog_off.observation.raw_data.units)
    listed_alone: Counter[str] = Counter(
        raw_pb2.DisplayType.Name(unit.display_type)
        for unit in listed_with_the_fog_on
        if unit.alliance == raw_pb2.Alliance.Enemy and not _listed_where_it_stands(unit, listed_with_the_fog_off)
    )
    return {
        "enemy units by kind": {kind: dict(listed) for kind, listed in kinds.items()},
        "enemy units listed with the fog on alone, by display": dict(listed_alone),
        "enemy units with orders, fog off": sum(
            1 for unit in fog_off.observation.raw_data.units if unit.alliance == raw_pb2.Alliance.Enemy and unit.orders
        ),
        "the same tiles in sight": bool((_tiles_in_sight(fog_on) == in_sight).all()),
        "examples": examples,
    }


def _listed_where_it_stands(unit: raw_pb2.Unit, units: Sequence[raw_pb2.Unit]) -> bool:
    """Whether `units` lists `unit`, under its tag or as one of its type where it stands."""
    return any(
        other.tag == unit.tag
        or (
            other.unit_type == unit.unit_type
            and abs(other.pos.x - unit.pos.x) < _SAME_PLACE
            and abs(other.pos.y - unit.pos.y) < _SAME_PLACE
        )
        for other in units
    )


def _deaths_reported(
    observation: sc2api_pb2.ResponseObservation, before: sc2api_pb2.ResponseObservation
) -> dict[str, int]:
    """The units `observation` reports dead, by the type `before` gives each, or `unknown`."""
    types = {unit.tag: unit.unit_type for unit in before.observation.raw_data.units}
    return dict(Counter(str(types.get(tag, "unknown")) for tag in observation.observation.raw_data.event.dead_units))


def _distance_to_sight(in_sight: numpy.ndarray, unit: raw_pb2.Unit) -> float | None:
    """How far `unit` stands from the center of the nearest tile in sight, or `None` if no tile is."""
    xs, ys = numpy.nonzero(in_sight)
    if not len(xs):
        return None
    return round(float(numpy.hypot(xs + 0.5 - unit.pos.x, ys + 0.5 - unit.pos.y).min()), 2)


def _enemy_tags_kept(earlier: sc2api_pb2.ResponseObservation, later: sc2api_pb2.ResponseObservation) -> dict[str, int]:
    """Of the enemy units `later` lists that `earlier` listed by type where they stand, how many kept their tag, by
    their display in `later`."""
    earlier_tags = {
        (unit.unit_type, round(unit.pos.x), round(unit.pos.y)): unit.tag
        for unit in earlier.observation.raw_data.units
        if unit.alliance == raw_pb2.Alliance.Enemy
    }
    kept: Counter[str] = Counter()
    for unit in later.observation.raw_data.units:
        earlier_tag = earlier_tags.get((unit.unit_type, round(unit.pos.x), round(unit.pos.y)))
        if unit.alliance == raw_pb2.Alliance.Enemy and earlier_tag is not None:
            same = "kept its tag" if earlier_tag == unit.tag else "under another tag"
            kept[f"{raw_pb2.DisplayType.Name(unit.display_type)}, {same}"] += 1
    return dict(kept)


def sweep_unseen(installation: Installation, replay: Path, recording: Path) -> dict[str, Any]:
    """What the fog off shows player 1 of the enemy units it does not see, against what the fog on shows it."""
    replay_data, player, steps = _replay_of_units_unseen(installation)
    findings: dict[str, Any] = {"player": player, "steps": steps}
    with _replay_client(installation) as transport:
        fog_on_views = _observations_at(
            transport, replay_data, player=player, disable_fog=False, steps=list(steps.values())
        )
        fog_off_views = _observations_at(
            transport, replay_data, player=player, disable_fog=True, steps=list(steps.values())
        )
    for index, name in enumerate(steps):
        fog_on, fog_off = fog_on_views[index], fog_off_views[index]
        findings[name] = {"step played back to": fog_off.observation.game_loop} | _enemy_units_unseen(fog_on, fog_off)
        if index:
            findings[name]["deaths reported since the step before, by type"] = {
                "fog on": _deaths_reported(fog_on, fog_off_views[index - 1]),
                "fog off": _deaths_reported(fog_off, fog_off_views[index - 1]),
            }
    findings["enemy units standing still, from the first step to the second, fog off"] = _enemy_tags_kept(
        fog_off_views[0], fog_off_views[1]
    )
    return findings


def _replay_of_a_scouting_game(installation: Installation, random_seed: int) -> bytes:
    """Play 100 seconds against the computer, seeded `random_seed`, in which an SCV scouts the enemy's main and comes
    back, as the integration test of a playback with the fog off does, and return its replay."""
    api = Api()
    scout_ids: list[int] = []

    @api.on(TurnEvent, once=True)
    def send(_: TurnEvent) -> None:
        scout = api.units.own.workers[0]
        scout_ids.append(scout.id)
        api.orders.issue(scout, AbilityId.MOVE, target=api.map.all_start_locations[1])

    @api.on(TurnEvent, at_time=GameTime(steps=_SCOUT_BACK_AT_STEP))
    def come_back(_: TurnEvent) -> None:
        if (scout := api.units.own.get(scout_ids[0])) is not None:
            api.orders.issue(scout, AbilityId.MOVE, target=api.map.start_location)

    with tempfile.TemporaryDirectory() as directory:
        replay = Path(directory) / "scouting.SC2Replay"
        computer = Computer(Race.ZERG, Difficulty.VERY_EASY)
        run_local(
            MAP,
            api,
            computer,
            race=Race.TERRAN,
            steps_per_turn=_STEPS_PER_TURN,
            time_limit=GameTime.from_seconds(_SCOUTING_SECONDS),
            random_seed=random_seed,
            save_replay_to=replay,
            installation=installation,
        )
        return replay.read_bytes()


def _copies_retagged_in_the_fog(fog_on_views: Sequence[sc2api_pb2.ResponseObservation]) -> list[dict[str, Any]]:
    """Each copy in the fog that the fog-on view lists under another tag, still in the fog where it stood, at the next
    observation."""
    retagged = []
    for before, after in itertools.pairwise(fog_on_views):
        later_copies = [unit for unit in after.observation.raw_data.units if unit.display_type == _SNAPSHOT_DISPLAY]
        for unit in before.observation.raw_data.units:
            if unit.display_type != _SNAPSHOT_DISPLAY:
                continue
            for other in later_copies:
                same_place = abs(other.pos.x - unit.pos.x) < _SAME_PLACE and abs(other.pos.y - unit.pos.y) < _SAME_PLACE
                if other.tag != unit.tag and other.unit_type == unit.unit_type and same_place:
                    retagged.append(
                        {
                            "type": unit.unit_type,
                            "alliance": raw_pb2.Alliance.Name(unit.alliance),
                            "steps": [before.observation.game_loop, after.observation.game_loop],
                        }
                    )
    return retagged


def sweep_scouting(installation: Installation, replay: Path, recording: Path) -> dict[str, Any]:
    """What the fog off lists of the units out of sight in games of scouting, against the fog on: the enemy's hidden
    units and the map's units in the fog, the rest of each observation, and the copies the fog on gives new tags."""
    hidden: Counter[str] = Counter()
    in_the_fog: Counter[str] = Counter()
    fields_differing: Counter[str] = Counter()
    retagged: dict[int, list[dict[str, Any]]] = {}
    observations = 0
    for seed in _SCOUTING_SEEDS:
        replay_data = _replay_of_a_scouting_game(installation, seed)
        with _replay_client(installation) as transport:
            _start_replay(transport, player=1, replay_data=replay_data)
            fog_on_views = [observed.observation for observed in _observations_to_the_end(transport, _STEPS_PER_TURN)]
            _start_replay(transport, player=1, disable_fog=True, replay_data=replay_data)
            fog_off_views = [observed.observation for observed in _observations_to_the_end(transport, _STEPS_PER_TURN)]
        observations += len(fog_off_views)
        _sort_the_units_out_of_sight(fog_on_views, fog_off_views, hidden, in_the_fog, fields_differing)
        retagged[seed] = _copies_retagged_in_the_fog(fog_on_views)
    return {
        "games": len(_SCOUTING_SEEDS),
        "observations": observations,
        "hidden enemy units, fog off": dict(hidden),
        "the map's units in the fog, fog off": dict(in_the_fog),
        "observations whose field differs between the views": dict(fields_differing),
        "copies the fog on lists under another tag at the next observation, by seed": retagged,
    }


def _sort_the_units_out_of_sight(
    fog_on_views: Sequence[sc2api_pb2.ResponseObservation],
    fog_off_views: Sequence[sc2api_pb2.ResponseObservation],
    hidden: Counter[str],
    in_the_fog: Counter[str],
    fields_differing: Counter[str],
) -> None:
    """Count into `hidden` and `in_the_fog` the enemy's hidden units and the map's units in the fog of each fog-off
    view, and into `fields_differing` the fields of the rest of the observation that differ from the fog-on view."""
    for fog_on, fog_off in zip(fog_on_views, fog_off_views, strict=False):
        listed_with_the_fog_on = {unit.tag for unit in fog_on.observation.raw_data.units}
        in_sight = _tiles_in_sight(fog_off)
        for unit in fog_off.observation.raw_data.units:
            sight = "in sight" if in_sight[int(unit.pos.x), int(unit.pos.y)] else "out of sight"
            listed = "listed with the fog on" if unit.tag in listed_with_the_fog_on else "not listed with the fog on"
            if unit.alliance == raw_pb2.Alliance.Enemy and unit.display_type == raw_pb2.DisplayType.Hidden:
                owner = "an owner" if unit.owner else "no owner"
                health = "a maximum health" if unit.health_max else "no maximum health"
                hidden[f"on a tile {sight}, reporting {owner} and {health}, {listed}"] += 1
            elif unit.alliance == raw_pb2.Alliance.Neutral and unit.display_type == _SNAPSHOT_DISPLAY:
                contents = "contents" if unit.mineral_contents or unit.vespene_contents else "no contents"
                in_the_fog[f"on a tile {sight}, reporting {contents}, {listed} under its tag"] += 1
        on, off = fog_on.observation, fog_off.observation
        for name, one, other in (
            ("creep", on.raw_data.map_state.creep, off.raw_data.map_state.creep),
            ("visibility", on.raw_data.map_state.visibility, off.raw_data.map_state.visibility),
            ("score", on.score, off.score),
            ("player", on.player_common, off.player_common),
            ("alerts", list(on.alerts), list(off.alerts)),
            ("actions", list(fog_on.actions), list(fog_off.actions)),
            ("effects", list(on.raw_data.effects), list(off.raw_data.effects)),
            ("deaths", list(on.raw_data.event.dead_units), list(off.raw_data.event.dead_units)),
        ):
            fields_differing[name] += one != other


def _timed_playback(
    transport: WebSocketTransport, replay_data: bytes, *, steps_per_turn: int, disable_fog: bool
) -> dict[str, Any]:
    """How long playing the replay back takes, observing every `steps_per_turn` steps, and an observation's size."""
    start = _start_replay(transport, player=1, disable_fog=disable_fog, replay_data=replay_data)
    sizes = []
    last_step = 0
    began = time.perf_counter()
    for response in _observations_to_the_end(transport, steps_per_turn):
        sizes.append(response.observation.ByteSize())
        last_step = response.observation.observation.game_loop
    seconds = time.perf_counter() - began
    return {
        "start seconds": start["seconds"],
        "observations": len(sizes),
        "seconds": round(seconds, 2),
        "steps a second": round(last_step / seconds),
        "observation bytes, mean": round(sum(sizes) / len(sizes)),
        "observation bytes, most": max(sizes),
    }


def sweep_speed(installation: Installation, replay: Path, recording: Path) -> dict[str, Any]:
    """How fast a replay plays back, and what recording one played back at every step costs."""
    replay_data = replay.read_bytes()
    findings: dict[str, Any] = {}
    with _replay_client(installation) as transport:
        for steps_per_turn in (_STEPS_PER_TURN, 1):
            for disable_fog in (False, True):
                name = f"{steps_per_turn} steps a turn, fog {'off' if disable_fog else 'on'}"
                findings[name] = _timed_playback(
                    transport, replay_data, steps_per_turn=steps_per_turn, disable_fog=disable_fog
                )
    with tempfile.TemporaryDirectory() as directory:
        played_back = Path(directory) / "played-back.sc2rec"
        began = time.perf_counter()
        result = run_replay(replay, Api(), player_id=1, record_to=played_back, installation=installation)
        recorded_seconds = time.perf_counter() - began
        began = time.perf_counter()
        api = Api()
        run_recording(played_back, api)
        findings["recorded through run_replay, every step"] = {
            "result": result.name,
            "seconds, the client's start included": round(recorded_seconds, 2),
            "recording megabytes": round(played_back.stat().st_size / 1e6, 2),
            "run_recording seconds": round(time.perf_counter() - began, 2),
            "run_recording last step": api.step,
        }
    return findings


_SWEEPS_BY_NAME: dict[str, Callable[[Installation, Path, Path], object]] = {
    "views": sweep_views,
    "requests": sweep_requests_answered,
    "ending": sweep_ending,
    "unseen": sweep_unseen,
    "scouting": sweep_scouting,
    "speed": sweep_speed,
}


def main(argv: Sequence[str]) -> None:
    """Play the shared game, run the sweeps named, or all of them, and write the findings."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sweeps", nargs="*", help=f"{', '.join(_SWEEPS_BY_NAME)}; all when none are named")
    parser.add_argument("--out", type=Path, default=Path("replays.json"), help="where to write the findings")
    arguments = parser.parse_args(argv)
    if unknown := set(arguments.sweeps) - set(_SWEEPS_BY_NAME):
        raise SystemExit(f"No sweep is called {', '.join(sorted(unknown))}")
    installation = Installation.find()
    with tempfile.TemporaryDirectory() as directory:
        replay, recording = _record_the_shared_game(Path(directory))
        findings = {
            name: sweep(installation, replay, recording)
            for name, sweep in _SWEEPS_BY_NAME.items()
            if name in arguments.sweeps or not arguments.sweeps
        }
    arguments.out.write_text(json.dumps(findings, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
    logger.info("Wrote the findings to {}", arguments.out)


if __name__ == "__main__":
    main(sys.argv[1:])
