"""One game as it is played, and the events each observation reports."""

import functools
import itertools
from collections.abc import Callable, Hashable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Final, Self, cast

from loguru import logger
from s2clientprotocol import sc2api_pb2

from sc2nachos._fog_off_playback import _FogOffPlayback
from sc2nachos.camera import Camera
from sc2nachos.debug import DebugCommands
from sc2nachos.enemy import Enemy, UpgradeInference
from sc2nachos.events import (
    AlertEvent,
    AnyChatEvent,
    EnemyRaceKnownEvent,
    EnemyUnitCloakChangedEvent,
    EnemyUnitDamagedEvent,
    EnemyUnitDiedEvent,
    EnemyUnitEnergyLostEvent,
    EnemyUnitEnteredAreaEvent,
    EnemyUnitEnteredSightEvent,
    EnemyUnitFirstSeenEvent,
    EnemyUnitGainedBuffEvent,
    EnemyUnitLeftAreaEvent,
    EnemyUnitLeftSightEvent,
    EnemyUnitLostBuffEvent,
    EnemyUnitTypeChangedEvent,
    EnemyUnitVitalDroppedEvent,
    EnemyUnitVitalReachedEvent,
    Event,
    NeutralUnitDiedEvent,
    OpponentChatEvent,
    OwnActionEvent,
    OwnChatEvent,
    OwnConstructionFinishedEvent,
    OwnConstructionStartedEvent,
    OwnUnitBecameIdleEvent,
    OwnUnitCloakChangedEvent,
    OwnUnitCreatedEvent,
    OwnUnitDamagedEvent,
    OwnUnitDiedEvent,
    OwnUnitEnergyLostEvent,
    OwnUnitEnteredAreaEvent,
    OwnUnitGainedBuffEvent,
    OwnUnitLeftAreaEvent,
    OwnUnitLostBuffEvent,
    OwnUnitTypeChangedEvent,
    OwnUnitVitalDroppedEvent,
    OwnUnitVitalReachedEvent,
    OwnUpgradeFinishedEvent,
    OwnUpgradeStartedEvent,
    OwnUpgradeStoppedEvent,
    OwnWarpInFinishedEvent,
    OwnWarpInStartedEvent,
    ParameterizedEvent,
    UnitAllianceChangedEvent,
    UnitFoundDeadEvent,
)
from sc2nachos.events._event_bus import _EventBus
from sc2nachos.events._event_subscriptions import _EventSubscriptions
from sc2nachos.game_time import GameTime
from sc2nachos.gamedata import Attribute, GameData
from sc2nachos.gamemap import Expansion, GameMap
from sc2nachos.geometry import Area
from sc2nachos.ids import UnitTypeId, UpgradeId
from sc2nachos.match import Race, Result
from sc2nachos.orders import OrderBook
from sc2nachos.pathing import Pathing
from sc2nachos.placement import PlacementGrids
from sc2nachos.player import Player
from sc2nachos.protocol import Client
from sc2nachos.state import Alert, Research
from sc2nachos.state._state import _State
from sc2nachos.units import Alliance, OwnUnit, Unit, VitalType
from sc2nachos.units._snapshot import _Snapshot
from sc2nachos.units._tracking import _Tracker

_ALERT_BY_VALUE: Mapping[int, Alert] = MappingProxyType({int(alert): alert for alert in Alert})
# The event a happening comes as, by the alliance of the unit it happened to. An alliance left out gets none.
type _EventTypeByAlliance = Mapping[Alliance, type[Event]]
_TYPE_CHANGED_EVENT_BY_ALLIANCE: Final[_EventTypeByAlliance] = MappingProxyType(
    {Alliance.OWN: OwnUnitTypeChangedEvent, Alliance.ENEMY: EnemyUnitTypeChangedEvent}
)
_DIED_EVENT_BY_ALLIANCE: Final[_EventTypeByAlliance] = MappingProxyType(
    {Alliance.OWN: OwnUnitDiedEvent, Alliance.ENEMY: EnemyUnitDiedEvent, Alliance.NEUTRAL: NeutralUnitDiedEvent}
)
_CHAT_EVENT_TYPES: Final[tuple[type[AnyChatEvent], ...]] = (OwnChatEvent, OpponentChatEvent)


@dataclass(slots=True, kw_only=True)
class _Game:
    """One game as it is played: its client, and everything seen of it so far."""

    client: Final[Client]
    game_map: Final[GameMap]
    expansions: Final[tuple[Expansion, ...]]
    game_data: Final[GameData]
    player: Final[Player]
    enemy: Final[Enemy | None]
    enemy_upgrade_inference: Final[UpgradeInference]
    tracker: Final[_Tracker]
    orders: Final[OrderBook]
    observation: sc2api_pb2.ResponseObservation
    state: _State
    placement: Final[PlacementGrids]
    pathing: Final[Pathing]
    camera: Final[Camera]
    debug: Final[DebugCommands]
    # In a replay played back with the fog off, what the player was shown of each observation, and every unit. `None`
    # in a game, and with the fog on.
    _fog_off_playback: Final[_FogOffPlayback | None] = None
    # The event each player's chat comes as, by player id: this player's own, and `None` for an observer's. Any other
    # player's is the opponent's.
    _chat_event_types: Final[Mapping[int, type[AnyChatEvent] | None]]
    # The ids of this player's units idle as `OwnUnitBecameIdleEvent` counts it, at the update they were taken.
    _idle_unit_ids: _Snapshot[frozenset[int]] = field(default_factory=lambda: _Snapshot(frozenset()))
    # The researches running or queued, by upgrade, at the update they were taken.
    _researching: _Snapshot[Mapping[UpgradeId, Research]] = field(default_factory=lambda: _Snapshot({}))
    # Whether a handler has asked to leave the game once the turn's requests have gone out.
    leaving: bool = False
    # Whether the enemy's race became known since the last report, which then hands out `EnemyRaceKnownEvent`.
    _enemy_race_to_report: bool = False
    # The turn's chat, in the order given, as the actions that send it.
    _chat_to_send: list[sc2api_pb2.Action] = field(default_factory=list)
    # Kept beside the observation: reading it out of the protobuf costs over ten times as much. The time is the step's,
    # made once for every event of the observation.
    step: int
    time: GameTime
    result: Result | None = None

    @classmethod
    def start(cls, client: Client, *, enemy_upgrade_inference: UpgradeInference, enemy_ladder_id: str | None) -> Self:
        """Start the game `client` has joined: fetch its map and pre-upgrade tables once, observe it, and find its
        expansions. `enemy_ladder_id` is the ladder's id for the enemy, in a ladder game."""
        info, data = client.game_info(), client.game_data()
        observation = client.observation()
        step = _step(observation)
        game_data = GameData(data)
        if (player_id := client.player_id) is None:
            raise ValueError("the client has joined no game")
        enemy = Enemy._from_game(info, player_id, ladder_id=enemy_ladder_id)
        fog_off_playback = None
        if client.fog_disabled:
            fog_off_playback = _FogOffPlayback(game_data, enemy)
            tracker = _Tracker(game_data, enemy, id_provider=fog_off_playback.id_of_unit)
            observation = fog_off_playback.read(observation, step)
        else:
            tracker = _Tracker(game_data, enemy)
        game_map = GameMap._from_game(info, observation)
        tracker.update(observation.observation.raw_data, step)
        present_units = tracker.unit_tracker.present
        order_book = OrderBook(game_data)
        state = _State(observation, tracker, game_map)
        game = cls(
            client=client,
            game_map=game_map,
            expansions=Expansion._find_all(game_map, tracker.unit_tracker),
            game_data=game_data,
            player=Player._from_game(info, player_id),
            enemy=enemy,
            enemy_upgrade_inference=enemy_upgrade_inference,
            tracker=tracker,
            orders=order_book,
            observation=observation,
            state=state,
            placement=PlacementGrids._build_at_start(
                game_map,
                present_units=present_units,
                state=state,
                step=step,
                game_data=game_data,
                order_book=order_book,
            ),
            pathing=Pathing(client),
            camera=Camera._from_player(observation.observation.raw_data.player),
            debug=DebugCommands(client, game_map, opponent_id=None if enemy is None else enemy.id, step=step),
            _chat_event_types=_chat_event_types_by_player(info, player_id),
            _enemy_race_to_report=enemy is not None and enemy.race is not None,
            step=step,
            time=GameTime(steps=step),
            _fog_off_playback=fog_off_playback,
        )
        game._read(observation, step)
        return game

    @property
    def whole_game_tracker(self) -> _Tracker | None:
        """Every unit of a replay played back with the fog off, each read in full, or `None` where the fog is on."""
        return None if self._fog_off_playback is None else self._fog_off_playback.whole_game_tracker

    def observe(self, step: int | None = None) -> None:
        """Observe the game now, or once it reaches `step`."""
        observation = self.client.observation(game_loop=step)
        step_observed = _step(observation)
        if self._fog_off_playback is not None:
            observation = self._fog_off_playback.read(observation, step_observed)
        self._take_in(observation, step_observed)

    def _take_in(self, observation: sc2api_pb2.ResponseObservation, step: int) -> None:
        """Read `observation`: its units, the enemy upgrades they show if inference is on, and the rest it reports."""
        self.tracker.update(observation.observation.raw_data, step)
        self._read(observation, step)

    def _read(self, observation: sc2api_pb2.ResponseObservation, step: int) -> None:
        """Read `observation`, whose units the tracker has taken in: the enemy upgrades they show if inference is on,
        and the rest it reports."""
        self.observation = observation
        self.step = step
        self.time = GameTime(steps=step)
        self.state = _State(observation, self.tracker, self.game_map)
        self.placement._update(self.tracker.unit_tracker.present, self.state, step)
        self.pathing._update(self.tracker.unit_tracker.present)
        self.camera._update(observation.observation.raw_data.player)
        self.debug._update(step)
        changes = self.tracker.last_changes
        changed_hands = (unit for unit, _ in changes.units_alliance_changed)
        lost = itertools.chain(changes.units_died, changes.units_found_dead, changed_hands)
        self.orders._observe(self.time, lost)
        if self.enemy is not None:
            self._read_enemy(self.enemy, changes.enemy_units_first_seen)

    def _read_enemy(self, enemy: Enemy, first_seen: Sequence[Unit[Any]]) -> None:
        """Read into `enemy` its race, if not known yet, from the units `first_seen`, and the upgrades its units show
        if inference is on."""
        if enemy.race is None:
            self._learn_enemy_race(enemy, first_seen)
        units = self.tracker.unit_tracker.present
        reader = self.tracker.upgrade_tracker.reader
        if self.enemy_upgrade_inference >= UpgradeInference.BASIC:
            enemy.assume_upgrades(*reader.read_basic_upgrades(units))
        if self.enemy_upgrade_inference >= UpgradeInference.INTERMEDIATE:
            enemy.assume_upgrades(*reader.read_intermediate_upgrades(units, self.state.effects))

    def _learn_enemy_race(self, enemy: Enemy, first_seen: Sequence[Unit[Any]]) -> None:
        """Take the race of the first of `first_seen` that has one as `enemy`'s."""
        for unit in first_seen:
            if (row := self.game_data.units.get(unit.type_id)) is not None and row.race is not Race.NONE:
                enemy._learn_race(row.race)
                self._enemy_race_to_report = True
                return

    def report(self, events: _EventBus) -> list[Event]:
        """The events the last observation reports, in the order they are handed out, limited to what a handler of
        `events` not yet done wants: every event of a type a handler takes whole, and those of the keys handlers select
        through `only` or `of`."""
        subscriptions = events._subscriptions
        happened: list[Event] = []
        self._report_enemy_race(subscriptions, happened)
        self._report_units_coming_and_changing(subscriptions, happened)
        self._report_construction_and_research(subscriptions, happened)
        self._report_what_units_went_through(subscriptions, happened)
        self._report_idle_units_and_deaths(subscriptions, happened)
        self._report_actions_chat_and_alerts(subscriptions, happened)
        return happened

    def _report_enemy_race(self, subscriptions: _EventSubscriptions, happened: list[Event]) -> None:
        """Add to `happened` the enemy's race, if it became known since the last report and a handler wants it."""
        enemy = self.enemy
        if not self._enemy_race_to_report or enemy is None or enemy.race is None:
            return
        self._enemy_race_to_report = False
        if subscriptions.wants_every(EnemyRaceKnownEvent):
            happened.append(EnemyRaceKnownEvent(enemy.race, time=self.time))

    def _report_units_coming_and_changing(self, subscriptions: _EventSubscriptions, happened: list[Event]) -> None:
        """Add to `happened` the units first seen, and those that changed type or sides, that a handler wants."""
        changes = self.tracker.last_changes
        add_wanted = functools.partial(self._add_wanted_events, subscriptions, happened)
        add_wanted(OwnUnitCreatedEvent, changes.own_units_created)
        add_wanted(EnemyUnitFirstSeenEvent, changes.enemy_units_first_seen)
        self._add_wanted_events_by_alliance(
            subscriptions, happened, _TYPE_CHANGED_EVENT_BY_ALLIANCE, changes.units_type_changed, tuples=True
        )
        add_wanted(UnitAllianceChangedEvent, changes.units_alliance_changed, tuples=True)

    def _report_construction_and_research(self, subscriptions: _EventSubscriptions, happened: list[Event]) -> None:
        """Add to `happened` the constructions and warp-ins started and finished, and the researches started, stopped
        and finished, that a handler wants."""
        changes = self.tracker.last_changes
        add_wanted = functools.partial(self._add_wanted_events, subscriptions, happened)
        if changes.own_units_created:
            if _wants_any(subscriptions, OwnConstructionStartedEvent):
                built = [u for u in changes.own_units_created if not u.is_complete and self._is_structure(u)]
                add_wanted(OwnConstructionStartedEvent, built)
            if _wants_any(subscriptions, OwnWarpInStartedEvent):
                warping = [u for u in changes.own_units_created if not u.is_complete and not self._is_structure(u)]
                add_wanted(OwnWarpInStartedEvent, warping)
        if changes.own_units_finished:
            if _wants_any(subscriptions, OwnConstructionFinishedEvent):
                finished = [unit for unit in changes.own_units_finished if self._is_structure(unit)]
                add_wanted(OwnConstructionFinishedEvent, finished)
            if _wants_any(subscriptions, OwnWarpInFinishedEvent):
                warped = [unit for unit in changes.own_units_finished if not self._is_structure(unit)]
                add_wanted(OwnWarpInFinishedEvent, warped)
        if _wants_any(subscriptions, OwnUpgradeStartedEvent) or _wants_any(subscriptions, OwnUpgradeStoppedEvent):
            started, stopped = self._researches_started_and_stopped()
            add_wanted(OwnUpgradeStartedEvent, started, tuples=True)
            add_wanted(OwnUpgradeStoppedEvent, stopped, tuples=True)
        add_wanted(OwnUpgradeFinishedEvent, changes.own_upgrades_finished)

    def _report_what_units_went_through(self, subscriptions: _EventSubscriptions, happened: list[Event]) -> None:
        """Add to `happened` what units went through since the observation before that a handler wants: damage,
        energy lost, vitals crossed, cloak and buffs changed, and sight and areas entered and left."""
        changes = self.tracker.last_changes
        add_wanted = functools.partial(self._add_wanted_events, subscriptions, happened)
        compared, watched = self._compare(subscriptions), self._watch(subscriptions)
        own, enemy = changes.own, changes.enemy
        if compared:
            add_wanted(OwnUnitDamagedEvent, own.damaged, tuples=True)
            add_wanted(EnemyUnitDamagedEvent, enemy.damaged, tuples=True)
            add_wanted(OwnUnitEnergyLostEvent, own.energy_lost, tuples=True)
            add_wanted(EnemyUnitEnergyLostEvent, enemy.energy_lost, tuples=True)
        if watched:
            add_wanted(OwnUnitVitalReachedEvent, own.vital_reached, tuples=True)
            add_wanted(EnemyUnitVitalReachedEvent, enemy.vital_reached, tuples=True)
            add_wanted(OwnUnitVitalDroppedEvent, own.vital_dropped, tuples=True)
            add_wanted(EnemyUnitVitalDroppedEvent, enemy.vital_dropped, tuples=True)
        if compared:
            add_wanted(OwnUnitCloakChangedEvent, own.cloak_changed, tuples=True)
            add_wanted(EnemyUnitCloakChangedEvent, enemy.cloak_changed, tuples=True)
            add_wanted(OwnUnitGainedBuffEvent, own.gained_buff, tuples=True)
            add_wanted(EnemyUnitGainedBuffEvent, enemy.gained_buff, tuples=True)
            add_wanted(OwnUnitLostBuffEvent, own.lost_buff, tuples=True)
            add_wanted(EnemyUnitLostBuffEvent, enemy.lost_buff, tuples=True)
        add_wanted(EnemyUnitEnteredSightEvent, changes.enemy_units_entered_sight)
        add_wanted(EnemyUnitLeftSightEvent, changes.enemy_units_left_sight)
        if watched:
            add_wanted(OwnUnitEnteredAreaEvent, own.entered_area, tuples=True)
            add_wanted(EnemyUnitEnteredAreaEvent, enemy.entered_area, tuples=True)
            add_wanted(OwnUnitLeftAreaEvent, own.left_area, tuples=True)
            add_wanted(EnemyUnitLeftAreaEvent, enemy.left_area, tuples=True)

    def _report_idle_units_and_deaths(self, subscriptions: _EventSubscriptions, happened: list[Event]) -> None:
        """Add to `happened` the units become idle, those the game reports dead and those found dead, that a handler
        wants."""
        changes = self.tracker.last_changes
        if _wants_any(subscriptions, OwnUnitBecameIdleEvent):
            self._add_wanted_events(subscriptions, happened, OwnUnitBecameIdleEvent, self._units_become_idle())
        self._add_wanted_events_by_alliance(subscriptions, happened, _DIED_EVENT_BY_ALLIANCE, changes.units_died)
        self._add_wanted_events(subscriptions, happened, UnitFoundDeadEvent, changes.units_found_dead)

    def _report_actions_chat_and_alerts(self, subscriptions: _EventSubscriptions, happened: list[Event]) -> None:
        """Add to `happened` this player's actions, the chat and the alerts of the last observation that a handler
        wants."""
        observation, add, game_time = self.observation, happened.append, self.time
        if observation.actions and subscriptions.wants_every(OwnActionEvent):
            for action in self.state.actions:
                add(OwnActionEvent(action, time=game_time))
        if observation.chat and any(subscriptions.wants_every(event_type) for event_type in _CHAT_EVENT_TYPES):
            for message in observation.chat:
                event_type = self._chat_event_type(message.player_id)
                if event_type is not None and subscriptions.wants_every(event_type):
                    add(event_type(message.player_id, message.message, time=game_time))
        if observation.observation.alerts and _wants_any(subscriptions, AlertEvent):
            every, keys = subscriptions.wants_every(AlertEvent), subscriptions.wanted_keys(AlertEvent)
            for value in observation.observation.alerts:
                # `AlertError` and `TrainError` have no member and are skipped.
                if (alert := _ALERT_BY_VALUE.get(value)) is not None and (every or AlertEvent._key_of(alert) in keys):
                    add(AlertEvent(alert, time=game_time))

    def _compare(self, subscriptions: _EventSubscriptions) -> bool:
        """Have the tracker compare each unit with the update before, for the damage, energy lost, cloak and buff
        events handlers want: the buffs handlers select, or every buff if a handler takes them all. Stop it when no
        handler wants any of these. Returns whether it compared."""
        comparer = self.tracker.unit_comparer
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
        """Have the tracker watch each unit for the vitals and areas handlers select, or stop it when none does. Returns
        whether it watched."""
        watcher = self.tracker.unit_watcher
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

    def _add_wanted_events(
        self,
        subscriptions: _EventSubscriptions,
        happened: list[Event],
        event_type: type[Event],
        found: Sequence[Any],
        *,
        tuples: bool = False,
    ) -> None:
        """Add to `happened` an event of `event_type` for each item of `found` a handler wants, made of the item, or of
        its items if `tuples`: every item if a handler takes every event of the type, else those whose key a handler
        selects. A parameterized event is made only for selected keys, even for a handler of one of its bases."""
        if not found:
            return
        every, keys = _wanted(subscriptions, event_type)
        if not (every or keys):
            return
        add, game_time, key_of = happened.append, self.time, event_type._key_of
        make = cast("Callable[..., Event]", event_type)
        for item in found:
            if tuples:
                if every or key_of(*item) in keys:
                    add(make(*item, time=game_time))
            elif every or key_of(item) in keys:
                add(make(item, time=game_time))

    def _add_wanted_events_by_alliance(
        self,
        subscriptions: _EventSubscriptions,
        happened: list[Event],
        event_types: _EventTypeByAlliance,
        found: Sequence[Any],
        *,
        tuples: bool = False,
    ) -> None:
        """Add to `happened`, for each item of `found` a handler wants, the event of `event_types` for the alliance its
        unit is on, made as `_add_wanted_events` makes it; a unit of an alliance `event_types` leaves out gets none. The
        unit is the item, or its first item if `tuples`."""
        if not found:
            return
        wanted = {alliance: _wanted(subscriptions, event_type) for alliance, event_type in event_types.items()}
        if not any(every or keys for every, keys in wanted.values()):
            return
        add, game_time = happened.append, self.time
        for item in found:
            made_of = item if tuples else (item,)
            alliance = made_of[0].alliance
            if (event_type := event_types.get(alliance)) is None:
                continue
            every, keys = wanted[alliance]
            if every or (keys and event_type._key_of(*made_of) in keys):
                add(cast("Callable[..., Event]", event_type)(*made_of, time=game_time))

    def _units_become_idle(self) -> list[OwnUnit[Any]]:
        """The units of this player's that are idle now and were not at the update before, by
        `OwnUnitBecameIdleEvent`'s reckoning, in the order the tracker lists them. Every idle unit counts as newly so
        when the update before was not looked at. Keeps the idle units for the next update: call it once an update."""
        update = self.tracker.last_changes.update
        before = self._idle_unit_ids.at(update - 1, default=frozenset())
        idle = [
            unit
            for unit in self.tracker.unit_tracker.present.own
            if unit.is_complete and unit.is_idle and not self.orders.issued_to(unit, include_sent=True)
        ]
        self._idle_unit_ids = _Snapshot(frozenset(unit.id for unit in idle), update)
        return [unit for unit in idle if unit.id not in before]

    def _researches_started_and_stopped(
        self,
    ) -> tuple[list[tuple[UpgradeId, OwnUnit[Any]]], list[tuple[UpgradeId, OwnUnit[Any]]]]:
        """The researches that show now and did not at the update before, and those that showed then and show nowhere
        now without having finished, each with its structure, in id order. None stopped, and every one showing started,
        when the update before was not looked at. Keeps the researches for the next update: call it once an update."""
        update = self.tracker.last_changes.update
        before = self._researching.at(update - 1, default={})
        now = self.state.production.researches
        self._researching = _Snapshot(now, update)
        finished = set(self.tracker.last_changes.own_upgrades_finished)
        started = [(upgrade, now[upgrade].unit) for upgrade in sorted(now.keys() - before.keys())]
        gone = sorted(before.keys() - now.keys() - finished)
        return started, [(upgrade, before[upgrade].unit) for upgrade in gone]

    def _chat_event_type(self, player_id: int) -> type[AnyChatEvent] | None:
        """The event a chat message of the player `player_id` comes as, or `None` for an observer's, which comes as
        none."""
        return self._chat_event_types.get(player_id, OpponentChatEvent)

    def _is_structure(self, unit: Unit[Any]) -> bool:
        """Whether the game's tables give the type of `unit` the structure attribute."""
        row = self.game_data.units.get(unit.type_id)
        return row is not None and Attribute.STRUCTURE in row.attributes

    def queue_chat(self, text: str, *, team_only: bool) -> None:
        """Send `text` to every player, or to this player's team only if `team_only`, with the turn's request."""
        channel = sc2api_pb2.ActionChat.Team if team_only else sc2api_pb2.ActionChat.Broadcast
        self._chat_to_send.append(sc2api_pb2.Action(action_chat=sc2api_pb2.ActionChat(channel=channel, message=text)))

    def send_turn(self) -> None:
        """Send what the turn's handlers asked for: the orders, then the camera's move and the chat, in one request,
        and after it the debug commands and drawing."""
        chat, self._chat_to_send = self._chat_to_send, []
        move = self.camera._create_move_action()
        self.orders._send(self.client, then=[*([] if move is None else [move]), *chat])
        self.debug._send()

    def outcome(self) -> Result | None:
        """How the game ended as of the last observation, or `None` while it goes on. Ending it settles the result."""
        if (result := self.client.result) is not None:
            return self.finish(result)
        if not self.client.in_game:
            # Over, and the game would not say how even when the client asked it again.
            return self.finish(Result.UNDECIDED)
        return None

    def finish(self, result: Result) -> Result:
        """Record `result` as how the game ended, and log it."""
        self.result = result
        logger.info("The game ended in a {} at step {}, {:.0f} seconds in", result, self.step, self.time.seconds)
        return result


def _step(observation: sc2api_pb2.ResponseObservation) -> int:
    """The step `observation` was made at."""
    # The protocol's game loop is NachOS's step; this is the one place the two meet.
    return observation.observation.game_loop


_NO_KEYS: frozenset[Hashable] = frozenset()


def _wanted(subscriptions: _EventSubscriptions, event_type: type[Event]) -> tuple[bool, frozenset[Hashable]]:
    """Whether a handler in `subscriptions` takes every event of `event_type`, and else the keys handlers select. A
    parameterized event is never taken whole."""
    every = subscriptions.wants_every(event_type) and not issubclass(event_type, ParameterizedEvent)
    return every, _NO_KEYS if every else subscriptions.wanted_keys(event_type)


def _chat_event_types_by_player(
    info: sc2api_pb2.ResponseGameInfo, player: int | None
) -> Mapping[int, type[AnyChatEvent] | None]:
    """The event the chat of each player of the game `info` describes comes as, for those whose chat is not the
    opponent's: `player`'s own, and none for an observer's."""
    event_types: dict[int, type[AnyChatEvent] | None] = {
        entry.player_id: None for entry in info.player_info if entry.type == sc2api_pb2.PlayerType.Observer
    }
    if player is not None:
        event_types[player] = OwnChatEvent
    return MappingProxyType(event_types)


def _wants_any(subscriptions: _EventSubscriptions, event_type: type[Event]) -> bool:
    """Whether a handler in `subscriptions` wants events of `event_type`, whole or by key."""
    return subscriptions.wants_every(event_type) or bool(subscriptions.wanted_keys(event_type))
