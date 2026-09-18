"""One game as it is played, and what each of its observations reports has happened, handed on as events."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final, Self

from loguru import logger
from s2clientprotocol import sc2api_pb2

from sc2nachos.constants import steps_to_seconds
from sc2nachos.enemy import Enemy
from sc2nachos.events import (
    AlertEvent,
    ChatEvent,
    EnemyUnitCloakChangedEvent,
    EnemyUnitDamagedEvent,
    EnemyUnitEnergyLostEvent,
    EnemyUnitEnteredSightEvent,
    EnemyUnitFirstSeenEvent,
    EnemyUnitGainedBuffEvent,
    EnemyUnitLeftSightEvent,
    EnemyUnitLostBuffEvent,
    EventBus,
    OwnActionEvent,
    OwnConstructionFinishedEvent,
    OwnConstructionStartedEvent,
    OwnUnitCloakChangedEvent,
    OwnUnitCreatedEvent,
    OwnUnitDamagedEvent,
    OwnUnitEnergyLostEvent,
    OwnUnitGainedBuffEvent,
    OwnUnitLostBuffEvent,
    OwnUpgradeFinishedEvent,
    OwnWarpInFinishedEvent,
    UnitAllianceChangedEvent,
    UnitDiedEvent,
    UnitFoundDeadEvent,
    UnitTypeChangedEvent,
)
from sc2nachos.gamedata import Attribute, GameData
from sc2nachos.gamemap import GameMap
from sc2nachos.match import Result
from sc2nachos.protocol import Client
from sc2nachos.state import Alert
from sc2nachos.state._state import _State
from sc2nachos.units import Unit
from sc2nachos.units._tracker import _UnitTracker
from sc2nachos.upgrade_reader import UpgradeInference

# Each alert by the protocol's value.
_ALERTS: Mapping[int, Alert] = MappingProxyType({int(alert): alert for alert in Alert})


@dataclass(slots=True)
class _Game:
    """One game as it is played: the client it is played on, and everything seen of it so far."""

    client: Final[Client]
    map: Final[GameMap]
    data: Final[GameData]
    enemy: Final[Enemy]
    infer_enemy_upgrades: Final[UpgradeInference]
    unit_tracker: Final[_UnitTracker]
    observation: sc2api_pb2.ResponseObservation
    state: _State
    # Kept beside the observation, because reading it out of the protobuf costs over ten times as much.
    step: int
    result: Result | None = None

    @classmethod
    def start(cls, client: Client, *, infer_enemy_upgrades: UpgradeInference) -> Self:
        """Start on the game `client` has joined: ask once for its map and pre-upgrade tables, and observe it."""
        info, data = client.game_info(), client.game_data()
        observation = client.observation()
        step = _step(observation)
        tables = GameData(data)
        enemy = Enemy()
        unit_tracker = _UnitTracker(tables, enemy)
        game_map = GameMap(info)
        game = cls(
            client,
            game_map,
            tables,
            enemy,
            infer_enemy_upgrades,
            unit_tracker,
            observation,
            _State(observation, unit_tracker, game_map),
            step,
        )
        game._take_in(observation, step)
        return game

    def observe(self, step: int | None = None) -> None:
        """Observe the game now, or once it reaches `step`."""
        observation = self.client.observation(game_loop=step)
        self._take_in(observation, _step(observation))

    def _take_in(self, observation: sc2api_pb2.ResponseObservation, step: int) -> None:
        """Read `observation`: its units, what they show of the enemy's upgrades if told to, and all else it reports."""
        self.observation = observation
        self.step = step
        self.unit_tracker.update(observation.observation.raw_data, step)
        self.state = _State(observation, self.unit_tracker, self.map)
        units, reader = self.unit_tracker.present_units, self.unit_tracker.upgrade_reader
        if self.infer_enemy_upgrades >= UpgradeInference.BASIC:
            self.enemy.assume_upgrades(*reader.read_basic_upgrades(units))
        if self.infer_enemy_upgrades >= UpgradeInference.INTERMEDIATE:
            self.enemy.assume_upgrades(*reader.read_intermediate_upgrades(units, self.state.effects))

    def report(self, events: EventBus) -> None:
        """Hand on to the handlers of `events` what the last observation reports has happened, as events of the types
        with a handler still to run."""
        tracker, observation, state, step = self.unit_tracker, self.observation, self.state, self.step
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
                if not unit.is_complete and self._is_structure(unit):
                    emit(OwnConstructionStartedEvent(step, unit))
        if changes.own_units_finished:
            if wanted(OwnConstructionFinishedEvent):
                for unit in changes.own_units_finished:
                    if self._is_structure(unit):
                        emit(OwnConstructionFinishedEvent(step, unit))
            if wanted(OwnWarpInFinishedEvent):
                for unit in changes.own_units_finished:
                    if not self._is_structure(unit):
                        emit(OwnWarpInFinishedEvent(step, unit))
        if changes.own_upgrades_finished and wanted(OwnUpgradeFinishedEvent):
            for upgrade in changes.own_upgrades_finished:
                emit(OwnUpgradeFinishedEvent(step, upgrade))
        self._report_compared(events)
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

    def _report_compared(self, events: EventBus) -> None:
        """Have the tracker compare each unit with the update before, as the damage, energy, cloak and buff events with
        a handler still to run need, and hand on what it found; have it let go of what it kept when none has."""
        tracker, step = self.unit_tracker, self.step
        wanted = events._has_handlers
        damage = wanted(OwnUnitDamagedEvent) or wanted(EnemyUnitDamagedEvent)
        energy = wanted(OwnUnitEnergyLostEvent) or wanted(EnemyUnitEnergyLostEvent)
        cloak = wanted(OwnUnitCloakChangedEvent) or wanted(EnemyUnitCloakChangedEvent)
        buff_events = (OwnUnitGainedBuffEvent, EnemyUnitGainedBuffEvent, OwnUnitLostBuffEvent, EnemyUnitLostBuffEvent)
        buffs = any(wanted(event_type) for event_type in buff_events)
        if not (damage or energy or cloak or buffs):
            tracker.comparison.stop()
            return
        tracker.comparison.compare(damage=damage, energy=energy, cloak=cloak, buffs=buffs)
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
        if changes.own_units_gained_buff and wanted(OwnUnitGainedBuffEvent):
            for own, buff in changes.own_units_gained_buff:
                emit(OwnUnitGainedBuffEvent(step, own, buff))
        if changes.enemy_units_gained_buff and wanted(EnemyUnitGainedBuffEvent):
            for unit, buff in changes.enemy_units_gained_buff:
                emit(EnemyUnitGainedBuffEvent(step, unit, buff))
        if changes.own_units_lost_buff and wanted(OwnUnitLostBuffEvent):
            for own, buff in changes.own_units_lost_buff:
                emit(OwnUnitLostBuffEvent(step, own, buff))
        if changes.enemy_units_lost_buff and wanted(EnemyUnitLostBuffEvent):
            for unit, buff in changes.enemy_units_lost_buff:
                emit(EnemyUnitLostBuffEvent(step, unit, buff))

    def _is_structure(self, unit: Unit[Any]) -> bool:
        """Whether the game's tables give the type of `unit` the structure attribute."""
        row = self.data.units.get(unit.type_id)
        return row is not None and Attribute.STRUCTURE in row.attributes

    def outcome(self) -> Result | None:
        """How the game ended as of the last observation, settled once it has, or `None` while it goes on."""
        if (result := self.client.result) is not None:
            return self.finish(result)
        if not self.client.in_game:
            # Over, and the game would not say how even when the client asked it again.
            return self.finish(Result.UNDECIDED)
        return None

    def finish(self, result: Result) -> Result:
        """Settle how the game ended, and say so."""
        self.result = result
        seconds = steps_to_seconds(self.step)
        logger.info("The game ended in a {} at step {}, {:.0f} seconds in", result, self.step, seconds)
        return result


def _step(observation: sc2api_pb2.ResponseObservation) -> int:
    """The step `observation` was made at."""
    # The protocol's game loop is what NachOS calls a step, and this is the one place the two meet.
    return observation.observation.game_loop
