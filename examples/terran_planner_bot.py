"""A Terran bot whose builds the build planner decides: it values SCVs, marines and marauders, more in a mix, with
Stimpack and as the game goes on, and five upgrades. It makes what each plan lists in the plan's order, and attacks with
its marines and marauders, stimming them in a fight.

    uv run python examples/terran_planner_bot.py --ComputerRace ZERG --ComputerDifficulty MEDIUM --Map PylonAIE_v4

The planner decides what to make and when: the SCVs, the supply depots, the barracks and their add-ons, the command
centers, the refineries and how many SCVs collect vespene. The bot decides where each structure goes, which SCV builds
it, and what the army does. It asks for a new plan every 20 seconds, and `plan_now` solves before it returns, so a turn
that plans takes up to a second longer.

It takes the options of `run_from_command_line`, so the match workflow can play it as it plays `run_game.py`.
"""

from __future__ import annotations

import itertools
import math
from typing import TYPE_CHECKING, Any

from loguru import logger

from sc2nachos import (
    AbilityId,
    Api,
    BuffId,
    GameTime,
    OwnUnit,
    Point,
    Race,
    Unit,
    Units,
    UnitType,
    UnitTypeId,
    UpgradeId,
    run_from_command_line,
)
from sc2nachos.events import EventPriority, OwnConstructionFinishedEvent, TurnEvent
from sc2nachos.extensions.planner import Planner, Reward, RewardTerm, WeightCurve
from sc2nachos.extensions.speed_mining import SpeedMining
from sc2nachos.gametime import TimeCurve

if TYPE_CHECKING:
    from sc2nachos import Expansion
    from sc2nachos.extensions.planner import PlanItem
    from sc2nachos.gamedata import Cost


class Extensions:
    """The planner bot's extensions: the build planner, and speed mining for its SCVs."""

    def __init__(self, api: Api) -> None:
        self.planner = Planner(api)
        self.speed_mining = SpeedMining(api)


api = Api(extensions=Extensions)

# The army worth more as the game goes on: twice as much from 12:00 as up to 5:00, as a `time_factor` of its terms.
ARMY_WORTH_RISING = TimeCurve([(GameTime(minutes=5), 1.0), (GameTime(minutes=12), 2.0)])
# What each unit is worth a second it is alive, and each upgrade a second it is done. SCVs up to three bases' worth, for
# which the plans take command centers. Marines and marauders without end, and as much again in a mix of three marines
# to a marauder up to 45 marines, so that a plan makes both; and each more once Stimpack is done, a marauder for two.
REWARD = Reward.weighted(
    {UnitTypeId.SCV: WeightCurve.up_to(1.0, 66)},
    terms=[
        RewardTerm.weighted(UnitTypeId.MARINE, 0.5, time_factor=ARMY_WORTH_RISING),
        RewardTerm.weighted(UnitTypeId.MARAUDER, 1.1, time_factor=ARMY_WORTH_RISING),
        RewardTerm.combination(
            [{UnitTypeId.MARINE: 1}, {UnitTypeId.MARAUDER: 3}],
            WeightCurve.up_to(0.87, 45),
            time_factor=ARMY_WORTH_RISING,
        ),
        RewardTerm.weighted(
            {UnitTypeId.MARINE: 1, UnitTypeId.MARAUDER: 2},
            WeightCurve.up_to(0.25, 70),
            time_factor=ARMY_WORTH_RISING,
            requirements=[UpgradeId.STIMPACK],
        ),
    ],
    upgrade_weights={
        UpgradeId.STIMPACK: 10.0,
        UpgradeId.COMBAT_SHIELD: 5.0,
        UpgradeId.CONCUSSIVE_SHELLS: 3.0,
        UpgradeId.TERRAN_INFANTRY_WEAPONS_1: 6.0,
        UpgradeId.TERRAN_INFANTRY_ARMOR_1: 5.0,
    },
)
REPLAN_EVERY = GameTime(seconds=20)
PLAN_TIME_LIMIT_SECONDS = 1.0
EXPANSIONS_PLANNED = 2
# How far ahead of its planned start an item may start, if it can.
START_EARLY_BY = GameTime(seconds=5)
ATTACK_WITH_SUPPLY = 40
RETREAT_BELOW_SUPPLY = 12
DEFEND_WITHIN = 15.0
# How far from a target an attack a unit carries out may be aimed and still count as aimed at it.
SAME_TARGET_WITHIN = 3.0
STIM_WITHIN = 6.0
STIM_ABOVE_HEALTH = 0.7


# --- Following the plan


class Budget:
    """What is left to spend this turn: the bank, less what this turn's orders will cost once they go out, and the
    orders that spend it. Each is given only if a unit of this player's can carry it out now and what is left pays for
    it, and says whether it was. Where a unit can carry it out but what is left falls short, its cost is set aside all
    the same, so that nothing after it spends what it waits for."""

    def __init__(self) -> None:
        pending_cost = api.orders.pending_cost
        self.resources_left = api.resources - pending_cost.resources
        self.supply_left = api.supply.left - pending_cost.supply

    def start_plan_item(self, item: PlanItem) -> bool:
        """Start `item`, by whichever order makes its product."""
        product = item.product
        if isinstance(product, UpgradeId):
            return self.research(product)
        if product is UnitTypeId.ORBITAL_COMMAND:
            return self.morph_to_orbital_command()
        if product in UnitType.AnyAddOn.type_ids:
            return self.build_add_on(product)
        if UnitTypeId.SCV in api.data.units[product].producers:
            return self.build_structure(product)
        return self.train(product)

    def train(self, unit_type: UnitTypeId) -> bool:
        """Train a `unit_type` at a finished structure with room in its queue, one with a tech lab for a unit that
        needs one, and one without a tech lab first for any other, so that the tech labs are left for the units that
        need them."""
        row = api.data.units[unit_type]
        needs_tech_lab = row.tech_requirements.needs_tech_lab
        producers = api.units.own.of_type(row.producers).complete.filter(
            lambda producer: (
                len(producer.orders) < producer.production_capacity
                and (producer.has_tech_lab or not needs_tech_lab)
                and _unit_is_free(producer)
            )
        )
        if not producers or not api.is_unlocked(unit_type) or not self._try_pay_for(row.cost):
            return False
        min(producers, key=lambda producer: producer.has_tech_lab).train(unit_type)
        return True

    def morph_to_orbital_command(self) -> bool:
        """Morph an idle command center into an orbital command."""
        row = api.data.units[UnitTypeId.ORBITAL_COMMAND]
        command_centers = api.units.own.of_type(UnitType.CommandCenter).complete.idle.filter(_unit_is_free)
        if not command_centers or not api.is_unlocked(UnitTypeId.ORBITAL_COMMAND) or not self._try_pay_for(row.cost):
            return False
        command_centers[0].issue(AbilityId.COMMAND_CENTER_MORPH_ORBITAL_COMMAND)
        return True

    def build_add_on(self, add_on_type: UnitTypeId) -> bool:
        """Build an `add_on_type` on an idle barracks without an add-on, whose add-on's ground was kept free when it
        was placed."""
        hosts = api.units.own.of_type(UnitType.Barracks).complete.idle.filter(
            lambda barracks: barracks.add_on is None and _unit_is_free(barracks)
        )
        if not hosts or not self._try_pay_for(api.data.units[add_on_type].cost):
            return False
        hosts[0].build(add_on_type)
        return True

    def research(self, upgrade: UpgradeId) -> bool:
        """Research `upgrade` at an idle structure that researches it."""
        row = api.data.upgrades[upgrade]
        researchers = api.units.own.of_type(row.researchers).complete.idle.filter(_unit_is_free)
        if not researchers or not api.is_unlocked(upgrade) or not self._try_pay_for(row.cost):
            return False
        researchers[0].research(upgrade)
        return True

    def build_structure(self, unit_type: UnitTypeId) -> bool:
        """Order the free SCV nearest the site of a `unit_type` to build it there. The game charges the build at once,
        while the SCV walks."""
        row = api.data.units[unit_type]
        scvs = _free_scvs()
        if not scvs or not api.is_unlocked(unit_type):
            return False
        site = _site_for_structure(unit_type)
        if site is None or not self._try_pay_for(row.cost):
            return False
        scvs.closest_to(site).build(unit_type, target=site)
        if unit_type is UnitTypeId.BARRACKS and isinstance(site, Point):
            # Its add-on's ground, 2.5 right of its center and 0.5 below, kept free of the structures placed after it.
            api.placement.reserve(UnitTypeId.TECH_LAB_BARRACKS, at=site + Point((2.5, -0.5)))
        return True

    def _try_pay_for(self, cost: Cost) -> bool:
        """Whether what is left pays for `cost`: its supply too if it takes any, so that a structure is paid for over
        the cap, once supply depots are lost. Its cost is taken either way."""
        affordable = self.resources_left.covers(cost.resources) and (
            cost.supply <= 0 or self.supply_left >= cost.supply
        )
        self.resources_left -= cost.resources
        self.supply_left -= cost.supply
        return affordable


class PlanFollower:
    """The planner's last plan, carried out: the items of it not started yet, each started in the plan's order as soon
    as a unit can make it and the budget pays for it."""

    def __init__(self) -> None:
        self.items_left: list[PlanItem] = []
        self.next_plan_time = GameTime.ZERO
        api.subscribe(self)

    @api.on(TurnEvent)
    def follow_plan(self, _: TurnEvent) -> None:
        """Plan again if it is time to, then start what is due of the plan: the items planned to start by
        `START_EARLY_BY` from now, which come first, since a plan lists its items in the order they start."""
        if api.time >= self.next_plan_time and not _production_ordered_but_not_shown():
            self._plan()
        due_by = api.time + START_EARLY_BY
        due = list(itertools.takewhile(lambda item: item.start <= due_by, self.items_left))
        budget = Budget()
        not_started = [item for item in due if not budget.start_plan_item(item)]
        self.items_left = not_started + self.items_left[len(due) :]

    def _plan(self) -> None:
        """Ask the planner for a plan from the game as it is now, which replaces the one followed, if it finds one."""
        plan = api.extensions.planner.plan_now(
            REWARD, expansions=_expansions_to_take(), time_limit=PLAN_TIME_LIMIT_SECONDS
        )
        self.next_plan_time = api.time + REPLAN_EVERY
        if plan is None:
            logger.warning("The planner found no plan at {}; following the last one", api.time.to_string(0))
            return
        logger.info("{}", plan.to_string())
        self.items_left = list(plan.items)


def _production_ordered_but_not_shown() -> bool:
    """Whether an order that costs something went out to a unit of this player's lately and the observation may not
    show it yet, so that the planner's state would leave it out and a new plan make it again."""
    abilities = api.data.abilities
    return any(
        (row := abilities.get(order.ability)) is not None and (row.cost.minerals or row.cost.vespene)
        for unit in api.units.own
        for order in api.orders.issued_to(unit, include_sent=True)
    )


def _expansions_to_take() -> list[Expansion]:
    """The expansions a plan may take, nearest the start first: those reachable from it where a command center may
    stand now and no enemy is in sight."""
    free = (expansion for expansion in api.expansions if _expansion_is_free_to_take(expansion))
    return [expansion for _, expansion in zip(range(EXPANSIONS_PLANNED), free, strict=False)]


def _expansion_is_free_to_take(expansion: Expansion) -> bool:
    """Whether `expansion` is reachable from this player's start, a command center may stand there now, and no enemy is
    in sight near it."""
    return (
        not math.isinf(expansion.pathing_distance_from_start)
        and api.placement.is_free(UnitTypeId.COMMAND_CENTER, at=expansion.center)
        and not api.units.enemy.within(12, of=expansion.center)
    )


def _unit_is_free(unit: OwnUnit[Any]) -> bool:
    """Whether `unit` was given no order this turn, nor one lately that the observation may not show yet."""
    return not api.orders.issued_to(unit, include_sent=True)


# --- Building


def _free_scvs() -> Units[OwnUnit[Any]]:
    """The SCVs free to build: neither building nor on their way to build, collecting no vespene, and given nothing
    lately. Besides building, this bot's SCVs only mine and collect vespene."""
    collectors = api.extensions.speed_mining.collectors
    return api.units.own.workers.filter(
        lambda scv: not scv.is_constructing and scv not in collectors and _unit_is_free(scv)
    )


def _site_for_structure(unit_type: UnitTypeId) -> Point | Unit[Any] | None:
    """Where to build a `unit_type`, or `None` if nowhere: a refinery on a free geyser, a command center at the next
    expansion to take, a barracks between the main and its natural with room for its add-on, and any other structure on
    the far side of the main's townhall from its minerals, out of the miners' way, and within 25 of there."""
    if unit_type is UnitTypeId.REFINERY:
        return _free_geyser()
    if unit_type is UnitTypeId.COMMAND_CENTER:
        expansions = _expansions_to_take()
        return expansions[0].center if expansions else None
    main = api.player.start_location
    if unit_type is UnitTypeId.BARRACKS:
        anchor = main.center.towards(main.natural.center, 9)
        # Free tiles on its left, right, bottom and top: its add-on's two on the right, and one more all around, so that
        # the units it trains can walk out.
        return api.placement.nearest_free(unit_type, near=anchor, max_distance=math.inf, margin=(1, 3, 1, 1))
    anchor = main.center.towards(main.mineral_fields.center, -8)
    margin = 0 if unit_type is UnitTypeId.SUPPLY_DEPOT else 1
    return api.placement.nearest_free(unit_type, near=anchor, max_distance=25, margin=margin)


def _free_geyser() -> Unit[Any] | None:
    """A geyser with vespene left at a base with a finished townhall of this player's, with no gas building on it and
    none ordered."""
    taken = {building.position for building in api.units.gas_buildings} | _geysers_ordered_built_on()
    for expansion in api.expansions:
        townhall = expansion.townhall
        if townhall is None or not townhall.is_own or not townhall.is_complete:
            continue
        for geyser in expansion.geysers:
            if geyser.position not in taken and geyser.vespene_contents > 0:
                return geyser
    return None


def _geysers_ordered_built_on() -> set[Point]:
    """Where SCVs were ordered to build a refinery that does not stand yet: this turn, lately, or as their orders
    show."""
    return {
        order.target.position
        for scv in api.units.own.workers
        for order in (*scv.orders, *api.orders.issued_to(scv, include_sent=True))
        if order.ability is AbilityId.SCV_BUILD_REFINERY and isinstance(order.target, Unit)
    }


@api.on(OwnConstructionFinishedEvent.only(UnitType.SupplyDepot))
def lower_depot(event: OwnConstructionFinishedEvent) -> None:
    """Lower a finished depot, so it blocks nothing."""
    event.unit.issue(AbilityId.SUPPLY_DEPOT_LOWER)


# --- Workers


# At a low priority, so it runs after `follow_plan`, whose builders `_free_scvs` then leaves out.
@api.on(TurnEvent, min_interval=GameTime(steps=8), priority=EventPriority.LOW)
def put_scvs_to_work(_: TurnEvent) -> None:
    """Keep as many SCVs collecting vespene as the plan has now, as many as the refineries take, and hand every other
    free SCV to speed mining to mine."""
    speed_mining = api.extensions.speed_mining
    wanted = _collectors_wanted()
    surplus = len(speed_mining.collectors) - wanted
    if surplus > 0:
        for scv in speed_mining.release_collectors(surplus):
            speed_mining.mine(scv)
    for scv in _free_scvs().filter(lambda scv: scv not in speed_mining.workers):
        if len(speed_mining.collectors) >= wanted or speed_mining.collect(scv) is None:
            speed_mining.mine(scv)
    shortfall = wanted - len(speed_mining.collectors)
    if shortfall > 0:
        for scv in speed_mining.release_miners(shortfall):
            if speed_mining.collect(scv) is None:
                speed_mining.mine(scv)


def _collectors_wanted() -> int:
    """How many SCVs the plan has collecting vespene now, at most three to each finished refinery with vespene left."""
    plan = api.extensions.planner.plan
    if plan is None:
        return 0
    interval = plan.interval_at(api.time) or plan.intervals[-1]
    refineries = api.units.own.of_type(UnitType.Refinery).complete.filter(
        lambda refinery: refinery.vespene_contents > 0
    )
    return min(round(interval.collectors), 3 * len(refineries))


@api.on(TurnEvent, min_interval=GameTime(steps=16))
def call_down_mules(_: TurnEvent) -> None:
    """Call down a MULE from each orbital command with the energy for one, on the mineral field with the most left near
    it, as the plan's economy expects."""
    for orbital_command in api.units.own.of_type(UnitType.OrbitalCommand).complete:
        if orbital_command.energy < 50:
            continue
        fields = api.units.mineral_fields.within(10, of=orbital_command)
        if fields:
            richest = max(fields, key=lambda field: field.mineral_contents)
            orbital_command.issue(AbilityId.ORBITAL_COMMAND_CALLDOWN_MULE, target=richest)


# --- The army


def _army_units() -> Units[OwnUnit[Any]]:
    """The marines and marauders."""
    return api.units.own.of_type(UnitType.Marine, UnitType.Marauder)


def _has_attack_aimed_near(unit: OwnUnit[Any], target: Point) -> bool:
    """Whether an attack among `unit`'s orders, queued ones included, is aimed within `SAME_TARGET_WITHIN` of `target`.
    Such a unit is not ordered again: to clear the site of a structure about to go up, the game puts a move ahead of the
    orders of a unit standing on it, which a new order replaces, and the game gives the build up once the unit has
    stood there about 3.6 seconds (in game)."""
    for order in unit.orders:
        aimed_at = order.known_target
        if isinstance(aimed_at, Unit):
            aimed_at = aimed_at.position
        if (
            order.ability is AbilityId.ATTACK
            and aimed_at is not None
            and aimed_at.distance_to(target) <= SAME_TARGET_WITHIN
        ):
            return True
    return False


class Army:
    """The marines and marauders: they wait at the defense location, defend the bases, attack once the army is big
    enough, and stim in a fight."""

    def __init__(self) -> None:
        self.attacking = False
        api.subscribe(self)

    @api.on(TurnEvent, min_interval=GameTime(steps=4))
    def order_army(self, _: TurnEvent) -> None:
        """Defend against what comes near the bases, attack once the army is big enough, and fall back once too little
        of it is left."""
        army = _army_units()
        supply = army.supply_used
        threats = api.units.enemy.non_structures.within(DEFEND_WITHIN, of=api.units.own.townhalls)
        was_attacking = self.attacking
        if supply >= ATTACK_WITH_SUPPLY:
            self.attacking = True
        elif supply < RETREAT_BELOW_SUPPLY:
            self.attacking = False
        if threats and not self.attacking:
            for unit in army:
                target = threats.closest_to(unit.position).position
                if not _has_attack_aimed_near(unit, target):
                    unit.issue(AbilityId.ATTACK, target=target)
        elif self.attacking:
            target = self._attack_target(army)
            attackers = army.filter(lambda unit: not _has_attack_aimed_near(unit, target))
            if attackers:
                api.orders.issue(attackers, AbilityId.ATTACK, target=target)
        elif was_attacking:
            api.orders.issue(army, AbilityId.MOVE, target=self._defense_location())
        else:
            defense_location = self._defense_location()
            idle = army.idle.beyond(4, of=defense_location)
            if idle:
                api.orders.issue(idle, AbilityId.ATTACK, target=defense_location)

    @api.on(TurnEvent, min_interval=GameTime(steps=4))
    def stim_in_fights(self, _: TurnEvent) -> None:
        """Stim each marine and marauder with an enemy near and most of its health, once Stimpack is done, unless it is
        stimmed already."""
        if UpgradeId.STIMPACK not in api.upgrades:
            return
        enemies = api.units.enemy.non_structures
        stimming = _army_units().filter(
            lambda unit: (
                unit.health_fraction >= STIM_ABOVE_HEALTH
                and not unit.has_buff((BuffId.MARINE_STIMMED, BuffId.MARAUDER_STIMMED))
                and bool(enemies.within(STIM_WITHIN, of=unit))
            )
        )
        if stimming:
            api.orders.issue(stimming, AbilityId.STIM)

    def _defense_location(self) -> Point:
        """Where the army waits: toward the map's center from this player's townhall furthest from its start."""
        taken = [expansion for expansion in api.expansions if isinstance(expansion.townhall, OwnUnit)]
        base = taken[-1].center if taken else api.map.start_location
        return base.towards(api.map.playable_area.center, 7)

    def _attack_target(self, army: Units[OwnUnit[Any]]) -> Point:
        """The enemy structure nearest the army, or, with none known, the place looked at longest ago."""
        structures = api.units.enemy.structures
        if structures:
            return structures.closest_to(army.center).position
        last_in_sight = api.vision.last_in_sight
        return min(_possible_enemy_base_locations(), key=lambda place: last_in_sight[place])


def _possible_enemy_base_locations() -> list[Point]:
    """Where an enemy base may stand: the opponents' start locations and every expansion this player's units can walk
    to."""
    reachable = [expansion for expansion in api.expansions if not math.isinf(expansion.pathing_distance_from_start)]
    return [*api.map.opponent_start_locations, *(expansion.center for expansion in reachable)]


plan_follower = PlanFollower()
army = Army()

if __name__ == "__main__":
    run_from_command_line(api, race=Race.TERRAN, name="NachOS planner demo")
