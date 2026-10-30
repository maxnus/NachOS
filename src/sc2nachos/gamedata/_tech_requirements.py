"""What must be in place before an ability is offered."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, final

if TYPE_CHECKING:
    from sc2nachos.gamedata._tech import Tech
    from sc2nachos.ids import UnitTypeId, UpgradeId


@final
@dataclass(frozen=True, slots=True)
class TechRequirements:
    """The techs, add-on and upgrades a unit needs before it is offered an ability, beyond the ability's cost."""

    techs: frozenset[Tech] = frozenset()
    """Every tech this player must have: a structure of a type granting it must stand, a spire or a greater spire for
    `Tech.SPIRE`, as `GameData.types_granting` lists them."""
    add_on: UnitTypeId | None = None
    """The add-on the unit itself must have: a barracks trains a ghost only with its own tech lab, `TECH_LAB_BARRACKS`.
    `None` where it needs none."""
    upgrades: frozenset[UpgradeId] = frozenset()
    """Every upgrade that must be researched."""
