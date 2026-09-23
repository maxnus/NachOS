"""The recorded games everything above the protocol is tested against, and what must hold in them."""

import contextlib
from collections.abc import Hashable
from functools import cached_property
from pathlib import Path
from typing import Any

import pytest
from s2clientprotocol import sc2api_pb2

from sc2nachos import Api
from sc2nachos._enum import ReadableIntEnum
from sc2nachos.enemy import Enemy
from sc2nachos.events import (
    EnemyUnitDamagedEvent,
    EnemyUnitEnergyLostEvent,
    EnemyUnitEnteredAreaEvent,
    EnemyUnitEnteredSightEvent,
    EnemyUnitFirstSeenEvent,
    EnemyUnitLeftAreaEvent,
    EnemyUnitLeftSightEvent,
    EnemyUnitVitalDroppedEvent,
    EnemyUnitVitalReachedEvent,
    Event,
    OwnUnitCreatedEvent,
    OwnUnitDamagedEvent,
    OwnUnitEnergyLostEvent,
    OwnUnitEnteredAreaEvent,
    OwnUnitLeftAreaEvent,
    OwnUnitVitalDroppedEvent,
    OwnUnitVitalReachedEvent,
    UnitDiedEvent,
    UnitFoundDeadEvent,
)
from sc2nachos.gamedata import GameData
from sc2nachos.gamemap import GameMap
from sc2nachos.geometry import Circle, Point
from sc2nachos.ids import AbilityId, BuffId, EffectId, UnitTypeId, UpgradeId
from sc2nachos.match import Computer, Participant, Race, Result
from sc2nachos.protocol import Client, PlaybackTransport, Recording
from sc2nachos.state._state import _State
from sc2nachos.units import NotReportedError, OwnUnit, Unit, VitalType
from sc2nachos.units._tracking import _Tracker
from support import HAPPENINGS

# Recorded by `tools/record_corpus.py`, which says what each game is.
CORPUS = sorted((Path(__file__).parent / "corpus").glob("*.sc2rec"))

_CURATED: tuple[type[ReadableIntEnum], ...] = (UnitTypeId, AbilityId, UpgradeId, BuffId, EffectId)


def _observations(recording: Recording) -> list[sc2api_pb2.ResponseObservation]:
    """Every observation in `recording`, in order."""
    return [exchange.response.observation for exchange in recording if exchange.response.HasField("observation")]


def test_there_is_a_corpus() -> None:
    assert CORPUS, "tests/corpus holds no recordings, so nothing below ran"


@pytest.mark.parametrize("path", CORPUS, ids=lambda path: path.stem)
def test_a_recorded_game_replays_to_its_end_asking_what_it_asked(path: Path) -> None:
    """A change to what the library asks a game, or in what order, shows up here as an unanswered request."""
    recording = Recording(path)
    last = _observations(recording)[-1]
    client = Client(PlaybackTransport(recording))
    # A recording answers each kind of request in turn without reading it, so the setup can be repeated without its
    # details.
    client.create_game("recorded", [Participant(), Computer()])
    player = client.join_game(Race.RANDOM)

    api = Api()
    result = api.play(client)

    assert api.step == last.observation.game_loop
    assert result is {entry.player_id: Result(entry.result) for entry in last.player_result}[player]
    client.leave_game()
    client.quit()


@pytest.mark.parametrize("path", CORPUS, ids=lambda path: path.stem)
def test_every_id_a_game_reported_is_curated(path: Path) -> None:
    """A curated enum raises on an id it lacks, so an id a real game reports must be in it."""
    reported: dict[type[ReadableIntEnum], set[int]] = {enum: set() for enum in _CURATED}
    for observation in _observations(Recording(path)):
        raw = observation.observation.raw_data
        for unit in raw.units:
            reported[UnitTypeId].add(unit.unit_type)
            reported[UnitTypeId].update(passenger.unit_type for passenger in unit.passengers)
            reported[BuffId].update(unit.buff_ids)
            reported[AbilityId].update(order.ability_id for order in unit.orders)
        reported[UpgradeId].update(raw.player.upgrade_ids)
        reported[EffectId].update(effect.effect_id for effect in raw.effects)

    known = {enum: {int(member) for member in enum} for enum in _CURATED}
    missing = {enum.__name__: sorted(ids - known[enum]) for enum, ids in reported.items()}
    assert not any(missing.values()), f"{path.stem} reported ids with no curated member: {missing}"


def _tables(recording: Recording) -> GameData:
    """The game data `recording` was played with."""
    return GameData(next(exchange.response.data for exchange in recording if exchange.response.HasField("data")))


# Every property of a unit. In a real game each one returns a value or raises `NotReportedError`, nothing else.
_READS = {
    cls: sorted(name for base in cls.__mro__ for name, member in vars(base).items() if isinstance(member, property))
    for cls in (Unit, OwnUnit)
}
# The properties that name other units; every tag they hold must be one the game reported a unit under.
_NAMING = ("orders", "rally_targets", "passengers", "add_on", "engaged_target", "construction", "builder")


@pytest.mark.parametrize("path", CORPUS, ids=lambda path: path.stem)
def test_the_units_are_every_tagged_unit_the_game_reported_each_one_object_under_one_id(path: Path) -> None:
    recording = Recording(path)
    tracker = _Tracker(_tables(recording), Enemy())
    objects: dict[int, Unit[Any]] = {}
    for index, observation in enumerate(_observations(recording)):
        step = observation.observation.game_loop
        tracker.update(observation.observation.raw_data, step)
        units = tracker.unit_tracker.present
        assert [unit.tag for unit in units] == [unit.tag for unit in observation.observation.raw_data.units if unit.tag]
        assert len({id(unit) for unit in units}) == len(units), "two tags of one observation are one unit"
        for unit in units:
            assert objects.setdefault(unit.id, unit) is unit
            assert not unit.is_stale
        for unit in units.own:
            for name in _NAMING:
                getattr(unit, name)
        if index % 50 == 0:
            for unit in tracker.unit_tracker.known:
                for name in _READS[type(unit)]:
                    with contextlib.suppress(NotReportedError):
                        getattr(unit, name)


@pytest.mark.parametrize("path", CORPUS, ids=lambda path: path.stem)
def test_what_happened_holds_together_over_a_whole_game(path: Path) -> None:
    """Each own unit is created once and each enemy unit first seen once, the dead are dead, a unit enters and
    leaves sight alternately, and damage is never zero."""
    recording = Recording(path)
    client = Client(PlaybackTransport(recording))
    client.create_game("recorded", [Participant(), Computer()])
    client.join_game(Race.RANDOM)
    api = Api()
    seen: list[Event] = []
    for event_type in HAPPENINGS:
        api.events.on(event_type)(lambda event: seen.append(event))
    api.play(client)

    tracker = api._current_game().tracker
    # The last observation gets no turn, so what it first saw is never reported.
    unreported = {
        unit.id for unit in (*tracker.last_changes.own_units_created, *tracker.last_changes.enemy_units_first_seen)
    }
    ever = [unit_id for unit_id in tracker.unit_tracker._units_by_id if unit_id not in unreported]
    created = [event.unit for event in seen if isinstance(event, OwnUnitCreatedEvent)]
    first_seen = [event.unit for event in seen if isinstance(event, EnemyUnitFirstSeenEvent)]
    assert sorted(unit.id for unit in created) == [unit_id for unit_id in sorted(ever) if unit_id // 100_000 == 1]
    assert sorted(unit.id for unit in first_seen) == [unit_id for unit_id in sorted(ever) if unit_id // 100_000 == 4]
    assert all(event.unit.is_dead for event in seen if isinstance(event, UnitDiedEvent | UnitFoundDeadEvent))
    in_sight: dict[int, bool] = {}
    for event in seen:
        if isinstance(event, EnemyUnitEnteredSightEvent | EnemyUnitLeftSightEvent):
            entering = isinstance(event, EnemyUnitEnteredSightEvent)
            assert in_sight.get(event.unit.id, False) is not entering, f"{event} twice in a row"
            in_sight[event.unit.id] = entering
    assert all(event.damage > 0 for event in seen if isinstance(event, OwnUnitDamagedEvent | EnemyUnitDamagedEvent))
    drained = [event for event in seen if isinstance(event, OwnUnitEnergyLostEvent | EnemyUnitEnergyLostEvent)]
    assert all(event.energy_lost > 0 for event in drained)
    assert in_sight, "no enemy unit ever came into sight"
    client.leave_game()
    client.quit()


_REACHED = (OwnUnitVitalReachedEvent, EnemyUnitVitalReachedEvent)
_DROPPED = (OwnUnitVitalDroppedEvent, EnemyUnitVitalDroppedEvent)
# A value of each vital that units of a game cross: half their most, or an amount many units have.
_VALUES = {
    VitalType.HEALTH: 50.0,
    VitalType.SHIELD: 20.0,
    VitalType.LIFE: 100.0,
    VitalType.ENERGY: 50.0,
    **{vital: 0.5 for vital in VitalType if vital.is_fraction},
}
_ENTERED = (OwnUnitEnteredAreaEvent, EnemyUnitEnteredAreaEvent)
_LEFT = (OwnUnitLeftAreaEvent, EnemyUnitLeftAreaEvent)


@pytest.mark.parametrize("path", CORPUS, ids=lambda path: path.stem)
def test_what_is_watched_holds_together_over_a_whole_game(path: Path) -> None:
    """A unit that crosses a vital value is on the side it crossed to, a unit that enters an area is inside it and one
    that leaves is outside, and each unit crosses alternately in each direction."""
    recording = Recording(path)
    game_map = GameMap(
        next(exchange.response.game_info for exchange in recording if exchange.response.HasField("game_info"))
    )
    client = Client(PlaybackTransport(recording))
    client.create_game("recorded", [Participant(), Computer()])
    client.join_game(Race.RANDOM)
    api = Api()
    wrong: list[str] = []
    crossed: dict[tuple[int, Hashable], bool] = {}

    def crossing(event: Event, unit: Unit[Any], key: Hashable, onward: bool, *, holds: bool) -> None:
        if not holds:
            wrong.append(f"{event} does not hold of {unit}")
        if crossed.get((unit.id, key)) is onward:
            wrong.append(f"{event} twice in a row")
        crossed[unit.id, key] = onward

    def vital(event: Event) -> None:
        assert isinstance(event, _REACHED + _DROPPED)
        now, reached = getattr(event.unit, event.vital.value.replace(" ", "_")), isinstance(event, _REACHED)
        holds = now >= event.value if reached else now < event.value
        crossing(event, event.unit, (event.vital, event.value), reached, holds=holds)

    def area(event: Event) -> None:
        assert isinstance(event, _ENTERED + _LEFT)
        entered = isinstance(event, _ENTERED)
        crossing(event, event.unit, event.area, entered, holds=(event.unit.position in event.area) is entered)

    for vital_type, value in _VALUES.items():
        for event_type in _REACHED + _DROPPED:
            api.events.on(event_type.of(vital_type, value))(vital)
    # The starting units stand around this player's main base, and its workers cross a circle around them as they
    # mine.
    starting = [unit.pos for unit in _observations(recording)[0].observation.raw_data.units if unit.alliance == 1]
    home = Point((sum(pos.x for pos in starting) / len(starting), sum(pos.y for pos in starting) / len(starting)))
    for watched in (
        Circle(home, 4),
        Circle(home, 20),
        Circle(game_map.playable_area.center, 30),
        *(Circle(at, 20) for at in game_map.opponent_start_locations),
    ):
        for event_type in _ENTERED + _LEFT:
            api.events.on(event_type.of(watched))(area)
    api.play(client)

    assert not wrong, wrong[:5]
    assert crossed, "no unit crossed anything watched"
    client.leave_game()
    client.quit()


# Every public property of the state beyond its units.
_STATE_READS = sorted(
    name
    for name, member in vars(_State).items()
    if isinstance(member, property | cached_property) and not name.startswith("_")
)


@pytest.mark.parametrize("path", CORPUS, ids=lambda path: path.stem)
def test_every_observation_answers_every_read_beyond_its_units(path: Path) -> None:
    recording = Recording(path)
    tracker = _Tracker(_tables(recording), Enemy())
    game_map = GameMap(
        next(exchange.response.game_info for exchange in recording if exchange.response.HasField("game_info"))
    )
    for observation in _observations(recording):
        tracker.update(observation.observation.raw_data, observation.observation.game_loop)
        state = _State(observation, tracker, game_map)
        for name in _STATE_READS:
            getattr(state, name)
    assert "actions" in _STATE_READS and "vision" in _STATE_READS
