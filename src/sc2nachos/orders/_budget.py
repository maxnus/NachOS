"""What a turn can still pay for, after everything it has already ordered."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, final

from sc2nachos.gamedata import OrderBehavior, Resources
from sc2nachos.ids import UnitTypeId
from sc2nachos.orders._order_state import OrderState
from sc2nachos.state import ActionResult

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sc2nachos.gamedata import AbilityData
    from sc2nachos.ids import AbilityId
    from sc2nachos.orders._order import Order
    from sc2nachos.orders._order_book import OrderBook
    from sc2nachos.units import OwnUnit

# What a structure takes before the game answers `QUEUE_IS_FULL`, on a barracks, a factory and a starport alike: 8
# with a reactor and 5 with a tech lab or none (in game, docs/game-behavior.md).
_QUEUE_DEPTH = 5
_QUEUE_DEPTH_WITH_REACTOR = 8

# A larva takes one order and becomes an egg carrying it, and a warp gate warps in on a charge: neither keeps a
# queue, and neither was ever answered `QUEUE_IS_FULL` (in game, docs/game-behavior.md).
_KEEPS_NO_QUEUE = frozenset({UnitTypeId.LARVA, UnitTypeId.EGG, UnitTypeId.WARP_GATE})

_REACTORS = frozenset(
    {
        UnitTypeId.REACTOR,
        UnitTypeId.REACTOR_BARRACKS,
        UnitTypeId.REACTOR_FACTORY,
        UnitTypeId.REACTOR_STARPORT,
    }
)

# What a structure is charged for and given a slot for.
_PRODUCTION = frozenset({OrderBehavior.QUEUES, OrderBehavior.NEEDS_IDLE})


@final
@dataclass(frozen=True, slots=True)
class _Tally:
    """What the orders a turn still holds have taken: minerals and vespene, supply, and slots by unit id."""

    spent: Resources
    supply: float
    items: Mapping[int, int]


@final
class Budget:
    """What this player can still pay for this turn, after everything the turn has already ordered.

    The game charges a train, a research, a morph and a build as each is ordered, so a turn that orders more than
    it has is a turn that burns it. Every read here counts the orders the turn still holds: one withdrawn or
    overridden stops counting at once, and a cancel frees neither a slot nor a mineral until the game has stepped
    (in game).
    """

    __slots__ = ("_book", "_standing", "_standing_step")

    def __init__(self, book: OrderBook) -> None:
        """A budget over what `book` has been given this turn and what the game last reported."""
        self._book = book
        self._standing: frozenset[UnitTypeId] = frozenset()
        self._standing_step = -1

    @property
    def resources(self) -> Resources:
        """The minerals and vespene left after what the turn has ordered."""
        return self._book._state.resources - self._tally().spent

    @property
    def supply_left(self) -> float:
        """What is left under the cap after every unit the turn has ordered, whether it will start at once or wait
        in a queue: the minerals go as it is ordered, so a queue the cap cannot feed is money hung."""
        return self._book._state.supply.left - self._tally().supply

    def slots_left(self, structure: OwnUnit[Any]) -> int:
        """How many more things `structure` will take before the game answers `QUEUE_IS_FULL`, counting what it is
        making and what the turn has ordered it.

        A structure holds 5, or 8 with a finished reactor. A larva and a warp gate keep no queue at all and were
        never answered `QUEUE_IS_FULL`, so they answer one while they are making nothing, and an order to one is
        never refused for want of a slot (in game).
        """
        return self._slots_left(structure, self._tally())

    def covers(self, ability: AbilityId, performer: OwnUnit[Any]) -> bool:
        """Whether the turn can still pay for `performer` running `ability`: its minerals, its vespene, the supply
        what it makes will take, and a slot to put it in."""
        return self.refusal(ability, performer) is None

    def refusal(self, ability: AbilityId, performer: OwnUnit[Any]) -> ActionResult | None:
        """What the game would answer an order of `ability` by `performer` now, or `None` where nothing stands in
        the way. This is what `api.order.issue` refuses by.

        It answers what this player has too little of, what the tables say `performer` cannot run, and
        `NOT_SUPPORTED` for what a structure must be idle to take. The game answers one refusal and nobody has
        measured which it picks when two hold, so the order among them is NachOS's own.
        """
        return self._refusal_for(ability, (performer,))

    def _refusal_for(self, ability: AbilityId, performers: Sequence[OwnUnit[Any]]) -> ActionResult | None:
        """What an order of `ability` to all of `performers` would be answered. One command is one thing to the
        game, so an order is judged whole: every unit it names is charged for before anything is looked at."""
        row = self._book._game_data.abilities.get(ability)
        if row is None:
            # An ability NachOS holds no row for is the game's to judge, as it is today.
            return None
        for performer in performers:
            if not self._offered(ability, performer, row):
                return ActionResult.NOT_SUPPORTED
        if not row.cost.total and not row.supply_cost and row.behavior not in _PRODUCTION:
            # A move, an attack and every other ability that charges nothing and takes no slot: nothing the turn
            # has ordered can stand in the way of it, so the turn is never walked for one. Most orders are these.
            return None
        tally = self._tally(adding=(ability, performers))
        if row.behavior in _PRODUCTION and any(self._slots_left(one, tally) < 0 for one in performers):
            return ActionResult.QUEUE_IS_FULL
        left = self._book._state.resources - tally.spent
        if left.minerals < 0:
            return ActionResult.NOT_ENOUGH_MINERALS
        if left.vespene < 0:
            return ActionResult.NOT_ENOUGH_VESPENE
        if self._book._state.supply.left - tally.supply < 0:
            # The game takes a queued order the cap cannot feed and hangs it at no progress for as long as it is
            # left there, charging its minerals and reporting nothing (in game). NachOS refuses it instead, so a
            # bot is never left holding an order that will never start; one meant to wait says `checked=False`.
            return ActionResult.NOT_ENOUGH_FOOD
        return None

    def _tally(self, adding: tuple[AbilityId, Sequence[OwnUnit[Any]]] | None = None) -> _Tally:
        """What the orders the turn still holds have taken, with `adding` counted as one more of them.

        It is summed afresh on every read rather than kept as a running total, so an order withdrawn or overridden
        stops counting by itself. That costs a walk of the turn's own orders, which is a short list; a cache would
        have to be invalidated from `Order.withdraw`, which would tie every handle a bot holds back to the book.
        """
        book = self._book
        given = [order for order in book._given_orders if order.state is OrderState.GIVEN]
        holder = book._holder(given)
        charges = [(order.ability, self._units_charged(order, holder)) for order in given]
        if adding is not None:
            charges.append(adding)
        spent, supply = Resources(0, 0), 0.0
        items: defaultdict[int, int] = defaultdict(int)
        for ability, units in charges:
            row = book._game_data.abilities.get(ability)
            if row is None or not units:
                continue
            # A spell, a structure and a morph are carried out by one of the units the order names and charged
            # once (in game). What one command naming several structures trains has not been measured -- the
            # `one-command-many-makers` sweep asks -- and until it has, each of them is charged for.
            count = 1 if row.needs_placement else len(units)
            spent += row.cost * count
            supply += row.supply_cost * count
            if row.behavior in _PRODUCTION:
                for unit in units:
                    items[unit.id] += 1
        return _Tally(spent, supply, items)

    def _units_charged(self, order: Order[Any], holder: Mapping[int, Order[Any]]) -> Sequence[OwnUnit[Any]]:
        """The units `order` would go out for, which is none of those a later order of the turn has taken."""
        if not self._book._competes(order):
            return order.units
        return tuple(unit for unit in order.units if holder.get(unit.id) is order)

    def _slots_left(self, structure: OwnUnit[Any], tally: _Tally) -> int:
        """What `structure` will still take, counting `tally` as already ordered."""
        ordered = tally.items.get(structure.id, 0) + len(structure.orders)
        if structure.type_id in _KEEPS_NO_QUEUE:
            return max(1 - ordered, 0)
        return (_QUEUE_DEPTH_WITH_REACTOR if _has_reactor(structure) else _QUEUE_DEPTH) - ordered

    def _offered(self, ability: AbilityId, performer: OwnUnit[Any], row: AbilityData) -> bool:
        """Whether the game would offer `performer` the ability now: it is one its type performs, what it needs
        stands and is researched, and it is idle where it has to be."""
        if row.performers and performer.type_id not in row.performers:
            return False
        if row.behavior is OrderBehavior.NEEDS_IDLE and performer.orders:
            # A morph, an add-on or a lift on a structure making something is answered `NOT_SUPPORTED` (in game).
            return False
        row_of_performer = self._book._game_data.units.get(performer.type_id)
        needs = row_of_performer.ability_requirements.get(ability) if row_of_performer is not None else None
        if needs is None:
            # A general id, or an ability no sweep saw a requirement for: the game is left to judge it.
            return True
        return needs.structures <= self._standing_types() and needs.upgrades <= self._book._tracker.upgrades.own

    def _standing_types(self) -> frozenset[UnitTypeId]:
        """Every unit type this player has standing and finished, each counting as its tech aliases too: an orbital
        command stands in for the command center a barracks needs. Read once an observation."""
        book = self._book
        if self._standing_step == book._step:
            return self._standing
        data = book._game_data
        standing: set[UnitTypeId] = set()
        for unit in book._tracker.units.present.own:
            if not unit.is_complete:
                continue
            standing.add(unit.type_id)
            if (row := data.units.get(unit.type_id)) is not None:
                standing.update(row.tech_aliases)
        self._standing = frozenset(standing)
        self._standing_step = book._step
        return self._standing


def _has_reactor(structure: OwnUnit[Any]) -> bool:
    """Whether a finished reactor is attached to `structure`, which is what makes its queue 8 deep. One still going
    up leaves the structure showing the order that builds it, which counts as a thing it is making (in game)."""
    add_on = structure.add_on
    return add_on is not None and add_on.type_id in _REACTORS and add_on.is_complete
