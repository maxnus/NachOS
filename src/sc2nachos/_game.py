"""One game as it is played, and what each of its observations reports has happened, handed on as events."""

import functools
from collections.abc import Callable, Hashable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final, Self, cast

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
    EnemyUnitEnteredAreaEvent,
    EnemyUnitEnteredSightEvent,
    EnemyUnitFirstSeenEvent,
    EnemyUnitGainedBuffEvent,
    EnemyUnitLeftAreaEvent,
    EnemyUnitLeftSightEvent,
    EnemyUnitLostBuffEvent,
    EnemyUnitVitalDroppedEvent,
    EnemyUnitVitalReachedEvent,
    Event,
    EventBus,
    OwnActionEvent,
    OwnConstructionFinishedEvent,
    OwnConstructionStartedEvent,
    OwnUnitCloakChangedEvent,
    OwnUnitCreatedEvent,
    OwnUnitDamagedEvent,
    OwnUnitEnergyLostEvent,
    OwnUnitEnteredAreaEvent,
    OwnUnitGainedBuffEvent,
    OwnUnitLeftAreaEvent,
    OwnUnitLostBuffEvent,
    OwnUnitVitalDroppedEvent,
    OwnUnitVitalReachedEvent,
    OwnUpgradeFinishedEvent,
    OwnWarpInFinishedEvent,
    ParameterizedEvent,
    UnitAllianceChangedEvent,
    UnitDiedEvent,
    UnitFoundDeadEvent,
    UnitTypeChangedEvent,
)
from sc2nachos.gamedata import Attribute, GameData
from sc2nachos.gamemap import GameMap
from sc2nachos.geometry import Area
from sc2nachos.ids import UnitTypeId
from sc2nachos.match import Result
from sc2nachos.protocol import Client
from sc2nachos.state import Alert
from sc2nachos.state._state import _State
from sc2nachos.units import Unit, VitalType
from sc2nachos.units._tracking import _Tracker
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
    tracker: Final[_Tracker]
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
        tracker = _Tracker(tables, enemy)
        game_map = GameMap(info)
        game = cls(
            client,
            game_map,
            tables,
            enemy,
            infer_enemy_upgrades,
            tracker,
            observation,
            _State(observation, tracker, game_map),
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
        self.tracker.update(observation.observation.raw_data, step)
        self.state = _State(observation, self.tracker, self.map)
        units, reader = self.tracker.units.present, self.tracker.upgrades.reader
        if self.infer_enemy_upgrades >= UpgradeInference.BASIC:
            self.enemy.assume_upgrades(*reader.read_basic_upgrades(units))
        if self.infer_enemy_upgrades >= UpgradeInference.INTERMEDIATE:
            self.enemy.assume_upgrades(*reader.read_intermediate_upgrades(units, self.state.effects))

    def report(self, events: EventBus) -> list[Event]:
        """What the last observation reports has happened, as the events a handler of `events` still to run this game
        wants, in the order they are handed out: every event of a type a handler takes whole, and those of the keys
        handlers select through `only` or `of`."""
        tracker, observation = self.tracker, self.observation
        changes = tracker.last_changes
        happened: list[Event] = []
        hand_on = functools.partial(self._hand_on, events, happened)
        hand_on(OwnUnitCreatedEvent, changes.own_units_created, _unit_type)
        hand_on(EnemyUnitFirstSeenEvent, changes.enemy_units_first_seen, _unit_type)
        hand_on(UnitTypeChangedEvent, changes.units_type_changed, _first_unit_type, tuples=True)
        hand_on(UnitAllianceChangedEvent, changes.units_alliance_changed, _first_unit_type, tuples=True)
        if changes.own_units_created and _wants(events, OwnConstructionStartedEvent):
            started = [unit for unit in changes.own_units_created if not unit.is_complete and self._is_structure(unit)]
            hand_on(OwnConstructionStartedEvent, started, _unit_type)
        if changes.own_units_finished:
            if _wants(events, OwnConstructionFinishedEvent):
                built = [unit for unit in changes.own_units_finished if self._is_structure(unit)]
                hand_on(OwnConstructionFinishedEvent, built, _unit_type)
            if _wants(events, OwnWarpInFinishedEvent):
                warped = [unit for unit in changes.own_units_finished if not self._is_structure(unit)]
                hand_on(OwnWarpInFinishedEvent, warped, _unit_type)
        hand_on(OwnUpgradeFinishedEvent, changes.own_upgrades_finished, _itself)
        compared, watched = self._compare(events), self._watch(events)
        own, enemy = changes.own, changes.enemy
        if compared:
            hand_on(OwnUnitDamagedEvent, own.damaged, _first_unit_type, tuples=True)
            hand_on(EnemyUnitDamagedEvent, enemy.damaged, _first_unit_type, tuples=True)
            hand_on(OwnUnitEnergyLostEvent, own.energy_lost, _first_unit_type, tuples=True)
            hand_on(EnemyUnitEnergyLostEvent, enemy.energy_lost, _first_unit_type, tuples=True)
        if watched:
            hand_on(OwnUnitVitalReachedEvent, own.vital_reached, _vital_key, tuples=True)
            hand_on(EnemyUnitVitalReachedEvent, enemy.vital_reached, _vital_key, tuples=True)
            hand_on(OwnUnitVitalDroppedEvent, own.vital_dropped, _vital_key, tuples=True)
            hand_on(EnemyUnitVitalDroppedEvent, enemy.vital_dropped, _vital_key, tuples=True)
        if compared:
            hand_on(OwnUnitCloakChangedEvent, own.cloak_changed, _first_unit_type, tuples=True)
            hand_on(EnemyUnitCloakChangedEvent, enemy.cloak_changed, _first_unit_type, tuples=True)
            hand_on(OwnUnitGainedBuffEvent, own.gained_buff, _second, tuples=True)
            hand_on(EnemyUnitGainedBuffEvent, enemy.gained_buff, _second, tuples=True)
            hand_on(OwnUnitLostBuffEvent, own.lost_buff, _second, tuples=True)
            hand_on(EnemyUnitLostBuffEvent, enemy.lost_buff, _second, tuples=True)
        hand_on(EnemyUnitEnteredSightEvent, changes.enemy_units_entered_sight, _unit_type)
        hand_on(EnemyUnitLeftSightEvent, changes.enemy_units_left_sight, _unit_type)
        if watched:
            hand_on(OwnUnitEnteredAreaEvent, own.entered_area, _second, tuples=True)
            hand_on(OwnUnitLeftAreaEvent, own.left_area, _second, tuples=True)
            hand_on(EnemyUnitEnteredAreaEvent, enemy.entered_area, _second, tuples=True)
            hand_on(EnemyUnitLeftAreaEvent, enemy.left_area, _second, tuples=True)
        hand_on(UnitDiedEvent, changes.units_died, _unit_type)
        hand_on(UnitFoundDeadEvent, changes.units_found_dead, _unit_type)
        emit, step = happened.append, self.step
        if observation.actions and events._has_handlers(OwnActionEvent):
            for action in self.state.actions:
                emit(OwnActionEvent(action, step=step))
        if observation.chat and events._has_handlers(ChatEvent):
            for message in observation.chat:
                emit(ChatEvent(message.player_id, message.message, step=step))
        if observation.observation.alerts and _wants(events, AlertEvent):
            every, keys = events._has_handlers(AlertEvent), events._wanted_keys(AlertEvent)
            for value in observation.observation.alerts:
                # `AlertError` and `TrainError` have no member, and are passed over.
                if (alert := _ALERTS.get(value)) is not None and (every or alert in keys):
                    emit(AlertEvent(alert, step=step))
        return happened

    def _compare(self, events: EventBus) -> bool:
        """Have the tracker compare each unit with the update before, as the damage, energy lost, cloak and buff events
        a handler wants need: the buffs handlers select, or every one if a handler takes them all. Have it let go of
        what it kept when no handler wants any. Say whether it compared."""
        comparer = self.tracker.comparer
        damage = _wants(events, OwnUnitDamagedEvent) or _wants(events, EnemyUnitDamagedEvent)
        energy = _wants(events, OwnUnitEnergyLostEvent) or _wants(events, EnemyUnitEnergyLostEvent)
        cloak = _wants(events, OwnUnitCloakChangedEvent) or _wants(events, EnemyUnitCloakChangedEvent)
        buff_events = (OwnUnitGainedBuffEvent, EnemyUnitGainedBuffEvent, OwnUnitLostBuffEvent, EnemyUnitLostBuffEvent)
        only_buffs: frozenset[Hashable] | None = None
        if any(events._has_handlers(event_type) for event_type in buff_events):
            buffs = True
        else:
            only_buffs = frozenset().union(*(events._wanted_keys(event_type) for event_type in buff_events))
            buffs = bool(only_buffs)
        if not (damage or energy or cloak or buffs):
            comparer.stop()
            return False
        comparer.compare(
            damage=damage, energy=energy, cloak=cloak, buffs=buffs, only_buffs=cast("frozenset[int] | None", only_buffs)
        )
        return True

    def _watch(self, events: EventBus) -> bool:
        """Have the tracker watch each unit for the vitals and areas handlers select, or let go of what it kept when
        none does. Say whether it watched."""
        watcher = self.tracker.watcher
        if not events._keyed:
            watcher.stop()
            return False
        keys = events._wanted_keys
        own_reached, own_dropped = keys(OwnUnitVitalReachedEvent), keys(OwnUnitVitalDroppedEvent)
        enemy_reached, enemy_dropped = keys(EnemyUnitVitalReachedEvent), keys(EnemyUnitVitalDroppedEvent)
        own_areas = keys(OwnUnitEnteredAreaEvent) | keys(OwnUnitLeftAreaEvent)
        enemy_areas = keys(EnemyUnitEnteredAreaEvent) | keys(EnemyUnitLeftAreaEvent)
        if not any((own_reached, own_dropped, enemy_reached, enemy_dropped, own_areas, enemy_areas)):
            watcher.stop()
            return False
        watcher.watch(
            own_reached=cast("frozenset[tuple[VitalType, float, UnitTypeId]]", own_reached),
            own_dropped=cast("frozenset[tuple[VitalType, float, UnitTypeId]]", own_dropped),
            enemy_reached=cast("frozenset[tuple[VitalType, float, UnitTypeId]]", enemy_reached),
            enemy_dropped=cast("frozenset[tuple[VitalType, float, UnitTypeId]]", enemy_dropped),
            own_areas=cast("frozenset[Area]", own_areas),
            enemy_areas=cast("frozenset[Area]", enemy_areas),
        )
        return True

    def _hand_on(
        self,
        events: EventBus,
        happened: list[Event],
        event_type: type[Event],
        found: Sequence[Any],
        key: Callable[[Any], Hashable],
        *,
        tuples: bool = False,
    ) -> None:
        """Add to `happened` an event of `event_type` made of each of `found` a handler of `events` wants, of a tuple's
        items if `tuples`: all of them if a handler takes every event of the type, or else those whose key, read by
        `key`, a handler selects. A parameterized event is made only for the keys selected, a handler of a base of it
        taking those."""
        if not found:
            return
        every = events._has_handlers(event_type) and not issubclass(event_type, ParameterizedEvent)
        keys = _NO_KEYS if every else events._wanted_keys(event_type)
        if not (every or keys):
            return
        add, step, make = happened.append, self.step, cast("Callable[..., Event]", event_type)
        for item in found:
            if every or key(item) in keys:
                add(make(*item, step=step) if tuples else make(item, step=step))

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


_NO_KEYS: frozenset[Hashable] = frozenset()


def _wants(events: EventBus, event_type: type[Event]) -> bool:
    """Whether a handler of `events` wants an event of `event_type`: every one, or one of some keys."""
    return events._has_handlers(event_type) or bool(events._wanted_keys(event_type))


def _itself[T](item: T) -> T:
    """`item`, the key of an event made of it alone."""
    return item


def _unit_type(unit: Unit[Any]) -> Hashable:
    """The type of `unit`, the key of an event of it."""
    return unit.type_id


def _first_unit_type(pair: tuple[Unit[Any], object]) -> Hashable:
    """The type of the unit `pair` starts with, the key of an event of it."""
    return pair[0].type_id


def _second(pair: tuple[object, Hashable]) -> Hashable:
    """What `pair` holds beside its unit, the key of an event of it: a buff or an area."""
    return pair[1]


def _vital_key(crossed: tuple[Unit[Any], VitalType, float]) -> Hashable:
    """The vital, the value and the type of the unit of `crossed`, the key of an event of crossing it."""
    unit, vital, value = crossed
    return vital, value, unit.type_id
