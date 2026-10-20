"""Measure the ground each structure covers, and what lies under the map's rocks.

Needs StarCraft II installed. `footprints` plays on the sandbox's map under `show_map`, with this player's workers and
the map's resources gone, in a new game whenever one ends in the tie a game with no resources comes to after a few
minutes. On open ground it makes one unit at a time of every structure type and every neutral type, and asks the game
at every tile center around it whether a one-tile structure could stand there: a sensor tower, which the game refuses
on creep, and a queen's creep tumor, which it refuses off creep. The tiles both are refused are the unit's footprint.
Then, in a game of its own, it asks the game about each structure type a unit places at a point, on open ground and
on a hatchery's creep, with a pylon powering both. It writes all of it to `data/footprints.json`, which
`tools/generate_footprints.py` reads. `creep` asks only those last questions again, and writes their answers into
`data/footprints.json` beside the footprints already there.

`ground-under-rocks` plays one game on each map of the corpus under `show_map`. For each neutral unit but a resource,
it asks the same about every tile of the unit's footprint, as `data/footprints.json` has it, before and after killing
the unit and whatever it leaves behind. It prints, for each map and type, how many of those tiles the start's
placement grid leaves open or blocks, by whether the game took a structure there with the unit standing and once it was
gone.

`grids` plays one game on each map of the corpus under `show_map`, with every unit of both players' that is no
structure killed, and asks the same about every tile of the playable area. It prints every tile where the game and
`api.placement.free` disagree::

    uv run python tools/sweep_placement.py footprints
    uv run python tools/sweep_placement.py creep
    uv run python tools/generate_footprints.py
    uv run python tools/sweep_placement.py ground-under-rocks --out ground-under-rocks.json
    uv run python tools/sweep_placement.py grids --out placement-grids.json
"""

import argparse
import json
import math
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from operator import attrgetter
from pathlib import Path
from typing import Any

import numpy
from _sandbox import NEUTRAL, NEUTRAL_REPORTED, Cheat, Sandbox, playing, without_uncurated_units
from generate_footprints import centers_where_it_stood
from loguru import logger
from record_corpus import GAMES
from s2clientprotocol import data_pb2, error_pb2, raw_pb2

from sc2nachos.gamedata import Attribute, GameData, TargetType
from sc2nachos.gamemap import GameMap
from sc2nachos.gamemap._image_data import image_tiles
from sc2nachos.geometry import Footprint, Point, Tile
from sc2nachos.ids import AbilityId, UncuratedIdError, UnitTypeId
from sc2nachos.ids.raw import RawAbilityId, RawUnitTypeId
from sc2nachos.launch import Installation
from sc2nachos.match import Race
from sc2nachos.orders import OrderBook
from sc2nachos.placement import PlacementGrids
from sc2nachos.protocol import GameEndedError
from sc2nachos.state._state import _State
from sc2nachos.units._tracking import _Tracker
from sc2nachos.units._unit_type import UnitType

REPO = Path(__file__).parents[1]
FOOTPRINTS = REPO / "data" / "footprints.json"

# In tiles along each axis from the center asked about: wide enough for a townhall, and asked again wider for what
# reaches the edge. The widest footprint, a huge rock's, is 12 tiles across.
_PROBE_REACH = 5
_WIDE_PROBE_REACH = 8
# How far past the tiles asked about a unit may stand and still cover one of them.
_NEIGHBOR_REACH = 8
_CLEARING_TRIES = 8
_MEASURING_TRIES = 3
_SETTLING_STEPS = 32
_DEBRIS_STEPS = 96
_ADD_ON_BUILD_STEPS = 480
_TILES_ASKED_AT_ONCE = 4096
_CREEP_SPREADING_STEPS = 240
_SPOTS_TO_TRY_ON_CREEP = ((0, 0), (1, 0), (0, 1), (-1, 0), (0, -1), (1, 1), (-1, -1), (2, 0), (0, 2))
# How long a hatchery's creep may take to cover a townhall's ground beside it: about 960 steps, seen.
_CREEP_COVERING_STEPS = 3200
_OFF_CREEP_PROBE = AbilityId.SCV_BUILD_SENSOR_TOWER
_ON_CREEP_PROBE = RawAbilityId.Build_CreepTumor_Queen
_RESOURCES = UnitType.AnyMineralField._type_ids | UnitType.AnyVespeneGeyser._type_ids
# The game creates these only on creep, so they are measured last, on a hatchery's creep.
_MADE_ONLY_ON_CREEP = (UnitTypeId.CREEP_TUMOR, UnitTypeId.CREEP_TUMOR_QUEEN)
# A build the placement query refuses under the ability's own id, by the id of the unit that places it.
_ASKED_AS = {
    UnitTypeId.CREEP_TUMOR_QUEEN: RawAbilityId.Build_CreepTumor_Queen,
    UnitTypeId.CREEP_TUMOR: RawAbilityId.Build_CreepTumor_Tumor,
}
# The add-ons the game does not create alone, each with its host and its host's build of it. Lifted off, a host leaves
# a plain tech lab or reactor behind, so these are measured beside their host.
_ADD_ON_HOSTS = {
    UnitTypeId.TECH_LAB_BARRACKS: (UnitTypeId.BARRACKS, RawAbilityId.Build_TechLab_Barracks),
    UnitTypeId.REACTOR_BARRACKS: (UnitTypeId.BARRACKS, RawAbilityId.Build_Reactor_Barracks),
    UnitTypeId.TECH_LAB_FACTORY: (UnitTypeId.FACTORY, RawAbilityId.Build_TechLab_Factory),
    UnitTypeId.REACTOR_FACTORY: (UnitTypeId.FACTORY, RawAbilityId.Build_Reactor_Factory),
    UnitTypeId.TECH_LAB_STARPORT: (UnitTypeId.STARPORT, RawAbilityId.Build_TechLab_Starport),
    UnitTypeId.REACTOR_STARPORT: (UnitTypeId.STARPORT, RawAbilityId.Build_Reactor_Starport),
}


@dataclass(slots=True)
class MeasuredFootprint:
    """The tiles one unit covered, as the game refused the probes around it."""

    position: tuple[float, float]
    lower_left: tuple[int, int] | None
    """The lowest, leftmost corner of the tiles covered, or None where it covered none."""
    rows: list[str]
    """The tiles covered within their bounding box, the top row first: `#` covered, `.` not."""
    touches_edge: bool
    """Whether a covered tile lies on the edge of the tiles asked about, so the footprint may reach past them."""


@dataclass(slots=True)
class FootprintFindings:
    """Every footprint measured, by the raw name of the unit's type."""

    base_build: int = 0
    unit_types: dict[str, MeasuredFootprint] = field(default_factory=dict)
    """The types that do not move."""
    moving: dict[str, MeasuredFootprint] = field(default_factory=dict)
    """The structure types that move, such as a flying barracks, measured for the record."""
    not_made: list[str] = field(default_factory=list)
    creep: dict[str, dict[str, str]] = field(default_factory=dict)
    """For each structure type a unit places at a point, the game's answer to its placement on creep and off it, with
    a pylon's power over both: `Success` or why not."""


def _tile_centers_around(at: Point, reach: int) -> list[Point]:
    """Every tile center within `reach` tiles of `at` along each axis."""
    x0, y0 = math.floor(at.x), math.floor(at.y)
    return [
        Point((x + 0.5, y + 0.5)) for x in range(x0 - reach, x0 + reach + 1) for y in range(y0 - reach, y0 + reach + 1)
    ]


def _covered_tiles(game: Sandbox, centers: Sequence[Point]) -> set[Tile]:
    """The tiles, of those centered at `centers`, the game refuses both a sensor tower and a creep tumor."""
    off_creep = game.placeable(_OFF_CREEP_PROBE, centers)
    on_creep = game.placeable(_ON_CREEP_PROBE, centers)
    return {
        Tile.containing(center)
        for center, sensor_tower_taken, creep_tumor_taken in zip(centers, off_creep, on_creep, strict=True)
        if not sensor_tower_taken and not creep_tumor_taken
    }


def _measured(position: tuple[float, float], covered: set[Tile], centers: Sequence[Point]) -> MeasuredFootprint:
    """The footprint of a unit at `position` that covered the tiles `covered`, of those centered at `centers`."""
    if not covered:
        return MeasuredFootprint(position, None, [], touches_edge=False)
    xs = [tile.x for tile in covered]
    ys = [tile.y for tile in covered]
    rows = [
        "".join("#" if Tile(x, y) in covered else "." for x in range(min(xs), max(xs) + 1))
        for y in range(max(ys), min(ys) - 1, -1)
    ]
    edge_xs = {math.floor(min(center.x for center in centers)), math.floor(max(center.x for center in centers))}
    edge_ys = {math.floor(min(center.y for center in centers)), math.floor(max(center.y for center in centers))}
    touches_edge = any(tile.x in edge_xs or tile.y in edge_ys for tile in covered)
    return MeasuredFootprint(position, (min(xs), min(ys)), rows, touches_edge=touches_edge)


def _units_near(game: Sandbox, at: Point, reach: int) -> list[raw_pb2.Unit]:
    """The units standing close enough to cover a tile within `reach` tiles of `at`."""
    reach += _NEIGHBOR_REACH
    return [unit for unit in game.units() if abs(unit.pos.x - at.x) <= reach and abs(unit.pos.y - at.y) <= reach]


class _MeasuringGround:
    """The open ground units are made on to be measured, one patch for each reach asked about.

    It kills only neutral units and the units this player has since been given, so both players keep what they
    started with and the game goes on.
    """

    def __init__(self, game: Sandbox, game_map: GameMap) -> None:
        self._game = game
        self._game_map = game_map
        self._patches: dict[int, Point] = {}
        self._players_units = {unit.tag for unit in game.units() if unit.owner != NEUTRAL_REPORTED}

    def _removable_near(self, at: Point, reach: int) -> list[raw_pb2.Unit]:
        """The units near `at` this sweep may kill: the neutral ones and those it made for this player."""
        return [
            unit
            for unit in _units_near(self._game, at, reach)
            if unit.tag not in self._players_units and unit.owner in (NEUTRAL_REPORTED, self._game.player)
        ]

    def cleared(self, reach: int) -> Point:
        """The center of a patch where the game takes a sensor tower at every tile within `reach`, with nothing left
        standing on it: the last one handed out if it clears, or new open ground if it will not."""
        at = self._patches.get(reach)
        if at is None or not self._clear(at, reach):
            at = self._open_ground(reach)
            logger.info("Measuring within {} of {}", reach, at)
        self._patches[reach] = at
        return at

    def _clear(self, at: Point, reach: int) -> bool:
        """Kill what may be killed around `at` and wait until the game refuses neither probe at any tile within
        `reach`; whether it did."""
        centers = _tile_centers_around(at, reach)
        covered: set[Tile] = set()
        for _ in range(_CLEARING_TRIES):
            self._game.kill(unit.tag for unit in self._removable_near(at, reach))
            self._game.client.step(_SETTLING_STEPS)
            covered = _covered_tiles(self._game, centers)
            if not self._removable_near(at, reach) and not covered:
                return True
        logger.warning("The ground around {} did not clear: {} tiles stay covered", at, len(covered))
        return False

    def _open_ground(self, reach: int) -> Point:
        """The center of open ground where the game takes a sensor tower at every tile within `reach`: of the open
        squares with no unit standing in them, the farthest from every unit that qualifies. A collapsible tower
        outlives a kill, so each one measured takes up its ground for the rest of the game."""
        size = 2 * reach + 1
        open_squares = self._game_map.placement.where_footprint_fits(Footprint.square(size))
        xs, ys = numpy.nonzero(open_squares.values)
        centers = numpy.stack((xs + open_squares.origin.x + 0.5, ys + open_squares.origin.y + 0.5), axis=-1)
        positions = numpy.array([(unit.pos.x, unit.pos.y) for unit in self._game.units()])
        distance_to_nearest_unit = numpy.linalg.norm(centers[:, numpy.newaxis] - positions[numpy.newaxis], axis=-1).min(
            axis=1
        )
        for index in numpy.argsort(-distance_to_nearest_unit):
            if distance_to_nearest_unit[index] <= reach:
                break
            x, y = centers[index]
            at = Point((float(x), float(y)))
            if not _covered_tiles(self._game, _tile_centers_around(at, reach)):
                return at
        raise RuntimeError(f"no open ground {size} tiles across takes a sensor tower everywhere")


def _measured_add_on(game: Sandbox, add_on: UnitTypeId, at: Point, reach: int) -> MeasuredFootprint | None:
    """The footprint of an `add_on` its host builds beside `at`: the tiles covered once it is built, less those its
    host covered alone. None if the host never finished it."""
    host_type, build = _ADD_ON_HOSTS[add_on]
    centers = _tile_centers_around(at, reach)
    hosts = game.spawn([(host_type, game.player, Point((at.x - 1.5, at.y + 0.5)))])
    if not hosts:
        return None
    host_tiles = _covered_tiles(game, centers)
    game.order(build, hosts[0].tag)
    for _ in range(_ADD_ON_BUILD_STEPS // 8):
        game.client.step(8)
        built = next((unit for unit in game.units() if unit.unit_type == add_on and unit.build_progress == 1), None)
        if built is not None:
            return _measured((built.pos.x, built.pos.y), _covered_tiles(game, centers) - host_tiles, centers)
    return None


def _measured_at(
    game: Sandbox, unit_type: UnitTypeId, owner: int, at: Point, reach: int, *, made_at: Point | None = None
) -> MeasuredFootprint | None:
    """Make one `unit_type` for `owner` at or near `made_at`, `at` unless given, and ask about every tile within
    `reach` of `at`. None if the game would not make one."""
    centers = _tile_centers_around(at, reach)
    made = game.spawn([(unit_type, owner, made_at or at)])
    if not made:
        return None
    return _measured((made[0].pos.x, made[0].pos.y), _covered_tiles(game, centers), centers)


def _measure(
    game: Sandbox, ground: _MeasuringGround, unit_type: UnitTypeId, owner: int, *, on_creep: bool = False
) -> MeasuredFootprint | None:
    """The footprint of one `unit_type` made for `owner`, asked about within `_PROBE_REACH` and again within
    `_WIDE_PROBE_REACH` if it reaches that far, and measured again if its tiles do not center where it stands, as when
    something left over stood beside it. None if the game would not make one."""
    footprint = None
    for _ in range(_MEASURING_TRIES):
        footprint = _measure_once(game, ground, unit_type, owner, on_creep=on_creep)
        if footprint is None or _centers_where_it_stands(footprint):
            return footprint
        logger.warning("{} covered tiles that do not center where it stands; measuring it again", unit_type.name)
    return footprint


def _centers_where_it_stands(measured: MeasuredFootprint) -> bool:
    """Whether the tiles `measured` covered center on where the unit stood, as a footprint centers."""
    return centers_where_it_stood(measured.rows, measured.position, measured.lower_left)


def _measure_once(
    game: Sandbox, ground: _MeasuringGround, unit_type: UnitTypeId, owner: int, *, on_creep: bool
) -> MeasuredFootprint | None:
    """The footprint of one `unit_type` made for `owner`, asked about within `_PROBE_REACH` and again within
    `_WIDE_PROBE_REACH` if it reaches that far. None if the game would not make one."""
    footprint = None
    for reach in (_PROBE_REACH, _WIDE_PROBE_REACH):
        at = ground.cleared(reach)
        if on_creep:
            _leave_creep(game, at)
            # The game makes a creep tumor at some spots on creep and not at others (in game).
            for dx, dy in _SPOTS_TO_TRY_ON_CREEP:
                made_at = Point((at.x + dx, at.y + dy))
                if (footprint := _measured_at(game, unit_type, owner, at, reach, made_at=made_at)) is not None:
                    break
        else:
            footprint = _measured_at(game, unit_type, owner, at, reach)
        if footprint is None and unit_type in _ADD_ON_HOSTS:
            # Asked for an add-on of a host's, the game makes a plain one, which would stand where the host builds.
            footprint = _measured_add_on(game, unit_type, ground.cleared(reach), reach)
        if footprint is None or not footprint.touches_edge:
            break
    if footprint is not None:
        picture = "\n".join(footprint.rows) or "(nothing)"
        logger.info("{} at {}, lower left {}:\n{}", unit_type.name, footprint.position, footprint.lower_left, picture)
        if footprint.touches_edge:
            logger.warning("{} covers a tile on the edge of those asked about", unit_type.name)
    return footprint


def _leave_creep(game: Sandbox, at: Point) -> None:
    """Spread a hatchery's creep around `at`, and take the hatchery away again."""
    hatcheries = game.spawn([(UnitTypeId.HATCHERY, game.player, at)])
    game.client.step(_CREEP_SPREADING_STEPS)
    game.remove(unit.tag for unit in hatcheries)


def _curated(unit_type: int) -> UnitTypeId | None:
    try:
        return UnitTypeId.read(unit_type)
    except UncuratedIdError:
        return None


@dataclass(frozen=True, slots=True)
class _TypeToMeasure:
    """A unit type the sweep makes and measures, and how."""

    unit_type: UnitTypeId
    neutral: bool
    """Whether it is made for no player, as the map's own units are."""
    moves: bool
    made_only_on_creep: bool = False


def _types_to_measure(rows: Iterable[data_pb2.UnitTypeData]) -> list[_TypeToMeasure]:
    """The curated structure types of every race that do not move, the curated neutral types that do not move, the
    structure types that do, and last those the game makes only on creep."""
    structure = data_pb2.Attribute.Value("Structure")
    standing, neutral, moving = [], [], []
    for row in sorted(rows, key=attrgetter("name")):
        unit_type = _curated(row.unit_id)
        if unit_type is None or unit_type is UnitTypeId.NOT_A_UNIT or unit_type in _MADE_ONLY_ON_CREEP:
            continue
        if row.race == 0:
            if row.movement_speed == 0:
                neutral.append(_TypeToMeasure(unit_type, neutral=True, moves=False))
        elif structure in row.attributes:
            moves = row.movement_speed > 0
            (moving if moves else standing).append(_TypeToMeasure(unit_type, neutral=False, moves=moves))
    on_creep = [
        _TypeToMeasure(unit_type, neutral=False, moves=False, made_only_on_creep=True)
        for unit_type in _MADE_ONLY_ON_CREEP
    ]
    return standing + neutral + moving + on_creep


def _measuring_ground(game: Sandbox) -> _MeasuringGround:
    """Open ground on the map of `game`, with this player's workers and every resource gone: workers wander onto the
    ground asked about, and resources take up the open ground beside them."""
    game.cheat(Cheat.SHOW_MAP, Cheat.FREE, Cheat.FAST_BUILD)
    game.client.step(4)
    game.remove(unit.tag for unit in game.units() if unit.unit_type in _RESOURCES | {UnitTypeId.SCV})
    return _MeasuringGround(game, GameMap._from_game(game.client.game_info(), game.client.observation()))


def measure_footprints(installation: Installation) -> FootprintFindings:
    """Measure the footprint of every structure type and every neutral type that the game makes.

    With every resource gone the game ends in a tie after a few minutes, and the sweep goes on in a new one.
    """
    findings = FootprintFindings()
    to_measure: list[_TypeToMeasure] | None = None
    while to_measure is None or to_measure:
        with playing(Race.TERRAN, installation) as game:
            if to_measure is None:
                findings.base_build = game.client.ping().base_build
                to_measure = _types_to_measure(game.client.game_data().units)
            ground = _measuring_ground(game)
            try:
                while to_measure:
                    kind = to_measure[0]
                    owner = NEUTRAL if kind.neutral else game.player
                    footprint = _measure(game, ground, kind.unit_type, owner, on_creep=kind.made_only_on_creep)
                    _record(findings, kind, footprint)
                    to_measure.pop(0)
            except GameEndedError:
                logger.info("The game ended; measuring the {} types left in a new one", len(to_measure))
    logger.info(
        "Measured {} types that stand and {} that move; not made: {}",
        len(findings.unit_types),
        len(findings.moving),
        findings.not_made or "none",
    )
    findings.creep = measure_creep(installation, findings.unit_types)
    return findings


def _builds_placed_at_a_point(game_data: GameData, measured: Iterable[str]) -> dict[str, int]:
    """The ability that places each of the types named in `measured` that a player's unit places at a point, as the
    placement query takes it: a build, or a spell such as a raven's auto turret, which the tables do not mark as
    needing placement."""
    builds = {}
    for name in measured:
        unit_type = UnitTypeId.read(RawUnitTypeId[name])
        row = game_data.units.get(unit_type)
        if row is None or row.race is Race.NONE or row.creation_ability is None:
            continue
        ability = game_data.abilities[row.creation_ability]
        if ability.target_type is TargetType.POINT:
            builds[name] = _ASKED_AS.get(unit_type, row.creation_ability)
    return builds


def _answers_at(game: Sandbox, builds: Mapping[str, int], at: Point) -> dict[str, str]:
    """The game's answer to each of `builds` placed at `at`, by its name."""
    return {
        name: error_pb2.ActionResult.Name(game.placement_answers(ability, [at])[0]) for name, ability in builds.items()
    }


def _wait_for_creep(game: Sandbox, around: Point) -> None:
    """Step the game until creep covers the 5x5 tiles around `around`, as a townhall's ground."""
    for _ in range(_CREEP_COVERING_STEPS // _SETTLING_STEPS):
        observation = game.client.observation()
        game_map = GameMap._from_game(game.client.game_info(), observation)
        creep = image_tiles(observation.observation.raw_data.map_state.creep, game_map.playable_area)
        x, y = game_map.placement.index_of(around)
        if creep[x - 2 : x + 3, y - 2 : y + 3].all():
            return
        game.client.step(_SETTLING_STEPS)
    raise RuntimeError(f"creep never covered the ground around {around}")


def measure_creep(installation: Installation, measured: Iterable[str]) -> dict[str, dict[str, str]]:
    """Ask the game about each structure type in `measured` that a unit places at a point, off creep and then on a
    hatchery's creep beside it, with a pylon powering the ground both times."""
    with playing(Race.TERRAN, installation) as game:
        builds = _builds_placed_at_a_point(GameData(game.client.game_data()), measured)
        ground = _measuring_ground(game)
        answers: dict[bool, dict[str, str]] = {}
        for on_creep in (False, True):
            # Cleared again, the ground may be new, so what stands on it is placed around it each time.
            at = ground.cleared(_WIDE_PROBE_REACH)
            asked = Point((at.x + 1.5, at.y + 1.5))
            standing = [(UnitTypeId.PYLON, game.player, Point((at.x + 4, at.y - 3)))]
            if on_creep:
                standing.append((UnitTypeId.HATCHERY, game.player, Point((at.x - 4.5, at.y - 4.5))))
            game.spawn(standing)
            if on_creep:
                _wait_for_creep(game, asked)
            answers[on_creep] = _answers_at(game, builds, asked)
    for name in builds:
        logger.info("{}: on creep {}, off creep {}", name, answers[True][name], answers[False][name])
    return {name: {"on_creep": answers[True][name], "off_creep": answers[False][name]} for name in builds}


def _record(findings: FootprintFindings, kind: _TypeToMeasure, footprint: MeasuredFootprint | None) -> None:
    name = RawUnitTypeId(kind.unit_type).name
    if footprint is None:
        findings.not_made.append(name)
    elif kind.moves:
        findings.moving[name] = footprint
    else:
        findings.unit_types[name] = footprint


@dataclass(slots=True)
class GroundUnderAUnit:
    """What the game said about the tiles under one neutral unit, before and after it was killed."""

    name: str
    position: tuple[float, float]
    outlived_the_kill: bool = False
    """Whether the game still listed the unit once it was killed, as it does a collapsible tower."""
    debris: list[str] = field(default_factory=list)
    """The raw names of what the unit left behind, killed too."""
    tiles: list[tuple[int, int, bool, bool, bool, float]] = field(default_factory=list)
    """Each tile of the unit's footprint: its corner, whether the start's placement grid takes a structure there,
    whether the game did with the unit standing, whether it did once the unit was gone, and its height."""
    changed_outside: list[tuple[int, int, bool, bool]] = field(default_factory=list)
    """Each tile around the unit but outside its footprint where the game's answer changed: its corner, and whether
    the game took a structure there before and after."""


@dataclass(slots=True)
class GroundUnderRocks:
    """The ground under every neutral unit but a resource on one map."""

    map: str
    units: list[GroundUnderAUnit] = field(default_factory=list)
    not_measured: list[str] = field(default_factory=list)
    """The raw names of the types `data/footprints.json` lacks."""


def _footprint_tiles(measured: Mapping[str, Any], position: Point) -> set[Tile]:
    """The tiles a unit measured as `measured` covers standing at `position`: its footprint moved from where it was
    measured by the whole tiles between the two positions."""
    rows = measured["rows"]
    lower_left = measured["lower_left"]
    if not rows or lower_left is None:
        return set()
    measured_at = measured["position"]
    dx = round(position.x - measured_at[0])
    dy = round(position.y - measured_at[1])
    height = len(rows)
    return {
        Tile(lower_left[0] + dx + x, lower_left[1] + dy + height - 1 - row)
        for row, line in enumerate(rows)
        for x, mark in enumerate(line)
        if mark == "#"
    }


def _taken(game: Sandbox, tiles: Sequence[Tile]) -> dict[Tile, bool]:
    """Whether the game takes a one-tile structure on each of `tiles`, on creep or off it."""
    centers = [tile.center for tile in tiles]
    off_creep = game.placeable(_OFF_CREEP_PROBE, centers)
    on_creep = game.placeable(_ON_CREEP_PROBE, centers)
    return {tile: a or b for tile, a, b in zip(tiles, off_creep, on_creep, strict=True)}


def _check_ground_on(
    installation: Installation, map_name: str, footprints: Mapping[str, Mapping[str, Any]]
) -> GroundUnderRocks:
    """Ask the game about the ground under each neutral unit on `map_name` but a resource, before and after killing
    it."""
    ground = GroundUnderRocks(map_name)
    with playing(Race.TERRAN, installation, map_name=map_name) as game:
        game.cheat(Cheat.SHOW_MAP)
        game.client.step(4)
        observation = game.client.observation()
        game_map = GameMap._from_game(game.client.game_info(), observation)
        raw, _ = without_uncurated_units(observation.observation.raw_data)
        blockers = [unit for unit in raw.units if unit.owner == NEUTRAL_REPORTED and unit.unit_type not in _RESOURCES]
        for blocker in blockers:
            name = RawUnitTypeId(blocker.unit_type).name
            if name not in footprints:
                ground.not_measured.append(name)
                continue
            position = Point((blocker.pos.x, blocker.pos.y))
            under = _footprint_tiles(footprints[name], position)
            around = sorted(Tile.containing(center) for center in _tile_centers_around(position, _WIDE_PROBE_REACH))
            before = _taken(game, around)
            tags_before = {unit.tag for unit in game.units()}
            measured = GroundUnderAUnit(name, (position.x, position.y))
            game.kill([blocker.tag])
            game.client.step(_DEBRIS_STEPS)
            left_behind = [
                unit
                for unit in game.units()
                if unit.tag not in tags_before
                and unit.owner == NEUTRAL_REPORTED
                and position.distance_to(Point((unit.pos.x, unit.pos.y))) < 2 * _WIDE_PROBE_REACH
            ]
            measured.debris = sorted(RawUnitTypeId(unit.unit_type).name for unit in left_behind)
            game.kill(unit.tag for unit in left_behind)
            game.client.step(_SETTLING_STEPS)
            measured.outlived_the_kill = blocker.tag in {unit.tag for unit in game.units()}
            after = _taken(game, around)
            for tile in sorted(under):
                start_taken = bool(game_map.placement[tile.center]) if tile in before else False
                height = float(game_map.height[tile.center]) if tile in before else math.nan
                measured.tiles.append(
                    (tile.x, tile.y, start_taken, before.get(tile, False), after.get(tile, False), height)
                )
            measured.changed_outside = [
                (tile.x, tile.y, before[tile], after[tile])
                for tile in around
                if tile not in under and before[tile] != after[tile]
            ]
            ground.units.append(measured)
    return ground


def _summary(ground: GroundUnderRocks) -> str:
    """For each type on the map, how many units outlived the kill, and how many tiles of their footprints the start's
    placement grid leaves open or blocks, by whether the game took a structure there with the unit standing and once
    it was gone."""
    counts: dict[str, Counter[str]] = {}
    for unit in ground.units:
        count = counts.setdefault(unit.name, Counter())
        count["units"] += 1
        count["outlived the kill"] += unit.outlived_the_kill
        for _, _, start_taken, before, after, _ in unit.tiles:
            start = "start open" if start_taken else "start blocks"
            count[
                f"{start}, {'taken' if before else 'refused'} with it, {'taken' if after else 'refused'} without"
            ] += 1
    return "\n".join(f"  {name}: {dict(count)}" for name, count in sorted(counts.items()))


def check_ground_under_rocks(installation: Installation) -> list[GroundUnderRocks]:
    """Ask about the ground under the neutral units of every map in the corpus."""
    footprints = json.loads(FOOTPRINTS.read_text(encoding="utf-8"))["unit_types"]
    all_ground = []
    for map_name in sorted({game.map for game in GAMES}):
        ground = _check_ground_on(installation, map_name, footprints)
        all_ground.append(ground)
        logger.info("{}; not measured: {}\n{}", map_name, ground.not_measured or "none", _summary(ground))
    return all_ground


@dataclass(slots=True)
class GridCheck:
    """Where the game and `api.placement.free` disagree on one map."""

    map: str
    asked: int = 0
    """How many tiles the game was asked about."""
    disagreed: list[tuple[int, int, bool]] = field(default_factory=list)
    """Each tile the two disagree on, by its corner, with whether the game takes a structure there."""


def _check_grids_on(installation: Installation, map_name: str) -> GridCheck:
    """Ask the game about every tile of `map_name`'s playable area, with only structures left standing, and compare
    its answers with the dynamic grid."""
    with playing(Race.TERRAN, installation, map_name=map_name) as game:
        game.cheat(Cheat.SHOW_MAP)
        game.client.step(4)
        data = GameData(game.client.game_data())
        structure = Attribute.STRUCTURE
        game.remove(
            unit.tag
            for unit in game.units()
            if unit.owner != NEUTRAL_REPORTED
            and (unit_type := _curated(unit.unit_type)) is not None
            and structure not in data.units[unit_type].attributes
        )
        observation = game.client.observation()
        game_map = GameMap._from_game(game.client.game_info(), observation)
        tracker = _Tracker(data, None)
        raw, _ = without_uncurated_units(observation.observation.raw_data)
        tracker.update(raw, observation.observation.game_loop)
        state = _State(observation, tracker, game_map)
        order_book = OrderBook(data)
        present_units = tracker.unit_tracker.present
        placement = PlacementGrids._build_at_start(
            game_map.placement,
            present_units=present_units,
            state=state,
            step=observation.observation.game_loop,
            game_data=data,
            order_book=order_book,
        )
        free = placement.free
        tiles = sorted(game_map.playable_area.tiles())
        check = GridCheck(map_name, asked=len(tiles))
        for start in range(0, len(tiles), _TILES_ASKED_AT_ONCE):
            for tile, taken in _taken(game, tiles[start : start + _TILES_ASKED_AT_ONCE]).items():
                if taken != free[tile]:
                    check.disagreed.append((tile.x, tile.y, taken))
    return check


def check_grids(installation: Installation) -> list[GridCheck]:
    """Compare the game with `api.placement.free` on every map in the corpus."""
    all_checks = []
    for map_name in sorted({game.map for game in GAMES}):
        check = _check_grids_on(installation, map_name)
        all_checks.append(check)
        logger.info("{}: {} tiles asked, {} disagreed {}", map_name, check.asked, len(check.disagreed), check.disagreed)
    return all_checks


def main(argv: Sequence[str]) -> None:
    """Run the sweep named, and write its findings."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sweep", choices=("footprints", "creep", "ground-under-rocks", "grids"))
    parser.add_argument("--out", type=Path, help=f"where to write the findings; the footprints go to {FOOTPRINTS}")
    arguments = parser.parse_args(argv)
    installation = Installation.find()
    if arguments.sweep == "footprints":
        findings: object = asdict(measure_footprints(installation))
        out = arguments.out or FOOTPRINTS
    elif arguments.sweep == "creep":
        footprints = json.loads(FOOTPRINTS.read_text(encoding="utf-8"))
        footprints["creep"] = measure_creep(installation, footprints["unit_types"])
        findings, out = footprints, arguments.out or FOOTPRINTS
    elif arguments.sweep == "ground-under-rocks":
        findings = [asdict(ground) for ground in check_ground_under_rocks(installation)]
        out = arguments.out or Path("ground-under-rocks.json")
    else:
        findings = [asdict(check) for check in check_grids(installation)]
        out = arguments.out or Path("placement-grids.json")
    out.write_text(json.dumps(findings, indent=1) + "\n", encoding="utf-8")
    logger.info("Wrote the findings to {}", out)


if __name__ == "__main__":
    main(sys.argv[1:])
