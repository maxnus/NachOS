"""Each unit watched for crossing a value of its energy or its life, or the edge of an area."""

from __future__ import annotations

from collections.abc import Collection
from typing import TYPE_CHECKING, Any, cast, final

from s2clientprotocol import raw_pb2

from sc2nachos.units._tracking._side_watches import _SideWatches

if TYPE_CHECKING:
    from sc2nachos.geometry import Area
    from sc2nachos.ids import UnitTypeId
    from sc2nachos.units._own_unit import OwnUnit
    from sc2nachos.units._tracking._tracker import _Tracker
    from sc2nachos.units._unit import Unit

_IN_VISION = raw_pb2.DisplayType.Visible
_IN_FOG = raw_pb2.DisplayType.Snapshot
_OWN = raw_pb2.Alliance.Self
_ENEMY = raw_pb2.Alliance.Enemy


@final
class _UnitWatcher:
    """Which units of this player's and the enemy's crossed the values and areas watched since each was last seen,
    recorded among the tracker's last changes.

    A unit crosses a value of its energy or its life when it is seen in vision on one side of it, at or above it or
    below it, having last been seen in vision on the other, and the edge of an area when it is seen in sight on the
    other side of it than last, or first seen inside it. A watch reports nothing on the first update it runs on, and
    keeps what it found for the next.
    """

    __slots__ = ("_enemy", "_own", "_tracker")

    def __init__(self, tracker: _Tracker) -> None:
        self._tracker = tracker
        self._own: _SideWatches[OwnUnit[Any]] = _SideWatches()
        self._enemy: _SideWatches[Unit[Any]] = _SideWatches()

    def watch(
        self,
        *,
        own_energy: Collection[tuple[UnitTypeId, float]] = (),
        enemy_energy: Collection[tuple[UnitTypeId, float]] = (),
        own_life_reached: Collection[float] = (),
        own_life_dropped: Collection[float] = (),
        enemy_life_reached: Collection[float] = (),
        enemy_life_dropped: Collection[float] = (),
        own_areas: Collection[Area] = (),
        enemy_areas: Collection[Area] = (),
    ) -> None:
        """Record in the tracker's last changes each unit that crossed a value or area watched for its side: an energy
        of a unit type reached from below, a life fraction reached from below or dropped below, the edge of an area.

        Stop watching what is no longer asked for, and start watching what is asked for anew.
        """
        own, enemy = self._own, self._enemy
        own.watch(own_energy, own_life_reached, own_life_dropped, own_areas)
        enemy.watch(enemy_energy, enemy_life_reached, enemy_life_dropped, enemy_areas)
        for unit in self._tracker.units.present:
            report = unit._latest_data
            alliance = report.alliance
            if alliance == _OWN:
                if own.watching and (display := report.display_type) != _IN_FOG:
                    own.compare(cast("OwnUnit[Any]", unit), report, in_vision=display == _IN_VISION)
            elif alliance == _ENEMY and enemy.watching and (display := report.display_type) != _IN_FOG:
                enemy.compare(unit, report, in_vision=display == _IN_VISION)
        changes = self._tracker.last_changes
        dead = [unit._id for unit in (*changes.units_died, *changes.units_found_dead)]
        for side in (own, enemy):
            side.settle(dead)
        changes.own_units_energy_reached = own.energy_reached
        changes.enemy_units_energy_reached = enemy.energy_reached
        changes.own_units_life_fraction_reached = own.life_reached
        changes.enemy_units_life_fraction_reached = enemy.life_reached
        changes.own_units_life_fraction_dropped = own.life_dropped
        changes.enemy_units_life_fraction_dropped = enemy.life_dropped
        changes.own_units_entered_area = own.entered
        changes.enemy_units_entered_area = enemy.entered
        changes.own_units_left_area = own.left
        changes.enemy_units_left_area = enemy.left

    def stop(self) -> None:
        """Let go of everything watched."""
        if self._own.watching or self._enemy.watching:
            self._own = _SideWatches()
            self._enemy = _SideWatches()
