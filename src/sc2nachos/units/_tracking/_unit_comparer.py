"""Each unit compared with the unit tracker's previous update."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, final

from s2clientprotocol import raw_pb2

from sc2nachos.ids import BuffId
from sc2nachos.units._values import CloakState

if TYPE_CHECKING:
    from collections.abc import Collection

    from sc2nachos.units._tracking._tracker import _Tracker
    from sc2nachos.units._tracking._tracker_changes import _ComparedChanges
    from sc2nachos.units._unit import Unit

_IN_VISION = raw_pb2.DisplayType.Visible
_IN_FOG = raw_pb2.DisplayType.Snapshot
_OWN = raw_pb2.Alliance.Self
_ENEMY = raw_pb2.Alliance.Enemy


@final
class _UnitComparer:
    """Records, on request, what each of this player's and the enemy's units lost since the tracker's previous
    update, how its cloak changed, and the buffs it gained and lost, into the tracker's last changes."""

    __slots__ = ("_compared", "_compared_update", "_tracker", "_worn", "_worn_update")

    def __init__(self, tracker: _Tracker) -> None:
        self._tracker = tracker
        # The report of each of this player's and the enemy's units in sight, by id, as of the update `compare` last
        # ran on.
        self._compared: dict[int, raw_pb2.Unit] = {}
        self._compared_update = 0
        # The buffs of each of those units in vision that has any, by id, as of the update `compare` last compared
        # buffs on. Kept separately, and only for units with buffs, since reading a unit's buffs costs some five times
        # what reading another field does.
        self._worn: dict[int, tuple[int, ...]] = {}
        self._worn_update = 0

    def compare(
        self, *, damage: bool, energy: bool, cloak: bool, buffs: bool, only_buffs: Collection[int] | None = None
    ) -> None:
        """Record into the tracker's last changes, as the flags ask, the health and shields and the energy each unit
        lost since the previous update, how its cloak changed, and the buffs it gained and lost (those in
        `only_buffs`, or every buff), provided this ran on that update too. Keep each unit's report for the next
        update.

        Cloak is compared for a unit in sight in both updates, since an enemy unit nothing detects is listed cloaked
        but not in vision. Buffs are compared for a unit in vision in both, since such an enemy unit shows none (in
        game). Loss is compared for a unit in vision in both and of the same type.

        Raises `UncuratedIdError` for a buff the curated ids leave out. Such a buff belongs among them.
        """
        tracker = self._tracker
        changes = tracker.last_changes
        update = changes.update
        before = self._compared if self._compared_update == update - 1 else None
        worn_before = self._worn if buffs and self._worn_update == update - 1 else None
        compared: dict[int, raw_pb2.Unit] = {}
        worn: dict[int, tuple[int, ...]] = {}
        for unit in tracker.unit_tracker.present:
            report = unit._latest_report
            alliance = report.alliance
            if (alliance != _OWN and alliance != _ENEMY) or report.display_type == _IN_FOG:
                continue
            compared[unit._id] = report
            in_vision = report.display_type == _IN_VISION
            side: _ComparedChanges[Any] = changes.own if alliance == _OWN else changes.enemy
            if buffs and in_vision and (listed := report.buff_ids):
                worn[unit._id] = tuple(listed)
            if before is None or (then := before.get(unit._id)) is None:
                continue
            if cloak and report.cloak != then.cloak:
                side.cloak_changed.append((unit, CloakState(then.cloak)))
            if not in_vision or then.display_type != _IN_VISION:
                continue
            if worn_before is not None and (now := worn.get(unit._id, ())) != (was := worn_before.get(unit._id, ())):
                self._record_buffs(side, unit, was, now, only_buffs)
            if report.unit_type != then.unit_type:
                continue
            if damage and (lost := max(0.0, then.health - report.health) + max(0.0, then.shield - report.shield)):
                side.damaged.append((unit, lost))
            if energy and (drained := then.energy - report.energy) > 0.0:
                side.energy_lost.append((unit, drained))
        self._compared = compared
        self._compared_update = update
        if buffs:
            self._worn = worn
            self._worn_update = update

    def stop(self) -> None:
        """Drop the reports and buffs kept."""
        self._compared = {}
        self._worn = {}

    def _record_buffs(
        self,
        side: _ComparedChanges[Any],
        unit: Unit[Any],
        was: tuple[int, ...],
        now: tuple[int, ...],
        only: Collection[int] | None,
    ) -> None:
        """Record into `side` the buffs `unit` gained and lost between `was` and `now`, in order of id, limited to
        `only` if given."""
        if not was or not now:
            # The unit had no buffs before or has none now, as a worker picking up or delivering minerals does every
            # trip. That is most changes (corpus).
            gained, lost = sorted(now), sorted(was)
        else:
            gained, lost = sorted(set(now).difference(was)), sorted(set(was).difference(now))
        if only is not None:
            gained = [buff for buff in gained if buff in only]
            lost = [buff for buff in lost if buff in only]
        side.gained_buff.extend((unit, BuffId.read(buff)) for buff in gained)
        side.lost_buff.extend((unit, BuffId.read(buff)) for buff in lost)
