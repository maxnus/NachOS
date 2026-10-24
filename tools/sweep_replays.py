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
import json
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from _sandbox import MAP, playing
from loguru import logger
from s2clientprotocol import common_pb2, debug_pb2, query_pb2, raw_pb2, sc2api_pb2

from sc2nachos import Api, Computer, Difficulty, Race, run_local, run_recording, run_replay
from sc2nachos.launch import GameProcess, Installation
from sc2nachos.protocol import Recording, WebSocketTransport

_DEMO_BOT = Path(__file__).parents[1] / "examples" / "demo_bot.py"
_GAME_SECONDS = 300
_STEPS_PER_TURN = 8
_IN_REPLAY = sc2api_pb2.Status.in_replay
_ENDED = sc2api_pb2.Status.ended
_INTERFACE = sc2api_pb2.InterfaceOptions(
    raw=True,
    score=True,
    show_cloaked=True,
    show_burrowed_shadows=True,
    show_placeholders=True,
    raw_crop_to_playable_area=False,
)
_MOVE_ABILITY = 16
_SCV = 45
# The steps at which `views` compares the views.
_VIEW_STEPS = (0, 2240, 4480, 6712)
_UNIT_FIELDS = ("type", "owner", "display", "x", "y", "health", "shield", "energy", "orders")
# The step at which `views` compares the enemy units player 1 did not see with their owner's view of them, and the
# fields it compares.
_UNSEEN_UNITS_STEP = 4480
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
        time_limit=_GAME_SECONDS,
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
    request = sc2api_pb2.RequestStartReplay(observed_player_id=player, options=_INTERFACE, disable_fog=disable_fog)
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
    """What the replay shows `player` at each of `_VIEW_STEPS`, with the tags it lists."""
    start = _start_replay(transport, player=player, disable_fog=disable_fog, replay_data=replay_data)
    if start["errors"] or start["error"]:
        return {-1: {"start": start}}
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
    scv = next(unit for unit in units if unit.alliance == raw_pb2.Alliance.Self and unit.unit_type == _SCV)
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
            if request(sc2api_pb2.Request(observation=sc2api_pb2.RequestObservation())).status == _ENDED:
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


_SWEEPS: dict[str, Callable[[Installation, Path, Path], object]] = {
    "views": sweep_views,
    "requests": sweep_requests_answered,
    "ending": sweep_ending,
    "speed": sweep_speed,
}


def main(argv: Sequence[str]) -> None:
    """Play the shared game, run the sweeps named, or all of them, and write the findings."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sweeps", nargs="*", help=f"{', '.join(_SWEEPS)}; all when none are named")
    parser.add_argument("--out", type=Path, default=Path("replays.json"), help="where to write the findings")
    arguments = parser.parse_args(argv)
    if unknown := set(arguments.sweeps) - set(_SWEEPS):
        raise SystemExit(f"No sweep is called {', '.join(sorted(unknown))}")
    installation = Installation.find()
    with tempfile.TemporaryDirectory() as directory:
        replay, recording = _record_the_shared_game(Path(directory))
        findings = {
            name: sweep(installation, replay, recording)
            for name, sweep in _SWEEPS.items()
            if name in arguments.sweeps or not arguments.sweeps
        }
    arguments.out.write_text(json.dumps(findings, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
    logger.info("Wrote the findings to {}", arguments.out)


if __name__ == "__main__":
    main(sys.argv[1:])
