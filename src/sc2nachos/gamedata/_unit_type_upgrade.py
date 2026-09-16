"""What an upgrade adds to a type of unit."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, final

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sc2nachos.gamedata._unittype import Attribute


@final
@dataclass(frozen=True, slots=True)
class WeaponUpgrade:
    """What an upgrade adds to one of a unit type's weapons."""

    damage: float = 0.0
    """What it adds to the damage of a hit."""
    damage_bonuses: Mapping[Attribute, float] = field(default_factory=lambda: MappingProxyType({}))
    """What it adds to the bonus against each attribute, a bonus the weapon did not have included."""
    range: float = 0.0
    """What it adds to how far the weapon reaches."""


@final
@dataclass(frozen=True, slots=True)
class UnitTypeUpgrade:
    """What an upgrade adds to one type of unit."""

    armor: float = 0.0
    """What it adds to the armor."""
    speed: float = 0.0
    """What it adds to how fast the type moves, in distance per second of the game's Faster speed, 22.4 steps."""
    weapons: tuple[WeaponUpgrade, ...] = ()
    """What it adds to each weapon, in the order the type's weapons are, a `WeaponUpgrade()` for a weapon it leaves
    alone. Empty where it changes no weapon."""
