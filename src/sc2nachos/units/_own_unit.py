"""One of this player's own units."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sc2nachos.game_time import GameTime
from sc2nachos.ids import AbilityId
from sc2nachos.units._snapshot import _Snapshot
from sc2nachos.units._unit import Unit
from sc2nachos.units._unit_type import UnitType
from sc2nachos.units._values import Passenger, RallyTarget, UnitOrder

if TYPE_CHECKING:
    from collections.abc import Collection

    from s2clientprotocol import raw_pb2


class OwnUnit[K: UnitType.AnyType](Unit[K]):
    """One of this player's units. It also reads what the game reports only to a unit's owner.

    A unit changes between `Unit` and `OwnUnit` in place when it changes sides, as under a neural parasite.
    """

    __slots__ = ()

    @property
    def orders(self) -> tuple[UnitOrder, ...]:
        """The unit's current order, then its queued orders."""
        update = self._tracker.last_changes.update
        # Read off the snapshot's fields rather than through `at`, to spare a call on the common path.
        if (cached := self._orders).update == update:
            return cached.value
        reported = self._latest_report.orders
        orders = tuple(UnitOrder._from_proto(order, self._unit_target_of_order) for order in reported)
        self._orders = _Snapshot(orders, update)
        return orders

    def _unit_target_of_order(self, order: raw_pb2.UnitOrder) -> Unit[Any] | None:
        """The unit `order` names: the one the game reported under its tag, or, for a tag the game never reports, the
        one this unit's last order of the same ability named under a tag it did; `None` if there is none."""
        unit = self._tracker.unit_tracker.get_by_tag(order.target_unit_tag)
        if unit is None and self._order_targets is not None:
            unit = self._order_targets.get(order.ability_id)
        return unit

    @property
    def is_idle(self) -> bool:
        """Whether the unit has no orders."""
        return not self._latest_report.orders

    def is_doing(self, abilities: AbilityId | Collection[AbilityId]) -> bool:
        """Whether the unit's current order, the first of `orders`, carries out `abilities`, one ability or any of
        several. False for an idle unit."""
        # Read off the report: building `orders` to read the first would read every order and look up its target.
        if not (reported_orders := self._latest_report.orders):
            return False
        ability_id = AbilityId.read(reported_orders[0].ability_id)
        if isinstance(abilities, AbilityId):
            return ability_id == abilities
        return ability_id in abilities

    @property
    def is_moving(self) -> bool:
        """Whether the unit's current order is a move."""
        return self.is_doing(AbilityId.MOVE)

    @property
    def is_attacking(self) -> bool:
        """Whether the unit's current order is an attack, on a unit or a point, an attack-move reading as one."""
        return self.is_doing(AbilityId.ATTACK)

    @property
    def is_gathering(self) -> bool:
        """Whether the unit's current order is a gather."""
        return self.is_doing(AbilityId.GATHER)

    @property
    def is_returning(self) -> bool:
        """Whether the unit's current order is a return of what it carries."""
        return self.is_doing(AbilityId.RETURN)

    @property
    def is_repairing(self) -> bool:
        """Whether the unit's current order is a repair."""
        return self.is_doing(AbilityId.REPAIR)

    @property
    def is_constructing(self) -> bool:
        """Whether the unit builds a structure, or walks to the site of one it was ordered to build: it has a
        `construction`, or its current order's ability places a structure, a barracks building its add-on and a queen
        placing a creep tumor among them."""
        return self.construction is not None or self.is_doing(self._tracker.game_data._placing_abilities)

    @property
    def weapon_cooldown(self) -> GameTime:
        """The time until the unit can attack again, as the game reports it: the cooldown of its last attack, with the
        random delay drawn for it, counting down one step each step. Zero for a unit without a weapon; `weapons` says
        whether it can attack at all.

        It reads 0 too while an attack is under way, for 0 to 6 steps before it by type and up to 8 for an ultralisk,
        and a move ordered 2 steps or more before the attack cancels it, 1 step before it does not (in game). So a unit
        that moves between attacks keeps each by waiting for the cooldown to rise, in the step of the attack.
        `docs/reference/game-behavior/damage-and-buffs.md` has the steps each type reads 0.
        """
        return GameTime(steps=self._latest_report.weapon_cooldown)

    @property
    def buff_duration_max(self) -> GameTime | None:
        """The whole length of the unit's timed life, as a MULE's or a broodling's, or of a timed buff on it, as a
        hatchery's inject timer, as the game reports it; `None` where it reports none, as for a worker or a townhall
        (in game; corpus)."""
        maximum = self._latest_report.buff_duration_max
        return GameTime(steps=maximum) if maximum else None

    @property
    def buff_duration_remaining(self) -> GameTime | None:
        """What is left of `buff_duration_max`, counting down a step at a time; `None` where `buff_duration_max` is.

        It reads zero, not `None`, once run out. It can start above the maximum: a MULE read 1441 and 1443 steps against
        1440, and a broodling up to 132 against 128. A MULE or a broodling is listed last at zero, against its maximum,
        and reported dead the step after (in game).
        `docs/reference/game-behavior/units.md` has the readings.
        """
        report = self._latest_report
        return GameTime(steps=report.buff_duration_remain) if report.buff_duration_max else None

    @property
    def assigned_harvesters(self) -> int:
        """The workers mining a town hall's minerals or a gas building's vespene. 0 for anything else."""
        return self._latest_report.assigned_harvesters

    @property
    def ideal_harvesters(self) -> int:
        """The workers a town hall or gas building can use before another adds nothing: 2 per field, 3 per geyser."""
        return self._latest_report.ideal_harvesters

    @property
    def passengers(self) -> tuple[Passenger, ...]:
        """The units inside the unit."""
        unit_by_tag = self._tracker.unit_tracker.by_tag
        return tuple(Passenger._from_proto(passenger, unit_by_tag) for passenger in self._latest_report.passengers)

    @property
    def cargo_used(self) -> int:
        """The cargo slots the passengers fill."""
        return self._latest_report.cargo_space_taken

    @property
    def cargo_max(self) -> int:
        """The unit's cargo slots. 0 for a unit that carries nothing."""
        return self._latest_report.cargo_space_max

    @property
    def rally_targets(self) -> tuple[RallyTarget, ...]:
        """Where the structure sends what it makes."""
        unit_by_tag = self._tracker.unit_tracker.by_tag
        return tuple(RallyTarget._from_proto(rally, unit_by_tag) for rally in self._latest_report.rally_targets)

    @property
    def is_weapon_ready(self) -> bool:
        """Whether the unit has a weapon and its `weapon_cooldown` is zero: it can attack now, or is under way with an
        attack, whose cooldown reads zero until it fires (in game). False for an oracle whose beam is off, as
        `weapon_against` has it."""
        return bool(self._weapons_firing_now()) and not self.weapon_cooldown

    @property
    def has_reactor(self) -> bool:
        """Whether a reactor is attached to the unit."""
        add_on = self.add_on
        return add_on is not None and UnitType.AnyReactor.includes(add_on)

    @property
    def has_tech_lab(self) -> bool:
        """Whether a tech lab is attached to the unit."""
        add_on = self.add_on
        return add_on is not None and UnitType.AnyTechLab.includes(add_on)

    @property
    def production_capacity(self) -> int:
        """How many units the unit trains at once: 2 for a structure with a reactor, 1 for another structure the
        game's tables give as a maker of a unit, and 0 for anything else, an SCV and a larva among them, which make no
        unit from a queue. A warp gate reads 1, though it warps in without one. Whether the structure is finished is
        left out, and so is whether its add-on is: a barracks building its reactor reads 2, though it trains nothing
        until the reactor finishes."""
        if self.type_id not in self._tracker.game_data._unit_trainers:
            return 0
        return 2 if self.has_reactor else 1

    @property
    def add_on(self) -> OwnUnit[Any] | None:
        """The structure's add-on, or `None`."""
        tag = self._latest_report.add_on_tag
        # An add-on is its structure's owner's.
        return cast("OwnUnit[Any]", self._tracker.unit_tracker.by_tag(tag)) if tag else None

    @property
    def engaged_target(self) -> Unit[Any] | None:
        """The unit this unit is attacking, which may be dead or gone out of the observation, or `None`. Also `None` for
        a target no observation listed, such as a unit created and killed within a step (in game)."""
        tag = self._latest_report.engaged_target_tag
        return self._tracker.unit_tracker.get_by_tag(tag) if tag else None

    @property
    def construction(self) -> OwnUnit[Any] | None:
        """The unfinished structure the unit is building, or the one a drone became until it finishes, or `None`, as
        for a dead unit."""
        return self._tracker.builder_tracker.structure_built_by(self)

    @property
    def builder(self) -> OwnUnit[Any] | None:
        """The SCV building this structure, or the drone that became it, or `None` for a structure nobody is building:
        one halted, one warped in, or one finished."""
        return self._tracker.builder_tracker.builder_of(self)
