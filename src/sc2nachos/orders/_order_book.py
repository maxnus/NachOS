"""The orders a bot gives in a turn, sent after its handlers have run."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Any, final, overload

from s2clientprotocol import raw_pb2

from sc2nachos.gamedata import Cost, OrderBehavior
from sc2nachos.gamedata._techtree import ABILITIES_SENT_AS_ANOTHER, MULTI_UNIT_ABILITIES
from sc2nachos.geometry import Point
from sc2nachos.ids import AbilityId, UnitTypeId
from sc2nachos.orders._commands import create_unit_command_actions
from sc2nachos.orders._order import Order
from sc2nachos.orders._targets import aimed_at, check_target, same_point, same_target
from sc2nachos.protocol import ProtocolError
from sc2nachos.state import ActionFailure, ActionResult
from sc2nachos.units import OwnUnit, Unit
from sc2nachos.units._units import _unit_tuple

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence

    from s2clientprotocol import sc2api_pb2

    from sc2nachos.gamedata import AbilityData, GameData
    from sc2nachos.gamedata._sent_as import SentAs
    from sc2nachos.geometry import PointLike
    from sc2nachos.protocol import Client
    from sc2nachos.units import Target

# The observations after the one a turn read by which its orders show in a unit's orders: the next in a stepped game,
# and the one after that on the ladder or in realtime (docs/reference/game-behavior/protocol.md).
_SHOWN_WITHIN = 2
# The ability each game ability is, aimed at the unit given it: an unload at a point aimed at the transport itself is
# `UNLOAD`.
_IS_AIMED_AT_ITSELF = MappingProxyType(
    {
        sending.ability: ability
        for ability, sent_as in ABILITIES_SENT_AS_ANOTHER.items()
        for sending in sent_as.values()
        if sending.aimed_at_itself
    }
)
# What an ability goes out as where no type is sent another.
_SENT_UNCHANGED: Mapping[UnitTypeId, SentAs] = MappingProxyType({})


@final
class OrderBook:
    """This player's orders for the turn.

    A bot gives orders through `api.orders` while its handlers run. They are sent in one request after the turn's
    last handler returns. A unit carries out only the last order it is given in a turn, plus any abilities it
    carries out at once.
    """

    __slots__ = (
        "_forced",
        "_game_data",
        "_issued",
        "_issued_by_unit",
        "_last_sent",
        "_observations",
        "_sent_not_replacing",
        "_refusals",
        "_step",
    )

    def __init__(self, game_data: GameData) -> None:
        """A book for one game. `game_data` says what each ability does and what it must be aimed at."""
        self._game_data = game_data
        # The turn's orders in the order they were issued, and those of each unit by its id.
        self._issued: list[Order[Any]] = []
        self._issued_by_unit: dict[int, list[Order[Any]]] = {}
        # The turn's orders issued with `force`, sent even to units already carrying them out.
        self._forced: set[Order[Any]] = set()
        # By unit id, the last unqueued order the game took that replaces the unit's orders, and the number of the
        # observation its turn read.
        self._last_sent: dict[int, tuple[Order[Any], int]] = {}
        # By unit id, each order the game took that does not replace the unit's orders, one queued, a train, a
        # research, an add-on or a morph, with the number of the observation its turn read, until the observations must
        # show it or an order replaces the unit's orders.
        self._sent_not_replacing: dict[int, list[tuple[Order[Any], int]]] = {}
        # What the game refused of the last turn's orders, one entry per unit a refused command named.
        self._refusals: tuple[ActionFailure, ...] = ()
        self._observations = 0
        self._step = 0

    @overload
    def issue(
        self,
        units: OwnUnit[Any] | Iterable[OwnUnit[Any]],
        ability: AbilityId,
        *,
        target: PointLike | Unit[Any] | None = None,
        queued: bool = False,
        force: bool = False,
    ) -> Order[None]: ...

    @overload
    def issue[T](
        self,
        units: OwnUnit[Any] | Iterable[OwnUnit[Any]],
        ability: AbilityId,
        *,
        target: PointLike | Unit[Any] | None = None,
        queued: bool = False,
        force: bool = False,
        data: T,
    ) -> Order[T]: ...

    def issue(
        self,
        units: OwnUnit[Any] | Iterable[OwnUnit[Any]],
        ability: AbilityId,
        *,
        target: PointLike | Unit[Any] | None = None,
        queued: bool = False,
        force: bool = False,
        data: Any = None,
    ) -> Order[Any]:
        """Order `units` to use `ability`, aimed at a point, a unit or nothing, and return the `Order`.

        Every unit given carries it out, and a unit given twice counts once. An ability every unit of one command
        carries out (`AbilityData.multi_unit`) goes out as one command naming them all, so units ordered together keep
        their spacing where the game spreads them; any other goes out as a command to each. `queued` puts the order
        behind each unit's current orders. `data` is the bot's own; NachOS carries it and never reads it. Nothing is
        sent until the turn's handlers have all run.

        An unqueued order that replaces a unit's orders is not sent to a unit already doing it: sending it would only
        drop what the unit has queued behind it. What a unit is doing is the last such order sent to it, while the
        observation after its turn may not show it yet, and otherwise its first reported order. `force` sends it to
        those units too: that is how a unit is made to drop its queue and go on with its current order. It still
        competes with the turn's other orders like any other.

        An ability that goes out as another for some types (`AbilityData.sent_as`) goes out as that one to their units,
        in commands of its own. A command the game refuses is listed in `api.action_failures` once for each unit it
        names.

        Raises `TypeError` for a target the ability cannot be aimed at, for an add-on with no point given to a flying
        structure, and for a transport's unload at a point aimed at one of `units` itself, which is `UNLOAD` for that
        one: a group given an unload at one of its own transports is two orders. Raises `ValueError` for a research
        given to several units, and for an ability that places something at its target (`AbilityData.places_at_target`)
        given to several aimed at one; and for a custom ability given to a type it has nothing to be sent as for, or
        which this game's tables lack an ability it is sent as for.
        """
        given = _unit_tuple(units)
        if len(given) > 1:
            given = tuple(dict.fromkeys(given))
        if not given:
            raise ValueError("an order needs a unit to give it to")
        row = self._game_data.abilities.get(ability)
        sent_as = ABILITIES_SENT_AS_ANOTHER.get(ability, _SENT_UNCHANGED)
        if ability.is_custom:
            if row is None:
                raise ValueError(f"{ability.name} has nothing in this game's tables to be sent as")
            _check_sent_as(ability, sent_as, given)
        aimed = aimed_at(target)
        check_target(ability, aimed, row)
        _check_each_can_carry_it_out(ability, row, aimed, len(given))
        if aimed is None:
            _check_placed_where_flying(ability, row, given)
        if isinstance(aimed, Unit):
            _check_not_aimed_at_itself(ability, aimed, given)
        order = Order(
            ability,
            given,
            aimed,
            queued=queued,
            data=data,
            step=self._step,
        )
        self._add(order)
        if force:
            self._forced.add(order)
        return order

    def issued_to(self, unit: OwnUnit[Any], *, include_sent: bool = False) -> tuple[Order[Any], ...]:
        """The orders issued to `unit` so far this turn, in the order they were issued. Withdrawn orders are left out.
        With `include_sent`, first come the orders that went out to it lately and that the observations may not show
        yet: the last one sent that replaced its orders, and those sent since that did not, queued ones, trains,
        researches, add-ons and morphs, but not what it carries out at once.

        A handler late in a turn reads this to leave alone a unit an earlier handler has ordered, since only the last
        order a unit is given goes out.
        """
        turns = self._issued_by_unit.get(unit.id, ())
        sent = self._sent_but_not_observed_yet(unit) if include_sent else ()
        return (*sent, *(order for order in turns if not order._withdrawn))

    @property
    def pending(self) -> tuple[Order[Any], ...]:
        """Every order issued this turn and still to be sent. Withdrawn orders are left out."""
        return tuple(order for order in self._issued if not order._withdrawn)

    @property
    def pending_cost(self) -> Cost:
        """What the bank does not show yet: the cost of the orders of `pending` that will go out, each its ability's
        `cost` in the game's tables for each unit it goes out to. A unit given orders that replace one another pays for
        the last, and one already carrying an order out pays nothing for it again. Its steps are every order's added up,
        though the units may carry them out side by side."""
        total = Cost(0, 0)
        for order, units in self._orders_to_send(self._issued, self._forced):
            row = self._game_data.abilities.get(order.ability)
            if row is not None:
                total += row.cost * len(units)
        return total

    def _send(self, client: Client, *, then: Sequence[sc2api_pb2.Action] = ()) -> None:
        """Send the turn's orders and after them `then`, actions that belong to no order, in one request, and keep what
        the game refused of the orders. Sends nothing if there is nothing to send."""
        issued, forced = self._issued, self._forced
        self._issued, self._issued_by_unit, self._forced = [], {}, set()
        self._refusals = ()
        commands: list[tuple[Order[Any], sc2api_pb2.Action, tuple[OwnUnit[Any], ...]]] = []
        for order, units in self._orders_to_send(issued, forced):
            sent_as = ABILITIES_SENT_AS_ANOTHER.get(order.ability, _SENT_UNCHANGED)
            groups = (units,) if order.ability in MULTI_UNIT_ABILITIES else tuple((unit,) for unit in units)
            for group in groups:
                actions = create_unit_command_actions(order.ability, group, order.target, sent_as, queued=order.queued)
                commands += [(order, *command) for command in actions]
        actions = [*(action for _, action, _ in commands), *then]
        if not actions:
            return
        results = client.act(actions).result
        if len(results) < len(commands):
            raise ProtocolError(f"the game answered {len(results)} of the {len(commands)} commands it was sent")
        # What comes after the commands is answered after them, and belongs to no order.
        refusals: list[ActionFailure] = []
        for (order, _, units), result in zip(commands, results, strict=False):
            action_result = ActionResult.read(result)
            if action_result is not ActionResult.SUCCESS:
                refusals += [ActionFailure(self._step, unit, order.ability, action_result) for unit in units]
            else:
                self._remember_sent(order, units)
        self._refusals = tuple(refusals)

    def _observe(self, step: int, lost: Iterable[Unit[Any]]) -> None:
        """Take in the observation at `step`, which found `lost` dead or no longer this player's."""
        self._step = step
        self._observations += 1
        for unit in lost:
            self._last_sent.pop(unit.id, None)
            self._sent_not_replacing.pop(unit.id, None)
        recent = self._observations - _SHOWN_WITHIN + 1
        for unit_id, sent in list(self._sent_not_replacing.items()):
            if not (sent := [(order, sent_at) for order, sent_at in sent if sent_at >= recent]):
                del self._sent_not_replacing[unit_id]
            else:
                self._sent_not_replacing[unit_id] = sent

    def _orders_to_send(
        self, issued: Sequence[Order[Any]], forced: Collection[Order[Any]]
    ) -> Iterator[tuple[Order[Any], tuple[OwnUnit[Any], ...]]]:
        """Each order of `issued` that goes out, with the units it goes out to.

        An order that acts at once, or that is queued behind a unit's current orders, competes with nothing. Of the
        rest, a unit keeps only the last it was given. An order is then left out for the units already carrying it
        out, unless it is in `forced`. A withdrawn order, or one left with no unit, is not sent.
        """
        holder: dict[int, Order[Any]] = {}
        for order in issued:
            if not order._withdrawn:
                for unit in order.units:
                    if self._competes_for(order, unit):
                        holder[unit.id] = order
        for order in issued:
            if order._withdrawn:
                continue
            units = tuple(
                unit for unit in order.units if holder.get(unit.id) is order or not self._competes_for(order, unit)
            )
            if order not in forced:
                units = self._units_not_carrying_it_out(order, units)
            if units:
                yield order, units

    def _competes_for(self, order: Order[Any], unit: OwnUnit[Any]) -> bool:
        """Whether `order` competes with the turn's other orders for `unit`, which then takes the last of them.

        Every unqueued order does, a structure's train included, whatever it would do to the unit. A queued order
        competes with nothing, and nor does an ability the unit's type carries out at once: the unit does both (in
        game). Each unit of a group is judged by its own type.
        """
        if order.queued:
            return False
        row = self._game_data.abilities.get(order.ability)
        return _behavior_for(row, unit) is not OrderBehavior.KEEPS_ORDERS

    def _add(self, order: Order[Any]) -> None:
        """Count `order` among the turn's, last."""
        self._issued.append(order)
        for unit_id in {unit.id for unit in order.units}:
            self._issued_by_unit.setdefault(unit_id, []).append(order)

    def _remember_sent(self, order: Order[Any], units: Sequence[OwnUnit[Any]]) -> None:
        """Record `order`, which the game took, as sent to each of `units`: the last sent to a unit whose orders it
        replaces, unqueued, and otherwise one sent that does not replace them, unless the unit carries it out at
        once."""
        row = self._game_data.abilities.get(order.ability)
        for unit in units:
            behavior = _behavior_for(row, unit)
            if not order.queued and behavior is OrderBehavior.REPLACES:
                self._last_sent[unit.id] = (order, self._observations)
                self._sent_not_replacing.pop(unit.id, None)
            elif order.queued or behavior is not OrderBehavior.KEEPS_ORDERS:
                self._sent_not_replacing.setdefault(unit.id, []).append((order, self._observations))

    def _units_not_carrying_it_out(
        self, order: Order[Any], units: tuple[OwnUnit[Any], ...]
    ) -> tuple[OwnUnit[Any], ...]:
        """The units of `units` that `order` would change anything for: all of them for a queued order, else all but
        those whose orders it replaces and which are already doing it."""
        if order.queued:
            return units
        row = self._game_data.abilities.get(order.ability)
        return tuple(
            unit
            for unit in units
            if _behavior_for(row, unit) is not OrderBehavior.REPLACES
            or not self._is_doing(unit, order.ability, order.target)
        )

    def _is_doing(self, unit: OwnUnit[Any], ability: AbilityId, target: Target | None) -> bool:
        """Whether `unit` is doing `ability` at `target`: the last order sent to it says, while it may not show yet,
        and otherwise its first reported order."""
        sent = self._sent_and_not_shown(unit)
        if sent is not None:
            return sent.ability is ability and same_target(sent.target, target)
        return self._unit_is_at(unit, ability, target)

    def _sent_and_not_shown(self, unit: OwnUnit[Any]) -> Order[Any] | None:
        """The last order sent to `unit` that replaced its orders, while the observations may not show it yet."""
        last = self._last_sent.get(unit.id)
        if last is not None and self._observations - last[1] < _SHOWN_WITHIN:
            return last[0]
        return None

    def _sent_but_not_observed_yet(self, unit: OwnUnit[Any]) -> list[Order[Any]]:
        """The orders that went out to `unit` lately and that the observations may not show yet: the last sent that
        replaced its orders, and those sent since that did not."""
        orders = []
        if (sent := self._sent_and_not_shown(unit)) is not None:
            orders.append(sent)
        recent = self._observations - _SHOWN_WITHIN + 1
        orders += [order for order, sent_at in self._sent_not_replacing.get(unit.id, ()) if sent_at >= recent]
        return orders

    def _unit_is_at(self, unit: OwnUnit[Any], ability: AbilityId, target: Target | None) -> bool:
        """Whether `unit`'s first order runs `ability` at `target`. A unit the last observation left out, in a
        transport for one, is at nothing."""
        orders = unit._latest_report.orders
        if unit.is_stale or not orders:
            return False
        first = orders[0]
        if _ability_of_unit_order(first) is not ability:
            return False
        match first.WhichOneof("target"):
            case "target_world_space_pos":
                point = first.target_world_space_pos
                return isinstance(target, Point) and same_point(target, point.x, point.y)
            case "target_unit_tag":
                return isinstance(target, Unit) and unit._unit_target_of_order(first) is target
            case _:
                return target is None


def _behavior_for(row: AbilityData | None, unit: OwnUnit[Any]) -> OrderBehavior:
    """What an ability does to the current orders of `unit`, a unit of its type. An ability with no row replaces
    them."""
    return OrderBehavior.REPLACES if row is None else row.order_behavior_for(unit.type_id)


def _ability_of_unit_order(order: raw_pb2.UnitOrder) -> AbilityId:
    """The ability a raw order runs, or `NULL` if the curated ids leave it out. No order of ours runs such an
    ability."""
    return AbilityId.get(order.ability_id) or AbilityId.NULL


def _check_sent_as(ability: AbilityId, sent_as: Mapping[UnitTypeId, SentAs], units: Sequence[OwnUnit[Any]]) -> None:
    """Raise `ValueError` if the custom `ability` has nothing to be sent as for one of `units`."""
    if missing := sorted({unit.type_id.name for unit in units if unit.type_id not in sent_as}):
        raise ValueError(f"{ability.name} has nothing to be sent as for {', '.join(missing)}")


def _check_each_can_carry_it_out(
    ability: AbilityId, row: AbilityData | None, target: Target | None, count: int
) -> None:
    """Raise `ValueError` if `ability`, given to `count` units, is a research, or places something at `target`."""
    if count < 2 or row is None:
        return
    if row.is_research:
        raise ValueError(f"give {ability.name} to one unit, not {count}: only one of them can research it")
    if row.places_at_target and target is not None:
        raise ValueError(f"give {ability.name} to one unit, not {count}: only one of them can carry it out there")


def _check_placed_where_flying(ability: AbilityId, row: AbilityData | None, units: Sequence[OwnUnit[Any]]) -> None:
    """Raise `TypeError` if `ability`, given no target, places something and one of `units` is flying."""
    if row is None or not row.needs_placement:
        return
    if flying := sorted({unit.type_id.name for unit in units if unit.is_flying}):
        raise TypeError(f"{ability.name} takes a point for a flying {', '.join(flying)}, and was given no target")


def _check_not_aimed_at_itself(ability: AbilityId, target: Unit[Any], units: Sequence[OwnUnit[Any]]) -> None:
    """Raise `TypeError` if `ability` is aimed at one of `units` and another id is that."""
    custom = _IS_AIMED_AT_ITSELF.get(ability)
    if custom is not None and any(unit.id == target.id for unit in units):
        raise TypeError(f"{ability.name} aimed at the unit itself is {custom.name}")
