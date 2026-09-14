"""One of the player's own units."""

from __future__ import annotations

from typing import Any

from sc2nachos.units._unit import Unit
from sc2nachos.units._unit_type import UnitType
from sc2nachos.units._values import Order, Passenger, RallyTarget


class OwnUnit[K: UnitType.AnyType](Unit[K]):
    """One of this player's units, which also reads what the game reports only to the player a unit belongs to.

    A unit changes between `Unit` and `OwnUnit` in place when it changes sides, as under a neural parasite.
    """

    __slots__ = ()

    @property
    def orders(self) -> tuple[Order, ...]:
        """What it is doing, then what it has queued."""
        unit_by_tag = self._tracker.unit_by_tag
        return tuple(Order.from_proto(order, unit_by_tag) for order in self._latest_data.orders)

    @property
    def is_idle(self) -> bool:
        """Whether it has no orders."""
        return not self._latest_data.orders

    @property
    def weapon_cooldown_steps(self) -> float:
        """Steps until it can attack again, and 0 for a unit without a weapon."""
        return self._latest_data.weapon_cooldown

    @property
    def assigned_harvesters(self) -> int:
        """The workers mining a town hall's minerals or a gas building's vespene, and 0 for anything else."""
        return self._latest_data.assigned_harvesters

    @property
    def ideal_harvesters(self) -> int:
        """The workers a town hall or gas building takes before another mines nothing more: 2 a field, 3 a geyser."""
        return self._latest_data.ideal_harvesters

    @property
    def passengers(self) -> tuple[Passenger, ...]:
        """The units inside it."""
        unit_by_tag = self._tracker.unit_by_tag
        return tuple(Passenger.from_proto(passenger, unit_by_tag) for passenger in self._latest_data.passengers)

    @property
    def cargo_used(self) -> int:
        """The cargo slots its passengers fill."""
        return self._latest_data.cargo_space_taken

    @property
    def cargo_max(self) -> int:
        """The cargo slots it has, and 0 for a unit that carries none."""
        return self._latest_data.cargo_space_max

    @property
    def rally_targets(self) -> tuple[RallyTarget, ...]:
        """Where it sends what it makes."""
        unit_by_tag = self._tracker.unit_by_tag
        return tuple(RallyTarget.from_proto(rally, unit_by_tag) for rally in self._latest_data.rally_targets)

    @property
    def add_on(self) -> Unit[Any] | None:
        """Its add-on, or `None` without one."""
        tag = self._latest_data.add_on_tag
        return self._tracker.unit_by_tag(tag) if tag else None

    @property
    def engaged_target(self) -> Unit[Any] | None:
        """The unit it is attacking, or `None`."""
        tag = self._latest_data.engaged_target_tag
        return self._tracker.unit_by_tag(tag) if tag else None

    @property
    def construction(self) -> Unit[Any] | None:
        """The unfinished structure it is building now, or the one a drone became until it finishes, or `None`."""
        return self._tracker.construction_of(self)

    @property
    def builder(self) -> Unit[Any] | None:
        """The SCV building this structure now, or the drone that became it, or `None` for a structure nobody is
        building, such as one halted, one warped in, or one finished."""
        return self._tracker.builder_of(self)
