"""What each observation of a game reports has happened, handed on as events."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING

from sc2nachos.events import (
    AlertEvent,
    ChatEvent,
    EnemyUnitCloakChangedEvent,
    EnemyUnitDamagedEvent,
    EnemyUnitEnergyLostEvent,
    EnemyUnitEnteredSightEvent,
    EnemyUnitFirstSeenEvent,
    EnemyUnitLeftSightEvent,
    OwnActionEvent,
    OwnConstructionFinishedEvent,
    OwnConstructionStartedEvent,
    OwnUnitCloakChangedEvent,
    OwnUnitCreatedEvent,
    OwnUnitDamagedEvent,
    OwnUnitEnergyLostEvent,
    OwnUpgradeFinishedEvent,
    OwnWarpInFinishedEvent,
    UnitAllianceChangedEvent,
    UnitDiedEvent,
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


def _report(
    events: EventBus, tracker: _UnitTracker, observation: sc2api_pb2.ResponseObservation, state: _State, step: int
) -> None:
    """Hand on what `observation`, taken in at `step` and read as `state`, reports has happened, as events of the
    types with a handler still to run."""
    changes = tracker.last_changes
    wanted = events._has_handlers
    emit = events._emit
    if changes.own_units_created and wanted(OwnUnitCreatedEvent):
        for unit in changes.own_units_created:
            emit(OwnUnitCreatedEvent(step, unit))
    if changes.enemy_units_first_seen and wanted(EnemyUnitFirstSeenEvent):
        for unit in changes.enemy_units_first_seen:
            emit(EnemyUnitFirstSeenEvent(step, unit))
    if changes.units_type_changed and wanted(UnitTypeChangedEvent):
        for unit, previous_type in changes.units_type_changed:
            emit(UnitTypeChangedEvent(step, unit, previous_type))
    if changes.units_alliance_changed and wanted(UnitAllianceChangedEvent):
        for unit, previous_alliance in changes.units_alliance_changed:
            emit(UnitAllianceChangedEvent(step, unit, previous_alliance))
    if changes.own_units_created and wanted(OwnConstructionStartedEvent):
        for unit in changes.own_units_created:
            if not unit.is_complete and _is_structure(tracker, unit):
                emit(OwnConstructionStartedEvent(step, unit))
    if changes.own_units_finished:
        if wanted(OwnConstructionFinishedEvent):
            for unit in changes.own_units_finished:
                if _is_structure(tracker, unit):
                    emit(OwnConstructionFinishedEvent(step, unit))
        if wanted(OwnWarpInFinishedEvent):
            for unit in changes.own_units_finished:
                if not _is_structure(tracker, unit):
                    emit(OwnWarpInFinishedEvent(step, unit))
    if changes.own_upgrades_finished and wanted(OwnUpgradeFinishedEvent):
        for upgrade in changes.own_upgrades_finished:
            emit(OwnUpgradeFinishedEvent(step, upgrade))
    _report_compared(events, tracker, step)
    if changes.enemy_units_entered_sight and wanted(EnemyUnitEnteredSightEvent):
        for unit in changes.enemy_units_entered_sight:
            emit(EnemyUnitEnteredSightEvent(step, unit))
    if changes.enemy_units_left_sight and wanted(EnemyUnitLeftSightEvent):
        for unit in changes.enemy_units_left_sight:
            emit(EnemyUnitLeftSightEvent(step, unit))
    if changes.units_died and wanted(UnitDiedEvent):
        for unit in changes.units_died:
            emit(UnitDiedEvent(step, unit))
    if changes.units_found_dead and wanted(UnitFoundDeadEvent):
        for unit in changes.units_found_dead:
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


def _report_compared(events: EventBus, tracker: _UnitTracker, step: int) -> None:
    """Have `tracker` compare each unit with the update before, as the damage, energy and cloak events with a handler
    still to run need, and hand on what it found; have it let go of what it kept when none has."""
    wanted = events._has_handlers
    damage = wanted(OwnUnitDamagedEvent) or wanted(EnemyUnitDamagedEvent)
    energy = wanted(OwnUnitEnergyLostEvent) or wanted(EnemyUnitEnergyLostEvent)
    cloak = wanted(OwnUnitCloakChangedEvent) or wanted(EnemyUnitCloakChangedEvent)
    if not (damage or energy or cloak):
        tracker.stop_comparing_units()
        return
    tracker.compare_units(damage=damage, energy=energy, cloak=cloak)
    changes = tracker.last_changes
    emit = events._emit
    if changes.own_units_damaged and wanted(OwnUnitDamagedEvent):
        for own, lost in changes.own_units_damaged:
            emit(OwnUnitDamagedEvent(step, own, lost))
    if changes.enemy_units_damaged and wanted(EnemyUnitDamagedEvent):
        for unit, lost in changes.enemy_units_damaged:
            emit(EnemyUnitDamagedEvent(step, unit, lost))
    if changes.own_units_energy_lost and wanted(OwnUnitEnergyLostEvent):
        for own, lost in changes.own_units_energy_lost:
            emit(OwnUnitEnergyLostEvent(step, own, lost))
    if changes.enemy_units_energy_lost and wanted(EnemyUnitEnergyLostEvent):
        for unit, lost in changes.enemy_units_energy_lost:
            emit(EnemyUnitEnergyLostEvent(step, unit, lost))
    if changes.own_units_cloak_changed and wanted(OwnUnitCloakChangedEvent):
        for own, previous_cloak in changes.own_units_cloak_changed:
            emit(OwnUnitCloakChangedEvent(step, own, previous_cloak))
    if changes.enemy_units_cloak_changed and wanted(EnemyUnitCloakChangedEvent):
        for unit, previous_cloak in changes.enemy_units_cloak_changed:
            emit(EnemyUnitCloakChangedEvent(step, unit, previous_cloak))


def _is_structure(tracker: _UnitTracker, unit: Unit[Any]) -> bool:
    """Whether the game's tables give the type of `unit` the structure attribute."""
    row = tracker.data.units.get(unit.type_id)
    return row is not None and Attribute.STRUCTURE in row.attributes
