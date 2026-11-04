"""A Terran bot written with NachOS alone: it trains SCVs, which speed mine, builds supply depots, barracks and command
centers, and attacks with marines.

    uv run python examples/demo_bot.py --ComputerRace ZERG --ComputerDifficulty MEDIUM --Map PylonAIE_v4

It takes the options of `run_from_command_line`, so the match workflow can play it as it plays `run_game.py`.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from sc2nachos import AbilityId, Api, GameTime, OwnUnit, Race, UnitType, UnitTypeId, run_from_command_line
from sc2nachos.events import EventPriority, OwnConstructionFinishedEvent, TurnEvent
from sc2nachos.extensions.speed_mining import SpeedMining
from sc2nachos.extensions.upgrade_inference import UpgradeInference

if TYPE_CHECKING:
    from sc2nachos import Expansion, Point, Units


class Extensions:
    """The demo bot's extensions: speed mining for its SCVs, and upgrade inference, whose findings every enemy unit's
    weapons and speed count."""

    def __init__(self, api: Api) -> None:
        self.speed_mining = SpeedMining(api)
        self.upgrade_inference = UpgradeInference(api)


api = Api(extensions=Extensions)

MAX_TOWNHALLS = 4
MAX_WORKERS = 66
BARRACKS_PER_TOWNHALL = 3
MAX_BARRACKS = 10
ATTACK_WITH = 24
RETREAT_BELOW = 8
DEFEND_WITHIN = 15.0


# --- Spending


class Budget:
    """What is left to spend this turn: the bank, less what this turn's orders will cost once they go out."""

    def __init__(self) -> None:
        pending_cost = api.orders.pending_cost
        self.resources_left = api.resources - pending_cost.resources
        self.supply_left = api.supply.left - pending_cost.supply

    def try_pay_for(self, ability: AbilityId, *, save: bool = False) -> bool:
        """Whether what is left pays for `ability`, taking its cost if so. With `save`, its cost is set aside even if
        not, so nothing cheaper spends it first."""
        cost = api.data.abilities[ability].cost
        affordable = self.resources_left.covers(cost.resources) and self.supply_left >= cost.supply
        if affordable or save:
            self.resources_left -= cost.resources
            self.supply_left -= cost.supply
        return affordable


@api.on(TurnEvent)
def macro(_: TurnEvent) -> None:
    """Spend the turn's money, most important first."""
    budget = Budget()
    train_workers(budget)
    build_supply(budget)
    expand(budget)
    build_barracks(budget)
    train_marines(budget)


# --- Workers


def train_workers(budget: Budget) -> None:
    """Train an SCV at each idle townhall, up to what the bases can use and a few to build with."""
    townhalls = api.units.own.townhalls.complete
    wanted = min(sum(townhall.ideal_harvesters for townhall in townhalls) + 6, MAX_WORKERS)
    count = len(api.units.own.workers) + len(api.in_production(UnitTypeId.SCV))
    for townhall in townhalls.idle:
        if count >= wanted:
            return
        if api.orders.issued_to(townhall, include_sent=True):
            continue
        if budget.try_pay_for(AbilityId.COMMAND_CENTER_TRAIN_SCV):
            api.orders.issue(townhall, AbilityId.COMMAND_CENTER_TRAIN_SCV)
            count += 1


# At a low priority, so it runs after `macro`, whose builders `_free_scvs` then leaves out.
@api.on(TurnEvent, min_interval=GameTime(steps=8), priority=EventPriority.LOW)
def hand_scvs_to_speed_mining(_: TurnEvent) -> None:
    """Hand each free SCV that speed mining does not hold over to it, which picks the SCV's field: at the base nearest
    it with a field fewer than two SCVs mine. An SCV with no such field keeps on as it is, to build or for the next
    base: idle, or gathering where speed mining let it go, its base's command center gone."""
    speed_mining = api.extensions.speed_mining
    for scv in _free_scvs():
        if scv not in speed_mining.miners:
            speed_mining.mine(scv)


# --- Building


def _free_scvs() -> Units[OwnUnit[Any]]:
    """The SCVs free to build: neither building nor on their way to build, and given nothing lately. Besides building,
    this bot's SCVs only mine."""
    return api.units.own.workers.filter(
        lambda scv: not scv.is_constructing and not api.orders.issued_to(scv, include_sent=True)
    )


def _build(ability: AbilityId, site: Point) -> bool:
    """Order the free SCV nearest `site` to build there. The game charges the build at once, while the SCV walks."""
    scvs = _free_scvs()
    if not scvs:
        return False
    api.orders.issue(scvs.closest_to(site), ability, target=site)
    return True


def _ordered_count(ability: AbilityId) -> int:
    """How many builds of `ability` SCVs were given that `api.in_production` may not count yet: ordered this turn, or
    sent and maybe not shown. One shown already may count in both for an observation or two."""
    return sum(
        1
        for scv in api.units.own.workers
        for order in api.orders.issued_to(scv, include_sent=True)
        if order.ability is ability
    )


def build_supply(budget: Budget) -> None:
    """Build a depot on the far side of the main's townhall from its minerals before supply runs out, saving for it
    first."""
    supply = api.supply
    if supply.cap >= 200:
        return
    producers = len(api.units.own.townhalls) + len(api.units.own.of_type(UnitType.AnyBarracks))
    in_progress = len(api.in_production(UnitTypeId.SUPPLY_DEPOT)) + _ordered_count(AbilityId.SCV_BUILD_SUPPLY_DEPOT)
    if supply.left + 8 * in_progress >= 2 + 2 * producers or in_progress >= 3:
        return
    site = api.placement.nearest_free(UnitTypeId.SUPPLY_DEPOT, near=_depot_anchor(), max_distance=25)
    if site is not None and budget.try_pay_for(AbilityId.SCV_BUILD_SUPPLY_DEPOT, save=True):
        _build(AbilityId.SCV_BUILD_SUPPLY_DEPOT, site)


def _depot_anchor() -> Point:
    """8 tiles from the main's townhall on the side away from its minerals, out of the miners' way."""
    main = api.player.start_location
    return main.center.towards(main.mineral_fields.center, -8)


@api.on(OwnConstructionFinishedEvent.only(UnitType.SupplyDepot))
def lower_depot(event: OwnConstructionFinishedEvent) -> None:
    """Lower a finished depot, so it blocks nothing."""
    api.orders.issue(event.unit, AbilityId.SUPPLY_DEPOT_LOWER)


def expand(budget: Budget) -> None:
    """Build a command center at the next free expansion once the bases have nearly all the SCVs they can use, or the
    bank is full, saving for it first."""
    townhalls = api.units.own.townhalls
    if len(townhalls) >= MAX_TOWNHALLS or not townhalls.complete:
        return
    building = len(api.in_production(UnitTypeId.COMMAND_CENTER)) + _ordered_count(AbilityId.SCV_BUILD_COMMAND_CENTER)
    if building:
        return
    ideal = sum(townhall.ideal_harvesters for townhall in townhalls.complete)
    if len(api.units.own.workers) < ideal - 4 and api.resources.minerals < 600:
        return
    if not api.units.own.of_type(UnitType.Barracks):
        return
    expansion = _next_expansion()
    if expansion is not None and budget.try_pay_for(AbilityId.SCV_BUILD_COMMAND_CENTER, save=True):
        _build(AbilityId.SCV_BUILD_COMMAND_CENTER, expansion.center)


def _next_expansion() -> Expansion | None:
    """The expansion nearest this player's start, and reachable from it, where a command center may stand now and no
    enemy is in sight."""
    for expansion in api.expansions:
        if math.isinf(expansion.pathing_distance_from_start):
            break
        if not api.placement.is_free(UnitTypeId.COMMAND_CENTER, at=expansion.center):
            continue
        if api.units.enemy.within(12, of=expansion.center):
            continue
        return expansion
    return None


def build_barracks(budget: Budget) -> None:
    """Build one barracks before the first expansion, and then more as the bases grow, each with a tile free all
    around it, so the units it trains can always walk out."""
    if not api.units.own.of_type(UnitType.AnySupplyDepot).complete:
        return
    count = len(api.units.own.of_type(UnitType.AnyBarracks)) + _ordered_count(AbilityId.SCV_BUILD_BARRACKS)
    townhalls = len(api.units.own.townhalls)
    if count >= min(BARRACKS_PER_TOWNHALL * townhalls, MAX_BARRACKS) or (count >= 1 and townhalls < 2):
        return
    start = api.player.start_location
    anchor = start.center.towards(start.natural.center, 9)
    site = api.placement.nearest_free(UnitTypeId.BARRACKS, near=anchor, max_distance=math.inf, margin=1)
    if site is not None and budget.try_pay_for(AbilityId.SCV_BUILD_BARRACKS):
        _build(AbilityId.SCV_BUILD_BARRACKS, site)


def train_marines(budget: Budget) -> None:
    """Train a marine at each idle barracks."""
    for barracks in api.units.own.of_type(UnitType.Barracks).complete.idle:
        if api.orders.issued_to(barracks, include_sent=True):
            continue
        if budget.try_pay_for(AbilityId.BARRACKS_TRAIN_MARINE):
            api.orders.issue(barracks, AbilityId.BARRACKS_TRAIN_MARINE)


# --- The army


class Army:
    """The marines: they wait at the defense location, defend the bases, and attack once there are enough of them."""

    def __init__(self) -> None:
        self.attacking = False
        self.last_seen: dict[Point, int] = {}
        api.subscribe(self)

    @api.on(TurnEvent, min_interval=GameTime(steps=4))
    def command(self, _: TurnEvent) -> None:
        """Defend against what comes near the bases, attack once there are enough marines, and fall back once too few
        are left."""
        marines = api.units.own.of_type(UnitType.Marine)
        self._remember_what_is_seen()
        threats = api.units.enemy.non_structures.within(DEFEND_WITHIN, of=api.units.own.townhalls)
        if len(marines) >= ATTACK_WITH:
            self.attacking = True
        elif len(marines) < RETREAT_BELOW:
            self.attacking = False
        if threats and not self.attacking:
            for marine in marines:
                api.orders.issue(marine, AbilityId.ATTACK, target=threats.closest_to(marine.position).position)
        elif self.attacking:
            api.orders.issue(marines, AbilityId.ATTACK, target=self._attack_target(marines))
        else:
            defense_location = self._defense_location()
            idle = marines.idle.beyond(4, of=defense_location)
            if idle:
                api.orders.issue(idle, AbilityId.ATTACK, target=defense_location)

    def _defense_location(self) -> Point:
        """Where the army waits: toward the map's center from this player's townhall furthest from its start."""
        taken = [expansion for expansion in api.expansions if isinstance(expansion.townhall, OwnUnit)]
        base = taken[-1].center if taken else api.map.start_location
        return base.towards(api.map.playable_area.center, 7)

    def _attack_target(self, marines: Units[OwnUnit[UnitType.Marine]]) -> Point:
        """The enemy structure nearest the army, or, with none known, the place looked at longest ago."""
        structures = api.units.enemy.structures
        if structures:
            return structures.closest_to(marines.center).position
        return min(_places_to_look(), key=lambda place: self.last_seen.get(place, -1))

    def _remember_what_is_seen(self) -> None:
        """Note the step at which each place to look is in sight."""
        for place in _places_to_look():
            if api.vision[place]:
                self.last_seen[place] = api.step


def _places_to_look() -> list[Point]:
    """The opponents' start locations and every expansion this player's units can walk to."""
    reachable = [expansion for expansion in api.expansions if not math.isinf(expansion.pathing_distance_from_start)]
    return [*api.map.opponent_start_locations, *(expansion.center for expansion in reachable)]


army = Army()

if __name__ == "__main__":
    run_from_command_line(api, race=Race.TERRAN, name="NachOS demo")
