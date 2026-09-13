"""Find the buffs a melee game puts on units, by making everything that could put one there happen.

Needs StarCraft II installed. Plays one game as each race, or only those named, under the `all_resources` and
`fast_build` cheats but never `tech_tree`, which waives what a player has to research first (`docs/curating-ids.md`).
Each game researches every upgrade its race has, for real. It then creates each of the race's unit types in turn
beside a set of targets, and orders every ability that unit is offered, one ability at a time, recording each
buff that turns up on a unit nearby::

    uv run python tools/sweep_buffs.py
    uv run python tools/sweep_buffs.py zerg --out buffs-zerg.json

What it finds is written as JSON: for each buff, in the raw catalog's spelling, every unit type and ability that
brought it on and the unit that wore it, where an ability of `null` means the unit carried the buff from the
moment it was created.
"""

import argparse
import json
import sys
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

import numpy
from loguru import logger
from numpy.lib.stride_tricks import sliding_window_view
from s2clientprotocol import common_pb2, data_pb2, debug_pb2, error_pb2, query_pb2, raw_pb2, sc2api_pb2

from sc2nachos.gamedata import Attribute, GameData
from sc2nachos.gamemap import GameMap
from sc2nachos.geometry import Point
from sc2nachos.ids import AbilityId, UnitTypeId
from sc2nachos.ids.raw import RawAbilityId, RawBuffId, RawUnitTypeId, RawUpgradeId
from sc2nachos.launch import GameProcess, Installation, Map
from sc2nachos.match import Computer, Difficulty, Participant, Race
from sc2nachos.protocol import Client, GameEndedError, WebSocketTransport

MAP = "PylonAIE_v4"

# A trial counts what lands within this reach of the middle of the sandbox, and watches that long for it.
_REACH = 14.0
_WATCHES = 16
_STEPS_PER_WATCH = 8
# Each trial runs this many times, since a unit that is in the way or dies early can leave a buff unseen once.
_REPEATS = 2
# A unit created for player 0 is the map's own, and the game then reports it as belonging to player 16.
_NEUTRAL = 0
_NEUTRAL_REPORTED = 16

# A few enemy units of every shape a spell can ask for: light and armored, bio and mech, shields and energy, air.
_ENEMIES = (
    UnitTypeId.MARINE,
    UnitTypeId.ZEALOT,
    UnitTypeId.ZERGLING,
    UnitTypeId.STALKER,
    UnitTypeId.SIEGE_TANK,
    UnitTypeId.VIKING,
    UnitTypeId.RAVEN,
    UnitTypeId.MUTALISK,
)
_ENEMY_STRUCTURE = UnitTypeId.SUPPLY_DEPOT
# What a friendly spell is cast on: a unit it heals, restores or loads, and the structures it speeds up.
_FRIENDS = {
    Race.TERRAN: (UnitTypeId.MARINE, UnitTypeId.SCV, UnitTypeId.SIEGE_TANK, UnitTypeId.MEDIVAC),
    Race.PROTOSS: (UnitTypeId.ZEALOT, UnitTypeId.PROBE, UnitTypeId.SENTRY, UnitTypeId.WARP_PRISM),
    Race.ZERG: (UnitTypeId.ZERGLING, UnitTypeId.DRONE, UnitTypeId.ROACH, UnitTypeId.OVERLORD),
}
_FRIENDLY_STRUCTURES = {
    Race.TERRAN: (UnitTypeId.COMMAND_CENTER, UnitTypeId.BARRACKS, UnitTypeId.SUPPLY_DEPOT),
    Race.PROTOSS: (UnitTypeId.NEXUS, UnitTypeId.GATEWAY, UnitTypeId.PYLON, UnitTypeId.SHIELD_BATTERY),
    Race.ZERG: (UnitTypeId.HATCHERY,),
}
# Structures that stand only on a geyser, and research nothing.
_NOT_FOR_RESEARCH = frozenset(
    {UnitTypeId.REFINERY, UnitTypeId.REFINERY_RICH, UnitTypeId.ASSIMILATOR, UnitTypeId.ASSIMILATOR_RICH}
    | {UnitTypeId.EXTRACTOR, UnitTypeId.EXTRACTOR_RICH}
)
_ADD_ON_BUILDERS = frozenset({UnitTypeId.BARRACKS, UnitTypeId.FACTORY, UnitTypeId.STARPORT})
# Abilities whose names say they make, research or send somewhere, none of which puts a buff on anything, and gathering,
# since the sandbox has nothing to gather. Carrying a harvest is a buff all the same.
_SKIPPED_ANYWHERE = ("Train", "Research", "UpgradeTo")
_SKIPPED_PREFIXES = (
    "TerranBuild_",
    "ProtossBuild_",
    "ZergBuild_",
    "Build_TechLab",
    "Build_Reactor",
    "Rally",
    "Move",
    "Patrol",
    "HoldPosition",
    "stop_",
    "Stop_",
    "Smart",
    "Harvest_",
)

_SUCCESS = error_pb2.ActionResult.Success


@dataclass(frozen=True, slots=True)
class Sighting:
    """A buff seen on `wearer` after `caster` was ordered `ability`, or on creation where that is `None`."""

    caster: str
    ability: str | None
    wearer: str


@dataclass(slots=True)
class Findings:
    """Every sighting of every buff, the upgrades researched before any of them, and the unit types swept so far."""

    buffs: defaultdict[str, set[Sighting]] = field(default_factory=lambda: defaultdict(set))
    researched: set[str] = field(default_factory=set)
    swept: set[UnitTypeId] = field(default_factory=set)

    def as_json(self) -> dict[str, object]:
        """Findings a person can read: buffs in id order, each sighting as a caster, an ability and a wearer."""
        buffs = sorted(self.buffs.items(), key=lambda item: RawBuffId[item[0]])
        return {
            "researched": sorted(self.researched),
            "swept": sorted(unit_type.name for unit_type in self.swept),
            "buffs": {
                name: [
                    [sighting.caster, sighting.ability, sighting.wearer]
                    for sighting in sorted(sightings, key=lambda sighting: (sighting.caster, sighting.ability or ""))
                ]
                for name, sightings in buffs
            },
        }


class OpenGround:
    """The ground a structure can be put on that nothing has claimed yet."""

    def __init__(self, game_map: GameMap, units: Iterable[raw_pb2.Unit]) -> None:
        grid = game_map.placement
        self._origin = grid.origin
        self._free = numpy.array(grid.values, dtype=bool)
        for unit in units:
            reach = int(unit.radius) + 2
            self._claim(int(unit.pos.x), int(unit.pos.y), reach)

    def claim(self, near: Point, half: int) -> Point:
        """The center of the free square `2 * half + 1` tiles across nearest to `near`, which is then taken."""
        size = 2 * half + 1
        fits = sliding_window_view(self._free, (size, size)).all(axis=(2, 3))
        xs, ys = numpy.nonzero(fits)
        if not len(xs):
            raise RuntimeError(f"no free ground {size} tiles across is left")
        x0, y0 = near.x - self._origin.x - half, near.y - self._origin.y - half
        best = int(numpy.argmin((xs - x0) ** 2 + (ys - y0) ** 2))
        x, y = int(xs[best]) + half, int(ys[best]) + half
        self._claim(x, y, half)
        return Point((self._origin.x + x + 0.5, self._origin.y + y + 0.5))

    def _claim(self, x: int, y: int, half: int) -> None:
        """Take the square `2 * half + 1` tiles across centered on the tile at index `(x, y)`."""
        self._free[max(x - half, 0) : x + half + 1, max(y - half, 0) : y + half + 1] = False


class Sweep:
    """One game played as `race`, and what it is found to put on units."""

    def __init__(
        self, client: Client, transport: WebSocketTransport, player: int, race: Race, findings: Findings
    ) -> None:
        self._client = client
        self._transport = transport
        self._player = player
        self._race = race
        self._map = GameMap(client.game_info())
        raw_data = client.game_data()
        self._data = GameData(raw_data)
        self._targets = {ability.ability_id: ability.target for ability in raw_data.abilities}
        # Lifted before anything is counted, since what the fog hides, such as the computer's base, would otherwise
        # be taken for something the sweep put there and cleared away, which ends the game.
        self._debug(
            debug_pb2.DebugCommand(game_state=debug_pb2.DebugGameState.show_map),
            debug_pb2.DebugCommand(game_state=debug_pb2.DebugGameState.all_resources),
            debug_pb2.DebugCommand(game_state=debug_pb2.DebugGameState.fast_build),
        )
        client.step(1)
        units = self._units()
        home = next(Point((u.pos.x, u.pos.y)) for u in units if u.owner == player and u.radius > 2)
        self._home = home
        self._ground = OpenGround(self._map, units)
        # Claimed before the research structures take the ground near home: out of the mineral line, toward the
        # middle of the map, where the easiest computer does not come this early.
        self._sandbox = self._ground.claim(home + (self._map.playable_area.center - home) * 0.3, 10)
        # What was on the map before the sweep put anything there, and the structures it put up to research: never
        # counted and never cleared away. The sandbox's own structures stay too, but what lands on them counts.
        self._world: set[int] = {unit.tag for unit in units}
        # The sandbox's structures, each by what put it there, since a trial can destroy one.
        self._fixtures: dict[int, tuple[UnitTypeId, int, Point]] = {}
        self.findings = findings

    # --- Talking to the game

    def _units(self) -> list[raw_pb2.Unit]:
        return list(self._client.observation().observation.raw_data.units)

    def _debug(self, *commands: debug_pb2.DebugCommand) -> None:
        self._client.debug(commands)

    def _create(self, unit_type: UnitTypeId, owner: int, at: Point) -> debug_pb2.DebugCommand:
        position = common_pb2.Point2D(x=at.x, y=at.y)
        return debug_pb2.DebugCommand(
            create_unit=debug_pb2.DebugCreateUnit(unit_type=unit_type, owner=owner, pos=position, quantity=1)
        )

    def _offered(self, tag: int) -> list[int]:
        query = query_pb2.RequestQuery(
            abilities=[query_pb2.RequestQueryAvailableAbilities(unit_tag=tag)], ignore_resource_requirements=True
        )
        response = self._transport.request(sc2api_pb2.Request(query=query))
        return [ability.ability_id for ability in response.query.abilities[0].abilities]

    def _order(self, ability: int, tag: int, target: Point | int | None = None) -> error_pb2.ActionResult.ValueType:
        command = raw_pb2.ActionRawUnitCommand(ability_id=ability, unit_tags=[tag])
        if isinstance(target, Point):
            command.target_world_space_pos.x, command.target_world_space_pos.y = target
        elif target is not None:
            command.target_unit_tag = target
        action = sc2api_pb2.Action(action_raw=raw_pb2.ActionRaw(unit_command=command))
        return self._client.act([action]).result[0]

    def _spawn(self, requests: Sequence[tuple[UnitTypeId, int, Point]]) -> list[raw_pb2.Unit]:
        """Create every unit asked for, and return those the game really made, which it does not always."""
        before = {unit.tag for unit in self._units()}
        self._debug(*(self._create(unit_type, owner, at) for unit_type, owner, at in requests))
        self._client.step(4)
        made = [unit for unit in self._units() if unit.tag not in before]
        wanted = {(unit_type, _reported(owner)) for unit_type, owner, _ in requests}
        missing = wanted - {(unit.unit_type, unit.owner) for unit in made}
        for unit_type, owner in sorted(missing):
            logger.warning("The game did not create a {} for player {}", RawUnitTypeId(unit_type).name, owner)
        return made

    # --- Researching everything

    def research_everything(self) -> None:
        """Put up every structure of the race, and research everything they offer until nothing is left."""
        # Whatever a structure is offered to research, curated or not: the upgrade table names research the game no
        # longer honors, and a research added in a patch can unlock an ability no curated id names yet.
        structures = self._put_up_structures()
        for _ in range(200):
            if not self._research_round(structures):
                break
        upgrades = self._client.observation().observation.raw_data.player.upgrade_ids
        self.findings.researched |= {RawUpgradeId(upgrade).name for upgrade in upgrades}
        logger.info("Researched {} upgrades as {}", len(upgrades), self._race)

    def _put_up_structures(self) -> list[int]:
        """Every structure of the race that could research something, standing, powered and with its add-on."""
        requests: list[tuple[UnitTypeId, int, Point]] = []
        for unit_type in self._research_structures():
            spot = self._ground.claim(self._home, 4)
            requests.append((unit_type, self._player, spot - (1, 0)))
            if self._race is Race.PROTOSS:
                requests.append((UnitTypeId.PYLON, self._player, spot + (2.5, 2.5)))
        made = self._spawn(requests)
        for unit in made:
            if unit.unit_type in _ADD_ON_BUILDERS:
                self._order(AbilityId.GENERAL_BUILD_TECH_LAB, unit.tag, Point((unit.pos.x, unit.pos.y)))
        self._client.step(22 * 20)
        standing = [unit for unit in self._units() if unit.owner == self._player]
        self._world.update(unit.tag for unit in standing)
        structures = {row.id for row in self._data.units.values() if Attribute.STRUCTURE in row.attributes}
        return [unit.tag for unit in standing if unit.unit_type in structures]

    def _research_structures(self) -> list[UnitTypeId]:
        """The race's structures that can be put up, skipping those that must stand on a geyser or a structure."""
        return [
            row.id
            for row in self._data.units.values()
            if row.race is self._race
            and Attribute.STRUCTURE in row.attributes
            and row.creation_ability is not None
            and row.id not in _NOT_FOR_RESEARCH
            and "TECH_LAB" not in row.id.name
            and "REACTOR" not in row.id.name
        ]

    def _research_round(self, structures: Sequence[int]) -> bool:
        """Order one research on every idle structure that offers one, and let it run. False once none is left."""
        busy = {unit.tag for unit in self._units() if unit.orders}
        ordered = False
        for tag in structures:
            if tag in busy:
                ordered = True
                continue
            offered = [ability for ability in self._offered(tag) if "Research" in RawAbilityId(ability).name]
            if offered and self._order(offered[0], tag) == _SUCCESS:
                ordered = True
        self._client.step(22 * 3)
        return ordered

    # --- Ordering every ability

    def sweep_units(self) -> None:
        """Order every ability each of the race's unit types is offered, one trial each, and watch for buffs."""
        sandbox = self._sandbox
        self._restore_fixtures(self._sandbox_structures(sandbox))
        for row in sorted(self._data.units.values(), key=lambda row: row.id.name):
            if row.race is self._race and "TECH_LAB" not in row.id.name and "REACTOR" not in row.id.name:
                self._once(row.id, lambda unit_type=row.id: self._sweep_unit(unit_type, sandbox))
        for zone in (unit_type for unit_type in UnitTypeId if "_ZONE_" in unit_type.name):
            self._once(zone, lambda zone=zone: self._sweep_zone(zone, sandbox))

    def _once(self, unit_type: UnitTypeId, sweep: Callable[[], None]) -> None:
        """Run `sweep` unless a game before this one already swept `unit_type`."""
        if unit_type not in self.findings.swept:
            sweep()
            self.findings.swept.add(unit_type)

    def _sandbox_structures(self, sandbox: Point) -> list[tuple[UnitTypeId, int, Point]]:
        """The structures that stay in the sandbox through every trial: friendly ones above, an enemy one below."""
        requests = [
            (unit_type, self._player, sandbox + (-7 + 5 * index, 7))
            for index, unit_type in enumerate(_FRIENDLY_STRUCTURES[self._race])
        ]
        requests.append((_ENEMY_STRUCTURE, self._enemy, sandbox + (6, -7)))
        return requests

    def _restore_fixtures(self, requests: Sequence[tuple[UnitTypeId, int, Point]]) -> None:
        """Put back every sandbox structure a trial destroyed, and heal the rest."""
        alive = {unit.tag: unit for unit in self._units() if unit.tag in self._fixtures}
        self._fixtures = {tag: request for tag, request in self._fixtures.items() if tag in alive}
        standing = set(self._fixtures.values())
        for request in requests:
            if request not in standing:
                made = self._spawn([request])
                self._fixtures.update((unit.tag, request) for unit in made if unit.unit_type == request[0])
        value = debug_pb2.DebugSetUnitValue
        heals = [value(unit_value=value.Life, value=unit.health_max, unit_tag=unit.tag) for unit in alive.values()]
        if heals:
            self._debug(*(debug_pb2.DebugCommand(unit_value=heal) for heal in heals))

    @property
    def _enemy(self) -> int:
        return 3 - self._player

    def _sweep_unit(self, unit_type: UnitTypeId, sandbox: Point) -> None:
        """Try each ability a fresh `unit_type` is offered, on fresh targets, recording what each puts on anything."""
        caster = self._set_up_trial(unit_type, sandbox)
        if caster is None:
            return
        self._record(unit_type, None, {}, [caster])
        abilities = [ability for ability in self._offered(caster.tag) if not _is_skipped(ability)]
        logger.info("{} is offered {}", unit_type, [RawAbilityId(ability).name for ability in abilities])
        for ability in abilities * _REPEATS:
            caster = self._set_up_trial(unit_type, sandbox)
            if caster is not None:
                self._trial(unit_type, ability, caster, sandbox)

    def _sweep_zone(self, zone: UnitTypeId, sandbox: Point) -> None:
        """Put one of the map's zones in the sandbox, walk the friends through it, and record what it puts on them."""
        self._set_up_trial(zone, sandbox, owner=_NEUTRAL)
        friends = [unit for unit in self._in_trial(sandbox) if unit.owner == self._player]
        before = {unit.tag: set(unit.buff_ids) for unit in friends}
        for friend in friends:
            self._order(AbilityId.GENERAL_MOVE, friend.tag, sandbox + (4, 0))
        for _ in range(_WATCHES):
            self._client.step(_STEPS_PER_WATCH)
            self._record(zone, AbilityId.GENERAL_MOVE, before, self._in_trial(sandbox))

    def _set_up_trial(self, unit_type: UnitTypeId, sandbox: Point, *, owner: int | None = None) -> raw_pb2.Unit | None:
        """Clear the sandbox of the last trial, and put in a fresh caster, with energy, and fresh targets.

        The caster is this player's unless `owner` says otherwise.
        """
        owner = self._player if owner is None else owner
        kept = self._world | set(self._fixtures)
        leftovers = [unit.tag for unit in self._units() if unit.tag not in kept and _near(unit, sandbox)]
        if leftovers:
            self._debug(debug_pb2.DebugCommand(kill_unit=debug_pb2.DebugKillUnit(tag=leftovers)))
            self._client.step(2)
        self._restore_fixtures(self._sandbox_structures(sandbox))
        friends = [(t, self._player, sandbox + (-4, -3 + 2 * i)) for i, t in enumerate(_FRIENDS[self._race])]
        enemies = [(t, self._enemy, sandbox + (4, -7 + 2 * i)) for i, t in enumerate(_ENEMIES)]
        made = self._spawn([(unit_type, owner, sandbox), *friends, *enemies])
        caster = next((u for u in made if u.unit_type == unit_type and u.owner == _reported(owner)), None)
        if caster is not None:
            self._charge(made, caster)
        return caster

    def _charge(self, made: Iterable[raw_pb2.Unit], caster: raw_pb2.Unit) -> None:
        """Fill every energy bar, for the caster to spend and a spell to drain, and wound the friends to heal."""
        value = debug_pb2.DebugSetUnitValue
        commands = [value(unit_value=value.Energy, value=200, unit_tag=unit.tag) for unit in made]
        friends = [unit for unit in made if unit.owner == self._player and unit.tag != caster.tag]
        commands += [value(unit_value=value.Life, value=unit.health_max / 2, unit_tag=unit.tag) for unit in friends]
        commands += [value(unit_value=value.Shields, value=0, unit_tag=unit.tag) for unit in friends if unit.shield_max]
        self._debug(*(debug_pb2.DebugCommand(unit_value=command) for command in commands))
        self._client.step(2)

    def _trial(self, unit_type: UnitTypeId, ability: int, caster: raw_pb2.Unit, sandbox: Point) -> None:
        """Order `ability` on `caster` against whatever it will take, and record every buff that turns up."""
        before = {unit.tag: set(unit.buff_ids) for unit in self._in_trial(sandbox)}
        if (refusal := self._order_somehow(ability, caster, sandbox)) is not None:
            reason = error_pb2.ActionResult.Name(refusal)
            logger.warning("{} could not be ordered {} at anything: {}", unit_type, RawAbilityId(ability).name, reason)
            return
        for _ in range(_WATCHES):
            self._client.step(_STEPS_PER_WATCH)
            self._record(unit_type, ability, before, self._in_trial(sandbox))

    def _in_trial(self, sandbox: Point) -> list[raw_pb2.Unit]:
        """Every unit near the sandbox that the sweep put there."""
        return [unit for unit in self._units() if unit.tag not in self._world and _near(unit, sandbox)]

    def _order_somehow(
        self, ability: int, caster: raw_pb2.Unit, sandbox: Point
    ) -> error_pb2.ActionResult.ValueType | None:
        """Order `ability` at the first target the game accepts for it, or return why the last one was refused."""
        target = self._targets.get(ability, data_pb2.AbilityData.Target.Value("None"))
        units = [unit for unit in self._in_trial(sandbox) if unit.tag != caster.tag]
        enemy_units = [u.tag for u in units if u.owner == self._enemy]
        own_units = [u.tag for u in units if u.owner == self._player]
        candidates: list[Point | int | None] = []
        if target in (data_pb2.AbilityData.Target.Unit, data_pb2.AbilityData.Target.PointOrUnit):
            candidates += [*enemy_units, *own_units]
        if target in (data_pb2.AbilityData.Target.Point, data_pb2.AbilityData.Target.PointOrUnit):
            # Where the enemies stand, where the friends stand, and open ground above and below the caster.
            candidates += [sandbox + (4, 0), sandbox + (-4, 0), sandbox + (0, -5), sandbox + (0, 5)]
        if target in (data_pb2.AbilityData.Target.Value("None"), data_pb2.AbilityData.Target.PointOrNone):
            candidates.append(None)
        refusal = error_pb2.ActionResult.Error
        for candidate in candidates:
            if (refusal := self._order(ability, caster.tag, candidate)) == _SUCCESS:
                return None
        return refusal

    def _record(
        self, unit_type: UnitTypeId, ability: int | None, before: dict[int, set[int]], units: Iterable[raw_pb2.Unit]
    ) -> None:
        """Record each buff on `units` that the unit wearing it did not have `before`."""
        caster = unit_type.name
        ordered = None if ability is None else RawAbilityId(ability).name
        for unit in units:
            wearer = f"{self._relation(unit.owner)} {RawUnitTypeId(unit.unit_type).name}"
            for buff in set(unit.buff_ids) - before.get(unit.tag, set()):
                self.findings.buffs[RawBuffId(buff).name].add(Sighting(caster, ordered, wearer))

    def _relation(self, owner: int) -> str:
        """Whose a unit is, from this player's side."""
        return "own" if owner == self._player else "enemy" if owner == self._enemy else "neutral"


def _is_skipped(ability: int) -> bool:
    """Whether `ability` makes, researches or only sends a unit somewhere, which puts no buff on anything."""
    name = RawAbilityId(ability).name
    return name.startswith(_SKIPPED_PREFIXES) or any(fragment in name for fragment in _SKIPPED_ANYWHERE)


def _reported(owner: int) -> int:
    """The player the game reports a unit created for `owner` as belonging to."""
    return _NEUTRAL_REPORTED if owner == _NEUTRAL else owner


def _near(unit: raw_pb2.Unit, sandbox: Point) -> bool:
    return (unit.pos.x - sandbox.x) ** 2 + (unit.pos.y - sandbox.y) ** 2 <= _REACH**2


def sweep(race: Race, installation: Installation) -> Findings:
    """Research everything as `race`, order everything, and return what turned up.

    The computer concedes a game it sees as lost, which ends it, so the sweep goes on in a new game from the unit
    type it had reached, for as long as each game gets further than the one before.
    """
    findings = Findings()
    while True:
        reached = len(findings.swept)
        try:
            _play(race, installation, findings)
        except GameEndedError:
            if len(findings.swept) == reached:
                raise
            logger.warning("The game ended after {} unit types; going on in another", len(findings.swept))
        else:
            return findings


def _play(race: Race, installation: Installation, findings: Findings) -> None:
    """Play one game as `race`, sweeping whatever `findings` has not swept yet into it."""
    game_map = Map.find(MAP, installation=installation)
    with GameProcess.launch(installation, window=(1024, 768)) as game:
        transport = WebSocketTransport.connect(game.url)
        with closing(Client(transport)) as client:
            try:
                # Someone to play against keeps the game open; the easiest computer leaves the sandbox alone.
                client.create_game(game_map.path, [Participant(), Computer(race, Difficulty.VERY_EASY)])
                player = client.join_game(race, name="NachOS")
                run = Sweep(client, transport, player, race, findings)
                run.research_everything()
                run.sweep_units()
            finally:
                client.leave_game()
                client.quit()


def main(argv: Sequence[str]) -> None:
    """Sweep the races named, or all three, and write what turned up."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("races", nargs="*", help="terran, protoss or zerg; all three when none are named")
    parser.add_argument("--out", type=Path, default=Path("buffs.json"), help="where to write the findings")
    arguments = parser.parse_args(argv)
    playable = {race.name.lower(): race for race in (Race.TERRAN, Race.PROTOSS, Race.ZERG)}
    if unknown := set(arguments.races) - set(playable):
        raise SystemExit(f"No race is called {', '.join(sorted(unknown))}")
    races = [playable[name] for name in arguments.races] or list(playable.values())
    installation = Installation.find()
    report = {race.name.lower(): sweep(race, installation).as_json() for race in races}
    arguments.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    logger.info("Wrote the findings to {}", arguments.out)


if __name__ == "__main__":
    main(sys.argv[1:])
