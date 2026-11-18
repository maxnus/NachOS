"""Measure how fast units move: with each speed upgrade and without, on creep and off, and under buffs that change it.

Needs StarCraft II installed. Plays a game as each race named, or as every race, against the easiest computer, which is
cleared down to one structure, under `show_map`, `free` and `fast_build`. The types measured are those the tables give
exactly one upgrade that changes their speed, a zealot's Charge among them. A burrowed roach, which the rows give two,
is left out.

Each trial makes one unit at one end of a straight course on open ground and orders it to move to the other end,
along each of `_HEADINGS_DEGREES`, recording where it stood at every step. A Zerg ground type runs again on creep,
between four hatcheries made beside a course of its own, once their creep has stopped spreading, every point of it
`_CREEP_MARGIN` or more from ground without creep. As Protoss, a sentry's hallucinations of the types it can
hallucinate run too. Then the sweep researches each of the race's speed upgrades, at researchers it makes with whatever
they need, and runs every trial again. Before the speed upgrades and after, each race's units that put a buff on
themselves run wearing it: a medivac with its afterburners lit, a hydralisk lunging once Nanomuscular Swell is in, a
void ray in prismatic alignment, and, after alone, as Charge is its speed upgrade, a zealot charging a zergling of the
computer's 3.5 ahead.

As Zerg, every other Zerg type on the ground with a speed runs too, off creep and on it, once, as no upgrade changes it.
As Terran, once the speed upgrades are in, Stimpack and Concussive Shells are researched too, and a marine and a
marauder run stimmed; a zergling, a marine, a zealot, a roach and an ultralisk run along a course while a marauder of
this player's, ordered to attack it, slows it with concussive shells, its life and shields set back to full at every
step; and a marine, a zergling and a medivac run through an inhibitor zone of each size, made neutral at the course's
middle. Then two at once: a marine and a marauder stimmed and slowed, a marine stimmed through a medium zone, and a
zergling slowed through it. As Zerg, once the speed upgrades are in, Stimpack and Concussive Shells are researched too,
and a zergling and a roach run on creep slowed by concussive shells, and through a medium zone on creep::

    uv run python tools/sweep_movement.py
    uv run python tools/sweep_movement.py zerg --out movement-zerg.json

With `rivals`, or with no names, it also plays a game of two players, both played from here, in which a zergling, a
marine, a roach and a mutalisk of the enemy's run along a ground course under a fungal of an infestor of this player's,
cast on the runner a third of the way along, and under a time warp of a mothership's, cast at the course's middle as
the runner sets off, and under both, the fungal cast once the runner wears the warp's buff; its life is set back to
full at every step, since the mothership fires at it. A zergling and a roach also run under each alone on the enemy's
own creep, by its start. They are watched for as long as the course takes at a quarter of the row's speed, a stop
notwithstanding::

    uv run python tools/sweep_movement.py rivals --out movement-rivals.json

Each trial records the row's speed, the steps until the unit moved and until it cruised, at 98% of its fastest step or
more, its fastest, median and slowest step while cruising, and the most and least a window of each of
`_WINDOW_STEPS` steps took it while cruising, all in distance per second, as `Unit.velocity` reads; how far it moved in
each step; and for a unit on the ground, how many steps it ended on creep and how near it came to ground of the other
kind; and the buffs it wore after each step. It writes `movement.json`, or the file `--out` names, and logs, for each
trial, how fast it moved in the steps it began and ended wearing the same buffs, by those buffs. A trial the game would
not set up is recorded as its error.
"""

import argparse
import functools
import json
import math
import statistics
import sys
from collections import defaultdict
from collections.abc import Callable, Iterator, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Self

from _sandbox import NEUTRAL, SUCCESS, Cheat, OpenGround, Rivals, Sandbox, playing, playing_rivals
from loguru import logger
from s2clientprotocol import debug_pb2, raw_pb2

from sc2nachos.gamedata import Attribute, GameData, Tech
from sc2nachos.gamemap import GameMap
from sc2nachos.gamemap._image_data import image_tiles
from sc2nachos.gametime import STEPS_PER_SECOND
from sc2nachos.geometry import Grid, Point, Rectangle
from sc2nachos.ids import UnitTypeId, UpgradeId
from sc2nachos.ids.raw import RawAbilityId, RawBuffId
from sc2nachos.match import Race
from sc2nachos.run import Installation
from sc2nachos.units import UnitType

DEFAULT_FINDINGS_PATH = Path("movement.json")
RACES_BY_NAME = {"terran": Race.TERRAN, "zerg": Race.ZERG, "protoss": Race.PROTOSS}
# The name of the game of two players, beside the races.
RIVALS = "rivals"

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
_ADD_ON_HOSTS = {UnitTypeId.TECH_LAB_BARRACKS: UnitTypeId.BARRACKS, UnitTypeId.TECH_LAB_STARPORT: UnitTypeId.STARPORT}
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
_LIFE_VALUE = debug_pb2.DebugSetUnitValue.Life
_SHIELDS_VALUE = debug_pb2.DebugSetUnitValue.Shields
# The Zerg types on the ground that run on creep though no upgrade changes their speed leave out a larva, which wanders,
# and the eggs and cocoons, which stay where they are.
_NOT_RUN_ON_CREEP = frozenset({UnitTypeId.LARVA, *UnitType.Cocoon.type_ids, *UnitType.Egg.type_ids})
# The researches a Terran game makes after its speed upgrades, for the buffs they bring.
_BUFF_UPGRADES = (UpgradeId.STIMPACK, UpgradeId.CONCUSSIVE_SHELLS)
_STIMS = {UnitTypeId.MARINE: RawAbilityId.Effect_Stim_Marine, UnitTypeId.MARAUDER: RawAbilityId.Effect_Stim_Marauder}
# The types a marauder slows with concussive shells, light and armored, on foot and massive, each starting this far
# along its course from the marauder, which is made at the course's start; and how long the marauder is given for its
# first hit.
_SLOWED_TYPES = (UnitTypeId.ZERGLING, UnitTypeId.MARINE, UnitTypeId.ZEALOT, UnitTypeId.ROACH, UnitTypeId.ULTRALISK)
_SLOWED_HEAD_START = 2.0
_FIRST_HIT_STEPS = 32
# The inhibitor zones, by size, and the types run through each, one that flies among them, along the ground course.
_INHIBITOR_ZONES = (UnitTypeId.INHIBITOR_ZONE_SMALL, UnitTypeId.INHIBITOR_ZONE_MEDIUM, UnitTypeId.INHIBITOR_ZONE_LARGE)
_ZONE_RUNNERS = (UnitTypeId.MARINE, UnitTypeId.ZERGLING, UnitTypeId.MEDIVAC)
# A buffed trial watches its unit for as long as the course takes it at this share of its row speed, as a slow can
# halve it.
_BUFFED_WATCH_SPEED_SHARE = 0.25
# The buffs a unit of each race puts on itself that may change its speed, run before the speed upgrades and after: the
# type, the ability it uses, and the research the ability needs, if any. A zealot charges a zergling of the computer's
# made this far along the course ahead of it, which moves it, and is watched until it stands still.
_SELF_BUFFS: dict[Race, tuple[tuple[UnitTypeId, RawAbilityId, UpgradeId | None], ...]] = {
    Race.TERRAN: ((UnitTypeId.MEDIVAC, RawAbilityId.Effect_MedivacIgniteAfterburners, None),),
    Race.ZERG: ((UnitTypeId.HYDRALISK, RawAbilityId.HydraliskFrenzy, UpgradeId.HYDRALISK_LUNGE),),
    Race.PROTOSS: (
        (UnitTypeId.VOID_RAY, RawAbilityId.Effect_VoidRayPrismaticAlignment, None),
        (UnitTypeId.ZEALOT, RawAbilityId.Effect_Charge, UpgradeId.CHARGE),
    ),
}
_CHARGE_TARGET_DISTANCE = 3.5
# Two modifiers at once, in the Terran game: a stimmed marine and marauder slowed by concussive shells, a stimmed marine
# in an inhibitor zone of this size, and a zergling slowed by concussive shells in it; in the Zerg game, once its speed
# upgrades are in, these types run on creep slowed by concussive shells and through the zone.
_PAIRED_ZONE = UnitTypeId.INHIBITOR_ZONE_MEDIUM
_SLOWED_ON_CREEP_TYPES = (UnitTypeId.ZERGLING, UnitTypeId.ROACH)
# The spells of this player's that land on the enemy's units alone, each its caster, made this far to the side of the
# course's middle, and its ability, cast at a point; and the trials' conditions, by the spells cast. A time warp lands
# at the course's middle as the runner sets off, since it slows nothing for its first 1.79 s (in game). A fungal lands
# this far ahead of the runner once it is this share of the way along, or, with a time warp, once it wears the warp's
# buff.
_FUNGAL = (UnitTypeId.INFESTOR, RawAbilityId.FungalGrowth_FungalGrowth)
_TIME_WARP = (UnitTypeId.MOTHERSHIP, RawAbilityId.Effect_TimeWarp)
_ENEMY_SPELLS = {
    "slowed by a fungal": (_FUNGAL,),
    "slowed by a time warp": (_TIME_WARP,),
    "slowed by a fungal and a time warp": (_TIME_WARP, _FUNGAL),
}
_TIME_WARP_BUFF = "TemporalField"
_CASTER_SIDE_DISTANCE = 5.0
_FUNGAL_ALONG = 1 / 3
_FUNGAL_LEAD = 1.0
# The enemy's types run under them, on foot and flying; and those run on its own creep, at its main, under each spell
# alone, along the longest course along either axis through a tile center this near its start, every point of it and
# this much to either side on creep.
_ENEMY_RUNNERS = (UnitTypeId.ZERGLING, UnitTypeId.MARINE, UnitTypeId.ROACH, UnitTypeId.MUTALISK)
_ENEMY_CREEP_RUNNERS = (UnitTypeId.ZERGLING, UnitTypeId.ROACH)
_ENEMY_CREEP_CONDITIONS = ("slowed by a fungal", "slowed by a time warp")
_ENEMY_CREEP_SEARCH = 10
_ENEMY_CREEP_CLEARANCE = 1.0


@dataclass(slots=True)
class MoveFinding:
    """One unit ordered along a course: how fast it moved, against its row."""

    unit_type: str
    upgrade: str | None
    """The upgrade that changes the type's speed; None for a type no upgrade changes, run on creep and off it."""
    upgraded: bool
    on_creep: bool
    hallucination: bool
    heading_degrees: float
    flying: bool
    """Whether the unit made flew, as the game reported it."""
    row_speed: float
    """The speed the rows give the type, with the upgrade when `upgraded`, in distance per second."""
    condition: str | None = None
    """What else the trial put on it: `stimmed`, `slowed by concussive shells`, or the inhibitor zone it ran through."""
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
    buffs: list[list[str]] = field(default_factory=list)
    """The buffs it wore as ordered and after each step, in the raw catalog's spelling, by name."""


def speed_upgrades_by_type(data: GameData, race: Race) -> dict[UnitTypeId, UpgradeId]:
    """Each type of `race` with a speed that the tables give exactly one upgrade changing it, with that upgrade, in the
    order of the types' names: a zealot's Charge among them, which upgrade inference reads off a buff instead."""
    speed_upgrades: dict[UnitTypeId, UpgradeId] = {}
    for unit_type, row in data.units.items():
        if row.race is not race or row.speed == 0:
            continue
        speeding = [upgrade for upgrade, change in row.upgrades.items() if change.speed != 0]
        if len(speeding) == 1:
            speed_upgrades[unit_type] = speeding[0]
    return dict(sorted(speed_upgrades.items(), key=lambda item: item[0].name))


def creep_runners(data: GameData, race: Race, speed_upgrades: dict[UnitTypeId, UpgradeId]) -> list[UnitTypeId]:
    """The types of `race`, if Zerg, that are no structure, have a speed in the rows and no upgrade changing it, but
    `_NOT_RUN_ON_CREEP`, in the order of their names; whether one flies is for the game to say."""
    if race is not Race.ZERG:
        return []
    runners = [
        unit_type
        for unit_type, row in data.units.items()
        if row.race is Race.ZERG
        and Attribute.STRUCTURE not in row.attributes
        and row.speed > 0
        and unit_type not in speed_upgrades
        and unit_type not in _NOT_RUN_ON_CREEP
    ]
    return sorted(runners, key=lambda unit_type: unit_type.name)


def _buff_names(unit: raw_pb2.Unit) -> list[str]:
    """The buffs `unit` wears, in the raw catalog's spelling, by name, or by number for one it leaves out."""
    names = []
    for buff_id in unit.buff_ids:
        try:
            names.append(RawBuffId(buff_id).name)
        except ValueError:
            names.append(str(buff_id))
    return sorted(names)


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
        self.creep_runners = creep_runners(self.data, race, self.speed_upgrades)
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
            heading: longest_ground_course(self._map, heading, away_from) for heading in _HEADINGS_DEGREES
        }
        for heading, course in self._ground_courses.items():
            logger.info("The ground course at {} degrees runs {:.1f} from {}", heading, course.length, course.start)
        self._creep_half_length: float | None = None
        self._flying_by_type: dict[UnitTypeId, bool] = {}

    def run_trials(self, *, upgraded: bool) -> list[dict[str, object]]:
        """Every trial of the race's types, and as Protoss of its hallucinations, as findings or errors; unupgraded, as
        Zerg, of the types that run on creep with no upgrade too."""
        trials: list[dict[str, object]] = []
        types_run: list[tuple[UnitTypeId, UpgradeId | None]] = list(self.speed_upgrades.items())
        if not upgraded:
            types_run += [(unit_type, None) for unit_type in self.creep_runners if not self._may_fly(unit_type)]
        for unit_type, upgrade in types_run:
            for heading in _HEADINGS_DEGREES:
                trials.append(self._recorded(unit_type, upgrade, heading, upgraded=upgraded))
            if self._creep_center is not None and not self._may_fly(unit_type):
                for heading in _CREEP_HEADINGS_DEGREES:
                    trials.append(self._recorded(unit_type, upgrade, heading, upgraded=upgraded, on_creep=True))
            if unit_type in _HALLUCINATIONS:
                for heading in _HEADINGS_DEGREES:
                    trials.append(self._recorded(unit_type, upgrade, heading, upgraded=upgraded, hallucination=True))
        return trials

    def research_buff_upgrades(self) -> dict[str, str]:
        """Research `_BUFF_UPGRADES`, and return why each that was not researched was not."""
        not_researched: dict[str, str] = {}
        for upgrade in _BUFF_UPGRADES:
            try:
                self._research(upgrade)
            except RuntimeError as error:
                logger.warning("{} was not researched: {}", upgrade.name, error)
                not_researched[upgrade.name] = str(error)
            else:
                logger.info("Researched {}", upgrade.name)
        return not_researched

    def run_buff_trials(self) -> list[dict[str, object]]:
        """The stimmed, slowed and inhibited trials, as findings or errors."""
        trials: list[dict[str, object]] = []
        for unit_type, stim in _STIMS.items():
            for heading in _HEADINGS_DEGREES:
                trials.append(
                    self._recorded(
                        unit_type,
                        None,
                        heading,
                        upgraded=False,
                        condition="stimmed",
                        before_move=functools.partial(self._buffed_by, stim),
                    )
                )
        for unit_type in _SLOWED_TYPES:
            course = self._ground_courses[_HEADINGS_DEGREES[0]]
            head_start = Course(
                _point_toward(course.start, math.radians(_HEADINGS_DEGREES[0]), _SLOWED_HEAD_START), course.end
            )
            attacker_tags: list[int] = []
            trials.append(
                self._recorded(
                    unit_type,
                    None,
                    _HEADINGS_DEGREES[0],
                    upgraded=False,
                    condition="slowed by concussive shells",
                    course=head_start,
                    before_move=functools.partial(self._slowed, course.start, attacker_tags),
                    each_step=self._kept_alive,
                    made_tags=attacker_tags,
                )
            )
        for zone_type in _INHIBITOR_ZONES:
            course = self._ground_courses[_HEADINGS_DEGREES[0]]
            middle = Point(((course.start.x + course.end.x) / 2, (course.start.y + course.end.y) / 2))
            try:
                zone = self._make(zone_type, middle, owner=NEUTRAL)
            except RuntimeError as error:
                logger.warning("{}: {}", zone_type.name, error)
                trials.append({"condition": zone_type.name, "error": str(error)})
                continue
            try:
                for unit_type in _ZONE_RUNNERS:
                    trials.append(
                        self._recorded(
                            unit_type,
                            None,
                            _HEADINGS_DEGREES[0],
                            upgraded=False,
                            condition=zone_type.name,
                            course=course,
                        )
                    )
            finally:
                self._game.remove([zone.tag])
        trials += self._paired_trials()
        return trials

    def _paired_trials(self) -> list[dict[str, object]]:
        """The Terran game's trials of two modifiers at once, as findings or errors."""
        trials: list[dict[str, object]] = []
        course = self._ground_courses[_HEADINGS_DEGREES[0]]
        head_start = Course(
            _point_toward(course.start, math.radians(_HEADINGS_DEGREES[0]), _SLOWED_HEAD_START), course.end
        )
        for unit_type, stim in _STIMS.items():
            attacker_tags: list[int] = []
            trials.append(
                self._recorded(
                    unit_type,
                    None,
                    _HEADINGS_DEGREES[0],
                    upgraded=False,
                    condition="stimmed, slowed by concussive shells",
                    course=head_start,
                    before_move=functools.partial(self._stimmed_and_slowed, stim, course.start, attacker_tags),
                    each_step=self._kept_alive,
                    made_tags=attacker_tags,
                )
            )
        middle = Point(((course.start.x + course.end.x) / 2, (course.start.y + course.end.y) / 2))
        try:
            zone = self._make(_PAIRED_ZONE, middle, owner=NEUTRAL)
        except RuntimeError as error:
            logger.warning("{}: {}", _PAIRED_ZONE.name, error)
            return [*trials, {"condition": f"paired with {_PAIRED_ZONE.name}", "error": str(error)}]
        try:
            trials.append(
                self._recorded(
                    UnitTypeId.MARINE,
                    None,
                    _HEADINGS_DEGREES[0],
                    upgraded=False,
                    condition=f"stimmed, {_PAIRED_ZONE.name}",
                    course=course,
                    before_move=functools.partial(self._buffed_by, _STIMS[UnitTypeId.MARINE]),
                )
            )
            attacker_tags = []
            trials.append(
                self._recorded(
                    UnitTypeId.ZERGLING,
                    None,
                    _HEADINGS_DEGREES[0],
                    upgraded=False,
                    condition=f"slowed by concussive shells, {_PAIRED_ZONE.name}",
                    course=head_start,
                    before_move=functools.partial(self._slowed, course.start, attacker_tags),
                    each_step=self._kept_alive,
                    made_tags=attacker_tags,
                )
            )
        finally:
            self._game.remove([zone.tag])
        return trials

    def run_creep_pair_trials(self) -> list[dict[str, object]]:
        """The Zerg game's trials on creep of a slow at once, once its speed upgrades and Concussive Shells are in: each
        of `_SLOWED_ON_CREEP_TYPES` slowed by concussive shells, and run through an inhibitor zone, as findings or
        errors."""
        trials: list[dict[str, object]] = []
        try:
            course = self._creep_course(_CREEP_HEADINGS_DEGREES[0])
        except RuntimeError as error:
            return [{"condition": "on creep, paired", "error": str(error)}]
        head_start = Course(
            _point_toward(course.start, math.radians(_CREEP_HEADINGS_DEGREES[0]), _SLOWED_HEAD_START), course.end
        )
        for unit_type in _SLOWED_ON_CREEP_TYPES:
            attacker_tags: list[int] = []
            trials.append(
                self._recorded(
                    unit_type,
                    self.speed_upgrades.get(unit_type),
                    _CREEP_HEADINGS_DEGREES[0],
                    upgraded=True,
                    on_creep=True,
                    condition="slowed by concussive shells",
                    course=head_start,
                    before_move=functools.partial(self._slowed, course.start, attacker_tags),
                    each_step=self._kept_alive,
                    made_tags=attacker_tags,
                )
            )
        middle = Point(((course.start.x + course.end.x) / 2, (course.start.y + course.end.y) / 2))
        try:
            zone = self._make(_PAIRED_ZONE, middle, owner=NEUTRAL)
        except RuntimeError as error:
            logger.warning("{}: {}", _PAIRED_ZONE.name, error)
            return [*trials, {"condition": f"on creep, {_PAIRED_ZONE.name}", "error": str(error)}]
        try:
            for unit_type in _SLOWED_ON_CREEP_TYPES:
                trials.append(
                    self._recorded(
                        unit_type,
                        self.speed_upgrades.get(unit_type),
                        _CREEP_HEADINGS_DEGREES[0],
                        upgraded=True,
                        on_creep=True,
                        condition=_PAIRED_ZONE.name,
                        course=course,
                    )
                )
        finally:
            self._game.remove([zone.tag])
        return trials

    def _stimmed_and_slowed(
        self, stim: RawAbilityId, attacker_at: Point, attacker_tags: list[int], mover: raw_pb2.Unit
    ) -> None:
        """Stim `mover`, then have a marauder slow it, as `_buffed_by` and `_slowed` do."""
        self._buffed_by(stim, mover)
        self._slowed(attacker_at, attacker_tags, mover)

    def _buffed_by(self, ability: RawAbilityId, mover: raw_pb2.Unit) -> None:
        """Order `mover` to use `ability` on itself, as a stim, and step until it wears a buff."""
        if (verdict := self._game.order(ability, mover.tag)) != SUCCESS:
            raise RuntimeError(f"the game refused {ability.name}: {verdict}")
        for _ in range(_MAKING_STEPS):
            self._client.step(1)
            if self._listed(mover.tag).buff_ids:
                return
        raise RuntimeError(f"no buff within {_MAKING_STEPS} steps of {ability.name}")

    def _charged(self, target_at: Point, made_tags: list[int], mover: raw_pb2.Unit) -> None:
        """Make a zergling of the computer's at `target_at`, adding its tag to `made_tags`, and order `mover` to charge
        it, which moves it, with no move ordered after."""
        computer = 2 if self._game.player == 1 else 1
        target = self._make(UnitTypeId.ZERGLING, target_at, owner=computer)
        made_tags.append(target.tag)
        if (verdict := self._game.order(RawAbilityId.Effect_Charge, mover.tag, target.tag)) != SUCCESS:
            raise RuntimeError(f"the game refused the charge: {verdict}")

    def run_self_buff_trials(self, *, upgraded: bool) -> tuple[list[dict[str, object]], dict[str, str]]:
        """The trials of the race's `_SELF_BUFFS`, with the speed upgrades in or not, researching what each needs, as
        findings or errors, and why each research that failed did; unupgraded, leaving out a type whose ability needs
        the research that changes its speed, a zealot's Charge."""
        trials: list[dict[str, object]] = []
        not_researched: dict[str, str] = {}
        for unit_type, ability, needed in _SELF_BUFFS.get(self._race, ()):
            changes_its_speed = (
                needed is not None
                and needed in self.data.units[unit_type].upgrades
                and bool(self.data.units[unit_type].upgrades[needed].speed)
            )
            if not upgraded and changes_its_speed:
                continue
            if needed is not None and needed not in self._researched():
                try:
                    self._research(needed)
                except RuntimeError as error:
                    logger.warning("{} was not researched: {}", needed.name, error)
                    not_researched[needed.name] = str(error)
                    trials.append({"unit_type": unit_type.name, "condition": ability.name, "error": str(error)})
                    continue
            speed_upgrade = self.speed_upgrades.get(unit_type)
            if ability is RawAbilityId.Effect_Charge:
                course = self._ground_courses[_HEADINGS_DEGREES[0]]
                target_at = _point_toward(course.start, math.radians(_HEADINGS_DEGREES[0]), _CHARGE_TARGET_DISTANCE)
                made_tags: list[int] = []
                trials.append(
                    self._recorded(
                        unit_type,
                        speed_upgrade,
                        _HEADINGS_DEGREES[0],
                        upgraded=upgraded and speed_upgrade is not None,
                        condition=f"using {ability.name}",
                        course=Course(course.start, target_at),
                        before_move=functools.partial(self._charged, target_at, made_tags),
                        made_tags=made_tags,
                        order_move=False,
                    )
                )
                continue
            for heading in _HEADINGS_DEGREES:
                trials.append(
                    self._recorded(
                        unit_type,
                        speed_upgrade,
                        heading,
                        upgraded=upgraded and speed_upgrade is not None,
                        condition=f"using {ability.name}",
                        before_move=functools.partial(self._buffed_by, ability),
                    )
                )
        return trials, not_researched

    def _slowed(self, attacker_at: Point, attacker_tags: list[int], mover: raw_pb2.Unit) -> None:
        """Make a marauder at `attacker_at`, adding its tag to `attacker_tags`, order it to attack `mover`, and step
        until it has hit `mover`, whose life and shields are then set back to full. An ultralisk, being massive, is hit
        but never slowed."""
        attacker = self._make(UnitTypeId.MARAUDER, attacker_at)
        attacker_tags.append(attacker.tag)
        if (verdict := self._game.order(RawAbilityId.Attack, attacker.tag, mover.tag)) != SUCCESS:
            raise RuntimeError(f"the game refused the marauder's attack: {verdict}")
        for _ in range(_FIRST_HIT_STEPS):
            self._client.step(1)
            target = self._listed(mover.tag)
            if target.health < target.health_max or target.shield < target.shield_max:
                self._kept_alive(mover.tag)
                return
        raise RuntimeError(f"the marauder did not hit within {_FIRST_HIT_STEPS} steps")

    def _kept_alive(self, tag: int) -> None:
        """Set the unit `tag`'s life and shields back to full."""
        unit = self._listed(tag)
        self._game.set_value(_LIFE_VALUE, unit.health_max, [tag])
        if unit.shield_max:
            self._game.set_value(_SHIELDS_VALUE, unit.shield_max, [tag])

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
        upgrade: UpgradeId | None,
        heading_degrees: float,
        *,
        upgraded: bool,
        on_creep: bool = False,
        hallucination: bool = False,
        condition: str | None = None,
        course: Course | None = None,
        before_move: Callable[[raw_pb2.Unit], None] | None = None,
        each_step: Callable[[int], None] | None = None,
        made_tags: list[int] | None = None,
        order_move: bool = True,
    ) -> dict[str, object]:
        """One trial as its finding, or as its error if the game would not set it up: along `course` if given, with
        `before_move` given the unit made before it is ordered, `each_step` given its tag after each step, and the tags
        in `made_tags`, filled as the trial goes, removed with it; with no move ordered unless `order_move`, for a
        `before_move` that moves the unit itself."""
        conditions = {
            "unit_type": unit_type.name,
            "upgraded": upgraded,
            "on_creep": on_creep,
            "hallucination": hallucination,
            "condition": condition,
            "heading_degrees": heading_degrees,
        }
        try:
            finding = self._trial(
                unit_type,
                upgrade,
                heading_degrees,
                upgraded,
                on_creep,
                hallucination,
                condition=condition,
                course=course,
                before_move=before_move,
                each_step=each_step,
                made_tags=[] if made_tags is None else made_tags,
                order_move=order_move,
            )
        except RuntimeError as error:
            logger.warning("{}: {}", conditions, error)
            return {**conditions, "error": str(error)}
        logger.info(
            "{} {}{}{}{} at {} degrees: cruised at {} a second against the row's {}, windows {}, {} of {} steps on"
            " creep, {} from ground of the other kind; by buffs {}",
            unit_type.name,
            "upgraded" if upgraded else "unupgraded",
            " on creep" if on_creep else "",
            " hallucinated" if hallucination else "",
            f" {condition}" if condition else "",
            heading_degrees,
            finding.median_cruising_speed,
            finding.row_speed,
            finding.window_speeds.get(str(_WINDOW_STEPS[1])),
            finding.steps_on_creep,
            len(finding.step_distances),
            finding.least_distance_to_other_ground,
            speeds_by_buffs(finding),
        )
        return asdict(finding)

    def _trial(
        self,
        unit_type: UnitTypeId,
        upgrade: UpgradeId | None,
        heading_degrees: float,
        upgraded: bool,
        on_creep: bool,
        hallucination: bool,
        *,
        condition: str | None,
        course: Course | None,
        before_move: Callable[[raw_pb2.Unit], None] | None,
        each_step: Callable[[int], None] | None,
        made_tags: list[int],
        order_move: bool,
    ) -> MoveFinding:
        """Make a `unit_type` at the start of its course along `heading_degrees`, or of `course`, order it to the end,
        and record how far it moved in each step and what it wore, as `_recorded` says."""
        row = self.data.units[unit_type]
        row_speed = row.with_upgrades({upgrade}).speed if upgraded and upgrade is not None else row.speed
        if on_creep:
            self._game.remove(unit.tag for unit in self._game.units() if unit.unit_type == UnitTypeId.LARVA)
        if course is not None:
            pass
        elif on_creep:
            course = self._creep_course(heading_degrees)
        elif self._flies(unit_type):
            course = Course.through(self._map.playable_area.center, heading_degrees, _FLYING_COURSE_HALF_LENGTH)
        else:
            course = self._ground_courses[heading_degrees]
        try:
            if hallucination:
                mover = self._hallucinated(unit_type, course.start, made_tags)
            else:
                mover = self._make(unit_type, course.start)
                made_tags.append(mover.tag)
            if not mover.is_flying and not on_creep and self._has_creep_on(course):
                raise RuntimeError("the course off creep has creep on it")
            self._client.step(_SETTLING_STEPS)
            if before_move is not None:
                before_move(mover)
            watch_speed_share = _WATCH_SPEED_SHARE if condition is None else _BUFFED_WATCH_SPEED_SHARE
            positions, buffs = self._watched_moving(
                mover.tag, course, min(row.speed, row_speed), watch_speed_share, each_step, order_move=order_move
            )
        finally:
            self._game.remove(made_tags)
        finding = MoveFinding(
            unit_type.name,
            None if upgrade is None else upgrade.name,
            upgraded,
            on_creep,
            hallucination,
            heading_degrees,
            mover.is_flying,
            round(row_speed, 4),
            condition,
        )
        _measure_into(finding, positions)
        finding.buffs = buffs
        if not mover.is_flying:
            creep = self._creep()
            finding.steps_on_creep = sum(1 for position in positions[1:] if creep[position])
            finding.least_distance_to_other_ground = _least_distance_to_tile_of(
                creep, positions[1:], has_creep=not on_creep
            )
        return finding

    def _watched_moving(
        self,
        tag: int,
        course: Course,
        slower_row_speed: float,
        watch_speed_share: float,
        each_step: Callable[[int], None] | None,
        *,
        order_move: bool = True,
    ) -> tuple[list[Point], list[list[str]]]:
        """Order the unit `tag` to the end of `course`, if `order_move`, and return where it stood and the buffs it wore
        as ordered and after each step, until it arrived, stood still after moving, or had taken as long as the course
        takes it at `watch_speed_share` of `slower_row_speed`, calling `each_step`, if given, with its tag after each
        step."""
        mover = self._listed(tag)
        positions = [_position_of(mover)]
        buffs = [_buff_names(mover)]
        if order_move:
            self._game.order(RawAbilityId.Move_Move, tag, course.end)
        watch_steps = math.ceil(course.length / (slower_row_speed * watch_speed_share / STEPS_PER_SECOND))
        still_steps = 0
        for _ in range(watch_steps):
            self._client.step(1)
            mover = self._listed(tag)
            buffs.append(_buff_names(mover))
            if each_step is not None:
                each_step(tag)
            position = _position_of(mover)
            has_moved_before = positions[-1].distance_to(positions[0]) > _SMALLEST_MOVE
            still_steps = still_steps + 1 if position.distance_to(positions[-1]) < _SMALLEST_MOVE else 0
            positions.append(position)
            if position.distance_to(course.end) < _ARRIVED_DISTANCE:
                break
            if has_moved_before and still_steps >= _STILL_STEPS_TO_STOP:
                break
        return positions, buffs

    def _may_fly(self, unit_type: UnitTypeId) -> bool:
        """Whether a `unit_type` flies, as `_flies` reads it, or False if the game would not make one, so that its trial
        records why."""
        try:
            return self._flies(unit_type)
        except RuntimeError:
            return False

    def _flies(self, unit_type: UnitTypeId) -> bool:
        """Whether a `unit_type` flies, as the game reports one made at the start of the first ground course."""
        if (flies := self._flying_by_type.get(unit_type)) is None:
            probe = self._make(unit_type, self._ground_courses[_HEADINGS_DEGREES[0]].start)
            self._game.remove([probe.tag])
            flies = self._flying_by_type[unit_type] = probe.is_flying
        return flies

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

    def _make(self, unit_type: UnitTypeId, at: Point, *, owner: int | None = None) -> raw_pb2.Unit:
        """Make one `unit_type` at `at`, this player's or `owner`'s, and step until the game lists it."""
        tags_before = {unit.tag for unit in self._game.units()}
        self._game.debug(self._game.create(unit_type, self._game.player if owner is None else owner, at))
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


def longest_ground_course(game_map: GameMap, heading_degrees: float, away_from: Sequence[Point]) -> Course:
    """The longest ground course along `heading_degrees` through a tile center at least
    `_GROUND_COURSE_DISTANCE_FROM_OTHERS` from each of `away_from`. Raises `RuntimeError` if there is none."""
    pathing = game_map.pathing
    area = game_map.playable_area
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


def sweep_enemy_buffs(installation: Installation) -> dict[str, object]:
    """The game of two players: each of `_ENEMY_RUNNERS` of the enemy's under each of `_ENEMY_SPELLS`."""
    with playing_rivals(Race.ZERG, installation) as rivals:
        me = rivals.me
        me.cheat(Cheat.SHOW_MAP)
        rivals.step(_SETTLING_STEPS)
        data = GameData(me.client.game_data())
        game_map = GameMap._from_game(me.client.game_info(), me.client.observation())
        course = longest_ground_course(game_map, _HEADINGS_DEGREES[0], game_map.all_start_locations)
        logger.info("The course for the enemy's runners runs {:.1f} from {}", course.length, course.start)
        trials = [
            _recorded_enemy_buff(rivals, data, course, condition, runner_type, on_creep=False)
            for condition in _ENEMY_SPELLS
            for runner_type in _ENEMY_RUNNERS
        ]
        enemy_start = next(start for start in game_map.all_start_locations if start != game_map.start_location)
        try:
            creep_course = _enemy_creep_course(game_map, _creep_of(rivals.enemy, game_map), enemy_start)
        except RuntimeError as error:
            trials.append({"condition": "the enemy's, on creep", "error": str(error)})
        else:
            logger.info("The enemy's creep course runs {:.1f} from {}", creep_course.length, creep_course.start)
            trials += [
                _recorded_enemy_buff(rivals, data, creep_course, condition, runner_type, on_creep=True)
                for condition in _ENEMY_CREEP_CONDITIONS
                for runner_type in _ENEMY_CREEP_RUNNERS
            ]
        return {"base_build": me.client.ping().base_build, "trials": trials}


def _creep_of(game: Sandbox, game_map: GameMap) -> Grid[bool]:
    """Where there is creep now, as `game` sees it."""
    creep_image = game.client.observation().observation.raw_data.map_state.creep
    return Grid(image_tiles(creep_image, game_map.playable_area) != 0, origin=game_map.pathing.origin, outside=False)


def _enemy_creep_course(game_map: GameMap, creep: Grid[bool], start: Point) -> Course:
    """The longest course along the x- or the y-axis through a tile center within `_ENEMY_CREEP_SEARCH` of `start`, on
    ground pathable and on creep along it and `_ENEMY_CREEP_CLEARANCE` to either side, ending `_COURSE_END_INSET`
    inside. Raises `RuntimeError` if there is none."""
    pathing = game_map.pathing
    best: tuple[float, Point, float] | None = None
    for dx in range(-_ENEMY_CREEP_SEARCH, _ENEMY_CREEP_SEARCH + 1):
        for dy in range(-_ENEMY_CREEP_SEARCH, _ENEMY_CREEP_SEARCH + 1):
            center = Point((math.floor(start.x) + dx + 0.5, math.floor(start.y) + dy + 0.5))
            for heading in (0.0, 90.0):
                half_length = _clear_half_length(
                    center, heading, lambda point: pathing[point] and creep[point], clearance=_ENEMY_CREEP_CLEARANCE
                )
                if best is None or half_length > best[0]:
                    best = (half_length, center, heading)
    if best is None or best[0] <= _COURSE_END_INSET:
        raise RuntimeError(f"no course on creep within {_ENEMY_CREEP_SEARCH} of the enemy's start")
    half_length, center, heading = best
    return Course.through(center, heading, half_length - _COURSE_END_INSET)


def _recorded_enemy_buff(
    rivals: Rivals, data: GameData, course: Course, condition: str, runner_type: UnitTypeId, *, on_creep: bool
) -> dict[str, object]:
    """One trial of the game of two players as its finding, or as its error if the game would not set it up."""
    conditions = {"unit_type": runner_type.name, "condition": condition, "on_creep": on_creep}
    made_tags: list[int] = []
    try:
        finding = _enemy_buff_trial(rivals, data, course, condition, runner_type, made_tags, on_creep=on_creep)
    except RuntimeError as error:
        logger.warning("{}: {}", conditions, error)
        return {**conditions, "error": str(error)}
    finally:
        _removed_by_both(rivals, made_tags)
    logger.info(
        "The enemy's {}{} {}: {} steps, by buffs {}",
        runner_type.name,
        " on creep" if on_creep else "",
        condition,
        len(finding.step_distances),
        speeds_by_buffs(finding),
    )
    return asdict(finding)


def _enemy_buff_trial(
    rivals: Rivals,
    data: GameData,
    course: Course,
    condition: str,
    runner_type: UnitTypeId,
    made_tags: list[int],
    *,
    on_creep: bool,
) -> MoveFinding:
    """Make a `runner_type` of the enemy's at the start of `course` and the casters of `condition` to the side of its
    middle, run the one to the end under the others' spells, and record how far it moved in each step and what it wore,
    adding what was made to `made_tags`."""
    me, enemy = rivals.me, rivals.enemy
    spells = _ENEMY_SPELLS[condition]
    heading = math.atan2(course.end.y - course.start.y, course.end.x - course.start.x)
    middle = _point_toward(course.start, heading, course.length / 2)
    caster_at = _point_toward(middle, heading + math.pi / 2, _CASTER_SIDE_DISTANCE)
    tags_before = {unit.tag for unit in me.units()}
    me.debug(
        me.create(runner_type, enemy.player, course.start),
        *(me.create(caster_type, me.player, caster_at) for caster_type, _ in spells),
    )
    rivals.step(_MAKING_STEPS)
    made = {unit.unit_type: unit for unit in me.units() if unit.tag not in tags_before}
    made_tags.extend(unit.tag for unit in made.values())
    wanted = [runner_type, *(caster_type for caster_type, _ in spells)]
    if missing := [unit_type.name for unit_type in wanted if unit_type not in made]:
        raise RuntimeError(f"the game did not make the {', '.join(missing)}")
    runner_tag = made[runner_type].tag
    casters = {spell: made[caster_type].tag for caster_type, spell in spells}
    me.set_value(_ENERGY_VALUE, _FULL_ENERGY, casters.values())
    rivals.step(_SETTLING_STEPS)
    runner = _listed_by(enemy, runner_tag)
    positions, buffs = [_position_of(runner)], [_buff_names(runner)]
    if enemy.order(RawAbilityId.Move_Move, runner_tag, course.end) != SUCCESS:
        raise RuntimeError("the game refused the runner's move")
    time_warp = RawAbilityId.Effect_TimeWarp
    if time_warp in casters and (verdict := me.order(time_warp, casters[time_warp], middle)) != SUCCESS:
        raise RuntimeError(f"the game refused the time warp: {verdict}")
    fungal = RawAbilityId.FungalGrowth_FungalGrowth
    fungal_pending = fungal in casters
    row_speed = data.units[runner_type].speed
    watch_steps = math.ceil(course.length / (row_speed * _BUFFED_WATCH_SPEED_SHARE / STEPS_PER_SECOND))
    for _ in range(watch_steps):
        rivals.step(1)
        runner = _listed_by(enemy, runner_tag)
        me.set_value(_LIFE_VALUE, runner.health_max, [runner_tag])
        positions.append(_position_of(runner))
        buffs.append(_buff_names(runner))
        if fungal_pending and (
            _TIME_WARP_BUFF in buffs[-1]
            if time_warp in casters
            else positions[-1].distance_to(course.start) >= course.length * _FUNGAL_ALONG
        ):
            target = _point_toward(positions[-1], heading, _FUNGAL_LEAD)
            if (verdict := me.order(fungal, casters[fungal], target)) != SUCCESS:
                raise RuntimeError(f"the game refused the fungal: {verdict}")
            fungal_pending = False
        if positions[-1].distance_to(course.end) < _ARRIVED_DISTANCE:
            break
    finding = MoveFinding(
        runner_type.name,
        None,
        False,
        on_creep,
        False,
        round(math.degrees(heading), 1),
        runner.is_flying,
        round(row_speed, 4),
        f"the enemy's, {condition}",
    )
    _measure_into(finding, positions)
    finding.buffs = buffs
    if on_creep:
        creep = _creep_of(enemy, GameMap._from_game(me.client.game_info(), me.client.observation()))
        finding.steps_on_creep = sum(1 for position in positions[1:] if creep[position])
    return finding


def _removed_by_both(rivals: Rivals, tags: Sequence[int]) -> None:
    """Kill the units under `tags` and step both players until the game lists none of them: a game of two steps only
    once both ask, so `Sandbox.remove`, which steps one, would wait for ever."""
    me = rivals.me
    me.kill(tags)
    for _ in range(_MAKING_STEPS):
        rivals.step(1)
        if not set(tags) & {unit.tag for unit in me.units()}:
            return
    logger.warning("{} units were not removed", len(tags))


def _listed_by(game: Sandbox, tag: int) -> raw_pb2.Unit:
    """The unit `tag` as a fresh observation of `game`'s lists it."""
    if (unit := next((unit for unit in game.units() if unit.tag == tag), None)) is None:
        raise RuntimeError(f"unit {tag} is no longer listed")
    return unit


def speeds_by_buffs(finding: MoveFinding) -> dict[str, tuple[float, float, int]]:
    """How fast the unit moved in the steps it began and ended wearing the same buffs, after it first moved, by those
    buffs joined with `+`, `none` for none: the median and the fastest of those steps, in distance per second, and how
    many there were."""
    if finding.steps_until_moving is None:
        return {}
    by_buffs: dict[str, list[float]] = defaultdict(list)
    for index in range(finding.steps_until_moving, len(finding.step_distances)):
        before, after = finding.buffs[index], finding.buffs[index + 1]
        if before == after:
            by_buffs["+".join(after) or "none"].append(finding.step_distances[index])
    return {
        buffs: (_per_second(statistics.median(distances)), _per_second(max(distances)), len(distances))
        for buffs, distances in sorted(by_buffs.items())
    }


def sweep_race(race: Race, installation: Installation) -> dict[str, object]:
    """One game as `race`: every trial before the race's speed upgrades and after, and as Terran the buffed trials."""
    with playing(race, installation) as game:
        movement_sweep = MovementSweep(game, race)
        logger.info("{}: {}", race.name, {t.name: u.name for t, u in movement_sweep.speed_upgrades.items()})
        unupgraded = movement_sweep.run_trials(upgraded=False)
        self_buffed, not_researched = movement_sweep.run_self_buff_trials(upgraded=False)
        not_researched |= movement_sweep.research_speed_upgrades()
        upgraded = movement_sweep.run_trials(upgraded=True)
        upgraded_self_buffed, self_buffs_not_researched = movement_sweep.run_self_buff_trials(upgraded=True)
        self_buffed += upgraded_self_buffed
        not_researched |= self_buffs_not_researched
        buffed: list[dict[str, object]] = []
        if race is Race.TERRAN:
            not_researched |= movement_sweep.research_buff_upgrades()
            buffed = movement_sweep.run_buff_trials()
        elif race is Race.ZERG:
            not_researched |= movement_sweep.research_buff_upgrades()
            buffed = movement_sweep.run_creep_pair_trials()
        return {
            "base_build": movement_sweep.base_build,
            "speed_upgrades": {t.name: u.name for t, u in movement_sweep.speed_upgrades.items()},
            "creep_runners": [unit_type.name for unit_type in movement_sweep.creep_runners],
            "not_researched": not_researched,
            "trials": unupgraded + upgraded + self_buffed + buffed,
        }


def main(argv: Sequence[str]) -> None:
    """Sweep the races named, or every race, and write the findings."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "races",
        nargs="*",
        help=f"which to play, of {', '.join(RACES_BY_NAME)} and {RIVALS}, the game of two players; all when none",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_FINDINGS_PATH,
        help=f"where to write the findings; {DEFAULT_FINDINGS_PATH} if not named",
    )
    arguments = parser.parse_args(argv)
    if unknown_races := [name for name in arguments.races if name not in RACES_BY_NAME and name != RIVALS]:
        raise SystemExit(f"No race is called {', '.join(unknown_races)}")
    installation = Installation.find()
    findings = {
        name: sweep_enemy_buffs(installation) if name == RIVALS else sweep_race(RACES_BY_NAME[name], installation)
        for name in arguments.races or [*RACES_BY_NAME, RIVALS]
    }
    arguments.out.write_text(json.dumps(findings, indent=2) + "\n", encoding="utf-8")
    logger.info("Wrote the findings to {}", arguments.out)


if __name__ == "__main__":
    main(sys.argv[1:])
