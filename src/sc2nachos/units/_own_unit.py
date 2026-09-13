"""One of the player's own units."""

from __future__ import annotations

from sc2nachos.constants import steps_to_seconds
from sc2nachos.units._kind import Kind
from sc2nachos.units._unit import Unit
from sc2nachos.units._values import Passenger, RallyTarget, UnitOrder


class OwnUnit[K: Kind](Unit[K]):
    """One of this player's units, which also reads what the game reports only to the player a unit belongs to.

    A unit changes between `Unit` and `OwnUnit` in place when it changes sides, as under a neural parasite.
    """

    __slots__ = ()

    @property
    def orders(self) -> tuple[UnitOrder, ...]:
        """What it is doing, then what it has queued."""
        identify = self._tracker.identify
        return tuple(UnitOrder.from_proto(order, identify) for order in self._now.orders)

    @property
    def is_idle(self) -> bool:
        """Whether it has no orders."""
        return not self._now.orders

    @property
    def weapon_cooldown(self) -> float:
        """Seconds until it can attack again, and 0 for a unit without a weapon."""
        # The game counts it in steps.
        return steps_to_seconds(self._now.weapon_cooldown)

    @property
    def assigned_harvesters(self) -> int:
        """The workers mining from it."""
        return self._now.assigned_harvesters

    @property
    def ideal_harvesters(self) -> int:
        """The workers it can take before another mines nothing more."""
        return self._now.ideal_harvesters

    @property
    def passengers(self) -> tuple[Passenger, ...]:
        """The units inside it."""
        identify = self._tracker.identify
        return tuple(Passenger.from_proto(passenger, identify) for passenger in self._now.passengers)

    @property
    def cargo_used(self) -> int:
        """The cargo slots its passengers fill."""
        return self._now.cargo_space_taken

    @property
    def cargo_max(self) -> int:
        """The cargo slots it has, and 0 for a unit that carries none."""
        return self._now.cargo_space_max

    @property
    def rally_targets(self) -> tuple[RallyTarget, ...]:
        """Where it sends what it makes."""
        identify = self._tracker.identify
        return tuple(RallyTarget.from_proto(rally, identify) for rally in self._now.rally_targets)

    @property
    def add_on_id(self) -> int | None:
        """The id of its add-on, or `None` without one."""
        tag = self._now.add_on_tag
        return self._tracker.identify(tag) if tag else None

    @property
    def engaged_target_id(self) -> int | None:
        """The id of the unit it is attacking, or `None`."""
        tag = self._now.engaged_target_tag
        return self._tracker.identify(tag) if tag else None
