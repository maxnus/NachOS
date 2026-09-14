"""What has to be in place before an ability is offered."""

from __future__ import annotations

from dataclasses import dataclass
from typing import final

from sc2nachos.ids import UnitTypeId, UpgradeId


@final
@dataclass(frozen=True, slots=True)
class TechRequirements:
    """What has to stand and be researched before a unit is offered an ability, beyond what it costs."""

    structures: frozenset[UnitTypeId] = frozenset()
    """Every structure that must stand. A type whose `tech_aliases` name one counts as it, as a lowered supply depot
    counts as a supply depot and a hive as a lair, and an add-on among them has to be the unit's own: a barracks trains
    a ghost only with a tech lab of its own."""
    upgrades: frozenset[UpgradeId] = frozenset()
    """Every upgrade that must be researched."""
