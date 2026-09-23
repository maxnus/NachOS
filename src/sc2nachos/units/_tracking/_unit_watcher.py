"""Each unit watched for crossing a value of its energy or life, or the edge of an area."""

from __future__ import annotations

from collections.abc import Collection
from typing import TYPE_CHECKING, Any, cast, final

from s2clientprotocol import raw_pb2

from sc2nachos.units._tracking._watches import _Watches

if TYPE_CHECKING:
    from sc2nachos.geometry import Area
    from sc2nachos.ids import UnitTypeId
    from sc2nachos.units._own_unit import OwnUnit
    from sc2nachos.units._tracking._tracker import _Tracker
    from sc2nachos.units._unit import Unit
    from sc2nachos.units._values import VitalType

_IN_VISION = raw_pb2.DisplayType.Visible
_IN_FOG = raw_pb2.DisplayType.Snapshot
_OWN = raw_pb2.Alliance.Self
_ENEMY = raw_pb2.Alliance.Enemy


@final
class _UnitWatcher:
    """Records into the tracker's last changes which of this player's and the enemy's units crossed a watched vital
    value or area edge since each was last seen.

    A unit crosses a vital value when it is seen in vision on one side of the value (at or above it, or below it)
    after last being seen in vision on the other side. It crosses an area's edge when it is seen in sight on the
    other side of the edge from last time, or is first seen inside the area. A watch reports nothing on the first
    update it runs on, and keeps what it saw for the next.
    """

    __slots__ = ("_enemy", "_own", "_tracker")

    def __init__(self, tracker: _Tracker) -> None:
        self._tracker = tracker
        self._own: _Watches[OwnUnit[Any]] = _Watches()
        self._enemy: _Watches[Unit[Any]] = _Watches()

    def watch(
        self,
        *,
        own_reached: Collection[tuple[VitalType, float, UnitTypeId]] = (),
        own_dropped: Collection[tuple[VitalType, float, UnitTypeId]] = (),
        enemy_reached: Collection[tuple[VitalType, float, UnitTypeId]] = (),
        enemy_dropped: Collection[tuple[VitalType, float, UnitTypeId]] = (),
        own_areas: Collection[Area] = (),
        enemy_areas: Collection[Area] = (),
    ) -> None:
        """Record into the tracker's last changes each unit that crossed a vital value or area edge watched for its
        side: a value of a vital of its type, reached from below or dropped below, or the edge of an area.

        Stops watching what is no longer asked for, and starts watching what is newly asked for.
        """
        own, enemy = self._own, self._enemy
        own.watch(own_reached, own_dropped, own_areas)
        enemy.watch(enemy_reached, enemy_dropped, enemy_areas)
        changes = self._tracker.last_changes
        for unit in self._tracker.unit_tracker.present:
            report = unit._latest_report
            alliance = report.alliance
            if alliance == _OWN:
                if own.watching and (display := report.display_type) != _IN_FOG:
                    own.compare(cast("OwnUnit[Any]", unit), report, changes.own, in_vision=display == _IN_VISION)
            elif alliance == _ENEMY and enemy.watching and (display := report.display_type) != _IN_FOG:
                enemy.compare(unit, report, changes.enemy, in_vision=display == _IN_VISION)
        dead = [unit._id for unit in (*changes.units_died, *changes.units_found_dead)]
        own.end_update(dead)
        enemy.end_update(dead)

    def stop(self) -> None:
        """Drop everything watched."""
        if self._own.watching or self._enemy.watching:
            self._own = _Watches()
            self._enemy = _Watches()
