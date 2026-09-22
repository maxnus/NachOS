"""Find the buffs a melee game puts on units, by triggering everything that could put one there.

Needs StarCraft II installed. Plays one game as each race, or only those named, under the `all_resources` and
`fast_build` cheats, never `tech_tree`, which waives the research a player needs first (`docs/curating-ids.md`).
Each game researches every upgrade of its race for real, then creates each of the race's unit types in turn beside a
set of targets and orders every ability the unit is offered, one at a time, recording each buff that appears on a
unit nearby::

    uv run python tools/sweep_buffs.py
    uv run python tools/sweep_buffs.py zerg --out buffs-zerg.json

The findings are written as JSON: for each buff, in the raw catalog's spelling, every unit type and ability that
brought it on and the unit that wore it. An ability of `null` means the unit had the buff from creation.
"""

import argparse
import json
import sys
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from _sandbox import NEUTRAL, SUCCESS, OpenGround, Sandbox, playing, reported
from loguru import logger
from s2clientprotocol import data_pb2, debug_pb2, error_pb2, raw_pb2

from sc2nachos.gamedata import Attribute, GameData
from sc2nachos.gamemap import GameMap
from sc2nachos.geometry import Point
from sc2nachos.ids import AbilityId, UnitTypeId
from sc2nachos.ids.raw import RawAbilityId, RawBuffId, RawUnitTypeId, RawUpgradeId
from sc2nachos.launch import Installation
from sc2nachos.match import Race
from sc2nachos.protocol import GameEndedError

# A trial counts buffs within this reach of the sandbox center, checked this many times, this many steps apart.
_REACH = 14.0
_WATCHES = 16
_STEPS_PER_WATCH = 8
# Each trial runs this many times: a unit in the way, or one that dies early, can hide a buff once.
_REPEATS = 2
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
# Abilities that make, research or send a unit somewhere put no buff on anything, and the sandbox has nothing to
# gather. Carrying a harvest is a buff all the same.
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


class Sweep:
    """One game played as `race`, and the buffs it is found to put on units."""

    def __init__(self, game: Sandbox, race: Race, findings: Findings) -> None:
        client, player = game.client, game.player
        self._game = game
        self._client = client
        self._player = player
        self._race = race
        self._map = GameMap(client.game_info())
        raw_data = client.game_data()
        self._data = GameData(raw_data)
        self._targets = {ability.ability_id: ability.target for ability in raw_data.abilities}
        # The fog is lifted before anything is counted: otherwise what it hides, such as the computer's base, would be
        # taken for something the sweep put there and cleared away, which ends the game.
        game.cheat("show_map", "all_resources", "fast_build")
        client.step(1)
        units = self._game.units()
        home = next(Point((u.pos.x, u.pos.y)) for u in units if u.owner == player and u.radius > 2)
        self._home = home
        self._ground = OpenGround(self._map, units)
        # Claimed before the research structures take the ground near home: out of the mineral line, toward the map's
        # center, where the easiest computer does not come this early.
        self._sandbox = self._ground.claim(home + (self._map.playable_area.center - home) * 0.3, 10)
        # Everything on the map before the sweep, plus the research structures it puts up: never counted and never
        # cleared. The sandbox's own structures stay too, but buffs on them count.
        self._world: set[int] = {unit.tag for unit in units}
        # The sandbox's structures by tag, with the request that made each, since a trial can destroy one.
        self._fixtures: dict[int, tuple[UnitTypeId, int, Point]] = {}
        self.findings = findings

    # --- Researching everything

    def research_everything(self) -> None:
        """Put up every structure of the race, and research everything they offer until nothing is left."""
        # Whatever research a structure is offered, curated or not: the upgrade table names research the game no longer
        # honors, and a research added in a patch can unlock an ability no curated id names yet.
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
        made = self._game.spawn(requests)
        for unit in made:
            if unit.unit_type in _ADD_ON_BUILDERS:
                self._game.order(AbilityId.GENERAL_BUILD_TECH_LAB, unit.tag, Point((unit.pos.x, unit.pos.y)))
        self._client.step(22 * 20)
        standing = [unit for unit in self._game.units() if unit.owner == self._player]
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
        busy = {unit.tag for unit in self._game.units() if unit.orders}
        ordered = False
        for tag in structures:
            if tag in busy:
                ordered = True
                continue
            abilities = self._game.offered([tag])[tag]
            offered = [ability for ability in abilities if "Research" in RawAbilityId(ability).name]
            if offered and self._game.order(offered[0], tag) == SUCCESS:
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
        alive = {unit.tag: unit for unit in self._game.units() if unit.tag in self._fixtures}
        self._fixtures = {tag: request for tag, request in self._fixtures.items() if tag in alive}
        standing = set(self._fixtures.values())
        for request in requests:
            if request not in standing:
                made = self._game.spawn([request])
                self._fixtures.update((unit.tag, request) for unit in made if unit.unit_type == request[0])
        value = debug_pb2.DebugSetUnitValue
        heals = [value(unit_value=value.Life, value=unit.health_max, unit_tag=unit.tag) for unit in alive.values()]
        if heals:
            self._game.debug(*(debug_pb2.DebugCommand(unit_value=heal) for heal in heals))

    @property
    def _enemy(self) -> int:
        return 3 - self._player

    def _sweep_unit(self, unit_type: UnitTypeId, sandbox: Point) -> None:
        """Order each ability a fresh `unit_type` is offered, on fresh targets, and record every buff that appears."""
        caster = self._set_up_trial(unit_type, sandbox)
        if caster is None:
            return
        self._record(unit_type, None, {}, [caster])
        abilities = [ability for ability in self._game.offered([caster.tag])[caster.tag] if not _is_skipped(ability)]
        logger.info("{} is offered {}", unit_type, [RawAbilityId(ability).name for ability in abilities])
        for ability in abilities * _REPEATS:
            caster = self._set_up_trial(unit_type, sandbox)
            if caster is not None:
                self._trial(unit_type, ability, caster, sandbox)

    def _sweep_zone(self, zone: UnitTypeId, sandbox: Point) -> None:
        """Put one of the map's zones in the sandbox, walk the friends through it, and record what it puts on them."""
        self._set_up_trial(zone, sandbox, owner=NEUTRAL)
        friends = [unit for unit in self._in_trial(sandbox) if unit.owner == self._player]
        before = {unit.tag: set(unit.buff_ids) for unit in friends}
        for friend in friends:
            self._game.order(AbilityId.GENERAL_MOVE, friend.tag, sandbox + (4, 0))
        for _ in range(_WATCHES):
            self._client.step(_STEPS_PER_WATCH)
            self._record(zone, AbilityId.GENERAL_MOVE, before, self._in_trial(sandbox))

    def _set_up_trial(self, unit_type: UnitTypeId, sandbox: Point, *, owner: int | None = None) -> raw_pb2.Unit | None:
        """Clear the sandbox of the last trial, and put in a fresh caster, with energy, and fresh targets.

        The caster is this player's unless `owner` says otherwise.
        """
        owner = self._player if owner is None else owner
        kept = self._world | set(self._fixtures)
        leftovers = [unit.tag for unit in self._game.units() if unit.tag not in kept and _near(unit, sandbox)]
        if leftovers:
            self._game.debug(debug_pb2.DebugCommand(kill_unit=debug_pb2.DebugKillUnit(tag=leftovers)))
            self._client.step(2)
        self._restore_fixtures(self._sandbox_structures(sandbox))
        friends = [(t, self._player, sandbox + (-4, -3 + 2 * i)) for i, t in enumerate(_FRIENDS[self._race])]
        enemies = [(t, self._enemy, sandbox + (4, -7 + 2 * i)) for i, t in enumerate(_ENEMIES)]
        made = self._game.spawn([(unit_type, owner, sandbox), *friends, *enemies])
        caster = next((u for u in made if u.unit_type == unit_type and u.owner == reported(owner)), None)
        if caster is not None:
            self._charge(made, caster)
        return caster

    def _charge(self, made: Iterable[raw_pb2.Unit], caster: raw_pb2.Unit) -> None:
        """Fill every energy bar, so the caster can cast and a spell can drain, and wound the friends to be healed."""
        value = debug_pb2.DebugSetUnitValue
        commands = [value(unit_value=value.Energy, value=200, unit_tag=unit.tag) for unit in made]
        friends = [unit for unit in made if unit.owner == self._player and unit.tag != caster.tag]
        commands += [value(unit_value=value.Life, value=unit.health_max / 2, unit_tag=unit.tag) for unit in friends]
        commands += [value(unit_value=value.Shields, value=0, unit_tag=unit.tag) for unit in friends if unit.shield_max]
        self._game.debug(*(debug_pb2.DebugCommand(unit_value=command) for command in commands))
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
        return [unit for unit in self._game.units() if unit.tag not in self._world and _near(unit, sandbox)]

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
            if (refusal := self._game.order(ability, caster.tag, candidate)) == SUCCESS:
                return None
        return refusal

    def _record(
        self, unit_type: UnitTypeId, ability: int | None, before: dict[int, set[int]], units: Iterable[raw_pb2.Unit]
    ) -> None:
        """Record each buff on `units` that its wearer did not have `before`."""
        caster = unit_type.name
        ordered = None if ability is None else RawAbilityId(ability).name
        for unit in units:
            wearer = f"{self._relation(unit.owner)} {RawUnitTypeId(unit.unit_type).name}"
            for buff in set(unit.buff_ids) - before.get(unit.tag, set()):
                self.findings.buffs[RawBuffId(buff).name].add(Sighting(caster, ordered, wearer))

    def _relation(self, owner: int) -> str:
        """Whose the unit is, as seen from this player."""
        return "own" if owner == self._player else "enemy" if owner == self._enemy else "neutral"


def _is_skipped(ability: int) -> bool:
    """Whether `ability` makes, researches or only sends a unit somewhere, none of which puts a buff on anything."""
    name = RawAbilityId(ability).name
    return name.startswith(_SKIPPED_PREFIXES) or any(fragment in name for fragment in _SKIPPED_ANYWHERE)


def _near(unit: raw_pb2.Unit, sandbox: Point) -> bool:
    return (unit.pos.x - sandbox.x) ** 2 + (unit.pos.y - sandbox.y) ** 2 <= _REACH**2


def sweep(race: Race, installation: Installation) -> Findings:
    """Research everything as `race`, order everything, and return the findings.

    The computer concedes a game it sees as lost, which ends it. The sweep then goes on in a new game from the unit
    type it had reached, as long as each game gets further than the last.
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
    """Play one game as `race`, sweeping into `findings` whatever it has not swept yet."""
    with playing(race, installation) as game:
        run = Sweep(game, race, findings)
        run.research_everything()
        run.sweep_units()


def main(argv: Sequence[str]) -> None:
    """Sweep the races named, or all three, and write the findings."""
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
