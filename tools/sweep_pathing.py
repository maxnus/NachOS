"""Measure what the game's pathing grid shows as a game goes on, and what the pathing query does from inside a
structure.

Needs StarCraft II installed. `RequestGameInfo`, asked again during a game, answers a pathing grid of the ground as the
game held it at the last observation asked for.

`timeline` plays one game on the sandbox's map. For each action it takes, it asks for an observation and then the grid
before the action, straight after it and after each step that follows, and records each step at which the tiles near
the unit, or the unit as the observation reports it, change. First, with no cheat on:

- an enemy engineering bay made in sight of a missile turret of this player's, out of sight of its other units, then
  the turret killed;
- an enemy engineering bay made out of sight, then `show_map` turned on and off, and on again, with the change across
  the whole map recorded too.

Then, under `show_map`, so that no unit's sight changes what the grid shows:

- a supply depot made, lowered, raised and killed;
- a barracks made, lifted, landed and killed;
- this player's first townhall lifted and landed again;
- a depot an SCV builds near the start;
- the mineral field nearest the start killed, the one farthest from it, and the rock nearest the start that stands
  on ground the grid blocks;
- a marine and a sieged tank made.

It also reads the tiles a barracks made blocks after 16 steps with no observation asked for, and again after one
observation. It times the request, and records which of the answer's fields differ between the first answer and the
last.

`footprints` plays on the sandbox's map under `show_map`, on the ground `tools/sweep_placement.py footprints` clears,
with this player's workers and the map's resources gone. It makes one unit at a time of every structure type and every
neutral type that does not move, and records the tiles the grid blocks once it stands, beside the footprint
`data/footprints.json` holds for it.

`inside` makes one unit at a time of a few structure types for this player, and asks the pathing query from its center
and from the lowest tile of the leftmost column it blocks to open ground 10 tiles away::

    uv run python tools/sweep_pathing.py timeline --out pathing-timeline.json
    uv run python tools/sweep_pathing.py footprints --out pathing-footprints.json
    uv run python tools/sweep_pathing.py inside --out pathing-inside.json
"""

import argparse
import json
import sys
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy
from _sandbox import NEUTRAL, NEUTRAL_REPORTED, Cheat, OpenGround, Sandbox, playing
from loguru import logger
from s2clientprotocol import common_pb2, error_pb2, query_pb2, raw_pb2, sc2api_pb2
from sweep_placement import (
    _PROBE_REACH,
    _WIDE_PROBE_REACH,
    FOOTPRINTS,
    MeasuredFootprint,
    OutOfOpenGroundError,
    _measured,
    _measuring_ground,
    _MeasuringGround,
    _tile_centers_around,
    _types_to_measure,
)

from sc2nachos.gamemap import GameMap
from sc2nachos.gamemap._image_data import image_tiles
from sc2nachos.geometry import Point, Rectangle, Tile
from sc2nachos.ids import UnitTypeId
from sc2nachos.ids.raw import RawAbilityId, RawUnitTypeId
from sc2nachos.launch import Installation
from sc2nachos.match import Race
from sc2nachos.protocol import GameEndedError
from sc2nachos.units._unit_type import UnitType

# How far from a unit, in tiles along each axis, the tiles a trace reports lie.
_TRACE_REACH = 7
_TRACE_STEPS = 64
_TRACE_STEPS_TO_LAND = 160
_TRACE_STEPS_TO_BUILD = 320
# An enemy structure and the turret that sees it stand this far apart, within the turret's sight of 11 and beyond
# either's trace reach of the other.
_TURRET_DISTANCE = 8
# How far around the enemy structure a trace of its sight reports, so as to leave out the turret.
_SIGHT_TRACE_REACH = 3
_GAME_INFO_TIMINGS = 20
_UNOBSERVED_STEPS = 16
_FOOTPRINT_WAITING_STEPS = 16
# How many games may run out of open ground while one type is being measured before it is left unmeasured.
_GAMES_OUT_OF_GROUND_PER_TYPE = 2
_INSIDE_TYPES = (
    UnitTypeId.SUPPLY_DEPOT,
    UnitTypeId.BARRACKS,
    UnitTypeId.BUNKER,
    UnitTypeId.MISSILE_TURRET,
    UnitTypeId.COMMAND_CENTER,
    UnitTypeId.PYLON,
    UnitTypeId.GATEWAY,
    UnitTypeId.NEXUS,
    UnitTypeId.HATCHERY,
    UnitTypeId.SPINE_CRAWLER,
)
_INSIDE_REACH = 7
_INSIDE_TARGET_DISTANCE = 10
_SETTLING_STEPS = 8


def _walkable_now(game: Sandbox, playable: Rectangle) -> numpy.ndarray:
    """Where the game's pathing grid, asked for now, lets a ground unit walk, by tile of `playable`."""
    return image_tiles(game.client.game_info().start_raw.pathing_grid, playable) != 0


def _walkable_observed(game: Sandbox, playable: Rectangle) -> numpy.ndarray:
    """Where the game's pathing grid lets a ground unit walk, by tile of `playable`, asked for after an observation,
    since the grid shows the game as of the last one."""
    game.client.observation()
    return _walkable_now(game, playable)


def _tiles_near(mask: numpy.ndarray, playable: Rectangle, near: Point, reach: int) -> list[tuple[int, int]]:
    """The tiles within `reach` of the tile holding `near`, along each axis, where `mask` is true, as `(x, y)`."""
    x0, y0 = int(near.x) - int(playable.x), int(near.y) - int(playable.y)
    xs, ys = numpy.nonzero(mask[max(x0 - reach, 0) : x0 + reach + 1, max(y0 - reach, 0) : y0 + reach + 1])
    left, bottom = int(playable.x) + max(x0 - reach, 0), int(playable.y) + max(y0 - reach, 0)
    return sorted((left + int(x), bottom + int(y)) for x, y in zip(xs, ys, strict=True))


def _state_of(unit: raw_pb2.Unit | None) -> str:
    """The unit as the game reports it: its type, whether it flies, its build progress unless it is built, and how
    it is displayed unless it is in sight; or `not reported`."""
    if unit is None:
        return "not reported"
    parts = [RawUnitTypeId(unit.unit_type).name]
    if unit.is_flying:
        parts.append("flying")
    if unit.build_progress < 1:
        parts.append(f"built {unit.build_progress:.2f}")
    if unit.display_type != raw_pb2.DisplayType.Visible:
        parts.append(raw_pb2.DisplayType.Name(unit.display_type).lower())
    return ", ".join(parts)


@dataclass(slots=True)
class TraceStep:
    """What a trace saw at one step."""

    step: int
    """Steps since the action: -1 before it, and 0 straight after it, before any step."""
    game_loop: int
    blocked: list[tuple[int, int]]
    """The tiles near the unit the grid blocks that it did not before the action."""
    opened: list[tuple[int, int]]
    """The tiles near the unit the grid leaves open that it blocked before the action."""
    state: str
    """The unit as the game reports it, as `_state_of` describes it."""
    blocked_anywhere: int
    """How many tiles of the whole map the grid blocks that it did not before the action."""
    opened_anywhere: int


@dataclass(slots=True)
class Trace:
    """One action, and each step at which what it watches changed."""

    action: str
    near: tuple[float, float]
    answer: str | None
    """The game's answer to the order, where the action was one."""
    steps: list[TraceStep]
    """The step before the action, then each step at which the tiles near the unit or its state changed."""


@dataclass(slots=True)
class TimelineFindings:
    """Every action traced in one game, and what a `RequestGameInfo` costs in it."""

    base_build: int = 0
    map_name: str = ""
    game_info_ms: float = 0.0
    """The mean time a `RequestGameInfo` takes, in milliseconds."""
    game_info_bytes: int = 0
    unobserved: dict[str, int] = field(default_factory=dict)
    """The tiles a barracks made blocks, read after `_UNOBSERVED_STEPS` steps with no observation asked for, and read
    again after one observation."""
    fields_changed: list[str] = field(default_factory=list)
    """The fields of `ResponseGameInfo`, and of its `start_raw`, that differ between the first answer and the last."""
    traces: list[Trace] = field(default_factory=list)


UnitFinder = Callable[[Sequence[raw_pb2.Unit]], raw_pb2.Unit | None]


def _by_tag(tag: int) -> UnitFinder:
    """A finder of the unit `tag`."""
    return lambda units: next((unit for unit in units if unit.tag == tag), None)


def _new_of_type(unit_type: UnitTypeId, owner: int, known: set[int]) -> UnitFinder:
    """A finder of a unit of `unit_type` reported as `owner`'s whose tag is not in `known`."""
    return lambda units: next(
        (unit for unit in units if unit.tag not in known and unit.unit_type == unit_type and unit.owner == owner),
        None,
    )


def _of_type_near(unit_type: UnitTypeId, owner: int, near: Point) -> UnitFinder:
    """A finder of a unit of `unit_type` reported as `owner`'s within `_TRACE_REACH` of `near`."""
    return lambda units: next(
        (
            unit
            for unit in units
            if unit.unit_type == unit_type
            and unit.owner == owner
            and abs(unit.pos.x - near.x) <= _TRACE_REACH
            and abs(unit.pos.y - near.y) <= _TRACE_REACH
        ),
        None,
    )


class _Tracer:
    """Traces actions in one game, each around the unit it is about."""

    def __init__(self, game: Sandbox, playable: Rectangle, findings: TimelineFindings) -> None:
        self._game = game
        self._playable = playable
        self._findings = findings

    def trace(
        self,
        action: str,
        near: Point,
        unit_finder: UnitFinder,
        act: Callable[[], error_pb2.ActionResult.ValueType | None],
        *,
        steps: int = _TRACE_STEPS,
        reach: int = _TRACE_REACH,
        whole_map: bool = False,
    ) -> raw_pb2.Unit | None:
        """Take `act`, trace it around `near` for `steps` steps, and return the unit `unit_finder` finds last. With
        `whole_map`, a change anywhere on the map is recorded too."""
        game = self._game
        observation, walkable_before = self._observed()
        unit = unit_finder(observation.raw_data.units)
        recorded = [self._trace_step(-1, observation, unit, walkable_before, walkable_before, near, reach)]
        answer = act()
        for step in range(steps + 1):
            if step:
                game.client.step(1)
            observation, walkable_now = self._observed()
            unit = unit_finder(observation.raw_data.units)
            seen = self._trace_step(step, observation, unit, walkable_before, walkable_now, near, reach)
            last = recorded[-1]
            watched = (seen.blocked, seen.opened, seen.state)
            if watched != (last.blocked, last.opened, last.state) or (
                whole_map
                and (seen.blocked_anywhere, seen.opened_anywhere) != (last.blocked_anywhere, last.opened_anywhere)
            ):
                recorded.append(seen)
        answer_name = None if answer is None else error_pb2.ActionResult.Name(answer)
        self._findings.traces.append(Trace(action, (near.x, near.y), answer_name, recorded))
        summary = "; ".join(
            f"step {seen.step}: +{len(seen.blocked)} -{len(seen.opened)} (map +{seen.blocked_anywhere} "
            f"-{seen.opened_anywhere}) {seen.state}"
            for seen in recorded
        )
        logger.info("{} ({}): {}", action, answer_name or "no order", summary)
        return unit

    def _observed(self) -> tuple[sc2api_pb2.Observation, numpy.ndarray]:
        """An observation, then the grid, asked for in that order, as a bot asks for them each step."""
        observation = self._game.client.observation().observation
        return observation, _walkable_now(self._game, self._playable)

    def _trace_step(
        self,
        step: int,
        observation: sc2api_pb2.Observation,
        unit: raw_pb2.Unit | None,
        walkable_before: numpy.ndarray,
        walkable_now: numpy.ndarray,
        near: Point,
        reach: int,
    ) -> TraceStep:
        newly_blocked = walkable_before & ~walkable_now
        newly_opened = ~walkable_before & walkable_now
        return TraceStep(
            step=step,
            game_loop=observation.game_loop,
            blocked=_tiles_near(newly_blocked, self._playable, near, reach),
            opened=_tiles_near(newly_opened, self._playable, near, reach),
            state=_state_of(unit),
            blocked_anywhere=int(newly_blocked.sum()),
            opened_anywhere=int(newly_opened.sum()),
        )

    def made(self, action: str, unit_type: UnitTypeId, owner: int, at: Point, **trace: Any) -> raw_pb2.Unit | None:
        """Trace one `unit_type` made for `owner` at `at`, and return it."""
        known = {unit.tag for unit in self._game.units()}
        reported_owner = NEUTRAL_REPORTED if owner == NEUTRAL else owner
        finder = _new_of_type(unit_type, reported_owner, known)
        return self.trace(
            action, at, finder, lambda: self._game.debug(self._game.create(unit_type, owner, at)), **trace
        )

    def ordered(
        self, action: str, unit: raw_pb2.Unit, ability: int, target: Point | None = None, **trace: Any
    ) -> raw_pb2.Unit | None:
        """Trace `ability` ordered on `unit`, and return the unit as it is at the end."""
        near = Point((unit.pos.x, unit.pos.y))
        return self.trace(action, near, _by_tag(unit.tag), lambda: self._game.order(ability, unit.tag, target), **trace)

    def killed(self, action: str, unit: raw_pb2.Unit, **trace: Any) -> None:
        """Trace `unit` killed."""
        near = Point((unit.pos.x, unit.pos.y))
        self.trace(action, near, _by_tag(unit.tag), lambda: self._game.kill([unit.tag]), **trace)


def _time_game_info(game: Sandbox) -> tuple[float, int]:
    """The mean time a `RequestGameInfo` takes in milliseconds, and the size of its answer in bytes."""
    started = time.perf_counter()
    for _ in range(_GAME_INFO_TIMINGS):
        game.client.game_info()
    elapsed = time.perf_counter() - started
    return elapsed / _GAME_INFO_TIMINGS * 1000, game.client.game_info().ByteSize()


def _made_or_kept(unit: raw_pb2.Unit | None, what: str) -> raw_pb2.Unit:
    """`unit`, raising if the game did not make it or no longer reports it."""
    if unit is None:
        raise RuntimeError(f"the game did not make or keep {what}")
    return unit


def _nearest_to(units: Sequence[raw_pb2.Unit], to: Point) -> raw_pb2.Unit:
    """The unit of `units` standing nearest to `to`."""
    return min(units, key=lambda unit: (unit.pos.x - to.x) ** 2 + (unit.pos.y - to.y) ** 2)


def trace_timeline(installation: Installation) -> TimelineFindings:
    """Trace each action the module docstring lists, in one game."""
    findings = TimelineFindings()
    with playing(Race.TERRAN, installation) as game:
        info = game.client.game_info()
        findings.base_build = game.client.ping().base_build
        findings.map_name = info.map_name
        findings.game_info_ms, findings.game_info_bytes = _time_game_info(game)
        playable = Rectangle._from_proto(info.start_raw.playable_area)
        game_map = GameMap._from_game(info, game.client.observation())
        units_at_start = game.units()
        ground = OpenGround(game_map, units_at_start)
        tracer = _Tracer(game, playable, findings)
        _trace_sight(game, tracer, ground, game_map, playable)
        _trace_structures(game, tracer, ground, game_map.start_location, units_at_start)
        _trace_the_maps_units(game, tracer, game_map.start_location, playable)
        _trace_units(game, tracer, ground, game_map.start_location)
        findings.unobserved = _blocked_unobserved(game, ground, game_map.start_location, playable)
        findings.fields_changed = _fields_changed(info, game.client.game_info())
    return findings


def _blocked_unobserved(game: Sandbox, ground: OpenGround, start: Point, playable: Rectangle) -> dict[str, int]:
    """The tiles a barracks made blocks, read with no observation asked for since it was made, and read again after
    one."""
    at = ground.claim(start, 7)
    walkable_before = _walkable_observed(game, playable)
    game.debug(game.create(UnitTypeId.BARRACKS, game.player, at))
    game.client.step(_UNOBSERVED_STEPS)
    unobserved = int((walkable_before & ~_walkable_now(game, playable)).sum())
    game.client.observation()
    observed = int((walkable_before & ~_walkable_now(game, playable)).sum())
    logger.info("A barracks made blocks {} tiles unobserved, {} once observed", unobserved, observed)
    return {"unobserved": unobserved, "observed": observed}


def _fields_changed(first: sc2api_pb2.ResponseGameInfo, last: sc2api_pb2.ResponseGameInfo) -> list[str]:
    """The fields of `first` and of its `start_raw` whose value `last` does not share, `start_raw` itself left out."""
    changed = [
        descriptor.name
        for descriptor in first.DESCRIPTOR.fields
        if descriptor.name != "start_raw" and getattr(first, descriptor.name) != getattr(last, descriptor.name)
    ]
    start_raw_fields = first.start_raw.DESCRIPTOR.fields
    return changed + [
        f"start_raw.{descriptor.name}"
        for descriptor in start_raw_fields
        if getattr(first.start_raw, descriptor.name) != getattr(last.start_raw, descriptor.name)
    ]


def _enemy_of(game: Sandbox) -> int:
    """The other player's id, in a game of two."""
    return 2 if game.player == 1 else 1


def _trace_sight(game: Sandbox, tracer: _Tracer, ground: OpenGround, game_map: GameMap, playable: Rectangle) -> None:
    """Trace an enemy structure coming into sight and going out of it, and `show_map` turned on, off and on again,
    which it is left. The turret stands halfway to the map's center, out of sight of this player's other units."""
    enemy = _enemy_of(game)
    start = game_map.start_location
    halfway = Point(((start.x + playable.center.x) / 2, (start.y + playable.center.y) / 2))
    eye_at = ground.claim(halfway, 7)
    turrets = game.spawn([(UnitTypeId.MISSILE_TURRET, game.player, eye_at)])
    seen_at = Point((eye_at.x + _TURRET_DISTANCE, eye_at.y))
    seen = tracer.made(
        "an enemy engineering bay made in sight of a turret",
        UnitTypeId.ENGINEERING_BAY,
        enemy,
        seen_at,
        reach=_SIGHT_TRACE_REACH,
    )
    if turrets and seen is not None:
        tracer.trace(
            "the turret that sees the enemy engineering bay killed",
            Point((seen.pos.x, seen.pos.y)),
            _by_tag(seen.tag),
            lambda: game.kill([turrets[0].tag]),
            reach=_SIGHT_TRACE_REACH,
        )
    far_at = ground.claim(playable.center, 7)
    tracer.made("an enemy engineering bay made out of sight", UnitTypeId.ENGINEERING_BAY, enemy, far_at)
    unseen = _of_type_near(UnitTypeId.ENGINEERING_BAY, enemy, far_at)
    for action in ("show_map turned on", "show_map turned off", "show_map turned on again"):
        tracer.trace(action, far_at, unseen, lambda: game.cheat(Cheat.SHOW_MAP), whole_map=True)


def _trace_structures(
    game: Sandbox, tracer: _Tracer, ground: OpenGround, start: Point, units_at_start: Sequence[raw_pb2.Unit]
) -> None:
    """Trace this player's structures made, lowered, raised, lifted, landed, built and killed."""
    own = game.player
    depot = _made_or_kept(
        tracer.made("a supply depot made", UnitTypeId.SUPPLY_DEPOT, own, ground.claim(start, 7)), "a depot"
    )
    depot = _made_or_kept(tracer.ordered("the depot lowered", depot, RawAbilityId.Morph_SupplyDepot_Lower), "the depot")
    depot = _made_or_kept(tracer.ordered("the depot raised", depot, RawAbilityId.Morph_SupplyDepot_Raise), "the depot")
    tracer.killed("the depot killed", depot)

    barracks = _made_or_kept(
        tracer.made("a barracks made", UnitTypeId.BARRACKS, own, ground.claim(start, 7)), "a barracks"
    )
    landing_at = Point((barracks.pos.x, barracks.pos.y))
    barracks = _made_or_kept(
        tracer.ordered("the barracks lifted", barracks, RawAbilityId.Lift_Barracks), "the barracks"
    )
    barracks = _made_or_kept(
        tracer.ordered(
            "the barracks landed", barracks, RawAbilityId.Land_Barracks, landing_at, steps=_TRACE_STEPS_TO_LAND
        ),
        "the barracks",
    )
    tracer.killed("the barracks killed", barracks)

    townhalls = [unit for unit in units_at_start if unit.owner == own and unit.unit_type == UnitTypeId.COMMAND_CENTER]
    townhall = _made_or_kept(
        tracer.ordered(
            "this player's first townhall lifted", _nearest_to(townhalls, start), RawAbilityId.Lift_CommandCenter
        ),
        "the townhall",
    )
    tracer.ordered(
        "this player's first townhall landed",
        townhall,
        RawAbilityId.Land_CommandCenter,
        start,
        steps=_TRACE_STEPS_TO_LAND,
    )

    game.cheat(Cheat.MINERALS)
    building_at = ground.claim(start, 2)
    scvs = [unit for unit in game.units() if unit.owner == own and unit.unit_type == UnitTypeId.SCV]
    scv = _nearest_to(scvs, building_at)
    known = {unit.tag for unit in game.units()}
    tracer.trace(
        "a depot an SCV builds near the start",
        building_at,
        _new_of_type(UnitTypeId.SUPPLY_DEPOT, own, known),
        lambda: game.order(RawAbilityId.TerranBuild_SupplyDepot, scv.tag, building_at),
        steps=_TRACE_STEPS_TO_BUILD,
    )


def _trace_the_maps_units(game: Sandbox, tracer: _Tracer, start: Point, playable: Rectangle) -> None:
    """Trace the mineral fields nearest the start and farthest from it killed, and the rock nearest the start that
    stands on ground the grid blocks, as `show_map` shows them: a snapshot's tag is not the unit's."""
    neutral = [unit for unit in game.units() if unit.owner == NEUTRAL_REPORTED]
    fields = [unit for unit in neutral if unit.unit_type in UnitType.AnyMineralField._type_ids]
    tracer.killed("the mineral field nearest the start killed", _nearest_to(fields, start))
    farthest = max(fields, key=lambda unit: (unit.pos.x - start.x) ** 2 + (unit.pos.y - start.y) ** 2)
    tracer.killed("the mineral field farthest from the start killed", farthest)
    walkable = _walkable_observed(game, playable)
    rocks = [
        unit
        for unit in neutral
        if unit.unit_type in UnitType.Destructible._type_ids
        and unit.unit_type not in UnitType.CollapsibleTower._type_ids
        and not walkable[int(unit.pos.x) - int(playable.x), int(unit.pos.y) - int(playable.y)]
    ]
    if rocks:
        tracer.killed(
            "the rock nearest the start that blocks walking killed", _nearest_to(rocks, start), steps=_TRACE_STEPS * 2
        )


def _trace_units(game: Sandbox, tracer: _Tracer, ground: OpenGround, start: Point) -> None:
    """Trace a marine and a sieged tank made."""
    tracer.made("a marine made", UnitTypeId.MARINE, game.player, ground.claim(start, 7))
    tracer.made("a sieged tank made", UnitTypeId.SIEGE_TANK_SIEGED, game.player, ground.claim(start, 7))


@dataclass(slots=True)
class WalkingFootprint:
    """The tiles the grid blocked under one unit, beside the footprint `data/footprints.json` holds for its type."""

    blocked: MeasuredFootprint
    steps: int | None
    """The steps at which the grid blocked them, counted from the observation `spawn` asks for 4 steps after the unit
    was made, or None if it blocked nothing within `_FOOTPRINT_WAITING_STEPS`."""
    placement_rows: list[str] | None
    """The footprint `data/footprints.json` holds, or None where it holds none."""
    same_as_placement: bool


@dataclass(slots=True)
class FootprintFindings:
    """The tiles the grid blocked under each type, by its curated name."""

    base_build: int = 0
    unit_types: dict[str, WalkingFootprint] = field(default_factory=dict)
    not_made: list[str] = field(default_factory=list)


def _walking_footprint(
    game: Sandbox,
    playable: Rectangle,
    unit_type: UnitTypeId,
    owner: int,
    at: Point,
    reach: int,
    placement: dict[str, Any],
) -> WalkingFootprint | None:
    """Make one `unit_type` for `owner` at `at`, and read the tiles within `reach` of it the grid blocks once it
    does. None if the game would not make one."""
    walkable_before = _walkable_observed(game, playable)
    made = game.spawn([(unit_type, owner, at)])
    if not made:
        return None
    position = Point((made[0].pos.x, made[0].pos.y))
    blocked: list[tuple[int, int]] = []
    steps = None
    for step in range(_FOOTPRINT_WAITING_STEPS + 1):
        if step:
            game.client.step(1)
        blocked = _tiles_near(walkable_before & ~_walkable_observed(game, playable), playable, position, reach)
        if blocked:
            steps = step
            break
    centers = _tile_centers_around(position, reach)
    measured = _measured((position.x, position.y), {Tile(x, y) for x, y in blocked}, centers)
    placement_rows = placement.get(RawUnitTypeId(made[0].unit_type).name, {}).get("rows")
    return WalkingFootprint(measured, steps, placement_rows, same_as_placement=placement_rows == measured.rows)


def _measured_walking_footprint(
    game: Sandbox,
    ground: _MeasuringGround,
    playable: Rectangle,
    unit_type: UnitTypeId,
    owner: int,
    placement: dict[str, Any],
) -> WalkingFootprint | None:
    """The tiles the grid blocks under one `unit_type` made for `owner`, read within `_PROBE_REACH` and again within
    `_WIDE_PROBE_REACH` if they reach that far. None if the game would not make one."""
    footprint = None
    for reach in (_PROBE_REACH, _WIDE_PROBE_REACH):
        at = ground.cleared(reach)
        footprint = _walking_footprint(game, playable, unit_type, owner, at, reach, placement)
        if footprint is None or not footprint.blocked.touches_edge:
            break
    return footprint


def measure_footprints(installation: Installation) -> FootprintFindings:
    """Read the tiles the grid blocks under one unit of every structure type and every neutral type that does not
    move. With every resource gone the game ends in a tie after a few minutes, and the sweep goes on in a new one, as it
    does when a game runs out of open ground."""
    placement = json.loads(FOOTPRINTS.read_text(encoding="utf-8"))["unit_types"]
    findings = FootprintFindings()
    games_out_of_ground: Counter[UnitTypeId] = Counter()
    to_measure = None
    while to_measure is None or to_measure:
        with playing(Race.TERRAN, installation) as game:
            if to_measure is None:
                findings.base_build = game.client.ping().base_build
                to_measure = [
                    kind
                    for kind in _types_to_measure(game.client.game_data().units)
                    if not kind.moves and not kind.made_only_on_creep
                ]
            ground = _measuring_ground(game)
            playable = Rectangle._from_proto(game.client.game_info().start_raw.playable_area)
            try:
                while to_measure:
                    kind = to_measure[0]
                    owner = NEUTRAL if kind.neutral else game.player
                    footprint = _measured_walking_footprint(game, ground, playable, kind.unit_type, owner, placement)
                    if footprint is None:
                        findings.not_made.append(kind.unit_type.name)
                    else:
                        findings.unit_types[kind.unit_type.name] = footprint
                        picture = "\n".join(footprint.blocked.rows) or "(nothing)"
                        logger.info("{} blocks, after {} steps:\n{}", kind.unit_type.name, footprint.steps, picture)
                    to_measure.pop(0)
            except GameEndedError:
                logger.info("The game ended; measuring the {} types left in a new one", len(to_measure))
            except OutOfOpenGroundError as error:
                kind = to_measure[0]
                games_out_of_ground[kind.unit_type] += 1
                if games_out_of_ground[kind.unit_type] >= _GAMES_OUT_OF_GROUND_PER_TYPE:
                    findings.not_made.append(kind.unit_type.name)
                    to_measure.pop(0)
                logger.info("{}; measuring the {} types left in a new game", error, len(to_measure))
    return findings


@dataclass(slots=True)
class InsideAnswer:
    """The pathing query's answers from inside one structure to open ground."""

    center: tuple[float, float]
    edge_tile: tuple[int, int] | None
    """The lowest tile of the leftmost column the grid blocks under it, or None if it blocks none."""
    target: tuple[float, float]
    straight: float
    from_center: float
    from_edge_tile: float | None


def _pathing_query(game: Sandbox, pairs: Sequence[tuple[Point, Point]]) -> list[float]:
    """The game's pathing distance between each pair of points."""
    requests = [
        query_pb2.RequestQueryPathing(
            start_pos=common_pb2.Point2D(x=start.x, y=start.y), end_pos=common_pb2.Point2D(x=end.x, y=end.y)
        )
        for start, end in pairs
    ]
    response = game.transport.request(sc2api_pb2.Request(query=query_pb2.RequestQuery(pathing=requests)))
    if response.error:
        raise RuntimeError(f"the game refused the query: {list(response.error)}")
    return [answer.distance for answer in response.query.pathing]


def _walkable_target(walkable: numpy.ndarray, playable: Rectangle, center: Point) -> Point:
    """The walkable tile center nearest to a point `_INSIDE_TARGET_DISTANCE` to the right of `center`."""
    wanted = numpy.array((center.x + _INSIDE_TARGET_DISTANCE - playable.x, center.y - playable.y))
    xs, ys = numpy.nonzero(walkable)
    nearest = int(numpy.argmin((xs + 0.5 - wanted[0]) ** 2 + (ys + 0.5 - wanted[1]) ** 2))
    return Point((playable.x + int(xs[nearest]) + 0.5, playable.y + int(ys[nearest]) + 0.5))


def ask_from_inside(installation: Installation) -> dict[str, InsideAnswer]:
    """The pathing query's answers from inside one structure of each of `_INSIDE_TYPES`, made for this player."""
    answers: dict[str, InsideAnswer] = {}
    with playing(Race.TERRAN, installation) as game:
        game.cheat(Cheat.SHOW_MAP)
        info = game.client.game_info()
        playable = Rectangle._from_proto(info.start_raw.playable_area)
        game_map = GameMap._from_game(info, game.client.observation())
        ground = OpenGround(game_map, game.units())
        for unit_type in _INSIDE_TYPES:
            at = ground.claim(game_map.start_location, _INSIDE_REACH)
            walkable_before = _walkable_observed(game, playable)
            made = game.spawn([(unit_type, game.player, at)])
            if not made:
                logger.warning("The game did not make a {}", unit_type.name)
                continue
            game.client.step(_SETTLING_STEPS)
            walkable = _walkable_observed(game, playable)
            center = Point((made[0].pos.x, made[0].pos.y))
            blocked = _tiles_near(walkable_before & ~walkable, playable, center, _INSIDE_REACH)
            edge_tile = min(blocked) if blocked else None
            target = _walkable_target(walkable, playable, center)
            pairs = [(center, target)]
            if edge_tile is not None:
                pairs.append((Point((edge_tile[0] + 0.5, edge_tile[1] + 0.5)), target))
            distances = _pathing_query(game, pairs)
            answers[unit_type.name] = InsideAnswer(
                center=(center.x, center.y),
                edge_tile=edge_tile,
                target=(target.x, target.y),
                straight=center.distance_to(target),
                from_center=distances[0],
                from_edge_tile=distances[1] if edge_tile is not None else None,
            )
            logger.info("{}: {}", unit_type.name, answers[unit_type.name])
            game.remove(unit.tag for unit in made)
    return answers


def main(argv: Sequence[str]) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=("timeline", "footprints", "inside"))
    parser.add_argument("--out", type=Path, help="where to write what it found, as JSON")
    arguments = parser.parse_args(argv)
    installation = Installation.find()
    if arguments.mode == "timeline":
        found: Any = asdict(trace_timeline(installation))
    elif arguments.mode == "footprints":
        found = asdict(measure_footprints(installation))
    else:
        found = {name: asdict(answer) for name, answer in ask_from_inside(installation).items()}
    text = json.dumps(found, indent=2)
    if arguments.out is None:
        print(text)
    else:
        arguments.out.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1:])
