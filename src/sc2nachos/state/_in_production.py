"""One unit this player is making."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, final

if TYPE_CHECKING:
    from sc2nachos.gametime import GameTime
    from sc2nachos.ids import UnitTypeId
    from sc2nachos.units import OwnUnit


@final
@dataclass(frozen=True, slots=True)
class InProduction:
    """One unit this player is making: started, or paid for and on its way."""

    type_id: UnitTypeId
    """What is being made."""
    producer: OwnUnit[Any] | None
    """What makes it: the structure training it, the egg or cocoon, the worker sent to build it, the structure morphing
    into it, or for a structure going up, its `builder`: an SCV, an add-on's host, a queen planting a creep tumor, or
    the drone that became it, which has left the observation. `None` for a unit warping in, for an archon two high
    templar merged into, and for a structure nobody is building, such as a Protoss one."""
    unit: OwnUnit[Any] | None
    """The unit itself while it goes up, warps in or merges into an archon. `None` before it stands: for a train, an
    egg's or a cocoon's morph, a structure's morph, and a build or an add-on not begun."""
    progress: float | None
    """How far along it is, from 0 to 1: 0 for a queued train, and for a build or an add-on not begun yet. A structure's
    morph and a cocoon's, whose progress the game does not report, are at the `fraction` of their producer's
    `Unit.morph_progress`, and `None` where that raises, for a morph not seen to begin."""
    total_time: GameTime | None
    """The time the whole of it takes, a whole number of steps: the tables' build time rounded up, as `UnitTypeData`
    gives it (in game). `None` for a morph not seen to begin, and for a unit warping in or merging into an archon,
    whose time the tables do not give: a warp-in takes 256 steps or 81 as where it warps in decides (in game)."""
    time_left: GameTime | None
    """The time until it is done, a whole number of steps, exactly at the normal rate (in game), and a cocoon's to
    within a step: a chrono boost makes it sooner, and a Terran structure whose SCV has left stops, its time left with
    it. A train queued behind another has all its time left and waits for the one ahead first, and a build or an add-on
    not begun waits for its worker or its host. `None` where `total_time` is."""
