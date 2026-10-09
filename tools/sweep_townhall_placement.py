"""Find where the game lets a townhall stand around mineral fields and geysers, and check NachOS's expansions by it.

Needs StarCraft II installed. `footprint` plays one game on the sandbox's map under `show_map`, with this player's
workers and the map's resources gone, on open ground where the game would take a townhall at every center around.
It creates each kind of mineral field and geyser there in turn, and asks the game at every tile center around it
whether a command center, a nexus and a hatchery could be placed there. It prints, for each kind, a picture of the
centers refused, and whether they are exactly those `refused_by_mineral_field` or `refused_by_geyser` names.

`bases` plays one game on each map of the corpus under `show_map`, and asks the game about a command center at every
tile center around each expansion NachOS finds there, but the two starts, where the townhalls stand. It prints every
center where the game and NachOS disagree::

    uv run python tools/sweep_townhall_placement.py
    uv run python tools/sweep_townhall_placement.py bases --out townhall-placement.json

The findings are written as JSON: for each kind of resource, its footprint and position and the offsets from it of
every center refused, by townhall; for each map, its expansions and the centers disagreed on.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy
from _sandbox import NEUTRAL, Cheat, Sandbox, playing
from loguru import logger
from numpy.lib.stride_tricks import sliding_window_view
from record_corpus import GAMES
from s2clientprotocol import raw_pb2

from sc2nachos.enemy import Enemy
from sc2nachos.gamedata import GameData
from sc2nachos.gamemap import GameMap
from sc2nachos.gamemap._expansion import _where_townhall_allowed
from sc2nachos.geometry import Point
from sc2nachos.ids import AbilityId, UncuratedIdError, UnitTypeId
from sc2nachos.ids.raw import RawUnitTypeId
from sc2nachos.launch import Installation
from sc2nachos.match import Race
from sc2nachos.units._tracking import _Tracker
from sc2nachos.units._unit_type import UnitType

# In tiles along each axis. A townhall at the farthest center asked about reaches two and a half tiles further, and
# only main bases have open ground 19 across.
_ASKED_REACH = 7
_OPEN_GROUND_HALF_WIDTH = _ASKED_REACH + 2
_OPEN_GROUND_TRIES = 64
_REMOVAL_WAIT_STEPS = 64
_TOWNHALLS = {
    "command center": AbilityId.SCV_BUILD_COMMAND_CENTER,
    "nexus": AbilityId.PROBE_BUILD_NEXUS,
    "hatchery": AbilityId.DRONE_MORPH_HATCHERY,
}
_TOWNHALL_SIZE = 5
_MINERAL_FIELD_SIZE = (2, 1)
_GEYSER_SIZE = (3, 3)

type Offset = tuple[float, float]


@dataclass(slots=True)
class ResourceRefusals:
    """What the game refused around one kind of resource."""

    name: str
    size: tuple[int, int]
    """The resource's footprint, across and up."""
    position: tuple[float, float]
    refused: dict[str, list[Offset]] = field(default_factory=dict)
    """The offsets from the resource's position of the centers refused, by townhall."""
    as_stated: bool = False
    """Whether every townhall was refused exactly where the rule for its kind of resource says."""


def _footprint_gaps(offset: Offset, size: tuple[int, int]) -> tuple[float, float]:
    """The tiles a townhall centered `offset` from a resource of footprint `size` leaves between their footprints,
    across and up: 0 along an axis their footprints overlap on."""
    townhall_half_side = _TOWNHALL_SIZE / 2
    across = max(abs(offset[0]) - townhall_half_side - size[0] / 2, 0.0)
    up = max(abs(offset[1]) - townhall_half_side - size[1] / 2, 0.0)
    return across, up


def refused_by_mineral_field(offset: Offset) -> bool:
    """Whether a mineral field refuses a townhall centered `offset` from it: both gaps under 3, but not both 2."""
    across, up = _footprint_gaps(offset, _MINERAL_FIELD_SIZE)
    return across < 3 and up < 3 and not (across == 2 and up == 2)


def refused_by_geyser(offset: Offset) -> bool:
    """Whether a geyser refuses a townhall centered `offset` from it: the two gaps adding up to under 3."""
    across, up = _footprint_gaps(offset, _GEYSER_SIZE)
    return across + up < 3


def _tile_centers_around(around: Point) -> list[Point]:
    """Every tile center within `_ASKED_REACH` tiles of `around`, along each axis."""
    x0, y0 = int(around.x), int(around.y)
    return [
        Point((x + 0.5, y + 0.5))
        for x in range(x0 - _ASKED_REACH, x0 + _ASKED_REACH + 1)
        for y in range(y0 - _ASKED_REACH, y0 + _ASKED_REACH + 1)
    ]


def _refusal_picture(refusals: ResourceRefusals) -> str:
    """The centers around the resource `refusals` describes, the top row first: `#` refused, `.` allowed."""
    refused = set(refusals.refused["command center"])
    # Centers sit on half tiles, so their offsets from a resource on a tile edge are a half off whole numbers.
    shift_x = 0.5 - refusals.position[0] % 1
    shift_y = 0.5 - refusals.position[1] % 1
    rows = []
    for dy in range(_ASKED_REACH, -_ASKED_REACH - 1, -1):
        rows.append(
            "".join(
                "#" if (dx + shift_x, dy + shift_y) in refused else "." for dx in range(-_ASKED_REACH, _ASKED_REACH + 1)
            )
        )
    return "\n".join(rows)


def _refused_centers(game: Sandbox, centers: Sequence[Point], ability: AbilityId) -> set[Point]:
    """Those of `centers` the game would refuse the townhall `ability` builds."""
    answers = game.placeable(ability, centers)
    return {center for center, placeable in zip(centers, answers, strict=True) if not placeable}


def _clear_open_ground(game: Sandbox, game_map: GameMap, units: Sequence[raw_pb2.Unit]) -> Point:
    """The center of open ground where the game would take every townhall at every center asked about: of the
    `_OPEN_GROUND_TRIES` open squares farthest from every unit, the farthest that qualifies."""
    size = 2 * _OPEN_GROUND_HALF_WIDTH + 1
    open_squares = sliding_window_view(numpy.asarray(game_map.placement.values), (size, size)).all(axis=(2, 3))
    xs, ys = numpy.nonzero(open_squares)
    origin = game_map.placement.origin
    centers = numpy.stack(
        (xs + origin.x + _OPEN_GROUND_HALF_WIDTH + 0.5, ys + origin.y + _OPEN_GROUND_HALF_WIDTH + 0.5), axis=-1
    )
    positions = numpy.array([(unit.pos.x, unit.pos.y) for unit in units])
    distance_to_nearest_unit = numpy.linalg.norm(centers[:, numpy.newaxis] - positions[numpy.newaxis], axis=-1).min(
        axis=1
    )
    for index in numpy.argsort(-distance_to_nearest_unit)[:_OPEN_GROUND_TRIES]:
        x, y = centers[index]
        at = Point((float(x), float(y)))
        if not any(_refused_centers(game, _tile_centers_around(at), ability) for ability in _TOWNHALLS.values()):
            return at
    raise RuntimeError(f"no open ground {size} tiles across takes every townhall")


def _remove_and_wait(game: Sandbox, tags: Sequence[int]) -> None:
    """Remove the units under `tags` and wait until they are gone."""
    game.kill(tags)
    for _ in range(_REMOVAL_WAIT_STEPS):
        game.client.step(1)
        if not {unit.tag for unit in game.units()} & set(tags):
            return
    raise RuntimeError(f"{len(tags)} units were not removed")


def _measure_refusals(
    game: Sandbox, at: Point, unit_type: UnitTypeId, size: tuple[int, int]
) -> ResourceRefusals | None:
    """Create one `unit_type` at `at`, ask about every center around it, and remove it."""
    spawned = game.spawn([(unit_type, NEUTRAL, at)])
    if not spawned:
        return None
    unit = spawned[0]
    position = (unit.pos.x, unit.pos.y)
    centers = _tile_centers_around(at)
    offsets = {center: (center.x - position[0], center.y - position[1]) for center in centers}
    refusals = ResourceRefusals(unit_type.name, size, position)
    refusal_rule = refused_by_geyser if size == _GEYSER_SIZE else refused_by_mineral_field
    stated_refusals = sorted(offset for offset in offsets.values() if refusal_rule(offset))
    for townhall, ability in _TOWNHALLS.items():
        refusals.refused[townhall] = sorted(offsets[center] for center in _refused_centers(game, centers, ability))
    refusals.as_stated = all(refused == stated_refusals for refused in refusals.refused.values())
    _remove_and_wait(game, [unit.tag])
    return refusals


def measure_resource_refusals(installation: Installation) -> list[ResourceRefusals]:
    """Measure what every kind of mineral field and geyser refuses a townhall."""
    resource_kinds = [
        (UnitTypeId(type_id), _MINERAL_FIELD_SIZE) for type_id in sorted(UnitType.AnyMineralField._type_ids)
    ]
    resource_kinds += [(UnitTypeId(type_id), _GEYSER_SIZE) for type_id in sorted(UnitType.AnyVespeneGeyser._type_ids)]
    all_refusals = []
    with playing(Race.TERRAN, installation) as game:
        game.cheat(Cheat.SHOW_MAP)
        # The map's resources come into sight under new tags.
        game.client.step(4)
        # Workers wander onto the ground asked about, and the only open ground wide enough is beside resources.
        resources = UnitType.AnyMineralField._type_ids | UnitType.AnyVespeneGeyser._type_ids
        _remove_and_wait(game, [unit.tag for unit in game.units() if unit.unit_type in resources | {UnitTypeId.SCV}])
        game_map = GameMap._of_game(game.client.game_info(), game.client.observation())
        at = _clear_open_ground(game, game_map, game.units())
        for unit_type, size in resource_kinds:
            refusals = _measure_refusals(game, at, unit_type, size)
            if refusals is None:
                continue
            all_refusals.append(refusals)
            verdict = "as stated" if refusals.as_stated else "NOT as stated"
            logger.info("{} at {}: {}\n{}", refusals.name, refusals.position, verdict, _refusal_picture(refusals))
    stated_count = sum(refusals.as_stated for refusals in all_refusals)
    logger.info("{} of {} kinds refuse a townhall as stated", stated_count, len(all_refusals))
    return all_refusals


@dataclass(slots=True)
class ExpansionCheck:
    """Where the game and NachOS disagree on a townhall around the expansions NachOS finds on one map."""

    map: str
    expansions: list[tuple[float, float]]
    """Each expansion's location, as NachOS orders them."""
    asked: int = 0
    """How many centers the game was asked about."""
    disagreed: list[tuple[float, float, bool]] = field(default_factory=list)
    """Each center the two disagree on, with whether the game takes a command center there."""
    uncurated: list[str] = field(default_factory=list)
    """The raw names of the unit types `show_map` revealed that `UnitTypeId` leaves out, set aside unread."""


def _without_uncurated_units(raw: raw_pb2.ObservationRaw) -> tuple[raw_pb2.ObservationRaw, list[str]]:
    """`raw` without the units whose type `UnitTypeId` leaves out, and those types' raw names."""
    kept = raw_pb2.ObservationRaw()
    kept.CopyFrom(raw)
    del kept.units[:]
    left_out = set()
    for unit in raw.units:
        try:
            UnitTypeId.read(unit.unit_type)
        except UncuratedIdError:
            left_out.add(RawUnitTypeId(unit.unit_type).name)
        else:
            kept.units.append(unit)
    return kept, sorted(left_out)


def _check_expansions_on(installation: Installation, map_name: str) -> ExpansionCheck:
    """Ask the game about every center around each expansion NachOS finds on `map_name`, but the two starts, where
    the townhalls stand."""
    with playing(Race.TERRAN, installation, map_name=map_name) as game:
        game.cheat(Cheat.SHOW_MAP)
        game.client.step(4)
        observation = game.client.observation()
        game_map = GameMap._of_game(game.client.game_info(), observation)
        tracker = _Tracker(GameData(game.client.game_data()), Enemy())
        raw, uncurated = _without_uncurated_units(observation.observation.raw_data)
        tracker.update(raw, observation.observation.game_loop)
        neutral_units = tracker.unit_tracker.present.neutral
        expansions = game_map._expansions_among(neutral_units)
        fields, geysers = (
            neutral_units.of_type(UnitType.AnyMineralField),
            neutral_units.of_type(UnitType.AnyVespeneGeyser),
        )
        blockers = neutral_units.excluding_type(UnitType.AnyMineralField, UnitType.AnyVespeneGeyser)
        townhall_allowed = _where_townhall_allowed(game_map.placement, fields, geysers, blockers)
        starts = {game_map.start_location, *game_map.opponent_start_locations}
        centers = sorted(
            {
                center
                for expansion in expansions
                if expansion.location not in starts
                for center in _tile_centers_around(expansion.location)
            }
        )
        locations = [(expansion.location.x, expansion.location.y) for expansion in expansions]
        check = ExpansionCheck(map_name, locations, asked=len(centers), uncurated=uncurated)
        refused = _refused_centers(game, centers, _TOWNHALLS["command center"])
        for center in centers:
            taken = center not in refused
            if taken != bool(townhall_allowed[game_map.placement.index_of(center)]):
                check.disagreed.append((center.x, center.y, taken))
    return check


def check_expansions(installation: Installation) -> list[ExpansionCheck]:
    """Compare the game with NachOS around the expansions of every map in the corpus."""
    all_checks = []
    for map_name in sorted({game.map for game in GAMES}):
        check = _check_expansions_on(installation, map_name)
        all_checks.append(check)
        logger.info(
            "{}: {} expansions, {} centers asked, {} disagreed {}; uncurated units set aside: {}",
            map_name,
            len(check.expansions),
            check.asked,
            len(check.disagreed),
            check.disagreed,
            check.uncurated or "none",
        )
    return all_checks


_SWEEPS = {"footprint": measure_resource_refusals, "bases": check_expansions}


def main(argv: Sequence[str]) -> None:
    """Run the sweeps named, or both, and write the findings."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sweeps", nargs="*", help=f"{' or '.join(_SWEEPS)}; both when none are named")
    parser.add_argument("--out", type=Path, default=Path("townhall-placement.json"), help="where to write findings")
    arguments = parser.parse_args(argv)
    if unknown := set(arguments.sweeps) - set(_SWEEPS):
        raise SystemExit(f"No sweep is called {', '.join(sorted(unknown))}")
    installation = Installation.find()
    findings = {
        name: [asdict(finding) for finding in sweep(installation)]
        for name, sweep in _SWEEPS.items()
        if name in arguments.sweeps or not arguments.sweeps
    }
    arguments.out.write_text(json.dumps(findings, indent=2), encoding="utf-8")
    logger.info("Wrote the findings to {}", arguments.out)


if __name__ == "__main__":
    main(sys.argv[1:])
