"""An amount of minerals and vespene, and one of supply too."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, final

from sc2nachos._shared.vector_arithmetic import _VectorArithmetic

if TYPE_CHECKING:
    from sc2nachos.gamedata._cost import Cost


@final
@dataclass(frozen=True, slots=True)
class Resources(_VectorArithmetic):
    """An amount of minerals and vespene: spent, held, earned or averaged.

    Whole when it comes from the game or from adding such amounts up; fractional once scaled or divided.
    """

    minerals: float
    """Minerals."""
    vespene: float
    """Vespene."""

    @property
    def total(self) -> float:
        """Minerals plus vespene, counted alike."""
        return self.minerals + self.vespene

    def to_tuple(self) -> tuple[float, float]:
        """Its minerals and vespene."""
        return (self.minerals, self.vespene)

    def covers(self, other: Resources) -> bool:
        """Whether this amount is at least `other` in both minerals and vespene."""
        return self.minerals >= other.minerals and self.vespene >= other.vespene

    def minimum(self, other: Resources) -> Resources:
        """The smaller of the two amounts of each resource, each on its own: 75/50 of 100/50 and 75/80."""
        return Resources(min(self.minerals, other.minerals), min(self.vespene, other.vespene))

    def maximum(self, other: Resources) -> Resources:
        """The larger of the two amounts of each resource; `maximum(Resources(0, 0))` drops what is owed."""
        return Resources(max(self.minerals, other.minerals), max(self.vespene, other.vespene))


@final
@dataclass(frozen=True, slots=True)
class ResourcesAndSupply(_VectorArithmetic):
    """An amount of minerals, vespene and supply: what a player has left to spend, `api.budget`, or what orders take,
    `api.orders.pending_cost`. A bot giving several orders in a turn may take each one's cost off as it goes:
    `budget -= cost`.

    Not a `Resources`, which holds no supply, nor a `Cost`, which is the price of one thing, with the time it takes to
    make: `covers` says whether it pays for any of the three. Two add up and take off as `Resources` do, part by part,
    and a `Cost` takes off too, without its time.
    """

    minerals: float
    """Minerals."""
    vespene: float
    """Vespene."""
    supply: float = 0.0
    """Supply: what is free under the cap in what a player has left, negative once units that provided supply are lost
    or once more is taken off than was free; and in what orders take, what they take of the cap, negative for one that
    gives supply back, a spawning pool's."""

    def to_tuple(self) -> tuple[float, float, float]:
        """Its minerals, vespene and supply."""
        return (self.minerals, self.vespene, self.supply)

    @property
    def resources(self) -> Resources:
        """The minerals and vespene, without the supply."""
        return Resources(self.minerals, self.vespene)

    def covers(self, other: ResourcesAndSupply | Cost | Resources) -> bool:
        """Whether it pays for `other`: at least its minerals and its vespene, and its supply if it takes any. One that
        takes none, a structure's cost, is paid for however little supply is free, so that a player over its cap can
        still build what provides more."""
        supply = 0.0 if isinstance(other, Resources) else other.supply
        return (
            self.minerals >= other.minerals and self.vespene >= other.vespene and (supply <= 0 or self.supply >= supply)
        )

    # Takes a `Cost` besides its own kind, which the base class's `Self` reads as an incompatible override.
    def __sub__(self, other: ResourcesAndSupply | Cost) -> ResourcesAndSupply:  # pyright: ignore[reportIncompatibleMethodOverride]
        """This less `other`, part by part: negative where `other` is more, and more supply free for a cost that gives
        supply back, a spawning pool's. A `Cost`'s time is left out."""
        return ResourcesAndSupply(
            self.minerals - other.minerals, self.vespene - other.vespene, self.supply - other.supply
        )
