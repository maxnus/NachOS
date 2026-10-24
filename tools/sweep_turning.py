"""Measure how each unit type turns, and how far off its facing it fires.

Needs StarCraft II installed. Plays one game of two players on the sandbox's map, both played from here, so that no
computer moves either side's units, under `show_map` alone. The other player is cleared down to its townhalls, and every
trial stands on the widest square of open ground left near this player's start, 15 tiles across.

`turning` makes each type the tables give a speed, but for those nothing can order (a cocoon, a larva and an
interceptor), lets it settle, and orders it to move `_DESTINATION_DISTANCE` away at each of `_TURN_ANGLES_DEGREES` off
its facing, a new unit for each. It records the unit's facing and how far it has moved at every step for
`_TURN_WATCH_STEPS` steps: how far it turned at each step, the steps until it stopped turning, and the step it first
moved.

`firing` makes each type the tables give a weapon, stationary ones included, holding its position, and then an enemy at
each of `_SHOT_ANGLES_DEGREES` off its facing, within its reach, that does not fight back: an SCV for a weapon that hits
the ground and an overlord for one that hits the air. It orders the attack and records the attacker's facing at every
step until its first shot, which its cooldown rising or its target's loss shows: the steps to the shot, how far it
turned first and how far off its target it faced as it fired::

    uv run python tools/sweep_turning.py
    uv run python tools/sweep_turning.py turning --out turning.json
    uv run python tools/sweep_turning.py firing --types MARINE THOR --out firing.json

It writes `data/turning.json`, which `tools/generate_turning.py` turns into NachOS's tables, the build swept on and each
mode's findings by type, or for a mode or types named, the file `--out` names. A trial the game would not set up is
recorded as its error.
"""

import argparse
import json
import math
import sys
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

from _sandbox import Cheat, OpenGround, Rivals, playing_rivals
from loguru import logger
from s2clientprotocol import debug_pb2, raw_pb2

from sc2nachos.gamedata import GameData, TargetDomain
from sc2nachos.gamemap import GameMap
from sc2nachos.geometry import Point
from sc2nachos.ids import UnitTypeId
from sc2nachos.ids.raw import RawAbilityId
from sc2nachos.launch import Installation
from sc2nachos.match import Race
from sc2nachos.units._unit_type import UnitType

REPO = Path(__file__).parents[1]
OUTPUT = REPO / "data" / "turning.json"
MODES = ("turning", "firing")

# The widest free square near the start, once this player's units are marked on the open ground: 15 tiles across on
# PylonAIE_v4, its center 42 from the start (in game).
_TRIAL_GROUND_HALF_SIZE = 7
# The steps a unit made by debug command is waited for, and those it is given to settle once made.
_MAKING_STEPS = 8
_SETTLING_STEPS = 8
_REMOVAL_STEPS = 16
# How far off its facing, and how far away, a turning unit is ordered to move: every 2 degrees from 90 to 100 and every
# 10 from 100 to 170, so that a type that turns some 90 degrees a step shows where one step stops being enough.
_TURN_ANGLES_DEGREES = (45.0, 90.0, 92.0, 94.0, 96.0, 98.0, 100.0, 110.0, 120.0, 130.0, 140.0, 150.0, 160.0, 170.0)
_DESTINATION_DISTANCE = 6.0
# The steps a turning unit is watched: the slowest seen turned 170 degrees in 4 (in game).
_TURN_WATCH_STEPS = 40
# A turn this small in a step is no turn: a facing comes back in single precision.
_SMALLEST_TURN_DEGREES = 0.05
# A unit this far from where it stood has moved.
_SMALLEST_MOVE = 0.01
# How far off the attacker's facing each target is made: small angles to find how far off a unit that turns still
# fires, and wide ones to time the turn.
_SHOT_ANGLES_DEGREES = (0.0, 5.0, 10.0, 15.0, 20.0, 30.0, 45.0, 60.0, 90.0, 135.0, 180.0)
# The steps an attacker is given to fire: a planetary fortress at a target behind took 27 (in game).
_SHOT_WATCH_STEPS = 96
# A target stands this far inside the attacker's reach, the weapon's range and both radii, and never farther than the
# trial ground's half width allows; a melee attacker's stands this far from touching.
_INSIDE_REACH = 0.5
_FARTHEST_TARGET = 6.0
_MELEE_GAP = 0.1
_MELEE_RANGE = 1.0
# The types nothing can order to move: a cocoon or an egg carries its unit, a larva wanders, and an interceptor flies
# from its carrier.
_UNORDERABLE_MOVERS = frozenset(
    {
        UnitTypeId.BANELING_COCOON,
        UnitTypeId.BROOD_LORD_COCOON,
        UnitTypeId.INTERCEPTOR,
        UnitTypeId.LARVA,
        UnitTypeId.LURKER_EGG,
        UnitTypeId.OVERLORD_COCOON,
        UnitTypeId.OVERLORD_TRANSPORT_COCOON,
    }
)
# The types the tables give a weapon that never attack: a changeling disguised, and an interceptor, which only a carrier
# launches.
_NEVER_ATTACKING = frozenset(
    {
        UnitTypeId.CHANGELING_MARINE,
        UnitTypeId.CHANGELING_MARINE_SHIELD,
        UnitTypeId.CHANGELING_ZEALOT,
        UnitTypeId.CHANGELING_ZERGLING,
        UnitTypeId.CHANGELING_ZERGLING_WINGS,
        UnitTypeId.INTERCEPTOR,
    }
)
# The targets, by what the weapon hits, with their radii: neither fights back, held in place (corpus).
_GROUND_TARGET = UnitTypeId.SCV
_AIR_TARGET = UnitTypeId.OVERLORD
_TARGET_RADII = {_GROUND_TARGET: 0.375, _AIR_TARGET: 1.0}
# What an attacker is ordered before it can attack, with the energy that takes: an oracle turns its beam on.
_ORDERED_FIRST = {UnitTypeId.ORACLE: RawAbilityId.Behavior_PulsarBeamOn}
_ENERGY_VALUE = debug_pb2.DebugSetUnitValue.Energy
_FULL_ENERGY = 200.0
# A photon cannon fires only while a pylon powers it, made this far to its side.
_POWERED_BY_A_PYLON = frozenset({UnitTypeId.PHOTON_CANNON})
_PYLON_OFFSET = (-2.0, 0.0)


@dataclass(slots=True)
class TurnFinding:
    """One unit ordered to move off its facing: how it turned and when it moved."""

    ordered_off_facing_degrees: float
    start_facing: float
    turned_degrees: list[float] = field(default_factory=list)
    """How far it turned in each step after the order, counterclockwise positive."""
    steps_until_turned: int | None = None
    """The last step of the first steps after the order in which it turned, one after another, or None if it never
    turned. A unit turns a little more as it arrives, which this leaves out."""
    largest_turn_degrees: float | None = None
    """The most it turned in one of those steps."""
    steps_until_moving: int | None = None
    """The first step after the order in which it stood away from where it was, or None if it never did."""
    end_off_destination_degrees: float | None = None
    """How far its facing was off the bearing to its destination at the last step watched."""


@dataclass(slots=True)
class ShotFinding:
    """One attacker set upon a target made off its facing: when it fired and how it faced."""

    target: str
    asked_off_facing_degrees: float
    start_off_facing_degrees: float
    """How far off the attacker's facing the target stood as the attack was ordered."""
    distance: float
    steps_until_shot: int | None = None
    """The step after the order in which the attacker fired, or None if it did not within the watch."""
    turned_degrees_before_shot: float = 0.0
    """How far the attacker's facing moved from the order to the step it fired, counterclockwise positive."""
    off_facing_at_shot_degrees: float | None = None
    """How far off the attacker's facing the target stood in the step it fired."""


def _degrees_between(from_angle: float, to_angle: float) -> float:
    """The turn from `from_angle` to `to_angle`, both in radians, in degrees from -180 to 180, counterclockwise
    positive."""
    return math.degrees((to_angle - from_angle + math.pi) % math.tau - math.pi)


def _first_run_of_turns(turned_degrees: Sequence[float]) -> list[int]:
    """The indexes of the first steps of `turned_degrees` that turned, one after another: empty if none did."""
    run: list[int] = []
    for index, turned in enumerate(turned_degrees):
        if abs(turned) > _SMALLEST_TURN_DEGREES:
            run.append(index)
        elif run:
            break
    return run


def _position_of(unit: raw_pb2.Unit) -> Point:
    return Point((unit.pos.x, unit.pos.y))


def _bearing(from_point: Point, to_point: Point) -> float:
    """The direction from `from_point` to `to_point`, in radians from the positive x-axis."""
    return math.atan2(to_point.y - from_point.y, to_point.x - from_point.x)


def _point_toward(start: Point, direction: float, distance: float) -> Point:
    return Point((start.x + distance * math.cos(direction), start.y + distance * math.sin(direction)))


class TurningSweep:
    """The game a sweep plays, with the ground its trials stand on and the tables it reads."""

    def __init__(self, rivals: Rivals) -> None:
        """Turn on `show_map`, clear the other player down to its townhalls, and claim the trials' ground."""
        self._rivals = rivals
        self._me = rivals.me
        self._enemy_player = rivals.enemy.player
        self._me.cheat(Cheat.SHOW_MAP)
        rivals.step(_SETTLING_STEPS)
        self.base_build = self._me.client.ping().base_build
        self.data = GameData(self._me.client.game_data())
        townhall_types = UnitType.Townhall._type_ids
        self._remove(
            unit.tag
            for unit in self._me.units()
            if unit.owner == self._enemy_player and unit.unit_type not in townhall_types
        )
        game_map = GameMap._from_game(self._me.client.game_info(), self._me.client.observation())
        ground = OpenGround(game_map, self._me.units())
        self._ground_center = ground.claim(game_map.start_location, _TRIAL_GROUND_HALF_SIZE)

    def movers(self) -> list[UnitTypeId]:
        """Every type the tables give a speed and a race, but for those nothing can order to move, by name."""
        moving_types = (
            unit_type
            for unit_type, row in self.data.units.items()
            if row.speed > 0 and row.race is not Race.NONE and unit_type not in _UNORDERABLE_MOVERS
        )
        return sorted(moving_types, key=lambda unit_type: unit_type.name)

    def attackers(self) -> list[UnitTypeId]:
        """Every type the tables give a weapon and a race, but for those that never attack, by name."""
        attacking_types = (
            unit_type
            for unit_type, row in self.data.units.items()
            if row.weapons and row.race is not Race.NONE and unit_type not in _NEVER_ATTACKING
        )
        return sorted(attacking_types, key=lambda unit_type: unit_type.name)

    def targets_for(self, attacker_type: UnitTypeId) -> list[UnitTypeId]:
        """The targets `attacker_type`'s weapons hit between them: the ground one, the air one, or both."""
        domains = {weapon.target_domain for weapon in self.data.units[attacker_type].weapons}
        targets = []
        if domains & {TargetDomain.GROUND, TargetDomain.ANY}:
            targets.append(_GROUND_TARGET)
        if domains & {TargetDomain.AIR, TargetDomain.ANY}:
            targets.append(_AIR_TARGET)
        return targets

    def turn(self, unit_type: UnitTypeId, off_facing_degrees: float) -> TurnFinding:
        """Make a `unit_type`, order it to move `off_facing_degrees` off its facing, and watch it turn."""
        unit = self._make(unit_type, self._me.player, self._ground_center)
        try:
            self._rivals.step(_SETTLING_STEPS)
            unit = self._listed(unit.tag)
            start, start_facing = _position_of(unit), unit.facing
            destination = _point_toward(start, start_facing + math.radians(off_facing_degrees), _DESTINATION_DISTANCE)
            self._me.order(RawAbilityId.Move_Move, unit.tag, destination)
            finding = TurnFinding(off_facing_degrees, start_facing)
            watched = unit
            for step in range(1, _TURN_WATCH_STEPS + 1):
                self._rivals.step(1)
                previous_facing, watched = watched.facing, self._listed(unit.tag)
                turned = _degrees_between(previous_facing, watched.facing)
                finding.turned_degrees.append(round(turned, 3))
                if finding.steps_until_moving is None and _position_of(watched).distance_to(start) > _SMALLEST_MOVE:
                    finding.steps_until_moving = step
            first_turning_steps = _first_run_of_turns(finding.turned_degrees)
            if first_turning_steps:
                finding.steps_until_turned = first_turning_steps[-1] + 1
                finding.largest_turn_degrees = max(abs(finding.turned_degrees[index]) for index in first_turning_steps)
            last_bearing = _bearing(_position_of(watched), destination)
            finding.end_off_destination_degrees = round(_degrees_between(watched.facing, last_bearing), 3)
            return finding
        finally:
            self._remove([unit.tag])

    def shot(self, attacker_type: UnitTypeId, target_type: UnitTypeId, off_facing_degrees: float) -> ShotFinding:
        """Make a `attacker_type` holding its position and a `target_type` of the other player's `off_facing_degrees`
        off its facing, within its reach, order the attack and watch until the first shot."""
        made_tags: list[int] = []
        try:
            if attacker_type in _POWERED_BY_A_PYLON:
                pylon_at = Point((self._ground_center.x + _PYLON_OFFSET[0], self._ground_center.y + _PYLON_OFFSET[1]))
                made_tags.append(self._make(UnitTypeId.PYLON, self._me.player, pylon_at).tag)
            attacker = self._make(attacker_type, self._me.player, self._ground_center)
            made_tags.append(attacker.tag)
            self._me.order(RawAbilityId.HoldPosition_Hold, attacker.tag)
            if (first_order := _ORDERED_FIRST.get(attacker_type)) is not None:
                self._me.set_value(_ENERGY_VALUE, _FULL_ENERGY, [attacker.tag])
                self._rivals.step(1)
                self._me.order(first_order, attacker.tag)
            self._rivals.step(_SETTLING_STEPS)
            attacker = self._listed(attacker.tag)
            target_at = _point_toward(
                _position_of(attacker),
                attacker.facing + math.radians(off_facing_degrees),
                self._target_distance(attacker_type, attacker, target_type),
            )
            target = self._make(target_type, self._enemy_player, target_at)
            made_tags.append(target.tag)
            self._rivals.enemy.order(RawAbilityId.HoldPosition_Hold, target.tag)
            attacker, target = self._listed(attacker.tag), self._listed(target.tag)
            start_facing = attacker.facing
            finding = ShotFinding(
                target_type.name,
                off_facing_degrees,
                round(_degrees_between(start_facing, _bearing(_position_of(attacker), _position_of(target))), 3),
                round(_position_of(attacker).distance_to(_position_of(target)), 3),
            )
            self._me.order(RawAbilityId.Attack, attacker.tag, target.tag)
            target_life = target.health + target.shield
            for step in range(1, _SHOT_WATCH_STEPS + 1):
                self._rivals.step(1)
                listed = {unit.tag: unit for unit in self._me.units()}
                attacker_now, target_now = listed.get(attacker.tag), listed.get(target.tag)
                if attacker_now is None:
                    raise RuntimeError(f"the {attacker_type.name} was gone at step {step}")
                fired = (
                    attacker_now.weapon_cooldown > 0
                    or target_now is None
                    or target_now.health + target_now.shield < target_life
                )
                if fired:
                    finding.steps_until_shot = step
                    finding.turned_degrees_before_shot = round(_degrees_between(start_facing, attacker_now.facing), 3)
                    if target_now is not None:
                        bearing = _bearing(_position_of(attacker_now), _position_of(target_now))
                        finding.off_facing_at_shot_degrees = round(_degrees_between(attacker_now.facing, bearing), 3)
                    break
            return finding
        finally:
            self._remove(made_tags)

    def _target_distance(self, attacker_type: UnitTypeId, attacker: raw_pb2.Unit, target_type: UnitTypeId) -> float:
        """How far from the attacker its target is made: within the reach of its longest weapon that hits the target,
        or nearly touching for a melee one."""
        target_flies = target_type is _AIR_TARGET
        hitting = TargetDomain.AIR if target_flies else TargetDomain.GROUND
        ranges = [
            weapon.range
            for weapon in self.data.units[attacker_type].weapons
            if weapon.target_domain in (hitting, TargetDomain.ANY)
        ]
        both_radii = attacker.radius + _TARGET_RADII[target_type]
        weapon_range = max(ranges)
        if weapon_range < _MELEE_RANGE:
            return both_radii + _MELEE_GAP
        return min(weapon_range + both_radii - _INSIDE_REACH, _FARTHEST_TARGET)

    def _make(self, unit_type: UnitTypeId, owner: int, at: Point) -> raw_pb2.Unit:
        """Make one `unit_type` for `owner` at `at` and step until the game lists it."""
        tags_before = {unit.tag for unit in self._me.units()}
        self._me.debug(self._me.create(unit_type, owner, at))
        for _ in range(_MAKING_STEPS):
            self._rivals.step(1)
            made = [
                unit
                for unit in self._me.units()
                if unit.tag not in tags_before and unit.unit_type == unit_type and unit.display_type == raw_pb2.Visible
            ]
            if made:
                return made[0]
        raise RuntimeError(f"the game did not make a {unit_type.name}")

    def _listed(self, tag: int) -> raw_pb2.Unit:
        """The unit `tag` as a fresh observation lists it."""
        if (unit := next((unit for unit in self._me.units() if unit.tag == tag), None)) is None:
            raise RuntimeError(f"unit {tag} is no longer listed")
        return unit

    def _remove(self, tags: Iterable[int]) -> None:
        """Kill the units `tags` and step until the game lists none of them."""
        tags_to_remove = set(tags)
        if not tags_to_remove:
            return
        self._me.kill(tags_to_remove)
        for _ in range(_REMOVAL_STEPS):
            self._rivals.step(1)
            if not tags_to_remove & {unit.tag for unit in self._me.units()}:
                return
        logger.warning("{} units were not removed", len(tags_to_remove))


def sweep_turning(turning_sweep: TurningSweep, unit_types: Sequence[UnitTypeId]) -> dict[str, object]:
    """Each mover's turns, by type name."""
    turns: dict[str, object] = {}
    for unit_type in unit_types or turning_sweep.movers():
        try:
            type_turns = [turning_sweep.turn(unit_type, angle) for angle in _TURN_ANGLES_DEGREES]
        except RuntimeError as error:
            logger.warning("{}: {}", unit_type.name, error)
            turns[unit_type.name] = {"error": str(error)}
            continue
        turns[unit_type.name] = [asdict(turn) for turn in type_turns]
        logger.info("{} turned until: {}", unit_type.name, [turn.steps_until_turned for turn in type_turns])
    return turns


def sweep_firing(turning_sweep: TurningSweep, unit_types: Sequence[UnitTypeId]) -> dict[str, object]:
    """Each attacker's shots at targets off its facing, by type name."""
    shots: dict[str, object] = {}
    for attacker_type in unit_types or turning_sweep.attackers():
        attacker_shots: list[dict[str, object]] = []
        for target_type in turning_sweep.targets_for(attacker_type):
            for angle in _SHOT_ANGLES_DEGREES:
                try:
                    attacker_shots.append(asdict(turning_sweep.shot(attacker_type, target_type, angle)))
                except RuntimeError as error:
                    logger.warning("{} at a {} {} off: {}", attacker_type.name, target_type.name, angle, error)
                    failed_shot = {"target": target_type.name, "asked_off_facing_degrees": angle, "error": str(error)}
                    attacker_shots.append(failed_shot)
        shots[attacker_type.name] = attacker_shots
        logger.info("{} fired after: {}", attacker_type.name, [shot.get("steps_until_shot") for shot in attacker_shots])
    return shots


def main(argv: Sequence[str]) -> None:
    """Sweep the modes and types named, or every mode and type, and write the findings."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("modes", nargs="*", help=f"which to measure, of {', '.join(MODES)}; both when none")
    parser.add_argument(
        "--types", nargs="*", default=[], help="unit types to measure, as `UnitTypeId` names; every one"
    )
    parser.add_argument(
        "--out", type=Path, default=None, help=f"where to write the findings; {OUTPUT}, for a sweep of everything"
    )
    arguments = parser.parse_args(argv)
    if unknown_modes := [mode for mode in arguments.modes if mode not in MODES]:
        raise SystemExit(f"No mode is called {', '.join(unknown_modes)}")
    if unknown_names := [name for name in arguments.types if name not in UnitTypeId.__members__]:
        raise SystemExit(f"No unit type is called {', '.join(unknown_names)}")
    modes = arguments.modes or list(MODES)
    if (arguments.types or set(modes) != set(MODES)) and arguments.out is None:
        # A generator would make its tables of the types or the mode named alone.
        raise SystemExit(f"Name --out for a sweep of some modes or types, so that {OUTPUT} keeps everything")
    unit_types = [UnitTypeId[name] for name in arguments.types]
    with playing_rivals(Race.TERRAN, Installation.find()) as rivals:
        turning_sweep = TurningSweep(rivals)
        findings: dict[str, object] = {"base_build": turning_sweep.base_build}
        if "turning" in modes:
            findings["turning"] = sweep_turning(turning_sweep, unit_types)
        if "firing" in modes:
            findings["firing"] = sweep_firing(turning_sweep, unit_types)
    out_path = arguments.out or OUTPUT
    out_path.write_text(json.dumps(findings, indent=2) + "\n", encoding="utf-8")
    logger.info("Wrote the findings to {}", out_path)


if __name__ == "__main__":
    main(sys.argv[1:])
