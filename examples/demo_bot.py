"""A Terran bot written with NachOS alone: it trains SCVs, builds supply depots, barracks and command centers, and
attacks with marines.

    uv run python examples/demo_bot.py --opponent-race ZERG --opponent-difficulty MEDIUM --map PylonAIE_v4

It takes the options of `run_from_command_line`, so the match workflow can play it as it plays `run_game.py`.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from sc2nachos import Api, ApiBot, Race, run_from_command_line
from sc2nachos.events import EventPriority, OwnConstructionFinishedEvent, TurnEvent
from sc2nachos.gamedata import Resources
from sc2nachos.geometry import Circle, Footprint, Point
from sc2nachos.ids import AbilityId, UnitTypeId
from sc2nachos.units import Units, UnitType

if TYPE_CHECKING:
    from sc2nachos.gamemap import Expansion
    from sc2nachos.units import OwnUnit, Unit

api = Api()
on = api.events.on

MAX_TOWNHALLS = 4
MAX_WORKERS = 66
BARRACKS_PER_TOWNHALL = 3
MAX_BARRACKS = 10
ATTACK_WITH = 24
RETREAT_BELOW = 8
DEFEND_WITHIN = 15.0


# --- Spending


class Budget:
    """What is left to spend this turn: the bank, less what the builds NachOS holds will cost once their SCVs reach
    their sites, less what this turn's orders cost so far."""

    def __init__(self) -> None:
        self.left = api.resources - _unpaid()
        self.supply_left = api.supply.left

    def take(self, ability: AbilityId, *, save: bool = False) -> bool:
        """Whether `ability` can be paid for now, taking its cost if so. With `save`, its cost is set aside even if
        not, so nothing cheaper spends it first."""
        cost = api.data.abilities[ability].cost
        affordable = self.left.covers(cost.resources) and self.supply_left >= cost.supply
        if affordable or save:
            self.left -= cost.resources
            self.supply_left -= cost.supply
        return affordable


def _unpaid() -> Resources:
    """What the orders NachOS holds and the turn's orders so far will cost, which the bank does not show yet."""
    orders = (*api.orders.held, *api.orders.pending)
    return sum((api.data.abilities[order.ability].cost.resources for order in orders), Resources(0, 0))


@on(TurnEvent)
def macro(event: TurnEvent) -> None:
    """Spend the turn's money, most important first."""
    budget = Budget()
    train_workers(budget)
    build_supply(budget)
    expand(budget)
    build_barracks(budget)
    train_marines(budget)


# --- Workers


def townhalls() -> Units[OwnUnit[UnitType.Townhall]]:
    """This player's command centers, finished or not."""
    return api.units.own.of_type(UnitType.Townhall)


def train_workers(budget: Budget) -> None:
    """Train an SCV at each idle townhall, up to what the bases can use and a few to build with."""
    ideal = sum(townhall.ideal_harvesters for townhall in townhalls().complete)
    wanted = min(ideal + 6, MAX_WORKERS)
    count = len(api.units.own.of_type(UnitType.Scv)) + len(api.in_production(UnitTypeId.SCV))
    for townhall in townhalls().complete.idle:
        if count >= wanted:
            return
        if api.orders.issued_to(townhall, include_sent=True):
            continue
        if budget.take(AbilityId.COMMAND_CENTER_TRAIN_SCV):
            api.orders.issue(townhall, AbilityId.COMMAND_CENTER_TRAIN_SCV)
            count += 1


# At a low priority, so it runs after `macro`, whose builders `issued_to` then shows.
@on(TurnEvent, every_steps=8, priority=EventPriority.LOW)
def send_idle_workers_to_mine(event: TurnEvent) -> None:
    """Send each idle SCV that has not been ordered this turn to mine at the townhall with the most room."""
    for scv in api.units.own.of_type(UnitType.Scv).idle:
        if api.orders.issued_to(scv, include_sent=True):
            continue
        field = _mineral_field_with_room(scv.position)
        if field is not None:
            api.orders.issue(scv, AbilityId.GATHER, target=field)


@on(TurnEvent, every_steps=64, priority=EventPriority.LOW)
def balance_workers(event: TurnEvent) -> None:
    """Move SCVs from a townhall with more than it needs to one with room."""
    for townhall in townhalls().complete:
        extra = townhall.assigned_harvesters - townhall.ideal_harvesters
        if extra <= 0:
            continue
        miners = _scvs_free_to_build().in_area(Circle(townhall.position, 10))
        for scv in miners.closest_n_to(extra, townhall.position):
            field = _mineral_field_with_room(scv.position)
            if field is not None and field.position.distance_to(townhall.position) > 10:
                api.orders.issue(scv, AbilityId.GATHER, target=field)


def _mineral_field_with_room(near: Point) -> Unit[UnitType.AnyMineralField] | None:
    """The mineral field nearest `near` of the townhall nearest it that needs more SCVs, or of the nearest one at
    all."""
    bases = townhalls().complete
    if not bases:
        return None
    with_room = bases.filter(lambda townhall: townhall.assigned_harvesters < townhall.ideal_harvesters)
    base = (with_room or bases).closest_to(near)
    expansion = min(api.expansions, key=lambda expansion: expansion.center.distance_to(base.position))
    fields = expansion.mineral_fields.filter(lambda field: not field.is_dead)
    return fields.closest_to(near) if fields else None


@on(OwnConstructionFinishedEvent.only(UnitType.Townhall))
def rally_new_workers(event: OwnConstructionFinishedEvent) -> None:
    """Have a finished townhall send the SCVs it trains to its nearest mineral field."""
    field = _mineral_field_with_room(event.unit.position)
    if field is not None:
        api.orders.issue(event.unit, AbilityId.RALLY_WORKERS, target=field)


@on(TurnEvent, once=True)
def rally_first_workers(event: TurnEvent) -> None:
    """Have the first townhall send the SCVs it trains to its nearest mineral field."""
    for townhall in townhalls():
        field = _mineral_field_with_room(townhall.position)
        if field is not None:
            api.orders.issue(townhall, AbilityId.RALLY_WORKERS, target=field)


# --- Building


def _is_free_to_build(scv: OwnUnit[UnitType.Scv]) -> bool:
    """Whether `scv` is mining or idle, and not on its way to build or building."""
    if scv.construction is not None or api.orders.issued_to(scv, include_sent=True):
        return False
    return all(order.ability in (AbilityId.GATHER, AbilityId.RETURN) for order in scv.orders)


def _scvs_free_to_build() -> Units[OwnUnit[UnitType.Scv]]:
    """The SCVs mining or idle."""
    return api.units.own.of_type(UnitType.Scv).filter(_is_free_to_build)


def _build(ability: AbilityId, site: Point) -> bool:
    """Order the SCV nearest `site` that is free to build it there. NachOS holds the build and sends the SCV on its
    way, and the game charges it once the SCV arrives."""
    scvs = _scvs_free_to_build()
    if not scvs:
        return False
    api.orders.issue(scvs.closest_to(site), ability, target=site)
    return True


def _ordered_count(ability: AbilityId) -> int:
    """How many builds of `ability` SCVs were given that `api.in_production` may not count yet: held, ordered this
    turn, or sent and maybe not shown. One shown already may count in both for an observation or two."""
    return sum(
        1
        for scv in api.units.own.of_type(UnitType.Scv)
        for order in api.orders.issued_to(scv, include_sent=True)
        if order.ability is ability
    )


def build_supply(budget: Budget) -> None:
    """Build a depot behind the main base's minerals before supply runs out, saving for it first."""
    supply = api.supply
    if supply.cap >= 200:
        return
    producers = len(townhalls()) + len(api.units.own.of_type(UnitType.Barracks))
    in_progress = len(api.in_production(UnitTypeId.SUPPLY_DEPOT)) + _ordered_count(AbilityId.SCV_BUILD_SUPPLY_DEPOT)
    if supply.left + 8 * in_progress >= 2 + 2 * producers or in_progress >= 3:
        return
    site = api.placement.nearest_free(UnitTypeId.SUPPLY_DEPOT, near=_depot_anchor(), max_distance=25)
    if site is not None and budget.take(AbilityId.SCV_BUILD_SUPPLY_DEPOT, save=True):
        _build(AbilityId.SCV_BUILD_SUPPLY_DEPOT, site)


def _depot_anchor() -> Point:
    """Behind the main base's minerals, away from where the army walks."""
    main = api.expansions[0]
    return main.center.towards(main.mineral_fields.center, -8)


@on(OwnConstructionFinishedEvent.only(UnitType.SupplyDepot))
def lower_depot(event: OwnConstructionFinishedEvent) -> None:
    """Lower a finished depot, so it blocks nothing."""
    api.orders.issue(event.unit, AbilityId.SUPPLY_DEPOT_LOWER)


def expand(budget: Budget) -> None:
    """Build a command center at the next free expansion once the bases have nearly all the SCVs they can use, or the
    bank is full, saving for it first."""
    if len(townhalls()) >= MAX_TOWNHALLS or not townhalls().complete:
        return
    building = len(api.in_production(UnitTypeId.COMMAND_CENTER)) + _ordered_count(AbilityId.SCV_BUILD_COMMAND_CENTER)
    if building:
        return
    ideal = sum(townhall.ideal_harvesters for townhall in townhalls().complete)
    if len(api.units.own.of_type(UnitType.Scv)) < ideal - 4 and api.resources.minerals < 600:
        return
    if not api.units.own.of_type(UnitType.Barracks):
        return
    expansion = _next_expansion()
    if expansion is not None and budget.take(AbilityId.SCV_BUILD_COMMAND_CENTER, save=True):
        _build(AbilityId.SCV_BUILD_COMMAND_CENTER, expansion.center)


def _next_expansion() -> Expansion | None:
    """The expansion nearest this player's start, and reachable from it, where a command center may stand now and no
    enemy is in sight."""
    for expansion in api.expansions:
        if math.isinf(expansion.pathing_distance_from_start):
            break
        if not api.placement.is_free(UnitTypeId.COMMAND_CENTER, at=expansion.center):
            continue
        if api.units.enemy.in_area(Circle(expansion.center, 12)):
            continue
        return expansion
    return None


# A barracks covers 3x3, so this is one with a tile free on every side. Both center on the same point.
_BARRACKS_WITH_ROOM = Footprint.square(5)


def build_barracks(budget: Budget) -> None:
    """Build one barracks before the first expansion, and then more as the bases grow."""
    if not api.units.own.of_type(UnitType.SupplyDepot, UnitType.SupplyDepotLowered).complete:
        return
    count = len(api.units.own.of_type(UnitType.Barracks, UnitType.BarracksFlying))
    count += _ordered_count(AbilityId.SCV_BUILD_BARRACKS)
    wanted = min(BARRACKS_PER_TOWNHALL * len(townhalls()), MAX_BARRACKS)
    if count >= wanted or (count >= 1 and len(townhalls()) < 2):
        return
    site = _barracks_site()
    if site is not None and budget.take(AbilityId.SCV_BUILD_BARRACKS):
        _build(AbilityId.SCV_BUILD_BARRACKS, site)


def _barracks_site() -> Point | None:
    """The spot nearest the main base's front where a barracks may stand with a tile free all around it, so the units
    it trains can always walk out."""
    room = api.placement.free_for(UnitTypeId.BARRACKS).where_footprint_fits(_BARRACKS_WITH_ROOM)
    if not room.any():
        return None
    anchor = api.expansions[0].center.towards(api.expansions[1].center, 9)
    distance = room.distance_from(anchor)
    best = distance.where(room, float("inf")).argmin()
    return _BARRACKS_WITH_ROOM.center_in_tile(best)


def train_marines(budget: Budget) -> None:
    """Train a marine at each idle barracks."""
    for barracks in api.units.own.of_type(UnitType.Barracks).complete.idle:
        if api.orders.issued_to(barracks, include_sent=True):
            continue
        if budget.take(AbilityId.BARRACKS_TRAIN_MARINE):
            api.orders.issue(barracks, AbilityId.BARRACKS_TRAIN_MARINE)


# --- The army


class Army:
    """The marines: they gather at the front, defend the bases, and attack once there are enough of them."""

    def __init__(self) -> None:
        self.attacking = False
        self.last_seen: dict[Point, int] = {}
        api.events.subscribe(self)

    @on(TurnEvent, every_steps=4)
    def command(self, event: TurnEvent) -> None:
        """Defend against what comes near the bases, attack once there are enough marines, and fall back once too few
        are left."""
        marines = api.units.own.of_type(UnitType.Marine)
        self._remember_what_is_seen()
        threats = self._threats()
        if len(marines) >= ATTACK_WITH:
            self.attacking = True
        elif len(marines) < RETREAT_BELOW:
            self.attacking = False
        if threats and not self.attacking:
            for marine in marines:
                api.orders.issue(marine, AbilityId.ATTACK, target=threats.closest_to(marine.position).position)
        elif self.attacking:
            target = self._attack_target(marines)
            api.orders.issue(marines, AbilityId.ATTACK, target=target)
        else:
            front = self._front()
            idle = marines.filter(lambda marine: marine.is_idle and marine.position.distance_to(front) > 4)
            if idle:
                api.orders.issue(idle, AbilityId.ATTACK, target=front)

    def _threats(self) -> Units[Unit[Any]]:
        """Enemy units other than structures near this player's townhalls."""
        near = (api.units.enemy.in_area(Circle(townhall.position, DEFEND_WITHIN)) for townhall in townhalls())
        return Units.combined(*near).filter(lambda enemy: not enemy.is_structure)

    def _front(self) -> Point:
        """Where the army waits: toward the map's center from this player's townhall furthest from its start."""
        taken = [expansion for expansion in api.expansions if townhalls().in_area(Circle(expansion.center, 3))]
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
    run_from_command_line(ApiBot(api, Race.TERRAN, "NachOS demo"))
