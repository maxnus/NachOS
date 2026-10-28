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
a light target, an armored one and one that flies, with neither side upgraded, with this player's side upgraded, and
then with both, so that the target's armor is raised too.

`attack-speed`, under `god`, times the first shots of one attacker at a time at a command center of the other
player's, by the steps at which its cooldown rises, before this player's upgrades and after, a marine and a marauder
then stimmed too.

`shot-timing` times `_TIMED_SHOTS` shots of each of a few attackers at a command center healed as it goes, over as
many trials as it takes, with no upgrade and no cheat but `show_map`, and records the steps between shots, the
cooldown each showed at its shot and the delay added to each shot's cooldown, worked back. `delay-bounds` does the
same for `_SHOTS_FOR_DELAY_BOUNDS` shots of a zergling, and `delay-weapons` for `_TIMED_SHOTS` shots of each of more
attackers.

`delay-sped-up` times a zergling's and an adept's shots before the `upgrade` cheat's three sends and after, which
give them Adrenal Glands and Resonating Glaives, and records the cooldown each shot started from.

`move-before-shot` orders an attacker firing at a command center to move a few steps before a shot due, or lets it
be, and records whether and when its cooldown rose, the target lost health and the attacker moved.

`cooldown-trace` reads the same attackers' cooldowns and the command center's health at every step over
`_TRACED_SHOTS` shots after the first, and records how long the cooldown read 0 before each shot and when the target
lost health::

    uv run python tools/sweep_combat.py range --out combat-range.json
    uv run python tools/sweep_combat.py damage --out combat-damage.json
    uv run python tools/sweep_combat.py no-weapon --out combat-no-weapon.json
    uv run python tools/sweep_combat.py attack-speed --out combat-attack-speed.json
    uv run python tools/sweep_combat.py shot-timing --out combat-shot-timing.json
    uv run python tools/sweep_combat.py delay-bounds --out combat-delay-bounds.json
    uv run python tools/sweep_combat.py delay-weapons --out combat-delay-weapons.json
    uv run python tools/sweep_combat.py cooldown-trace --out combat-cooldown-trace.json
    uv run python tools/sweep_combat.py delay-sped-up --out combat-delay-sped-up.json
    uv run python tools/sweep_combat.py move-before-shot --out combat-move-before-shot.json
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

from sc2nachos.gamedata import GameData, TargetDomain, Weapon
from sc2nachos.gamedata._missing_attacks import MISSING_WEAPONS
from sc2nachos.gamemap import GameMap
from sc2nachos.geometry import Point
from sc2nachos.ids import UnitTypeId
from sc2nachos.ids.raw import RawAbilityId, RawBuffId
from sc2nachos.launch import Installation
from sc2nachos.match import Race
from sc2nachos.units._unit_type import UnitType

# How many steps a unit is given to fire at a target placed within its reach: a planetary fortress turning its turret
# toward one took 22 (in game). And how many it is given to settle once made.
_FIRING_STEPS = 64
_SETTLING_STEPS = 4
_REMOVAL_STEPS = 8
_TOWNHALL_TYPES = UnitType.Townhall.type_ids
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


def _tables_weapons(game_data: GameData, unit_type: UnitTypeId) -> tuple[Weapon, ...]:
    """The weapons the game's tables give `unit_type`: none where NachOS gives it the weapons measured here instead."""
    weapons = game_data.units[unit_type].weapons
    return () if weapons == MISSING_WEAPONS.get(unit_type) else weapons


def _weapon_range(game_data: GameData, attacker: UnitTypeId, *, flying: bool) -> float | None:
    """The range of the attacker's weapon the tables give it against a target that flies or does not, or None."""
    wanted = TargetDomain.AIR if flying else TargetDomain.GROUND
    weapons = _tables_weapons(game_data, attacker)
    ranges = [weapon.range for weapon in weapons if weapon.target_domain in (wanted, TargetDomain.ANY)]
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
    attacker_buffs: dict[str, int] = field(default_factory=dict)
    """The buffs the attacker wore while it was set upon the target, each with the steps it wore it."""


def _is_melee(game_data: GameData, unit_type: UnitTypeId) -> bool:
    """Whether every weapon the tables give `unit_type` reaches less than 1, as a melee one does."""
    weapons = _tables_weapons(game_data, unit_type)
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
) -> tuple[raw_pb2.Unit, raw_pb2.Unit, list[Loss], dict[str, int]]:
    """Make an attacker of this player's at `at` and a target of the other's across, `_TRIAL_DISTANCE` from it or, from
    a melee attacker, `_MELEE_TRIAL_DISTANCE`, both holding, the target's shields set to `shields` if given and the
    abilities `before` ordered on the attacker first, in turn, each with the energy it needs; and what the target lost
    at each step for `_ATTACK_STEPS` steps, and the buffs the attacker wore over them, each with the steps it wore
    it."""
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
    attacker_buffs: Counter[str] = Counter()
    for step in range(1, _ATTACK_STEPS + 1):
        rivals.step(1)
        if (attacker_now := _unit_tagged(me, attacker.tag)) is not None:
            attacker_buffs.update(_buff_name(buff) for buff in attacker_now.buff_ids)
        now = _unit_tagged(me, target.tag)
        if now is None:
            losses.append(Loss(step, last[0], last[1]))
            break
        health_lost, shields_lost = last[0] - now.health, last[1] - now.shield
        if health_lost > 0 or shields_lost > 0:
            losses.append(Loss(step, health_lost, shields_lost))
        last = (now.health, now.shield)
    _kill_and_wait(rivals, [attacker.tag, target.tag])
    return attacker, target, losses, dict(attacker_buffs)


def _buff_name(buff: int) -> str:
    """The game's name for the raw buff id `buff`, or the number for one the raw catalog lacks."""
    try:
        return RawBuffId(buff).name
    except ValueError:
        return str(buff)


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
            "cooldown_steps": weapon.base_cooldown.steps,
            "damage_bonuses": {attribute.name: bonus for attribute, bonus in weapon.damage_bonuses.items()},
        }
        for weapon in _tables_weapons(game_data, unit_type)
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
    _, target, losses, attacker_buffs = _set_upon(
        rivals, attacker_type, target_type, at, shields=shields, before=before
    )
    row = base_tables.units[target_type]
    weapons = _tables_weapons(game_data, attacker_type)
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
        attacker_buffs,
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
# air, and what each is ordered first: an oracle turns its beam on. An oracle is set on a zealot too, for the shields,
# on an overlord, which it is not known to hit, and on an SCV with its beam never on and turned on and off again.
_NO_WEAPON_TRIALS: tuple[tuple[UnitTypeId, UnitTypeId, tuple[int, ...]], ...] = (
    (UnitTypeId.BATTLECRUISER, UnitTypeId.SCV, ()),
    (UnitTypeId.BATTLECRUISER, UnitTypeId.ROACH, ()),
    (UnitTypeId.BATTLECRUISER, UnitTypeId.OVERLORD, ()),
    (UnitTypeId.ORACLE, UnitTypeId.SCV, (RawAbilityId.Behavior_PulsarBeamOn,)),
    (UnitTypeId.ORACLE, UnitTypeId.ROACH, (RawAbilityId.Behavior_PulsarBeamOn,)),
    (UnitTypeId.ORACLE, UnitTypeId.ZEALOT, (RawAbilityId.Behavior_PulsarBeamOn,)),
    (UnitTypeId.ORACLE, UnitTypeId.OVERLORD, (RawAbilityId.Behavior_PulsarBeamOn,)),
    (UnitTypeId.ORACLE, UnitTypeId.SCV, ()),
    (UnitTypeId.ORACLE, UnitTypeId.SCV, (RawAbilityId.Behavior_PulsarBeamOn, RawAbilityId.Behavior_PulsarBeamOff)),
    (UnitTypeId.VOID_RAY, UnitTypeId.SCV, ()),
    (UnitTypeId.VOID_RAY, UnitTypeId.ROACH, ()),
    (UnitTypeId.VOID_RAY, UnitTypeId.OVERLORD, ()),
    (UnitTypeId.SENTRY, UnitTypeId.SCV, ()),
    (UnitTypeId.SENTRY, UnitTypeId.ROACH, ()),
    (UnitTypeId.SENTRY, UnitTypeId.OVERLORD, ()),
)


def measure_no_weapon(installation: Installation) -> list[AttackFinding]:
    """Every trial of `_NO_WEAPON_TRIALS` with neither side upgraded, then the attacker's side, then both."""
    findings = []
    with playing_rivals(Race.TERRAN, installation) as rivals:
        me = rivals.me
        me.cheat(Cheat.SHOW_MAP)
        rivals.step(_SETTLING_STEPS)
        at = _trial_ground(rivals)
        base_tables = GameData(me.client.game_data())
        for upgrades, upgrading in (("neither", None), ("attacker", rivals.me), ("both", rivals.enemy)):
            if upgrading is not None:
                _send_upgrade_levels(upgrading, rivals)
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
) -> tuple[raw_pb2.Unit, raw_pb2.Unit, float | None]:
    """Make a command center of the other player's at `at` and an attacker of this player's to its left, by its edge
    if the attacker is melee and well within reach if not, and return the attacker, the command center and the
    cooldown `game_data` gives the attacker's first weapon, or None if it gives none."""
    (target,) = _units_made(rivals, [(UnitTypeId.COMMAND_CENTER, _enemy_of(rivals), at)])
    away = _MELEE_FROM_COMMAND_CENTER if _is_melee(game_data, attacker_type) else _RANGED_FROM_COMMAND_CENTER
    (attacker,) = _units_made(rivals, [(attacker_type, rivals.me.player, Point((target.pos.x - away, target.pos.y)))])
    weapons = _tables_weapons(game_data, attacker_type)
    return attacker, target, weapons[0].base_cooldown.steps if weapons else None


class _AttackWatch:
    """An attacker of this player's and its target, both read after every step."""

    def __init__(self, rivals: Rivals, attacker: raw_pb2.Unit, target: raw_pb2.Unit) -> None:
        self._rivals = rivals
        self.attacker = attacker
        self.target = target
        self.step = 0
        self.rose = False
        """Whether the attacker's cooldown rose in the last step."""
        self.lost_health = False
        """Whether the target lost health in the last step."""

    def advance(self) -> bool:
        """Step once and read both units again; False if either is gone."""
        self._rivals.step(1)
        self.step += 1
        units = {unit.tag: unit for unit in self._rivals.me.units()}
        attacker, target = units.get(self.attacker.tag), units.get(self.target.tag)
        if attacker is None or target is None:
            return False
        self.rose = attacker.weapon_cooldown > self.attacker.weapon_cooldown
        self.lost_health = target.health < self.target.health
        self.attacker, self.target = attacker, target
        return True

    def until_rise(self, most_steps: int) -> bool:
        """Step until the attacker's cooldown rises, at most `most_steps` times; whether it rose."""
        for _ in range(most_steps):
            if not self.advance():
                return False
            if self.rose:
                return True
        return False


def _attack_interval(
    rivals: Rivals, attacker_type: UnitTypeId, at: Point, *, upgraded: bool, stim: int | None
) -> AttackIntervalFinding:
    """Make a command center of the other player's at `at` and an attacker of this player's beside it, stimmed if
    `stim` is its stim ability, and time its first `_SHOTS_TIMED` shots by the steps at which its cooldown rises."""
    me = rivals.me
    game_data = GameData(me.client.game_data())
    attacker, target, table_cooldown_steps = _by_a_command_center(rivals, game_data, attacker_type, at)
    watch = _AttackWatch(rivals, attacker, target)
    if stim is not None:
        me.order(stim, attacker.tag)
        rivals.step(1)
    me.order(RawAbilityId.Attack, attacker.tag, target.tag)
    shots: list[int] = []
    buffs: list[int] = []
    for step in range(_ATTACK_SPEED_STEPS):
        if not watch.advance():
            break
        if watch.rose:
            if not shots:
                buffs = list(watch.attacker.buff_ids)
            shots.append(step)
            if len(shots) > _SHOTS_TIMED:
                break
    _kill_and_wait(rivals, [attacker.tag, target.tag])
    intervals = [later - earlier for earlier, later in itertools.pairwise(shots)]
    finding = AttackIntervalFinding(
        attacker_type.name,
        upgraded,
        stim is not None,
        buffs,
        table_cooldown_steps,
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
# How many shots of one attacker are timed for the ends of the delay's range: about ten for each of the 4096 values the
# delay takes (in game).
_SHOTS_FOR_DELAY_BOUNDS = 40_000
# The most steps one trial runs before its units are made afresh: a game of two went to a tie some 6400 steps after a
# unit was last made or killed (in game), so an attacker's shots are timed over as many trials as it takes.
_LONGEST_TRIAL_STEPS = 5000
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
# More weapons, of every race, of workers, melee and ranged units, fliers, and of several hits or a bounce, to check
# the delay against the eight above.
_DELAY_WEAPON_TRIALS = (
    UnitTypeId.SCV,
    UnitTypeId.PROBE,
    UnitTypeId.DRONE,
    UnitTypeId.REAPER,
    UnitTypeId.GHOST,
    UnitTypeId.HELLION,
    UnitTypeId.CYCLONE,
    UnitTypeId.THOR,
    UnitTypeId.BANSHEE,
    UnitTypeId.ADEPT,
    UnitTypeId.IMMORTAL,
    UnitTypeId.COLOSSUS,
    UnitTypeId.ARCHON,
    UnitTypeId.DARK_TEMPLAR,
    UnitTypeId.TEMPEST,
    UnitTypeId.QUEEN,
    UnitTypeId.RAVAGER,
    UnitTypeId.MUTALISK,
    UnitTypeId.ULTRALISK,
)


@dataclass(slots=True)
class ShotTimingFinding:
    """The steps between an attacker's shots over one trial, and the cooldown it showed at each, beside its tables'. An
    attacker's shots may take several trials, each a finding."""

    attacker: str
    upgraded: bool
    """Whether this player had had the `upgrade` cheat's three sends."""
    table_cooldown_steps: float | None
    shot_steps: list[int]
    """The steps at which its cooldown rose: each shot."""
    cooldowns_after_shots: list[float]
    """The cooldown it showed at each of those steps."""
    intervals: dict[int, int]
    """How many times each number of steps came between two shots."""
    mean_interval: float | None
    steps_at_zero_before_shots: list[int]
    """For each shot after the first, the steps since the one before less the cooldown shown at that one, rounded up:
    how many steps the cooldown read 0 before it."""
    cooldowns_started: list[float]
    """For each shot after the first, the cooldown it started from when the one before ran out, worked back: the
    cooldown shown at the shot and the steps since the one before, less the cooldown shown at that one."""
    delays: list[float]
    """Each of `cooldowns_started` less the tables' cooldown, which leaves out what speeds an attack up. Empty if the
    tables give the attacker no weapon."""


def _shot_timing(
    rivals: Rivals, game_data: GameData, attacker_type: UnitTypeId, at: Point, shots_timed: int, *, upgraded: bool
) -> ShotTimingFinding:
    """Make a command center of the other player's at `at`, healed every `_HEALING_EVERY_STEPS`, and an attacker of
    this player's beside it, and time `shots_timed` of its shots by the steps at which its cooldown rises, or as many
    as come in `_LONGEST_TRIAL_STEPS`."""
    me = rivals.me
    attacker, target, table_cooldown_steps = _by_a_command_center(rivals, game_data, attacker_type, at)
    watch = _AttackWatch(rivals, attacker, target)
    me.order(RawAbilityId.Attack, attacker.tag, target.tag)
    shots: list[int] = []
    cooldowns: list[float] = []
    for step in range(_LONGEST_TRIAL_STEPS):
        if step % _HEALING_EVERY_STEPS == 0:
            me.set_value(_LIFE_VALUE, target.health_max, [target.tag])
        if not watch.advance():
            break
        if watch.rose:
            shots.append(step)
            cooldowns.append(watch.attacker.weapon_cooldown)
            if len(shots) > shots_timed:
                break
    _kill_and_wait(rivals, [attacker.tag, target.tag])
    between = [later - earlier for earlier, later in itertools.pairwise(shots)]
    at_zero = [steps - math.ceil(shown) for steps, shown in zip(between, cooldowns[:-1], strict=True)]
    started = [
        shown + steps - before for before, shown, steps in zip(cooldowns[:-1], cooldowns[1:], between, strict=True)
    ]
    delays = [] if table_cooldown_steps is None else [cooldown - table_cooldown_steps for cooldown in started]
    finding = ShotTimingFinding(
        attacker_type.name,
        upgraded,
        table_cooldown_steps,
        shots,
        cooldowns,
        dict(sorted(Counter(between).items())),
        sum(between) / len(between) if between else None,
        at_zero,
        started,
        delays,
    )
    logger.info(
        "{}{}: {} shots, table {}, mean interval {}, intervals {}, steps at 0 {}, delays {} to {}, mean {}",
        attacker_type.name,
        ", upgraded" if upgraded else "",
        len(shots),
        finding.table_cooldown_steps,
        finding.mean_interval,
        finding.intervals,
        dict(Counter(at_zero)),
        min(delays, default=None),
        max(delays, default=None),
        sum(delays) / len(delays) if delays else None,
    )
    return finding


def _attackers_timed(
    rivals: Rivals, at: Point, attacker_types: Sequence[UnitTypeId], shots: int, *, upgraded: bool
) -> list[ShotTimingFinding]:
    """`shots` shots of each of `attacker_types` at `at`, by this player's tables as they are now, over as many trials
    of `_LONGEST_TRIAL_STEPS` at most as it takes, each a finding. An attacker that fires fewer than twice in a trial
    is not tried again."""
    game_data = GameData(rivals.me.client.game_data())
    findings = []
    for attacker_type in attacker_types:
        timed = 0
        while timed < shots:
            finding = _shot_timing(rivals, game_data, attacker_type, at, shots - timed, upgraded=upgraded)
            findings.append(finding)
            if len(finding.shot_steps) < 2:
                break
            timed += len(finding.shot_steps) - 1
    return findings


def _shots_timed(
    installation: Installation, attacker_types: Sequence[UnitTypeId], shots: int
) -> list[ShotTimingFinding]:
    """`shots` shots of each of `attacker_types`, timed with neither side upgraded and no cheat but `show_map`."""
    with playing_rivals(Race.TERRAN, installation) as rivals:
        rivals.me.cheat(Cheat.SHOW_MAP)
        rivals.step(_SETTLING_STEPS)
        at = _trial_ground(rivals)
        return _attackers_timed(rivals, at, attacker_types, shots, upgraded=False)


def measure_shot_timing(installation: Installation) -> list[ShotTimingFinding]:
    """`_TIMED_SHOTS` shots of each attacker of `_SHOT_TIMING_TRIALS`."""
    return _shots_timed(installation, _SHOT_TIMING_TRIALS, _TIMED_SHOTS)


def measure_delay_bounds(installation: Installation) -> list[ShotTimingFinding]:
    """`_SHOTS_FOR_DELAY_BOUNDS` shots of a zergling, the quickest of `_SHOT_TIMING_TRIALS`."""
    return _shots_timed(installation, (UnitTypeId.ZERGLING,), _SHOTS_FOR_DELAY_BOUNDS)


def measure_delay_weapons(installation: Installation) -> list[ShotTimingFinding]:
    """`_TIMED_SHOTS` shots of each attacker of `_DELAY_WEAPON_TRIALS`."""
    return _shots_timed(installation, _DELAY_WEAPON_TRIALS, _TIMED_SHOTS)


# The attackers the `upgrade` cheat speeds up, a zergling by Adrenal Glands and an adept by Resonating Glaives, and
# how many of their shots are timed before it and after.
_SPED_UP_TRIALS = (UnitTypeId.ZERGLING, UnitTypeId.ADEPT)
_SHOTS_BEFORE_SPEEDING_UP = 500
_SHOTS_SPED_UP = 3000


def measure_delay_sped_up(installation: Installation) -> list[ShotTimingFinding]:
    """`_SHOTS_BEFORE_SPEEDING_UP` shots of each of `_SPED_UP_TRIALS`, then `_SHOTS_SPED_UP` after the `upgrade`
    cheat's three sends, in one game with no other cheat but `show_map`."""
    with playing_rivals(Race.TERRAN, installation) as rivals:
        me = rivals.me
        me.cheat(Cheat.SHOW_MAP)
        rivals.step(_SETTLING_STEPS)
        at = _trial_ground(rivals)
        findings = _attackers_timed(rivals, at, _SPED_UP_TRIALS, _SHOTS_BEFORE_SPEEDING_UP, upgraded=False)
        _send_upgrade_levels(me, rivals)
        return findings + _attackers_timed(rivals, at, _SPED_UP_TRIALS, _SHOTS_SPED_UP, upgraded=True)


# How many shots are traced, each from the shot before it: few enough that no attacker kills the command center, which
# is never healed, so its losses read clean.
_TRACED_SHOTS = 10


@dataclass(slots=True)
class TracedStep:
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
    readings: list[TracedStep]
    shot_steps: list[int]
    """The steps at which its cooldown rose: the first shot and the `_TRACED_SHOTS` after it."""
    loss_steps: list[int]
    """The steps at which the target lost health. A missile fired at the last shot lands after the trace ends."""
    steps_at_zero_before_shots: list[int]
    """For each shot after the first, how many steps in a row before it the cooldown read 0."""
    losses_after_nearest_shot: list[int]
    """For each loss, how many steps after the shot nearest it it came: negative if before."""


def _cooldown_trace(rivals: Rivals, game_data: GameData, attacker_type: UnitTypeId, at: Point) -> CooldownTraceFinding:
    """Make a command center of the other player's at `at` and an attacker of this player's beside it, and read the
    attacker's cooldown and the command center's health at every step, from the order to attack to the rise of the
    `_TRACED_SHOTS`-th shot after the first."""
    me = rivals.me
    attacker, target, table_cooldown_steps = _by_a_command_center(rivals, game_data, attacker_type, at)
    watch = _AttackWatch(rivals, attacker, target)
    me.order(RawAbilityId.Attack, attacker.tag, target.tag)
    readings: list[TracedStep] = []
    shots: list[int] = []
    for step in range(_LONGEST_TRIAL_STEPS):
        if not watch.advance():
            break
        if watch.rose:
            shots.append(step)
        readings.append(TracedStep(step, watch.attacker.weapon_cooldown, watch.target.health))
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
        table_cooldown_steps,
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
        game_data = GameData(me.client.game_data())
        for attacker_type in _SHOT_TIMING_TRIALS:
            findings.append(_cooldown_trace(rivals, game_data, attacker_type, at))
    return findings


# The attackers ordered to move just before a shot: of 0, 1 and 3 steps at 0 before each shot, melee and ranged. Each is
# ordered to move `_MOVES_EACH` times at each number of steps before the shot due, and let fire as many times.
_MOVE_TRIALS = (UnitTypeId.MARAUDER, UnitTypeId.MARINE, UnitTypeId.ZERGLING, UnitTypeId.STALKER)
_MOVES_EACH = 10
# How many steps after the order its outcome is watched, how far left of where it stands the move sends the attacker,
# how far it must have gone to count as moved, and how many steps it is given to fire again once ordered to attack.
_STEPS_WATCHED_AFTER_A_MOVE = 16
_MOVE_DISTANCE = 4.0
_MOVED_AT_LEAST = 0.05
_STEPS_TO_FIRE_AGAIN = 120


@dataclass(slots=True)
class MoveBeforeShotFinding:
    """What came of a shot due when its attacker, firing at a command center, was ordered to move some steps before
    it, or was let be."""

    attacker: str
    steps_at_zero: int
    """How many steps its cooldown read 0 before a shot, found from its second, third and fourth."""
    steps_before_shot: int | None
    """How many steps before the shot due the move was ordered: 0 in the step its cooldown rose, once the rise was
    read. None for a shot let be, watched from the step before it."""
    shot_after: int | None
    """How many steps after the order its cooldown rose, 0 for the step the order was given in and less for one before,
    or None if it did not within `_STEPS_WATCHED_AFTER_A_MOVE`."""
    loss_after: int | None
    """How many steps after the order the target lost health, counted the same way, or None."""
    moved_after: int | None
    """How many steps after the order the attacker stood `_MOVED_AT_LEAST` from where it was, or None."""


def _moves_before_shots(
    rivals: Rivals, game_data: GameData, attacker_type: UnitTypeId, at: Point
) -> list[MoveBeforeShotFinding]:
    """Make a command center of the other player's at `at` and an attacker of this player's beside it, find how many
    steps its cooldown reads 0 before a shot, and order it to move `_MOVES_EACH` times at each number of steps before
    a shot due, from that many and one more down to 0, and let it fire as many times. The command center is healed at
    the start of each. An attacker whose steps at 0 differ between its second and third shots and its third and
    fourth is left out."""
    me = rivals.me
    attacker, target, _ = _by_a_command_center(rivals, game_data, attacker_type, at)
    watch = _AttackWatch(rivals, attacker, target)
    me.order(RawAbilityId.Attack, attacker.tag, target.tag)
    rises: list[tuple[int, float]] = []
    while len(rises) < 4 and watch.until_rise(_STEPS_TO_FIRE_AGAIN):
        rises.append((watch.step, watch.attacker.weapon_cooldown))
    each_at_zero = {
        later - earlier - math.ceil(shown) for (earlier, shown), (later, _) in itertools.pairwise(rises[1:])
    }
    if len(rises) < 4 or len(each_at_zero) != 1:
        logger.warning("{}: left out, its steps at 0 before its shots {}", attacker_type.name, each_at_zero)
        _kill_and_wait(rivals, [attacker.tag, target.tag])
        return []
    (at_zero,) = each_at_zero
    findings = []
    moved = False
    for steps_before in [None, *range(at_zero + 1, -1, -1)]:
        for _ in range(_MOVES_EACH):
            me.set_value(_LIFE_VALUE, target.health_max, [target.tag])
            if moved:
                me.order(RawAbilityId.Attack, attacker.tag, target.tag)
                if not watch.until_rise(_STEPS_TO_FIRE_AGAIN):
                    break
            if not watch.until_rise(_STEPS_TO_FIRE_AGAIN):
                break
            due = watch.step + math.ceil(watch.attacker.weapon_cooldown) + at_zero
            ordered_at = due - (1 if steps_before is None else steps_before)
            shot = None
            while watch.step < ordered_at and watch.advance():
                if shot is None and watch.rose:
                    shot = watch.step - ordered_at
            loss = 0 if watch.lost_health and watch.step == ordered_at else None
            moved_from = Point((watch.attacker.pos.x, watch.attacker.pos.y))
            moved = steps_before is not None
            if moved:
                me.order(RawAbilityId.Move_Move, attacker.tag, Point((moved_from.x - _MOVE_DISTANCE, moved_from.y)))
            moved_after = None
            for after in range(1, _STEPS_WATCHED_AFTER_A_MOVE + 1):
                if not watch.advance():
                    break
                if shot is None and watch.rose:
                    shot = after
                if loss is None and watch.lost_health:
                    loss = after
                if (
                    moved_after is None
                    and moved_from.distance_to(Point((watch.attacker.pos.x, watch.attacker.pos.y))) > _MOVED_AT_LEAST
                ):
                    moved_after = after
            findings.append(MoveBeforeShotFinding(attacker_type.name, at_zero, steps_before, shot, loss, moved_after))
    _kill_and_wait(rivals, [attacker.tag, target.tag])
    for steps_before in [None, *range(at_zero + 1, -1, -1)]:
        outcomes = Counter(
            (finding.shot_after, finding.loss_after, finding.moved_after)
            for finding in findings
            if finding.steps_before_shot == steps_before
        )
        logger.info(
            "{}, {} steps at 0, moved {} steps before the shot: (shot, loss, moved) after {}",
            attacker_type.name,
            at_zero,
            steps_before,
            dict(outcomes),
        )
    return findings


def measure_moves_before_shots(installation: Installation) -> list[MoveBeforeShotFinding]:
    """Every attacker of `_MOVE_TRIALS`, with neither side upgraded and no cheat but `show_map`."""
    findings = []
    with playing_rivals(Race.TERRAN, installation) as rivals:
        me = rivals.me
        me.cheat(Cheat.SHOW_MAP)
        rivals.step(_SETTLING_STEPS)
        at = _trial_ground(rivals)
        game_data = GameData(me.client.game_data())
        for attacker_type in _MOVE_TRIALS:
            findings.extend(_moves_before_shots(rivals, game_data, attacker_type, at))
    return findings


def main(argv: Sequence[str]) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    measures = {
        "range": measure_ranges,
        "damage": measure_damage,
        "no-weapon": measure_no_weapon,
        "attack-speed": measure_attack_speed,
        "shot-timing": measure_shot_timing,
        "delay-bounds": measure_delay_bounds,
        "delay-weapons": measure_delay_weapons,
        "cooldown-trace": measure_cooldown_trace,
        "delay-sped-up": measure_delay_sped_up,
        "move-before-shot": measure_moves_before_shots,
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
