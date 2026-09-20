"""Find how the game takes orders: what each ability does to the orders a unit has, what the queue flag does to
training and research, what several orders to one unit in one request do, what the game reports it carried out, how
precisely it keeps a point, what re-sending an order costs, which orders fail once the game has accepted them, and when
a realtime game shows an order.

Needs StarCraft II installed. Each sweep plays a game of its own, against the easiest computer, under the cheats it
names, and runs trials in turn::

    uv run python tools/sweep_orders.py
    uv run python tools/sweep_orders.py keeps-terran requests --out orders.json

- `keeps-terran`, `keeps-protoss` and `keeps-zerg` give every unit type that moves each ability it is offered, bar
  those that make, research, build, send it somewhere, gather, load or cancel, while it moves: unqueued, then queued.
- `production-terran`, `production-protoss` and `production-zerg` give structures that are making something more to
  make, research and morph, unqueued and queued, and other orders.
- `requests` sends several orders to one unit in one request, orders to units that cannot take them, odd targets and
  odd points, a camera move, an autocast toggle, and 500 commands at once.
- `repeats` re-sends what a unit is doing every step, or every few, and measures what it costs.
- `errors` plays without cheats, so that minerals and supply run short; `spell-errors` has orders fail for want of
  energy, and gives spells and builds to groups.
- `realtime` plays a realtime game and watches for each order.
- `queues` gives structures that are making something each cancel there is, more cancels than they hold, more to
  make or a morph or an add-on after cancels, in the same request and a step later, and more marines than a barracks
  with each add-on holds.
- `refunds` plays without `free`, and has a cancel's refund pay for what follows it, on the same structure or
  another, in the same request and a step later.
- `structure-abilities-terran`, `-protoss` and `-zerg` give each structure that makes something every ability it is
  offered that makes nothing, while it is making something.
- `toggles-terran`, `-protoss` and `-zerg` turn each toggle on while its unit moves, then off, which a unit is
  offered only once the first half has taken.
- `supply` plays under a real supply cap, and gives structures more to make than the supply left.
- `slots-terran`, `-protoss` and `-zerg` give a structure with a reactor more than it makes at once, a research
  structure every research it has, a larva three morphs and a warp gate two warp-ins.
- `cancels-offered` asks a morph, an add-on, a research, a train and a part-built structure what cancels them,
  at real prices, so that what each gives back is recorded too.
- `producer-dies` kills what is making something, and reads what the observations after say.
- `cancel-a-middle-item` joins with the interface a player has, and asks the game's own production panel to drop the
  third of five queued, which no raw ability can name.

What it finds is written as JSON, one entry per trial: every verdict the game answered, one list per request sent,
the orders of the units the trial read, with when, what the game reported it carried out, every action error, and
what the trial measured. `docs/game-behavior.md` holds the findings. Measured in game while writing this:

- A unit made out of sight is not found made, so a unit of this player's is made first where it would stand.
- A vital set to 0 by debug command is set to its most, so a unit is drained to 1 energy.
- Under `tech_tree` a barracks without an add-on trains two marines at once, so the terran and zerg production sweeps
  play without it and make what their orders need.
- A battlecruiser moves by an ability of its own, and a carrier shows its interceptors being built before its move, so
  a unit's move is read off its orders rather than assumed.
- The `gas` cheat hands out no vespene, so `refunds` has an SCV and an orbital command paid for, not a research.
"""

import argparse
import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from functools import partial
from pathlib import Path
from typing import Self

from _sandbox import OpenGround, Sandbox, playing
from loguru import logger
from s2clientprotocol import common_pb2, debug_pb2, error_pb2, raw_pb2, sc2api_pb2, ui_pb2

from sc2nachos.gamedata import Attribute, GameData, OrderBehavior
from sc2nachos.gamemap import GameMap
from sc2nachos.geometry import Point
from sc2nachos.ids import AbilityId, BuffId, UnitTypeId
from sc2nachos.ids.raw import RawAbilityId
from sc2nachos.launch import Installation
from sc2nachos.match import Race
from sc2nachos.protocol import ProtocolError

_OWN = raw_pb2.Alliance.Self
_SNAPSHOT = raw_pb2.DisplayType.Snapshot
# Steps waited after each trial, so that a late report or action error is not taken for the next trial's.
_SETTLE = 16
# What each ability is aimed at, as the game's tables name it.
_NOTHING, _POINT, _UNIT, _POINT_OR_UNIT, _POINT_OR_NOTHING = 1, 2, 3, 4, 5

type Target = Point | raw_pb2.Unit | int | None
"""What an order is aimed at: a point, a unit, a unit's tag, or nothing."""


@dataclass(slots=True)
class Trial:
    """What one trial did, and what the game answered and reported meanwhile."""

    name: str
    verdicts: list[list[str]] = field(default_factory=list)
    """The game's answer to each request sent, one verdict per action in it."""
    reads: list[dict[str, object]] = field(default_factory=list)
    """The units read, each read labeled, with the step it was made at."""
    reported: list[dict[str, object]] = field(default_factory=list)
    """What the game reported this player did, with the step it was carried out at and the step it was seen at."""
    errors: list[dict[str, object]] = field(default_factory=list)
    """Each action error, with the step it was seen at."""
    notes: dict[str, object] = field(default_factory=dict)
    """What the trial measured or concluded."""


def _at(unit: raw_pb2.Unit) -> Point:
    return Point((unit.pos.x, unit.pos.y))


def _name(ability: int) -> str:
    """The curated name of `ability`, or the raw catalog's where it has none."""
    if (curated := AbilityId.get(ability)) is not None:
        return curated.name
    try:
        return RawAbilityId(ability).name
    except ValueError:
        return str(ability)


def _buff_name(buff: int) -> str:
    curated = BuffId.get(buff)
    return curated.name if curated is not None else str(buff)


def _raw_name(ability: int) -> str:
    try:
        return RawAbilityId(ability).name
    except ValueError:
        return str(ability)


def _point(point: common_pb2.Point) -> list[float]:
    return [point.x, point.y]


def _order(order: raw_pb2.UnitOrder) -> dict[str, object]:
    entry: dict[str, object] = {"ability": _name(order.ability_id)}
    match order.WhichOneof("target"):
        case "target_world_space_pos":
            entry["point"] = _point(order.target_world_space_pos)
        case "target_unit_tag":
            entry["unit"] = order.target_unit_tag
    if order.progress:
        entry["progress"] = round(order.progress, 3)
    return entry


def _reported(action: sc2api_pb2.Action, seen: int) -> dict[str, object]:
    entry: dict[str, object] = {"step": action.game_loop, "seen": seen}
    if not action.HasField("action_raw"):
        entry["kind"] = [field.name for field, _ in action.ListFields()]
        return entry
    raw = action.action_raw
    match raw.WhichOneof("action"):
        case "unit_command":
            command = raw.unit_command
            entry |= {"kind": "command", "ability": _name(command.ability_id), "units": list(command.unit_tags)}
            match command.WhichOneof("target"):
                case "target_world_space_pos":
                    entry["point"] = [command.target_world_space_pos.x, command.target_world_space_pos.y]
                case "target_unit_tag":
                    entry["unit"] = command.target_unit_tag
            entry["queued"] = command.queue_command
        case "camera_move":
            entry |= {"kind": "camera", "center": _point(raw.camera_move.center_world_space)}
        case "toggle_autocast":
            toggle = raw.toggle_autocast
            entry |= {"kind": "autocast", "ability": _name(toggle.ability_id), "units": list(toggle.unit_tags)}
        case other:
            entry["kind"] = other
    return entry


def _error(error: sc2api_pb2.ActionError, seen: int) -> dict[str, object]:
    return {
        "seen": seen,
        "unit": error.unit_tag,
        "ability": _name(error.ability_id),
        "result": error_pb2.ActionResult.Name(error.result),
    }


class _Game:
    """A sweep's game, stepped and observed here only, so that nothing the game reports goes unrecorded."""

    def __init__(self, sandbox: Sandbox, *, realtime: bool = False) -> None:
        self.sandbox = sandbox
        self.client = sandbox.client
        self.player = sandbox.player
        self.enemy = 3 - sandbox.player
        self.realtime = realtime
        self.map = GameMap(self.client.game_info())
        raw_data = self.client.game_data()
        self.data = GameData(raw_data)
        self.aims = {entry.ability_id: entry.target for entry in raw_data.abilities}
        self.step = 0
        self.units: dict[int, raw_pb2.Unit] = {}
        self.minerals = 0
        self.vespene = 0
        self.supply = (0, 0)
        self.running: Trial | None = None
        # Every unit made by debug command, the enemy's among them, which a trial kills once done.
        self.made: set[int] = set()
        # Ground the trial running has claimed, given back once it has killed what it made.
        self._claimed: list[Point] = []
        # What of the computer's can fight: everything with a weapon but its workers. It is killed as it comes, so
        # that it never ends a long sweep's game.
        workers = {UnitTypeId.SCV, UnitTypeId.PROBE, UnitTypeId.DRONE}
        self._fighters = frozenset(row.id for row in self.data.units.values() if row.weapons) - workers
        self._guarded_at = 0
        self.observe()
        self.ground = OpenGround(self.map, self.units.values())
        townhalls = (UnitTypeId.COMMAND_CENTER, UnitTypeId.NEXUS, UnitTypeId.HATCHERY)
        self.home = _at(self.own(*townhalls)[0])
        self.middle = self.map.playable_area.center

    # --- Reading the game

    def observe(self, game_loop: int | None = None) -> None:
        """Observe the game, at `game_loop` in a realtime game, recording what it reports into the trial running."""
        response = self.client.observation(game_loop=game_loop)
        observation = response.observation
        self.step = observation.game_loop
        self.units = {unit.tag: unit for unit in observation.raw_data.units}
        common = observation.player_common
        self.minerals = common.minerals
        self.vespene = common.vespene
        self.supply = (common.food_used, common.food_cap)
        if self.running is not None:
            self.running.reported.extend(_reported(action, self.step) for action in response.actions)
            self.running.errors.extend(_error(error, self.step) for error in response.action_errors)

    def turn(self, steps: int = 1) -> None:
        """Let `steps` pass, then observe."""
        if self.realtime:
            self.observe(self.step + steps)
            return
        while steps > 0:
            chunk = min(steps, 64)
            self.client.step(chunk)
            steps -= chunk
        self.observe()
        if self.step - self._guarded_at >= 64:
            self._guarded_at = self.step
            fighters = [
                tag
                for tag, unit in self.units.items()
                if unit.alliance == raw_pb2.Alliance.Enemy and unit.unit_type in self._fighters and tag not in self.made
            ]
            if fighters:
                self.sandbox.kill(fighters)

    def until(self, done: Callable[[], bool], *, steps: int = 1, limit: int = 3000) -> bool:
        """Turn until `done`, for at most `limit` steps, and say whether it came to be."""
        end = self.step + limit
        while not done():
            if self.step >= end:
                logger.warning("Gave up waiting at step {}", self.step)
                return False
            self.turn(steps)
        return True

    def own(self, *unit_types: UnitTypeId) -> list[raw_pb2.Unit]:
        return [unit for unit in self.units.values() if unit.alliance == _OWN and unit.unit_type in unit_types]

    def unit(self, tag: int) -> raw_pb2.Unit | None:
        return self.units.get(tag)

    def read(self, label: str, units: Iterable[raw_pb2.Unit | int]) -> list[dict[str, object]]:
        """Record each unit's orders as they stand, under `label`, and answer them."""
        found: list[dict[str, object]] = []
        for tag in (unit if isinstance(unit, int) else unit.tag for unit in units):
            if (unit := self.units.get(tag)) is None:
                found.append({"tag": tag, "gone": True})
                continue
            entry: dict[str, object] = {
                "tag": tag,
                "type": _type_name(unit.unit_type),
                "at": [round(unit.pos.x, 3), round(unit.pos.y, 3)],
                "orders": [_order(order) for order in unit.orders],
                "life": round(unit.health + unit.shield, 1),
            }
            if unit.energy_max:
                entry["energy"] = round(unit.energy, 1)
            if unit.buff_ids:
                entry["buffs"] = [_buff_name(buff) for buff in unit.buff_ids]
            found.append(entry)
        if self.running is not None:
            self.running.reads.append({"label": label, "step": self.step, "units": found})
        return found

    # --- Acting on it

    def command(
        self, ability: int, units: Iterable[raw_pb2.Unit | int], target: Target = None, *, queued: bool = False
    ) -> sc2api_pb2.Action:
        """The action that orders `ability` on `units` together, aimed at `target`."""
        tags = [unit if isinstance(unit, int) else unit.tag for unit in units]
        command = raw_pb2.ActionRawUnitCommand(ability_id=ability, unit_tags=tags, queue_command=queued)
        if isinstance(target, Point):
            command.target_world_space_pos.x, command.target_world_space_pos.y = target
        elif isinstance(target, raw_pb2.Unit):
            command.target_unit_tag = target.tag
        elif target is not None:
            command.target_unit_tag = target
        return sc2api_pb2.Action(action_raw=raw_pb2.ActionRaw(unit_command=command))

    def act(self, *actions: sc2api_pb2.Action) -> list[str]:
        """Send `actions` in one request, and answer each verdict, recorded into the trial running."""
        try:
            results = self.client.act(actions).result
        except ProtocolError as error:
            verdicts = [f"request refused: {error}"]
        else:
            verdicts = [error_pb2.ActionResult.Name(result) for result in results]
        if self.running is not None:
            self.running.verdicts.append(verdicts)
        return verdicts

    def order(
        self, ability: int, units: Iterable[raw_pb2.Unit | int], target: Target = None, *, queued: bool = False
    ) -> str:
        """Order `ability` on `units` together, and answer the verdict."""
        verdicts = self.act(self.command(ability, units, target, queued=queued))
        return verdicts[0] if verdicts else "no verdict"

    def camera(self, at: Point) -> list[str]:
        move = raw_pb2.ActionRawCameraMove(center_world_space=common_pb2.Point(x=at.x, y=at.y))
        return self.act(sc2api_pb2.Action(action_raw=raw_pb2.ActionRaw(camera_move=move)))

    def create(
        self, unit_type: UnitTypeId, at: Point, *, owner: int | None = None, count: int = 1
    ) -> list[raw_pb2.Unit]:
        """Create `count` of `unit_type` at `at`, this player's unless another `owner` is given."""
        before = set(self.units)
        command = self.sandbox.create(unit_type, owner or self.player, at)
        self.sandbox.debug(*([command] * count))
        made: list[raw_pb2.Unit] = []
        # What is made can show up a few steps late, as vision catches up with it.
        for _ in range(4):
            self.turn(4)
            made = [unit for tag, unit in self.units.items() if tag not in before and unit.unit_type == unit_type]
            if len(made) >= count:
                break
        self.made.update(unit.tag for unit in made)
        if len(made) < count:
            logger.warning("Made {} of the {} {} asked for", len(made), count, unit_type.name)
        return made

    def set_energy(self, value: float, units: Iterable[raw_pb2.Unit]) -> None:
        self.sandbox.set_value(debug_pb2.DebugSetUnitValue.Energy, value, (unit.tag for unit in units))

    def kill(self, units: Iterable[raw_pb2.Unit | int]) -> None:
        self.sandbox.kill(unit if isinstance(unit, int) else unit.tag for unit in units)
        self.turn(2)

    def spot(self, near: Point, half: int) -> Point:
        """The center of the free square `2 * half + 1` tiles across nearest to `near`, which is then taken.

        Ground claimed while a trial runs is given back once it has killed what it made, so a long sweep does not
        push each trial further out than the last, until a structure has no room beside it for an add-on.
        """
        at = self.ground.claim(near, half)
        if self.running is not None:
            self._claimed.append(at)
        return at

    def toward(self, distance: float) -> Point:
        """The point `distance` from home toward the middle of the map."""
        return self.home.towards(self.middle, distance)

    def trial(self, name: str, run: Callable[[Trial], None], *, clear: bool = True) -> Trial:
        """Run `run` as the trial `name`, then kill what it made and give back the ground it claimed if `clear`."""
        logger.info("Trial: {}", name)
        trial = Trial(name)
        before = set(self.units)
        self.running = trial
        self._claimed = []
        try:
            run(trial)
            self.turn(_SETTLE)
        except Exception as error:  # A trial that fails is recorded, and the sweep goes on.
            logger.exception("Trial {} failed", name)
            trial.notes["failed"] = repr(error)
        finally:
            self.running = None
        # Only what this player has and what the trial made: killing what the computer makes has it give up.
        new = [
            tag for tag, unit in self.units.items() if tag not in before and (unit.alliance == _OWN or tag in self.made)
        ]
        if clear and new:
            self.kill(new)
        if clear:
            # Once nothing of the trial's stands there any more; a trial whose units stay keeps its ground too.
            for at in self._claimed:
                self.ground.release(at)
        self._claimed = []
        logger.info("  verdicts {}, errors {}, notes {}", trial.verdicts, trial.errors, trial.notes)
        return trial


def _type_name(unit_type: int) -> str:
    curated = UnitTypeId.get(unit_type)
    return curated.name if curated is not None else str(unit_type)


# --- 1. What an ability does to the orders a unit has


# What is not given to a moving unit: anything that makes, researches or builds, sends the unit somewhere, gathers,
# loads, or cancels, halts or lands, each of which says what the unit does next by its nature.
_NOT_TRIED = ("Train", "Research", "Build", "Move", "Patrol", "Attack", "attack", "Smart", "Rally", "Harvest")
_NOT_TRIED += ("Load", "Unload", "Cancel", "Halt", "Land", "Lift")
# Unit types never given an order of their own here: what the game makes and moves on its own, and what waits.
_NOT_PERFORMERS = frozenset(
    {
        UnitTypeId.LARVA,
        UnitTypeId.EGG,
        UnitTypeId.BANELING_COCOON,
        UnitTypeId.BROOD_LORD_COCOON,
        UnitTypeId.RAVAGER_COCOON,
        UnitTypeId.LURKER_EGG,
        UnitTypeId.OVERLORD_TRANSPORT_COCOON,
        UnitTypeId.INTERCEPTOR,
        UnitTypeId.LOCUST,
        UnitTypeId.LOCUST_FLYING,
        UnitTypeId.BROODLING,
        UnitTypeId.CHANGELING,
        UnitTypeId.MULE,
        UnitTypeId.REAPER_GRENADE,
    }
)
# What a unit's ability is aimed at: enemies of every kind a spell can ask for, and units of this player's, biological
# and mechanical, for what heals or repairs, all standing close enough that no caster has to walk.
_ENEMY_AIMS = (UnitTypeId.MARAUDER, UnitTypeId.SIEGE_TANK, UnitTypeId.VIKING)
_OWN_AIMS = (UnitTypeId.MARINE, UnitTypeId.SIEGE_TANK)
# How far a unit is sent before its ability, and when its orders are read afterwards.
_MOVE_DISTANCE = 20
_READS = (1, 6, 48)


def _keeps(game: _Game, race: Race) -> list[Trial]:
    trials: list[Trial] = []
    makers = {row.creation_ability for row in game.data.units.values() if row.base_type is None}
    performers = [
        row.id
        for row in sorted(game.data.units.values(), key=lambda row: row.id.name)
        if row.race is race
        and row.speed > 0
        and Attribute.STRUCTURE not in row.attributes
        and row.base_type is None
        and row.id not in _NOT_PERFORMERS
    ]
    pad = game.spot(game.toward(12), 4)
    for performer in performers:
        made = game.create(performer, pad)
        if not made:
            continue
        offered = game.sandbox.offered([made[0].tag]).get(made[0].tag, [])
        game.kill(made)
        for ability in offered:
            name = _raw_name(ability)
            if any(fragment in name for fragment in _NOT_TRIED) or AbilityId.get(ability) in makers:
                continue
            for queued in (False, True):
                label = f"{performer.name} given {_name(ability)}{' queued' if queued else ''} while moving"
                trials.append(game.trial(label, partial(_cast, game, performer, ability, queued, pad)))
    return trials


def _cast(game: _Game, performer: UnitTypeId, ability: int, queued: bool, pad: Point, trial: Trial) -> None:
    aim_kind = game.aims.get(ability, _NOTHING)
    wants_unit = aim_kind in (_UNIT, _POINT_OR_UNIT)
    requests = [(performer, game.player, pad)]
    if wants_unit:
        requests += [(target, game.enemy, pad + (3, -2 + 2 * index)) for index, target in enumerate(_ENEMY_AIMS)]
        requests += [(target, game.player, pad + (-2, -2 + 4 * index)) for index, target in enumerate(_OWN_AIMS)]
    made = game.sandbox.spawn(requests)
    game.made.update(unit.tag for unit in made)
    game.observe()
    caster = next((unit for unit in made if unit.unit_type == performer and unit.owner == game.player), None)
    if caster is None:
        trial.notes["class"] = "not made"
        return
    game.set_energy(200, [caster])
    # This player's units aimed at are hurt, so that what heals or repairs has something to do.
    helped = [unit.tag for unit in made if unit.owner == game.player and unit.tag != caster.tag]
    game.sandbox.set_value(debug_pb2.DebugSetUnitValue.Life, 20, helped)
    destination = _at(caster).towards(game.middle, _MOVE_DISTANCE)
    game.order(AbilityId.GENERAL_MOVE, [caster], destination)
    game.turn(2)
    game.read("moving", [caster])
    # The move as the unit shows it: a battlecruiser moves by an ability of its own, and a carrier building
    # interceptors shows that first.
    moving = game.unit(caster.tag)
    shown = [_Shown.of(order) for order in moving.orders] if moving is not None else []
    move = next((order for order in shown if "MOVE" in order.ability), None)
    aims: list[Target] = []
    if aim_kind in (_NOTHING, _POINT_OR_NOTHING):
        aims.append(None)
    if wants_unit:
        aims += [unit.tag for unit in made if unit.tag != caster.tag]
    if aim_kind in (_POINT, _POINT_OR_UNIT, _POINT_OR_NOTHING):
        # Off to the side of its way, so that a unit sent there is seen to leave it.
        aims.append(_at(game.units.get(caster.tag, caster)) + (0, 3))
    verdict = "no aim"
    for aim in aims:
        verdict = game.order(ability, [caster], aim, queued=queued)
        if verdict == "Success":
            trial.notes["aim"] = "nothing" if aim is None else "point" if isinstance(aim, Point) else "unit"
            break
    orders: list[list[_Shown] | None] = []
    distances: list[float | None] = []
    last = 0
    for at in _READS:
        game.turn(at - last)
        last = at
        game.read(f"{at} after", [caster])
        unit = game.unit(caster.tag)
        orders.append(None if unit is None else [_Shown.of(order) for order in unit.orders])
        distances.append(None if unit is None else round(_at(unit).distance_to(destination), 1))
    trial.notes["class"] = _class(verdict, move, orders)
    trial.notes["distance left"] = distances


@dataclass(frozen=True, slots=True)
class _Shown:
    """An order as a unit shows it: its ability, and the point it is aimed at, if any."""

    ability: str
    point: tuple[float, float] | None

    @classmethod
    def of(cls, order: raw_pb2.UnitOrder) -> Self:
        at = order.target_world_space_pos if order.HasField("target_world_space_pos") else None
        return cls(_name(order.ability_id), None if at is None else (at.x, at.y))


def _class(verdict: str, move: _Shown | None, orders: Sequence[list[_Shown] | None]) -> str:
    """How an ability given to a moving unit left its `move`, from the unit's orders read after it."""
    if verdict != "Success":
        return f"refused: {verdict}"
    first = orders[0]
    if first is None:
        return "gone"
    if move not in first:
        later = any(shown is not None and move in shown for shown in orders[1:])
        return "replaces, then resumes" if later else "replaces"
    if first.index(move) == 0:
        return "keeps" if len(first) == 1 else "keeps, the ability behind it"
    return "goes first, then resumes"


# --- 2. Production and the queue flag


def _terran_production(game: _Game) -> list[Trial]:
    trials: list[Trial] = []
    (center,) = game.own(UnitTypeId.COMMAND_CENTER)[:1]
    train_scv = AbilityId.COMMAND_CENTER_TRAIN_SCV

    def trains(trial: Trial) -> None:
        game.order(train_scv, [center])
        game.turn(2)
        game.read("one SCV", [center])
        game.order(train_scv, [center])
        game.turn(2)
        game.read("then one unqueued", [center])
        game.order(train_scv, [center], queued=True)
        game.turn(2)
        game.read("then one queued", [center])
        game.act(game.command(train_scv, [center]), game.command(train_scv, [center]))
        game.turn(2)
        game.read("then two unqueued in one request", [center])
        game.order(train_scv, [center])
        game.turn(2)
        game.read("then a sixth", [center])
        game.act(*(game.command(AbilityId.GENERAL_CANCEL_LAST, [center]) for _ in range(5)))

    trials.append(game.trial("a command center training an SCV given more to train", trains))

    def reactor(trial: Trial) -> None:
        (plain,) = game.create(UnitTypeId.BARRACKS, game.spot(game.toward(8), 4))
        (paired,) = game.create(UnitTypeId.BARRACKS, game.spot(game.toward(8), 4))
        game.order(AbilityId.BARRACKS_BUILD_REACTOR, [paired])
        game.until(lambda: _add_on_finished(game, paired.tag), steps=16, limit=2400)
        marine = AbilityId.BARRACKS_TRAIN_MARINE
        # A barracks is offered no marine in the step its add-on first reads finished.
        game.until(lambda: marine in game.sandbox.offered([paired.tag])[paired.tag], limit=64)
        trial.notes["add-ons"] = {
            label: _type_name(add_on.unit_type) if (add_on := _add_on(game, barracks.tag)) is not None else None
            for barracks, label in ((paired, "paired"), (plain, "plain"))
        }
        for barracks, label in ((paired, "with a reactor"), (plain, "without an add-on")):
            game.act(game.command(marine, [barracks]), game.command(marine, [barracks]))
            game.turn(2)
            game.read(f"two marines unqueued in one request, {label}", [barracks])
            game.act(game.command(marine, [barracks]), game.command(marine, [barracks]))
            game.turn(2)
            game.read(f"two more, {label}", [barracks])
        before = {unit.tag for unit in game.own(UnitTypeId.MARINE)}
        game.turn(420)
        game.read("420 steps later, a marine's time up", [paired, plain])
        trial.notes["marines made by both"] = sum(1 for u in game.own(UnitTypeId.MARINE) if u.tag not in before)

    trials.append(game.trial("barracks given two marines in one request", reactor))

    def research(trial: Trial) -> None:
        bays = [game.create(UnitTypeId.ENGINEERING_BAY, game.spot(game.toward(8), 2))[0] for _ in range(2)]
        game.order(AbilityId.ENGINEERING_BAY_RESEARCH_INFANTRY_WEAPONS_1, [bays[0]])
        game.order(AbilityId.ENGINEERING_BAY_RESEARCH_BUILDING_ARMOR, [bays[1]])
        game.turn(2)
        game.read("researching weapons and building armor", bays)
        game.order(AbilityId.ENGINEERING_BAY_RESEARCH_INFANTRY_ARMOR_1, [bays[0]])
        game.order(AbilityId.ENGINEERING_BAY_RESEARCH_HISEC_AUTO_TRACKING, [bays[1]], queued=True)
        game.turn(2)
        game.read("infantry armor given the first unqueued, hi-sec the second queued", bays)
        (third,) = game.create(UnitTypeId.ENGINEERING_BAY, game.spot(game.toward(8), 2))
        game.order(AbilityId.ENGINEERING_BAY_RESEARCH_INFANTRY_WEAPONS_1, [third])
        game.turn(2)
        game.read("a third bay given the research the first is doing", [third])

    trials.append(game.trial("engineering bays researching given another research", research))

    def morphs(trial: Trial) -> None:
        # What an orbital command and a planetary fortress need.
        game.create(UnitTypeId.BARRACKS, game.spot(game.toward(8), 3))
        game.create(UnitTypeId.ENGINEERING_BAY, game.spot(game.toward(8), 2))
        centers = [game.create(UnitTypeId.COMMAND_CENTER, game.spot(game.toward(10), 3))[0] for _ in range(5)]
        for each in centers[:4]:
            game.order(train_scv, [each])
        game.turn(2)
        orbital = AbilityId.COMMAND_CENTER_MORPH_ORBITAL_COMMAND
        planetary = AbilityId.COMMAND_CENTER_MORPH_PLANETARY_FORTRESS
        given = ["orbital unqueued", "orbital queued", "planetary unqueued", "planetary queued", "orbital to one idle"]
        trial.notes["given"] = given
        game.order(orbital, [centers[0]])
        game.order(orbital, [centers[1]], queued=True)
        game.order(planetary, [centers[2]])
        game.order(planetary, [centers[3]], queued=True)
        game.order(orbital, [centers[4]])
        game.turn(2)
        game.read("just after", centers)
        game.turn(300)
        game.read("300 steps later, the SCV's time up", centers)

    trials.append(game.trial("command centers training an SCV given a morph", morphs))

    def busy(trial: Trial) -> None:
        marine = AbilityId.BARRACKS_TRAIN_MARINE
        cases = ("rally", "lift", "stop", "cancel last", "move", "hold position")
        barracks = [game.create(UnitTypeId.BARRACKS, game.spot(game.toward(14), 3))[0] for _ in cases]
        for each in barracks:
            game.act(game.command(marine, [each]), game.command(marine, [each]))
        game.turn(2)
        game.read("each training two marines", barracks)
        offered = game.sandbox.offered([barracks[0].tag])[barracks[0].tag]
        rally = next(ability for ability in offered if "Rally" in _raw_name(ability))
        trial.notes["given"] = list(cases)
        trial.notes["rally"] = _name(rally)
        game.order(rally, [barracks[0]], game.toward(20))
        game.order(AbilityId.BARRACKS_LIFT, [barracks[1]])
        game.order(AbilityId.GENERAL_STOP, [barracks[2]])
        game.order(AbilityId.GENERAL_CANCEL_LAST, [barracks[3]])
        game.order(AbilityId.GENERAL_MOVE, [barracks[4]], game.toward(20))
        game.order(AbilityId.GENERAL_HOLD_POSITION, [barracks[5]])
        game.turn(2)
        game.read("each given its order", barracks)

    trials.append(game.trial("barracks training given other orders", busy))
    return trials


def _add_on(game: _Game, tag: int) -> raw_pb2.Unit | None:
    structure = game.unit(tag)
    return game.unit(structure.add_on_tag) if structure is not None and structure.add_on_tag else None


def _add_on_finished(game: _Game, tag: int) -> bool:
    add_on = _add_on(game, tag)
    return add_on is not None and add_on.build_progress == 1.0


def _zerg_production(game: _Game) -> list[Trial]:
    trials: list[Trial] = []
    (hatchery,) = game.own(UnitTypeId.HATCHERY)[:1]
    queen = AbilityId.HATCHERY_TRAIN_QUEEN

    def queens(trial: Trial) -> None:
        game.create(UnitTypeId.SPAWNING_POOL, game.spot(game.toward(8), 2))
        game.order(queen, [hatchery])
        game.turn(2)
        game.read("one queen", [hatchery])
        game.order(queen, [hatchery])
        game.turn(2)
        game.read("then one unqueued", [hatchery])
        game.order(queen, [hatchery], queued=True)
        game.turn(2)
        game.read("then one queued", [hatchery])
        game.act(*(game.command(AbilityId.GENERAL_CANCEL_LAST, [hatchery]) for _ in range(3)))

    trials.append(game.trial("a hatchery training a queen given more queens", queens))

    def lairs(trial: Trial) -> None:
        game.create(UnitTypeId.SPAWNING_POOL, game.spot(game.toward(8), 2))
        hatcheries = [game.create(UnitTypeId.HATCHERY, game.spot(game.toward(12), 3))[0] for _ in range(2)]
        for each in hatcheries:
            game.order(queen, [each])
        game.turn(2)
        game.order(AbilityId.HATCHERY_MORPH_LAIR, [hatcheries[0]])
        game.order(AbilityId.HATCHERY_MORPH_LAIR, [hatcheries[1]], queued=True)
        trial.notes["given"] = ["lair unqueued", "lair queued"]
        game.turn(2)
        game.read("just after", hatcheries)
        game.turn(820)
        game.read("820 steps later, the queen's time up", hatcheries)

    trials.append(game.trial("hatcheries training a queen given a lair", lairs))

    def inject(trial: Trial) -> None:
        game.create(UnitTypeId.SPAWNING_POOL, game.spot(game.toward(8), 2))
        (other,) = game.create(UnitTypeId.HATCHERY, game.spot(game.toward(12), 3))
        game.order(queen, [other])
        (injector,) = game.create(UnitTypeId.QUEEN, _at(other) + (3, 0))
        game.set_energy(200, [injector])
        game.turn(2)
        game.order(AbilityId.QUEEN_INJECT, [injector], other)
        game.turn(16)
        game.read("inject given", [other, injector])

    trials.append(game.trial("a hatchery training a queen injected", inject))

    def larvae(trial: Trial) -> None:
        (larva,) = game.own(UnitTypeId.LARVA)[:1]
        drone = RawAbilityId.LarvaTrain_Drone
        game.act(game.command(drone, [larva]), game.command(drone, [larva]))
        game.turn(2)
        game.read("a larva given two drones in one request", [larva])
        trial.notes["drones and eggs"] = len(game.own(UnitTypeId.DRONE)), len(game.own(UnitTypeId.EGG))

    trials.append(game.trial("a larva given two drones", larvae, clear=False))

    def research(trial: Trial) -> None:
        chambers = [game.create(UnitTypeId.EVOLUTION_CHAMBER, game.spot(game.toward(8), 2))[0] for _ in range(2)]
        game.order(AbilityId.EVOLUTION_CHAMBER_RESEARCH_MELEE_WEAPONS_1, [chambers[0]])
        game.order(AbilityId.EVOLUTION_CHAMBER_RESEARCH_RANGE_WEAPONS_1, [chambers[1]])
        game.turn(2)
        game.order(AbilityId.EVOLUTION_CHAMBER_RESEARCH_GROUND_ARMOR_1, [chambers[0]])
        game.order(AbilityId.EVOLUTION_CHAMBER_RESEARCH_RANGE_WEAPONS_2, [chambers[1]], queued=True)
        game.turn(2)
        game.read("armor given the first unqueued and the second queued", chambers)

    trials.append(game.trial("evolution chambers researching given another research", research))
    return trials


def _protoss_production(game: _Game) -> list[Trial]:
    trials: list[Trial] = []
    (nexus,) = game.own(UnitTypeId.NEXUS)[:1]
    probe = AbilityId.NEXUS_TRAIN_PROBE

    def probes(trial: Trial) -> None:
        game.order(probe, [nexus])
        game.turn(2)
        game.read("one probe", [nexus])
        game.order(probe, [nexus])
        game.turn(2)
        game.read("then one unqueued", [nexus])
        game.order(probe, [nexus], queued=True)
        game.turn(2)
        game.read("then one queued", [nexus])
        game.set_energy(200, [nexus])
        game.turn(1)
        game.order(AbilityId.NEXUS_CHRONO_BOOST, [nexus], nexus)
        game.turn(2)
        game.read("then chrono boosted by itself", [nexus])
        game.act(*(game.command(AbilityId.GENERAL_CANCEL_LAST, [nexus]) for _ in range(3)))

    trials.append(game.trial("a nexus training a probe given more probes and chrono boost", probes))

    def powered(unit_type: UnitTypeId, count: int) -> list[raw_pb2.Unit]:
        spot = game.spot(game.toward(12), 1)
        game.create(UnitTypeId.PYLON, spot)
        return [game.create(unit_type, game.spot(spot, 2))[0] for _ in range(count)]

    def gateways(trial: Trial) -> None:
        gates = powered(UnitTypeId.GATEWAY, 2)
        for gate in gates:
            game.order(AbilityId.GATEWAY_TRAIN_ZEALOT, [gate])
        game.turn(2)
        game.order(AbilityId.GATEWAY_MORPH_WARP_GATE, [gates[0]])
        game.order(AbilityId.GATEWAY_MORPH_WARP_GATE, [gates[1]], queued=True)
        trial.notes["given"] = ["warp gate unqueued", "warp gate queued"]
        game.turn(2)
        game.read("just after", gates)
        game.turn(620)
        game.read("620 steps later, the zealot's time up", gates)

    trials.append(game.trial("gateways training a zealot given the warp gate morph", gateways))

    def research(trial: Trial) -> None:
        forges = powered(UnitTypeId.FORGE, 2)
        game.order(AbilityId.FORGE_RESEARCH_GROUND_WEAPONS_1, [forges[0]])
        game.order(AbilityId.FORGE_RESEARCH_SHIELDS_1, [forges[1]])
        game.turn(2)
        game.order(AbilityId.FORGE_RESEARCH_GROUND_ARMOR_1, [forges[0]])
        game.order(AbilityId.FORGE_RESEARCH_SHIELDS_2, [forges[1]], queued=True)
        game.turn(2)
        game.read("armor given the first unqueued and the second queued", forges)

    trials.append(game.trial("forges researching given another research", research))
    return trials


# --- 3, 4, 5 and 9. Requests, reports, points, the camera, autocast and a request's size


def _requests(game: _Game) -> list[Trial]:
    trials: list[Trial] = []
    start = game.toward(8)
    far, near, aside = game.toward(24), game.toward(16), start.towards(game.home, 6)
    move = AbilityId.GENERAL_MOVE

    def marine() -> raw_pb2.Unit:
        return game.create(UnitTypeId.MARINE, start)[0]

    def two_moves(trial: Trial) -> None:
        unit = marine()
        game.act(game.command(move, [unit], far), game.command(move, [unit], near))
        game.turn(1)
        game.read("after moves to far, then near", [unit])

    trials.append(game.trial("two unqueued moves to one marine in one request", two_moves))

    def then_queued(trial: Trial) -> None:
        unit = marine()
        game.act(game.command(move, [unit], far), game.command(move, [unit], near, queued=True))
        game.turn(1)
        game.read("after a move to far, then one to near queued", [unit])

    trials.append(game.trial("an unqueued move, then a queued one, in one request", then_queued))

    def queued_first(trial: Trial) -> None:
        unit = marine()
        game.order(move, [unit], aside)
        game.turn(2)
        game.act(game.command(move, [unit], far, queued=True), game.command(move, [unit], near))
        game.turn(1)
        game.read("moving aside, after a move to far queued, then one to near", [unit])

    trials.append(game.trial("a queued move, then an unqueued one, in one request", queued_first))

    def stims(trial: Trial) -> None:
        first, second = marine(), marine()
        game.act(game.command(AbilityId.MARINE_STIM, [first]), game.command(move, [first], far))
        game.act(game.command(move, [second], far), game.command(AbilityId.MARINE_STIM, [second]))
        game.turn(1)
        game.read("the first stimmed, then moved; the second moved, then stimmed", [first, second])

    trials.append(game.trial("stim and a move in one request, each way round", stims))

    def same_tag(trial: Trial) -> None:
        unit = marine()
        game.act(game.command(move, [unit.tag, unit.tag], far))
        game.turn(1)
        game.read("after one move naming it twice", [unit])

    trials.append(game.trial("one command naming the same marine twice", same_tag))

    def mixed_group(trial: Trial) -> None:
        marines = game.create(UnitTypeId.MARINE, start, count=3)
        (depot,) = game.create(UnitTypeId.SUPPLY_DEPOT, game.spot(game.toward(12), 1))
        game.order(move, [*marines, depot], far)
        game.turn(1)
        game.read("after one move to all four", [*marines, depot])

    trials.append(game.trial("three marines and a supply depot given one move", mixed_group))

    def cannot(trial: Trial) -> None:
        dead = marine()
        game.kill([dead])
        (enemy,) = game.create(UnitTypeId.SCV, start + (3, 0), owner=game.enemy)
        trial.notes["given"] = ["a dead marine's tag", "an enemy SCV's tag", "a tag never used"]
        game.order(move, [dead.tag], far)
        game.order(move, [enemy.tag], far)
        game.order(move, [(1 << 32) + 12345], far)
        live = marine()
        game.act(game.command(move, [dead.tag, live.tag], far))
        trial.notes["then"] = "the dead marine and a live one given one move"
        game.turn(1)
        game.read("after", [live, enemy])

    trials.append(game.trial("moves given to units that cannot take them", cannot))

    def fogged(trial: Trial) -> None:
        site = game.toward(40)
        # The pylon is made where a marine of this player's sees it, since only what is seen is found made.
        (scout,) = game.create(UnitTypeId.MARINE, site + (0, -4))
        (pylon,) = game.create(UnitTypeId.PYLON, site, owner=game.enemy)
        seen = game.unit(pylon.tag)
        trial.notes["seen tag"] = pylon.tag
        trial.notes["seen as"] = None if seen is None else raw_pb2.DisplayType.Name(seen.display_type)
        game.kill([scout])
        game.until(lambda: pylon.tag not in game.units or game.units[pylon.tag].display_type == _SNAPSHOT, limit=200)
        snapshot = next(
            (
                unit
                for unit in game.units.values()
                if unit.display_type == _SNAPSHOT and _at(unit).distance_to(_at(pylon)) < 0.5
            ),
            None,
        )
        trial.notes["snapshot tag"] = None if snapshot is None else snapshot.tag
        by_seen, by_snapshot = marine(), marine()
        trial.notes["given"] = ["attack by the tag seen", "attack by the snapshot's tag"]
        game.order(AbilityId.GENERAL_ATTACK, [by_seen], pylon.tag)
        game.order(AbilityId.GENERAL_ATTACK, [by_snapshot], snapshot.tag if snapshot is not None else 0)
        game.turn(1)
        game.read("after", [by_seen, by_snapshot])
        game.turn(48)
        game.read("48 steps later", [by_seen, by_snapshot])
        game.kill([pylon.tag, *([snapshot.tag] if snapshot is not None else [])])

    trials.append(game.trial("attacks at an enemy pylon out of sight", fogged))

    def odd_targets(trial: Trial) -> None:
        stop, walk, stim = marine(), marine(), marine()
        (scv,) = game.own(UnitTypeId.SCV)[:1]
        trial.notes["given"] = ["stop at a point", "move at nothing", "stim at a point", "build a depot at a unit"]
        game.order(AbilityId.GENERAL_STOP, [stop], far)
        game.order(move, [walk])
        game.order(AbilityId.MARINE_STIM, [stim], far)
        game.order(AbilityId.SCV_BUILD_SUPPLY_DEPOT, [scv], stop)
        game.turn(1)
        game.read("after", [stop, walk, stim, scv])

    trials.append(game.trial("orders with the wrong kind of target", odd_targets))

    def again(trial: Trial) -> None:
        unit, idle = marine(), marine()
        game.order(move, [unit], far)
        game.turn(4)
        game.read("moving", [unit])
        trial.notes["given"] = ["the same move again", "stop to an idle marine", "hold position twice"]
        game.order(move, [unit], far)
        game.order(AbilityId.GENERAL_STOP, [idle])
        game.turn(1)
        game.read("after", [unit, idle])
        game.order(AbilityId.GENERAL_HOLD_POSITION, [idle])
        game.turn(2)
        game.order(AbilityId.GENERAL_HOLD_POSITION, [idle])
        game.turn(1)
        game.read("after hold position twice", [idle])

    trials.append(game.trial("orders that change nothing", again))

    def nearly(trial: Trial) -> None:
        units = [marine() for _ in range(4)]
        for unit in units:
            game.order(move, [unit], far)
        game.turn(4)
        trial.notes["given"] = [
            "the move 1/4096 off",
            "the move 1/2048 off",
            "the move again, queued",
            "GENERAL_MOVE_EXACT",
        ]
        game.order(move, [units[0]], far + (1 / 4096, 0))
        game.order(move, [units[1]], far + (1 / 2048, 0))
        game.order(move, [units[2]], far, queued=True)
        game.order(AbilityId.GENERAL_MOVE_EXACT, [units[3]], far)
        game.turn(1)
        game.read("after", units)

    trials.append(game.trial("moves nearly the same as the one a marine has", nearly))

    def attack_again(trial: Trial) -> None:
        site = game.spot(game.toward(14), 1)
        (unit,) = game.create(UnitTypeId.MARINE, site.towards(game.home, 4))
        (pylon,) = game.create(UnitTypeId.PYLON, site, owner=game.enemy)
        game.order(AbilityId.GENERAL_ATTACK, [unit], pylon)
        game.turn(8)
        game.order(AbilityId.GENERAL_ATTACK, [unit], pylon)
        game.turn(1)
        game.read("after the same attack again", [unit])

    trials.append(game.trial("a marine attacking a pylon given the same attack again", attack_again))

    def second_again(trial: Trial) -> None:
        unit = marine()
        game.act(game.command(move, [unit], near), game.command(move, [unit], far, queued=True))
        game.turn(2)
        game.read("with two moves", [unit])
        game.order(move, [unit], far)
        game.turn(1)
        game.read("given its second, unqueued", [unit])

    trials.append(game.trial("a marine with a queued move given that move unqueued", second_again))

    def queue_lost(trial: Trial) -> None:
        unit = marine()
        game.act(
            game.command(move, [unit], near),
            game.command(move, [unit], far, queued=True),
            game.command(move, [unit], aside, queued=True),
        )
        game.turn(2)
        game.read("with three moves", [unit])
        game.order(move, [unit], near)
        game.turn(1)
        game.read("given the first again, unqueued", [unit])

    trials.append(game.trial("a marine with queued moves given its first again", queue_lost))

    def points(trial: Trial) -> None:
        unit = marine()
        fine = Point((int(far.x) + 0.123456, int(far.y) + 0.987654))
        close = Point((fine.x + 0.001, fine.y))
        trial.notes["asked"] = [[fine.x, fine.y], [close.x, close.y]]
        game.order(move, [unit], fine)
        game.turn(2)
        game.read("sent to the fine point", [unit])
        game.order(move, [unit], close)
        game.turn(2)
        game.read("sent 0.001 further", [unit])
        # A depot's footprint is even and a barracks' odd, each asked for off the grid.
        builders = game.own(UnitTypeId.SCV)[:2]
        asked: dict[str, list[float]] = {}
        for builder, (label, ability, half) in zip(
            builders,
            (("depot", AbilityId.SCV_BUILD_SUPPLY_DEPOT, 1), ("barracks", AbilityId.SCV_BUILD_SUPPLY_DEPOT, 2)),
            strict=True,
        ):
            tile = game.spot(game.toward(18), half)
            off_grid = Point((int(tile.x) + 0.3, int(tile.y) + 0.7))
            asked[label] = [off_grid.x, off_grid.y]
            game.order(ability, [builder], off_grid)
        trial.notes["structures asked at"] = asked
        game.turn(2)
        game.read("SCVs sent to build a depot and a barracks off the grid", builders)
        game.order(AbilityId.GENERAL_STOP, builders)

    trials.append(game.trial("points kept to what precision", points))

    def camera(trial: Trial) -> None:
        game.camera(game.toward(20))
        game.turn(2)
        game.camera(game.home)
        game.turn(2)

    trials.append(game.trial("the camera moved away and back", camera))

    def autocast(trial: Trial) -> None:
        (scv,) = game.own(UnitTypeId.SCV)[:1]
        toggle = raw_pb2.ActionRawToggleAutocast(ability_id=RawAbilityId.Effect_Repair_SCV, unit_tags=[scv.tag])
        game.act(sc2api_pb2.Action(action_raw=raw_pb2.ActionRaw(toggle_autocast=toggle)))
        game.turn(2)
        game.read("after", [scv])

    trials.append(game.trial("an SCV's repair set to autocast", autocast))

    def many(trial: Trial) -> None:
        marines = game.create(UnitTypeId.MARINE, start, count=50)
        commands = [game.command(move, [unit], far if i % 2 else near) for i in range(10) for unit in marines]
        verdicts = game.act(*commands)
        trial.verdicts[-1] = [f"{len(verdicts)} verdicts", *sorted(set(verdicts))]
        trial.notes["sent"] = len(commands)
        game.turn(1)
        trial.notes["reported"] = sum(1 for entry in trial.reported if entry.get("kind") == "command")
        trial.reported.clear()

    trials.append(game.trial("500 commands in one request", many))
    return trials


# --- 6. What re-sending an order costs

# How often an order is re-sent: never, every step, and every few.
_CADENCES: tuple[tuple[str, int | None], ...] = (("once", None), ("every step", 1), ("every 4 steps", 4))


def _repeats(game: _Game) -> list[Trial]:
    trials: list[Trial] = []
    attack_cadences = (*_CADENCES, ("every 16 steps", 16))

    sites = [game.spot(game.toward(14 + 6 * index), 1) for index, _ in enumerate(attack_cadences)]

    def attacks(trial: Trial) -> None:
        # Each cadence stands at each site once, so that where a marine stands is told apart from how often its
        # attack is re-sent.
        damage: dict[str, list[float]] = {label: [] for label, _ in attack_cadences}
        reports: dict[str, int] = dict.fromkeys(damage, 0)
        for turn in range(len(sites)):
            placed = [sites[(index + turn) % len(sites)] for index in range(len(attack_cadences))]
            # Each marine is made first, so that it sees its pylon made, and the pylons are made in one request, so
            # that every marine opens fire at the same step.
            marines = [game.create(UnitTypeId.MARINE, site.towards(game.home, 4))[0] for site in placed]
            pylons = _made_together(game, UnitTypeId.PYLON, placed, owner=game.enemy)
            life = {pylon.tag: pylon.health + pylon.shield for pylon in pylons}
            reported = len(trial.reported)
            for unit, pylon in zip(marines, pylons, strict=True):
                game.order(AbilityId.GENERAL_ATTACK, [unit], pylon)
            for step in range(1, 897):
                game.turn(1)
                for unit, pylon, (_, every) in zip(marines, pylons, attack_cadences, strict=True):
                    if every and step % every == 0:
                        game.order(AbilityId.GENERAL_ATTACK, [unit], pylon)
            for unit, pylon, (label, _) in zip(marines, pylons, attack_cadences, strict=True):
                damage[label].append(round(life[pylon.tag] - _life(game, pylon.tag), 1))
                reports[label] += sum(
                    1
                    for entry in trial.reported[reported:]
                    if entry.get("ability") == "GENERAL_ATTACK_EXACT" and unit.tag in entry.get("units", [])  # type: ignore[operator]
                )
            game.kill([*marines, *pylons])
        trial.notes["damage over 896 steps, at each site in turn"] = damage
        trial.notes["damage in all"] = {label: round(sum(dealt), 1) for label, dealt in damage.items()}
        trial.notes["attacks reported"] = reports
        _summarize(trial)

    trials.append(game.trial("marines attacking pylons, their attacks re-sent, each cadence at each site", attacks))

    def moves(trial: Trial) -> None:
        covered: dict[str, float] = {}
        for label, every in _CADENCES:
            (unit,) = game.create(UnitTypeId.MARINE, game.toward(6))
            origin = _at(unit)
            destination = origin.towards(game.middle, 40)
            game.order(AbilityId.GENERAL_MOVE, [unit], destination)
            for step in range(1, 113):
                game.turn(1)
                if every and step % every == 0:
                    game.order(AbilityId.GENERAL_MOVE, [unit], destination)
            covered[label] = round(_at(game.units[unit.tag]).distance_to(origin), 2)
            game.kill([unit])
        trial.notes["distance over 112 steps"] = covered
        _summarize(trial)

    trials.append(game.trial("a marine moving, its move re-sent", moves))

    def gathers(trial: Trial) -> None:
        scvs = game.own(UnitTypeId.SCV)
        field = _nearest_field(game)
        worker, others = scvs[0], scvs[1:]
        game.order(AbilityId.GENERAL_MOVE, others, game.home.towards(_at(field), -10))

        def doing(name: str) -> bool:
            unit = game.unit(worker.tag)
            return unit is not None and bool(unit.orders) and _name(unit.orders[0].ability_id) == name

        cadences: tuple[tuple[str, int | None, Callable[[], bool]], ...] = (
            ("once", None, lambda: True),
            ("every step", 1, lambda: True),
            ("every 16 steps", 16, lambda: True),
            ("every step while it gathers", 1, lambda: doing("SCV_GATHER")),
            ("every step while it returns cargo", 1, lambda: doing("SCV_RETURN")),
        )
        mined: dict[str, int] = {}
        reports: dict[str, str] = {}
        for label, every, when in cadences:
            game.order(AbilityId.SCV_GATHER, [worker], field)
            game.turn(224)
            before, reported = game.minerals, len(trial.reported)
            sent = 0
            for step in range(1, 1345):
                game.turn(1)
                if every and step % every == 0 and when():
                    game.order(AbilityId.SCV_GATHER, [worker], field)
                    sent += 1
            mined[label] = game.minerals - before
            reports[label] = f"{len(trial.reported) - reported} of {sent} re-sent"
        trial.notes["minerals over 1344 steps"] = mined
        trial.notes["reports"] = reports
        _summarize(trial)

    trials.append(game.trial("an SCV mining, its gather re-sent", gathers, clear=False))
    return trials


def _made_together(game: _Game, unit_type: UnitTypeId, sites: Sequence[Point], *, owner: int) -> list[raw_pb2.Unit]:
    """One `unit_type` for `owner` at each of `sites`, all made in one request, in the order of `sites`."""
    before = set(game.units)
    game.sandbox.debug(*(game.sandbox.create(unit_type, owner, site) for site in sites))

    def made() -> list[raw_pb2.Unit]:
        return [unit for tag, unit in game.units.items() if tag not in before and unit.unit_type == unit_type]

    game.until(lambda: len(made()) >= len(sites), limit=32)
    game.made.update(unit.tag for unit in made())
    return [min(made(), key=lambda unit: _at(unit).distance_to(site)) for site in sites]


def _summarize(trial: Trial) -> None:
    """Keep a long trial's record short: the verdicts it had, and the first few reports."""
    trial.verdicts = [sorted({verdict for verdicts in trial.verdicts for verdict in verdicts})]
    trial.reported = trial.reported[:6]


def _depot_site(game: _Game, trial: Trial) -> Point:
    """A site far off that the game says a depot could go up on now, so that nothing but the minerals is in question."""
    depot = AbilityId.SCV_BUILD_SUPPLY_DEPOT
    site = game.spot(game.toward(32), 1) + (0.5, 0.5)
    while not game.sandbox.placeable(depot, [site])[0]:
        site = game.spot(game.toward(32), 1) + (0.5, 0.5)
    trial.notes["site"] = [site.x, site.y]
    return site


def _watch_build(game: _Game, trial: Trial, builder: raw_pb2.Unit, site: Point, *, mining: bool) -> None:
    """Record every 16 steps for 608 the minerals, how far `builder` is from `site`, its orders, and how far a depot
    there has come. Unless `mining`, every other SCV that takes up work is stopped, so that no minerals come in."""
    watched: list[tuple[int, int, float, list[str], float | None]] = []
    for _ in range(38):
        game.turn(16)
        if not mining and (busy := [u for u in game.own(UnitTypeId.SCV) if u.tag != builder.tag and u.orders]):
            game.order(AbilityId.GENERAL_STOP, busy)
        unit = game.units[builder.tag]
        there = next((u for u in game.own(UnitTypeId.SUPPLY_DEPOT) if _at(u).distance_to(site) < 1), None)
        progress = None if there is None else round(there.build_progress, 2)
        orders = [_name(order.ability_id) for order in unit.orders]
        watched.append((game.step, game.minerals, round(_at(unit).distance_to(site), 1), orders, progress))
    trial.notes["step, minerals, builder's distance, its orders, depot"] = watched
    game.read("608 steps later", [builder])


def _nearest_field(game: _Game) -> raw_pb2.Unit:
    fields = [unit for unit in game.units.values() if "MINERAL_FIELD" in _type_name(unit.unit_type)]
    return min(fields, key=lambda unit: _at(unit).distance_to(game.home))


def _life(game: _Game, tag: int) -> float:
    unit = game.unit(tag)
    return 0.0 if unit is None else unit.health + unit.shield


# --- 7. Action errors


def _errors(game: _Game) -> list[Trial]:
    trials: list[Trial] = []
    (center,) = game.own(UnitTypeId.COMMAND_CENTER)[:1]
    train_scv = AbilityId.COMMAND_CENTER_TRAIN_SCV
    depot = AbilityId.SCV_BUILD_SUPPLY_DEPOT

    def minerals_short(trial: Trial) -> None:
        trial.notes["minerals"] = game.minerals
        game.order(train_scv, [center])
        game.order(train_scv, [center])
        game.order(train_scv, [center], queued=True)
        trial.notes["given"] = ["an SCV", "another unqueued", "another queued"]
        game.turn(2)
        game.read("after", [center])
        game.turn(300)
        game.read("300 steps later", [center])

    trials.append(game.trial("SCVs ordered with the 50 minerals the game starts with", minerals_short, clear=False))

    def paid(trial: Trial) -> None:
        game.until(lambda: game.minerals >= 100, steps=16, limit=6000)
        builder, *others = game.own(UnitTypeId.SCV)
        site = _depot_site(game, trial)
        # The rest stop mining, so that no minerals come in meanwhile.
        game.order(AbilityId.GENERAL_STOP, others)
        trial.notes["minerals before the depot"] = game.minerals
        game.order(depot, [builder], site)
        game.order(train_scv, [center])
        _watch_build(game, trial, builder, site, mining=False)
        game.order(AbilityId.SCV_GATHER, others, _nearest_field(game))

    trials.append(game.trial("a depot ordered far off, then an SCV with the minerals left", paid, clear=False))

    def queued_build(trial: Trial, *, short: bool) -> None:
        game.until(lambda: game.minerals >= 100, steps=16, limit=6000)
        builder, *others = game.own(UnitTypeId.SCV)
        site = _depot_site(game, trial)
        game.order(AbilityId.GENERAL_STOP, others)
        if short:
            # SCVs are queued until fewer minerals are left than a depot costs.
            while game.minerals >= 100 and len(game.units[center.tag].orders) < 5:
                game.order(train_scv, [center])
                game.turn(1)
        trial.notes["minerals before the orders"] = game.minerals
        # Behind the command center first, so that the builder is a while on its way to the site.
        waypoint = game.home.towards(game.middle, -10)
        game.act(
            game.command(AbilityId.GENERAL_MOVE, [builder], waypoint), game.command(depot, [builder], site, queued=True)
        )
        if short:
            # The rest mine again, so that minerals come in while the builder is on its way.
            game.order(AbilityId.SCV_GATHER, others, _nearest_field(game))
        _watch_build(game, trial, builder, site, mining=short)
        if not short:
            game.order(AbilityId.SCV_GATHER, others, _nearest_field(game))

    trials.append(
        game.trial(
            "a move, then a depot queued behind it, with the minerals for it",
            lambda t: queued_build(t, short=False),
            clear=False,
        )
    )
    trials.append(
        game.trial(
            "a move, then a depot queued behind it, with too few minerals, and mining meanwhile",
            lambda t: queued_build(t, short=True),
            clear=False,
        )
    )

    def supply_short(trial: Trial) -> None:
        # Every depot goes, so that the command center's 15 is the cap.
        game.kill(game.own(UnitTypeId.SUPPLY_DEPOT, UnitTypeId.SUPPLY_DEPOT_LOWERED))
        game.until(lambda: game.minerals >= 300, steps=16, limit=6000)
        trial.notes["supply"] = list(game.supply)
        game.act(*(game.command(train_scv, [center], queued=True) for _ in range(5)))
        game.turn(2)
        game.read("5 SCVs queued", [center])
        game.turn(1500)
        game.read("1500 steps later", [center])

    trials.append(game.trial("5 SCVs queued with too little supply left", supply_short, clear=False))

    def blocked(trial: Trial, blocker: UnitTypeId, owner: int) -> None:
        game.until(lambda: game.minerals >= 100, steps=16, limit=6000)
        site = game.spot(game.toward(18), 1) + (0.5, 0.5)
        (builder,) = game.own(UnitTypeId.SCV)[:1]
        game.order(depot, [builder], site)
        game.until(lambda: _at(game.units[builder.tag]).distance_to(site) < 4, limit=600)
        (standing,) = game.create(blocker, site, owner=owner)
        if owner == game.player:
            game.order(AbilityId.GENERAL_HOLD_POSITION, [standing])
        game.turn(48)
        game.read("48 steps after the blocker came", [builder, standing])
        game.turn(192)
        game.read("240 steps after", [builder, standing])
        game.kill([standing])

    trials.append(
        game.trial(
            "a depot's site stood on by an enemy SCV", lambda t: blocked(t, UnitTypeId.SCV, game.enemy), clear=False
        )
    )
    trials.append(
        game.trial(
            "a depot's site stood on by a marine of this player's, holding position",
            lambda t: blocked(t, UnitTypeId.MARINE, game.player),
            clear=False,
        )
    )

    return trials


def _spell_errors(game: _Game) -> list[Trial]:
    trials: list[Trial] = []
    storm = AbilityId.HIGH_TEMPLAR_STORM
    start = game.toward(10)

    def drained(trial: Trial) -> None:
        (templar,) = game.create(UnitTypeId.HIGH_TEMPLAR, start)
        game.set_energy(75, [templar])
        game.turn(2)
        destination = start.towards(game.middle, 8)
        game.order(AbilityId.GENERAL_MOVE, [templar], destination)
        game.order(storm, [templar], destination.towards(game.middle, 4), queued=True)
        # Not 0, which a debug command reads as its most.
        game.set_energy(1, [templar])
        game.turn(2)
        game.read("moving, a storm queued, its energy gone", [templar])
        game.until(lambda: not game.units[templar.tag].orders, limit=400)
        game.read("done", [templar])

    trials.append(game.trial("a storm queued behind a move, the energy gone meanwhile", drained))

    def group(trial: Trial, energies: tuple[float, float]) -> None:
        templars = game.create(UnitTypeId.HIGH_TEMPLAR, start, count=2)
        for templar, energy in zip(templars, energies, strict=True):
            game.set_energy(energy, [templar])
        game.turn(2)
        game.read("before", templars)
        game.order(storm, templars, start.towards(game.middle, 5))
        game.turn(2)
        game.read("just after", templars)
        game.turn(48)
        game.read("48 steps later", templars)

    trials.append(game.trial("two templar given one storm", lambda t: group(t, (75, 75))))
    trials.append(game.trial("two templar given one storm, one of them without energy", lambda t: group(t, (1, 75))))
    trials.append(game.trial("two templar given one storm, the second with more energy", lambda t: group(t, (75, 150))))

    def group_build(trial: Trial) -> None:
        probes = game.create(UnitTypeId.PROBE, start, count=2)
        site = game.spot(game.toward(16), 1)
        game.order(AbilityId.PROBE_BUILD_PYLON, probes, site)
        game.turn(2)
        game.read("just after", probes)
        game.turn(120)
        game.read("120 steps later", probes)
        trial.notes["pylons at the site"] = sum(
            1 for unit in game.own(UnitTypeId.PYLON) if _at(unit).distance_to(site) < 1.5
        )

    trials.append(game.trial("two probes given one pylon to build", group_build))
    return trials


# --- 8. Realtime


def _realtime(game: _Game) -> list[Trial]:
    trials: list[Trial] = []
    (center,) = game.own(UnitTypeId.COMMAND_CENTER)[:1]
    (scv,) = game.own(UnitTypeId.SCV)[:1]

    def watch(trial: Trial, give: Callable[[], None], shows: Callable[[], bool]) -> None:
        for attempt in range(5):
            game.turn(8)
            sent = game.step
            reported = len(trial.reported)
            give()
            seen: list[dict[str, object]] = []
            for _ in range(10):
                game.turn(1)
                new = trial.reported[reported:]
                reported = len(trial.reported)
                seen.append({"step": game.step, "shows": shows(), "reported": [entry["step"] for entry in new]})
            trial.notes[f"attempt {attempt}, sent after step {sent}"] = seen

    def moves(trial: Trial) -> None:
        points = [game.toward(6), game.home.towards(game.middle, -6)]
        index = [0]

        def give() -> None:
            index[0] += 1
            game.order(AbilityId.GENERAL_MOVE, [scv], points[index[0] % 2])

        def shows() -> bool:
            unit = game.unit(scv.tag)
            target = points[index[0] % 2]
            return unit is not None and any(
                order.ability_id == AbilityId.GENERAL_MOVE_EXACT
                and Point((order.target_world_space_pos.x, order.target_world_space_pos.y)).distance_to(target) < 0.1
                for order in unit.orders
            )

        watch(trial, give, shows)

    trials.append(game.trial("an SCV moved, in a realtime game", moves, clear=False))

    def trains(trial: Trial) -> None:
        count = [0]

        def give() -> None:
            count[0] = len(game.units[center.tag].orders)
            game.order(AbilityId.COMMAND_CENTER_TRAIN_SCV, [center])

        def shows() -> bool:
            return len(game.units[center.tag].orders) > count[0]

        watch(trial, give, shows)

    trials.append(game.trial("an SCV trained, in a realtime game", trains, clear=False))
    return trials


# --- 9. Cancels, add-ons and how much a structure holds

_CANCEL_LAST = AbilityId.GENERAL_CANCEL_LAST
_TRAIN_SCV = AbilityId.COMMAND_CENTER_TRAIN_SCV
_MARINE = AbilityId.BARRACKS_TRAIN_MARINE
_REAPER = AbilityId.BARRACKS_TRAIN_REAPER
_ORBITAL = AbilityId.COMMAND_CENTER_MORPH_ORBITAL_COMMAND


def _cancels(game: _Game, tag: int, count: int) -> list[sc2api_pb2.Action]:
    return [game.command(_CANCEL_LAST, [tag]) for _ in range(count)]


def _queues(game: _Game) -> list[Trial]:
    trials: list[Trial] = []
    # What an orbital command needs.
    game.create(UnitTypeId.BARRACKS, game.spot(game.toward(8), 3))
    tech_lab = AbilityId.BARRACKS_BUILD_TECH_LAB

    def barracks(count: int) -> list[raw_pb2.Unit]:
        # Room on the right for an add-on.
        return [game.create(UnitTypeId.BARRACKS, game.spot(game.toward(12), 4))[0] for _ in range(count)]

    def ready(each: raw_pb2.Unit) -> list[list[object]]:
        """From the step `each`'s add-on reads finished, a marine each step until one is taken, then cancelled."""
        game.until(lambda: _add_on_finished(game, each.tag), limit=2400)
        watched: list[list[object]] = []
        for _ in range(32):
            trains = [_raw_name(a) for a in game.sandbox.offered([each.tag])[each.tag] if "Train" in _raw_name(a)]
            orders = [_order(order) for order in game.units[each.tag].orders]
            verdict = game.order(_MARINE, [each])
            watched.append([game.step, orders, trains, verdict])
            game.turn(1)
            if verdict == "Success":
                break
        game.order(_CANCEL_LAST, [each])
        game.turn(2)
        return watched

    def limits(trial: Trial) -> None:
        paired, labbed, plain = barracks(3)
        game.order(AbilityId.BARRACKS_BUILD_REACTOR, [paired])
        game.order(tech_lab, [labbed])
        # The tech lab finishes first.
        for each, label in ((labbed, "with a tech lab"), (paired, "with a reactor")):
            trial.notes[f"{label}: step, orders, trains offered and a marine's verdict, from its add-on finished"] = (
                ready(each)
            )
        cases = ((paired, "with a reactor"), (labbed, "with a tech lab"), (plain, "without an add-on"))
        offered = game.sandbox.offered(each.tag for each, _ in cases)
        for each, label in cases:
            add_on = _add_on(game, each.tag)
            trial.notes[f"{label}: its add-on, and the trains it is offered"] = [
                None if add_on is None else [_type_name(add_on.unit_type), add_on.build_progress],
                [_raw_name(ability) for ability in offered[each.tag] if "Train" in _raw_name(ability)],
            ]
            trial.notes[f"{label}: 10 marines in one step"] = [game.order(_MARINE, [each]) for _ in range(10)]
            game.turn(2)
            game.read(f"{label}, 10 marines given in one step", [each])
            game.act(*_cancels(game, each.tag, 10))
            game.turn(2)
            stepped: list[str] = []
            for _ in range(10):
                stepped.append(game.order(_MARINE, [each]))
                game.turn(1)
            trial.notes[f"{label}: 10 marines one a step"] = stepped
            game.read(f"{label}, 10 marines given one a step", [each])
            game.act(*_cancels(game, each.tag, 10))
        (center,) = game.create(UnitTypeId.COMMAND_CENTER, game.spot(game.toward(12), 3))
        game.act(*(game.command(_TRAIN_SCV, [center]) for _ in range(7)))
        game.turn(2)
        game.read("a command center given 7 SCVs in one request", [center])

    # First, while there is room near the main base.
    trials.append(
        game.trial("barracks watched from their add-on finished, given 10 marines, and a command center 7 SCVs", limits)
    )

    def offered(trial: Trial) -> None:
        (each,) = barracks(1)
        (center,) = game.create(UnitTypeId.COMMAND_CENTER, game.spot(game.toward(12), 3))
        (bay,) = game.create(UnitTypeId.ENGINEERING_BAY, game.spot(game.toward(8), 2))
        structures = [each, center, bay]
        game.order(_MARINE, [each])
        game.order(_TRAIN_SCV, [center])
        game.order(AbilityId.ENGINEERING_BAY_RESEARCH_INFANTRY_WEAPONS_1, [bay])
        game.turn(2)
        game.read("each making something", structures)
        trial.notes["cancels offered"] = {
            _type_name(game.units[tag].unit_type): [_raw_name(a) for a in abilities if "ancel" in _raw_name(a)]
            for tag, abilities in game.sandbox.offered(unit.tag for unit in structures).items()
        }

    trials.append(
        game.trial("the cancels a barracks, a command center and a bay making something are offered", offered)
    )

    def which(trial: Trial) -> None:
        tried = (
            RawAbilityId.Cancel_Last,
            RawAbilityId.Cancel_Slot,
            RawAbilityId.Cancel_Queue5,
            RawAbilityId.CancelSlot_Queue5,
            RawAbilityId.Cancel_QueueCancelToSelection,
            RawAbilityId.CancelSlot_QueueCancelToSelection,
            RawAbilityId.Cancel,
        )
        pattern = (_MARINE, _REAPER, _MARINE, _REAPER, _MARINE)
        trial.notes["queued"] = [_name(ability) for ability in pattern]
        for ability, each in zip(tried, barracks(len(tried)), strict=True):
            game.act(*(game.command(train, [each]) for train in pattern))
            game.turn(2)
            game.read(f"before {_raw_name(ability)}", [each])
            game.order(ability, [each])
            game.turn(2)
            game.read(f"given {_raw_name(ability)}", [each])

    trials.append(
        game.trial("barracks with a marine, a reaper, a marine, a reaper and a marine given each cancel", which)
    )

    def beyond(trial: Trial) -> None:
        busy, idle = barracks(2)
        game.act(game.command(_MARINE, [busy]), game.command(_MARINE, [busy]))
        game.turn(2)
        trial.notes["given"] = [
            "three cancels in one request to a barracks training two marines",
            "a cancel to an idle one",
        ]
        game.act(*_cancels(game, busy.tag, 3))
        game.order(_CANCEL_LAST, [idle])
        game.turn(2)
        game.read("after", [busy, idle])

    trials.append(game.trial("more cancels than a barracks has marines", beyond))

    def full(trial: Trial) -> None:
        each, roomy = barracks(2)
        game.act(*(game.command(_MARINE, [each]) for _ in range(5)))
        game.act(*(game.command(_MARINE, [roomy]) for _ in range(4)))
        game.turn(2)
        game.read("5 marines and 4", [each, roomy])
        trial.notes["given"] = [
            "a cancel, then a marine, in one request to the one with 5",
            "a cancel, then a reaper, in one request to the one with 4",
            "a marine to the one with 5, a step later",
        ]
        game.act(game.command(_CANCEL_LAST, [each]), game.command(_MARINE, [each]))
        game.act(game.command(_CANCEL_LAST, [roomy]), game.command(_REAPER, [roomy]))
        game.turn(1)
        game.read("after the requests", [each, roomy])
        game.order(_MARINE, [each])
        game.turn(1)
        game.read("a marine a step later", [each])

    trials.append(game.trial("barracks with 5 marines and with 4 given a cancel and one more", full))

    def morph_after(trial: Trial) -> None:
        centers = [game.create(UnitTypeId.COMMAND_CENTER, game.spot(game.toward(12), 3))[0] for _ in range(3)]
        for each in centers:
            game.act(game.command(_TRAIN_SCV, [each]), game.command(_TRAIN_SCV, [each]))
        game.turn(2)
        game.read("each training two SCVs", centers)
        first, second, third = centers
        trial.notes["given"] = [
            "two cancels, then the orbital",
            "one cancel, then the orbital",
            "the orbital, then two cancels",
            "then the orbital again to each, a step later",
        ]
        game.act(*_cancels(game, first.tag, 2), game.command(_ORBITAL, [first]))
        game.act(*_cancels(game, second.tag, 1), game.command(_ORBITAL, [second]))
        game.act(game.command(_ORBITAL, [third]), *_cancels(game, third.tag, 2))
        game.turn(1)
        game.read("after the requests", centers)
        for each in centers:
            game.order(_ORBITAL, [each])
        game.turn(2)
        game.read("the orbital again, a step later", centers)

    trials.append(
        game.trial("command centers training two SCVs given cancels and an orbital in one request", morph_after)
    )

    def add_ons(trial: Trial) -> None:
        busy, queued, idle, cleared = barracks(4)
        for each in (busy, queued, cleared):
            game.order(_MARINE, [each])
        game.turn(2)
        trial.notes["given"] = [
            "a tech lab to one training a marine",
            "a tech lab queued to one training a marine",
            "a tech lab to an idle one",
            "a cancel, then a tech lab, in one request to one training a marine",
            "then a tech lab to that one again, a step later",
        ]
        game.order(tech_lab, [busy])
        game.order(tech_lab, [queued], queued=True)
        game.order(tech_lab, [idle])
        game.act(game.command(_CANCEL_LAST, [cleared]), game.command(tech_lab, [cleared]))
        game.turn(1)
        game.read("after the requests", [busy, queued, idle, cleared])
        game.order(tech_lab, [cleared])
        game.turn(2)
        game.read("a tech lab again, a step later", [cleared])
        game.turn(420)
        game.read("420 steps later, a marine's time up", [busy, queued, idle, cleared])
        trial.notes["add-ons"] = [
            _type_name(add_on.unit_type) if (add_on := _add_on(game, each.tag)) is not None else None
            for each in (busy, queued, idle, cleared)
        ]

    trials.append(game.trial("barracks given a tech lab", add_ons))
    return trials


def _refunds(game: _Game) -> list[Trial]:
    trials: list[Trial] = []
    (center,) = game.own(UnitTypeId.COMMAND_CENTER)[:1]
    # What an orbital command needs.
    game.create(UnitTypeId.BARRACKS, game.spot(game.toward(8), 3))
    # Command centers whose SCVs take up what minerals are left over, so that a trial starts with as few as it asks.
    sinks = [game.create(UnitTypeId.COMMAND_CENTER, game.spot(game.toward(14), 3))[0] for _ in range(3)]

    def mine_until(amount: int) -> None:
        """Mine until there are `amount` minerals, then stop, so that no more come in."""
        game.order(AbilityId.SCV_GATHER, game.own(UnitTypeId.SCV), _nearest_field(game))
        game.until(lambda: game.minerals >= amount, steps=4, limit=6000)
        game.order(AbilityId.GENERAL_STOP, game.own(UnitTypeId.SCV))
        game.turn(2)

    def spend_below(amount: int) -> None:
        """Queue SCVs in the sinks until fewer than `amount` minerals are left."""
        while game.minerals >= amount:
            sink = next(sink for sink in sinks if len(game.units[sink.tag].orders) < 5)
            game.order(_TRAIN_SCV, [sink])
            game.turn(1)

    def emptied(run: Callable[[Trial], None]) -> Callable[[Trial], None]:
        """`run`, then the sinks and the command center emptied, their minerals back."""

        def then_emptied(trial: Trial) -> None:
            run(trial)
            game.act(*(action for each in (*sinks, center) for action in _cancels(game, each.tag, 5)))

        return then_emptied

    def refund(trial: Trial) -> None:
        mine_until(100)
        game.act(game.command(_TRAIN_SCV, [center]), game.command(_TRAIN_SCV, [center]))
        game.turn(136)
        watched = [(game.step, game.minerals, [_order(order) for order in game.units[center.tag].orders])]
        for _ in range(2):
            game.order(_CANCEL_LAST, [center])
            for _ in range(2):
                game.turn(1)
                watched.append((game.step, game.minerals, [_order(o) for o in game.units[center.tag].orders]))
        trial.notes["step, minerals, orders; a cancel sent after the first and after the third"] = watched

    trials.append(game.trial("two SCVs cancelled, the first half made", emptied(refund), clear=False))

    def train_after(trial: Trial) -> None:
        mine_until(200)
        game.act(*(game.command(_TRAIN_SCV, [center]) for _ in range(4)))
        game.turn(2)
        spend_below(50)
        game.read("4 SCVs queued", [center])
        trial.notes["given"] = ["a cancel, then an SCV, in one request", "an SCV, a step later"]
        minerals = [game.minerals]
        game.act(game.command(_CANCEL_LAST, [center]), game.command(_TRAIN_SCV, [center]))
        game.turn(1)
        minerals.append(game.minerals)
        game.read("after the request", [center])
        game.order(_TRAIN_SCV, [center])
        game.turn(1)
        minerals.append(game.minerals)
        game.read("an SCV a step later", [center])
        trial.notes["minerals before, after the request, a step later"] = minerals

    trials.append(
        game.trial(
            "a command center with 4 SCVs and too few minerals for another given a cancel and one",
            emptied(train_after),
            clear=False,
        )
    )

    def orbital_after(trial: Trial) -> None:
        (idle,) = game.create(UnitTypeId.COMMAND_CENTER, game.spot(game.toward(14), 3))
        mine_until(250)
        game.act(*(game.command(_TRAIN_SCV, [center]) for _ in range(5)))
        game.turn(2)
        spend_below(50)
        game.read("5 SCVs queued, and an idle command center", [center, idle])
        trial.notes["given"] = [
            "5 cancels to the one training, then the orbital to the idle one, in one request",
            "the orbital, a step later",
        ]
        minerals = [game.minerals]
        game.act(*_cancels(game, center.tag, 5), game.command(_ORBITAL, [idle]))
        game.turn(1)
        minerals.append(game.minerals)
        game.read("after the request", [center, idle])
        game.order(_ORBITAL, [idle])
        game.turn(1)
        minerals.append(game.minerals)
        game.read("the orbital a step later", [idle])
        trial.notes["minerals before, after the request, a step later"] = minerals

    trials.append(
        game.trial(
            "an idle command center given an orbital with too few minerals, after 5 cancels to another, in one request",
            emptied(orbital_after),
        )
    )
    return trials


# --- 10. What a producer's other abilities do to what it is making


# What a structure making something is not given here: what makes something, which the production sweeps measured,
# a cancel, which takes away what it is making by design, and the right-click, which stands for what the game picks.
_NOT_BESIDE = ("Smart", "Cancel")
# What stands beside a structure put to work, so that nothing it is given has to walk or look far: a worker, a
# caster for what only an energy-capable unit takes, and a structure of the race, which is what a supply drop or a
# Chrono Boost is aimed at, and which powers a protoss one besides.
_BESIDE_AIMS = {
    Race.TERRAN: (UnitTypeId.SCV, UnitTypeId.GHOST, UnitTypeId.SUPPLY_DEPOT),
    Race.PROTOSS: (UnitTypeId.PROBE, UnitTypeId.SENTRY, UnitTypeId.PYLON),
    Race.ZERG: (UnitTypeId.DRONE, UnitTypeId.QUEEN, UnitTypeId.SPAWNING_POOL),
}


def _puts_to_work(game: _Game, structure: UnitTypeId) -> AbilityId | None:
    """The ability that has `structure` start making something, or `None` where it makes nothing of its own.

    The first by name, which is a ghost for a barracks and a mothership for a nexus, so the sweep plays under
    `tech_tree`: without it the structure is offered neither and the trial records that it made nothing.
    """
    works = sorted(
        row.id.name
        for row in game.data.abilities.values()
        if structure in row.performers and row.behavior is OrderBehavior.QUEUES
    )
    return AbilityId[works[0]] if works else None


def _structure_abilities(game: _Game, race: Race) -> list[Trial]:
    """Give each structure of `race` that makes something every ability it is offered that makes nothing, while it is
    making something. NachOS reads all of these as leaving a structure's orders alone, having seen only two."""
    trials: list[Trial] = []
    structures = [
        row.id
        for row in sorted(game.data.units.values(), key=lambda row: row.id.name)
        if row.race is race and Attribute.STRUCTURE in row.attributes and row.base_type is None
    ]
    for structure in structures:
        work = _puts_to_work(game, structure)
        if work is None:
            continue
        pad = game.spot(game.toward(12), 5)
        made = game.create(structure, pad)
        if not made:
            continue
        offered = game.sandbox.offered([made[0].tag]).get(made[0].tag, [])
        game.kill(made)
        for ability in offered:
            curated = AbilityId.get(ability)
            row = game.data.abilities.get(curated) if curated is not None else None
            if row is None or row.product is not None or any(part in _raw_name(ability) for part in _NOT_BESIDE):
                continue
            label = f"{structure.name} making something given {_name(ability)}"
            trials.append(game.trial(label, partial(_beside, game, structure, work, ability, pad, race=race)))
    return trials


def _beside(
    game: _Game, structure: UnitTypeId, work: AbilityId, ability: int, pad: Point, trial: Trial, *, race: Race
) -> None:
    """Put `structure` to work with `work`, give it `ability` beside that, and read what became of what it makes."""
    aim_kind = game.aims.get(ability, _NOTHING)
    wants_unit = aim_kind in (_UNIT, _POINT_OR_UNIT)
    # Beside it whatever it is given: what an ability is aimed at, and what one that takes no target finds for
    # itself, such as the units a command center loads.
    requests = [(structure, game.player, pad)]
    requests += [(kind, game.player, pad + (3, -3 + 3 * index)) for index, kind in enumerate(_BESIDE_AIMS[race])]
    made = game.sandbox.spawn(requests)
    game.made.update(unit.tag for unit in made)
    # A step for a pylon's power to reach the structure it stands by, which a gateway is offered nothing without.
    game.turn(2)
    maker = next((unit for unit in made if unit.unit_type == structure), None)
    if maker is None:
        trial.notes["class"] = "not made"
        return
    game.set_energy(200, [maker])
    trial.notes["put to work by"] = work.name
    if (started := game.order(work, [maker])) != "Success":
        trial.notes["class"] = f"made nothing: {started}"
        return
    game.turn(2)
    before = game.read("making", [maker])[0]
    aims: list[Target] = []
    if aim_kind in (_NOTHING, _POINT_OR_NOTHING):
        aims.append(None)
    if wants_unit:
        aims += [unit.tag for unit in made if unit.tag != maker.tag]
    if aim_kind in (_POINT, _POINT_OR_UNIT, _POINT_OR_NOTHING):
        aims.append(_at(maker) + (0, 4))
    verdict = "no aim"
    for aim in aims:
        verdict = game.order(ability, [maker], aim)
        if verdict == "Success":
            trial.notes["aim"] = "nothing" if aim is None else "point" if isinstance(aim, Point) else "unit"
            break
    after: list[dict[str, object]] = []
    last = 0
    for at in _READS:
        game.turn(at - last)
        last = at
        after.append(game.read(f"{at} after", [maker])[0])
    trial.notes["class"] = _still_making(verdict, before, after)


def _making(read: dict[str, object], work: str) -> tuple[int, float | None] | None:
    """Where `work` stands in what a unit read is carrying out and how far along it is, or `None` where the unit is
    not carrying it out at all. Progress is `None` where the order has not started, which the game leaves out."""
    orders = read.get("orders")
    if not isinstance(orders, list):
        return None
    for place, order in enumerate(orders):
        if not isinstance(order, dict) or order.get("ability") != work:
            continue
        progress = order.get("progress")
        return place, float(progress) if isinstance(progress, int | float) else None
    return None


def _still_making(verdict: str, before: dict[str, object], after: Sequence[dict[str, object]]) -> str:
    """What an ability left of what a structure was making, from the structure read before it and after.

    Every read is looked at, so an ability that goes in front of what a structure is making is told from one that
    takes it away: the first says so and goes on to say whether the work survived behind it.
    """
    if verdict != "Success":
        return f"refused: {verdict}"
    orders = before.get("orders")
    if not isinstance(orders, list) or not orders or not isinstance(first := orders[0], dict):
        return "was making nothing"
    work = str(first.get("ability"))
    was = _making(before, work)
    assert was is not None
    ahead = False
    for read in after:
        if read.get("gone"):
            return "gone"
        now = _making(read, work)
        if now is None:
            return "goes first, and the work is gone" if ahead else "stops making"
        if was[1] is not None and (now[1] is None or now[1] + 1e-6 < was[1]):
            return "starts over"
        ahead = ahead or now[0] > was[0]
    return "goes first, and the work goes on" if ahead else "goes on making"


# --- 11. The half of a toggle that turns one off


# Each toggle a unit that moves is offered: the half that turns it on, which #42 saw it keep moving through, and the
# half that turns it off, which a unit is offered only once the first has taken.
_TOGGLES = (
    (UnitTypeId.BANELING, AbilityId.BANELING_ATTACK_STRUCTURES_ON, AbilityId.BANELING_ATTACK_STRUCTURES_OFF),
    (UnitTypeId.BANSHEE, AbilityId.BANSHEE_CLOAK_ON, AbilityId.BANSHEE_CLOAK_OFF),
    (UnitTypeId.GHOST, AbilityId.GHOST_CLOAK_ON, AbilityId.GHOST_CLOAK_OFF),
    (UnitTypeId.GHOST, AbilityId.GHOST_HOLD_FIRE_ON, AbilityId.GHOST_HOLD_FIRE_OFF),
    (UnitTypeId.ORACLE, AbilityId.ORACLE_PULSAR_BEAM_ON, AbilityId.ORACLE_PULSAR_BEAM_OFF),
    (UnitTypeId.OVERLORD, AbilityId.OVERLORD_CREEP_ON, AbilityId.OVERLORD_CREEP_OFF),
)


def _toggles_off(game: _Game, race: Race) -> list[Trial]:
    """Turn each of `race`'s toggles on while its unit moves, then off, and read what its move became."""
    trials: list[Trial] = []
    for performer, on, off in _TOGGLES:
        if game.data.units[performer].race is not race:
            continue
        pad = game.spot(game.toward(12), 4)
        label = f"{performer.name} moving given {off.name} after {on.name}"
        trials.append(game.trial(label, partial(_toggled, game, performer, on, off, pad)))
    return trials


def _toggled(game: _Game, performer: UnitTypeId, on: AbilityId, off: AbilityId, pad: Point, trial: Trial) -> None:
    """Send `performer` off, turn `on` while it moves, then turn it `off`, and class what each half left of its move.

    Both halves are classed, not only the off one: a unit is offered the on half of some toggles only under a name
    the sweep of what an ability does to a moving unit passes over, such as the baneling's attack on structures.
    """
    made = game.create(performer, pad)
    if not made:
        trial.notes["class"] = "not made"
        return
    mover = made[0]
    game.set_energy(200, [mover])
    destination = _at(mover).towards(game.middle, _MOVE_DISTANCE)
    game.order(AbilityId.GENERAL_MOVE, [mover], destination)
    game.turn(2)
    game.read("moving", [mover])
    moving = game.unit(mover.tag)
    shown = [_Shown.of(order) for order in moving.orders] if moving is not None else []
    move = next((order for order in shown if "MOVE" in order.ability), None)

    def given(half: AbilityId, label: str) -> tuple[str, list[float | None]]:
        """Give `half` and answer how it left the move, and how far the unit still has to go at each read."""
        verdict = game.order(half, [mover])
        orders: list[list[_Shown] | None] = []
        distances: list[float | None] = []
        last = 0
        for at in _READS:
            game.turn(at - last)
            last = at
            game.read(f"{at} after {label}", [mover])
            seen = game.unit(mover.tag)
            orders.append(None if seen is None else [_Shown.of(order) for order in seen.orders])
            distances.append(None if seen is None else round(_at(seen).distance_to(destination), 1))
        return _class(verdict, move, orders), distances

    trial.notes["on: class"], trial.notes["on: distance left"] = given(on, on.name)
    offered = game.sandbox.offered([mover.tag]).get(mover.tag, [])
    trial.notes["the off half is offered"] = int(off) in offered
    trial.notes["class"], trial.notes["distance left"] = given(off, off.name)


# --- 12. When supply is charged


def _supply(game: _Game) -> list[Trial]:
    """Play under a real supply cap, and find when the game takes supply for what it is asked to make."""
    trials: list[Trial] = []

    def watched(tag: int, steps: int, *, every: int = 1) -> list[list[object]]:
        """Each step, what the supply stands at and what the structure `tag` is making."""
        seen: list[list[object]] = []
        for _ in range(steps):
            unit = game.unit(tag)
            seen.append([game.step, *game.supply, None if unit is None else [_order(o) for o in unit.orders]])
            game.turn(every)
        return seen

    def crowd(trial: Trial, leaving: int) -> None:
        """Fill the supply with marines made by debug command until `leaving` is left under the cap."""
        used, cap = game.supply
        wanted = int(cap - used) - leaving
        if wanted > 0:
            game.create(UnitTypeId.MARINE, game.spot(game.toward(16), 3), count=wanted)
        trial.notes["supply filled to"] = list(game.supply)

    def queued(trial: Trial) -> None:
        # The barracks stands before the supply is filled, so that what is left under the cap is read as it will be
        # when the marines are ordered.
        (each,) = game.create(UnitTypeId.BARRACKS, game.spot(game.toward(12), 4))
        crowd(trial, leaving=2)
        used, cap = game.supply
        trial.notes["supply before"] = [used, cap]
        trial.notes["marines given"] = [game.order(_MARINE, [each]) for _ in range(5)]
        game.read("given five marines with two supply left", [each])
        trial.notes["step, used, cap, orders"] = watched(each.tag, 64, every=8)

    trials.append(game.trial("a barracks given five marines with two supply left", queued))

    def full(trial: Trial) -> None:
        (each,) = game.create(UnitTypeId.BARRACKS, game.spot(game.toward(12), 4))
        crowd(trial, leaving=0)
        used, cap = game.supply
        trial.notes["supply before"] = [used, cap]
        trial.notes["marines given"] = [game.order(_MARINE, [each]) for _ in range(3)]
        game.read("given three marines with none left", [each])
        trial.notes["step, used, cap, orders"] = watched(each.tag, 48, every=8)

    trials.append(game.trial("a barracks given three marines with no supply left", full))

    def cancelled(trial: Trial) -> None:
        (each,) = game.create(UnitTypeId.COMMAND_CENTER, game.spot(game.toward(12), 3))
        before = list(game.supply)
        game.act(game.command(_TRAIN_SCV, [each]), game.command(_TRAIN_SCV, [each]))
        game.turn(4)
        training = list(game.supply)
        game.read("training two SCVs", [each])
        game.order(_CANCEL_LAST, [each])
        game.turn(4)
        game.read("one cancelled", [each])
        trial.notes["supply before, training two, after a cancel"] = [before, training, list(game.supply)]

    trials.append(game.trial("two SCVs trained and one cancelled, and the supply each took", cancelled))
    return trials


# --- 13. How much a structure holds, and what makes without a queue


def _terran_slots(game: _Game) -> list[Trial]:
    """How many of what a structure with a reactor holds are made at once, and how deep a research structure goes."""
    trials: list[Trial] = []
    # What a factory and a starport need, which is a structure rather than the tech tree waived, since waiving it
    # has a barracks without an add-on train two at once.
    game.create(UnitTypeId.BARRACKS, game.spot(game.toward(8), 3))
    game.create(UnitTypeId.FACTORY, game.spot(game.toward(8), 3))

    def paired(structure: UnitTypeId, add_on: AbilityId, train: AbilityId) -> Callable[[Trial], None]:
        def run(trial: Trial) -> None:
            (each,) = game.create(structure, game.spot(game.toward(12), 6))
            game.order(add_on, [each])
            # A structure with no room beside it lifts off to build its add-on elsewhere, which takes a while.
            game.until(lambda: _add_on_finished(game, each.tag), steps=16, limit=4800)
            game.until(lambda: int(train) in game.sandbox.offered([each.tag])[each.tag], limit=64)
            trial.notes["10 given in one step"] = [game.order(train, [each]) for _ in range(10)]
            game.turn(2)
            game.read("10 given in one step", [each])
            seen: list[list[object]] = []
            for _ in range(48):
                unit = game.unit(each.tag)
                seen.append([game.step, None if unit is None else [_order(order) for order in unit.orders]])
                game.turn(8)
            trial.notes["step and orders, eight steps apart"] = seen

        return run

    for structure, add_on, train in (
        (UnitTypeId.BARRACKS, AbilityId.BARRACKS_BUILD_REACTOR, _MARINE),
        (UnitTypeId.FACTORY, AbilityId.FACTORY_BUILD_REACTOR, AbilityId.FACTORY_TRAIN_HELLION),
        (UnitTypeId.STARPORT, AbilityId.STARPORT_BUILD_REACTOR, AbilityId.STARPORT_TRAIN_VIKING),
    ):
        label = f"a {structure.name.lower()} with a reactor given 10 to make, watched until they are made"
        trials.append(game.trial(label, paired(structure, add_on, train)))

    trials.append(
        game.trial(
            "an engineering bay given five researches at once",
            partial(
                _research_depth,
                game,
                UnitTypeId.ENGINEERING_BAY,
                (
                    AbilityId.ENGINEERING_BAY_RESEARCH_INFANTRY_WEAPONS_1,
                    AbilityId.ENGINEERING_BAY_RESEARCH_INFANTRY_ARMOR_1,
                    AbilityId.ENGINEERING_BAY_RESEARCH_BUILDING_ARMOR,
                    AbilityId.ENGINEERING_BAY_RESEARCH_HISEC_AUTO_TRACKING,
                    AbilityId.ENGINEERING_BAY_RESEARCH_INFANTRY_WEAPONS_2,
                ),
            ),
        )
    )
    return trials


def _research_depth(game: _Game, structure: UnitTypeId, researches: Sequence[AbilityId], trial: Trial) -> None:
    """Give `structure` every research at once, and read how many of them it takes."""
    at = game.spot(game.toward(8), 3)
    requests = [(structure, game.player, at)]
    if game.data.units[structure].needs_power:
        requests.append((UnitTypeId.PYLON, game.player, at + (0, 4)))
    made = game.sandbox.spawn(requests)
    game.made.update(unit.tag for unit in made)
    game.turn(4)
    each = next((unit for unit in made if unit.unit_type == structure), None)
    if each is None:
        trial.notes["class"] = "not made"
        return
    trial.notes["given"] = {ability.name: game.order(ability, [each]) for ability in researches}
    game.turn(2)
    game.read("given every research at once", [each])
    offered = game.sandbox.offered([each.tag]).get(each.tag, [])
    trial.notes["researches offered after"] = [_raw_name(a) for a in offered if "Research" in _raw_name(a)]


def _zerg_slots(game: _Game) -> list[Trial]:
    """What a larva takes instead of a queue, and how deep an evolution chamber goes."""
    trials: list[Trial] = []
    (hatchery,) = game.own(UnitTypeId.HATCHERY)[:1]

    def larva(trial: Trial) -> None:
        if not game.until(lambda: bool(game.own(UnitTypeId.LARVA)), limit=400):
            trial.notes["class"] = "no larva"
            return
        one = game.own(UnitTypeId.LARVA)[0]
        trial.notes["offered"] = [
            _raw_name(a) for a in game.sandbox.offered([one.tag]).get(one.tag, []) if "Train" in _raw_name(a)
        ]
        trial.notes["three morphs in one request"] = game.act(
            game.command(AbilityId.LARVA_MORPH_DRONE, [one]),
            game.command(AbilityId.LARVA_MORPH_OVERLORD, [one]),
            game.command(AbilityId.LARVA_MORPH_DRONE, [one]),
        )
        game.turn(2)
        game.read("the larva given three morphs at once", [one])
        game.turn(16)
        game.read("16 steps later", [one])

    def spread(trial: Trial) -> None:
        if not game.until(lambda: len(game.own(UnitTypeId.LARVA)) >= 3, limit=800):
            trial.notes["class"] = "too few larvae"
        larvae = game.own(UnitTypeId.LARVA)
        trial.notes["larvae"] = len(larvae)
        trial.notes["a drone to each, and two over"] = [
            game.order(AbilityId.LARVA_MORPH_DRONE, [each]) for each in (*larvae, *larvae[:2])
        ]
        game.turn(2)
        game.read("a drone to each larva, and two over", [*larvae, hatchery])

    # Before the trial that spends one, since a hatchery grows its larvae back slowly.
    trials.append(game.trial("a drone ordered on every larva, and two more", spread, clear=False))
    trials.append(game.trial("one larva given three morphs in one request", larva, clear=False))

    trials.append(
        game.trial(
            "an evolution chamber given every research at once",
            partial(
                _research_depth,
                game,
                UnitTypeId.EVOLUTION_CHAMBER,
                (
                    AbilityId.EVOLUTION_CHAMBER_RESEARCH_MELEE_WEAPONS_1,
                    AbilityId.EVOLUTION_CHAMBER_RESEARCH_RANGE_WEAPONS_1,
                    AbilityId.EVOLUTION_CHAMBER_RESEARCH_GROUND_ARMOR_1,
                    AbilityId.EVOLUTION_CHAMBER_RESEARCH_MELEE_WEAPONS_2,
                ),
            ),
        )
    )
    return trials


def _protoss_slots(game: _Game) -> list[Trial]:
    """What a warp gate takes instead of a queue, and how deep a forge goes."""
    trials: list[Trial] = []

    def warping(trial: Trial) -> None:
        pad = game.spot(game.toward(12), 4)
        made = game.sandbox.spawn(
            [(UnitTypeId.PYLON, game.player, pad + (0, 5)), (UnitTypeId.WARP_GATE, game.player, pad)]
        )
        game.made.update(unit.tag for unit in made)
        game.turn(4)
        gate = next((unit for unit in made if unit.unit_type == UnitTypeId.WARP_GATE), None)
        if gate is None:
            trial.notes["class"] = "not made"
            return
        offered = game.sandbox.offered([gate.tag]).get(gate.tag, [])
        trial.notes["warp-ins offered"] = [_raw_name(a) for a in offered if "Warp" in _raw_name(a)]
        zealot = AbilityId.WARP_GATE_WARP_IN_ZEALOT
        trial.notes["two warp-ins in one request"] = game.act(
            game.command(zealot, [gate], pad + (2, 0)), game.command(zealot, [gate], pad + (-2, 0))
        )
        game.turn(2)
        game.read("a warp gate given two warp-ins", [gate])
        game.turn(16)
        game.read("16 steps later", [gate])

    trials.append(game.trial("a warp gate given two warp-ins in one request", warping))

    trials.append(
        game.trial(
            "a forge given every research at once",
            partial(
                _research_depth,
                game,
                UnitTypeId.FORGE,
                (
                    AbilityId.FORGE_RESEARCH_GROUND_WEAPONS_1,
                    AbilityId.FORGE_RESEARCH_GROUND_ARMOR_1,
                    AbilityId.FORGE_RESEARCH_SHIELDS_1,
                    AbilityId.FORGE_RESEARCH_GROUND_WEAPONS_2,
                ),
            ),
        )
    )
    return trials


# --- 14. Which cancel a structure part way through something is offered


def _cancels_offered(game: _Game) -> list[Trial]:
    """Ask what each thing part way through something is offered to cancel it with, and cancel it with that.

    It plays at real prices and records the purse each step besides, which shows what the cancel gave back.
    What that comes to is the game's own rule -- three quarters of a morph, an add-on or a structure going
    up, and all of a train or a research -- so the numbers are a check on it rather than the finding.
    """
    trials: list[Trial] = []
    # What an orbital command and a planetary fortress need.
    game.create(UnitTypeId.BARRACKS, game.spot(game.toward(8), 3))
    game.create(UnitTypeId.ENGINEERING_BAY, game.spot(game.toward(8), 2))

    def purse() -> list[int]:
        return [game.step, game.minerals, game.vespene]

    def cancelling(start: Callable[[], raw_pb2.Unit | None], ability: AbilityId, wait: int) -> Callable[[Trial], None]:
        def run(trial: Trial) -> None:
            each = start()
            if each is None:
                trial.notes["class"] = "not made"
                return
            spent = [purse()]
            trial.notes["ordered"] = game.order(ability, [each])
            game.turn(1)
            spent.append(purse())
            game.turn(wait)
            spent.append(purse())
            game.read("part way through", [each])
            # Which cancel a structure part way through something is offered, rather than the one it ought to be.
            offered = game.sandbox.offered([each.tag]).get(each.tag, [])
            cancels = [a for a in offered if "ancel" in _raw_name(a)]
            trial.notes["cancels offered"] = [_raw_name(a) for a in cancels]
            given: dict[str, str] = {}
            for cancel in cancels or [int(_CANCEL_LAST)]:
                given[_raw_name(cancel)] = verdict = game.order(cancel, [each])
                if verdict == "Success":
                    break
            trial.notes["cancelled"] = given
            for _ in range(4):
                game.turn(1)
                spent.append(purse())
            game.read("after the cancel", [each])
            trial.notes[
                "step, minerals and vespene: before, after the order, part way, then each step after the cancel"
            ] = spent

        return run

    def a(unit_type: UnitTypeId, half: int = 3) -> Callable[[], raw_pb2.Unit | None]:
        def make() -> raw_pb2.Unit | None:
            made = game.create(unit_type, game.spot(game.toward(12), half))
            return made[0] if made else None

        return make

    for label, start, ability, wait in (
        ("a command center morphing into an orbital", a(UnitTypeId.COMMAND_CENTER), _ORBITAL, 40),
        (
            "a command center morphing into a planetary fortress",
            a(UnitTypeId.COMMAND_CENTER),
            AbilityId.COMMAND_CENTER_MORPH_PLANETARY_FORTRESS,
            40,
        ),
        ("a barracks building a tech lab", a(UnitTypeId.BARRACKS, 4), AbilityId.BARRACKS_BUILD_TECH_LAB, 40),
        (
            "an engineering bay researching infantry weapons",
            a(UnitTypeId.ENGINEERING_BAY, 2),
            AbilityId.ENGINEERING_BAY_RESEARCH_INFANTRY_WEAPONS_1,
            40,
        ),
        ("a command center training an SCV", a(UnitTypeId.COMMAND_CENTER), _TRAIN_SCV, 40),
        # The same morph left most of the way through, to find whether what comes back turns on how far it got.
        ("a command center most of the way into an orbital", a(UnitTypeId.COMMAND_CENTER), _ORBITAL, 400),
        ("a command center most of the way through an SCV", a(UnitTypeId.COMMAND_CENTER), _TRAIN_SCV, 240),
    ):
        trials.append(game.trial(f"{label}, cancelled part way through", cancelling(start, ability, wait)))

    def building(threshold: float) -> Callable[[Trial], None]:
        def run(trial: Trial) -> None:
            _part_built(game, purse, threshold, trial)

        return run

    for threshold, label in ((0.0, "as it starts"), (0.5, "half way up")):
        trials.append(game.trial(f"a supply depot cancelled {label}", building(threshold)))
    return trials


def _part_built(game: _Game, purse: Callable[[], list[int]], threshold: float, trial: Trial) -> None:
    """Have an SCV put up a supply depot, and cancel it once it stands `threshold` of the way up."""
    scv = next(iter(game.own(UnitTypeId.SCV)), None)
    if scv is None:
        trial.notes["class"] = "no SCV"
        return

    def standing() -> raw_pb2.Unit | None:
        """The depot going up, once it stands above `threshold` and is not finished."""
        return next(
            (
                unit
                for unit in game.units.values()
                if unit.alliance == _OWN
                and unit.unit_type == UnitTypeId.SUPPLY_DEPOT
                and 0 < unit.build_progress < 1
                and unit.build_progress >= threshold
            ),
            None,
        )

    spent = [purse()]
    given: list[str] = []
    # The game answers a placement it will not take `Success` and refuses it by action error a step later, so each
    # spot is given until one is built on.
    for attempt in range(3):
        given.append(game.order(AbilityId.SCV_BUILD_SUPPLY_DEPOT, [scv], game.spot(game.toward(12 + 4 * attempt), 3)))
        if attempt == 0:
            game.turn(2)
            spent.append(purse())
        if game.until(lambda: standing() is not None, steps=8, limit=600):
            break
    trial.notes["ordered"] = given
    under = standing()
    if under is None:
        trial.notes["class"] = "never got there"
        return
    spent.append(purse())
    trial.notes["progress when cancelled"] = round(under.build_progress, 3)
    game.read("part built", [under])
    trial.notes["cancelled"] = game.order(AbilityId.GENERAL_CANCEL_BUILDING, [under])
    for _ in range(4):
        game.turn(1)
        spent.append(purse())
    trial.notes["step, minerals and vespene: before, after the order, part built, then each step after"] = spent


# --- 15. What the game says when a structure making something dies


def _producer_dies(game: _Game) -> list[Trial]:
    """Kill what is making something, and read what the observations after say about what it was making."""
    trials: list[Trial] = []

    def training(trial: Trial) -> None:
        (each,) = game.create(UnitTypeId.BARRACKS, game.spot(game.toward(12), 3))
        trial.notes["given"] = [game.order(_MARINE, [each]) for _ in range(3)]
        game.turn(8)
        game.read("training three marines", [each])
        game.sandbox.kill([each.tag])
        for at in (1, 2, 8):
            game.turn(at)
            game.read(f"{at} after it died", [each])

    trials.append(game.trial("a barracks training three marines, killed", training))

    def builder(trial: Trial) -> None:
        scv = next((unit for unit in game.own(UnitTypeId.SCV)), None)
        if scv is None:
            trial.notes["class"] = "no SCV"
            return
        at = game.spot(game.toward(16), 3)
        trial.notes["ordered"] = game.order(AbilityId.SCV_BUILD_SUPPLY_DEPOT, [scv], at)
        game.turn(2)
        game.read("on its way", [scv])
        game.sandbox.kill([scv.tag])
        for step in (1, 2, 8):
            game.turn(step)
            game.read(f"{step} after it died", [scv])

    trials.append(game.trial("an SCV killed on its way to build", builder))
    return trials


# --- 16. Whether the item in the middle of a queue can be cancelled at all


# What a game is joined with here. The raw interface NachOS plays on carries no way to name a queue slot, so the
# production panel is asked instead; `raw_affects_selection` is what puts a structure ordered into the selection the
# panel shows. The feature layer is asked for in one and left out of the other, to find which the game needs.
_UI_INTERFACE = sc2api_pb2.InterfaceOptions(
    raw=True,
    score=True,
    raw_affects_selection=True,
    feature_layer=sc2api_pb2.SpatialCameraSetup(
        width=24,
        resolution=common_pb2.Size2DI(x=84, y=84),
        minimap_resolution=common_pb2.Size2DI(x=64, y=64),
    ),
)
_SELECTING_INTERFACE = sc2api_pb2.InterfaceOptions(raw=True, score=True, raw_affects_selection=True)
# The slot ids the game offers no structure, which a selection might be all they wanted.
_SLOT_CANCELS = (
    RawAbilityId.Cancel_Slot,
    RawAbilityId.CancelSlot_Queue5,
    RawAbilityId.CancelSlot_QueueCancelToSelection,
)


def _middle_item(game: _Game, *, panels: bool) -> list[Trial]:
    """Ask the game's own production panel to drop the item in the middle of a queue, which no raw ability names.

    The panel itself is read only where the game was joined with `panels`, since a game joined without the feature
    layer carries no `ui_data`; what a structure is making is read off the structure either way.
    """
    trials: list[Trial] = []

    def panel() -> dict[str, object] | None:
        """What the game's production panel shows for the selection, which only a UI interface carries."""
        if not panels:
            return None
        ui = game.client.observation().observation.ui_data
        return {
            "building": _type_name(ui.production.unit.unit_type) if ui.production.HasField("unit") else None,
            "queue": [_type_name(unit.unit_type) for unit in ui.production.build_queue],
        }

    def fill(trial: Trial) -> raw_pb2.Unit:
        """A barracks holding five, selected, which a rally does while leaving it making what it is making."""
        (each,) = game.create(UnitTypeId.BARRACKS, game.spot(game.toward(12), 3))
        pattern = (_MARINE, _REAPER, _MARINE, _REAPER, _MARINE)
        trial.notes["queued"] = [_name(ability) for ability in pattern]
        trial.notes["given"] = game.act(*(game.command(train, [each]) for train in pattern))
        game.turn(2)
        game.read("five queued", [each])
        trial.notes["selected by a rally"] = game.order(AbilityId.GENERAL_RALLY, [each], _at(each) + (0, 5))
        game.turn(2)
        return each

    def which(trial: Trial) -> None:
        each = fill(trial)
        trial.notes["the panel before"] = panel()
        remove = ui_pb2.ActionProductionPanelRemoveFromQueue(unit_index=2)
        action = sc2api_pb2.Action(action_ui=ui_pb2.ActionUI(production_panel=remove))
        trial.notes["the third asked to go"] = game.act(action)
        game.turn(2)
        game.read("after the third was asked to go", [each])
        trial.notes["the panel after"] = panel()

    trials.append(game.trial("a barracks holding five, asked through the panel to drop the third", which))

    def selected(trial: Trial) -> None:
        each = fill(trial)
        trial.notes["the panel before"] = panel()
        for ability in _SLOT_CANCELS:
            trial.notes[f"{_raw_name(ability)} to the selected barracks"] = game.order(ability, [each])
            game.turn(2)
            game.read(f"after {_raw_name(ability)}", [each])
        trial.notes["the panel after"] = panel()

    trials.append(game.trial("a barracks holding five, selected, given each slot cancel raw", selected))
    return trials


_BASE_CHEATS = ("free", "food")
_KEEPS_CHEATS = (*_BASE_CHEATS, "god", "cooldown", "tech_tree")


@dataclass(frozen=True, slots=True)
class _Sweep:
    """One sweep: the race it plays, the trials it runs, the cheats it plays under, whether it plays in realtime,
    and the interface it joins with, which is the raw one unless a sweep needs what only another carries."""

    race: Race
    trials: Callable[[_Game], list[Trial]]
    cheats: tuple[str, ...] = _BASE_CHEATS
    realtime: bool = False
    interface: sc2api_pb2.InterfaceOptions | None = None


_SWEEPS: dict[str, _Sweep] = {
    "keeps-terran": _Sweep(Race.TERRAN, lambda g: _keeps(g, Race.TERRAN), _KEEPS_CHEATS),
    "keeps-protoss": _Sweep(Race.PROTOSS, lambda g: _keeps(g, Race.PROTOSS), _KEEPS_CHEATS),
    "keeps-zerg": _Sweep(Race.ZERG, lambda g: _keeps(g, Race.ZERG), _KEEPS_CHEATS),
    # Not `tech_tree` where it matters, which has a barracks without an add-on train two marines at once.
    "production-terran": _Sweep(Race.TERRAN, _terran_production),
    "production-protoss": _Sweep(Race.PROTOSS, _protoss_production, (*_BASE_CHEATS, "tech_tree")),
    "production-zerg": _Sweep(Race.ZERG, _zerg_production),
    "requests": _Sweep(Race.TERRAN, _requests, (*_BASE_CHEATS, "tech_tree")),
    "repeats": _Sweep(Race.TERRAN, _repeats, ("food",)),
    "errors": _Sweep(Race.TERRAN, _errors, ()),
    "spell-errors": _Sweep(Race.PROTOSS, _spell_errors, (*_BASE_CHEATS, "tech_tree")),
    "realtime": _Sweep(Race.TERRAN, _realtime, realtime=True),
    "queues": _Sweep(Race.TERRAN, _queues),
    # Not `free`, so that a cancel's refund counts.
    "refunds": _Sweep(Race.TERRAN, _refunds, ("food",)),
    "structure-abilities-terran": _Sweep(Race.TERRAN, lambda g: _structure_abilities(g, Race.TERRAN), _KEEPS_CHEATS),
    "structure-abilities-protoss": _Sweep(Race.PROTOSS, lambda g: _structure_abilities(g, Race.PROTOSS), _KEEPS_CHEATS),
    "structure-abilities-zerg": _Sweep(Race.ZERG, lambda g: _structure_abilities(g, Race.ZERG), _KEEPS_CHEATS),
    "toggles-terran": _Sweep(Race.TERRAN, lambda g: _toggles_off(g, Race.TERRAN), _KEEPS_CHEATS),
    "toggles-protoss": _Sweep(Race.PROTOSS, lambda g: _toggles_off(g, Race.PROTOSS), _KEEPS_CHEATS),
    "toggles-zerg": _Sweep(Race.ZERG, lambda g: _toggles_off(g, Race.ZERG), _KEEPS_CHEATS),
    # A real supply cap, and minerals enough that nothing is refused for want of them.
    "supply": _Sweep(Race.TERRAN, _supply, ("minerals",)),
    # Not `tech_tree`, which has a structure without a reactor make two at once.
    "slots-terran": _Sweep(Race.TERRAN, _terran_slots),
    "slots-protoss": _Sweep(Race.PROTOSS, _protoss_slots),
    "slots-zerg": _Sweep(Race.ZERG, _zerg_slots),
    # Not `free`, so that what a cancel gives back counts.
    "cancels-offered": _Sweep(Race.TERRAN, _cancels_offered, ("food", "all_resources")),
    "producer-dies": _Sweep(Race.TERRAN, _producer_dies),
    "cancel-a-middle-item": _Sweep(Race.TERRAN, lambda g: _middle_item(g, panels=True), interface=_UI_INTERFACE),
    # The same without the feature layer, to find whether the selection alone is what the game wanted.
    "cancel-a-middle-item-selected": _Sweep(
        Race.TERRAN, lambda g: _middle_item(g, panels=False), interface=_SELECTING_INTERFACE
    ),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sweeps", nargs="*", help=f"any of {', '.join(_SWEEPS)}; all of them when none")
    parser.add_argument("--out", type=Path, default=Path("orders.json"), help="where to write the findings")
    args = parser.parse_args()
    if unknown := set(args.sweeps) - set(_SWEEPS):
        parser.error(f"no such sweep: {', '.join(sorted(unknown))}")
    installation = Installation.find()
    findings: dict[str, list[dict[str, object]]] = {}
    for name in args.sweeps or _SWEEPS:
        sweep = _SWEEPS[name]
        with playing(sweep.race, installation, realtime=sweep.realtime, interface=sweep.interface) as sandbox:
            if sweep.cheats:
                sandbox.cheat(*sweep.cheats)
            if not sweep.realtime:
                sandbox.client.step(8)
            findings[name] = [asdict(trial) for trial in sweep.trials(_Game(sandbox, realtime=sweep.realtime))]
        # Written after each sweep, so that a sweep that fails later loses nothing already found.
        args.out.write_text(json.dumps(findings, indent=2) + "\n", encoding="utf-8")
    logger.info("Wrote {}", args.out)


if __name__ == "__main__":
    main()
