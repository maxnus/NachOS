"""One unit this player is making."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, final

if TYPE_CHECKING:
    from sc2nachos.ids import UnitTypeId
    from sc2nachos.units import OwnUnit


@final
@dataclass(frozen=True, slots=True)
class InProduction:
    """One unit this player is making: started, or paid for and on its way."""

    type_id: UnitTypeId
    """What is being made."""
    unit: OwnUnit[Any]
    """The unit it is read from: the structure training it, the egg or cocoon, the worker sent to build it, the
    structure morphing into it, or the unit itself while it goes up or warps in."""
    progress: float | None
    """How far along it is, from 0 to 1: 0 for a queued train, and for a build or an add-on not begun yet. `None` for
    a morph other than a larva's, whose progress the game does not report: a structure's, such as an orbital command
    or a lair, and a unit's in its cocoon."""
