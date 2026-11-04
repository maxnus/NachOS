"""Measure how fast each unit type a speed upgrade changes moves, without the upgrade and with it.

Needs StarCraft II installed. Plays a game as each race named, or as every race, against the easiest computer, which is
cleared down to one structure, under `show_map`, `free` and `fast_build`. The types measured are those the tables give
exactly one upgrade that changes their speed, but those whose upgrade upgrade inference reads off a buff: Charge's
zealot. A burrowed roach, which the rows give two, is left out.

Each trial makes one unit at one end of a straight course on open ground and orders it to move to the other end,
along each of `_HEADINGS_DEGREES`, recording where it stood at every step. A Zerg ground type runs again on creep,
between four hatcheries made beside a course of its own, once their creep has stopped spreading, every point of it
`_CREEP_MARGIN` or more from ground without creep. As Protoss, a sentry's hallucinations of the types it can
hallucinate run too. Then the sweep researches each of the race's speed upgrades, at researchers it makes with whatever
they need, and runs every trial again::

    uv run python tools/sweep_movement.py
    uv run python tools/sweep_movement.py zerg --out movement-zerg.json

Each trial records the row's speed, the steps until the unit moved and until it cruised, at 98% of its fastest step or
more, its fastest, median and slowest step while cruising, and the most and least a window of each of
`_WINDOW_STEPS` steps took it while cruising, all in distance per second, as `Unit.velocity` reads; how far it moved in
each step; and for a unit on the ground, how many steps it ended on creep and how near it came to ground of the other
kind. It writes `movement.json`, or the file `--out` names. A trial the game would not set up is recorded as its
error.
"""

import argparse
import json
import math
import statistics
import sys
from collections.abc import Callable, Iterator, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Self

from _sandbox import SUCCESS, Cheat, OpenGround, Sandbox, playing
from loguru import logger
from s2clientprotocol import debug_pb2, raw_pb2

from sc2nachos.gamedata import Attribute, GameData, Tech
from sc2nachos.gamemap import GameMap
from sc2nachos.gamemap._image_data import image_tiles
from sc2nachos.gametime import STEPS_PER_SECOND
from sc2nachos.geometry import Grid, Point, Rectangle
from sc2nachos.ids import UnitTypeId, UpgradeId
from sc2nachos.ids.raw import RawAbilityId
from sc2nachos.match import Race
from sc2nachos.run import Installation

DEFAULT_FINDINGS_PATH = Path("movement.json")
RACES_BY_NAME = {"terran": Race.TERRAN, "zerg": Race.ZERG, "protoss": Race.PROTOSS}

# The upgrades upgrade inference reads off a buff, whose types the sweep leaves out.
_READ_OFF_BUFFS = frozenset({UpgradeId.CHARGE})
# The headings each course is run along, in degrees from the positive x-axis.
_HEADINGS_DEGREES = (0.0, 45.0, 90.0)
# The creep course is run along its hatcheries, both ways.
_CREEP_HEADINGS_DEGREES = (0.0, 180.0)
# A flying unit's course runs this far either side of the map's center.
_FLYING_COURSE_HALF_LENGTH = 20.0
# A ground course is the longest straight line the pathing grid leaves clear this far to either side, through a tile
# center looked at every few tiles, at least this far from every start location and from the creep course, and at most
# twice the longest half below. A course ends this far inside where its ground stops being clear, which is looked at
# this finely along it.
_GROUND_CLEARANCE = 1.5
_GROUND_COURSE_SEARCH_SPACING = 2
_GROUND_COURSE_DISTANCE_FROM_OTHERS = 30.0
_LONGEST_COURSE_HALF_LENGTH = 16.0
_COURSE_END_INSET = 1.5
_CLEAR_CHECK_SPACING = 0.5
# The creep course lies in the free square this wide nearest the point this share of the way from this player's start
# to the map's center, between four hatcheries made in it on tile centers, as a 5x5 structure stands. It runs along
# the x-axis through the square's center once the creep within the radius below has stopped spreading, the same tiles
# for that many looks in a row, as far as creep covers clear ground this far to either side, and ends that far inside
# where it stops. It is laid only if each half is at least as long as the least below.
_CREEP_SQUARE_HALF_SIZE = 7
_CREEP_COURSE_ALONG = 0.35
_CREEP_HATCHERY_OFFSETS = ((-4.0, 5.0), (4.0, 5.0), (-4.0, -5.0), (4.0, -5.0))
_CREEP_SETTLING_RADIUS = 20.0
_CREEP_SETTLED_CHECKS = 20
_CREEP_MARGIN = 3.0
_CREEP_COURSE_LEAST_HALF_LENGTH = 5.0
# How often, and for how long, the sweep looks at the hatcheries' creep.
_CREEP_CHECK_STEPS = 22
_CREEP_WAIT_STEPS = 13440
# How far around each point a unit on the ground stood the sweep looks for ground of the other kind than its course's.
_OTHER_GROUND_SEARCH_RADIUS = 8
# The steps a unit made by debug command is waited for, and those it is given to settle once made.
_MAKING_STEPS = 16
_SETTLING_STEPS = 8
# A trial watches a unit for as long as the course takes it at this share of the slower of its two row speeds, and
# stops once it has stood still this many steps after moving, or come this close to the course's end.
_WATCH_SPEED_SHARE = 0.5
_STILL_STEPS_TO_STOP = 8
_ARRIVED_DISTANCE = 0.3
# A unit this far from where it stood has moved.
_SMALLEST_MOVE = 0.01
# A step at this share of the fastest one or more counts as cruising.
_CRUISING_SHARE = 0.98
# The windows, in steps, whose distance the cruise is read over: about half a second, a second and two.
_WINDOW_STEPS = (11, 22, 44)
# How many places the step distances are written to.
_DISTANCE_PLACES = 5
# The ground a researcher is put up on, by half the width of the square claimed, which fits a 5x5 structure. A Protoss
# researcher stands beside its own pylon, and a host beside its add-on, within a square that fits both.
_RESEARCHER_HALF_SIZE = 2
_POWERED_HALF_SIZE = 3
_POWERED_OFFSET = (1.0, 0.0)
_PYLON_OFFSET = (-2.5, -0.5)
_HOST_OFFSET = (-1.0, 0.0)
# The structure an add-on researcher is built from, and the claims near this player's start tried for a Zerg one on
# creep.
_ADD_ON_HOSTS = {UnitTypeId.TECH_LAB_STARPORT: UnitTypeId.STARPORT}
_CREEP_CLAIM_TRIES = 16
# How long a research is waited for under `fast_build`.
_RESEARCH_CHECK_STEPS = 22
_RESEARCH_WAIT_STEPS = 2240
# The types a sentry can hallucinate, by the ability that does it, and the energy it is given for one.
_HALLUCINATIONS = {
    UnitTypeId.VOID_RAY: RawAbilityId.Hallucination_VoidRay,
    UnitTypeId.WARP_PRISM: RawAbilityId.Hallucination_WarpPrism,
}
_ENERGY_VALUE = debug_pb2.DebugSetUnitValue.Energy
_FULL_ENERGY = 200.0


@dataclass(slots=True)
class MoveFinding:
    """One unit ordered along a course: how fast it moved, against its row."""

    unit_type: str
    upgrade: str
    upgraded: bool
    on_creep: bool
    hallucination: bool
    heading_degrees: float
    flying: bool
    """Whether the unit made flew, as the game reported it."""
    row_speed: float
    """The speed the rows give the type, with the upgrade when `upgraded`, in distance per second."""
    steps_until_moving: int | None = None
    """The first step after the order in which it stood away from where it was, or None if it never did."""
    steps_until_cruising: int | None = None
    """The first step after the order in which it moved at `_CRUISING_SHARE` of its fastest step or more."""
    cruising_steps: int = 0
    """The steps from the first to the last it cruised in, both counted."""
    fastest_step_speed: float | None = None
    median_cruising_speed: float | None = None
    slowest_cruising_speed: float | None = None
    """The slowest step from the first to the last it cruised in."""
    steps_on_creep: int | None = None
    """For a unit on the ground, how many of the steps after the order it ended on a tile of creep."""
    least_distance_to_other_ground: float | None = None
    """For a unit on the ground, the least distance from where it stood after any step to a tile of the other kind than
    its course's: without creep on the creep course and with it off; None if there was none within
    `_OTHER_GROUND_SEARCH_RADIUS`."""
    window_speeds: dict[str, tuple[float, float]] = field(default_factory=dict)
    """The least and the most distance a window of each of `_WINDOW_STEPS` steps took it while cruising, from where it
    stood at the window's start to where it stood at its end, by the window's steps."""
    step_distances: list[float] = field(default_factory=list)
    """How far it moved in each step after the order."""


def speed_upgrades_by_type(data: GameData, race: Race) -> dict[UnitTypeId, UpgradeId]:
    """Each type of `race` that exactly one upgrade speeds up in the rows, with that upgrade, but those whose upgrade
    upgrade inference reads off a buff, and a type the rows give no speed without one."""
    speed_upgrades: dict[UnitTypeId, UpgradeId] = {}
    for unit_type, row in data.units.items():
        if row.race is not race or row.speed == 0:
            continue
        speeding = [upgrade for upgrade in row.upgrades if row.with_upgrades({upgrade}).speed != row.speed]
        if len(speeding) == 1 and speeding[0] not in _READ_OFF_BUFFS:
            speed_upgrades[unit_type] = speeding[0]
    return dict(sorted(speed_upgrades.items(), key=lambda item: item[0].name))


def _point_toward(start: Point, heading: float, distance: float) -> Point:
    """The point `distance` from `start` along `heading`, in radians from the positive x-axis."""
    return Point((start.x + distance * math.cos(heading), start.y + distance * math.sin(heading)))


def _position_of(unit: raw_pb2.Unit) -> Point:
    """Where `unit` stands, on the ground plane."""
    return Point((unit.pos.x, unit.pos.y))


def _per_second(distance_per_step: float) -> float:
    """A distance per step as a distance per second."""
    return round(distance_per_step * STEPS_PER_SECOND, 4)


@dataclass(frozen=True, slots=True)
class Course:
    """A straight run on open ground, from `start` to `end`."""

    start: Point
    end: Point

    @classmethod
    def through(cls, center: Point, heading_degrees: float, half_length: float) -> Self:
        """The course of `2 * half_length` through `center` along `heading_degrees`."""
        heading = math.radians(heading_degrees)
        return cls(_point_toward(center, heading + math.pi, half_length), _point_toward(center, heading, half_length))

    @property
    def length(self) -> float:
        """How far the end is from the start."""
        return self.start.distance_to(self.end)

    def points_along(self, spacing: float) -> Iterator[Point]:
        """Points from the start to the end, `spacing` apart, both ends included."""
        count = max(int(self.length / spacing), 1)
        for index in range(count + 1):
            share = index / count
            yield Point(
                (self.start.x + share * (self.end.x - self.start.x), self.start.y + share * (self.end.y - self.start.y))
            )


class MovementSweep:
    """The game a sweep plays as `race`, with its courses and the tables it reads."""

    def __init__(self, game: Sandbox, race: Race) -> None:
        """Turn the cheats on, clear the computer down to one structure, lay the courses and, as Zerg, make the creep
        course's hatcheries."""
        self._game = game
        self._client = game.client
        self._race = race
        game.cheat(Cheat.SHOW_MAP, Cheat.FREE, Cheat.FAST_BUILD)
        self._client.step(_SETTLING_STEPS)
        game.leave_the_computer_one_structure()
        self.base_build = self._client.ping().base_build
        self.data = GameData(self._client.game_data())
        self.speed_upgrades = speed_upgrades_by_type(self.data, race)
        self._map = GameMap._from_game(self._client.game_info(), self._client.observation())
        self._ground = OpenGround(self._map, game.units())
        self._home = self._map.start_location
        self._creep_center: Point | None = None
        if race is Race.ZERG:
            toward_center = self._map.playable_area.center - self._home
            self._creep_center = self._ground.claim(
                self._home + toward_center * _CREEP_COURSE_ALONG, _CREEP_SQUARE_HALF_SIZE
            )
            for offset_x, offset_y in _CREEP_HATCHERY_OFFSETS:
                self._make(
                    UnitTypeId.HATCHERY, Point((self._creep_center.x + offset_x, self._creep_center.y + offset_y))
                )
        away_from = [*self._map.all_start_locations, *([self._creep_center] if self._creep_center else [])]
        self._ground_courses = {
            heading: self._longest_ground_course(heading, away_from) for heading in _HEADINGS_DEGREES
        }
        for heading, course in self._ground_courses.items():
            logger.info("The ground course at {} degrees runs {:.1f} from {}", heading, course.length, course.start)
        self._creep_half_length: float | None = None
        self._flying_by_type: dict[UnitTypeId, bool] = {}

    def run_trials(self, *, upgraded: bool) -> list[dict[str, object]]:
        """Every trial of the race's types, and as Protoss of its hallucinations, as findings or errors."""
        trials: list[dict[str, object]] = []
        for unit_type, upgrade in self.speed_upgrades.items():
            for heading in _HEADINGS_DEGREES:
                trials.append(self._recorded(unit_type, upgrade, heading, upgraded=upgraded))
            if self._creep_center is not None and not self._flies(unit_type):
                for heading in _CREEP_HEADINGS_DEGREES:
                    trials.append(self._recorded(unit_type, upgrade, heading, upgraded=upgraded, on_creep=True))
            if unit_type in _HALLUCINATIONS:
                for heading in _HEADINGS_DEGREES:
                    trials.append(self._recorded(unit_type, upgrade, heading, upgraded=upgraded, hallucination=True))
        return trials

    def research_speed_upgrades(self) -> dict[str, str]:
        """Research each of the race's speed upgrades, and return why each that was not researched was not."""
        not_researched: dict[str, str] = {}
        for upgrade in sorted(set(self.speed_upgrades.values()), key=lambda upgrade: upgrade.name):
            try:
                self._research(upgrade)
            except RuntimeError as error:
                logger.warning("{} was not researched: {}", upgrade.name, error)
                not_researched[upgrade.name] = str(error)
            else:
                logger.info("Researched {}", upgrade.name)
        return not_researched

    def _recorded(
        self,
        unit_type: UnitTypeId,
        upgrade: UpgradeId,
        heading_degrees: float,
        *,
        upgraded: bool,
        on_creep: bool = False,
        hallucination: bool = False,
    ) -> dict[str, object]:
        """One trial as its finding, or as its error if the game would not set it up."""
        conditions = {
            "unit_type": unit_type.name,
            "upgraded": upgraded,
            "on_creep": on_creep,
            "hallucination": hallucination,
            "heading_degrees": heading_degrees,
        }
        try:
            finding = self._trial(unit_type, upgrade, heading_degrees, upgraded, on_creep, hallucination)
        except RuntimeError as error:
            logger.warning("{}: {}", conditions, error)
            return {**conditions, "error": str(error)}
        logger.info(
            "{} {}{}{} at {} degrees: cruised at {} a second against the row's {}, windows {}, {} of {} steps on creep,"
            " {} from ground of the other kind",
            unit_type.name,
            "upgraded" if upgraded else "unupgraded",
            " on creep" if on_creep else "",
            " hallucinated" if hallucination else "",
            heading_degrees,
            finding.median_cruising_speed,
            finding.row_speed,
            finding.window_speeds.get(str(_WINDOW_STEPS[1])),
            finding.steps_on_creep,
            len(finding.step_distances),
            finding.least_distance_to_other_ground,
        )
        return asdict(finding)

    def _trial(
        self,
        unit_type: UnitTypeId,
        upgrade: UpgradeId,
        heading_degrees: float,
        upgraded: bool,
        on_creep: bool,
        hallucination: bool,
    ) -> MoveFinding:
        """Make a `unit_type` at the start of its course along `heading_degrees`, order it to the end, and record how
        far it moved in each step."""
        row = self.data.units[unit_type]
        row_speed = row.with_upgrades({upgrade}).speed if upgraded else row.speed
        if on_creep:
            course = self._creep_course(heading_degrees)
            self._game.remove(unit.tag for unit in self._game.units() if unit.unit_type == UnitTypeId.LARVA)
        elif self._flies(unit_type):
            course = Course.through(self._map.playable_area.center, heading_degrees, _FLYING_COURSE_HALF_LENGTH)
        else:
            course = self._ground_courses[heading_degrees]
        made_tags: list[int] = []
        try:
            if hallucination:
                mover = self._hallucinated(unit_type, course.start, made_tags)
            else:
                mover = self._make(unit_type, course.start)
                made_tags.append(mover.tag)
            if not mover.is_flying and not on_creep and self._has_creep_on(course):
                raise RuntimeError("the course off creep has creep on it")
            self._client.step(_SETTLING_STEPS)
            positions = self._watched_moving(mover.tag, course, min(row.speed, row_speed))
        finally:
            self._game.remove(made_tags)
        finding = MoveFinding(
            unit_type.name,
            upgrade.name,
            upgraded,
            on_creep,
            hallucination,
            heading_degrees,
            mover.is_flying,
            round(row_speed, 4),
        )
        _measure_into(finding, positions)
        if not mover.is_flying:
            creep = self._creep()
            finding.steps_on_creep = sum(1 for position in positions[1:] if creep[position])
            finding.least_distance_to_other_ground = _least_distance_to_tile_of(
                creep, positions[1:], has_creep=not on_creep
            )
        return finding

    def _watched_moving(self, tag: int, course: Course, slower_row_speed: float) -> list[Point]:
        """Order the unit `tag` to the end of `course` and return where it stood as ordered and after each step,
        until it arrived, stood still after moving, or had taken as long as the course takes it at `_WATCH_SPEED_SHARE`
        of `slower_row_speed`."""
        positions = [_position_of(self._listed(tag))]
        self._game.order(RawAbilityId.Move_Move, tag, course.end)
        watch_steps = math.ceil(course.length / (slower_row_speed * _WATCH_SPEED_SHARE / STEPS_PER_SECOND))
        still_steps = 0
        for _ in range(watch_steps):
            self._client.step(1)
            position = _position_of(self._listed(tag))
            has_moved_before = positions[-1].distance_to(positions[0]) > _SMALLEST_MOVE
            still_steps = still_steps + 1 if position.distance_to(positions[-1]) < _SMALLEST_MOVE else 0
            positions.append(position)
            if position.distance_to(course.end) < _ARRIVED_DISTANCE:
                break
            if has_moved_before and still_steps >= _STILL_STEPS_TO_STOP:
                break
        return positions

    def _flies(self, unit_type: UnitTypeId) -> bool:
        """Whether a `unit_type` flies, as the game reports one made at the start of the first ground course."""
        if (flies := self._flying_by_type.get(unit_type)) is None:
            probe = self._make(unit_type, self._ground_courses[_HEADINGS_DEGREES[0]].start)
            self._game.remove([probe.tag])
            flies = self._flying_by_type[unit_type] = probe.is_flying
        return flies

    def _longest_ground_course(self, heading_degrees: float, away_from: Sequence[Point]) -> Course:
        """The longest ground course along `heading_degrees` through a tile center at least
        `_GROUND_COURSE_DISTANCE_FROM_OTHERS` from each of `away_from`. Raises `RuntimeError` if there is none."""
        pathing = self._map.pathing
        area = self._map.playable_area
        best_center, best_half_length = None, 0.0
        for x in range(int(area.x) + 1, int(area.x + area.width) - 1, _GROUND_COURSE_SEARCH_SPACING):
            for y in range(int(area.y) + 1, int(area.y + area.height) - 1, _GROUND_COURSE_SEARCH_SPACING):
                center = Point((x + 0.5, y + 0.5))
                if any(center.distance_to(other) < _GROUND_COURSE_DISTANCE_FROM_OTHERS for other in away_from):
                    continue
                half_length = _clear_half_length(
                    center, heading_degrees, lambda point: pathing[point], clearance=_GROUND_CLEARANCE
                )
                if half_length > best_half_length:
                    best_center, best_half_length = center, half_length
        if best_center is None or best_half_length <= _COURSE_END_INSET:
            raise RuntimeError(f"no clear ground at {heading_degrees} degrees for a course")
        return Course.through(best_center, heading_degrees, best_half_length - _COURSE_END_INSET)

    def _creep_course(self, heading_degrees: float) -> Course:
        """The creep course along `heading_degrees`, once the hatcheries' creep covers it. Raises `RuntimeError` if it
        never did."""
        if self._creep_center is None:
            raise RuntimeError("only a Zerg game lays a creep course")
        if self._creep_half_length is None:
            self._creep_half_length = self._awaited_creep_half_length(self._creep_center)
            logger.info("The creep course runs {:.1f} through {}", 2 * self._creep_half_length, self._creep_center)
        if self._creep_half_length <= 0:
            raise RuntimeError(f"the hatcheries' creep did not settle over a course within {_CREEP_WAIT_STEPS} steps")
        return Course.through(self._creep_center, heading_degrees, self._creep_half_length)

    def _awaited_creep_half_length(self, center: Point) -> float:
        """Step until the creep within `_CREEP_SETTLING_RADIUS` of `center` has stopped spreading, and return the creep
        course's half length along the x-axis through `center`, or 0 if creep did not stop in time or covers too
        little."""
        pathing = self._map.pathing
        settling_area = Rectangle.from_center(center, 2 * _CREEP_SETTLING_RADIUS, 2 * _CREEP_SETTLING_RADIUS)
        creep_tiles_before, looks_unchanged = -1, 0
        for _ in range(_CREEP_WAIT_STEPS // _CREEP_CHECK_STEPS):
            creep = self._creep()
            creep_tiles = int(creep[settling_area].sum())
            looks_unchanged = looks_unchanged + 1 if creep_tiles == creep_tiles_before else 0
            creep_tiles_before = creep_tiles
            if looks_unchanged >= _CREEP_SETTLED_CHECKS:
                covered_half_length = _clear_half_length(
                    center,
                    0.0,
                    lambda point, creep_now=creep: pathing[point] and creep_now[point],
                    clearance=_CREEP_MARGIN,
                )
                half_length = covered_half_length - _CREEP_MARGIN
                logger.info("The creep stopped spreading at {} tiles near the creep course", creep_tiles)
                return half_length if half_length >= _CREEP_COURSE_LEAST_HALF_LENGTH else 0.0
            self._client.step(_CREEP_CHECK_STEPS)
        return 0.0

    def _has_creep_on(self, course: Course) -> bool:
        """Whether there is creep now at any point along `course`."""
        creep = self._creep()
        return any(creep[point] for point in course.points_along(_CLEAR_CHECK_SPACING))

    def _creep(self) -> Grid[bool]:
        """Where there is creep now."""
        creep_image = self._client.observation().observation.raw_data.map_state.creep
        creep_tiles = image_tiles(creep_image, self._map.playable_area) != 0
        return Grid(creep_tiles, origin=self._map.pathing.origin, outside=False)

    def _research(self, upgrade: UpgradeId) -> None:
        """Research `upgrade` at a researcher of this player's, putting it up with every tech it needs first. Raises
        `RuntimeError` if the game refused it or it did not finish in time."""
        upgrade_row = self.data.upgrades[upgrade]
        if upgrade_row.research_ability is None:
            raise RuntimeError("nothing researches it")
        self._put_up_techs(upgrade_row.tech_requirements.techs)
        researchers = self._own_of_types(upgrade_row.researchers)
        if researchers:
            researcher = researchers[0]
        else:
            researcher = self._put_up(min(upgrade_row.researchers, key=lambda unit_type: unit_type.name))
        if (verdict := self._game.order(upgrade_row.research_ability, researcher.tag)) != SUCCESS:
            raise RuntimeError(f"the game refused the research at a {UnitTypeId(researcher.unit_type).name}: {verdict}")
        for _ in range(_RESEARCH_WAIT_STEPS // _RESEARCH_CHECK_STEPS):
            self._client.step(_RESEARCH_CHECK_STEPS)
            if upgrade in self._researched():
                return
        raise RuntimeError(f"the research did not finish within {_RESEARCH_WAIT_STEPS} steps")

    def _researched(self) -> set[UpgradeId]:
        """The curated upgrades this player has."""
        upgrade_ids = self._client.observation().observation.raw_data.player.upgrade_ids
        return {upgrade for upgrade_id in upgrade_ids if (upgrade := UpgradeId.get(upgrade_id)) is not None}

    def _put_up_techs(self, techs: frozenset[Tech]) -> None:
        """Put up a structure granting each of `techs` that this player has none of."""
        for tech in sorted(techs, key=lambda tech: tech.name):
            if not self._own_of_types(self.data.types_granting(tech)):
                self._put_up(tech.base_type)

    def _put_up(self, structure_type: UnitTypeId) -> raw_pb2.Unit:
        """Make a `structure_type` of this player's where it can stand, with the techs it needs first: on creep near
        this player's start for a Zerg one, beside a pylon of its own for a Protoss one, built from its host for an
        add-on, and morphed from the structure it is a morph of, as a lair from a hatchery."""
        structure_row = self.data.units[structure_type]
        self._put_up_techs(structure_row.tech_requirements.techs)
        if (host_type := _ADD_ON_HOSTS.get(structure_type)) is not None:
            return self._built_add_on(structure_type, host_type)
        morphed_from = structure_row.morphed_from
        if morphed_from is not None and Attribute.STRUCTURE in self.data.units[morphed_from].attributes:
            return self._morphed(structure_type, morphed_from)
        race = structure_row.race
        if race is Race.PROTOSS:
            at = self._ground.claim(self._home, _POWERED_HALF_SIZE)
            self._make(UnitTypeId.PYLON, Point((at.x + _PYLON_OFFSET[0], at.y + _PYLON_OFFSET[1])))
            return self._make(structure_type, Point((at.x + _POWERED_OFFSET[0], at.y + _POWERED_OFFSET[1])))
        if race is Race.ZERG and structure_type is not UnitTypeId.HATCHERY:
            return self._make(structure_type, self._claim_on_creep())
        return self._make(structure_type, self._ground.claim(self._home, _RESEARCHER_HALF_SIZE))

    def _morphed(self, morphed_type: UnitTypeId, from_type: UnitTypeId) -> raw_pb2.Unit:
        """Morph a `from_type` of this player's into a `morphed_type`, and return it once it stands."""
        creation_ability = self.data.units[morphed_type].creation_ability
        if creation_ability is None or not (from_units := self._own_of_types({from_type})):
            raise RuntimeError(f"this player has no {from_type.name} to morph into a {morphed_type.name}")
        if (verdict := self._game.order(creation_ability, from_units[0].tag)) != SUCCESS:
            raise RuntimeError(f"the game refused the {morphed_type.name}: {verdict}")
        return self._awaited_own(morphed_type)

    def _awaited_own(self, unit_type: UnitTypeId) -> raw_pb2.Unit:
        """Step until this player has a finished `unit_type`, and return it."""
        for _ in range(_RESEARCH_WAIT_STEPS // _RESEARCH_CHECK_STEPS):
            self._client.step(_RESEARCH_CHECK_STEPS)
            if finished := self._own_of_types({unit_type}):
                return finished[0]
        raise RuntimeError(f"no {unit_type.name} finished within {_RESEARCH_WAIT_STEPS} steps")

    def _built_add_on(self, add_on_type: UnitTypeId, host_type: UnitTypeId) -> raw_pb2.Unit:
        """Make a `host_type` and order it to build its `add_on_type`, and return the add-on once it stands."""
        creation_ability = self.data.units[add_on_type].creation_ability
        if creation_ability is None:
            raise RuntimeError(f"nothing builds a {add_on_type.name}")
        at = self._ground.claim(self._home, _POWERED_HALF_SIZE)
        host = self._make(host_type, Point((at.x + _HOST_OFFSET[0], at.y + _HOST_OFFSET[1])))
        if (verdict := self._game.order(creation_ability, host.tag)) != SUCCESS:
            raise RuntimeError(f"the game refused the {add_on_type.name}: {verdict}")
        return self._awaited_own(add_on_type)

    def _claim_on_creep(self) -> Point:
        """The center of free ground near this player's start that creep covers, claimed."""
        creep = self._creep()
        for _ in range(_CREEP_CLAIM_TRIES):
            at = self._ground.claim(self._home, _RESEARCHER_HALF_SIZE)
            corners = [
                Point((at.x + dx * _RESEARCHER_HALF_SIZE, at.y + dy * _RESEARCHER_HALF_SIZE))
                for dx in (-1, 1)
                for dy in (-1, 1)
            ]
            if all(creep[point] for point in [at, *corners]):
                return at
        raise RuntimeError(f"no free ground on creep near the start in {_CREEP_CLAIM_TRIES} tries")

    def _hallucinated(self, unit_type: UnitTypeId, at: Point, made_tags: list[int]) -> raw_pb2.Unit:
        """Make a sentry at `at` with full energy and have it hallucinate a `unit_type`, adding both tags to
        `made_tags`, and return the hallucination."""
        sentry = self._make(UnitTypeId.SENTRY, at)
        made_tags.append(sentry.tag)
        self._game.set_value(_ENERGY_VALUE, _FULL_ENERGY, [sentry.tag])
        self._client.step(1)
        tags_before = {unit.tag for unit in self._game.units()}
        self._game.order(_HALLUCINATIONS[unit_type], sentry.tag)
        for _ in range(_MAKING_STEPS):
            self._client.step(1)
            hallucinations = [
                unit
                for unit in self._game.units()
                if unit.tag not in tags_before and unit.unit_type == unit_type and unit.is_hallucination
            ]
            if hallucinations:
                made_tags.append(hallucinations[0].tag)
                return hallucinations[0]
        raise RuntimeError(f"the sentry did not hallucinate a {unit_type.name}")

    def _own_of_types(self, unit_types: frozenset[UnitTypeId] | set[UnitTypeId]) -> list[raw_pb2.Unit]:
        """This player's finished units of `unit_types`, by tag."""
        own = [
            unit
            for unit in self._game.units()
            if unit.owner == self._game.player and unit.unit_type in unit_types and unit.build_progress == 1
        ]
        return sorted(own, key=lambda unit: unit.tag)

    def _make(self, unit_type: UnitTypeId, at: Point) -> raw_pb2.Unit:
        """Make one `unit_type` of this player's at `at` and step until the game lists it."""
        tags_before = {unit.tag for unit in self._game.units()}
        self._game.debug(self._game.create(unit_type, self._game.player, at))
        for _ in range(_MAKING_STEPS):
            self._client.step(1)
            made = [
                unit
                for unit in self._game.units()
                if unit.tag not in tags_before and unit.unit_type == unit_type and unit.display_type == raw_pb2.Visible
            ]
            if made:
                return made[0]
        raise RuntimeError(f"the game did not make a {unit_type.name} at {at}")

    def _listed(self, tag: int) -> raw_pb2.Unit:
        """The unit `tag` as a fresh observation lists it."""
        if (unit := next((unit for unit in self._game.units() if unit.tag == tag), None)) is None:
            raise RuntimeError(f"unit {tag} is no longer listed")
        return unit


def _clear_half_length(
    center: Point, heading_degrees: float, is_clear: Callable[[Point], bool], *, clearance: float
) -> float:
    """How far a line through `center` along `heading_degrees` runs either way, up to `_LONGEST_COURSE_HALF_LENGTH`,
    with every point on it and `clearance` to either side clear, as `is_clear` reads; 0 if `center` is not."""
    heading = math.radians(heading_degrees)
    across = heading + math.pi / 2
    distance = 0.0
    while distance < _LONGEST_COURSE_HALF_LENGTH:
        for direction in (heading, heading + math.pi):
            on_line = _point_toward(center, direction, distance)
            for side in (-clearance, 0.0, clearance):
                if not is_clear(_point_toward(on_line, across, side)):
                    return distance
        distance += _CLEAR_CHECK_SPACING
    return distance


def _least_distance_to_tile_of(grid: Grid[bool], points: Sequence[Point], *, has_creep: bool) -> float | None:
    """The least distance from any of `points` to a tile `grid` reads `has_creep` for, looked for within
    `_OTHER_GROUND_SEARCH_RADIUS` tiles of each, or None if there is none so near. A tile beyond the grid reads no
    creep."""
    reach = _OTHER_GROUND_SEARCH_RADIUS
    least_distance: float | None = None
    for point in points:
        for tile_x in range(int(point.x) - reach, int(point.x) + reach + 1):
            for tile_y in range(int(point.y) - reach, int(point.y) + reach + 1):
                if grid[(tile_x + 0.5, tile_y + 0.5)] != has_creep:
                    continue
                # How far the point is from the tile's square, 0 inside it.
                off_x = max(tile_x - point.x, 0.0, point.x - (tile_x + 1))
                off_y = max(tile_y - point.y, 0.0, point.y - (tile_y + 1))
                distance = math.hypot(off_x, off_y)
                if least_distance is None or distance < least_distance:
                    least_distance = distance
    return None if least_distance is None else round(least_distance, 2)


def _measure_into(finding: MoveFinding, positions: Sequence[Point]) -> None:
    """Fill `finding` in from where the unit stood as ordered and after each step."""
    step_distances = [after.distance_to(before) for before, after in zip(positions, positions[1:], strict=False)]
    finding.step_distances = [round(distance, _DISTANCE_PLACES) for distance in step_distances]
    moving_indexes = [
        index for index, position in enumerate(positions) if position.distance_to(positions[0]) > _SMALLEST_MOVE
    ]
    if not moving_indexes:
        return
    finding.steps_until_moving = moving_indexes[0]
    fastest = max(step_distances)
    cruising_indexes = [index for index, distance in enumerate(step_distances) if distance >= fastest * _CRUISING_SHARE]
    first_cruising, last_cruising = cruising_indexes[0], cruising_indexes[-1]
    cruise = step_distances[first_cruising : last_cruising + 1]
    finding.steps_until_cruising = first_cruising + 1
    finding.cruising_steps = len(cruise)
    finding.fastest_step_speed = _per_second(fastest)
    finding.median_cruising_speed = _per_second(statistics.median(cruise))
    finding.slowest_cruising_speed = _per_second(min(cruise))
    # Position index i stands after step i, so the cruise runs from the position before its first step to the one
    # after its last.
    cruise_positions = positions[first_cruising : last_cruising + 2]
    for window_steps in _WINDOW_STEPS:
        window_distances = [
            cruise_positions[start + window_steps].distance_to(cruise_positions[start])
            for start in range(len(cruise_positions) - window_steps)
        ]
        if window_distances:
            finding.window_speeds[str(window_steps)] = (
                _per_second(min(window_distances) / window_steps),
                _per_second(max(window_distances) / window_steps),
            )


def sweep_race(race: Race, installation: Installation) -> dict[str, object]:
    """One game as `race`: every trial before the race's speed upgrades and after."""
    with playing(race, installation) as game:
        movement_sweep = MovementSweep(game, race)
        logger.info("{}: {}", race.name, {t.name: u.name for t, u in movement_sweep.speed_upgrades.items()})
        unupgraded = movement_sweep.run_trials(upgraded=False)
        not_researched = movement_sweep.research_speed_upgrades()
        upgraded = movement_sweep.run_trials(upgraded=True)
        return {
            "base_build": movement_sweep.base_build,
            "speed_upgrades": {t.name: u.name for t, u in movement_sweep.speed_upgrades.items()},
            "not_researched": not_researched,
            "trials": unupgraded + upgraded,
        }


def main(argv: Sequence[str]) -> None:
    """Sweep the races named, or every race, and write the findings."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("races", nargs="*", help=f"which to play, of {', '.join(RACES_BY_NAME)}; every one when none")
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_FINDINGS_PATH,
        help=f"where to write the findings; {DEFAULT_FINDINGS_PATH} if not named",
    )
    arguments = parser.parse_args(argv)
    if unknown_races := [name for name in arguments.races if name not in RACES_BY_NAME]:
        raise SystemExit(f"No race is called {', '.join(unknown_races)}")
    installation = Installation.find()
    findings = {name: sweep_race(RACES_BY_NAME[name], installation) for name in arguments.races or RACES_BY_NAME}
    arguments.out.write_text(json.dumps(findings, indent=2) + "\n", encoding="utf-8")
    logger.info("Wrote the findings to {}", arguments.out)


if __name__ == "__main__":
    main(sys.argv[1:])
