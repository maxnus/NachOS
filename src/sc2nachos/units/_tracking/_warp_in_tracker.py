"""How far each of this player's units warping in has got."""

from __future__ import annotations

import itertools
from typing import TYPE_CHECKING, Any, final

from sc2nachos._shared.errors import NotReportedError
from sc2nachos.gamedata._unit_type import UnitType
from sc2nachos.gametime import GameTime
from sc2nachos.ids import UnitTypeId
from sc2nachos.units._progress import Progress

if TYPE_CHECKING:
    from sc2nachos.units._tracking._tracker import _Tracker
    from sc2nachos.units._unit import Unit

# A warp-in's build progress rises 1/256 a step at a pylon far from any warp gate, and it is done 256 steps after the
# step its order was sent after; or 1/80 a step at a pylon among warp gates or beside a phasing warp prism, and it is
# done a step after it reaches 80/80, 81 steps after (in game).
_SLOW_WARP_IN_TIME = GameTime(steps=256)
_FAST_WARP_IN_RISE_TIME = GameTime(steps=80)
_FAST_WARP_IN_TIME = _FAST_WARP_IN_RISE_TIME + GameTime(steps=1)
# A warp-in that has risen more than this many times as far as a slow one would have is a fast one, which rises 3.2.
_FAST_WARP_IN_LEAST_RISE_OVER_SLOW = 2.0


@final
class _WarpInTracker:
    """When each of this player's units warping in began: at the step of the observation before the first that lists
    it, the step its order was sent after (in game), or `None` for one listed in the first observation."""

    __slots__ = ("_starts", "_step", "_tracker")

    def __init__(self, tracker: _Tracker) -> None:
        self._tracker = tracker
        # The step each unit warping in began at, by id, `None` where that was not seen.
        self._starts: dict[int, int | None] = {}
        # The step of the last update, `None` before the first.
        self._step: int | None = None

    def update(self, step: int) -> None:
        """Take in what the tracker's update at `step` found: this player's units new to it warping in, and those
        finished or dead."""
        previous_step = self._step
        self._step = step
        changes = self._tracker.last_changes
        for unit in changes.own_units_created:
            if unit._type_id in UnitType.GatewayUnit.type_ids and unit._latest_report.build_progress < 1.0:
                self._starts[unit._id] = previous_step
        if self._starts:
            for unit in itertools.chain(changes.own_units_finished, changes.units_died, changes.units_found_dead):
                self._starts.pop(unit._id, None)

    def progress_of(self, unit: Unit[Any]) -> Progress[UnitTypeId]:
        """How far `unit`, warping in, has got: its build progress, and the time it takes and has left, slow or fast by
        how far it has risen since it began. In a realtime game, or on the ladder, where it can show an observation
        late, it can read slow for its first observations.

        Raises `NotReportedError` for one not seen to begin, and `KeyError` for a unit not warping in.
        """
        began = self._starts[unit._id]
        if began is None or self._step is None:
            raise NotReportedError(f"{unit!r} was warping in when first seen, so it was not seen to begin")
        fraction = unit._latest_report.build_progress
        steps_done = self._step - began
        if fraction * _SLOW_WARP_IN_TIME.steps > _FAST_WARP_IN_LEAST_RISE_OVER_SLOW * steps_done:
            total_time = _FAST_WARP_IN_TIME
            time_left = GameTime(steps=1 + round((1 - fraction) * _FAST_WARP_IN_RISE_TIME.steps))
        else:
            total_time = _SLOW_WARP_IN_TIME
            time_left = GameTime(steps=round((1 - fraction) * _SLOW_WARP_IN_TIME.steps))
        return Progress(unit._type_id, fraction, total_time, time_left)
