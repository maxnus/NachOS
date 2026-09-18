"""What each observation of a game reports has happened, handed on as events."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, final

from sc2nachos.events import (
    AlertEvent,
    ChatEvent,
    EnemyUnitEnteredSightEvent,
    EnemyUnitFirstSeenEvent,
    EnemyUnitLeftSightEvent,
    OwnActionEvent,
    OwnConstructionFinishedEvent,
    OwnConstructionStartedEvent,
    OwnUnitCreatedEvent,
    OwnUpgradeFinishedEvent,
    OwnWarpInFinishedEvent,
    UnitAllianceChangedEvent,
    UnitDamagedEvent,
    UnitDiedEvent,
    UnitEnergyLostEvent,
    UnitFoundDeadEvent,
    UnitTypeChangedEvent,
)
from sc2nachos.gamedata import Attribute
from sc2nachos.state import Alert

if TYPE_CHECKING:
    from typing import Any

    from s2clientprotocol import sc2api_pb2

    from sc2nachos.events import EventBus
    from sc2nachos.state._state import _State
    from sc2nachos.units import Unit
    from sc2nachos.units._tracker import _UnitTracker


# Each alert by the protocol's value.
_ALERTS: Mapping[int, Alert] = MappingProxyType({int(alert): alert for alert in Alert})


@final
class _Reporter:
    """Hands on what each observation of one game reports has happened, each as an event, and only to a type with a
    handler still to run."""

    __slots__ = ("_tracker", "_vitals", "_vitals_update")

    def __init__(self, tracker: _UnitTracker) -> None:
        self._tracker = tracker
        # The raw type, health, shields and energy of each unit in vision by id, as of the tracker update
        # `_vitals_update` counts, kept while `UnitDamagedEvent` or `UnitEnergyLostEvent` has a handler.
        self._vitals: dict[int, tuple[int, float, float, float]] = {}
        self._vitals_update = 0

    def report(self, events: EventBus, observation: sc2api_pb2.ResponseObservation, state: _State, step: int) -> None:
        """Hand on what `observation`, taken in at `step` and read as `state`, reports has happened."""
        changes = self._tracker.changes
        wanted = events._has_handlers
        emit = events._emit
        if changes.created and wanted(OwnUnitCreatedEvent):
            for unit in changes.created:
                emit(OwnUnitCreatedEvent(step, unit))
        if changes.first_seen and wanted(EnemyUnitFirstSeenEvent):
            for unit in changes.first_seen:
                emit(EnemyUnitFirstSeenEvent(step, unit))
        if changes.type_changes and wanted(UnitTypeChangedEvent):
            for unit, previous_type in changes.type_changes:
                emit(UnitTypeChangedEvent(step, unit, previous_type))
        if changes.alliance_changes and wanted(UnitAllianceChangedEvent):
            for unit, previous_alliance in changes.alliance_changes:
                emit(UnitAllianceChangedEvent(step, unit, previous_alliance))
        if changes.created and wanted(OwnConstructionStartedEvent):
            for unit in changes.created:
                if not unit.is_complete and self._is_structure(unit):
                    emit(OwnConstructionStartedEvent(step, unit))
        if changes.finished:
            if wanted(OwnConstructionFinishedEvent):
                for unit in changes.finished:
                    if self._is_structure(unit):
                        emit(OwnConstructionFinishedEvent(step, unit))
            if wanted(OwnWarpInFinishedEvent):
                for unit in changes.finished:
                    if not self._is_structure(unit):
                        emit(OwnWarpInFinishedEvent(step, unit))
        if changes.upgrades and wanted(OwnUpgradeFinishedEvent):
            for upgrade in changes.upgrades:
                emit(OwnUpgradeFinishedEvent(step, upgrade))
        damage, energy = wanted(UnitDamagedEvent), wanted(UnitEnergyLostEvent)
        if damage or energy:
            self._report_vitals(events, step, damage=damage, energy=energy)
        elif self._vitals:
            self._vitals = {}
        if changes.entered_sight and wanted(EnemyUnitEnteredSightEvent):
            for unit in changes.entered_sight:
                emit(EnemyUnitEnteredSightEvent(step, unit))
        if changes.left_sight and wanted(EnemyUnitLeftSightEvent):
            for unit in changes.left_sight:
                emit(EnemyUnitLeftSightEvent(step, unit))
        if changes.died and wanted(UnitDiedEvent):
            for unit in changes.died:
                emit(UnitDiedEvent(step, unit))
        if changes.found_dead and wanted(UnitFoundDeadEvent):
            for unit in changes.found_dead:
                emit(UnitFoundDeadEvent(step, unit))
        if observation.actions and wanted(OwnActionEvent):
            for action in state.actions:
                emit(OwnActionEvent(step, action))
        if observation.chat and wanted(ChatEvent):
            for message in observation.chat:
                emit(ChatEvent(step, message.player_id, message.message))
        if wanted(AlertEvent):
            for value in observation.observation.alerts:
                # `AlertError` and `TrainError` have no member, and are passed over.
                if (alert := _ALERTS.get(value)) is not None:
                    emit(AlertEvent(step, alert))

    def _is_structure(self, unit: Unit[Any]) -> bool:
        """Whether the game's tables give the type of `unit` the structure attribute."""
        row = self._tracker.data.units.get(unit.type_id)
        return row is not None and Attribute.STRUCTURE in row.attributes

    def _report_vitals(self, events: EventBus, step: int, *, damage: bool, energy: bool) -> None:
        """Hand on, as asked, the health and shields and the energy each unit in vision lost since the tracker's
        update before, if this reporter kept them then, and keep them for the next."""
        updates = self._tracker.updates
        before = self._vitals if self._vitals_update == updates - 1 else None
        vitals: dict[int, tuple[int, float, float, float]] = {}
        damaged: list[UnitDamagedEvent] = []
        drained: list[UnitEnergyLostEvent] = []
        for unit in self._tracker.present_units:
            report = unit._latest_data
            if unit._latest_data_in_vision is not report:
                continue
            now = vitals[unit._id] = (report.unit_type, report.health, report.shield, report.energy)
            if before is None or (then := before.get(unit._id)) is None or then[0] != now[0]:
                continue
            if damage and (lost := max(0.0, then[1] - now[1]) + max(0.0, then[2] - now[2])) > 0.0:
                damaged.append(UnitDamagedEvent(step, unit, lost))
            if energy and now[3] < then[3]:
                drained.append(UnitEnergyLostEvent(step, unit, then[3] - now[3]))
        self._vitals = vitals
        self._vitals_update = updates
        for event in damaged:
            events._emit(event)
        for event in drained:
            events._emit(event)
