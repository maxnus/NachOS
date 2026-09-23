"""The events each observation produces, driven by observations written here."""

from collections.abc import Callable, Iterator, Sequence
from contextlib import closing, contextmanager
from typing import Any

import pytest
from s2clientprotocol import common_pb2, data_pb2, debug_pb2, raw_pb2, sc2api_pb2

from sc2nachos import _game
from sc2nachos.enemy import Enemy
from sc2nachos.events import (
    AlertEvent,
    AreaEvent,
    BuffEvent,
    ChatEvent,
    Done,
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
    EventFilter,
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
    UnitAllianceChangedEvent,
    UnitDiedEvent,
    UnitEvent,
    UnitFoundDeadEvent,
    UnitTypeChangedEvent,
    VitalEvent,
)
from sc2nachos.gamemap import GameMap
from sc2nachos.geometry import Area, Circle, Point, Rectangle, Tile, TileSet
from sc2nachos.ids import AbilityId, BuffId, UncuratedIdError, UnitTypeId, UpgradeId
from sc2nachos.launch import GameProcess, MapFile, MapNotFoundError
from sc2nachos.match import Computer, Difficulty, Participant, Race
from sc2nachos.protocol import Client, WebSocketTransport
from sc2nachos.state import Alert, CameraMove
from sc2nachos.state._state import _State
from sc2nachos.units import Alliance, CloakState, OwnUnit, Unit, UnitType, Visibility, VitalType
from sc2nachos.units._tracking import _Tracker
from support import (
    HAPPENINGS,
    RealGame,
    make_client,
    make_game_info,
    make_observation,
    make_tables,
    make_unit,
    played,
    record,
)

_ENEMY = Alliance.ENEMY
# Every alert the protocol names, in its order, and the two that no `Alert` stands for.
_EVERY_ALERT = [value.number for value in sc2api_pb2.Alert.DESCRIPTOR.values]
_PASSED_OVER = frozenset({sc2api_pb2.Alert.AlertError, sc2api_pb2.Alert.TrainError})
_STRUCTURE = [data_pb2.Attribute.Structure]
_TABLES = make_tables(
    data_pb2.UnitTypeData(unit_id=UnitTypeId.BARRACKS, attributes=_STRUCTURE),
    data_pb2.UnitTypeData(unit_id=UnitTypeId.SUPPLY_DEPOT, attributes=_STRUCTURE),
)


class _Game:
    """A game fed one observation at a time, each taken in and reported as `Api.play` does each turn."""

    def __init__(self, events: EventBus | None = None) -> None:
        self.events = events or EventBus()
        self.tracker = _Tracker(_TABLES, Enemy())
        self.map = GameMap(make_game_info())
        self.state: _State | None = None
        self._client, _ = make_client()
        self._game: _game._Game | None = None

    def observe(self, step: int, *units: raw_pb2.Unit, **fields: Any) -> dict[int, Unit[Any]]:
        """Take in an observation of `units` and `fields` at `step`, report it, and return the units by tag."""
        observation = make_observation(step, units=units, **fields)
        self._game = played(self._game, self._client, self.map, self.tracker, observation, self.events)
        self.state = self._game.state
        return {unit.tag: unit for unit in self.tracker.unit_tracker.present}


def _depot(tag: int, **fields: Any) -> raw_pb2.Unit:
    return make_unit(tag, UnitTypeId.SUPPLY_DEPOT, at=(30.5, 40.5), alliance=_ENEMY, build_progress=1.0, **fields)


def _everything(game: _Game) -> None:
    """Two turns: the first with the starting units, the second reporting one event of every kind."""
    game.observe(
        0,
        make_unit(1, health=45.0, energy=50.0, build_progress=1.0, buff_ids=[BuffId.MARINE_STIMMED]),
        make_unit(2, UnitTypeId.BARRACKS, build_progress=0.5),
        make_unit(3, UnitTypeId.ZEALOT, build_progress=0.5),
        make_unit(4, UnitTypeId.SIEGE_TANK, alliance=_ENEMY, health=100.0),
        make_unit(5, UnitTypeId.ZERGLING, alliance=_ENEMY),
        make_unit(6, alliance=_ENEMY),
        _depot(900, visibility=Visibility.IN_FOG),
        make_unit(8, build_progress=1.0),
        make_unit(12, UnitTypeId.GHOST, build_progress=1.0, cloak=CloakState.NOT_CLOAKED),
        make_unit(
            13,
            UnitTypeId.HIGH_TEMPLAR,
            alliance=_ENEMY,
            health=40.0,
            energy=100.0,
            buff_ids=[BuffId.SENTRY_GUARDIAN_SHIELD],
        ),
        make_unit(14, UnitTypeId.OBSERVER, alliance=_ENEMY, visibility=Visibility.INVISIBLE, cloak=CloakState.CLOAKED),
    )
    camera = raw_pb2.ActionRawCameraMove(center_world_space=common_pb2.Point(x=30.75, y=139.0))
    game.observe(
        16,
        make_unit(1, health=40.0, energy=25.0, build_progress=1.0),
        make_unit(2, UnitTypeId.BARRACKS, build_progress=1.0),
        make_unit(3, UnitTypeId.ZEALOT, build_progress=1.0),
        make_unit(4, UnitTypeId.SIEGE_TANK_SIEGED, alliance=_ENEMY, health=90.0),
        make_unit(6, alliance=Alliance.OWN),
        make_unit(9, UnitTypeId.ROACH, alliance=_ENEMY),
        make_unit(10, UnitTypeId.SCV, build_progress=1.0),
        make_unit(11, UnitTypeId.SUPPLY_DEPOT, build_progress=0.1),
        make_unit(
            12, UnitTypeId.GHOST, build_progress=1.0, cloak=CloakState.CLOAKED_ALLIED, buff_ids=[BuffId.GHOST_CLOAK]
        ),
        make_unit(
            13,
            UnitTypeId.HIGH_TEMPLAR,
            alliance=_ENEMY,
            health=30.0,
            energy=50.0,
            buff_ids=[BuffId.INFESTOR_FUNGAL_GROWTH],
        ),
        make_unit(14, UnitTypeId.OBSERVER, alliance=_ENEMY, cloak=CloakState.CLOAKED_DETECTED),
        dead=(8,),
        upgrades=[UpgradeId.STIMPACK],
        actions=[sc2api_pb2.Action(action_raw=raw_pb2.ActionRaw(camera_move=camera), game_loop=12)],
        chat=[(2, "gl hf")],
        alerts=_EVERY_ALERT,
    )


class TestWhatATurnReports:
    def test_one_of_everything_comes_grouped_by_type_in_order(self) -> None:
        game = _Game()
        seen = record(game.events, *HAPPENINGS)
        _everything(game)
        second = [type(event) for event in seen if event.step == 16]
        assert list(dict.fromkeys(second)) == list(HAPPENINGS)

    def test_the_first_turn_reports_the_units_the_game_starts_with(self) -> None:
        game = _Game()
        seen = record(game.events, *HAPPENINGS)
        game.observe(0, make_unit(1, build_progress=1.0), make_unit(2, UnitTypeId.BARRACKS, build_progress=0.5))
        units = game.tracker.unit_tracker.present
        assert [(type(event), event.unit) for event in seen] == [
            (OwnUnitCreatedEvent, units[0]),
            (OwnUnitCreatedEvent, units[1]),
            (OwnConstructionStartedEvent, units[1]),
        ]

    def test_each_event_carries_what_happened(self) -> None:
        game = _Game()
        seen = record(game.events, *HAPPENINGS)
        _everything(game)
        units = {unit.tag: unit for unit in game.tracker.unit_tracker._units_by_id.values()}
        own = {tag: unit for tag, unit in units.items() if isinstance(unit, OwnUnit)}
        at_16 = {type(event): event for event in seen if event.step == 16}
        assert at_16[UnitTypeChangedEvent] == UnitTypeChangedEvent(units[4], UnitTypeId.SIEGE_TANK, step=16)
        assert at_16[UnitAllianceChangedEvent] == UnitAllianceChangedEvent(units[6], _ENEMY, step=16)
        assert at_16[OwnConstructionFinishedEvent].unit is units[2]
        assert at_16[OwnWarpInFinishedEvent].unit is units[3]
        assert at_16[OwnUpgradeFinishedEvent] == OwnUpgradeFinishedEvent(UpgradeId.STIMPACK, step=16)
        assert at_16[OwnUnitDamagedEvent] == OwnUnitDamagedEvent(own[1], 5.0, step=16)
        assert at_16[EnemyUnitDamagedEvent] == EnemyUnitDamagedEvent(units[13], 10.0, step=16)
        assert at_16[OwnUnitEnergyLostEvent] == OwnUnitEnergyLostEvent(own[1], 25.0, step=16)
        assert at_16[EnemyUnitEnergyLostEvent] == EnemyUnitEnergyLostEvent(units[13], 50.0, step=16)
        assert at_16[OwnUnitCloakChangedEvent] == OwnUnitCloakChangedEvent(own[12], CloakState.NOT_CLOAKED, step=16)
        assert at_16[EnemyUnitCloakChangedEvent] == EnemyUnitCloakChangedEvent(units[14], CloakState.CLOAKED, step=16)
        assert at_16[OwnUnitGainedBuffEvent] == OwnUnitGainedBuffEvent(own[12], BuffId.GHOST_CLOAK, step=16)
        assert at_16[OwnUnitLostBuffEvent] == OwnUnitLostBuffEvent(own[1], BuffId.MARINE_STIMMED, step=16)
        gained = EnemyUnitGainedBuffEvent(units[13], BuffId.INFESTOR_FUNGAL_GROWTH, step=16)
        assert at_16[EnemyUnitGainedBuffEvent] == gained
        lost = EnemyUnitLostBuffEvent(units[13], BuffId.SENTRY_GUARDIAN_SHIELD, step=16)
        assert at_16[EnemyUnitLostBuffEvent] == lost
        assert at_16[EnemyUnitLeftSightEvent] == EnemyUnitLeftSightEvent(units[5], step=16)
        assert at_16[UnitDiedEvent] == UnitDiedEvent(units[8], step=16)
        assert at_16[UnitFoundDeadEvent] == UnitFoundDeadEvent(units[900], step=16)
        assert at_16[OwnActionEvent] == OwnActionEvent(CameraMove(12, Point((30.75, 139.0))), step=16)
        assert at_16[ChatEvent] == ChatEvent(2, "gl hf", step=16)
        alerts = [event.alert for event in seen if isinstance(event, AlertEvent)]
        assert alerts == [Alert(value) for value in _EVERY_ALERT if value not in _PASSED_OVER]

    def test_a_unit_created_and_killed_in_one_observation_reads_in_order(self) -> None:
        game = _Game()
        seen = record(game.events, UnitDiedEvent, OwnUnitCreatedEvent)
        game.observe(0)
        game.observe(16, make_unit(1), dead=(1,))
        assert [type(event) for event in seen] == [OwnUnitCreatedEvent, UnitDiedEvent]

    def test_every_alert_the_protocol_names_is_curated_but_the_two_passed_over(self) -> None:
        protocol = {value.number for value in sc2api_pb2.Alert.DESCRIPTOR.values}
        assert {int(alert) for alert in Alert} == protocol - _PASSED_OVER

    def test_an_alert_passed_over_reaches_no_handler(self) -> None:
        game = _Game()
        seen = record(game.events, *HAPPENINGS)
        game.observe(0, alerts=list(_PASSED_OVER))
        assert not seen


class TestOnlyWhatIsWanted:
    @pytest.mark.parametrize("wanted", HAPPENINGS, ids=lambda event_type: event_type.__name__)
    def test_an_event_is_made_only_for_a_type_with_a_handler(
        self, wanted: type[Event], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        game = _Game()
        made: list[type[Event]] = []
        hand_out = EventBus._hand_out

        def counted(bus: EventBus, events: Sequence[Event]) -> None:
            made.extend(type(event) for event in events)
            hand_out(bus, events)

        monkeypatch.setattr(EventBus, "_hand_out", counted)
        record(game.events, wanted)
        _everything(game)
        assert made and set(made) == {wanted}

    def test_with_no_handler_the_actions_are_never_read_and_no_units_are_compared(self) -> None:
        game = _Game()
        _everything(game)
        assert game.state is not None and "actions" not in vars(game.state)
        assert not game.tracker.unit_comparer._compared

    def test_a_handler_that_is_done_stops_the_units_being_compared(self) -> None:
        game = _Game()
        game.events.on(OwnUnitDamagedEvent)(lambda event: Done)
        game.observe(0, make_unit(1, health=45.0))
        game.observe(16, make_unit(1, health=40.0))
        game.observe(32, make_unit(1, health=35.0))
        assert not game.tracker.unit_comparer._compared


def _marine(health: float, *, shield: float = 0.0, **fields: Any) -> raw_pb2.Unit:
    return make_unit(1, alliance=_ENEMY, health=health, shield=shield, **fields)


class TestEnergy:
    def _lost(self, *units: raw_pb2.Unit) -> list[float]:
        """The energy reported lost as `units` are observed one after another."""
        game = _Game()
        seen = record(game.events, EnemyUnitEnergyLostEvent)
        for step, unit in enumerate(units):
            game.observe(step * 16, unit)
        return [event.energy_lost for event in seen]

    def test_energy_lost_is_what_went_net_of_what_regenerated(self) -> None:
        assert self._lost(_raven(100.0), _raven(50.5), _raven(51.0)) == [49.5]

    def test_energy_regenerating_or_kept_is_never_lost(self) -> None:
        assert not self._lost(_raven(100.0), _raven(100.0), _raven(101.0))

    def test_a_unit_that_changed_type_lost_none(self) -> None:
        assert not self._lost(_raven(100.0), _raven(50.0, unit_type=UnitTypeId.ORACLE))

    def test_a_unit_out_of_vision_in_either_observation_lost_none(self) -> None:
        assert not self._lost(_raven(100.0), _raven(50.0, visibility=Visibility.INVISIBLE), _raven(25.0))

    def test_energy_is_compared_for_a_handler_of_its_own_as_damage_is(self) -> None:
        game = _Game()
        seen = record(game.events, EnemyUnitEnergyLostEvent)
        game.observe(0, _raven(100.0))
        game.observe(16, _raven(25.0))
        assert [event.energy_lost for event in seen] == [75.0]
        assert game.tracker.unit_comparer._compared

    def test_a_unit_of_this_players_loses_energy_to_the_event_of_its_own(self) -> None:
        game = _Game()
        own, enemy = record(game.events, OwnUnitEnergyLostEvent), record(game.events, EnemyUnitEnergyLostEvent)
        game.observe(0, _raven(100.0, alliance=Alliance.OWN))
        game.observe(16, _raven(50.0, alliance=Alliance.OWN))
        assert [(event.unit, event.energy_lost) for event in own] == [(game.tracker.unit_tracker.present[0], 50.0)]
        assert not enemy


def _raven(energy: float, **fields: Any) -> raw_pb2.Unit:
    unit_type, alliance = fields.pop("unit_type", UnitTypeId.RAVEN), fields.pop("alliance", _ENEMY)
    return make_unit(1, unit_type, alliance=alliance, energy=energy, **fields)


class TestDamage:
    def _damage(self, *units: raw_pb2.Unit) -> list[float]:
        """The damage reported as `units` are observed one after another."""
        game = _Game()
        seen = record(game.events, EnemyUnitDamagedEvent)
        for step, unit in enumerate(units):
            game.observe(step * 16, unit)
        return [event.damage for event in seen]

    def test_damage_is_the_health_and_shields_lost_since_the_observation_before(self) -> None:
        assert self._damage(_marine(100.0, shield=50.0), _marine(90.0, shield=30.0)) == [30.0]

    def test_what_is_regained_offsets_only_its_own_loss(self) -> None:
        assert self._damage(_marine(100.0, shield=30.0), _marine(90.0, shield=35.0), _marine(95.0, shield=35.0)) == [
            10.0
        ]

    def test_a_unit_that_changed_type_took_no_damage(self) -> None:
        tank = UnitTypeId.SIEGE_TANK
        assert not self._damage(_marine(175.0, unit_type=tank), _marine(160.0, unit_type=UnitTypeId.SIEGE_TANK_SIEGED))

    def test_a_unit_out_of_vision_in_either_observation_took_none(self) -> None:
        hidden = Visibility.INVISIBLE
        assert not self._damage(_marine(45.0), _marine(40.0, visibility=hidden), _marine(30.0))

    def test_damage_goes_to_the_event_of_the_units_side_and_none_for_a_neutral_unit(self) -> None:
        game = _Game()
        own, enemy = record(game.events, OwnUnitDamagedEvent), record(game.events, EnemyUnitDamagedEvent)
        rock = UnitTypeId.DESTRUCTIBLE_2X4_HORIZONTAL_ROCK
        game.observe(
            0, _marine(45.0), make_unit(2, health=45.0), make_unit(3, rock, alliance=Alliance.NEUTRAL, health=2000.0)
        )
        game.observe(
            16, _marine(40.0), make_unit(2, health=30.0), make_unit(3, rock, alliance=Alliance.NEUTRAL, health=1000.0)
        )
        units = {unit.tag: unit for unit in game.tracker.unit_tracker.present}
        assert [(event.unit, event.damage) for event in own] == [(units[2], 15.0)]
        assert [(event.unit, event.damage) for event in enemy] == [(units[1], 5.0)]

    def test_a_handler_subscribed_mid_game_hears_of_damage_from_the_turn_after(self) -> None:
        game = _Game()
        game.observe(0, _marine(45.0))
        seen = record(game.events, EnemyUnitDamagedEvent)
        game.observe(16, _marine(40.0))
        game.observe(32, _marine(30.0))
        assert [(event.step, event.damage) for event in seen] == [(32, 10.0)]

    def test_after_a_gap_without_handlers_damage_starts_afresh(self) -> None:
        game = _Game()

        @game.events.on(EnemyUnitDamagedEvent)
        def handler(event: EnemyUnitDamagedEvent) -> None:
            pass

        game.observe(0, _marine(45.0))
        game.events.unsubscribe(handler)
        game.observe(16, _marine(40.0))
        seen = record(game.events, EnemyUnitDamagedEvent)
        game.observe(32, _marine(30.0))
        game.observe(48, _marine(25.0))
        assert [(event.step, event.damage) for event in seen] == [(48, 5.0)]


class TestCloak:
    def _changes(self, *units: raw_pb2.Unit | None) -> list[tuple[type[Event], CloakState]]:
        """The cloak changes reported as `units` are observed one after another; `None` is an observation without it."""
        game = _Game()
        seen = record(game.events, OwnUnitCloakChangedEvent, EnemyUnitCloakChangedEvent)
        for step, unit in enumerate(units):
            game.observe(step * 16, *([] if unit is None else [unit]))
        return [(type(event), event.previous_cloak_state) for event in seen]

    def test_a_unit_of_this_players_cloaking_and_uncloaking(self) -> None:
        assert self._changes(
            _ghost(CloakState.NOT_CLOAKED), _ghost(CloakState.CLOAKED_ALLIED), _ghost(CloakState.NOT_CLOAKED)
        ) == [
            (OwnUnitCloakChangedEvent, CloakState.NOT_CLOAKED),
            (OwnUnitCloakChangedEvent, CloakState.CLOAKED_ALLIED),
        ]

    def test_an_enemy_unit_coming_to_be_detected_and_no_longer(self) -> None:
        hidden = _observer(CloakState.CLOAKED, visibility=Visibility.INVISIBLE)
        assert self._changes(hidden, _observer(CloakState.CLOAKED_DETECTED), hidden) == [
            (EnemyUnitCloakChangedEvent, CloakState.CLOAKED),
            (EnemyUnitCloakChangedEvent, CloakState.CLOAKED_DETECTED),
        ]

    def test_a_unit_out_of_the_observation_in_between_changed_nothing(self) -> None:
        hidden = _observer(CloakState.CLOAKED, visibility=Visibility.INVISIBLE)
        assert not self._changes(hidden, None, _observer(CloakState.CLOAKED_DETECTED))

    def test_a_unit_that_changed_type_is_still_compared(self) -> None:
        sieged = _observer(CloakState.CLOAKED_DETECTED, unit_type=UnitTypeId.OBSERVER_SIEGED)
        assert self._changes(_observer(CloakState.CLOAKED, visibility=Visibility.INVISIBLE), sieged) == [
            (EnemyUnitCloakChangedEvent, CloakState.CLOAKED)
        ]


class TestBuffs:
    def _changes(self, *units: raw_pb2.Unit) -> list[tuple[str, BuffId]]:
        """The buffs reported gained and lost as `units` are observed one after another."""
        game = _Game()
        buff_events = (OwnUnitGainedBuffEvent, OwnUnitLostBuffEvent, EnemyUnitGainedBuffEvent, EnemyUnitLostBuffEvent)
        seen = record(game.events, *buff_events)
        for step, unit in enumerate(units):
            game.observe(step * 16, unit)
        return [(type(event).__name__, event.buff) for event in seen]

    def test_the_buffs_gained_then_those_lost_each_in_the_order_of_their_ids(self) -> None:
        stims, boosts = BuffId.MARINE_STIMMED, BuffId.MEDIVAC_BOOST
        before = _worn(BuffId.QUEEN_TRANSFUSED, BuffId.GHOST_CLOAK)
        after = _worn(boosts, stims, BuffId.GHOST_CLOAK)
        assert self._changes(before, after) == [
            ("OwnUnitGainedBuffEvent", stims),
            ("OwnUnitGainedBuffEvent", boosts),
            ("OwnUnitLostBuffEvent", BuffId.QUEEN_TRANSFUSED),
        ]

    def test_the_first_sight_of_a_unit_gains_it_nothing(self) -> None:
        assert not self._changes(_worn(BuffId.MARINE_STIMMED))

    def test_an_enemy_unit_coming_to_be_detected_gains_nothing(self) -> None:
        hidden = _worn(BuffId.GHOST_CLOAK, alliance=_ENEMY, visibility=Visibility.INVISIBLE, buff_ids=[])
        assert not self._changes(hidden, _worn(BuffId.GHOST_CLOAK, alliance=_ENEMY))

    def test_a_unit_that_changed_type_is_still_compared(self) -> None:
        sieged = _worn(BuffId.RAVEN_INTERFERENCE_MATRIX, unit_type=UnitTypeId.SIEGE_TANK_SIEGED)
        assert self._changes(_worn(unit_type=UnitTypeId.SIEGE_TANK), sieged) == [
            ("OwnUnitGainedBuffEvent", BuffId.RAVEN_INTERFERENCE_MATRIX)
        ]

    def test_a_buff_the_curated_ids_leave_out_raises(self) -> None:
        uncurated = 236
        with pytest.raises(UncuratedIdError):
            self._changes(_worn(), _worn(buff_ids=[uncurated]))


def _worn(*buffs: BuffId, **fields: Any) -> raw_pb2.Unit:
    fields.setdefault("buff_ids", list(buffs))
    return make_unit(1, fields.pop("unit_type", UnitTypeId.MARINE), **fields)


def _ghost(cloak: CloakState) -> raw_pb2.Unit:
    return make_unit(1, UnitTypeId.GHOST, cloak=cloak)


def _observer(cloak: CloakState, **fields: Any) -> raw_pb2.Unit:
    return make_unit(1, fields.pop("unit_type", UnitTypeId.OBSERVER), alliance=_ENEMY, cloak=cloak, **fields)


# For each event type `only` narrows, one key out of the several `_everything` reports for that type, or the one.
_ONE_KEY: list[EventFilter[Any]] = [
    OwnUnitCreatedEvent.only(UnitTypeId.BARRACKS),
    EnemyUnitFirstSeenEvent.only(UnitTypeId.ROACH),
    UnitTypeChangedEvent.only(UnitTypeId.SIEGE_TANK_SIEGED),
    UnitAllianceChangedEvent.only(UnitTypeId.MARINE),
    OwnConstructionStartedEvent.only(UnitTypeId.BARRACKS),
    OwnConstructionFinishedEvent.only(UnitTypeId.BARRACKS),
    OwnWarpInFinishedEvent.only(UnitTypeId.ZEALOT),
    OwnUpgradeFinishedEvent.only(UpgradeId.STIMPACK),
    OwnUnitDamagedEvent.only(UnitTypeId.MARINE),
    EnemyUnitDamagedEvent.only(UnitTypeId.HIGH_TEMPLAR),
    OwnUnitEnergyLostEvent.only(UnitTypeId.MARINE),
    EnemyUnitEnergyLostEvent.only(UnitTypeId.HIGH_TEMPLAR),
    OwnUnitCloakChangedEvent.only(UnitTypeId.GHOST),
    EnemyUnitCloakChangedEvent.only(UnitTypeId.OBSERVER),
    OwnUnitGainedBuffEvent.only(BuffId.GHOST_CLOAK),
    EnemyUnitGainedBuffEvent.only(BuffId.INFESTOR_FUNGAL_GROWTH),
    OwnUnitLostBuffEvent.only(BuffId.MARINE_STIMMED),
    EnemyUnitLostBuffEvent.only(BuffId.SENTRY_GUARDIAN_SHIELD),
    EnemyUnitEnteredSightEvent.only(UnitTypeId.ROACH),
    EnemyUnitLeftSightEvent.only(UnitTypeId.ZERGLING),
    UnitDiedEvent.only(UnitTypeId.MARINE),
    UnitFoundDeadEvent.only(UnitTypeId.SUPPLY_DEPOT),
    AlertEvent.only(Alert.RESEARCH_COMPLETE),
]


class TestOnlySomeKeys:
    @pytest.mark.parametrize("selected", _ONE_KEY, ids=lambda selected: selected.event_type.__name__)
    def test_an_event_is_made_only_for_the_keys_selected(
        self, selected: EventFilter[Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        game = _Game()
        made: list[Event] = []
        hand_out = EventBus._hand_out

        def counted(bus: EventBus, events: Sequence[Event]) -> None:
            made.extend(events)
            hand_out(bus, events)

        monkeypatch.setattr(EventBus, "_hand_out", counted)
        game.events.on(selected)(lambda event: None)
        _everything(game)
        assert made and {type(event) for event in made} == {selected.event_type}
        assert selected.keys is not None and all(event._key() in selected.keys for event in made)

    def test_a_type_change_is_selected_by_the_type_changed_to(self) -> None:
        game = _Game()
        seen = record(game.events, UnitTypeChangedEvent.only(UnitTypeId.SIEGE_TANK))
        _everything(game)
        assert not seen

    def test_a_buff_selected_is_the_only_one_compared_into_the_changes(self) -> None:
        game = _Game()
        game.events.on(OwnUnitLostBuffEvent.only(BuffId.MARINE_STIMMED))(lambda event: None)
        _everything(game)
        changes = game.tracker.last_changes
        assert [buff for _, buff in changes.own.lost_buff] == [BuffId.MARINE_STIMMED]
        assert not changes.own.gained_buff and not changes.enemy.gained_buff


def _templar(energy: float, **fields: Any) -> raw_pb2.Unit:
    tag, alliance = fields.pop("tag", 1), fields.pop("alliance", _ENEMY)
    return make_unit(tag, UnitTypeId.HIGH_TEMPLAR, alliance=alliance, energy=energy, energy_max=200.0, **fields)


def _zealot(health: float, shield: float, **fields: Any) -> raw_pb2.Unit:
    return make_unit(
        1,
        UnitTypeId.ZEALOT,
        alliance=fields.pop("alliance", _ENEMY),
        health=health,
        health_max=100.0,
        shield=shield,
        shield_max=50.0,
        **fields,
    )


_STORM_READY = EnemyUnitVitalReachedEvent.of(VitalType.ENERGY, 75, UnitTypeId.HIGH_TEMPLAR)


class TestVitalReached:
    def _steps(self, *units: raw_pb2.Unit | None, of: EventFilter[Any] = _STORM_READY) -> list[int]:
        """The steps at which an enemy unit is reported reaching what `of` selects, by default 75 energy on a high
        templar, as `units` are observed one after another; `None` is an observation without it."""
        game = _Game()
        seen = record(game.events, of)
        for step, unit in enumerate(units):
            game.observe(step * 16, *([] if unit is None else [unit]))
        return [event.step for event in seen]

    def test_a_rise_across_the_value_is_reported_once(self) -> None:
        assert self._steps(_templar(70), _templar(74), _templar(75), _templar(90), _templar(80)) == [32]

    def test_a_drop_below_it_and_a_rise_again_is_reported_again(self) -> None:
        assert self._steps(_templar(70), _templar(80), _templar(10), _templar(76)) == [16, 48]

    def test_a_unit_first_seen_at_or_above_it_is_never_reported(self) -> None:
        assert not self._steps(_templar(100), _templar(120))

    def test_a_rise_out_of_vision_is_reported_when_the_unit_is_next_seen(self) -> None:
        hidden = _templar(0, visibility=Visibility.INVISIBLE)
        assert self._steps(_templar(70), None, hidden, _templar(90)) == [48]

    def test_another_unit_type_is_not_watched(self) -> None:
        archons = (make_unit(1, UnitTypeId.ARCHON, alliance=_ENEMY, energy=e, energy_max=200.0) for e in (70, 90))
        assert not self._steps(*archons)

    def test_a_unit_type_group_watches_every_type_in_it_and_none_every_type(self) -> None:
        protoss = EnemyUnitVitalReachedEvent.of(VitalType.ENERGY, 75, UnitType.Protoss)
        assert self._steps(_templar(70), _templar(80), of=protoss) == [16]
        assert self._steps(_templar(70), _templar(80), of=EnemyUnitVitalReachedEvent.of(VitalType.ENERGY, 75)) == [16]

    def test_each_side_is_reported_to_its_own_event(self) -> None:
        game = _Game()
        own = record(game.events, OwnUnitVitalReachedEvent.of(VitalType.ENERGY, 75, UnitTypeId.HIGH_TEMPLAR))
        enemy = record(game.events, _STORM_READY)
        game.observe(0, _templar(70, tag=1, alliance=Alliance.OWN), _templar(70, tag=2))
        units = game.observe(16, _templar(80, tag=1, alliance=Alliance.OWN), _templar(70, tag=2))
        assert [(event.step, event.unit, event.vital, event.value) for event in own] == [
            (16, units[1], VitalType.ENERGY, 75.0)
        ]
        assert not enemy

    def test_a_watch_starts_on_the_turn_its_handler_subscribed_reporting_nothing_that_turn(self) -> None:
        game = _Game()
        game.observe(0, _templar(70))
        seen = record(game.events, _STORM_READY)
        game.observe(16, _templar(80))
        game.observe(32, _templar(10))
        game.observe(48, _templar(80))
        assert [event.step for event in seen] == [48]


class TestVitals:
    def _crossings(self, vital: VitalType, value: float, *units: raw_pb2.Unit) -> list[tuple[str, int]]:
        """Each reported crossing of `value` by an enemy unit's `vital`, with its step, as `units` are observed one
        after another."""
        game = _Game()
        watched = (EnemyUnitVitalReachedEvent.of(vital, value), EnemyUnitVitalDroppedEvent.of(vital, value))
        seen = record(game.events, *watched)
        for step, unit in enumerate(units):
            game.observe(step * 16, unit)
        return [(type(event).__name__, event.step) for event in seen]

    def test_a_drop_below_the_value_and_a_rise_back_to_it_are_each_reported_once(self) -> None:
        lives = [(100, 50), (50, 0), (30, 0), (70, 10), (100, 50)]
        zealots = (_zealot(health, shield) for health, shield in lives)
        assert self._crossings(VitalType.LIFE_FRACTION, 0.5, *zealots) == [
            ("EnemyUnitVitalDroppedEvent", 16),
            ("EnemyUnitVitalReachedEvent", 48),
        ]

    def test_the_value_itself_is_reached(self) -> None:
        zealots = (_zealot(100, 50), _zealot(75, 0), _zealot(74, 0), _zealot(75, 0))
        assert self._crossings(VitalType.LIFE_FRACTION, 0.5, *zealots) == [
            ("EnemyUnitVitalDroppedEvent", 32),
            ("EnemyUnitVitalReachedEvent", 48),
        ]

    @pytest.mark.parametrize(
        ("vital", "value", "dropping"),
        [
            (VitalType.HEALTH, 80, _zealot(70, 50)),
            (VitalType.SHIELD, 20, _zealot(100, 10)),
            (VitalType.LIFE, 120, _zealot(90, 20)),
            (VitalType.HEALTH_FRACTION, 0.8, _zealot(70, 50)),
            (VitalType.SHIELD_FRACTION, 0.4, _zealot(100, 10)),
            (VitalType.LIFE_FRACTION, 0.8, _zealot(90, 20)),
            (VitalType.ENERGY, 50, _templar(40)),
            (VitalType.ENERGY_FRACTION, 0.25, _templar(40)),
        ],
        ids=lambda value: value.name if isinstance(value, VitalType) else None,
    )
    def test_every_vital_is_read_as_its_type_says(self, vital: VitalType, value: float, dropping: raw_pb2.Unit) -> None:
        full = _templar(100) if dropping.unit_type == UnitTypeId.HIGH_TEMPLAR else _zealot(100, 50)
        assert self._crossings(vital, value, full, dropping, full) == [
            ("EnemyUnitVitalDroppedEvent", 16),
            ("EnemyUnitVitalReachedEvent", 32),
        ]

    @pytest.mark.parametrize("vital", [VitalType.SHIELD, VitalType.SHIELD_FRACTION, VitalType.ENERGY])
    def test_a_unit_without_the_vital_has_none_to_cross(self, vital: VitalType) -> None:
        marines = (make_unit(1, alliance=_ENEMY, health=health, health_max=45.0) for health in (45, 10, 45))
        assert not self._crossings(vital, 0.5, *marines)

    def test_a_unit_of_this_players_back_to_full(self) -> None:
        game = _Game()
        seen = record(game.events, OwnUnitVitalReachedEvent.of(VitalType.LIFE_FRACTION, 1.0))
        for step, health in enumerate((100, 60, 90, 100, 100)):
            game.observe(step * 16, _zealot(health, 50, alliance=Alliance.OWN))
        assert [event.step for event in seen] == [48]


_AREA = Circle(Point((20.0, 20.0)), 5)
_INSIDE, _OUTSIDE = (20.0, 21.0), (40.0, 40.0)


class TestAreas:
    def _crossings(
        self, *positions: tuple[float, float] | None, area: Area = _AREA, **fields: Any
    ) -> list[tuple[str, int]]:
        """Each reported crossing of the edge of `area` by an enemy marine, with its step, as it is observed at each
        of `positions` in turn; `None` is an observation without it."""
        game = _Game()
        seen = record(game.events, EnemyUnitEnteredAreaEvent.of(area), EnemyUnitLeftAreaEvent.of(area))
        for step, at in enumerate(positions):
            game.observe(step * 16, *([] if at is None else [make_unit(1, alliance=_ENEMY, at=at, **fields)]))
        return [(type(event).__name__, event.step) for event in seen]

    def test_entering_and_leaving(self) -> None:
        assert self._crossings(_OUTSIDE, _INSIDE, _INSIDE, _OUTSIDE, _OUTSIDE) == [
            ("EnemyUnitEnteredAreaEvent", 16),
            ("EnemyUnitLeftAreaEvent", 48),
        ]

    def test_a_unit_first_seen_inside_has_entered(self) -> None:
        assert self._crossings(None, _INSIDE) == [("EnemyUnitEnteredAreaEvent", 16)]

    def test_what_the_watchs_first_turn_finds_inside_is_taken_as_it_stands(self) -> None:
        assert self._crossings(_INSIDE, _INSIDE, _OUTSIDE) == [("EnemyUnitLeftAreaEvent", 32)]

    def test_a_unit_out_of_sight_inside_that_turns_up_outside_has_left(self) -> None:
        assert self._crossings(_OUTSIDE, _INSIDE, None, None, _OUTSIDE) == [
            ("EnemyUnitEnteredAreaEvent", 16),
            ("EnemyUnitLeftAreaEvent", 64),
        ]

    def test_a_unit_that_dies_inside_does_not_leave(self) -> None:
        game = _Game()
        seen = record(game.events, EnemyUnitEnteredAreaEvent.of(_AREA), EnemyUnitLeftAreaEvent.of(_AREA))
        game.observe(0, make_unit(1, alliance=_ENEMY, at=_OUTSIDE))
        game.observe(16, make_unit(1, alliance=_ENEMY, at=_INSIDE))
        game.observe(32, dead=(1,))
        game.observe(48)
        assert [type(event) for event in seen] == [EnemyUnitEnteredAreaEvent]
        assert not any(game.tracker.unit_watcher._enemy._area_watches[_AREA].inside)

    def test_an_enemy_unit_in_the_fog_is_not_counted(self) -> None:
        assert not self._crossings(None, _INSIDE, visibility=Visibility.IN_FOG)

    def test_a_cloaked_enemy_unit_in_sight_is_counted(self) -> None:
        assert self._crossings(None, _INSIDE, visibility=Visibility.INVISIBLE) == [("EnemyUnitEnteredAreaEvent", 16)]

    @pytest.mark.parametrize(
        "area",
        [Rectangle(18, 18, 4, 4), TileSet([Tile(20, 21)])],
        ids=["Rectangle", "TileSet"],
    )
    def test_any_area(self, area: Area) -> None:
        assert self._crossings(_OUTSIDE, _INSIDE, _OUTSIDE, area=area) == [
            ("EnemyUnitEnteredAreaEvent", 16),
            ("EnemyUnitLeftAreaEvent", 32),
        ]

    def test_units_of_this_players_to_their_own_events(self) -> None:
        game = _Game()
        seen = record(game.events, OwnUnitEnteredAreaEvent.of(_AREA), OwnUnitLeftAreaEvent.of(_AREA))
        for step, at in enumerate((_OUTSIDE, _INSIDE, _OUTSIDE)):
            game.observe(step * 16, make_unit(1, at=at))
        assert [(type(event), event.step, event.area) for event in seen] == [
            (OwnUnitEnteredAreaEvent, 16, _AREA),
            (OwnUnitLeftAreaEvent, 32, _AREA),
        ]


class TestBases:
    def test_a_handler_of_unit_event_is_handed_every_units_event_of_either_side(self) -> None:
        game = _Game()
        seen = record(game.events, UnitEvent)
        _everything(game)
        kinds = {type(event) for event in seen}
        assert kinds == {event_type for event_type in HAPPENINGS if issubclass(event_type, UnitEvent)}
        assert {OwnUnitCreatedEvent, EnemyUnitFirstSeenEvent, UnitDiedEvent} <= kinds
        assert not kinds & {OwnUnitGainedBuffEvent, OwnUpgradeFinishedEvent, AlertEvent}
        assert {event.unit.alliance for event in seen} == {Alliance.OWN, Alliance.ENEMY}

    def test_only_on_unit_event_selects_by_type_across_every_kind(self) -> None:
        game = _Game()
        seen = record(game.events, UnitEvent.only(UnitTypeId.MARINE))
        _everything(game)
        assert seen and all(event.unit.type_id is UnitTypeId.MARINE for event in seen)
        assert {OwnUnitCreatedEvent, EnemyUnitFirstSeenEvent, OwnUnitDamagedEvent, UnitDiedEvent} <= {
            type(event) for event in seen
        }

    def test_a_handler_of_buff_event_is_handed_gains_and_losses_on_either_side(self) -> None:
        game = _Game()
        seen = record(game.events, BuffEvent.only(BuffId.MARINE_STIMMED))
        for step, buffs in enumerate(([], [BuffId.MARINE_STIMMED], [])):
            game.observe(step * 16, make_unit(1, buff_ids=buffs), make_unit(2, alliance=_ENEMY, buff_ids=buffs))
        assert [type(event) for event in seen] == [
            OwnUnitGainedBuffEvent,
            EnemyUnitGainedBuffEvent,
            OwnUnitLostBuffEvent,
            EnemyUnitLostBuffEvent,
        ]

    def test_a_handler_of_vital_event_is_handed_the_drop_and_the_rise_on_either_side(self) -> None:
        game = _Game()
        seen = record(game.events, VitalEvent.of(VitalType.LIFE_FRACTION, 0.5))
        for step, (health, shield) in enumerate(((100, 50), (50, 0), (100, 50))):
            own = make_unit(2, UnitTypeId.ZEALOT, health=health, health_max=100.0, shield=shield, shield_max=50.0)
            game.observe(step * 16, _zealot(health, shield), own)
        assert [type(event) for event in seen] == [
            OwnUnitVitalDroppedEvent,
            EnemyUnitVitalDroppedEvent,
            OwnUnitVitalReachedEvent,
            EnemyUnitVitalReachedEvent,
        ]

    def test_a_handler_of_area_event_is_handed_the_entry_and_the_exit_on_either_side(self) -> None:
        game = _Game()
        seen = record(game.events, AreaEvent.of(_AREA))
        for step, at in enumerate((_OUTSIDE, _INSIDE, _OUTSIDE)):
            game.observe(step * 16, make_unit(1, alliance=_ENEMY, at=at), make_unit(2, at=at))
        assert [type(event) for event in seen] == [
            OwnUnitEnteredAreaEvent,
            EnemyUnitEnteredAreaEvent,
            OwnUnitLeftAreaEvent,
            EnemyUnitLeftAreaEvent,
        ]


class TestWatching:
    def test_nothing_is_watched_until_wanted_and_a_watch_ends_once_its_handlers_are_done(self) -> None:
        game = _Game()
        game.observe(0, _templar(70))
        assert not game.tracker.unit_watcher._enemy.watching
        game.events.on(_STORM_READY)(lambda event: Done)
        game.observe(16, _templar(70))
        assert game.tracker.unit_watcher._enemy._vital_ids
        game.observe(32, _templar(80))
        game.observe(48, _templar(80))
        assert not game.tracker.unit_watcher._enemy.watching

    def test_what_is_watched_comes_in_its_place_in_a_turn(self) -> None:
        game = _Game()
        seen = record(
            game.events,
            EnemyUnitEnteredAreaEvent.of(_AREA),
            EnemyUnitVitalDroppedEvent.of(VitalType.LIFE_FRACTION, 0.5),
            _STORM_READY,
            EnemyUnitEnergyLostEvent,
            EnemyUnitLeftSightEvent,
        )
        leaving = make_unit(3, alliance=_ENEMY)
        game.observe(0, _templar(100, tag=1), _templar(70, tag=2, at=_OUTSIDE, health=40, health_max=40), leaving)
        game.observe(16, _templar(50, tag=1), _templar(80, tag=2, at=_INSIDE, health=10, health_max=40))
        assert [type(event) for event in seen] == [
            EnemyUnitEnergyLostEvent,
            EnemyUnitVitalReachedEvent,
            EnemyUnitVitalDroppedEvent,
            EnemyUnitLeftSightEvent,
            EnemyUnitEnteredAreaEvent,
        ]


@contextmanager
def _played_as(race: Race) -> Iterator[tuple[RealGame, list[Any], Point]]:
    """A game as `race` under the `free` and `fast_build` cheats, the list of every event from its first observation
    on, and the center of the map."""
    try:
        game_map = MapFile.find("PylonAIE_v4")
    except MapNotFoundError as missing:
        pytest.skip(str(missing))
    with (
        GameProcess.launch(window=(640, 480)) as process,
        closing(Client(WebSocketTransport.connect(process.url))) as client,
    ):
        client.create_game(game_map.path, [Participant(), Computer(Race.ZERG, Difficulty.VERY_EASY)])
        events = EventBus()
        seen = record(events, *HAPPENINGS)
        game = RealGame(client, client.join_game(race), events)
        state = debug_pb2.DebugGameState
        game.debug(*(debug_pb2.DebugCommand(game_state=cheat) for cheat in (state.free, state.fast_build)))
        # The cheats take a few steps to take effect.
        game.turn(8)
        yield game, seen, game.map.playable_area.center
        client.leave_game()


def _own(game: RealGame, unit_type: UnitTypeId) -> OwnUnit[Any]:
    unit = next(unit for unit in game.tracker.unit_tracker.present.own if unit.type_id is unit_type)
    assert isinstance(unit, OwnUnit)
    return unit


def _until(game: RealGame, done: Callable[[], object], *, steps: int = 4, turns: int = 400) -> None:
    for _ in range(turns):
        if done():
            return
        game.turn(steps)
    raise AssertionError("it never happened")


def _alerts(seen: list[Any], alert: Alert) -> list[AlertEvent]:
    return [event for event in seen if isinstance(event, AlertEvent) and event.alert is alert]


def _of(seen: list[Any], *event_types: type[Event]) -> list[Any]:
    return [event for event in seen if type(event) in event_types]


def _vitals(seen: list[Any], unit: Unit[Any]) -> list[Any]:
    return [event for event in _of(seen, OwnUnitVitalReachedEvent, OwnUnitVitalDroppedEvent) if event.unit is unit]


_AREAS = (OwnUnitEnteredAreaEvent, OwnUnitLeftAreaEvent)


@pytest.mark.integration
class TestAgainstTheRealGame:
    """Run with `pytest -m integration`. Each test plays a few minutes of a game under cheats."""

    def test_zerg_units_hatch_morph_and_become_structures(self) -> None:
        with _played_as(Race.ZERG) as (game, seen, middle):
            # The units the game starts with are created on the first turn.
            started = {event.unit for event in _of(seen, OwnUnitCreatedEvent) if event.step == 0}
            assert started == set(game.tracker.unit_tracker.present.own)
            home = _own(game, UnitTypeId.HATCHERY).position

            # A larva becomes an egg, which is reported dead as the drone hatches out of it under a new tag.
            game.order(AbilityId.LARVA_MORPH_DRONE, _own(game, UnitTypeId.LARVA))
            _until(game, lambda: _of(seen, UnitDiedEvent))
            (egg,) = [
                event.unit for event in _of(seen, UnitTypeChangedEvent) if event.previous_type is UnitTypeId.LARVA
            ]
            (died,) = _of(seen, UnitDiedEvent)
            assert died.unit is egg
            hatched = [event.unit.type_id for event in _of(seen, OwnUnitCreatedEvent) if event.step == died.step]
            assert UnitTypeId.DRONE in hatched
            assert AlertEvent(Alert.TRAIN_WORKER_COMPLETE, step=died.step) in seen

            # A drone becomes a spawning pool, and the game reports it dead as the pool finishes.
            drone = _own(game, UnitTypeId.DRONE)
            game.order(
                AbilityId.DRONE_MORPH_SPAWNING_POOL, drone, target=game.open_ground(home.towards(middle, 6), size=3)
            )
            _until(game, lambda: _of(seen, OwnConstructionFinishedEvent))
            (started_pool,) = _of(seen, OwnConstructionStartedEvent)
            (finished,) = _of(seen, OwnConstructionFinishedEvent)
            assert started_pool.unit is finished.unit
            assert finished.unit.type_id is UnitTypeId.SPAWNING_POOL
            # The game reports the drone dead in the step the pool finishes or the one after.
            _until(game, lambda: drone.is_dead)
            (drone_died,) = [event for event in _of(seen, UnitDiedEvent, UnitFoundDeadEvent) if event.unit is drone]
            assert type(drone_died) is UnitDiedEvent and drone_died.step - finished.step in (0, 4)
            assert AlertEvent(Alert.BUILDING_COMPLETE, step=finished.step) in seen

            # A zergling stays itself through its cocoon.
            game.debug(game.create(UnitTypeId.BANELING_NEST, game.open_ground(home.towards(middle, 10), size=3)))
            game.debug(game.create(UnitTypeId.ZERGLING, home.towards(middle, 4)))
            game.turn(4)
            zergling = _own(game, UnitTypeId.ZERGLING)
            game.order(AbilityId.ZERGLING_MORPH_BANELING, zergling)
            _until(game, lambda: zergling.type_id is UnitTypeId.BANELING)
            assert [event.previous_type for event in _of(seen, UnitTypeChangedEvent) if event.unit is zergling] == [
                UnitTypeId.ZERGLING,
                UnitTypeId.BANELING_COCOON,
            ]
            assert _alerts(seen, Alert.MORPH_COMPLETE)

            # This player's own message comes back.
            chat = sc2api_pb2.ActionChat(channel=sc2api_pb2.ActionChat.Broadcast, message="gl hf")
            game.client.act([sc2api_pb2.Action(action_chat=chat)])
            _until(game, lambda: _of(seen, ChatEvent), steps=2)
            game.turn(2)
            assert [(event.player_id, event.text) for event in _of(seen, ChatEvent)] == [(game.player, "gl hf")]

    def test_terran_structures_add_ons_morphs_and_research(self) -> None:
        with _played_as(Race.TERRAN) as (game, seen, middle):
            home = _own(game, UnitTypeId.COMMAND_CENTER).position

            # A depot is built, then lowered.
            scv = _own(game, UnitTypeId.SCV)
            game.order(AbilityId.SCV_BUILD_SUPPLY_DEPOT, scv, target=game.open_ground(home.towards(middle, 8), size=2))
            _until(game, lambda: _of(seen, OwnConstructionFinishedEvent))
            (depot,) = [event.unit for event in _of(seen, OwnConstructionStartedEvent)]
            assert [event.unit for event in _of(seen, OwnConstructionFinishedEvent)] == [depot]
            game.order(AbilityId.SUPPLY_DEPOT_LOWER, depot)
            _until(game, lambda: depot.type_id is UnitTypeId.SUPPLY_DEPOT_LOWERED)
            assert UnitTypeChangedEvent(depot, UnitTypeId.SUPPLY_DEPOT, step=game.step) in seen

            # An add-on is a unit of its own, seen as soon as it is started.
            game.debug(game.create(UnitTypeId.BARRACKS, game.open_ground(home.towards(middle, 13), size=5)))
            game.turn(4)
            game.order(AbilityId.GENERAL_BUILD_REACTOR, _own(game, UnitTypeId.BARRACKS))
            _until(game, lambda: game.tracker.unit_tracker.present.own.of_type(UnitTypeId.REACTOR_BARRACKS), steps=1)
            reactor = _own(game, UnitTypeId.REACTOR_BARRACKS)
            assert reactor.build_progress < 0.1
            assert OwnConstructionStartedEvent(reactor, step=game.step) in seen
            _until(game, lambda: reactor.is_complete)
            assert _alerts(seen, Alert.ADD_ON_COMPLETE)

            # A command center becomes an orbital command once the morph finishes, which the game calls an upgrade.
            center = _own(game, UnitTypeId.COMMAND_CENTER)
            game.order(AbilityId.COMMAND_CENTER_MORPH_ORBITAL_COMMAND, center)
            _until(game, lambda: center.type_id is UnitTypeId.ORBITAL_COMMAND)
            assert UnitTypeChangedEvent(center, UnitTypeId.COMMAND_CENTER, step=game.step) in seen
            assert AlertEvent(Alert.UPGRADE_COMPLETE, step=game.step) in seen

            # A research finishes.
            game.debug(game.create(UnitTypeId.ENGINEERING_BAY, game.open_ground(home.towards(middle, 18), size=3)))
            game.turn(4)
            game.order(AbilityId.ENGINEERING_BAY_RESEARCH_INFANTRY_WEAPONS_1, _own(game, UnitTypeId.ENGINEERING_BAY))
            _until(game, lambda: _of(seen, OwnUpgradeFinishedEvent))
            (upgrade,) = _of(seen, OwnUpgradeFinishedEvent)
            assert upgrade.upgrade is UpgradeId.TERRAN_INFANTRY_WEAPONS_1

            # A structure cancelled while built is reported dead.
            scv = _own(game, UnitTypeId.SCV)
            spot = game.open_ground(home.towards(middle, 22), size=3)
            game.order(AbilityId.SCV_BUILD_ENGINEERING_BAY, scv, target=spot)
            _until(game, lambda: len(_of(seen, OwnConstructionStartedEvent)) == 3, steps=2)
            bay = _of(seen, OwnConstructionStartedEvent)[-1].unit
            game.order(AbilityId.GENERAL_CANCEL_BUILDING, bay)
            _until(game, lambda: bay.is_dead)
            assert UnitDiedEvent(bay, step=game.step) in seen

            # A MULE is reported dead once it expires.
            field = min(
                (unit for unit in game.tracker.unit_tracker.present if unit.type_id is UnitTypeId.MINERAL_FIELD),
                key=lambda unit: unit.position.distance_to(home),
            )
            energy = debug_pb2.DebugSetUnitValue(
                unit_value=debug_pb2.DebugSetUnitValue.Energy, value=200, unit_tag=center.tag
            )
            game.debug(debug_pb2.DebugCommand(unit_value=energy))
            game.turn(2)
            game.order(AbilityId.ORBITAL_COMMAND_CALLDOWN_MULE, center, target=field)
            _until(game, lambda: game.tracker.unit_tracker.present.own.of_type(UnitTypeId.MULE))
            mule = _own(game, UnitTypeId.MULE)
            _until(game, lambda: mule.is_dead, steps=100, turns=20)
            assert UnitDiedEvent(mule, step=game.step) in seen
            assert AlertEvent(Alert.MULE_EXPIRED, step=game.step) in seen

    def test_cloaking_detection_and_buffs(self) -> None:
        with _played_as(Race.TERRAN) as (game, seen, middle):
            home = _own(game, UnitTypeId.COMMAND_CENTER).position

            # A ghost cloaks and uncloaks once its academy has researched cloaking.
            game.debug(
                game.create(UnitTypeId.GHOST_ACADEMY, game.open_ground(home.towards(middle, 10), size=3)),
                game.create(UnitTypeId.GHOST, home.towards(middle, 6)),
            )
            _until(
                game,
                lambda: (
                    len(game.tracker.unit_tracker.present.own.of_type([UnitTypeId.GHOST, UnitTypeId.GHOST_ACADEMY]))
                    == 2
                ),
            )
            game.order(AbilityId.GHOST_ACADEMY_RESEARCH_GHOST_CLOAK, _own(game, UnitTypeId.GHOST_ACADEMY))
            _until(game, lambda: UpgradeId.GHOST_CLOAK in game.tracker.upgrade_tracker.own)
            ghost = _own(game, UnitTypeId.GHOST)
            game.order(AbilityId.GHOST_CLOAK_ON, ghost)
            _until(game, lambda: _of(seen, OwnUnitCloakChangedEvent), steps=1)
            game.order(AbilityId.GHOST_CLOAK_OFF, ghost)
            _until(game, lambda: len(_of(seen, OwnUnitCloakChangedEvent)) == 2, steps=1)
            assert [(event.unit, event.previous_cloak_state) for event in _of(seen, OwnUnitCloakChangedEvent)] == [
                (ghost, CloakState.NOT_CLOAKED),
                (ghost, CloakState.CLOAKED_ALLIED),
            ]
            cloaks = [event for event in _of(seen, OwnUnitGainedBuffEvent, OwnUnitLostBuffEvent) if event.unit is ghost]
            assert [(type(event), event.buff) for event in cloaks] == [
                (OwnUnitGainedBuffEvent, BuffId.GHOST_CLOAK),
                (OwnUnitLostBuffEvent, BuffId.GHOST_CLOAK),
            ]

            # An enemy observer nothing detects is listed cloaked, out of vision, until a raven beside it detects it.
            game.debug(game.create(UnitTypeId.OBSERVER, home.towards(middle, 4), owner=3 - game.player))
            _until(game, lambda: game.tracker.unit_tracker.present.enemy.of_type(UnitTypeId.OBSERVER), steps=1)
            observer = game.newest(UnitTypeId.OBSERVER)
            assert observer.cloak_state is CloakState.CLOAKED and observer.visibility is Visibility.INVISIBLE
            game.debug(game.create(UnitTypeId.RAVEN, observer.position))
            _until(game, lambda: _of(seen, EnemyUnitCloakChangedEvent), steps=1)
            (detected,) = _of(seen, EnemyUnitCloakChangedEvent)
            assert detected.unit is observer and detected.previous_cloak_state is CloakState.CLOAKED
            assert observer.cloak_state is CloakState.CLOAKED_DETECTED and observer.visibility is Visibility.IN_VISION

            # A fungal growth on an enemy marine is a buff it gains, and loses as it wears off.
            game.debug(
                game.create(UnitTypeId.MARINE, home.towards(middle, 22), owner=3 - game.player),
                game.create(UnitTypeId.INFESTOR, home.towards(middle, 16)),
            )
            _until(game, lambda: game.tracker.unit_tracker.present.enemy.of_type(UnitTypeId.MARINE), steps=1)
            marine = game.newest(UnitTypeId.MARINE)
            game.order(AbilityId.INFESTOR_FUNGAL_GROWTH, _own(game, UnitTypeId.INFESTOR), target=marine.position)
            _until(game, lambda: _of(seen, EnemyUnitLostBuffEvent), steps=2)
            fungal = [
                event for event in _of(seen, EnemyUnitGainedBuffEvent, EnemyUnitLostBuffEvent) if event.unit is marine
            ]
            assert [(type(event), event.buff) for event in fungal] == [
                (EnemyUnitGainedBuffEvent, BuffId.INFESTOR_FUNGAL_GROWTH),
                (EnemyUnitLostBuffEvent, BuffId.INFESTOR_FUNGAL_GROWTH),
            ]

    def test_protoss_warp_ins_damage_sight_death_archons_and_energy(self) -> None:
        with _played_as(Race.PROTOSS) as (game, seen, middle):
            home = _own(game, UnitTypeId.NEXUS).position

            # A zealot warping in is created, and warped in some steps later.
            pylon = game.open_ground(home.towards(middle, 9), size=2)
            game.debug(game.create(UnitTypeId.PYLON, pylon))
            game.debug(game.create(UnitTypeId.WARP_GATE, game.open_ground(home.towards(middle, 13), size=3)))
            game.turn(8)
            game.order(AbilityId.WARP_GATE_WARP_IN_ZEALOT, _own(game, UnitTypeId.WARP_GATE), target=pylon + (3.0, 0.0))
            _until(game, lambda: game.tracker.unit_tracker.present.own.of_type(UnitTypeId.ZEALOT), steps=1)
            zealot = _own(game, UnitTypeId.ZEALOT)
            assert not zealot.is_complete
            _until(game, lambda: zealot.is_complete, steps=1)
            assert OwnWarpInFinishedEvent(zealot, step=game.step) in seen
            assert AlertEvent(Alert.WARP_IN_COMPLETE, step=game.step) in seen

            # Shields lost are damage.
            shields = debug_pb2.DebugSetUnitValue(
                unit_value=debug_pb2.DebugSetUnitValue.Shields, value=10, unit_tag=zealot.tag
            )
            game.debug(debug_pb2.DebugCommand(unit_value=shields))
            _until(game, lambda: _of(seen, OwnUnitDamagedEvent), steps=2)
            (damaged,) = _of(seen, OwnUnitDamagedEvent)
            assert damaged.unit is zealot and damaged.damage == pytest.approx(zealot.shield_max - 10, abs=1)

            # Two templar ordered to merge walk to each other, each reporting the order it runs, and become an archon.
            at = home.towards(middle, 5)
            game.debug(game.create(UnitTypeId.HIGH_TEMPLAR, at), game.create(UnitTypeId.HIGH_TEMPLAR, at + (6.0, 0.0)))
            _until(game, lambda: len(game.tracker.unit_tracker.present.own.of_type(UnitTypeId.HIGH_TEMPLAR)) == 2)
            templar = [
                u
                for u in game.tracker.unit_tracker.present.own.of_type(UnitTypeId.HIGH_TEMPLAR)
                if isinstance(u, OwnUnit)
            ]
            merge = raw_pb2.ActionRawUnitCommand(
                ability_id=AbilityId.GENERAL_MORPH_ARCHON, unit_tags=[unit.tag for unit in templar]
            )
            game.client.act([sc2api_pb2.Action(action_raw=raw_pb2.ActionRaw(unit_command=merge))])
            _until(game, lambda: all(unit.orders for unit in templar), steps=1)
            assert [unit.orders[0].ability for unit in templar] == [AbilityId.GENERAL_MORPH_ARCHON_EXACT] * 2
            _until(game, lambda: _alerts(seen, Alert.MERGE_COMPLETE))
            assert game.tracker.unit_tracker.present.own.of_type(UnitTypeId.ARCHON)

            # An enemy pylon out of sight comes into sight beside an observer, goes out of it some steps after the
            # observer dies, and is found dead once its spot is seen again after it died in the fog.
            far = game.open_ground(middle, size=2)
            game.debug(game.create(UnitTypeId.PYLON, far, owner=3 - game.player))
            game.turn(4)
            assert not _of(seen, EnemyUnitFirstSeenEvent)
            for times in (1, 2):
                game.debug(game.create(UnitTypeId.OBSERVER, far + (3.0, 0.0)))
                _until(game, lambda times=times: len(_of(seen, EnemyUnitEnteredSightEvent)) == times)
                game.debug(game.kill(_own(game, UnitTypeId.OBSERVER)))
                # What a unit saw stays in sight for some steps after it dies.
                _until(game, lambda times=times: len(_of(seen, EnemyUnitLeftSightEvent)) == times, steps=16)
            (first,) = _of(seen, EnemyUnitFirstSeenEvent)
            enemy = first.unit
            assert [type(event) for event in seen if getattr(event, "unit", None) is enemy] == [
                EnemyUnitFirstSeenEvent,
                EnemyUnitEnteredSightEvent,
                EnemyUnitLeftSightEvent,
                EnemyUnitEnteredSightEvent,
                EnemyUnitLeftSightEvent,
            ]
            assert enemy.visibility is Visibility.IN_FOG
            sighting = enemy._latest_report_in_vision
            assert sighting is not None
            game.debug(debug_pb2.DebugCommand(kill_unit=debug_pb2.DebugKillUnit(tag=[sighting.tag])))
            game.turn(8)
            game.debug(game.create(UnitTypeId.OBSERVER, far + (3.0, 0.0)))
            _until(game, lambda: enemy.is_dead)
            assert UnitFoundDeadEvent(enemy, step=game.step) in seen

            # A feedback costs its caster 50 energy and drains its target's, which no buff or effect shows. `free`, a
            # toggle, would make it cost nothing, so it is turned off.
            game.debug(
                debug_pb2.DebugCommand(game_state=debug_pb2.DebugGameState.free),
                game.create(UnitTypeId.HIGH_TEMPLAR, at),
                game.create(UnitTypeId.RAVEN, at + (4.0, 0.0), owner=3 - game.player),
            )
            pair = [UnitTypeId.HIGH_TEMPLAR, UnitTypeId.RAVEN]
            _until(game, lambda: len(game.tracker.unit_tracker.present.of_type(pair)) == 2)
            caster, raven = game.newest(UnitTypeId.HIGH_TEMPLAR), game.newest(UnitTypeId.RAVEN)

            def charge(energy: float, *units: Unit[Any]) -> None:
                value = debug_pb2.DebugSetUnitValue.Energy
                commands = [debug_pb2.DebugSetUnitValue(unit_value=value, value=energy, unit_tag=u.tag) for u in units]
                game.debug(*(debug_pb2.DebugCommand(unit_value=command) for command in commands))
                game.turn(2)

            drained = (OwnUnitEnergyLostEvent, EnemyUnitEnergyLostEvent)

            def lost_after(since: int, *units: Unit[Any]) -> dict[Unit[Any], float]:
                """The energy each of `units` loses from the `since`-th report on, waiting until each has lost some."""
                _until(game, lambda: {e.unit for e in _of(seen, *drained)[since:]} >= set(units), steps=1)
                return {e.unit: e.energy_lost for e in _of(seen, *drained)[since:]}

            charge(100, caster, raven)
            since = len(_of(seen, *drained))
            game.order(AbilityId.HIGH_TEMPLAR_FEEDBACK, caster, target=raven)
            lost = lost_after(since, caster, raven)
            assert lost[caster] == pytest.approx(50, abs=1) and lost[raven] == pytest.approx(100, abs=1)
            assert not raven.buffs and not game.state.effects
            assert {type(event) for event in _of(seen, *drained)[since:] if event.unit is raven} == {
                EnemyUnitEnergyLostEvent
            }

            # An EMP costs its caster 75 energy, and drains up to 100 of every unit's where it lands.
            game.debug(game.create(UnitTypeId.GHOST, at + (-4.0, 0.0)))
            _until(game, lambda: game.tracker.unit_tracker.present.own.of_type(UnitTypeId.GHOST))
            ghost = game.newest(UnitTypeId.GHOST)
            charge(150, ghost, raven)
            since = len(_of(seen, *drained))
            game.order(AbilityId.GHOST_EMP, ghost, target=raven.position)
            lost = lost_after(since, ghost, raven)
            assert lost[ghost] == pytest.approx(75, abs=1) and lost[raven] == pytest.approx(100, abs=1)

    def test_energy_life_areas_and_selected_keys(self) -> None:
        with _played_as(Race.PROTOSS) as (game, seen, middle):
            home = _own(game, UnitTypeId.NEXUS).position
            circle = Circle(home.towards(middle, 14), 4)
            watched: list[Event] = []
            for selected in (
                OwnUnitVitalReachedEvent.of(VitalType.ENERGY, 75, UnitType.HighTemplar),
                OwnUnitVitalDroppedEvent.of(VitalType.LIFE_FRACTION, 0.7),
                OwnUnitVitalReachedEvent.of(VitalType.LIFE_FRACTION, 1.0),
                OwnUnitEnteredAreaEvent.of(circle),
                OwnUnitLeftAreaEvent.of(circle),
                EnemyUnitEnteredAreaEvent.of(circle),
            ):
                game.events.on(selected)(lambda event: watched.append(event))
            workers = record(game.events, AlertEvent.only(Alert.TRAIN_WORKER_COMPLETE))
            structures = record(game.events, UnitDiedEvent.only(UnitType.Structure))

            # A worker trained raises an alert the handler of that alert hears. Trained first, before the units made
            # below take up the supply.
            game.order(AbilityId.NEXUS_TRAIN_PROBE, _own(game, UnitTypeId.NEXUS))
            _until(game, lambda: workers, steps=2)

            # A high templar made with 50 energy, set to 70, regenerates across 75 once.
            game.debug(game.create(UnitTypeId.HIGH_TEMPLAR, home.towards(middle, 6)))
            _until(game, lambda: game.tracker.unit_tracker.present.own.of_type(UnitTypeId.HIGH_TEMPLAR), steps=1)
            templar = _own(game, UnitTypeId.HIGH_TEMPLAR)
            energy = debug_pb2.DebugSetUnitValue(
                unit_value=debug_pb2.DebugSetUnitValue.Energy, value=70, unit_tag=templar.tag
            )
            game.debug(debug_pb2.DebugCommand(unit_value=energy))
            _until(game, lambda: _vitals(watched, templar))
            (reached,) = _vitals(watched, templar)
            assert (type(reached), reached.vital, reached.value) == (OwnUnitVitalReachedEvent, VitalType.ENERGY, 75)
            assert templar.energy >= 75
            game.turn(64)
            assert len(_vitals(watched, templar)) == 1

            # A zealot whose shields are set to 1 has 101 of 150 life left, and is back to full once they regenerate.
            # The game passes over shields set to 0 (in game).
            game.debug(game.create(UnitTypeId.ZEALOT, home.towards(middle, 20)))
            _until(game, lambda: game.tracker.unit_tracker.present.own.of_type(UnitTypeId.ZEALOT), steps=1)
            zealot = _own(game, UnitTypeId.ZEALOT)
            shields = debug_pb2.DebugSetUnitValue(
                unit_value=debug_pb2.DebugSetUnitValue.Shields, value=1, unit_tag=zealot.tag
            )
            game.debug(debug_pb2.DebugCommand(unit_value=shields))
            _until(game, lambda: len(_vitals(watched, zealot)) == 2, steps=16)
            assert [(type(event), event.vital, event.value) for event in _vitals(watched, zealot)] == [
                (OwnUnitVitalDroppedEvent, VitalType.LIFE_FRACTION, 0.7),
                (OwnUnitVitalReachedEvent, VitalType.LIFE_FRACTION, 1.0),
            ]

            # The zealot walks into the circle and out of it again, and an enemy overlord made inside it has entered it.
            game.order(AbilityId.GENERAL_MOVE, zealot, target=circle.center)
            _until(game, lambda: _of(watched, OwnUnitEnteredAreaEvent), steps=2)
            game.order(AbilityId.GENERAL_MOVE, zealot, target=home.towards(middle, 20))
            _until(game, lambda: _of(watched, OwnUnitLeftAreaEvent), steps=2)
            assert [type(event) for event in _of(watched, *_AREAS) if event.unit is zealot] == list(_AREAS)
            game.debug(game.create(UnitTypeId.OVERLORD, circle.center, owner=3 - game.player))
            _until(game, lambda: _of(watched, EnemyUnitEnteredAreaEvent), steps=1)
            (entered,) = _of(watched, EnemyUnitEnteredAreaEvent)
            assert entered.unit.type_id is UnitTypeId.OVERLORD and entered.area == circle

            # The handler of structures' deaths hears a pylon killed beside the zealot, and not the zealot.
            game.debug(game.create(UnitTypeId.PYLON, game.open_ground(home.towards(middle, 9), size=2)))
            _until(game, lambda: game.tracker.unit_tracker.present.own.of_type(UnitTypeId.PYLON), steps=1)
            game.debug(game.kill(_own(game, UnitTypeId.PYLON), zealot))
            _until(game, lambda: zealot.is_dead, steps=1)
            assert [event.unit.type_id for event in structures] == [UnitTypeId.PYLON]
            assert {event.unit.type_id for event in _of(seen, UnitDiedEvent)} >= {UnitTypeId.PYLON, UnitTypeId.ZEALOT}
            assert {event.alert for event in workers} == {Alert.TRAIN_WORKER_COMPLETE}
            assert workers == _alerts(seen, Alert.TRAIN_WORKER_COMPLETE)
