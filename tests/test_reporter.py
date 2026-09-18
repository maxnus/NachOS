"""What each observation reports has happened, handed on as events, driven by observations written here."""

from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from typing import Any

import pytest
from s2clientprotocol import common_pb2, data_pb2, debug_pb2, raw_pb2, sc2api_pb2

from sc2nachos._reporter import _report
from sc2nachos.enemy import Enemy
from sc2nachos.events import (
    AlertEvent,
    ChatEvent,
    Done,
    EnemyUnitCloakChangedEvent,
    EnemyUnitDamagedEvent,
    EnemyUnitEnergyLostEvent,
    EnemyUnitEnteredSightEvent,
    EnemyUnitFirstSeenEvent,
    EnemyUnitLeftSightEvent,
    Event,
    EventBus,
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
from sc2nachos.gamemap import GameMap
from sc2nachos.geometry import Point
from sc2nachos.ids import AbilityId, UnitTypeId, UpgradeId
from sc2nachos.launch import GameProcess, Map, MapNotFoundError
from sc2nachos.match import Computer, Difficulty, Participant, Race
from sc2nachos.protocol import Client, WebSocketTransport
from sc2nachos.state import Alert, CameraMove
from sc2nachos.state._state import _State
from sc2nachos.units import Alliance, CloakState, OwnUnit, Unit, Visibility
from sc2nachos.units._tracker import _UnitTracker
from support import HAPPENINGS, RealGame, make_game_info, make_observation, make_tables, make_unit, record

_ENEMY = Alliance.ENEMY
# Every alert the protocol names, in the order it names them, and the two no `Alert` stands for.
_EVERY_ALERT = [value.number for value in sc2api_pb2.Alert.DESCRIPTOR.values]
_PASSED_OVER = frozenset({sc2api_pb2.Alert.AlertError, sc2api_pb2.Alert.TrainError})
_STRUCTURE = [data_pb2.Attribute.Structure]
_TABLES = make_tables(
    data_pb2.UnitTypeData(unit_id=UnitTypeId.BARRACKS, attributes=_STRUCTURE),
    data_pb2.UnitTypeData(unit_id=UnitTypeId.SUPPLY_DEPOT, attributes=_STRUCTURE),
)


class _Game:
    """A tracker fed one observation after another and each reported, as `Api.play` does each turn."""

    def __init__(self, events: EventBus | None = None) -> None:
        self.events = events or EventBus()
        self.tracker = _UnitTracker(_TABLES, Enemy())
        self.map = GameMap(make_game_info())
        self.state: _State | None = None

    def observe(self, step: int, *units: raw_pb2.Unit, **fields: Any) -> dict[int, Unit[Any]]:
        """Take in `units` and the rest of an observation at `step`, report it, and answer the units by tag."""
        observation = make_observation(step, units=units, **fields)
        self.tracker.update(observation.observation.raw_data, step)
        self.state = _State(observation, self.tracker, self.map)
        _report(self.events, self.tracker, observation, self.state, step)
        return {unit.tag: unit for unit in self.tracker.present_units}


def _depot(tag: int, **fields: Any) -> raw_pb2.Unit:
    return make_unit(tag, UnitTypeId.SUPPLY_DEPOT, at=(30.5, 40.5), alliance=_ENEMY, build_progress=1.0, **fields)


def _everything(game: _Game) -> None:
    """Two turns, the second reporting one of everything, the first the units the game starts with."""
    game.observe(
        0,
        make_unit(1, health=45.0, energy=50.0, build_progress=1.0),
        make_unit(2, UnitTypeId.BARRACKS, build_progress=0.5),
        make_unit(3, UnitTypeId.ZEALOT, build_progress=0.5),
        make_unit(4, UnitTypeId.SIEGE_TANK, alliance=_ENEMY, health=100.0),
        make_unit(5, UnitTypeId.ZERGLING, alliance=_ENEMY),
        make_unit(6, alliance=_ENEMY),
        _depot(900, visibility=Visibility.IN_FOG),
        make_unit(8, build_progress=1.0),
        make_unit(12, UnitTypeId.GHOST, build_progress=1.0, cloak=CloakState.NOT_CLOAKED),
        make_unit(13, UnitTypeId.HIGH_TEMPLAR, alliance=_ENEMY, health=40.0, energy=100.0),
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
        make_unit(12, UnitTypeId.GHOST, build_progress=1.0, cloak=CloakState.CLOAKED_ALLIED),
        make_unit(13, UnitTypeId.HIGH_TEMPLAR, alliance=_ENEMY, health=30.0, energy=50.0),
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
        units = game.tracker.present_units
        assert [(type(event), event.unit) for event in seen] == [
            (OwnUnitCreatedEvent, units[0]),
            (OwnUnitCreatedEvent, units[1]),
            (OwnConstructionStartedEvent, units[1]),
        ]

    def test_each_event_carries_what_happened(self) -> None:
        game = _Game()
        seen = record(game.events, *HAPPENINGS)
        _everything(game)
        units = {unit.tag: unit for unit in game.tracker._units_by_id.values()}
        own = {tag: unit for tag, unit in units.items() if isinstance(unit, OwnUnit)}
        at_16 = {type(event): event for event in seen if event.step == 16}
        assert at_16[UnitTypeChangedEvent] == UnitTypeChangedEvent(16, units[4], UnitTypeId.SIEGE_TANK)
        assert at_16[UnitAllianceChangedEvent] == UnitAllianceChangedEvent(16, units[6], _ENEMY)
        assert at_16[OwnConstructionFinishedEvent].unit is units[2]
        assert at_16[OwnWarpInFinishedEvent].unit is units[3]
        assert at_16[OwnUpgradeFinishedEvent] == OwnUpgradeFinishedEvent(16, UpgradeId.STIMPACK)
        assert at_16[OwnUnitDamagedEvent] == OwnUnitDamagedEvent(16, own[1], 5.0)
        assert at_16[EnemyUnitDamagedEvent] == EnemyUnitDamagedEvent(16, units[13], 10.0)
        assert at_16[OwnUnitEnergyLostEvent] == OwnUnitEnergyLostEvent(16, own[1], 25.0)
        assert at_16[EnemyUnitEnergyLostEvent] == EnemyUnitEnergyLostEvent(16, units[13], 50.0)
        assert at_16[OwnUnitCloakChangedEvent] == OwnUnitCloakChangedEvent(16, own[12], CloakState.NOT_CLOAKED)
        assert at_16[EnemyUnitCloakChangedEvent] == EnemyUnitCloakChangedEvent(16, units[14], CloakState.CLOAKED)
        assert at_16[EnemyUnitLeftSightEvent] == EnemyUnitLeftSightEvent(16, units[5])
        assert at_16[UnitDiedEvent] == UnitDiedEvent(16, units[8])
        assert at_16[UnitFoundDeadEvent] == UnitFoundDeadEvent(16, units[900])
        assert at_16[OwnActionEvent] == OwnActionEvent(16, CameraMove(12, Point((30.75, 139.0))))
        assert at_16[ChatEvent] == ChatEvent(16, 2, "gl hf")
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
        emit = EventBus._emit

        def counted(bus: EventBus, event: Event) -> None:
            made.append(type(event))
            emit(bus, event)

        monkeypatch.setattr(EventBus, "_emit", counted)
        record(game.events, wanted)
        _everything(game)
        assert made and set(made) == {wanted}

    def test_with_no_handler_the_actions_are_never_read_and_no_units_are_compared(self) -> None:
        game = _Game()
        _everything(game)
        assert game.state is not None and "actions" not in vars(game.state)
        assert not game.tracker._compared

    def test_a_handler_that_is_done_stops_the_units_being_compared(self) -> None:
        game = _Game()
        game.events.on(OwnUnitDamagedEvent)(lambda event: Done)
        game.observe(0, make_unit(1, health=45.0))
        game.observe(16, make_unit(1, health=40.0))
        game.observe(32, make_unit(1, health=35.0))
        assert not game.tracker._compared


def _marine(health: float, *, shield: float = 0.0, **fields: Any) -> raw_pb2.Unit:
    return make_unit(1, alliance=_ENEMY, health=health, shield=shield, **fields)


class TestEnergy:
    def _lost(self, *units: raw_pb2.Unit) -> list[float]:
        """The energy reported lost as each of `units` is observed in turn."""
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
        assert game.tracker._compared

    def test_a_unit_of_this_players_loses_energy_to_the_event_of_its_own(self) -> None:
        game = _Game()
        own, enemy = record(game.events, OwnUnitEnergyLostEvent), record(game.events, EnemyUnitEnergyLostEvent)
        game.observe(0, _raven(100.0, alliance=Alliance.OWN))
        game.observe(16, _raven(50.0, alliance=Alliance.OWN))
        assert [(event.unit, event.energy_lost) for event in own] == [(game.tracker.present_units[0], 50.0)]
        assert not enemy


def _raven(energy: float, **fields: Any) -> raw_pb2.Unit:
    unit_type, alliance = fields.pop("unit_type", UnitTypeId.RAVEN), fields.pop("alliance", _ENEMY)
    return make_unit(1, unit_type, alliance=alliance, energy=energy, **fields)


class TestDamage:
    def _damage(self, *units: raw_pb2.Unit) -> list[float]:
        """The damage reported as each of `units` is observed in turn."""
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
        units = {unit.tag: unit for unit in game.tracker.present_units}
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
        """The cloak changes reported as each of `units` is observed in turn, `None` for an observation without it."""
        game = _Game()
        seen = record(game.events, OwnUnitCloakChangedEvent, EnemyUnitCloakChangedEvent)
        for step, unit in enumerate(units):
            game.observe(step * 16, *([] if unit is None else [unit]))
        return [(type(event), event.previous_cloak) for event in seen]

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


def _ghost(cloak: CloakState) -> raw_pb2.Unit:
    return make_unit(1, UnitTypeId.GHOST, cloak=cloak)


def _observer(cloak: CloakState, **fields: Any) -> raw_pb2.Unit:
    return make_unit(1, fields.pop("unit_type", UnitTypeId.OBSERVER), alliance=_ENEMY, cloak=cloak, **fields)


@contextmanager
def _played_as(race: Race) -> Iterator[tuple[RealGame, list[Any], Point]]:
    """A game as `race` under `free` and `fast_build`, every event it hands out from its first observation on, and
    the middle of the map."""
    try:
        game_map = Map.find("PylonAIE_v4")
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
        # The cheats take a few steps to hold.
        game.turn(8)
        yield game, seen, game.map.playable_area.center
        client.leave_game()


def _own(game: RealGame, unit_type: UnitTypeId) -> OwnUnit[Any]:
    unit = next(unit for unit in game.tracker.present_units.own if unit.type_id is unit_type)
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


@pytest.mark.integration
class TestAgainstTheRealGame:
    """Run with `pytest -m integration`. Each plays a few minutes of a game under cheats."""

    def test_zerg_units_hatch_morph_and_become_structures(self) -> None:
        with _played_as(Race.ZERG) as (game, seen, middle):
            # The units the game starts with are created on the first turn.
            started = {event.unit for event in _of(seen, OwnUnitCreatedEvent) if event.step == 0}
            assert started == set(game.tracker.present_units.own)
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
            assert AlertEvent(died.step, Alert.TRAIN_WORKER_COMPLETE) in seen

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
            assert AlertEvent(finished.step, Alert.BUILDING_COMPLETE) in seen

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
            assert UnitTypeChangedEvent(game.step, depot, UnitTypeId.SUPPLY_DEPOT) in seen

            # An add-on is a unit of its own, seen as soon as it is started.
            game.debug(game.create(UnitTypeId.BARRACKS, game.open_ground(home.towards(middle, 13), size=5)))
            game.turn(4)
            game.order(AbilityId.GENERAL_BUILD_REACTOR, _own(game, UnitTypeId.BARRACKS))
            _until(game, lambda: game.tracker.present_units.own.of_type(UnitTypeId.REACTOR_BARRACKS), steps=1)
            reactor = _own(game, UnitTypeId.REACTOR_BARRACKS)
            assert reactor.build_progress < 0.1
            assert OwnConstructionStartedEvent(game.step, reactor) in seen
            _until(game, lambda: reactor.is_complete)
            assert _alerts(seen, Alert.ADD_ON_COMPLETE)

            # A command center becomes an orbital command once the morph finishes, which the game calls an upgrade.
            center = _own(game, UnitTypeId.COMMAND_CENTER)
            game.order(AbilityId.COMMAND_CENTER_MORPH_ORBITAL_COMMAND, center)
            _until(game, lambda: center.type_id is UnitTypeId.ORBITAL_COMMAND)
            assert UnitTypeChangedEvent(game.step, center, UnitTypeId.COMMAND_CENTER) in seen
            assert AlertEvent(game.step, Alert.UPGRADE_COMPLETE) in seen

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
            assert UnitDiedEvent(game.step, bay) in seen

            # A MULE is reported dead once it expires.
            field = min(
                (unit for unit in game.tracker.present_units if unit.type_id is UnitTypeId.MINERAL_FIELD),
                key=lambda unit: unit.position.distance_to(home),
            )
            energy = debug_pb2.DebugSetUnitValue(
                unit_value=debug_pb2.DebugSetUnitValue.Energy, value=200, unit_tag=center.tag
            )
            game.debug(debug_pb2.DebugCommand(unit_value=energy))
            game.turn(2)
            game.order(AbilityId.ORBITAL_COMMAND_CALLDOWN_MULE, center, target=field)
            _until(game, lambda: game.tracker.present_units.own.of_type(UnitTypeId.MULE))
            mule = _own(game, UnitTypeId.MULE)
            _until(game, lambda: mule.is_dead, steps=100, turns=20)
            assert UnitDiedEvent(game.step, mule) in seen
            assert AlertEvent(game.step, Alert.MULE_EXPIRED) in seen

    def test_a_ghost_cloaking_and_an_enemy_observer_being_detected(self) -> None:
        with _played_as(Race.TERRAN) as (game, seen, middle):
            home = _own(game, UnitTypeId.COMMAND_CENTER).position

            # A ghost cloaks and uncloaks once its academy has researched cloaking.
            game.debug(
                game.create(UnitTypeId.GHOST_ACADEMY, game.open_ground(home.towards(middle, 10), size=3)),
                game.create(UnitTypeId.GHOST, home.towards(middle, 6)),
            )
            _until(
                game,
                lambda: len(game.tracker.present_units.own.of_type([UnitTypeId.GHOST, UnitTypeId.GHOST_ACADEMY])) == 2,
            )
            game.order(AbilityId.GHOST_ACADEMY_RESEARCH_GHOST_CLOAK, _own(game, UnitTypeId.GHOST_ACADEMY))
            _until(game, lambda: UpgradeId.GHOST_CLOAK in game.tracker.upgrades)
            ghost = _own(game, UnitTypeId.GHOST)
            game.order(AbilityId.GHOST_CLOAK_ON, ghost)
            _until(game, lambda: _of(seen, OwnUnitCloakChangedEvent), steps=1)
            game.order(AbilityId.GHOST_CLOAK_OFF, ghost)
            _until(game, lambda: len(_of(seen, OwnUnitCloakChangedEvent)) == 2, steps=1)
            assert [(event.unit, event.previous_cloak) for event in _of(seen, OwnUnitCloakChangedEvent)] == [
                (ghost, CloakState.NOT_CLOAKED),
                (ghost, CloakState.CLOAKED_ALLIED),
            ]

            # An enemy observer nothing detects is listed cloaked, out of vision, until a raven beside it detects it.
            game.debug(game.create(UnitTypeId.OBSERVER, home.towards(middle, 4), owner=3 - game.player))
            _until(game, lambda: game.tracker.present_units.enemy.of_type(UnitTypeId.OBSERVER), steps=1)
            observer = game.newest(UnitTypeId.OBSERVER)
            assert observer.cloak is CloakState.CLOAKED and observer.visibility is Visibility.INVISIBLE
            game.debug(game.create(UnitTypeId.RAVEN, observer.position))
            _until(game, lambda: _of(seen, EnemyUnitCloakChangedEvent), steps=1)
            (detected,) = _of(seen, EnemyUnitCloakChangedEvent)
            assert detected.unit is observer and detected.previous_cloak is CloakState.CLOAKED
            assert observer.cloak is CloakState.CLOAKED_DETECTED and observer.visibility is Visibility.IN_VISION

    def test_protoss_warp_ins_damage_sight_death_archons_and_energy(self) -> None:
        with _played_as(Race.PROTOSS) as (game, seen, middle):
            home = _own(game, UnitTypeId.NEXUS).position

            # A zealot warping in is created, and warped in some steps later.
            pylon = game.open_ground(home.towards(middle, 9), size=2)
            game.debug(game.create(UnitTypeId.PYLON, pylon))
            game.debug(game.create(UnitTypeId.WARP_GATE, game.open_ground(home.towards(middle, 13), size=3)))
            game.turn(8)
            game.order(AbilityId.WARP_GATE_WARP_IN_ZEALOT, _own(game, UnitTypeId.WARP_GATE), target=pylon + (3.0, 0.0))
            _until(game, lambda: game.tracker.present_units.own.of_type(UnitTypeId.ZEALOT), steps=1)
            zealot = _own(game, UnitTypeId.ZEALOT)
            assert not zealot.is_complete
            _until(game, lambda: zealot.is_complete, steps=1)
            assert OwnWarpInFinishedEvent(game.step, zealot) in seen
            assert AlertEvent(game.step, Alert.WARP_IN_COMPLETE) in seen

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
            _until(game, lambda: len(game.tracker.present_units.own.of_type(UnitTypeId.HIGH_TEMPLAR)) == 2)
            templar = [
                u for u in game.tracker.present_units.own.of_type(UnitTypeId.HIGH_TEMPLAR) if isinstance(u, OwnUnit)
            ]
            merge = raw_pb2.ActionRawUnitCommand(
                ability_id=AbilityId.GENERAL_MORPH_ARCHON, unit_tags=[unit.tag for unit in templar]
            )
            game.client.act([sc2api_pb2.Action(action_raw=raw_pb2.ActionRaw(unit_command=merge))])
            _until(game, lambda: all(unit.orders for unit in templar), steps=1)
            assert [unit.orders[0].ability for unit in templar] == [AbilityId.GENERAL_MORPH_ARCHON_EXACT] * 2
            _until(game, lambda: _alerts(seen, Alert.MERGE_COMPLETE))
            assert game.tracker.present_units.own.of_type(UnitTypeId.ARCHON)

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
            sighting = enemy._latest_data_in_vision
            assert sighting is not None
            game.debug(debug_pb2.DebugCommand(kill_unit=debug_pb2.DebugKillUnit(tag=[sighting.tag])))
            game.turn(8)
            game.debug(game.create(UnitTypeId.OBSERVER, far + (3.0, 0.0)))
            _until(game, lambda: enemy.is_dead)
            assert UnitFoundDeadEvent(game.step, enemy) in seen

            # A feedback costs its caster 50 energy and drains its target's, which no buff or effect shows. `free`, a
            # toggle, would make it cost nothing, so it is turned off.
            game.debug(
                debug_pb2.DebugCommand(game_state=debug_pb2.DebugGameState.free),
                game.create(UnitTypeId.HIGH_TEMPLAR, at),
                game.create(UnitTypeId.RAVEN, at + (4.0, 0.0), owner=3 - game.player),
            )
            pair = [UnitTypeId.HIGH_TEMPLAR, UnitTypeId.RAVEN]
            _until(game, lambda: len(game.tracker.present_units.of_type(pair)) == 2)
            caster, raven = game.newest(UnitTypeId.HIGH_TEMPLAR), game.newest(UnitTypeId.RAVEN)

            def charge(energy: float, *units: Unit[Any]) -> None:
                value = debug_pb2.DebugSetUnitValue.Energy
                commands = [debug_pb2.DebugSetUnitValue(unit_value=value, value=energy, unit_tag=u.tag) for u in units]
                game.debug(*(debug_pb2.DebugCommand(unit_value=command) for command in commands))
                game.turn(2)

            drained = (OwnUnitEnergyLostEvent, EnemyUnitEnergyLostEvent)

            def lost_after(since: int, *units: Unit[Any]) -> dict[Unit[Any], float]:
                """The energy each of `units` is reported to lose from the `since`-th report on, once each has."""
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
            _until(game, lambda: game.tracker.present_units.own.of_type(UnitTypeId.GHOST))
            ghost = game.newest(UnitTypeId.GHOST)
            charge(150, ghost, raven)
            since = len(_of(seen, *drained))
            game.order(AbilityId.GHOST_EMP, ghost, target=raven.position)
            lost = lost_after(since, ghost, raven)
            assert lost[ghost] == pytest.approx(75, abs=1) and lost[raven] == pytest.approx(100, abs=1)
