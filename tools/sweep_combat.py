"""Measure what a unit's attack does: how far it reaches, what one hit takes off a target, what the units the tables
give no weapon do, and what speeds an attack up.

Needs StarCraft II installed. Each mode plays a game of two players on the sandbox's map, both played from here, so
that no computer moves either side's units; the other player is cleared down to its townhalls and the trials stand
by this player's start. This player turns on `show_map`, so it sees every unit.

`range`, under `god` too, so that its attackers take no damage, makes one attacker of this player's at a time,
holding its position, and then one target of the other player's at a distance from it, across or diagonally, a
structure of the two standing still and the other placed by it. It finds by halving the distance at which the
attacker still fires within `_FIRING_STEPS` steps, as its weapon's cooldown or its target's loss shows, and records
that distance between the centers beside the weapon's range and both radii. A sample the game moved stops the halving.

`damage` sets one attacker upon one target at a time, both holding, and records what the target loses every step:
with neither side upgraded, with the target's side given the `upgrade` cheat three times, the third level of every
weapon, armor and shield, and then with both sides so. Two trials set a zealot's shields to 2 first.

`no-weapon` does the same for the battlecruiser, the oracle, its beam turned on, the void ray and the sentry, against
a light target, an armored one and one that flies, before and after this player's upgrades.

`attack-speed`, under `god`, times the first shots of one attacker at a time at a command center of the other
player's, by the steps at which its cooldown rises, before this player's upgrades and after, a marine and a marauder
then stimmed too.

`shot-timing` times `_TIMED_SHOTS` shots of each of a few attackers at a command center healed as it goes, with no
upgrade and no cheat but `show_map`, and records the steps between shots and the cooldown each showed at its shot.

`cooldown-trace` reads the same attackers' cooldowns and the command center's health at every step over
`_TRACED_SHOTS` shots, and records how long the cooldown read 0 before each shot and when the target lost health::

    uv run python tools/sweep_combat.py range --out combat-range.json
    uv run python tools/sweep_combat.py damage --out combat-damage.json
    uv run python tools/sweep_combat.py no-weapon --out combat-no-weapon.json
    uv run python tools/sweep_combat.py attack-speed --out combat-attack-speed.json
    uv run python tools/sweep_combat.py shot-timing --out combat-shot-timing.json
    uv run python tools/sweep_combat.py cooldown-trace --out combat-cooldown-trace.json
"""

import argparse
import itertools
import json
import math
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from _sandbox import Cheat, OpenGround, Rivals, Sandbox, playing_rivals
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

# How many steps a unit is given to fire at a target placed within its reach: a planetary fortress turning its turret
# toward one took 22 (in game). And how many it is given to settle once made.
_FIRING_STEPS = 64
_SETTLING_STEPS = 4
_REMOVAL_STEPS = 8
_TOWNHALL_TYPES = UnitType.Townhall._type_ids
# Half the width of the open ground trials stand on: room for the longest reach searched, a sieged tank's 13 and more.
_OPEN_GROUND_HALF_WIDTH = 10
# How far from where it was asked for a unit may stand before its sample is set aside as displaced.
_LARGEST_DISPLACEMENT = 0.05
# How many times the distance between where an attacker fires and where it does not is halved.
_HALVINGS_AROUND_A_RANGE = 8
# How far either side of its range plus both radii the search for where an attacker stops firing begins, and, for an
# attacker the tables give no weapon, where it searches between instead, in more halvings.
_SEARCH_SPAN = 1.5
_NEAREST_SEARCHED = 0.5
_FARTHEST_SEARCHED = 12.0
_HALVINGS_WITHOUT_A_RANGE = 11
_ACROSS_DIRECTION = (1.0, 0.0)
_DIAGONAL_DIRECTION = (math.sqrt(0.5), math.sqrt(0.5))
# Each attacker against each target it is measured against, and the direction from the attacker to the target: units
# on the ground and in the air, a colossus, which python-sc2 has air weapons hit, structures as targets and as
# attackers, and an attacker that cannot move.
_RANGE_TRIALS = (
    (UnitTypeId.MARINE, UnitTypeId.SCV, _ACROSS_DIRECTION),
    (UnitTypeId.MARINE, UnitTypeId.SCV, _DIAGONAL_DIRECTION),
    (UnitTypeId.MARINE, UnitTypeId.OVERLORD, _ACROSS_DIRECTION),
    (UnitTypeId.MARINE, UnitTypeId.COLOSSUS, _ACROSS_DIRECTION),
    (UnitTypeId.ZEALOT, UnitTypeId.SCV, _ACROSS_DIRECTION),
    (UnitTypeId.VIKING, UnitTypeId.OVERLORD, _ACROSS_DIRECTION),
    (UnitTypeId.VIKING, UnitTypeId.COLOSSUS, _ACROSS_DIRECTION),
    (UnitTypeId.SIEGE_TANK, UnitTypeId.SCV, _ACROSS_DIRECTION),
    (UnitTypeId.SIEGE_TANK_SIEGED, UnitTypeId.SCV, _ACROSS_DIRECTION),
    (UnitTypeId.MARINE, UnitTypeId.SUPPLY_DEPOT, _ACROSS_DIRECTION),
    (UnitTypeId.MARINE, UnitTypeId.SUPPLY_DEPOT, _DIAGONAL_DIRECTION),
    (UnitTypeId.MARINE, UnitTypeId.BARRACKS, _ACROSS_DIRECTION),
    (UnitTypeId.MARINE, UnitTypeId.BARRACKS, _DIAGONAL_DIRECTION),
    (UnitTypeId.MARINE, UnitTypeId.COMMAND_CENTER, _ACROSS_DIRECTION),
    (UnitTypeId.MARINE, UnitTypeId.COMMAND_CENTER, _DIAGONAL_DIRECTION),
    (UnitTypeId.MISSILE_TURRET, UnitTypeId.OVERLORD, _ACROSS_DIRECTION),
    (UnitTypeId.MISSILE_TURRET, UnitTypeId.OVERLORD, _DIAGONAL_DIRECTION),
    (UnitTypeId.PLANETARY_FORTRESS, UnitTypeId.SCV, _ACROSS_DIRECTION),
    (UnitTypeId.PLANETARY_FORTRESS, UnitTypeId.SCV, _DIAGONAL_DIRECTION),
    (UnitTypeId.SPINE_CRAWLER, UnitTypeId.SCV, _ACROSS_DIRECTION),
    (UnitTypeId.BATTLECRUISER, UnitTypeId.SCV, _ACROSS_DIRECTION),
    (UnitTypeId.BATTLECRUISER, UnitTypeId.OVERLORD, _ACROSS_DIRECTION),
    (UnitTypeId.ORACLE, UnitTypeId.SCV, _ACROSS_DIRECTION),
    (UnitTypeId.VOID_RAY, UnitTypeId.SCV, _ACROSS_DIRECTION),
    (UnitTypeId.VOID_RAY, UnitTypeId.OVERLORD, _ACROSS_DIRECTION),
    (UnitTypeId.SENTRY, UnitTypeId.SCV, _ACROSS_DIRECTION),
    (UnitTypeId.SENTRY, UnitTypeId.OVERLORD, _ACROSS_DIRECTION),
)
# What an attacker is ordered before it can attack: an oracle turns its beam on, with the energy that takes.
_ORDERED_FIRST = {UnitTypeId.ORACLE: RawAbilityId.Behavior_PulsarBeamOn}


def _enemy_of(rivals: Rivals) -> int:
    """The other player's id."""
    return rivals.enemy.player


def _units_made(rivals: Rivals, requests: Sequence[tuple[UnitTypeId, int, Point]]) -> list[raw_pb2.Unit]:
    """Make the units requested by debug command, step both players until they settle, and return those made."""
    me = rivals.me
    before = {unit.tag for unit in me.units()}
    me.debug(*(me.create(unit_type, owner, at) for unit_type, owner, at in requests))
    rivals.step(_SETTLING_STEPS)
    wanted = {(unit_type, owner) for unit_type, owner, _ in requests}
    return [unit for unit in me.units() if unit.tag not in before and (unit.unit_type, unit.owner) in wanted]


def _kill_and_wait(rivals: Rivals, tags: Sequence[int]) -> None:
    """Kill the units under `tags` and step both players until the game reports none of them."""
    me = rivals.me
    me.kill(tags)
    for _ in range(_REMOVAL_STEPS):
        rivals.step(1)
        if not set(tags) & {unit.tag for unit in me.units()}:
            return
    logger.warning("{} units were not removed", len(tags))


def _trial_ground(rivals: Rivals) -> Point:
    """Clear the other player down to its townhalls, which keep it in the game, and return the center of open ground
    by this player's start, far from them, for the trials to stand on: no attacker sees an enemy but its target."""
    me = rivals.me
    enemy = _enemy_of(rivals)
    units = me.units()
    _kill_and_wait(
        rivals, [unit.tag for unit in units if unit.owner == enemy and unit.unit_type not in _TOWNHALL_TYPES]
    )
    game_map = GameMap._from_game(me.client.game_info(), me.client.observation())
    return OpenGround(game_map, me.units()).claim(game_map.start_location, _OPEN_GROUND_HALF_WIDTH)


def _unit_tagged(game: Sandbox, tag: int) -> raw_pb2.Unit | None:
    return next((unit for unit in game.units() if unit.tag == tag), None)


@dataclass(slots=True)
class FiringSample:
    """One placing of a target: the distance between the centers, whether the attacker fired, and whether the game
    made the unit placed beside the other more than `_LARGEST_DISPLACEMENT` from where it was asked for, as it does
    where something stands in the way."""

    distance: float
    fired: bool
    displaced: bool


@dataclass(slots=True)
class RangeFinding:
    """How far one attacker fired at one target in one direction, beside its weapon's range and both radii."""

    attacker: str
    target: str
    direction: tuple[float, float]
    weapon_range: float | None
    """The range of the attacker's weapon that the tables give it against the target's domain, or None if none does."""
    attacker_radius: float
    target_radius: float
    farthest_fired: float | None
    """The longest distance between the centers at which it fired, or None if it never did."""
    nearest_not_fired: float | None
    """The shortest distance between the centers, beyond the farthest it fired at, at which it did not."""
    samples: list[FiringSample] = field(default_factory=list)
    inconclusive: bool = False
    """Whether the halving stopped at a displaced sample, so the two distances may not bracket the reach."""


def _weapon_range(game_data: GameData, attacker: UnitTypeId, *, flying: bool) -> float | None:
    """The range of the attacker's weapon the tables give it against a target that flies or does not, or None."""
    row = game_data.units[attacker]
    wanted = TargetDomain.AIR if flying else TargetDomain.GROUND
    ranges = [weapon.range for weapon in row.weapons if weapon.target_domain in (wanted, TargetDomain.ANY)]
    return max(ranges) if ranges else None


def _fixed_center(game_data: GameData, unit_type: UnitTypeId, at: Point) -> Point:
    """Where a unit of `unit_type` made at `at` stands: a structure's center snaps to its footprint's grid."""
    footprint = game_data.units[unit_type].footprint
    return at if footprint is None else footprint.snapped_center(at=at)


def _fires_at(
    rivals: Rivals,
    game_data: GameData,
    trial: tuple[UnitTypeId, UnitTypeId, tuple[float, float]],
    at: Point,
    offset: float,
) -> tuple[FiringSample, float, float, bool]:
    """Make an attacker holding its position and a target `offset` from it in the trial's direction, whichever is a
    structure standing at `at`; whether the attacker fired within `_FIRING_STEPS` steps, at the distance the two stood
    apart, both radii, and whether the target flew. The attacker is made first, so it holds before it sees its
    target."""
    attacker_type, target_type, (dx, dy) = trial
    me = rivals.me
    attacker_moves = (
        game_data.units[target_type].footprint is not None and game_data.units[attacker_type].footprint is None
    )
    if attacker_moves:
        target_at = _fixed_center(game_data, target_type, at)
        attacker_at = Point((target_at.x - offset * dx, target_at.y - offset * dy))
    else:
        attacker_at = _fixed_center(game_data, attacker_type, at)
        target_at = Point((attacker_at.x + offset * dx, attacker_at.y + offset * dy))
    (attacker,) = _units_made(rivals, [(attacker_type, me.player, attacker_at)])
    me.order(RawAbilityId.HoldPosition_Hold, attacker.tag)
    if (first := _ORDERED_FIRST.get(attacker_type)) is not None:
        me.set_value(_ENERGY_VALUE, 200, [attacker.tag])
        rivals.step(1)
        me.order(first, attacker.tag)
        rivals.step(_SETTLING_STEPS)
    made = _units_made(rivals, [(target_type, _enemy_of(rivals), target_at)])
    if not made:
        _kill_and_wait(rivals, [attacker.tag])
        raise RuntimeError(f"the game did not make a {target_type.name} at {target_at}")
    (target,) = made
    distance = Point((attacker.pos.x, attacker.pos.y)).distance_to((target.pos.x, target.pos.y))
    placed, asked = (attacker, attacker_at) if attacker_moves else (target, target_at)
    displaced = asked.distance_to((placed.pos.x, placed.pos.y)) > _LARGEST_DISPLACEMENT
    fired = False
    for _ in range(_FIRING_STEPS):
        rivals.step(1)
        now = _unit_tagged(me, attacker.tag)
        hit = _unit_tagged(me, target.tag)
        # An attacker the tables give no weapon shows no cooldown: what its target loses shows its shots.
        if (
            (now is not None and now.weapon_cooldown > 0)
            or hit is None
            or hit.health + hit.shield < (target.health + target.shield)
        ):
            fired = True
            break
    _kill_and_wait(rivals, [attacker.tag, target.tag])
    return FiringSample(distance, fired, displaced), attacker.radius, target.radius, target.is_flying


def _measured_range(
    rivals: Rivals, game_data: GameData, trial: tuple[UnitTypeId, UnitTypeId, tuple[float, float]], at: Point
) -> RangeFinding:
    """The distance at which the trial's attacker stops firing at its target, found by halving."""
    attacker_type, target_type, direction = trial
    first, attacker_radius, target_radius, flying = _fires_at(rivals, game_data, trial, at, 1.5)
    # A colossus, on the ground, is measured against an air weapon where the attacker has no other.
    weapon_range = _weapon_range(game_data, attacker_type, flying=flying)
    searched_range = weapon_range if weapon_range is not None else _weapon_range(game_data, attacker_type, flying=True)
    reach = (searched_range or 0.0) + attacker_radius + target_radius
    finding = RangeFinding(
        attacker_type.name,
        target_type.name,
        direction,
        weapon_range,
        attacker_radius,
        target_radius,
        None,
        None,
        [first],
    )
    if searched_range is None:
        near, far, halvings = _NEAREST_SEARCHED, _FARTHEST_SEARCHED, _HALVINGS_WITHOUT_A_RANGE
    else:
        near, far, halvings = (
            max(reach - _SEARCH_SPAN, _NEAREST_SEARCHED),
            reach + _SEARCH_SPAN,
            _HALVINGS_AROUND_A_RANGE,
        )
    for _ in range(halvings):
        offset = (near + far) / 2
        sample, _, _, _ = _fires_at(rivals, game_data, trial, at, offset)
        finding.samples.append(sample)
        if sample.displaced:
            finding.inconclusive = True
            break
        if sample.fired:
            near = offset
        else:
            far = offset
    placed = [sample for sample in finding.samples if not sample.displaced]
    fired = [sample.distance for sample in placed if sample.fired]
    finding.farthest_fired = max(fired) if fired else None
    beyond = [
        sample.distance
        for sample in placed
        if not sample.fired and (finding.farthest_fired is None or sample.distance > finding.farthest_fired)
    ]
    finding.nearest_not_fired = min(beyond) if beyond else None
    logger.info(
        "{} at {} {}: fired out to {}, not at {}; range {} + radii {} + {} = {}",
        attacker_type.name,
        target_type.name,
        "across" if direction == _ACROSS_DIRECTION else "diagonally",
        finding.farthest_fired,
        finding.nearest_not_fired,
        finding.weapon_range,
        attacker_radius,
        target_radius,
        reach,
    )
    return finding


def measure_ranges(installation: Installation) -> list[RangeFinding]:
    """Measure every trial of `_RANGE_TRIALS`, in one game."""
    findings = []
    with playing_rivals(Race.TERRAN, installation) as rivals:
        me = rivals.me
        me.cheat(Cheat.SHOW_MAP, Cheat.GOD)
        rivals.step(_SETTLING_STEPS)
        game_data = GameData(me.client.game_data())
        at = _trial_ground(rivals)
        for trial in _RANGE_TRIALS:
            findings.append(_measured_range(rivals, game_data, trial, at))
    return findings


_ATTACK_STEPS = 96
# Within how many steps of an attack's first hit the rest of its hits land, for a weapon of several: a zealot's second
# blade lands 4 steps after the first, and a colossus's two beams together, in one loss. A weapon of one hit, or an
# attacker the tables give none, such as a battlecruiser firing every 3.6 steps, has its first loss as its attack.
_ONE_ATTACK_STEPS = 8
# How many steps the target's shields are given to read what a debug command set them to.
_SHIELDS_SETTLING_STEPS = 8
_TRIAL_DISTANCE = 3.0
# How far from a melee attacker its target is made: too close for both, so the game pushes them apart to touching.
_MELEE_TRIAL_DISTANCE = 0.5
_UPGRADE_SENDS = 3
_SHIELDS_VALUE = debug_pb2.DebugSetUnitValue.Shields
_ENERGY_VALUE = debug_pb2.DebugSetUnitValue.Energy
_LIFE_VALUE = debug_pb2.DebugSetUnitValue.Life


@dataclass(slots=True)
class Loss:
    """What a target lost at one step."""

    step: int
    health: float
    shields: float


@dataclass(slots=True)
class AttackFinding:
    """What one attacker took off one target, step by step, beside what the tables and the two units report."""

    attacker: str
    target: str
    upgrades: str
    """Which side had the `upgrade` cheat's three sends: `neither`, `target`, `attacker` or `both`."""
    weapons: list[dict[str, Any]]
    """The attacker's weapons as this player's tables give them, upgrades included."""
    target_armor: float
    """The armor the tables gave the target's type before either side had an upgrade."""
    target_armor_upgrade: int
    target_shield_upgrade: int
    target_attributes: list[str]
    shields_at_start: float
    losses: list[Loss] = field(default_factory=list)
    first_attack: Loss | None = None
    """What the first attack took off in all, its hits within `_ONE_ATTACK_STEPS` of the first."""


def _is_melee(game_data: GameData, unit_type: UnitTypeId) -> bool:
    """Whether every weapon the tables give `unit_type` reaches less than 1, as a melee one does."""
    weapons = game_data.units[unit_type].weapons
    return bool(weapons) and max(weapon.range for weapon in weapons) < 1


def _hold_position(game: Sandbox, tag: int) -> None:
    game.order(RawAbilityId.HoldPosition_Hold, tag)


def _set_upon(
    rivals: Rivals,
    attacker_type: UnitTypeId,
    target_type: UnitTypeId,
    at: Point,
    *,
    shields: float | None = None,
    before: Sequence[tuple[int, int | None]] = (),
) -> tuple[raw_pb2.Unit, raw_pb2.Unit, list[Loss]]:
    """Make an attacker of this player's at `at` and a target of the other's across, `_TRIAL_DISTANCE` from it or, from
    a melee attacker, `_MELEE_TRIAL_DISTANCE`, both holding, the target's shields set to `shields` if given and the
    abilities `before` ordered on the attacker first, each with the energy it needs; and what the target lost at each
    step for `_ATTACK_STEPS` steps."""
    me, enemy = rivals.me, rivals.enemy
    melee = _is_melee(GameData(me.client.game_data()), attacker_type)
    distance = _MELEE_TRIAL_DISTANCE if melee else _TRIAL_DISTANCE
    (attacker,) = _units_made(rivals, [(attacker_type, me.player, at)])
    _hold_position(me, attacker.tag)
    if before:
        me.set_value(_ENERGY_VALUE, 200, [attacker.tag])
        rivals.step(1)
        for ability, target_tag in before:
            me.order(ability, attacker.tag, target_tag)
        rivals.step(_SETTLING_STEPS)
    (target,) = _units_made(rivals, [(target_type, _enemy_of(rivals), Point((at.x + distance, at.y)))])
    _hold_position(enemy, target.tag)
    last = (target.health, target.shield)
    if shields is not None:
        me.set_value(_SHIELDS_VALUE, shields, [target.tag])
        for _ in range(_SHIELDS_SETTLING_STEPS):
            rivals.step(1)
            if (settled := _unit_tagged(me, target.tag)) is not None and settled.shield <= shields + 1:
                last = (settled.health, settled.shield)
                break
    losses = []
    for step in range(1, _ATTACK_STEPS + 1):
        rivals.step(1)
        now = _unit_tagged(me, target.tag)
        if now is None:
            losses.append(Loss(step, last[0], last[1]))
            break
        health_lost, shields_lost = last[0] - now.health, last[1] - now.shield
        if health_lost > 0 or shields_lost > 0:
            losses.append(Loss(step, health_lost, shields_lost))
        last = (now.health, now.shield)
    _kill_and_wait(rivals, [attacker.tag, target.tag])
    return attacker, target, losses


def _first_attack(losses: Sequence[Loss], attacks: int) -> Loss | None:
    """The losses of the first attack together: for a weapon of several `attacks`, its first hit and those within
    `_ONE_ATTACK_STEPS` of it, and otherwise the first loss alone."""
    if not losses:
        return None
    if attacks <= 1:
        return losses[0]
    first = losses[0].step
    hits = [loss for loss in losses if loss.step - first <= _ONE_ATTACK_STEPS]
    return Loss(first, sum(hit.health for hit in hits), sum(hit.shields for hit in hits))


def _weapons_of(game_data: GameData, unit_type: UnitTypeId) -> list[dict[str, Any]]:
    return [
        {
            "target_domain": weapon.target_domain.name,
            "damage": weapon.damage,
            "attacks": weapon.attacks,
            "range": weapon.range,
            "cooldown_steps": weapon.cooldown_steps,
            "damage_bonuses": {attribute.name: bonus for attribute, bonus in weapon.damage_bonuses.items()},
        }
        for weapon in game_data.units[unit_type].weapons
    ]


def _attack_finding(
    rivals: Rivals,
    base_tables: GameData,
    attacker_type: UnitTypeId,
    target_type: UnitTypeId,
    at: Point,
    upgrades: str,
    *,
    shields: float | None = None,
    before: Sequence[tuple[int, int | None]] = (),
) -> AttackFinding:
    """Set one attacker upon one target and record what it took off, beside the attacker's weapons as this player's
    tables give them now and the target's armor as `base_tables`, read before either side had an upgrade, give it."""
    game_data = GameData(rivals.me.client.game_data())
    _, target, losses = _set_upon(rivals, attacker_type, target_type, at, shields=shields, before=before)
    row = base_tables.units[target_type]
    weapons = game_data.units[attacker_type].weapons
    finding = AttackFinding(
        attacker_type.name,
        target_type.name,
        upgrades,
        _weapons_of(game_data, attacker_type),
        row.armor,
        target.armor_upgrade_level,
        target.shield_upgrade_level,
        sorted(attribute.name for attribute in row.attributes),
        shields if shields is not None else target.shield,
        losses,
        _first_attack(losses, max((weapon.attacks for weapon in weapons), default=1)),
    )
    logger.info(
        "{} on {} ({} upgraded): first attack {}, every loss {}",
        attacker_type.name,
        target_type.name,
        upgrades,
        finding.first_attack,
        [(loss.step, loss.health, loss.shields) for loss in losses[:6]],
    )
    return finding


# Each attacker against each target: armor, a bonus, shields, several hits an attack, and the 0.5 a hit takes off at
# least, against an ultralisk's armor. The last two set the zealot's shields to 2, so a hit spills over to its health.
_DAMAGE_TRIALS: tuple[tuple[UnitTypeId, UnitTypeId, float | None], ...] = (
    (UnitTypeId.MARINE, UnitTypeId.SCV, None),
    (UnitTypeId.MARINE, UnitTypeId.ZERGLING, None),
    (UnitTypeId.MARINE, UnitTypeId.ROACH, None),
    (UnitTypeId.MARINE, UnitTypeId.ULTRALISK, None),
    (UnitTypeId.MARINE, UnitTypeId.ZEALOT, None),
    (UnitTypeId.MARAUDER, UnitTypeId.ROACH, None),
    (UnitTypeId.MARAUDER, UnitTypeId.ZERGLING, None),
    (UnitTypeId.MARAUDER, UnitTypeId.STALKER, None),
    (UnitTypeId.ZEALOT, UnitTypeId.SCV, None),
    (UnitTypeId.ZEALOT, UnitTypeId.ROACH, None),
    (UnitTypeId.COLOSSUS, UnitTypeId.ZERGLING, None),
    (UnitTypeId.MARINE, UnitTypeId.ZEALOT, 2.0),
    (UnitTypeId.ZEALOT, UnitTypeId.ZEALOT, 2.0),
)


def _send_upgrade_levels(game: Sandbox, rivals: Rivals) -> None:
    """Send the `upgrade` cheat from `game` `_UPGRADE_SENDS` times, the third level of every weapon, armor and
    shield, and let it take effect."""
    for _ in range(_UPGRADE_SENDS):
        game.cheat(Cheat.UPGRADE)
        rivals.step(_SETTLING_STEPS * 4)


def measure_damage(installation: Installation) -> list[AttackFinding]:
    """Every trial of `_DAMAGE_TRIALS` with neither side upgraded, then with the target's side upgraded, then both."""
    findings = []
    with playing_rivals(Race.TERRAN, installation) as rivals:
        me = rivals.me
        me.cheat(Cheat.SHOW_MAP)
        rivals.step(_SETTLING_STEPS)
        at = _trial_ground(rivals)
        base_tables = GameData(me.client.game_data())
        for upgrades, upgrading in (("neither", None), ("target", rivals.enemy), ("both", rivals.me)):
            if upgrading is not None:
                _send_upgrade_levels(upgrading, rivals)
            for attacker_type, target_type, shields in _DAMAGE_TRIALS:
                findings.append(
                    _attack_finding(rivals, base_tables, attacker_type, target_type, at, upgrades, shields=shields)
                )
    return findings


# The attackers the tables give no weapon, each against a light target and an armored one, on the ground or in the
# air, and what each is ordered first: an oracle turns its beam on.
_NO_WEAPON_TRIALS: tuple[tuple[UnitTypeId, UnitTypeId, tuple[int, ...]], ...] = (
    (UnitTypeId.BATTLECRUISER, UnitTypeId.SCV, ()),
    (UnitTypeId.BATTLECRUISER, UnitTypeId.ROACH, ()),
    (UnitTypeId.BATTLECRUISER, UnitTypeId.OVERLORD, ()),
    (UnitTypeId.ORACLE, UnitTypeId.SCV, (RawAbilityId.Behavior_PulsarBeamOn,)),
    (UnitTypeId.ORACLE, UnitTypeId.ROACH, (RawAbilityId.Behavior_PulsarBeamOn,)),
    (UnitTypeId.VOID_RAY, UnitTypeId.SCV, ()),
    (UnitTypeId.VOID_RAY, UnitTypeId.ROACH, ()),
    (UnitTypeId.VOID_RAY, UnitTypeId.OVERLORD, ()),
    (UnitTypeId.SENTRY, UnitTypeId.SCV, ()),
    (UnitTypeId.SENTRY, UnitTypeId.ROACH, ()),
    (UnitTypeId.SENTRY, UnitTypeId.OVERLORD, ()),
)


def measure_no_weapon(installation: Installation) -> list[AttackFinding]:
    """Every trial of `_NO_WEAPON_TRIALS`, with neither side upgraded and then with the attacker's side upgraded."""
    findings = []
    with playing_rivals(Race.TERRAN, installation) as rivals:
        me = rivals.me
        me.cheat(Cheat.SHOW_MAP)
        rivals.step(_SETTLING_STEPS)
        at = _trial_ground(rivals)
        base_tables = GameData(me.client.game_data())
        for upgrades in ("neither", "attacker"):
            if upgrades == "attacker":
                _send_upgrade_levels(me, rivals)
            for attacker_type, target_type, abilities in _NO_WEAPON_TRIALS:
                before = [(ability, None) for ability in abilities]
                findings.append(
                    _attack_finding(rivals, base_tables, attacker_type, target_type, at, upgrades, before=before)
                )
    return findings


_SHOTS_TIMED = 6
_ATTACK_SPEED_STEPS = 320
# How far from the command center an attacker stands: by its edge for a melee one, which the game pushes to touching,
# and well within reach for the rest.
_MELEE_FROM_COMMAND_CENTER = 3.0
_RANGED_FROM_COMMAND_CENTER = 4.0


@dataclass(slots=True)
class AttackIntervalFinding:
    """The steps between an attacker's shots at a command center, beside its tables' cooldown."""

    attacker: str
    upgraded: bool
    stimmed: bool
    buffs: list[int]
    """The buffs it had at its first shot."""
    table_cooldown_steps: float | None
    shot_steps: list[int]
    """The steps at which its cooldown rose: each shot."""
    mean_interval: float | None
    """The mean of the steps between its shots, or None if it fired fewer than twice."""


def _by_a_command_center(
    rivals: Rivals, game_data: GameData, attacker_type: UnitTypeId, at: Point
) -> tuple[raw_pb2.Unit, raw_pb2.Unit]:
    """Make a command center of the other player's at `at` and an attacker of this player's to its left, by its edge
    if the attacker is melee and well within reach if not, and return the attacker and the command center."""
    (target,) = _units_made(rivals, [(UnitTypeId.COMMAND_CENTER, _enemy_of(rivals), at)])
    away = _MELEE_FROM_COMMAND_CENTER if _is_melee(game_data, attacker_type) else _RANGED_FROM_COMMAND_CENTER
    (attacker,) = _units_made(rivals, [(attacker_type, rivals.me.player, Point((target.pos.x - away, target.pos.y)))])
    return attacker, target


def _attack_interval(
    rivals: Rivals, attacker_type: UnitTypeId, at: Point, *, upgraded: bool, stim: int | None
) -> AttackIntervalFinding:
    """Make a command center of the other player's at `at` and an attacker of this player's beside it, stimmed if
    `stim` is its stim ability, and time its first `_SHOTS_TIMED` shots by the steps at which its cooldown rises."""
    me = rivals.me
    game_data = GameData(me.client.game_data())
    weapons = game_data.units[attacker_type].weapons
    attacker, target = _by_a_command_center(rivals, game_data, attacker_type, at)
    if stim is not None:
        me.order(stim, attacker.tag)
        rivals.step(1)
    me.order(RawAbilityId.Attack, attacker.tag, target.tag)
    shots: list[int] = []
    buffs: list[int] = []
    last = 0.0
    for step in range(_ATTACK_SPEED_STEPS):
        rivals.step(1)
        now = _unit_tagged(me, attacker.tag)
        if now is None:
            break
        if now.weapon_cooldown > last:
            if not shots:
                buffs = list(now.buff_ids)
            shots.append(step)
            if len(shots) > _SHOTS_TIMED:
                break
        last = now.weapon_cooldown
    _kill_and_wait(rivals, [attacker.tag, target.tag])
    intervals = [later - earlier for earlier, later in itertools.pairwise(shots)]
    finding = AttackIntervalFinding(
        attacker_type.name,
        upgraded,
        stim is not None,
        buffs,
        weapons[0].cooldown_steps if weapons else None,
        shots,
        sum(intervals) / len(intervals) if intervals else None,
    )
    logger.info("{}: {}", attacker_type.name, finding)
    return finding


_ATTACK_SPEED_TRIALS: tuple[tuple[UnitTypeId, int | None], ...] = (
    (UnitTypeId.ZERGLING, None),
    (UnitTypeId.ADEPT, None),
    (UnitTypeId.MARINE, None),
    (UnitTypeId.MARAUDER, None),
    (UnitTypeId.MARINE, RawAbilityId.Effect_Stim_Marine),
    (UnitTypeId.MARAUDER, RawAbilityId.Effect_Stim_Marauder),
)


def measure_attack_speed(installation: Installation) -> list[AttackIntervalFinding]:
    """Every trial of `_ATTACK_SPEED_TRIALS` before this player has any upgrade and after the `upgrade` cheat, which
    gives it Adrenal Glands, Resonating Glaives and Stimpack among the rest. Its units fire at a command center."""
    findings = []
    with playing_rivals(Race.TERRAN, installation) as rivals:
        me = rivals.me
        me.cheat(Cheat.SHOW_MAP, Cheat.GOD)
        rivals.step(_SETTLING_STEPS)
        at = _trial_ground(rivals)
        for upgraded in (False, True):
            if upgraded:
                _send_upgrade_levels(me, rivals)
            for attacker_type, stim in _ATTACK_SPEED_TRIALS:
                if stim is not None and not upgraded:
                    continue
                findings.append(_attack_interval(rivals, attacker_type, at, upgraded=upgraded, stim=stim))
    return findings


_TIMED_SHOTS = 200
# How many steps an attacker is given per shot timed, at the slowest cooldown timed, a roach's or a stalker's, and more.
_STEPS_PER_TIMED_SHOT = 48
# How often the command center fired at is healed back to its full life, so that no attacker kills it.
_HEALING_EVERY_STEPS = 32
_SHOT_TIMING_TRIALS = (
    UnitTypeId.MARINE,
    UnitTypeId.MARAUDER,
    UnitTypeId.ZERGLING,
    UnitTypeId.ZEALOT,
    UnitTypeId.STALKER,
    UnitTypeId.ROACH,
    UnitTypeId.HYDRALISK,
    UnitTypeId.SIEGE_TANK,
)


@dataclass(slots=True)
class ShotTimingFinding:
    """The steps between an attacker's shots over many, and the cooldown it showed at each, beside its tables'."""

    attacker: str
    table_cooldown_steps: float | None
    shot_steps: list[int]
    """The steps at which its cooldown rose: each shot."""
    cooldowns_after_shots: list[float]
    """The cooldown it showed at each of those steps."""
    intervals: dict[int, int]
    """How many times each number of steps came between two shots."""
    mean_interval: float | None


def _shot_timing(rivals: Rivals, attacker_type: UnitTypeId, at: Point) -> ShotTimingFinding:
    """Make a command center of the other player's at `at`, healed every `_HEALING_EVERY_STEPS`, and an attacker of
    this player's beside it, and time `_TIMED_SHOTS` of its shots by the steps at which its cooldown rises."""
    me = rivals.me
    game_data = GameData(me.client.game_data())
    weapons = game_data.units[attacker_type].weapons
    attacker, target = _by_a_command_center(rivals, game_data, attacker_type, at)
    me.order(RawAbilityId.Attack, attacker.tag, target.tag)
    shots: list[int] = []
    cooldowns: list[float] = []
    last = 0.0
    for step in range(_TIMED_SHOTS * _STEPS_PER_TIMED_SHOT):
        if step % _HEALING_EVERY_STEPS == 0:
            me.set_value(_LIFE_VALUE, target.health_max, [target.tag])
        rivals.step(1)
        now = _unit_tagged(me, attacker.tag)
        if now is None:
            break
        if now.weapon_cooldown > last:
            shots.append(step)
            cooldowns.append(now.weapon_cooldown)
            if len(shots) > _TIMED_SHOTS:
                break
        last = now.weapon_cooldown
    _kill_and_wait(rivals, [attacker.tag, target.tag])
    between = [later - earlier for earlier, later in itertools.pairwise(shots)]
    finding = ShotTimingFinding(
        attacker_type.name,
        weapons[0].cooldown_steps if weapons else None,
        shots,
        cooldowns,
        dict(sorted(Counter(between).items())),
        sum(between) / len(between) if between else None,
    )
    logger.info(
        "{}: {} shots, table {}, mean interval {}, intervals {}, cooldown after a shot {} to {}",
        attacker_type.name,
        len(shots),
        finding.table_cooldown_steps,
        finding.mean_interval,
        finding.intervals,
        min(cooldowns, default=None),
        max(cooldowns, default=None),
    )
    return finding


def measure_shot_timing(installation: Installation) -> list[ShotTimingFinding]:
    """Every trial of `_SHOT_TIMING_TRIALS`, with neither side upgraded and no cheat but `show_map`."""
    findings = []
    with playing_rivals(Race.TERRAN, installation) as rivals:
        me = rivals.me
        me.cheat(Cheat.SHOW_MAP)
        rivals.step(_SETTLING_STEPS)
        at = _trial_ground(rivals)
        for attacker_type in _SHOT_TIMING_TRIALS:
            findings.append(_shot_timing(rivals, attacker_type, at))
    return findings


# Few enough shots that no attacker kills the command center, which is never healed, so its losses read clean.
_TRACED_SHOTS = 10


@dataclass(slots=True)
class CooldownReading:
    """What one step showed: the attacker's weapon cooldown and its target's health."""

    step: int
    weapon_cooldown: float
    target_health: float


@dataclass(slots=True)
class CooldownTraceFinding:
    """An attacker's weapon cooldown and its target's health at every step over a few shots, beside its tables'
    cooldown."""

    attacker: str
    table_cooldown_steps: float | None
    readings: list[CooldownReading]
    shot_steps: list[int]
    """The steps at which its cooldown rose: each shot."""
    loss_steps: list[int]
    """The steps at which the target lost health."""
    steps_at_zero_before_shots: list[int]
    """For each shot after the first, how many steps in a row before it the cooldown read 0."""
    losses_after_nearest_shot: list[int]
    """For each loss, how many steps after the shot nearest it it came: negative if before."""


def _cooldown_trace(rivals: Rivals, attacker_type: UnitTypeId, at: Point) -> CooldownTraceFinding:
    """Make a command center of the other player's at `at` and an attacker of this player's beside it, and read the
    attacker's cooldown and the command center's health at every step until `_TRACED_SHOTS` shots and the rise of the
    next."""
    me = rivals.me
    game_data = GameData(me.client.game_data())
    weapons = game_data.units[attacker_type].weapons
    attacker, target = _by_a_command_center(rivals, game_data, attacker_type, at)
    me.order(RawAbilityId.Attack, attacker.tag, target.tag)
    readings: list[CooldownReading] = []
    shots: list[int] = []
    for step in range(_TRACED_SHOTS * _STEPS_PER_TIMED_SHOT):
        rivals.step(1)
        now, struck = _unit_tagged(me, attacker.tag), _unit_tagged(me, target.tag)
        if now is None or struck is None:
            break
        if readings and now.weapon_cooldown > readings[-1].weapon_cooldown:
            shots.append(step)
        readings.append(CooldownReading(step, now.weapon_cooldown, struck.health))
        if len(shots) > _TRACED_SHOTS:
            break
    _kill_and_wait(rivals, [attacker.tag, target.tag])
    losses = [
        later.step for earlier, later in itertools.pairwise(readings) if later.target_health < earlier.target_health
    ]
    at_zero = []
    for shot in shots[1:]:
        first_at_zero = shot
        while first_at_zero > 0 and readings[first_at_zero - 1].weapon_cooldown == 0:
            first_at_zero -= 1
        at_zero.append(shot - first_at_zero)
    finding = CooldownTraceFinding(
        attacker_type.name,
        weapons[0].cooldown_steps if weapons else None,
        readings,
        shots,
        losses,
        at_zero,
        [loss - min(shots, key=lambda shot: abs(loss - shot)) for loss in losses] if shots else [],
    )
    logger.info(
        "{}: {} shots, table {}, steps at 0 before a shot {}, losses after the nearest shot {}",
        attacker_type.name,
        len(shots),
        finding.table_cooldown_steps,
        at_zero,
        finding.losses_after_nearest_shot,
    )
    return finding


def measure_cooldown_trace(installation: Installation) -> list[CooldownTraceFinding]:
    """Every trial of `_SHOT_TIMING_TRIALS`, traced step by step, with neither side upgraded and no cheat but
    `show_map`."""
    findings = []
    with playing_rivals(Race.TERRAN, installation) as rivals:
        me = rivals.me
        me.cheat(Cheat.SHOW_MAP)
        rivals.step(_SETTLING_STEPS)
        at = _trial_ground(rivals)
        for attacker_type in _SHOT_TIMING_TRIALS:
            findings.append(_cooldown_trace(rivals, attacker_type, at))
    return findings


def main(argv: Sequence[str]) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    measures = {
        "range": measure_ranges,
        "damage": measure_damage,
        "no-weapon": measure_no_weapon,
        "attack-speed": measure_attack_speed,
        "shot-timing": measure_shot_timing,
        "cooldown-trace": measure_cooldown_trace,
    }
    parser.add_argument("mode", choices=tuple(measures))
    parser.add_argument("--out", type=Path, help="where to write what it found, as JSON")
    arguments = parser.parse_args(argv)
    installation = Installation.find()
    found: Any = [asdict(finding) for finding in measures[arguments.mode](installation)]
    text = json.dumps(found, indent=2)
    if arguments.out is None:
        print(text)
    else:
        arguments.out.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1:])
