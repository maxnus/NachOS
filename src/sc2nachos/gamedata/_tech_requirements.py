"""What must be in place before an ability is offered."""

from __future__ import annotations

from dataclasses import dataclass
from typing import final

from sc2nachos.ids import UnitTypeId, UpgradeId


@final
@dataclass(frozen=True, slots=True)
class TechRequirements:
    """The structures and upgrades a unit needs before it is offered an ability, beyond the ability's cost."""

    structures: frozenset[UnitTypeId] = frozenset()
    """Every structure that must stand. A type whose `tech_aliases` name one counts as it: a lowered supply depot as a
    supply depot, a hive as a lair. An add-on must be the unit's own: a barracks trains a ghost only with its own tech
    lab."""
    upgrades: frozenset[UpgradeId] = frozenset()
    """Every upgrade that must be researched."""
