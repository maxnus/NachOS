"""Find which alerts the game raises for this player, and when, by causing what each one names.

Needs StarCraft II installed. Plays one game as each race, or only those named, under the `free`, `fast_build`,
`food` and `show_map` cheats, killing any of the computer's units that could fight, and runs trials in turn. Each
trial causes something a counted number of times, such as three marines trained, and records every alert raised
meanwhile, every action error, and every order the game refused outright, with the step each came at.

`errors` and `zerg-errors` play without the cheats, so that minerals and supply run short. `rivals` plays a game of
two players, both from here, for what only an enemy causes. `attacks` runs the attack trials alone, which `terran`
begins with. `suppression` runs the long trials that time how long an attack alert is held back; it runs only when
named::

    uv run python tools/sweep_alerts.py
    uv run python tools/sweep_alerts.py rivals errors --out alerts-rivals.json
    uv run python tools/sweep_alerts.py suppression

The findings are written as JSON, one entry per trial: the steps at which what it caused was seen, and the steps at
which each alert came. An alert raised in no trial is one the sweep did not cause, or one the game does not raise.
"""

import argparse
import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

from _sandbox import OpenGround, Sandbox, playing, playing_rivals
from loguru import logger
from s2clientprotocol import common_pb2, debug_pb2, error_pb2, raw_pb2, sc2api_pb2

from sc2nachos.gamedata import GameData
from sc2nachos.gamemap import GameMap
from sc2nachos.geometry import Point
from sc2nachos.ids import AbilityId, UnitTypeId
from sc2nachos.launch import Installation
from sc2nachos.match import Race

# Steps waited after each trial, so a late alert is not credited to the next trial.
_SETTLE = 96
_OWN = raw_pb2.Alliance.Self
_ENEMY = raw_pb2.Alliance.Enemy
# What the attack trials create for the enemy; the computer plays zerg and never has these itself.
_ATTACKERS = frozenset({UnitTypeId.PYLON, UnitTypeId.PHOTON_CANNON})


@dataclass(slots=True)
class Trial:
    """What one trial caused, and the alerts raised meanwhile."""

    name: str
    happened: list[int] = field(default_factory=list)
    """The step each occurrence was seen at."""
    alerts: dict[str, list[int]] = field(default_factory=dict)
    """The steps each alert came at. An action error is keyed `error:` plus its result, and an order refused outright
    `verdict:` plus the game's answer."""


def _at(unit: raw_pb2.Unit) -> Point:
    return Point((unit.pos.x, unit.pos.y))


class _Game:
    """A sweep's game, stepped and observed here only, so that no observation's alerts go unrecorded."""

    def __init__(self, sandbox: Sandbox, *, step: Callable[[int], object] | None = None, guarded: bool = True) -> None:
        """Play the game `sandbox` has joined, stepping it with `step` (in a game of two, one that steps both players),
        and killing the computer's fighters if `guarded`."""
        self.sandbox = sandbox
        self.client = sandbox.client
        self._step = step or self.client.step
        self._guarded = guarded
        self.player = sandbox.player
        self.enemy = 3 - sandbox.player
        self.map = GameMap(self.client.game_info())
        tables = GameData(self.client.game_data()).units
        # The computer's units that can hurt anything: everything with a weapon except workers, plus banelings. The
        # rest are left alone, since the computer gives up once it has nothing left.
        armed = {unit_type for unit_type, row in tables.items() if row.weapons and unit_type is not UnitTypeId.DRONE}
        self.fighters = frozenset(armed | {UnitTypeId.BANELING}) - _ATTACKERS
        self.step = 0
        self.units: list[raw_pb2.Unit] = []
        self.upgrades: set[int] = set()
        self.minerals = 0
        self.supply = (0, 0)
        self.alerts: list[tuple[int, str]] = []
        # The mineral fields at home, and the step each was first missing at.
        self.home_fields: set[int] = set()
        self.fields_gone: dict[int, int] = {}
        self.observe()
        self.ground = OpenGround(self.map, self.units)
        townhalls = (UnitTypeId.COMMAND_CENTER, UnitTypeId.NEXUS, UnitTypeId.HATCHERY)
        self.home = _at(self.own(*townhalls)[0])
        self.middle = self.map.playable_area.center
        self.home_fields = {
            unit.tag
            for unit in self.units
            if "MINERAL_FIELD" in UnitTypeId(unit.unit_type).name and _at(unit).distance_to(self.home) < 12
        }

    def observe(self) -> None:
        response = self.client.observation()
        self.step = response.observation.game_loop
        self.units = list(response.observation.raw_data.units)
        self.upgrades = set(response.observation.raw_data.player.upgrade_ids)
        common = response.observation.player_common
        self.minerals = common.minerals
        self.supply = (common.food_used, common.food_cap)
        for tag in self.home_fields - {unit.tag for unit in self.units}:
            self.fields_gone.setdefault(tag, self.step)
        self.alerts.extend((self.step, sc2api_pb2.Alert.Name(alert)) for alert in response.observation.alerts)
        self.alerts.extend(
            (self.step, f"error:{error_pb2.ActionResult.Name(error.result)}") for error in response.action_errors
        )

    def turn(self, steps: int = 4) -> None:
        """Let `steps` pass and observe, then kill every fighter of the computer's, which `show_map` reveals wherever
        it is, so it never attacks what a trial counts or ends the game."""
        while steps > 0:
            # A long wait is split into short ones, so nothing the computer makes lives long.
            chunk = min(steps, 64)
            self._step(chunk)
            self.observe()
            steps -= chunk
            if not self._guarded:
                continue
            if fighters := [u.tag for u in self.units if u.alliance == _ENEMY and u.unit_type in self.fighters]:
                self.sandbox.kill(fighters)

    def until(self, done: Callable[[], bool], *, steps: int = 4, limit: int = 3000) -> bool:
        """Turn until `done`, for at most `limit` steps, and return whether it did."""
        end = self.step + limit
        while not done():
            if self.step >= end:
                logger.warning("Gave up waiting at step {}", self.step)
                return False
            self.turn(steps)
        return True

    def own(self, *unit_types: UnitTypeId) -> list[raw_pb2.Unit]:
        return [unit for unit in self.units if unit.alliance == _OWN and unit.unit_type in unit_types]

    def of_tags(self, tags: Iterable[int]) -> list[raw_pb2.Unit]:
        wanted = set(tags)
        return [unit for unit in self.units if unit.tag in wanted]

    def create(
        self, unit_type: UnitTypeId, at: Point, *, owner: int | None = None, count: int = 1
    ) -> list[raw_pb2.Unit]:
        """Create `count` of `unit_type` at `at`, this player's unless another `owner` is given."""
        before = {unit.tag for unit in self.units}
        command = self.sandbox.create(unit_type, owner or self.player, at)
        self.sandbox.debug(*([command] * count))
        made: list[raw_pb2.Unit] = []
        # A new unit can show up a few steps late, once vision catches up with it.
        for _ in range(4):
            self.turn(4)
            made = [unit for unit in self.units if unit.tag not in before and unit.unit_type == unit_type]
            if len(made) >= count:
                break
        if len(made) < count:
            logger.warning("Made {} of the {} {} asked for", len(made), count, unit_type.name)
        return made

    def energize(self, units: Iterable[raw_pb2.Unit]) -> None:
        self.sandbox.set_value(debug_pb2.DebugSetUnitValue.Energy, 200, (unit.tag for unit in units))
        self.turn(2)

    def order(
        self,
        ability: int,
        units: Iterable[raw_pb2.Unit],
        target: Point | raw_pb2.Unit | None = None,
        *,
        queued: bool = False,
    ) -> str:
        """Order `ability` on `units` together, and return the game's verdict."""
        command = raw_pb2.ActionRawUnitCommand(
            ability_id=ability, unit_tags=[unit.tag for unit in units], queue_command=queued
        )
        if isinstance(target, Point):
            command.target_world_space_pos.x, command.target_world_space_pos.y = target
        elif target is not None:
            command.target_unit_tag = target.tag
        result = self.client.act([sc2api_pb2.Action(action_raw=raw_pb2.ActionRaw(unit_command=command))]).result[0]
        verdict = error_pb2.ActionResult.Name(result)
        if result != error_pb2.ActionResult.Success:
            logger.info("Ability {} answered {}", ability, verdict)
            self.alerts.append((self.step, f"verdict:{verdict}"))
        return verdict

    def camera(self, at: Point) -> None:
        move = raw_pb2.ActionRawCameraMove(center_world_space=common_pb2.Point(x=at.x, y=at.y))
        self.client.act([sc2api_pb2.Action(action_raw=raw_pb2.ActionRaw(camera_move=move))])

    def spot(self, near: Point, half: int) -> Point:
        """Take the free square `2 * half + 1` tiles across nearest to `near`, and return its center."""
        return self.ground.claim(near, half)

    def toward(self, distance: float) -> Point:
        """The point `distance` from home toward the middle of the map."""
        return self.home.towards(self.middle, distance)

    def trial(self, name: str, run: Callable[[], list[int]]) -> Trial:
        logger.info("Trial: {}", name)
        start = len(self.alerts)
        happened = run()
        self.turn(_SETTLE)
        alerts: dict[str, list[int]] = {}
        for step, alert in self.alerts[start:]:
            alerts.setdefault(alert, []).append(step)
        logger.info("  happened at {}, alerts {}", happened, alerts)
        return Trial(name, happened, alerts)

    def _first(self, found: Callable[[], Iterable[int]], count: int, *, steps: int, limit: int) -> list[int]:
        """The step each of the first `count` tags `found` yields was first yielded at."""
        seen: dict[int, int] = {}

        def done() -> bool:
            for tag in found():
                seen.setdefault(tag, self.step)
            return len(seen) >= count

        self.until(done, steps=steps, limit=limit)
        return sorted(seen.values())

    def appear(self, unit_types: Iterable[UnitTypeId], count: int, *, steps: int = 4, limit: int = 3000) -> list[int]:
        """The step each of this player's next `count` units of `unit_types` is first seen at."""
        types = tuple(unit_types)
        before = {unit.tag for unit in self.own(*types)}
        return self._first(
            lambda: (unit.tag for unit in self.own(*types) if unit.tag not in before), count, steps=steps, limit=limit
        )

    def finish(self, tags: Iterable[int], *, steps: int = 4, limit: int = 3000) -> list[int]:
        """The step each unit of `tags` is first seen finished at."""
        wanted = list(tags)
        return self._first(
            lambda: (unit.tag for unit in self.of_tags(wanted) if unit.build_progress == 1.0),
            len(wanted),
            steps=steps,
            limit=limit,
        )

    def become(self, tags: Iterable[int], unit_type: UnitTypeId, *, steps: int = 4, limit: int = 3000) -> list[int]:
        """The step each unit of `tags` is first seen as `unit_type` at."""
        wanted = list(tags)
        return self._first(
            lambda: (unit.tag for unit in self.of_tags(wanted) if unit.unit_type == unit_type),
            len(wanted),
            steps=steps,
            limit=limit,
        )

    def gone(self, tags: Iterable[int], count: int | None = None, *, steps: int = 4, limit: int = 3000) -> list[int]:
        """The step each of the first `count` units of `tags`, or all of them, is first missing from the observation."""
        wanted = set(tags)
        return self._first(
            lambda: wanted - {unit.tag for unit in self.units}, count or len(wanted), steps=steps, limit=limit
        )

    def researched(self, count: int, *, steps: int = 4, limit: int = 3000) -> list[int]:
        """The step each of this player's next `count` upgrades is first seen at."""
        before = set(self.upgrades)
        return self._first(lambda: self.upgrades - before, count, steps=steps, limit=limit)

    def kill(self, units: Iterable[raw_pb2.Unit]) -> None:
        self.sandbox.kill(unit.tag for unit in units)
        self.turn(4)


# Where each attack's cannon stands around its target, pylon beside it, cycling so none is put where the last one
# died.
_CANNON_OFFSETS = ((3.0, 3.0), (-3.0, 3.0), (-3.0, -3.0), (3.0, -3.0))


def _cannon(game: _Game, at: Point, attack: int = 0) -> None:
    """An enemy photon cannon beside `at` and an enemy pylon to power it, at the `attack`-th place around it."""
    dx, dy = _CANNON_OFFSETS[attack % len(_CANNON_OFFSETS)]
    game.create(UnitTypeId.PYLON, at + (dx, dy + (2.0 if dy > 0 else -2.0)), owner=game.enemy)
    for cannon in game.create(UnitTypeId.PHOTON_CANNON, at + (dx, dy), owner=game.enemy):
        if not cannon.is_powered or _at(cannon).distance_to(at) > 6:
            logger.warning(
                "A cannon at {} is powered {}, {:.1f} from its target",
                _at(cannon),
                cannon.is_powered,
                _at(cannon).distance_to(at),
            )


def _hit(game: _Game, life: dict[int, float]) -> bool:
    """Wait until one of the units in `life` (tag to health plus shields) has lost some, or died."""
    return game.until(
        lambda: (
            len(game.of_tags(life)) < len(life)
            or any(unit.health + unit.shield < life[unit.tag] for unit in game.of_tags(life))
        ),
        steps=1,
        limit=200,
    )


def _clear(game: _Game, at: Point) -> None:
    """Kill every enemy unit near `at`."""
    game.kill([unit for unit in game.units if unit.alliance == _ENEMY and _near(unit, at, 12)])


def _attacks(game: _Game, at: Point, *, gaps: Sequence[int], on_screen: bool, bait: UnitTypeId) -> list[int]:
    """Create a `bait` for this player at `at` and have an enemy photon cannon attack it for 48 steps, then again after
    each of `gaps` steps, each attack on a fresh unit. Returns the step each attack first hit."""
    game.camera(at if on_screen else game.home)
    hit: list[int] = []
    for attack, gap in enumerate((0, *gaps)):
        game.turn(max(gap, 4))
        target = game.create(bait, at)
        life = {unit.tag: unit.health + unit.shield for unit in target}
        _cannon(game, at, attack)
        if _hit(game, life):
            hit.append(game.step)
        game.turn(48)
        game.kill(game.of_tags(life))
        _clear(game, at)
    game.camera(game.home)
    return hit


def _sustained(game: _Game, at: Point, unit_type: UnitTypeId, *, steps: int) -> list[int]:
    """Create a `unit_type` for this player at `at`, off camera, and have an enemy photon cannon attack it for `steps`
    steps, kept alive throughout. Returns the steps it was seen losing health at."""
    game.camera(game.home)
    (target,) = game.create(unit_type, at)
    _cannon(game, at)
    hit: list[int] = []
    end = game.step + steps
    last = target.health + target.shield
    while game.step < end:
        game.turn(8)
        (now,) = game.of_tags([target.tag])
        if now.health + now.shield < last:
            hit.append(game.step)
        game.sandbox.set_value(debug_pb2.DebugSetUnitValue.Life, now.health_max, [target.tag])
        game.sandbox.set_value(debug_pb2.DebugSetUnitValue.Shields, now.shield_max, [target.tag])
        last = now.health_max + now.shield_max
    game.kill(game.of_tags([target.tag]))
    _clear(game, at)
    return hit


def _bursts(game: _Game, at: Point, unit_type: UnitTypeId, *, gaps: Sequence[int]) -> list[int]:
    """Create one `unit_type` for this player at `at`, off camera and kept alive, and have an enemy photon cannon
    attack it for 48 steps, then again after each of `gaps` steps. Returns the step each attack first hit."""
    game.camera(game.home)
    if not (made := game.create(unit_type, at)):
        return []
    (target,) = made
    if unit_type is not UnitTypeId.SUPPLY_DEPOT:
        # A unit that cannot fight back drifts away from what attacks it, unless it holds its position.
        game.order(AbilityId.GENERAL_HOLD_POSITION, made)
    hit: list[int] = []
    for attack, gap in enumerate((0, *gaps)):
        game.turn(max(gap, 4))
        if not (present := game.of_tags([target.tag])):
            logger.warning("The {} attacked is gone before attack {}", unit_type.name, attack)
            break
        (now,) = present
        life = {target.tag: now.health + now.shield}
        _cannon(game, at, attack)
        if _hit(game, life):
            hit.append(game.step)
        for _ in range(6):
            game.sandbox.set_value(debug_pb2.DebugSetUnitValue.Life, now.health_max, [target.tag])
            game.turn(8)
        _clear(game, at)
        game.sandbox.set_value(debug_pb2.DebugSetUnitValue.Life, now.health_max, [target.tag])
        game.sandbox.set_value(debug_pb2.DebugSetUnitValue.Shields, now.shield_max, [target.tag])
        if not game.of_tags([target.tag]):
            logger.warning("The {} attacked died", unit_type.name)
            break
    game.kill(game.of_tags([target.tag]))
    return hit


def _together(game: _Game, spots: Sequence[Point]) -> list[int]:
    """Create a marine for this player at each of `spots`, off camera, each attacked by its own enemy photon cannon,
    all at once. Returns the step each was first hit at."""
    game.camera(game.home)
    marines = [game.create(UnitTypeId.MARINE, spot)[0] for spot in spots]
    life = {marine.tag: marine.health for marine in marines}
    for spot in spots:
        game.create(UnitTypeId.PYLON, spot + (0.0, 5.0), owner=game.enemy)
    game.sandbox.debug(
        *(game.sandbox.create(UnitTypeId.PHOTON_CANNON, game.enemy, spot + (3.0, 3.0)) for spot in spots)
    )
    first: dict[int, int] = {}

    def done() -> bool:
        present = {unit.tag: unit for unit in game.of_tags(life)}
        for tag in life:
            if tag not in present or present[tag].health < life[tag]:
                first.setdefault(tag, game.step)
        return len(first) == len(life)

    game.until(done, steps=1, limit=200)
    game.turn(48)
    game.kill(game.of_tags(life))
    for spot in spots:
        _clear(game, spot)
    return sorted(first.values())


def _near(unit: raw_pb2.Unit, at: Point, reach: float) -> bool:
    return _at(unit).distance_to(at) < reach


def _minerals(game: _Game) -> list[raw_pb2.Unit]:
    """The mineral fields, nearest home first."""
    fields = [unit for unit in game.units if "MINERAL_FIELD" in UnitTypeId(unit.unit_type).name]
    return sorted(fields, key=lambda unit: _at(unit).distance_to(game.home))


def _geysers(game: _Game) -> list[raw_pb2.Unit]:
    """The geysers at home."""
    return [
        unit
        for unit in game.units
        if "GEYSER" in UnitTypeId(unit.unit_type).name and _at(unit).distance_to(game.home) < 12
    ]


def _attack_trials(game: _Game) -> list[Trial]:
    """Enemy photon cannon attacks on this player's units and structures, each trial on its own ground in the middle of
    the map, off camera unless a trial moves the camera there."""
    trials: list[Trial] = []
    gaps = (60, 150, 300, 600)
    for bait in (UnitTypeId.MARINE, UnitTypeId.SUPPLY_DEPOT):
        for on_screen in (False, True):
            where = "on screen" if on_screen else "off screen"
            at = game.spot(game.middle, 4)
            trials.append(
                game.trial(
                    f"a new {bait.name.lower()} attacked {where} each time, the next after each of {gaps} steps",
                    lambda at=at, bait=bait, on_screen=on_screen: _attacks(
                        game, at, gaps=gaps, on_screen=on_screen, bait=bait
                    ),
                )
            )
    for unit_type in (UnitTypeId.OVERLORD, UnitTypeId.SUPPLY_DEPOT):
        at = game.spot(game.middle, 4)
        trials.append(
            game.trial(
                f"one {unit_type.name.lower()} attacked off screen for 600 steps",
                lambda at=at, unit_type=unit_type: _sustained(game, at, unit_type, steps=600),
            )
        )
    bursts = (300, 1200, 2400, 4800, 9600)
    for unit_type in (UnitTypeId.OVERLORD, UnitTypeId.SUPPLY_DEPOT):
        at = game.spot(game.middle, 4)
        trials.append(
            game.trial(
                f"one {unit_type.name.lower()} attacked off screen for 48 steps, again after each of {bursts} steps",
                lambda at=at, unit_type=unit_type: _bursts(game, at, unit_type, gaps=bursts),
            )
        )
    every = game.spot(game.middle, 4)
    trials.append(
        game.trial(
            "one overlord attacked off screen for 48 steps, again every 1000 steps, 7 times in all",
            lambda: _bursts(game, every, UnitTypeId.OVERLORD, gaps=(1000,) * 6),
        )
    )
    for gap in (1500, 1800, 2100):
        at = game.spot(game.middle, 4)
        trials.append(
            game.trial(
                f"a new overlord attacked off screen for 48 steps, again after {gap} steps",
                lambda at=at, gap=gap: _bursts(game, at, UnitTypeId.OVERLORD, gaps=(gap,)),
            )
        )
    near = game.spot(game.middle, 4)
    # Past the middle of the map from home, well away from home and from `near`.
    elsewhere = game.spot(game.middle.towards(game.home, -25), 4)
    trials.append(game.trial("2 marines 4 apart attacked at once", lambda: _together(game, [near, near + (4.0, 0.0)])))
    trials.append(
        game.trial(
            f"2 marines {near.distance_to(elsewhere):.0f} apart attacked at once",
            lambda: _together(game, [near, elsewhere]),
        )
    )
    return trials


def _suppression_trials(game: _Game) -> list[Trial]:
    """How long an attack alert on one unit suppresses the next for it, and whether the clock runs from its last attack
    or its last alert: one unit attacked every 3000 steps, and new units attacked again after ever longer gaps."""
    trials: list[Trial] = []
    every = game.spot(game.middle, 4)
    trials.append(
        game.trial(
            "one overlord attacked off screen for 48 steps, again every 3000 steps, 9 times in all",
            lambda: _bursts(game, every, UnitTypeId.OVERLORD, gaps=(3000,) * 8),
        )
    )
    for gap in (6000, 7500, 9000, 12000, 15000):
        at = game.spot(game.middle, 4)
        trials.append(
            game.trial(
                f"a new overlord attacked off screen for 48 steps, again after {gap} steps",
                lambda at=at, gap=gap: _bursts(game, at, UnitTypeId.OVERLORD, gaps=(gap,)),
            )
        )
    return trials


def _terran_errors(game: _Game) -> list[Trial]:
    """Orders a player without cheats gives that the game cannot carry out, at once or when it gets to them."""
    trials: list[Trial] = []
    center = game.own(UnitTypeId.COMMAND_CENTER)[0]
    scvs = [unit for unit in game.own(UnitTypeId.SCV)]

    def minerals_short() -> list[int]:
        game.order(AbilityId.COMMAND_CENTER_TRAIN_SCV, [center])
        game.order(AbilityId.COMMAND_CENTER_TRAIN_SCV, [center], queued=True)
        game.turn(400)
        return []

    trials.append(
        game.trial("an SCV trained with the 50 minerals the game starts with, and another queued", minerals_short)
    )

    def supply_short() -> list[int]:
        game.until(lambda: game.minerals >= 250, limit=6000)
        logger.info("Supply {} with {} minerals", game.supply, game.minerals)
        for _ in range(5):
            game.order(AbilityId.COMMAND_CENTER_TRAIN_SCV, [center], queued=True)
        game.turn(1500)
        return []

    trials.append(game.trial("5 SCVs queued with 2 supply left", supply_short))

    def supply_lost() -> list[int]:
        (depot,) = game.create(UnitTypeId.SUPPLY_DEPOT, game.spot(game.toward(10), 1) + (0.5, 0.5))
        game.until(lambda: game.minerals >= 250, limit=6000)
        for _ in range(5):
            game.order(AbilityId.COMMAND_CENTER_TRAIN_SCV, [center], queued=True)
        game.turn(64)
        logger.info("Supply {} before the depot is killed", game.supply)
        game.kill([depot])
        game.turn(1500)
        return []

    trials.append(game.trial("5 SCVs queued, then the supply depot they need killed", supply_lost))

    def spent_meanwhile() -> list[int]:
        game.until(lambda: game.minerals >= 150, limit=6000)
        game.order(AbilityId.SCV_BUILD_SUPPLY_DEPOT, scvs[:1], game.spot(game.toward(24), 1) + (0.5, 0.5))
        game.order(AbilityId.COMMAND_CENTER_TRAIN_SCV, [center], queued=True)
        game.turn(600)
        return []

    trials.append(game.trial("a depot ordered far off, its minerals spent before the SCV gets there", spent_meanwhile))

    def blocked() -> list[int]:
        game.until(lambda: game.minerals >= 100, limit=6000)
        spot = game.spot(game.toward(20), 1) + (0.5, 0.5)
        game.order(AbilityId.SCV_BUILD_SUPPLY_DEPOT, scvs[1:2], spot)
        game.create(UnitTypeId.SIEGE_TANK_SIEGED, spot)
        game.turn(600)
        return []

    trials.append(game.trial("a depot ordered where a sieged tank then stands", blocked))

    def placed_badly() -> list[int]:
        game.until(lambda: game.minerals >= 100, limit=6000)
        game.order(AbilityId.SCV_BUILD_SUPPLY_DEPOT, scvs[2:3], _at(_minerals(game)[0]))
        game.turn(200)
        return []

    trials.append(game.trial("a depot ordered on a mineral field", placed_badly))
    return trials


def _zerg_errors(game: _Game) -> list[Trial]:
    """Orders a zerg player without cheats gives that the game cannot carry out."""
    trials: list[Trial] = []

    def minerals_short() -> list[int]:
        for larva in game.own(UnitTypeId.LARVA)[:2]:
            game.order(AbilityId.LARVA_MORPH_DRONE, [larva])
        game.turn(400)
        return []

    trials.append(game.trial("a drone morphed with the 50 minerals the game starts with, and another", minerals_short))

    def supply_short() -> list[int]:
        game.until(lambda: game.minerals >= 200 and len(game.own(UnitTypeId.LARVA)) >= 3, limit=6000)
        logger.info("Supply {} with {} minerals", game.supply, game.minerals)
        for larva in game.own(UnitTypeId.LARVA)[:3]:
            game.order(AbilityId.LARVA_MORPH_DRONE, [larva])
        game.turn(600)
        return []

    trials.append(game.trial("3 drones morphed with 1 supply left", supply_short))

    def overlord_lost() -> list[int]:
        game.until(lambda: game.minerals >= 150 and len(game.own(UnitTypeId.LARVA)) >= 2, limit=6000)
        for larva in game.own(UnitTypeId.LARVA)[:2]:
            game.order(AbilityId.LARVA_MORPH_DRONE, [larva])
        game.turn(16)
        game.kill(game.own(UnitTypeId.OVERLORD)[:1])
        game.turn(600)
        return []

    trials.append(game.trial("2 drones morphing, then an overlord killed", overlord_lost))
    return trials


def _rival_trials(me: _Game, enemy: _Game) -> list[Trial]:
    """What only an enemy causes, with the enemy played from here too: its nukes and nydus worms, where this player
    sees and where it does not."""
    trials: list[Trial] = []
    # A debug cheat is a toggle for the whole game, so one side turns each on.
    me.sandbox.cheat("free", "fast_build", "food")
    me.turn(8)
    enemy.observe()
    factory = enemy.toward(12)
    enemy.sandbox.debug(
        enemy.sandbox.create(UnitTypeId.FACTORY, enemy.player, factory),
        enemy.sandbox.create(UnitTypeId.GHOST_ACADEMY, enemy.player, enemy.toward(16)),
        enemy.sandbox.create(UnitTypeId.NYDUS_NETWORK, enemy.player, factory + (0.0, 6.0)),
    )
    me.turn(8)
    enemy.observe()
    unseen = me.spot(me.middle, 4)

    def nuke(target: Point, ghost_at: Point) -> list[int]:
        enemy.order(AbilityId.GHOST_ACADEMY_BUILD_NUKE, enemy.own(UnitTypeId.GHOST_ACADEMY))
        (ghost,) = enemy.create(UnitTypeId.GHOST, ghost_at)
        enemy.order(AbilityId.GHOST_HOLD_FIRE_ON, [ghost])
        me.turn(400)
        enemy.observe()
        verdict = enemy.order(AbilityId.GHOST_TACTICAL_NUKE, [ghost], target)
        logger.info("The enemy's nuke answered {}", verdict)
        launched = me.step
        me.turn(400)
        enemy.observe()
        enemy.kill([ghost])
        return [launched]

    trials.append(me.trial("the enemy's nuke launched at this player's home", lambda: nuke(me.home, me.toward(8))))
    trials.append(
        me.trial("the enemy's nuke launched where this player sees nothing", lambda: nuke(unseen, unseen + (8.0, 0.0)))
    )

    def worm(at: Point) -> list[int]:
        enemy.create(UnitTypeId.OVERSEER, at)
        verdict = enemy.order(AbilityId.NYDUS_NETWORK_BUILD_NYDUS_WORM, enemy.own(UnitTypeId.NYDUS_NETWORK), at)
        logger.info("The enemy's nydus worm answered {}", verdict)
        summoned = me.step
        me.turn(600)
        enemy.observe()
        return [summoned]

    near = me.spot(me.toward(10), 2)
    trials.append(me.trial("the enemy's nydus worm summoned where this player sees", lambda: worm(near)))
    elsewhere = me.spot(me.middle, 4)
    trials.append(me.trial("the enemy's nydus worm summoned where this player sees nothing", lambda: worm(elsewhere)))
    return trials


def _terran(game: _Game) -> list[Trial]:
    trials = _attack_trials(game)
    center = game.own(UnitTypeId.COMMAND_CENTER)[0]

    def workers() -> list[int]:
        for _ in range(3):
            game.order(AbilityId.COMMAND_CENTER_TRAIN_SCV, [center], queued=True)
        return game.appear([UnitTypeId.SCV], 3)

    trials.append(game.trial("3 SCVs trained one after another at a command center", workers))

    barracks = [game.create(UnitTypeId.BARRACKS, game.spot(game.toward(12), 3))[0] for _ in range(3)]

    def marines_queued() -> list[int]:
        for _ in range(3):
            game.order(AbilityId.BARRACKS_TRAIN_MARINE, [barracks[0]], queued=True)
        return game.appear([UnitTypeId.MARINE], 3)

    trials.append(game.trial("3 marines trained one after another at a barracks", marines_queued))

    def marines_together() -> list[int]:
        for structure in barracks:
            game.order(AbilityId.BARRACKS_TRAIN_MARINE, [structure])
        return game.appear([UnitTypeId.MARINE], 3, steps=1)

    trials.append(game.trial("3 marines trained at once at 3 barracks", marines_together))

    def depots() -> list[int]:
        for scv in game.own(UnitTypeId.SCV)[:3]:
            game.order(AbilityId.SCV_BUILD_SUPPLY_DEPOT, [scv], game.spot(game.toward(8), 1) + (0.5, 0.5))
        game.appear([UnitTypeId.SUPPLY_DEPOT], 3)
        placed = [unit.tag for unit in game.own(UnitTypeId.SUPPLY_DEPOT) if unit.build_progress < 1.0]
        return game.finish(placed, steps=1)

    trials.append(game.trial("3 supply depots built by 3 SCVs at once", depots))

    def add_ons() -> list[int]:
        game.order(AbilityId.GENERAL_BUILD_TECH_LAB, [barracks[1]])
        game.order(AbilityId.GENERAL_BUILD_REACTOR, [barracks[2]])
        game.appear([UnitTypeId.TECH_LAB_BARRACKS, UnitTypeId.REACTOR_BARRACKS], 2)
        placed = game.own(UnitTypeId.TECH_LAB_BARRACKS, UnitTypeId.REACTOR_BARRACKS)
        return game.finish((unit.tag for unit in placed), steps=1)

    trials.append(game.trial("a tech lab and a reactor built at once", add_ons))

    def research() -> list[int]:
        (tech_lab,) = game.own(UnitTypeId.TECH_LAB_BARRACKS)
        game.order(AbilityId.BARRACKS_TECH_LAB_RESEARCH_STIMPACK, [tech_lab])
        game.order(AbilityId.BARRACKS_TECH_LAB_RESEARCH_COMBAT_SHIELD, [tech_lab], queued=True)
        return game.researched(2)

    trials.append(game.trial("stimpack, then combat shield, researched at a tech lab", research))

    bays = [game.create(UnitTypeId.ENGINEERING_BAY, game.spot(game.toward(14), 2))[0] for _ in range(2)]

    def levels() -> list[int]:
        game.order(AbilityId.ENGINEERING_BAY_RESEARCH_INFANTRY_WEAPONS_1, [bays[0]])
        game.order(AbilityId.ENGINEERING_BAY_RESEARCH_INFANTRY_ARMOR_1, [bays[1]])
        return game.researched(2, steps=1)

    trials.append(game.trial("infantry weapons and armor level 1 researched at once", levels))

    centers = [game.create(UnitTypeId.COMMAND_CENTER, game.spot(game.toward(22), 3))[0] for _ in range(2)]

    def townhall_morphs() -> list[int]:
        game.order(AbilityId.COMMAND_CENTER_MORPH_ORBITAL_COMMAND, [centers[0]])
        game.order(AbilityId.COMMAND_CENTER_MORPH_PLANETARY_FORTRESS, [centers[1]])
        orbital = game.become([centers[0].tag], UnitTypeId.ORBITAL_COMMAND)
        return orbital + game.become([centers[1].tag], UnitTypeId.PLANETARY_FORTRESS)

    trials.append(
        game.trial("a command center became an orbital command, another a planetary fortress", townhall_morphs)
    )

    game.create(UnitTypeId.ARMORY, game.spot(game.toward(14), 2))
    hellions = game.create(UnitTypeId.HELLION, game.spot(game.toward(6), 1), count=2)

    def hellbats() -> list[int]:
        game.order(AbilityId.HELLION_MORPH_HELLBAT, hellions)
        return game.become((unit.tag for unit in hellions), UnitTypeId.HELLBAT)

    trials.append(game.trial("2 hellions became hellbats", hellbats))

    def mules() -> list[int]:
        orbitals = game.of_tags([centers[0].tag])
        game.energize(orbitals)
        field = _minerals(game)[0]
        game.order(AbilityId.ORBITAL_COMMAND_CALLDOWN_MULE, orbitals, field)
        game.order(AbilityId.ORBITAL_COMMAND_CALLDOWN_MULE, orbitals, field, queued=True)
        game.appear([UnitTypeId.MULE], 2)
        return game.gone((unit.tag for unit in game.own(UnitTypeId.MULE)), steps=16, limit=2000)

    trials.append(game.trial("2 MULEs expired", mules))

    # Arming a nuke needs a factory.
    game.create(UnitTypeId.FACTORY, game.spot(game.toward(16), 3))
    academy = game.create(UnitTypeId.GHOST_ACADEMY, game.spot(game.toward(16), 2))

    def arm() -> list[int]:
        game.order(AbilityId.GHOST_ACADEMY_BUILD_NUKE, academy)
        game.turn(600)
        return []

    trials.append(game.trial("a nuke armed at a ghost academy", arm))

    def launch() -> list[int]:
        ghost = game.create(UnitTypeId.GHOST, game.spot(game.toward(18), 1))
        game.order(AbilityId.GHOST_TACTICAL_NUKE, ghost, _at(ghost[0]).towards(game.middle, 8))
        game.turn(600)
        return []

    trials.append(game.trial("this player's ghost launched a nuke", launch))

    def errors() -> list[int]:
        for _ in range(6):
            game.order(AbilityId.BARRACKS_TRAIN_MARINE, [barracks[0]], queued=True)
        scvs = game.own(UnitTypeId.SCV)
        game.order(AbilityId.SCV_BUILD_SUPPLY_DEPOT, scvs[:1], _at(_minerals(game)[0]))
        blocked = game.spot(game.toward(24), 1) + (0.5, 0.5)
        game.order(AbilityId.SCV_BUILD_SUPPLY_DEPOT, scvs[1:2], blocked)
        game.create(UnitTypeId.MARINE, blocked)
        game.turn(400)
        return []

    trials.append(game.trial("orders that fail: a sixth marine queued, a depot on minerals, a depot blocked", errors))

    def vespene() -> list[int]:
        refineries = [unit for geyser in _geysers(game) for unit in game.create(UnitTypeId.REFINERY, _at(geyser))]
        scvs = game.own(UnitTypeId.SCV)
        for i, refinery in enumerate(refineries):
            game.order(AbilityId.SCV_GATHER, scvs[3 * i : 3 * i + 3], refinery)
        tags = [unit.tag for unit in refineries]
        return game._first(
            lambda: (unit.tag for unit in game.of_tags(tags) if unit.vespene_contents == 0),
            len(tags),
            steps=200,
            limit=40_000,
        )

    trials.append(game.trial("the geysers at home mined out", vespene))

    def minerals() -> list[int]:
        if near := [unit for unit in _minerals(game) if unit.tag in game.home_fields]:
            for i, scv in enumerate(game.own(UnitTypeId.SCV)):
                game.order(AbilityId.SCV_GATHER, [scv], near[i % len(near)])
            game.until(lambda: len(game.fields_gone) == len(game.home_fields), steps=32, limit=40_000)
        return []

    trials.append(game.trial("the rest of the mineral fields at home mined out", minerals))
    every = [step for step, alert in game.alerts if alert == "MineralsExhausted"]
    trials.append(
        Trial(
            "over the whole game: each mineral field at home gone, against every MineralsExhausted",
            sorted(game.fields_gone.values()),
            {"MineralsExhausted": every},
        )
    )
    return trials


def _zerg(game: _Game) -> list[Trial]:
    trials: list[Trial] = []
    hatchery = game.own(UnitTypeId.HATCHERY)[0]

    def drones() -> list[int]:
        for larva in game.own(UnitTypeId.LARVA)[:3]:
            game.order(AbilityId.LARVA_MORPH_DRONE, [larva])
        return game.appear([UnitTypeId.DRONE], 3)

    trials.append(game.trial("3 drones hatched from larva", drones))

    def overlords() -> list[int]:
        game.until(lambda: len(game.own(UnitTypeId.LARVA)) >= 2, limit=2000)
        for larva in game.own(UnitTypeId.LARVA)[:2]:
            game.order(AbilityId.LARVA_MORPH_OVERLORD, [larva])
        return game.appear([UnitTypeId.OVERLORD], 2)

    trials.append(game.trial("2 overlords hatched from larva", overlords))

    def natural_larva() -> list[int]:
        game.kill(game.own(UnitTypeId.LARVA))
        return game.appear([UnitTypeId.LARVA], 3, limit=1500)

    trials.append(game.trial("larva spawned by a hatchery short of three", natural_larva))

    def injected_larva() -> list[int]:
        game.until(lambda: len(game.own(UnitTypeId.LARVA)) >= 3, limit=1500)
        queen = game.create(UnitTypeId.QUEEN, game.toward(4))
        game.energize(queen)
        game.order(AbilityId.QUEEN_INJECT, queen, hatchery)
        return game.appear([UnitTypeId.LARVA], 3, limit=1500)

    trials.append(game.trial("larva spawned by a queen's inject", injected_larva))

    for structure in (UnitTypeId.SPAWNING_POOL, UnitTypeId.BANELING_NEST, UnitTypeId.ROACH_WARREN):
        game.create(structure, game.spot(game.toward(8), 2))
    game.create(UnitTypeId.EVOLUTION_CHAMBER, game.spot(game.toward(8), 2))

    def lair() -> list[int]:
        game.order(AbilityId.HATCHERY_MORPH_LAIR, [hatchery])
        return game.become([hatchery.tag], UnitTypeId.LAIR)

    trials.append(game.trial("a hatchery became a lair", lair))

    def morphs() -> list[int]:
        zerglings = game.create(UnitTypeId.ZERGLING, game.spot(game.toward(6), 1), count=2)
        roach = game.create(UnitTypeId.ROACH, game.spot(game.toward(6), 1))
        overlords = game.own(UnitTypeId.OVERLORD)[:2]
        # A morph ordered on several units together morphs only one of them, so each is ordered alone.
        for ability, units in (
            (AbilityId.ZERGLING_MORPH_BANELING, zerglings),
            (AbilityId.OVERLORD_MORPH_OVERSEER, overlords),
            (AbilityId.ROACH_MORPH_RAVAGER, roach),
        ):
            for unit in units:
                game.order(ability, [unit])
        return (
            game.become((unit.tag for unit in zerglings), UnitTypeId.BANELING)
            + game.become((unit.tag for unit in overlords), UnitTypeId.OVERSEER)
            + game.appear([UnitTypeId.RAVAGER], 1)
        )

    trials.append(game.trial("2 banelings, 2 overseers and a ravager morphed at once", morphs))

    def research() -> list[int]:
        game.order(AbilityId.SPAWNING_POOL_RESEARCH_ZERGLING_SPEED, game.own(UnitTypeId.SPAWNING_POOL))
        game.order(AbilityId.EVOLUTION_CHAMBER_RESEARCH_MELEE_WEAPONS_1, game.own(UnitTypeId.EVOLUTION_CHAMBER))
        return game.researched(2, steps=1)

    trials.append(game.trial("zergling speed and melee weapons level 1 researched at once", research))

    def structures() -> list[int]:
        for drone in game.own(UnitTypeId.DRONE)[:2]:
            game.order(AbilityId.DRONE_MORPH_EVOLUTION_CHAMBER, [drone], game.spot(game.toward(6), 2))
        game.appear([UnitTypeId.EVOLUTION_CHAMBER], 2)
        placed = [unit.tag for unit in game.own(UnitTypeId.EVOLUTION_CHAMBER) if unit.build_progress < 1.0]
        return game.finish(placed, steps=1)

    trials.append(game.trial("2 evolution chambers built by drones at once", structures))

    def nydus() -> list[int]:
        network = game.create(UnitTypeId.NYDUS_NETWORK, game.spot(game.toward(12), 2))
        game.turn(8)
        game.order(AbilityId.NYDUS_NETWORK_BUILD_NYDUS_WORM, network, game.spot(game.toward(16), 2))
        worm = game.appear([UnitTypeId.NYDUS_WORM], 1)
        game.turn(400)
        return worm

    trials.append(game.trial("this player's nydus worm summoned", nydus))
    return trials


def _protoss(game: _Game) -> list[Trial]:
    trials: list[Trial] = []
    nexus = game.own(UnitTypeId.NEXUS)[0]

    def probes() -> list[int]:
        for _ in range(3):
            game.order(AbilityId.NEXUS_TRAIN_PROBE, [nexus], queued=True)
        return game.appear([UnitTypeId.PROBE], 3)

    trials.append(game.trial("3 probes trained one after another at a nexus", probes))

    def powered(unit_type: UnitTypeId) -> list[raw_pb2.Unit]:
        """A new `unit_type` with a pylon of its own beside it."""
        at = game.spot(game.toward(10), 2)
        game.create(UnitTypeId.PYLON, game.spot(at, 1) + (0.5, 0.5))
        return game.create(unit_type, at)

    # Warp-ins land beside a pylon with open ground around it.
    warp_field = game.spot(game.toward(20), 3)
    game.create(UnitTypeId.PYLON, warp_field)
    gateways = [powered(UnitTypeId.GATEWAY)[0] for _ in range(3)]

    def zealots() -> list[int]:
        for gateway in gateways:
            game.order(AbilityId.GATEWAY_TRAIN_ZEALOT, [gateway])
        return game.appear([UnitTypeId.ZEALOT], 3, steps=1)

    trials.append(game.trial("3 zealots trained at once at 3 gateways", zealots))

    def pylons() -> list[int]:
        for probe in game.own(UnitTypeId.PROBE)[:2]:
            game.order(AbilityId.PROBE_BUILD_PYLON, [probe], game.spot(game.toward(6), 1) + (0.5, 0.5))
        game.appear([UnitTypeId.PYLON], 2)
        placed = [unit.tag for unit in game.own(UnitTypeId.PYLON) if unit.build_progress < 1.0]
        return game.finish(placed, steps=1)

    trials.append(game.trial("2 pylons built by probes at once", pylons))

    core = powered(UnitTypeId.CYBERNETICS_CORE)
    forge = powered(UnitTypeId.FORGE)
    council = powered(UnitTypeId.TWILIGHT_COUNCIL)

    def research() -> list[int]:
        game.order(AbilityId.CYBERNETICS_CORE_RESEARCH_WARP_GATE, core)
        game.order(AbilityId.FORGE_RESEARCH_GROUND_WEAPONS_1, forge)
        game.order(AbilityId.TWILIGHT_COUNCIL_RESEARCH_CHARGE, council)
        return game.researched(3, steps=1)

    trials.append(game.trial("warp gate, ground weapons level 1 and charge researched at once", research))

    def warp_gates() -> list[int]:
        # Researching warp gate turns every gateway into one on its own, so one is turned back, then into a warp gate
        # again.
        (gate,) = game.own(UnitTypeId.WARP_GATE)[:1]
        game.turn(64)
        game.order(AbilityId.WARP_GATE_MORPH_GATEWAY, [gate])
        back = game.become([gate.tag], UnitTypeId.GATEWAY)
        game.turn(64)
        game.order(AbilityId.GATEWAY_MORPH_WARP_GATE, [gate])
        return back + game.become([gate.tag], UnitTypeId.WARP_GATE)

    trials.append(game.trial("a warp gate became a gateway, then a warp gate again", warp_gates))

    def warp_ins() -> list[int]:
        game.turn(64)
        for i, gate in enumerate(game.own(UnitTypeId.WARP_GATE)):
            game.order(AbilityId.WARP_GATE_WARP_IN_ZEALOT, [gate], warp_field + (2.0 * (i - 1), 2.5))
        game.appear([UnitTypeId.ZEALOT], 3, steps=1)
        warping = [unit.tag for unit in game.own(UnitTypeId.ZEALOT) if unit.build_progress < 1.0]
        return game.finish(warping, steps=1)

    trials.append(game.trial("3 zealots warped in at once", warp_ins))

    def archons() -> list[int]:
        templar = game.create(UnitTypeId.HIGH_TEMPLAR, game.spot(game.toward(6), 1), count=2)
        dark = game.create(UnitTypeId.DARK_TEMPLAR, game.spot(game.toward(6), 1), count=2)
        game.order(AbilityId.GENERAL_MORPH_ARCHON, templar)
        game.order(AbilityId.GENERAL_MORPH_ARCHON, dark)
        return game.appear([UnitTypeId.ARCHON], 2)

    trials.append(game.trial("2 archons merged, one of high and one of dark templar", archons))

    def mothership() -> list[int]:
        powered(UnitTypeId.FLEET_BEACON)
        game.order(AbilityId.NEXUS_TRAIN_MOTHERSHIP, [nexus])
        return game.appear([UnitTypeId.MOTHERSHIP], 1)

    trials.append(game.trial("a mothership trained", mothership))
    return trials


_CHEATS = ("free", "fast_build", "food", "show_map")
# The sweeps of one player against the computer: race, trials and cheats. The error sweeps play without the cheats
# that would keep minerals and supply from running short.
_SOLO: dict[str, tuple[Race, Callable[[_Game], list[Trial]], tuple[str, ...]]] = {
    "terran": (Race.TERRAN, _terran, _CHEATS),
    "zerg": (Race.ZERG, _zerg, _CHEATS),
    "protoss": (Race.PROTOSS, _protoss, _CHEATS),
    "errors": (Race.TERRAN, _terran_errors, ("show_map",)),
    "zerg-errors": (Race.ZERG, _zerg_errors, ("show_map",)),
    "attacks": (Race.TERRAN, _attack_trials, _CHEATS),
    "suppression": (Race.TERRAN, _suppression_trials, _CHEATS),
}
# `rivals` is the game of two players; `attacks` is a part of `terran`, and `suppression` is long.
_EVERY = ("terran", "zerg", "protoss", "errors", "zerg-errors", "rivals")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "sweeps", nargs="*", help=f"any of {', '.join([*_SOLO, 'rivals'])}; all but the last two when none"
    )
    parser.add_argument("--out", type=Path, default=Path("alerts.json"), help="where to write the findings")
    args = parser.parse_args()
    if unknown := set(args.sweeps) - {*_SOLO, "rivals"}:
        parser.error(f"no such sweep: {', '.join(sorted(unknown))}")
    installation = Installation.find()
    findings: dict[str, list[dict[str, object]]] = {}
    for name in args.sweeps or _EVERY:
        if name == "rivals":
            with playing_rivals(Race.TERRAN, installation) as rivals:
                me = _Game(rivals.me, step=rivals.step, guarded=False)
                enemy = _Game(rivals.enemy, step=rivals.step, guarded=False)
                findings[name] = [asdict(trial) for trial in _rival_trials(me, enemy)]
            continue
        race, sweep, cheats = _SOLO[name]
        with playing(race, installation) as sandbox:
            sandbox.cheat(*cheats)
            sandbox.client.step(8)
            findings[name] = [asdict(trial) for trial in sweep(_Game(sandbox))]
    args.out.write_text(json.dumps(findings, indent=2) + "\n", encoding="utf-8")
    logger.info("Wrote {}", args.out)


if __name__ == "__main__":
    main()
