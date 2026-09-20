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
from sc2nachos.events._event_subscriptions import _EventSubscriptions
from sc2nachos.gamedata import Attribute, GameData
from sc2nachos.gamemap import GameMap
from sc2nachos.geometry import Area
from sc2nachos.ids import UnitTypeId
from sc2nachos.match import Result
from sc2nachos.orders import OrderBook
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
    orders: Final[OrderBook]
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
            OrderBook(tables),
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
        self.orders._take_in(self.state, step)
        units, reader = self.tracker.units.present, self.tracker.upgrades.reader
        if self.infer_enemy_upgrades >= UpgradeInference.BASIC:
            self.enemy.assume_upgrades(*reader.read_basic_upgrades(units))
        if self.infer_enemy_upgrades >= UpgradeInference.INTERMEDIATE:
            self.enemy.assume_upgrades(*reader.read_intermediate_upgrades(units, self.state.effects))

    def report(self, events: EventBus) -> list[Event]:
        """What the last observation reports has happened, as the events a handler of `events` still to run this game
        wants, in the order they are handed out: every event of a type a handler takes whole, and those of the keys
        handlers select through `only` or `of`."""
        tracker, observation, subscriptions = self.tracker, self.observation, events._subscriptions
        changes = tracker.last_changes
        happened: list[Event] = []
        hand_on = functools.partial(self._hand_on, subscriptions, happened)
        hand_on(OwnUnitCreatedEvent, changes.own_units_created)
        hand_on(EnemyUnitFirstSeenEvent, changes.enemy_units_first_seen)
        hand_on(UnitTypeChangedEvent, changes.units_type_changed, tuples=True)
        hand_on(UnitAllianceChangedEvent, changes.units_alliance_changed, tuples=True)
        if changes.own_units_created and _wants_any(subscriptions, OwnConstructionStartedEvent):
            started = [unit for unit in changes.own_units_created if not unit.is_complete and self._is_structure(unit)]
            hand_on(OwnConstructionStartedEvent, started)
        if changes.own_units_finished:
            if _wants_any(subscriptions, OwnConstructionFinishedEvent):
                hand_on(OwnConstructionFinishedEvent, [u for u in changes.own_units_finished if self._is_structure(u)])
            if _wants_any(subscriptions, OwnWarpInFinishedEvent):
                warped = [unit for unit in changes.own_units_finished if not self._is_structure(unit)]
                hand_on(OwnWarpInFinishedEvent, warped)
        hand_on(OwnUpgradeFinishedEvent, changes.own_upgrades_finished)
        compared, watched = self._compare(subscriptions), self._watch(subscriptions)
        own, enemy = changes.own, changes.enemy
        if compared:
            hand_on(OwnUnitDamagedEvent, own.damaged, tuples=True)
            hand_on(EnemyUnitDamagedEvent, enemy.damaged, tuples=True)
            hand_on(OwnUnitEnergyLostEvent, own.energy_lost, tuples=True)
            hand_on(EnemyUnitEnergyLostEvent, enemy.energy_lost, tuples=True)
        if watched:
            hand_on(OwnUnitVitalReachedEvent, own.vital_reached, tuples=True)
            hand_on(EnemyUnitVitalReachedEvent, enemy.vital_reached, tuples=True)
            hand_on(OwnUnitVitalDroppedEvent, own.vital_dropped, tuples=True)
            hand_on(EnemyUnitVitalDroppedEvent, enemy.vital_dropped, tuples=True)
        if compared:
            hand_on(OwnUnitCloakChangedEvent, own.cloak_changed, tuples=True)
            hand_on(EnemyUnitCloakChangedEvent, enemy.cloak_changed, tuples=True)
            hand_on(OwnUnitGainedBuffEvent, own.gained_buff, tuples=True)
            hand_on(EnemyUnitGainedBuffEvent, enemy.gained_buff, tuples=True)
            hand_on(OwnUnitLostBuffEvent, own.lost_buff, tuples=True)
            hand_on(EnemyUnitLostBuffEvent, enemy.lost_buff, tuples=True)
        hand_on(EnemyUnitEnteredSightEvent, changes.enemy_units_entered_sight)
        hand_on(EnemyUnitLeftSightEvent, changes.enemy_units_left_sight)
        if watched:
            hand_on(OwnUnitEnteredAreaEvent, own.entered_area, tuples=True)
            hand_on(OwnUnitLeftAreaEvent, own.left_area, tuples=True)
            hand_on(EnemyUnitEnteredAreaEvent, enemy.entered_area, tuples=True)
            hand_on(EnemyUnitLeftAreaEvent, enemy.left_area, tuples=True)
        hand_on(UnitDiedEvent, changes.units_died)
        hand_on(UnitFoundDeadEvent, changes.units_found_dead)
        add, step = happened.append, self.step
        if observation.actions and subscriptions.wants_every(OwnActionEvent):
            for action in self.state.actions:
                add(OwnActionEvent(action, step=step))
        if observation.chat and subscriptions.wants_every(ChatEvent):
            for message in observation.chat:
                add(ChatEvent(message.player_id, message.message, step=step))
        if observation.observation.alerts and _wants_any(subscriptions, AlertEvent):
            every, keys = subscriptions.wants_every(AlertEvent), subscriptions.wanted_keys(AlertEvent)
            for value in observation.observation.alerts:
                # `AlertError` and `TrainError` have no member, and are passed over.
                if (alert := _ALERTS.get(value)) is not None and (every or AlertEvent._key_of(alert) in keys):
                    add(AlertEvent(alert, step=step))
        return happened

    def _compare(self, subscriptions: _EventSubscriptions) -> bool:
        """Have the tracker compare each unit with the update before, as the damage, energy lost, cloak and buff events
        a handler wants need: the buffs handlers select, or every one if a handler takes them all. Have it let go of
        what it kept when no handler wants any. Say whether it compared."""
        comparer = self.tracker.comparer
        wants = functools.partial(_wants_any, subscriptions)
        damage = wants(OwnUnitDamagedEvent) or wants(EnemyUnitDamagedEvent)
        energy = wants(OwnUnitEnergyLostEvent) or wants(EnemyUnitEnergyLostEvent)
        cloak = wants(OwnUnitCloakChangedEvent) or wants(EnemyUnitCloakChangedEvent)
        buff_events = (OwnUnitGainedBuffEvent, EnemyUnitGainedBuffEvent, OwnUnitLostBuffEvent, EnemyUnitLostBuffEvent)
        only_buffs: frozenset[Hashable] | None = None
        if any(subscriptions.wants_every(event_type) for event_type in buff_events):
            buffs = True
        else:
            only_buffs = frozenset().union(*(subscriptions.wanted_keys(event_type) for event_type in buff_events))
            buffs = bool(only_buffs)
        if not (damage or energy or cloak or buffs):
            comparer.stop()
            return False
        comparer.compare(
            damage=damage, energy=energy, cloak=cloak, buffs=buffs, only_buffs=cast("frozenset[int] | None", only_buffs)
        )
        return True

    def _watch(self, subscriptions: _EventSubscriptions) -> bool:
        """Have the tracker watch each unit for the vitals and areas handlers select, or let go of what it kept when
        none does. Say whether it watched."""
        watcher = self.tracker.watcher
        if not subscriptions.any_keyed:
            watcher.stop()
            return False
        keys = subscriptions.wanted_keys
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
        subscriptions: _EventSubscriptions,
        happened: list[Event],
        event_type: type[Event],
        found: Sequence[Any],
        *,
        tuples: bool = False,
    ) -> None:
        """Add to `happened` an event of `event_type` made of each of `found` a handler wants, of a tuple's items if
        `tuples`: all of them if a handler takes every event of the type, or else those whose key a handler selects.
        A parameterized event is made only for the keys selected, a handler of a base of it taking those."""
        if not found:
            return
        every = subscriptions.wants_every(event_type) and not issubclass(event_type, ParameterizedEvent)
        keys = _NO_KEYS if every else subscriptions.wanted_keys(event_type)
        if not (every or keys):
            return
        add, step, key_of = happened.append, self.step, event_type._key_of
        make = cast("Callable[..., Event]", event_type)
        for item in found:
            if tuples:
                if every or key_of(*item) in keys:
                    add(make(*item, step=step))
            elif every or key_of(item) in keys:
                add(make(item, step=step))

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


def _wants_any(subscriptions: _EventSubscriptions, event_type: type[Event]) -> bool:
    """Whether a handler of `subscriptions` wants an event of `event_type`: every one, or one of some keys."""
    return subscriptions.wants_every(event_type) or bool(subscriptions.wanted_keys(event_type))
