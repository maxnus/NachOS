"""Each unit compared with the update of the unit tracker before."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, final

from s2clientprotocol import raw_pb2

from sc2nachos.ids import BuffId
from sc2nachos.units._own_unit import OwnUnit
from sc2nachos.units._values import CloakState

if TYPE_CHECKING:
    from sc2nachos.units._tracking._tracker import _Tracker
    from sc2nachos.units._unit import Unit

_IN_VISION = raw_pb2.DisplayType.Visible
_IN_FOG = raw_pb2.DisplayType.Snapshot
_OWN = raw_pb2.Alliance.Self
_ENEMY = raw_pb2.Alliance.Enemy


@final
class _UnitComparer:
    """What each unit of this player's or the enemy's lost since the tracker's update before, how its cloak changed,
    and the buffs it gained and lost, recorded among the tracker's last changes as asked."""

    __slots__ = ("_compared", "_compared_update", "_tracker", "_worn", "_worn_update")

    def __init__(self, tracker: _Tracker) -> None:
        self._tracker = tracker
        # The report of each unit of this player's or the enemy's in sight, by id, as of the update `compare` last ran
        # on.
        self._compared: dict[int, raw_pb2.Unit] = {}
        self._compared_update = 0
        # The buffs of each of those units in vision wearing any, by id, as of the update `compare` last compared buffs
        # on. Kept apart, and only for units wearing something, since reading a unit's buffs costs some five times what
        # reading another of its fields does.
        self._worn: dict[int, tuple[int, ...]] = {}
        self._worn_update = 0

    def compare(self, *, damage: bool, energy: bool, cloak: bool, buffs: bool) -> None:
        """Record in the tracker's last changes, as asked, the health and shields and the energy each unit lost since
        the update before, how its cloak changed, and the buffs it gained and lost, if this ran on that update too.
        Keep each unit's report for the next.

        Cloak is compared for a unit in sight in both, since an enemy unit nothing detects is listed cloaked but not in
        vision; buffs for one in vision in both, since such an enemy unit shows none (in game); loss for one in vision
        in both and of the same type.

        Raises `UncuratedIdError` for a buff the curated ids leave out, which belongs in them.
        """
        tracker = self._tracker
        update = tracker._number_of_updates
        before = self._compared if self._compared_update == update - 1 else None
        worn_before = self._worn if buffs and self._worn_update == update - 1 else None
        compared: dict[int, raw_pb2.Unit] = {}
        worn: dict[int, tuple[int, ...]] = {}
        changes = tracker.last_changes
        for unit in tracker.units.present:
            report = unit._latest_data
            alliance = report.alliance
            if (alliance != _OWN and alliance != _ENEMY) or report.display_type == _IN_FOG:
                continue
            compared[unit._id] = report
            in_vision = report.display_type == _IN_VISION
            if buffs and in_vision and (listed := report.buff_ids):
                worn[unit._id] = tuple(listed)
            if before is None or (then := before.get(unit._id)) is None:
                continue
            if cloak and report.cloak != then.cloak:
                if isinstance(unit, OwnUnit):
                    changes.own_units_cloak_changed.append((unit, CloakState(then.cloak)))
                else:
                    changes.enemy_units_cloak_changed.append((unit, CloakState(then.cloak)))
            if not in_vision or then.display_type != _IN_VISION:
                continue
            if worn_before is not None and (now := worn.get(unit._id, ())) != (was := worn_before.get(unit._id, ())):
                self._record_buffs(unit, was, now)
            if report.unit_type != then.unit_type:
                continue
            if damage and (lost := max(0.0, then.health - report.health) + max(0.0, then.shield - report.shield)):
                if isinstance(unit, OwnUnit):
                    changes.own_units_damaged.append((unit, lost))
                else:
                    changes.enemy_units_damaged.append((unit, lost))
            if energy and (drained := then.energy - report.energy) > 0.0:
                if isinstance(unit, OwnUnit):
                    changes.own_units_energy_lost.append((unit, drained))
                else:
                    changes.enemy_units_energy_lost.append((unit, drained))
        self._compared = compared
        self._compared_update = update
        if buffs:
            self._worn = worn
            self._worn_update = update

    def stop(self) -> None:
        """Let go of the reports and buffs kept."""
        self._compared = {}
        self._worn = {}

    def _record_buffs(self, unit: Unit[Any], was: tuple[int, ...], now: tuple[int, ...]) -> None:
        """Record the buffs `unit` gained and lost between wearing `was` and `now`, each in the order of their ids."""
        if not was or not now:
            # A unit that wore nothing before or wears nothing now, as a worker picking up minerals or delivering them
            # does every trip, which is most changes (corpus).
            gained, lost = sorted(now), sorted(was)
        else:
            gained, lost = sorted(set(now).difference(was)), sorted(set(was).difference(now))
        gained_buffs = [BuffId.read(buff) for buff in gained]
        lost_buffs = [BuffId.read(buff) for buff in lost]
        changes = self._tracker.last_changes
        if isinstance(unit, OwnUnit):
            changes.own_units_gained_buff.extend((unit, buff) for buff in gained_buffs)
            changes.own_units_lost_buff.extend((unit, buff) for buff in lost_buffs)
        else:
            changes.enemy_units_gained_buff.extend((unit, buff) for buff in gained_buffs)
            changes.enemy_units_lost_buff.extend((unit, buff) for buff in lost_buffs)
