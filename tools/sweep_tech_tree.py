"""Find what each unit type is offered, what each ability needs first, and which abilities use up the unit ordered.

Needs StarCraft II installed. Plays one game as each race, or only those named, under the `free`, `fast_build`, `food`
and `god` cheats but never `tech_tree`, which waives every requirement (`docs/curating-ids.md`)::

    uv run python tools/sweep_tech_tree.py
    uv run python tools/sweep_tech_tree.py zerg --out tech-zerg.json

What a unit is offered is what `RequestQueryAvailableAbilities` answers for it, and requirements are read off how that
answer changes. Measured in game while writing this:

- The answer leaves out what the unit lacks the tech for, and an add-on counts only on the structure it is attached to.
  It ignores energy and cooldowns, and offers an unpowered structure nothing it needs power for.
- A cancel is offered only to a structure making something or being put up, and a halt only to a worker putting one
  up and to what it puts up.
- A structure's requirement leaves the answer within 4 steps of the structure leaving the observation. A lifted barracks
  still counts as a barracks.
- The other half of a toggle is offered once the unit has switched, up to 22 steps after the order.
- Once Burrow is researched, every zerg unit that burrows is offered every zerg unit's burrow, and burrows as itself
  whichever it is ordered.
- A gateway turns into a warp gate on its own once Warp Gate is researched, so what a gateway trains is tried before any
  research. Larva die with their hatchery.

Each game puts up every structure of the race and one of every other unit type, and reads what each is offered. It
finds what each ability offered needs by killing every structure of one type at a time and seeing what goes, then
researches one upgrade at a time and reads what each newly offers, finding what that needs the same way. It orders
every ability that makes a unit type on a new unit of each type offered it, before the research and after, to see what
it does to that unit, switches every toggle, loads every transport, and sets every structure making something and
a worker building, to read what each is offered then.

What it finds is written as JSON, in the raw catalog's spelling, for `tools/generate_tech_tree.py`.
"""

import argparse
import json
import sys
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from _sandbox import NEUTRAL, SUCCESS, OpenGround, Sandbox, playing
from loguru import logger
from s2clientprotocol import data_pb2, debug_pb2, error_pb2, raw_pb2

from sc2nachos.gamedata import Attribute, GameData, TargetType
from sc2nachos.gamedata._techtree import CREATION_ABILITY_OVERRIDES
from sc2nachos.gamemap import GameMap
from sc2nachos.geometry import Point
from sc2nachos.ids import AbilityId, UnitTypeId
from sc2nachos.ids.raw import RawAbilityId, RawUnitTypeId, RawUpgradeId
from sc2nachos.launch import Installation
from sc2nachos.match import Race
from sc2nachos.protocol import GameEndedError

OUT = Path(__file__).parents[1] / "data" / "tech_tree.json"

# How long a killed structure takes to leave what is offered, once it has left the observation, and a toggle to switch.
_SETTLE_STEPS = 6
_SWITCH_STEPS = 22
# How long an order that makes something is watched for what it does, and research for finishing, under `fast_build`.
_MAKE_STEPS = 600
# How long a worker harvesting gas stays inside the structure, and then some.
_RETURN_STEPS = 120
_RESEARCH_STEPS = 3000

_ADD_ONS = {
    UnitTypeId.BARRACKS: (UnitTypeId.TECH_LAB_BARRACKS, UnitTypeId.REACTOR_BARRACKS),
    UnitTypeId.FACTORY: (UnitTypeId.TECH_LAB_FACTORY, UnitTypeId.REACTOR_FACTORY),
    UnitTypeId.STARPORT: (UnitTypeId.TECH_LAB_STARPORT, UnitTypeId.REACTOR_STARPORT),
}
_BUILD_ADD_ON = {"TechLab": AbilityId.GENERAL_BUILD_TECH_LAB, "Reactor": AbilityId.GENERAL_BUILD_REACTOR}
# What a unit is ordered while its toggles are switched: anything that neither makes, researches, sends the unit
# somewhere, stops it nor empties a transport, whose unload is read while it still carries something. Salvaging a
# bunker and exploding a baneling are toggles of a kind too, and harmless here.
_NOT_SWITCHES = ("Train", "Research", "Build", "Morph", "UpgradeTo", "Move", "Patrol", "Hold", "Stop", "stop_", "Rally")
_NOT_SWITCHES += (
    "Smart",
    "attack",
    "Attack",
    "Harvest",
    "Lift",
    "Land",
    "Burrow",
    "Siege",
    "Unsiege",
    "Cancel",
    "Unload",
)
_WORKERS = {Race.TERRAN: UnitTypeId.SCV, Race.PROTOSS: UnitTypeId.PROBE, Race.ZERG: UnitTypeId.DRONE}
# What a structure is set making while its cancel is read.
_MAKING = ("Train", "Research", "UpgradeTo")
_STRUCTURE_ROOM = 4
# Only one may stand at a time, and none is offered while one does, so it comes after what makes it has been read.
_ONE_AT_A_TIME = frozenset({UnitTypeId.MOTHERSHIP})

type Pair = tuple[str, str]
"""A unit type and an ability it is offered, both in the raw catalog's spelling."""


@dataclass(slots=True)
class Findings:
    """Everything read so far, in the raw catalog's spelling."""

    base_build: int = 0
    offered: defaultdict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    # The pairs whose requirements were read, and what was found for each.
    read: set[Pair] = field(default_factory=set)
    structures: defaultdict[Pair, set[str]] = field(default_factory=lambda: defaultdict(set))
    upgrades: defaultdict[Pair, set[str]] = field(default_factory=lambda: defaultdict(set))
    attached: set[Pair] = field(default_factory=set)
    # What ordering an ability that makes a unit type did to each unit type offered it.
    made: dict[tuple[str, str, str], str] = field(default_factory=dict)
    powered: set[str] = field(default_factory=set)
    races: set[str] = field(default_factory=set)
    # What the game's own tables say: the general ability each one stands for, and what makes and researches what.
    remaps: dict[str, str] = field(default_factory=dict)
    creation_abilities: dict[str, str] = field(default_factory=dict)
    research_abilities: dict[str, str] = field(default_factory=dict)

    def as_json(self) -> dict[str, object]:
        """Findings a person can read: every list sorted, every requirement read under its unit type and ability."""
        return {
            "base_build": self.base_build,
            "races": sorted(self.races),
            "offered": {unit_type: sorted(abilities) for unit_type, abilities in sorted(self.offered.items())},
            "requirements": [
                {
                    "performer": performer,
                    "ability": ability,
                    "structures": sorted(self.structures.get((performer, ability), ())),
                    "attached": (performer, ability) in self.attached,
                    "upgrades": sorted(self.upgrades.get((performer, ability), ())),
                }
                for performer, ability in sorted(self.read)
            ],
            "made": [
                {"performer": performer, "ability": ability, "product": product, "result": result}
                for (performer, ability, product), result in sorted(self.made.items())
            ],
            "powered": sorted(self.powered),
            "remaps": dict(sorted(self.remaps.items())),
            "creation_abilities": dict(sorted(self.creation_abilities.items())),
            "research_abilities": dict(sorted(self.research_abilities.items())),
        }


class TechSweep:
    """One game played as `race`, and what its unit types are offered and need."""

    def __init__(self, game: Sandbox, race: Race, findings: Findings) -> None:
        self._game = game
        self._client = game.client
        self._player = game.player
        self._race = race
        self.findings = findings
        findings.base_build = self._client.ping().base_build
        self._map = GameMap(self._client.game_info())
        raw_data = self._client.game_data()
        self._data = GameData(raw_data)
        self._targets = {ability.ability_id: ability.target for ability in raw_data.abilities}
        findings.remaps |= {
            _ability_name(entry.ability_id): _ability_name(entry.remaps_to_ability_id)
            for entry in raw_data.abilities
            if entry.remaps_to_ability_id
        }
        findings.creation_abilities |= {
            _unit_name(entry.unit_id): _ability_name(entry.ability_id) for entry in raw_data.units if entry.ability_id
        }
        findings.research_abilities |= {
            _upgrade_name(entry.upgrade_id): _ability_name(entry.ability_id)
            for entry in raw_data.upgrades
            if entry.ability_id
        }
        # What makes each unit type: what the table names where that works, and the override where it does not.
        table = {entry.unit_id: AbilityId.get(entry.ability_id) for entry in raw_data.units}
        self._makers = {
            row.id: ability
            for row in self._data.units.values()
            if (ability := table.get(row.id) or CREATION_ABILITY_OVERRIDES.get(row.id))
        }
        # `free` rather than `all_resources`, which runs dry once add-ons have been rebuilt a few hundred times.
        # `god`, since the computer comes to attack at some point, which the sweep would take for a requirement lost.
        game.cheat("free", "fast_build", "food", "god")
        self._client.step(2)
        units = game.units()
        self._home = next(Point((u.pos.x, u.pos.y)) for u in units if u.owner == self._player and u.radius > 2)
        self._ground = OpenGround(self._map, units)
        self._sandbox = self._home + (self._map.playable_area.center - self._home) * 0.3
        self._seen: set[Pair] = set()
        self._pad = self._sandbox
        self._structure_types = {row.id for row in self._data.units.values() if Attribute.STRUCTURE in row.attributes}
        self._creation_abilities = {int(ability) for ability in self._makers.values()}

    # --- Reading the game

    def _mine(self) -> list[raw_pb2.Unit]:
        return [unit for unit in self._game.units() if unit.owner == self._player]

    def _read(self) -> dict[int, tuple[int, set[int]]]:
        """Every unit of this player, by tag: its type and what it is offered, recorded as found."""
        units = {unit.tag: unit.unit_type for unit in self._mine()}
        offered = self._game.offered(units)
        for tag, abilities in offered.items():
            type_name = _unit_name(units[tag])
            self.findings.offered[type_name].update(_ability_name(ability) for ability in abilities)
        return {tag: (units[tag], set(abilities)) for tag, abilities in offered.items()}

    def _pairs(self, read: dict[int, tuple[int, set[int]]]) -> set[Pair]:
        return {(_unit_name(unit_type), _ability_name(a)) for unit_type, abilities in read.values() for a in abilities}

    def _race_types(self) -> list[UnitTypeId]:
        """Every curated unit type of the race, but the add-ons, which only a structure can build."""
        add_ons = {add_on for pair in _ADD_ONS.values() for add_on in pair} | {UnitTypeId.TECH_LAB, UnitTypeId.REACTOR}
        rows = sorted(self._data.units.values(), key=lambda row: row.id.name)
        return [row.id for row in rows if row.race is self._race and row.id not in add_ons]

    # --- Putting everything up

    def set_up(self) -> None:
        """Every structure of the race standing, with its add-ons and power, and one of every other unit type."""
        # The workers the game starts with go, since one carrying minerals is offered what one without is not.
        worker = _WORKERS[self._race]
        self._game.kill(unit.tag for unit in self._mine() if unit.unit_type == worker)
        requests: list[tuple[UnitTypeId, int, Point]] = []
        units = [
            unit_type
            for unit_type in self._race_types()
            if unit_type not in self._structure_types and unit_type not in _ONE_AT_A_TIME
        ]
        for unit_type in self._race_types():
            if unit_type not in self._structure_types:
                continue
            # Add-on builders three times: bare, with a tech lab and with a reactor, since an add-on counts only where
            # it is attached.
            for _ in range(3 if unit_type in _ADD_ONS else 1):
                spot = self._ground.claim(self._sandbox, _STRUCTURE_ROOM)
                requests.append((unit_type, self._player, spot - (1, 0)))
                if self._race is Race.PROTOSS:
                    requests.append((UnitTypeId.PYLON, self._player, spot + (2.5, 2.5)))
        area = self._ground.claim(self._sandbox, 8)
        requests += [
            (unit_type, self._player, area + (-7 + 2 * (index % 8), -7 + 2 * (index // 8)))
            for index, unit_type in enumerate(units)
        ]
        made = self._game.spawn(requests)
        self._build_add_ons(made)
        self._client.step(22 * 4)
        if self._race is Race.PROTOSS:
            self._power_everything()
        self._charge()
        self._client.step(22)
        self.findings.powered |= {_unit_name(unit.unit_type) for unit in self._mine() if unit.is_powered}
        logger.info("Put up {} units as {}", len(made), self._race)

    def _power_everything(self) -> None:
        """Put a pylon beside every structure still unpowered, since the game does not make every pylon asked for.

        A nexus, an assimilator and a pylon never read as powered, so each gets one it does not need.
        """
        for _ in range(2):
            unpowered = [
                unit
                for unit in self._mine()
                if unit.unit_type in self._structure_types
                and unit.unit_type != UnitTypeId.PYLON
                and not unit.is_powered
            ]
            spots = [self._ground.claim(Point((unit.pos.x, unit.pos.y)), 1) for unit in unpowered]
            self._game.spawn([(UnitTypeId.PYLON, self._player, spot) for spot in spots])
            self._client.step(22 * 2)
        still = sorted(
            {
                _unit_name(unit.unit_type)
                for unit in self._mine()
                if unit.unit_type in self._structure_types and not unit.is_powered
            }
        )
        logger.info("Unpowered after putting up pylons: {}", still)

    def _build_add_ons(self, made: Iterable[raw_pb2.Unit]) -> None:
        """Order a tech lab on the second of each add-on builder and a reactor on the third."""
        builds = (None, AbilityId.GENERAL_BUILD_TECH_LAB, AbilityId.GENERAL_BUILD_REACTOR)
        counts: defaultdict[int, int] = defaultdict(int)
        for unit in sorted(made, key=lambda unit: unit.tag):
            if unit.unit_type not in _ADD_ONS:
                continue
            build = builds[counts[unit.unit_type] % 3]
            counts[unit.unit_type] += 1
            if build is not None:
                self._game.order(build, unit.tag, Point((unit.pos.x, unit.pos.y)))

    def _charge(self) -> None:
        """Fill every energy bar, so nothing reads as out of reach for want of energy."""
        self._game.set_value(debug_pb2.DebugSetUnitValue.Energy, 200, (unit.tag for unit in self._mine()))

    # --- Requirements

    def read_first_requirements(self) -> None:
        """Find what everything offered before any research needs."""
        pairs = self._pairs(self._read())
        self._seen |= pairs
        self.read_requirements(pairs)

    def read_last_requirements(self) -> None:
        """Find what everything offered only once switched, loaded or made last needs, as far as structures go: what
        an upgrade unlocks was read as it was researched."""
        self.read_requirements(self._pairs(self._read()))

    def read_requirements(self, pairs: set[Pair]) -> None:
        """Find which structures each of `pairs` needs, by killing each structure type in turn."""
        pairs = pairs - self.findings.read
        if not pairs:
            return
        logger.info("Reading what {} newly offered abilities need", len(pairs))
        standing = {UnitTypeId(unit.unit_type) for unit in self._mine() if UnitTypeId.get(unit.unit_type)}
        for structure in sorted(standing & self._structure_types, key=lambda unit_type: unit_type.name):
            aliases = {row.id for row in self._data.units.values() if structure in row.tech_aliases} & standing
            gone = self._lost({structure, *aliases}, pairs)
            if aliases:
                gone -= self._lost(aliases, pairs)
            for pair in gone:
                # A structure that needs power is offered nothing it needs it for once the pylons are gone, which is
                # what `powered` says already, and no requirement.
                if structure is UnitTypeId.PYLON and pair[0] in self.findings.powered:
                    continue
                self.findings.structures[pair].add(_unit_name(structure))
        self._read_attached(pairs)
        self.findings.read |= pairs

    def _lost(self, unit_types: set[UnitTypeId], pairs: set[Pair]) -> set[Pair]:
        """Which of `pairs` a unit stops being offered once every unit of `unit_types` is dead, all then put back."""
        before = self._read()
        units = self._mine()
        victims = [unit for unit in units if unit.unit_type in unit_types]
        # An add-on left behind by its structure would stay useless, so it goes and comes back with it.
        hosts = {unit.tag for unit in victims}
        victims += [unit for unit in units if unit.tag in {host.add_on_tag for host in victims} - hosts]
        self._game.kill(unit.tag for unit in victims)
        tags = {unit.tag for unit in victims}
        for _ in range(40):
            self._client.step(2)
            if not tags & {unit.tag for unit in self._game.units()}:
                break
        self._client.step(_SETTLE_STEPS)
        after = self._read()
        self._put_back(victims)
        # Only what comes back once the victims are back was theirs to take away.
        again = self._read()
        return pairs & {
            (_unit_name(unit_type), _ability_name(ability))
            for tag, (unit_type, abilities) in before.items()
            if tag in after and tag in again
            for ability in (abilities - after[tag][1]) & again[tag][1]
        }

    def _put_back(self, victims: Sequence[raw_pb2.Unit]) -> None:
        """Create again what `victims` were, with their add-ons, larva for a hatchery, and energy."""
        add_ons = {unit.tag: unit for unit in victims if _is_add_on(_unit_name(unit.unit_type))}
        hosts = [unit for unit in victims if unit.tag not in add_ons]
        requests = [(UnitTypeId(unit.unit_type), self._player, Point((unit.pos.x, unit.pos.y))) for unit in hosts]
        larva = [unit for unit in hosts if unit.unit_type in (UnitTypeId.HATCHERY, UnitTypeId.LAIR, UnitTypeId.HIVE)]
        requests += [(UnitTypeId.LARVA, self._player, Point((unit.pos.x, unit.pos.y - 3))) for unit in larva]
        made = self._game.spawn(requests) if requests else []
        # An add-on whose host stood is built again by it; one whose host died, by the host just made in its place.
        host_at = {(round(unit.pos.x), round(unit.pos.y)): unit.tag for unit in [*made, *self._mine()]}
        for add_on in add_ons.values():
            host = host_at.get((round(add_on.pos.x - 2.5), round(add_on.pos.y + 0.5)))
            kind = next(
                ability for fragment, ability in _BUILD_ADD_ON.items() if fragment in _unit_name(add_on.unit_type)
            )
            if host is not None:
                self._game.order(kind, host, Point((add_on.pos.x - 2.5, add_on.pos.y + 0.5)))
            else:
                logger.warning("No host found to rebuild a {} on", _unit_name(add_on.unit_type))
        if add_ons:
            self._client.step(22 * 3)
        self._charge()
        self._client.step(2)

    def _read_attached(self, pairs: set[Pair]) -> None:
        """Mark the pairs needing an add-on that a bare structure of the same type is not offered."""
        units = self._mine()
        read = self._read()
        for pair in pairs:
            if not any(_is_add_on(structure) for structure in self.findings.structures.get(pair, ())):
                continue
            bare = [unit.tag for unit in units if _unit_name(unit.unit_type) == pair[0] and not unit.add_on_tag]
            if not bare:
                logger.warning("No bare {} to tell whether {} needs its add-on attached", *pair)
            elif all(pair[1] not in {_ability_name(a) for a in read[tag][1]} for tag in bare if tag in read):
                self.findings.attached.add(pair)

    # --- Research

    def research_everything(self) -> None:
        """Research one upgrade at a time, reading what each newly offers and what that needs."""
        refused: set[tuple[int, int]] = set()
        for _ in range(300):
            read = self._read()
            self._seen |= self._pairs(read)
            busy = {unit.tag for unit in self._mine() if unit.orders}
            choice = next(
                (
                    (tag, ability)
                    for tag, (_, abilities) in sorted(read.items())
                    if tag not in busy
                    for ability in sorted(abilities)
                    if "Research" in _ability_name(ability) and (tag, ability) not in refused
                ),
                None,
            )
            if choice is None:
                break
            tag, ability = choice
            before = self._upgrades()
            if self._game.order(ability, tag) != SUCCESS or not self._await_research(before):
                logger.warning("{} researched nothing", _ability_name(ability))
                refused.add(choice)
                continue
            self._client.step(22)
            new_upgrades = {RawUpgradeId(upgrade).name for upgrade in self._upgrades() - before}
            new_pairs = self._pairs(self._read()) - self._seen
            # What a unit is offered only for a moment, such as a worker's return with cargo, is not the upgrade's.
            self._client.step(22)
            new_pairs &= self._pairs(self._read())
            logger.info("{} newly offers {}", sorted(new_upgrades), sorted(new_pairs))
            for pair in new_pairs:
                self.findings.upgrades[pair] |= new_upgrades
            self._seen |= new_pairs
            self.read_requirements(new_pairs)
        logger.info("Researched {} upgrades as {}", len(self._upgrades()), self._race)

    def _upgrades(self) -> set[int]:
        return set(self._client.observation().observation.raw_data.player.upgrade_ids)

    def _await_research(self, before: set[int]) -> bool:
        for _ in range(_RESEARCH_STEPS // 22):
            self._client.step(22)
            if self._upgrades() - before:
                return True
        return False

    # --- Toggles and cargo

    def sweep_states(self) -> None:
        """Switch every toggle and load every transport, reading what each is offered then."""
        worker = _WORKERS[self._race]
        late = [unit_type for unit_type in _ONE_AT_A_TIME if self._data.units[unit_type].race is self._race]
        self._game.spawn([(unit_type, self._player, self._sandbox) for unit_type in late])
        self._charge()
        self._read()
        done: set[int] = set()
        for unit in sorted(self._mine(), key=lambda unit: unit.tag):
            if unit.unit_type in done or not UnitTypeId.get(unit.unit_type):
                continue
            done.add(unit.unit_type)
            offered = self._game.offered([unit.tag]).get(unit.tag, [])
            for ability in offered:
                name = _ability_name(ability)
                if name.startswith("Load"):
                    passenger = self._game.spawn([(worker, self._player, Point((unit.pos.x, unit.pos.y - 2)))])
                    if passenger:
                        self._game.order(ability, unit.tag, passenger[0].tag)
                        self._client.step(60)
                        self._read()
                elif self._targets.get(ability) == _NOTHING and ability not in self._creation_abilities:
                    if not any(fragment in name for fragment in _NOT_SWITCHES):
                        self._game.order(ability, unit.tag)
                        self._client.step(_SWITCH_STEPS)
                        self._read()
        # Read while the transports still carry something and the toggles are still switched.
        self.read_requirements(self._pairs(self._read()) - self._seen)

    def sweep_busy(self) -> None:
        """Set every structure making something and every worker building, reading what each is offered meanwhile,
        since a cancel and a halt are offered only then."""
        seen = self._pairs(self._read())
        busy: set[int] = set()
        for unit in sorted(self._mine(), key=lambda unit: unit.tag):
            if unit.unit_type in busy or unit.orders:
                continue
            makes = [
                ability
                for ability in self._game.offered([unit.tag])[unit.tag]
                if self._targets.get(ability) == _NOTHING and any(verb in _ability_name(ability) for verb in _MAKING)
            ]
            if makes and self._game.order(makes[0], unit.tag) == SUCCESS:
                busy.add(unit.unit_type)
        self._client.step(2)
        found = self._pairs(self._read()) - seen
        found |= self._build_something()
        found |= self._carry_something()
        self.read_requirements(found)

    def _carry_something(self) -> set[Pair]:
        """What a worker is offered while it carries minerals home."""
        worker = _WORKERS[self._race]
        made = self._game.spawn([(worker, self._player, self._home + (0, -6))])
        gatherer = next(unit for unit in made if unit.unit_type == worker)
        fields = [unit for unit in self._game.units() if unit.mineral_contents]
        field = min(fields, key=lambda u: (u.pos.x - gatherer.pos.x) ** 2 + (u.pos.y - gatherer.pos.y) ** 2)
        gather = next(a for a in self._game.offered([gatherer.tag])[gatherer.tag] if "Gather" in _ability_name(a))
        self._game.order(gather, gatherer.tag, field.tag)
        try:
            for _ in range(_MAKE_STEPS // 4):
                # Another worker at the field would keep this one waiting its turn, and the town hall may be training
                # one, set busy a moment ago.
                self._game.kill(u.tag for u in self._mine() if u.unit_type == worker and u.tag != gatherer.tag)
                self._client.step(4)
                if any("Return" in _ability_name(a) for a in self._game.offered([gatherer.tag])[gatherer.tag]):
                    return self._pairs(self._read())
            now = next((u for u in self._game.units() if u.tag == gatherer.tag), None)
            logger.warning(
                "No {} came to carry anything from {} at {}: it is at {} with orders {}",
                worker.name,
                _unit_name(field.unit_type),
                (field.pos.x, field.pos.y),
                now and (now.pos.x, now.pos.y),
                now and [order.ability_id for order in now.orders],
            )
            return set()
        finally:
            self._clear({gatherer.tag})

    def _build_something(self) -> set[Pair]:
        """What a worker and the structure it is putting up are offered while it is being put up."""
        worker = _WORKERS[self._race]
        (builder,) = [
            u for u in self._game.spawn([(worker, self._player, self._home + (0, -6))]) if u.unit_type == worker
        ]
        makers = {ability: unit_type for unit_type, ability in self._makers.items()}
        offered = filter(None, (AbilityId.get(ability) for ability in self._game.offered([builder.tag])[builder.tag]))
        builds = [a for a in offered if a in makers and self._data.abilities[a].target_type is TargetType.POINT]
        # The first the game will let go up near home: a protoss structure needing power will not, without a pylon.
        build, site = next(((a, s) for a in builds if (s := self._site(a)) is not False), (None, None))
        if build is None or not isinstance(site, Point):
            logger.warning("No {} could put anything up", worker.name)
            return set()
        before = {unit.tag for unit in self._game.units()}
        self._game.order(build, builder.tag, site)
        for _ in range(_MAKE_STEPS // 4):
            self._client.step(4)
            # A placeholder, with no tag, stands at the site from the moment the order is given.
            if any(u.tag and u.tag not in before and u.unit_type == makers[build] for u in self._mine()):
                found = self._pairs(self._read())
                self._clear({u.tag for u in self._mine() if u.tag not in before} | {builder.tag})
                return found
        logger.warning("Nothing was put up by {}", _ability_name(build))
        return set()

    # --- What an ability that makes a unit does to the unit ordered

    def sweep_makers(self) -> None:
        """Order every ability that makes a unit type on each unit type offered it, once, and see what it does.

        Run before the research, for what a gateway trains before it turns into a warp gate, and after it, for what
        research unlocks.
        """
        if self._pad == self._sandbox:
            self._pad = self._ground.claim(self._sandbox, _STRUCTURE_ROOM + 2)
        for row in sorted(self._data.units.values(), key=lambda row: row.id.name):
            ability = self._makers.get(row.id)
            if row.race is not self._race or ability is None:
                continue
            name = _ability_name(ability)
            for performer_name in sorted(t for t, offered in self.findings.offered.items() if name in offered):
                performer = UnitTypeId.get(RawUnitTypeId[performer_name]) if performer_name in _RAW_UNITS else None
                trial = (performer_name, name, _unit_name(row.id))
                if performer is None or trial in self.findings.made:
                    continue
                if (result := self._make(row.id, performer, ability)) is not None:
                    self.findings.made[trial] = result

    def _make(self, product: UnitTypeId, performer: UnitTypeId, ability: AbilityId) -> str | None:
        """What ordering `ability` on a new `performer` did, as `_watch` tells it, or `None` where nothing could."""
        before = {unit.tag for unit in self._game.units()}
        try:
            unit = self._performer(performer, ability)
            return None if unit is None else self._watch(product, unit, ability)
        finally:
            self._clear({u.tag for u in self._mine() if u.tag not in before})

    def _clear(self, tags: set[int]) -> None:
        """Kill the units `tags`, and wait until they are gone and the ground they stood on is free again."""
        self._game.kill(tags)
        for _ in range(50):
            self._client.step(4)
            if not tags & {unit.tag for unit in self._game.units()}:
                break
        self._client.step(22 * 2)

    def _performer(self, performer: UnitTypeId, ability: AbilityId) -> raw_pb2.Unit | None:
        """A unit of `performer` offered `ability`: a larva of a hatchery's, or a new one on the pad kept for these,
        powered, and with a tech lab where that is what it lacks."""
        if performer is UnitTypeId.LARVA:
            # The trials use larva up faster than the hatcheries make them, so one is waited for.
            for _ in range(30):
                larva = [u for u in self._mine() if u.unit_type == performer and not u.orders]
                offered = self._game.offered(u.tag for u in larva)
                if (one := next((u for u in larva if ability in offered.get(u.tag, ())), None)) is not None:
                    # An order to one larva can be carried out by another of the same hatchery, which would leave the
                    # one ordered a larva beside the egg; so it is left the only one.
                    self._game.kill(u.tag for u in self._mine() if u.unit_type == performer and u.tag != one.tag)
                    self._client.step(2)
                    return one
                self._client.step(22)
            logger.warning("No larva is offered {}", _ability_name(ability))
            return None
        # Room for an add-on to the right, as the structures put up at the start have. A worker starts near home, where
        # what it builds goes, since the walk from the pad can take longer than the watch.
        spot = self._pad - ((1, 0) if performer in _ADD_ONS else (0, 0))
        if performer not in self._structure_types and self._data.abilities[ability].needs_placement:
            spot = self._home + (0, -6)
        requests = [(performer, self._player, spot)]
        if _unit_name(performer) in self.findings.powered:
            requests.append((UnitTypeId.PYLON, self._player, spot + (0, 4)))
        made = self._game.spawn(requests)
        unit = next((u for u in made if u.unit_type == performer), None)
        if unit is None:
            return None
        self._charge()
        self._client.step(_SWITCH_STEPS)
        offered = self._game.offered([unit.tag])[unit.tag]
        if ability not in offered and performer in _ADD_ONS:
            self._build_tech_lab(unit, offered)
            offered = self._game.offered([unit.tag])[unit.tag]
        if ability not in offered:
            logger.warning("A new {} is not offered {}", performer.name, _ability_name(ability))
            return None
        return unit

    def _build_tech_lab(self, unit: raw_pb2.Unit, offered: Iterable[int]) -> None:
        build = next((a for a in offered if _ability_name(a).startswith("Build_TechLab")), None)
        if build is None or self._game.order(build, unit.tag, Point((unit.pos.x, unit.pos.y))) != SUCCESS:
            return
        for _ in range(50):
            self._client.step(4)
            if any(u.tag == unit.tag and u.add_on_tag for u in self._mine()):
                break
        self._client.step(4)

    def _watch(self, product: UnitTypeId, unit: raw_pb2.Unit, ability: AbilityId) -> str | None:
        """Order `ability` on `unit`, and tell what it did: `morph` where the unit became a `product` or was used up
        making one, `build` where it made one beside itself, `other` where it became something else, and `None` where
        the order was refused or nothing came of it.
        """
        before = {u.tag for u in self._game.units()}
        target = self._target(ability, unit, product)
        answer = None if target is False else self._game.order(ability, unit.tag, target)
        if answer != SUCCESS:
            reason = "nothing to aim at" if answer is None else error_pb2.ActionResult.Name(answer)
            logger.warning("{} refused {}: {}", _unit_name(unit.unit_type), _ability_name(ability), reason)
            return None
        now: raw_pb2.Unit | None = unit
        for _ in range(_MAKE_STEPS // 4):
            self._client.step(4)
            units = self._game.units()
            now = next((u for u in units if u.tag == unit.tag), None)
            if now is None or now.unit_type == product:
                return "morph"
            # A structure whose add-on has no room lifts off to build it elsewhere, which is a `build` all the same.
            if now.unit_type != unit.unit_type and unit.unit_type not in self._structure_types:
                form = self._data.units.get(UnitTypeId(now.unit_type)) if UnitTypeId.get(now.unit_type) else None
                if form is not None and form.base_type is not None and form.base_type == unit.unit_type:
                    # It turned into a form of itself, as a zergling ordered to burrow as a drone does.
                    return "other"
                # On its way, as a larva is an egg first.
                continue
            # A placeholder, with no tag, stands where a structure was ordered from the moment it is ordered. Only
            # while the unit ordered is still what it was does a product beside it count, since an egg or a cocoon
            # put up at the start can hatch one at any moment.
            if any(u.tag and u.tag not in before and u.unit_type == product and u.owner == self._player for u in units):
                # A drone and the structure it becomes can both be in one observation, and a worker that has put up a
                # gas structure goes inside to harvest from it for a moment; only one that never comes back is used up.
                for _ in range(_RETURN_STEPS // 4):
                    self._client.step(4)
                    if any(u.tag == unit.tag for u in self._game.units()):
                        return "build"
                return "morph"
        if now is not None and now.unit_type != unit.unit_type and unit.unit_type not in self._structure_types:
            return "other"
        logger.warning("Nothing came of {} on a {}", _ability_name(ability), _unit_name(unit.unit_type))
        return None

    def _target(self, ability: AbilityId, unit: raw_pb2.Unit, product: UnitTypeId) -> Point | int | None | bool:
        """Something to aim `ability` at to make `product`, other than a new structure's site, or `False` where nothing
        will do."""
        row = self._data.abilities[ability]
        if row.target_type is TargetType.UNIT:
            return self._geyser(rich=product.name.endswith("_RICH"))
        if row.target_type in (TargetType.NOTHING, TargetType.POINT_OR_NOTHING):
            return None
        if row.needs_placement:
            # An add-on is built from where its structure stands, and a structure wherever the game will have it.
            if unit.unit_type in self._structure_types:
                return Point((unit.pos.x, unit.pos.y))
            return self._site(ability)
        return Point((unit.pos.x + 3, unit.pos.y))

    def _geyser(self, *, rich: bool) -> int | bool:
        """The geyser nearest home with nothing on it, rich or not, which is the one unit anything is made on.

        The map has no rich geyser, so one is created where it is wanted.
        """
        taken = {(round(u.pos.x), round(u.pos.y)) for u in self._mine()}
        geysers = [
            u
            for u in self._game.units()
            if u.vespene_contents
            and u.owner != self._player
            and (round(u.pos.x), round(u.pos.y)) not in taken
            and (u.unit_type == UnitTypeId.VESPENE_GEYSER_RICH) == rich
        ]
        if not geysers and rich:
            spot = self._ground.claim(self._home, 3)
            geysers = [u for u in self._game.spawn([(UnitTypeId.VESPENE_GEYSER_RICH, NEUTRAL, spot)])]
        if not geysers:
            return False
        return min(geysers, key=lambda u: (u.pos.x - self._home.x) ** 2 + (u.pos.y - self._home.y) ** 2).tag

    def _site(self, ability: AbilityId) -> Point | bool:
        """The nearest point to home where the game would put up what `ability` makes, or `False` where it would not."""
        corner = Point((int(self._home.x), int(self._home.y)))
        offsets = sorted(
            ((dx + half, dy + half) for dx in range(-20, 21) for dy in range(-20, 21) for half in (0.0, 0.5)),
            key=lambda offset: offset[0] ** 2 + offset[1] ** 2,
        )
        for start in range(0, len(offsets), 400):
            points = [corner + offset for offset in offsets[start : start + 400]]
            for point, fits in zip(points, self._game.placeable(ability, points), strict=True):
                if fits:
                    return point
        return False


_NOTHING = data_pb2.AbilityData.Target.Value("None")
_RAW_UNITS = {member.name for member in RawUnitTypeId}


def _unit_name(unit_type: int) -> str:
    try:
        return RawUnitTypeId(unit_type).name
    except ValueError:
        return str(unit_type)


def _upgrade_name(upgrade: int) -> str:
    try:
        return RawUpgradeId(upgrade).name
    except ValueError:
        return str(upgrade)


def _ability_name(ability: int) -> str:
    try:
        return RawAbilityId(ability).name
    except ValueError:
        return str(ability)


def _is_add_on(unit_type: str) -> bool:
    return "TechLab" in unit_type or "Reactor" in unit_type


def sweep(race: Race, installation: Installation, findings: Findings) -> None:
    """Read everything as `race` into `findings`, trying a second game if the computer ends the first."""
    for attempt in (1, 2):
        try:
            with playing(race, installation) as game:
                run = TechSweep(game, race, findings)
                run.set_up()
                run.read_first_requirements()
                run.sweep_makers()
                run.research_everything()
                run.sweep_states()
                run.sweep_busy()
                run.sweep_makers()
                run.read_last_requirements()
        except GameEndedError:
            if attempt == 2:
                raise
            logger.warning("The game ended as {}; trying again", race)
        else:
            findings.races.add(race.name.lower())
            return


def main(argv: Sequence[str]) -> None:
    """Sweep the races named, or all three, and write what turned up."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("races", nargs="*", help="terran, protoss or zerg; all three when none are named")
    parser.add_argument("--out", type=Path, default=OUT, help="where to write the findings")
    arguments = parser.parse_args(argv)
    playable = {race.name.lower(): race for race in (Race.TERRAN, Race.PROTOSS, Race.ZERG)}
    if unknown := set(arguments.races) - set(playable):
        raise SystemExit(f"No race is called {', '.join(sorted(unknown))}")
    races = [playable[name] for name in arguments.races] or list(playable.values())
    installation = Installation.find()
    findings = Findings()
    for race in races:
        sweep(race, installation, findings)
    arguments.out.write_text(json.dumps(findings.as_json(), indent=1) + "\n", encoding="utf-8")
    logger.info("Wrote the findings to {}", arguments.out)


if __name__ == "__main__":
    main(sys.argv[1:])
