"""What an upgrade adds to a type of unit."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, final

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sc2nachos.gamedata._unit_type_data import Attribute


@final
@dataclass(frozen=True, slots=True)
class WeaponUpgrade:
    """What an upgrade adds to one of a unit type's weapons."""

    damage: float = 0.0
    """Added damage per hit."""
    damage_bonuses: Mapping[Attribute, float] = field(default_factory=lambda: MappingProxyType({}))
    """Added bonus damage against each attribute, including attributes the weapon had no bonus against."""
    range: float = 0.0
    """Added range."""
    attack_speed_multiplier: float = 1.0
    """How many times as often the weapon attacks: 1.4 for a zergling's with Adrenal Glands (in game)."""

    def __add__(self, other: WeaponUpgrade) -> WeaponUpgrade:
        """What the two upgrades add to the weapon together: their damage, bonuses and range added, and their attack
        speed multipliers multiplied."""
        bonuses = dict(self.damage_bonuses)
        for attribute, bonus in other.damage_bonuses.items():
            bonuses[attribute] = bonuses.get(attribute, 0.0) + bonus
        return WeaponUpgrade(
            damage=self.damage + other.damage,
            damage_bonuses=MappingProxyType(bonuses),
            range=self.range + other.range,
            attack_speed_multiplier=self.attack_speed_multiplier * other.attack_speed_multiplier,
        )


@final
@dataclass(frozen=True, slots=True)
class UnitTypeUpgrade:
    """What an upgrade adds to one type of unit."""

    armor: float = 0.0
    """Added armor."""
    speed: float = 0.0
    """Added movement speed, in distance per second at the game's Faster speed (22.4 steps)."""
    weapons: tuple[WeaponUpgrade, ...] = ()
    """The change to each weapon, in the type's weapon order, with a `WeaponUpgrade()` for a weapon left alone. Empty if
    no weapon changes."""
