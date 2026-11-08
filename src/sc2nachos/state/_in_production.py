"""One unit this player is making."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, final

from sc2nachos.ids import UnitTypeId
from sc2nachos.units import Progress

if TYPE_CHECKING:
    from sc2nachos.units import OwnUnit


@final
@dataclass(frozen=True, slots=True)
class InProduction(Progress[UnitTypeId]):
    """One unit this player is making, started or paid for and on its way, and how far it has got.

    `fraction` is 0 for a queued train, and for a build or an add-on not begun yet, and a structure's morph and a
    cocoon's, whose progress the game does not report, are at their producer's `Unit.morph_progress`. `total_time` is
    the tables' build time rounded up, as `UnitTypeData` gives it (in game), but for a warp-in, which takes 256 steps or
    81 as where it warps in decides, told apart by how fast it goes up (in game). `time_left` is exact at the normal
    rate (in game), and a cocoon's to within a step: a chrono boost makes it sooner, and a Terran structure whose SCV
    has left stops, its time left with it. A train queued behind another has all its time left and waits for the one
    ahead first, and a build or an add-on not begun waits for its worker or its host.
    """

    producer: OwnUnit[Any] | None
    """What makes it: the structure training it, the egg or cocoon, the worker sent to build it, the structure morphing
    into it, or for a structure going up, its `builder`: an SCV, an add-on's host, a queen planting a creep tumor, or
    the drone that became it, which has left the observation. `None` for a unit warping in, for an archon two high
    templar merged into, and for a structure nobody is building, such as a Protoss one."""
    unit: OwnUnit[Any] | None
    """The unit itself while it goes up, warps in or merges into an archon. `None` before it stands: for a train, an
    egg's or a cocoon's morph, a structure's morph, and a build or an add-on not begun."""
