"""A Zerg bot written with NachOS alone: it morphs drones, which speed mine and collect vespene, overlords, hatcheries,
a spawning pool, extractors and a roach warren; its queens inject larvae and plant creep tumors, each of which plants
one more; and it attacks with zerglings and roaches.

    uv run python examples/zerg_demo_bot.py --ComputerRace TERRAN --ComputerDifficulty MEDIUM --Map PylonAIE_v4

It takes the options of `run_from_command_line`, so the match workflow can play it as it plays `run_game.py`.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from sc2nachos import (
    AbilityId,
    Api,
    BuffId,
    GameTime,
    OwnUnit,
    Race,
    Unit,
    UnitDict,
    Units,
    UnitType,
    UnitTypeId,
    UpgradeId,
    run_from_command_line,
)
from sc2nachos.events import EventPriority, TurnEvent
from sc2nachos.extensions.speed_mining import SpeedMining
from sc2nachos.extensions.upgrade_inference import UpgradeInference

if TYPE_CHECKING:
    from sc2nachos import Expansion, Point


class Extensions:
    """The demo bot's extensions: speed mining for its drones, and upgrade inference, whose findings every enemy unit's
    weapons and speed count."""

    def __init__(self, api: Api) -> None:
        self.speed_mining = SpeedMining(api)
        self.upgrade_inference = UpgradeInference(api)


api = Api(extensions=Extensions)

MAX_TOWNHALLS = 4
MAX_WORKERS = 66
DRONES_PER_TOWNHALL = 16
COLLECTORS_PER_EXTRACTOR = 3
MAX_EXTRACTORS = 3
QUEENS_PLANTING_CREEP = 1
MAX_QUEENS = 5
ARMY_SUPPLY_PER_DRONE_BEYOND_THE_FIRST_BASE = 0.5
ATTACK_WITH_SUPPLY = 40
RETREAT_BELOW_SUPPLY = 12
DEFEND_WITHIN = 15.0
ARMY_MORPHS = frozenset({AbilityId.LARVA_MORPH_ZERGLING, AbilityId.LARVA_MORPH_ROACH})
CREEP_TUMOR_REACH = 7.0
# A tumor is refused a plant up to 287 steps after its burrow ends, and takes one from 289 (in game).
TUMOR_PLANTS_AFTER_ITS_BURROW = GameTime(steps=289)


# --- Spending


class Budget:
    """What is left to spend this turn: `api.budget` as the turn starts, less each command's cost as the bot gives it,
    and what it sets aside."""

    def __init__(self) -> None:
        self.left = api.budget

    def can_pay_for(self, ability: AbilityId) -> bool:
        """Whether what is left pays for `ability`."""
        return self.left.covers(api.data.abilities[ability].cost)

    def try_pay_for(self, ability: AbilityId, *, save: bool = False) -> bool:
        """Whether what is left pays for `ability`, taking its cost if so. With `save`, its cost is set aside even if
        not, so nothing cheaper spends it first."""
        affordable = self.can_pay_for(ability)
        if affordable or save:
            self.left -= api.data.abilities[ability].cost
        return affordable


@api.on(TurnEvent)
def spend_money(_: TurnEvent) -> None:
    """Spend the turn's money, most important first."""
    budget = Budget()
    build_overlords(budget)
    train_queens(budget)
    take_next_expansion(budget)
    build_tech(budget)
    research_zergling_speed(budget)
    spend_larvae(budget)


def _ordered_count(ability: AbilityId) -> int:
    """How many commands of `ability` this player's units were given that `api.in_production` may not count yet:
    staged this turn, or sent and maybe not shown. One shown already may count in both for an observation or two."""
    return sum(1 for unit in api.units.own for command in unit.pending_commands if command.ability is ability)


def _free_larvae() -> Units[OwnUnit[UnitType.Larva]]:
    """The larvae given nothing yet."""
    return api.units.own.of_type(UnitType.Larva).filter(lambda larva: not larva.pending_commands)


# --- Larvae


def build_overlords(budget: Budget) -> None:
    """Morph an overlord before supply runs out, saving for it first."""
    supply = api.supply
    if supply.cap >= 200:
        return
    in_progress = len(api.in_production(UnitTypeId.OVERLORD)) + _ordered_count(AbilityId.LARVA_MORPH_OVERLORD)
    townhalls = len(api.units.own.townhalls.complete)
    if supply.left + 8 * in_progress >= 3 + 3 * townhalls or in_progress >= 4:
        return
    larvae = _free_larvae()
    if larvae and budget.try_pay_for(AbilityId.LARVA_MORPH_OVERLORD, save=True):
        larvae[0].command(AbilityId.LARVA_MORPH_OVERLORD)


def spend_larvae(budget: Budget) -> None:
    """Morph each free larva into a drone while the bases want more and the army is big enough for them, and else into
    a roach or a pair of zerglings once the pool stands."""
    drones = len(api.units.own.workers) + len(api.in_production(UnitTypeId.DRONE))
    army_supply = _army_supply(_army_units()) + _army_supply_in_eggs()
    threatened = bool(_threats())
    for larva in _free_larvae():
        ability = _next_larva_morph(budget, drones, army_supply, threatened=threatened)
        if ability is None or not budget.try_pay_for(ability):
            return
        larva.command(ability)
        if ability is AbilityId.LARVA_MORPH_DRONE:
            drones += 1
        else:
            army_supply += api.data.abilities[ability].cost.supply


def _next_larva_morph(budget: Budget, drones: int, army_supply: float, *, threatened: bool) -> AbilityId | None:
    """What the next larva becomes: a drone while more are wanted, the base is safe and the army keeps up, else an army
    unit, a roach where the warren stands and the vespene pays for one. `None` before the pool stands, with no drone
    wanted."""
    pool_done = bool(api.units.own.of_type(UnitType.SpawningPool).complete)
    army_needed = ARMY_SUPPLY_PER_DRONE_BEYOND_THE_FIRST_BASE * max(drones - DRONES_PER_TOWNHALL, 0)
    if drones < _wanted_drones() and not threatened and (not pool_done or army_supply >= army_needed):
        return AbilityId.LARVA_MORPH_DRONE
    if not pool_done:
        return None
    if api.units.own.of_type(UnitType.RoachWarren).complete and budget.can_pay_for(AbilityId.LARVA_MORPH_ROACH):
        return AbilityId.LARVA_MORPH_ROACH
    return AbilityId.LARVA_MORPH_ZERGLING


def _wanted_drones() -> int:
    """Enough drones to mine at every townhall, finished or not, and collect at every extractor, and two to build."""
    extractors = len(api.units.own.gas_buildings)
    wanted = DRONES_PER_TOWNHALL * len(api.units.own.townhalls) + COLLECTORS_PER_EXTRACTOR * extractors + 2
    return min(wanted, MAX_WORKERS)


# --- Drones


# At a low priority, so it runs after `spend_money`, whose builders `_free_drones` then leaves out.
@api.on(TurnEvent, min_interval=GameTime(steps=8), priority=EventPriority.LOW)
def hand_drones_to_speed_mining(_: TurnEvent) -> None:
    """Keep three drones collecting at each extractor speed mining collects at, taking them from the miners, and hand
    every other free drone that speed mining does not hold over to it to mine."""
    speed_mining = api.extensions.speed_mining
    missing = COLLECTORS_PER_EXTRACTOR * len(_extractors_to_collect_at()) - len(speed_mining.collectors)
    if missing > 0:
        for drone in speed_mining.release_miners(missing):
            speed_mining.collect(drone)
    for drone in _free_drones():
        if drone not in speed_mining.workers:
            speed_mining.mine(drone)


def _free_drones() -> Units[OwnUnit[Any]]:
    """The drones free to build: given nothing lately. Besides building, this bot's drones only mine and collect."""
    return api.units.own.workers.filter(lambda drone: not drone.pending_commands)


def _order_drone_to_build(ability: AbilityId, target: Point | Unit[Any]) -> bool:
    """Command the free drone nearest `target` to morph into a structure there. The game charges it at once, while the
    drone walks."""
    drones = _free_drones()
    if not drones:
        return False
    site = target.position if isinstance(target, Unit) else target
    drones.closest_to(site).command(ability, target=target)
    return True


# --- Structures


def take_next_expansion(budget: Budget) -> None:
    """Morph a hatchery at the next free expansion once the bases have nearly all the drones they can use, or the bank
    is full, saving for it first. A third one waits for the pool."""
    townhalls = api.units.own.townhalls
    if len(townhalls) >= MAX_TOWNHALLS:
        return
    if api.in_production(UnitTypeId.HATCHERY) or _ordered_count(AbilityId.DRONE_MORPH_HATCHERY):
        return
    if len(api.units.own.workers) < DRONES_PER_TOWNHALL * len(townhalls) - 2 and api.resources.minerals < 500:
        return
    if len(townhalls) >= 2 and not api.units.own.of_type(UnitType.SpawningPool):
        return
    expansion = _next_expansion()
    if expansion is not None and budget.try_pay_for(AbilityId.DRONE_MORPH_HATCHERY, save=True):
        _order_drone_to_build(AbilityId.DRONE_MORPH_HATCHERY, expansion.center)


def _next_expansion() -> Expansion | None:
    """The expansion nearest this player's start, and reachable from it, where a hatchery may stand now and no enemy is
    in sight."""
    for expansion in api.expansions:
        if math.isinf(expansion.pathing_distance_from_start):
            break
        if not api.placement.is_free(UnitTypeId.HATCHERY, at=expansion.center):
            continue
        if api.units.enemy.within(12, of=expansion.center):
            continue
        return expansion
    return None


def build_tech(budget: Budget) -> None:
    """Morph a spawning pool once the natural is under way or 18 drones mine, then an extractor, a roach warren once the
    pool stands, and an extractor a finished townhall after it, each saving for it first."""
    own = api.units.own
    if not own.of_type(UnitType.SpawningPool) and not _ordered_count(AbilityId.DRONE_MORPH_SPAWNING_POOL):
        if len(own.townhalls) >= 2 or len(own.workers) >= 18:
            _build_near_main(budget, AbilityId.DRONE_MORPH_SPAWNING_POOL, UnitTypeId.SPAWNING_POOL)
        return
    has_warren = bool(own.of_type(UnitType.RoachWarren)) or bool(_ordered_count(AbilityId.DRONE_MORPH_ROACH_WARREN))
    wanted_extractors = min(len(own.townhalls.complete), MAX_EXTRACTORS) if has_warren else 1
    if len(own.gas_buildings) + _ordered_count(AbilityId.DRONE_MORPH_EXTRACTOR) < wanted_extractors:
        geyser = _free_geyser()
        if geyser is not None and budget.try_pay_for(AbilityId.DRONE_MORPH_EXTRACTOR):
            _order_drone_to_build(AbilityId.DRONE_MORPH_EXTRACTOR, geyser)
    if not has_warren and own.of_type(UnitType.SpawningPool).complete and len(own.townhalls) >= 2:
        _build_near_main(budget, AbilityId.DRONE_MORPH_ROACH_WARREN, UnitTypeId.ROACH_WARREN)


def _build_near_main(budget: Budget, ability: AbilityId, unit_type: UnitTypeId) -> None:
    """Morph a structure on the main's creep, on the far side of its hatchery from its minerals, with a tile free all
    around it, saving for it first."""
    main = api.player.start_location
    anchor = main.center.towards(main.mineral_fields.center, -6)
    site = api.placement.nearest_free(unit_type, near=anchor, max_distance=12, margin=1)
    if site is not None and budget.try_pay_for(ability, save=True):
        _order_drone_to_build(ability, site)


def _expansions_with_a_finished_townhall() -> list[Expansion]:
    """The expansions where a finished townhall of this player's stands."""
    return [
        expansion
        for expansion in api.expansions
        if isinstance(townhall := expansion.townhall, OwnUnit) and townhall.is_complete
    ]


def _free_geyser() -> Unit[UnitType.AnyVespeneGeyser] | None:
    """A geyser at one of this player's finished townhalls with no gas building on it."""
    gas_buildings = api.units.gas_buildings
    for expansion in _expansions_with_a_finished_townhall():
        for geyser in expansion.geysers:
            if not gas_buildings.within(1, of=geyser.position):
                return geyser
    return None


def _extractors_to_collect_at() -> list[Unit[Any]]:
    """The finished extractors with vespene left at one of this player's finished townhalls: those speed mining
    collects at."""
    extractors = api.units.own.gas_buildings.complete
    return [
        extractor
        for expansion in _expansions_with_a_finished_townhall()
        for geyser in expansion.geysers
        for extractor in extractors.within(1, of=geyser.position)
        if extractor.vespene_contents
    ]


def train_queens(budget: Budget) -> None:
    """Train a queen at each idle finished townhall, up to one a townhall and some to plant creep tumors, once the
    pool stands."""
    own = api.units.own
    if not own.of_type(UnitType.SpawningPool).complete:
        return
    queens = len(own.of_type(UnitType.Queen)) + len(api.in_production(UnitTypeId.QUEEN))
    wanted = min(len(own.townhalls.complete) + QUEENS_PLANTING_CREEP, MAX_QUEENS)
    for townhall in own.townhalls.complete.idle:
        if queens >= wanted:
            return
        if townhall.pending_commands:
            continue
        if budget.try_pay_for(AbilityId.HATCHERY_TRAIN_QUEEN):
            townhall.command(AbilityId.HATCHERY_TRAIN_QUEEN)
            queens += 1


def research_zergling_speed(budget: Budget) -> None:
    """Research Metabolic Boost at the pool once the vespene pays for it."""
    if UpgradeId.ZERGLING_SPEED in api.upgrades or UpgradeId.ZERGLING_SPEED in api.researches:
        return
    for pool in api.units.own.of_type(UnitType.SpawningPool).complete.idle:
        if pool.pending_commands:
            continue
        if budget.try_pay_for(AbilityId.SPAWNING_POOL_RESEARCH_ZERGLING_SPEED):
            pool.command(AbilityId.SPAWNING_POOL_RESEARCH_ZERGLING_SPEED)


# --- Queens and creep


def _creep_tumor_site(origin: Point) -> Point | None:
    """Where a creep tumor may go, on creep, about `CREEP_TUMOR_REACH` from `origin` toward the enemy's start."""
    target = origin.towards(api.map.opponent_start_locations[0], CREEP_TUMOR_REACH)
    return api.placement.nearest_free(UnitTypeId.CREEP_TUMOR, near=target, max_distance=3)


class QueenCommander:
    """One queen to each finished townhall injects its larvae, and plants a creep tumor with the energy left over; the
    others only plant tumors. Each defends what comes near its townhall."""

    def __init__(self) -> None:
        self.townhall_of: UnitDict[OwnUnit[UnitType.Queen], Unit[Any]] = UnitDict()
        api.subscribe(self)

    @api.on(TurnEvent, min_interval=GameTime(steps=8))
    def order_queens(self, _: TurnEvent) -> None:
        """Pair the queens with the townhalls, and command each that is free."""
        queens = api.units.own.of_type(UnitType.Queen)
        self._pair(queens)
        threats = api.units.enemy.non_structures
        for queen in queens:
            townhall = self.townhall_of.get(queen)
            home = townhall.position if townhall is not None else api.player.start_location.center
            near = threats.within(DEFEND_WITHIN, of=home)
            if near:
                queen.command(AbilityId.ATTACK, target=near.closest_to(queen.position).position)
            elif queen.is_idle and not queen.pending_commands:
                self._order_free_queen(queen, townhall)

    def _pair(self, queens: Units[OwnUnit[UnitType.Queen]]) -> None:
        """Give each queen without a townhall the nearest finished one no queen has."""
        townhalls = api.units.own.townhalls.complete
        for queen, townhall in list(self.townhall_of.items()):
            if townhall not in townhalls:
                del self.townhall_of[queen]
        unpaired = townhalls.without(Units(self.townhall_of.values()))
        for queen in queens:
            if queen not in self.townhall_of and unpaired:
                townhall = unpaired.closest_to(queen.position)
                self.townhall_of[queen] = townhall
                unpaired = unpaired.without(townhall)

    def _order_free_queen(self, queen: OwnUnit[UnitType.Queen], townhall: Unit[Any] | None) -> None:
        """Inject the queen's townhall if it can, or plant a creep tumor while keeping the energy for the next inject,
        or walk back to its townhall."""
        if townhall is not None and queen.energy >= 25 and not townhall.has_buff(BuffId.QUEEN_INJECTED):
            queen.command(AbilityId.QUEEN_INJECT, target=townhall)
        elif queen.energy >= (50 if townhall is not None else 25):
            origin = townhall.position if townhall is not None else queen.position
            site = _creep_tumor_site(origin)
            if site is not None:
                queen.command(AbilityId.BUILD_CREEP_TUMOR, target=site)
        elif townhall is not None and queen.distance_to(townhall) > 6:
            queen.command(AbilityId.MOVE, target=townhall.position)


class CreepSpread:
    """Each creep tumor plants one more toward the enemy, as soon as it can. One the game refuses plants none."""

    def __init__(self) -> None:
        self.plants_at: UnitDict[OwnUnit[Any], GameTime | None] = UnitDict()
        api.subscribe(self)

    @api.on(TurnEvent, min_interval=GameTime(steps=16))
    def plant_tumors(self, _: TurnEvent) -> None:
        """Command each tumor that can plant one now, and has not been commanded to, to plant one toward the enemy."""
        for tumor in api.units.own.of_type(UnitType.CreepTumorBurrowed):
            if tumor.orders:
                continue
            plants_at = self.plants_at.setdefault(tumor, api.time + TUMOR_PLANTS_AFTER_ITS_BURROW)
            if plants_at is None or api.time < plants_at:
                continue
            site = _creep_tumor_site(tumor.position)
            if site is not None:
                tumor.command(AbilityId.BUILD_CREEP_TUMOR, target=site)
                self.plants_at[tumor] = None


# --- The army


def _army_units() -> Units[OwnUnit[Any]]:
    """The zerglings and roaches."""
    return api.units.own.of_type(UnitType.Zergling, UnitType.Roach)


def _army_supply_in_eggs() -> float:
    """The supply of the zerglings and roaches the eggs will hatch, as their morphs charge it: 1 for a pair of
    zerglings."""
    return sum(
        api.data.abilities[order.ability].cost.supply
        for egg in api.units.own.of_type(UnitType.Egg)
        if (order := egg.current_order) is not None and order.ability in ARMY_MORPHS
    )


def _army_supply(army: Units[OwnUnit[Any]]) -> float:
    """The supply `army` takes."""
    return sum(unit.type_data.supply_used for unit in army)


def _threats() -> Units[Unit[Any]]:
    """The enemy's ground units near this player's townhalls but workers, which zerglings and roaches can fight."""
    near = api.units.enemy.non_structures.excluding_type(UnitType.Worker).within(
        DEFEND_WITHIN, of=api.units.own.townhalls
    )
    return near.filter(lambda unit: not unit.is_flying)


class Army:
    """The zerglings and roaches: they wait at the defense location, defend the bases, and attack once there are enough
    of them."""

    def __init__(self) -> None:
        self.attacking = False
        api.subscribe(self)

    @api.on(TurnEvent, min_interval=GameTime(steps=4))
    def order_army(self, _: TurnEvent) -> None:
        """Defend against what comes near the bases, attack once the army is big enough, and fall back once too little
        of it is left."""
        army = _army_units()
        supply = _army_supply(army)
        threats = _threats()
        was_attacking = self.attacking
        if supply >= ATTACK_WITH_SUPPLY:
            self.attacking = True
        elif supply < RETREAT_BELOW_SUPPLY:
            self.attacking = False
        if threats and not self.attacking:
            for unit in army:
                unit.command(AbilityId.ATTACK, target=threats.closest_to(unit.position).position)
        elif self.attacking:
            api.commands.command(army, AbilityId.ATTACK, target=self._attack_target(army))
        elif was_attacking:
            api.commands.command(army, AbilityId.MOVE, target=self._defense_location())
        else:
            defense_location = self._defense_location()
            idle = army.idle.beyond(4, of=defense_location)
            if idle:
                api.commands.command(idle, AbilityId.ATTACK, target=defense_location)

    def _defense_location(self) -> Point:
        """Where the army waits: toward the map's center from this player's townhall furthest from its start."""
        taken = [expansion for expansion in api.expansions if isinstance(expansion.townhall, OwnUnit)]
        base = taken[-1].center if taken else api.map.start_location
        return base.towards(api.map.playable_area.center, 7)

    def _attack_target(self, army: Units[OwnUnit[Any]]) -> Point:
        """The enemy structure on the ground nearest the army, or, with none known, the place looked at longest ago."""
        structures = api.units.enemy.structures.filter(lambda structure: not structure.is_flying)
        if structures:
            return structures.closest_to(army.center).position
        last_in_sight = api.vision.last_in_sight
        return min(_places_to_look(), key=lambda place: last_in_sight[place])


def _places_to_look() -> list[Point]:
    """The opponents' start locations and every expansion this player's units can walk to."""
    reachable = [expansion for expansion in api.expansions if not math.isinf(expansion.pathing_distance_from_start)]
    return [*api.map.opponent_start_locations, *(expansion.center for expansion in reachable)]


queen_commander = QueenCommander()
creep_spread = CreepSpread()
army = Army()

if __name__ == "__main__":
    run_from_command_line(api, race=Race.ZERG, name="NachOS Zerg demo")
